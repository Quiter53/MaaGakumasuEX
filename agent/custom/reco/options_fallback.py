"""Conservative three-option fallback after repeated strict recognition misses."""

import time
import numpy as np

from maa.agent.agent_server import AgentServer
from maa.custom_recognition import CustomRecognition
from utils import logger

TEMPLATES = {name: f"produce/choose_{name}.png" for name in ("Vo", "Da", "Vi", "null")}


def validated_rows(candidates):
    groups = []
    for name, box, score in sorted(candidates, key=lambda c: c[1][1]):
        x, y, w, h = box
        if not (score >= .78 and 20 <= x <= 100 and 640 <= y <= 900 and 540 <= w <= 660 and 55 <= h <= 100):
            continue
        group = next((g for g in groups if abs(g[0][1][1] - y) <= 18), None)
        if group is None:
            groups.append([(name, box, score)])
        else:
            group.append((name, box, score))
    rows = []
    for group in groups:
        ranked = sorted(group, key=lambda c: c[2], reverse=True)
        best = ranked[0]
        if best[2] < .84:
            continue
        competitors = [c for c in ranked if c[0] != best[0]]
        if competitors and best[2] - competitors[0][2] < .04:
            return []
        rows.append(best)
    if len(rows) != 3:
        return []
    if any(not 80 <= b[1][1] - a[1][1] <= 140 for a, b in zip(rows, rows[1:])):
        return []
    if max(r[1][0] for r in rows) - min(r[1][0] for r in rows) > 12:
        return []
    return rows


def read_fallback_options(context, image):
    candidates = []
    for name, template in TEMPLATES.items():
        detail = context.run_recognition(
            "ProduceRecognitionWorkOptions", image,
            pipeline_override={"ProduceRecognitionWorkOptions": {"template": template, "threshold": .84, "focus": None}},
        )
        if detail:
            candidates.extend((name, list(r.box), float(r.score)) for r in detail.all_results)
    return validated_rows(candidates)


class FailureGate:
    def __init__(self):
        self.reset()

    def reset(self):
        self.count = 0
        self.start = self.last = None
        self.boxes = None
        self.crop = None

    def observe(self, rows, image, now):
        boxes = np.asarray([r[1] for r in rows])
        crop = image[640:960:4, 20:700:4].astype(np.float32)
        if (self.last is None or now - self.last > 4 or self.boxes is None
                or self.boxes.shape != boxes.shape or np.max(np.abs(self.boxes - boxes)) > 5
                or self.crop.shape != crop.shape or np.mean(np.abs(self.crop - crop)) > 3):
            self.reset()
            self.start = now
        self.last, self.boxes, self.crop = now, boxes, crop
        self.count += 1
        return self.count >= 5 and now - self.start >= 6


FAILURE_GATE = FailureGate()


@AgentServer.custom_recognition("ProduceOptionsFailureFallback")
class ProduceOptionsFailureFallback(CustomRecognition):
    def __init__(self):
        super().__init__()
        self.gate = FAILURE_GATE
        self.task_id = None

    def analyze(self, context, argv):
        task_id = getattr(getattr(argv, "task_detail", None), "task_id", None)
        if task_id != self.task_id:
            self.gate.reset()
            self.task_id = task_id
        image = argv.image
        strict = context.run_recognition("ProduceRecognitionWorkOptions", image)
        if strict and strict.hit:
            self.gate.reset()
            return None
        # Avoid four extra template passes on unrelated pages.
        raw = getattr(strict, "all_results", []) if strict else []
        if not validated_rows([("shape", list(r.box), float(r.score)) for r in raw]):
            self.gate.reset()
            return None
        rows = read_fallback_options(context, image)
        if not rows:
            self.gate.reset()
            return None
        if not self.gate.observe(rows, image, time.monotonic()):
            return None
        logger.warning(f"三选项连续识别失败 {self.gate.count} 次且持续至少 6 秒，启用本次保底，保留原属性选择策略")
        self.gate.reset()
        return CustomRecognition.AnalyzeResult(box=rows[0][1], detail={"fallback": True})

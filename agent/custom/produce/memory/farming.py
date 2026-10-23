"""Collect multiple finalized primary memories across cultivation rounds."""

import re
import json
import time
import hashlib
from pathlib import Path
from dataclasses import dataclass

import numpy as np
from utils import logger
from custom.produce.memory import modes as m
from custom.produce.memory.diamond import diamond_row, summary_diamond


def target_key(target):
    return json.dumps({k: target[k] for k in ("name", "upgrade", "customMin")}, ensure_ascii=False, sort_keys=True)


def batch_key(session):
    return hashlib.sha256("\n".join(sorted(target_key(t) for t in session.targets)).encode()).hexdigest()


def load_progress(session, path=None):
    session.progress_path = Path(path) if path is not None else Path(__file__).resolve().parents[4] / "config/memory_farming_progress.json"
    try:
        data = json.loads(session.progress_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("invalid progress")
        saved = data.get(batch_key(session), {})
        if not isinstance(saved, dict):
            raise ValueError("invalid batch")
        completed = set(saved.get("completed", []))
        session.collected = {t["slot"] for t in session.targets if target_key(t) in completed}
        if len(session.collected) == len(session.targets):
            session.collected = set()  # A deliberate new task after completion starts a new batch.
        elif session.collected:
            logger.info(f"恢复回忆收集进度: {len(session.collected)}/{len(session.targets)}")
    except (OSError, ValueError, TypeError):
        logger.info("本次回忆收集从未完成目标开始")


def save_progress(session):
    if session.progress_path is None:
        return  # Isolated tests may use configure directly.
    path = session.progress_path
    try:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        data[batch_key(session)] = {"completed": [target_key(t) for t in session.targets if t["slot"] in session.collected]}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError as error:
        logger.warning(f"回忆进度写入失败，本次任务仍保留内存进度: {error}")


@dataclass(frozen=True)
class Candidate:
    slots: frozenset
    diamond: bool | None
    priority: tuple


def evaluate(cards, diamond, session):
    slots = frozenset(t["slot"] for t in session.targets if any(m.card_satisfies(c, t, "card") for c in cards))
    missing = slots - session.collected
    # Already completed ordinary cards no longer justify spending a round.
    # A diamond version remains highest priority even after its target was met.
    rank = 3 if slots and diamond is True else 2 if missing else 1 if diamond is not False else 0
    return Candidate(slots, diamond, (rank, len(missing), len(slots)))


def content_layout(texts, top, bottom):
    """Ignore OCR order, scrollbar and animated backdrop; retain row positions."""
    result = []
    for item in texts:
        x, y, w, h = item["box"]
        text = re.sub(r"\s+", "", item.get("text", ""))
        if 55 <= x < 650 and top <= y and y + h <= bottom and len(text) >= 3:
            result.append((text, int(y)))
    return sorted(result)


def same_content(before, after):
    # Enough text is required to prove that a swipe did not move the list.
    return len(before) >= 3 and len(before) == len(after) and all(a[0] == b[0] and abs(a[1] - b[1]) <= 8 for a, b in zip(before, after))


def read_diamond(reader, kind):
    image, texts = reader.capture()
    if m.screen_kind(texts) != kind:
        raise m.MemoryTransition("词条读取时页面切换")
    if kind == "summary":
        result = summary_diamond(reader.context, image)
        if result is not None:
            return result
    seen_heading = False
    previous_layout = None
    previous_pane = None
    for _ in range(8):
        headings = [t for t in texts if re.fullmatch(r"アビリティ|能力|词条", t.get("text", "").strip())]
        seen_heading = seen_heading or bool(headings)
        top = max((t["box"][1] + t["box"][3] for t in headings), default=700 if kind == "summary" else 230)
        bottom = 1110 if kind == "summary" else 950
        if seen_heading and diamond_row(reader.context, image, top, bottom):
            return True
        pane_top = 700 if kind == "summary" else 230
        layout = content_layout(texts, pane_top, bottom)
        pane = image[pane_top:bottom:4, 65:650:4, :3].astype(np.int16)
        pixels_still = previous_pane is not None and previous_pane.shape == pane.shape and np.mean(np.abs(previous_pane - pane)) < 0.7
        # Check the newly exposed rows FIRST, then accept a stable bottom.
        if previous_layout is not None and (same_content(previous_layout, layout) or pixels_still):
            return False if seen_heading else None
        previous_layout, previous_pane = layout, pane
        reader.scroll(True, kind)
        image, texts = reader.capture()
        if m.screen_kind(texts) != kind:
            raise m.MemoryTransition("词条滚动期间页面切换")
    return None


def handle(context):
    from custom.produce.memory.flow import handle as advance

    return advance(context)

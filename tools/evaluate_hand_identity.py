"""Offline manually-labelled screenshot evaluation; never controls the game."""
import argparse
from collections import defaultdict
from dataclasses import asdict
import json
from pathlib import Path
import sys
import time

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"agent"))
# Do not import custom.__init__: it registers native actions.
import types
package = types.ModuleType("custom")
package.__path__ = [str(ROOT/"agent/custom")]
sys.modules.setdefault("custom", package)
from custom.hand_identity import HandIdentityReader
from custom.card_visual import normalize_card_name


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    reader = HandIdentityReader()
    frames = json.loads((ROOT/"tests/fixtures/hand_identity_labels.json").read_text(encoding="utf-8"))["frames"]
    counts = defaultdict(lambda: dict(total=0, correct=0, wrong=0, unknown=0, top1_correct=0))
    records = []
    for frame in frames:
        image = np.asarray(Image.open(args.source/frame["file"]).convert("RGB"))[:,:,::-1].copy()
        boxes = [[x,884,189,249] for x in frame["xs"]]
        for box, label in zip(boxes, frame["names"]):
            start = time.perf_counter()
            result = reader.read(image, box, boxes)
            ms = (time.perf_counter()-start)*1000
            predicted = result.name if result else None
            equal = lambda a: normalize_card_name(a) == normalize_card_name(label)
            group = counts[str(len(boxes))]
            group["total"] += 1
            group["unknown" if predicted is None else "correct" if equal(predicted) else "wrong"] += 1
            group["top1_correct"] += int(result is not None and equal(result.candidate))
            records.append(dict(file=frame["file"],box=box,expected=label,ms=round(ms,2),result=asdict(result) if result else None))
    report = dict(scope="Identity given manually annotated boxes, not end-to-end detection; no live-game proof",groups=counts,records=records)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(counts,ensure_ascii=False))
    print("median/p95 ms", np.percentile([r["ms"] for r in records],[50,95]).round(2).tolist())


if __name__ == "__main__":
    main()

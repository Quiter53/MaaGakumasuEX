"""Read-only audit of actual template misses; durations are observed log spans."""
import argparse
import json
import re
from datetime import datetime
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("roots", nargs="+", type=Path)
    args = parser.parse_args()
    events = {}
    files = set()
    for root in args.roots:
        files.update(root.rglob("*maafw*.log"))
    scanned = matched = misses = 0
    for path in sorted(files):
        scanned += 1
        with path.open(encoding="utf-8", errors="replace") as stream:
            for line in stream:
                if "TemplateMatcher::analyze] ProduceRecognitionWorkOptions " not in line:
                    continue
                matched += 1
                if "[best_result_=null]" not in line or "[param_.thresholds=[0.900000]]" not in line:
                    continue
                misses += 1
                match = re.search(r"\[all_results_=(\[.*?\])\]", line)
                stamp = re.match(r"\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+)\]", line)
                if not match or not stamp:
                    continue
                rows = []
                for result in json.loads(match.group(1)):
                    x, y, w, h = result["box"]
                    if .84 <= result["score"] < .9 and 20 <= x <= 100 and 640 <= y <= 900 and 540 <= w <= 660 and 55 <= h <= 100:
                        if not any(abs(y - row) <= 18 for row in rows):
                            rows.append(y)
                if len(rows) == 3:
                    key = stamp.group(1)
                    events[key] = {"time": key, "file": str(path), "score": max(r["score"] for r in json.loads(match.group(1)))}
    episodes = []
    for key in sorted(events):
        event = events[key]
        t = datetime.fromisoformat(key)
        if not episodes or (t - datetime.fromisoformat(episodes[-1]["last"])).total_seconds() > 5:
            episodes.append({"first": key, "last": key, "samples": 1, "file": event["file"]})
        else:
            episodes[-1]["last"] = key
            episodes[-1]["samples"] += 1
    for episode in episodes:
        episode["seconds"] = round((datetime.fromisoformat(episode["last"]) - datetime.fromisoformat(episode["first"])).total_seconds(), 1)
    print(json.dumps({"files": scanned, "checks": matched, "strictMisses": misses,
                      "threeRowNearMissFramesDeduplicated": len(events), "episodes": episodes}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

"""Replay a swallowed memory-generation click with native OCR and a virtual controller."""

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image
from maa.controller import CustomController
from maa.resource import Resource
from maa.tasker import Tasker
from maa.toolkit import Toolkit


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("frame", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    Toolkit.init_option(str(args.output.resolve()))
    root = Path(__file__).resolve().parents[1]
    frame = np.asarray(Image.open(args.frame).convert("RGB"))[:, :, ::-1].copy()
    assert frame.shape == (1280, 720, 3)
    source = json.loads((root / "assets/resource/base/pipeline/Produce.json").read_text("utf-8"))

    class Replay(CustomController):
        def __init__(self):
            super().__init__()
            self.clicks = []

        def connect(self):
            return True

        def request_uuid(self):
            return "memory-generation-offline"

        def get_features(self):
            return 0

        def screencap(self):
            return frame.copy() if len(self.clicks) < 3 else np.full_like(frame, (0, 255, 0))

        def click(self, x, y):
            assert 310 <= x <= 410 and 945 <= y <= 1005, (x, y)
            self.clicks.append((x, y))
            return True

    results = []
    for retry in (False, True):
        resource = Resource()
        resource.use_cpu()
        assert resource.post_bundle(str(root / "assets/resource/base")).wait().succeeded
        controller = Replay()
        assert controller.post_connection().wait().succeeded
        tasker = Tasker()
        assert tasker.bind(resource, controller)
        node = source["ProduceGeneration"]
        next_nodes = [name for name in node["next"] if retry or name != "ProduceGeneration"]
        overrides = {
            name.removeprefix("[JumpBack]"): {"enabled": False}
            for name in next_nodes if name != "ProduceGeneration"
        }
        overrides["ProduceGeneration"] = {"next": next_nodes + ["ReplayDone"], "timeout": 1500}
        overrides["ReplayDone"] = {
            "recognition": {"type": "ColorMatch", "param": {
                "roi": [0, 0, 10, 10], "method": 4,
                "lower": [[0, 250, 0]], "upper": [[5, 255, 5]], "count": 100,
            }},
            "action": {"type": "DoNothing"}, "next": [],
        }
        result = tasker.post_task("ProduceGeneration", overrides).wait()
        assert result.succeeded == retry, (retry, result.status)
        assert len(controller.clicks) == (3 if retry else 1), controller.clicks
        results.append({"retryRoute": retry, "completed": result.succeeded, "virtualClicks": controller.clicks})
    (args.output / "result.json").write_text(json.dumps(results, indent=2), "utf-8")
    print(json.dumps(results))


if __name__ == "__main__":
    main()

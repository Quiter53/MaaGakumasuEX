"""Read-only native model replay; controller input is disabled."""
import argparse
import importlib
import json
import sys
import types
from pathlib import Path

import numpy as np
from PIL import Image
from maa.controller import CustomController
from maa.pipeline import JNeuralNetworkDetect
from maa.resource import Resource
from maa.tasker import Tasker
from maa.toolkit import Toolkit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--image", type=Path)
    parser.add_argument("--node", default="ProduceRecognitionCards")
    parser.add_argument("--crop", type=int, nargs=4, metavar=("LEFT", "TOP", "RIGHT", "BOTTOM"))
    parser.add_argument("--deck", choices=("change", "delete"), help="Also replay deck-only frame supplementation")
    parser.add_argument("--threshold", type=float, help="Offline experiment only; does not edit runtime settings")
    parser.add_argument("--identity", action="store_true", help="Report square-card visual identity separately from detection")
    parser.add_argument("--reward", action="store_true", help="Validate three/four-card reward layout")
    args = parser.parse_args()
    if args.deck or args.identity or args.reward:
        # Load pure vision helpers without custom/__init__.py, whose action
        # decorators initialize AgentServer and disable standalone resources.
        package = types.ModuleType("_deck_replay")
        package.__path__ = [str(Path(__file__).resolve().parents[1] / "agent" / "custom")]
        sys.modules[package.__name__] = package
        complete_grid_rows = importlib.import_module("_deck_replay.deck_scan").complete_grid_rows
        card_visual = importlib.import_module("_deck_replay.card_visual")
        reward_card_row = importlib.import_module("_deck_replay.reward_layout").reward_card_row
    Toolkit.init_option(".")
    resource = Resource()
    resource.use_cpu()
    assert resource.post_bundle(args.bundle).wait().status.succeeded, "resource load failed"
    image = np.zeros((1280, 720, 3), dtype=np.uint8)
    if args.image:
        with Image.open(args.image) as source:
            if args.crop:
                source = source.crop(tuple(args.crop))
            image = np.asarray(source.convert("RGB"))[:, :, ::-1].copy()
        if image.shape[1] != 720 or image.shape[0] not in (1279, 1280):
            raise ValueError("Use an unmodified 720x1279/1280 game frame")

    class ReadOnlyController(CustomController):
        def connect(self): return True
        def request_uuid(self): return "offline-model-check"
        def screencap(self): return image.copy()
        def get_features(self): return 0
        def click(self, *args): raise RuntimeError("Input disabled")
        def swipe(self, *args): raise RuntimeError("Input disabled")

    controller = ReadOnlyController()
    assert controller.post_connection().wait().status.succeeded
    tasker = Tasker()
    assert tasker.bind(resource, controller)
    node = resource.get_node_data(args.node)
    params = JNeuralNetworkDetect(**node["recognition"]["param"])
    if args.threshold is not None:
        if not 0 < args.threshold <= 1:
            raise ValueError("threshold must be in (0, 1]")
        params.threshold = [args.threshold]
    detail = tasker.post_recognition("NeuralNetworkDetect", params, image).wait().get()
    assert detail and detail.nodes, "native recognition returned no result"
    result = detail.nodes[0].recognition
    assert result is not None
    print(json.dumps({"bundle": str(args.bundle), "image": str(args.image), "hit": result.hit,
                      "results": [{"label": r.label, "box": list(r.box), "score": r.score}
                                  for r in result.all_results]}, ensure_ascii=False))
    if args.deck:
        top = 620 if args.deck == "change" else 380
        boxes = [list(map(int, r.box)) for r in result.all_results]
        boxes = [b for b in boxes if 60 <= b[0] and b[0]+b[2] <= 660
                 and top <= b[1] and b[1]+b[3] <= 1100
                 and 90 <= b[2] <= 145 and 90 <= b[3] <= 145 and .8 <= b[2]/b[3] <= 1.2]
        completed = complete_grid_rows(image, boxes)
        print(json.dumps({"deck": args.deck, "detected_complete_tiles": len(boxes),
                          "supplemented_complete_tiles": len(completed), "boxes": completed}))
    if args.reward:
        reward_row = reward_card_row([{"box": list(r.box), "detector_score": r.score}
                                      for r in result.all_results if r.label in ("cards", "suggestions")])
        print(json.dumps({"reward_row": reward_row}, ensure_ascii=False))
    if args.identity:
        boxes = ([c["box"] for c in reward_row] if args.reward else
                 completed if args.deck else [list(r.box) for r in result.all_results])
        identities = []
        for box in boxes:
            match = card_visual.match_card_visual(image, box)
            identities.append({"box": box, "name": match.name if match else None,
                               "confidence": match.confidence if match else None,
                               "score": match.score if match else None,
                               "margin": match.margin if match else None})
        print(json.dumps({"visual_identity": identities}, ensure_ascii=False))


if __name__ == "__main__":
    main()

"""Read-only native model replay; controller input is disabled."""
import argparse
import json
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
    args = parser.parse_args()
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
    detail = tasker.post_recognition("NeuralNetworkDetect", params, image).wait().get()
    assert detail and detail.nodes, "native recognition returned no result"
    result = detail.nodes[0].recognition
    assert result is not None
    print(json.dumps({"bundle": str(args.bundle), "image": str(args.image), "hit": result.hit,
                      "results": [{"label": r.label, "box": list(r.box), "score": r.score}
                                  for r in result.all_results]}, ensure_ascii=False))


if __name__ == "__main__":
    main()

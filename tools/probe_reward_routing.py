"""Read-only native reward routing replay. Never binds a live controller."""
import argparse
import dataclasses
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PIL import Image
from maa.library import Library


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("portable", type=Path)
    parser.add_argument("image", type=Path)
    parser.add_argument("--crop", nargs=4, type=int)
    args = parser.parse_args()
    Library.open(args.portable / "runtimes/win-x64/native")
    Library.version()
    from maa.resource import Resource
    from maa.tasker import Tasker
    from maa.pipeline import JOCR, JTemplateMatch, JFeatureMatch

    resource = Resource()
    assert resource.post_bundle(args.portable / "resource/base").wait().status.succeeded
    tasker = Tasker()
    tasker._resource_holder = resource
    assert Library.framework().MaaTaskerBindResource(tasker._handle, resource._handle)
    source = Image.open(args.image).convert("RGB")
    if args.crop:
        source = source.crop(tuple(args.crop))
    image = np.asarray(source.resize((720, 1280)))[:, :, ::-1].copy()

    class ReadOnlyContext:
        def __init__(self):
            self.tasker = SimpleNamespace(stopping=False)
            self.calls = []

        def run_recognition(self, name, frame, pipeline_override=None):
            override = (pipeline_override or {}).get(name, {})
            node = resource.get_node_data(name)
            kind = override.get("recognition", node["recognition"]["type"])
            params = {**node["recognition"]["param"], **override}
            cls = {"OCR": JOCR, "TemplateMatch": JTemplateMatch, "FeatureMatch": JFeatureMatch}[kind]
            params = {key: value for key, value in params.items() if key in {f.name for f in dataclasses.fields(cls)}}
            if cls is JTemplateMatch:
                for key in ("template", "threshold"):
                    if key in params and not isinstance(params[key], list):
                        params[key] = [params[key]]
            started = time.perf_counter()
            result = tasker.post_recognition(kind, cls(**params), frame).wait().get().nodes[0].recognition
            self.calls.append({"node": name, "hit": result.hit, "seconds": round(time.perf_counter() - started, 3)})
            return result

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
    with patch("maa.agent.agent_server.AgentServer.custom_action", lambda _: lambda c: c), \
         patch("maa.agent.agent_server.AgentServer.custom_recognition", lambda _: lambda c: c):
        from custom.action.produce import ProduceChooseRewardAuto
    context = ReadOnlyContext()
    kind = ProduceChooseRewardAuto._reward_kind(context, image)
    print(json.dumps({"kind": kind, "calls": context.calls}, ensure_ascii=False))


if __name__ == "__main__":
    main()

"""Exercise the real pipeline's orientation gate with a virtual landscape screen."""

import argparse
import json
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import maa
from maa.library import Library
from maa.controller import CustomController
from maa.resource import Resource
from maa.tasker import Tasker
from maa.toolkit import Toolkit
from maa.agent.agent_server import AgentServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
# Register directly on the in-process Resource instead of starting an IPC agent.
with patch.object(AgentServer, "custom_recognition", side_effect=lambda name: lambda cls: cls), \
     patch.object(AgentServer, "custom_action", side_effect=lambda name: lambda cls: cls):
    from custom.reco.produce import ProduceLandscapeWait


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    output = parser.parse_args().output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    Library.open(Path(maa.__file__).parent / "bin")
    Toolkit.init_option(str(output))

    class Replay(CustomController):
        def connect(self): return True
        def request_uuid(self): return "landscape-route-offline"
        def get_features(self): return 0
        def screencap(self): return np.zeros((720, 1280, 3), np.uint8)
        def click(self, x, y): raise AssertionError("Orientation routing must not click")

    resource = Resource()
    resource.use_cpu()
    assert resource.post_bundle(str(ROOT / "assets/resource/base")).wait().succeeded
    assert resource.register_custom_recognition("ProduceLandscapeWait", ProduceLandscapeWait())
    controller = Replay()
    assert controller.post_connection().wait().succeeded
    tasker = Tasker()
    assert tasker.bind(resource, controller)
    for entry in ("ProduceEntry", "ProduceEntryNIA", "ProduceEntryHIF"):
        result = tasker.post_task(entry, {"ProduceLandscapeWait": {"next": [], "focus": {}, "post_delay": 0}}).wait()
        assert result.succeeded, entry
    logs = "\n".join(path.read_text("utf-8", errors="replace") for path in output.rglob("*.log"))
    assert "roi is out of range" not in logs
    assert "uid is invalid" not in logs
    (output / "result.json").write_text(json.dumps({"scenarios": 3, "passed": True, "roiErrors": 0}), "utf-8")
    print("3 native landscape routes passed, no ROI or UID errors")


if __name__ == "__main__":
    main()

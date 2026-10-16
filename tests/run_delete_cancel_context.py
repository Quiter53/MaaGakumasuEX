"""Native task-context state check; no game controller or input is used."""
import sys
import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
from maa.library import Library


def main():
    portable = Path(sys.argv[1])
    Library.open(portable / "runtimes/win-x64/native")
    Library.version()
    from maa.resource import Resource
    from maa.tasker import Tasker
    from maa.custom_action import CustomAction
    from maa.controller import CustomController

    class ReadOnlyController(CustomController):
        def connect(self): return True
        def request_uuid(self): return "delete-cancel-context-offline"
        def screencap(self): return np.zeros((1280, 720, 3), np.uint8)
        def get_features(self): return 0
        def start_app(self, *args): return False
        def stop_app(self, *args): return False
        def click(self, *args): return False
        def swipe(self, *args): return False
        def touch_down(self, *args): return False
        def touch_move(self, *args): return False
        def touch_up(self, *args): return False
        def click_key(self, *args): return False
        def input_text(self, *args): return False
        def key_down(self, *args): return False
        def key_up(self, *args): return False

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
    with (patch("maa.agent.agent_server.AgentServer.custom_action", lambda _: lambda cls: cls),
          patch("maa.agent.agent_server.AgentServer.custom_recognition", lambda _: lambda cls: cls)):
        from custom.produce.shop import ProduceShoppingAuto, ProduceHIFDeleteAuto

    resource = Resource()
    assert resource.post_bundle(portable / "resource/base").wait().status.succeeded
    observations = []

    class CheckState(CustomAction):
        def run(self, context, argv):
            shop = ProduceShoppingAuto()
            stage = json.loads(argv.custom_action_param)
            before = shop._delete_cancel_state(context)
            observations.append((stage, before))
            if stage == "set":
                assert not before, "Cancel state leaked into a new task"
                shop._delete_cancel_state(context, pending=True)
            else:
                assert ProduceHIFDeleteAuto()._delete_cancel_state(context), "Callback lost pending cancellation"
            return True

    assert resource.register_custom_action("DeleteCancelContextCheck", CheckState())
    tasker = Tasker()
    controller = ReadOnlyController()
    assert controller.post_connection().wait().status.succeeded
    assert tasker.bind(resource, controller)
    nodes = {
        "CancelStateSet": {"recognition": "DirectHit", "action": "Custom",
                           "custom_action": "DeleteCancelContextCheck", "custom_action_param": "set",
                           "next": ["CancelStateRead"]},
        "CancelStateRead": {"recognition": "DirectHit", "action": "Custom",
                            "custom_action": "DeleteCancelContextCheck", "custom_action_param": "read"},
    }
    for _ in range(2):
        assert tasker.post_task("CancelStateSet", nodes).wait().status.succeeded
    assert observations == [("set", False), ("read", True)] * 2, observations
    print("NATIVE_CANCEL_CONTEXT_OK", observations)


if __name__ == "__main__":
    main()

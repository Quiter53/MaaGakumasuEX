"""Read-only native focus override probe; never binds a live controller."""
import json
import sys
from pathlib import Path

import numpy as np
from maa.library import Library


def main():
    root = Path(sys.argv[1])
    Library.open(root / "runtimes/win-x64/native")
    from maa.resource import Resource
    from maa.tasker import Tasker
    from maa.custom_action import CustomAction
    from maa.controller import CustomController
    from maa.event_sink import EventSink

    resource = Resource()
    assert resource.post_bundle(root / "resource/base").wait().status.succeeded
    variants = {
        "original": {},
        "empty_string": {"focus": {"Node.Recognition.Succeeded": ""}},
        "empty_object": {"focus": {}},
        "null_focus": {"focus": None},
        "null_event": {"focus": {"Node.Recognition.Succeeded": None}},
        "empty_list": {"focus": {"Node.Recognition.Succeeded": []}},
    }
    nodes = {"FocusProbe": {"recognition": "DirectHit", "action": "Custom", "custom_action": "FocusProbe", "pre_delay": 0, "post_delay": 0}}
    for key in variants:
        nodes[key] = {"recognition": "DirectHit", "action": "DoNothing", "focus": {"Node.Recognition.Succeeded": "original"}}
    assert resource.override_pipeline(nodes)
    frame = np.zeros((1280, 720, 3), dtype=np.uint8)

    class ReadOnlyController(CustomController):
        def connect(self): return True
        def request_uuid(self): return "focus-read-only"
        def screencap(self): return frame.copy()
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

    events = []

    class Sink(EventSink):
        def _on_raw_notification(self, handle, msg, details):
            if msg == "Node.Recognition.Succeeded":
                events.append({"message": msg, "details": details})

    class Probe(CustomAction):
        def run(self, context, argv):
            for key, override in variants.items():
                result = context.run_recognition(key, frame, {key: override})
                assert result and result.hit, key
                assert events and events[-1]["details"]["name"] == key, key
                expected_focus = override.get("focus", {"Node.Recognition.Succeeded": "original"})
                assert events[-1]["details"]["focus"] == expected_focus, key
                print(json.dumps({"variant": key, "hit": bool(result and result.hit), "event": events[-1] if events else None}, ensure_ascii=False), flush=True)
            return True

    assert resource.register_custom_action("FocusProbe", Probe())
    controller = ReadOnlyController()
    assert controller.post_connection().wait().status.succeeded
    tasker = Tasker()
    assert tasker.bind(resource, controller)
    assert tasker.add_context_sink(Sink())
    result = tasker.post_task("FocusProbe").wait().status
    assert result.succeeded
    print("FOCUS_OVERRIDE_ASSERTIONS_PASSED")


if __name__ == "__main__":
    main()

"""Native pipeline count regression with a synthetic, no-input controller.

This verifies routing/max_hit only. It is not a live game cultivation test.
"""
import argparse
import json
from collections import Counter
from pathlib import Path

import numpy as np
from maa.library import Library


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("portable", type=Path)
    root = parser.parse_args().portable.resolve()
    Library.open(root / "runtimes/win-x64/native")
    from maa.resource import Resource
    from maa.tasker import Tasker
    from maa.controller import CustomController
    from maa.custom_action import CustomAction

    class NoInputController(CustomController):
        def connect(self): return True
        def request_uuid(self): return "count-no-game-input"
        def screencap(self): return np.zeros((1279,720,3), np.uint8)
        def get_features(self): return 0
        def start_app(self, *args): raise AssertionError("unexpected input")
        def stop_app(self, *args): raise AssertionError("unexpected input")
        def click(self, *args): raise AssertionError("unexpected input")
        def swipe(self, *args): raise AssertionError("unexpected input")
        def touch_down(self, *args): raise AssertionError("unexpected input")
        def touch_move(self, *args): raise AssertionError("unexpected input")
        def touch_up(self, *args): raise AssertionError("unexpected input")
        def click_key(self, *args): raise AssertionError("unexpected input")
        def input_text(self, *args): raise AssertionError("unexpected input")
        def key_down(self, *args): raise AssertionError("unexpected input")
        def key_up(self, *args): raise AssertionError("unexpected input")

    counts = Counter()

    class Mark(CustomAction):
        def run(self, context, argv):
            counts[argv.node_name] += 1
            if settle == "ProduceFinished":
                # The finish control disappears after its click, reappearing
                # only in the next round. Simulate that, not an endless button.
                if argv.node_name == "ProduceLoop":
                    context.override_pipeline({"ProduceFinished": {"enabled": True}})
                elif argv.node_name == "ProduceFinished":
                    context.override_pipeline({"ProduceFinished": {"enabled": False}})
            if counts[argv.node_name] > 7:
                context.tasker.post_stop()
                return False
            return True

    controller = NoInputController()
    assert controller.post_connection().wait().status.succeeded
    resource = Resource()
    assert resource.post_bundle(root / "resource/base").wait().status.succeeded
    source = json.loads((Path(__file__).resolve().parents[1] / "assets/resource/base/pipeline/Produce.json").read_text(encoding="utf-8"))
    assert resource.override_pipeline(source)
    assert resource.register_custom_action("CountTestMark", Mark())
    tasker = Tasker()
    assert tasker.bind(resource, controller)

    for settle in ("ProduceEnd", "ProduceGeneration", "ProduceFinished"):
        for limit in (1, 2, 6):
            counts.clear()
            override = {name.removeprefix("[JumpBack]"): {"enabled": False}
                        for name in source[settle]["next"] if name != "ProduceBackHome"}
            for name in ("ProduceLoop", settle, "ProduceBackHome"):
                override[name] = {"enabled": True, "recognition": "DirectHit", "action": "Custom",
                                  "custom_action": "CountTestMark", "pre_delay": 0, "post_delay": 0,
                                  "pre_wait_freezes": 0, "post_wait_freezes": 0, "timeout": 50,
                                  "rate_limit": 10}
            override["ProduceLoop"].update(max_hit=limit, next=[settle])
            # Do not alter settlement -> home -> loop links: these are under test.
            tasker.post_task("ProduceLoop", override).wait()
            assert counts["ProduceLoop"] == counts["ProduceBackHome"] == limit, (settle, limit, counts)
            assert not counts["ProduceHomeFlag"] and not counts["ProduceStart"], counts
            print(json.dumps({"settlement": settle, "limit": limit, "rounds": counts["ProduceLoop"],
                              "settlements": counts["ProduceBackHome"], "gameInput": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()

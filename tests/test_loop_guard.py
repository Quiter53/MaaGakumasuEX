"""子流程防卡死守卫：兜底点击/自循环超过时限后结束子流程，交回上层。"""

import sys
import json
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
from custom.reco.utils import ProduceLoopGuard

PIPELINE = ROOT / "assets/resource/base/pipeline"
GUARDED = {
    "Produce.json": ("ProduceChooseStrengthenClick", "ProduceShoppingDecide"),
    "ProduceNIA.json": ("ProduceWorkEntry", "ProduceWorkFlag", "ProduceShoppingEntry", "ProduceGuideEntry"),
}


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class LoopGuardTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        ProduceLoopGuard.reset()
        self._old_clock = ProduceLoopGuard.clock
        ProduceLoopGuard.clock = staticmethod(self.clock)

    def tearDown(self):
        ProduceLoopGuard.clock = self._old_clock
        ProduceLoopGuard.reset()

    def analyze(self, seconds=60):
        argv = SimpleNamespace(custom_recognition_param=json.dumps({"seconds": seconds}), node_name="ProduceLoopGuard")
        return ProduceLoopGuard().analyze(Mock(), argv)

    def test_continuous_fallback_exits_after_limit_then_restarts(self):
        for _ in range(59):
            self.assertIsNone(self.analyze())
            self.clock.now += 1
        self.assertIsNone(self.analyze())
        self.clock.now += 1
        self.assertIsNotNone(self.analyze())
        # 命中后重新计时，下一次子流程不会立刻退出。
        self.clock.now += 1
        self.assertIsNone(self.analyze())

    def test_idle_gap_starts_a_new_streak(self):
        self.assertIsNone(self.analyze(seconds=10))
        self.clock.now += 9
        self.assertIsNone(self.analyze(seconds=10))
        self.clock.now += ProduceLoopGuard.IDLE_RESET + 1
        self.assertIsNone(self.analyze(seconds=10))
        self.clock.now += 9
        self.assertIsNone(self.analyze(seconds=10))

    def test_guard_precedes_fallback_click_and_self_loop(self):
        nodes = json.loads((PIPELINE / "ProduceUtils.json").read_text(encoding="utf-8"))
        guard = nodes["ProduceLoopGuard"]
        self.assertEqual([], guard["next"])
        self.assertEqual("DoNothing", guard["action"]["type"])
        for file, names in GUARDED.items():
            data = json.loads((PIPELINE / file).read_text(encoding="utf-8"))
            for name in names:
                routes = data[name]["next"]
                with self.subTest(node=name):
                    fallback = [i for i, route in enumerate(routes) if route in ("[JumpBack]Click_1", name)]
                    self.assertLess(routes.index("ProduceLoopGuard"), min(fallback))

    def test_jumpback_custom_actions_yield_on_error(self):
        nodes = {}
        for path in PIPELINE.glob("Produce*.json"):
            nodes.update(json.loads(path.read_text(encoding="utf-8")))
        for name in ("ProduceSkipED", "ProduceSkipShow", "ProduceSkipChatFlag", "ProduceFollow"):
            self.assertEqual(["ProduceActionYield"], nodes[name]["on_error"])


if __name__ == "__main__":
    unittest.main()

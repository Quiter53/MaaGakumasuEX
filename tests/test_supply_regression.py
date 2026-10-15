"""Supply animations must not intercept the schedule or reward strategy."""

import json
import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

from custom.action.produce import ProduceHIFChooseSupplyAuto


class SupplyRegressionTest(unittest.TestCase):
    def setUp(self):
        self.context = Mock()
        self.context.tasker.stopping = False
        self.action = ProduceHIFChooseSupplyAuto()

    def test_schedule_supply_clicks_once(self):
        result = SimpleNamespace(text="差し入れ", box=[390, 1063, 102, 28])
        with patch.object(self.action, "_ocr", return_value=[result]) as ocr:
            self.assertTrue(self.action.run(self.context, None))
        self.assertEqual((["^差し入れ$"], [0, 800, 720, 350]), ocr.call_args.args[-2:])
        self.context.tasker.controller.post_click.assert_called_once_with(441, 1077)

    def test_animation_effect_is_never_a_supply_button(self):
        # Exact text and coordinates are from the 11:45:43 regression log.
        effect = SimpleNamespace(text="活動支給・差し入れ選択時、", box=[122, 462, 311, 29])
        with patch.object(self.action, "_ocr", return_value=[effect]):
            self.assertTrue(self.action.run(self.context, None))
        self.context.tasker.controller.post_click.assert_not_called()

    def test_title_or_effect_outside_schedule_is_not_clicked(self):
        title = SimpleNamespace(text="差し入れ", box=[32, 45, 100, 28])
        with patch.object(self.action, "_ocr", return_value=[title]):
            self.assertTrue(self.action.run(self.context, None))
        self.context.tasker.controller.post_click.assert_not_called()

    def test_transition_missing_choice_is_quiet(self):
        with patch.object(self.action, "_ocr", return_value=[]), \
                patch("custom.action.produce.logger") as logger:
            self.assertTrue(self.action.run(self.context, None))
        self.context.tasker.controller.post_click.assert_not_called()
        logger.warning.assert_not_called()
        logger.success.assert_not_called()

    def test_stopping_does_not_capture_or_click(self):
        self.context.tasker.stopping = True
        self.assertTrue(self.action.run(self.context, None))
        self.context.tasker.controller.post_screencap.assert_not_called()
        self.context.tasker.controller.post_click.assert_not_called()

    def test_supply_pipeline_only_recognizes_schedule_label(self):
        path = ROOT / "assets/resource/base/pipeline/ProduceHIF.json"
        node = json.loads(path.read_text(encoding="utf-8"))["ProduceHIFSupplyFlag"]
        param = node["recognition"]["param"]
        self.assertEqual([0, 800, 720, 350], param["roi"])
        self.assertTrue(any(re.search(pattern, "差し入れ") for pattern in param["expected"]))
        self.assertFalse(any(re.search(pattern, "活動支給・差し入れ選択時、")
                             for pattern in param["expected"]))
        self.assertNotIn("Node.Action.Succeeded", node.get("focus", {}))

    def test_reward_strategy_precedes_skip_without_removed_priority_option(self):
        pipeline = json.loads((ROOT/'assets/resource/base/pipeline/Produce.json').read_text('utf8'))
        sequence = pipeline['ProduceChooseGetFlag']['next']
        self.assertLess(sequence.index('ProduceChooseRewardStrategy'), sequence.index('ProduceChooseSkip'))
        self.assertEqual(['[JumpBack]ProduceDisconnectError', '[JumpBack]ProduceDisconnectTitle',
                          '[JumpBack]ProduceDisconnectHome'], sequence[:3])
        self.assertEqual('ProduceChooseRewardAuto', pipeline['ProduceChooseRewardStrategy']['action']['param']['custom_action'])
        found = []

        def inspect(value):
            if isinstance(value, dict):
                override = value.get("pipeline_override", {}).get("ProduceChooseGetFlag", {})
                if "next" in override:
                    sequence = override["next"]
                    found.append(sequence)
                    self.assertIn("ProduceChooseRewardStrategy", sequence)
                    self.assertLess(sequence.index("ProduceChooseRewardStrategy"),
                                    sequence.index("ProduceChooseSkip"))
                    self.assertEqual(["[JumpBack]ProduceDisconnectError",
                                      "[JumpBack]ProduceDisconnectTitle",
                                      "[JumpBack]ProduceDisconnectHome"], sequence[:3])
                for item in value.values():
                    inspect(item)
            elif isinstance(value, list):
                for item in value:
                    inspect(item)

        tasks = {}
        for filename in ("produce.json", "produce_cn.json"):
            tasks[filename] = json.loads((ROOT / "assets/tasks" / filename).read_text(encoding="utf-8"))
            inspect(tasks[filename])
        for task in tasks.values():
            self.assertNotIn('卡片选择优先级', task['task'][0]['option'])
            self.assertNotIn('卡片选择优先级', task.get('option', {}))



if __name__ == "__main__":
    unittest.main()

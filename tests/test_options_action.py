"""Exercise the action entry, including MaaFramework's omitted-parameter null."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
from custom.produce.options import ProduceChooseOptionsAuto


class OptionsActionTest(unittest.TestCase):
    def setup_action(self):
        action = ProduceChooseOptionsAuto()
        context = Mock()
        context.get_node_data.return_value = {
            "action": {"param": {"custom_action_param": {"first": "Vi", "second": "Da"}}}}
        context.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value = np.zeros((1280, 720, 3), np.uint8)
        action._get_current_score = Mock(return_value={"Vo": 280, "Da": 514, "Vi": 925, "max": 2600})
        action._get_available_options = Mock(return_value=[{"Da": [53, 763, 614, 80]}])
        action._double_click = Mock()
        return action, context

    def test_normal_entry_reaches_click_with_null_and_other_non_object_params(self):
        for param in ["null", None, "", " ", "{}", "[]", "false", "1", "broken", {},
                      {"consecutive_failure_fallback": False}, '{"consecutive_failure_fallback":"false"}',
                      {"consecutive_failure_fallback": "false"}]:
            with self.subTest(param=param):
                action, context = self.setup_action()
                with patch("random.random", return_value=.9):
                    action.run(context, SimpleNamespace(custom_action_param=param))
                action._get_available_options.assert_called_once()
                action._double_click.assert_called_once_with(context, 360, 803)

    def test_explicit_fallback_rechecks_rows_and_clicks(self):
        action, context = self.setup_action()
        for param in ('{"consecutive_failure_fallback":true}', {"consecutive_failure_fallback": True}):
            with self.subTest(param=param):
                action, context = self.setup_action()
                with patch("custom.reco.options_fallback.read_fallback_options", return_value=[("Da", [53, 763, 614, 80], .895)]), patch(
                    "random.random", return_value=.9
                ):
                    action.run(context, SimpleNamespace(custom_action_param=param))
                action._get_available_options.assert_not_called()
                action._double_click.assert_called_once_with(context, 360, 803)

    def test_changed_fallback_scene_does_not_click(self):
        action, context = self.setup_action()
        with patch("custom.reco.options_fallback.read_fallback_options", return_value=[]):
            action.run(context, SimpleNamespace(custom_action_param='{"consecutive_failure_fallback":true}'))
        action._double_click.assert_not_called()

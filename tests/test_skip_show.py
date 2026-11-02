"""同步上游 b3da0cb：演出跳过仅在横屏时点击，识别节点不再附带点击副作用。"""

import sys
import json
import re
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
from custom.reco.produce import ProduceShowEnd, ProduceShowStart, ProduceCardsFlagAuto
from custom.produce.lifecycle import ProduceSkipShowAuto


def fake_context(frames):
    ctx = Mock()
    ctx.tasker.stopping = False
    images = iter(frames)
    ctx.tasker.controller.post_screencap.return_value.wait.return_value.get.side_effect = lambda: next(images)
    ctx.tasker.controller.post_click.return_value.wait.return_value.succeeded = True
    return ctx


class SkipShowTest(unittest.TestCase):
    def test_landscape_clicks_inside_current_frame(self):
        landscape = np.zeros((720, 1280, 3), np.uint8)
        ctx = fake_context([landscape] * 3)
        with patch("custom.produce.lifecycle.time.sleep"):
            self.assertTrue(ProduceSkipShowAuto().run(ctx, SimpleNamespace()))
        clicks = [call.args for call in ctx.tasker.controller.post_click.call_args_list]
        self.assertEqual([(1192, 80), (1192, 80), (20, 20)], clicks)

    def test_portrait_frame_never_clicks(self):
        ctx = fake_context([np.zeros((1280, 720, 3), np.uint8)])
        self.assertTrue(ProduceSkipShowAuto().run(ctx, SimpleNamespace()))
        ctx.tasker.controller.post_click.assert_not_called()

    def test_scene_probes_never_change_the_page_even_with_legacy_tap_parameters(self):
        for reco in (ProduceShowStart, ProduceShowEnd, ProduceCardsFlagAuto):
            for shape in ((720, 1280), (1280, 720)):
                for tap in (False, True):
                    with self.subTest(reco=reco.__name__, shape=shape, tap=tap):
                        ctx = Mock()
                        ctx.run_recognition.return_value = SimpleNamespace(hit=False)
                        args = SimpleNamespace(image=np.zeros((*shape, 3), np.uint8), custom_recognition_param=json.dumps({"tap": tap}))
                        reco().analyze(ctx, args)
                        ctx.run_action.assert_not_called()
                        ctx.tasker.controller.post_click.assert_not_called()

    def test_scene_probes_leave_animation_advancement_to_pipeline_actions(self):
        produce = json.loads((ROOT / "assets/resource/base/pipeline/Produce.json").read_text(encoding="utf-8"))
        for name in ("ProduceExit", "ProduceEnd", "ProduceSkipED"):
            self.assertNotIn("custom_recognition_param", produce[name]["recognition"]["param"])
        self.assertIn("[JumpBack]Click_1", produce["ProduceEnd"]["next"])
        self.assertIn("[JumpBack]ProduceSkipShow", produce["ProduceExit"]["next"])
        self.assertIn("ProduceGeneration", produce["ProduceGeneration"]["next"])
        pattern = produce["ProduceGeneration"]["recognition"]["param"]["expected"]
        self.assertIsNotNone(re.search(pattern, "生成"))
        self.assertIsNone(re.search(pattern, "再生成"))
        self.assertNotIn("Node.Recognition.Succeeded", produce["ProduceGeneration"]["focus"])

    def test_pipeline_uses_shared_skip_action(self):
        produce = json.loads((ROOT / "assets/resource/base/pipeline/Produce.json").read_text(encoding="utf-8"))
        utils = json.loads((ROOT / "assets/resource/base/pipeline/ProduceUtils.json").read_text(encoding="utf-8"))
        for node in (produce["ProduceSkipED"], utils["ProduceSkipShow"]):
            self.assertEqual("ProduceSkipShowAuto", node["action"]["param"]["custom_action"])


if __name__ == "__main__":
    unittest.main()

"""同步上游 b3da0cb：演出跳过仅在横屏时点击，识别节点不再附带点击副作用。"""

import sys
import json
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
from custom.reco.produce import ProduceShowEnd, ProduceShowStart
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

    def test_show_recognitions_have_no_click_side_effect(self):
        ctx = Mock()
        for reco, image in ((ProduceShowStart, (720, 1280)), (ProduceShowEnd, (1280, 720))):
            result = reco().analyze(ctx, SimpleNamespace(image=np.zeros((*image, 3), np.uint8)))
            self.assertIsNotNone(result.box)
        ctx.run_action.assert_not_called()

    def test_pipeline_uses_shared_skip_action(self):
        produce = json.loads((ROOT / "assets/resource/base/pipeline/Produce.json").read_text(encoding="utf-8"))
        utils = json.loads((ROOT / "assets/resource/base/pipeline/ProduceUtils.json").read_text(encoding="utf-8"))
        for node in (produce["ProduceSkipED"], utils["ProduceSkipShow"]):
            self.assertEqual("ProduceSkipShowAuto", node["action"]["param"]["custom_action"])


if __name__ == "__main__":
    unittest.main()

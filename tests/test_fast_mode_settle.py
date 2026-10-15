"""Both scan modes wait for stable frames with bounded, click-free retries."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'agent'))
from custom.action.produce import ProduceHIFChangeSourceAuto, ProduceShoppingAuto, ProduceChooseRewardAuto


class FastSettleTest(unittest.TestCase):
    def observe(self, fast, stable_after):
        action, ctx = ProduceHIFChangeSourceAuto(), Mock()
        action._removal_search_policy = (set(), set(), set(), not fast)
        ctx.tasker.stopping = False
        ctx.run_recognition.return_value = SimpleNamespace(hit=True)
        image = np.zeros((1280,720,3), np.uint8)
        ctx.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value = image
        results = [False]*stable_after + [True]
        with patch('custom.action.produce.time.sleep'), \
             patch('custom.action.produce._detect_disconnect_screen', return_value=False), \
             patch.object(ProduceShoppingAuto, '_delete_card_boxes', return_value=[[82,398,116,116]]), \
             patch.object(ProduceChooseRewardAuto, '_scene_signature', return_value='signature'), \
             patch.object(action, '_source_frames_stable', side_effect=lambda *args: results.pop(0) if results else False) as stable, \
             patch.object(ProduceShoppingAuto, '_recognize_delete_cards', return_value=['evaluated']) as evaluate:
            result = action._page(ctx, change=False)
        ctx.tasker.controller.post_click.assert_not_called()
        ctx.tasker.controller.post_swipe.assert_not_called()
        return result, stable.call_count, evaluate.call_count

    def test_fast_waits_for_reflow_without_lowering_stability(self):
        result, frames, evaluations = self.observe(True, 6)
        self.assertIsNotNone(result)
        self.assertEqual(7, frames)
        self.assertEqual(1, evaluations)

    def test_strict_waits_for_reflow_without_lowering_stability(self):
        result, frames, evaluations = self.observe(False, 6)
        self.assertIsNotNone(result)
        self.assertLessEqual(frames, 15)
        self.assertEqual(1, evaluations)

    def test_fast_retry_bounded_and_never_evaluates_unstable_cards(self):
        for fast in (False, True):
            with self.subTest(fast=fast):
                result, frames, evaluations = self.observe(fast, 20)
                self.assertIsNone(result)
                self.assertLessEqual(frames, 15)
                self.assertEqual(0, evaluations)

    def test_already_stable_fast_page_gets_no_extra_wait(self):
        _, frames, evaluations = self.observe(True, 1)
        self.assertEqual(2, frames)
        self.assertEqual(1, evaluations)


if __name__ == '__main__':
    unittest.main()

"""基于画面变化的等待：画面稳定即返回帧，持续变化时不超过上限，停止任务立即返回。"""

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
from custom import frame_wait


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def context(frames):
    ctx = Mock()
    ctx.tasker.stopping = False
    ctx.tasker.controller.post_screencap.return_value.wait.return_value.get.side_effect = frames
    return ctx


class WaitSettledTest(unittest.TestCase):
    def run_wait(self, frames, **kwargs):
        clock = Clock()
        with patch.object(frame_wait.time, "monotonic", clock.monotonic), patch.object(frame_wait.time, "sleep", clock.sleep):
            ctx = context(frames)
            return frame_wait.wait_settled(ctx, **kwargs), clock.now, ctx

    def test_returns_first_stable_frame_without_extra_screenshot(self):
        moving = np.zeros((1280, 720, 3), np.uint8)
        still = np.full((1280, 720, 3), 90, np.uint8)
        frame, elapsed, ctx = self.run_wait([moving, still, still.copy(), still], min_wait=0.3, max_wait=1.0)
        self.assertTrue(np.array_equal(still, frame))
        self.assertEqual(3, ctx.tasker.controller.post_screencap.call_count)
        self.assertLess(elapsed, 1.0)

    def test_keeps_upper_bound_while_screen_moves(self):
        frames = [np.full((1280, 720, 3), (i * 20) % 250, np.uint8) for i in range(30)]
        frame, elapsed, _ = self.run_wait(frames, min_wait=0.2, max_wait=1.0, interval=0.12)
        self.assertIsNotNone(frame)
        self.assertLess(elapsed, 1.0 + 0.12 + 1e-6)

    def test_stopping_returns_none(self):
        ctx = context([np.zeros((1280, 720, 3), np.uint8)])
        ctx.tasker.stopping = True
        with patch.object(frame_wait.time, "sleep"):
            self.assertIsNone(frame_wait.wait_settled(ctx))
        ctx.tasker.controller.post_screencap.assert_not_called()


if __name__ == "__main__":
    unittest.main()

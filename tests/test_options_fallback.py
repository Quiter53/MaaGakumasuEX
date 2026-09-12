import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
from custom.reco.options_fallback import FailureGate, validated_rows, ProduceOptionsFailureFallback


ROWS = [(name, [53, y, 614, 80], score) for name, y, score in
        [("Vi", 661, .898), ("Vo", 763, .895), ("Da", 864, .845)]]


class OptionsFallbackTest(unittest.TestCase):
    def test_normal_recognition_wins_and_resets_failure_history(self):
        recognizer = ProduceOptionsFailureFallback()
        recognizer.gate.count = 4
        context = Mock()
        context.run_recognition.return_value = SimpleNamespace(hit=True)
        argv = SimpleNamespace(image=np.zeros((1280, 720, 3), np.uint8))
        with patch("custom.reco.options_fallback.read_fallback_options") as relaxed:
            self.assertIsNone(recognizer.analyze(context, argv))
            relaxed.assert_not_called()
        self.assertEqual(0, recognizer.gate.count)

    def test_consecutive_misses_trigger_once_then_start_over(self):
        recognizer = ProduceOptionsFailureFallback()
        recognizer.gate.reset()
        context = Mock()
        context.run_recognition.return_value = SimpleNamespace(
            hit=False, all_results=[SimpleNamespace(box=b, score=s) for _, b, s in ROWS])
        argv = SimpleNamespace(image=np.zeros((1280, 720, 3), np.uint8))
        with patch("custom.reco.options_fallback.read_fallback_options", return_value=ROWS), patch(
            "custom.reco.options_fallback.time.monotonic", side_effect=[0, 1.5, 3, 4.5, 6, 7.5]
        ):
            for _ in range(4):
                self.assertIsNone(recognizer.analyze(context, argv))
            self.assertIsNotNone(recognizer.analyze(context, argv))
            self.assertIsNone(recognizer.analyze(context, argv))

    def test_recorded_three_rows_are_valid(self):
        self.assertEqual(ROWS, validated_rows(ROWS))

    def test_incomplete_square_or_ambiguous_results_rejected(self):
        self.assertFalse(validated_rows(ROWS[:2]))
        self.assertFalse(validated_rows(ROWS + [("null", [53, 661, 614, 80], .88)]))
        self.assertFalse(validated_rows(ROWS + [("null", [53, 864, 614, 80], .83)]))
        self.assertFalse(validated_rows([(n, [x, y, 126, 126], s) for n, (x, y, w, h), s in ROWS]))

    def test_requires_count_and_elapsed_time(self):
        gate = FailureGate()
        image = np.zeros((1280, 720, 3), np.uint8)
        for t in [0, 1, 2, 3, 4, 5]:
            self.assertFalse(gate.observe(ROWS, image, t))
        self.assertTrue(gate.observe(ROWS, image, 6))

    def test_scene_change_gap_and_explicit_reset_clear_history(self):
        gate = FailureGate()
        image = np.zeros((1280, 720, 3), np.uint8)
        for t in range(6):
            gate.observe(ROWS, image, t)
        self.assertFalse(gate.observe(ROWS, image + 20, 6))
        self.assertEqual(1, gate.count)
        self.assertFalse(gate.observe(ROWS, image + 20, 20))
        self.assertEqual(1, gate.count)
        gate.reset()
        self.assertEqual(0, gate.count)

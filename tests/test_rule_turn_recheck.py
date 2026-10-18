"""Regress the reported allowed-card -> missing bonus OCR -> reselect loop."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent'))
from custom.battle_drinks import CardNameReader, TurnReader
from custom.produce.battle import ProduceCardsAuto


def row(text, score):
    return NS(text=text, score=score)


class BonusCropTests(unittest.TestCase):
    def setUp(self):
        self.reader = TurnReader()
        self.ctx = Mock()
        self.image = np.zeros((1280, 720, 3), np.uint8)
        self.image[118:163,90:160] = 255
        self.mask = np.zeros((45, 70), np.uint8)
        self.mask[10:27, 16:44] = 1

    def test_logged_low_scores_use_bounded_recognition_without_lowering_gate(self):
        for score in (.839248, .773876, .863875, .892565, .817096):
            with self.subTest(score=score), patch('custom.battle_drinks.ocr', return_value=[row('+1', score)]):
                self.ctx.run_recognition.return_value = NS(hit=True, filtered_results=[row('+1', .99)])
                self.assertEqual(1, self.reader._read_bonus(self.ctx, self.image, self.mask))
                params = self.ctx.run_recognition.call_args.kwargs['pipeline_override']['ProduceRecognitionPoint']
                self.assertTrue(params['only_rec'])
                self.assertEqual([103, 125, 34, 23], params['roi'])

    def test_fallback_still_rejects_low_score_ambiguous_or_non_bonus_text(self):
        for rows in ([row('+1', .89)], [row('1', .99)], [row('+0', .99)],
                     [row('+1', .99), row('+2', .99)], []):
            with self.subTest(rows=rows), patch('custom.battle_drinks.ocr', return_value=[row('+1', .83)]):
                self.ctx.run_recognition.return_value = NS(hit=True, filtered_results=rows)
                self.assertIsNone(self.reader._read_bonus(self.ctx, self.image, self.mask))

    def test_confident_bonus_and_absent_bonus_need_no_extra_ocr(self):
        with patch('custom.battle_drinks.ocr', return_value=[row('+2', .96)]) as ocr:
            self.assertEqual(2, self.reader._read_bonus(self.ctx, self.image, self.mask))
            self.assertEqual(0, self.reader._read_bonus(self.ctx, self.image, np.zeros_like(self.mask)))
            ocr.assert_called_once()
        self.ctx.run_recognition.assert_not_called()

    def test_cyan_stage_pixels_outside_white_pill_are_excluded(self):
        self.image[118:163,90:160] = 0
        self.image[122:158,96:150] = 255
        self.mask[:,65:] = 1
        with patch('custom.battle_drinks.ocr', return_value=[row('+1', .83)]):
            self.ctx.run_recognition.return_value = NS(hit=True, filtered_results=[row('+1', .99)])
            self.assertEqual(1, self.reader._read_bonus(self.ctx, self.image, self.mask))
        roi = self.ctx.run_recognition.call_args.kwargs['pipeline_override']['ProduceRecognitionPoint']['roi']
        self.assertEqual([103,125,34,23], roi)

    def test_cyan_background_without_white_pill_cannot_authorize_bonus(self):
        self.image[:] = 0
        with patch('custom.battle_drinks.ocr', return_value=[row('+1', .83)]):
            self.assertIsNone(self.reader._read_bonus(self.ctx, self.image, self.mask))
        self.ctx.run_recognition.assert_not_called()

    def test_new_total_still_requires_two_readings(self):
        self.reader._anchor = True
        with patch('custom.battle_drinks.ocr', return_value=[row('5', .99)]), \
             patch.object(self.reader, '_read_bonus', side_effect=[1, 1, 3, 3]):
            self.assertIsNone(self.reader.read(self.ctx, self.image))
            self.assertEqual(6, self.reader.read(self.ctx, self.image))
            changed = self.image.copy()
            changed[58:123, 20:95] = 255
            self.assertIsNone(self.reader.read(self.ctx, changed))
            self.assertEqual(8, self.reader.read(self.ctx, changed))


class RuleTurnRecheckTests(unittest.TestCase):
    def setUp(self):
        self.action = ProduceCardsAuto()
        self.image = np.zeros((1280, 720, 3), np.uint8)
        self.box = [20, 850, 185, 250]
        self.ctx = Mock()
        self.ctx.tasker.stopping = False
        self.ctx.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value = self.image
        self.action._drinks = NS(turns=Mock(), pending_card=None, opening=False, inspection_streak=0,
                                 needs_card_name=Mock(return_value=False))

    def play(self, readings):
        self.action._selected_identity = (tuple(self.box), 'せのびの魔法')
        self.action._drinks.turns.read.side_effect = readings
        with patch.object(CardNameReader, 'locate_raised', return_value=self.box), \
             patch.object(CardNameReader, 'same_selection', return_value=True), \
             patch.object(self.action, '_observe_selected_card', return_value=(self.image, self.box, None)), \
             patch.object(self.action, '_drink_step', return_value=False), \
             patch.object(self.action, '_confirm_card_submission', return_value=True), \
             patch.object(self.action, '_wait_until_playable', return_value=True), \
             patch('custom.produce.shared._detect_disconnect_screen', return_value=False), \
             patch('time.sleep'):
            return self.action._play_a_card(self.ctx, self.box)

    def test_lost_bonus_before_first_guard_recovers_and_plays_once(self):
        self.assertTrue(self.play([None, None, 6, 6]))
        self.ctx.tasker.controller.post_click.assert_called_once()
        self.assertEqual(0, self.action._held_reselections)

    def test_lost_bonus_at_final_click_recovers_without_reselecting(self):
        self.assertTrue(self.play([6, None, None, 6]))
        self.ctx.tasker.controller.post_click.assert_called_once()

    def test_recovered_turn_extension_blocks_play(self):
        self.assertTrue(self.play([6, None, None, 8]))
        self.ctx.tasker.controller.post_click.assert_not_called()
        self.assertEqual('せのびの魔法', self.action._held_selection[0])

    def test_unresolved_turns_never_reuse_old_six_or_count_as_held(self):
        self.assertTrue(self.play([6, None, None, None, None]))
        self.ctx.tasker.controller.post_click.assert_not_called()
        self.assertEqual(0, getattr(self.action, '_held_reselections', 0))

    def test_selection_change_aborts_turn_retry(self):
        with patch.object(CardNameReader, 'locate_raised', return_value=[350, 850, 185, 250]), \
             patch('custom.produce.shared._detect_disconnect_screen', return_value=False), \
             patch('time.sleep'):
            self.assertIsNone(self.action._resolve_selected_rule_turns(self.ctx, self.image, self.box, 'せのびの魔法', None))
        self.action._drinks.turns.read.assert_not_called()

    def test_stop_and_disconnect_cannot_trigger_a_click(self):
        self.ctx.tasker.stopping = True
        self.assertIsNone(self.action._resolve_selected_rule_turns(self.ctx, self.image, self.box, 'せのびの魔法', None))
        self.ctx.tasker.controller.post_screencap.assert_not_called()
        self.ctx.tasker.stopping = False
        with patch('custom.produce.shared._detect_disconnect_screen', return_value=True), \
             patch('time.sleep'):
            self.assertIsNone(self.action._resolve_selected_rule_turns(self.ctx, self.image, self.box, 'せのびの魔法', None))
        self.assertTrue(self.action._disconnect_detected)
        self.ctx.tasker.controller.post_click.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)

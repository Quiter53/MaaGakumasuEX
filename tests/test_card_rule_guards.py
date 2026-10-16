import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'agent'))
from custom.produce.battle import ProduceCardsAuto
from custom.battle_drinks import CardNameReader, TurnReader


class FinisherTurnGuardTests(unittest.TestCase):
    def setUp(self):
        self.action = ProduceCardsAuto()
        self.frame = np.zeros((1280, 720, 3), np.uint8)
        self.ctx = Mock()
        self.ctx.tasker.stopping = False
        self.ctx.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value = self.frame
        self.session = NS(turns=Mock(), art=Mock(), names=Mock(), pending_card=None,
                          needs_card_name=Mock(return_value=False), opening=False, inspection_streak=0)
        self.session.turns.read.return_value = 8
        self.action._drinks = self.session
        self.action._drink_error = False
        self.cards = [dict(box=[40 + i * 200, 884, 189, 249], label='suggestions', detector_score=.99)
                      for i in range(3)]
        self.names = {40: '届いて！', 240: 'せのびの魔法', 440: 'ゆめみごこち'}
        self.session.art.read.side_effect = lambda image, box, boxes: NS(name=self.names[box[0]], mode='full')

    def choose(self, turns, cards=None):
        self.session.turns.read.return_value = turns
        rarity = NS(rarity='SSR', confidence=1, best_distance=0, margin=1,
                    distances={}, signature=[0,0,0], channel_order='test')
        with patch('custom.produce.battle.classify_rarity', return_value=rarity), \
             patch.object(self.action, '_extra_action_status', return_value=('yes', 1, [])), \
             patch.object(self.action, '_apply_card_name_fallback'):
            return self.action._choose_card(self.ctx, self.frame, self.cards if cards is None else cards)

    def test_exact_boundaries_upgrades_and_punctuation(self):
        for name, limit in [('届いて！', 2), ('届いて!+', 2), ('届いて！＋', 2),
                            ('せのびの魔法', 7), ('せのびの魔法＋', 7)]:
            for turns in [None, 0, -1, 1, 2, 3, 7, 8, 12]:
                with self.subTest(name=name, turns=turns):
                    self.assertEqual(not (turns is not None and 1 <= turns <= limit),
                                     self.action._held_until_late(name, turns))
        for turns in [None, 0, 1, 8, 12]:
            self.assertFalse(self.action._held_until_late('ゆめみごこち', turns))

    def test_recommended_extra_action_finishers_cannot_win_early(self):
        self.assertEqual(self.cards[2]['box'], self.choose(8)['box'])
        self.assertEqual(self.cards[1]['box'], self.choose(7)['box'])
        self.assertEqual(self.cards[1]['box'], self.choose(3)['box'])
        self.assertEqual(self.cards[0]['box'], self.choose(2)['box'])
        self.assertEqual(self.cards[0]['box'], self.choose(1)['box'])

    def test_unknown_rarity_fallback_does_not_bypass_hold(self):
        with patch('custom.produce.battle.rarity_priority', return_value=0):
            self.assertEqual(self.cards[2]['box'], self.choose(8)['box'])

    def test_drink_lock_does_not_override_window(self):
        self.session.pending_card = '届いて！'
        self.assertEqual(self.cards[2]['box'], self.choose(8)['box'])
        self.assertIsNone(self.session.pending_card)

    def test_only_held_cards_are_not_selected_even_in_fallback(self):
        self.assertIsNone(self.choose(8, self.cards[:2]))
        self.assertIsNone(self.choose(3, self.cards[:1]))
        self.assertIsNone(self.choose(None, self.cards[:2]))

    def test_extra_turns_can_close_window_again(self):
        self.assertIsNotNone(self.choose(2, self.cards[:1]))
        self.assertIsNone(self.choose(3, self.cards[:1]))
        self.assertIsNotNone(self.choose(7, self.cards[1:2]))
        self.assertIsNone(self.choose(8, self.cards[1:2]))

    def test_only_held_hand_skips_after_three_observations_without_play(self):
        with patch.object(self.action, '_choose_card', return_value=None), \
             patch.object(self.action, '_wait_until_playable'), \
             patch.object(self.action, '_play_a_card') as play:
            for count in range(3):
                self.assertTrue(self.action._play_candidate(self.ctx, self.frame, self.cards[:2]))
                self.assertEqual(1 if count == 2 else 0, self.ctx.run_task.call_count)
        play.assert_not_called()
        self.ctx.run_task.assert_called_once_with('ProduceRecognitionSkipRound')

    def test_unknown_turn_rechecks_then_skips_held_hand_without_stopping(self):
        self.session.turns.read.return_value = None
        with patch.object(self.action, '_choose_card', return_value=None), \
             patch.object(self.action, '_wait_until_playable'):
            for _ in range(3):
                self.assertTrue(self.action._play_candidate(self.ctx, self.frame, self.cards[:2]))
        self.ctx.run_task.assert_called_once_with('ProduceRecognitionSkipRound')
        self.ctx.tasker.controller.post_click.assert_not_called()

    def play(self, name, turns, final_turns=None, raised_name=None):
        final = turns if final_turns is None else final_turns
        self.session.turns.read.side_effect = [turns] + [final] * 5
        box = self.cards[0]['box']
        raised = [40, 820, 189, 249]
        self.action._selected_identity = (tuple(box), name)
        self.session.names.read_raised.return_value = dict(name=raised_name, box=raised) if raised_name else None
        with patch.object(CardNameReader, 'locate_raised', return_value=raised), \
             patch.object(self.action, '_observe_selected_card', return_value=(self.frame, raised)), \
             patch.object(self.action, '_drink_step', return_value=False) as drink, \
             patch.object(self.action, '_confirm_card_submission', return_value=True), \
             patch.object(self.action, '_wait_until_playable', return_value=True), \
             patch('custom.produce.shared._detect_disconnect_screen', return_value=False), \
             patch('time.sleep'):
            result = self.action._play_a_card(self.ctx, box)
        return result, drink

    def test_final_submission_guard_blocks_direct_bypass_before_drinking(self):
        for name, turns in [('届いて！', 3), ('せのびの魔法', 8)]:
            with self.subTest(name=name, turns=turns):
                result, drink = self.play(name, turns)
                self.assertTrue(result)
                self.assertEqual(name, self.action._held_selection[0])
                drink.assert_not_called()
                self.ctx.tasker.controller.post_click.assert_not_called()

    def test_unknown_turn_is_reobserved_without_marking_card_held(self):
        result, drink = self.play('せのびの魔法', None)
        self.assertTrue(result)
        self.assertIsNone(getattr(self.action, '_held_selection', None))
        self.assertEqual(0, getattr(self.action, '_held_reselections', 0))
        drink.assert_not_called()
        self.ctx.tasker.controller.post_click.assert_not_called()

    def test_selected_unknown_card_is_read_even_without_named_drinks(self):
        result, drink = self.play(None, 8, raised_name='せのびの魔法')
        self.assertTrue(result)
        self.assertEqual('せのびの魔法', self.action._held_selection[0])
        drink.assert_not_called()
        self.assertEqual(2, self.session.names.read_raised.call_count)
        self.ctx.tasker.controller.post_click.assert_not_called()

    def test_unconfirmed_identity_uses_original_play_without_named_drink(self):
        result, drink = self.play(None, 8)
        self.assertTrue(result)
        drink.assert_called_once_with(self.ctx, self.frame, card_name=None)
        self.ctx.tasker.controller.post_click.assert_called_once()

    def test_final_recheck_blocks_turn_extension(self):
        result, _ = self.play('届いて！', 2, final_turns=3)
        self.assertTrue(result)
        self.assertEqual('届いて！', self.action._held_selection[0])
        self.ctx.tasker.controller.post_click.assert_not_called()

    def test_cards_in_window_and_other_cards_keep_original_submission(self):
        for name, turns in [('届いて！', 2), ('届いて！', 1), ('せのびの魔法', 7),
                            ('せのびの魔法', 1), ('ゆめみごこち', 12)]:
            with self.subTest(name=name, turns=turns):
                self.ctx.tasker.controller.post_click.reset_mock()
                result, drink = self.play(name, turns)
                self.assertTrue(result)
                drink.assert_called_once()
                self.ctx.tasker.controller.post_click.assert_called_once()

    def test_turn_reader_returns_main_plus_bonus_for_guard(self):
        reader = TurnReader()
        reader._anchor = True
        with patch('custom.battle_drinks.ocr', return_value=[NS(text='2', score=.99)]), \
             patch.object(reader, '_read_bonus', return_value=1):
            self.assertIsNone(reader.read(self.ctx, self.frame))
            total = reader.read(self.ctx, self.frame)
        self.assertEqual(3, total)
        self.assertTrue(self.action._held_until_late('届いて！', total))

    def test_safe_known_card_is_preferred_over_unknown_recommended_card(self):
        self.names[40] = None
        self.cards[0]['label'] = 'suggestions'
        self.cards[2]['label'] = 'cards'
        self.session.names.read.return_value = None
        self.assertEqual(self.cards[2]['box'], self.choose(8)['box'])

    def test_unknown_name_extra_action_still_beats_known_nonextra(self):
        self.names[40] = None
        self.session.names.read.return_value = None
        rarity = NS(rarity='SR', confidence=1, best_distance=0, margin=1,
                    distances={}, signature=[0,0,0], channel_order='test')
        with patch('custom.produce.battle.classify_rarity',return_value=rarity), \
             patch.object(self.action,'_extra_action_status',side_effect=lambda ctx,img,box:
                          ('yes',.745,[]) if box[0]==40 else ('no',.1,[])), \
             patch.object(self.action,'_apply_card_name_fallback'):
            chosen=self.action._choose_card(self.ctx,self.frame,self.cards)
        self.assertEqual(self.cards[0]['box'],chosen['box'])
        self.assertEqual('yes',chosen['extra_action'])

    def test_held_raised_title_prevents_reselection_and_picks_other_card(self):
        held_box = [40, 820, 189, 249]
        self.cards[0]['box'] = held_box
        self.action._held_selection = ('届いて！', held_box)
        self.names[40] = None
        self.session.names.read.return_value = None
        with patch.object(CardNameReader, 'locate_raised', return_value=held_box):
            self.assertEqual(self.cards[2]['box'], self.choose(8)['box'])

    def test_held_observation_is_discarded_when_selection_disappears(self):
        self.action._held_selection = ('届いて！', [40, 820, 189, 249])
        self.names[40] = 'ゆめみごこち'
        with patch.object(CardNameReader, 'locate_raised', return_value=None):
            self.assertEqual(self.cards[0]['box'], self.choose(8)['box'])
        self.assertIsNone(self.action._held_selection)

    def test_repeated_held_reselections_are_bounded_and_keep_running(self):
        with patch.object(self.action, '_wait_until_playable'):
            for index in range(6):
                self.assertTrue(self.action._defer_held_card(self.ctx, '届いて！', self.cards[0]['box'], 8))
                self.assertEqual(int(index == 5), self.ctx.run_task.call_count)
        self.ctx.run_task.assert_called_once_with('ProduceRecognitionSkipRound')
        self.assertEqual(0, self.action._held_reselections)
        self.assertIsNone(self.action._held_selection)
        self.ctx.tasker.controller.post_click.assert_not_called()

    def test_held_hand_small_detector_jitter_does_not_loop_forever(self):
        with patch.object(self.action, '_choose_card', return_value=None), \
             patch.object(self.action, '_wait_until_playable'):
            for _ in range(3):
                self.cards[0]['box'][0] += 1
                self.assertTrue(self.action._play_candidate(self.ctx, self.frame, self.cards[:2]))
        self.ctx.run_task.assert_called_once()

    def test_user_stop_prevents_automatic_skip(self):
        self.ctx.tasker.stopping = True
        self.action._skip_held_round(self.ctx)
        self.ctx.run_task.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)

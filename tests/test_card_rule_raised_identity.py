"""A selected battle card must be re-localized, not read from an old strip."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent'))
from custom.battle_drinks import CardNameReader
from custom.produce.battle import ProduceCardsAuto


class RaisedBattleIdentityTests(unittest.TestCase):
    def setUp(self):
        self.reader = CardNameReader()
        self.image = np.zeros((1280, 720, 3), np.uint8)
        self.old = [142, 884, 189, 249]
        self.raised = [142, 820, 189, 249]

    def context(self, boxes):
        context = Mock()
        context.run_recognition.return_value = NS(hit=True, all_results=[
            NS(box=box, label='cards', score=.99) for box in boxes])
        return context

    def test_read_uses_fresh_raised_box_and_exact_title(self):
        context = self.context([self.raised, [266,884,189,249]])
        with patch.object(self.reader, '_line', return_value=[NS(text='輝くキミへ+', score=.99)]) as read:
            result = self.reader.read_raised(context, self.image, self.old)
        self.assertEqual(dict(name='輝くキミへ', box=self.raised), result)
        self.assertEqual([146,1035,181,31], read.call_args.args[2])

    def test_resting_or_other_raised_card_cannot_supply_name(self):
        for boxes in ([self.old], [[389,800,189,249]], [],
                      [[142,820,189,249],[146,770,220,290]]):
            with self.subTest(boxes=boxes), patch.object(self.reader, '_line') as read:
                self.assertIsNone(self.reader.read_raised(self.context(boxes), self.image, self.old))
                read.assert_not_called()

    def test_duplicate_classes_are_one_card_not_ambiguity(self):
        context = self.context([self.raised, [143,821,188,248]])
        with patch.object(self.reader, '_line', return_value=[NS(text='輝くキミへ+', score=.99)]):
            self.assertIsNotNone(self.reader.read_raised(context, self.image, self.old))

    def test_low_confidence_partial_and_multiple_names_rejected(self):
        for rows in ([NS(text='輝くキミへ+',score=.79)], [NS(text='輝くキミ',score=.99)],
                     [NS(text='輝くキミへ',score=.99), NS(text='思考の基本',score=.99)]):
            with self.subTest(rows=rows), patch.object(self.reader, '_line', return_value=rows):
                self.assertIsNone(self.reader.read_raised(self.context([self.raised]),self.image,self.old))

    def action_context(self, pending=None):
        action = ProduceCardsAuto()
        action._drinks = NS(pending_card=pending, names=Mock(), opening=False,
                            policy={'test':dict(timing='before_card')}, inspection_streak=0,
                            needs_card_name=Mock(return_value=True))
        action._drink_error = False
        context = Mock()
        context.tasker.stopping = False
        context.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value = self.image
        initial=[None]
        locate=patch.object(CardNameReader,'locate_raised',side_effect=lambda *a, detection=None: initial.pop() if initial else self.raised)
        locate.start();self.addCleanup(locate.stop)
        submitted=patch.object(action,'_confirm_card_submission',return_value=True)
        submitted.start();self.addCleanup(submitted.stop)
        return action, context

    def test_two_selected_reads_before_drink_trigger_and_new_click_box(self):
        action, context = self.action_context()
        action._drinks.names.read_raised.return_value = dict(name='輝くキミへ',box=self.raised)
        with patch('time.sleep'), \
             patch('custom.produce.shared._detect_disconnect_screen',return_value=False), \
             patch.object(action,'_drink_step',return_value=False) as drink, \
             patch.object(action,'_wait_until_playable',return_value=False):
            self.assertTrue(action._play_a_card(context,self.old))
        self.assertEqual(2,action._drinks.names.read_raised.call_count)
        self.assertEqual('輝くキミへ',drink.call_args.kwargs['card_name'])
        self.assertEqual([(236,1008),(236,944)], [call.args for call in context.tasker.controller.post_click.call_args_list])

    def test_unresolved_pending_card_clears_stale_pin_without_stopping_battle(self):
        action, context = self.action_context(pending='輝くキミへ')
        action._drinks.names.read_raised.return_value = dict(name='思考の基本',box=self.raised)
        with patch('time.sleep'), \
             patch('custom.produce.shared._detect_disconnect_screen',return_value=False), \
             patch.object(action,'_drink_step',return_value=False) as drink, \
             patch.object(action,'_wait_until_playable',return_value=False):
            self.assertTrue(action._play_a_card(context,self.old))
        self.assertEqual(2,context.tasker.controller.post_click.call_count)
        self.assertIsNone(drink.call_args.kwargs['card_name'])
        self.assertIsNone(action._drinks.pending_card)

    def test_known_current_frame_art_does_not_add_detail_ocr(self):
        action, context = self.action_context()
        action._selected_identity = (tuple(self.old),'輝くキミへ')
        with patch('time.sleep'), \
             patch('custom.produce.shared._detect_disconnect_screen',return_value=False), \
             patch.object(action,'_drink_step',return_value=False), \
             patch.object(action,'_wait_until_playable',return_value=False):
            self.assertTrue(action._play_a_card(context,self.old))
        action._drinks.names.read_raised.assert_not_called()

    def test_pending_weak_candidate_is_only_a_localization_hint(self):
        action, context = self.action_context(pending='輝くキミへ')
        action._identity_boxes = [self.old]
        action._drinks.art = Mock()
        action._drinks.art.read.return_value = NS(name=None,candidate='輝くキミへ',mode='local')
        candidate = dict(box=self.old,label='cards',detector_score=.9)
        with patch.object(action,'_extra_action_status',return_value=('no',0.,self.old)), \
             patch.object(action,'_apply_card_name_fallback'), \
             patch('custom.produce.shared._save_card_diagnostic'):
            selected = action._choose_card(context,self.image,[candidate])
        self.assertEqual(self.old,selected['box'])
        self.assertFalse(action._drink_error)
        self.assertEqual((tuple(self.old),None),action._selected_identity)
        context.tasker.controller.post_click.assert_not_called()

    def test_ambiguous_pending_hints_release_pin_to_current_card_priority(self):
        action, context = self.action_context(pending='輝くキミへ')
        action._drinks.art = Mock()
        action._drinks.art.read.return_value = NS(name=None,candidate='輝くキミへ',mode='local')
        candidates = [dict(box=box,label='cards',detector_score=.9)
                      for box in (self.old,[389,884,189,249])]
        action._identity_boxes = [c['box'] for c in candidates]
        with patch.object(action,'_extra_action_status',return_value=('no',0.,self.old)), \
             patch.object(action,'_apply_card_name_fallback'), \
             patch('custom.produce.shared._save_card_diagnostic'):
            action._choose_card(context,self.image,candidates)
        self.assertFalse(action._drink_error)
        self.assertIsNone(action._drinks.pending_card)
        self.assertTrue(action._skip_named_drink_once)
        context.tasker.controller.post_click.assert_not_called()


if __name__ == '__main__':
    unittest.main()

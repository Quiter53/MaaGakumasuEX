import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'agent'))
sys.path.append(r'C:\Users\SIMO\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\Lib\site-packages')
import numpy as np
from PIL import Image
from custom.produce.battle import ProduceCardsAuto
from custom.battle_drinks import CardNameReader

@unittest.skipUnless((Path(__file__).parent / 'fixtures/card_rules/stuck-game.png').exists(), 'Local replay frames not distributed')
class SelectedRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.image=np.asarray(Image.open(Path(__file__).parent / 'fixtures/card_rules/stuck-game.png').convert('RGB'))[:,:,::-1].copy()
        self.old=[523,885,173,249]
        self.ctx=Mock()
        self.ctx.tasker.stopping=False
        self.ctx.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value=self.image
        self.ctx.run_recognition.return_value=NS(hit=True,all_results=[])
        self.label=NS(text='SELECT',score=.999109,box=[558,1113,89,35])
        self.reader=CardNameReader()
        self.action=ProduceCardsAuto()

    def test_logged_missing_detector_row_uses_fresh_badge_and_tooltip(self):
        with patch('custom.battle_drinks.ocr',return_value=[self.label]):
            box=self.reader.locate_raised(self.ctx,self.image,self.old)
            self.assertEqual([516,860,173,249],box)
            self.assertEqual(box,self.reader.locate_raised(self.ctx,self.image,box))
        self.ctx.tasker.controller.post_click.assert_not_called()

    def test_no_tooltip_no_orange_badge_and_wrong_card_never_locate(self):
        with patch('custom.battle_drinks.ocr',return_value=[self.label]):
            self.assertIsNone(self.reader.locate_raised(self.ctx,np.zeros_like(self.image),self.old))
            self.assertIsNone(self.reader.locate_raised(self.ctx,self.image,[143,885,187,248]))
            no_badge=self.image.copy();no_badge[1100:1165,500:700]=0
            self.assertIsNone(self.reader.locate_raised(self.ctx,no_badge,self.old))

    def test_badge_ocr_requires_exact_unique_confident_current_label(self):
        for rows in ([],[NS(text='SELECT',score=.7,box=self.label.box)],
                     [NS(text='SELECTED',score=.99,box=self.label.box)],
                     [self.label,self.label],[NS(text='SELECT',score=.99,box=[143,1113,89,35])]):
            with self.subTest(rows=rows),patch('custom.battle_drinks.ocr',return_value=rows):
                self.assertIsNone(self.reader.locate_raised(self.ctx,self.image,self.old))

    def test_two_observations_recover_missing_selected_detection(self):
        with patch('custom.battle_drinks.ocr',return_value=[self.label]), \
             patch('custom.produce.shared._detect_disconnect_screen',return_value=False), \
             patch('time.sleep'):
            result=self.action._observe_selected_card(self.ctx,self.old)
        self.assertEqual([516,860,173,249],result[1])

    def test_failed_selection_does_not_wait_for_own_tooltip_to_close(self):
        with patch.object(CardNameReader,'locate_raised',return_value=None), \
             patch.object(self.action,'_observe_selected_card',return_value=None), \
             patch.object(self.action,'_wait_until_playable') as wait, \
             patch('custom.produce.shared._detect_disconnect_screen',return_value=False), \
             patch('time.sleep'):
            self.assertTrue(self.action._play_a_card(self.ctx,self.old))
        wait.assert_not_called()
        self.assertEqual(1,self.action._selection_failures)

    def test_three_failed_selections_recover_automatically_with_visible_skip(self):
        with patch.object(self.action,'_skip_held_round') as skip, \
             patch('custom.produce.shared._detect_disconnect_screen',return_value=False), \
             patch('time.sleep'):
            for index in range(3):
                self.assertTrue(self.action._recover_card_selection(self.ctx))
                self.assertEqual(int(index==2),skip.call_count)
        self.assertEqual(0,self.action._selection_failures)

    def test_recovery_never_blindly_skips_on_missing_button_or_disconnect(self):
        self.ctx.run_recognition.return_value=NS(hit=False)
        self.action._selection_failures=2
        with patch.object(self.action,'_skip_held_round') as skip, \
             patch('custom.produce.shared._detect_disconnect_screen',return_value=False), \
             patch('time.sleep'):
            self.assertTrue(self.action._recover_card_selection(self.ctx))
        skip.assert_not_called()
        with patch.object(self.action,'_skip_held_round') as skip, \
             patch('custom.produce.shared._detect_disconnect_screen',return_value='disconnect'):
            self.assertTrue(self.action._recover_card_selection(self.ctx))
        skip.assert_not_called()
        self.assertTrue(self.action._disconnect_detected)

    def test_recovery_respects_user_stop(self):
        self.ctx.tasker.stopping=True
        self.assertTrue(self.action._recover_card_selection(self.ctx))
        self.ctx.tasker.controller.post_screencap.assert_not_called()

if __name__=='__main__':unittest.main(verbosity=2)

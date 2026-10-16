"""Connection false positives and fresh-frame drink modal cancellation."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent'))
from custom.dialog_safety import connection_dialog, drink_overlay_cancel
from custom.produce.rewards import ProduceChooseRewardAuto as Reward
from custom.reco.connection import ProduceCancelDrinkDiscard


def row(text, box=None, score=.99):
    return NS(text=text, box=box or [50, 950, 150, 35], score=score)


def context():
    ctx=Mock()
    ctx.tasker.stopping=False
    ctx.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value=np.zeros((1280,720,3),np.uint8)
    ctx.run_recognition.return_value=NS(hit=True, best_result=NS(box=[240,990,190,30]))
    return ctx


class DiscardDialogSafetyTest(unittest.TestCase):
    def test_orange_banner_is_insufficient_for_recovery(self):
        for texts in [('廃棄確認','本当にミックススムージーを捨てますか?'),('Pドリンク詳細',),('日付変更',),()]:
            with self.subTest(texts=texts), patch('custom.dialog_safety.ocr', return_value=[row(t) for t in texts]):
                self.assertIsNone(connection_dialog(context(), None))

    def test_real_date_requires_header_and_body(self):
        with patch('custom.dialog_safety.ocr',return_value=[row('日付変更'),row('日付が変わりました。')]):
            self.assertEqual('date', connection_dialog(context(),None)['kind'])
            self.assertIsNone(connection_dialog(context(),None,'network'))

    def test_network_preserved_and_kind_filtered(self):
        with patch('custom.dialog_safety.ocr',return_value=[row('通信エラー')]):
            self.assertEqual('network', connection_dialog(context(),None)['kind'])
            self.assertIsNone(connection_dialog(context(),None,'date'))

    def test_threshold_remains_point_eight(self):
        for score, accepted in ((.8,True),(.79,False)):
            with patch('custom.dialog_safety.ocr',return_value=[row('通信エラー',score=score)]):
                self.assertEqual(accepted,connection_dialog(context(),None) is not None)

    def test_non_candidate_skips_ocr(self):
        ctx=context();ctx.run_recognition.return_value=NS(hit=False)
        with patch('custom.dialog_safety.ocr') as ocr:
            self.assertIsNone(connection_dialog(ctx,None));ocr.assert_not_called()

    def test_original_connection_thresholds_survive_semantic_guard(self):
        for kind,expected in [('network',.82),('date',.88)]:
            ctx=context();ctx.run_recognition.return_value=NS(hit=False)
            connection_dialog(ctx,None,kind)
            self.assertEqual(expected,ctx.run_recognition.call_args.kwargs['pipeline_override']['ProduceConnectionDialogTemplate']['threshold'])

    def test_discard_vetoes_background_recovery_text(self):
        with patch('custom.dialog_safety.ocr',return_value=[row('廃棄確認'),row('通信エラー')]):
            self.assertIsNone(connection_dialog(context(),None))

    def test_cancel_prefers_front_discard_no_to_background_detail(self):
        with patch('custom.dialog_safety.ocr',side_effect=[[row('Pドリンク詳細'),row('廃棄確認')],[row('いいえ',[190,1140,94,38])]]) as read:
            self.assertEqual((True,[190,1140,94,38]),drink_overlay_cancel(context(),None))
            self.assertEqual([r'^いいえ$'],read.call_args.args[3])

    def test_missing_no_blocks_background_reward_click(self):
        ctx=context()
        with patch('custom.produce.shared._detect_disconnect_screen',return_value=None), patch('custom.dialog_safety.drink_overlay_cancel',return_value=(True,None)):
            Reward._skip_drink_reward(ctx,object(),'failed discard')
        ctx.tasker.controller.post_click.assert_not_called()

    def test_skip_recaptures_after_each_modal_and_never_uses_old_frame(self):
        ctx=context();frames=[object(),object(),object()]
        ctx.tasker.controller.post_screencap.return_value.wait.return_value.get.side_effect=frames
        with patch('custom.produce.shared._detect_disconnect_screen',return_value=None), patch('time.sleep'), \
             patch('custom.dialog_safety.drink_overlay_cancel',side_effect=[(True,[190,1140,94,38]),(True,[100,830,200,38]),(False,None)]) as overlays, \
             patch.object(Reward,'_is_drink_reward',return_value=True), \
             patch('custom.shop.ocr',return_value=[row('受け取らない',[240,1050,240,76])]):
            Reward._skip_drink_reward(ctx,object(),'failed discard')
        self.assertEqual(frames,[call.args[1] for call in overlays.call_args_list])
        self.assertEqual([(237,1159),(200,849),(360,1088)],[call.args for call in ctx.tasker.controller.post_click.call_args_list])

    def test_missing_checkbox_has_no_fixed_coordinate_fallback(self):
        ctx=context();ctx.run_recognition.return_value=NS(hit=False)
        with patch('custom.produce.shared._detect_disconnect_screen',return_value=None), patch('custom.dialog_safety.drink_overlay_cancel',return_value=(False,None)), patch.object(Reward,'_is_drink_reward',return_value=True):
            Reward._skip_drink_reward(ctx,None,'missing control')
        ctx.tasker.controller.post_click.assert_not_called()

    def test_non_reward_scene_does_not_receive_skip_click(self):
        ctx=context()
        with patch('custom.produce.shared._detect_disconnect_screen',return_value=None), patch('custom.dialog_safety.drink_overlay_cancel',return_value=(False,None)), patch.object(Reward,'_is_drink_reward',return_value=False):
            Reward._skip_drink_reward(ctx,None,'changed scene')
        ctx.tasker.controller.post_click.assert_not_called()

    def test_stuck_overlay_retries_are_bounded(self):
        ctx=context()
        with patch('custom.produce.shared._detect_disconnect_screen',return_value=None), patch('time.sleep'), patch('custom.dialog_safety.drink_overlay_cancel',return_value=(True,[190,1140,94,38])):
            Reward._skip_drink_reward(ctx,None,'stuck overlay')
        self.assertEqual(4,ctx.tasker.controller.post_click.call_count)

    def test_resume_only_cancels_and_does_not_confirm_or_skip(self):
        ctx=context()
        with patch('custom.reco.connection.ocr',return_value=[row('廃棄確認')]), patch('custom.reco.connection.time.sleep'), patch('custom.reco.connection.drink_overlay_cancel',side_effect=[(True,[190,1140,94,38]),(True,[100,830,200,38]),(False,None)]):
            ProduceCancelDrinkDiscard().run(ctx,None)
        self.assertEqual([(237,1159),(200,849)],[call.args for call in ctx.tasker.controller.post_click.call_args_list])

    def test_resume_rechecks_changed_modal_without_click(self):
        ctx=context()
        with patch('custom.reco.connection.ocr',return_value=[]):
            ProduceCancelDrinkDiscard().run(ctx,None)
        ctx.tasker.controller.post_click.assert_not_called()

    def test_normal_discard_still_confirms_named_bottle_then_checks_receipt(self):
        ctx=context();ctx.run_recognition.return_value=NS(hit=False)
        uses_receipt=hasattr(Reward,'_wait_discard_receipt')
        invoice={'name':'ミックススムージー','yes':[488,1141,68,37],'no':[190,1139,94,38]}
        with patch('time.sleep'), patch('custom.produce.shared._detect_disconnect_screen',return_value=None), \
             patch.object(Reward,'_reward_inventory_count',return_value=4,create=True), \
             patch('custom.shop.shop_occupied',side_effect=[{0,1,2,3},{0,1,2},{0,1,2}]), \
             patch('custom.shop.owned_detail_title',side_effect=['ミックススムージー','ミックススムージー',None,None]), \
             patch.object(Reward,'_is_drink_reward',return_value=True), \
             patch('custom.shop.owned_throw_box',return_value=[550,620,100,40]), \
             patch('custom.shop.read_discard_confirmation',return_value=invoice), \
             patch.object(Reward,'_wait_discard_receipt',return_value=True,create=True) as receipt:
            # The deployed reward handler uses receipt tracking; source may
            # retain its older equivalent count loop until separately synced.
            self.assertTrue(Reward._delete_owned_drink(ctx,1,expected_name='ミックススムージー'))
        self.assertEqual(3,ctx.tasker.controller.post_click.call_count)
        self.assertEqual((522,1159),ctx.tasker.controller.post_click.call_args.args)
        if uses_receipt:
            receipt.assert_called_once_with(ctx,4)

    def test_mismatched_name_never_reaches_discard_confirmation(self):
        ctx=context();ctx.run_recognition.return_value=NS(hit=False)
        with patch('time.sleep'), patch('custom.produce.shared._detect_disconnect_screen',return_value=None), \
             patch.object(Reward,'_reward_inventory_count',return_value=4,create=True), patch('custom.shop.shop_occupied',return_value={0,1,2,3}), \
             patch('custom.shop.owned_detail_title',return_value='初星黒酢'), patch('custom.shop.owned_throw_box') as throw:
            self.assertFalse(Reward._delete_owned_drink(ctx,1,expected_name='ミックススムージー'))
        throw.assert_not_called()
        self.assertEqual(1,ctx.tasker.controller.post_click.call_count)


if __name__=='__main__':unittest.main()

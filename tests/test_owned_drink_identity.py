"""Owned-title anchoring, shop capacity rules and duplicate rewards."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
from custom.shop import owned_detail_title, read_discard_confirmation, drink_title
from custom.produce.shop import ProduceShoppingAuto, ProduceHIFIntervalShoppingAuto
from custom.produce.rewards import ProduceChooseRewardAuto


def row(text, box=None, score=.99):
    return SimpleNamespace(text=text, box=box or [189,924,292,30], score=score)


def context():
    ctx=Mock(); ctx.tasker.stopping=False; ctx.get_node_data.return_value={"attach":{}}
    ctx.run_recognition.return_value = SimpleNamespace(hit=False, filtered_results=[], all_results=[], best_result=None)
    ctx.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value=np.zeros((1279,720,3),np.uint8)
    return ctx


def owned(*ids):
    return {i:{"id":key,"name":ProduceChooseRewardAuto.TARGET_DRINKS[key],"verified":True} for i,key in enumerate(ids)}


class OwnedDrinkTest(unittest.TestCase):
    def setUp(self):
        # These inventory cases start at full HP; exercise the real shop
        # entry/exit checks without inventing a healing purchase.
        health = patch("custom.shop.read_interval_health", return_value=(80, 80))
        health.start()
        self.addCleanup(health.stop)

    def test_only_observed_boost_dakuten_error_is_normalized(self):
        self.assertEqual("ブーストエキス",drink_title("プーストエキス"))
        self.assertEqual("ブーストエキス改",drink_title("ブーストエキス改"))
    def test_title_roi_follows_short_or_long_detail_header(self):
        for top,name in ((839,"初星ホエイプロテイン"),(718,"初星黒酢"),(536,"初星ホエイプロテイン")):
            with self.subTest(top=top):
                def read(c,img,roi,expected):
                    if roi[0]==15: return [row("Pドリンク詳細",[52,top,220,38])]
                    self.assertEqual([170,top+68,500,65],roi)
                    return [row(name,[189,top+80,292,30]),row("100"),row("7詳細"),row("捨てる",[570,top+80,67,30])]
                with patch("custom.shop.ocr",side_effect=read):
                    self.assertEqual(name,owned_detail_title(context(),None))

    def test_price_header_effects_and_low_confidence_are_not_drink_names(self):
        for values in ([row("100"),row("7詳細")],[row("スキルカード使用数追加+1")],
                       [row("初星ホエイプロテイン",score=.80)],
                       [row("初星ホエイプロテイン"),row("初星黒酢")]):
            with patch("custom.shop.ocr",side_effect=[[row("Pドリンク詳細",[52,839,220,38])],values]):
                self.assertIsNone(owned_detail_title(context(),None))

    def test_missing_detail_header_cannot_read_background_product(self):
        with patch("custom.shop.ocr",return_value=[]) as read:
            self.assertIsNone(owned_detail_title(context(),None))
        read.assert_called_once()

    def discard_reader(self,c,img,roi,expected):
        if roi[0]==15: return [row("廃棄確認",[52,948,138,44])]
        if roi[0]==30:
            self.assertEqual([30,1017,650,115],roi)
            return [row("本当に初星ホエイプロテインを捨てますか?",[58,1039,475,24])]
        if roi[0]==380: return [row("はい",[488,1139,66,38])]
        if roi[0]==65: return [row("いいえ",[191,1141,92,33])]
        return []

    def test_discard_requires_exact_name_question_and_both_buttons(self):
        with patch("custom.shop.ocr",side_effect=self.discard_reader):
            self.assertEqual("初星ホエイプロテイン",read_discard_confirmation(context(),None,"初星ホエイプロテイン")["name"])
            self.assertIsNone(read_discard_confirmation(context(),None,"ホエイプロテイン"))
            self.assertIsNone(read_discard_confirmation(context(),None,"ブーストエキス"))
        for absent in (15,30,380,65):
            with patch("custom.shop.ocr",side_effect=lambda c,i,r,e:[] if r[0]==absent else self.discard_reader(c,i,r,e)):
                self.assertIsNone(read_discard_confirmation(context(),None,"初星ホエイプロテイン"))

    def test_owned_read_waits_for_two_matching_name_frames(self):
        ctx=context(); action=ProduceShoppingAuto()
        with patch("time.sleep"), \
             patch("custom.produce.shared._detect_disconnect_screen",return_value=False), \
             patch("custom.shop.shop_occupied",return_value={0}), \
             patch("custom.shop.owned_detail_title",side_effect=[None,"初星ホエイプロテイン","初星ホエイプロテイン"]), \
             patch("custom.shop.ocr",return_value=[row("キャンセル",[150,1139,160,35])]):
            found=action._read_owned_drinks(ctx)
        self.assertEqual("429282",found[0]["id"])
        self.assertEqual(2,ctx.tasker.controller.post_click.call_count)

    def test_unstable_owned_name_is_not_marked_verified(self):
        ctx=context(); action=ProduceShoppingAuto()
        with patch("time.sleep"), \
             patch("custom.produce.shared._detect_disconnect_screen",return_value=False), \
             patch("custom.shop.shop_occupied",return_value={0}), \
             patch("custom.shop.owned_detail_title",side_effect=["初星黒酢","ブーストエキス"]*3), \
             patch("custom.shop.ocr",return_value=[row("キャンセル",[150,1139,160,35])]):
            self.assertIsNone(action._read_owned_drinks(ctx))

    def test_cached_duplicate_does_not_block_purchase(self):
        ctx=context(); action=ProduceShoppingAuto(); action._owned_drinks=owned("429282")
        with patch("time.sleep"), \
             patch("custom.produce.shared._detect_disconnect_screen",return_value=False), \
             patch.object(action,"_comment_bubble_present",return_value=False), \
             patch.object(action,"_recognize_points",return_value=100), \
             patch("custom.shop.selected_drink_matches",return_value=False):
            self.assertEqual("unavailable",action._purchase(ctx,[220,670,126,126],"protein",is_drink=True,drink_id="429282",expected_price=100))
        ctx.tasker.controller.post_click.assert_called_once_with(283,733)

    def test_ordinary_shop_buys_cheapest_duplicate_when_not_full_then_stops_at_full(self):
        ctx=context(); action=ProduceShoppingAuto(); image=ctx.tasker.controller.post_screencap().wait().get()
        lists={key:{"enabled":False,"cards":[]} for key in ("blacklist","whitelist","replacementList")}
        stock=[{"id":"429282","name":"初星ホエイプロテイン","price":50,"box":[220,670,126,126]},
               {"id":"235611","name":"ブーストエキス","price":75,"box":[528,670,126,126]}]
        with patch("custom.produce.shared._load_card_lists",return_value=lists), \
             patch("custom.produce.shared._detect_disconnect_screen",return_value=False), \
             patch("custom.shop.is_interval_shop",return_value=False), \
             patch.object(action,"_is_shop_scene",return_value=True), \
             patch.object(action,"_dismiss_comment_bubble",return_value=True), \
             patch.object(action,"_wait_shop_ready",return_value=image), \
             patch.object(action,"_find_shop_delete_entry",return_value=None), \
             patch.object(action,"_recognize_points",return_value=345), \
             patch.object(action,"_recognize_shop_cards",return_value=[]), \
             patch.object(action,"_recognize_drinks",return_value=stock), \
             patch.object(action,"_drink_inventory_full",side_effect=[False,True]), \
             patch.object(action,"_read_owned_drinks",return_value=owned("429282","235611","679991","767772")) as details, \
             patch.object(action,"_make_drink_space") as discard, \
             patch.object(action,"_reenter_shop",return_value=True), \
             patch.object(action,"_purchase",return_value="bought") as purchase:
            action.run(ctx,SimpleNamespace())
        purchase.assert_called_once()
        self.assertEqual("429282",purchase.call_args.kwargs["drink_id"])
        self.assertEqual(50,purchase.call_args.kwargs["expected_price"])
        details.assert_called_once(); discard.assert_not_called()
        ctx.run_task.assert_called_once_with("ProduceShoppingExit")

    def test_interval_buys_cheapest_duplicate_when_not_full_then_stops_at_full(self):
        ctx=context(); action=ProduceHIFIntervalShoppingAuto(); image=ctx.tasker.controller.post_screencap().wait().get()
        stock=[{"id":"429282","name":"初星ホエイプロテイン","price":50,"box":[374,690,126,126]},
               {"id":"235611","name":"ブーストエキス","price":75,"box":[528,690,126,126]}]
        with patch("time.sleep"), \
             patch("custom.produce.shared._detect_disconnect_screen",return_value=False), \
             patch("custom.shop.shop_occupied",return_value={0}), \
             patch.object(action,"_is_shop_scene",return_value=True), \
             patch.object(action,"_wait_shop_ready",return_value=image), \
             patch.object(action,"_recognize_points",return_value=345), \
             patch.object(action,"_recognize_drinks",return_value=stock), \
             patch.object(action,"_drink_inventory_full",side_effect=[False,True]), \
             patch.object(action,"_read_owned_drinks",return_value=owned("429282","235611","679991","767772")) as details, \
             patch.object(action,"_make_drink_space") as discard, \
             patch("custom.shop.read_refresh") as refresh, \
             patch.object(action,"_purchase",return_value="bought") as purchase:
            self.assertTrue(action.run(ctx,SimpleNamespace()))
        purchase.assert_called_once()
        self.assertEqual("429282",purchase.call_args.kwargs["drink_id"])
        self.assertEqual(50,purchase.call_args.kwargs["expected_price"])
        details.assert_called_once(); discard.assert_not_called(); refresh.assert_not_called()
        ctx.run_task.assert_called_once_with("ProduceShoppingExit")

    def test_capacity_requires_two_matching_slot_frames(self):
        ctx=context(); action=ProduceShoppingAuto()
        for slots,expected in (([{0,1,2,3}]*2,True),([{0,1,2}]*2,False),
                               ([{0,1,2},{0,1,2,3},{0,1,2,3}],True),
                               ([set(),{0},set(),{0},set()],None)):
            with self.subTest(slots=slots), patch("time.sleep"), \
                 patch("custom.produce.shared._detect_disconnect_screen",return_value=False), \
                 patch("custom.shop.shop_occupied",side_effect=slots):
                self.assertIs(expected,action._drink_inventory_full(ctx,None))
        ctx.tasker.controller.post_click.assert_not_called()

    def test_full_interval_all_targets_exits_after_verifying_names(self):
        ctx=context(); action=ProduceHIFIntervalShoppingAuto(); image=ctx.tasker.controller.post_screencap().wait().get()
        with patch("time.sleep"), \
             patch("custom.produce.shared._detect_disconnect_screen",return_value=False), \
             patch("custom.shop.shop_occupied",return_value={0,1,2,3}), \
             patch.object(action,"_is_shop_scene",return_value=True), \
             patch.object(action,"_wait_shop_ready",return_value=image), \
             patch.object(action,"_recognize_points",return_value=345), \
             patch.object(action,"_recognize_drinks") as scan, \
             patch.object(action,"_read_owned_drinks",return_value=owned("429282","235611","679991","767772")) as details, \
             patch.object(action,"_make_drink_space") as discard, \
             patch("custom.shop.read_refresh") as refresh, \
             patch.object(action,"_purchase") as purchase:
            self.assertTrue(action.run(ctx,SimpleNamespace()))
        details.assert_called_once()
        for method in (scan,discard,refresh,purchase): method.assert_not_called()
        ctx.tasker.controller.post_click.assert_not_called()
        ctx.run_task.assert_called_once_with("ProduceShoppingExit")

    def test_ordinary_capacity_full_or_unstable_never_discards(self):
        for capacity,status,expected,calls in ((True,"bought",True,0),
                                               (None,"bought",True,0),
                                               (False,"full",True,1)):
            with self.subTest(capacity=capacity,status=status):
                ctx=context(); action=ProduceShoppingAuto(); image=ctx.tasker.controller.post_screencap().wait().get()
                lists={key:{"enabled":False,"cards":[]} for key in ("blacklist","whitelist","replacementList")}
                stock=[{"id":"429282","name":"初星ホエイプロテイン","price":50,"box":[220,670,126,126]}]
                with patch("custom.produce.shared._load_card_lists",return_value=lists), \
                     patch("custom.produce.shared._detect_disconnect_screen",return_value=False), \
                     patch("custom.shop.is_interval_shop",return_value=False), \
                     patch.object(action,"_is_shop_scene",return_value=True), \
                     patch.object(action,"_dismiss_comment_bubble",return_value=True), \
                     patch.object(action,"_wait_shop_ready",return_value=image), \
                     patch.object(action,"_find_shop_delete_entry",return_value=None), \
                     patch.object(action,"_recognize_points",return_value=345), \
                     patch.object(action,"_recognize_shop_cards",return_value=[]), \
                     patch.object(action,"_recognize_drinks",return_value=stock), \
                     patch.object(action,"_drink_inventory_full",return_value=capacity), \
                     patch.object(action,"_read_owned_drinks",return_value=owned("429282","235611","679991","767772")) as details, \
                     patch.object(action,"_make_drink_space") as discard, \
                     patch.object(action,"_purchase",return_value=status) as purchase:
                    self.assertIs(expected,action.run(ctx,SimpleNamespace()))
                self.assertEqual(calls,purchase.call_count)
                if capacity is True: details.assert_called_once()
                else: details.assert_not_called()
                discard.assert_not_called()
                if expected: ctx.run_task.assert_called_once_with("ProduceShoppingExit")
                else: ctx.run_task.assert_not_called()

    def test_interval_late_full_report_exits_without_discard_or_refresh(self):
        ctx=context(); action=ProduceHIFIntervalShoppingAuto(); image=ctx.tasker.controller.post_screencap().wait().get()
        stock=[{"id":"429282","name":"初星ホエイプロテイン","price":50,"box":[374,690,126,126]}]
        with patch("time.sleep"), \
             patch("custom.produce.shared._detect_disconnect_screen",return_value=False), \
             patch.object(action,"_drink_inventory_full",return_value=False), \
             patch.object(action,"_is_shop_scene",return_value=True), \
             patch.object(action,"_wait_shop_ready",return_value=image), \
             patch.object(action,"_recognize_points",return_value=345), \
             patch.object(action,"_recognize_drinks",return_value=stock), \
             patch.object(action,"_make_drink_space") as discard, \
             patch("custom.shop.read_refresh") as refresh, \
             patch.object(action,"_purchase",return_value="full") as purchase:
            self.assertTrue(action.run(ctx,SimpleNamespace()))
        purchase.assert_called_once(); discard.assert_not_called(); refresh.assert_not_called()
        ctx.run_task.assert_called_once_with("ProduceShoppingExit")

    def reward(self, states, delete=True):
        ctx=context(); image=ctx.tasker.controller.post_screencap().wait().get(); action=ProduceChooseRewardAuto
        with patch.object(action,"_stable_drink_state",side_effect=[(image,s) for s in states]), \
             patch.object(action,"_delete_owned_drink",return_value=delete) as discard, \
             patch.object(action,"_click_reward_drink",return_value=True) as take, \
             patch.object(action,"_skip_drink_reward",return_value=True) as skip:
            self.assertTrue(action._handle_drink_reward(ctx,image))
        return discard,take,skip

    def reward_state(self,*ids,full=False,reward_id="429282"):
        return {"reward":{0:{"id":reward_id,"name":ProduceChooseRewardAuto.TARGET_DRINKS[reward_id]}},
                "inventory":owned(*ids),"occupied":set(range(len(ids))),"full":full,"stable":True}

    def test_not_full_duplicate_reward_keeps_existing_and_receives_copy(self):
        discard,take,skip=self.reward([self.reward_state("429282","235611")])
        discard.assert_not_called(); take.assert_called_once(); skip.assert_not_called()

    def test_full_target_inventory_skips_duplicate_reward(self):
        discard,take,skip=self.reward([self.reward_state("429282","235611","679991","767772",full=True)])
        discard.assert_not_called(); take.assert_not_called(); skip.assert_called_once()

    def test_existing_two_duplicates_are_preserved_when_receiving_third(self):
        discard,take,skip=self.reward([self.reward_state("429282","235611","429282")])
        discard.assert_not_called()
        take.assert_called_once(); skip.assert_not_called()

    def test_failed_non_target_discard_never_receives_reward(self):
        state = self.reward_state("429282","235611","679991",full=True)
        state["occupied"] = {0,1,2,3}  # Only slot 3 is not a target.
        discard,take,skip=self.reward([state],delete=False)
        self.assertEqual(3, discard.call_args.args[1])
        self.assertTrue(discard.call_args.kwargs["expected_non_target"])
        discard.assert_called_once(); take.assert_not_called(); skip.assert_called_once()

    def test_reward_discard_requires_identity_modal_and_final_count(self):
        ctx=context(); action=ProduceChooseRewardAuto
        invoice={"name":"初星ホエイプロテイン","yes":[488,1138,66,37],"no":[191,1139,92,35]}
        with patch("time.sleep"), \
             patch("custom.produce.shared._detect_disconnect_screen",return_value=False), \
             patch.object(action,"_reward_inventory_count",side_effect=[2,1,1]) as count, \
             patch("custom.shop.owned_detail_title",side_effect=["初星ホエイプロテイン","初星ホエイプロテイン",None,None]), \
             patch("custom.shop.owned_throw_box",return_value=[570,920,67,29]), \
             patch("custom.shop.read_discard_confirmation",return_value=invoice), \
             patch.object(action,"_is_drink_reward",return_value=True):
            self.assertTrue(action._delete_owned_drink(ctx,0,expected_name="初星ホエイプロテイン"))
        self.assertEqual([(65,1200),(603,934),(521,1156)],[c.args for c in ctx.tasker.controller.post_click.call_args_list])
        self.assertEqual(3, count.call_count)

    def test_wrong_reward_drink_identity_never_clicks_discard(self):
        ctx=context()
        with patch("time.sleep"), \
             patch("custom.produce.shared._detect_disconnect_screen",return_value=False), \
             patch("custom.shop.shop_occupied",return_value={0,1}), \
             patch("custom.shop.owned_detail_title",return_value="初星黒酢"), \
             patch("custom.shop.owned_throw_box") as throw:
            self.assertFalse(ProduceChooseRewardAuto._delete_owned_drink(ctx,0,expected_name="初星ホエイプロテイン"))
        throw.assert_not_called(); ctx.tasker.controller.post_click.assert_called_once_with(65,1200)

    def test_shop_discard_confirms_two_frames_then_three_remaining_slots(self):
        ctx=context(); action=ProduceShoppingAuto()
        action._owned_drinks=owned("429282","429282","767772","235611")
        invoice={"name":"初星ホエイプロテイン","yes":[488,1138,66,37],"no":[191,1139,92,35]}
        moved={**invoice,"yes":[489,1139,66,37]}
        with patch("time.sleep"), \
             patch("custom.produce.shared._detect_disconnect_screen",return_value=False), \
             patch("custom.shop.owned_throw_box",return_value=[570,920,67,29]), \
             patch("custom.shop.read_discard_confirmation",side_effect=[invoice,moved,None,None]), \
             patch("custom.shop.shop_occupied",return_value={0,1,2}):
            self.assertTrue(action._make_drink_space(ctx,"679991"))
        self.assertIsNone(action._owned_drinks)
        self.assertEqual([(65,1200),(603,934),(522,1157)],[c.args for c in ctx.tasker.controller.post_click.call_args_list])

    def test_wrong_shop_discard_confirmation_never_clicks_yes(self):
        ctx=context(); action=ProduceShoppingAuto()
        action._owned_drinks=owned("429282","429282","767772","235611")
        with patch("time.sleep"), \
             patch("custom.produce.shared._detect_disconnect_screen",return_value=False), \
             patch("custom.shop.owned_throw_box",return_value=[570,920,67,29]), \
             patch("custom.shop.read_discard_confirmation",return_value=None):
            self.assertFalse(action._make_drink_space(ctx,"679991"))
        self.assertTrue(action._inventory_read_failed)
        self.assertEqual(2,ctx.tasker.controller.post_click.call_count)

    def test_missing_reward_does_not_discard_target_duplicates_when_full(self):
        discard,take,skip=self.reward([self.reward_state("235611","235611","679991","767772",full=True)])
        discard.assert_not_called(); take.assert_not_called(); skip.assert_called_once()


if __name__=="__main__": unittest.main()

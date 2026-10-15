"""Owned-card change selection, protected cards, scrolling and submit guards."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
from custom.action.produce import ProduceHIFChangeSourceAuto, ProduceChooseRewardAuto
from custom.hif import is_hif_change_source


def frame(enabled=False):
    image=np.zeros((1279,720,3),np.uint8)
    image[660:1100,70:650]=(245,230,150)
    image[1140:1170,420:585]=(40,150,250) if enabled else (60,120,180)
    return image


def card(name, rarity):
    return dict(name=name,name_key=name,rarity=rarity,name_confidence=.9,
                box=[227,784,114,117],priority=100,label="cards",detector_score=.9,identity_verified=True)


class Done:
    def __init__(self,image=None): self.image=image
    def wait(self): return self
    def get(self): return self.image


class Controller:
    def __init__(self,advance=True):
        self.selected=self.finished=False
        self.advance=advance
        self.clicks=[]
        self.swipes=[]
    def post_screencap(self):
        return Done(np.full((1279,720,3),40,np.uint8) if self.finished else frame(self.selected))
    def post_click(self,x,y):
        self.clicks.append((x,y))
        if y<1100: self.selected=True
        elif self.advance: self.finished=True
        return Done()
    def post_swipe(self,*args): self.swipes.append(args); return Done()


class HIFSourceTest(unittest.TestCase):
    def setUp(self):
        # These tests exercise ranking/submission from the first page. Actual
        # scrollbar/rewind behavior has dedicated crosspage_audit tests.
        rewind = patch.object(ProduceHIFChangeSourceAuto, '_rewind_source', autospec=True,
                              side_effect=lambda action, ctx, change=True: action._page(ctx) if change else action._page(ctx, change=False))
        rewind.start()
        self.addCleanup(rewind.stop)

    def context(self, controller, stopping=False):
        context=Mock()
        context.tasker=SimpleNamespace(controller=controller,stopping=stopping)
        context.run_recognition.return_value=SimpleNamespace(hit=True,all_results=[SimpleNamespace(box=[227,784,114,117])])
        return context

    def run_action(self,context,cards,lists=None):
        if lists is None:
            lists={key:dict(enabled=False,cards=[]) for key in ("blacklist","whitelist")}
        with patch("custom.action.produce.time.sleep"), \
             patch("custom.action.produce._detect_disconnect_screen",return_value=False), \
             patch("custom.action.produce._load_card_lists",return_value=lists), \
             patch.object(ProduceChooseRewardAuto,"_evaluate",return_value=cards), \
             patch("custom.removal.selected_card_box_matches", side_effect=lambda *a: context.tasker.controller.selected), \
             patch("custom.removal.read_selected_card", return_value=cards[0]), \
             patch.object(ProduceHIFChangeSourceAuto,"_ocr",return_value=[SimpleNamespace(box=[460,1140,120,30])]):
            return ProduceHIFChangeSourceAuto().run(context,SimpleNamespace())

    def test_exact_header_and_source_prompt_required(self):
        context=Mock(); context.run_recognition.return_value=SimpleNamespace(hit=True)
        self.assertTrue(is_hif_change_source(context,frame()))
        context.run_recognition.side_effect=[SimpleNamespace(hit=True),SimpleNamespace(hit=False)]
        self.assertFalse(is_hif_change_source(context,frame()))
        context.run_recognition.reset_mock()
        self.assertFalse(is_hif_change_source(context,np.zeros((1279,720,3),np.uint8)))
        context.run_recognition.assert_not_called()

    def test_boxes_reject_upper_destination_footer_and_partial_cards(self):
        boxes=[[409,94,139,141],[227,784,114,117],[227,784,114,117],
               [520,1060,116,115],[81,610,114,117],[70,800,20,100]]
        self.assertEqual([[227,784,114,117]],ProduceHIFChangeSourceAuto._boxes([
            SimpleNamespace(box=b) for b in boxes]))

    def test_live_first_row_is_not_filtered_as_upper_destination(self):
        boxes=[[409,97,139,140],[83,635,114,136],[228,637,115,118],
               [375,637,115,118],[520,637,117,118]]
        self.assertEqual(boxes[1:],ProduceHIFChangeSourceAuto._boxes([
            SimpleNamespace(box=b) for b in boxes]))

    def test_live_one_pixel_row_jitter_does_not_pair_different_cards(self):
        # Actual 20:59:48/50 boxes: N and SSR swap sequence because their y
        # coordinates alternate between 637 and 638 on the same grid row.
        old_boxes=[[83,635,114,124],[520,637,117,118],[228,638,115,118],
                   [374,638,115,118],[83,788,113,114],[521,788,116,113],
                   [228,931,115,116],[375,932,115,116]]
        new_boxes=[[83,635,114,124],[228,637,115,118],[520,637,117,118],
                   [374,638,115,118],[83,787,113,114],[521,788,116,113],
                   [228,931,115,116],[375,932,115,116]]
        old=tuple((tuple(box),(box[0]//8,)*27) for box in old_boxes)
        new=tuple((tuple(box),(box[0]//8,)*27) for box in new_boxes)
        self.assertFalse(ProduceChooseRewardAuto._reward_frames_stable(old,new))
        self.assertTrue(ProduceHIFChangeSourceAuto._source_frames_stable(old,new))

    def test_spatial_stability_rejects_missing_moved_ambiguous_or_changed_cards(self):
        old=(((80,635,114,118),(10,)*27),((228,638,115,118),(20,)*27))
        stable=(((229,637,115,118),(20,)*27),((81,636,114,118),(10,)*27))
        check=ProduceHIFChangeSourceAuto._source_frames_stable
        self.assertTrue(check(old,stable))
        for invalid in (None,(),stable[:1],
                        (((229,630,115,118),(20,)*27),stable[1]),
                        (stable[1],stable[1]),
                        ((stable[0][0],(30,)*27),stable[1])):
            with self.subTest(invalid=invalid):
                self.assertFalse(check(old,invalid))
        self.assertFalse(check(None,stable))

    def test_source_page_stabilizes_before_evaluating_card_names(self):
        controller=Controller(); ctx=self.context(controller)
        boxes_a=[[83,635,114,124],[520,637,117,118],[228,638,115,118],[374,638,115,118]]
        boxes_b=[[83,635,114,124],[228,637,115,118],[520,637,117,118],[374,638,115,118]]
        ctx.run_recognition.side_effect=[SimpleNamespace(hit=True,all_results=[SimpleNamespace(box=b) for b in boxes])
                                         for boxes in (boxes_a,boxes_b)]
        with patch("custom.hif.is_hif_change_source",return_value=True), \
             patch("custom.action.produce._detect_disconnect_screen",return_value=False), \
             patch("custom.action.produce.time.sleep"), \
             patch.object(ProduceChooseRewardAuto,"_evaluate",return_value=[card("normal","N")]) as evaluate:
            self.assertIsNotNone(ProduceHIFChangeSourceAuto()._page(ctx))
        self.assertEqual(2,ctx.run_recognition.call_count)
        evaluate.assert_called_once()
        self.assertFalse(controller.clicks or controller.swipes)

    def test_unstable_source_page_logs_reason_without_clicking(self):
        controller=Controller(); ctx=self.context(controller)
        ctx.run_recognition.return_value=SimpleNamespace(hit=False)
        with patch("custom.hif.is_hif_change_source",return_value=True), \
             patch("custom.action.produce._detect_disconnect_screen",return_value=False), \
             patch("custom.action.produce.time.sleep"), \
             patch.object(ProduceChooseRewardAuto,"_evaluate") as evaluate, \
             patch("custom.action.produce.logger.warning") as warning:
            self.assertIsNone(ProduceHIFChangeSourceAuto()._page(ctx))
        evaluate.assert_not_called()
        warning.assert_called_once()
        self.assertFalse(controller.clicks or controller.swipes)

    def test_page_movement_in_live_first_row_is_not_mistaken_for_bottom(self):
        first=frame(); second=first.copy(); second[620:665,70:650]=120
        self.assertFalse(ProduceHIFChangeSourceAuto._same_page(first,second))

    def test_native_first_page_log_keeps_all_ten_complete_owned_cards(self):
        # Actual ProduceRecognitionCards results at 17:37:49; the old cutoff
        # kept six, dropping every source card at y=635..637.
        boxes=[[11,24,267,59],[81,931,115,129],[83,635,114,136],
               [83,786,114,113],[228,637,115,118],[228,787,115,114],
               [228,930,114,117],[375,637,115,118],[376,931,113,117],
               [409,97,139,140],[520,637,117,118],[520,786,117,114]]
        found=ProduceHIFChangeSourceAuto._boxes([SimpleNamespace(box=b) for b in boxes])
        self.assertEqual(10,len(found))
        self.assertEqual(4,sum(b[1]<655 for b in found))

    def test_unverified_visual_n_does_not_override_exact_replacement(self):
        controller=Controller(); ctx=self.context(controller); action=ProduceHIFChangeSourceAuto()
        normal={**card("normal","N"),"identity_verified":False,"box":[80,784,114,117]}
        sr=card("sr","SR")
        with patch.object(action,"_page",return_value=(frame(),[normal,sr])), \
             patch.object(action,"_scroll"), \
             patch("custom.action.produce.ProduceShoppingAuto._probe_removal_candidates",side_effect=lambda c,items,*a,**k:items):
            self.assertEqual("sr", action._scan_source(ctx,set(),set(),{"sr"})["name"])

    def test_normal_card_selected_and_page_progress_confirmed(self):
        controller=Controller()
        self.assertTrue(self.run_action(self.context(controller),[card("normal","N")]))
        self.assertEqual([(284,842),(520,1155)],controller.clicks)
        self.assertFalse(controller.swipes)

    def test_submit_not_effective_never_claims_success(self):
        controller=Controller(advance=False)
        with patch("custom.action.produce.logger.success") as success:
            self.assertTrue(self.run_action(self.context(controller),[card("normal","N")]))
        self.assertEqual(2,len(controller.clicks))
        success.assert_not_called()

    def test_stop_does_not_touch_game(self):
        controller=Controller()
        self.assertTrue(self.run_action(self.context(controller,stopping=True),[card("normal","N")]))
        self.assertFalse(controller.clicks or controller.swipes)

    def test_no_eligible_card_does_not_delete_a_valuable_card(self):
        controller=Controller()
        self.assertTrue(self.run_action(self.context(controller),[card("keep","SSR")]))
        self.assertFalse(controller.clicks)
        self.assertLessEqual(len(controller.swipes),5)

    def test_enabled_replacement_can_submit_whitelisted_source(self):
        controller=Controller()
        lists=dict(blacklist=dict(enabled=False,cards=[]),
                   whitelist=dict(enabled=True,cards=["both"]),
                   replacementList=dict(enabled=True,cards=["both"]))
        self.assertTrue(self.run_action(self.context(controller),[card("both","SSR")],lists))
        self.assertEqual([(284,842),(520,1155)],controller.clicks)

    def test_disabled_replacement_still_protects_whitelisted_source(self):
        controller=Controller()
        lists=dict(blacklist=dict(enabled=False,cards=[]),
                   whitelist=dict(enabled=True,cards=["both"]),
                   replacementList=dict(enabled=False,cards=["both"]))
        self.assertTrue(self.run_action(self.context(controller),[card("both","SSR")],lists))
        self.assertFalse(controller.clicks)

    def test_strict_later_r_card_precedes_first_page_replacement(self):
        controller=Controller(); context=self.context(controller)
        action=ProduceHIFChangeSourceAuto()
        replacement=card("both","SSR"); rare=card("rare","R")
        moved=frame(); moved[700:1050,70:650]=120
        pages=[(frame(),[replacement]),(moved,[rare]),(moved,[rare]),(moved,[rare]),(moved,[rare])]
        lists=dict(blacklist=dict(enabled=False,cards=[]),
                   whitelist=dict(enabled=True,cards=["both"]),
                   replacementList=dict(enabled=True,cards=["both"]))
        with patch("custom.action.produce._load_card_lists",return_value=lists), \
             patch.object(action,"_page",side_effect=pages), \
             patch.object(action,"_scroll") as scroll, \
             patch.object(action,"_submit",return_value=True) as submit:
            selected = action._scan_source(context,
                set(lists['blacklist']['cards']) if lists['blacklist']['enabled'] else set(),
                set(lists['whitelist']['cards']) if lists['whitelist']['enabled'] else set(),
                set(lists.get('replacementList', {}).get('cards', [])), strict=True)
            self.assertIsNotNone(selected)
            self.assertTrue(action._submit(context, selected))
        self.assertEqual("rare",submit.call_args.args[1]["name"])
        self.assertEqual(1,submit.call_args.args[1]["delete_rank"])
        self.assertTrue(all(call.kwargs["forward"] for call in scroll.call_args_list))

    def test_strict_later_replacement_sr_precedes_first_page_replacement_ssr(self):
        controller=Controller(); context=self.context(controller)
        action=ProduceHIFChangeSourceAuto()
        rainbow=card("rainbow","SSR"); sr=card("sr","SR")
        moved=frame(); moved[700:1050,70:650]=120
        pages=[(frame(),[rainbow]),(moved,[sr]),(moved,[sr]),(moved,[sr]),(moved,[sr])]
        lists=dict(blacklist=dict(enabled=False,cards=[]),
                   whitelist=dict(enabled=True,cards=["rainbow","sr"]),
                   replacementList=dict(enabled=True,cards=["rainbow","sr"]))
        with patch("custom.action.produce._load_card_lists",return_value=lists), \
             patch.object(action,"_page",side_effect=pages), \
             patch.object(action,"_scroll"), \
             patch.object(action,"_submit",return_value=True) as submit:
            selected = action._scan_source(context,
                set(lists['blacklist']['cards']) if lists['blacklist']['enabled'] else set(),
                set(lists['whitelist']['cards']) if lists['whitelist']['enabled'] else set(),
                set(lists.get('replacementList', {}).get('cards', [])), strict=True)
            self.assertIsNotNone(selected)
            self.assertTrue(action._submit(context, selected))
        self.assertEqual("sr",submit.call_args.args[1]["name"])

    def test_network_error_hands_back_without_click(self):
        controller=Controller(); context=self.context(controller)
        action=ProduceHIFChangeSourceAuto()
        with patch("custom.action.produce._load_card_lists",return_value={key:dict(enabled=False,cards=[]) for key in ("blacklist","whitelist")}), \
             patch("custom.action.produce._detect_disconnect_screen",return_value="ProduceDisconnectError"):
            self.assertTrue(action.run(context,SimpleNamespace()))
        self.assertFalse(controller.clicks or controller.swipes)

    def test_live_entry_keeps_fast_search_and_does_not_scan_a_later_blacklist(self):
        controller = Controller()
        context = self.context(controller)
        action = ProduceHIFChangeSourceAuto()
        lists = dict(blacklist=dict(enabled=True,cards=["blocked"]), whitelist=dict(enabled=False,cards=[]))
        with patch("custom.action.produce._load_card_lists", return_value=lists), \
             patch.object(action, "_page", side_effect=[(frame(),[card("rare","R")]),
                                                       (frame(),[card("blocked","SSR")])]) as page, \
             patch.object(action, "_scroll") as scroll, \
             patch.object(action, "_submit", return_value=True) as submit:
            self.assertTrue(action.run(context, SimpleNamespace()))
        page.assert_called_once()
        scroll.assert_not_called()
        self.assertEqual("rare", submit.call_args.args[1]["name"])

    def test_strict_scroll_reacquires_card_box_before_submit(self):
        controller=Controller(); context=self.context(controller)
        normal=card("normal","N"); normal["box"]=[80,900,114,117]
        action=ProduceHIFChangeSourceAuto()
        moved=frame(); moved[700:1050,70:650]=120
        pages=[(frame(),[card("rare","R")]),(moved,[normal])]
        lists={key:dict(enabled=False,cards=[]) for key in ("blacklist","whitelist")}
        with patch("custom.action.produce._load_card_lists",return_value=lists), \
             patch.object(action,"_page",side_effect=pages), \
             patch.object(action,"_scroll") as scroll, \
             patch.object(action,"_submit",return_value=True) as submit:
            selected = action._scan_source(context,
                set(lists['blacklist']['cards']) if lists['blacklist']['enabled'] else set(),
                set(lists['whitelist']['cards']) if lists['whitelist']['enabled'] else set(),
                set(lists.get('replacementList', {}).get('cards', [])), strict=True)
            self.assertIsNotNone(selected)
            self.assertTrue(action._submit(context, selected))
        scroll.assert_called_once_with(context,forward=True)
        self.assertEqual([80,900,114,117],submit.call_args.args[1]["box"])

    def test_strict_end_of_scroll_does_not_count_a_nonexistent_page(self):
        controller=Controller(); context=self.context(controller)
        action=ProduceHIFChangeSourceAuto()
        rare=card("rare","R")
        pages=[(frame(),[rare]),(frame(),[rare]),(frame(),[rare]),(frame(),[rare])]
        lists={key:dict(enabled=False,cards=[]) for key in ("blacklist","whitelist")}
        with patch("custom.action.produce._load_card_lists",return_value=lists), \
             patch.object(action,"_page",side_effect=pages), \
             patch.object(action,"_scroll") as scroll, \
             patch.object(action,"_submit",return_value=True) as submit:
            selected = action._scan_source(context,
                set(lists['blacklist']['cards']) if lists['blacklist']['enabled'] else set(),
                set(lists['whitelist']['cards']) if lists['whitelist']['enabled'] else set(),
                set(lists.get('replacementList', {}).get('cards', [])), strict=True)
            self.assertIsNotNone(selected)
            self.assertTrue(action._submit(context, selected))
        self.assertEqual([True, True], [call.kwargs['forward'] for call in scroll.call_args_list])
        submit.assert_called_once()

    def test_strict_blacklist_found_lower_is_reacquired_on_current_page(self):
        controller=Controller(); context=self.context(controller)
        action=ProduceHIFChangeSourceAuto()
        rare=card("rare","R"); blocked=card("blocked","SSR")
        moved=frame(); moved[700:1050,70:650]=120
        pages=[(frame(),[rare]),(moved,[blocked]),(moved,[blocked]),(moved,[blocked]),(moved,[blocked])]
        lists=dict(blacklist=dict(enabled=True,cards=["blocked"]),whitelist=dict(enabled=False,cards=[]))
        with patch("custom.action.produce._load_card_lists",return_value=lists), \
             patch.object(action,"_page",side_effect=pages), \
             patch.object(action,"_scroll") as scroll, \
             patch.object(action,"_submit",return_value=True) as submit:
            selected = action._scan_source(context,
                set(lists['blacklist']['cards']) if lists['blacklist']['enabled'] else set(),
                set(lists['whitelist']['cards']) if lists['whitelist']['enabled'] else set(),
                set(lists.get('replacementList', {}).get('cards', [])), strict=True)
            self.assertIsNotNone(selected)
            self.assertTrue(action._submit(context, selected))
        self.assertEqual("blocked",submit.call_args.args[1]["name"])
        self.assertTrue(all(call.kwargs["forward"] for call in scroll.call_args_list))

    def test_first_page_blacklist_beats_later_sr_and_reacquires_new_coordinates(self):
        controller=Controller(); ctx=self.context(controller); action=ProduceHIFChangeSourceAuto()
        blocked=card("blocked","SSR"); sr=card("sr","SR")
        relocated={**blocked,"box":[80,650,116,118]}
        moved=frame(); moved[700:1050,70:650]=120
        pages=[(frame(),[blocked]),(moved,[sr]),(moved,[sr]),(moved,[sr]),(frame(),[relocated])]
        with patch.object(action,"_page",side_effect=pages), patch.object(action,"_scroll") as scroll:
            selected=action._scan_source(ctx,{"blocked"},set(),{"sr"})
        self.assertEqual("blocked",selected["name"])
        self.assertEqual([80,650,116,118],selected["box"])
        self.assertEqual([True,True,True,False],[call.kwargs["forward"] for call in scroll.call_args_list])

    def test_unresolved_first_page_does_not_block_known_global_best(self):
        controller=Controller(); ctx=self.context(controller); action=ProduceHIFChangeSourceAuto()
        unresolved={**card("unknown","SSR"),"name_confidence":0.,"probe_failed":True}
        sr=card("sr","SR"); moved=frame(); moved[700:1050,70:650]=120
        pages=[(frame(),[unresolved]),(moved,[sr]),(moved,[sr]),(moved,[sr])]
        with patch.object(action,"_page",side_effect=pages), patch.object(action,"_scroll") as scroll:
            self.assertEqual("sr", action._scan_source(ctx,set(),set(),{"sr"})["name"])
        self.assertEqual([True,True,True],[call.kwargs["forward"] for call in scroll.call_args_list])

    def test_unknown_later_page_does_not_forget_first_page_best(self):
        controller=Controller(); ctx=self.context(controller); action=ProduceHIFChangeSourceAuto()
        first=frame(); bottom=frame(); bottom[700:1050,70:650]=120
        blocked=card('blocked','SSR'); sr=card('sr','SR')
        unknown=dict(card('unknown','N'),identity_verified=False,name_confidence=0.,probe_failed=True,box=[80,784,114,117])
        relocated=dict(blocked,box=[80,650,116,118])
        pages=[(first,[blocked]),(bottom,[unknown,sr]),(bottom,[unknown,sr]),
               (bottom,[unknown,sr]),(first,[relocated])]
        with patch.object(action,'_page',side_effect=pages), patch.object(action,'_scroll'), \
             patch('custom.action.produce.ProduceShoppingAuto._probe_removal_candidates',
                   side_effect=lambda c,items,*a,**k:items):
            selected=action._scan_source(ctx,{'blocked'},set(),{'sr'})
        self.assertEqual('blocked',selected['name'])
        self.assertEqual(relocated['box'],selected['box'])

    def test_shop_deletion_keeps_strict_unknown_guard(self):
        controller=Controller(); ctx=self.context(controller); action=ProduceHIFChangeSourceAuto()
        unknown=dict(card('unknown','N'),identity_verified=False,name_confidence=0.,probe_failed=True,box=[80,784,114,117])
        with patch.object(action,'_page',return_value=(frame(),[unknown,card('sr','SR')])), \
             patch.object(action,'_scroll'), \
             patch('custom.action.produce.ProduceShoppingAuto._probe_removal_candidates',
                   side_effect=lambda c,items,*a,**k:items):
            self.assertIsNone(action._scan_source(ctx,set(),set(),{'sr'},change=False))

    def test_unstable_source_yields_without_failing_or_cancel_click(self):
        controller=Controller(); ctx=self.context(controller); action=ProduceHIFChangeSourceAuto()
        lists={key:dict(enabled=False,cards=[]) for key in ('blacklist','whitelist')}
        with patch('custom.action.produce._load_card_lists',return_value=lists), \
             patch('custom.action.produce.time.sleep'), \
             patch.object(action,'_rewind_source',return_value=None), \
             patch.object(action,'_submit') as submit:
            self.assertTrue(action.run(ctx,SimpleNamespace()))
        submit.assert_not_called()
        self.assertFalse(controller.clicks)

    def test_retry_does_not_spam_same_warning_or_claim_exchange_success(self):
        controller=Controller(); ctx=self.context(controller); action=ProduceHIFChangeSourceAuto()
        action._interrupted=False
        with patch('custom.action.produce.time.monotonic',side_effect=[100,101,131]), \
             patch('custom.action.produce.time.sleep'), \
             patch('custom.action.produce.logger.warning') as warning, \
             patch('custom.action.produce.logger.success') as success:
            for _ in range(3):
                self.assertTrue(action._defer_source(ctx,'test'))
        self.assertEqual(2,warning.call_count)
        success.assert_not_called()
        self.assertFalse(controller.clicks)

    def test_first_page_n_stops_before_any_scroll_even_with_replacement_sr(self):
        controller=Controller(); ctx=self.context(controller); action=ProduceHIFChangeSourceAuto()
        normal={**card("normal","N"),"box":[520,637,117,118]}
        with patch.object(action,"_page",return_value=(frame(),[card("sr","SR"),normal])), \
             patch.object(action,"_scroll") as scroll:
            selected=action._scan_source(ctx,set(),set(),{"sr"})
        self.assertEqual("normal",selected["name"])
        scroll.assert_not_called()

    def test_high_confidence_n_participates_without_scan_clicks(self):
        controller=Controller(); ctx=self.context(controller); action=ProduceHIFChangeSourceAuto()
        normal=dict(card('normal','N'), identity_verified=False, name_confidence=.95,
                    name_score=.5, name_margin=.2, frame_rarity='N', rarity_confidence=.7,
                    detail_selected=False)
        with patch.object(action,'_page',return_value=(frame(),[normal])), \
             patch.object(action,'_scroll') as scroll:
            selected=action._scan_source(ctx,set(),set(),set())
        self.assertEqual('normal', selected['name'])
        self.assertFalse(selected['identity_verified'])
        self.assertFalse(selected['detail_selected'])
        self.assertFalse(controller.clicks)
        scroll.assert_not_called()

    def test_visual_candidate_cannot_submit_with_wrong_detail_name(self):
        controller=Controller(); ctx=self.context(controller); action=ProduceHIFChangeSourceAuto()
        selected=dict(card('expected','N'), identity_verified=False, detail_selected=False, delete_rank=3)
        with patch('custom.action.produce.time.sleep'), \
             patch('custom.action.produce._detect_disconnect_screen',return_value=False), \
             patch('custom.hif.is_hif_change_source',return_value=True), \
             patch('custom.removal.read_selected_card',return_value=card('different','N')):
            self.assertFalse(action._submit(ctx,selected))
        self.assertEqual(1,len(controller.clicks))
        self.assertLess(controller.clicks[0][1],1100)

    def test_later_n_still_wins_despite_unresolved_previous_page(self):
        controller=Controller(); ctx=self.context(controller); action=ProduceHIFChangeSourceAuto()
        unresolved={**card("unknown","SSR"),"name_confidence":0.,"probe_failed":True}
        normal=card("normal","N"); moved=frame(); moved[700:1050,70:650]=120
        with patch.object(action,"_page",side_effect=[(frame(),[unresolved]),(moved,[normal])]), \
             patch.object(action,"_scroll"):
            selected=action._scan_source(ctx,set(),set(),set())
        self.assertEqual("normal",selected["name"])

    def test_failed_first_swipe_is_retried_before_choosing_lower_priority(self):
        controller=Controller(); ctx=self.context(controller); action=ProduceHIFChangeSourceAuto()
        first=frame(); moved=frame(); moved[700:1050,70:650]=120
        pages=[(first,[card("sr","SR")]),(first,[card("sr","SR")]),(moved,[card("normal","N")])]
        with patch.object(action,"_page",side_effect=pages), patch.object(action,"_scroll") as scroll:
            selected=action._scan_source(ctx,set(),set(),{"sr"})
        self.assertEqual("normal",selected["name"])
        self.assertEqual(2,scroll.call_count)

    def test_delete_page_comparison_includes_upper_deck_rows(self):
        first=frame(); second=first.copy(); second[390:510,80:650]=120
        self.assertFalse(ProduceHIFChangeSourceAuto._same_page(first,second,top=380))

    def test_return_to_target_does_not_probe_unrelated_cards(self):
        controller=Controller(); ctx=self.context(controller); action=ProduceHIFChangeSourceAuto()
        first=frame(); bottom=frame(); bottom[700:1050,70:650]=120
        blocked=card('blocked','SSR'); rare=card('rare','R')
        unrelated=dict(card('unrelated','SSR'),identity_verified=False)
        relocated=dict(blocked,box=[80,660,114,117])
        pages=[(first,[blocked]),(bottom,[rare]),(bottom,[rare]),(bottom,[rare]),
               (first,[unrelated,relocated])]
        with patch.object(action,'_page',side_effect=pages), patch.object(action,'_scroll'), \
             patch('custom.action.produce.ProduceShoppingAuto._probe_removal_candidates',
                   side_effect=lambda c,items,*a,**k:items) as probe:
            selected=action._scan_source(ctx,{'blocked'},set(),set())
        self.assertEqual('blocked',selected['name'])
        self.assertEqual(2,probe.call_count)  # forward scan only, no return-page sweep
        self.assertFalse(selected['detail_selected'])

    def test_later_overlap_identity_resolves_earlier_page_failure(self):
        controller=Controller(); ctx=self.context(controller); action=ProduceHIFChangeSourceAuto()
        first=np.zeros((1279,720,3),np.uint8); bottom=np.full_like(first,30)
        tile=np.random.default_rng(8).integers(0,256,(116,116,3),dtype=np.uint8)
        first[650:766,80:196]=tile; bottom[800:916,80:196]=tile
        unknown=dict(card('','R'),identity_verified=False,probe_failed=True,box=[80,650,116,116])
        known=dict(card('rare','R'),box=[80,800,116,116])
        pages=[(first,[unknown]),(bottom,[known]),(bottom,[known]),(bottom,[known])]
        with patch.object(action,'_page',side_effect=pages), patch.object(action,'_scroll'), \
             patch('custom.action.produce.ProduceShoppingAuto._probe_removal_candidates',
                   side_effect=lambda c,items,*a,**k:items):
            selected=action._scan_source(ctx,set(),set(),set())
        self.assertEqual('rare',selected['name'])


if __name__=="__main__": unittest.main()

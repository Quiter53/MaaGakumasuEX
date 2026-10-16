"""Weak tile identities, exact detail probes and verified deletion lifecycle."""
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
from custom.produce.shop import ProduceShoppingAuto, ProduceHIFDeleteAuto
from custom.produce.rewards import ProduceChooseRewardAuto, ProduceHIFChangeSourceAuto
from custom.removal import read_selected_card, read_delete_confirmation, read_delete_button, _bilinear_double, _double_title_crop


def card(name, rarity, confidence=.9, box=None):
    return dict(name=name, name_key=name, rarity=rarity, name_confidence=confidence,
                rarity_confidence=.5, box=box or [80,800,120,120])


def row(text, box=None):
    return SimpleNamespace(text=text, box=box or [200,280,150,30], score=.99)


def context():
    ctx = Mock()
    ctx.tasker.stopping = False
    ctx.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value = np.zeros((1279,720,3),np.uint8)
    return ctx


class VerifiedRemovalTest(unittest.TestCase):
    def test_strong_art_and_frame_skip_probe_without_forging_ocr_verification(self):
        strong = dict(card('strong', 'SR', .96), name_score=.50, name_margin=.20,
                      frame_rarity='SR', rarity_confidence=.80)
        result, ctx = self.probe([strong], [], {'strong'})
        ctx.tasker.controller.post_click.assert_not_called()
        self.assertFalse(result[0].get('identity_verified', False))
        self.assertEqual('strong', ProduceHIFChangeSourceAuto._choose_source_candidate(result, set(), set(), {'strong'})['name'])

    def test_strong_art_with_conflicting_frame_still_probes(self):
        strong = dict(card('strong', 'SSR', .99), name_score=.60, name_margin=.25,
                      frame_rarity='N', rarity_confidence=.80)
        _, ctx = self.probe([strong], [None] * 4)
        ctx.tasker.controller.post_click.assert_called_once()

    def test_ambiguous_runner_up_still_probes_when_membership_matters(self):
        strong = dict(card('strong', 'SR', .99), name_score=.60, name_margin=.06,
                      frame_rarity='SR', rarity_confidence=.80)
        _, ctx = self.probe([strong], [None] * 4, {'strong'})
        ctx.tasker.controller.post_click.assert_called_once()

    def probe(self, cards, exact, replacements=None):
        ctx, shop = context(), ProduceShoppingAuto()
        with patch("time.sleep"), \
             patch("custom.produce.shared._detect_disconnect_screen", return_value=False), \
             patch("custom.removal.selected_card_box_matches", return_value=True), \
             patch("custom.removal.read_selected_card", side_effect=list(exact) + [None] * (len(cards) * 4)):
            result = shop._probe_removal_candidates(ctx, cards, set(), set(), replacements or set(), change=True)
        return result, ctx

    def test_log_weak_initial_n_is_detail_verified_before_replacement_ssr(self):
        normal = card("思考の基本","N",.536)
        exact = {**normal, "name_confidence":1., "rarity_confidence":1., "identity_verified":True}
        result, ctx = self.probe([normal,card("鳴り止まない拍手","SSR")], [exact,exact], {"鳴り止まない拍手"})
        selected = ProduceHIFChangeSourceAuto._choose_source_candidate(result,set(),set(),{"鳴り止まない拍手"})
        self.assertEqual("思考の基本",selected["name"])
        ctx.tasker.controller.post_click.assert_called_once_with(140,860)

    def test_confident_unlisted_ssr_thumbnail_cannot_hide_initial_n(self):
        visual = card("夏夜に咲く思い出","SSR",.98)
        exact = {**card("思考の基本","N",1.),"identity_verified":True,"rarity_confidence":1.}
        result,ctx=self.probe([visual],[exact,exact])
        selected=ProduceShoppingAuto._choose_delete_candidate(result,set(),set())
        self.assertEqual(("思考の基本",3),(selected["name"],selected["delete_rank"]))
        ctx.tasker.controller.post_click.assert_called_once_with(140,860)

    def test_confident_visual_replacement_still_requires_exact_detail(self):
        result,ctx=self.probe([card("意地","SR",.99)],[None]*4,{"意地"})
        self.assertIsNone(ProduceHIFChangeSourceAuto._choose_source_candidate(result,set(),set(),{"意地"}))
        self.assertTrue(result[0]["probe_failed"])
        ctx.tasker.controller.post_click.assert_called_once_with(140,860)

    def test_n_frame_is_probed_first_even_when_artwork_says_ssr(self):
        normal=dict(card('wrong-art','SSR',.98,box=[80,650,116,116]),frame_rarity='N',rarity_confidence=.8)
        other=card('replacement','SR',.99,box=[374,800,116,116])
        exact=dict(card('starter','N',1.),identity_verified=True)
        result,ctx=self.probe([other,normal],[exact,exact],{'replacement'})
        ctx.tasker.controller.post_click.assert_called_once_with(138,708)
        self.assertEqual('starter',ProduceShoppingAuto._choose_delete_candidate(result,set(),set())['name'])

    def test_log_weak_replacement_sr_precedes_high_confidence_ssr(self):
        sr = card("意地","SR",.343)
        exact = {**sr, "name_confidence":1., "rarity_confidence":1., "identity_verified":True}
        replacement = {"意地","鳴り止まない拍手"}
        result, _ = self.probe([card("鳴り止まない拍手","SSR"),sr],[exact,exact],replacement)
        selected = ProduceHIFChangeSourceAuto._choose_source_candidate(result,set(),replacement,replacement)
        self.assertEqual("意地",selected["name"])

    def test_visual_n_misidentified_as_unlisted_ssr_is_protected(self):
        normal = card("思考の基本","N",.3)
        exact = card("夏夜に咲く思い出","SSR",1.)
        exact.update(identity_verified=True, rarity_confidence=1.)
        result, _ = self.probe([normal],[exact,exact])
        self.assertIsNone(ProduceShoppingAuto._choose_delete_candidate(result,set(),set()))

    def test_missing_or_changing_detail_never_promotes_candidate(self):
        for exact in ([None]*4, [card("a","N"),card("b","N")]*2):
            result, _ = self.probe([card("思考の基本","N",.3)],exact)
            self.assertEqual(0.,result[0]["name_confidence"])

    def test_stopped_probe_does_not_click(self):
        ctx = context(); ctx.tasker.stopping=True
        ProduceShoppingAuto()._probe_removal_candidates(ctx,[card("思考の基本","N",.3)],set(),set(),set())
        ctx.tasker.controller.post_click.assert_not_called()

    def test_shop_recognition_calls_real_keyword_only_evaluator(self):
        # Do not mock _evaluate: a permissive Mock hid the positional API bug.
        ctx = context()
        ctx.run_recognition.return_value = SimpleNamespace(hit=True, all_results=[
            SimpleNamespace(label="cards", box=[80,800,120,120],score=.9),
            SimpleNamespace(label="useless", box=[220,800,120,120],score=.8)])
        visual = SimpleNamespace(name="思考の基本",rarity="N",confidence=.9,score=.6,margin=.128,extra_action=False)
        rarity = SimpleNamespace(rarity="N",confidence=.327,best_distance=.039,margin=.082,signature=[.8,.85,.9])
        with patch("custom.produce.rewards.classify_rarity", return_value=rarity), \
             patch("custom.square_identity.recognize", return_value=visual) as square, \
             patch("custom.produce.rewards.match_card_visual") as legacy:
            found = ProduceShoppingAuto()._recognize_delete_cards(ctx,np.zeros((1279,720,3),np.uint8))
        self.assertEqual("思考の基本",found[0]["name"])
        self.assertEqual([80,800,120,120],found[0]["box"])
        self.assertEqual(2,len(found))
        self.assertEqual(2, square.call_count)
        legacy.assert_not_called()
        from custom.deck_scan import scan_identity_known
        self.assertTrue(all(item['square_art_attempted'] and scan_identity_known(item) for item in found))
        self.assertTrue(all(not item.get('identity_verified') for item in found))

    def test_unknown_sr_detail_can_reveal_replacement_identity(self):
        unknown=card("","SR",0.)
        exact={**card("意地","SR",1.),"identity_verified":True,"rarity_confidence":1.}
        result,_=self.probe([unknown],[exact,exact],{"意地"})
        self.assertEqual("意地",ProduceHIFChangeSourceAuto._choose_source_candidate(result,set(),set(),{"意地"})["name"])

    def test_detail_reader_rejects_fuzzy_or_multiple_catalog_names(self):
        ctx = context()
        with patch("custom.removal.ocr",return_value=[row("思考の基本")]):
            self.assertEqual("N",read_selected_card(ctx,None)["rarity"])
        with patch("custom.removal.ocr",return_value=[row("思考の基")]):
            self.assertIsNone(read_selected_card(ctx,None))
        with patch("custom.removal.ocr",return_value=[row("思考の基本"),row("意地")]):
            self.assertIsNone(read_selected_card(ctx,None))

    def test_source_title_has_padding_and_uses_requested_point_eight_gate(self):
        ctx = context()
        title = row("輝くキミへ+")
        title.score = .80
        with patch("custom.removal.ocr", return_value=[title]) as reader:
            self.assertEqual("輝くキミへ", read_selected_card(ctx, None, change=True)["name"])
            reader.assert_called_once_with(ctx, None, [175, 250, 490, 65], [".*"])
        title.score = .799
        with patch("custom.removal.ocr", return_value=[title]):
            self.assertIsNone(read_selected_card(ctx, None, change=True))

    def test_native_point_848_title_does_not_need_extra_ocr(self):
        ctx = context()
        title = row('輝くキミへ+', [182,264,179,40]); title.score = .847979
        with patch('custom.removal.ocr', return_value=[title]):
            result = read_selected_card(ctx,np.zeros((1279,720,3),np.uint8),change=True)
        self.assertEqual('輝くキミへ', result['name'])
        ctx.run_recognition.assert_not_called()

    def test_requested_point_eight_name_gate_applies_to_delete_and_confirmation(self):
        for flags in ({}, {'confirmation':True}, {'change':True,'confirmation':True}):
            for score, accepted in ((.80, True), (.799, False)):
                title = row('輝くキミへ+'); title.score = score
                with self.subTest(flags=flags, score=score), patch('custom.removal.ocr',return_value=[title]):
                    result = read_selected_card(context(),None,**flags)
                self.assertEqual(accepted, result is not None)
                if accepted:
                    self.assertEqual('輝くキミへ', result['name'])

    def test_point_eight_still_rejects_fuzzy_and_conflicting_names(self):
        for names in (['輝くキミ'], ['思考の基'], ['輝くキミへ','意地']):
            rows=[row(name) for name in names]
            for item in rows:
                item.score=.80
            for flags in ({}, {'change':True}, {'confirmation':True}):
                ctx=context()
                with self.subTest(names=names, flags=flags), patch('custom.removal.ocr',return_value=rows):
                    self.assertIsNone(read_selected_card(ctx,None,**flags))
                ctx.run_recognition.assert_not_called()

    def test_weak_exact_title_retries_only_its_box_and_needs_same_high_score_name(self):
        image = np.zeros((1279, 720, 3), np.uint8)
        title = row("輝くキミへ+", [182, 264, 179, 40]); title.score = .78
        for retry_text, score, accepted in (("輝くキミへ+", .80, True), ("輝くキミへ+", .799, False),
                                             ("意地", .99, False), ("輝くキミ", .99, False)):
            ctx = context()
            retry = row(retry_text); retry.score = score
            ctx.run_recognition.return_value = SimpleNamespace(hit=True, filtered_results=[retry])
            with patch("custom.removal.ocr", return_value=[title]):
                result = read_selected_card(ctx, image, change=True)
            self.assertEqual(accepted, result is not None, (retry_text, score))
            override = ctx.run_recognition.call_args_list[0].kwargs['pipeline_override']['ProduceRecognitionPoint']
            self.assertTrue(override['only_rec'])
            self.assertEqual([180, 262, 183, 44], override['roi'])
            self.assertLessEqual(ctx.run_recognition.call_count, 3)

    def test_bilinear_double_half_pixel_weights_and_edge_clamping(self):
        pixels = np.array([[0, 40], [80, 120]], np.uint8)[:, :, None]
        expected = np.array([[0, 10, 30, 40], [20, 30, 50, 60],
                             [60, 70, 90, 100], [80, 90, 110, 120]], np.uint8)
        resized = _bilinear_double(pixels)
        np.testing.assert_array_equal(expected, resized[:, :, 0])
        self.assertEqual(np.uint8, resized.dtype)

    def test_weak_exact_title_has_one_bounded_scaled_retry_with_unchanged_gate(self):
        image = np.zeros((1279, 720, 3), np.uint8)
        title = row('輝くキミへ+', [182, 264, 179, 40]); title.score = .78
        for name, score, accepted in [('輝くキミへ+', .80, True), ('輝くキミへ+', .799, False),
                                      ('意地', .99, False), ('輝くキミ', .99, False)]:
            ctx = context()
            weak = row('輝くキミへ+'); weak.score = .79
            scaled = row(name); scaled.score = score
            ctx.run_recognition.side_effect = [SimpleNamespace(hit=True, filtered_results=[value])
                                               for value in (weak, weak, scaled)]
            with patch('custom.removal.ocr', return_value=[title]):
                result = read_selected_card(ctx, image, change=True)
            self.assertEqual(accepted, result is not None, (name, score))
            self.assertEqual(3, ctx.run_recognition.call_count)
            call = ctx.run_recognition.call_args_list[-1]
            self.assertEqual((88, 366, 3), call.args[1].shape)
            parameters = call.kwargs['pipeline_override']['ProduceRecognitionPoint']
            self.assertEqual([0, 0, 366, 88], parameters['roi'])
            self.assertTrue(parameters['only_rec'])

    def test_local_title_resize_matches_full_frame_including_roi_edges(self):
        image = np.random.default_rng(41).integers(0, 256, size=(75, 250, 3), dtype=np.uint8)
        full = _bilinear_double(image)
        for x, y, width, height in ((0, 0, 30, 20), (2, 3, 180, 40), (220, 55, 30, 20)):
            with self.subTest(roi=(x, y, width, height)):
                local = _double_title_crop(image, [x, y, width, height])
                np.testing.assert_array_equal(local, full[y*2:(y+height)*2, x*2:(x+width)*2])
                self.assertTrue(local.flags.c_contiguous)

    def test_ambiguous_weak_titles_do_not_get_fishing_retries(self):
        ctx = context()
        titles = [row("輝くキミへ+"), row("意地")]
        for title in titles:
            title.score = .79
        with patch("custom.removal.ocr", return_value=titles):
            self.assertIsNone(read_selected_card(ctx, np.zeros((1279,720,3),np.uint8), change=True))
        ctx.run_recognition.assert_not_called()

    def test_confirmation_requires_title_question_identity_debit_and_both_buttons(self):
        def text_reader(ctx,img,roi,expected):
            return {
                15:[row("スキルカード削除",[57,635,257,31])],30:[row("Pポイントを使用してスキルカードを削除しますか?",[73,717,567,22])],
                350:[row("削除",[488,1138,68,39])],65:[row("×キャンセル",[106,1137,209,42])],
                175:[row("夏夜に咲く思い出")],265:[row("420",[308,1002,61,31]),row("358",[461,1002,61,31])]
            }.get(roi[0],[])
        image=np.zeros((1279,720,3),np.uint8)
        image[1130:1185,385:570]=(40,150,250)
        ctx=context(); ctx.run_recognition.return_value=SimpleNamespace(hit=False)
        with patch("custom.removal.ocr",side_effect=text_reader) as reader:
            found=read_delete_confirmation(ctx,image)
            self.assertEqual((420,358),(found["before"],found["after"]))
            self.assertEqual((522,1157),found["confirm"])
        question_roi=next(c.args[2] for c in reader.call_args_list if c.args[2][0]==30)
        self.assertEqual([30,681,660,115],question_roi)
        for absent in (15,30,350,65,175,265):
            with patch("custom.removal.ocr",side_effect=lambda c,i,r,e:[] if r[0]==absent else text_reader(c,i,r,e)):
                self.assertIsNone(read_delete_confirmation(ctx,image))

    def test_confirmation_anchors_question_for_short_and_long_card_effects(self):
        for title_y, question_y, name in ((635,717,"思考の基本+"),(527,610,"夏夜に咲く思い出")):
            with self.subTest(name=name):
                def text_reader(ctx,img,roi,expected):
                    if roi[0]==15: return [row("スキルカード削除",[57,title_y,257,31])]
                    if roi[0]==30:
                        self.assertLessEqual(roi[1],question_y)
                        self.assertGreaterEqual(roi[1]+roi[3],question_y+22)
                        return [row("Pポイントを使用してスキルカードを削除しますか?",[73,question_y,567,22])]
                    if roi[0]==175:
                        self.assertGreater(roi[1],question_y+22)
                        return [row(name)]
                    if roi[0]==65: return [row("キャンセル")]
                    if roi[0]==265: return [row("420",[308,1002,61,31]),row("358",[461,1002,61,31])]
                    return []
                with patch("custom.removal.ocr",side_effect=text_reader), \
                     patch("custom.removal.read_delete_button",return_value=[390,1138,172,40]):
                    self.assertIsNotNone(read_delete_confirmation(context(),None))

    def test_delete_button_restores_template_and_requires_enabled_orange(self):
        ctx=context(); image=np.zeros((1279,720,3),np.uint8)
        box=[390,1138,172,40]; image[1138:1178,390:562]=(40,150,250)
        ctx.run_recognition.return_value=SimpleNamespace(hit=True,best_result=SimpleNamespace(box=box,score=.972))
        with patch("custom.removal.ocr",return_value=[]) as reader:
            self.assertEqual(box,read_delete_button(ctx,image,confirmation=True))
            reader.assert_not_called()
        params=ctx.run_recognition.call_args.kwargs["pipeline_override"]["ProduceChooseDeleteClick"]
        self.assertEqual(["produce/delete_confirm.png"],params["template"])
        self.assertEqual(.75,params["threshold"])
        image[1138:1178,390:562]=(60,120,180)
        with patch("custom.removal.ocr",return_value=[row("削除",box)]):
            self.assertIsNone(read_delete_button(ctx,image,confirmation=True))

    def test_delete_button_rejects_weak_template_and_wrong_location(self):
        ctx=context(); image=np.full((1279,720,3),(40,150,250),np.uint8)
        for box,score in (([390,1138,172,40],.607),([300,700,172,40],.98)):
            ctx.run_recognition.return_value=SimpleNamespace(hit=True,best_result=SimpleNamespace(box=box,score=score))
            with patch("custom.removal.ocr",return_value=[]):
                self.assertIsNone(read_delete_button(ctx,image,confirmation=True))

    def test_delete_candidates_stay_out_of_ui_log_even_with_diagnostics_enabled(self):
        ctx=context(); ctx.run_recognition.return_value=SimpleNamespace(hit=True,all_results=[
            SimpleNamespace(label="cards",box=[80,800,120,120],score=.9)])
        rarity=SimpleNamespace(rarity="N",confidence=.5,best_distance=.04,margin=.08,signature=[.8,.85,.9])
        with patch("custom.produce.rewards.classify_rarity",return_value=rarity), \
             patch("custom.produce.rewards.match_card_visual",return_value=None), \
             patch("custom.produce.rewards.reconcile_visual_identity",return_value=None), \
             patch("custom.produce.shared._diagnostics_enabled",return_value=True), \
             patch("custom.produce.shared._diagnostic_info") as diagnostics, \
             patch("custom.produce.shared.logger.info") as info:
            self.assertEqual(1,len(ProduceShoppingAuto()._recognize_delete_cards(ctx,np.zeros((1279,720,3),np.uint8))))
        info.assert_not_called(); diagnostics.assert_not_called()

    def test_delete_scan_verified_n_stops_without_inferring_all_cards_visible(self):
        ctx=context(); action=ProduceHIFChangeSourceAuto()
        normal=card("思考の基本","N")
        with patch.object(action,"_page",return_value=(np.zeros((1279,720,3),np.uint8),[normal])), \
             patch.object(action,"_scroll") as scroll, \
             patch.object(ProduceShoppingAuto,"_probe_removal_candidates",side_effect=lambda c,items,*a,**k:[{**item,"identity_verified":True} for item in items]), \
             patch("custom.produce.shared.logger.info") as info:
            self.assertEqual("思考の基本",action._scan_source(ctx,set(),set(),set(),change=False)["name"])
        scroll.assert_not_called()
        self.assertIn("最高优先级 N",info.call_args.args[0])

    def test_delete_scan_without_n_scrolls_and_selects_later_n(self):
        ctx=context(); action=ProduceHIFChangeSourceAuto()
        first=np.zeros((1279,720,3),np.uint8); second=np.full_like(first,120)
        with patch.object(action,"_page",side_effect=[(first,[card("R卡","R")]),(second,[card("思考の基本","N")])]), \
             patch.object(action,"_scroll") as scroll, \
             patch.object(ProduceShoppingAuto,"_probe_removal_candidates",side_effect=lambda c,items,*a,**k:[{**item,"identity_verified":True} for item in items]):
            self.assertEqual("思考の基本",action._scan_source(ctx,set(),set(),set(),change=False)["name"])
        scroll.assert_called_once_with(ctx,forward=True)

    def test_delete_scan_checks_scroll_movement_before_accepting_r_on_only_page(self):
        ctx=context(); action=ProduceHIFChangeSourceAuto()
        with patch.object(action,"_page",return_value=(np.zeros((1279,720,3),np.uint8),[card("R卡","R")])), \
             patch.object(action,"_scroll") as scroll, \
             patch.object(ProduceShoppingAuto,"_probe_removal_candidates",side_effect=lambda c,items,*a,**k:[{**item,"identity_verified":True} for item in items]):
            self.assertEqual("R卡",action._scan_source(ctx,set(),set(),set(),change=False)["name"])
        self.assertEqual([True, True], [call.kwargs['forward'] for call in scroll.call_args_list])

    def test_delete_success_requires_two_final_debit_frames(self):
        ctx = context(); shop=ProduceShoppingAuto()
        invoice=dict(before=420,after=358,confirm=(522,1157))
        selected={**card("思考の基本","N"),"delete_rank":3}
        with patch("time.sleep"), \
             patch("custom.produce.shared._detect_disconnect_screen",return_value=False), \
             patch("custom.removal.read_delete_confirmation",return_value=None), \
             patch.object(shop,"_recognize_points",side_effect=[400,358,358]):
            self.assertEqual("deleted",shop._commit_delete(ctx,invoice,selected))
        ctx.tasker.controller.post_click.assert_called_once_with(522,1157)

    def test_unchanged_confirmation_never_claims_delete_success_or_clicks_twice(self):
        ctx=context(); shop=ProduceShoppingAuto()
        invoice=dict(before=420,after=358,confirm=(522,1157))
        with patch("time.sleep"), \
             patch("custom.produce.shared._detect_disconnect_screen",return_value=False), \
             patch("custom.removal.read_delete_confirmation",return_value=invoice), \
             patch.object(shop,"_recognize_points",return_value=420):
            self.assertEqual("uncertain",shop._commit_delete(ctx,invoice,{**card("n","N"),"delete_rank":3}))
        ctx.tasker.controller.post_click.assert_called_once_with(522,1157)

    def test_delete_deck_flow_waits_for_matching_stable_confirmation(self):
        ctx=context(); shop=ProduceShoppingAuto()
        selected={**card("思考の基本","N"),"delete_rank":3}
        invoice=dict(selected=selected,before=420,after=358,confirm=(522,1157),cancel=(210,1158))
        wrong={**invoice,"selected":card("夏夜に咲く思い出","SSR")}
        with patch("time.sleep"), \
             patch("custom.produce.shared._detect_disconnect_screen",return_value=False), \
             patch.object(ProduceHIFChangeSourceAuto,"_rewind_source",return_value=(np.zeros((1279,720,3),np.uint8),[])), \
             patch.object(ProduceHIFChangeSourceAuto,"_scan_source",return_value=selected), \
             patch.object(shop,"_verify_delete_selection",return_value=selected), \
             patch("custom.removal.read_delete_button",return_value=[394,1135,164,45]), \
             patch("custom.removal.read_delete_confirmation",side_effect=[wrong,invoice,invoice]), \
             patch.object(shop,"_commit_delete",return_value="deleted") as commit:
            self.assertEqual("deleted",shop._delete_from_deck(ctx,set(),set(),set()))
        commit.assert_called_once_with(ctx,invoice,selected)
        self.assertEqual([(140,860),(476,1157)],[call.args for call in ctx.tasker.controller.post_click.call_args_list])

    def test_delete_wrong_card_confirmation_is_not_committed(self):
        ctx=context(); shop=ProduceShoppingAuto()
        selected={**card("思考の基本","N"),"delete_rank":3}
        wrong=dict(selected=card("夏夜に咲く思い出","SSR"),before=420,after=358,confirm=(522,1157))
        with patch("time.sleep"), \
             patch("custom.produce.shared._detect_disconnect_screen",return_value=False), \
             patch.object(ProduceHIFChangeSourceAuto,"_rewind_source",return_value=(np.zeros((1279,720,3),np.uint8),[])), \
             patch.object(ProduceHIFChangeSourceAuto,"_scan_source",return_value=selected), \
             patch.object(shop,"_verify_delete_selection",return_value=selected), \
             patch("custom.removal.read_delete_button",return_value=[394,1135,164,45]), \
             patch("custom.removal.read_delete_confirmation",return_value=wrong), \
             patch.object(shop,"_commit_delete") as commit:
            self.assertEqual("uncertain",shop._delete_from_deck(ctx,set(),set(),set()))
        commit.assert_not_called()

    def test_detail_probed_delete_card_is_not_toggled_off_by_a_second_click(self):
        ctx=context(); shop=ProduceShoppingAuto()
        selected={**card("思考の基本","N"),"delete_rank":3,"detail_selected":True}
        invoice=dict(selected=selected,before=420,after=358,confirm=(522,1157),cancel=(210,1158))
        with patch("time.sleep"), \
             patch("custom.produce.shared._detect_disconnect_screen",return_value=False), \
             patch.object(ProduceHIFChangeSourceAuto,"_rewind_source",return_value=(np.zeros((1279,720,3),np.uint8),[])), \
             patch.object(ProduceHIFChangeSourceAuto,"_scan_source",return_value=selected), \
             patch.object(shop,"_verify_delete_selection",return_value=selected), \
             patch("custom.removal.read_delete_button",return_value=[394,1135,164,45]), \
             patch("custom.removal.read_delete_confirmation",return_value=invoice), \
             patch.object(shop,"_commit_delete",return_value="deleted"):
            self.assertEqual("deleted",shop._delete_from_deck(ctx,set(),set(),set()))
        ctx.tasker.controller.post_click.assert_called_once_with(476,1157)

    def test_detail_probed_change_card_only_clicks_submit_after_identity_verification(self):
        ctx=context(); before=np.zeros((1279,720,3),np.uint8)
        before[670:1090,60:650]=150; before[1140:1170,420:585]=(40,150,250)
        after=np.zeros_like(before)
        # Availability is checked once before the two identity observations.
        ctx.tasker.controller.post_screencap.return_value.wait.return_value.get.side_effect=[before,before,before,after,after]
        selected={**card("思考の基本","N"),"delete_rank":3,"detail_selected":True}
        with patch("time.sleep"), \
             patch("custom.produce.shared._detect_disconnect_screen",return_value=False), \
             patch("custom.hif.is_hif_change_source",side_effect=[True,True,True,False,False]), \
             patch("custom.removal.selected_card_box_matches", return_value=True), \
             patch("custom.removal.read_selected_card",return_value=selected), \
             patch.object(ProduceHIFChangeSourceAuto,"_ocr",return_value=[row("チェンジ",[460,1140,120,30])]):
            self.assertTrue(ProduceHIFChangeSourceAuto()._submit(ctx,selected))
        ctx.tasker.controller.post_click.assert_called_once_with(520,1155)

    def test_residual_delete_modal_is_cancelled_before_strategy_rescan(self):
        ctx=context(); action=ProduceHIFDeleteAuto()
        action._allow_shop_delete=Mock(return_value=True)
        lists={key:{"enabled":False,"cards":[]} for key in ("blacklist","whitelist","replacementList")}
        with patch("time.sleep"), \
             patch("custom.produce.shared._detect_disconnect_screen",return_value=False), \
             patch("custom.produce.shared._load_card_lists",return_value=lists), \
             patch("custom.shop.ocr",side_effect=[[row("スキルカード削除")],[row("×キャンセル",[106,1137,209,42])],[]]), \
             patch.object(action,"_delete_from_deck",return_value="deleted") as delete, \
             patch.object(action,"_reenter_shop",return_value=True), \
             patch.object(ProduceShoppingAuto,"run",return_value=True) as shop:
            self.assertTrue(action.run(ctx,SimpleNamespace()))
        ctx.tasker.controller.post_click.assert_called_once_with(210,1158)
        delete.assert_called_once_with(ctx,set(),set(),set())
        self.assertTrue(action._skip_delete)
        shop.assert_called_once()

    def test_hif_delete_cannot_reach_old_first_card_fallback(self):
        nodes=json.loads((ROOT/"assets/resource/base/pipeline/ProduceHIF.json").read_text(encoding="utf-8"))
        targets=nodes["ProduceEntryHIF"]["next"]
        self.assertIn("[JumpBack]ProduceHIFDeleteFlag",targets)
        self.assertNotIn("[JumpBack]ProduceChooseDeleteFlag",targets)
        self.assertEqual("ProduceHIFDeleteAuto",nodes["ProduceHIFDeleteFlag"]["action"]["param"]["custom_action"])
        self.assertNotIn("produce/delete_icon.png",nodes["ProduceHIFCopyExchangeFlag"]["recognition"]["param"]["template"])

    def test_change_submit_does_not_trust_a_different_selected_detail(self):
        ctx=context(); image=np.zeros((1279,720,3),np.uint8); image[1140:1170,420:585]=(40,150,250)
        ctx.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value=image
        with patch("time.sleep"), \
             patch("custom.produce.shared._detect_disconnect_screen",return_value=False), \
             patch("custom.hif.is_hif_change_source",return_value=True), \
             patch("custom.removal.read_selected_card",return_value=card("other","SSR")), \
             patch.object(ProduceHIFChangeSourceAuto,"_ocr",return_value=[row("チェンジ",[460,1140,120,30])]):
            self.assertFalse(ProduceHIFChangeSourceAuto()._submit(ctx,{**card("思考の基本","N"),"delete_rank":3}))
        ctx.tasker.controller.post_click.assert_called_once_with(140,860)


if __name__ == "__main__": unittest.main()

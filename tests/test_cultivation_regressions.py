"""Count boundary, HIF course policy, price OCR and removal safety."""
import json
import sys
import unittest
from itertools import permutations, product
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
from custom.action.produce import (ProduceShoppingAuto, ProduceHIFIntervalShoppingAuto,
                                  ProduceHIFChooseCourseAuto, ProduceHIFChooseClassAuto,
                                  ProduceChooseNIAEventAuto, ProduceHIFChangeSourceAuto)
from custom.hif import read_hif_scores
from custom.shop import parse_shop_price


def detail(*texts, hit=True):
    rows = [SimpleNamespace(text=text, box=[377, 825 + i * 25, 110, 23], score=.99)
            for i, text in enumerate(texts)]
    return SimpleNamespace(hit=hit, all_results=rows, filtered_results=rows if hit else [],
                           best_result=rows[0] if rows else None)


def card(name, rarity, **values):
    return dict(name=name, name_key=name, rarity=rarity, name_confidence=.9,
                rarity_confidence=.8, box=[80, 800, 120, 120], **values)


class CultivationRegressionsTest(unittest.TestCase):
    def test_settlement_home_cannot_bypass_limited_loop(self):
        nodes = json.loads((ROOT / "assets/resource/base/pipeline/Produce.json").read_text(encoding="utf-8"))
        for name in ("ProduceEnd", "ProduceGeneration", "ProduceFinished"):
            targets = nodes[name]["next"]
            self.assertNotIn("ProduceHomeFlag", targets)
            self.assertIn("ProduceBackHome", targets)
        targets = nodes["ProduceBackHome"]["next"]
        self.assertEqual(["ProduceLoop", "ProduceComplete"], [name for name in targets if not name.startswith("[JumpBack]")])
        self.assertEqual("DirectHit", nodes["ProduceComplete"]["recognition"]["type"])
        self.assertNotIn("next", nodes["ProduceComplete"])
        self.assertEqual(["ProduceBackHome"], [name for name, node in nodes.items()
                                               if "ProduceComplete" in node.get("next", [])])
        self.assertNotIn("ProduceStart", targets)
        options = json.loads((ROOT / "assets/tasks/produce.json").read_text(encoding="utf-8"))["option"]
        for case in options["培育次数"]["cases"][:6]:
            self.assertEqual(int(case["name"]), case["pipeline_override"]["ProduceLoop"]["max_hit"])

    def test_price_accepts_joined_p_icon_and_fullwidth_digits(self):
        for text in ("P 100", "Ｐ１００", "100", "p75"):
            self.assertEqual(100 if '100' in text or '１００' in text else 75, parse_shop_price(text))
        for text in ("00", "0", "P", "SALE75", "75100", "100积分", "-100"):
            self.assertIsNone(parse_shop_price(text))

    def test_native_unfiltered_p_price_is_read(self):
        context = Mock()
        context.run_recognition.return_value = detail("P 100", hit=False)
        with patch("custom.shop.read_number", return_value=None):
            self.assertEqual(100, ProduceShoppingAuto._recognize_price(context, None, [374, 690, 126, 126]))

    def test_sale_uses_rightmost_payable_price(self):
        context = Mock()
        result = detail("P75", "52")
        result.all_results[1].box[0] = 450
        context.run_recognition.return_value = result
        self.assertEqual(52, ProduceShoppingAuto._recognize_price(context, None, [374, 690, 126, 126]))

    def test_unknown_weak_n_is_not_a_deletion_candidate(self):
        weak = card("", "N")
        weak.update(name_confidence=0, rarity_confidence=.098)
        self.assertIsNone(ProduceShoppingAuto._choose_delete_candidate([weak], set(), set()))
        weak.update(name_key="known", name_confidence=.9)
        self.assertIsNone(ProduceShoppingAuto._choose_delete_candidate([weak], set(), set()))

    def test_change_order_and_explicit_whitelist_override(self):
        items = [card("replacement_ssr", "SSR"), card("replacement_sr", "SR"),
                 card("rare", "R"), card("black", "SSR"), card("normal", "N")]
        replacements = {"replacement_ssr", "replacement_sr"}
        for expected in ("normal", "black", "rare", "replacement_sr", "replacement_ssr"):
            selected = ProduceHIFChangeSourceAuto._choose_source_candidate(items, {"black"}, replacements, replacements)
            self.assertEqual(expected, selected["name"])
            items = [item for item in items if item["name"] != expected]

    def test_selected_delete_requires_exact_catalog_identity_and_same_policy(self):
        context = Mock()
        image = np.zeros((1280,720,3), np.uint8)
        selected = card("test", "N")
        result = SimpleNamespace(matched_name="test", confidence=1., rarity="SSR")
        context.run_recognition.return_value = detail("test")
        action = ProduceShoppingAuto()
        with patch("custom.action.produce.match_card_catalog", return_value=result), \
             patch("custom.removal.selected_card_box_matches", return_value=True):
            self.assertIsNone(action._verify_delete_selection(context, image, selected, set(), set(), set()))
            result.rarity = "N"
            verified = action._verify_delete_selection(context, image, selected, set(), set(), set())
            self.assertTrue(verified["identity_verified"])
            result.matched_name = "different"
            self.assertIsNone(action._verify_delete_selection(context, image, selected, set(), set(), set()))

    def course_context(self):
        context = Mock()
        context.get_node_data.side_effect = lambda name: (
            {"enabled": False} if name == "ProduceSuggestion" else
            {"attach": {"lesson_attribute": "third"}} if name == "ProduceHIFStrategy" else
            {"action": {"param": {"custom_action_param": {"first": "Vo", "second": "Da"}}}})
        return context

    def course(self, score, sp=(False, False, False), suggestion="", order=("Vo", "Da"), available=("Vo", "Da", "Vi")):
        context = self.course_context()
        original = context.get_node_data.side_effect
        context.get_node_data.side_effect = lambda name: (
            {"enabled": bool(suggestion)} if name == "ProduceSuggestion" else
            {"action": {"param": {"custom_action_param": {"first": order[0], "second": order[1]}}}}
            if name == "ProduceChooseNIAEventFlag" else original(name))
        rows = [(name, [150 + i * 150, 1000, 120, 80], 1.) for i, name in enumerate(available)]
        with patch("custom.hif.read_hif_scores", return_value=score), \
             patch.object(ProduceChooseNIAEventAuto, "_get_sp_course", side_effect=sp), \
             patch.object(ProduceChooseNIAEventAuto, "_get_suggestion", side_effect=AssertionError("HIF does not follow suggestions")), \
             patch.object(ProduceChooseNIAEventAuto, "_get_health", side_effect=AssertionError("HIF course order does not need health")), \
             patch.object(ProduceChooseNIAEventAuto, "_get_current_points", side_effect=AssertionError("HIF course order does not need points")), \
             patch.object(ProduceChooseNIAEventAuto, "_choose_best_event", side_effect=AssertionError("NIA policy must stay separate")):
            return ProduceHIFChooseCourseAuto()._lesson_attribute(context, np.zeros((1279,720,3)), rows, "レッスン")

    def test_hif_course_has_no_score_limit_or_score_ocr_gate_and_class_stays_configured(self):
        for score in (None, {"Vo":2559,"Da":1000,"Vi":1000,"max":3200},
                      {"Vo":2560,"Da":2560,"Vi":2560,"max":3200},
                      {"Vo":3200,"Da":3200,"Vi":3200,"max":3200}):
            with self.subTest(score=score):
                self.assertEqual("Vo", self.course(score))
                self.assertEqual("Da", self.course(score, sp=(False, True, True)))
        self.assertEqual("Vi", ProduceHIFChooseClassAuto()._lesson_attribute(self.course_context(), None, [], "授業"))

    def test_hif_sp_and_normal_order_for_all_attribute_preferences_and_sp_combinations(self):
        attrs = ("Vo", "Da", "Vi")
        for order in permutations(attrs):
            for sp in product((False, True), repeat=3):
                with self.subTest(order=order, sp=sp):
                    expected = next((attr for attr in order if sp[attrs.index(attr)]), order[0])
                    self.assertEqual(expected, self.course(None, sp=sp, order=order[:2]))

    def test_reported_blue_yellow_sp_choose_blue_even_above_eighty_percent(self):
        score = {"Vo":1764,"Da":2662,"Vi":69,"max":3200}
        self.assertEqual("Da", self.course(score, sp=(False, True, True)))
        self.assertEqual("Vi", self.course(score, sp=(False, False, True)))

    def test_teacher_cannot_override_hif_sp_or_normal_order(self):
        self.assertEqual("Da", self.course(None, sp=(False, True, True), suggestion="ボーカル"))
        self.assertEqual("Vo", self.course(None, suggestion="ビジュアル"))

    def test_hif_uses_available_normal_courses_in_preference_order(self):
        for available, expected in ((("Vi", "Da"), "Da"), (("Vi",), "Vi"), ((), None)):
            with self.subTest(available=available):
                self.assertEqual(expected, self.course(None, available=available))

    def test_nia_course_policy_retains_eighty_percent_and_teacher_rules(self):
        policy = ProduceChooseNIAEventAuto()
        policy.first, policy.second = "Vo", "Da"
        score = {"Vo":1764,"Da":2662,"Vi":69,"max":3200}
        events = [{"Vo":[100,1000,120,80], "SP":False},
                  {"Da":[300,1000,120,80], "SP":True},
                  {"Vi":[500,1000,120,80], "SP":True}]
        health = {"current":28, "ratio":.85}
        self.assertEqual("Vo", policy._choose_best_event("", health, 200, score, events)["name"])
        self.assertEqual("Da", policy._choose_best_event("ダンス", health, 200, score, events)["name"])

    def test_mid_course_resume_uses_course_policy_not_fixed_class_attribute(self):
        context = self.course_context()
        context.tasker.stopping = False
        with patch("custom.hif.read_hif_choice_kind", return_value="course"), \
             patch.object(ProduceHIFChooseCourseAuto, "run", return_value=True) as course:
            self.assertTrue(ProduceHIFChooseClassAuto().run(context, SimpleNamespace()))
        course.assert_called_once()

    def test_hif_daily_scores_and_raised_hud_are_complete_or_fail_closed(self):
        context = Mock()
        context.run_recognition.side_effect = [detail("§1634", "/3200"), detail("2584/3200"), detail("1026", "/3200")]
        self.assertEqual({"Vo":1634,"Da":2584,"Vi":1026,"max":3200}, read_hif_scores(context, None))
        context.run_recognition.side_effect = [detail(), detail("2000/3200"), detail("2000/3200"), detail("2000/3200")]
        self.assertEqual(2000, read_hif_scores(context, None)["Vo"])
        context.run_recognition.return_value = detail("P100")
        context.run_recognition.side_effect = None
        self.assertIsNone(read_hif_scores(context, None))

    def interval_run(self, scans):
        context = Mock()
        context.get_node_data.return_value = {"attach": {}}
        context.run_recognition.return_value = detail(hit=False)
        context.tasker.stopping = False
        image = np.zeros((1279,720,3), np.uint8)
        context.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value = image
        action = ProduceHIFIntervalShoppingAuto()
        with patch("custom.action.produce.time.sleep"), patch("custom.action.produce._detect_disconnect_screen", return_value=False), \
             patch("custom.shop.read_interval_health", return_value=(80,80)), \
             patch("custom.shop.shop_occupied", return_value=set()), \
             patch.object(action, "_wait_shop_ready", return_value=image), \
             patch.object(action, "_is_shop_scene", return_value=True), \
             patch.object(action, "_recognize_points", return_value=438), \
             patch.object(action, "_recognize_drinks", side_effect=scans), \
             patch.object(action, "_purchase", return_value="bought") as purchase, \
             patch("custom.shop.read_refresh", return_value=None):
            result = action.run(context, SimpleNamespace())
        return result, context, purchase

    def test_interval_price_none_is_retried_not_successfully_exited(self):
        unknown = {"id":"429282", "name":"初星ホエイプロテイン", "box":[374,690,126,126], "price":None}
        known = {**unknown, "price":100}
        result, context, purchase = self.interval_run([[unknown], [known], []])
        self.assertTrue(result)
        purchase.assert_called_once()
        self.assertEqual(100, purchase.call_args.kwargs["expected_price"])
        context.run_task.assert_called_once_with("ProduceShoppingExit")
        result, context, purchase = self.interval_run([[unknown]] * 4)
        self.assertTrue(result)  # Bounded price retries cancel shopping, not cultivation.
        purchase.assert_not_called()
        context.run_task.assert_called_once_with("ProduceShoppingExit")
        context.tasker.controller.post_click.assert_not_called()

    def test_interval_buys_cheapest_then_remaining_target_then_refreshes_and_buys_again(self):
        ctx=Mock(); ctx.tasker.stopping=False; ctx.get_node_data.return_value={"attach":{}}
        ctx.run_recognition.return_value=detail(hit=False)
        state={"points":538,"image":np.zeros((1279,720,3),np.uint8)}
        ctx.tasker.controller.post_screencap.return_value.wait.return_value.get.side_effect=lambda:state["image"]
        action=ProduceHIFIntervalShoppingAuto()
        cheap={"id":"679991","name":"センブリソーダ","price":75,"box":[528,690,126,126]}
        expensive={"id":"429282","name":"初星ホエイプロテイン","price":100,"box":[374,690,126,126]}
        fresh={"id":"235611","name":"ブーストエキス","price":75,"box":[374,690,126,126]}
        purchases=[]; blocked=[]
        scans=iter([[expensive,cheap],[expensive],[],[fresh],[]])
        def scan(context,image,boxes):
            blocked.append([list(box) for box in boxes])
            return next(scans)
        def purchase(context,box,name,**kwargs):
            purchases.append(name); state["points"]-=kwargs["expected_price"]
            return "bought"
        def refresh(x,y):
            self.assertEqual((105,1066),(x,y))
            state["points"]-=10; state["image"]=np.full((1279,720,3),100,np.uint8)
            return Mock()
        ctx.tasker.controller.post_click.side_effect=refresh
        with patch("custom.action.produce.time.sleep"), \
             patch("custom.action.produce._detect_disconnect_screen",return_value=False), \
             patch("custom.shop.read_interval_health",return_value=(80,80)), \
             patch("custom.shop.shop_occupied",return_value=set()), \
             patch.object(action,"_wait_shop_ready",side_effect=lambda c:state["image"]), \
             patch.object(action,"_is_shop_scene",return_value=True), \
             patch.object(action,"_recognize_points",side_effect=lambda c,i:state["points"]), \
             patch.object(action,"_recognize_drinks",side_effect=scan), \
             patch.object(action,"_purchase",side_effect=purchase), \
             patch("custom.shop.read_refresh",side_effect=[{"point":(105,1066),"cost":10},None]):
            self.assertTrue(action.run(ctx,SimpleNamespace()))
        self.assertEqual(["センブリソーダ","初星ホエイプロテイン","ブーストエキス"],purchases)
        self.assertEqual([],blocked[0]); self.assertEqual([cheap["box"]],blocked[1])
        self.assertEqual([],blocked[3])  # refreshed stock clears purchased slots
        self.assertEqual(278,state["points"])
        ctx.tasker.controller.post_click.assert_called_once_with(105,1066)
        ctx.run_task.assert_called_once_with("ProduceShoppingExit")

    def test_interval_unverified_refresh_is_not_repeated_and_shopping_is_cancelled(self):
        ctx=Mock(); ctx.tasker.stopping=False; ctx.get_node_data.return_value={"attach":{}}
        ctx.run_recognition.return_value=detail(hit=False)
        image=np.zeros((1279,720,3),np.uint8)
        ctx.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value=image
        action=ProduceHIFIntervalShoppingAuto()
        with patch("custom.action.produce.time.sleep"), \
             patch("custom.action.produce._detect_disconnect_screen",return_value=False), \
             patch("custom.shop.read_interval_health",return_value=(80,80)), \
             patch("custom.shop.shop_occupied",return_value=set()), \
             patch.object(action,"_wait_shop_ready",return_value=image), \
             patch.object(action,"_is_shop_scene",return_value=True), \
             patch.object(action,"_recognize_points",return_value=438), \
             patch.object(action,"_recognize_drinks",return_value=[]), \
             patch("custom.shop.read_refresh",return_value={"point":(105,1066),"cost":10}):
            self.assertTrue(action.run(ctx,SimpleNamespace()))
        ctx.tasker.controller.post_click.assert_called_once_with(105,1066)
        ctx.run_task.assert_called_once_with("ProduceShoppingExit")

    def test_interval_four_different_targets_stops_before_refresh(self):
        ctx=Mock(); ctx.tasker.stopping=False; ctx.get_node_data.return_value={"attach":{}}
        ctx.run_recognition.return_value=detail(hit=False)
        image=np.zeros((1279,720,3),np.uint8)
        ctx.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value=image
        action=ProduceHIFIntervalShoppingAuto()
        owned={i:{"id":key,"verified":True} for i,key in enumerate(("767772","235611","679991","429282"))}
        with patch("custom.action.produce.time.sleep"), \
             patch("custom.action.produce._detect_disconnect_screen",return_value=False), \
             patch("custom.shop.read_interval_health",return_value=(80,80)), \
             patch("custom.shop.shop_occupied",return_value={0,1,2,3}), \
             patch.object(action,"_wait_shop_ready",return_value=image), \
             patch.object(action,"_is_shop_scene",return_value=True), \
             patch.object(action,"_recognize_points",return_value=438), \
             patch.object(action,"_read_owned_drinks",return_value=owned), \
             patch.object(action,"_recognize_drinks") as scan, \
             patch("custom.shop.read_refresh") as refresh:
            self.assertTrue(action.run(ctx,SimpleNamespace()))
        scan.assert_not_called(); refresh.assert_not_called()
        ctx.tasker.controller.post_click.assert_not_called()
        ctx.run_task.assert_called_once_with("ProduceShoppingExit")


if __name__ == "__main__":
    unittest.main()

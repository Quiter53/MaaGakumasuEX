"""Replay swallowed clicks, nested overlays and interrupted counter updates."""

import sys
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
from custom.action.produce import ProduceChooseRewardAuto, ProduceShoppingAuto, _REWARD_BUDGETS, _reset_produce_card_run_state
from custom.action.challenge import ChallengeAuto


class Completed:
    def __init__(self, value=None):
        self.value = value

    def wait(self):
        return self

    def get(self):
        return self.value


class Scene:
    """Controller whose page changes only when the intended button accepts a click."""

    IDS = {"reward": 1, "remove": 2, "detail": 3, "selection": 4, "error": 5, "shop": 6, "bubble": 7}

    def __init__(self, state="reward", swallowed=0, scale=1):
        self.state = state
        self.swallowed = swallowed
        self.scale = scale
        self.remaining = 2
        self.removed = False
        self.confirm_clicks = 0
        self.clicks = []
        self.recognitions = []
        self.on_click = None
        self.tasker = SimpleNamespace(stopping=False, controller=self)

    def frame(self):
        frame = np.zeros((1280, 720, 3), np.uint8)
        frame[0, 0, 0] = self.IDS[self.state]
        if self.state == "remove":
            frame[1122:1196, 445:600] = (0, 140, 245)
        if self.state == "selection":
            frame[8:62, 432:612] = (0, 140, 245)
        if self.state == "bubble":
            frame[975:1085, 60:660] = 255
        if self.scale != 1:
            frame = frame[::2, ::2].copy()
        return frame

    def post_screencap(self):
        return Completed(self.frame())

    def post_click(self, x, y):
        self.clicks.append((x, y))
        point = (round(x / self.scale), round(y / self.scale))
        if self.on_click:
            self.on_click(point)
        elif self.state == "reward" and point == ProduceChooseRewardAuto.REMOVE_BUTTON:
            self.state = "remove"
        elif self.state == "remove" and max(abs(a - b) for a, b in zip(point, ProduceChooseRewardAuto.REMOVE_CONFIRM_BUTTON)) <= 1:
            self.confirm_clicks += 1
            if self.confirm_clicks > self.swallowed:
                self.remaining -= 1
                self.removed = True
                self.state = "reward"
        elif self.state == "remove" and point == ProduceChooseRewardAuto.REMOVE_CANCEL_POINT:
            self.state = "reward"
        elif self.state == "detail" and point == ProduceChooseRewardAuto.CARD_DETAIL_CLOSE_POINT:
            self.state = "selection"
        elif self.state == "selection" and point == ProduceChooseRewardAuto.SELECT_BAR_CANCEL_POINT:
            self.state = "reward"
        elif self.state == "bubble" and point == ProduceShoppingAuto.COMMENT_BUBBLE_TAP_POINT:
            self.state = "shop"
        return Completed()

    def run_recognition(self, node, image, pipeline_override=None):
        self.recognitions.append(node)
        state = next(k for k, v in self.IDS.items() if v == image[0, 0, 0])
        states = {
            "ProduceChooseCardRewardFlag": {"reward"},
            "ProduceChooseGetFlag": {"reward"},
            "ProduceCancelRewardRemoveDialog": {"remove"},
            "ProduceCancelRewardSelection": {"selection"},
            "ProduceCloseCardDetail": {"detail"},
            "ProduceShoppingFlag": {"shop", "bubble"},
            "ProduceShoppingDrinkFull": {"shop"},
        }
        return SimpleNamespace(hit=state in states.get(node, set()), best_result=None)

    def cards(self, context, image):
        if self.state != "reward":
            raise AssertionError("detector was called on an overlay")
        names = ("allowed", "second", "third", "event") if self.removed else ("blocked", "second", "third", "event")
        return [{"name": name, "name_key": name, "rarity": "SSR", "extra_action": "yes",
                 "priority": 1400, "name_confidence": 1, "detector_score": .99, "label": "cards",
                 "box": [100 + index * 140, 820, 120, 120]} for index, name in enumerate(names)]


class RewardRecoveryTest(unittest.TestCase):
    def setUp(self):
        _reset_produce_card_run_state()
        self.sleep = patch("custom.action.produce.time.sleep").start()
        self.addCleanup(patch.stopall)
        patch("custom.action.produce._detect_disconnect_screen",
              side_effect=lambda c, i: "ProduceDisconnectError" if i[0, 0, 0] == Scene.IDS["error"] else None).start()
        patch.object(ProduceChooseRewardAuto, "_evaluate", side_effect=lambda c, i: c.cards(c, i)).start()

    def test_two_swallowed_confirmation_clicks_still_count_exactly_one_removal(self):
        scene = Scene(swallowed=2)
        lists = {"blacklist": {"enabled": True, "cards": ["blocked"]},
                 "whitelist": {"enabled": False, "cards": []}}
        with patch("custom.action.produce._load_card_lists", return_value=lists), patch.object(
            ProduceChooseRewardAuto, "_stable_action_counts", side_effect=lambda c, i: {"remove": c.remaining, "refresh": 0}
        ):
            self.assertTrue(ProduceChooseRewardAuto().run(scene, None))
        budget = _REWARD_BUDGETS["remove"]
        self.assertEqual((1, 1, None), (budget.remaining, budget.confirmed, budget.pending_before))
        self.assertEqual(3, scene.confirm_clicks)
        self.assertEqual(1, scene.clicks.count(ProduceChooseRewardAuto.REMOVE_BUTTON))
        self.assertEqual("reward", scene.state)

    def test_confirmation_is_bounded_when_all_clicks_are_swallowed(self):
        scene = Scene("remove", swallowed=100)
        image, cards = ProduceChooseRewardAuto()._recover_reward_page(scene, allow_remove=True)
        self.assertEqual([], cards)
        self.assertEqual(3, scene.confirm_clicks)
        self.assertEqual(2, scene.remaining)
        self.assertEqual(Scene.IDS["remove"], image[0, 0, 0])

    def test_unarmed_remove_dialog_is_cancelled_not_confirmed(self):
        scene = Scene("remove")
        _, cards = ProduceChooseRewardAuto()._recover_reward_page(scene)
        self.assertEqual([ProduceChooseRewardAuto.REMOVE_CANCEL_POINT], scene.clicks)
        self.assertEqual(4, len(cards))
        self.assertEqual(2, scene.remaining)

    def test_detail_and_selection_layers_close_in_order_before_detector(self):
        scene = Scene("detail")
        _, cards = ProduceChooseRewardAuto()._recover_reward_page(scene)
        self.assertEqual([ProduceChooseRewardAuto.CARD_DETAIL_CLOSE_POINT,
                          ProduceChooseRewardAuto.SELECT_BAR_CANCEL_POINT], scene.clicks)
        self.assertEqual(4, len(cards))

    def test_detail_thumbnails_are_rejected_before_detector(self):
        scene = Scene("detail")
        _, cards = ProduceChooseRewardAuto()._wait_for_stable_cards(scene)
        self.assertEqual([], cards)
        self.assertEqual([], scene.clicks)

    def test_orange_button_without_remove_text_is_not_confirmed(self):
        scene = Scene("remove")
        scene.run_recognition = lambda *a, **k: SimpleNamespace(hit=False)
        self.assertNotEqual("remove", ProduceChooseRewardAuto()._reward_overlay(scene, scene.frame()))

    def test_error_during_confirmation_stops_additional_clicks(self):
        scene = Scene("remove")
        scene.on_click = lambda point: setattr(scene, "state", "error")
        _, cards = ProduceChooseRewardAuto()._recover_reward_page(scene, allow_remove=True)
        self.assertEqual([], cards)
        self.assertEqual([ProduceChooseRewardAuto.REMOVE_CONFIRM_BUTTON], scene.clicks)

    def test_stopping_and_error_pages_never_receive_recovery_clicks(self):
        for state, stopping in (("remove", True), ("error", False)):
            with self.subTest(state=state):
                scene = Scene(state)
                scene.tasker.stopping = stopping
                _, cards = ProduceChooseRewardAuto()._recover_reward_page(scene, allow_remove=True)
                self.assertEqual(([], []), (cards, scene.clicks))

    def test_stubborn_detail_never_receives_first_reward_fallback_click(self):
        scene = Scene("detail")
        scene.on_click = lambda point: None
        self.assertTrue(ProduceChooseRewardAuto()._fallback_select(scene, scene.frame()))
        self.assertEqual(6, len(scene.clicks))
        self.assertTrue(all(point == ProduceChooseRewardAuto.CARD_DETAIL_CLOSE_POINT for point in scene.clicks))

    def test_fallback_uses_fresh_scene_not_cached_reward_frame(self):
        scene = Scene("reward")
        stale = scene.frame()
        scene.state = "error"
        ProduceChooseRewardAuto()._fallback_select(scene, stale)
        self.assertEqual([], scene.clicks)

    def test_final_card_click_is_aborted_when_network_error_appears(self):
        scene = Scene("reward")
        card = scene.cards(scene, scene.frame())[0]
        scene.state = "error"
        self.assertTrue(ProduceChooseRewardAuto()._select(scene, card, "白名单"))
        self.assertEqual([], scene.clicks)

    def test_recovery_returns_to_configured_mode_and_all_modes_have_entry_guards(self):
        nodes = ("ProduceCloseCardDetail", "ProduceCancelRewardRemoveDialog", "ProduceCancelRewardSelection")
        base = json.loads((ROOT / "assets/resource/base/pipeline/Produce.json").read_text(encoding="utf-8"))
        for node in nodes:
            # Empty next yields to the enclosing JumpBack in the current scenario.
            self.assertEqual([], base[node]["next"])
        for file, entry in (("Produce.json", "ProduceEntry"), ("ProduceNIA.json", "ProduceEntryNIA"),
                            ("ProduceHIF.json", "ProduceEntryHIF")):
            data = json.loads((ROOT / "assets/resource/base/pipeline" / file).read_text(encoding="utf-8"))
            for node in nodes:
                self.assertIn("[JumpBack]" + node, data[entry]["next"])

    def test_interrupted_pending_counter_is_settled_on_next_reward_page(self):
        budget = _REWARD_BUDGETS["remove"]
        budget.observe(2)
        self.assertTrue(budget.begin())
        budget.confirm(2, None)
        with patch.object(ProduceChooseRewardAuto, "_stable_action_counts", return_value={"remove": 1, "refresh": 0}):
            ProduceChooseRewardAuto._sync_reward_action_counts(None, None)
            ProduceChooseRewardAuto._sync_reward_action_counts(None, None)
        self.assertEqual((1, 1, None), (budget.remaining, budget.confirmed, budget.pending_before))

    def test_scaled_overlay_detection_and_click_target(self):
        scene = Scene("remove", scale=.5)
        _, cards = ProduceChooseRewardAuto()._recover_reward_page(scene, allow_remove=True)
        self.assertEqual([(258, 578)], scene.clicks)
        self.assertEqual(4, len(cards))


class ShopRecoveryTest(unittest.TestCase):
    def setUp(self):
        patch("custom.action.produce.time.sleep").start()
        patch("custom.action.produce._detect_disconnect_screen",
              side_effect=lambda c, i: "ProduceDisconnectError" if i[0, 0, 0] == Scene.IDS["error"] else None).start()
        self.addCleanup(patch.stopall)

    def test_shop_bubble_is_closed_and_verified(self):
        scene = Scene("bubble")
        self.assertTrue(ProduceShoppingAuto()._dismiss_comment_bubble(scene))
        self.assertEqual([ProduceShoppingAuto.COMMENT_BUBBLE_TAP_POINT], scene.clicks)
        self.assertEqual("shop", scene.state)

    def test_clear_shop_needs_no_header_ocr_or_click(self):
        scene = Scene("shop")
        self.assertTrue(ProduceShoppingAuto()._dismiss_comment_bubble(scene))
        self.assertEqual(([], []), (scene.clicks, scene.recognitions))

    def test_white_reward_panel_cannot_be_treated_as_shop_bubble(self):
        scene = Scene("bubble")
        scene.run_recognition = lambda *a, **k: SimpleNamespace(hit=False)
        self.assertFalse(ProduceShoppingAuto()._dismiss_comment_bubble(scene))
        self.assertEqual([], scene.clicks)

    def test_stubborn_bubble_has_three_attempts_and_reports_failure(self):
        scene = Scene("bubble")
        scene.on_click = lambda point: None
        self.assertFalse(ProduceShoppingAuto()._dismiss_comment_bubble(scene))
        self.assertEqual(3, len(scene.clicks))

    def test_bubble_recovery_stops_on_network_error(self):
        scene = Scene("bubble")
        scene.on_click = lambda point: setattr(scene, "state", "error")
        shop = ProduceShoppingAuto()
        self.assertFalse(shop._dismiss_comment_bubble(scene))
        self.assertTrue(shop._disconnect_detected)
        self.assertEqual(1, len(scene.clicks))

    def test_cheapest_drink_label_still_checks_full_inventory(self):
        scene = Scene("shop")
        with patch("custom.shop.selected_drink_matches", return_value=True):
            self.assertEqual("full", ProduceShoppingAuto()._purchase(
                scene, [220, 670, 126, 126], "最低价饮料", is_drink=True, drink_id="235611"))
        self.assertEqual([(283, 733)], scene.clicks)
        self.assertIn("ProduceShoppingDrinkFull", scene.recognitions)

    def test_purchase_does_not_click_cached_product_after_disconnect(self):
        scene = Scene("error")
        self.assertEqual("disconnected", ProduceShoppingAuto()._purchase(
            scene, [220, 670, 126, 126], "最低价饮料", is_drink=True))
        self.assertEqual([], scene.clicks)

    def test_bubble_coordinates_follow_scaled_frame(self):
        scene = Scene("bubble", scale=.5)
        self.assertTrue(ProduceShoppingAuto()._dismiss_comment_bubble(scene))
        self.assertEqual([(180, 510)], scene.clicks)


class ChallengeParamsTest(unittest.TestCase):
    def test_missing_invalid_or_object_params_choose_default_second_challenge(self):
        for value in (None, "null", "", " ", "broken", "[]", {}, {"mode": "fixed", "index": 1}):
            with self.subTest(value=value):
                context = Mock()
                self.assertTrue(ChallengeAuto().run(context, SimpleNamespace(custom_action_param=value)))
                context.run_task.assert_called_once_with("ChallengeIndex", pipeline_override={
                    "ChallengeIndex": {"recognition": {"param": {"index": 1}}}})


if __name__ == "__main__":
    unittest.main()

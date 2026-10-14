"""HIF shop entry/exit races, bounded hand-off and silent scene probes."""
import json
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
from custom.shop import CONSULTATION_SLOTS, consultation_grid_visible, read_consultation_desk_entry
from custom.action.produce import ProduceShoppingAuto, ProduceHIFShoppingAuto, ProduceHIFShopAuto
from custom.reco.hif import ProduceHIFConsultationShop


def frame(*, icons=True):
    image = np.zeros((1279, 720, 3), np.uint8)
    for x, y, w, h in CONSULTATION_SLOTS:
        image[y + h:y + h + 42, x:x + w] = 255
        if icons:
            image[y + h + 8:y + h + 16, x + 6:x + 14] = (20, 220, 240)
    return image


def context(image):
    ctx = Mock()
    ctx.tasker.stopping = False
    ctx.run_recognition.return_value = SimpleNamespace(hit=False)
    ctx.get_node_data.return_value = {"attach": {}}
    ctx.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value = image
    return ctx


class ShopTransitionTest(unittest.TestCase):
    def test_white_areas_alone_are_not_a_hif_shop(self):
        image = frame(icons=False)
        self.assertTrue(ProduceShoppingAuto._looks_like_shop_grid(image))
        self.assertFalse(consultation_grid_visible(image))
        self.assertIsNone(ProduceHIFConsultationShop().analyze(Mock(), SimpleNamespace(image=image)))

    def test_joint_prices_and_p_icons_require_both_rows(self):
        image = frame()
        self.assertTrue(consultation_grid_visible(image))
        self.assertIsNotNone(ProduceHIFConsultationShop().analyze(Mock(), SimpleNamespace(image=image)))
        image[796:838] = 0
        self.assertFalse(consultation_grid_visible(image))
        for shape in ((720, 1280, 3), (1280, 720), (1280, 720, 4), (1100, 720, 3)):
            self.assertFalse(consultation_grid_visible(np.zeros(shape, np.uint8)))

    def test_shifted_interval_price_rows_do_not_match_consultation(self):
        image = np.zeros((1279, 720, 3), np.uint8)
        for x, y, w, h in CONSULTATION_SLOTS:
            image[y + h + 16:y + h + 58, x:x + w] = 255
            image[y + h + 32:y + h + 40, x + 6:x + 14] = (20, 220, 240)
        self.assertFalse(consultation_grid_visible(image))

    def test_hif_generic_button_does_not_exit_shop_and_nia_keeps_original(self):
        hif = json.loads((ROOT / "assets/resource/base/pipeline/ProduceHIF.json").read_text(encoding="utf-8"))
        base = json.loads((ROOT / "assets/resource/base/pipeline/Produce.json").read_text(encoding="utf-8"))
        templates = base["ProduceButton"]["recognition"]["param"]["template"]
        self.assertEqual([s for s in templates if s != "produce/shop_exit.png"],
                         hif["ProduceHIFButton"]["recognition"]["param"]["template"])
        self.assertIn("produce/shop_exit.png", templates)
        self.assertIn("[JumpBack]ProduceHIFButton", hif["ProduceEntryHIF"]["next"])
        self.assertNotIn("[JumpBack]ProduceButton", hif["ProduceEntryHIF"]["next"])
        self.assertEqual("ProduceHIFConsultationShop", hif["ProduceHIFShopFlag"]["recognition"]["param"]["custom_recognition"])
        self.assertNotIn("focus", hif["ProduceHIFShopFlag"])

    def test_reenter_on_exam_hands_back_after_two_observations_without_click(self):
        ctx = context(frame(icons=False))
        action = ProduceHIFShoppingAuto()
        with patch("custom.action.produce.time.sleep"), patch("custom.action.produce._detect_disconnect_screen", return_value=False):
            self.assertFalse(action._reenter_shop(ctx))
        self.assertTrue(action._shop_departed)
        self.assertEqual(2, ctx.tasker.controller.post_screencap.call_count)
        ctx.tasker.controller.post_click.assert_not_called()

    def test_reenter_when_grid_already_open_needs_two_frames_no_click_or_desk_ocr(self):
        ctx = context(frame())
        action = ProduceHIFShoppingAuto()
        with patch("custom.action.produce.time.sleep"), patch("custom.action.produce._detect_disconnect_screen", return_value=False), \
             patch("custom.shop.read_consultation_desk_entry") as desk:
            self.assertTrue(action._reenter_shop(ctx))
        self.assertFalse(action._shop_departed)
        self.assertEqual(2, ctx.tasker.controller.post_screencap.call_count)
        desk.assert_not_called()
        ctx.tasker.controller.post_click.assert_not_called()

    def test_desk_requires_header_and_label_and_two_stable_observations(self):
        ctx = context(np.zeros((1279, 720, 3), np.uint8))
        action = ProduceHIFShoppingAuto()
        with patch("custom.action.produce.time.sleep"), patch("custom.action.produce._detect_disconnect_screen", return_value=False), \
             patch.object(action, "_is_shop_scene", side_effect=[False, False, True, True]), \
             patch("custom.shop.read_consultation_desk_entry", return_value=[250, 600, 220, 60]):
            self.assertTrue(action._reenter_shop(ctx))
        ctx.tasker.controller.post_click.assert_called_once_with(360, 630)
        header = SimpleNamespace(text="相談", score=.99, box=[50, 70, 60, 30])
        button = SimpleNamespace(text="交換する", score=.99, box=[250, 600, 220, 60])
        with patch("custom.shop.ocr", return_value=[button]):
            self.assertIsNone(read_consultation_desk_entry(ctx, None))
        with patch("custom.shop.ocr", return_value=[header, button]):
            self.assertEqual([250, 600, 220, 60], read_consultation_desk_entry(ctx, None))
            button.box[1] = 1060
            self.assertIsNone(read_consultation_desk_entry(ctx, None))

    def test_departed_shop_is_not_reported_as_task_failure_or_successful_purchase(self):
        ctx = context(frame(icons=False))
        with patch("custom.action.produce.time.sleep"), patch("custom.action.produce._detect_disconnect_screen", return_value=False), \
             patch.object(ProduceShoppingAuto, "_purchase") as purchase, patch("custom.action.produce.logger.info") as info:
            self.assertTrue(ProduceHIFShopAuto().run(ctx, SimpleNamespace()))
        purchase.assert_not_called()
        info.assert_not_called()
        ctx.run_task.assert_not_called()
        ctx.tasker.controller.post_click.assert_not_called()

    def test_purchase_uncertainty_still_fails_but_verified_purchase_then_departure_hands_back(self):
        for status, expected in (("uncertain", False), ("bought", True)):
            image = frame()
            ctx = context(image)
            action = ProduceHIFShoppingAuto()
            product = {"box": [66, 670, 126, 126], "id": "235611", "name": "ブーストエキス", "price": 75}
            def departed(_):
                action._shop_departed = True
                return False
            with ExitStack() as stack:
                stack.enter_context(patch("custom.action.produce._load_card_lists", return_value={
                    "blacklist": {"enabled": False}, "whitelist": {"enabled": False}}))
                stack.enter_context(patch("custom.shop.is_interval_shop", return_value=False))
                for name, result in (("_wait_shop_ready", image), ("_dismiss_comment_bubble", True),
                                     ("_recognize_points", 200), ("_find_shop_delete_entry", None),
                                     ("_recognize_shop_cards", []), ("_recognize_drinks", [product]),
                                     ("_drink_inventory_full", False), ("_purchase", status)):
                    stack.enter_context(patch.object(action, name, return_value=result))
                stack.enter_context(patch.object(action, "_reenter_shop", side_effect=departed))
                self.assertEqual(expected, action.run(ctx, SimpleNamespace()))
                action._purchase.assert_called_once()
                self.assertEqual(1, action._dismiss_comment_bubble.call_count)
            ctx.run_task.assert_not_called()
            ctx.tasker.controller.post_click.assert_not_called()

    def test_comment_bubble_retries_emit_one_normal_message(self):
        ctx = context(frame())
        ctx.run_recognition.return_value = SimpleNamespace(hit=True)
        action = ProduceHIFShoppingAuto()
        with patch.object(action, "_comment_bubble_present", side_effect=[True, True, False]), \
             patch("custom.action.produce._detect_disconnect_screen", return_value=False), \
             patch("custom.action.produce.time.sleep"), patch("custom.action.produce.logger.info") as info:
            self.assertTrue(action._dismiss_comment_bubble(ctx))
        self.assertEqual(1, info.call_count)
        self.assertEqual(2, ctx.tasker.controller.post_click.call_count)

    def test_stable_grid_readiness_has_only_one_short_wait(self):
        image = frame()
        ctx = context(image)
        with patch("custom.action.produce.time.sleep") as wait, patch("custom.action.produce._detect_disconnect_screen", return_value=False):
            self.assertIs(image, ProduceHIFShoppingAuto()._wait_shop_ready(ctx))
        wait.assert_called_once_with(.25)
        ctx.run_recognition.assert_not_called()
        ctx.tasker.controller.post_click.assert_not_called()

    def test_left_shop_is_detected_quickly_without_price_timeout_or_probe_messages(self):
        ctx = context(frame(icons=False))
        action = ProduceHIFShoppingAuto()
        with patch("custom.action.produce.time.sleep") as wait, patch("custom.action.produce._detect_disconnect_screen", return_value=False):
            self.assertIsNone(action._wait_shop_ready(ctx))
        self.assertTrue(action._shop_departed)
        wait.assert_called_once_with(.25)
        ctx.tasker.controller.post_click.assert_not_called()
        for call in ctx.run_recognition.call_args_list:
            self.assertEqual({}, call.kwargs["pipeline_override"]["ProduceShoppingFlag"]["focus"])

    def test_overlay_wait_is_bounded_and_clear_taps_are_limited(self):
        ctx = context(frame(icons=False))
        ctx.run_recognition.return_value = SimpleNamespace(hit=True)
        action = ProduceHIFShoppingAuto()
        with patch("custom.action.produce.time.sleep"), patch("custom.action.produce._detect_disconnect_screen", return_value=False):
            self.assertIsNone(action._wait_shop_ready(ctx))
        self.assertFalse(action._shop_departed)
        self.assertEqual(10, ctx.tasker.controller.post_screencap.call_count)
        self.assertEqual(2, ctx.tasker.controller.post_click.call_count)

    def test_stopping_or_disconnect_never_reenters(self):
        for stopping, disconnected in ((True, False), (False, True)):
            ctx = context(frame())
            ctx.tasker.stopping = stopping
            action = ProduceHIFShoppingAuto()
            with patch("custom.action.produce._detect_disconnect_screen", return_value=disconnected):
                self.assertFalse(action._reenter_shop(ctx))
            ctx.tasker.controller.post_click.assert_not_called()


if __name__ == "__main__":
    unittest.main()

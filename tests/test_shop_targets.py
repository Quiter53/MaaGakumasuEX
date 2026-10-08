import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
from custom.shop import (TARGET_DRINK_NAMES, INTERVAL_DRINK_SLOTS, INTERVAL_CARD_SLOTS,
                         choose_discard, owned_targets_complete, refresh_is_affordable,
                         is_interval_shop, read_refresh, selected_drink_matches)
from custom.action.produce import ProduceShoppingAuto, ProduceHIFIntervalShoppingAuto, ProduceHIFFailedAuto


def rows(*texts):
    results = [SimpleNamespace(text=text, box=[50, 1050, 130, 30], score=.99) for text in texts]
    return SimpleNamespace(hit=bool(results), filtered_results=results, all_results=results, best_result=results[0] if results else None)


def owned(*ids):
    return {i: {"id": key, "name": key or "非目标", "verified": True} for i, key in enumerate(ids)}


class ShopTargetsTest(unittest.TestCase):
    def test_exact_six_target_identities(self):
        self.assertEqual(6, len(TARGET_DRINK_NAMES))
        self.assertEqual({"340077", "767772", "264496", "235611", "679991", "429282"}, set(TARGET_DRINK_NAMES))

    def test_unknown_balance_price_and_nonpositive_price_are_not_affordable(self):
        for points, price in ((None, 75), (100, None), (100, 0), (100, -1), (74, 75)):
            self.assertFalse(ProduceShoppingAuto._is_affordable({"price": price}, points))
        self.assertTrue(ProduceShoppingAuto._is_affordable({"price": 75}, 75))

    def test_non_target_is_discarded_before_duplicate(self):
        self.assertEqual(3, choose_discard(owned("767772", "767772", "235611", None), "679991"))

    def test_duplicate_is_replaced_only_for_missing_target(self):
        self.assertEqual(0, choose_discard(owned("767772", "767772", "235611", "235611"), "679991"))
        self.assertIsNone(choose_discard(owned("767772", "767772", "235611", "235611"), "235611"))

    def test_four_distinct_targets_are_preserved(self):
        state = owned("767772", "235611", "679991", "429282")
        self.assertTrue(owned_targets_complete(state))
        self.assertIsNone(choose_discard(state, "264496"))

    def test_unknown_or_not_full_inventory_cannot_be_discarded(self):
        state = owned("767772", "767772", "235611", None)
        state[3]["verified"] = False
        self.assertIsNone(choose_discard(state, "679991"))
        self.assertFalse(owned_targets_complete(state))
        self.assertIsNone(choose_discard(owned("767772", "767772", "235611"), "679991"))

    def test_refresh_requires_known_cost_and_purchase_reserve(self):
        for points, cost in ((None, 10), (100, None), (100, 0), (84, 10), (94, 20)):
            self.assertFalse(refresh_is_affordable(points, cost))
        self.assertTrue(refresh_is_affordable(85, 10))
        self.assertTrue(refresh_is_affordable(95, 20))

    def test_refresh_label_may_include_left_icon_but_not_other_controls(self):
        row = SimpleNamespace(text="リフッシュ", box=[18, 1044, 174, 44], score=.98)
        with patch("custom.shop.is_interval_shop", return_value=True), \
             patch("custom.shop.ocr", return_value=[row]), \
             patch("custom.shop.read_number", return_value=20):
            self.assertEqual({"point": (105, 1066), "cost": 20}, read_refresh(Mock(), None))
            row.box = [250, 1044, 174, 44]
            self.assertIsNone(read_refresh(Mock(), None))

    def test_unknown_current_points_prevent_product_click(self):
        context = Mock()
        context.tasker.stopping = False
        shop = ProduceShoppingAuto()
        with patch("custom.action.produce._detect_disconnect_screen", return_value=False), \
             patch.object(shop, "_comment_bubble_present", return_value=False), \
             patch.object(shop, "_recognize_points", return_value=None):
            self.assertEqual("unavailable", shop._purchase(context, [374, 690, 126, 126], "target",
                is_drink=True, drink_id="235611", expected_price=75))
        context.tasker.controller.post_click.assert_not_called()

    def test_title_identity_is_exact_and_rechecked_in_dialog(self):
        context = Mock()
        context.run_recognition.return_value = rows("初星黒酢")
        self.assertTrue(selected_drink_matches(context, None, "767772"))
        self.assertFalse(selected_drink_matches(context, None, "235611"))
        self.assertFalse(selected_drink_matches(context, None, None))
        selected_drink_matches(context, None, "767772", confirmation=True)
        self.assertEqual([160, 600, 520, 410], context.run_recognition.call_args.kwargs["pipeline_override"]["ProduceRecognitionPoint"]["roi"])
        context.run_recognition.return_value = rows("初星黒酢風ドリンク")
        self.assertFalse(selected_drink_matches(context, None, "767772"))

    def test_full_inventory_does_not_bypass_selected_title_guard(self):
        context = Mock()
        context.tasker.stopping = False
        context.run_recognition.return_value = rows("full")
        shop = ProduceShoppingAuto()
        with patch("custom.action.produce._detect_disconnect_screen", return_value=False), \
             patch("custom.action.produce.time.sleep"), \
             patch.object(shop, "_comment_bubble_present", return_value=False), \
             patch.object(shop, "_recognize_points", return_value=100), \
             patch("custom.shop.selected_drink_matches", return_value=False):
            self.assertEqual("unavailable", shop._purchase(context, [374, 690, 126, 126], "target",
                is_drink=True, drink_id="235611", expected_price=75))
        context.run_recognition.assert_not_called()
        context.tasker.controller.post_click.assert_called_once_with(437, 753)

    def test_purchase_waits_for_final_balance_instead_of_animation_value(self):
        context = Mock()
        context.tasker.stopping = False
        def recognize(name, image, pipeline_override=None):
            if name == "ProduceShoppingDrinkFull":
                return rows()
            template = (pipeline_override or {}).get(name, {}).get("template", "")
            return rows() if template == "produce/point_runout.png" else rows("confirm")
        context.run_recognition.side_effect = recognize
        shop = ProduceShoppingAuto()
        with patch("custom.action.produce._detect_disconnect_screen", return_value=False), \
             patch("custom.action.produce.time.sleep"), \
             patch.object(shop, "_comment_bubble_present", return_value=False), \
             patch.object(shop, "_recognize_points", side_effect=[358, 306, 288, 288]) as balance, \
             patch("custom.shop.selected_drink_matches", return_value=True), \
             patch("custom.shop.ocr", return_value=rows("交換確認").filtered_results):
            self.assertEqual("bought", shop._purchase(context, [66, 670, 126, 126], "target",
                is_drink=True, drink_id="767772", expected_price=70))
        self.assertEqual(4, balance.call_count)
        self.assertEqual(4, context.tasker.controller.post_click.call_count)
        self.assertEqual((360, 360), context.tasker.controller.post_click.call_args.args)

    def test_interval_grid_and_header_are_both_required(self):
        image = np.zeros((1279, 720, 3), np.uint8)
        context = Mock()
        context.run_recognition.return_value = rows("インターパル")
        self.assertFalse(is_interval_shop(context, image))
        for x, y, w, h in INTERVAL_CARD_SLOTS + INTERVAL_DRINK_SLOTS:
            image[y+h:y+h+42, x:x+w] = 255
        exit_result = rows("終了")
        exit_result.best_result.box = [585, 1062, 120, 44]
        context.run_recognition.side_effect = [rows("インターパル"), exit_result]
        self.assertTrue(is_interval_shop(context, image))
        context.run_recognition.side_effect = [rows(), exit_result, rows(), rows("リフレッシュ")]
        self.assertFalse(is_interval_shop(context, image))
        context.run_recognition.side_effect = [rows(), exit_result, rows("カスタマイズ"), rows("リフレッシュ")]
        self.assertTrue(is_interval_shop(context, image))

    def test_layout_is_two_drinks_not_second_card_row(self):
        self.assertEqual(2, len(ProduceHIFIntervalShoppingAuto.DRINK_SLOTS))
        self.assertEqual(INTERVAL_DRINK_SLOTS, ProduceHIFIntervalShoppingAuto.DRINK_SLOTS)
        self.assertTrue(all(box[0] >= 374 for box in INTERVAL_DRINK_SLOTS))
        self.assertEqual(INTERVAL_CARD_SLOTS, ProduceHIFIntervalShoppingAuto.CARD_SLOTS)

    def test_interval_wait_uses_six_cards_and_two_drinks(self):
        context = Mock()
        context.tasker.stopping = False
        image = np.zeros((1279, 720, 3), np.uint8)
        context.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value = image
        with patch("custom.action.produce._detect_disconnect_screen", return_value=False), \
             patch("custom.action.produce.time.sleep"), \
             patch("custom.shop.price_grid_visible", return_value=True) as visible, \
             patch("custom.shop.stock_signature", return_value=np.zeros(3)):
            self.assertIs(image, ProduceHIFIntervalShoppingAuto()._wait_shop_ready(context))
        self.assertEqual(INTERVAL_CARD_SLOTS + INTERVAL_DRINK_SLOTS, visible.call_args.args[1])
        context.tasker.controller.post_click.assert_not_called()

    def test_shop_scan_waits_for_visible_stable_prices(self):
        context = Mock()
        context.tasker.stopping = False
        image = np.zeros((1279, 720, 3), np.uint8)
        context.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value = image
        context.run_recognition.return_value = rows("shop header")
        with patch("custom.action.produce._detect_disconnect_screen", return_value=False), \
             patch("custom.action.produce.time.sleep"), \
             patch("custom.shop.price_grid_visible", side_effect=[False, True, True]), \
             patch("custom.shop.stock_signature", return_value=np.zeros(3)):
            self.assertIs(image, ProduceShoppingAuto()._wait_shop_ready(context))
        context.tasker.controller.post_click.assert_called_once_with(360, 360)

    def test_unverified_shop_never_taps_to_clear_overlay(self):
        context = Mock()
        context.tasker.stopping = False
        image = np.zeros((1279, 720, 3), np.uint8)
        context.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value = image
        context.run_recognition.return_value = rows()
        with patch("custom.action.produce._detect_disconnect_screen", return_value=False), \
             patch("custom.action.produce.time.sleep"):
            self.assertIsNone(ProduceShoppingAuto()._wait_shop_ready(context))
        context.tasker.controller.post_click.assert_not_called()

    def test_hif_failure_route_is_isolated(self):
        data = json.loads((ROOT / "assets/resource/base/pipeline/ProduceHIF.json").read_text(encoding="utf-8"))
        self.assertIn("[JumpBack]ProduceHIFFailedFlag", data["ProduceEntryHIF"]["next"])
        self.assertNotIn("ProduceFailedFlag", data["ProduceEntryHIF"]["next"])
        self.assertLess(data["ProduceEntryHIF"]["next"].index("[JumpBack]ProduceHIFIntervalShopFlag"),
                        data["ProduceEntryHIF"]["next"].index("[JumpBack]ProduceChooseItemFlag"))
        for file, entry, failed in (("Produce.json", "ProduceEntry", "ProduceFailedFlag"),
                                   ("ProduceNIA.json", "ProduceEntryNIA", "ProduceNIAFailedFlag")):
            other = json.loads((ROOT / "assets/resource/base/pipeline" / file).read_text(encoding="utf-8"))
            self.assertIn(failed, other[entry]["next"])

    def test_hif_selection_keeps_original_failure_policy(self):
        data = json.loads((ROOT / "assets/tasks/produce.json").read_text(encoding="utf-8"))
        cases = data["option"]["「HIF」类型"]["cases"]
        self.assertEqual("ProduceEntry", cases[0]["pipeline_override"]["ProduceEntryFlag"]["next"])
        self.assertEqual("ProduceEntryHIF", cases[1]["pipeline_override"]["ProduceEntryFlag"]["next"])

    def test_disabled_strategy_holder_does_not_enable_retry(self):
        context = Mock()
        context.get_node_data.return_value = {"enabled": False}
        context.tasker.stopping = False
        flag = rows("failure")
        stop = rows("end")
        stop.best_result.box = [106, 1127, 222, 66]
        context.run_recognition.side_effect = [flag, stop]
        with patch("custom.action.produce._detect_disconnect_screen", return_value=False):
            self.assertTrue(ProduceHIFFailedAuto().run(context, SimpleNamespace()))
        context.tasker.controller.post_click.assert_called_once_with(217, 1160)
        context.run_task.assert_not_called()

    def test_hif_finale_never_clicks_retry_if_stop_button_missing(self):
        context = Mock()
        context.get_node_data.return_value = {"enabled": True}
        context.tasker.stopping = False
        context.run_recognition.return_value = rows()
        with patch("custom.action.produce._detect_disconnect_screen", return_value=False), patch("custom.action.produce.time.sleep"):
            self.assertFalse(ProduceHIFFailedAuto().run(context, SimpleNamespace()))
        context.tasker.controller.post_click.assert_not_called()
        context.run_task.assert_not_called()


if __name__ == "__main__":
    unittest.main()

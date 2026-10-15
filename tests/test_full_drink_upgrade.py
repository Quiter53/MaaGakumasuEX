"""Full drink slots are replaced only for a confirmed affordable target."""
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
from custom.action.produce import ProduceShoppingAuto, ProduceHIFIntervalShoppingAuto, ProduceChooseRewardAuto
from custom.shop import TARGET_DRINK_NAMES, choose_discard


def owned(non_target=True):
    keys = ["429282", "767772", "235611", None if non_target else "679991"]
    return {i: {"id": key, "name": TARGET_DRINK_NAMES[key] if key else "初星水", "verified": True}
            for i, key in enumerate(keys)}


class FullDrinkUpgradeTest(unittest.TestCase):
    def run_shop(self, *, interval=False, stock=True, price=75, identity=True, discarded=True):
        ctx = Mock()
        ctx.tasker.stopping = False
        ctx.get_node_data.return_value = {"attach": {}}
        ctx.run_recognition.return_value = SimpleNamespace(hit=False, filtered_results=[], all_results=[], best_result=None)
        image = np.zeros((1279, 720, 3), np.uint8)
        ctx.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value = image
        action = ProduceHIFIntervalShoppingAuto() if interval else ProduceShoppingAuto()
        product = {"id": "429282", "name": TARGET_DRINK_NAMES["429282"], "price": price,
                   "box": [374, 690, 126, 126] if interval else [220, 670, 126, 126]}
        with ExitStack() as stack:
            stack.enter_context(patch("custom.action.produce.time.sleep"))
            stack.enter_context(patch("custom.shop.read_interval_health", return_value=(80, 80)))
            stack.enter_context(patch("custom.action.produce._detect_disconnect_screen", return_value=False))
            stack.enter_context(patch("custom.action.produce._load_card_lists", return_value={
                k: {"enabled": False, "cards": []} for k in ("blacklist", "whitelist")}))
            stack.enter_context(patch("custom.shop.is_interval_shop", return_value=False))
            refresh = stack.enter_context(patch("custom.shop.read_refresh", return_value=None))
            mocks = {}
            for name, result in (("_is_shop_scene", True), ("_wait_shop_ready", image),
                                 ("_dismiss_comment_bubble", True), ("_find_shop_delete_entry", None),
                                 ("_recognize_points", 330), ("_recognize_shop_cards", []),
                                 ("_read_owned_drinks", owned() if identity else None),
                                 ("_make_drink_space", discarded), ("_reenter_shop", True)):
                mocks[name] = stack.enter_context(patch.object(action, name, return_value=result))
            def scan(*_):
                return [product] if stock and not mocks["_purchase"].call_count else []
            mocks["scan"] = stack.enter_context(patch.object(action, "_recognize_drinks", side_effect=scan))
            mocks["_purchase"] = stack.enter_context(patch.object(action, "_purchase", return_value="bought"))
            # A full inventory becomes three slots only after a successful discard.
            capacity = iter([True, False, True])
            stack.enter_context(patch.object(action, "_drink_inventory_full", side_effect=lambda *_: next(capacity, True)))
            result = action.run(ctx, SimpleNamespace())
        return result, ctx, mocks, refresh

    def test_both_shops_replace_non_target_even_if_wanted_kind_already_owned(self):
        for interval in (False, True):
            with self.subTest(interval=interval):
                result, ctx, mocks, _ = self.run_shop(interval=interval)
                self.assertTrue(result)
                self.assertEqual(2 if interval else 1, mocks["_read_owned_drinks"].call_count)
                mocks["_make_drink_space"].assert_called_once_with(ctx, "429282", non_target_only=True)
                mocks["_purchase"].assert_called_once()
                self.assertEqual(75, mocks["_purchase"].call_args.kwargs["expected_price"])

    def test_no_target_keeps_every_bottle_and_ordinary_points(self):
        for interval in (False, True):
            result, _, mocks, refresh = self.run_shop(interval=interval, stock=False)
            self.assertTrue(result)
            mocks["_make_drink_space"].assert_not_called()
            mocks["_purchase"].assert_not_called()
            if not interval:
                mocks["_read_owned_drinks"].assert_not_called()
                refresh.assert_not_called()
            else:
                refresh.assert_called_once()

    def test_unaffordable_target_never_authorizes_discard(self):
        for interval in (False, True):
            result, _, mocks, _ = self.run_shop(interval=interval, price=400)
            self.assertTrue(result)
            mocks["_make_drink_space"].assert_not_called()
            mocks["_purchase"].assert_not_called()

    def test_unknown_owned_identity_or_unconfirmed_discard_never_buys(self):
        for interval in (False, True):
            for options in ({"identity": False}, {"discarded": False}):
                result, ctx, mocks, _ = self.run_shop(interval=interval, **options)
                self.assertTrue(result)  # Cancel optional shopping; keep cultivation running.
                mocks["_purchase"].assert_not_called()
                ctx.run_task.assert_called_once_with("ProduceShoppingExit")
                ctx.tasker.controller.post_click.assert_not_called()

    def test_non_target_is_allowed_before_duplicate_guard_but_all_targets_protected(self):
        self.assertEqual(3, choose_discard(owned(), "429282", non_target_only=True))
        self.assertIsNone(choose_discard(owned(False), "340077", non_target_only=True))
        self.assertIsNone(choose_discard({**owned(), 3: {"id": None, "verified": False}}, "429282"))

    def test_reward_full_selects_missing_target_and_checks_non_target_before_discard(self):
        ctx = Mock()
        ctx.tasker.stopping = False
        image = np.zeros((1279, 720, 3), np.uint8)
        state = {"full": True, "stable": True, "occupied": {0, 1, 2, 3},
                 "reward": {0: {"id": "767772", "name": "初星黑醋"},
                            1: {"id": "429282", "name": "初星乳清蛋白"}},
                 "inventory": {2: {"id": "429282", "name": "初星乳清蛋白"}}}
        action = ProduceChooseRewardAuto
        with patch.object(action, "_stable_drink_state", return_value=(image, state)), \
             patch.object(action, "_delete_owned_drink", return_value=True) as discard, \
             patch.object(action, "_click_reward_drink", return_value=True) as take:
            self.assertTrue(action._handle_drink_reward(ctx, image))
        discard.assert_called_once_with(ctx, 0, expected_non_target=True)
        self.assertEqual(0, take.call_args.args[1])

    def test_changing_reward_page_never_clicks_stale_drink_or_skip(self):
        ctx = Mock()
        ctx.tasker.stopping = False
        action = ProduceChooseRewardAuto
        with patch.object(action, "_stable_drink_state", return_value=(None, {"aborted": True})), \
             patch.object(action, "_delete_owned_drink") as discard, \
             patch.object(action, "_click_reward_drink") as take, \
             patch.object(action, "_skip_drink_reward") as skip:
            self.assertTrue(action._handle_drink_reward(ctx, None))
        discard.assert_not_called()
        take.assert_not_called()
        skip.assert_not_called()

    def test_checked_skip_page_has_a_route_back_to_reward_strategy(self):
        data = json.loads((ROOT / "assets/resource/base/pipeline/Produce.json").read_text(encoding="utf-8"))
        self.assertIn("P.?ドリンクを受け取らないで次へ進みます",
                      data["ProduceChooseGetFlag"]["recognition"]["param"]["expected"])

    def test_reward_receive_clears_checked_skip_before_selecting_target(self):
        ctx = Mock()
        ctx.tasker.stopping = False
        ctx.run_recognition.side_effect = [SimpleNamespace(hit=True),
            SimpleNamespace(hit=True, best_result=SimpleNamespace(box=[239, 993, 190, 26]))]
        with patch("custom.action.produce._detect_disconnect_screen", return_value=False), \
             patch("custom.action.produce.time.sleep"):
            self.assertTrue(ProduceChooseRewardAuto._click_reward_drink(ctx, 0, "target"))
        self.assertEqual([(334, 1006), (225, 890)], [c.args for c in ctx.tasker.controller.post_click.call_args_list])

    def test_skip_already_checked_submits_without_toggling_checkbox(self):
        ctx = Mock()
        ctx.tasker.stopping = False
        ctx.run_recognition.return_value = SimpleNamespace(hit=True)
        with patch("custom.shop.ocr", return_value=[SimpleNamespace(box=[239, 1053, 240, 76], score=.99)]), \
             patch("custom.dialog_safety.drink_overlay_cancel", return_value=(False, None)), \
             patch("custom.action.produce._detect_disconnect_screen", return_value=None), \
             patch.object(ProduceChooseRewardAuto, "_is_drink_reward", return_value=True):
            self.assertTrue(ProduceChooseRewardAuto._skip_drink_reward(ctx, None, "all targets"))
        ctx.tasker.controller.post_click.assert_called_once_with(359, 1091)


if __name__ == "__main__":
    unittest.main()

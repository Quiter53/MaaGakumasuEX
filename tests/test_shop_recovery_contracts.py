"""Recovery receipts across callbacks; True means cultivation may continue."""
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
from custom.produce.shop import ProduceShoppingAuto, ProduceHIFIntervalShoppingAuto
from custom.produce.rewards import ProduceChooseRewardAuto


def context():
    ctx = Mock()
    nodes = {}
    def merge(target, values):
        for key, value in values.items():
            if isinstance(value, dict):
                merge(target.setdefault(key, {}), value)
            else:
                target[key] = deepcopy(value)
    ctx.get_node_data.side_effect = lambda name: deepcopy(nodes.get(name, {}))
    ctx.override_pipeline.side_effect = lambda values: merge(nodes, values)
    ctx.tasker.stopping = False
    ctx.run_recognition.return_value = SimpleNamespace(hit=False, filtered_results=[], all_results=[], best_result=None)
    ctx.run_task.return_value = SimpleNamespace(status=SimpleNamespace(succeeded=True))
    ctx.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value = np.zeros((1280,720,3), np.uint8)
    return ctx


class ShopRecoveryContractsTest(unittest.TestCase):
    def test_normal_interval_exit_still_checks_surplus_healing(self):
        ctx = context()
        shop = ProduceHIFIntervalShoppingAuto()
        image = ctx.tasker.controller.post_screencap().wait().get()
        with patch("time.sleep"), \
             patch("custom.produce.shared._detect_disconnect_screen", return_value=False), \
             patch("custom.shop.read_interval_health", return_value=(80,80)), \
             patch("custom.shop.read_refresh", return_value=None), \
             patch.object(shop, "_wait_shop_ready", return_value=image), \
             patch.object(shop, "_is_shop_scene", return_value=True), \
             patch.object(shop, "_recognize_points", return_value=300), \
             patch.object(shop, "_drink_inventory_full", return_value=False), \
             patch.object(shop, "_recognize_drinks", return_value=[]), \
             patch.object(shop, "_dismiss_comment_bubble", return_value=True), \
             patch.object(shop, "_heal_before_drinks", wraps=shop._heal_before_drinks) as heal:
            self.assertTrue(shop.run(ctx, SimpleNamespace()))
        self.assertEqual(2, heal.call_count)
        self.assertEqual({}, heal.call_args_list[0].kwargs)
        self.assertEqual({"spend_remaining":True}, heal.call_args_list[1].kwargs)
        ctx.run_task.assert_called_once_with("ProduceShoppingExit")
        ctx.tasker.controller.post_click.assert_not_called()

    def test_swallowed_exit_keeps_intent_across_instances_without_spending_again(self):
        for cls in (ProduceShoppingAuto, ProduceHIFIntervalShoppingAuto):
            with self.subTest(shop=cls.__name__), ExitStack() as stack:
                ctx = context()
                stack.enter_context(patch("time.sleep"))
                stack.enter_context(patch("custom.produce.shared._detect_disconnect_screen", return_value=False))
                stack.enter_context(patch.object(cls, "_is_shop_scene", return_value=True))
                stack.enter_context(patch.object(cls, "_dismiss_comment_bubble", return_value=True))
                purchase = stack.enter_context(patch.object(cls, "_purchase"))
                discard = stack.enter_context(patch.object(cls, "_make_drink_space"))
                scan = stack.enter_context(patch.object(cls, "_recognize_drinks"))
                refresh = stack.enter_context(patch("custom.shop.read_refresh"))
                self.assertTrue(cls()._finish_optional_shop(ctx))
                self.assertTrue(cls._visit_state(ctx)["exit_pending"])
                self.assertTrue(cls().run(ctx, SimpleNamespace()))
                self.assertTrue(cls._visit_state(ctx)["exit_pending"])
                for operation in (purchase, discard, scan, refresh):
                    operation.assert_not_called()
                self.assertEqual(2, ctx.run_task.call_count)
                ctx.tasker.controller.post_click.assert_not_called()

    def test_exit_receipt_requires_two_consecutive_absent_frames(self):
        for observations, pending in (([True, False, True, False, True], True),
                                      ([True, False, False], False)):
            with self.subTest(observations=observations):
                ctx = context()
                shop = ProduceShoppingAuto()
                shop._visit_state(ctx, heal_pending=True)
                with patch("time.sleep"), \
                     patch("custom.produce.shared._detect_disconnect_screen", return_value=False), \
                     patch.object(shop, "_is_shop_scene", side_effect=observations), \
                     patch.object(shop, "_dismiss_comment_bubble", return_value=True):
                    self.assertTrue(shop._finish_optional_shop(ctx))
                self.assertEqual(pending, shop._visit_state(ctx)["exit_pending"])
                self.assertEqual(pending, shop._visit_state(ctx)["heal_pending"])
                ctx.run_task.assert_called_once_with("ProduceShoppingExit")

    def test_cancel_modal_before_exit_and_never_click_purchase_confirmation(self):
        ctx = context()
        shop = ProduceShoppingAuto()
        events = []
        ctx.tasker.controller.post_click.side_effect = lambda x,y: events.append((x,y)) or Mock()
        ctx.run_task.side_effect = lambda name: events.append(name) or SimpleNamespace(status=SimpleNamespace(succeeded=True))
        with patch("time.sleep"), \
             patch("custom.produce.shared._detect_disconnect_screen", return_value=False), \
             patch("custom.battle_drinks.BattleDrinkSession._cancel_box", side_effect=[[100,1100,100,40], None]), \
             patch.object(shop, "_is_shop_scene", side_effect=[True,False,False]), \
             patch.object(shop, "_dismiss_comment_bubble", return_value=True):
            self.assertTrue(shop._finish_optional_shop(ctx))
        self.assertEqual([(150,1120), "ProduceShoppingExit"], events)
        self.assertFalse(shop._visit_state(ctx)["exit_pending"])

    def test_stop_or_disconnect_never_clicks_recovery_controls(self):
        for stopped, disconnected in ((True,False),(False,True)):
            with self.subTest(stopped=stopped):
                ctx = context()
                ctx.tasker.stopping = stopped
                with patch("custom.produce.shared._detect_disconnect_screen", return_value=disconnected):
                    self.assertTrue(ProduceShoppingAuto()._finish_optional_shop(ctx))
                ctx.run_task.assert_not_called()
                ctx.tasker.controller.post_click.assert_not_called()

    def test_pending_discard_never_repeats_delete_or_uses_stale_reward(self):
        ctx = context()
        action = ProduceChooseRewardAuto
        action._discard_receipt_state(ctx, {"before_count":4,"name":"初星水","attempts":0}, write=True)
        with patch("time.sleep"), \
             patch("custom.produce.shared._detect_disconnect_screen", return_value=False), \
             patch("custom.shop.owned_detail_title", return_value="初星水"), \
             patch.object(action, "_delete_owned_drink") as discard, \
             patch.object(action, "_click_reward_drink") as take, \
             patch.object(action, "_stable_drink_state") as scan, \
             patch.object(action, "_skip_drink_reward") as skip:
            for _ in range(2):
                self.assertTrue(action._handle_drink_reward(ctx, np.zeros((1280,720,3),np.uint8)))
            for operation in (discard,take,scan,skip):
                operation.assert_not_called()
        self.assertEqual(2, action._discard_receipt_state(ctx)["attempts"])
        ctx.tasker.controller.post_click.assert_not_called()

    def test_pending_discard_requires_two_matching_counts_then_clears_receipt(self):
        ctx = context()
        action = ProduceChooseRewardAuto
        action._discard_receipt_state(ctx, {"before_count":4,"attempts":0}, write=True)
        with patch("time.sleep"), \
             patch("custom.produce.shared._detect_disconnect_screen", return_value=False), \
             patch("custom.shop.owned_detail_title", return_value=None), \
             patch.object(action, "_is_drink_reward", return_value=True), \
             patch.object(action, "_reward_inventory_count", side_effect=[3,4,3,3]) as count:
            self.assertTrue(action._wait_discard_receipt(ctx, 4))
        self.assertEqual(4, count.call_count)
        self.assertIsNone(action._discard_receipt_state(ctx))
        ctx.tasker.controller.post_click.assert_not_called()

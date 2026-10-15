"""A delayed shop exit must not suppress the next verified visit."""
import sys
from pathlib import Path
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
from test_shop_recovery_contracts import context
from custom.action.produce import ProduceShoppingAuto, ProduceHIFEnterShopAuto, ProduceCardsAuto, ProduceChooseEventBase


class ShopVisitLifetimeTest(unittest.TestCase):
    def test_unrecognized_or_stopped_schedule_keeps_old_receipts(self):
        for stopped in (False, True):
            with self.subTest(stopped=stopped):
                ctx = context()
                ctx.tasker.stopping = stopped
                ProduceShoppingAuto._visit_state(ctx, exit_pending=True, heal_pending=True)
                with patch("custom.action.produce.time.sleep"), \
                     patch("custom.action.produce._detect_disconnect_screen", return_value=False), \
                     patch("custom.hif.read_hif_shop_entry", return_value=None):
                    self.assertTrue(ProduceHIFEnterShopAuto().run(ctx, SimpleNamespace()))
                self.assertTrue(ProduceShoppingAuto._visit_state(ctx)["exit_pending"])
                self.assertTrue(ProduceShoppingAuto._visit_state(ctx)["heal_pending"])
                ctx.tasker.controller.post_click.assert_not_called()

    def test_confirmed_battle_ends_visit_but_unrecognized_frame_does_not(self):
        for confirmed in (False, True):
            with self.subTest(confirmed=confirmed):
                ctx = context()
                ProduceShoppingAuto._visit_state(ctx, exit_pending=True, heal_pending=True, unrelated="keep")
                ProduceShoppingAuto._delete_cancel_state(ctx, pending=True)
                ProduceShoppingAuto._shop_delete_visit(ctx, checked=True)
                action = ProduceCardsAuto()
                with patch("custom.action.produce.time.sleep"), \
                     patch("custom.action.produce._detect_disconnect_screen", return_value=False), \
                     patch.object(action, "_wait_until_playable", return_value=True), \
                     patch.object(action, "_is_playing_card", side_effect=[True,False] if confirmed else [False]), \
                     patch.object(action, "_drink_step", return_value=True):
                    self.assertTrue(action.run(ctx, SimpleNamespace()))
                state = ProduceShoppingAuto._visit_state(ctx)
                self.assertEqual(not confirmed, state["exit_pending"])
                self.assertEqual(not confirmed, state["heal_pending"])
                self.assertEqual("keep", state["unrelated"])
                self.assertEqual(not confirmed, ProduceShoppingAuto._delete_cancel_state(ctx))
                self.assertEqual(not confirmed, ProduceShoppingAuto._shop_delete_visit(ctx))
                ctx.tasker.controller.post_click.assert_not_called()

    def test_recognized_schedule_closes_old_visit_without_changing_event_choice(self):
        for events in ([], [{"name":"交谈","box":[100,800,100,100]}]):
            with self.subTest(events=bool(events)):
                ctx = context()
                ProduceShoppingAuto._visit_state(ctx, exit_pending=True, heal_pending=True)
                action = ProduceChooseEventBase()
                image = ctx.tasker.controller.post_screencap().wait().get()
                with patch.object(action, "_get_screenshot", return_value=image), \
                     patch.object(action, "_get_suggestion", return_value=""), \
                     patch.object(action, "_get_health", return_value=None), \
                     patch.object(action, "_get_current_points", return_value=300), \
                     patch.object(action, "_get_current_score", return_value=None), \
                     patch.object(action, "_get_available_events", return_value=events), \
                     patch.object(action, "_choose_best_event", return_value=None):
                    self.assertTrue(action.run(ctx, SimpleNamespace(custom_action_param="{}")))
                self.assertEqual(not bool(events), ProduceShoppingAuto._visit_state(ctx)["exit_pending"])
                ctx.tasker.controller.post_click.assert_not_called()

    def test_verified_next_schedule_entry_releases_old_exit_intent(self):
        ctx = context()
        image = ctx.tasker.controller.post_screencap().wait().get()
        with patch("custom.action.produce.time.sleep"), \
             patch("custom.action.produce._detect_disconnect_screen", return_value=False), \
             patch.object(ProduceShoppingAuto, "_is_shop_scene", return_value=True), \
             patch.object(ProduceShoppingAuto, "_dismiss_comment_bubble", return_value=True):
            # Exit reports success, but all four receipt frames are still in
            # the shop. Keep the guard: immediately retrying spending is wrong.
            self.assertTrue(ProduceShoppingAuto()._finish_optional_shop(ctx))
        self.assertTrue(ProduceShoppingAuto._visit_state(ctx)["exit_pending"])

        # The old shop exits asynchronously. The real next schedule-entry
        # action sees a stable shop tile, clicks it and observes two new frames.
        moved = np.full_like(image, 120)
        ctx.tasker.controller.post_screencap.return_value.wait.return_value.get.side_effect = [image,image,moved,moved]
        with patch("custom.action.produce.time.sleep"), \
             patch("custom.action.produce._detect_disconnect_screen", return_value=False), \
             patch("custom.hif.read_hif_shop_entry", side_effect=[[100,950,200,100],[100,950,200,100],None,None]):
            self.assertTrue(ProduceHIFEnterShopAuto().run(ctx, SimpleNamespace()))
        ctx.tasker.controller.post_click.assert_called_once_with(200,1000)
        pending = ProduceShoppingAuto._visit_state(ctx).get("exit_pending")
        ctx.tasker.controller.post_screencap.return_value.wait.return_value.get.side_effect = None
        ctx.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value = moved
        shop = ProduceShoppingAuto()
        with patch("custom.action.produce.time.sleep"), \
             patch("custom.action.produce._detect_disconnect_screen", return_value=False), \
             patch("custom.action.produce._load_card_lists", return_value={
                 k:{"enabled":False,"cards":[]} for k in ("blacklist","whitelist","replacementList")}), \
             patch("custom.shop.is_interval_shop", return_value=False), \
             patch.object(shop, "_is_shop_scene", return_value=True), \
             patch.object(shop, "_dismiss_comment_bubble", return_value=True), \
             patch.object(shop, "_wait_shop_ready", return_value=moved), \
             patch.object(shop, "_find_shop_delete_entry", return_value=None), \
             patch.object(shop, "_recognize_points", return_value=300), \
             patch.object(shop, "_recognize_shop_cards", return_value=[]), \
             patch.object(shop, "_recognize_drinks", return_value=[]) as scan:
            self.assertTrue(shop.run(ctx, SimpleNamespace()))
        self.assertEqual((False,1), (pending,scan.call_count),
                         "Old exit intent survives a verified new visit and bypasses the new shop's strategy")


if __name__ == "__main__":
    unittest.main(verbosity=2)

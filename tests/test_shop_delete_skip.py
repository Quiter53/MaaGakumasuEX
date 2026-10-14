"""Deletion never inherits exchange-list permission; empty scans cancel safely."""
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
from custom.action.produce import ProduceHIFChangeSourceAuto, ProduceHIFDeleteAuto, ProduceShoppingAuto


def candidate(name, rarity):
    return dict(name=name, name_key=name, rarity=rarity, name_confidence=1.,
                rarity_confidence=1., identity_verified=True, box=[80, 650, 120, 120])


def context():
    ctx = Mock()
    nodes = {"ProduceHIFDeleteFlag": {"attach": {"existing_option": "keep"}}}
    ctx.get_node_data.side_effect = lambda name: deepcopy(nodes.get(name, {}))
    def override(changes):
        for name, values in changes.items():
            nodes.setdefault(name, {}).update(deepcopy(values))
        return True
    ctx.override_pipeline.side_effect = override
    ctx.tasker.stopping = False
    ctx.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value = np.zeros((1280, 720, 3), np.uint8)
    return ctx


class ShopDeleteSkipTest(unittest.TestCase):
    def test_shop_delete_stops_after_n_blacklist_r(self):
        cards = [candidate("replace_sr", "SR"), candidate("replace_ssr", "SSR"),
                 candidate("rare", "R"), candidate("black", "SSR"), candidate("normal", "N")]
        replacements = {"replace_sr", "replace_ssr"}
        for name, rank in (("normal", 3), ("black", 2), ("rare", 1)):
            selected = ProduceShoppingAuto._choose_delete_candidate(cards, {"black"}, set(), replacements)
            self.assertEqual((name, rank), (selected["name"], selected["delete_rank"]))
            cards = [card for card in cards if card["name"] != name]
        self.assertIsNone(ProduceShoppingAuto._choose_delete_candidate(cards, {"black"}, set(), replacements))

    def test_replacement_does_not_override_whitelist_for_deletion(self):
        for rarity in ("N", "R", "SR", "SSR"):
            with self.subTest(rarity=rarity):
                card = candidate("protected", rarity)
                self.assertIsNone(ProduceShoppingAuto._choose_delete_candidate(
                    [card], set(), {"protected"}, {"protected"}))
                self.assertIsNotNone(ProduceHIFChangeSourceAuto._choose_source_candidate(
                    [card], set(), {"protected"}, {"protected"}))

    def test_blacklisted_high_rarity_is_still_deletable(self):
        for rarity in ("SR", "SSR"):
            selected = ProduceShoppingAuto._choose_delete_candidate([candidate("black", rarity)], {"black"}, set())
            self.assertEqual(2, selected["delete_rank"])

    def test_no_candidate_cancels_without_clicking_a_card_or_submit(self):
        ctx, shop = context(), ProduceShoppingAuto()
        first = (np.zeros((1280, 720, 3), np.uint8), [])
        with patch.object(ProduceHIFChangeSourceAuto, "_rewind_source", return_value=first), \
             patch.object(ProduceHIFChangeSourceAuto, "_scan_source", return_value=None) as scan, \
             patch.object(shop, "_cancel_delete", return_value="skipped") as cancel, \
             patch.object(shop, "_commit_delete") as commit:
            self.assertEqual("skipped", shop._delete_from_deck(ctx, set(), set(), {"replace_ssr"}))
        self.assertEqual(set(), scan.call_args.args[3])
        self.assertIs(scan.call_args.kwargs["change"], False)
        cancel.assert_called_once_with(ctx)
        commit.assert_not_called()
        ctx.tasker.controller.post_click.assert_not_called()
        ctx.run_task.assert_not_called()

    def test_cancel_requires_observed_shop_after_deck_disappears(self):
        ctx, shop = context(), ProduceShoppingAuto()
        ctx.run_recognition.side_effect = [SimpleNamespace(hit=True), SimpleNamespace(hit=False)]
        cancel = SimpleNamespace(text="キャンセル", score=.99, box=[100, 1130, 200, 50])
        with patch("custom.action.produce.time.sleep"), \
             patch("custom.action.produce._detect_disconnect_screen", return_value=False), \
             patch("custom.shop.ocr", return_value=[cancel]), \
             patch.object(shop, "_is_shop_scene", return_value=True):
            self.assertEqual("skipped", shop._cancel_delete(ctx))
        ctx.tasker.controller.post_click.assert_called_once_with(200, 1155)
        ctx.run_task.assert_not_called()

    def test_cancel_with_missing_low_confidence_or_ambiguous_button_never_clicks(self):
        low = SimpleNamespace(text="キャンセル", score=.79, box=[100, 1130, 200, 50])
        good = SimpleNamespace(text="キャンセル", score=.99, box=[100, 1130, 200, 50])
        for buttons in ([], [low], [good, good]):
            with self.subTest(buttons=buttons):
                ctx, shop = context(), ProduceShoppingAuto()
                ctx.run_recognition.return_value = SimpleNamespace(hit=True)
                with patch("custom.action.produce.time.sleep"), \
                     patch("custom.action.produce._detect_disconnect_screen", return_value=False), \
                     patch("custom.shop.ocr", return_value=buttons):
                    self.assertEqual("cancel_pending", shop._cancel_delete(ctx))
                ctx.tasker.controller.post_click.assert_not_called()
                ctx.run_task.assert_not_called()

    def test_disappearing_delete_flag_alone_is_not_success(self):
        ctx, shop = context(), ProduceShoppingAuto()
        ctx.run_recognition.return_value = SimpleNamespace(hit=False)
        with patch("custom.action.produce.time.sleep"), \
             patch("custom.action.produce._detect_disconnect_screen", return_value=False), \
             patch.object(shop, "_is_shop_scene", return_value=False):
            self.assertEqual("cancel_pending", shop._cancel_delete(ctx))
        ctx.tasker.controller.post_click.assert_not_called()
        ctx.run_task.assert_not_called()

    def test_cancel_honors_stop_and_disconnect(self):
        ctx, shop = context(), ProduceShoppingAuto()
        ctx.tasker.stopping = True
        self.assertEqual("cancel_pending", shop._cancel_delete(ctx))
        ctx.tasker.controller.post_screencap.assert_not_called()
        ctx.tasker.stopping = False
        with patch("custom.action.produce._detect_disconnect_screen", return_value=True):
            self.assertEqual("disconnected", shop._cancel_delete(ctx))
        self.assertTrue(shop._disconnect_detected)
        ctx.tasker.controller.post_click.assert_not_called()

    def test_pending_cancel_callback_does_not_rescan_cards(self):
        ctx, shop = context(), ProduceShoppingAuto()
        ctx.run_recognition.return_value = SimpleNamespace(hit=True)
        with patch("custom.action.produce.time.sleep"), \
             patch("custom.action.produce._detect_disconnect_screen", return_value=False), \
             patch("custom.shop.ocr", return_value=[]), \
             patch.object(ProduceHIFChangeSourceAuto, "_rewind_source") as rewind, \
             patch.object(ProduceHIFChangeSourceAuto, "_scan_source") as scan:
            self.assertEqual("cancel_pending", shop._cancel_delete(ctx))
            self.assertTrue(shop._delete_cancel_state(ctx))
            self.assertEqual("cancel_pending", shop._delete_from_deck(ctx, set(), set(), set()))
            rewind.assert_not_called()
            scan.assert_not_called()
            ctx.run_recognition.return_value = SimpleNamespace(hit=False)
            with patch.object(shop, "_is_shop_scene", return_value=True):
                self.assertEqual("skipped", shop._delete_from_deck(ctx, set(), set(), set()))
            self.assertFalse(shop._delete_cancel_state(ctx))

    def test_pending_cancel_is_shared_across_action_instances(self):
        ctx, shop = context(), ProduceShoppingAuto()
        resume = ProduceHIFDeleteAuto()
        ctx.run_recognition.return_value = SimpleNamespace(hit=True)
        with patch("custom.action.produce.time.sleep"), \
             patch("custom.action.produce._detect_disconnect_screen", return_value=False), \
             patch("custom.shop.ocr", return_value=[]), \
             patch.object(ProduceHIFChangeSourceAuto, "_rewind_source") as rewind, \
             patch.object(ProduceHIFChangeSourceAuto, "_scan_source") as scan:
            self.assertEqual("cancel_pending", shop._cancel_delete(ctx))
            self.assertTrue(resume._delete_cancel_state(ctx))
            self.assertEqual("cancel_pending", resume._delete_from_deck(ctx, set(), set(), set()))
        rewind.assert_not_called()
        scan.assert_not_called()
        ctx.tasker.controller.post_click.assert_not_called()

    def test_different_task_context_does_not_inherit_pending_cancel(self):
        old_task, new_task = context(), context()
        action = ProduceHIFDeleteAuto()
        action._delete_cancel_state(old_task, pending=True)
        self.assertTrue(action._delete_cancel_state(old_task))
        self.assertFalse(action._delete_cancel_state(new_task))
        with patch.object(ProduceHIFChangeSourceAuto, "_rewind_source", return_value=None) as rewind, \
             patch.object(action, "_cancel_delete", return_value="skipped"):
            self.assertEqual("skipped", action._delete_from_deck(new_task, set(), set(), set()))
        rewind.assert_called_once_with(new_task, change=False)
        self.assertTrue(action._delete_cancel_state(old_task))

    def test_cancel_state_updates_preserve_other_attach_fields(self):
        ctx, shop = context(), ProduceShoppingAuto()
        for pending in (True, False):
            shop._delete_cancel_state(ctx, pending=pending)
            self.assertIs(shop._delete_cancel_state(ctx), pending)
            attach = ctx.get_node_data("ProduceHIFDeleteFlag")["attach"]
            self.assertEqual("keep", attach["existing_option"])
            self.assertIs(attach["shop_delete_cancel_pending"], pending)

    def test_generic_shop_return_clears_pending_and_skips_only_current_visit(self):
        ctx, shop = context(), ProduceShoppingAuto()
        image = np.zeros((1280, 720, 3), np.uint8)
        lists = {name: {"enabled": False, "cards": []} for name in ("blacklist", "whitelist")}
        shop._delete_cancel_state(ctx, pending=True)
        with patch("custom.action.produce._load_card_lists", return_value=lists), \
             patch("custom.action.produce._detect_disconnect_screen", return_value=False), \
             patch("custom.shop.is_interval_shop", return_value=False), \
             patch("custom.shop.read_number", return_value=150), \
             patch.object(shop, "_is_shop_scene", return_value=True), \
             patch.object(shop, "_reenter_shop", return_value=True), \
             patch.object(shop, "_dismiss_comment_bubble", return_value=True), \
             patch.object(shop, "_wait_shop_ready", return_value=image), \
             patch.object(shop, "_recognize_points", return_value=330), \
             patch.object(shop, "_find_shop_delete_entry", return_value=(516, 1007)), \
             patch.object(shop, "_recognize_shop_cards", return_value=[]), \
             patch.object(shop, "_recognize_drinks", return_value=[]), \
             patch.object(shop, "_try_delete_owned_card", return_value="skipped") as delete:
            self.assertTrue(shop.run(ctx, SimpleNamespace()))
            delete.assert_not_called()
            self.assertFalse(shop._delete_cancel_state(ctx))
            self.assertTrue(shop.run(ctx, SimpleNamespace()))
            delete.assert_called_once()
        self.assertEqual(2, ctx.run_task.call_count)
        self.assertTrue(all(call.args == ("ProduceShoppingExit",) for call in ctx.run_task.call_args_list))

    def test_unstable_initial_delete_page_cancels_without_scanning(self):
        ctx, shop = context(), ProduceShoppingAuto()
        with patch.object(ProduceHIFChangeSourceAuto, "_rewind_source", return_value=None), \
             patch.object(ProduceHIFChangeSourceAuto, "_scan_source") as scan, \
             patch.object(shop, "_cancel_delete", return_value="skipped") as cancel:
            self.assertEqual("skipped", shop._delete_from_deck(ctx, set(), set(), set()))
        scan.assert_not_called()
        cancel.assert_called_once_with(ctx)

    def test_stopped_delete_entry_never_scans_or_cancels(self):
        ctx, shop = context(), ProduceShoppingAuto()
        ctx.tasker.stopping = True
        with patch.object(ProduceHIFChangeSourceAuto, "_rewind_source") as rewind, \
             patch.object(shop, "_cancel_delete") as cancel:
            self.assertEqual("cancel_pending", shop._delete_from_deck(ctx, set(), set(), set()))
        rewind.assert_not_called()
        cancel.assert_not_called()

    def test_stopped_at_bottom_does_not_recompute_old_page_cache(self):
        ctx, action = context(), ProduceHIFChangeSourceAuto()
        image = np.zeros((1280, 720, 3), np.uint8)
        cards = [candidate("rare", "R")]
        pages = 0
        def page(*args, **kwargs):
            nonlocal pages
            pages += 1
            if pages == 3:
                ctx.tasker.stopping = True
            return image, cards
        with patch("custom.deck_scan.DeckIdentityCache") as cache_class, \
             patch.object(action, "_page", side_effect=page), \
             patch.object(action, "_same_page", return_value=True), \
             patch.object(action, "_scroll"), \
             patch.object(ProduceShoppingAuto, "_probe_removal_candidates", side_effect=lambda c, items, *a, **k: items):
            cache = cache_class.return_value
            cache.restore.side_effect = lambda image, items: items
            self.assertIsNone(action._scan_source(ctx, set(), set(), set(), change=False))
            self.assertEqual(1, cache.restore.call_count)

    def test_no_permitted_target_skips_old_page_identity_replay(self):
        ctx, action = context(), ProduceHIFChangeSourceAuto()
        image = np.zeros((1280, 720, 3), np.uint8)
        cards = [candidate("replacement", "SSR")]
        with patch("custom.deck_scan.DeckIdentityCache") as cache_class, \
             patch.object(action, "_page", return_value=(image, cards)), \
             patch.object(action, "_same_page", return_value=True), \
             patch.object(action, "_scroll"), \
             patch.object(ProduceShoppingAuto, "_probe_removal_candidates", side_effect=lambda c, items, *a, **k: items):
            cache = cache_class.return_value
            cache.restore.side_effect = lambda image, items: items
            self.assertIsNone(action._scan_source(ctx, set(), set(), {"replacement"}, change=False))
            self.assertEqual(1, cache.restore.call_count)

    def test_hif_skipped_resumes_shop_without_deletion_loop(self):
        ctx, action = context(), ProduceHIFDeleteAuto()
        with patch("custom.action.produce._detect_disconnect_screen", return_value=False), \
             patch("custom.action.produce._load_card_lists", return_value={}), \
             patch("custom.shop.ocr", return_value=[]), \
             patch.object(action, "_delete_from_deck", return_value="skipped"), \
             patch.object(action, "_reenter_shop", return_value=True) as reenter, \
             patch.object(ProduceShoppingAuto, "run", return_value=True) as resume:
            self.assertTrue(action.run(ctx, SimpleNamespace()))
        reenter.assert_called_once_with(ctx)
        resume.assert_called_once()
        self.assertTrue(action._skip_delete)

    def test_hif_pending_cancel_keeps_task_alive_without_shop_actions(self):
        ctx, action = context(), ProduceHIFDeleteAuto()
        with patch("custom.action.produce._detect_disconnect_screen", return_value=False), \
             patch("custom.action.produce._load_card_lists", return_value={}), \
             patch("custom.shop.ocr", return_value=[]), \
             patch.object(action, "_delete_from_deck", return_value="cancel_pending"), \
             patch.object(action, "_reenter_shop") as reenter, \
             patch.object(ProduceShoppingAuto, "run") as resume:
            self.assertTrue(action.run(ctx, SimpleNamespace()))
        reenter.assert_not_called()
        resume.assert_not_called()
        ctx.tasker.controller.post_click.assert_not_called()
        ctx.run_task.assert_not_called()


if __name__ == "__main__":
    unittest.main()

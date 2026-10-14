"""Regression for a swallowed deck click returning the preceding card detail."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent'))
from custom.action.produce import ProduceShoppingAuto, ProduceHIFChangeSourceAuto
from custom.deck_scan import DeckIdentityCache, deck_scrollbar_at_top
from custom.removal import selected_card_box_matches


class CrossPageAuditTest(unittest.TestCase):
    @staticmethod
    def selected_frame(color=(20, 155, 255)):
        image = np.zeros((1280, 720, 3), np.uint8)
        box = [82, 650, 118, 118]
        x, y, w, h = box
        for left, top in ((x - 8, y - 8), (x + w - 14, y - 8),
                          (x - 8, y + h - 14), (x + w - 14, y + h - 14)):
            image[top:top + 22, left:left + 22] = color
        image[y + 2:y + h - 2, x + 2:x + w - 2] = 90
        return image, box

    def test_selection_requires_four_outer_orange_corners(self):
        image, box = self.selected_frame()
        self.assertTrue(selected_card_box_matches(image, box))
        self.assertFalse(selected_card_box_matches(image, [228, 650, 118, 118]))
        image[630:670, 60:110] = 0
        self.assertFalse(selected_card_box_matches(image, box))

    def test_unselected_gold_rarity_and_orange_art_are_not_selection(self):
        for color in ((20, 220, 250), (140, 165, 240), (50, 80, 255)):
            image, box = self.selected_frame(color)
            self.assertFalse(selected_card_box_matches(image, box), color)
        image = np.zeros((1280, 720, 3), np.uint8)
        image[652:766, 84:198] = (20, 155, 255)
        self.assertFalse(selected_card_box_matches(image, [82, 650, 118, 118]))

    def test_selection_rejects_missing_or_clipped_frame(self):
        self.assertFalse(selected_card_box_matches(None, [82, 650, 118, 118]))
        image = np.full((1280, 720, 3), (20, 155, 255), np.uint8)
        for box in ([0, 650, 118, 118], [82, 0, 118, 118], [650, 650, 118, 118],
                    [82, 1200, 118, 118], [82, 650, 0, 118], [82, 650, 118]):
            self.assertFalse(selected_card_box_matches(image, box), box)

    def test_resume_already_selected_visual_candidate_is_not_toggled_off(self):
        image, box = self.selected_frame()
        image[1140:1170, 420:585] = (40, 150, 250)
        unselected = image.copy()
        blue, green, red = unselected[..., 0], unselected[..., 1], unselected[..., 2]
        unselected[(blue < 110) & (green >= 95) & (green <= 210) & (red > 225)] = 0
        state = dict(selected=True, submitted=False)
        ctx = Mock()
        ctx.tasker.stopping = False
        ctx.tasker.controller.post_screencap.return_value.wait.return_value.get.side_effect = (
            lambda: np.zeros_like(image) if state['submitted'] else image if state['selected'] else unselected)
        def click(x, y):
            if y < 1100:
                state['selected'] = not state['selected']
            else:
                state['submitted'] = True
            return Mock()
        ctx.tasker.controller.post_click.side_effect = click
        candidate = dict(name='candidate', name_key='candidate', rarity='SR',
                         delete_rank=0, box=box, detail_selected=False)
        action = ProduceHIFChangeSourceAuto()
        with patch('custom.action.produce.time.sleep'), \
             patch('custom.action.produce._detect_disconnect_screen', return_value=False), \
             patch('custom.hif.is_hif_change_source', side_effect=lambda *args: not state['submitted']), \
             patch('custom.removal.read_selected_card', return_value=candidate), \
             patch.object(action, '_ocr', return_value=[SimpleNamespace(box=[460, 1140, 120, 30])]), \
             patch.object(action, '_same_page', return_value=False):
            self.assertTrue(action._submit(ctx, candidate))
        self.assertEqual([(520, 1155)], [call.args for call in ctx.tasker.controller.post_click.call_args_list])

    def test_resume_already_selected_delete_candidate_is_not_toggled_off(self):
        image, box = self.selected_frame()
        selected = dict(name='candidate', name_key='candidate', rarity='R',
                        delete_rank=1, box=box, detail_selected=False)
        invoice = dict(selected=selected, before=420, after=358, confirm=(522, 1157))
        ctx = Mock()
        ctx.tasker.stopping = False
        ctx.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value = image
        shop = ProduceShoppingAuto()
        with patch('custom.action.produce.time.sleep'), \
             patch('custom.action.produce._detect_disconnect_screen', return_value=False), \
             patch.object(ProduceHIFChangeSourceAuto, '_rewind_source', return_value=(image, [])), \
             patch.object(ProduceHIFChangeSourceAuto, '_scan_source', return_value=selected), \
             patch.object(shop, '_verify_delete_selection', return_value=selected), \
             patch('custom.removal.read_delete_button', return_value=[394, 1135, 164, 45]), \
             patch('custom.removal.read_delete_confirmation', return_value=invoice), \
             patch.object(shop, '_commit_delete', return_value='deleted'):
            self.assertEqual('deleted', shop._delete_from_deck(ctx, set(), set(), {'candidate'}))
        ctx.tasker.controller.post_click.assert_called_once_with(476, 1157)

    def test_delete_final_check_rejects_stale_title_at_wrong_tile(self):
        image, _ = self.selected_frame()
        selected = dict(name='starter', name_key='starter', rarity='N', name_confidence=1.,
                        rarity_confidence=1., box=[228, 650, 118, 118])
        with patch('custom.shop.ocr', return_value=[SimpleNamespace(text='starter', score=1.)]) as reader:
            self.assertIsNone(ProduceShoppingAuto()._verify_delete_selection(Mock(), image, selected, set(), set(), set()))
        reader.assert_not_called()

    def test_delete_final_check_accepts_matching_outline_and_exact_title(self):
        image, box = self.selected_frame()
        selected = dict(name='starter', name_key='starter', rarity='N', name_confidence=.9,
                        rarity_confidence=1., box=box)
        identity = SimpleNamespace(matched_name='starter', rarity='N', confidence=1.)
        with patch('custom.shop.ocr', return_value=[SimpleNamespace(text='starter', score=1.)]), \
             patch('custom.action.produce.match_card_catalog', return_value=identity):
            verified = ProduceShoppingAuto()._verify_delete_selection(Mock(), image, selected, set(), set(), set())
        self.assertTrue(verified['identity_verified'])
        self.assertEqual(('starter', 'N', 3), (verified['name'], verified['rarity'], verified['delete_rank']))

    def test_unchanged_old_detail_must_not_be_cached_for_a_different_tile(self):
        image = np.zeros((1280, 720, 3), np.uint8)
        rng = np.random.default_rng(110)
        image[650:768, 82:200] = rng.integers(0, 256, (118, 118, 3), dtype=np.uint8)
        image[650:768, 228:346] = rng.integers(0, 256, (118, 118, 3), dtype=np.uint8)
        # Selection stays on the old, left tile after the requested right-tile
        # click. The detail OCR is stable but belongs to that old selection.
        image[642:648, 74:208] = (20, 155, 255)
        image[770:776, 74:208] = (20, 155, 255)
        image[642:776, 74:80] = (20, 155, 255)
        image[642:776, 202:208] = (20, 155, 255)
        candidate = dict(name='', name_key='', rarity='SR', name_confidence=0.,
                         rarity_confidence=.4, box=[228, 650, 118, 118])
        stale = dict(name='old-card', name_key='old-card', rarity='SR',
                     name_confidence=1., rarity_confidence=1., identity_verified=True)
        shop = ProduceShoppingAuto()
        shop._deck_identity_cache = DeckIdentityCache()
        shop._deck_image = image
        ctx = Mock()
        ctx.tasker.stopping = False
        ctx.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value = image
        with patch('custom.action.produce.time.sleep'), \
             patch('custom.action.produce._detect_disconnect_screen', return_value=False), \
             patch('custom.removal.read_selected_card', return_value=stale), \
             patch.object(shop, '_click_product'):
            result = shop._probe_removal_candidates(ctx, [candidate], set(), set(), {'old-card'}, change=True)
        self.assertFalse(result[0].get('identity_verified', False))
        self.assertTrue(result[0].get('probe_failed', False))
        self.assertEqual([], shop._deck_identity_cache.entries)

    @staticmethod
    def scrollbar_frame(head=None, *, width=5):
        image = np.zeros((1280, 720, 3), np.uint8)
        if head is not None:
            image[head:head + 280, 680:680 + width] = 170
        return image

    def test_scrollbar_distinguishes_top_bottom_absence_and_wide_panel(self):
        self.assertTrue(deck_scrollbar_at_top(self.scrollbar_frame(638)))
        self.assertFalse(deck_scrollbar_at_top(self.scrollbar_frame(794)))
        self.assertIsNone(deck_scrollbar_at_top(self.scrollbar_frame()))
        self.assertIsNone(deck_scrollbar_at_top(self.scrollbar_frame(638, width=15)))
        self.assertIsNone(deck_scrollbar_at_top(self.scrollbar_frame(638, width=1)))
        self.assertTrue(deck_scrollbar_at_top(self.scrollbar_frame(398), top=380))
        self.assertFalse(deck_scrollbar_at_top(self.scrollbar_frame(650), top=380))

    def test_scrollbar_real_source_screenshots(self):
        from PIL import Image
        fixtures = Path(__file__).resolve().parents[1] / 'tests/fixtures/deck'
        for name, expected in (('selected-window.png', True), ('bottom-window.png', False)):
            with Image.open(fixtures/name) as source:
                image = np.asarray(source.convert('RGB').crop((1,45,721,1325)))[:, :, ::-1].copy()
            self.assertIs(deck_scrollbar_at_top(image), expected, name)

    def rewind(self, images, *, stopping=False):
        ctx = Mock()
        ctx.tasker.stopping = stopping
        action = ProduceHIFChangeSourceAuto()
        with patch.object(action, '_page', side_effect=[(image, []) for image in images]), \
             patch.object(action, '_scroll') as scroll:
            result = action._rewind_source(ctx)
        return result, scroll

    def test_confirmed_top_reuses_initial_page_without_swiping(self):
        first = self.scrollbar_frame(638)
        result, scroll = self.rewind([first])
        self.assertIs(result[0], first)
        scroll.assert_not_called()

    def test_resume_from_bottom_rewinds_until_confirmed_top(self):
        bottom, middle, first = [self.scrollbar_frame(head) for head in (794, 710, 638)]
        result, scroll = self.rewind([bottom, middle, first])
        self.assertIs(result[0], first)
        self.assertEqual([False, False], [call.kwargs['forward'] for call in scroll.call_args_list])

    def test_failed_reverse_drags_at_visible_bottom_do_not_claim_top(self):
        bottom = self.scrollbar_frame(794)
        result, scroll = self.rewind([bottom] * 7)
        self.assertIsNone(result)
        self.assertEqual(6, scroll.call_count)

    def test_unknown_scrollbar_requires_two_stationary_reverse_observations(self):
        unknown = self.scrollbar_frame()
        result, scroll = self.rewind([unknown] * 3)
        self.assertIs(result[0], unknown)
        self.assertEqual(2, scroll.call_count)

    def test_disappearing_lower_thumb_does_not_authorize_unknown_top(self):
        bottom, unknown = self.scrollbar_frame(794), self.scrollbar_frame()
        result, scroll = self.rewind([bottom] + [unknown] * 6)
        self.assertIsNone(result)
        self.assertEqual(6, scroll.call_count)

    def test_rewind_stopped_does_not_swipe(self):
        result, scroll = self.rewind([self.scrollbar_frame(794)], stopping=True)
        self.assertIsNone(result)
        scroll.assert_not_called()

    def test_scan_consumes_confirmed_first_page_without_reading_it_again(self):
        image = self.scrollbar_frame(638)
        candidate = dict(name='starter', name_key='starter', rarity='N', name_confidence=1.,
                         identity_verified=True, box=[82, 650, 118, 118])
        action = ProduceHIFChangeSourceAuto()
        ctx = Mock()
        ctx.tasker.stopping = False
        with patch.object(action, '_page') as read, patch.object(action, '_scroll') as scroll:
            selected = action._scan_source(ctx, set(), set(), set(), initial_page=(image, [candidate]))
        self.assertEqual('starter', selected['name'])
        read.assert_not_called()
        scroll.assert_not_called()

    def test_failed_forward_drags_above_bottom_cannot_finish_global_scan(self):
        candidate = dict(name='replacement', name_key='replacement', rarity='SR', name_confidence=1.,
                         identity_verified=True, box=[82, 650, 118, 118])
        for head in (638, 710):
            image = self.scrollbar_frame(head)
            action = ProduceHIFChangeSourceAuto()
            ctx = Mock()
            ctx.tasker.stopping = False
            with patch.object(action, '_page', return_value=(image, [candidate])), \
                 patch.object(action, '_scroll') as scroll:
                selected = action._scan_source(ctx, set(), set(), {'replacement'})
            self.assertIsNone(selected, head)
            self.assertEqual(5, scroll.call_count)
            self.assertTrue(all(call.kwargs['forward'] for call in scroll.call_args_list))

    def test_stationary_visible_bottom_allows_completed_global_scan(self):
        image = self.scrollbar_frame(814)  # lower edge1094, same as real bottom fixture
        candidate = dict(name='replacement', name_key='replacement', rarity='SR', name_confidence=1.,
                         identity_verified=True, box=[82, 650, 118, 118])
        action = ProduceHIFChangeSourceAuto()
        ctx = Mock()
        ctx.tasker.stopping = False
        with patch.object(action, '_page', return_value=(image, [candidate])), \
             patch.object(action, '_scroll') as scroll:
            selected = action._scan_source(ctx, set(), set(), {'replacement'})
        self.assertEqual('replacement', selected['name'])
        self.assertEqual(2, scroll.call_count)


if __name__ == '__main__':
    unittest.main()

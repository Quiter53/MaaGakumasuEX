"""Deck-only localization and exact-identity cache regressions."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent'))
from custom.deck_scan import DeckIdentityCache, complete_grid_rows, normalize_deck_boxes
from custom.produce.shop import ProduceShoppingAuto


def identity(name='starter'):
    return dict(name=name, name_key=name, rarity='N', name_confidence=1.,
                identity_verified=True, detail_selected=True, box=[82, 635, 116, 116])


class DeckScanTest(unittest.TestCase):
    def test_shop_price_bleed_and_highlight_box_fit_actual_tile_edges(self):
        image = np.zeros((1280,720,3), np.uint8)
        physical = [[x,388,116,116] for x in (82,228,374,520)]
        for x,y,w,h in physical:
            image[y:y+h,x:x+w] = 200
        raw = [[82,384,116,134], [228,385,116,132],
               [368,378,132,151], [520,385,116,130]]
        result = normalize_deck_boxes(image, raw)
        self.assertEqual(physical, result)

    def test_malformed_box_without_actual_edges_is_not_clamped_into_a_card(self):
        image = np.zeros((1280,720,3), np.uint8)
        anchors = [[82,680,116,116], [228,680,116,116]]
        raw = anchors + [[368,378,132,151]]
        self.assertEqual(anchors, normalize_deck_boxes(image, raw))

    def test_normalization_keeps_valid_boxes_and_rejects_partial_footer(self):
        image = np.zeros((1280,720,3), np.uint8)
        anchors = [[82,680,116,116], [228,680,116,116]]
        for x,y,w,h in anchors:
            image[y:y+h,x:x+w] = 200
        image[1050:1180,374:490] = 200
        self.assertEqual(anchors, normalize_deck_boxes(image, anchors+[[374,1050,116,130]]))

    def test_complete_row_requires_four_real_edges(self):
        image = np.zeros((1280, 720, 3), np.uint8)
        for x in (82, 228, 374):
            image[635:751, x:x+116] = 200
        anchors = [[82,635,116,116], [228,635,116,116]]
        self.assertEqual(anchors + [[374,635,116,116]], complete_grid_rows(image, anchors))

    def test_no_invented_row_without_two_anchors(self):
        image = np.full((1280,720,3), 200, np.uint8)
        self.assertEqual([], complete_grid_rows(image, []))
        anchor = [[82,635,116,116]]
        self.assertEqual(anchor, complete_grid_rows(image, anchor))

    def test_partial_or_borderless_slot_is_not_added(self):
        image = np.zeros((1280,720,3), np.uint8)
        image[635:720,374:490] = 200  # partial tile, no bottom edge
        anchors = [[82,635,116,116], [228,635,116,116]]
        self.assertEqual(anchors, complete_grid_rows(image, anchors))

    def test_single_last_row_recovered_only_where_four_edges_exist(self):
        image = np.zeros((1280,720,3), np.uint8)
        anchors = [[x,y,116,116] for y in (680,826) for x in (82,228,374,520)]
        for x,y,w,h in anchors + [[82,972,116,116]]:
            image[y:y+h,x:x+w] = 200
        self.assertEqual(anchors+[[82,972,116,116]], complete_grid_rows(image, anchors))

    def test_missing_middle_row_recovered_from_grid(self):
        image = np.zeros((1280,720,3), np.uint8)
        anchors = [[x,y,116,116] for y in (640,932) for x in (82,228)]
        missing = [82,786,116,116]
        for x,y,w,h in anchors+[missing]:
            image[y:y+h,x:x+w] = 200
        self.assertIn(missing, complete_grid_rows(image, anchors))

    def test_missing_first_row_obeys_scene_pane_boundary(self):
        image = np.zeros((1280,720,3), np.uint8)
        anchors = [[x,y,116,116] for y in (786,932) for x in (82,228)]
        missing = [82,640,116,116]
        for x,y,w,h in anchors+[missing]:
            image[y:y+h,x:x+w] = 200
        self.assertIn(missing, complete_grid_rows(image, anchors, top=620))
        self.assertNotIn(missing, complete_grid_rows(image, anchors, top=670))

    def test_extrapolation_rejects_partial_empty_and_wrong_spacing(self):
        anchors = [[x,y,116,116] for y in (680,826) for x in (82,228)]
        for mode in ('empty', 'partial', 'wrong_spacing'):
            image = np.zeros((1280,720,3), np.uint8)
            for x,y,w,h in anchors:
                image[y:y+h,x:x+w] = 200
            if mode == 'partial':
                image[972:1050,82:198] = 200
            elif mode == 'wrong_spacing':
                image[955:1071,82:198] = 200
            self.assertEqual(anchors, complete_grid_rows(image, anchors), mode)

    def scene(self):
        image = np.zeros((1280,720,3), np.uint8)
        tile = np.random.default_rng(17).integers(0,256,(116,116,3),dtype=np.uint8)
        image[635:751,82:198] = tile
        image[800:916,374:490] = tile
        return image

    def test_overlap_reuses_identity_but_never_old_coordinate_or_selection(self):
        image = self.scene(); cache = DeckIdentityCache()
        cache.remember(image, [identity()])
        unknown = dict(box=[374,800,116,116], identity_verified=False, name='unknown')
        result = cache.restore(image, [unknown])[0]
        self.assertEqual('starter', result['name'])
        self.assertEqual(unknown['box'], result['box'])
        self.assertFalse(result['detail_selected'])
        self.assertTrue(result['identity_verified'])

    def test_conflicting_identical_art_is_not_reused(self):
        image = self.scene(); cache = DeckIdentityCache()
        cache.remember(image, [identity('a'), identity('b')])
        result = cache.restore(image, [dict(box=[374,800,116,116],identity_verified=False)])[0]
        self.assertFalse(result['identity_verified'])

    def test_unverified_failed_and_flat_tiles_are_not_cached(self):
        cache = DeckIdentityCache(); image = self.scene()
        cache.remember(image, [dict(identity(),identity_verified=False),dict(identity(),probe_failed=True)])
        cache.remember(np.zeros_like(image), [identity()])
        flat=np.zeros_like(image); flat[:]=(230,80,20)
        cache.remember(flat,[identity()])
        self.assertFalse(cache.entries)

    def test_effect_overlay_change_and_new_transaction_do_not_reuse(self):
        image = self.scene(); cache = DeckIdentityCache()
        cache.remember(image, [identity()])
        image[810:855,384:435] = 0
        unknown = dict(box=[374,800,116,116],identity_verified=False)
        self.assertFalse(cache.restore(image,[unknown])[0]['identity_verified'])
        self.assertFalse(DeckIdentityCache().restore(self.scene(),[unknown])[0]['identity_verified'])

    def test_cached_card_does_not_trigger_another_detail_click(self):
        image = self.scene(); cache = DeckIdentityCache()
        cache.remember(image, [identity()])
        items = cache.restore(image, [dict(box=[374,800,116,116],identity_verified=False)])
        context = Mock(); context.tasker = SimpleNamespace(stopping=False)
        shop = ProduceShoppingAuto()
        with patch.object(shop, '_click_product') as click:
            result = shop._probe_removal_candidates(context, items, set(), set(), set(), change=True)
        click.assert_not_called()
        self.assertFalse(result[0]['detail_selected'])  # final submit must select afresh

    def test_detector_jitter_is_aligned_without_lowering_identity_threshold(self):
        image = self.scene(); cache = DeckIdentityCache()
        cache.remember(image, [identity()])
        for box in ([376,798,114,120], [371,803,120,112], [374,800,116,116]):
            with self.subTest(box=box):
                result = cache.restore(image, [dict(box=box,identity_verified=False)])[0]
                self.assertTrue(result['identity_verified'])
                self.assertEqual(box, result['box'])

    def test_identical_copies_remain_two_selectable_observations(self):
        image=self.scene(); cache=DeckIdentityCache(); cache.remember(image,[identity()])
        copies=[dict(box=box,identity_verified=False) for box in ([82,635,116,116],[374,800,116,116])]
        result=cache.restore(image,copies)
        self.assertEqual(2,len(result))
        self.assertTrue(all(item['identity_verified'] for item in result))
        self.assertNotEqual(result[0]['box'],result[1]['box'])

    def test_remember_does_not_grow_for_same_observation(self):
        image=self.scene(); cache=DeckIdentityCache()
        for _ in range(5):
            cache.remember(image,[identity()])
        self.assertEqual(1,len(cache.entries))

    def test_same_page_duplicate_is_probed_once(self):
        image=self.scene(); shop=ProduceShoppingAuto()
        shop._deck_identity_cache=DeckIdentityCache(); shop._deck_image=image
        items=[dict(identity(),name='unknown',name_key='',rarity='SR',identity_verified=False,box=box)
               for box in ([82,635,116,116],[374,800,116,116])]
        exact=dict(identity(),rarity='SR')
        ctx=Mock(); ctx.tasker.stopping=False
        ctx.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value=image
        with patch('time.sleep'), \
             patch('custom.produce.shared._detect_disconnect_screen',return_value=False), \
             patch('custom.removal.selected_card_box_matches',return_value=True), \
             patch('custom.removal.read_selected_card',return_value=exact), \
             patch.object(shop,'_click_product') as click:
            result=shop._probe_removal_candidates(ctx,items,set(),set(),{'starter'})
        self.assertEqual(1,click.call_count)
        self.assertEqual(2,len(result))
        self.assertTrue(all(item['identity_verified'] for item in result))


if __name__ == '__main__':
    unittest.main()

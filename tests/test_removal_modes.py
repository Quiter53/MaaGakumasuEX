"""Fast/strict stopping rules and whole deletion grids; no game input."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'agent'))
from custom.deck_scan import deck_view_complete, normalize_deck_boxes, removal_priority_bound, strict_removal_mode
from custom.action.produce import ProduceShoppingAuto, ProduceHIFChangeSourceAuto


def card(name, rarity='SSR', known=True, x=82):
    return dict(name=name, name_key=name, rarity=rarity, frame_rarity=rarity,
                rarity_confidence=.80, name_confidence=.80 if known else .1,
                name_score=.5, name_margin=.2, box=[x, 390, 116, 116])


def context():
    ctx = Mock()
    ctx.tasker.stopping = False
    return ctx


class RemovalModesTest(unittest.TestCase):
    def test_switch_position_and_both_overrides(self):
        config = json.loads((ROOT/'assets/tasks/produce.json').read_text(encoding='utf-8'))
        for path in ('produce.json', 'produce_cn.json'):
            doc = json.loads((ROOT/'assets/tasks'/path).read_text(encoding='utf-8'))
            options = doc['task'][0]['option']
            self.assertEqual('严格模式', options[options.index('启用关注租借')+1])
        option = config['option']['严格模式']
        self.assertEqual('No', option['default_case'])
        ctx = context()
        for case in option['cases']:
            ctx.get_node_data.return_value = case['pipeline_override']['ProduceDeckScanPolicy']
            self.assertEqual(case['name'] == 'Yes', strict_removal_mode(ctx))
        ctx.get_node_data.return_value = None
        self.assertTrue(strict_removal_mode(ctx))

    def probe(self, items, black=(), replacement=(), strict=True, change=False, exact=None, incumbent=None):
        ctx, shop = context(), ProduceShoppingAuto()
        with patch('custom.action.produce.time.sleep'), \
             patch('custom.action.produce._detect_disconnect_screen', return_value=False), \
             patch('custom.removal.selected_card_box_matches', return_value=True), \
             patch('custom.removal.read_selected_card', return_value=exact):
            result = shop._probe_removal_candidates(ctx, items, set(black), set(), set(replacement),
                                                    change=change, strict=strict, incumbent=incumbent)
        return result, ctx

    def test_fast_known_blacklist_skips_possible_n_probe(self):
        items = [card('black'), card('unclear', 'N', False, 228)]
        _, ctx = self.probe(items, black={'black'}, strict=False)
        ctx.tasker.controller.post_click.assert_not_called()

    def test_strict_known_blacklist_checks_possible_n(self):
        items = [card('black'), card('unclear', 'N', False, 228)]
        exact = dict(card('starter', 'N'), identity_verified=True, name_confidence=1.)
        result, ctx = self.probe(items, black={'black'}, exact=exact)
        ctx.tasker.controller.post_click.assert_called_once()
        self.assertEqual('starter', ProduceShoppingAuto._choose_delete_candidate(result, {'black'}, set())['name'])

    def test_blacklist_is_optimal_once_remaining_frames_exclude_n(self):
        _, ctx = self.probe([card('black'), card('unknown', 'SR', False)], black={'black'})
        ctx.tasker.controller.post_click.assert_not_called()

    def test_no_blacklist_no_n_or_r_needs_no_names_for_delete(self):
        _, ctx = self.probe([card('unknown', 'SR', False), card('unknown2', 'SSR', False)], replacement={'unknown'})
        ctx.tasker.controller.post_click.assert_not_called()

    def test_rarity_uncertain_cannot_rule_out_n(self):
        item = dict(card('unknown', 'SSR', False), rarity_confidence=.54)
        self.assertEqual((3, 0), removal_priority_bound(item, set(), set()))

    def test_replacement_sr_optimal_over_unidentified_ssr(self):
        _, ctx = self.probe([card('replace', 'SR'), card('unknown', 'SSR', False)],
                            replacement={'replace', 'unknown'}, change=True)
        ctx.tasker.controller.post_click.assert_not_called()

    def test_replacement_ssr_still_checks_possible_sr(self):
        exact = dict(card('sr', 'SR'), identity_verified=True, name_confidence=1.)
        _, ctx = self.probe([card('replace'), card('sr', 'SR', False)],
                            replacement={'replace', 'sr'}, change=True, exact=exact)
        ctx.tasker.controller.post_click.assert_called_once()

    def test_prior_page_blacklist_suppresses_lower_tier_details(self):
        incumbent = dict(card('black'), delete_rank=2)
        _, ctx = self.probe([card('unknown', 'SR', False)], black={'black'}, incumbent=incumbent)
        ctx.tasker.controller.post_click.assert_not_called()

    def test_fast_scanner_does_not_visit_other_pages(self):
        action, ctx = ProduceHIFChangeSourceAuto(), context()
        image = np.zeros((1280, 720, 3), np.uint8)
        with patch.object(action, '_scroll') as scroll:
            result = action._scan_source(ctx, {'black'}, set(), set(), strict=False,
                                         initial_page=(image, [card('black')]))
        self.assertEqual('black', result['name'])
        scroll.assert_not_called()

    def test_known_optimum_or_fast_hit_never_rewinds_from_middle(self):
        image = np.zeros((1280,720,3), np.uint8)
        for strict, rarity in ((True, 'N'), (False, 'SR')):
            action, ctx = ProduceHIFChangeSourceAuto(), context()
            item = dict(card('allowed', rarity), box=[82,680,116,116])
            action._removal_search_policy = ({'allowed'}, set(), set(), strict)
            with patch.object(action, '_page', return_value=(image, [item])), patch.object(action, '_scroll') as scroll:
                self.assertIsNotNone(action._rewind_source(ctx, change=False))
            scroll.assert_not_called()

    def test_fast_preserves_white_protection_and_delete_excludes_replacements(self):
        image = np.zeros((1280,720,3), np.uint8)
        items = [card('white', 'N'), card('replace', 'SR')]
        ctx, action = context(), ProduceHIFChangeSourceAuto()
        with patch.object(action, '_page', return_value=(image, items)), \
             patch.object(action, '_scroll'), patch.object(action, '_same_page', return_value=True):
            self.assertIsNone(action._scan_source(ctx, set(), {'white'}, {'replace'}, change=False,
                                                 strict=False, initial_page=(image, items)))
        ctx.tasker.controller.post_click.assert_not_called()

    def test_actual_eighteen_card_grid_is_complete_and_never_scrolls(self):
        with Image.open(ROOT/'tests/fixtures/deck/shop-delete-18-scroll-desktop.png') as source:
            image = np.asarray(source.convert('RGB').crop((789,201,1509,1481)))[:,:,::-1].copy()
        raw = [[78,385,120,124],[79,535,118,117],[81,682,117,117],[83,975,115,117],
               [227,386,118,130],[228,534,116,116],[228,680,116,118],[228,828,117,118],
               [229,974,115,117],[374,386,117,143],[375,829,116,118],[376,531,115,119],
               [376,680,114,119],[521,386,119,126],[521,535,118,118],[522,680,116,120]]
        boxes = normalize_deck_boxes(image, raw)
        self.assertEqual(18, len(boxes))
        self.assertTrue(deck_view_complete(image, boxes))
        self.assertFalse(deck_view_complete(image, boxes, change=True))
        items = [dict(card(str(index)), box=box) for index, box in enumerate(boxes)]
        action, ctx = ProduceHIFChangeSourceAuto(), context()
        with patch.object(action, '_page', return_value=(image, items)), patch.object(action, '_scroll') as scroll:
            initial = action._rewind_source(ctx, change=False)
            self.assertIsNotNone(initial)
            result = action._scan_source(ctx, set(), set(), set(), change=False, initial_page=initial)
        self.assertIsNone(result)
        scroll.assert_not_called()
        ctx.tasker.controller.post_click.assert_not_called()
        # Missing middle tile or below-top viewport cannot prove completeness.
        self.assertFalse(deck_view_complete(image, boxes[1:]))
        self.assertFalse(deck_view_complete(image, [b for b in boxes if not (b[0]>200 and b[1]>950)]))
        self.assertFalse(deck_view_complete(image, [[x,y+50,w,h] for x,y,w,h in boxes]))

    def test_delete_bottom_allows_small_bounce_but_change_keeps_footer_boundary(self):
        image = np.zeros((1280,720,3), np.uint8)
        boxes = [[82,986,116,116], [228,986,116,116]]
        self.assertEqual(2, len(normalize_deck_boxes(image, boxes, top=380)))
        self.assertEqual([], normalize_deck_boxes(image, boxes, top=620))

    def test_count_alone_does_not_prove_complete_and_scrollbar_can_veto(self):
        image = np.zeros((1280,720,3), np.uint8)
        boxes = [[82+c*146,390+r*146,116,116] for r in range(5) for c in range(4)][:18]
        self.assertTrue(deck_view_complete(image, boxes))
        image[450:850,683:688] = 170
        self.assertFalse(deck_view_complete(image, boxes))
        self.assertFalse(deck_view_complete(image, boxes[:-3]))


if __name__ == '__main__':
    unittest.main()

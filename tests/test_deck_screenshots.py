"""User-provided 720px client captures, plus real native detector boxes.

These are reproduction fixtures, not an independent recognition benchmark.
"""
import sys
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'agent'))
from custom.deck_scan import complete_grid_rows, normalize_deck_boxes, same_deck_view
from custom.removal import selected_card_box_matches


def frame(name):
    with Image.open(ROOT/'tests/fixtures/deck'/name) as source:
        return np.asarray(source.convert('RGB').crop((1,45,721,1325)))[:,:,::-1].copy()


BOTTOM_BOXES = [[83,682,114,117], [377,682,113,116], [523,682,116,116],
                [230,683,115,116], [522,826,118,119], [84,828,115,118],
                [377,828,114,118], [228,829,117,117]]


@unittest.skipUnless((Path(__file__).parent / 'fixtures/deck/shop-delete-18-desktop.png').exists(), 'Local replay frames not distributed')
class DeckScreenshotTest(unittest.TestCase):
    def test_shop_delete_native_boxes_recover_eighteen_without_background_prices(self):
        with Image.open(ROOT/'tests/fixtures/deck/shop-delete-18-desktop.png') as source:
            image = np.asarray(source.convert('RGB').crop((752,89,1472,1369)))[:,:,::-1].copy()
        # Actual original-model native output, including the oversized first
        # row box and a false tall box covering part of another card below it.
        raw = [[28,107,149,201],[80,534,117,116],[81,384,118,133],
               [81,681,117,119],[81,824,115,120],[83,970,115,120],
               [227,972,117,121],[228,385,117,130],[228,534,116,117],
               [228,679,117,118],[228,828,117,117],[228,901,116,194],
               [368,378,132,151],[375,679,114,119],[375,827,115,118],
               [376,528,114,121],[521,385,118,127],[522,535,117,115],
               [523,680,116,119]]
        boxes = normalize_deck_boxes(image, raw)
        self.assertEqual(18, len(boxes))
        rows = {index:[] for index in range(5)}
        for box in boxes:
            row = round((box[1]-386)/147)
            rows[row].append(round((box[0]-82)/146))
        self.assertEqual({0:[0,1,2,3],1:[0,1,2,3],2:[0,1,2,3],
                          3:[0,1,2,3],4:[0,1]},
                         {index:sorted(columns) for index,columns in rows.items()})
        first_row = [box for box in boxes if box[1] < 500]
        self.assertTrue(all(abs(w-h) <= 3 for _,_,w,h in first_row))
        self.assertFalse(any(box[1] == 901 for box in boxes))
        # Repair is stable, and orange brackets around the last card still
        # point to that physical card rather than an adjacent slot.
        self.assertEqual(boxes, normalize_deck_boxes(image, boxes))
        selected = [box for box in boxes if selected_card_box_matches(image, box)]
        self.assertEqual(1, len(selected))
        self.assertGreater(selected[0][1], 950)
        self.assertTrue(220 <= selected[0][0] <= 235)

    def test_selection_and_detail_change_do_not_create_a_new_page(self):
        before = frame('bottom-window.png')
        with Image.open(ROOT/'tests/fixtures/deck/bottom-selected-desktop.png') as source:
            after = np.asarray(source.convert('RGB').crop((775,55,1495,1335)))[:,:,::-1].copy()
        old = complete_grid_rows(before, BOTTOM_BOXES, top=620)
        new = complete_grid_rows(after, [[83,681,114,117],[376,682,114,116],
                                [522,682,116,116],[229,683,114,116],[522,826,118,119],
                                [82,827,115,119],[376,827,114,119],[226,828,119,120]], top=620)
        self.assertTrue(same_deck_view(before, after, old, new))

    def test_live_singleton_selection_tint_does_not_create_a_new_page(self):
        images = []
        for name in ('live-bottom.png', 'live-bottom-selected.png'):
            with Image.open(ROOT/'tests/fixtures/deck'/name) as source:
                images.append(np.asarray(source.convert('RGB').crop((1,30,481,884)).resize((720,1280)))[:,:,::-1].copy())
        anchors = [[82,680,115,116], [228,682,115,115], [228,828,115,116],
                   [375,682,114,115], [375,824,115,120], [520,824,119,120], [521,681,117,115]]
        boxes = [complete_grid_rows(image, anchors, top=620) for image in images]
        self.assertTrue(same_deck_view(*images, *boxes))

    def test_view_comparison_rejects_scroll_lost_tile_and_replaced_art(self):
        image = frame('bottom-window.png')
        boxes = complete_grid_rows(image, BOTTOM_BOXES, top=620)
        moved = [[x, y+12, w, h] for x,y,w,h in boxes]
        self.assertFalse(same_deck_view(image, image, boxes, moved))
        self.assertFalse(same_deck_view(image, image, boxes, boxes[:-1]))
        self.assertFalse(same_deck_view(image, np.roll(image, 146, axis=0), boxes, boxes))
        changed = image.copy()
        x,y,w,h = boxes[0]
        changed[y+20:y+h-20, x+20:x+w-20] = 0
        self.assertFalse(same_deck_view(image, changed, boxes, boxes))

    def test_view_comparison_ignores_background_but_respects_scrollbar(self):
        image = frame('bottom-window.png')
        boxes = complete_grid_rows(image, BOTTOM_BOXES, top=620)
        changed = np.zeros_like(image)
        for x,y,w,h in boxes:
            changed[y:y+h, x:x+w] = image[y:y+h, x:x+w]
        self.assertTrue(same_deck_view(image, changed, boxes, boxes))
        changed[638:938, 683:688] = 170
        self.assertFalse(same_deck_view(image, changed, boxes, boxes))

    def test_bottom_single_card_added_no_empty_slots(self):
        image = frame('bottom-window.png')
        boxes = complete_grid_rows(image, BOTTOM_BOXES, top=620)
        self.assertEqual(9, len(boxes))
        added = [b for b in boxes if b not in BOTTOM_BOXES]
        self.assertEqual(1, len(added))
        x,y,w,h = added[0]
        self.assertTrue(78<=x<=88 and 968<=y<=982 and 110<=w<=122 and 110<=h<=122)
        self.assertEqual(boxes, complete_grid_rows(image, boxes, top=620))

    def test_bottom_recovery_tolerates_detector_jitter(self):
        image = frame('bottom-window.png')
        for shift in (-2,-1,0,1,2):
            anchors = [[x+shift,y+shift,w,h] for x,y,w,h in BOTTOM_BOXES]
            boxes = complete_grid_rows(image, anchors, top=620)
            self.assertEqual(9, len(boxes), shift)

    def test_live_selection_does_not_match_neighboring_cards(self):
        image = frame('selected-window.png')
        self.assertTrue(selected_card_box_matches(image, [375,636,118,125]))
        for box in ([84,635,115,128], [228,637,117,121], [522,635,117,131], [231,787,115,114]):
            self.assertFalse(selected_card_box_matches(image, box), box)

    def test_no_extra_row_for_partial_footer_cards(self):
        image = frame('selected-window.png')
        boxes = [[x,y,116,116] for y in (639,786,933) for x in (83,229,375,521)]
        self.assertEqual(12, len(complete_grid_rows(image, boxes, top=620)))

    def test_live_bottom_highlight_does_not_shift_inferred_last_row_away(self):
        with Image.open(ROOT/'tests/fixtures/deck/live-bottom-selected.png') as source:
            image = np.asarray(source.convert('RGB').crop((1,30,481,884)).resize((720,1280)))[:,:,::-1].copy()
        # Native detector misses both the last singleton and left tile above
        # it. Taller boxes in row two previously shifted inference upward.
        anchors = [[82,680,115,116], [228,682,115,115], [228,828,115,116],
                   [375,682,114,115], [375,824,115,120], [520,824,119,120], [521,681,117,115]]
        boxes = complete_grid_rows(image, anchors, top=620)
        self.assertEqual(9, len(boxes))
        selected = [box for box in boxes if selected_card_box_matches(image, box)]
        self.assertEqual(1, len(selected))
        self.assertGreater(selected[0][1], 960)

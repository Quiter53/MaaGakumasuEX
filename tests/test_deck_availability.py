"""Unavailable exchange tiles and bounded, inertia-free deck paging."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'agent'))
from custom.deck_scan import deck_card_disabled, deck_scroll_distance, DeckIdentityCache
from custom.produce.rewards import ProduceHIFChangeSourceAuto
from custom.produce.shop import ProduceShoppingAuto
from custom.removal import read_selected_card


def card(name, rarity='N', **kwargs):
    return dict(name=name, name_key=name, rarity=rarity, name_confidence=1.,
                identity_verified=True, box=[82, 650, 116, 116], **kwargs)


class AvailabilityPolicyTest(unittest.TestCase):
    def test_disabled_n_loses_to_enabled_r_and_recovers_next_frame(self):
        sleepy=card('眠気', selectable=False); rare=card('other', 'R')
        choose=ProduceShoppingAuto._choose_removal_candidate
        self.assertEqual('other', choose([sleepy,rare],set(),set())['name'])
        sleepy['selectable']=True
        self.assertEqual('眠気', choose([sleepy,rare],set(),set())['name'])

    def test_disabled_duplicate_and_replacement_are_excluded(self):
        for rarity, count in [('N',None),('R',2),('SSR',2)]:
            item=card('limited',rarity,owned_count=count,selectable=False)
            with patch('custom.produce.shop.single_copy_cards',return_value={'limited'}):
                self.assertIsNone(ProduceShoppingAuto._choose_removal_candidate([item],{'limited'},set(),{'limited'}))

    def test_disabled_unknown_is_not_probed(self):
        ctx=Mock();ctx.tasker.stopping=False
        item=card('sleep',selectable=False,frame_rarity='N',rarity_confidence=1.)
        item['identity_verified']=False;item['name_confidence']=0.
        result=ProduceShoppingAuto()._probe_removal_candidates(ctx,[item],set(),set(),set(),change=True)
        self.assertFalse(result[0]['selectable'])
        ctx.tasker.controller.post_click.assert_not_called()

    def test_identity_cache_cannot_reenable_unavailable_card(self):
        im=np.random.default_rng(1).integers(0,256,(1280,720,3),dtype=np.uint8)
        cache=DeckIdentityCache();cache.remember(im,[card('sleep')])
        result=cache.restore(im,[card('sleep',selectable=False)])[0]
        self.assertFalse(result['selectable']);self.assertTrue(result['identity_verified'])

    def test_global_scan_ignores_later_disabled_n(self):
        a=np.zeros((1280,720,3),np.uint8);b=np.full_like(a,40)
        rare=card('rare','R');sleepy=card('sleep',selectable=False)
        action=ProduceHIFChangeSourceAuto();ctx=Mock();ctx.tasker.stopping=False
        with patch.object(action,'_page',side_effect=[(a,[rare]),(b,[sleepy]),(b,[sleepy]),(b,[sleepy]),(a,[rare])]), \
             patch.object(action,'_scroll'), patch('custom.produce.rewards.single_copy_cards',return_value=set()):
            chosen=action._scan_source(ctx,set(),set(),set(),strict=True)
        self.assertEqual('rare',chosen['name'])

    def test_run_reranks_only_explicit_unavailability_before_commit(self):
        for unavailable, expected in [(True,2),(False,1)]:
            action=ProduceHIFChangeSourceAuto();ctx=Mock();ctx.tasker.stopping=False
            def submit(*args):
                action._source_unavailable=unavailable
                return False
            lists={k:dict(enabled=False,cards=[]) for k in ('blacklist','whitelist')}
            with patch('custom.produce.shared._load_card_lists',return_value=lists), \
                 patch.object(action,'_rewind_source',return_value=(None,[])), \
                 patch.object(action,'_scan_source',return_value=card('sleep')) as scan, \
                 patch.object(action,'_submit',side_effect=submit), patch('time.sleep'):
                self.assertTrue(action.run(ctx,SimpleNamespace()))
            self.assertEqual(expected,scan.call_count)
            ctx.tasker.controller.post_click.assert_not_called()


class DeckPagingTest(unittest.TestCase):
    def test_three_complete_rows_advance_twelve_cards(self):
        boxes=[[x,y,116,116] for y in [638,784,930] for x in [82,228,374,520]]
        self.assertEqual(438,deck_scroll_distance(boxes))
        self.assertEqual(292,deck_scroll_distance(boxes,overlap=True))

    def test_partial_page_aligns_next_unseen_row_without_skipping(self):
        boxes=[[x,y,116,116] for y in [700,846] for x in [82,228,374,520]]
        distance=deck_scroll_distance(boxes)
        self.assertEqual(638,846+146-distance)
        self.assertLess(distance,438)

    def test_detector_jitter_never_turns_one_page_into_four_rows(self):
        for shift in range(-3,4):
            boxes=[[x,y+shift,116,116] for y in [638,784,930] for x in [82,228,374,520]]
            self.assertLessEqual(deck_scroll_distance(boxes),438)

    def test_native_swipe_requests_end_hold_in_both_directions(self):
        action=ProduceHIFChangeSourceAuto();ctx=Mock();ctx.tasker.stopping=False
        action._scroll_geometry=([[x,y,116,116] for y in [638,784,930] for x in [82,228,374,520]],620)
        ctx.run_action_direct.return_value=SimpleNamespace(success=True)
        with patch('time.sleep'), patch('custom.produce.rewards.single_copy_cards',return_value=set()):
            for forward in (True,False):
                self.assertTrue(action._scroll(ctx,forward=forward))
                kind, params=ctx.run_action_direct.call_args.args
                self.assertEqual('Swipe',kind)
                self.assertEqual(438,abs(params.begin[1]-params.end[0][1]))
                self.assertGreaterEqual(params.end_hold[0],300)
                self.assertTrue(620<params.begin[1]<1100 and 620<params.end[0][1]<1100)
        ctx.tasker.controller.post_swipe.assert_not_called()

    def test_stopped_or_failed_drag_does_not_claim_progress(self):
        action=ProduceHIFChangeSourceAuto();ctx=Mock();ctx.tasker.stopping=True
        self.assertFalse(action._scroll(ctx,forward=True));ctx.run_action_direct.assert_not_called()
        ctx.tasker.stopping=False;ctx.run_action_direct.return_value=SimpleNamespace(success=False)
        with patch('time.sleep'):
            self.assertFalse(action._scroll(ctx,forward=True))


@unittest.skipUnless((ROOT/'tests/fixtures/deck/change-disabled-sleep.png').exists(),'Local user screenshots not distributed')
class AvailabilityScreenshotTest(unittest.TestCase):
    @staticmethod
    def frame(name):
        return np.asarray(Image.open(ROOT/'tests/fixtures/deck'/name).convert('RGB').crop((1,45,721,1325)))[:,:,::-1].copy()

    def test_actual_disabled_sleep_tolerates_box_jitter(self):
        im=self.frame('change-disabled-sleep.png')
        for dx in range(-3,4):
            for dy in range(-3,4):
                self.assertTrue(deck_card_disabled(im,[83+dx,974+dy,116,116]))

    def test_enabled_rows_including_dark_art_are_not_excluded(self):
        for name, ys in [('change-enabled-page.png',[638,785,932]),('change-middle-page.png',[662,809,956])]:
            im=self.frame(name)
            for y in ys:
                for x in [82,228,374,520]:
                    self.assertFalse(deck_card_disabled(im,[x,y,116,116]),(name,x,y))

    def test_sleep_natural_gray_art_is_not_itself_disabled(self):
        im=self.frame('change-disabled-sleep.png')
        # The undimmed detail thumbnail is the same gray artwork. This
        # checks appearance only; the preview is not a selectable tile.
        self.assertFalse(deck_card_disabled(im,[38,270,141,142]))

    def test_disabled_submit_never_clicks_card_or_change_button(self):
        im=self.frame('change-disabled-sleep.png');ctx=Mock();ctx.tasker.stopping=False
        ctx.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value=im
        selected=card('眠気');selected['box']=[83,974,116,116]
        action=ProduceHIFChangeSourceAuto()
        with patch('custom.hif.is_hif_change_source',return_value=True), \
             patch('custom.produce.shared._detect_disconnect_screen',return_value=False):
            self.assertFalse(action._submit(ctx,selected))
        self.assertTrue(action._source_unavailable);ctx.tasker.controller.post_click.assert_not_called()

    def test_stale_sleep_description_cannot_identify_selected_glasses(self):
        im=self.frame('change-enabled-page.png')
        title=SimpleNamespace(text='眠気',score=.99,box=[195,270,80,30])
        with patch('custom.removal.ocr',return_value=[title]):
            self.assertIsNone(read_selected_card(Mock(),im,change=True))


if __name__=='__main__':unittest.main()

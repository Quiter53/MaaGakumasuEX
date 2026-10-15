"""Deletion uses the shared strong-art path without skipping final identity checks."""
import ast
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'agent'))
from custom.action.produce import ProduceChooseRewardAuto, ProduceHIFChangeSourceAuto, ProduceShoppingAuto
from custom.deck_scan import DeckIdentityCache, scan_identity_known


def card(name='思考の基本', rarity='N', confidence=.80):
    return dict(name=name, name_key=name, rarity=rarity, name_confidence=confidence,
                name_score=.375, name_margin=.12, frame_rarity=rarity,
                rarity_confidence=.30, box=[82, 635, 116, 116])


def context():
    ctx = Mock()
    ctx.tasker.stopping = False
    ctx.run_recognition.return_value = SimpleNamespace(hit=True)
    ctx.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value = np.zeros((1280, 720, 3), np.uint8)
    return ctx


class DeleteFastRecognitionTest(unittest.TestCase):
    def test_visual_confidence_boundary_is_point_eight(self):
        self.assertTrue(scan_identity_known(card(confidence=.80)))
        self.assertFalse(scan_identity_known(card(confidence=.799)))
        self.assertFalse(card().get('identity_verified', False))

    def test_point_eight_preserves_margin_score_frame_and_conflict_guards(self):
        for difference in (dict(name_score=.374), dict(name_margin=.119),
                           dict(frame_rarity='SSR'), dict(rarity_confidence=.299),
                           dict(identity_conflict=True), dict(probe_failed=True), dict(name_key='')):
            with self.subTest(difference=difference):
                self.assertFalse(scan_identity_known({**card(), **difference}))

    def test_cache_does_not_override_point_eight_conflicting_visual_identity(self):
        image = np.random.default_rng(4).integers(0, 256, (1280, 720, 3), dtype=np.uint8)
        cache = DeckIdentityCache()
        cache.remember(image, [{**card('prior'), 'identity_verified': True}])
        observed = card('different')
        restored = cache.restore(image, [observed])[0]
        self.assertEqual('different', restored['name_key'])
        self.assertFalse(restored.get('identity_verified', False))

    def test_high_confidence_delete_scan_does_not_click_any_known_cards(self):
        ctx = context()
        candidates = [card('strong-r', 'R'), {**card('strong-ssr', 'SSR'), 'box': [228, 635, 116, 116]}]
        with patch('custom.removal.read_selected_card') as reader:
            found = ProduceShoppingAuto()._probe_removal_candidates(ctx, candidates, set(), set(), set(), change=False)
        self.assertEqual(2, len(found))
        self.assertEqual('strong-r', ProduceShoppingAuto._choose_delete_candidate(found, set(), set())['name'])
        self.assertTrue(all(not item.get('identity_verified', False) for item in found))
        ctx.tasker.controller.post_click.assert_not_called()
        reader.assert_not_called()

    def test_subthreshold_delete_card_still_gets_detail_probe(self):
        ctx = context()
        exact = {**card(confidence=1.), 'identity_verified': True}
        with patch('custom.action.produce.time.sleep'), \
             patch('custom.action.produce._detect_disconnect_screen', return_value=False), \
             patch('custom.removal.selected_card_box_matches', return_value=True), \
             patch('custom.removal.read_selected_card', return_value=exact) as reader:
            found = ProduceShoppingAuto()._probe_removal_candidates(ctx, [card(confidence=.799)], set(), set(), set(), change=False)
        self.assertTrue(found[0]['identity_verified'])
        self.assertEqual(2, reader.call_count)
        ctx.tasker.controller.post_click.assert_called_once_with(140, 693)

    def test_final_delete_reads_exact_title_at_point_eight_and_rejects_other_names(self):
        ctx, shop = context(), ProduceShoppingAuto()
        selected = card()
        for names, score, expected in ((['思考の基本+'], .80, True), (['思考の基本+'], .799, False),
                                       (['思考の基'], .99, False), (['意地'], .99, False),
                                       (['思考の基本+', '意地'], .99, False)):
            rows = [SimpleNamespace(text=name, score=score) for name in names]
            with self.subTest(names=names, score=score), \
                 patch('custom.removal.selected_card_box_matches', return_value=True), \
                 patch('custom.shop.ocr', return_value=rows) as reader:
                found = shop._verify_delete_selection(ctx, None, selected, set(), set(), set())
            self.assertEqual(expected, found is not None)
            reader.assert_called_once_with(ctx, None, [160, 100, 520, 290], ['.*'])

    def test_final_delete_requires_selection_corners_even_with_trusted_art(self):
        with patch('custom.removal.selected_card_box_matches', return_value=False), patch('custom.shop.ocr') as reader:
            found = ProduceShoppingAuto()._verify_delete_selection(context(), None, card(), set(), set(), set())
        self.assertIsNone(found)
        reader.assert_not_called()

    def test_delete_page_waits_for_stability_before_identity_evaluation(self):
        ctx, action = context(), ProduceHIFChangeSourceAuto()
        boxes, candidates, events = [[82, 635, 116, 116]], [card()], []
        def stable(previous, signature):
            events.append('stable')
            return len(events) == 2
        def evaluate(*args, **kwargs):
            events.append('evaluate')
            self.assertEqual(boxes, kwargs['verified_boxes'])
            return candidates
        with patch('custom.action.produce.time.sleep'), \
             patch('custom.action.produce._detect_disconnect_screen', return_value=False), \
             patch.object(ProduceShoppingAuto, '_delete_card_boxes', return_value=boxes) as detector, \
             patch.object(ProduceShoppingAuto, '_recognize_delete_cards', side_effect=evaluate) as evaluator, \
             patch.object(action, '_source_frames_stable', side_effect=stable):
            result = action._page(ctx, change=False)
        self.assertEqual(['stable', 'stable', 'evaluate'], events)
        self.assertEqual(2, detector.call_count)
        evaluator.assert_called_once()
        self.assertEqual(candidates, result[1])
        for call in ctx.run_recognition.call_args_list:
            if call.args[0] == 'ProduceChooseDeleteFlag':
                self.assertEqual({}, call.kwargs['pipeline_override']['ProduceChooseDeleteFlag']['focus'])

    def test_unstable_delete_page_never_evaluates_card_identities(self):
        ctx, action = context(), ProduceHIFChangeSourceAuto()
        with patch('custom.action.produce.time.sleep'), \
             patch('custom.action.produce._detect_disconnect_screen', return_value=False), \
             patch.object(ProduceShoppingAuto, '_delete_card_boxes', return_value=[[82, 635, 116, 116]]), \
             patch.object(ProduceShoppingAuto, '_recognize_delete_cards') as evaluator, \
             patch.object(ProduceChooseRewardAuto, '_evaluate') as reward_evaluator, \
             patch.object(action, '_source_frames_stable', return_value=False):
            self.assertIsNone(action._page(ctx, change=False))
        evaluator.assert_not_called()
        reward_evaluator.assert_not_called()
        ctx.tasker.controller.post_click.assert_not_called()

    def test_deletion_resolves_selected_square(self):
        ctx = context()
        image = ctx.tasker.controller.post_screencap().wait().get()
        boxes, candidates = [[82, 635, 116, 116]], [card()]
        with patch.object(ProduceShoppingAuto, '_delete_card_boxes') as detector, \
             patch.object(ProduceChooseRewardAuto, '_evaluate', return_value=candidates) as evaluator, \
             patch('custom.square_identity.resolve_selected', return_value=candidates) as resolve:
            result = ProduceShoppingAuto()._recognize_delete_cards(ctx, image, verified_boxes=boxes)
        detector.assert_not_called()
        evaluator.assert_called_once_with(ctx, image, verified_boxes=boxes, read_extra_action=False, diagnostics=False)
        resolve.assert_called_once_with(ctx, image, candidates)
        self.assertEqual(candidates, result)

    def test_delete_detection_normalizes_all_raw_boxes_before_filtering_or_deduplicating(self):
        ctx = context()
        image = ctx.tasker.controller.post_screencap().wait().get()
        raw = [[368, 378, 132, 151], [80, 526, 132, 175],
               [82, 534, 116, 116], [520, 680, 116, 116]]
        ctx.run_recognition.return_value = SimpleNamespace(hit=True, all_results=[
            SimpleNamespace(label=label, box=box, score=.9)
            for label, box in zip(('cards', 'suggestions', 'useless', 'cards'), raw)
        ] + [SimpleNamespace(label='other', box=[228, 534, 116, 116], score=.99)])
        # The first box is above the old y=380 cutoff and taller than 145;
        # the second covers the third. Both need geometry repair before
        # old dimension filters or IoU deduplication could discard them.
        repaired = [[82 + column * 146, 388 + line * 146, 116, 116]
                    for line in range(5) for column in range(4)][:18]
        with patch('custom.deck_scan.normalize_deck_boxes', return_value=repaired) as normalize:
            result = ProduceShoppingAuto._delete_card_boxes(ctx, image)
        ctx.run_recognition.assert_called_once_with('ProduceRecognitionCards', image)
        normalize.assert_called_once_with(image, raw, top=380)
        self.assertIs(repaired, result)
        self.assertEqual(18, len(result))

    def test_silent_internal_recognition_never_emits_empty_focus_messages(self):
        # Empty strings still create timestamp-only UI rows. Silence is an
        # empty focus object, which MaaFramework replaces rather than merges.
        for filename in ('agent/custom/action/produce.py', 'agent/custom/reco/hif.py'):
            tree = ast.parse((ROOT / filename).read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Dict):
                    continue
                for key, value in zip(node.keys, node.values):
                    if not isinstance(key, ast.Constant) or key.value != 'focus' or not isinstance(value, ast.Dict):
                        continue
                    for event in value.values:
                        with self.subTest(filename=filename, line=event.lineno):
                            self.assertFalse(isinstance(event, ast.Constant) and event.value == '')


if __name__ == '__main__':
    unittest.main()

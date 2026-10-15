"""Current square-card recognition guards; no controller or live game input."""
import sys
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent'))
from custom import square_identity as square
from custom.deck_scan import scan_identity_known


def observation(**changes):
    return dict(dict(box=[82, 390, 118, 118], name='test', name_key='test', rarity='SR',
                     name_confidence=.9, name_score=.6, name_margin=.2,
                     frame_rarity='SR', rarity_confidence=.8, square_art_attempted=True,
                     square_art_trusted=True, square_art_score=.6, square_art_margin=.2), **changes)


class SquareIdentityTest(unittest.TestCase):
    def tearDown(self):
        square.gallery.cache_clear()

    def test_blank_noise_and_partial_boxes_are_rejected(self):
        for image in (np.zeros((118,118,3), np.uint8), np.full((118,118,3), 160, np.uint8),
                      np.random.default_rng(17).integers(0,256,(118,118,3),dtype=np.uint8)):
            self.assertIsNone(square.match(image, [0,0,118,118]))
        self.assertIsNone(square.match(image, [0,0,118,60]))
        self.assertIsNone(square.match(image, [-1,0,118,118]))

    def test_accepted_art_allows_ranking_not_submission(self):
        result = dict(name='test', rarity='SR', extra_action=False, score=.6, margin=.2)
        with patch.object(square, 'match', return_value=result):
            visual = square.recognize(None, [0,0,118,118], SimpleNamespace(rarity='SR', confidence=.8))
        item = observation(**square.evidence(visual))
        self.assertTrue(scan_identity_known(item))
        self.assertFalse(item.get('identity_verified', False))
        self.assertFalse(item.get('detail_selected', False))

    def test_frame_conflict_requires_detail(self):
        with patch.object(square, 'match', return_value=dict(name='test', rarity='SR', extra_action=False, score=.9, margin=.4)):
            self.assertIsNone(square.recognize(None, [0,0,118,118], SimpleNamespace(rarity='N', confidence=.8)))

    def test_two_regions_must_agree_and_pass_their_own_gates(self):
        model = (np.asarray(['a','b']), np.asarray(['N','SR']), np.asarray([False,True]),
                 (np.eye(2), np.eye(2)))
        for first, second, expected in (([.9,.1],[.8,.2],'a'),
                                        ([.9,.1],[.1,.9],None),
                                        ([.9,.1],[.55,.1],None),
                                        ([.9,.1],[.8,.7],None)):
            with self.subTest(first=first, second=second), \
                 patch.object(square, '_queries', side_effect=[np.asarray(first)[:,None], np.asarray(second)[:,None]]):
                result = square._match(None, [0,0,118,118], model, square._TRANSFORMS)
            self.assertEqual(expected, result['name'] if result else None)

    def test_rejected_square_cannot_fall_back_to_generic_scores(self):
        item = observation(square_art_trusted=False, name_score=1., name_margin=1.)
        self.assertFalse(scan_identity_known(item))
        item['identity_verified'] = True
        self.assertTrue(scan_identity_known(item))

    def test_score_margin_conflict_and_probe_guards(self):
        self.assertTrue(scan_identity_known(observation(square_art_score=.56, square_art_margin=.12)))
        for change in (dict(square_art_score=.559), dict(square_art_margin=.119),
                       dict(square_art_score=float('nan')), dict(name_key=''),
                       dict(identity_conflict=True), dict(probe_failed=True), dict(rarity='unknown')):
            with self.subTest(change=change):
                self.assertFalse(scan_identity_known(observation(**change)))

    def test_new_evaluation_does_not_reuse_previous_frame_evidence(self):
        from custom.action.produce import ProduceChooseRewardAuto
        frame = SimpleNamespace(rarity='SR', confidence=.8, best_distance=.1, margin=.2, signature=[.5]*3)
        image = np.zeros((1280,720,3), np.uint8)
        visual = SimpleNamespace(name='test', rarity='SR', extra_action=False, confidence=.9, score=.6, margin=.2)
        action = ProduceChooseRewardAuto()
        with patch('custom.action.produce.classify_rarity', return_value=frame), \
             patch.object(square, 'recognize', side_effect=[visual, None]):
            kwargs = dict(verified_boxes=[[82,390,118,118]], read_extra_action=False, diagnostics=False)
            first = action._evaluate(None, image, **kwargs)[0]
            second = action._evaluate(None, image, **kwargs)[0]
        self.assertTrue(scan_identity_known(first))
        self.assertFalse(scan_identity_known(second))
        self.assertFalse(second.get('square_art_trusted', False))
        self.assertEqual('', second['name_key'])

    def test_exact_identity_does_not_require_another_detail_probe(self):
        item = observation(identity_verified=True)
        with patch('custom.removal.selected_card_box_matches') as selected, \
             patch('custom.removal.read_selected_card') as reader:
            self.assertEqual([item], square.resolve_selected(None, None, [item]))
        selected.assert_not_called()
        reader.assert_not_called()

    def test_selected_rejected_art_can_be_resolved_without_clicking(self):
        item = observation(square_art_trusted=False)
        ctx = Mock()
        exact = dict(name='exact', name_key='exact', rarity='N', identity_verified=True)
        with patch('custom.removal.selected_card_box_matches', return_value=True), \
             patch('custom.removal.read_selected_card', return_value=exact):
            square.resolve_selected(ctx, None, [item])
        self.assertTrue(scan_identity_known(item))
        self.assertEqual('exact', item['name_key'])
        ctx.tasker.controller.post_click.assert_not_called()

    def test_corrupt_gallery_falls_back_and_warns_once(self):
        square.gallery.cache_clear()
        with patch.object(square.np, 'load', side_effect=ValueError('corrupt')) as loader, \
             patch.object(square.logger, 'warning') as warning:
            self.assertIsNone(square.gallery())
            self.assertIsNone(square.gallery())
        loader.assert_called_once()
        warning.assert_called_once()

    def test_malformed_gallery_falls_back(self):
        valid = dict(templates=np.zeros((2,32,32,3),np.uint8), names=np.asarray(['a','b']),
                     rarities=np.asarray(['N','SR']), extra=np.asarray([False,True]))
        for change in (dict(templates=np.zeros((2,64,64,3),np.uint8)),
                       dict(templates=np.zeros((2,32,32,3),np.float32)),
                       dict(names=np.asarray(['a'])), dict(rarities=np.asarray(['N','unknown'])),
                       dict(extra=np.asarray([False]))):
            with self.subTest(change=tuple(change)):
                square.gallery.cache_clear()
                buffer = BytesIO()
                np.savez(buffer, **{**valid, **change})
                buffer.seek(0)
                archive = np.load(buffer, allow_pickle=False)
                with patch.object(square.np, 'load', return_value=archive), patch.object(square.logger, 'warning'):
                    self.assertIsNone(square.gallery())

    def test_bundled_gallery_has_consistent_identity_dimensions(self):
        loaded = square.gallery()
        self.assertIsNotNone(loaded)
        names, rarities, extra, vectors = loaded
        self.assertGreater(len(names), 400)
        self.assertEqual(len(names), len(rarities))
        self.assertEqual(len(names), len(extra))
        for reference in vectors:
            self.assertEqual(len(names), len(reference))
            self.assertTrue(np.isfinite(reference).all())

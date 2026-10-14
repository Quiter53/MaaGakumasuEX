"""Independent regressions for contradictory deck-art evidence and fallback."""
import sys
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent'))
from custom.deck_art import gallery, improve_identities
from custom.deck_scan import scan_identity_known


def strong(name='old'):
    return dict(box=[80, 640, 118, 118], name=name, name_key=name, rarity='SR',
                name_confidence=.96, name_score=.50, name_margin=.20,
                frame_rarity='SR', rarity_confidence=.80)


def art(name='new'):
    return dict(name=name, rarity='SR', score=.60, margin=.20)


class DeckArtAuditTest(unittest.TestCase):
    def tearDown(self):
        gallery.cache_clear()

    def test_old_and_new_high_confidence_conflict_requires_detail(self):
        item = strong()
        self.assertTrue(scan_identity_known(item))
        with patch('custom.deck_art.match', return_value=art()):
            improve_identities(None, [item])
        self.assertTrue(item.get('identity_conflict'))
        self.assertFalse(scan_identity_known(item))
        # Exact detail, not either visual score, can resolve the conflict.
        item.update(identity_verified=True, name='exact', name_key='exact')
        self.assertTrue(scan_identity_known(item))

    def test_disagreeing_tall_and_square_crops_are_not_promoted(self):
        item = strong()
        item['box'][3] = 128
        with patch('custom.deck_art.match', side_effect=[art('old'), art('other')]):
            improve_identities(None, [item])
        self.assertTrue(item.get('identity_conflict'))
        self.assertFalse(scan_identity_known(item))
        self.assertFalse(item.get('deck_art_trusted', False))

    def test_previous_frame_trust_does_not_survive_failed_match(self):
        item = dict(strong('new'), name_score=0, name_margin=0,
                    deck_art_trusted=True, deck_art_score=.60, deck_art_margin=.20)
        self.assertTrue(scan_identity_known(item))
        with patch('custom.deck_art.match', return_value=None):
            improve_identities(None, [item])
        self.assertFalse(scan_identity_known(item))

    def test_exact_identity_is_not_replaced_by_new_art(self):
        item = dict(strong('exact'), identity_verified=True)
        with patch('custom.deck_art.match', return_value=art('other')) as matcher:
            improve_identities(None, [item])
        matcher.assert_not_called()
        self.assertEqual('exact', item['name_key'])
        self.assertTrue(scan_identity_known(item))

    def test_optional_corrupt_gallery_falls_back_and_warns_once(self):
        gallery.cache_clear()
        with patch('custom.deck_art.np.load', side_effect=ValueError('corrupt file')) as loader, \
             patch('custom.deck_art.Path.exists', return_value=True), \
             patch('custom.deck_art.logger.warning') as warning:
            self.assertIsNone(gallery())
            self.assertIsNone(gallery())
        loader.assert_called_once()
        warning.assert_called_once()

    def test_non_archive_gallery_is_rejected(self):
        gallery.cache_clear()
        with patch('custom.deck_art.np.load', return_value=np.zeros((2,64,64,3))), \
             patch('custom.deck_art.Path.exists', return_value=True), \
             patch('custom.deck_art.logger.warning'):
            self.assertIsNone(gallery())

    def test_malformed_gallery_arrays_fall_back(self):
        valid = dict(images=np.zeros((2,64,64,3), np.uint8),
                     names=np.asarray(['a', 'b']), rarities=np.asarray(['N', 'SR']))
        malformed = (
            dict(images=np.zeros((2,32,32,3), np.uint8)),
            dict(images=np.zeros((2,64,64,3), np.float32)),
            dict(names=np.asarray(['a'])),
            dict(rarities=np.asarray(['N', 'unknown'])),
        )
        for overrides in malformed:
            with self.subTest(overrides=tuple(overrides)):
                gallery.cache_clear()
                buffer = BytesIO()
                np.savez(buffer, **{**valid, **overrides})
                buffer.seek(0)
                archive = np.load(buffer, allow_pickle=False)
                with patch('custom.deck_art.np.load', return_value=archive), \
                     patch('custom.deck_art.Path.exists', return_value=True), \
                     patch('custom.deck_art.logger.warning'):
                    self.assertIsNone(gallery())

    def test_bundled_gallery_has_consistent_identity_dimensions(self):
        gallery.cache_clear()
        loaded = gallery()
        self.assertIsNotNone(loaded)
        names, rarities, vectors = loaded
        self.assertEqual(len(names), len(rarities))
        self.assertGreater(len(names), 400)
        for reference in vectors:
            self.assertEqual(len(names), len(reference))
            self.assertTrue(np.isfinite(reference).all())


if __name__ == '__main__':
    unittest.main()

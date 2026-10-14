import sys
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent'))
from custom.deck_art import match, improve_identities
from custom.deck_scan import scan_identity_known


class DeckArtTest(unittest.TestCase):
    def test_blank_and_noise_rejected(self):
        for image in (np.zeros((118,118,3), np.uint8), np.full((118,118,3), 160, np.uint8),
                      np.random.default_rng(17).integers(0,256,(118,118,3),dtype=np.uint8)):
            self.assertIsNone(match(image, [0,0,118,118]))

    def test_partial_box_rejected(self):
        self.assertIsNone(match(np.zeros((118,118,3), np.uint8), [0,0,118,60]))

    def test_matching_frame_allows_ranking_not_submission(self):
        item = dict(box=[0,0,118,118], frame_rarity='SR', rarity_confidence=.8)
        with patch('custom.deck_art.match', return_value=dict(name='test', rarity='SR', score=.6, margin=.2)):
            improve_identities(None, [item])
        self.assertTrue(scan_identity_known(item))
        self.assertFalse(item.get('identity_verified', False))
        self.assertFalse(item.get('detail_selected', False))

    def test_conflicts_do_not_promote(self):
        for extra in (dict(frame_rarity='N'), dict(name_key='other', name_confidence=.99)):
            item = dict(box=[0,0,118,118], frame_rarity='SR', rarity_confidence=.8)
            item.update(extra)
            with patch('custom.deck_art.match', return_value=dict(name='test', rarity='SR', score=.6, margin=.2)):
                improve_identities(None, [item])
            self.assertFalse(item.get('deck_art_trusted', False))

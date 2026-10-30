"""Real saved synthetic failures: a thumbnail cannot establish card identity."""
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent'))
from custom.hand_identity import HandIdentityReader, independent_artwork_range

FIXTURES = Path(__file__).with_name('fixtures')


@pytest.mark.parametrize('index', range(20))
def test_saved_flat_badge_failure_is_rejected_without_weakening_scores(index):
    with np.load(FIXTURES / 'hand_flat_badge_cases.npz') as data:
        crop = data['crops'][index]
    image = np.zeros((1280, 720, 3), np.uint8)
    image[884:884+crop.shape[0], 48:48+crop.shape[1]] = crop[:, :, ::-1]
    box = [48, 884, 188, 248]
    result = HandIdentityReader().read(image, box, [box])
    assert result is not None and result.name is None
    assert result.candidate == '夢はまだ続く'
    assert result.score >= .4 and result.margin >= .09  # old gate accepted these
    assert result.rejection_reason == 'flat_artwork'


@pytest.mark.parametrize('mode', ['full', 'local'])
def test_flat_input_cannot_be_rescued_by_either_gate(mode):
    reader = HandIdentityReader()
    reader.gates = {mode: {'score': .4, 'margin': .09}, 'badge_'+mode: {'score': .4, 'margin': .12}}
    box = [48, 884, 188, 248]
    boxes = [box] if mode == 'full' else [box, [170, 884, 188, 248]]
    image = np.full((1280, 720, 3), (30, 70, 110), np.uint8)
    for ranks in [[('wrong', .8, .3)], [('wrong', .43, .07), ('wrong', .75, .14)]]:
        reader.cache.clear()
        with patch.object(reader, 'rank', side_effect=ranks), patch('custom.hand_identity.badge_bounds', return_value=(132, 48, 56, 59)):
            result = reader.read(image, box, boxes)
        assert result.name is None and result.rejection_reason == 'flat_artwork'
        assert not result.badge_recovered
        assert reader.read(image, box, boxes) == result  # cached rejection stays rejected


def test_contrast_is_spatial_not_difference_between_rgb_channels():
    target = np.full((32, 32, 3), (.1, .4, .9))
    mask = np.ones((32, 32), bool)
    target[:, 20:] = np.random.default_rng(71).random((32, 12, 3))
    assert independent_artwork_range(target, mask) == 0
    target[12, 12, 0] += .02
    assert independent_artwork_range(target, mask) > 1 / 255
    mask[12, 12] = False
    assert independent_artwork_range(target, mask) == 0


def test_small_real_contrast_keeps_the_original_result():
    reader = HandIdentityReader()
    image = np.full((1280, 720, 3), 100, np.uint8)
    image[950:990, 100:140] = 104
    box = [48, 884, 188, 248]
    with patch.object(reader, 'rank', return_value=('accepted', .8, .3)) as rank:
        result = reader.read(image, box, [box])
    assert result.name == 'accepted' and result.rejection_reason is None
    assert result.score == .8 and result.margin == .3 and rank.call_count == 1

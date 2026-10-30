"""Badge recovery must add evidence without weakening ordinary-card decisions."""
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageDraw
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent'))
from custom.hand_badge import badge_bounds, exclude_badge
from custom.hand_identity import HandIdentityReader, visible_mask


def artwork():
    image = Image.new('RGB', (189, 215), (60, 170, 205))
    draw = ImageDraw.Draw(image)
    # Keep independent card texture: a thumbnail on flat colour is now unknown.
    for x in range(40, 110):
        draw.line((x, 70, x, 145), fill=(60 + x, 170, 205))
    draw.rounded_rectangle((132, 48, 187, 106), radius=10, fill=(255, 255, 255))
    draw.rounded_rectangle((135, 51, 184, 103), radius=7, fill=(235, 60, 100))
    draw.rectangle((141, 60, 178, 95), fill=(70, 80, 125))
    return np.asarray(image)


def frame():
    image = np.zeros((1280, 720, 3), np.uint8)
    image[884:1099, 48:237] = artwork()[:, :, ::-1]
    return image


def test_thumbnail_rim_is_detected_when_full_or_partly_occluded():
    for right in (189, 164):
        bounds = badge_bounds(artwork(), right)
        assert bounds is not None
        x, y, w, h = bounds
        assert abs(x-132) <= 5 and abs(y-48) <= 5
        mask = exclude_badge(visible_mask([48,884,189,249], [[48,884,189,249]]), bounds, artwork().shape)
        assert not mask[8:16, 23:30].any()
    assert badge_bounds(artwork(), 123) is None


def test_stacked_thumbnails_exclude_the_confirmed_continuation():
    image = Image.fromarray(artwork())
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((132, 78, 187, 136), radius=10, fill=(255,255,255))
    draw.rounded_rectangle((135, 81, 184, 133), radius=7, fill=(235,60,100))
    draw.rectangle((141,90,178,125), fill=(70,80,125))
    bounds = badge_bounds(np.asarray(image),189)
    assert bounds is not None and bounds[1] + bounds[3] >= 132


@pytest.mark.parametrize('color', [(255,255,255), (255,204,210), (20,20,20), (235,60,100)])
def test_plain_or_pink_artwork_is_not_a_thumbnail_border(color):
    assert badge_bounds(np.full((215,189,3), color, np.uint8), 189) is None


def reader():
    result = HandIdentityReader()
    result.gates = {'full': {'score': .4, 'margin': .09}, 'badge_full': {'score': .4, 'margin': .12}}
    return result


def test_unconfirmed_name_can_be_recovered_with_matching_independent_evidence():
    matcher = reader()
    with patch.object(matcher, 'rank', side_effect=[('pose',.43,.07),('pose',.75,.14)]):
        result = matcher.read(frame(), [48,884,189,249], [[48,884,189,249]])
    assert result.name == 'pose' and result.badge_recovered
    assert result.mode == 'full'


@pytest.mark.parametrize('clean', [('other',.95,.5), ('pose',.399,.5), ('pose',.95,.119)])
def test_disagreement_or_weak_remaining_art_stays_unknown(clean):
    matcher = reader()
    with patch.object(matcher, 'rank', side_effect=[('pose',.43,.07),clean]):
        result = matcher.read(frame(), [48,884,189,249], [[48,884,189,249]])
    assert result.name is None and not result.badge_recovered


def test_existing_accepted_cards_keep_their_original_decision():
    matcher = reader()
    with patch.object(matcher, 'rank', return_value=('accepted',.8,.3)) as rank:
        result = matcher.read(frame(), [48,884,189,249], [[48,884,189,249]])
    assert result.name == 'accepted' and not result.badge_recovered
    assert (result.score, result.margin) == (.8,.3)
    assert rank.call_count == 1


def test_recovery_requires_a_visible_rim_and_configured_gate():
    for image, gates in [(np.zeros((1280,720,3),np.uint8), reader().gates),
                         (frame(), {'full': {'score': .4, 'margin': .09}})]:
        matcher = reader()
        matcher.gates = gates
        with patch.object(matcher, 'rank', return_value=('pose',.43,.07)) as rank:
            result = matcher.read(image, [48,884,189,249], [[48,884,189,249]])
        assert result.name is None
        assert rank.call_count == 1

"""Cup alignment regression using the shipped atlas, with no private frames."""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent'))
from custom import drink_visual as visual
from custom.drink_overflow import read_view, handle_overflow, RetryOverflow
from utils.log_presentation import concise_message


def cup_frame(key='678359', size=73, dx=-1, dy=1):
    with np.load(Path(visual.__file__).with_name('drink_fingerprints.npz')) as atlas:
        template = atlas['templates'][list(atlas['ids']).index(key)]
    ys, xs = np.mgrid[0:160, 0:160]
    sx = np.clip((xs-35-dx)/size*39, 0, 38.999)
    sy = np.clip((ys-35-dy)/size*39, 0, 38.999)
    ix, iy = sx.astype(int), sy.astype(int)
    ax, ay = (sx-ix)[..., None], (sy-iy)[..., None]
    image = (template[iy, ix]*(1-ax)*(1-ay) + template[iy, ix+1]*ax*(1-ay)
             + template[iy+1, ix]*(1-ax)*ay + template[iy+1, ix+1]*ax*ay)
    return image.astype(np.uint8)[:, :, ::-1].copy()


def overflow_frame(with_cup=True):
    frame = np.zeros((1280, 720, 3), np.uint8)
    if with_cup:
        frame[474:634, 25:185] = cup_frame()
    frame[539:565, 617:643] = [206, 203, 198]
    return frame


def test_subpixel_cup_peak_recovers_without_lowering_gates():
    frame = cup_frame()
    assert visual.match(frame, [35, 35, 86, 86]) is None
    result = visual.match(frame, [35, 35, 86, 86], refine_ambiguous=True)
    assert result['id'] == '678359'
    assert result['score'] >= .78 and result['margin'] >= .14


@pytest.mark.parametrize('key', list(visual.CATALOG))
def test_fine_alignment_never_substitutes_another_catalog_bottle(key):
    result = visual.match(cup_frame(key), [35, 35, 86, 86], refine_ambiguous=True)
    assert result is None or result['id'] == key


def test_competing_identical_art_remains_unknown():
    ids, references = visual.gallery()
    coffee = references[list(ids).index('678359')]
    with patch.object(visual, 'gallery', return_value=(np.array(['678359', '739375']), np.stack([coffee, coffee]))):
        assert visual.match(cup_frame(), [35, 35, 86, 86], refine_ambiguous=True) is None


def test_blank_art_is_not_accepted():
    assert visual.match(np.zeros((160, 160, 3), np.uint8), [35, 35, 86, 86], refine_ambiguous=True) is None


def test_overflow_reader_uses_refinement_and_preserves_checkbox():
    view = read_view(overflow_frame())
    assert len(view.rows) == 1
    assert view.rows[0].id == '678359'
    assert view.rows[0].y == 552
    assert view.rows[0].selected is False


def test_unidentified_art_cannot_trigger_selection_or_submit():
    controller = Mock()
    controller.post_screencap.return_value.wait.return_value.get.return_value = overflow_frame(False)
    context = SimpleNamespace(tasker=SimpleNamespace(stopping=False, controller=controller),
                              run_recognition=Mock(return_value=SimpleNamespace(hit=True)))
    with patch('custom.drink_overflow.time.sleep'):
        assert handle_overflow(context) is True
    controller.post_click.assert_not_called()


def test_failed_identity_reason_is_visible_in_concise_logs():
    with pytest.raises(RetryOverflow) as error:
        read_view(overflow_frame(False))
    message = '饮料保留：' + str(error.value)
    assert '饮料图标未核实' in concise_message(message, 'WARNING')

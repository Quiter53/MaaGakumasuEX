"""HUD parsing and battle-session continuity after a transient health-bar miss."""
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import numpy as np
import pytest

from custom.shop import read_interval_health, read_shop_points
from custom.produce.battle import ProduceCardsAuto


def hud():
    image = np.zeros((1280, 720, 3), np.uint8)
    image[56:80, 293:324] = [20, 240, 80]
    return image


@pytest.mark.parametrize('digits,expected', [('33/33', (33, 33)), ('9/33', (9, 33)), ('0/33', (0, 33))])
def test_health_reads_only_digits_to_right_of_heart(digits, expected):
    ctx = Mock()
    def recognize(name, image, pipeline_override):
        node = pipeline_override[name]
        x, y, w, h = node['roi']
        assert [x, y, w, h] == [328, 48, 122, 42]
        assert node['only_rec'] is True
        return NS(hit=True, filtered_results=[NS(text=digits, score=.99)])
    ctx.run_recognition.side_effect = recognize
    assert read_interval_health(ctx, hud()) == expected
    assert ctx.run_recognition.call_count == 1


@pytest.mark.parametrize('score', [.882372, .896866, .878153])
def test_logged_low_confidence_heart_text_is_not_accepted(score):
    ctx = Mock()
    ctx.run_recognition.return_value = NS(hit=True, filtered_results=[NS(text='❤33/33', score=score)])
    assert read_interval_health(ctx, hud()) is None


@pytest.mark.parametrize('text', ['34/33', '33/0', '488', '回復33/33'])
def test_isolated_digits_still_require_valid_unambiguous_fraction(text):
    ctx = Mock()
    ctx.run_recognition.return_value = NS(hit=True, filtered_results=[NS(text=text, score=.99)])
    assert read_interval_health(ctx, hud()) is None


def test_fixed_health_roi_does_not_depend_on_heart_color():
    ctx = Mock()
    ctx.run_recognition.return_value = NS(hit=True, filtered_results=[NS(text='33/33', score=.99)])
    assert read_interval_health(ctx, np.zeros((1280, 720, 3), np.uint8)) == (33, 33)
    assert ctx.run_recognition.call_count == 1


def test_conflicting_numbers_are_not_used_for_healing():
    ctx = Mock()
    ctx.run_recognition.return_value = NS(hit=True, filtered_results=[
        NS(text='20/33', score=.99), NS(text='33/33', score=.99)])
    assert read_interval_health(ctx, hud()) is None


@pytest.mark.parametrize('text,expected', [
    ('0', 0), ('9', 9), ('19', 19), ('488', 488), ('1000', 1000),
    ('1,000', 1000), ('１２３４', 1234), ('１，２３４', 1234),
    ('+19', None), ('P19', None), ('1,00', None), ('1/9', None),
    ('19P', None), ('1 9', None), ('', None),
])
def test_points_use_digit_only_line_and_strict_balance(text, expected):
    ctx = Mock()
    ctx.run_recognition.return_value = NS(hit=True, filtered_results=[NS(text=text, score=.99)])
    assert read_shop_points(ctx, hud()) == expected
    node = ctx.run_recognition.call_args.kwargs['pipeline_override']['ProduceRecognitionPoint']
    assert node['roi'] == [328, 94, 122, 42]
    assert node['only_rec'] is True
    assert ctx.run_recognition.call_count == 1


@pytest.mark.parametrize('reader', [read_interval_health, read_shop_points])
def test_hud_rejects_non_reference_frame_without_ocr(reader):
    ctx = Mock()
    assert reader(ctx, np.zeros((720, 1280, 3), np.uint8)) is None
    ctx.run_recognition.assert_not_called()


@pytest.mark.parametrize('rows', [
    [NS(text='19', score=.89)],
    [NS(text='19', score=.99), NS(text='119', score=.99)],
    [],
])
def test_points_reject_low_confidence_or_ambiguous_balance(rows):
    ctx = Mock()
    ctx.run_recognition.return_value = NS(hit=True, filtered_results=rows)
    assert read_shop_points(ctx, hud()) is None


@pytest.mark.parametrize('health,expected_captures', [([True], 1), ([False, True], 2), ([False, False], 2)])
def test_battle_recheck_is_bounded_and_returns_fresh_frame(health, expected_captures):
    action = ProduceCardsAuto()
    ctx = Mock()
    ctx.tasker.stopping = False
    frames = [hud(), hud()]
    ctx.tasker.controller.post_screencap.return_value.wait.return_value.get.side_effect = frames
    with patch('custom.produce.battle.time.sleep'), \
         patch('custom.produce.battle.shared._detect_disconnect_screen', return_value=None), \
         patch.object(action, '_is_playing_card', side_effect=health):
        result = action._recheck_battle_frame(ctx)
    assert ctx.tasker.controller.post_screencap.call_count == expected_captures
    assert result is (frames[expected_captures-1] if health[-1] else None)
    ctx.tasker.controller.post_click.assert_not_called()
    ctx.run_task.assert_not_called()


@pytest.mark.parametrize('stop', [False, True])
def test_recheck_respects_disconnect_and_stop(stop):
    action = ProduceCardsAuto()
    ctx = Mock()
    ctx.tasker.stopping = stop
    with patch('custom.produce.battle.time.sleep'), \
         patch('custom.produce.battle.shared._detect_disconnect_screen', return_value='ProduceDisconnectError'):
        assert action._recheck_battle_frame(ctx) is None
    assert action._disconnect_detected is (not stop)
    assert ctx.tasker.controller.post_screencap.call_count == int(not stop)
    ctx.tasker.controller.post_click.assert_not_called()


def test_recheck_stops_before_capture_if_cancelled_during_wait():
    action = ProduceCardsAuto()
    ctx = Mock()
    ctx.tasker.stopping = False
    def stop(_):
        ctx.tasker.stopping = True
    with patch('custom.produce.battle.time.sleep', side_effect=stop):
        assert action._recheck_battle_frame(ctx) is None
    ctx.tasker.controller.post_screencap.assert_not_called()


def test_main_loop_keeps_drink_and_national_state_after_health_recovers():
    action = ProduceCardsAuto()
    ctx = Mock()
    ctx.tasker.stopping = False
    frames = [hud() for _ in range(4)]
    ctx.tasker.controller.post_screencap.return_value.wait.return_value.get.side_effect = frames
    session = NS(combo_enabled=False)
    seen = []
    def drinks(context, frame):
        seen.append(frame)
        assert action._drinks is session
        if len(seen) == 1:
            action._combo.submitted('国民的アイドル')
            action._drinks.pending_card = 'configured-card'
        else:
            assert action._combo.after_national
            assert action._drinks.pending_card == 'configured-card'
            ctx.tasker.stopping = True
        return True
    with patch('custom.produce.battle.time.sleep'), \
         patch('custom.produce.battle.shared._detect_disconnect_screen', return_value=None), \
         patch('custom.produce.battle.BattleDrinkSession', return_value=session), \
         patch.object(action, '_rule_preset', return_value={'rules': []}), \
         patch.object(action, '_wait_until_playable', return_value=True), \
         patch.object(action, '_is_playing_card', side_effect=[True, False, True]), \
         patch.object(action, '_drink_step', side_effect=drinks), \
         patch('custom.produce.shop.ProduceShoppingAuto._reset_visit'):
        assert action.run(ctx, NS(custom_action_param={}))
    assert len(seen) == 2 and seen[1] is frames[3]

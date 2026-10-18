"""User delay must not bypass card submission or fresh-hand confirmation."""
import json
import re
import time
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import numpy as np
import pytest

from custom.produce.battle import ProduceCardsAuto

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('value', [0, '0', .5, '0.5', 1.5, '1.5', 5, '5'])
def test_accepted_delay(value):
    assert ProduceCardsAuto._configured_post_play_delay(json.dumps({'post_play_delay': value})) == float(value)


@pytest.mark.parametrize('raw', [None, '', 'broken', '[]', '{}', {'post_play_delay': None},
                                 {'post_play_delay': True}, {'post_play_delay': []},
                                 *({'post_play_delay': v} for v in [-1, 5.01, 'NaN', 'inf', 'oops'])])
def test_invalid_or_old_config_keeps_default(raw):
    assert ProduceCardsAuto._configured_post_play_delay(raw) == 1.5


def test_each_run_resets_delay_without_affecting_other_instance():
    action, other = ProduceCardsAuto(), ProduceCardsAuto()
    with patch.object(action, '_wait_until_playable', return_value=False):
        action.run(Mock(), NS(custom_action_param='{"post_play_delay":"0"}'))
        assert action._post_play_delay == 0
        assert other._post_play_delay == 1.5
        action.run(Mock(), NS(custom_action_param=None))
        assert action._post_play_delay == 1.5


@pytest.mark.parametrize('delay', [0, .5, 1.5, 5])
@pytest.mark.parametrize('outcome', ['ready', 'failed_submit', 'animation', 'stop', 'disconnect'])
def test_optional_wait_preserves_submission_and_stability_checks(delay, outcome):
    action = ProduceCardsAuto()
    action._post_play_delay = delay
    action.start_time = time.time()
    ctx = Mock()
    ctx.tasker.stopping = False
    frame = np.zeros((1280, 720, 3), np.uint8)
    box = [142, 820, 189, 249]
    ctx.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value = frame
    def stable(*args, **kwargs):
        if outcome == 'stop':
            ctx.tasker.stopping = True
        if outcome == 'disconnect':
            action._disconnect_detected = True
        return outcome != 'animation'
    with patch('custom.produce.battle.time.sleep') as sleep, \
         patch('custom.produce.shared._detect_disconnect_screen', return_value=False), \
         patch('custom.battle_drinks.CardNameReader.locate_raised', return_value=box), \
         patch('custom.battle_drinks.CardNameReader.same_selection', return_value=True), \
         patch.object(action, '_observe_selected_card', return_value=(frame, box, None)), \
         patch.object(action, '_confirm_card_submission', return_value=outcome != 'failed_submit') as submit, \
         patch.object(action, '_wait_until_playable', side_effect=stable) as wait:
        assert action._play_a_card(ctx, box)
        submit.assert_called_once()
        if outcome == 'failed_submit':
            wait.assert_not_called()
        else:
            wait.assert_called_once_with(ctx, require_hand_stable=True)
        if outcome == 'ready' and delay > 0:
            sleep.assert_called_once_with(delay)
        else:
            sleep.assert_not_called()


def test_task_option_references_localization_and_validation():
    read = lambda name: json.loads((ROOT / 'assets' / name).read_text('utf-8'))
    produce = read('tasks/produce.json')
    name = '出牌后额外等待'
    option = produce['option'][name]
    for document in [produce, read('tasks/produce_cn.json')]:
        assert name in document['task'][0]['option']
    idol = next(t for t in read('interface.json')['task'] if t['name'] == '偶像之路')
    assert name in idol['option']
    for lang in ['zh-CN', 'zh-Hant', 'en-US', 'ja-JP']:
        strings = read(f'lang/{lang}.json')
        for key in ['label']:
            assert strings[option[key][1:]]
        for key in ['label', 'description', 'pattern_msg']:
            assert strings[option['inputs'][0][key][1:]]
    expression = option['inputs'][0]['verify']
    for text in ['0', '0.5', '1.5', '4.99', '5.00']:
        assert re.fullmatch(expression, text)
    for text in ['-1', '5.01', 'NaN', '1.555', '', 'True']:
        assert not re.fullmatch(expression, text)
    assert set(option['pipeline_override']) == {'ProduceCardsFlag', 'IdolRoadAuto'}

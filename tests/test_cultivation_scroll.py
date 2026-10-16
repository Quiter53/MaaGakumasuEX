"""List gestures share braking, but each scene retains its own viewport."""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent'))
from custom.scroll import scroll_list
from custom.owned_deck import OwnedDeckReader
from custom.drink_overflow import OverflowPicker, RetryOverflow
from custom.produce.memory.modes import MemoryReader, MemoryTransition


def context():
    ctx = Mock()
    ctx.tasker.stopping = False
    ctx.run_action_direct.return_value = SimpleNamespace(success=True)
    return ctx


@pytest.mark.parametrize('kind,top', [('summary', 710), ('detail', 710), ('picker', 290)])
@pytest.mark.parametrize('up', [True, False])
def test_memory_scroll_stays_inside_original_content_pane(kind, top, up):
    ctx = context()
    reader = MemoryReader.__new__(MemoryReader)
    reader.context, reader.controller = ctx, ctx.tasker.controller
    with patch('time.sleep'):
        reader.scroll(up, kind)
    action, drag = ctx.run_action_direct.call_args.args
    assert action == 'Swipe' and drag.duration == [900] and drag.end_hold == [350]
    assert drag.begin[:2] == [600, top+350 if up else top+40]
    assert drag.end[0][:2] == [600, top+40 if up else top+350]
    assert abs(drag.begin[1]-drag.end[0][1]) == 310
    ctx.tasker.controller.post_swipe.assert_not_called()


@pytest.mark.parametrize('ys', [(405, 558, 711, 864), (480, 633, 786)])
def test_owned_deck_drag_keeps_full_overlap_on_partial_pages(ys):
    ctx = context(); reader = OwnedDeckReader(ctx)
    reader._scroll_rows = [[dict(box=[48+160*i, y, 144, 144]) for i in range(4)] for y in ys]
    with patch('time.sleep'):
        reader.scroll(True)
    _, drag = ctx.run_action_direct.call_args.args
    distance = drag.begin[1]-drag.end[0][1]
    assert 0 < distance <= 306
    assert any(400 <= y-distance and y-distance+144 <= 1010 for y in ys)
    assert drag.end_hold == [350]


@pytest.mark.parametrize('down', [True, False])
def test_drink_list_preserves_viewport_and_rechecks_after_drag(down):
    ctx = context(); reader = OverflowPicker(ctx)
    calls = Mock(); reader.capture = calls.capture; reader.stable = calls.stable
    calls.drag.return_value = SimpleNamespace(success=True)
    ctx.run_action_direct.side_effect = lambda *args: calls.drag()
    with patch('time.sleep'):
        reader.scroll(down)
    assert [call[0] for call in calls.mock_calls] == ['capture', 'drag', 'stable']
    _, drag = ctx.run_action_direct.call_args.args
    assert drag.begin[:2] == [650, 1000 if down else 500]
    assert drag.end[0][:2] == [650, 520 if down else 1000]
    assert drag.end_hold == [350]


@pytest.mark.parametrize('scenario', ['stopping', 'failure', 'missing', 'stopped_during_drag'])
def test_shared_scroll_does_not_claim_success_after_failure_or_stop(scenario):
    ctx = context()
    if scenario == 'stopping':
        ctx.tasker.stopping = True
    elif scenario == 'failure':
        ctx.run_action_direct.return_value.success = False
    elif scenario == 'missing':
        ctx.run_action_direct.return_value = None
    else:
        def stop(*args):
            ctx.tasker.stopping = True
            return SimpleNamespace(success=True)
        ctx.run_action_direct.side_effect = stop
    assert not scroll_list(ctx, (600, 1060), (600, 750))
    if scenario == 'stopping':
        ctx.run_action_direct.assert_not_called()


def test_failed_gesture_returns_each_scene_to_existing_recovery_path():
    ctx = context(); ctx.run_action_direct.return_value.success = False
    owned = OwnedDeckReader(ctx)
    drink = OverflowPicker(ctx); drink.capture = Mock(); drink.stable = Mock()
    memory = MemoryReader.__new__(MemoryReader); memory.context = ctx
    with pytest.raises(InterruptedError): owned.scroll(True)
    with pytest.raises(RetryOverflow): drink.scroll(True)
    drink.stable.assert_not_called()
    with pytest.raises(MemoryTransition): memory.scroll(True, 'summary')

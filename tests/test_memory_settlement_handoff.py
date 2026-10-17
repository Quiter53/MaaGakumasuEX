"""A committed memory must hand stamp/item overlays back to settlement."""
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest

from test_normal_memory_reroll import Settlement, CATALOG
from custom.produce.memory import modes as m
from custom.produce.memory.flow import MemoryFlow
from custom.produce.memory.actions import ProduceMemoryRecognition


@pytest.fixture
def session():
    state = m.Session()
    with patch.object(m, 'SESSION', state), patch.object(m, 'catalog', return_value=CATALOG):
        state.configure(dict(mode='card', reroll=True, targets=[dict(name='SR再動', slot=0, upgrade='any')]))
        yield state


@pytest.mark.parametrize('title', ['あさり先生シール獲得', 'アイテム獲得'])
def test_close_overlay_after_next_releases_memory_without_claiming_home(session, title):
    driver = Settlement([m.Card('R再動', False, 1)] * 2, [False, False])
    flow = MemoryFlow()
    flow.leaving = True
    session.flow = flow
    # Stamp title/close boxes are from 2026-10-17 20:09:32.873 real OCR.
    rows = [dict(text=title, box=[60, 238, 599, 71], score=.981513),
            dict(text='X', box=[313, 1141, 88, 87], score=.925543)]
    driver.capture = lambda: (driver.frame, rows)
    driver.context.run_recognition.side_effect = lambda node, image: NS(hit=node == 'CloseRoundButton')
    driver.run(flow)
    assert not flow.holding and not session.saved_result and not driver.clicks
    assert ProduceMemoryRecognition().analyze(driver.context, NS(image=driver.frame)) is None
    # Now the existing pipeline can reach CloseRoundButton instead of being
    # intercepted by ProduceMemoryFlag again. Home remains the save receipt.


@pytest.mark.parametrize('leaving,has_title,hit,release', [
    (False, False, True, False),
    (True, True, True, False),
    (True, False, False, False),
    (True, False, True, True),
])
def test_close_button_handoff_requires_submitted_next_and_non_memory_page(session, leaving, has_title, hit, release):
    driver = Settlement([m.Card('R再動', False, 1)] * 2, [False, False])
    rows = [dict(text='メモリー変換 プロデュース', box=[40, 430, 200, 40])] if has_title else []
    driver.capture = lambda: (driver.frame, rows)
    driver.context.run_recognition.side_effect = lambda node, image: NS(hit=hit and node == 'CloseRoundButton')
    flow = MemoryFlow()
    flow.leaving = leaving
    driver.run(flow)
    assert flow.holding is not release
    assert not session.saved_result and not driver.clicks


@pytest.mark.parametrize('mode', ['normal', 'card'])
def test_committed_candidate_summary_has_no_reroll_and_needs_no_missing_entry_warning(session, mode):
    if mode == 'normal': session.configure(dict(mode='normal', reroll=True))
    driver = Settlement([m.Card('R再動', False, 1)] * 2, [False, False])
    capture = driver.capture
    def actual_layout():
        image, rows = capture()
        if driver.stage == 'summary' and driver.chosen is not None:
            rows = [r for r in rows if r['text'] != '再生成']
        return image, rows
    driver.capture = actual_layout
    with patch('custom.produce.memory.flow.logger.warning') as warning:
        flow = driver.run()
    assert driver.spends == 1 and driver.reroll_requests == 1
    assert flow.finalized and driver.stage == 'home' and session.saved_result
    assert flow.missing_reroll == 0
    warning.assert_not_called()


def test_finalized_state_survives_yield_during_generation(session):
    driver = Settlement([m.Card('R再動', False, 1)] * 2, [False, False], stage='picker')
    original_click = driver.click
    def pause(reader, box):
        original_click(reader, box)
        if box[0] == 400: driver.clock += 40  # callback expires during reveal
    driver.click = pause
    flow = driver.run()
    assert flow.finalizing and not flow.finalized
    driver.click = original_click
    driver.run(flow)
    assert flow.finalized and driver.stage == 'home' and driver.reroll_requests == 0

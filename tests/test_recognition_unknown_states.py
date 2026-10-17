"""Missing observations must not silently become absence or completion."""
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import numpy as np
import pytest

from test_normal_memory_reroll import Settlement, CATALOG
from test_customization import target
from custom.produce.memory import modes as m
from custom.produce.memory.flow import MemoryFlow
from custom.produce.memory.actions import ProduceMemoryRecognition
from custom.produce.shop_customization import CustomizationSession
from custom.reco.connection import ProduceCancelDrinkDiscard


@pytest.fixture
def session():
    value = m.Session()
    value.configure(dict(mode='normal', reroll=True))
    with patch.object(m, 'SESSION', value), patch.object(m, 'catalog', return_value=CATALOG):
        yield value


def game():
    return Settlement([m.Card('R再動', False, 1)] * 2, [False, True])


def test_local_tab_and_footer_recover_missing_full_frame_controls(session):
    image = np.full((1280, 720, 3), 230, np.uint8)
    title = dict(text='メモリー変換', box=[44, 423, 130, 38])
    tab = dict(text='プロデュース', box=[165, 608, 120, 24])
    footer = [dict(text='再生成', box=[87, 1142, 82, 34]), dict(text='次へ', box=[324, 1138, 67, 42])]
    def read(ctx, frame, roi=None):
        if roi is None: return [title]  # Whole-frame detector missed both controls.
        if roi[1] == 580: return [tab]
        if roi[1] == 1060: return footer
        return [title]
    with patch.object(m, 'ocr', side_effect=read):
        texts = m.screen_ocr(Mock(), image)
    assert m.screen_kind(texts) == 'summary'
    assert m.button(texts, '再生成') == footer[0]['box']


def test_partial_panel_claims_page_instead_of_generic_next(session):
    image = np.full((1280, 720, 3), 230, np.uint8)
    title = [dict(text='メモリー変換', box=[44, 423, 130, 38])]
    with patch.object(m, 'ocr', return_value=title):
        assert ProduceMemoryRecognition().analyze(Mock(), NS(image=image)) is not None


def test_picker_controls_use_picker_layout_and_recover_candidate_marker(session):
    image = np.full((1280, 720, 3), 230, np.uint8)
    title = dict(text='獲得するメモリーを選択してください', box=[100, 60, 500, 35])
    marker = dict(text='再生成前', box=[310, 1000, 100, 35])
    decide = dict(text='決定', box=[320, 1140, 70, 35])
    def read(ctx, frame, roi=None):
        if roi is None: return [title]
        if roi[1] == 145: return [dict(text='プロデュース', box=[160, 170, 130, 35])]
        if roi[1] == 960: return [marker, decide]
        return [title] if roi[1] == 30 else []
    with patch.object(m, 'ocr', side_effect=read):
        texts = m.screen_ocr(Mock(), image)
    assert m.screen_kind(texts) == 'picker'
    assert m.candidate_marker(texts) == '再生成前'
    assert m.button(texts, '決定') == decide['box']


def test_arrival_at_home_commits_result_after_intervening_story(session):
    session.configure(dict(mode='card', targets=[dict(name='R再動', slot=0, upgrade='any')]))
    session.flow = MemoryFlow()
    session.flow.leaving = True
    session.flow.holding = False  # Released to a positively recognized story.
    session.result_seen = True
    session.pending_targets = {0}
    ctx = Mock()
    with patch('custom.produce.memory.farming.save_progress'):
        m.go_home(ctx)
    assert session.collected == {0} and session.matched


def test_home_without_leaving_does_not_commit_inspected_but_unsaved_result(session):
    session.configure(dict(mode='card', targets=[dict(name='R再動', slot=0, upgrade='any')]))
    session.flow = MemoryFlow()
    session.result_seen = True
    session.pending_targets = {0}
    with patch('custom.produce.memory.farming.save_progress') as save:
        m.go_home(Mock())
    assert not session.collected
    save.assert_not_called()


@pytest.mark.parametrize('missing', ['tab', 'footer', 'all'])
def test_transient_missing_page_fields_still_inspects_and_rerolls(session, missing):
    driver = game()
    capture = driver.capture
    calls = 0
    def fading():
        nonlocal calls
        calls += 1
        image, texts = capture()
        if calls <= 2:
            if missing == 'tab': texts = [driver.row('メモリー変換'), driver.row('次へ', 500)]
            elif missing == 'footer': texts = [driver.row('メモリー変換 プロデュース')]
            else: texts = []
        return image, texts
    driver.capture = fading
    driver.run()
    assert driver.spends == 1 and driver.stage == 'home'
    assert session.result_seen and session.saved_result


def test_empty_frames_do_not_prove_saved_and_guard_survives_callback(session):
    driver = game()
    driver.capture = lambda: (driver.frame, [])
    flow = MemoryFlow()
    flow.leaving = True
    session.flow = flow
    driver.run(flow)
    assert not session.saved_result and not driver.clicks
    assert flow.holding
    assert ProduceMemoryRecognition().analyze(driver.context, NS(image=driver.frame)) is not None
    driver.stage = 'home'
    driver.run(flow)
    assert session.saved_result and not flow.holding
    assert ProduceMemoryRecognition().analyze(driver.context, NS(image=driver.frame)) is None


def test_network_recovery_yields_without_losing_settlement(session):
    driver = game()
    driver.capture = lambda: (driver.frame, [])
    driver.context.run_recognition.side_effect = lambda node, image: NS(hit=node == 'ProduceDisconnectError')
    flow = driver.run()
    assert flow.holding and not session.saved_result and not driver.clicks


@pytest.mark.parametrize('permanent', [False, True])
def test_missing_reroll_rechecks_before_fallback_and_reports_unknown(session, permanent):
    driver = game()
    capture = driver.capture
    reads = 0
    def missing():
        nonlocal reads
        image, texts = capture()
        reads += 1
        if driver.stage == 'summary' and (permanent or reads <= 4):
            texts = [r for r in texts if r['text'] != '再生成']
        return image, texts
    driver.capture = missing
    with patch('custom.produce.memory.flow.logger.warning') as warning:
        flow = driver.run()
    assert driver.stage == 'home' and not flow.no_tickets
    if permanent:
        assert reads >= 5 and driver.spends == 0
        assert any('不再尝试使用再生成票' in call.args[0] for call in warning.call_args_list)
    else:
        assert driver.spends == 1
        warning.assert_not_called()


def test_discard_reuses_current_frame_title_but_still_verifies_cancel():
    ctx = Mock()
    ctx.tasker.stopping = False
    frame = np.zeros((1280, 720, 3), np.uint8)
    ctx.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value = frame
    title = NS(text='廃棄確認', score=.99, box=[50, 950, 150, 35])
    cancel = NS(text='いいえ', score=.99, box=[190, 1140, 94, 38])
    def modal_read(context, image, roi, expected):
        # The larger-region title miss used to veto the good narrow observation.
        return [cancel] if roi == [65, 1090, 300, 150] else []
    with patch('custom.reco.connection.ocr', return_value=[title]), \
         patch('custom.dialog_safety.ocr', side_effect=modal_read), patch('time.sleep'):
        ProduceCancelDrinkDiscard().run(ctx, None)
    ctx.tasker.controller.post_click.assert_called_once_with(237, 1159)


def test_discard_title_does_not_authorize_click_when_cancel_is_unreadable():
    ctx = Mock()
    ctx.tasker.stopping = False
    with patch('custom.reco.connection.ocr', return_value=[NS(text='廃棄確認', score=.99)]), \
         patch('custom.dialog_safety.ocr', return_value=[]):
        ProduceCancelDrinkDiscard().run(ctx, None)
    ctx.tasker.controller.post_click.assert_not_called()


@pytest.mark.parametrize('values', [[None, 1000], [None, None, None, 1000]])
def test_customization_recovers_points_before_reading_entry(values):
    ctx, shop, frame = Mock(), Mock(), object()
    shop._visit_state.return_value = {}
    shop._recognize_points.side_effect = values
    with patch('custom.customization.targets', return_value=[target()]):
        worker = CustomizationSession(ctx, shop)
    with patch.object(worker, 'capture', return_value=frame) as capture, \
         patch('custom.customization.entry', return_value=None) as entry, patch('time.sleep'):
        assert worker.run(frame) == (frame, None)
    assert not worker.finished
    assert capture.call_count == len(values)-1
    entry.assert_called_once_with(ctx, frame)


def test_customization_unknown_points_stay_retryable_and_real_low_balance_finishes():
    ctx, shop, frame = Mock(), Mock(), object()
    shop._visit_state.return_value = {}
    shop._recognize_points.return_value = None
    with patch('custom.customization.targets', return_value=[target()]):
        worker = CustomizationSession(ctx, shop)
    with patch.object(worker, 'capture', return_value=frame), patch('time.sleep'), \
         patch('custom.customization.entry') as entry:
        assert worker.run(frame) == (frame, None)
        assert not worker.finished
        assert shop._recognize_points.call_count == 4
        entry.assert_not_called()
        shop._recognize_points.return_value = 0
        worker.run(frame)
    assert worker.finished

"""State transitions from the 2026-10-20 脚光 incident."""
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import numpy as np
import pytest

from custom import customization as vision
from test_customization import menu, session, target
from test_customization_planning import ShopGame


def spotlight():
    rule = target('脚光', [0, 1])
    before = menu('脚光', remaining=(1, 1), total=1, prices=(40, 70))
    selected = deepcopy(before)
    selected['options'][1]['selected'] = True
    after = menu('脚光', remaining=(0, 0), total=0, prices=(None, None))
    for option in after['options']:
        option['capped'] = True
    return rule, before, selected, after


def test_real_single_target_planner_does_not_scan_or_reenter_after_purchase():
    game = ShopGame(465)
    rule, page, _, _ = spotlight()
    game.rules = [rule]; game.cards = {'脚光': page}
    assert game.run() == [('custom', '脚光', 2), ('drink',), ('exit',)]
    assert game.scans == [None, '脚光']
    assert game.points == 390


def test_plan_is_retained_across_refresh_instead_of_scanning_finished_targets():
    game = ShopGame(10000)
    game.run()
    assert game.scans == [None, '輝くキミへ', '仕切り直し', '幸せのおまじない']


def test_menu_progress_uses_known_balance_and_settles_only_on_clear_shop():
    obj, ctx, shop = session()
    rule, before, selected, after = spotlight()
    modal = np.zeros((1280, 720, 3), np.uint8)
    clear = np.ones_like(modal)
    obj.balance = 465
    obj.stable = Mock(side_effect=[(modal, before), (modal, selected)])
    obj.capture = Mock(side_effect=[modal, modal, clear])
    obj.click = Mock()
    obj.read_page = lambda image: after if image is modal else None
    shop._is_shop_scene.side_effect = lambda context, image: image is clear
    observed = []
    def read_balance(context, image):
        assert image is clear, 'never OCR currency through a customization overlay'
        observed.append(image)
        return 395
    shop._recognize_points.side_effect = read_balance
    with patch.object(vision, 'receipt', return_value=None), patch('time.sleep'):
        assert obj.customize_card(rule, before) == 'bought'
        assert obj.balance_pending and obj.balance == 395
        assert observed == []
        assert obj.confirm_shop_balance(clear) is clear
    assert not obj.balance_pending and len(observed) == 2
    assert shop._visit_state.call_args.kwargs == dict(custom_pending=False, custom_blocked=False)
    assert [call.args[0] for call in obj.click.call_args_list] == [before['options'][1]['box'], before['execute']]
    assert obj.stable.call_count == 2  # No post-completion re-open.


def test_named_receipt_and_shop_debit_finish_final_option_without_menu_reopen():
    obj, ctx, shop = session()
    rule, before, selected, _ = spotlight()
    modal = np.zeros((1280, 720, 3), np.uint8)
    toast = np.ones_like(modal)
    clear = np.full_like(modal, 2)
    obj.balance = 465
    obj.stable = Mock(side_effect=[(modal, before), (modal, selected)])
    obj.capture = Mock(side_effect=[toast, clear, clear])
    obj.click = Mock()
    obj.read_page = Mock(return_value=None)
    shop._is_shop_scene.side_effect = lambda context, image: image is clear
    def read_balance(context, image):
        assert image is clear
        return 395
    shop._recognize_points.side_effect = read_balance
    with patch.object(vision, 'receipt', side_effect=lambda context, image, key: [1, 1, 10, 10] if image is toast else None), \
         patch('time.sleep'):
        assert obj.customize_card(rule, before) == 'bought'
    assert obj.balance == 395 and not obj.balance_pending
    assert obj.stable.call_count == 2


def test_unexpected_shop_debit_stays_pending_and_never_reexecutes():
    obj, ctx, shop = session()
    clear = np.zeros((1280, 720, 3), np.uint8)
    obj.balance = 395; obj.balance_pending = True
    obj.read_page = Mock(return_value=None)
    obj.capture = Mock(return_value=clear)
    shop._is_shop_scene.return_value = True
    shop._recognize_points.return_value = 394
    with patch('time.sleep'), pytest.raises(InterruptedError, match='扣款未确认'):
        obj.confirm_shop_balance(clear)
    assert obj.balance_pending
    shop._visit_state.assert_not_called()
    ctx.tasker.controller.post_click.assert_not_called()


def test_obscured_list_never_reads_balance_or_clicks_even_when_ocr_might_succeed():
    obj, ctx, shop = session()
    modal = np.zeros((1280, 720, 3), np.uint8)
    obj.read_page = Mock(return_value=dict(stage='list', name=None, quota=1))
    obj.capture = Mock(return_value=modal)
    shop._is_shop_scene.return_value = True
    shop._recognize_points.side_effect = AssertionError('hidden HUD read')
    with patch('time.sleep'), pytest.raises(InterruptedError):
        obj.confirm_shop_balance(modal)
    ctx.tasker.controller.post_click.assert_not_called()
    shop._recognize_points.assert_not_called()


def test_unexpected_overlay_is_closed_before_returning_to_drink_flow():
    obj, ctx, shop = session()
    modal = np.zeros((1280, 720, 3), np.uint8)
    clear = np.ones_like(modal)
    obj.read_page = Mock(return_value=dict(stage='list', name=None, quota=1))
    obj.capture = Mock(return_value=modal)
    obj.leave = Mock(return_value=clear)
    shop._visit_state.return_value = {}
    shop._recognize_points.side_effect = AssertionError('hidden HUD read')
    with patch('time.sleep'):
        assert obj.run(modal) == (clear, None)
    obj.leave.assert_called_once()
    shop._recognize_points.assert_not_called()


def test_partially_finished_card_returned_to_shop_is_deferred_without_reopening():
    obj, ctx, shop = session()
    rule = target(counts=[2, 0, 0])
    before = menu()
    clear = np.zeros((1280, 720, 3), np.uint8)
    def bought(*args):
        obj.balance = 79
        obj.last_menu = None
        return 'bought'
    obj.purchase = Mock(side_effect=bought)
    obj.leave = Mock(return_value=clear)
    obj.read_page = Mock(return_value=None)
    shop._is_shop_scene.return_value = True
    shop._recognize_points.return_value = 79
    obj.click = Mock(); obj.scan = Mock()
    assert obj.customize_card(rule, before) == 'deferred'
    assert vision.next_purchase(rule, obj.deferred_menu, 79) == ('buy', 0)
    obj.click.assert_not_called(); obj.scan.assert_not_called()


def test_explicit_completed_tile_is_not_clicked_during_scan():
    obj, ctx, shop = session()
    cell = dict(box=[82, 860, 116, 116], name_key='脚光', customized=True,
                pixels=np.zeros((64, 64, 3), np.uint8))
    obj.rewind = Mock(return_value=(None, {}, [[cell]]))
    obj.open_card = Mock()
    with patch.object(vision, 'scrollbar', return_value=(800, 1037)):
        assert obj.scan() == []
    obj.open_card.assert_not_called()


def test_recorded_completed_badge_is_associated_with_its_own_tile():
    obj, ctx, shop = session()
    image = np.zeros((1280, 720, 3), np.uint8)
    boxes = [[82+146*i, 860, 116, 116] for i in range(4)]
    obj.stable = Mock(return_value=(image, dict(stage='list')))
    ctx.run_recognition.return_value = NS(all_results=[NS(box=b) for b in boxes])
    # Native replay of the user's screenshot: this badge is on the first card.
    badge = NS(text='カスタマイズ済', box=[78, 905, 120, 24], score=.999307)
    with patch('custom.produce.shop_customization.normalize_deck_boxes', return_value=boxes), \
         patch('custom.produce.shop_customization.match', return_value=None), \
         patch.object(vision, 'rows', return_value=[badge]):
        _, _, groups = obj.grid()
    assert [cell['customized'] for cell in groups[0]] == [True, False, False, False]


def test_unknown_unrelated_title_does_not_cancel_other_verified_jobs():
    obj, ctx, shop = session()
    unknown = dict(box=[82, 860, 116, 116], name_key=None, pixels=np.zeros((64, 64, 3), np.uint8))
    known = dict(box=[228, 860, 116, 116], name_key=obj.rules[0]['key'], pixels=np.ones((64, 64, 3), np.uint8))
    obj.rewind = Mock(return_value=(None, {}, [[unknown, known]]))
    obj.open_card = Mock(side_effect=[InterruptedError('选中卡名称未确认'), (dict(name=known['name_key']), menu())])
    obj.click = Mock(); obj.stable = Mock()
    with patch.object(vision, 'scrollbar', return_value=(800, 1037)):
        jobs = obj.scan()
    assert len(jobs) == 1 and jobs[0]['target'] == obj.rules[0]
    assert obj.scan_incomplete

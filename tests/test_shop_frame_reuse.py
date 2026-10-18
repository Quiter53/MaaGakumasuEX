"""Reuse only same-frame shop evidence, keeping refresh receipts intact."""
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from test_shop_recovery_contracts import context
from custom.shop import read_refresh
from custom.produce.shop import ProduceHIFIntervalShoppingAuto
from custom.produce.shop_customization import CustomizationSession


def test_refresh_reuses_only_the_verified_screenshot():
    ctx = context()
    image = np.zeros((1280, 720, 3), np.uint8)
    row = SimpleNamespace(box=[18, 1044, 174, 44])
    with patch('custom.shop.is_interval_shop', return_value=False) as scene, \
         patch('custom.shop.ocr', return_value=[row]) as label, \
         patch('custom.shop.read_number', return_value=20):
        assert read_refresh(ctx, image, verified_shop_image=image) == {'point': (105, 1066), 'cost': 20}
        scene.assert_not_called()
        # Equal pixels from another capture are not the verified screenshot.
        fresh = image.copy()
        assert read_refresh(ctx, fresh, verified_shop_image=image) is None
        assert scene.call_count == 1
        assert scene.call_args.args[1] is fresh
        assert label.call_count == 1
        # The default None must never be accepted as prior evidence.
        assert read_refresh(ctx, None) is None
        assert scene.call_count == 2


@pytest.mark.parametrize('cost,box,count', [
    (None, [18, 1044, 174, 44], 1),
    (0, [18, 1044, 174, 44], 1),
    (20, [250, 1044, 174, 44], 1),
    (20, [18, 1044, 174, 44], 2),
])
def test_verified_shop_still_checks_refresh_price_label_and_position(cost, box, count):
    image = np.zeros((1280, 720, 3), np.uint8)
    with patch('custom.shop.ocr', return_value=[SimpleNamespace(box=box)] * count), \
         patch('custom.shop.read_number', return_value=cost):
        assert read_refresh(context(), image, verified_shop_image=image) is None


@pytest.mark.parametrize('changed_by', ['neither', 'heal', 'customization'])
def test_interval_loop_passes_the_original_verified_frame(changed_by):
    ctx = context()
    shop = ProduceHIFIntervalShoppingAuto()
    original = np.zeros((1280, 720, 3), np.uint8)
    fresh = original.copy()
    healed = fresh if changed_by == 'heal' else original
    final = fresh if changed_by != 'neither' else original
    with ExitStack() as stack:
        stack.enter_context(patch('time.sleep'))
        stack.enter_context(patch('custom.produce.shared._detect_disconnect_screen', return_value=False))
        stack.enter_context(patch.object(shop, '_wait_shop_ready', return_value=original))
        stack.enter_context(patch.object(shop, '_is_shop_scene', return_value=True))
        stack.enter_context(patch.object(shop, '_heal_before_drinks', return_value=healed))
        stack.enter_context(patch.object(CustomizationSession, 'run', return_value=(final, None)))
        stack.enter_context(patch.object(shop, '_recognize_points', return_value=100))
        stack.enter_context(patch.object(shop, '_drink_inventory_full', return_value=False))
        stack.enter_context(patch.object(shop, '_recognize_drinks', return_value=[]))
        stack.enter_context(patch.object(shop, '_finish_optional_shop', return_value=True))
        refresh = stack.enter_context(patch('custom.shop.read_refresh', return_value=None))
        assert shop.run(ctx, SimpleNamespace())
    assert refresh.call_count == 1
    assert refresh.call_args.args[1] is final
    assert refresh.call_args.kwargs['verified_shop_image'] is original
    ctx.tasker.controller.post_click.assert_not_called()


def test_refresh_receipt_reuses_final_balance_without_third_read_for_logging():
    ctx = context()
    shop = ProduceHIFIntervalShoppingAuto()
    original = np.zeros((1280, 720, 3), np.uint8)
    after = [np.full_like(original, 20), np.full_like(original, 20)]
    ctx.tasker.controller.post_screencap.return_value.wait.return_value.get.side_effect = after
    with ExitStack() as stack:
        stack.enter_context(patch('time.sleep'))
        stack.enter_context(patch('custom.produce.shared._detect_disconnect_screen', return_value=False))
        stack.enter_context(patch.object(shop, '_wait_shop_ready', side_effect=[original, None]))
        stack.enter_context(patch.object(shop, '_is_shop_scene', return_value=True))
        stack.enter_context(patch.object(shop, '_heal_before_drinks', return_value=original))
        stack.enter_context(patch.object(CustomizationSession, 'run', return_value=(original, None)))
        balance = stack.enter_context(patch.object(shop, '_recognize_points', side_effect=[100, 90, 90]))
        stack.enter_context(patch.object(shop, '_drink_inventory_full', return_value=False))
        stack.enter_context(patch.object(shop, '_recognize_drinks', return_value=[]))
        stack.enter_context(patch.object(shop, '_finish_optional_shop', return_value=True))
        stack.enter_context(patch('custom.shop.read_refresh', return_value={'point': (105, 1066), 'cost': 10}))
        stack.enter_context(patch('custom.shop.stock_signature', side_effect=[np.zeros(3), np.full(3, 20), np.full(3, 20)]))
        assert shop.run(ctx, SimpleNamespace())
    assert balance.call_count == 3  # Initial balance, then two independent receipts.
    assert balance.call_args_list[1].args[1] is after[0]
    assert balance.call_args_list[2].args[1] is after[1]
    ctx.tasker.controller.post_click.assert_called_once_with(105, 1066)


@pytest.mark.parametrize('balances', [[90, 95] * 4, [None] * 8])
def test_refresh_still_requires_two_consecutive_correct_debits(balances):
    ctx = context()
    shop = ProduceHIFIntervalShoppingAuto()
    shop._disconnect_detected = False
    image = np.zeros((1280, 720, 3), np.uint8)
    with patch('time.sleep'), \
         patch('custom.produce.shared._detect_disconnect_screen', return_value=False), \
         patch.object(shop, '_is_shop_scene', return_value=True), \
         patch.object(shop, '_recognize_points', side_effect=balances), \
         patch('custom.shop.stock_signature', side_effect=[np.zeros(3)] + [np.full(3, 20)] * 8):
        assert shop._refresh_stock(ctx, image, 100, {'point': (105, 1066), 'cost': 10}) is None
    ctx.tasker.controller.post_click.assert_called_once_with(105, 1066)

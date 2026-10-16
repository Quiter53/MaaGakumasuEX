"""Recovery quote boundaries and the real 20/33 -> 33/33, 120 -> 50 incident."""
from types import SimpleNamespace as NS
from unittest.mock import patch

import numpy as np
import pytest

from test_shop_recovery_contracts import context
from custom.interval_recovery import read_dialog
from custom.produce.shop import ProduceHIFIntervalShoppingAuto


def read_quote(current, amount, before, after, *, question=None, maximum=33):
    image = np.zeros((1280, 720, 3), np.uint8)
    image[600:671, 35:670] = (0, 190, 250)
    def rows(ctx, frame, roi, **kwargs):
        x, y, w, h = roi
        text = {(35,600): '体力回復確認', (90,1111): 'キャンセル', (415,1111): '回復する',
                (295,751): f'+{amount}', (45,687): f'体力を{amount if question is None else question}回復',
                (285,1003): str(before), (455,1003): str(after)}.get((x,y))
        return [NS(text=text, box=roi, score=.99)] if text else []
    with patch('custom.interval_recovery.rows', side_effect=rows), \
         patch('custom.interval_recovery.read_fraction', return_value=(current+amount,maximum)), \
         patch('custom.interval_recovery.fraction_roi', return_value=[329,846,151,70]), \
         patch('custom.interval_recovery.point_row', return_value=(1013,1063)), \
         patch('custom.shop._recovery_step_button', side_effect=lambda image, roi, plus: [2 if plus else 3,0,1,1]):
        return read_dialog(context(), image, (current,maximum))


@pytest.mark.parametrize('current,amount,before,after,valid', [
    (20,13,120,50,True), (32,1,10,0,True), (21,12,120,60,True),
    (20,12,65,5,True), (20,13,120,55,False), (20,12,120,50,False),
    (20,13,120,40,False), (20,13,65,-5,True),
])
def test_quote_validates_steps_and_full_health_clipping(current,amount,before,after,valid):
    result = read_quote(current,amount,before,after)
    assert bool(result['quote']) is valid


def test_clipping_does_not_bypass_independent_recovery_amount_check():
    assert read_quote(20,13,120,50,question=12)['quote'] is None


@pytest.mark.parametrize('current,points,expected,remaining', [
    (20,120,33,50), (20,65,32,5), (20,10,22,0), (32,10,33,0), (20,9,20,9),
    (21,35,27,5),
])
def test_healing_adjusts_then_pays_once_and_verifies_receipt(current,points,expected,remaining):
    ctx = context()
    shop = ProduceHIFIntervalShoppingAuto()
    image = ctx.tasker.controller.post_screencap().wait().get()
    gain, paid, clicks = 2, False, []
    def dialog(*args):
        amount = min(gain, 33-current)
        result = read_quote(current,amount,points,points-((amount+1)//2)*10)
        if result['quote']:
            result['quote']['confirm'] = [4,0,1,1]
        return result
    def click(ctx, box):
        nonlocal gain, paid
        clicks.append(box[0])
        if box[0] == 2: gain += 2
        elif box[0] == 3: gain -= 2
        elif box[0] == 4:
            assert not paid
            assert min(current+gain,33) == expected
            paid = True
    with patch('time.sleep'), \
         patch('custom.produce.shared._detect_disconnect_screen', return_value=False), \
         patch('custom.shop.read_interval_health', side_effect=lambda *args: (expected if paid else current,33)), \
         patch('custom.shop.read_interval_heal_button', return_value=[1,0,1,1]), \
         patch('custom.shop.read_interval_recovery', side_effect=dialog), \
         patch.object(shop,'_recognize_points',side_effect=lambda *args: remaining if paid else points), \
         patch.object(shop,'_click_product',side_effect=click), \
         patch.object(shop,'_is_shop_scene',return_value=True), \
         patch.object(shop,'_wait_shop_ready') as cancelled:
        assert shop._heal_before_drinks(ctx,image,spend_remaining=True) is image
    assert clicks.count(4) == int(points >= 10)
    assert not shop._visit_state(ctx).get('heal_pending')
    cancelled.assert_not_called()

"""Overflow collection completes on verified inventory coverage, not thumb estimates."""
import sys
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent'))
from custom.drink_overflow import Bottle, OverflowPicker, RetryOverflow, View


class CloudPicker(OverflowPicker):
    """Two received + four owned rows; the cloud scrollbar retains a phantom tail.

    The owned heading moves from y=645 to 315 after a real 330 px scroll,
    matching the supplied log. Further drags cannot advance the actual list.
    """
    def __init__(self, selected=(2, 3, 4, 5), maximum=344):
        self.data = [NS(id=key, position=y, selected=i in selected)
                     for i, (key,y) in enumerate(zip(['T','N','T','T','X','Y'],[420,550,766,897,1045,1220]))]
        self.offset, self.maximum = 0, maximum
        self.scrolls, self.clicks, self.closed = [], [], False
        self.image = np.zeros((1279,720,3),np.uint8)
        self.controller = NS(post_click=Mock(side_effect=self.click),
                             post_screencap=Mock(return_value=NS(wait=lambda:NS(get=lambda:self.image))))
        self.context = NS(tasker=NS(stopping=False,controller=self.controller),run_recognition=self.recognize)

    def recognize(self, name, image):
        if name == 'ProduceKeepDrinkFlag':return NS(hit=not self.closed)
        assert name == 'ProduceDrinkNoButton'
        return NS(hit=True,best_result=NS(box=[300,1140,200,50]))

    def stable(self):
        rows=[Bottle(r.id,r.id,r.position,int(r.position-self.offset),r.selected)
              for r in self.data if 315<=r.position-self.offset<1025]
        return self.image,View(rows,self.offset,self.maximum)

    def top(self):
        self.offset=0
        return self.stable()

    def scroll(self, down):
        self.scrolls.append(down)
        self.offset=330 if down else 0
        return self.stable()

    def headings(self, image, offset):
        return {'new':332.,'owned':678.} if not offset else {'owned':678.}

    def zero_remaining(self, image):
        return sum(r.selected for r in self.data)==4

    def click(self,x,y):
        self.clicks.append((x,y))
        if y>1100:
            assert self.zero_remaining(self.image)
            self.closed=True
        else:
            matches=[r for r in self.data if abs(r.position-self.offset-y)<2]
            assert len(matches)==1
            matches[0].selected=not matches[0].selected
        return NS(wait=lambda:None)


def test_cloud_scan_collects_all_six_without_an_extra_clamped_drag():
    picker=CloudPicker()
    rows,_,view=picker.collect()
    assert not view.at_bottom  # The old scalar estimate still says there is more.
    assert len(rows)==6 and [r.source for r in rows]==['new','new','owned','owned','owned','owned']
    assert picker.scrolls==[True]
    assert picker.clicks==[]


def test_cloud_selection_and_final_verification_both_finish_with_phantom_tail():
    picker=CloudPicker()
    with patch('custom.drink_overflow.TARGET_NAMES',{'T'}),patch('custom.drink_overflow.time.sleep'):
        assert picker.run()
    assert [i for i,r in enumerate(picker.data) if r.selected]==[0,1,2,3]
    assert picker.closed
    # Collect, deselect, select, final collect: each visits the bottom once.
    assert picker.scrolls==[True]*4
    assert len(picker.clicks)==5


def test_already_correct_selection_submits_without_toggling_any_row():
    picker=CloudPicker(selected=(0,1,2,3))
    with patch('custom.drink_overflow.TARGET_NAMES',{'T'}),patch('custom.drink_overflow.time.sleep'):
        assert picker.run()
    assert picker.closed and len(picker.clicks)==1
    assert picker.scrolls==[True,True]


def test_three_owned_bottles_still_require_a_verified_bottom():
    picker=CloudPicker(maximum=330)
    picker.data.pop()
    rows,_,view=picker.collect()
    assert view.at_bottom and len(rows)==5
    assert sum(r.source=='owned' for r in rows)==3


def test_stalled_incomplete_scan_cannot_select_or_submit():
    picker=CloudPicker()
    picker.data.pop()
    with pytest.raises(RetryOverflow,match='列表滚动没有推进'):
        picker.collect()
    assert picker.clicks==[]


def test_missing_section_or_fewer_owned_rows_cannot_prove_coverage():
    rows=[Bottle('same','same',p,int(p),True) for p in [420,550,766,897,1045,1220]]
    assert not OverflowPicker.all_owned_seen(rows,{'owned':678})
    assert not OverflowPicker.all_owned_seen(rows[:-1],{'new':332,'owned':678})
    # Equal drink IDs are independent bottles, not a deduplicated inventory.
    assert OverflowPicker.all_owned_seen(rows,{'new':332,'owned':678})

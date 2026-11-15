"""Rental support selection: skip known conflicts before clicking."""
from types import SimpleNamespace as NS
from unittest.mock import patch

import numpy as np
import pytest
from custom.produce.support import ProduceChooseRentalSupportAuto, candidates, is_duplicate


def row(text, x=380, y=640, score=.99):
    return NS(text=text, box=[x, y, 90, 20], score=score)


@pytest.mark.parametrize('text', ['サポート重複', 'サポート 重複', '支援卡重复', '支援卡重複'])
def test_duplicate_alias(text):
    assert is_duplicate(text)


@pytest.mark.parametrize('text', ['サポート', 'フォロー', 'PLv90', '', 'サポートカード入れ替え'])
def test_other_text_is_not_badge(text):
    assert not is_duplicate(text)


def test_first_marked_skip_second_and_keep_order():
    result = candidates([row('PLv90'), row('PLv80', y=840), row('サポート重複', x=100)], first_page=True)
    assert [(r.y, r.duplicate) for r in result] == [(650, True), (850, False)]


def test_toolbar_occluded_third_row_is_never_clicked():
    result = candidates([row('PLv90'), row('PLv80', y=840), row('PLv90', y=1020), row('サポート重複', x=100, y=1020)], first_page=True)
    assert [r.y for r in result] == [650, 850]


def test_split_label_and_first_fallback():
    result = candidates([row('サポート', x=80), row('重複', x=180)], first_page=True)
    assert [(r.y, r.duplicate) for r in result] == [(620, True)]
    assert candidates([], first_page=True)[0].y == 620
    assert candidates([], first_page=False) == []


class Job:
    succeeded = True
    def __init__(self, value=None): self.value = value
    def wait(self): return self
    def get(self): return self.value


class Context:
    def __init__(self, pages, reject=(), unknown=False):
        self.pages, self.reject, self.unknown = pages, set(reject), unknown
        self.page = 0
        self.clicked = []
        self.swipes = 0
        self.dismissed = 0
        self.pending = None
        self.ocr = 0
        self.tasker = NS(stopping=False, controller=NS(
            post_screencap=self.capture, post_click=self.click, post_swipe=self.swipe))
    def capture(self): return Job(np.full((1280, 720, 3), self.page * 20, dtype=np.uint8))
    def click(self, x, y):
        self.clicked.append((self.page, x, y)); self.pending = (self.page, y); return Job()
    def swipe(self, *args):
        self.swipes += 1; self.page = min(self.page + 1, len(self.pages) - 1); return Job()
    def run_task(self, node):
        assert node == 'ProduceSupportDismissDuplicate'
        self.dismissed += 1; self.pending = None
        return NS(status=NS(succeeded=True))
    def run_recognition(self, node, image):
        if node == 'ProduceSupportRentalText':
            self.ocr += 1
            return NS(hit=bool(self.pages[self.page]), all_results=self.pages[self.page])
        if node == 'ProduceChooseSupportReplace': return NS(hit=self.pending in self.reject)
        if node == 'ProduceChooseSupportNext': return NS(hit=self.pending is not None and self.pending not in self.reject and not self.unknown)
        return NS(hit=False)


def run(context):
    with patch('custom.produce.support.time.sleep'):
        return ProduceChooseRentalSupportAuto().run(context, NS())


def test_normal_fast_path():
    context = Context([[row('PLv90')]])
    assert run(context)
    assert context.clicked == [(0, 420, 650)]
    assert context.ocr == 1 and context.swipes == 0


def test_unknown_badge_still_selects_first():
    context = Context([[]])
    assert run(context)
    assert context.clicked == [(0, 420, 620)]


def test_all_visible_duplicates_scroll_without_clicking():
    context = Context([[row('PLv90'), row('サポート重複', x=100), row('PLv80', y=840), row('サポート重複', x=100, y=840)], [row('PLv70')]])
    assert run(context)
    assert context.clicked == [(1, 420, 650)]
    assert context.swipes == 1


def test_missed_badge_cancel_then_next_visible():
    context = Context([[row('PLv90'), row('PLv80', y=840)]], reject=[(0, 650)])
    assert run(context)
    assert context.clicked == [(0, 420, 650), (0, 420, 850)]
    assert context.dismissed == 1 and context.swipes == 0 and context.ocr == 1


def test_rejected_candidate_not_clicked_again_after_scroll():
    owner = row('Alice', x=50, y=590)
    context = Context([[owner, row('PLv90')], [owner, row('PLv90'), row('PLv80', y=840)]], reject=[(0, 650)])
    assert run(context)
    assert context.clicked == [(0, 420, 650), (1, 420, 850)]


def test_same_level_is_not_same_owner():
    assert candidates([row('PL90')])[0].y == 650
    assert not candidates([row('PL90')])[0].identity
    context = Context([[row('PLv90')], [row('PLv90')]], reject=[(0, 650)])
    assert run(context)
    assert context.clicked == [(0, 420, 650), (1, 420, 650)]


def test_unknown_modal_does_not_trigger_more_clicks():
    context = Context([[row('PLv90'), row('PLv80', y=840)]], unknown=True)
    assert not run(context)
    assert len(context.clicked) == 1 and context.swipes == 0


def test_bottom_all_duplicates_is_bounded():
    context = Context([[row('PLv90'), row('サポート重複', x=100)]])
    assert not run(context)
    assert context.clicked == [] and context.swipes == 2


def test_stop_no_input():
    context = Context([[row('PLv90')]])
    context.tasker.stopping = True
    assert not run(context)
    assert not context.clicked and not context.swipes

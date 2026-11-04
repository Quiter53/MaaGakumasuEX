"""Hold-list routing, exact identity, and multi-pick commit regressions."""

from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import numpy as np
import pytest

from custom.battle_hold import GREEN_TEA, HoldPicker, hold_applies, selection_count
from custom.battle_drinks import BattleDrinkSession, signature
from custom.drink_policy import CATALOG


def preset(ordered=True):
    return {"career": "全力", "rules": [], "hold": {"ordered": ordered, "cards": ["イメトレ", "スポットライト"]}}


@pytest.mark.parametrize("career,source,expected", [
    ("全力", None, True), ("强气", None, True), ("集中", None, False),
    ("集中", GREEN_TEA, True), ("全力", "初星黒酢", False),
    ("全力", "初星湯", False), ("全力", "", False),
])
def test_hold_source_routing(career, source, expected):
    value = preset()
    value["career"] = career
    assert hold_applies(value, source) is expected
    value["hold"]["cards"] = []
    assert not hold_applies(value, source)


@pytest.mark.parametrize("texts,expected", [
    ([("0/2", .99)], 0), ([("２／２", .99)], 2),
    ([("移動 0/2", .99)], 0), ([("1/2", .70)], None), ([("1/2", .99), ("2/2", .99)], None),
    ([("12", .99)], None), ([("3/2", .99)], None),
])
def test_multi_count_is_exact_unambiguous_and_confident(texts, expected):
    with patch("custom.battle_hold.ocr", return_value=[NS(text=text, score=score) for text, score in texts]):
        assert selection_count(Mock(), Mock()) == expected


@pytest.mark.parametrize("ordered,expected", [(True, "イメトレ"), (False, "スポットライト")])
def test_single_hold_honors_list_order_or_encounter_order(ordered, expected):
    picker = HoldPicker(Mock(), preset(ordered), source=GREEN_TEA)
    box = [80, 400, 118, 118]
    with patch.object(picker, "capture"), patch.object(picker, "rewind"), \
         patch("custom.battle_hold.is_move_page", return_value=True), \
         patch("custom.battle_hold.selection_count", return_value=None), \
         patch.object(picker, "scan", side_effect=[["スポットライト", "イメトレ"], (expected, box)]), \
         patch.object(picker, "commit", return_value=expected) as commit:
        assert picker.run() == expected
        commit.assert_called_once_with(expected, box)


def test_multi_selection_locates_each_card_before_counted_selection():
    picker = HoldPicker(Mock(), preset())
    first, second = [80, 400, 118, 118], [226, 400, 118, 118]
    with patch.object(picker, "capture"), patch.object(picker, "rewind"), \
         patch("custom.battle_hold.is_move_page", return_value=True), \
         patch("custom.battle_hold.selection_count", return_value=0), \
         patch.object(picker, "scan", side_effect=[["スポットライト", "イメトレ"], ("イメトレ", first), ("スポットライト", second)]), \
         patch.object(picker, "select_multi") as select, patch.object(picker, "finish") as finish:
        assert picker.run() == ["イメトレ", "スポットライト"]
        assert [call.args for call in select.call_args_list] == [("イメトレ", first), ("スポットライト", second)]
        finish.assert_called_once()


def test_multi_no_named_target_keeps_page_without_random_clicks():
    picker = HoldPicker(Mock(), preset())
    with patch.object(picker, "capture"), patch.object(picker, "rewind"), \
         patch("custom.battle_hold.is_move_page", return_value=True), \
         patch("custom.battle_hold.selection_count", return_value=0), \
         patch.object(picker, "scan", return_value=["ゆめみごこち"]), patch.object(picker, "click") as click:
        with pytest.raises(RuntimeError, match="未找到"):
            picker.run()
        click.assert_not_called()


def test_existing_multi_selection_is_preserved():
    picker = HoldPicker(Mock(), preset())
    with patch.object(picker, "capture"), patch("custom.battle_hold.is_move_page", return_value=True), \
         patch("custom.battle_hold.selection_count", return_value=1), patch.object(picker, "click") as click:
        with pytest.raises(RuntimeError, match="已有选中"):
            picker.run()
        click.assert_not_called()


@pytest.mark.parametrize("source", [None, "本領発揮", "本領発揮+"])
def test_unknown_multi_count_never_falls_through_to_single_selection(source):
    picker = HoldPicker(Mock(), preset(), source=source)
    with patch.object(picker, "capture"), patch("custom.battle_hold.is_move_page", return_value=True), \
         patch("custom.battle_hold.selection_count", return_value=None), patch.object(picker, "click") as click, \
         patch("custom.battle_hold.time.sleep"):
        with pytest.raises(RuntimeError, match="数量未确认"):
            picker.run()
        click.assert_not_called()


def test_single_commit_uses_the_hold_page_recognizer():
    context = Mock()
    context.run_recognition.return_value = NS(hit=True)
    picker = HoldPicker(context, preset(), source=GREEN_TEA)
    with patch.object(picker, "capture"), patch.object(picker, "click") as click, \
         patch("custom.battle_hold.is_hold_page", side_effect=[True, False]), \
         patch("custom.battle_move.is_move_page", return_value=False), \
         patch("custom.battle_move.move_button", return_value=[300, 1140, 90, 40]), \
         patch("custom.battle_move.selected_title", return_value="イメトレ"), \
         patch("custom.battle_move.selected_card_box_matches", return_value=True), \
         patch("custom.battle_move.BattleDrinkSession.use_enabled", return_value=True), \
         patch("custom.battle_move.time.sleep"):
        assert picker.commit("イメトレ", [80, 400, 118, 118]) == "イメトレ"
        click.assert_called_once()


@pytest.mark.parametrize("counts,success", [([0, 1, 1], True), ([0] * 9, False), ([0, 2], False)])
def test_multi_click_requires_two_confirmed_count_increments(counts, success):
    picker = HoldPicker(Mock(), preset())
    picker.multi = True
    with patch.object(picker, "capture"), patch.object(picker, "click") as click, \
         patch("custom.battle_hold.is_move_page", return_value=True), \
         patch("custom.battle_hold.selected_title", return_value="イメトレ"), \
         patch("custom.battle_hold.selected_card_box_matches", return_value=True), \
         patch("custom.battle_hold.selection_count", side_effect=counts), patch("custom.battle_hold.time.sleep"):
        if success:
            picker.select_multi("イメトレ", [80, 400, 118, 118])
            assert picker.selected_count == 1
        else:
            with pytest.raises(RuntimeError):
                picker.select_multi("イメトレ", [80, 400, 118, 118])
            assert picker.selected_count == 0
        click.assert_called_once()


@pytest.mark.parametrize("raises", [False, True])
def test_green_tea_source_lives_only_during_post_drink_wait(raises):
    session = BattleDrinkSession()
    context = Mock()
    context.tasker.stopping = False
    frame = np.zeros((1280, 720, 3), np.uint8)
    frame[1100:1165, 400:580] = [20, 140, 255]
    key = "762145"
    session.inventory[0] = (signature(frame, session.SLOT_ROIS[0]), key)
    context.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value = frame
    context.run_recognition.return_value = NS(hit=True, best_result=NS(box=[400, 1100, 180, 65]))

    def wait(*args):
        assert session.move_source == GREEN_TEA
        if raises:
            raise RuntimeError("hold failed")
        return True

    with patch.object(session, "occupied", side_effect=[[0], []]), patch("custom.battle_drinks.time.sleep"), \
         patch("custom.battle_drinks.owned_detail_title", return_value=CATALOG[key]["jp"]):
        if raises:
            with pytest.raises(RuntimeError, match="hold failed"):
                session.prepare(context, frame, wait, remaining=4)
        else:
            assert session.prepare(context, frame, wait, remaining=4)
    assert session.move_source is None

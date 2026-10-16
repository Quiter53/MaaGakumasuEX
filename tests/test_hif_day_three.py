"""User activity choice survives transitions and cannot leak into lessons."""

import sys
import json
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
from custom.reco.hif import ProduceHIFOuting
from custom.hif_schedule import outing_choice, outing_header, read_schedule, schedule_choice, read_outing_choices
from custom.action.produce import ProduceChooseOptionsAuto, ProduceHIFChooseOutingAuto, ProduceHIFChooseSupplyAuto


def context(**settings):
    ctx = Mock()
    ctx.tasker.stopping = False
    ctx.get_node_data.return_value = {"attach": settings}
    ctx.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value = np.zeros((1280, 720, 3), np.uint8)
    ctx.run_recognition.return_value = SimpleNamespace(hit=False)
    return ctx


def row(text, box, score=.99):
    return SimpleNamespace(text=text, box=box, score=score)


TILES = {"outing": [180, 1060, 110, 30], "supply": [390, 1060, 110, 30], "rest": [620, 870, 50, 30]}
ROWS = [("null", [50, 660+i*100, 620, 76], .96) for i in range(3)]


@pytest.mark.parametrize("choice", ["outing", "supply", "rest"])
def test_requested_activity_clicked_once_after_two_observations(choice):
    ctx = context(day_three_activity=choice)
    with patch("custom.hif_schedule.read_schedule", return_value=TILES) as read, \
         patch("custom.action.produce._detect_disconnect_screen", return_value=False), \
         patch("custom.action.produce.time.sleep"):
        assert ProduceHIFChooseSupplyAuto().run(ctx, None)
    assert read.call_count == 2
    x, y, w, h = TILES[choice]
    ctx.tasker.controller.post_click.assert_called_once_with(x+w//2, y+h//2)


@pytest.mark.parametrize("choice,frames", [
    ("outing", [{"supply": TILES["supply"]}]),
    ("rest", [{"rest": TILES["rest"]}]),
    ("outing", [TILES, {}]),
    ("rest", [{k:v for k,v in TILES.items() if k != "rest"}]),
])
def test_missing_or_changed_tiles_never_fall_back_to_supply(choice, frames):
    ctx = context(day_three_activity=choice)
    with patch("custom.hif_schedule.read_schedule", side_effect=frames), \
         patch("custom.action.produce._detect_disconnect_screen", return_value=False), \
         patch("custom.action.produce.time.sleep"):
        assert ProduceHIFChooseSupplyAuto().run(ctx, None)
    ctx.tasker.controller.post_click.assert_not_called()


def test_no_rest_uses_left_does_not_click():
    ctx = context(day_three_activity="rest")
    with patch("custom.hif_schedule.read_schedule", return_value=TILES), \
         patch("custom.hif_schedule._ocr", return_value=[row("あと0回", [600,800,80,30])]), \
         patch("custom.action.produce._detect_disconnect_screen", return_value=False), \
         patch("custom.action.produce.time.sleep"):
        assert ProduceHIFChooseSupplyAuto().run(ctx, None)
    ctx.tasker.controller.post_click.assert_not_called()


@pytest.mark.parametrize("option", [1, 2, 3])
def test_outing_ordinal_ignores_lesson_option_and_attributes(option):
    ctx = context(outing_option=option, lesson_option=4-option, lesson_attribute="third")
    with patch("custom.hif_schedule.read_outing_choices", return_value=ROWS) as read, \
         patch("custom.action.produce._detect_disconnect_screen", return_value=False), \
         patch("custom.action.produce.time.sleep"):
        assert ProduceHIFChooseOutingAuto().run(ctx, None)
    assert read.call_count == 2
    ctx.tasker.controller.post_click.assert_called_once_with(360, 698+(option-1)*100)


def test_incomplete_outing_rows_yield_without_ending_task_or_clicking():
    ctx = context(outing_option=3)
    with patch("custom.hif_schedule.read_outing_choices", return_value=ROWS[:2]) as read, \
         patch("custom.action.produce._detect_disconnect_screen", return_value=False), \
         patch("custom.action.produce.time.sleep"):
        assert ProduceHIFChooseOutingAuto().run(ctx, None)
    assert read.call_count == 6
    ctx.tasker.controller.post_click.assert_not_called()


@pytest.mark.parametrize("action", [ProduceHIFChooseOutingAuto, ProduceHIFChooseSupplyAuto])
def test_stop_and_disconnect_never_click(action):
    ctx = context()
    ctx.tasker.stopping = True
    assert action().run(ctx, None)
    ctx.tasker.controller.post_screencap.assert_not_called()
    ctx.tasker.stopping = False
    with patch("custom.action.produce._detect_disconnect_screen", return_value=True):
        assert action().run(ctx, None)
    ctx.tasker.controller.post_click.assert_not_called()


def test_schedule_ocr_rejects_description_calendar_duplicates_and_low_scores():
    ctx = context()
    frame = np.zeros((1280, 720, 3), np.uint8)
    rows = [row("活動支給・差し入れ選択時、", [100, 900, 400, 30]),
            row("差し入れ", [350, 200, 150, 30]), row("休む", TILES["rest"], .7),
            row("お出かけ", TILES["outing"]), row("お出かけ", [10, 900, 110, 30])]
    ctx.run_recognition.return_value = SimpleNamespace(hit=True, filtered_results=rows)
    assert read_schedule(ctx, frame) == {}
    rows[:] = [row("お出かけ", TILES["outing"]), row("活動支給", TILES["supply"])]
    assert read_schedule(ctx, frame) == {"outing": TILES["outing"], "supply": TILES["supply"]}
    assert read_schedule(ctx, frame[:720]) == {}


def test_outing_title_owns_page_even_if_buttons_not_ready_and_lesson_rejected():
    ctx = context()
    frame = np.zeros((1280, 720, 3), np.uint8)
    ctx.run_recognition.return_value = SimpleNamespace(hit=True, filtered_results=[row("お出かけ", [30,40,150,40])])
    assert ProduceHIFOuting().analyze(ctx, SimpleNamespace(image=frame)) is not None
    ctx.run_recognition.return_value = SimpleNamespace(hit=True, filtered_results=[row("授業", [30,40,150,40])])
    with patch("custom.reco.options_fallback.read_fallback_options") as read:
        assert read_outing_choices(ctx, frame) == []
        read.assert_not_called()
    assert outing_header(ctx, frame) is None


def test_defaults_and_invalid_settings():
    assert schedule_choice({}) == "supply"
    assert schedule_choice({"day_three_activity": "bad"}) == "supply"
    assert outing_choice({}) == 1
    assert outing_choice({"outing_option": "bad"}) == 1
    assert outing_choice({"outing_option": 999}) == 3


@pytest.mark.parametrize("header", [True, False])
def test_generic_option_fallback_cannot_bypass_hif_ordinal(header):
    ctx = context()
    ctx.get_node_data.return_value = {"next": "ProduceEntryHIF"}
    with patch("custom.hif_schedule.outing_header", return_value=[30, 40, 150, 40] if header else None), \
         patch("custom.hif.read_hif_lesson_header", return_value=None), \
         patch.object(ProduceHIFChooseOutingAuto, "run", return_value=True) as outing:
        assert ProduceChooseOptionsAuto().run(ctx, None)
    assert outing.call_count == int(header)
    ctx.tasker.controller.post_click.assert_not_called()


def test_native_task_hierarchy_translations_and_exclusive_routes():
    data = json.loads((ROOT / "assets/tasks/produce.json").read_text("utf8"))
    options = data["option"]
    cases = options["「HIF」类型"]["cases"]
    main = next(case for case in cases if case["name"] == "本战")["option"]
    assert main.index("HIF本战第三周选项") == main.index("HIF本战上课属性") + 1
    activity = options["HIF本战第三周选项"]
    assert activity["default_case"] == "支给"
    assert [c["name"] for c in activity["cases"]] == ["外出", "支给", "休息"]
    for case, expected in zip(activity["cases"], ["outing", "supply", "rest"]):
        assert case["pipeline_override"]["ProduceHIFStrategy"]["attach"] == {"day_three_activity": expected}
        assert case.get("option", []) == (["HIF本战外出选项"] if expected == "outing" else [])
    for i, case in enumerate(options["HIF本战外出选项"]["cases"], 1):
        assert case["pipeline_override"]["ProduceHIFStrategy"]["attach"] == {"outing_option": i}
    for path in (ROOT / "assets/lang").glob("*.json"):
        translated = json.loads(path.read_text("utf8"))
        assert all(translated.get(k) for k in ["第三周选项", "外出选项", "外出", "支给", "休息"])
    pipeline = json.loads((ROOT / "assets/resource/base/pipeline/ProduceHIF.json").read_text("utf8"))
    route = pipeline["ProduceEntryHIF"]["next"]
    assert route.index("[JumpBack]ProduceHIFOutingFlag") < route.index("[JumpBack]ProduceRecognitionWorkOptions")
    assert "[JumpBack]ProduceTakeRest" in route
    for path in ["Produce.json", "ProduceNIA.json"]:
        assert "ProduceHIFOutingFlag" not in (ROOT / "assets/resource/base/pipeline" / path).read_text("utf8")
    # The translated game task shares the same difficulty/HIF option tree.
    cn = json.loads((ROOT / "assets/tasks/produce_cn.json").read_text("utf8"))
    assert "培育难度" in cn["task"][0]["option"]
    assert "培育难度" not in cn["option"]

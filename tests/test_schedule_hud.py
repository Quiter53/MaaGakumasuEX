import json
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import numpy as np
import pytest

from custom.cultivation_hud import read_schedule_health, read_points
from custom.produce.events import ProduceChooseEventBase
from custom.produce.options import ProduceChooseWorkAuto


@pytest.mark.parametrize("text,value", [("１７／３０", 17), ("0/30", 0), ("17/0", None), ("♡17/30", None), ("37/30", None)])
def test_schedule_uses_numeric_line_not_old_heart_roi(text, value):
    ctx = Mock()
    ctx.run_recognition.return_value = NS(hit=True, filtered_results=[NS(text=text, score=.99)])
    result = read_schedule_health(ctx, np.zeros((1280, 720, 3), np.uint8))
    assert (result["current"] if result else None) == value
    override = ctx.run_recognition.call_args.kwargs["pipeline_override"]["ProduceRecognitionPoint"]
    assert override["roi"] == [328, 56, 122, 50]
    assert override["only_rec"] is True


def test_schedule_points_excludes_icons_and_adjacent_rows():
    ctx = Mock()
    ctx.run_recognition.return_value = NS(hit=True, filtered_results=[NS(text="330", score=.99)])
    assert read_points(ctx, np.zeros((1280, 720, 3), np.uint8)) == 330
    assert ctx.run_recognition.call_args.kwargs["pipeline_override"]["ProduceRecognitionPoint"]["roi"] == [328, 104, 114, 38]


@pytest.mark.parametrize("health,points", [(None, 300), ({"current": 17, "max": 30, "ratio": 17/30}, None)])
def test_schedule_unknown_value_cannot_make_a_decision(health, points):
    ctx = Mock()
    action = ProduceChooseEventBase()
    with patch.object(action, "_get_suggestion", return_value=""), \
         patch.object(action, "_get_health", return_value=health), \
         patch.object(action, "_get_current_points", return_value=points), \
         patch.object(action, "_choose_best_event") as choose:
        assert action.run(ctx, NS(custom_action_param="{}")) is False
    choose.assert_not_called()
    ctx.tasker.controller.post_click.assert_not_called()


@pytest.mark.parametrize("current,expected", [(None, None), (0, [100, 1000]), (17, [100, 880])])
def test_work_unknown_is_not_zero_and_known_policy_unchanged(current, expected):
    action = ProduceChooseWorkAuto()
    ctx = Mock()
    health = None if current is None else {"current": current}
    with patch.object(action, "_get_health", return_value=health), \
         patch.object(action, "_get_health_position", return_value=[2]):
        assert action.run(ctx, NS()) == (current is not None)
    if expected is None:
        ctx.tasker.controller.post_click.assert_not_called()
    else:
        ctx.tasker.controller.post_click.assert_called_once_with(*expected)


def test_hud_failure_exits_to_observation_without_generic_click():
    root = Path(__file__).resolve().parents[1] / "assets/resource/base/pipeline"
    for filename, node in [("Produce.json", "ProduceChooseEventFlag"), ("ProduceNIA.json", "ProduceChooseNIAEventFlag"), ("ProduceNIA.json", "ProduceWorkFlag")]:
        data = json.loads((root / filename).read_text("utf-8"))
        assert data[node]["on_error"] == ["ProduceActionYield"]
    data = json.loads((root / "Produce.json").read_text("utf-8"))
    assert data["ProduceActionYield"]["action"]["type"] == "DoNothing"
    assert not data["ProduceActionYield"].get("next")

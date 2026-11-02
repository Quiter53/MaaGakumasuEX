"""Event strengthening uses the requested tiers and the existing confirmation pipeline."""

import itertools
import json
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import numpy as np
import pytest

from custom.produce.rewards import ProduceChooseRewardAuto, ProduceChooseStrengthenAuto


def card(name, rarity, extra="no", index=0):
    return dict(name=name, name_key=name, rarity=rarity, extra_action=extra,
                name_confidence=0.95, box=[89 + index * 140, 700, 123, 123])


def test_priority_order_and_whitelist_independent_of_position():
    ordered = [card("white", "R"), card("SSR+", "SSR", "yes"), card("SR+", "SR", "yes"),
               card("SSR", "SSR"), card("SR", "SR"), card("R", "R")]
    for candidates in itertools.permutations(ordered):
        assert sorted(candidates, key=lambda item: ProduceChooseStrengthenAuto.selection_key(item, {"white"}), reverse=True) == ordered


@pytest.mark.parametrize("extra", ["yes", "no", "unknown"])
def test_r_extra_does_not_leapfrog_requested_ssr_sr_tiers(extra):
    assert ProduceChooseStrengthenAuto.selection_key(card("SR", "SR"), set()) > ProduceChooseStrengthenAuto.selection_key(card("R", "R", extra), set())


@pytest.mark.parametrize("outcome", ["normal", "whitelist_off", "moving", "scene_left", "disconnect", "stopped", "confirm_absent", "unrecognized"])
def test_strengthen_entry_owns_selection_and_only_then_confirms(outcome):
    context = Mock()
    context.tasker.stopping = outcome == "stopped"
    frame = np.zeros((1280, 720, 3), np.uint8)
    context.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value = frame
    context.run_recognition.side_effect = lambda name, image: NS(hit=outcome != "scene_left" and (name != "ProduceChooseStrengthen" or outcome != "confirm_absent"))
    candidates = [card("SSR+", "SSR", "yes"), card("white", "R", index=1)]
    lists = {"whitelist": {"enabled": outcome != "whitelist_off", "cards": ["white"]}}
    with patch("custom.produce.shared._load_card_lists", return_value=lists), \
         patch("custom.produce.shared._detect_disconnect_screen", return_value=outcome == "disconnect"), \
         patch("custom.produce.rewards.time.sleep"), \
         patch.object(ProduceChooseRewardAuto, "_evaluate", return_value=[] if outcome == "unrecognized" else candidates), \
         patch.object(ProduceChooseRewardAuto, "_reward_frames_stable", side_effect=lambda before, after: before is not None and outcome != "moving"):
        assert ProduceChooseStrengthenAuto().run(context, None)
    if outcome in ("moving", "scene_left", "disconnect", "stopped"):
        context.tasker.controller.post_click.assert_not_called()
        context.run_task.assert_not_called()
    elif outcome == "unrecognized":
        context.tasker.controller.post_click.assert_called_once_with(140, 760)
        context.run_task.assert_called_once_with("ProduceChooseStrengthen")
    else:
        expected = candidates[0 if outcome == "whitelist_off" else 1]["box"]
        context.tasker.controller.post_click.assert_called_once_with(expected[0] + expected[2] // 2, expected[1] + expected[3] // 2)
        if outcome == "confirm_absent":
            context.run_task.assert_not_called()
        else:
            context.run_task.assert_called_once_with("ProduceChooseStrengthen")


def test_pipeline_does_not_preselect_first_card_or_confirm_failed_selection():
    data = json.loads((Path(__file__).resolve().parents[1] / "assets/resource/base/pipeline/Produce.json").read_text("utf-8"))
    assert data["ProduceChooseStrengthenFlag"]["action"]["type"] == "DoNothing"
    assert data["ProduceChooseStrengthenClick"]["action"]["param"]["custom_action"] == "ProduceChooseStrengthenAuto"
    assert data["ProduceChooseStrengthenClick"]["next"] == []
    assert data["ProduceChooseStrengthen"]["recognition"]["param"]["template"] == "produce/strengthen.png"

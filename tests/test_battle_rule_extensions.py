"""Optional rule schema and normal battle-path safety regressions."""

import copy
import sys
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

from custom import card_rules as rules
from custom.battle_followup import FollowUpSession
from custom.battle_state import BattleStateReader
from custom.produce.battle import ProduceCardsAuto


@pytest.fixture
def preset():
    value = rules.active_preset(rules.defaults())
    value["rules"] = [copy.deepcopy(value["rules"][0])]
    value["rules"][0].update(card="source", limited=False, points=[[30, 30], [1, 30]])
    return value


def follow_rule(preset, policy="normal", wait=1):
    preset["rules"][0]["followUp"] = {"cards": ["target", "backup"], "waitTurns": wait, "targetPolicy": policy}
    session = FollowUpSession()
    session.sync(preset)
    return session


def test_or_groups_and_atoms_and_unknown_states(preset):
    rule = preset["rules"][0]
    rule["conditions"] = [
        [{"field": "remaining", "min": 1, "max": 3}, {"field": "stance", "value": "全力"}],
        [{"field": "fullPower", "min": 10, "max": 20}],
    ]
    assert not rules.allowed(rule, 2)
    assert rules.allowed(rule, 2, {"stance": "全力"})
    assert not rules.allowed(rule, 4, {"stance": "全力"})
    assert rules.allowed(rule, None, {"fullPower": 10})
    assert not rules.allowed(rule, 2, {"stance": "温存", "fullPower": True})
    rule["limited"] = True
    assert not rules.allowed(rule, None, {"fullPower": 10})


def test_override_first_match_fallback_and_sort_contract(preset):
    rule = preset["rules"][0]
    rule["priorityOverrides"] = [
        {"conditions": [[{"field": "fullPower", "min": 0, "max": 20}]], "priority": 99},
        {"conditions": [[{"field": "stance", "value": "全力"}]], "priority": 75},
    ]
    assert rules.priority(rule, 5) == 30
    assert rules.priority(rule, None) == 50
    assert rules.priority(rule, None, {"stance": "全力"}) == 75
    state = {"stance": "全力", "fullPower": 20}
    assert rules.priority(rule, 5, state) == 99
    key = rules.sort_key(preset, "source+", "SSR", False, 5, 2, state)
    assert key == (1, 0, 3, 99, -2)
    assert key < rules.sort_key(preset, "other", "N", True, 5)


@pytest.mark.parametrize("field,value", [
    ("conditions", [[]]),
    ("conditions", [[{"field": "fullPower", "min": -1, "max": 999}]]),
    ("conditions", [[{"field": "remaining", "min": 1, "max": 31}]]),
    ("conditions", [[{"field": "stance", "value": "unknown"}]]),
    ("priorityOverrides", [{"conditions": [], "priority": 50}]),
    ("priorityOverrides", [{"conditions": [[{"field": "stance", "value": "全力"}]], "priority": True}]),
    ("followUp", {"cards": [], "waitTurns": 1, "targetPolicy": "normal"}),
    ("followUp", {"cards": ["x", "x+"], "waitTurns": 1, "targetPolicy": "normal"}),
    ("followUp", {"cards": ["x"], "waitTurns": 7, "targetPolicy": "normal"}),
])
def test_invalid_extensions_rejected(field, value):
    doc = rules.defaults()
    rules.active_preset(doc)["rules"][0][field] = value
    with pytest.raises(ValueError):
        rules.validate(doc)


def test_followup_cycle_rejected_but_national_pair_unchanged():
    doc = rules.defaults()
    preset = rules.active_preset(doc)
    first, second = preset["rules"][:2]
    first["followUp"] = {"cards": [second["card"]], "waitTurns": 1, "targetPolicy": "normal"}
    second["followUp"] = {"cards": [first["card"]], "waitTurns": 1, "targetPolicy": "normal"}
    with pytest.raises(ValueError, match="循环"):
        rules.validate(doc)
    second["followUp"] = None
    assert rules.validate(doc) is doc


def test_ordered_alternatives_and_confirmed_submission(preset):
    session = follow_rule(preset)
    items = [{"rule_name": "backup"}, {"rule_name": "target+"}, {"rule_name": "other"}]
    assert session.prefer(items) == items
    session.submitted("source", 5)
    assert session.prefer(items) == [items[1]]
    assert not session.allowed("other")
    assert session.active == "source"
    session.submitted("target+", 5)
    assert session.active is None
    assert session.prefer(items) == items


@pytest.mark.parametrize("policy", ["reserveFirst", "comboOnly"])
def test_reservation_release_only_after_success_or_timeout(preset, policy):
    session = follow_rule(preset, policy, wait=0)
    assert not session.allowed("target")
    session.submitted("source", 5)
    assert session.allowed("target")
    assert "source" not in session.released
    session.observe(None)
    assert session.active == "source"
    session.observe(4)
    assert session.active is None
    assert "source" in session.released
    assert session.allowed("target") is (policy == "reserveFirst")


def test_wait_is_bounded_and_preset_changes_reset(preset):
    session = follow_rule(preset, "reserveFirst", wait=2)
    session.submitted("source", 5)
    session.observe(4)
    session.observe(4)
    assert session.waited == 1
    session.observe(None)
    assert session.waited == 1
    with patch("custom.battle_followup.time.monotonic", return_value=session.started + 91):
        session.observe(None)
    assert session.active is None
    assert session.allowed("target")
    preset["name"] = "changed"
    session.sync(preset)
    assert not session.allowed("target")
    assert not session.released


def test_target_can_start_next_followup(preset):
    next_rule = copy.deepcopy(preset["rules"][0])
    next_rule.update(id="second", card="target", followUp={"cards": ["third"], "waitTurns": 2, "targetPolicy": "normal"})
    preset["rules"].append(next_rule)
    session = follow_rule(preset)
    session.submitted("source", 5)
    session.submitted("target", 5)
    assert session.active == "target"
    assert session.targets() == ["third"]
    assert "source" in session.released


def test_state_reader_two_fresh_observations_and_ambiguity():
    reader = BattleStateReader()
    frame = np.zeros((1280, 720, 3), np.uint8)
    with patch.object(reader, "pointer", side_effect=["全力", "全力", None, "全力"]), \
            patch.object(reader, "full_power", side_effect=[10, 10, 11, 11]):
        assert reader.read(Mock(), frame, {"stance", "fullPower"}) == {}
        # Controllers may reuse the ndarray buffer for a new capture.
        assert reader.read(Mock(), frame, {"stance", "fullPower"}) == {"stance": "全力", "fullPower": 10}
        assert reader.read(Mock(), frame.copy(), {"stance", "fullPower"}) == {}
        assert reader.read(Mock(), frame.copy(), {"stance", "fullPower"}) == {"fullPower": 11}
    assert reader.read(Mock(), np.zeros((720, 1280, 3), np.uint8), {"stance"}) == {}


def test_reader_disallows_multiple_pointers_and_ambiguous_numbers():
    reader = BattleStateReader()
    context = Mock()
    context.run_recognition.side_effect = [NS(hit=True), NS(hit=True), NS(hit=False)]
    assert reader.pointer(context, None) is None
    with patch("custom.battle_state.ocr", return_value=[NS(text="１２", score=.99), NS(text="13", score=.99)]):
        assert reader.full_power(context, None) is None
    with patch("custom.battle_state.ocr", return_value=[NS(text="１２", score=.99)]):
        assert reader.full_power(context, None) == 12


def test_normal_candidate_entry_applies_conditions_and_followup(preset):
    action = ProduceCardsAuto()
    action._rules_store = NS(preset=lambda: preset)
    frame = np.zeros((1280, 720, 3), np.uint8)
    candidates = [dict(box=[60 + i * 200, 880, 180, 240], label="cards", detector_score=.99) for i in range(3)]
    names = ["source", "target", "backup"]
    action._drinks = NS(turns=NS(read=lambda *_: 5), pending_card=None,
                        art=NS(read=lambda _, box, *args: NS(name=names[(box[0] - 60) // 200], mode="full")))
    rarity = NS(rarity="SR", confidence=.9, best_distance=.1, margin=.3, distances={}, signature=[.1]*3, channel_order="BGR")
    preset["rules"][0]["conditions"] = [[{"field": "stance", "value": "全力"}]]
    preset["rules"][0]["points"] = [[30, 100], [1, 100]]
    with patch("custom.produce.battle.classify_rarity", return_value=rarity), \
            patch.object(action, "_extra_action_status", return_value=("no", 0, [])), \
            patch.object(action, "_apply_card_name_fallback"), \
            patch.object(action._state_reader, "pointer", return_value="全力"):
        assert action._choose_card(Mock(), frame, candidates)["rule_name"] == "target"
        assert action._choose_card(Mock(), frame.copy(), candidates)["rule_name"] == "source"
        preset["rules"][0]["followUp"] = {"cards": ["backup", "target"], "waitTurns": 1, "targetPolicy": "normal"}
        action._followup.sync(preset)
        action._followup.submitted("source", 5)
        assert action._choose_card(Mock(), frame.copy(), candidates)["rule_name"] == "backup"


@pytest.fixture
def battle_submission(preset):
    follow_rule(preset, "reserveFirst")
    action = ProduceCardsAuto()
    action._rules_store = NS(preset=lambda: preset)
    action._drinks = NS(
        turns=NS(read=lambda *_: 5), pending_card=None, names=Mock(),
        needs_card_name=Mock(return_value=False), opening=False, inspection_streak=0,
    )
    frame = np.zeros((1280, 720, 3), np.uint8)
    box = [60, 820, 180, 240]
    action._selected_identity = (tuple(box), "source")
    context = Mock()
    context.tasker.stopping = False
    context.tasker.controller.post_screencap.return_value.wait.return_value.get.side_effect = lambda: frame.copy()
    with patch("custom.produce.shared._detect_disconnect_screen", return_value=False), \
            patch("custom.battle_drinks.CardNameReader.locate_raised", return_value=box), \
            patch("custom.battle_drinks.CardNameReader.same_selection", return_value=True), \
            patch.object(action, "_observe_selected_card", return_value=(frame, box, None)), \
            patch.object(action, "_drink_step", return_value=False), \
            patch.object(action, "_wait_until_playable", return_value=False), \
            patch("time.sleep"):
        yield action, context, box


@pytest.mark.parametrize("confirmed", [False, True])
def test_real_submission_path_advances_only_on_confirmation(battle_submission, confirmed):
    action, context, box = battle_submission
    with patch.object(action, "_confirm_card_submission", return_value=confirmed):
        assert action._play_a_card(context, box)
    assert action._followup.active == ("source" if confirmed else None)
    assert action._followup.last_remaining == (5 if confirmed else None)
    assert context.tasker.controller.post_click.call_count == 1


@pytest.mark.parametrize("raises", [False, True])
def test_confirmed_card_source_lives_only_through_move_wait(battle_submission, raises):
    action, context, box = battle_submission

    def wait(*args, **kwargs):
        assert action._hold_source == "source"
        if raises:
            raise RuntimeError("move failed")
        return False

    action._wait_until_playable.side_effect = wait
    with patch.object(action, "_confirm_card_submission", return_value=True):
        if raises:
            with pytest.raises(RuntimeError, match="move failed"):
                action._play_a_card(context, box)
        else:
            assert action._play_a_card(context, box)
    assert action._hold_source is None


def test_unknown_card_cannot_bypass_strict_new_rules(battle_submission):
    action, context, box = battle_submission
    action._selected_identity = None
    action._drinks.names.read_raised.return_value = None
    action._drinks.names.last_raised_candidate = None
    with patch.object(action, "_recover_card_selection", return_value=True) as recover, \
            patch.object(action, "_confirm_card_submission") as confirm:
        assert action._play_a_card(context, box)
    recover.assert_called_once()
    confirm.assert_not_called()
    context.tasker.controller.post_click.assert_not_called()
    assert action._followup.active is None


def test_stopping_clears_active_followup(battle_submission):
    action, context, box = battle_submission
    action._followup.sync(action._rule_preset())
    action._followup.submitted("source", 5)
    context.tasker.stopping = True
    assert action._play_a_card(context, box)
    assert action._followup.active is None
    assert action._followup.sources == {}


@pytest.fixture
def move_wait(preset):
    action = ProduceCardsAuto()
    action._rules_store = NS(preset=lambda: preset)
    preset.update(career="全力", hold={"cards": ["source"], "ordered": False})
    action._drinks = NS(move_source=None)
    frame = np.zeros((1280, 720, 3), np.uint8)
    context = Mock()
    context.tasker.stopping = False
    context.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value = frame
    context.run_recognition.side_effect = lambda node, *_: NS(hit=node == "ProduceRecognitionSkipRound")
    with patch("custom.produce.shared._detect_disconnect_screen", return_value=False), \
            patch("custom.battle_drinks.BattleDrinkSession._cancel_box", return_value=None), \
            patch("time.sleep"), patch.object(action, "_handle_move_cards") as legacy:
        yield action, context, preset, legacy
        legacy.assert_not_called()


@pytest.mark.parametrize("career,source,national,expected", [
    ("全力", None, False, "hold"),
    ("强气", None, False, "hold"),
    ("集中", "ほっと緑茶", False, "hold"),
    ("全力", "初星黒酢", True, "national"),
])
def test_wait_routes_hold_and_drink_sources(move_wait, career, source, national, expected):
    action, context, preset, _ = move_wait
    preset["career"] = career
    action._drinks.move_source = source
    if national:
        preset["rules"][0].update(card="国民的アイドル", comboTarget="target")
    with patch("custom.battle_move.is_move_page", side_effect=[True, False]), \
            patch("custom.battle_hold.is_hold_page", side_effect=[True, False]), \
            patch("custom.battle_hold.HoldPicker") as hold_picker, patch("custom.battle_move.MovePicker") as national_picker:
        assert action._wait_until_playable(context)
    assert hold_picker.call_count == int(expected == "hold")
    assert national_picker.call_count == int(expected == "national")
    if expected == "hold":
        assert hold_picker.call_args.kwargs["source"] == source


@pytest.mark.parametrize("failure", ["identity", "runtime", "disconnect", "stop"])
def test_hold_failure_never_enters_legacy_clicks(move_wait, failure):
    from custom.battle_move import MoveDisconnected

    action, context, _, _ = move_wait
    if failure == "identity":
        context.run_recognition.side_effect = lambda node, *_: NS(hit=node == "ProduceRecognitionChooseMoveCards")

    def run():
        if failure == "stop":
            context.tasker.stopping = True
            raise InterruptedError("stopped")
        if failure == "disconnect":
            raise MoveDisconnected("disconnected")
        raise RuntimeError("hold selection failed")

    with patch("custom.battle_move.is_move_page", return_value=failure != "identity"), \
            patch("custom.battle_hold.is_hold_page", return_value=failure != "identity"), \
            patch("custom.battle_hold.HoldPicker") as picker:
        picker.return_value.run.side_effect = run
        assert not action._wait_until_playable(context)
    if failure == "disconnect":
        assert action._disconnect_detected
    if failure in ("identity", "runtime"):
        assert action._drink_error
    context.tasker.controller.post_click.assert_not_called()

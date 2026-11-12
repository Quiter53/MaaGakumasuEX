"""Network identity and retry pacing from bounded OCR observations."""

import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
from custom.dialog_safety import connection_dialog
from custom.produce.lifecycle import ProduceRecoverConnectionAuto
from custom.reco.connection import ProduceConnectionDialog


# Public game text/coordinates only, with no log or account identifiers.
LOGGED_ROWS = [
    {"text": "通信エラ", "score": .99887, "box": [51, 953, 137, 37]},
    {"text": "通信中にエラーが発生しました", "score": .993269, "box": [56, 1039, 340, 28]},
    {"text": "一", "score": .6428, "box": [184, 962, 32, 21]},
]


@pytest.fixture
def context():
    ctx = Mock()
    ctx.tasker.stopping = False
    ctx.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value = np.zeros((1280, 720, 3), np.uint8)
    ctx.run_recognition.return_value = NS(hit=True)
    return ctx


@pytest.fixture(autouse=True)
def reset_retry_sequence():
    ProduceRecoverConnectionAuto._reset_network_retry_points()
    yield
    ProduceRecoverConnectionAuto._reset_network_retry_points()


def recognized(ctx, rows, kind="any"):
    with patch("custom.dialog_safety.ocr", return_value=[NS(**row) for row in rows]):
        return connection_dialog(ctx, None, kind)


@pytest.mark.parametrize("tail", [None, "一", "ー", "_"])
@pytest.mark.parametrize("kind", ["any", "network"])
def test_logged_title_and_body_accept_independent_of_split_tail(context, tail, kind):
    rows = copy.deepcopy(LOGGED_ROWS[:2])
    if tail:
        rows.append({**LOGGED_ROWS[2], "text": tail})
    assert recognized(context, rows, kind) == {"kind": "network", "box": [51, 953, 137, 37]}


def test_optional_japanese_period_and_whitespace(context):
    rows = copy.deepcopy(LOGGED_ROWS[:2])
    rows[0]["text"] = "通 信 エ ラ"
    rows[1]["text"] += "。"
    assert recognized(context, rows)["kind"] == "network"


@pytest.mark.parametrize("title", ["【通信エラー】", "通信エラ一", "通信工ラー", "通信\u200bエラ_"])
def test_restricted_title_candidates_handle_decorations_and_ocr_substitutions(context, title):
    rows = [{**LOGGED_ROWS[0], "text": title}, LOGGED_ROWS[1]]
    assert recognized(context, rows)["kind"] == "network"
    assert recognized(context, rows[:1]) is None


def test_full_body_allows_one_substitution_but_not_extra_words(context):
    rows = [LOGGED_ROWS[0], {**LOGGED_ROWS[1], "text": "通信中にエラーが発生しましだ。"}]
    assert recognized(context, rows)["kind"] == "network"
    rows[1]["text"] = "通信中にエラーが発生しましたか"
    assert recognized(context, rows) is None


def test_competing_title_classes_are_not_resolved_by_first_candidate(context):
    candidates = {"network": ["通信エラー"], "date": ["通信エラア"], "discard": ["廃棄確認"]}
    with patch("custom.dialog_safety.CONNECTION_TITLES", candidates):
        assert recognized(context, [{**LOGGED_ROWS[0], "text": "通信エラ一"}, LOGGED_ROWS[1]]) is None


def test_fuzzy_discard_vetoes_background_network_evidence(context):
    rows = [*LOGGED_ROWS, {"text": "【廃棄確認】", "score": .99, "box": [50, 950, 150, 35]}]
    assert recognized(context, rows) is None


def test_decorated_date_title_uses_date_message_and_geometry(context):
    rows = [{**LOGGED_ROWS[0], "text": "【日付変更】"}, {**LOGGED_ROWS[1], "text": "日付が変わりました。"}]
    assert recognized(context, rows)["kind"] == "date"
    assert recognized(context, rows, "network") is None


@pytest.mark.parametrize("field,text", [
    (0, "通信エ"), (0, "エラー"), (0, "通信工ラ"), (0, "通信エラ確認"),
    (0, "日付変更"), (0, "廃棄確認"),
    (1, "通信中にエラー"), (1, "通信中にエラーが発生していません"),
    (1, "日付が変わりました。"), (1, "通信中にエラーが発生しましたか"),
])
def test_partial_identity_requires_exact_network_words(context, field, text):
    rows = copy.deepcopy(LOGGED_ROWS)
    rows[field]["text"] = text
    assert recognized(context, rows) is None


@pytest.mark.parametrize("rows", [LOGGED_ROWS[:1], LOGGED_ROWS[1:], [], LOGGED_ROWS[:2] + [LOGGED_ROWS[0]]])
def test_missing_or_ambiguous_evidence_never_identifies(context, rows):
    assert recognized(context, rows) is None


@pytest.mark.parametrize("field,score,accepted", [(0, .949, False), (1, .949, False), (0, .95, True), (1, .95, True)])
def test_partial_identity_has_its_own_high_confidence_threshold(context, field, score, accepted):
    rows = copy.deepcopy(LOGGED_ROWS)
    rows[field]["score"] = score
    assert (recognized(context, rows) is not None) is accepted


@pytest.mark.parametrize("field,box", [
    (0, [51, 750, 137, 37]), (0, [51, 1080, 137, 37]),
    (1, [56, 900, 340, 28]), (1, [56, 995, 340, 28]),
    (1, [56, 1120, 340, 28]), (1, [400, 1039, 280, 28]),
    (1, [56, 1039, 100, 28]), (1, [56, 1039, 340, 0]), (1, None),
])
def test_title_and_body_must_share_the_lower_dialog_layout(context, field, box):
    rows = copy.deepcopy(LOGGED_ROWS)
    rows[field]["box"] = box
    assert recognized(context, rows) is None


@pytest.mark.parametrize("title", ["廃棄確認", "废弃确认", "日付変更", "日期變更"])
def test_foreground_discard_or_date_vetoes_truncated_background_network(context, title):
    rows = [*LOGGED_ROWS, {"text": title, "score": .99, "box": [50, 950, 150, 35]}]
    assert recognized(context, rows, "network") is None
    assert recognized(context, LOGGED_ROWS, "date") is None


def test_template_gate_remains_required(context):
    context.run_recognition.return_value = NS(hit=False)
    with patch("custom.dialog_safety.ocr") as read:
        assert connection_dialog(context, None) is None
    read.assert_not_called()


@pytest.mark.parametrize("text", ["通信エラー", "通信错误", "通信錯誤", "网络错误", "網絡錯誤"])
def test_original_exact_titles_keep_point_eight_threshold(context, text):
    assert recognized(context, [{"text": text, "score": .8, "box": [51, 953, 137, 37]}])["kind"] == "network"
    assert recognized(context, [{"text": text, "score": .799, "box": [51, 953, 137, 37]}]) is None


def test_shared_candidate_and_text_identity_flow_through_both_recovery_stages(context):
    image = context.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value

    def recognition(node, frame, pipeline_override=None):
        assert frame is image
        assert node == "ProduceConnectionDialogTemplate"
        override = (pipeline_override or {}).get(node, {})
        # Synthetic boundary case: a dedicated candidate misses .82 while
        # the shared candidate passes .85. These template identities/scores
        # are not a reconstruction of the incident; only OCR rows are logged.
        score = .8161 if override.get("template") == ["produce/disconnect_error.png"] else .957656
        return NS(hit=score >= override.get("threshold", .85))

    context.run_recognition.side_effect = recognition
    with patch("custom.dialog_safety.ocr", return_value=[NS(**row) for row in LOGGED_ROWS]):
        assert connection_dialog(context, image, "network") is None
        assert ProduceConnectionDialog().analyze(context, NS(image=image, custom_recognition_param=json.dumps({"kind": "any"})))
        assert ProduceRecoverConnectionAuto().run(context, NS())
    context.tasker.controller.post_click.assert_called_once_with(515, 1160)


@pytest.mark.parametrize("rows,expected", [
    ([{"text": "【廃棄確認】", "score": .99, "box": [50, 950, 150, 35]}, *LOGGED_ROWS], []),
    ([{"text": "Pドリンク詳細", "score": .99, "box": [50, 950, 150, 35]}], []),
    ([], []),
    ([{**LOGGED_ROWS[0], "text": "【日付変更】"}, {**LOGGED_ROWS[1], "text": "日付が変わりました。"}], [(360, 1170)]),
])
def test_action_rechecks_current_dialog_and_never_clicks_discard_yes(context, rows, expected):
    with patch("custom.dialog_safety.ocr", return_value=[NS(**row) for row in rows]):
        assert ProduceRecoverConnectionAuto().run(context, NS())
    assert [call.args for call in context.tasker.controller.post_click.call_args_list] == expected


@pytest.mark.parametrize("interval", [11.5, 22])
def test_real_eleven_second_polling_advances_only_the_six_safe_points(context, interval):
    action = ProduceRecoverConnectionAuto()
    with patch("custom.produce.lifecycle.time.monotonic", side_effect=[100 + interval * i for i in range(9)]), \
            patch("custom.produce.lifecycle.connection_dialog", return_value={"kind": "network"}):
        for _ in range(9):
            assert action.run(context, NS())
    expected = list(action.NETWORK_RETRY_POINTS) + list(action.NETWORK_RETRY_POINTS[:3])
    assert [call.args for call in context.tasker.controller.post_click.call_args_list] == expected
    assert set(expected) == {(515, 1160), (570, 1160), (625, 1160), (480, 1160), (570, 1125), (625, 1125)}


def test_long_idle_gap_starts_at_standard_point(context):
    action = ProduceRecoverConnectionAuto()
    with patch("custom.produce.lifecycle.time.monotonic", side_effect=[100, 111.5, 232]), \
            patch("custom.produce.lifecycle.connection_dialog", return_value={"kind": "network"}):
        for _ in range(3):
            assert action.run(context, NS())
    assert [call.args for call in context.tasker.controller.post_click.call_args_list] == [(515, 1160), (570, 1160), (515, 1160)]


@pytest.mark.parametrize("date", [False, True])
def test_changed_dialog_resets_sequence_without_another_retry_click(context, date):
    action = ProduceRecoverConnectionAuto()
    observations = [{"kind": "network"}, {"kind": "date"} if date else None, {"kind": "network"}]
    with patch("custom.produce.lifecycle.connection_dialog", side_effect=observations):
        for _ in range(3):
            assert action.run(context, NS())
    expected = [(515, 1160), (360, 1170), (515, 1160)] if date else [(515, 1160), (515, 1160)]
    assert [call.args for call in context.tasker.controller.post_click.call_args_list] == expected


@pytest.mark.parametrize("stage", ["before_capture", "during_recognition"])
def test_stop_prevents_retry_click_and_clears_sequence(context, stage):
    action = ProduceRecoverConnectionAuto()
    action._next_network_retry_point()
    if stage == "before_capture":
        context.tasker.stopping = True
    else:
        def stop(*_args, **_kwargs):
            context.tasker.stopping = True
            return {"kind": "network"}
    with patch("custom.produce.lifecycle.connection_dialog", side_effect=stop if stage != "before_capture" else None):
        assert action.run(context, NS())
    context.tasker.controller.post_click.assert_not_called()
    assert action._network_retry_index == 0

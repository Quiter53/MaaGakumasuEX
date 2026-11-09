"""Idol/title row association using public OCR text and coordinates only."""

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
# Load the recognition module without custom/__init__ importing all actions.
spec = importlib.util.spec_from_file_location("idol_identity_recognition", ROOT / "agent/custom/reco/produce.py")
module = importlib.util.module_from_spec(spec)
with patch("maa.agent.agent_server.AgentServer.custom_recognition", side_effect=lambda _name: lambda cls: cls):
    spec.loader.exec_module(module)
ProduceChooseIdolAuto = module.ProduceChooseIdolAuto

# OCR output of the reported missed-banner layout. No account/log data.
MISSED_BANNER_ROWS = [
    {"box": [447, 98, 250, 29], "score": 0.998730, "text": "[サンフェーデッド]"},
    {"box": [532, 130, 165, 31], "score": 0.517197, "text": "筱澤広"},
]


def analyze(rows, *, idol="篠澤広", song="サンフェーデッド", true_end=False):
    context = Mock()
    image = object()

    def recognize(node, observed, pipeline_override):
        assert observed is image
        if node == "ProduceChooseIdolTrueEnd":
            return NS(hit=true_end, all_results=[])
        x, y, w, h = pipeline_override[node]["roi"]
        visible = [NS(**row) for row in rows if x <= row["box"][0] and y <= row["box"][1]
                   and row["box"][0] + row["box"][2] <= x + w and row["box"][1] + row["box"][3] <= y + h]
        return NS(hit=bool(visible), all_results=visible)

    context.run_recognition.side_effect = recognize
    result = ProduceChooseIdolAuto().analyze(context, NS(
        image=image, custom_recognition_param=json.dumps({"idol_name": idol, "song_name": song}),
    ))
    context.tasker.controller.post_click.assert_not_called()
    context.tasker.controller.post_screencap.assert_not_called()
    return result.box is not None, context


@pytest.mark.parametrize("true_end", [False, True])
def test_logged_shifted_rows_match_with_or_without_banner(true_end):
    accepted, context = analyze(MISSED_BANNER_ROWS, true_end=true_end)
    assert accepted
    assert context.run_recognition.call_count == 3


@pytest.mark.parametrize("true_end", [False, True])
def test_normal_layout_survives_wrong_banner_detection(true_end):
    rows = [{**row, "box": [row["box"][0], row["box"][1] - 30, *row["box"][2:]]} for row in MISSED_BANNER_ROWS]
    accepted, context = analyze(rows, true_end=true_end)
    assert accepted
    assert context.run_recognition.call_count == (5 if true_end else 3)


@pytest.mark.parametrize("idol,song", [
    ("有村麻央", "サンフェーデッド"),
    ("篠澤広", "ハッピーミルフィーユ"),
    ("篠澤広", "コントラスト"),
    ("篠澤広", "標"),
    ("", "サンフェーデッド"),
    ("篠澤広", ""),
])
def test_both_identity_fields_must_match(idol, song):
    assert not analyze(MISSED_BANNER_ROWS, idol=idol, song=song)[0]


@pytest.mark.parametrize("rows", [
    [], MISSED_BANNER_ROWS[:1], MISSED_BANNER_ROWS[1:],
    [{"box": [447, 98, 250, 64], "score": .99, "text": "[サンフェーデッド]筱澤広"}],
    [{**MISSED_BANNER_ROWS[0], "box": [447, 130, 250, 29]}, {**MISSED_BANNER_ROWS[1], "box": [532, 98, 165, 31]}],
    [{**MISSED_BANNER_ROWS[0], "box": [10, 98, 250, 29]}, MISSED_BANNER_ROWS[1]],
])
def test_missing_merged_swapped_and_outside_rows_fail(rows):
    assert not analyze(rows)[0]


def test_horizontal_fragments_form_lines_without_joining_name_and_title():
    rows = [
        {"text": "広", "box": [642, 130, 55, 31]},
        {"text": "デッド]", "box": [587, 98, 110, 29]},
        {"text": "筱澤", "box": [532, 130, 110, 31]},
        {"text": "[サンフェー", "box": [447, 98, 140, 29]},
    ]
    assert analyze(rows)[0]


@pytest.mark.parametrize("wanted,observed,accepted", [
    ("標", "[標]", True), ("標", "[道標]", False),
    ("見て", "[見て！]", True), ("見て", "[見てて]", False),
    ("See", "[Seen]", False), ("め", "[ため]", False),
    ("36℃ U･B･U", "[３６°C Ｕ・Ｂ・Ｕ]", True),
    ("篠", "[筱]", False), ("!!!", "[!!!]", False),
])
def test_short_titles_require_complete_normalized_line(wanted, observed, accepted):
    rows = [{**MISSED_BANNER_ROWS[0], "text": observed}, MISSED_BANNER_ROWS[1]]
    assert analyze(rows, song=wanted)[0] is accepted


def test_name_only_glyph_alias_does_not_admit_another_name_error():
    rows = [MISSED_BANNER_ROWS[0], {**MISSED_BANNER_ROWS[1], "text": "筱澤应"}]
    assert not analyze(rows)[0]
    assert ProduceChooseIdolAuto.similarity_ratio("筱", "篠") == 0


@pytest.mark.parametrize("name_rows,song_rows,accepted", [
    (["姫崎莉波"], ["３６°C Ｕ・Ｂ・Ｕ"], True),
    (["姫崎莉波"], ["Fighting My Way"], False),
    (["姫崎莉波"], [], False),
    (["姫崎莉波", "其他行"], ["36℃ U･B･U"], False),
])
def test_legacy_results_without_boxes_keep_separate_field_contract(name_rows, song_rows, accepted):
    context = Mock()

    def recognize(node, *_args, **_kwargs):
        values = {"ProduceChooseIdolName": name_rows, "ProduceChooseIdolSong": song_rows}.get(node, [])
        return NS(hit=bool(values), all_results=[NS(text=text) for text in values])

    context.run_recognition.side_effect = recognize
    result = ProduceChooseIdolAuto().analyze(context, NS(image=None, custom_recognition_param=json.dumps({
        "idol_name": "姫崎莉波", "song_name": "36℃ U･B･U",
    })))
    assert (result.box is not None) is accepted

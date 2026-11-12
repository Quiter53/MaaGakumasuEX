"""Decorated numeric OCR keeps arithmetic, geometry and uncertainty intact."""
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import numpy as np
import pytest

from custom.battle_drinks import TurnReader, parse_turn
from custom.battle_state import BattleStateReader
from custom.cultivation_hud import numeric_rows_text, parse_health_rows, read_health, read_points
from custom.interval_recovery import numeric_one, read_fraction


def row(text, score=.99, box=None):
    return NS(text=text, score=score, box=box)


def context(rows):
    result = Mock()
    result.run_recognition.return_value = NS(hit=True, filtered_results=rows)
    return result


@pytest.mark.parametrize("text, expected", [
    ("❤♡♥３４ ／ ４０", (34, 40)), ("【♡０/４０】", (0, 40)),
    ("💗 34/40 ☆", (34, 40)), ("❤\ufe0f34/40", (34, 40)),
    ("❤❤", None), ("34", None), ("34/0", None), ("41/40", None),
    ("134/140", None), ("34/40 1", None), ("3 4/40", None),
    ("回復34/40", None), ("34/40+2", None), ("34/40/40", None),
    ("3❤4/40", None), ("34%/40", None),
])
def test_health_decoration_preserves_only_one_valid_fraction(text, expected):
    assert read_health(context([row(text)]), np.zeros((1280, 720, 3), np.uint8)) == expected


def test_separate_decoration_can_be_low_score_but_digits_cannot():
    assert parse_health_rows([row("♡", .5), row("34/40")]) == (34, 40)
    assert parse_health_rows([row("♡"), row("34/40", .89)]) is None


def test_fraction_fragments_need_one_baseline_and_tight_geometry():
    parts = [row("3", box=[330, 60, 10, 25]), row("4/", box=[342, 60, 20, 25]),
             row("4", box=[364, 60, 10, 25]), row("0", box=[376, 60, 10, 25])]
    assert parse_health_rows(list(reversed(parts))) == (34, 40)
    for changed in ([row("34/40"), row("34/40")], [row("34"), row("/40")],
                    [parts[0], row("4/", box=[355, 60, 20, 25]), *parts[2:]],
                    [*parts[:3], row("0", box=[376, 90, 10, 25])]):
        assert parse_health_rows(changed) is None


@pytest.mark.parametrize("text, expected", [("☆１，２３４☆", 1234), ("( 0 )", 0), ("♡ 330", 330),
    ("1 234", None), ("12 34", None), ("12\u200b34", None), ("12\u206034", None), ("1,23", None), ("12/34", None),
    ("+330", None), ("-330", None), ("330%", None), ("P330", 330), ("330.0", None),
    ("Ｐ ２９３", 293), ("p293", 293), ("HP293", None), ("P293 40", None), ("P-1", None)])
def test_points_keep_one_nonnegative_balance(text, expected):
    assert read_points(context([row(text)]), np.zeros((1280, 720, 3), np.uint8)) == expected


@pytest.mark.parametrize("rows", [[row("1"), row("2")], [row("12"), row("34")],
    [row("1", box=[20, 60, 10, 30]), row("2", box=[32, 60, 10, 30])]])
def test_bare_integer_fragments_are_not_concatenated(rows):
    assert numeric_rows_text(rows) is None
    assert parse_turn(rows) is None


@pytest.mark.parametrize("text, expected", [("☆１２☆", 12), ("( 9 )", 9), ("９９", 99),
    ("0", None), ("100", None), ("01", None), ("1 2", None), ("1ターン", None), ("+1", None)])
def test_turn_bounds_and_unknown(text, expected):
    assert parse_turn([row(text)]) == expected


def test_decorated_turn_still_needs_two_reads_and_existing_same_roi_fallback():
    reader = TurnReader()
    reader._anchor = True
    frame = np.zeros((1280, 720, 3), np.uint8)
    ctx = context([row("☆１２☆")])
    with patch("custom.battle_drinks.ocr", return_value=[row("1"), row("2")]) as ocr:
        assert reader.read(ctx, frame) is None
        assert reader.read(ctx, frame) == 12
    assert ocr.call_args.args[2:] == (reader.ROI, [".*"])
    request = ctx.run_recognition.call_args.kwargs["pipeline_override"]["ProduceRecognitionPoint"]
    assert request["roi"] == reader.ROI and request["only_rec"] is True


@pytest.mark.parametrize("text, expected", [("☆９９９☆", 999), ("0", 0), ("1000", None), ("12 3", None), ("+12", None)])
def test_full_power_uses_existing_bounds_and_confidence(text, expected):
    reader = BattleStateReader()
    with patch("custom.battle_state.ocr", return_value=[row(text)]) as ocr:
        assert reader.full_power(Mock(), None) == expected
        assert ocr.call_args.args[2:] == (reader.POWER_ROI, [".*"])
    with patch("custom.battle_state.ocr", return_value=[row("☆12☆", .84)]):
        assert reader.full_power(Mock(), None) is None


def test_interval_reuses_fraction_parser_without_changing_crop_or_fallback():
    with patch("custom.interval_recovery.fraction_roi", return_value=[329, 846, 151, 70]), \
         patch("custom.interval_recovery.rows", return_value=[row("♡３４／４０")]) as rows:
        assert read_fraction(Mock(), None, [275, 846, 205, 70]) == (34, 40)
    assert rows.call_args.args[2] == [329, 846, 151, 70]
    assert rows.call_args.kwargs == {"line": True}


@pytest.mark.parametrize("text, expected", [("☆120☆", "120"), ("12 0", None), ("120/50", None), ("P120", None)])
def test_interval_points_do_not_compact_two_numbers(text, expected):
    with patch("custom.interval_recovery.rows", return_value=[row(text)]):
        _, match = numeric_one(Mock(), None, [285, 1003, 115, 70], r"([0-9]{1,4})")
    assert (match[1] if match else None) == expected

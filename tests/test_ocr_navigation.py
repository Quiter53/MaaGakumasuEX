"""OCR text post-processing preserves semantic groups and original button boxes."""

import re
import sys
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
from custom import customization as c
from custom.hif import outing_header, read_hif_supply_receipt, read_schedule
from custom.shop import read_consultation_desk_entry


def row(text, box=(200, 900, 130, 35), score=.99):
    return NS(text=text, box=list(box), score=score)


def context_for(observations):
    """Apply the node's expected filter, so premature exact regexes are tested."""
    context = Mock()

    def recognize(node, _image, pipeline_override):
        params = pipeline_override[node]
        rows = [r for r in observations[tuple(params["roi"])]
                if any(re.search(pattern, r.text) for pattern in params["expected"])]
        return NS(hit=bool(rows), filtered_results=rows)

    context.run_recognition.side_effect = recognize
    return context


FRAME = np.zeros((1280, 720, 3), np.uint8)


def test_schedule_cleans_text_then_ranks_activity_groups():
    outing = row("お\u200bでかけけ", (100, 900, 130, 35))
    supply = row("活動 支給給", (390, 900, 130, 35))
    context = context_for({(0, 800, 720, 350): [outing, supply, row("休想")]})
    assert read_schedule(context, FRAME) == {"outing": outing.box, "supply": supply.box}


@pytest.mark.parametrize("observations", [
    [row("お出かけ"), row("お出かけけ", (400, 900, 130, 35))],
    [row("差し入れ選択時、体力回復")],
    [row("休"), row("差し人れ")],
    [row("活動支給給", score=.84)],
    [row("活動支給給", (200, 300, 130, 35))],
])
def test_schedule_does_not_guess_short_labels_prose_duplicates_or_off_row(observations):
    assert read_schedule(context_for({(0, 800, 720, 350): observations}), FRAME) == {}


def test_outing_heading_preserves_unique_observation_and_short_exact_labels():
    title = row("お\u200bでかけけ", (30, 40, 150, 40))
    context = context_for({(0, 0, 240, 180): [title]})
    assert outing_header(context, FRAME) == title.box
    for texts in (("外山",), ("お出かけ", "おでかけけ"), ("授業",)):
        context = context_for({(0, 0, 240, 180): [row(text, title.box) for text in texts]})
        assert outing_header(context, FRAME) is None


def test_supply_requires_fuzzy_header_and_unique_receive_group():
    title = row("差し入れれ", (69, 35, 85, 28))
    receive = row("受け取るる", (298, 1075, 119, 34))
    context = context_for({(0, 0, 240, 110): [title], (200, 1010, 350, 170): [receive]})
    assert read_hif_supply_receipt(context, FRAME) == receive.box
    for buttons in ([row("受け取らない", receive.box)], [receive, row("受け取る", receive.box)],
                    [row("受け取るる", receive.box, .84)], [row("受け取るる", (298, 600, 119, 34))]):
        context = context_for({(0, 0, 240, 110): [title], (200, 1010, 350, 170): buttons})
        assert read_hif_supply_receipt(context, FRAME) is None


def test_customization_same_screen_controls_are_not_merged_by_similarity():
    labels = ["一覧戻る", "もどる", "×終\u200b了", "カスタマイズズ", "実行する", "カスタマイズメ二ュー", "カスタマイズ上限"]
    observations = [row(label, (20 + index * 80, 1140, 70, 30)) for index, label in enumerate(labels)]
    for index, wanted in enumerate(("一覧に戻る", "もどる", "終了", "カスタマイズ", "実行する", "カスタマイズメニュー")):
        assert c.unique_control(observations, wanted) == observations[index].box
    assert c.unique_control(observations[5:], "カスタマイズ") is None
    assert c.unique_control([row("カスタマイズメ")], "カスタマイズ") is None
    assert c.unique_control([row("実行す")], "実行する") is None
    assert c.unique_control([row("終子")], "終了") is None
    assert c.unique_control([row("一覧戻る"), row("一覧に戻る")], "一覧に戻る") is None


def test_customization_menu_uses_cleaned_controls_but_keeps_price_and_name_requirements():
    bottom = [row("一覧戻る"), row("実行する", (460, 1140, 120, 35)), row("合計あと？回")]
    title = [row("未知のカード+")]
    menu = [row("カスタマイズメ二ュー")]
    with patch.object(c, "rows", side_effect=[bottom, title, menu]):
        page = c.read_page(Mock(), FRAME)
    assert page["stage"] == "menu" and page["back"] == bottom[0].box
    assert page["execute"] == bottom[1].box
    assert page["name"] is None and page["total"] is None and page["options"] == []


def test_consultation_cleans_header_and_fuzzes_only_exchange_group():
    header = row("相\u200b 談", (50, 70, 60, 30))
    exchange = row("Ｐポイント交歓", (250, 600, 220, 60))
    cancel = row("キャンセル", (400, 700, 100, 40))
    context = context_for({(30, 30, 660, 910): [header, exchange, cancel]})
    assert read_consultation_desk_entry(context, FRAME) == exchange.box
    for observations in ([exchange], [row("相淡", header.box), exchange],
                         [header, row("交換しない", exchange.box)],
                         [header, exchange, row("交換する", exchange.box)],
                         [header, row(exchange.text, exchange.box, .89)],
                         [header, row(exchange.text, (250, 1060, 220, 60))]):
        context = context_for({(30, 30, 660, 910): observations})
        assert read_consultation_desk_entry(context, FRAME) is None

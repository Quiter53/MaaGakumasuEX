"""Regression for the 2026-10-17 00:32 HIF resume timeout.

Rows below are OCR observations from maafw.log, not synthetic image replay.
"""
import sys
import json
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

from custom.hif import read_hif_resume, read_hif_supply_receipt
from custom.reco.hif import ProduceHIFSupplyReceipt


def row(text, box, score=.9999):
    return SimpleNamespace(text=text, box=box, score=score)


HEADER = row("差し入れ", [69, 35, 85, 28], .999942)
BUTTON = row("受け取る", [298, 1075, 119, 34], .999860)


def context_for(headers, buttons):
    context = Mock()
    context.run_recognition.side_effect = [
        SimpleNamespace(hit=bool(rows), filtered_results=rows) for rows in (headers, buttons)
    ]
    return context


def test_logged_receipt_returns_the_receive_button_not_the_header():
    context = context_for([HEADER], [BUTTON])
    image = np.zeros((1280, 720, 3), dtype=np.uint8)
    result = ProduceHIFSupplyReceipt().analyze(context, SimpleNamespace(image=image))
    assert list(result.box) == BUTTON.box
    assert result.detail == {"stage": "supply_receipt"}
    # Recognition itself never consumes the gift or bypasses pipeline handling.
    context.tasker.controller.post_click.assert_not_called()
    assert context.run_recognition.call_args_list[0].kwargs["pipeline_override"]["ProduceRecognitionPoint"]["roi"] == [0, 0, 240, 110]


@pytest.mark.parametrize("headers,buttons", [
    ([], [BUTTON]),
    ([row("ショップ", HEADER.box)], [BUTTON]),
    ([row("差し入れ", HEADER.box, .7)], [BUTTON]),
    ([HEADER], []),
    ([HEADER], [row("受け取らない", BUTTON.box)]),
    ([HEADER], [row("受け取る", BUTTON.box, .7)]),
    ([HEADER], [row("受け取る", [298, 600, 119, 34])]),
    ([HEADER], [BUTTON, BUTTON]),
])
def test_requires_unambiguous_title_and_receive_control(headers, buttons):
    assert read_hif_supply_receipt(context_for(headers, buttons), np.zeros((1280, 720, 3), np.uint8)) is None


def test_landscape_is_rejected_before_ocr():
    context = Mock()
    assert read_hif_supply_receipt(context, np.zeros((720, 1280, 3), np.uint8)) is None
    context.run_recognition.assert_not_called()


def test_resume_and_active_loop_share_receipt_node_before_generic_buttons():
    data = json.loads((ROOT / "assets/resource/base/pipeline/ProduceHIF.json").read_text("utf8"))
    name = "ProduceHIFSupplyReceiptFlag"
    context = Mock()
    context.run_recognition.side_effect = lambda node, image: SimpleNamespace(
        hit=node == name, best_result=SimpleNamespace(box=BUTTON.box))
    assert read_hif_resume(context, None) == BUTTON.box
    route = data["ProduceEntryHIF"]["next"]
    assert route.index("[JumpBack]" + name) < route.index("[JumpBack]ProduceHIFButton")
    assert data[name]["action"]["type"] == "Click"
    assert data[name]["recognition"]["param"]["custom_recognition"] == "ProduceHIFSupplyReceipt"
    # The activity tile still belongs to the configured day-three strategy.
    assert data["ProduceHIFSupplyFlag"]["action"]["param"]["custom_action"] == "ProduceHIFChooseSupplyAuto"

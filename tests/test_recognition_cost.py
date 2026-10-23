"""识别开销优化的回归测试：廉价前置检查不改变命中结果，只省掉必然失败的重识别。"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
from custom.reco import settlement_dialogs as dialogs
from custom.reco.produce import ProduceCardsFlagAuto

IMAGES = ROOT / "assets/resource"


def frame_with(template, x, y):
    frame = np.zeros((1280, 720, 3), np.uint8)
    art = np.asarray(Image.open(template).convert("RGB"))[:, :, ::-1]
    frame[y : y + art.shape[0], x : x + art.shape[1]] = art[: 1280 - y, : 720 - x]
    return frame


class ModalHeaderGateTest(unittest.TestCase):
    def test_real_dialog_headers_pass_the_color_gate(self):
        # 日服早送り确认、通信错误、日服与汉化版日期变更的标题栏都应通过。
        for path, x, y in (
            (IMAGES / "base/image/produce/skip_chat_confirm_flag.png", 34, 900),
            (IMAGES / "base/image/produce/disconnect_error.png", 140, 820),
            (IMAGES / "base/image/date_change.png", 40, 800),
            (IMAGES / "zh_CN/image/date_change.png", 40, 800),
        ):
            with self.subTest(path=path.name):
                self.assertTrue(dialogs.modal_header_visible(frame_with(path, x, y), dialogs.STORY_HEADING_ROI))

    def test_frames_without_orange_header_skip_ocr(self):
        frame = np.full((1280, 720, 3), 235, np.uint8)
        self.assertFalse(dialogs.modal_header_visible(frame, dialogs.STORY_HEADING_ROI))
        ctx = Mock()
        ctx.run_recognition.return_value = None
        self.assertIsNone(dialogs.story_dialog(ctx, frame))
        # 只跑了一次模板识别，没有再跑整块 OCR。
        self.assertEqual(["ProduceStoryDialogTemplate"], [call.args[0] for call in ctx.run_recognition.call_args_list])

    def test_unknown_frames_keep_the_original_ocr_path(self):
        self.assertTrue(dialogs.modal_header_visible(None, dialogs.STORY_HEADING_ROI))


class CardsFlagOrderTest(unittest.TestCase):
    def run_flag(self, health_hit, cards_hit=True):
        ctx = Mock()
        calls = []

        def recognize(name, image, *args, **kwargs):
            calls.append(name)
            hit = health_hit if name == "ProduceRecognitionHealthFlag" else cards_hit
            return NS(hit=hit)

        ctx.run_recognition.side_effect = recognize
        result = ProduceCardsFlagAuto().analyze(ctx, NS(image=np.zeros((1280, 720, 3), np.uint8)))
        return result.box, calls

    def test_detector_runs_only_after_health_bar_hit(self):
        box, calls = self.run_flag(health_hit=False)
        self.assertIsNone(box)
        self.assertEqual(["ProduceRecognitionHealthFlag"], calls)
        box, calls = self.run_flag(health_hit=True)
        self.assertIsNotNone(box)
        self.assertEqual(["ProduceRecognitionHealthFlag", "ProduceRecognitionCards"], calls)
        box, _ = self.run_flag(health_hit=True, cards_hit=False)
        self.assertIsNone(box)


if __name__ == "__main__":
    unittest.main()

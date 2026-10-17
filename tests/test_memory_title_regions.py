"""Regression for a local title being vetoed by downsampled full-frame OCR."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent'))
from custom.produce.memory import modes as m
from custom.produce.memory.actions import ProduceMemoryRecognition


def row(text, box, score=.99):
    return dict(text=text, box=box, score=score)


class MemoryTitleRegionTests(unittest.TestCase):
    def setUp(self):
        self.frame = np.zeros((1280, 720, 3), dtype=np.uint8)
        self.frame[410:475] = 230
        self.roi = [0, 390, 720, 100]
        # Actual 2026-10-17 09:06:19-20 observations: the same image was
        # passed to local and full-frame recognition by analyze().
        self.local = [row('メモリー変換', [44, 423, 130, 38], .993603)]
        self.full = [
            row('メモリ一変換', [47, 429, 122, 24], .897416),
            row('プロデュース', [165, 608, 120, 24]),
            row('冒険心+', [198, 709, 107, 37]),
            row('あと1回可能', [22, 1104, 119, 22]),
            row('再生成', [87, 1142, 82, 34]),
            row('次へ', [324, 1138, 67, 42]),
        ]

    def recognize(self, context, image, roi=None, expected=None):
        self.assertIs(image, self.frame)
        if roi is None:
            return list(self.full)
        if roi == self.roi:
            return list(self.local)
        x, y, w, h = roi
        return [r for r in self.full if x <= r['box'][0] < x+w and y <= r['box'][1] < y+h]

    def test_same_frame_local_title_recovers_summary_and_preserves_body(self):
        self.assertIsNone(m.screen_kind(self.full))
        with patch.object(m, 'ocr', side_effect=self.recognize):
            texts = m.screen_ocr(Mock(), self.frame)
        self.assertEqual(m.screen_kind(texts), 'summary')
        self.assertCountEqual(texts, self.full[1:] + self.local)
        self.assertEqual(m.button(texts, '再生成'), [87, 1142, 82, 34])
        self.assertEqual(self.full[0]['text'], 'メモリ一変換')

    def test_entry_reuses_the_local_observation_instead_of_vetoing_it(self):
        with patch.object(m, 'SESSION', NS(enabled=True)), \
             patch.object(m, 'ocr', side_effect=self.recognize) as recognize:
            result = ProduceMemoryRecognition().analyze(Mock(), NS(image=self.frame))
        self.assertIsNotNone(result)
        self.assertEqual(recognize.call_count, 4)

    def test_every_reader_capture_uses_current_frame_title(self):
        context = Mock()
        context.tasker.stopping = False
        context.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value = self.frame
        reader = object.__new__(m.MemoryReader)
        reader.context, reader.controller = context, context.tasker.controller
        with patch.object(m, 'ocr', side_effect=self.recognize):
            _, texts = reader.capture()
            self.assertEqual(m.screen_kind(texts), 'summary')
            self.full, self.local = [], []
            _, texts = reader.capture()
            self.assertIsNone(m.screen_kind(texts))

    def test_title_alone_does_not_prove_a_settlement(self):
        self.full = []
        with patch.object(m, 'ocr', side_effect=self.recognize):
            self.assertIsNone(m.screen_kind(m.screen_ocr(Mock(), self.frame)))

    def test_no_color_panel_does_not_add_local_ocr_to_battle(self):
        self.frame[:] = 0
        self.full = [row('レッスン', [100, 200, 100, 30])]
        with patch.object(m, 'ocr', side_effect=self.recognize) as recognize:
            self.assertEqual(m.screen_ocr(Mock(), self.frame), self.full)
        self.assertEqual(recognize.call_count, 1)

    def test_saved_detail_without_footer_is_not_summary(self):
        self.full = self.full[:3]
        with patch.object(m, 'ocr', side_effect=self.recognize):
            self.assertEqual(m.screen_kind(m.screen_ocr(Mock(), self.frame)), 'detail')


if __name__ == '__main__':
    unittest.main()

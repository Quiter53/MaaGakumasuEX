"""HIF's daily shop entry is distinct from the shop-interior template."""

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
from custom.hif import read_hif_shop_entry
from custom.action.produce import ProduceHIFEnterShopAuto


def frame():
    image = np.zeros((1279, 720, 3), np.uint8)
    image[933:1113, 285:435] = 250
    image[953:1043, 305:415] = (145, 225, 60)
    return image


def recognition(text="相談", box=(331, 1078, 58, 30), hit=True):
    return SimpleNamespace(hit=hit, filtered_results=[SimpleNamespace(text=text, box=box)])


class Done:
    def __init__(self, image=None): self.image = image
    def wait(self): return self
    def get(self): return self.image


class Controller:
    def __init__(self, required_clicks=1, advance=True):
        self.required_clicks, self.advance = required_clicks, advance
        self.clicks = []
        self.done = False

    def post_screencap(self):
        return Done(np.zeros((1279, 720, 3), np.uint8) if self.done else frame())

    def post_click(self, x, y):
        self.clicks.append((x, y))
        if self.advance and len(self.clicks) >= self.required_clicks:
            self.done = True
        return Done()


class HIFShopEntryTest(unittest.TestCase):
    def context(self, controller=None):
        context = Mock()
        context.run_recognition.return_value = recognition()
        context.tasker = SimpleNamespace(controller=controller, stopping=False)
        return context

    def run_action(self, controller):
        with patch("custom.action.produce.time.sleep"), patch("custom.action.produce._detect_disconnect_screen", return_value=False):
            return ProduceHIFEnterShopAuto().run(self.context(controller), SimpleNamespace())

    def test_daily_tile_requires_exact_label_and_green_icon(self):
        context = self.context()
        self.assertEqual([285, 933, 150, 185], read_hif_shop_entry(context, frame()))
        for color in (0, (145, 50, 244), (240, 150, 20), (40, 180, 240)):
            image = frame()
            image[953:1043, 305:415] = color
            self.assertIsNone(read_hif_shop_entry(context, image))

    def test_description_and_shop_header_are_not_daily_tiles(self):
        context = self.context()
        for text in ("相談できます", "相談終了", "相談後", "差し入れ"):
            context.run_recognition.return_value = recognition(text=text)
            self.assertIsNone(read_hif_shop_entry(context, frame()))
        for box in ((331, 70, 58, 30), (650, 1078, 58, 30), (331, 1078, 170, 30), (331, 1078, 58, 80)):
            context.run_recognition.return_value = recognition(box=box)
            self.assertIsNone(read_hif_shop_entry(context, frame()))

    def test_ocr_miss_and_partial_green_patch_are_rejected(self):
        context = self.context()
        context.run_recognition.return_value = recognition(hit=False)
        self.assertIsNone(read_hif_shop_entry(context, frame()))
        context.run_recognition.return_value = recognition()
        image = frame()
        image[953:1043, 305:415] = 250
        image[973:993, 325:345] = (145, 225, 60)
        self.assertIsNone(read_hif_shop_entry(context, image))

    def test_height_rounding_is_supported(self):
        context = self.context()
        for height in range(1278, 1283):
            image = np.zeros((height, 720, 3), np.uint8)
            image[:min(height, 1279)] = frame()[:min(height, 1279)]
            self.assertEqual([285, 933, 150, 185], read_hif_shop_entry(context, image))

    def test_invalid_shapes_do_not_run_ocr(self):
        context = self.context()
        for shape in ((720, 1280, 3), (1200, 720, 3), (1280, 720), (1280, 720, 4)):
            self.assertIsNone(read_hif_shop_entry(context, np.zeros(shape, np.uint8)))
        context.run_recognition.assert_not_called()

    def test_entry_advancement_is_observed_before_success(self):
        controller = Controller()
        with patch("custom.action.produce.logger.success") as success:
            self.assertTrue(self.run_action(controller))
        self.assertEqual([(360, 1025)], controller.clicks)
        success.assert_called_once()

    def test_preview_or_swallowed_click_requires_fresh_confirmation(self):
        controller = Controller(required_clicks=2)
        self.assertTrue(self.run_action(controller))
        self.assertEqual([(360, 1025), (360, 1025)], controller.clicks)

    def test_unresponsive_entry_stops_after_three_clicks(self):
        controller = Controller(advance=False)
        with patch("custom.action.produce.logger.success") as success:
            self.assertFalse(self.run_action(controller))
        self.assertEqual(3, len(controller.clicks))
        success.assert_not_called()

    def test_stopping_and_disconnect_never_click(self):
        controller = Controller()
        context = self.context(controller)
        context.tasker.stopping = True
        self.assertTrue(ProduceHIFEnterShopAuto().run(context, SimpleNamespace()))
        with patch("custom.action.produce._detect_disconnect_screen", return_value=True):
            self.assertTrue(ProduceHIFEnterShopAuto().run(self.context(controller), SimpleNamespace()))
        self.assertEqual([], controller.clicks)

    def test_already_entered_page_hands_back_without_click(self):
        controller = Controller()
        controller.done = True
        self.assertTrue(self.run_action(controller))
        self.assertEqual([], controller.clicks)

    def test_pipeline_hook_only_affects_hif(self):
        hif = json.loads((ROOT / "assets/resource/base/pipeline/ProduceHIF.json").read_text(encoding="utf-8"))
        entry = "[JumpBack]ProduceHIFShopEntryFlag"
        self.assertIn(entry, hif["ProduceEntryHIF"]["next"])
        self.assertLess(hif["ProduceEntryHIF"]["next"].index(entry), hif["ProduceEntryHIF"]["next"].index("[JumpBack]Click_1"))
        self.assertEqual("ProduceHIFEnterShopAuto", hif["ProduceHIFShopEntryFlag"]["action"]["param"]["custom_action"])
        for name in ("Produce.json", "ProduceNIA.json"):
            self.assertNotIn("ProduceHIFShopEntryFlag", (ROOT / "assets/resource/base/pipeline" / name).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

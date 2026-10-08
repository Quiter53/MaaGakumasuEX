"""HIF change destination routing without consuming reward removal budgets."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
from custom.hif import HIF_CHANGE_SLOTS, read_hif_change
from custom.action.produce import ProduceHIFChangeAuto, ProduceChooseRewardAuto, ProduceShoppingAuto


def frame(enabled=False):
    image = np.zeros((1279, 720, 3), np.uint8)
    for x, y, w, h in HIF_CHANGE_SLOTS:
        image[y:y+h, x:x+w] = (250, 235, 160)
    image[1070:1100, 280:450] = (50, 150, 250) if enabled else (70, 120, 185)
    return image


def card(key, rarity="SR", priority=200):
    return dict(name=key, name_key=key, rarity=rarity, priority=priority,
                name_confidence=.9, label="verified-slot", detector_score=0.,
                extra_action="no", box=list(HIF_CHANGE_SLOTS[0]))


class Done:
    def __init__(self, image=None): self.image = image
    def wait(self): return self
    def get(self): return self.image


class Controller:
    def __init__(self, advance=True):
        self.selected = self.finished = False
        self.advance = advance
        self.clicks = []
    def post_screencap(self):
        return Done(np.full((1279,720,3),80,np.uint8) if self.finished else frame(self.selected))
    def post_click(self, x, y):
        self.clicks.append((x,y))
        if y < 1000: self.selected = True
        elif self.advance: self.finished = True
        return Done()


class HIFChangeTest(unittest.TestCase):
    def test_destination_requires_prompt_and_three_card_backgrounds(self):
        context = Mock()
        context.run_recognition.return_value = SimpleNamespace(hit=True)
        self.assertEqual([list(b) for b in HIF_CHANGE_SLOTS], read_hif_change(context,frame()))
        context.run_recognition.return_value = SimpleNamespace(hit=False)
        self.assertIsNone(read_hif_change(context,frame()))
        image=frame(); image[833:962,297:421]=0
        context.run_recognition.reset_mock()
        self.assertIsNone(read_hif_change(context,image))
        context.run_recognition.assert_not_called()

    def test_same_slots_on_reward_page_are_not_change(self):
        context = Mock(); context.run_recognition.return_value = SimpleNamespace(hit=False)
        self.assertIsNone(read_hif_change(context,frame()))

    def test_destination_avoids_black_and_prioritizes_white(self):
        lists=dict(blacklist=dict(enabled=True,cards=["blocked"]),
                   whitelist=dict(enabled=True,cards=["white"]))
        selected=ProduceHIFChangeAuto._destination([card("blocked",priority=1400),
                    card("other",priority=1300),card("white",priority=100)], lists)
        self.assertEqual("white",selected["name"])

    def test_no_lists_uses_extra_action_and_rarity_priority(self):
        lists={key:dict(enabled=False,cards=[]) for key in ("blacklist","whitelist")}
        self.assertEqual("SSR extra",ProduceHIFChangeAuto._destination([
            card("SR extra",priority=1300),card("SSR extra",priority=1400)],lists)["name"])

    def test_source_prefers_normal_then_blacklist_then_r(self):
        normal, blocked, rare = card("normal", "N"), card("blocked", "SSR"), card("rare", "R")
        select=ProduceShoppingAuto._choose_delete_candidate
        self.assertEqual("normal",select([rare,blocked,normal],{"blocked"},set())["name"])
        self.assertEqual("blocked",select([rare,blocked],{"blocked"},set())["name"])
        self.assertEqual("rare",select([rare],set(),set())["name"])
        self.assertNotIn("delete_rank",normal)

    def test_source_protects_white_and_does_not_force_remove_sr_ssr(self):
        select=ProduceShoppingAuto._choose_delete_candidate
        self.assertIsNone(select([card("protected","N")],{"protected"},{"protected"}))
        self.assertIsNone(select([card("keep","SSR"),card("keep2","SR")],set(),set()))

    def run_action(self, controller, stopping=False):
        context=Mock(); context.tasker=SimpleNamespace(controller=controller,stopping=stopping)
        context.run_recognition.return_value=SimpleNamespace(hit=True)
        lists={key:dict(enabled=False,cards=[]) for key in ("blacklist","whitelist")}
        with patch("custom.action.produce.time.sleep"), \
             patch("custom.action.produce._detect_disconnect_screen",return_value=False), \
             patch("custom.action.produce._load_card_lists",return_value=lists), \
             patch.object(ProduceChooseRewardAuto,"_evaluate",return_value=[card("test")]), \
             patch.object(ProduceHIFChangeAuto,"_ocr",return_value=[SimpleNamespace(box=[320,1080,80,30])]):
            return ProduceHIFChangeAuto().run(context,SimpleNamespace())

    def test_disabled_next_selects_card_then_confirms_and_observes_progress(self):
        controller=Controller()
        self.assertTrue(self.run_action(controller))
        self.assertEqual([(221,897),(360,1095)],controller.clicks)
        self.assertTrue(controller.finished)

    def test_ignored_next_is_bounded_and_never_logs_success(self):
        controller=Controller(advance=False)
        with patch("custom.action.produce.logger.success") as success:
            self.assertFalse(self.run_action(controller))
        self.assertLessEqual(len(controller.clicks),6)
        success.assert_not_called()

    def test_stopping_never_clicks(self):
        controller=Controller()
        self.assertTrue(self.run_action(controller,stopping=True))
        self.assertFalse(controller.clicks)


if __name__ == "__main__": unittest.main()

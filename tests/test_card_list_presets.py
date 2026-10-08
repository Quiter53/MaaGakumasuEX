"""Active presets, legacy compatibility and replacement-only eligibility."""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import mock_open, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
from custom.action.produce import _load_card_lists, ProduceHIFChangeSourceAuto, ProduceShoppingAuto


def section(cards=(), enabled=True):
    return dict(cards=list(cards), enabled=enabled)


def card(key, rarity="SSR"):
    return dict(name=key, name_key=key, rarity=rarity, name_confidence=.9,
                priority=400, label="cards", detector_score=.9, box=[80,700,120,120])


class CardListPresetsTest(unittest.TestCase):
    def load(self, document):
        with patch("builtins.open", mock_open(read_data=json.dumps(document))):
            return _load_card_lists()

    def test_legacy_lists_continue_working(self):
        loaded=self.load(dict(blacklist=section(["blocked"]),whitelist=section(["wanted"])))
        self.assertEqual(["blocked"],loaded["blacklist"]["cards"])
        self.assertEqual(dict(enabled=False,cards=[]),loaded["replacementList"])

    def test_current_preset_is_authoritative_over_old_snapshot(self):
        loaded=self.load(dict(activePreset="干劲",blacklist=section(["old"]),presets={
            "感性":dict(blacklist=section(["sense"])),
            "干劲":dict(blacklist=section(["energy"]),replacementList=section(["change"]))}))
        self.assertEqual(["energy"],loaded["blacklist"]["cards"])
        self.assertEqual(["change"],loaded["replacementList"]["cards"])

    def test_haodiao_and_focus_presets_are_read_independently(self):
        for name in ("好调", "集中"):
            loaded=self.load(dict(activePreset=name,presets={
                "好调":dict(whitelist=section(["good"])),
                "集中":dict(whitelist=section(["focus"]),replacementList=section(["change"]))}))
            self.assertEqual(["good" if name=="好调" else "focus"],loaded["whitelist"]["cards"])
            self.assertEqual(["change"] if name=="集中" else [],loaded["replacementList"]["cards"])

    def test_missing_or_invalid_active_preset_uses_legacy_snapshot(self):
        for presets in (None,[],{},dict(感性=None)):
            loaded=self.load(dict(activePreset="感性",presets=presets,blacklist=section(["old"])))
            self.assertEqual(["old"],loaded["blacklist"]["cards"])

    def test_normal_priority_precedes_replacement(self):
        choose=ProduceHIFChangeSourceAuto._choose_source_candidate
        candidates=[card("n","N"),card("blocked"),card("rare","R"),card("change")]
        for index,expected in enumerate(("n","blocked","rare","change")):
            selected=choose(candidates[index:],{"blocked"},set(),{"change"})
            self.assertEqual(expected,selected["name_key"])
            self.assertEqual(3-index,selected["delete_rank"])

    def test_explicit_replacement_permits_whitelisted_source(self):
        choose=ProduceHIFChangeSourceAuto._choose_source_candidate
        self.assertEqual("both",choose([card("both")],set(),{"both"},{"both"})["name_key"])
        self.assertIsNone(choose([card("both")],set(),{"both"},set()))
        self.assertIsNone(ProduceShoppingAuto._choose_delete_candidate([card("both")],set(),{"both"}))

    def test_explicit_whitelisted_normal_card_retains_normal_priority(self):
        selected=ProduceHIFChangeSourceAuto._choose_source_candidate(
            [card("both","N"),card("rare","R")],set(),{"both"},{"both"})
        self.assertEqual("both",selected["name_key"])
        self.assertEqual(3,selected["delete_rank"])


if __name__=="__main__": unittest.main()

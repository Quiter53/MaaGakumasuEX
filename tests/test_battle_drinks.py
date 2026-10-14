import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
from custom.battle_drinks import BattleDrinkSession, CardNameReader, TurnReader, parse_turn, signature
from custom.drink_policy import CATALOG, defaults, due, load_policy, normalize


def row(text, score=.99, box=(30,65,40,40)):
    return NS(text=text,score=score,box=box)


class DrinkPolicyTests(unittest.TestCase):
    def test_defaults_and_coverage(self):
        rules=defaults()
        self.assertEqual(28,len(rules))
        self.assertEqual(6,sum(r["target"] for r in rules.values()))
        self.assertTrue(all(r["timing"]=="opening" for r in rules.values()))
        root=Path(__file__).resolve().parents[1]/"assets/resource/base/image/produce/drinks"
        self.assertEqual(set(CATALOG),{p.stem for p in root.glob("*.png")})

    def test_single_timing_and_final_turn(self):
        rule={"timing":"before_card","card":"思考の基本","turns":3}
        self.assertFalse(due(rule,opening=True,remaining=3))
        self.assertFalse(due(rule,card_name="思考"))
        self.assertTrue(due(rule,card_name="思考の基本+"))
        self.assertTrue(due(rule,remaining=1))
        rule["timing"]="last_turns"
        self.assertFalse(due(rule,remaining=None))
        self.assertFalse(due(rule,remaining=4))
        self.assertTrue(due(rule,remaining=2))

    def test_save_reload_and_corrupt_not_opening_default(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/"policy.json"
            self.assertEqual(defaults(),load_policy(path))
            doc={"version":1,"drinks":{"429282":{"target":False,"timing":"before_card","card":"思考の基本","turns":2}}}
            path.write_text(json.dumps(doc),encoding="utf-8")
            self.assertEqual("before_card",load_policy(path)["429282"]["timing"])
            path.write_text("broken",encoding="utf-8")
            with self.assertRaises(ValueError): load_policy(path)

    def test_bad_schema_rejected(self):
        for value in (0,100,True,1.5):
            with self.assertRaises(ValueError): normalize({"drinks":{"429282":{"turns":value}}})

    def test_turn_parsing_rejects_buffs_scores_and_ambiguity(self):
        self.assertEqual(12,parse_turn([row("１２")]))
        for rows in ([row("1",.7)],[row("1"),row("2")],[row("1ターン")],[row("200")],[row("0")]):
            self.assertIsNone(parse_turn(rows))

    def test_turn_two_observations_cache_and_increase(self):
        reader=TurnReader()
        frame=np.zeros((1280,720,3),np.uint8)
        with patch("custom.battle_drinks.ocr", side_effect=[[row("残りターン",box=[10,2,95,30])],[row("3")],[row("3")],[row("4")],[row("4")]]) as read:
            self.assertIsNone(reader.read(None,frame))
            self.assertEqual(3,reader.read(None,frame))
            self.assertEqual(3,reader.read(None,frame))
            self.assertEqual(3,read.call_count)
            frame[58:123,20:95]=255
            self.assertIsNone(reader.read(None,frame))
            self.assertEqual(4,reader.read(None,frame))

    def test_exact_selected_card_name_only_and_cache(self):
        reader=CardNameReader(); frame=np.zeros((1280,720,3),np.uint8); box=[100,880,180,250]
        with patch("custom.battle_drinks.ocr",return_value=[row("思考の基本+",box=[110,1090,160,25])]) as read:
            self.assertEqual("思考の基本",reader.read(None,frame,box))
            self.assertEqual("思考の基本",reader.read(None,frame,box))
            self.assertEqual(1,read.call_count)
        frame[1000:1100]=255
        with patch("custom.battle_drinks.ocr",return_value=[row("思考の基",box=[110,1090,160,25])]), patch.object(reader,"_line",return_value=[]):
            self.assertIsNone(reader.read(None,frame,box))

    def test_no_drink_no_inventory_clicks(self):
        session=BattleDrinkSession(); context=Mock(); context.tasker.stopping=False; context.run_recognition.return_value=None
        self.assertFalse(session.prepare(context,np.zeros((1280,720,3),np.uint8),Mock()))
        context.tasker.controller.post_click.assert_not_called()

    def test_configured_bottle_waits_without_reopening_or_drinking(self):
        session=BattleDrinkSession(); frame=np.zeros((1280,720,3),np.uint8)
        session.policy["429282"].update(timing="before_card",card="思考の基本")
        session.inventory[0]=(signature(frame,session.SLOT_ROIS[0]),"429282")
        context=Mock(); context.tasker.stopping=False
        with patch.object(session,"occupied",return_value=[0]):
            self.assertFalse(session.prepare(context,frame,Mock(),remaining=4))
            self.assertFalse(session.prepare(context,frame,Mock(),remaining=4,card_name="未知"))
        context.tasker.controller.post_click.assert_not_called()

    def test_use_one_then_invalidate_hand_and_pin_trigger_card(self):
        session=BattleDrinkSession(); frame=np.zeros((1280,720,3),np.uint8)
        session.policy["429282"].update(timing="before_card",card="思考の基本")
        session.inventory[0]=(signature(frame,session.SLOT_ROIS[0]),"429282")
        context=Mock(); context.tasker.stopping=False
        context.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value=frame
        context.run_recognition.return_value=NS(hit=True,best_result=NS(box=[400,1100,180,65]))
        with patch.object(session,"occupied",side_effect=[[0],[]]), patch("custom.battle_drinks.time.sleep"), \
             patch("custom.battle_drinks.owned_detail_title",return_value=CATALOG["429282"]["jp"]):
            self.assertTrue(session.prepare(context,frame,Mock(return_value=True),remaining=4,card_name="思考の基本"))
        self.assertEqual("思考の基本",session.pending_card)
        self.assertEqual({},session.inventory)
        self.assertEqual(2,context.tasker.controller.post_click.call_count)

    def test_final_turn_uses_unknown_bottle_without_inventory_title_lookup(self):
        session=BattleDrinkSession(); session.opening=False
        context=Mock(); context.tasker.stopping=False; frame=np.zeros((1280,720,3),np.uint8)
        context.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value=frame
        context.run_recognition.return_value=NS(hit=True,best_result=NS(box=[400,1100,180,65]))
        with patch.object(session,"occupied",side_effect=[[2],[]]), patch.object(session,"identify") as identify, patch("custom.battle_drinks.time.sleep"):
            self.assertTrue(session.prepare(context,frame,Mock(return_value=True),remaining=1))
        identify.assert_not_called()
        self.assertEqual((232,1200),context.tasker.controller.post_click.call_args_list[0].args)

    def test_consumption_not_confirmed_never_clicks_again(self):
        session=BattleDrinkSession(); context=Mock(); context.tasker.stopping=False; frame=np.zeros((1280,720,3),np.uint8)
        context.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value=frame
        context.run_recognition.return_value=NS(hit=True,best_result=NS(box=[400,1100,180,65]))
        with patch.object(session,"occupied",return_value=[0]), patch("custom.battle_drinks.time.sleep"):
            with self.assertRaisesRegex(RuntimeError,"数量未减少"):
                session.prepare(context,frame,Mock(return_value=True))
        self.assertEqual(2,context.tasker.controller.post_click.call_count)

    def test_target_ui_mapping_affects_acquisition_without_changing_timing(self):
        from custom.drink_policy import TARGET_NAMES
        from custom.shop import TARGET_DRINK_NAMES
        rules=defaults(); rules["429282"]["target"]=False; rules["368325"]["target"]=True
        with patch("custom.drink_policy.load_policy",return_value=rules):
            self.assertNotIn("429282",TARGET_NAMES)
            self.assertEqual("初星水",TARGET_DRINK_NAMES["368325"])
        self.assertEqual("opening",rules["429282"]["timing"])

    def test_card_line_fallback_requires_whole_name(self):
        reader=CardNameReader(); frame=np.zeros((1280,720,3),np.uint8)
        with patch("custom.battle_drinks.ocr",return_value=[]),patch.object(reader,"_line",return_value=[row("思考の基本+",.99)]):
            self.assertEqual("思考の基本",reader.read(None,frame,[100,880,180,250]))

    def test_final_drain_survives_extra_turn_drink(self):
        session=BattleDrinkSession(); session.opening=False; session.final_drain=True
        session.policy["429282"]["timing"]="before_card"
        context=Mock(); context.tasker.stopping=False; frame=np.zeros((1280,720,3),np.uint8)
        context.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value=frame
        context.run_recognition.return_value=NS(hit=True,best_result=NS(box=[400,1100,180,65]))
        with patch.object(session,"occupied",side_effect=[[0],[]]),patch("custom.battle_drinks.time.sleep"),patch.object(session,"identify") as identify:
            self.assertTrue(session.prepare(context,frame,Mock(return_value=True),remaining=2))
        identify.assert_not_called()


if __name__=="__main__": unittest.main()

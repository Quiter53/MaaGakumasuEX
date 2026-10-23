"""Contracts preserved when merging official v1.5.2 into the custom fork."""
import hashlib
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
from custom.produce.battle import ProduceCardsAuto
from custom.reco.produce import ProduceChooseIdolAuto
from custom.card_visual import rarity_priority


class UpstreamMergeTest(unittest.TestCase):
    def test_original_model_is_used_for_all_card_detection(self):
        nodes = json.loads((ROOT / "assets/resource/base/pipeline/ProduceUtils.json").read_text(encoding="utf-8"))
        self.assertEqual("cards.onnx", nodes["ProduceRecognitionCards"]["recognition"]["param"]["model"])
        self.assertNotIn("ProduceRecognitionBattleCards", nodes)
        model = (ROOT / "assets/resource/base/model/detect/cards.onnx").read_bytes()
        blob = b"blob " + str(len(model)).encode() + b"\0" + model
        self.assertEqual("c443c4e14c96309a296e4b269484b34d6c0fcf51", hashlib.sha1(blob).hexdigest())
        self.assertFalse((ROOT / "assets/resource/base/model/detect/cards_grid.onnx").exists())

    def test_special_characters_in_song_names(self):
        self.assertEqual(1, ProduceChooseIdolAuto.similarity_ratio("36℃ U･B･U", "36°C U・B・U"))

    def test_equivalent_song_glyphs_pass_actual_idol_recognition(self):
        for song, accepted in (("36°C U・B・U", True), ("３６℃ Ｕ･Ｂ･Ｕ", True),
                               ("Fighting My Way", False), ("", False), ("!!!", False)):
            with self.subTest(song=song):
                ctx = Mock()
                def recognize(node, image, pipeline_override=None):
                    text = {"ProduceChooseIdolName":"姫崎莉波", "ProduceChooseIdolSong":song}.get(node)
                    rows = [SimpleNamespace(text=text)] if text else []
                    return SimpleNamespace(hit=bool(rows), all_results=rows)
                ctx.run_recognition.side_effect = recognize
                result = ProduceChooseIdolAuto().analyze(ctx, SimpleNamespace(image=None,
                    custom_recognition_param=json.dumps({"idol_name":"姫崎莉波","song_name":"36℃ U･B･U"})))
                self.assertEqual(accepted, result.box is not None)
        self.assertEqual(0, ProduceChooseIdolAuto.similarity_ratio("", ""))
        self.assertEqual(0, ProduceChooseIdolAuto.similarity_ratio("!!!", "---"))

    def test_custom_song_labels_exist_in_every_supported_language(self):
        keys = {"自定义歌曲", "自定义歌曲名称", "自定义歌曲名称说明", "自定义歌曲名称输入框说明",
                "自定义歌曲第一属性", "自定义歌曲第二属性", "自定义歌曲推荐效果", "自定义歌曲推荐效果说明",
                "培育属性Vo", "培育属性Da", "培育属性Vi", "感性·好调", "感性·集中", "理性·干劲",
                "理性·好印象", "非凡·强气", "非凡·全力", "非凡·温存"}
        for language in ("zh-CN", "zh-Hant", "ja-JP", "en-US"):
            with self.subTest(language=language):
                translations = json.loads((ROOT/"assets/lang"/f"{language}.json").read_text(encoding="utf-8"))
                self.assertTrue(keys <= translations.keys())
                self.assertTrue(all(translations[key].strip() for key in keys))

    def test_custom_song_attribute_combinations(self):
        for filename in ("produce.json", "produce_cn.json"):
            options = json.loads((ROOT / "assets/tasks" / filename).read_text(encoding="utf-8"))["option"]
            entries = [case for option in options.values() for case in option.get("cases", [])
                       if case["name"] == "自定义歌曲"]
            self.assertGreaterEqual(len(entries), 13)
            for entry in entries:
                song, first = (options[key] for key in entry["option"])
                self.assertIn("ProduceChooseIdol", song["pipeline_override"])
                for first_case in first["cases"]:
                    second = options[first_case["option"][0]]
                    self.assertEqual(2, len(second["cases"]))
                    for second_case in second["cases"]:
                        self.assertNotEqual(first_case["name"], second_case["name"])
                        effect = options[second_case["option"][0]]
                        self.assertEqual(7, len(effect["cases"]))
                        for case in effect["cases"]:
                            params = case["pipeline_override"]["ProduceChooseNIAEventFlag"]["custom_action_param"]
                            self.assertEqual(first_case["name"], params["first"])
                            self.assertEqual(second_case["name"], params["second"])

    def test_custom_play_priority_preserved(self):
        self.assertGreater(rarity_priority("SR", "yes"), rarity_priority("SSR", "no"))
        self.assertGreater(rarity_priority("SSR", "yes"), rarity_priority("SR", "yes"))

    def run_battle(self, results, clock, frames=1):
        image = np.zeros((1280, 720, 3), dtype=np.uint8)
        context = Mock()
        context.tasker.stopping = False
        context.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value = image
        def recognize(node, image, pipeline_override=None):
            if node == "ProduceRecognitionCards":
                return SimpleNamespace(hit=True, all_results=results)
            return None
        context.run_recognition.side_effect = recognize
        action = ProduceCardsAuto()
        with patch.object(action, "_wait_until_playable", return_value=True), \
             patch.object(action, "_is_playing_card", side_effect=[True] * frames + [False] * 3), \
             patch.object(action, "_choose_card", side_effect=lambda ctx, img, cards: cards[0]), \
             patch.object(action, "_play_a_card", return_value=True) as play, \
             patch("custom.produce.shared._detect_disconnect_screen", return_value=None), \
             patch("custom.produce.shared._save_card_diagnostic"), \
             patch("time.sleep"), \
             patch("time.time", side_effect=clock):
            result = action.run(context, None)
        return result, context, play

    def test_out_of_hand_useless_cannot_skip_turn(self):
        result, context, play = self.run_battle(
            [SimpleNamespace(label="useless", box=[90, 100, 140, 220], score=.99)],
            [0, 1, 2, 3], frames=3)
        self.assertTrue(result)
        context.run_task.assert_not_called()
        play.assert_not_called()

    def test_timeout_uses_current_playable_fragment(self):
        result, context, play = self.run_battle(
            [SimpleNamespace(label="cards", box=[0, 850, 60, 220], score=.9)], [0, 16])
        self.assertTrue(result)
        play.assert_called_once()
        context.run_task.assert_not_called()

    def test_timeout_never_clicks_outside_hand(self):
        result, context, play = self.run_battle(
            [SimpleNamespace(label="suggestions", box=[80, 300, 140, 220], score=.99)], [0, 46])
        # A stalled recognition hands control back to the cultivation entry.
        # True continues the task; it does not mean a card was played.
        self.assertTrue(result)
        play.assert_not_called()
        context.run_task.assert_not_called()
        context.tasker.controller.post_click.assert_not_called()

    def test_timeout_rejects_weak_fragment(self):
        result, context, play = self.run_battle(
            [SimpleNamespace(label="cards", box=[0, 850, 60, 220], score=.4)], [0, 46])
        self.assertTrue(result)
        play.assert_not_called()
        context.run_task.assert_not_called()
        context.tasker.controller.post_click.assert_not_called()


if __name__ == "__main__":
    unittest.main()

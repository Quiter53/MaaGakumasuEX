import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

from custom.action.produce import (  # noqa: E402
    ProduceChooseRewardAuto,
    ProduceRecoverConnectionAuto,
)


class _Recognition:
    def __init__(self, hit: bool):
        self.hit = hit


class _Context:
    def __init__(self, hits):
        self._hits = iter(hits)
        self.calls = []

    def run_recognition(self, node, image, pipeline_override=None):
        self.calls.append((node, pipeline_override))
        return _Recognition(next(self._hits))


class _Completed:
    def __init__(self, value=None):
        self.value = value

    def wait(self):
        return self

    def get(self):
        return self.value


class _RecoveryController:
    def __init__(self):
        self.clicks = []

    def post_screencap(self):
        return _Completed(object())

    def post_click(self, x, y):
        self.clicks.append((x, y))
        return _Completed()


class _RecoveryContext:
    def __init__(self, hits):
        self.hits = hits
        self.calls = []
        self.tasker = SimpleNamespace(controller=_RecoveryController())

    def run_recognition(self, node, image):
        self.calls.append(node)
        return _Recognition(self.hits.get(node, False))


class ProduceRewardRoutingTest(unittest.TestCase):
    def setUp(self):
        ProduceRecoverConnectionAuto._reset_network_retry_points()

    def test_drink_prompt_short_circuits_card_routing(self):
        context = _Context([False, True])
        self.assertTrue(ProduceChooseRewardAuto._is_drink_reward(context, None))
        self.assertEqual(
            ["ProduceChooseCardRewardFlag", "ProduceChooseDrinkRewardFlag"],
            [item[0] for item in context.calls],
        )

    def test_card_prompt_wins_before_broad_drink_feature_matching(self):
        context = _Context([True])
        self.assertFalse(ProduceChooseRewardAuto._is_drink_reward(context, None))
        self.assertEqual(
            ["ProduceChooseCardRewardFlag"],
            [item[0] for item in context.calls],
        )

    def test_full_inventory_reject_marker_is_a_drink(self):
        context = _Context([False, False, True])
        self.assertTrue(ProduceChooseRewardAuto._is_drink_reward(context, None))
        self.assertEqual(
            [
                "ProduceChooseCardRewardFlag",
                "ProduceChooseDrinkRewardFlag",
                "ProduceChooseSkip",
            ],
            [item[0] for item in context.calls],
        )

    def test_any_known_reward_slot_drink_is_a_drink(self):
        context = _Context([False, False, False, True])
        self.assertTrue(ProduceChooseRewardAuto._is_drink_reward(context, None))
        node, override = context.calls[-1]
        self.assertEqual("ProduceRecognitionShopDrinks", node)
        config = override[node]
        self.assertEqual([100, 760, 520, 270], config["roi"])
        self.assertEqual(28, len(config["template"]))

    def test_card_page_requires_all_drink_signals_to_miss(self):
        context = _Context([False, False, False, False])
        self.assertFalse(ProduceChooseRewardAuto._is_drink_reward(context, None))

    def test_event_reward_keeps_four_card_detections(self):
        boxes = [
            [88, 840, 124, 128],
            [227, 836, 124, 132],
            [365, 840, 124, 128],
            [505, 820, 124, 148],
        ]
        results = [
            SimpleNamespace(label="cards", box=box, score=0.9)
            for box in boxes
        ]

        class _CardContext:
            def run_recognition(self, node, image):
                self.node = node
                return SimpleNamespace(hit=True, all_results=results)

        rarity = SimpleNamespace(
            rarity="SR",
            confidence=0.9,
            best_distance=0.01,
            margin=0.2,
            signature=(0.4, 0.8, 1.0),
        )
        image = __import__("numpy").zeros((1280, 720, 3), dtype="uint8")
        with (
            patch("custom.action.produce.classify_rarity", return_value=rarity),
            patch("custom.action.produce.match_card_visual", return_value=None),
            patch.object(
                __import__(
                    "custom.action.produce", fromlist=["ProduceCardsAuto"]
                ).ProduceCardsAuto,
                "_extra_action_status",
                return_value=("no", 0.0, [0, 0, 0, 0]),
            ),
        ):
            evaluated = ProduceChooseRewardAuto()._evaluate(_CardContext(), image)
        self.assertEqual(4, len(evaluated))
        self.assertEqual(boxes, [item["box"] for item in evaluated])

    def test_all_drink_templates_exist(self):
        template_dir = ROOT / "assets/resource/base/image/produce/drinks"
        missing = [
            template
            for template in ProduceChooseRewardAuto.ALL_DRINK_TEMPLATES
            if not (template_dir / Path(template).name).is_file()
        ]
        self.assertEqual([], missing)

    def test_network_and_date_change_return_to_scene_detection(self):
        path = ROOT / "assets/resource/base/pipeline/ProduceUtils.json"
        pipeline = json.loads(path.read_text(encoding="utf-8-sig"))
        error = pipeline["ProduceDisconnectError"]
        self.assertEqual(
            ["produce/disconnect_error.png", "date_change.png"],
            error["recognition"]["param"]["template"],
        )
        self.assertEqual("Custom", error["action"]["type"])
        self.assertEqual(
            "ProduceRecoverConnectionAuto",
            error["action"]["param"]["custom_action"],
        )
        self.assertEqual("date_change.png", pipeline["ProduceDateChangeFlag"]["recognition"]["param"]["template"])
        self.assertEqual(0.88, pipeline["ProduceDateChangeFlag"]["recognition"]["param"]["threshold"])
        self.assertEqual(0.85, error["recognition"]["param"]["threshold"])
        self.assertEqual("ProduceDisconnectRecovery", error["next"][-1])
        self.assertEqual(
            "ProduceDisconnectRetryResume",
            pipeline["ProduceDisconnectRecovery"]["next"][-1],
        )
        self.assertEqual(
            ["ProduceEntryFlag"],
            pipeline["ProduceDisconnectRetryResume"]["next"],
        )
        self.assertEqual(
            ["ProduceDisconnectRecovery"],
            pipeline["ProduceDisconnectTitle"]["next"],
        )
        self.assertEqual((515, 1160), ProduceRecoverConnectionAuto.NETWORK_RETRY_POINT)
        self.assertEqual((360, 1170), ProduceRecoverConnectionAuto.DATE_CHANGE_POINT)

    def test_date_change_clicks_the_single_title_button(self):
        context = _RecoveryContext({"ProduceDateChangeFlag": True})
        self.assertTrue(ProduceRecoverConnectionAuto().run(context, SimpleNamespace()))
        self.assertEqual([(360, 1170)], context.tasker.controller.clicks)

    def test_network_error_clicks_retry_instead_of_title(self):
        context = _RecoveryContext(
            {
                "ProduceDateChangeFlag": False,
                "ProduceDisconnectNetworkFlag": True,
            }
        )
        self.assertTrue(ProduceRecoverConnectionAuto().run(context, SimpleNamespace()))
        self.assertEqual([(515, 1160)], context.tasker.controller.clicks)

    def test_network_error_wins_when_date_template_also_hits(self):
        context = _RecoveryContext(
            {
                "ProduceDisconnectNetworkFlag": True,
                "ProduceDateChangeFlag": True,
            }
        )
        self.assertTrue(ProduceRecoverConnectionAuto().run(context, SimpleNamespace()))
        self.assertEqual([(515, 1160)], context.tasker.controller.clicks)
        self.assertEqual(["ProduceDisconnectNetworkFlag"], context.calls)

    def test_persistent_network_error_enumerates_only_right_button_points(self):
        context = _RecoveryContext({"ProduceDisconnectNetworkFlag": True})
        with patch("custom.action.produce.time.monotonic", side_effect=[10.0, 12.0, 14.0]):
            action = ProduceRecoverConnectionAuto()
            self.assertTrue(action.run(context, SimpleNamespace()))
            self.assertTrue(action.run(context, SimpleNamespace()))
            self.assertTrue(action.run(context, SimpleNamespace()))
        self.assertEqual(
            [(515, 1160), (570, 1160), (625, 1160)],
            context.tasker.controller.clicks,
        )
        self.assertTrue(all(x >= 480 for x, _ in context.tasker.controller.clicks))

    def test_card_title_node_and_focus_text_are_unambiguous(self):
        path = ROOT / "assets/resource/base/pipeline/Produce.json"
        pipeline = json.loads(path.read_text(encoding="utf-8-sig"))
        card = pipeline["ProduceChooseCardRewardFlag"]
        self.assertEqual(
            ["produce/choose_cards.png"],
            card["recognition"]["param"]["template"],
        )
        self.assertNotIn(
            "/",
            pipeline["ProduceChooseRewardStrategy"]["focus"]["Node.Action.Succeeded"],
        )


if __name__ == "__main__":
    unittest.main()

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

from custom.action.produce import ProduceChooseRewardAuto  # noqa: E402


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


class ProduceRewardRoutingTest(unittest.TestCase):
    def test_drink_prompt_short_circuits_card_routing(self):
        context = _Context([True])
        self.assertTrue(ProduceChooseRewardAuto._is_drink_reward(context, None))
        self.assertEqual(["ProduceChooseDrinkRewardFlag"], [item[0] for item in context.calls])

    def test_full_inventory_reject_marker_is_a_drink(self):
        context = _Context([False, True])
        self.assertTrue(ProduceChooseRewardAuto._is_drink_reward(context, None))
        self.assertEqual(
            ["ProduceChooseDrinkRewardFlag", "ProduceChooseSkip"],
            [item[0] for item in context.calls],
        )

    def test_any_known_reward_slot_drink_is_a_drink(self):
        context = _Context([False, False, True])
        self.assertTrue(ProduceChooseRewardAuto._is_drink_reward(context, None))
        node, override = context.calls[-1]
        self.assertEqual("ProduceRecognitionShopDrinks", node)
        config = override[node]
        self.assertEqual([100, 760, 520, 270], config["roi"])
        self.assertEqual(28, len(config["template"]))

    def test_card_page_requires_all_drink_signals_to_miss(self):
        context = _Context([False, False, False])
        self.assertFalse(ProduceChooseRewardAuto._is_drink_reward(context, None))

    def test_all_drink_templates_exist(self):
        template_dir = ROOT / "assets/resource/base/image/produce/drinks"
        missing = [
            template
            for template in ProduceChooseRewardAuto.ALL_DRINK_TEMPLATES
            if not (template_dir / Path(template).name).is_file()
        ]
        self.assertEqual([], missing)

    def test_retry_returns_to_scene_detection_instead_of_waiting_for_title(self):
        path = ROOT / "assets/resource/base/pipeline/ProduceUtils.json"
        pipeline = json.loads(path.read_text(encoding="utf-8-sig"))
        error = pipeline["ProduceDisconnectError"]
        self.assertGreaterEqual(error["action"]["param"]["target"][0], 380)
        self.assertEqual("ProduceDisconnectRetryResume", error["next"][-1])
        self.assertEqual(
            ["ProduceEntryFlag"],
            pipeline["ProduceDisconnectRetryResume"]["next"],
        )


if __name__ == "__main__":
    unittest.main()

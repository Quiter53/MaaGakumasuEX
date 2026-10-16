import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
from custom.card_visual import reconcile_visual_identity
from custom.produce.rewards import ProduceChooseRewardAuto


class CardConsistencyTest(unittest.TestCase):
    def test_conflicting_identity_cannot_override_confident_frame(self):
        visual = SimpleNamespace(rarity="N", extra_action=True)
        self.assertIsNone(reconcile_visual_identity(visual, SimpleNamespace(rarity="SSR", confidence=.8)))

    def test_identity_can_resolve_weak_or_unknown_frame(self):
        visual = SimpleNamespace(rarity="SSR")
        for rarity, confidence in (("N", .1), ("unknown", 0), ("SSR", .9)):
            self.assertIs(visual, reconcile_visual_identity(visual, SimpleNamespace(rarity=rarity, confidence=confidence)))

    def test_unknown_card_names_do_not_block_stable_reward_frame(self):
        frame = np.full((1280, 720, 3), 100, dtype=np.uint8)
        cards = [{"box": [90 + i * 150, 820, 124, 126], "label": "cards",
                  "rarity": "unknown", "name_key": "", "extra_action": "unknown"}
                 for i in range(3)]
        completed = SimpleNamespace(wait=lambda: SimpleNamespace(get=lambda: frame))
        context = SimpleNamespace(tasker=SimpleNamespace(stopping=False,
            controller=SimpleNamespace(post_screencap=lambda: completed)),
            run_recognition=lambda node, image: SimpleNamespace(hit=node == "ProduceChooseCardRewardFlag"))
        with patch.object(ProduceChooseRewardAuto, "_evaluate", return_value=cards) as evaluate, \
             patch("custom.produce.shared._detect_disconnect_screen", return_value=None), \
             patch("time.sleep"):
            _, result = ProduceChooseRewardAuto()._wait_for_stable_cards(context)
        self.assertEqual(cards, result)
        self.assertEqual(2, evaluate.call_count)

    def test_card_motion_or_replacement_is_not_stable(self):
        signature = (((100, 820, 124, 126), (10,) * 27),)
        moved = (((115, 820, 124, 126), (10,) * 27),)
        replaced = (((100, 820, 124, 126), (20,) * 27),)
        self.assertFalse(ProduceChooseRewardAuto._reward_frames_stable(signature, moved))
        self.assertFalse(ProduceChooseRewardAuto._reward_frames_stable(signature, replaced))


if __name__ == "__main__":
    unittest.main()

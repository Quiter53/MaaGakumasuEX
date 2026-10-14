import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
from custom.reward_layout import reward_card_row


def candidates(boxes):
    return [{"box": b, "label": "cards", "detector_score": .95} for b in boxes]


NORMAL = [[158, 820, 124, 126], [298, 821, 122, 126], [437, 820, 122, 127]]
EVENT = [[87, 839, 124, 130], [228, 838, 124, 131],
         [366, 839, 122, 129], [505, 828, 124, 141]]


class RewardLayoutTests(unittest.TestCase):
    def test_normal_three(self):
        self.assertEqual(len(reward_card_row(candidates(NORMAL))), 3)

    def test_event_four_excludes_real_replay_false_box(self):
        row = reward_card_row(candidates([[53, 969, 176, 153]] + EVENT))
        self.assertEqual([c["box"][0] for c in row], [87, 228, 366, 505])
        self.assertEqual(row[-1]["box"], [505, 845, 124, 124])

    def test_missing_event_card_never_becomes_normal_three(self):
        for missing in range(4):
            self.assertEqual(reward_card_row(candidates(EVENT[:missing] + EVENT[missing+1:])), [])

    def test_incomplete_normal_no_fixed_guess(self):
        self.assertEqual(reward_card_row(candidates(NORMAL[:2])), [])
        self.assertEqual(reward_card_row([]), [])

    def test_off_row_box_cannot_complete_row(self):
        self.assertEqual(reward_card_row(candidates(EVENT[:3] + [[505, 960, 124, 124]])), [])

    def test_order_and_input_preserved(self):
        source = candidates(EVENT[::-1])
        result = reward_card_row(source)
        self.assertEqual([c["box"][0] for c in result], [87, 228, 366, 505])
        self.assertEqual(source[0]["box"], EVENT[-1])

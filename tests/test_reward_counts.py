from unittest.mock import Mock
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
from custom.reward_counts import RewardActionBudget, parse_remaining
from custom.action.produce import (
    ProduceChooseRewardAuto, ProduceLoadCardLists, ProduceResetRewardCounts,
    _REWARD_BUDGETS, _reset_produce_card_run_state,
)


class RewardBudgetTest(unittest.TestCase):
    def setUp(self):
        _reset_produce_card_run_state()

    def test_replay_four_uses_and_repeated_false_replenishment(self):
        budget = RewardActionBudget()
        budget.observe(4)
        for before in range(4, 0, -1):
            self.assertTrue(budget.confirm(before, before - 1))
        for _ in range(100):
            budget.observe(4)
            self.assertFalse(budget.confirm(4, 3))
        self.assertEqual((4, 0, False), (budget.confirmed, budget.remaining, budget.can_attempt))

    def test_click_and_animation_without_counter_decrease_do_not_count(self):
        for after in (None, 3, 4):
            budget = RewardActionBudget()
            budget.observe(3)
            self.assertFalse(budget.confirm(3, after))
            self.assertEqual(0, budget.confirmed)
            self.assertFalse(budget.can_attempt)

    def test_duplicate_ack_is_not_counted_twice(self):
        budget = RewardActionBudget()
        budget.observe(2)
        self.assertTrue(budget.confirm(2, 1))
        self.assertFalse(budget.confirm(2, 1))
        self.assertEqual(1, budget.confirmed)

    def test_external_consumption_updates_budget_without_inventing_agent_uses(self):
        budget = RewardActionBudget()
        budget.observe(4)
        budget.observe(2)
        self.assertEqual(0, budget.confirmed)
        self.assertTrue(budget.confirm(2, 1))

    def test_unknown_budget_cannot_produce_an_unbounded_trial_loop(self):
        budget = RewardActionBudget()
        self.assertFalse(budget.can_attempt)
        self.assertFalse(budget.confirm(None, None))
        budget.observe(3)
        # A valid first observation resolves the unknown budget.
        self.assertEqual(3, budget.remaining)

    def test_interrupted_pending_use_is_confirmed_once_after_recovery(self):
        budget = RewardActionBudget()
        budget.observe(2)
        self.assertTrue(budget.begin())
        self.assertFalse(budget.can_attempt)
        self.assertFalse(budget.confirm(2, None))
        self.assertEqual(2, budget.pending_before)
        self.assertFalse(budget.begin())
        self.assertTrue(budget.confirm(2, 1))
        self.assertEqual((1, 1, None), (budget.remaining, budget.confirmed, budget.pending_before))
        self.assertFalse(budget.confirm(2, 1))
        self.assertEqual(1, budget.confirmed)

    def test_cancelled_pending_use_cannot_retry_the_same_counter(self):
        budget = RewardActionBudget()
        budget.observe(2)
        self.assertTrue(budget.begin())
        self.assertFalse(budget.confirm(2, 2))
        budget.observe(2)
        self.assertFalse(budget.can_attempt)
        self.assertIsNone(budget.pending_before)

    def test_strict_counter_ocr_rejects_prices_ambiguity_and_low_scores(self):
        for text, expected in (("あと４回", 4), ("剩余 2 次", 2), ("0", 0),
                               ("P100", None), ("1/3", None), ("1 2", None),
                               ("カード3", None), ("あと12回", None)):
            self.assertEqual(expected, parse_remaining(text, .99), text)
        self.assertIsNone(parse_remaining("あと4回", .4))

    def test_new_round_resets_but_task_resume_keeps_budget(self):
        ctx = Mock()
        ctx.get_node_data.return_value = {}
        _REWARD_BUDGETS["refresh"].observe(1)
        _REWARD_BUDGETS["refresh"].confirm(1, 0)
        with patch("custom.action.produce._load_card_lists", return_value={
            "blacklist": {"enabled": False, "cards": []},
            "whitelist": {"enabled": False, "cards": []},
        }):
            ProduceLoadCardLists().run(ctx, None)
        self.assertEqual(0, _REWARD_BUDGETS["refresh"].remaining)
        ProduceResetRewardCounts().run(ctx, None)
        self.assertIsNone(_REWARD_BUDGETS["refresh"].remaining)
        pipeline = json.loads((ROOT / "assets/resource/base/pipeline/Produce.json").read_text(encoding="utf-8"))
        self.assertEqual("ProduceResetRewardCounts", pipeline["ProduceMainPage"]["action"]["param"]["custom_action"])
        self.assertNotIn("ProduceResetRewardCounts", json.dumps(pipeline["ProduceChooseContinue"]))

    def test_two_frames_must_agree(self):
        completed = SimpleNamespace(wait=lambda: SimpleNamespace(get=lambda: object()))
        context = SimpleNamespace(tasker=SimpleNamespace(
            stopping=False, controller=SimpleNamespace(post_screencap=lambda: completed)))
        with patch.object(ProduceChooseRewardAuto, "_recognize_reward_action_remaining",
                          side_effect=[2, 4, 2, 3]), patch("custom.action.produce.time.sleep"):
            self.assertEqual({"remove": 2, "refresh": None},
                             ProduceChooseRewardAuto._stable_action_counts(context, object()))

    def test_reward_without_controls_does_not_spend_cached_budget(self):
        _REWARD_BUDGETS["remove"].observe(2)
        _REWARD_BUDGETS["refresh"].observe(4)
        cards = [{"name_key": name, "name": name, "priority": 200,
                  "name_confidence": 1, "label": "cards", "detector_score": .9,
                  "rarity": "R", "extra_action": "no"} for name in ("blocked", "allowed", "third")]
        completed = SimpleNamespace(wait=lambda: SimpleNamespace(get=lambda: object()))
        controller = SimpleNamespace(post_screencap=lambda: completed)
        context = SimpleNamespace(tasker=SimpleNamespace(stopping=False, controller=controller))
        with (patch.object(ProduceChooseRewardAuto, "_reward_kind", return_value="card"),
              patch.object(ProduceChooseRewardAuto, "_wait_for_stable_cards", return_value=(object(), cards)),
              patch.object(ProduceChooseRewardAuto, "_stable_action_counts", return_value={"remove": None, "refresh": None}),
              patch.object(ProduceChooseRewardAuto, "_select", return_value=True) as select,
              patch("custom.action.produce._load_card_lists", return_value={
                  "blacklist": {"enabled": True, "cards": ["blocked"]},
                  "whitelist": {"enabled": False, "cards": []}})):
            self.assertTrue(ProduceChooseRewardAuto().run(context, None))
            self.assertNotEqual("blocked", select.call_args.args[1]["name_key"])
        self.assertEqual(0, _REWARD_BUDGETS["refresh"].confirmed)


if __name__ == "__main__":
    unittest.main()

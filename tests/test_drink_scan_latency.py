import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent'))
from custom.produce.rewards import ProduceChooseRewardAuto as Action


def result(box, count=8):
    return SimpleNamespace(box=box, count=count)


class Context:
    def __init__(self, rows=None):
        self.calls = []
        self.rows = rows or {}
        self.tasker = SimpleNamespace(stopping=False)

    def run_recognition(self, node, pixels, pipeline_override=None):
        params = pipeline_override[node]
        self.calls.append(params)
        key = (params['template'][0].rsplit('/', 1)[1].split('.')[0], params['roi'][1])
        rows = self.rows.get(key, [])
        # Deliberately return the same result after masking. A stale or bad
        # native result must still terminate without selecting an extra slot.
        return SimpleNamespace(hit=bool(rows), filtered_results=rows)


class StateContext(Context):
    def __init__(self, rows=None, *, capacity=False, full_marker=False):
        super().__init__(rows)
        self.nodes = []
        self.hits = {'ProduceDrinkRewardFullFlag': capacity, 'ProduceKeepDrinkFlag': full_marker}

    def run_recognition(self, node, pixels, pipeline_override=None):
        self.nodes.append(node)
        if node in self.hits:
            return SimpleNamespace(hit=self.hits[node])
        return super().run_recognition(node, pixels, pipeline_override)


def drink(drink_id):
    return {'id': drink_id, 'name': drink_id.upper()}


class DrinkStateFallbackTests(unittest.TestCase):
    def scan(self, context, reward, inventory, occupied):
        frame = np.zeros((1280, 720, 3), np.uint8)
        with patch('custom.drink_visual.scan', side_effect=[reward, inventory]), \
             patch.object(Action, '_scan_inventory_occupancy', return_value=set(occupied)), \
             patch('custom.produce.rewards.TARGET_NAMES', {'a': 'A', 'b': 'B'}):
            return Action._scan_drink_state(context, frame)

    def test_complete_visible_state_needs_no_feature_fallback(self):
        context = StateContext()
        state = self.scan(context, {0: drink('a'), 1: drink('other'), 2: drink('b')},
                          {0: drink('other'), 1: drink('a')}, {0, 1})
        self.assertEqual([], context.calls)
        self.assertEqual({0, 2}, set(state['reward']))
        self.assertEqual({1}, set(state['inventory']))
        self.assertTrue(state['visual_complete'])
        self.assertFalse(state['full'])
        self.assertIn('ProduceKeepDrinkFlag', context.nodes)

    def test_complete_reward_skips_only_reward_fallback_and_keeps_non_target_identity(self):
        context = StateContext({('b', 1150): [result([35, 1180, 64, 65])],
                                ('a', 1150): [result([201, 1180, 64, 65])]}, capacity=True)
        reward = {0: drink('a'), 1: drink('other'), 2: drink('b')}
        state = self.scan(context, reward, {0: drink('other'), 1: drink('b')}, {0, 1})
        self.assertTrue(context.calls)
        self.assertTrue(all(call['roi'][1] == 1150 for call in context.calls))
        self.assertEqual({1: 'b', 2: 'a'}, {slot: item['id'] for slot, item in state['inventory'].items()})
        self.assertEqual(reward, state['all_reward'])
        self.assertEqual({0, 1, 2, 3}, state['occupied'])
        self.assertEqual({2, 3}, state['unknown_inventory'])
        self.assertTrue(state['full'])
        self.assertNotIn('ProduceKeepDrinkFlag', context.nodes)

    def test_complete_inventory_skips_only_inventory_fallback(self):
        context = StateContext({('b', 760): [result([163, 828, 124, 124])],
                                ('a', 760): [result([298, 828, 124, 124])]})
        inventory = {0: drink('other'), 1: drink('a'), 2: drink('b'), 3: drink('other')}
        state = self.scan(context, {0: drink('other'), 2: drink('b')}, inventory, range(4))
        self.assertTrue(context.calls)
        self.assertTrue(all(call['roi'][1] == 760 for call in context.calls))
        self.assertEqual({1: 'a', 2: 'b'}, {slot: item['id'] for slot, item in state['reward'].items()})
        self.assertEqual({1, 2}, set(state['inventory']))
        self.assertEqual(inventory, state['all_inventory'])
        self.assertTrue(state['full'])
        self.assertFalse(state['capacity_confirmed'])
        self.assertNotIn('ProduceKeepDrinkFlag', context.nodes)

    def test_partial_inventory_is_scanned_even_when_occupancy_also_missed_a_bottle(self):
        context = StateContext({('a', 1150): [result([201, 1180, 64, 65])]})
        state = self.scan(context, {0: drink('other')}, {0: drink('other')}, {0})
        self.assertEqual({760, 1150}, {call['roi'][1] for call in context.calls})
        self.assertEqual({0, 2}, state['occupied'])
        self.assertEqual('a', state['inventory'][2]['id'])
        self.assertFalse(state['full'])
        self.assertFalse(state['visual_complete'])
        self.assertIn('ProduceKeepDrinkFlag', context.nodes)

    def test_all_unknown_rows_keep_both_fallbacks_and_full_marker(self):
        context = StateContext(full_marker=True)
        state = self.scan(context, {}, {}, set())
        self.assertEqual(4, len(context.calls))
        self.assertEqual({760, 1150}, {call['roi'][1] for call in context.calls})
        self.assertEqual({}, state['reward'])
        self.assertEqual({}, state['inventory'])
        self.assertEqual(set(), state['occupied'])
        self.assertTrue(state['full'])
        self.assertFalse(state['capacity_confirmed'])
        self.assertIn('ProduceKeepDrinkFlag', context.nodes)

    def test_capacity_proof_keeps_unknown_bottles_protected_without_extra_full_marker(self):
        context = StateContext(capacity=True)
        state = self.scan(context, {}, {}, set())
        self.assertEqual(4, len(context.calls))
        self.assertEqual({0, 1, 2, 3}, state['occupied'])
        self.assertEqual({0, 1, 2, 3}, state['unknown_inventory'])
        self.assertEqual({}, state['inventory'])
        self.assertTrue(state['full'])
        self.assertTrue(state['capacity_confirmed'])
        self.assertNotIn('ProduceKeepDrinkFlag', context.nodes)


class DrinkScanLatencyTests(unittest.TestCase):
    def test_empty_rows_use_two_calls_per_target_not_seven(self):
        context = Context()
        with patch('custom.produce.rewards.TARGET_NAMES', {'a': 'A', 'b': 'B'}):
            self.assertEqual(({}, {}), Action._scan_target_drinks(context, np.zeros((1280, 720, 3), np.uint8)))
        self.assertEqual(4, len(context.calls))
        self.assertTrue(all(p['count'] == 4 and p['ratio'] == .62 for p in context.calls))

    def test_duplicates_are_kept_and_names_are_bound_to_templates(self):
        image = np.full((1280, 720, 3), 77, np.uint8)
        context = Context({('b', 760): [result([163, 828, 124, 124]), result([298, 828, 124, 124])],
                           ('a', 1150): [result([201, 1180, 64, 65]), result([283, 1180, 64, 65])]})
        with patch('custom.produce.rewards.TARGET_NAMES', {'a': 'A', 'b': 'B'}):
            reward, inventory = Action._scan_target_drinks(context, image)
        self.assertEqual({0: 'b', 1: 'b'}, {i: row['id'] for i, row in reward.items()})
        self.assertEqual({2: 'a', 3: 'a'}, {i: row['id'] for i, row in inventory.items()})
        self.assertEqual(6, len(context.calls))
        self.assertTrue(np.all(image == 77))

    def test_implausible_cross_slot_boxes_are_not_names(self):
        context = Context({('a', 760): [result([170, 820, 320, 124]), result([310, 670, 110, 110])],
                           ('a', 1150): [result([200, 1151, 130, 60])]})
        with patch('custom.produce.rewards.TARGET_NAMES', {'a': 'A'}):
            self.assertEqual(({}, {}), Action._scan_target_drinks(context, np.zeros((1280, 720, 3), np.uint8)))

    @staticmethod
    def capture_context(frames):
        images = iter(frames)
        capture = lambda: SimpleNamespace(wait=lambda: SimpleNamespace(get=lambda: next(images)))
        return SimpleNamespace(tasker=SimpleNamespace(controller=SimpleNamespace(post_screencap=capture)))

    def test_unchanged_two_frames_only_do_one_identity_scan(self):
        frame = np.zeros((1280, 720, 3), np.uint8)
        current = frame.copy()
        current[:100] = 200  # irrelevant background does not invalidate bottles
        state = {'reward': {}, 'inventory': {}, 'occupied': set(), 'full': False}
        with patch.object(Action, '_scan_drink_state', return_value=state) as scan, patch('time.sleep'):
            image, found = Action._stable_drink_state(self.capture_context([current]), frame)
        self.assertIs(image, current)
        self.assertEqual(state, found)
        self.assertEqual(1, scan.call_count)

    def test_changed_rows_are_rescanned_then_deferred_never_click_old_frame(self):
        frames = [np.full((1280, 720, 3), value, np.uint8) for value in (0, 50, 100, 150)]
        state = {'reward': {}, 'inventory': {}, 'occupied': set(), 'full': False}
        with patch.object(Action, '_scan_drink_state', return_value=state) as scan, patch('time.sleep'):
            image, found = Action._stable_drink_state(self.capture_context(frames[1:]), frames[0])
        self.assertIs(image, frames[-1])
        self.assertGreaterEqual(scan.call_count, 2)
        self.assertLessEqual(scan.call_count, 4)
        self.assertTrue(found['aborted'])
        self.assertFalse(found['stable'])


if __name__ == '__main__':
    unittest.main()

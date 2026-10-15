import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent'))
from custom.action.produce import ProduceChooseRewardAuto as Action


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


class DrinkScanLatencyTests(unittest.TestCase):
    def test_empty_rows_use_two_calls_per_target_not_seven(self):
        context = Context()
        with patch('custom.action.produce.TARGET_NAMES', {'a': 'A', 'b': 'B'}):
            self.assertEqual(({}, {}), Action._scan_target_drinks(context, np.zeros((1280, 720, 3), np.uint8)))
        self.assertEqual(4, len(context.calls))
        self.assertTrue(all(p['count'] == 4 and p['ratio'] == .62 for p in context.calls))

    def test_duplicates_are_kept_and_names_are_bound_to_templates(self):
        image = np.full((1280, 720, 3), 77, np.uint8)
        context = Context({('b', 760): [result([163, 828, 124, 124]), result([298, 828, 124, 124])],
                           ('a', 1150): [result([201, 1180, 64, 65]), result([283, 1180, 64, 65])]})
        with patch('custom.action.produce.TARGET_NAMES', {'a': 'A', 'b': 'B'}):
            reward, inventory = Action._scan_target_drinks(context, image)
        self.assertEqual({0: 'b', 1: 'b'}, {i: row['id'] for i, row in reward.items()})
        self.assertEqual({2: 'a', 3: 'a'}, {i: row['id'] for i, row in inventory.items()})
        self.assertEqual(6, len(context.calls))
        self.assertTrue(np.all(image == 77))

    def test_implausible_cross_slot_boxes_are_not_names(self):
        context = Context({('a', 760): [result([170, 820, 320, 124]), result([310, 670, 110, 110])],
                           ('a', 1150): [result([200, 1151, 130, 60])]})
        with patch('custom.action.produce.TARGET_NAMES', {'a': 'A'}):
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

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
from utils.runtime_compat import agent_identifier, framework_dependency_ready, framework_pin
from custom.produce.battle import ProduceCardsAuto


class RuntimeCompatibilityTest(unittest.TestCase):
    def test_extended_gui_arguments(self):
        self.assertEqual("abc", agent_identifier(["abc", "socket_id=abc", "instance_name=一键培育"]))

    def test_legacy_gui_arguments(self):
        self.assertEqual("abc", agent_identifier(["abc"]))
        with self.assertRaises(ValueError):
            agent_identifier(["instance_name=一键培育"])

    def test_pin_is_required(self):
        path = Mock()
        path.read_text.return_value = "maafw\nloguru\n"
        self.assertIsNone(framework_pin(path))
        self.assertFalse(framework_dependency_ready(path))

    def test_installed_version_must_match_even_if_resource_version_unchanged(self):
        path = Mock()
        path.read_text.return_value = "maafw==5.13.0\nloguru\n"
        with patch("utils.runtime_compat.version", return_value="5.12.2"):
            self.assertFalse(framework_dependency_ready(path))
        with patch("utils.runtime_compat.version", return_value="5.13.0"):
            self.assertTrue(framework_dependency_ready(path))

    def test_hand_bounds_keep_overlapping_cards_but_reject_below_hand(self):
        candidates = [{"box": [100, y, 150, 220]} for y in [700, 800, 884, 1050]]
        self.assertEqual([800, 884], [c["box"][1] for c in ProduceCardsAuto._get_full_hand_candidates(candidates)])

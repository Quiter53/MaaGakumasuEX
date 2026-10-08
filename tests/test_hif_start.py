import sys
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
from custom.hif import configure_hif_start_routes, read_hif_start, read_hif_resume


def result(text="プロデュース開始", box=(265, 1060, 200, 35), hit=True):
    return SimpleNamespace(hit=hit, filtered_results=[SimpleNamespace(text=text, box=box)])


class HIFStartTest(unittest.TestCase):
    def test_both_header_and_exact_start_button_are_required(self):
        context = Mock()
        context.run_recognition.side_effect = [result("開始確認"), result()]
        self.assertEqual([265, 1060, 200, 35], read_hif_start(context, None))
        context.run_recognition.side_effect = [result(hit=False)]
        self.assertIsNone(read_hif_start(context, None))
        context.run_recognition.side_effect = [result("開始確認"), result("プロデュース開始の説明")]
        self.assertIsNone(read_hif_start(context, None))
        context.run_recognition.side_effect = [result("開始確認"), result(box=(265, 650, 200, 35))]
        self.assertIsNone(read_hif_start(context, None))

    def test_hif_routes_survive_skip_idol_and_preserve_item_settings(self):
        nodes = {
            "ProduceEntryFlag": {"next": "ProduceEntryHIF"},
            "ProduceLoop": {"next": ["[JumpBack]ProduceDisconnectError", "ProduceSkipPreparation", "ProduceHomeFlag"]},
            "ProduceChooseDifficulty": {"next": ["ProduceLackAP", "ProduceChooseIdolNext"]},
            "ProduceChooseIdolNext": {"next": ["ProduceChooseSupport"]},
        }
        context = Mock()
        context.get_node_data.side_effect = lambda name: nodes.get(name, {})
        self.assertTrue(configure_hif_start_routes(context))
        updates = context.override_pipeline.call_args.args[0]
        self.assertEqual(["ProduceHIFBeginFlag", "ProduceLackAP", "ProduceChooseIdolNext"], updates["ProduceChooseDifficulty"]["next"])
        self.assertEqual(["ProduceHIFBeginFlag", "ProduceChooseSupport"], updates["ProduceChooseIdolNext"]["next"])
        loop = updates["ProduceLoop"]["next"]
        self.assertEqual("[JumpBack]ProduceDisconnectError", loop[0])
        self.assertLess(loop.index("ProduceHomeFlag"), loop.index("ProduceSkipPreparation"))
        self.assertNotIn("ProduceUseNote", updates)
        self.assertNotIn("ProduceUsePt", updates)
        self.assertNotIn("ProduceLoop", updates["ProduceLoop"]["next"])

    def test_normal_scenarios_do_not_change(self):
        for entry in ("ProduceEntry", "ProduceEntryNIA", None):
            context = Mock()
            context.get_node_data.return_value = {"next": entry} if entry else None
            self.assertFalse(configure_hif_start_routes(context))
            context.override_pipeline.assert_not_called()

    def test_framework_parsed_next_targets_are_supported(self):
        context = Mock()
        nodes = {"ProduceEntryFlag": {"next": [{"name": "ProduceEntryHIF", "jump_back": False, "anchor": False}]},
                 "ProduceLoop": {"next": [{"name": "ProduceDisconnectError", "jump_back": True, "anchor": False},
                                          {"name": "ProduceHomeFlag", "jump_back": False, "anchor": False}]}}
        context.get_node_data.side_effect = lambda name: nodes.get(name, {})
        self.assertTrue(configure_hif_start_routes(context))
        updates = context.override_pipeline.call_args.args[0]
        self.assertEqual(["[JumpBack]ProduceDisconnectError", "ProduceHIFBeginFlag", "ProduceHIFResumeFlag", "ProduceHomeFlag"],
                         updates["ProduceLoop"]["next"])

    def test_active_entry_cannot_reenter_preparation_on_a_stale_frame(self):
        data = json.loads((ROOT / "assets/resource/base/pipeline/ProduceHIF.json").read_text(encoding="utf-8"))
        self.assertNotIn("ProduceHIFBeginFlag", data["ProduceEntryHIF"]["next"])

    def test_resume_requires_a_cultivation_specific_match(self):
        context = Mock()
        context.run_recognition.return_value = SimpleNamespace(hit=False)
        self.assertIsNone(read_hif_resume(context, None))
        names = [call.args[0] for call in context.run_recognition.call_args_list]
        self.assertNotIn("ProduceButton", names)
        self.assertNotIn("ProduceEntryHIF", names)
        self.assertNotIn("Click_1", names)
        context.run_recognition.return_value = SimpleNamespace(hit=True, best_result=SimpleNamespace(box=[60, 920, 130, 160]))
        self.assertEqual([60, 920, 130, 160], read_hif_resume(context, None))

    def test_repeated_setup_does_not_duplicate_confirmation(self):
        context = Mock()
        nodes = {"ProduceEntryFlag": {"next": ["[JumpBack]ProduceDisconnectError", "ProduceEntryHIF"]}}
        context.get_node_data.side_effect = lambda name: nodes.get(name, {"next": ["ProduceHIFBeginFlag", "ProduceChooseSupport"]})
        configure_hif_start_routes(context)
        updates = context.override_pipeline.call_args.args[0]
        for name in ("ProduceLoop", "ProduceChooseDifficulty", "ProduceChooseIdolNext"):
            self.assertEqual(1, updates[name]["next"].count("ProduceHIFBeginFlag"))


if __name__ == "__main__":
    unittest.main()

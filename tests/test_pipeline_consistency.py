"""各剧本流水线写法一致性：共享守卫节点、无重复路由、统一的 v2 节点写法与参数格式。"""

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
from custom.params import action_mode

PIPELINE = ROOT / "assets/resource/base/pipeline"
ENTRIES = (("Produce.json", "ProduceEntry"), ("ProduceNIA.json", "ProduceEntryNIA"), ("ProduceHIF.json", "ProduceEntryHIF"))
SHARED_GUARDS = (
    "[JumpBack]ProduceCancelDrinkDiscard",
    "[JumpBack]ProduceDisconnectError",
    "[JumpBack]ProduceDisconnectTitle",
    "[JumpBack]ProduceDisconnectHome",
    "ProduceMemoryFlag",
    "[JumpBack]ProduceKeepDrinkFlag",
    "[JumpBack]ProduceChooseDrinkFastFlag",
    "ProduceExit",
    "ProduceGeneration",
    "ProducePhotoSelectResume",
    "ProducePhotoConfirmResume",
    "[JumpBack]ProduceCardsFlag",
    "[JumpBack]ProduceChooseItemFlag",
    "[JumpBack]ProduceChooseGetFlag",
    "[JumpBack]ProduceChooseStrengthenFlag",
    "[JumpBack]ProduceRecognitionWorkOptions",
    "[JumpBack]ProduceWorkOptionsFallback",
    "[JumpBack]ProduceSkipChatFlag",
    "[JumpBack]ProduceSkip",
)

# NIA 特例（保留）：工作选择与三选项识别排在奖励/恢复弹窗之前，沿用 EX 原有顺序。
WORK_OPTIONS = ("[JumpBack]ProduceRecognitionWorkOptions", "[JumpBack]ProduceWorkOptionsFallback")
WORK_OPTIONS_FIRST = {"ProduceEntryNIA"}


def load_all():
    nodes = {}
    for path in sorted(PIPELINE.glob("*.json")):
        nodes.update({name: (path.name, node) for name, node in json.loads(path.read_text(encoding="utf-8")).items()})
    return nodes


class PipelineConsistencyTest(unittest.TestCase):
    def test_every_scenario_entry_shares_the_common_guards_in_the_same_order(self):
        for file, entry in ENTRIES:
            with self.subTest(entry=entry):
                routes = json.loads((PIPELINE / file).read_text(encoding="utf-8"))[entry]["next"]
                guards = [g for g in SHARED_GUARDS if not (entry in WORK_OPTIONS_FIRST and g in WORK_OPTIONS)]
                positions = [routes.index(guard) for guard in guards]
                self.assertEqual(sorted(positions), positions)
                self.assertEqual("[JumpBack]Click_1", routes[-1])

    def test_nia_keeps_work_options_right_after_work_flag(self):
        routes = json.loads((PIPELINE / "ProduceNIA.json").read_text(encoding="utf-8"))["ProduceEntryNIA"]["next"]
        start = routes.index("[JumpBack]ProduceWorkFlag")
        self.assertEqual(list(WORK_OPTIONS), routes[start + 1 : start + 3])
        self.assertLess(start, routes.index("[JumpBack]ProduceChooseItemFlag"))

    def test_no_node_routes_to_the_same_target_twice(self):
        for name, (file, node) in load_all().items():
            for key in ("next", "on_error"):
                routes = node.get(key) or []
                with self.subTest(node=name, key=key):
                    self.assertEqual(len(routes), len(set(routes)), file)

    def test_produce_nodes_use_the_nested_recognition_and_action_style(self):
        for name, (file, node) in load_all().items():
            if not file.startswith("Produce"):
                continue
            with self.subTest(node=name):
                self.assertNotIsInstance(node.get("recognition", {}), str)
                self.assertNotIsInstance(node.get("action", {}), str)

    def test_custom_params_are_json_objects(self):
        for name, (file, node) in load_all().items():
            for kind in ("recognition", "action"):
                param = node.get(kind, {}).get("param", {}) if isinstance(node.get(kind), dict) else {}
                value = param.get(f"custom_{kind}_param")
                if value is not None:
                    with self.subTest(node=name):
                        self.assertIsInstance(value, (dict, list))

    def test_click_then_wait_nodes_poll_their_confirmation_twice_per_second(self):
        # 点击后等待确认按钮/下一步的节点：轮询间隔从默认 1000 ms 缩短为 500 ms；
        # 被等待的确认节点仍保留默认 pre_delay（防误触不变）。
        nodes = load_all()
        for name in ("ProduceChooseFirst", "ProduceChooseSkip", "ProduceShoppingBuy", "ProduceChooseRest", "ProduceUseDrink",
                     "ProduceFailedRetry", "ProduceNIAFailedRetry", "ProduceHIFCopyExchangeFlag"):
            with self.subTest(node=name):
                node = nodes[name][1]
                self.assertEqual(500, node["rate_limit"])
                for route in node["next"]:
                    target = nodes[route.split("]")[-1]][1]
                    if "Disconnect" not in route:
                        self.assertNotEqual(0, target.get("pre_delay", 200))

    def test_action_mode_accepts_framework_serialized_params(self):
        for raw in ('{"mode":"reset"}', '"reset"', "reset", {"mode": "reset"}):
            self.assertEqual("reset", action_mode(raw))
        self.assertEqual("", action_mode(""))
        self.assertEqual("", action_mode(None))
        for value in ('{}', '[]', 'null', '42', {}):
            self.assertEqual("", action_mode(value))

    def test_jumpback_custom_actions_yield_on_error(self):
        nodes = load_all()
        for name in ("ProduceSkipED", "ProduceSkipShow", "ProduceSkipChatFlag", "ProduceFollow"):
            self.assertEqual(["ProduceActionYield"], nodes[name][1]["on_error"])

    def test_guide_reset_and_done_reach_their_mode_branches(self):
        from unittest.mock import patch

        from custom.produce.shop import GuidanceCustomizationState, ProduceGuideCustomizeAuto

        nodes = load_all()
        for name, expected in (("ProduceGuideReset", "reset"), ("ProduceGuideDone", "done")):
            raw = json.dumps(nodes[name][1]["action"]["param"]["custom_action_param"], separators=(",", ":"))
            with self.subTest(node=name), patch.object(GuidanceCustomizationState, "_visit_state") as state:
                self.assertTrue(ProduceGuideCustomizeAuto().run(object(), SimpleNamespace(custom_action_param=raw)))
                updates = state.call_args.kwargs
                self.assertEqual(expected == "reset", updates.get("active"))


if __name__ == "__main__":
    unittest.main()

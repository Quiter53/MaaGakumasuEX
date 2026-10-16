"""Module ownership, framework exports and moved memory assets."""

import ast
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

from custom import hif
from custom.action import produce as entry
from custom.produce import shop, battle, shared, options, rewards
from custom.produce.memory import modes, diamond


def test_action_entry_exports_the_same_objects_once():
    assert entry.ProduceCardsAuto is battle.ProduceCardsAuto
    assert entry.ProduceChooseRewardAuto is rewards.ProduceChooseRewardAuto
    assert entry.ProduceShoppingAuto is shop.ProduceShoppingAuto
    assert entry.ProduceHIFChooseOutingAuto is options.ProduceHIFChooseOutingAuto
    assert len(entry.__all__) == len(set(entry.__all__)) == 26
    for name in entry.__all__:
        assert getattr(entry, name).__module__.startswith("custom.produce.")
    # No duplicated implementations behind the compatibility entry.
    tree = ast.parse((ROOT / "agent/custom/action/produce.py").read_text("utf8"))
    assert not any(isinstance(node, (ast.ClassDef, ast.FunctionDef)) for node in tree.body)


def test_all_policies_share_one_budget_and_one_hif_reader():
    assert battle.shared is rewards.shared is shop.shared is options.shared is shared
    assert hif.read_schedule.__module__ == hif.read_outing_choices.__module__ == "custom.hif"
    assert not (ROOT / "agent/custom/hif_schedule.py").exists()


def test_memory_paths_keep_install_root_and_load_the_same_asset():
    assert Path(modes.__file__).resolve().parents[4] == ROOT
    assert diamond.ROOT == ROOT / "resource/base/image/produce/memory"
    assert not (ROOT / "agent/custom/memory_modes.py").exists()
    with np.load(Path(modes.__file__).with_name("memory_badges.npz")) as data:
        templates = modes.plus_templates()
        assert len(templates) == len(data.files) > 0
        assert all(np.array_equal(actual, data[key]) for actual, key in zip(templates, data.files))

"""Shared ID-based UI/acquisition/battle contract. No screen coordinates here."""

import json
from pathlib import Path
from functools import lru_cache
from collections.abc import Mapping

CATALOG = {row["id"]: row for row in json.loads(Path(__file__).with_name("drink_catalog.json").read_text(encoding="utf-8"))}
POLICY_PATH = Path("config/drink_policy.json")
TIMINGS = ("opening", "before_card", "last_turns")
VINEGAR_ID = "767772"


def defaults():
    return {
        key: {"target": row["target"], "timing": "rule_combo" if key == VINEGAR_ID else "opening", "card": "", "turns": 1}
        for key, row in CATALOG.items()
    }


def normalize(document):
    result = defaults()
    if not isinstance(document, dict) or document.get("version", 1) != 1:
        raise ValueError("unsupported drink policy")
    entries = document.get("drinks", {})
    if not isinstance(entries, dict):
        raise ValueError("drink rules must be an object")
    for key, value in entries.items():
        if key not in result or not isinstance(value, dict):
            continue
        result[key]["target"] = value.get("target", result[key]["target"]) is True
        timing = value.get("timing", "opening")
        if key == VINEGAR_ID:
            timing = "rule_combo"
        elif timing not in TIMINGS:
            raise ValueError(f"invalid timing: {key}")
        result[key]["timing"] = timing
        result[key]["card"] = str(value.get("card", ""))
        turns = value.get("turns", 1)
        if isinstance(turns, bool) or not isinstance(turns, int) or not 1 <= turns <= 99:
            raise ValueError(f"invalid turns: {key}")
        result[key]["turns"] = turns
    return result


@lru_cache(maxsize=8)
def _read(path, stamp):
    return normalize(json.loads(Path(path).read_text(encoding="utf-8-sig")))


def load_policy(path=None):
    """Missing file preserves defaults; corrupt file must not silently drink all."""
    path = Path(path or POLICY_PATH).resolve()
    try:
        stat = path.stat()
    except FileNotFoundError:
        return defaults()
    return {key: dict(value) for key, value in _read(str(path), (stat.st_mtime_ns, stat.st_size)).items()}


def due(rule, *, opening=False, remaining=None, card_name=None):
    if rule["timing"] == "rule_combo":
        return False  # Requires a fresh hand decision; never final-turn draining.
    # Final turn is always the safety net, regardless of the configured timing.
    if remaining == 1:
        return True
    if rule["timing"] == "opening":
        return opening
    if rule["timing"] == "last_turns":
        return remaining is not None and 1 <= remaining <= rule["turns"]
    return bool(card_name and rule["card"] and card_name.rstrip("+＋") == rule["card"].rstrip("+＋"))


class TargetNames(Mapping):
    """Compatibility mapping: callers keep existing APIs, settings use mtime cache."""

    def _values(self):
        return {key: CATALOG[key]["jp"] for key, value in load_policy().items() if value["target"]}

    def __getitem__(self, key):
        return self._values()[key]

    def __iter__(self):
        return iter(self._values())

    def __len__(self):
        return len(self._values())


TARGET_NAMES = TargetNames()

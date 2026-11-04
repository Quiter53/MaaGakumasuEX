"""Independent fixed expectations and native/Python policy conformance."""
import copy
import json
import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
from custom import card_rules as rules

ORACLE = json.loads((ROOT / "tests/fixtures/card_rule_policy_oracle.json").read_text("utf-8"))
CATALOG = {card["name"] for card in json.loads((ROOT / "assets/config/card_library.json").read_text("utf-8"))["cards"]}


def oracle_rule(patch):
    rule = copy.deepcopy(ORACLE["baseRule"])
    rule.update(copy.deepcopy(patch))
    return rule


@pytest.mark.parametrize("case", ORACLE["policyCases"], ids=lambda case: case["id"])
def test_fixed_policy_expectations(case):
    rule = oracle_rule(case["rule"])
    state = case["state"]
    expected = case["expected"]
    assert rules.allowed(rule, case["turn"], state) is expected["allowed"]
    assert rules.priority(rule, case["turn"], state) == expected["priority"]
    assert rules.effective_tier(case["rarity"], rule) == expected["tier"]
    assert rules.effective_extra_action(case["extra"], rule) is expected["effectiveExtra"]
    preset = {"rules": [rule]}
    assert rules.sort_key(preset, rule["card"], case["rarity"], case["extra"], case["turn"], 2, state) == (
        int(expected["allowed"]), int(expected["effectiveExtra"]), expected["tier"], expected["priority"], -2
    )


@pytest.mark.parametrize("case", ORACLE["validationCases"], ids=lambda case: case["id"])
def test_fixed_document_validation(case):
    document = rules.defaults()
    preset = rules.active_preset(document)
    preset["rules"] = [oracle_rule(case["rule"]), *[oracle_rule(patch) for patch in case["additionalRules"]]]
    preset.update(copy.deepcopy(case["preset"]))
    if case["valid"]:
        rules.validate(document, CATALOG)
    else:
        with pytest.raises(ValueError):
            rules.validate(document, CATALOG)


def test_legacy_document_without_new_optional_fields_still_validates():
    document = rules.defaults()
    for preset in document["presets"]:
        preset.pop("hold", None)
        for rule in preset["rules"]:
            for name in ("conditions", "priorityOverrides", "followUp"):
                rule.pop(name, None)
    rules.validate(document, CATALOG)


@pytest.mark.parametrize("count,accepted", [(1, True), (50, True), (51, False)])
def test_followup_target_count_boundary(count, accepted):
    document = rules.defaults()
    rule = oracle_rule({"followUp": {"cards": sorted(CATALOG - {"イメトレ"})[:count], "waitTurns": 0, "targetPolicy": "normal"}})
    rules.active_preset(document)["rules"] = [rule]
    if accepted:
        rules.validate(document, CATALOG)
    else:
        with pytest.raises(ValueError):
            rules.validate(document, CATALOG)


def test_override_priority_keeps_extra_action_and_tier_ahead_of_score():
    override = [{"conditions": [[{"field": "remaining", "min": 1, "max": 30}]], "priority": 100}]
    rule = oracle_rule({"priorityOverrides": override})
    preset = {"rules": [rule]}
    boosted = rules.sort_key(preset, "イメトレ", "SR", False, 6)
    assert rules.sort_key(preset, "other", "R", True, 6) > boosted
    assert rules.sort_key(preset, "other", "SSR", False, 6) > boosted
    assert boosted > rules.sort_key(preset, "other", "SR", False, 6)


def native_output():
    path = os.environ.get("CARD_RULE_VECTORS")
    if not path:
        if os.environ.get("CARD_RULE_REQUIRE_VECTORS") == "1":
            pytest.fail("Release verification requires CARD_RULE_VECTORS from this build's CardRulesUi run")
        pytest.skip("Run CardRulesUi and supply CARD_RULE_VECTORS for native/Python differential verification")
    return Path(path)


def test_native_python_policy_equivalence():
    envelope = json.loads(native_output().read_text("utf-8"))
    assert isinstance(envelope, dict) and envelope.get("schemaVersion") == 2, "Native policy vectors must include the extended schema"
    vectors = envelope["cases"]
    assert len(vectors) >= 3600
    ids = [vector.get("id") for vector in vectors]
    oracle_ids = {case["id"] for case in ORACLE["policyCases"]}
    assert oracle_ids <= set(ids), "Native vectors did not exercise every fixed state/condition case"
    assert all(ids.count(case_id) == 1 for case_id in oracle_ids), "Oracle vector IDs must be unique"
    for vector in vectors:
        rule, turn, state = vector["rule"], vector["turn"], vector.get("state")
        assert vector["result"] == [
            rules.allowed(rule, turn, state), rules.effective_tier(vector["rarity"], rule), rules.priority(rule, turn, state)
        ], vector.get("id", vector)
        assert vector["effectiveExtra"] == rules.effective_extra_action(vector["extra"], rule), vector.get("id", vector)
    print(f"Native/Python equivalence: {len(vectors)} cases, {len(oracle_ids)} fixed state/condition cases")


def test_native_profession_share_round_trip_reaches_python_policy():
    output = native_output().parent
    share = json.loads((output / "profession-share.json").read_text("utf-8"))
    before = json.loads((output / "profession-before-document.json").read_text("utf-8"))
    imported = json.loads((output / "profession-import-document.json").read_text("utf-8"))
    assert set(share) == {"format", "version", "career", "rules", "hold"}
    assert share["format"] == "maagakumasu-ex-profession" and share["version"] == 1
    assert all("id" not in rule for rule in share["rules"])
    rules.validate(imported, CATALOG)
    assert imported["active"] == before["active"]
    target = rules.active_preset(imported)
    assert target["career"] == share["career"]
    assert [preset for preset in imported["presets"] if preset["id"] != imported["active"]] == [
        preset for preset in before["presets"] if preset["id"] != before["active"]
    ]
    assert [{key: value for key, value in rule.items() if key != "id"} for rule in target["rules"]] == share["rules"]
    old_ids = {rule["id"] for preset in before["presets"] for rule in preset["rules"]}
    new_ids = {rule["id"] for rule in target["rules"]}
    assert len(new_ids) == len(target["rules"]) and new_ids.isdisjoint(old_ids)
    assert target["hold"] == share["hold"] == {"cards": ["届いて！"], "ordered": True}
    rule = rules.rule_for(target, "イメトレ")
    assert rule is not None and rule["enabled"] is True and rule["singleCopy"] is True
    assert rule["conditions"] == [[{"field": "remaining", "min": 3, "max": 7}]]
    assert rule["followUp"] == {"cards": ["届いて！"], "waitTurns": 2, "targetPolicy": "reserveFirst"}
    assert any(saved["enabled"] is False for saved in target["rules"])
    assert rules.allowed(rule, 2, {"fullPower": 75, "stance": "全力"}) is False
    assert rules.allowed(rule, 3, {"fullPower": 75, "stance": "全力"}) is True
    assert rules.priority(rule, 6, {"fullPower": 75, "stance": "全力"}) == 73
    assert rules.priority(rule, 6, {"fullPower": 10, "stance": "全力"}) == 96
    assert rules.priority(rule, 6, {"fullPower": 10, "stance": "温存"}) == 60

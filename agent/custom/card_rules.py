"""Persisted battle rules shared with the native Avalonia editor.

Pure timing and ordering functions; no game controller is created.
"""

import json
import math
from pathlib import Path

TIERS = ("N", "R", "SR", "SSR", "LR")
CAREERS = ("好调", "集中", "干劲", "好印象", "强气", "全力")
STANCES = ("全力", "強気", "温存")
POLICY_PATH = Path(__file__).resolve().parents[2] / "config/card_rules.json"
# agent/custom -> install root (parents[2])


def name_key(value):
    value = str(value or "").strip().translate(str.maketrans({"＋": "+", "！": "!", "？": "?", "〜": "~", "～": "~"})).rstrip("+")
    return "".join(value.split())


def defaults():
    return json.loads(Path(__file__).with_name("card_rules.defaults.json").read_text(encoding="utf-8"))


def validate(document, catalog=None):
    def require(condition, message):
        if not condition:
            raise ValueError(message)

    def integer(value, low, high):
        return type(value) is int and low <= value <= high

    def conditions(value, *, nonempty=False):
        require(isinstance(value, list) and len(value) <= 8 and (value or not nonempty), "条件组数量无效")
        for group in value:
            require(isinstance(group, list) and 1 <= len(group) <= 8, "条件组应有 1–8 个条件")
            for atom in group:
                require(isinstance(atom, dict), "条件格式无效")
                field = atom.get("field")
                if field == "stance":
                    require(set(atom) <= {"field", "value"} and atom.get("value") in STANCES, "指针条件无效")
                else:
                    require(field in ("remaining", "fullPower"), "条件字段无效")
                    low, high = (1, 30) if field == "remaining" else (0, 999)
                    require(set(atom) <= {"field", "min", "max"} and ("min" in atom or "max" in atom), "条件范围字段无效")
                    require(all(integer(atom[key], low, high) for key in ("min", "max") if key in atom)
                            and atom.get("min", low) <= atom.get("max", high), "条件范围无效")

    def card_list(value):
        require(isinstance(value, list) and len(value) <= 50, "目标卡牌列表无效")
        require(all(isinstance(card, str) and name_key(card) and (catalog is None or card in catalog) for card in value), "目标卡牌无效")
        require(len({name_key(card) for card in value}) == len(value), "目标卡牌重复")

    require(isinstance(document, dict) and document.get("version") == 2, "不支持的规则文件版本")
    presets = document.get("presets")
    require(isinstance(presets, list) and 1 <= len(presets) <= 200, "预设数量无效")
    ids, names = set(), set()
    for preset in presets:
        require(isinstance(preset, dict), "预设格式无效")
        pid, name = preset.get("id"), preset.get("name")
        require(isinstance(pid, str) and 0 < len(pid) <= 100 and pid not in ids, "预设 ID 重复或无效")
        require(isinstance(name, str) and 0 < len(name.strip()) <= 30 and name not in names, "预设名称重复或无效")
        ids.add(pid)
        names.add(name)
        require(preset.get("career") in (*CAREERS, "通用"), "职业无效")
        require(type(preset.get("builtin")) is bool, "内置预设标记无效")
        if preset["builtin"]:
            require(pid == f"career-{CAREERS.index(preset['career'])}" and name == preset["career"], "内置预设不可更名")
        rules, hand = preset.get("rules"), preset.get("hand")
        require(isinstance(hand, list) and 1 <= len(hand) <= 5, "预览手牌应为 1–5 张")
        require(all(isinstance(n, str) and n and (catalog is None or n in catalog) for n in hand), "预览手牌不在卡牌库中")
        require(isinstance(rules, list) and len(rules) <= 1000, "规则列表无效")
        hold = preset.get("hold", {"cards": [], "ordered": False})
        require(isinstance(hold, dict) and set(hold) <= {"cards", "ordered"} and type(hold.get("ordered")) is bool, "保留名单格式无效")
        card_list(hold.get("cards"))
        cards, rule_ids = set(), set()
        follow_edges = {}
        for rule in rules:
            require(isinstance(rule, dict), "卡牌规则格式无效")
            card, rid = rule.get("card"), rule.get("id")
            require(isinstance(card, str) and card and (catalog is None or card in catalog), "规则卡牌不在卡牌库中")
            require(name_key(card) not in cards, "普通与强化卡只能配置一条规则")
            require(isinstance(rid, str) and rid and rid not in rule_ids, "规则 ID 重复或无效")
            cards.add(name_key(card))
            rule_ids.add(rid)
            require(type(rule.get("enabled")) is bool and type(rule.get("limited")) is bool, "规则开关无效")
            require(type(rule.get("singleCopy", False)) is bool, "限一张开关无效")
            require(type(rule.get("customExtraAction", False)) is bool, "自定义再动开关无效")
            conditions(rule.get("conditions", []))
            overrides = rule.get("priorityOverrides", [])
            require(isinstance(overrides, list) and len(overrides) <= 16, "条件优先级数量无效")
            for override in overrides:
                require(isinstance(override, dict) and set(override) <= {"conditions", "priority"}
                        and integer(override.get("priority"), 0, 100), "条件优先级无效")
                conditions(override.get("conditions"), nonempty=True)
            follow = rule.get("followUp")
            if follow is not None:
                require(isinstance(follow, dict) and set(follow) <= {"cards", "waitTurns", "targetPolicy"}, "连携格式无效")
                card_list(follow.get("cards"))
                require(bool(follow["cards"]) and name_key(card) not in {name_key(n) for n in follow["cards"]}, "连携目标不可为空或包含自身")
                require(integer(follow.get("waitTurns"), 0, 6), "连携等待回合无效")
                require(follow.get("targetPolicy") in ("normal", "reserveFirst", "comboOnly"), "连携目标策略无效")
                follow_edges[name_key(card)] = {name_key(n) for n in follow["cards"]}
            target = rule.get("comboTarget", "")
            require(
                isinstance(target, str)
                and (
                    not target
                    or (name_key(card) == "国民的アイドル" and name_key(target) != name_key(card) and (catalog is None or target in catalog))
                ),
                "国民配合目标无效",
            )
            require(integer(rule.get("min"), 1, 30) and integer(rule.get("max"), 1, 30) and rule["min"] <= rule["max"], "使用回合区间无效")
            require(rule.get("mode") in ("linear", "step"), "曲线类型无效")
            require(rule.get("tier") is None or rule["tier"] in TIERS, "排序档位无效")
            points = rule.get("points")
            require(isinstance(points, list) and 2 <= len(points) <= 30, "曲线需有 2–30 个节点")
            turns = set()
            for point in points:
                require(
                    isinstance(point, list) and len(point) == 2 and integer(point[0], 1, 30) and integer(point[1], 0, 100), "曲线节点数值无效"
                )
                require(point[0] not in turns, "曲线节点回合重复")
                turns.add(point[0])
        incoming = {card: 0 for card in follow_edges}
        for targets in follow_edges.values():
            for target in targets:
                incoming[target] = incoming.get(target, 0) + 1
        ready = [card for card, count in incoming.items() if count == 0]
        visited = 0
        while ready:
            card = ready.pop()
            visited += 1
            for target in follow_edges.get(card, ()):
                incoming[target] -= 1
                if incoming[target] == 0:
                    ready.append(target)
        require(visited == len(incoming), "连携目标形成循环")
    require(document.get("active") in ids, "当前预设不存在")
    require(all(any(p["id"] == f"career-{i}" and p["builtin"] for p in presets) for i in range(6)), "内置职业预设缺失")
    return document


def active_preset(document):
    return next(p for p in document["presets"] if p["id"] == document["active"])


def rule_for(preset, card):
    key = name_key(card)
    return next((r for r in preset["rules"] if name_key(r["card"]) == key and r["enabled"]), None)


def conditions_match(groups, remaining, state=None):
    values = {**(state or {}), "remaining": remaining}

    def matches(atom):
        field = atom["field"]
        value = values.get(field)
        if field == "stance":
            return value in STANCES and value == atom["value"]
        if field not in ("remaining", "fullPower"):
            return False
        low, high = (1, 30) if field == "remaining" else (0, 999)
        return type(value) is int and low <= value <= high and atom.get("min", low) <= value <= atom.get("max", high)

    return not groups or any(group and all(matches(atom) for atom in group) for group in groups)


def allowed(rule, remaining, state=None):
    if not rule or not rule["enabled"]:
        return True
    timing = not rule["limited"] or (type(remaining) is int and rule["min"] <= remaining <= rule["max"])
    return timing and conditions_match(rule.get("conditions", []), remaining, state)


def priority(rule, remaining, state=None):
    if not rule or not rule["enabled"]:
        return 50
    for override in rule.get("priorityOverrides", []):
        if conditions_match(override["conditions"], remaining, state):
            return override["priority"]
    if type(remaining) is not int:
        return 50
    points = sorted(rule["points"], reverse=True)
    if remaining >= points[0][0]:
        return points[0][1]
    if remaining <= points[-1][0]:
        return points[-1][1]
    for (a, v), (b, w) in zip(points, points[1:]):
        if b <= remaining <= a:
            if rule["mode"] == "step":
                return w if remaining == b else v
            return math.floor((v + (w - v) * (a - remaining) / (a - b)) * 10 + 0.5) / 10
    return 50


def effective_tier(rarity, rule):
    base = TIERS.index(rarity) if rarity in TIERS else -1
    if rule and rule["enabled"] and rule.get("tier") in TIERS:
        if rarity == "SSR" and rule["tier"] in ("R", "SR"):
            return TIERS.index(rule["tier"])
        base = max(base, min(3, TIERS.index(rule["tier"])))
    return base


def effective_extra_action(extra, rule):
    """A saved override promotes strategy only; it never removes real extra action."""
    return extra is True or extra == "yes" or bool(rule and rule["enabled"] and rule.get("customExtraAction", False))


def sort_key(preset, card, rarity, extra, remaining, slot=0, state=None):
    rule = rule_for(preset, card)
    return (
        int(allowed(rule, remaining, state)),
        int(effective_extra_action(extra, rule)),
        effective_tier(rarity, rule),
        priority(rule, remaining, state),
        -slot,
    )


class RuleStore:
    """mtime cache with last-good retention on interrupted/external bad writes."""

    def __init__(self, path=None):
        self.path = Path(path or POLICY_PATH)
        self.stamp = None
        self.document = defaults()
        self.error = None

    def load(self):
        try:
            stat = self.path.stat()
            stamp = (stat.st_mtime_ns, stat.st_size)
        except FileNotFoundError:
            # Keep last good policy if an external editor temporarily removes it.
            return self.document
        if stamp != self.stamp:
            self.stamp = stamp
            try:
                self.document = validate(json.loads(self.path.read_text(encoding="utf-8-sig")))
                self.error = None
            except (ValueError, OSError, TypeError, KeyError) as exc:
                self.error = str(exc)
                from loguru import logger

                logger.warning(f"出牌规则读取失败，继续上次有效规则：{exc}")
        return self.document

    def preset(self):
        return active_preset(self.load())


_cultivation_store = None


def cultivation_preset():
    global _cultivation_store
    if _cultivation_store is None:
        _cultivation_store = RuleStore()
    return _cultivation_store.preset()


def single_copy_cards():
    return {name_key(r["card"]) for r in cultivation_preset()["rules"] if r["enabled"] and r.get("singleCopy", False)}


def cultivation_rarity(card, rarity):
    """Apply the active saved tier to strategy, keeping recognition data real.

    Timing and curve scores apply only in battle. Missing identities/rarities
    cannot acquire a tier from a rule. This never changes acquisition counts.
    """
    if not card or rarity not in TIERS:
        return rarity
    rule = rule_for(cultivation_preset(), card)
    # Lowering SSR is a battle-only ordering preference, never a reason to
    # discard or undervalue it during acquisition or memory settlement.
    return TIERS[max(TIERS.index(rarity), effective_tier(rarity, rule))]


def combo_target(preset):
    rule = rule_for(preset, "国民的アイドル")
    return rule.get("comboTarget", "") if rule else ""


def cultivation_extra_action(card, extra):
    rule = rule_for(cultivation_preset(), card) if card else None
    return "yes" if effective_extra_action(extra, rule) else extra


def cultivation_priority(item):
    """Selection-time rank so detail confirmation or preset changes stay fresh."""
    from .card_visual import rarity_priority

    rarity = item.get("rarity", "unknown")
    effective = cultivation_rarity(item.get("name_key"), rarity)
    extra = item.get("extra_action", "unknown")
    effective_extra = cultivation_extra_action(item.get("name_key"), extra)
    if effective == rarity and effective_extra == extra and "priority" in item:
        return item["priority"]
    return rarity_priority(effective, effective_extra)

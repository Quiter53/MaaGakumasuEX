"""Per-battle follow-up state, advanced only by confirmed card submissions."""

import json
import time

from .card_rules import name_key


class FollowUpSession:
    MAX_WAIT_SECONDS = 90

    def __init__(self):
        self.signature = None
        self.sources = {}
        self.released = set()
        self.active = None
        self.last_remaining = None
        self.waited = 0
        self.started = 0

    def sync(self, preset):
        signature = json.dumps(preset, sort_keys=True, ensure_ascii=False)
        if signature == self.signature:
            return
        self.__init__()
        self.signature = signature
        self.sources = {
            name_key(rule["card"]): rule["followUp"]
            for rule in preset["rules"] if rule["enabled"] and rule.get("followUp")
        }

    def clear_active(self):
        if self.active:
            self.released.add(self.active)
        self.active = None
        self.last_remaining = None
        self.waited = 0

    def observe(self, remaining):
        if self.active is None:
            return
        if time.monotonic() - self.started >= self.MAX_WAIT_SECONDS:
            self.clear_active()
            return
        if type(remaining) is not int:
            return
        if self.last_remaining is not None and remaining < self.last_remaining:
            self.waited += self.last_remaining - remaining
        self.last_remaining = remaining
        if self.waited > self.sources[self.active]["waitTurns"]:
            self.clear_active()

    def targets(self):
        return [name_key(card) for card in self.sources[self.active]["cards"]] if self.active else []

    def allowed(self, card):
        key = name_key(card)
        if key in self.targets():
            return True
        if self.active and key:
            return False
        for source, follow in self.sources.items():
            if key not in {name_key(card) for card in follow["cards"]}:
                continue
            if follow["targetPolicy"] == "comboOnly":
                return False
            if follow["targetPolicy"] == "reserveFirst" and source not in self.released:
                return False
        return True

    def requires_identity(self):
        return bool(self.active) or any(follow["targetPolicy"] != "normal" for follow in self.sources.values())

    def submitted(self, card, remaining=None):
        key = name_key(card)
        if key in self.targets():
            self.clear_active()
        if key in self.sources:
            self.active = key
            self.last_remaining = remaining if type(remaining) is int else None
            self.waited = 0
            self.started = time.monotonic()

    def prefer(self, candidates):
        """The ordered list supplies alternatives; the first available wins."""
        if not self.active:
            return candidates
        for target in self.targets():
            found = [item for item in candidates if name_key(item.get("rule_name")) == target]
            if found:
                return found
        # Unidentified cards may be raised for a title check, but cannot be
        # submitted without matching the active targets in the final guard.
        return [item for item in candidates if not item.get("rule_name")]

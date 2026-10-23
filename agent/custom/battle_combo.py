"""National Idol pairing. Decisions use the current hand, not deck guesses."""
from .card_rules import combo_target, name_key

NATIONAL = '国民的アイドル'


class NationalCombo:
    def __init__(self):
        self.fetch_attempted = False
        self.after_national = False

    def fetch_target(self, preset, selected, hand, remaining):
        target = combo_target(preset)
        if not target or self.fetch_attempted or self.after_national:
            return None
        pair = {name_key(NATIONAL), name_key(target)}
        if name_key(selected) not in pair or not hand or any(not n for n in hand):
            return None
        present = {name_key(n) for n in hand}
        # Both present includes unusable cards. Never search the deck for a
        # card merely excluded from the playable-candidate list.
        if pair <= present or name_key(selected) not in present:
            return None
        return target if name_key(selected) == name_key(NATIONAL) else NATIONAL

    def submitted(self, name=None):
        self.fetch_attempted = False
        self.after_national = name_key(name) == name_key(NATIONAL)

"""Reward budgets derived from confirmed on-screen counter transitions."""

from dataclasses import dataclass
import re
import unicodedata


def parse_remaining(text: str, score: float) -> int | None:
    text = unicodedata.normalize("NFKC", text).strip()
    if score < 0.85:
        return None
    # Do not concatenate unrelated digits or extract prices from arbitrary OCR.
    match = re.fullmatch(r"(?:あと|残り|剩余)?\s*([0-9])\s*(?:回|次)?", text)
    return int(match[1]) if match else None


@dataclass
class RewardActionBudget:
    remaining: int | None = None
    initial: int | None = None
    confirmed: int = 0
    suspended: bool = False
    pending_before: int | None = None

    @property
    def can_attempt(self) -> bool:
        return (self.remaining is not None and self.remaining > 0
                and not self.suspended and self.pending_before is None)

    def begin(self) -> bool:
        """Remember the counter before a click, including across interruption."""
        if not self.can_attempt:
            return False
        self.pending_before = self.remaining
        return True

    def observe(self, remaining: int | None) -> None:
        if remaining is None or not 0 <= remaining <= 9:
            return
        if self.remaining is None:
            self.initial = self.remaining = remaining
            self.suspended = False
        elif remaining < self.remaining:
            self.remaining = remaining
            self.suspended = False
        # An OCR increase cannot replenish a per-run consumable budget.

    def confirm(self, before: int | None, after: int | None) -> bool:
        pending = before is not None and self.pending_before == before
        if (before is not None and before > 0 and self.remaining == before
                and after == before - 1 and (self.can_attempt or pending)):
            self.remaining = after
            self.confirmed += 1
            self.pending_before = None
            self.suspended = False
            return True
        # Keep an unknown transaction until a later stable reward page can
        # settle it. A second click must not start while it is unresolved.
        if after is not None:
            self.pending_before = None
        self.observe(after)
        self.suspended = True
        return False

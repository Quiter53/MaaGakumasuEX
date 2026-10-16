"""Single-copy policy; counts must come from the current deck, never click history."""

from .card_rules import single_copy_cards


def needs_deck_check(selected):
    return bool(selected and selected.get("name_key") in single_copy_cards())


def choose_after_check(selected, candidates, counts, blacklist, whitelist, key):
    """Only rerank after the preferred limited card is already owned.

    None is unknown, not zero. A failed inspection cannot authorize an
    ownership-based rerank. The caller handles retry/return to the source page.
    """
    if counts is None or selected["name_key"] not in counts:
        return None
    if counts[selected["name_key"]] == 0:
        return selected
    limited = single_copy_cards()
    alternatives = [
        c
        for c in candidates
        if c.get("name_key") and c["name_key"] not in blacklist and (c["name_key"] not in limited or counts.get(c["name_key"]) == 0)
    ]
    if alternatives:
        preferred = [c for c in alternatives if c["name_key"] in whitelist] or alternatives
        return max(preferred, key=key)
    # The user's explicit exception: duplicate beats an all-blacklisted choice.
    # Unknown counts of another limited card are not proof it is unavailable.
    if all(
        c["name_key"] == selected["name_key"] or c["name_key"] in blacklist or (c["name_key"] in limited and counts.get(c["name_key"], 0) > 0)
        for c in candidates
    ):
        return selected
    return None


def with_owned_counts(items, counts):
    """Copy observations; raw recognition and the current screen stay intact."""
    return [dict(item, owned_count=counts.get(item.get("name_key")) if counts is not None else None) for item in items]


class RemovalCopies:
    """Current transaction only: overlapping pages are not additional copies."""

    def __init__(self, wanted):
        from .owned_deck import RowCounts

        self.tracker = RowCounts(None, wanted)

    def observe(self, image, items):
        from .deck_scan import scan_identity_known
        from .card_visual import _resize_rgb_bilinear

        rows = []
        for item in sorted(items, key=lambda c: (c["box"][1], c["box"][0])):
            x, y, w, h = item["box"]
            row = next((r for r in rows if abs(r[0]["box"][1] - y) <= 10), None)
            if row is None:
                row = []
                rows.append(row)
            row.append(
                dict(
                    box=item["box"],
                    name_key=item.get("name_key") if scan_identity_known(item) else None,
                    pixels=_resize_rgb_bilinear(image[y + 20 : y + h - 20, x + 25 : x + w - 20], 24),
                )
            )
        for row in rows:
            row.sort(key=lambda c: c["box"][0])
        contiguous = all(len(row) == 4 for row in rows[:-1]) and all(
            abs(c["box"][0] - (82 + 146 * i)) <= 14 for row in rows for i, c in enumerate(row)
        )
        if not contiguous:
            # Keep only the independent per-view lower bound, never stitch a
            # page with missing columns into the preceding one.
            self.tracker.valid = False
        self.tracker.add(rows)
        return self.tracker.result()

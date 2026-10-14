"""Validate complete three/four-card reward rows in 720px game frames."""

from itertools import product


def reward_card_row(candidates: list) -> list:
    """Reject isolated false boxes and incomplete rows; strip event badges.

    Three-card and four-card rows have different horizontal anchors.  Do not
    infer a three-card reward merely because one event card was missed.
    Coordinates are the same normalized 720px space as ProduceRecognitionCards.
    """
    valid = []
    for candidate in candidates:
        x, y, w, h = candidate["box"]
        if not (700 <= y <= 1000 and 100 <= w <= 145 and 100 <= h <= 165
                and 0.75 <= w / h <= 1.15):
            continue
        valid.append(candidate)
    rows = []
    for centers in ((150, 290, 430, 570), (220, 360, 500)):
        slots = [[c for c in valid if abs(c["box"][0] + c["box"][2] / 2 - center) <= 18]
                 for center in centers]
        if not all(slots):
            continue
        for row in product(*slots):
            bottoms = [c["box"][1] + c["box"][3] for c in row]
            widths = [c["box"][2] for c in row]
            if max(bottoms) - min(bottoms) > 12 or max(widths) - min(widths) > 12:
                continue
            rows.append(row)
    if not rows:
        return []
    best = max(rows, key=lambda row: (len(row), sum(c.get("detector_score", 0) for c in row)))
    result = []
    for candidate in best:
        x, y, w, h = candidate["box"]
        # The event badge extends the top, not the card's bottom edge.
        result.append({**candidate, "box": [x, y + h - w, w, w]})
    return result

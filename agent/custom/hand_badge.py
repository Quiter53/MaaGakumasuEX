"""Locate the white rounded thumbnail frame on a resting battle card.

Only the frame is checked; its portrait is not used to identify the card.
Coordinates are relative to the artwork crop, before 32 px downsampling.
"""

import numpy as np


def _single_badge(art, visible_right, top_fraction):
    height, width = art.shape[:2]
    # Five-card hands normally hide this corner completely behind the next card.
    if visible_right < width * 0.76:
        return None
    best_score, best = 0.70, None
    for scale in (0.96, 1.0, 1.04):
        w, h = round(width * 0.30 * scale), round(height * 0.27 * scale)
        radius = round(min(w, h) * 0.20)
        for dx in (-4, -2, 0, 2, 4):
            for dy in (-6, -4, -2, 0, 2, 4, 6):
                x, y = width - w - 2 + dx, round(height * top_fraction) + dy
                edges = (
                    [(xx, y + 1, xx, y + 4) for xx in range(x + radius, x + w - radius)],
                    [(x + 1, yy, x + 4, yy) for yy in range(y + radius, y + h - radius)],
                )
                scores = []
                for probes in edges:
                    probes = [p for p in probes if min(p[0], p[2]) >= 0
                              and max(p[0], p[2]) < min(width, visible_right - 3)
                              and min(p[1], p[3]) >= 0 and max(p[1], p[3]) < height]
                    if len(probes) < 6:
                        break
                    p = np.asarray(probes)
                    edge = art[p[:, 1], p[:, 0]].astype(np.int16)
                    inner = art[p[:, 3], p[:, 2]].astype(np.int16)
                    # Rarity/portrait colours differ. Require a neutral rim plus
                    # an adjacent contrasting edge, not one fixed portrait hue.
                    # The bottom edge can be covered by the upgrade plus sign.
                    white = (edge.min(axis=1) > 205) & (np.ptp(edge, axis=1) < 35)
                    contrast = (edge - inner).max(axis=1) > 40
                    scores.append(float((white & contrast).mean()))
                if len(scores) >= 2 and min(scores) > best_score:
                    best_score, best = min(scores), (x, y, w, h)
    return best


def badge_bounds(art, visible_right):
    """Confirm the first thumbnail, then at most two stacked continuations."""
    first = _single_badge(art, visible_right, 0.23)
    if first is None:
        return None
    x, y, w, h = first
    right, bottom = x + w, y + h
    for level in (1, 2):
        following = _single_badge(art, visible_right, 0.23 + 0.14 * level)
        if following is None:
            break
        a, b, c, d = following
        x, right, bottom = min(x, a), max(right, a + c), max(bottom, b + d)
    return x, y, right - x, bottom - y


def exclude_badge(mask, bounds, art_shape):
    """Use the same remaining pixels for query and reference artwork."""
    height, width = art_shape[:2]
    x, y, w, h = bounds
    result = mask.copy()
    left = max(0, int(np.floor((x - 2) * 32 / width)))
    right = min(32, int(np.ceil((x + w + 2) * 32 / width)))
    top = max(0, int(np.floor((y - 2) * 32 / height)))
    bottom = min(32, int(np.ceil((y + h + 2) * 32 / height)))
    result[top:bottom, left:right] = False
    return result

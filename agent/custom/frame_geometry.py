"""Small capture/detector tolerances in MaaFramework's normalized PC space.

These checks admit border rounding, not another device layout. Never resize a
frame here: recognition boxes and controller clicks must share coordinates.
Layout-specific UI evidence is still required by every caller.
"""

import math

import numpy as np


def reference_frame(image):
    return (
        isinstance(image, np.ndarray)
        and image.ndim == 3
        and image.shape[2] == 3
        and abs(image.shape[1] - 720) <= 8
        and abs(image.shape[0] - 1280) <= 12
    )


def clip_roi(image, roi):
    """Intersect an OCR search region with the image, without moving its origin."""
    x, y, w, h = map(int, roi)
    left, top = max(0, x), max(0, y)
    right, bottom = min(image.shape[1], x + w), min(image.shape[0], y + h)
    return [left, top, right - left, bottom - top] if right > left and bottom > top else None


def same_card_slot(first, second):
    """Associate a hand slot despite small edge jitter; reject selection rises.

    This is geometry only, not proof of card identity or permission to play.
    Selected-card actions must also verify the tooltip/badge on fresh frames.
    """
    if first is None or second is None or len(first) != 4 or len(second) != 4:
        return False
    if not all(math.isfinite(v) for v in (*first, *second)):
        return False
    x, y, w, h = first
    a, b, c, d = second
    if min(w, h, c, d) <= 0:
        return False
    width, height = min(w, c), min(h, d)
    if (
        abs((x + w / 2) - (a + c / 2)) > min(12, width * 0.07)
        or abs(y - b) > min(12, height * 0.05)
        or abs(w - c) > min(16, width * 0.10)
        or abs(h - d) > min(32, height * 0.15)
    ):
        return False
    intersection = max(0, min(x + w, a + c) - max(x, a)) * max(0, min(y + h, b + d) - max(y, b))
    return intersection / (w * h + c * d - intersection) >= 0.78

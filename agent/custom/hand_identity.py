"""Battle-only artwork identity retrieval. Never changes detection/play priority.

Three cards: aligned whole-art descriptors. Four/five: dense local descriptors
on visible patches only. Names are base identities, NOT upgrade/effect states.
NumPy only; the existing detector and reward-card matcher remain untouched.
"""

import json
from pathlib import Path
from dataclasses import dataclass, replace

import numpy as np

from .card_visual import _resize_rgb_bilinear, _load_card_fingerprints
from .frame_geometry import trim_overwide_hand_box
from .hand_badge import badge_bounds, exclude_badge


@dataclass(frozen=True)
class Identity:
    name: str | None
    candidate: str
    mode: str
    score: float
    margin: float
    visible: float
    badge_recovered: bool = False
    rejection_reason: str | None = None


def art_box(box):
    x, y, w, h = map(int, box)
    # Battle cards append a 34 px title strip; never include the title in art.
    return x, y, w, round(h * 0.865)


def visible_mask(box, boxes):
    x, y, w, h = art_box(box)
    mask = np.zeros((32, 32), bool)
    mask[4:27, 4:28] = True
    # Dynamic score, left effect icons, plus/cost badges.
    mask[:9, :17] = False
    mask[12:, :7] = False
    mask[23:, 23:] = False
    for other in boxes:
        ox, oy, ow, oh = art_box(other)
        # Normal resting hand is painted left-to-right. Raised cards must not
        # enter this matcher: callers use a fresh unselected hand frame.
        if ox > x and ox < x + w and abs(oy - y) < 12:
            start = max(0, int((ox - x - 3) * 32 / w))
            mask[:, start:] = False
    return mask


def independent_artwork_range(target, mask):
    """Per-channel contrast outside the right-side thumbnail area, in 0..1.

    Thumbnail borders can go undetected on flat synthetic backgrounds. Keep
    this check independent of border detection, colour and the predicted name.
    The ordinary visibility mask still excludes neighbouring cards and UI.
    """
    evidence = mask.copy()
    evidence[:, 20:] = False  # rightmost 37.5% may contain single/stacked thumbnails
    pixels = target[evidence]
    return float(np.ptp(pixels, axis=0).max()) if len(pixels) else 0.0


class HandIdentityReader:
    def __init__(self):
        model = _load_card_fingerprints()
        self.model = model
        self.gates = None
        self.cache = {}
        path = Path(__file__).with_name("hand_identity_calibration.json")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("version") == 1:
                self.gates = data["gates"]
        except (OSError, ValueError, KeyError):
            pass

    def rank(self, target, mask, mode):
        templates, names, _, _ = self.model
        # Pool evidence within local patches for occluded cards; global
        # normalization on whole artwork for unobscured cards.
        regions = [mask] if mode == "full" else []
        if mode == "local":
            for yy in (4, 12, 20):
                for xx in (4, 12, 20):
                    part = np.zeros((32, 32), bool)
                    part[yy : yy + 8, xx : xx + 8] = mask[yy : yy + 8, xx : xx + 8]
                    if part.sum() >= 24:
                        regions.append(part)
        if not regions:
            return None
        scores, weights = [], []
        for region in regions:
            query = target[region]
            refs = templates[:, region, :]
            # Preserve color as well as structure; flat shared backgrounds
            # supply little information and must not dominate identification.
            q = query - query.mean(axis=0)
            r = refs - refs.mean(axis=1, keepdims=True)
            numerator = (r * q).sum(axis=(1, 2))
            denominator = np.sqrt((r * r).sum(axis=(1, 2)) * (q * q).sum()) + 1e-6
            corr = numerator / denominator
            distance = ((refs - query) ** 2).mean(axis=(1, 2))
            scores.append(corr - 0.5 * distance)
            weights.append(max(0.005, float(query.std(axis=0).mean())) * region.sum())
        values = np.average(np.asarray(scores), axis=0, weights=weights)
        index = int(np.argmax(values))
        different = names != names[index]
        second = float(values[different].max()) if different.any() else -1.0
        return str(names[index]), float(values[index]), float(values[index] - second)

    def read(self, image, box, boxes):
        # Missing unusable detections must not turn a visibly overlapping hand
        # into the whole-art route simply because only three boxes remain.
        ordered = sorted(boxes, key=lambda b: b[0])
        overlap = any(a[0] + a[2] > b[0] + 4 for a, b in zip(ordered, ordered[1:]))
        mode = "local" if len(boxes) >= 4 or overlap else "full"
        if not self.model or not 1 <= len(boxes) <= 5:
            return None
        if not any(abs(box[0] - b[0]) <= 3 and abs(box[1] - b[1]) <= 3 and abs(box[2] - b[2]) <= 5 and abs(box[3] - b[3]) <= 5 for b in boxes):
            return None
        x, y, w, h = art_box(box)
        if w < 100 or h < 140 or x < 0 or y < 0 or x + w > image.shape[1] or y + h > image.shape[0]:
            return None
        if any(abs(int(b[1]) - y) > 12 for b in boxes):
            return None  # raised/animating layouts are deliberately rejected
        mask = visible_mask(box, boxes)
        if mask.sum() < 120:
            return None
        art = image[y : y + h, x : x + w]
        target = _resize_rgb_bilinear(art, 32)
        # Border detection uses native pixels; include them in the cache key as well.
        corner = art[round(h * 0.20) : round(h * 0.83), round(w * 0.65) :].tobytes()
        key = (mode, tuple(box), mask.tobytes(), target.tobytes(), corner)
        if key in self.cache:
            return self.cache[key]
        ranked = self.rank(target, mask, mode)
        if ranked is None:
            return None
        name, score, margin = ranked
        gate = (self.gates or {}).get(mode)
        accepted = bool(gate and score >= gate["score"] and margin >= gate["margin"])
        result = Identity(name if accepted else None, name, mode, score, margin, float(mask.mean()))
        badge_gate = (self.gates or {}).get("badge_" + mode)
        if not accepted and badge_gate:
            visible_right = min([int(b[0]) - x for b in boxes if x < b[0] < x + w] or [w])
            # art is BGR; border detection expects RGB.
            bounds = badge_bounds(art[:, :, ::-1], visible_right)
            if bounds is not None:
                clean_mask = exclude_badge(mask, bounds, art.shape)
                if clean_mask.sum() >= 120:
                    clean = self.rank(target, clean_mask, mode)
                    if clean and clean[0] == name and clean[1] >= badge_gate["score"] and clean[2] >= badge_gate["margin"]:
                        result = Identity(name, name, mode, clean[1], clean[2], float(clean_mask.mean()), True)
        # A thumbnail alone can pass the ordinary score/margin gate. Require
        # some independent artwork structure for either acceptance route.
        # One 8-bit level only rejects flat/quantization-level fields; it does
        # not recalibrate the existing identity similarity thresholds.
        if result.name is not None and independent_artwork_range(target, mask) <= 1 / 255:
            result = replace(result, name=None, badge_recovered=False, rejection_reason="flat_artwork")
        if len(self.cache) >= 20:
            self.cache.clear()
        self.cache[key] = result
        return result


def hand_boxes(results):
    """Include unusable cards for occlusion; discard fragments/duplicate labels."""
    ranked = sorted(results, key=lambda r: float(getattr(r, "score", 0)), reverse=True)
    boxes = []
    for row in ranked:
        if row.label not in ("cards", "suggestions", "useless"):
            continue
        x, y, w, h = trim_overwide_hand_box(row.box, getattr(row, "score", 0))
        if not (780 <= y <= 1000 and 140 <= w <= 220 and 200 <= h <= 290):
            continue
        if float(getattr(row, "score", 0)) < 0.5:
            continue
        if any(abs(x - b[0]) < 30 and abs(y - b[1]) < 20 for b in boxes):
            continue
        boxes.append([x, y, w, h])
    return sorted(boxes, key=lambda b: b[0])

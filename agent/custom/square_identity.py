"""Aligned, two-region square-card retrieval using all bundled art variants.

Scores are cosine similarities, not probabilities. This is ranking evidence;
deletion/exchange submission must still verify the selected card's exact title.
Only numpy is required by the portable runtime. Nothing is learned from a run.
"""
from functools import lru_cache
from pathlib import Path
from zipfile import BadZipFile

import numpy as np
from loguru import logger

MIN_SCORE = .56
MIN_MARGIN = .12
_Y, _X = np.mgrid[0:32, 0:32].astype(np.float32) / 31
# Upper and middle artwork exclude left effect icons, right upgrade/cost,
# and the green duration badge. Keeping full RGB handles alternate costumes.
_MASKS = tuple((_X > a) & (_X < b) & (_Y > c) & (_Y < d)
               for a, b, c, d in ((.28, .64, .13, .46), (.32, .70, .28, .63)))
_TRANSFORMS = np.asarray([(scale, dx, dy) for scale in (.86, .90, .94, .98, 1.02)
                          for dx in (-.025, 0, .025) for dy in (-.025, 0, .025)], dtype=np.float32)
_RETRY_TRANSFORMS = np.asarray([(scale, dx, dy) for scale in (.80, .84, .88, .92, .96, 1., 1.04)
                                for dx in (-.04, 0, .04) for dy in (-.04, 0, .04)], dtype=np.float32)


def _unit(pixels):
    centered = pixels - pixels.mean(axis=-2, keepdims=True)
    norm = np.sqrt(np.sum(centered * centered, axis=(-2, -1), keepdims=True))
    return centered / np.maximum(norm, 1e-8)


@lru_cache(maxsize=1)
def gallery():
    try:
        with np.load(Path(__file__).with_name('card_fingerprints.npz'), allow_pickle=False) as data:
            images, names, rarities, extra = (data[key] for key in ('templates', 'names', 'rarities', 'extra'))
            if (images.ndim != 4 or images.shape[1:] != (32, 32, 3) or images.dtype != np.uint8
                    or len(images) < 2 or any(a.shape != (len(images),) for a in (names, rarities, extra))
                    or names.dtype.kind != 'U' or rarities.dtype.kind != 'U'
                    or not all(str(n).strip() for n in names)
                    or not all(r in ('N', 'R', 'SR', 'SSR') for r in rarities)):
                raise ValueError('invalid square-card gallery')
            pixels = images.astype(np.float32) / 255
            vectors = tuple(np.ascontiguousarray(_unit(pixels[:, mask]).reshape(len(images), -1)) for mask in _MASKS)
            return names, rarities, extra, vectors
    except (OSError, ValueError, KeyError, TypeError, EOFError, BadZipFile):
        logger.warning('整页卡图索引无法加载，使用详情核实')
        return None


def _queries(image, box, mask, transforms):
    x, y, w, h = box
    scale, dx, dy = transforms.T[:, :, None]
    sx = x + w * ((_X[mask][None] - .5) * scale + .5 + dx)
    sy = y + h * ((_Y[mask][None] - .5) * scale + .5 + dy)
    x0, y0 = sx.astype(np.int32), sy.astype(np.int32)
    ax, ay = (sx - x0.astype(np.float32))[..., None], (sy - y0.astype(np.float32))[..., None]
    # Cast before arithmetic to avoid uint8 overflow; reverse BGR to RGB.
    q = (image[y0, x0, :3] * (1-ax) * (1-ay)
         + image[y0, x0+1, :3] * ax * (1-ay)
         + image[y0+1, x0, :3] * (1-ax) * ay
         + image[y0+1, x0+1, :3] * ax * ay)[..., ::-1] / 255
    return np.ascontiguousarray(_unit(q).reshape(len(transforms), -1).T)


def match(image, box):
    """Return a name only when two regions agree against every other name."""
    if image is None or image.ndim != 3 or image.shape[2] < 3:
        return None
    x, y, w, h = map(int, box)
    if min(w, h) < 80 or max(w, h) > 180 or not .80 <= w/h <= 1.22:
        return None
    if min(x, y) < 0 or x+w > image.shape[1] or y+h > image.shape[0]:
        return None
    model = gallery()
    if model is None:
        return None
    return (_match(image, (x,y,w,h), model, _TRANSFORMS)
            or _match(image, (x,y,w,h), model, _RETRY_TRANSFORMS))


def _match(image, box, model, transforms):
    names, rarities, extra, vectors = model
    evidence = []
    for mask, reference in zip(_MASKS, vectors):
        scores = (reference @ _queries(image, box, mask, transforms)).max(axis=1)
        best = int(np.argmax(scores))
        other = scores[names != names[best]]
        score = float(scores[best])
        margin = score - float(other.max()) if len(other) else 0.
        if not np.isfinite(score) or score < MIN_SCORE or margin < MIN_MARGIN:
            return None
        evidence.append((best, score, margin))
    a, b = evidence
    if names[a[0]] != names[b[0]] or rarities[a[0]] != rarities[b[0]]:
        return None
    return dict(name=str(names[a[0]]), rarity=str(rarities[a[0]]), extra_action=bool(extra[a[0]]),
                score=min(a[1], b[1]), margin=min(a[2], b[2]))


def recognize(image, box, frame):
    """Adapt to the existing card record without claiming OCR verification."""
    from .card_visual import VisualCatalogResult
    result = match(image, box)
    if result is None:
        return None
    # Strong contradictory frame evidence must go through detail verification.
    if frame.confidence >= .55 and frame.rarity != result['rarity']:
        return None
    return VisualCatalogResult(result['name'], result['rarity'], result['extra_action'],
                               .9, result['score'], result['margin'])


def evidence(visual):
    return dict(square_art_trusted=True, square_art_score=visual.score,
                square_art_margin=visual.margin) if visual is not None else {}


def resolve_selected(context, image, items, *, change=False):
    """A tinted selected tile already has a detail panel: read it without input."""
    from .deck_scan import scan_identity_known
    from .removal import read_selected_card, selected_card_box_matches
    for item in items:
        if not scan_identity_known(item) and selected_card_box_matches(image, item['box']):
            exact = read_selected_card(context, image, change=change)
            if exact:
                item.update(exact, detail_selected=True, probe_failed=False)
    return items

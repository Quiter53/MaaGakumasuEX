"""Deck-only two-view art retrieval. Never authorizes transaction submission."""
from functools import lru_cache
from pathlib import Path
from zipfile import BadZipFile

import numpy as np
from loguru import logger

from .card_visual import _resize_rgb_bilinear, normalize_card_name
from .deck_scan import DECK_IDENTITY_MIN_CONFIDENCE


def descriptors(images, masked):
    size = images.shape[1]
    mask = np.zeros((size, size), bool)
    mask[round(size*.10):round(size*.77), round(size*.22):round(size*.78)] = True
    if masked:
        mask[round(size*.45):, :round(size*.47)] = False
        mask[round(size*.43):, round(size*.72):] = False
    pixels = images[:, mask, :]
    centered = pixels - pixels.mean(axis=1, keepdims=True)
    norm = np.sqrt((centered ** 2).sum(axis=(1, 2), keepdims=True)) + 1e-8
    return (centered / norm).reshape(len(images), -1)


@lru_cache(maxsize=1)
def gallery():
    path = Path(__file__).with_name('deck_art64.npz')
    if not path.exists():
        return None
    try:
        data = np.load(path, allow_pickle=False)
        if not isinstance(data, np.lib.npyio.NpzFile):
            raise ValueError('deck gallery is not an NPZ archive')
        with data:
            images, names, rarities = data['images'], data['names'], data['rarities']
            if (images.ndim != 4 or images.shape[1:] != (64, 64, 3)
                    or images.dtype != np.uint8 or len(images) < 2
                    or names.ndim != 1 or rarities.ndim != 1
                    or len(names) != len(images) or len(rarities) != len(images)
                    or names.dtype.kind != 'U' or rarities.dtype.kind != 'U'
                    or not all(str(name).strip() for name in names)
                    or not all(rarity in ('N', 'R', 'SR', 'SSR') for rarity in rarities)):
                raise ValueError('invalid deck gallery dimensions or identities')
            images = images.astype(np.float32) / 255.
            return names, rarities, tuple(descriptors(images, m) for m in (False, True))
    except (OSError, ValueError, KeyError, EOFError, BadZipFile) as error:
        # This optional gallery must never terminate a cultivation. Existing
        # recognition and exact detail probes remain available without it.
        logger.warning('换出卡图库无法加载，回退原识别与详情核实')
        logger.debug(f'deck gallery load failed: {error}')
        return None


def match(image, box):
    model = gallery()
    if model is None:
        return None
    x, y, w, h = map(int, box)
    if min(w, h) < 90 or abs(w-h) > 15 or min(x, y) < 0 or x+w > image.shape[1] or y+h > image.shape[0]:
        return None
    query = _resize_rgb_bilinear(image[y:y+h, x:x+w], 64)[None]
    names, rarities, vectors = model
    results = []
    for masked, reference in zip((False, True), vectors):
        scores = reference @ descriptors(query, masked)[0]
        best = int(np.argmax(scores))
        other = scores[names != names[best]]
        margin = float(scores[best] - other.max()) if len(other) else 0.
        results.append((str(names[best]), str(rarities[best]), float(scores[best]), margin))
    a, b = results
    # Both independent crops must agree and clear their own rejection gates.
    # Scores are similarities, not calibrated probabilities.
    if a[:2] != b[:2] or min(a[2], b[2]) < .45 or min(a[3], b[3]) < .12:
        return None
    return dict(name=a[0], rarity=a[1], score=min(a[2], b[2]), margin=min(a[3], b[3]))


def improve_identities(image, items):
    for item in items:
        # Evidence belongs to the current frame, not a reused item dictionary.
        for field in ('deck_art_trusted', 'deck_art_score', 'deck_art_margin', 'identity_conflict'):
            item.pop(field, None)
        if item.get('identity_verified'):
            continue
        result = match(image, item['box'])
        x, y, w, h = map(int, item['box'])
        # Deck tiles are square. A slightly tall detector box includes glow or
        # background; avoid stretching that into the reference artwork. Keep
        # the original click box, and reject conflicting crop identities.
        if 6 <= h-w <= 15:
            square = match(image, [x, y+(h-w)//2, w, w])
            if square and result and (result['name'], result['rarity']) != (square['name'], square['rarity']):
                item['identity_conflict'] = True
                continue
            if square:
                result = square
        if not result:
            continue
        # Contradicting strong existing evidence requires detail OCR.
        if (item.get('name_confidence', 0) >= DECK_IDENTITY_MIN_CONFIDENCE and item.get('name_key')
                and item['name_key'] != normalize_card_name(result['name'])):
            item['identity_conflict'] = True
            continue
        if item.get('frame_rarity') != result['rarity'] or item.get('rarity_confidence', 0) < .30:
            continue
        item.update(name=result['name'], name_key=normalize_card_name(result['name']),
                    rarity=result['rarity'], name_confidence=max(DECK_IDENTITY_MIN_CONFIDENCE, item.get('name_confidence', 0)),
                    deck_art_trusted=True, deck_art_score=result['score'], deck_art_margin=result['margin'])
    return items

"""Lightweight fixed-region recognition for Produce skill cards.

MaaFramework supplies screenshots in BGR channel order.  Both the square card
used by rewards/shops and the portrait card used in lessons retain the same
rarity-coloured frame in the upper-left corner.  Reading that small patch is
considerably more stable than comparing the illustration or the whole border.
"""

import os
import re
import json
from typing import Dict, List, Sequence
from difflib import SequenceMatcher
from dataclasses import dataclass

import numpy as np

# Median BGR colours measured from the canonical frame assets and then adjusted
# against captured 720x1280 game frames.  N has a second, dark prototype for
# negative/basic cards such as sleepiness.
RARITY_COLOUR_PROTOTYPES = {
    "SSR": (
        np.asarray([190, 168, 255], dtype=np.float32),
        np.asarray([221, 169, 255], dtype=np.float32),
    ),
    "SR": (
        np.asarray([105, 224, 255], dtype=np.float32),
        np.asarray([170, 238, 255], dtype=np.float32),
    ),
    "R": (np.asarray([250, 223, 195], dtype=np.float32),),
    "N": (
        np.asarray([205, 224, 240], dtype=np.float32),
        np.asarray([182, 144, 147], dtype=np.float32),
    ),
}

# Distances are normalised to 0..1 per channel.  A deliberately conservative
# rejection band prevents card art, clipped cards and disabled overlays from
# being promoted to a high rarity.
MAX_KNOWN_DISTANCE = 0.18
MIN_DISTANCE_MARGIN = 0.07

_EDGE_MODEL = None
_EDGE_CLASSES = ("SSR", "SR", "R", "N")
_CARD_CATALOG = None
_CARD_FINGERPRINTS = None


@dataclass
class RarityResult:
    rarity: str
    confidence: float
    best_distance: float
    margin: float
    distances: Dict[str, float]
    signature: List[float]
    channel_order: str


@dataclass
class CatalogResult:
    rarity: str
    extra_action: bool
    matched_name: str
    confidence: float


@dataclass
class VisualCatalogResult:
    name: str
    rarity: str
    extra_action: bool
    confidence: float
    score: float
    margin: float


def normalize_card_name(value: str) -> str:
    """Normalize the short Japanese title returned by the bundled OCR."""
    value = (
        str(value or "")
        .strip()
        .translate(
            str.maketrans(
                {
                    "＋": "+",
                    "！": "!",
                    "？": "?",
                    "〜": "~",
                    "～": "~",
                }
            )
        )
        .rstrip("+")
    )
    return "".join(character for character in value if not character.isspace())


def _catalog_query_variants(value: str) -> list[str]:
    """Return conservative variants for recurring Japanese OCR confusions."""
    base = normalize_card_name(value)
    if not base:
        return []
    confusion = str.maketrans(
        {
            "爱": "愛",
            "现": "現",
            "线": "線",
            "儿": "ル",
            "妇": "好",
            "彼": "線",
            "琪": "現",
            "要": "愛",
            "在": "仕",
            "章": "草",
        }
    )
    variants = {base, base.translate(confusion)}
    # Seen repeatedly on the narrow disabled title font: the long vowel and
    # dakuten strokes in "ーズ" are merged into two CJK glyphs by OCR.
    variants.update(query.replace("一人", "ーズ") for query in list(variants))
    for query in list(variants):
        # The title crop occasionally includes a cost/upgrade token or one
        # Latin detector artefact at either edge.
        variants.add(re.sub(r"[+＋]?\d+[A-Za-z]?$", "", query))
        variants.add(re.sub(r"^[A-Za-z0-9]+|[A-Za-z0-9]+$", "", query))
        if len(query) >= 5 and query.startswith("回"):
            variants.add(query[1:])
    return sorted({query for query in variants if len(query) >= 2})


def _load_card_catalog():
    """Load the compact name-to-rarity table once; fail closed if absent."""
    global _CARD_CATALOG
    if _CARD_CATALOG is not None:
        return _CARD_CATALOG
    path = os.path.join(os.path.dirname(__file__), "card_catalog.json")
    try:
        with open(path, "r", encoding="utf-8") as stream:
            entries = json.load(stream).get("entries", {})
        _CARD_CATALOG = {normalize_card_name(name): item for name, item in entries.items() if normalize_card_name(name)}
    except Exception:
        _CARD_CATALOG = {}
    return _CARD_CATALOG


def match_card_catalog(value: str) -> CatalogResult | None:
    """Match one OCR title conservatively against the local card catalog."""
    variants = _catalog_query_variants(value)
    catalog = _load_card_catalog()
    if not variants or not catalog:
        return None

    for query in variants:
        exact = catalog.get(query)
        if exact is not None:
            return CatalogResult(
                exact["rarity"],
                bool(exact.get("extra")),
                exact.get("name", query),
                1.0,
            )

    # A cropped title often loses its last one or two glyphs.  Accept a prefix
    # only when it identifies exactly one catalog card.  One trailing OCR
    # artefact may be discarded for sufficiently long queries.
    prefix_hits = {}
    for query in variants:
        prefixes = {query}
        if len(query) >= 6:
            prefixes.add(query[:-1])
        for prefix in prefixes:
            if len(prefix) < 3:
                continue
            hits = [(name, item) for name, item in catalog.items() if name.startswith(prefix) and len(prefix) / len(name) >= 0.55]
            if len(hits) == 1:
                name, item = hits[0]
                prefix_hits[name] = (len(prefix) / len(name), item)
    if len(prefix_hits) == 1:
        name, (score, item) = next(iter(prefix_hits.items()))
        return CatalogResult(item["rarity"], bool(item.get("extra")), item.get("name", name), score)

    ranked = []
    for name, item in catalog.items():
        score = max(
            (SequenceMatcher(None, query, name).ratio() for query in variants if abs(len(name) - len(query)) <= max(3, len(query) // 2)),
            default=0.0,
        )
        ranked.append((score, name, item))
    ranked.sort()
    if not ranked:
        return None
    best_score, best_name, best_item = ranked[-1]
    second_score = ranked[-2][0] if len(ranked) > 1 else 0.0
    # Short Japanese titles collide easily.  Require both a strong match and
    # a useful lead over the runner-up; otherwise keep the visual result.
    longest_query = max(len(query) for query in variants)
    required = 0.78 if longest_query >= 5 else 0.86
    normal_match = best_score >= required and best_score - second_score >= 0.06
    # Badly washed cards create two or three substituted glyphs.  A lower
    # score is still safe when the best title has a very large unique lead.
    clear_partial = longest_query >= 4 and best_score >= 0.60 and best_score - second_score >= 0.18
    if not (normal_match or clear_partial):
        return None
    return CatalogResult(
        best_item["rarity"],
        bool(best_item.get("extra")),
        best_item.get("name", best_name),
        float(best_score),
    )


def _load_card_fingerprints():
    """Load the compact reward-card artwork model once; fail closed."""
    global _CARD_FINGERPRINTS
    if _CARD_FINGERPRINTS is not None:
        return _CARD_FINGERPRINTS
    path = os.path.join(os.path.dirname(__file__), "card_fingerprints.npz")
    try:
        with np.load(path, allow_pickle=False) as model:
            templates = model["templates"].astype(np.float32) / 255.0
            names = model["names"].astype(str)
            rarities = model["rarities"].astype(str)
            extra = model["extra"].astype(np.uint8)
        _CARD_FINGERPRINTS = (templates, names, rarities, extra)
    except Exception:
        _CARD_FINGERPRINTS = False
    return _CARD_FINGERPRINTS


def _resize_rgb_bilinear(image: np.ndarray, size: int = 32) -> np.ndarray:
    """Resize a small BGR crop with vectorised bilinear interpolation."""
    height, width = image.shape[:2]
    if height < 2 or width < 2:
        return np.empty((0, 0, 3), dtype=np.float32)
    y = np.linspace(0, height - 1, size, dtype=np.float32)
    x = np.linspace(0, width - 1, size, dtype=np.float32)
    y0 = np.floor(y).astype(np.int32)
    x0 = np.floor(x).astype(np.int32)
    y1 = np.minimum(y0 + 1, height - 1)
    x1 = np.minimum(x0 + 1, width - 1)
    wy = (y - y0)[:, None, None]
    wx = (x - x0)[None, :, None]
    rgb = image[:, :, :3][:, :, ::-1].astype(np.float32) / 255.0
    top = rgb[y0[:, None], x0[None, :]] * (1.0 - wx) + rgb[y0[:, None], x1[None, :]] * wx
    bottom = rgb[y1[:, None], x0[None, :]] * (1.0 - wx) + rgb[y1[:, None], x1[None, :]] * wx
    return top * (1.0 - wy) + bottom * wy


def match_card_visual(image: np.ndarray, box: Sequence[int]) -> VisualCatalogResult | None:
    """Identify a square reward card by its illustration.

    Reward choices do not print their names, so OCR cannot support a named
    blacklist.  The model compares only the stable central artwork, excluding
    the frame, cost, score, plus marker, and lower-corner effect icons.  A
    useful lead over the runner-up is mandatory; uncertain cards remain
    unnamed and fall through to the rarity/extra-action policy.
    """
    model = _load_card_fingerprints()
    if not model:
        return None
    x, y, width, height = clip_box(box, image.shape)
    aspect = width / max(height, 1)
    if width < 40 or height < 40 or not 0.78 <= aspect <= 1.25:
        return None

    target = _resize_rgb_bilinear(image[y : y + height, x : x + width], 32)
    if target.size == 0:
        return None
    templates, names, rarities, extra = model

    # Three overlapping central masks are robust to the different score and
    # effect overlays used by active/mental and character-specific cards.
    masks = []
    for x1, y1, x2, y2 in ((3, 3, 28, 25), (7, 3, 25, 25), (4, 5, 27, 22)):
        mask = np.zeros((32, 32), dtype=bool)
        mask[y1:y2, x1:x2] = True
        mask[21:, :10] = False
        mask[21:, 22:] = False
        masks.append(mask)

    best_scores = np.full(len(templates), -99.0, dtype=np.float32)
    for mask in masks:
        target_pixels = target[mask]
        sample_pixels = templates[:, mask, :]
        target_mean = target_pixels.mean(axis=0)
        target_std = target_pixels.std(axis=0) + 0.05
        sample_mean = sample_pixels.mean(axis=1, keepdims=True)
        sample_std = sample_pixels.std(axis=1, keepdims=True) + 0.05
        target_normalized = (target_pixels - target_mean) / target_std
        sample_normalized = (sample_pixels - sample_mean) / sample_std
        correlation = (sample_normalized * target_normalized[None, :, :]).mean(axis=(1, 2))
        mse = ((sample_pixels - target_pixels[None, :, :]) ** 2).mean(axis=(1, 2))
        best_scores = np.maximum(best_scores, correlation - 0.45 * mse)

    order = np.argsort(best_scores)
    best_index = int(order[-1])
    best_score = float(best_scores[best_index])
    # The model may contain one artwork variant per idol for the same card.
    # Compare the winner only against a *different card name*; otherwise two
    # correct variants of one card would erase the confidence margin.
    different_card = names != names[best_index]
    second_score = float(best_scores[different_card].max()) if different_card.any() else -1.0
    margin = best_score - second_score
    # Captured examples—including character-specific variants—have scores
    # above 0.18 and a margin above 0.08.  The wider rejection band prevents
    # a visually novel or transitional card from being assigned a wrong name.
    if best_score < 0.15 or margin < 0.055:
        return None
    confidence = float(min(1.0, max(0.0, (best_score - 0.12) / 0.30) * min(1.0, margin / 0.14)))
    return VisualCatalogResult(
        name=str(names[best_index]),
        rarity=str(rarities[best_index]),
        extra_action=bool(extra[best_index]),
        confidence=confidence,
        score=best_score,
        margin=margin,
    )


def reconcile_visual_identity(visual, frame):
    """Reject a catalog identity that contradicts a confident frame reading."""
    if visual is not None and frame.rarity != "unknown" and frame.confidence >= 0.55 and visual.rarity != frame.rarity:
        return None
    return visual


def clip_box(box: Sequence[int], image_shape: Sequence[int]) -> List[int]:
    """Clip ``[x, y, width, height]`` to the image."""
    height, width = int(image_shape[0]), int(image_shape[1])
    x, y, w, h = [int(v) for v in box]
    x1, y1 = max(0, x), max(0, y)
    x2, y2 = min(width, x + max(0, w)), min(height, y + max(0, h))
    return [x1, y1, max(0, x2 - x1), max(0, y2 - y1)]


def _patch_colour(
    card: np.ndarray,
    y_range: Sequence[float],
    x_range: Sequence[float],
) -> np.ndarray:
    """Return a cleaned median BGR colour for one relative frame patch."""
    height, width = card.shape[:2]
    if height < 24 or width < 24:
        return np.empty(0, dtype=np.float32)

    y1 = max(1, int(height * y_range[0]))
    y2 = max(y1 + 2, int(height * y_range[1]))
    x1 = max(1, int(width * x_range[0]))
    x2 = max(x1 + 2, int(width * x_range[1]))
    pixels = card[y1:y2, x1:x2, :3].reshape(-1, 3).astype(np.float32)
    if not pixels.size:
        return np.empty(0, dtype=np.float32)

    # Drop the charcoal outer stroke, white shine and out-of-card background.
    maximum = pixels.max(axis=1)
    minimum = pixels.min(axis=1)
    usable = pixels[(maximum > 100) & (minimum < 252)]
    if len(usable) < max(12, len(pixels) // 5):
        return np.empty(0, dtype=np.float32)
    return np.median(usable, axis=0)


def _frame_colours(card: np.ndarray) -> List[tuple[str, np.ndarray]]:
    """Read layout-specific frame patches in reliability order.

    Portrait lesson cards expose a thin rarity-coloured strip at the top.  The
    older, taller sample included illustration pixels and could turn a gold SR
    into a purple SSR.  Large score numbers sometimes cover the upper-left, so
    the upper-right strip is an independent fallback.  Square reward/shop cards
    keep the original wider patch because their frame geometry is different.
    """
    height, width = card.shape[:2]
    aspect = width / max(height, 1)
    if aspect < 0.85:
        return [
            ("portrait-upper-left", _patch_colour(card, (0.010, 0.055), (0.055, 0.280))),
            ("portrait-upper-right", _patch_colour(card, (0.010, 0.055), (0.700, 0.940))),
        ]
    return [
        ("square-upper-left", _patch_colour(card, (0.025, 0.105), (0.055, 0.280))),
    ]


def _prototype_distances(frame_bgr: np.ndarray, region: str = "") -> Dict[str, float]:
    prototypes_by_rarity = dict(RARITY_COLOUR_PROTOTYPES)
    if region == "square-upper-left":
        # Down-sampling a square rainbow frame blends its pink and blue corner
        # into this pale lilac.  It is intentionally square-only: score text on
        # portrait cards can have a similar pale colour.
        prototypes_by_rarity["SSR"] = prototypes_by_rarity["SSR"] + (np.asarray([223, 217, 255], dtype=np.float32),)
    return {
        rarity: min(float(np.linalg.norm(frame_bgr - prototype) / 255.0) for prototype in prototypes)
        for rarity, prototypes in prototypes_by_rarity.items()
    }


def _edge_rarity_feature(card: np.ndarray) -> np.ndarray:
    """Describe rarity-coloured pixels along the two sides and bottom edge."""
    height, width = card.shape[:2]
    if width < 120 or height < 200:
        return np.empty(0, dtype=np.float32)

    regions = (
        card[int(height * 0.08) : int(height * 0.88), : max(3, int(width * 0.04)), :3],
        card[int(height * 0.08) : int(height * 0.88), int(width * 0.96) :, :3],
        card[int(height * 0.88) : int(height * 0.96), int(width * 0.08) : int(width * 0.92), :3],
    )
    feature = []
    for region in regions:
        pixels = region.reshape(-1, 3).astype(np.float32)
        usable = pixels[(pixels.max(axis=1) > 70) & (pixels.min(axis=1) < 253)]
        if len(usable) < 20:
            return np.empty(0, dtype=np.float32)
        for rarity in _EDGE_CLASSES:
            prototypes = RARITY_COLOUR_PROTOTYPES[rarity]
            distances = np.stack(
                [np.linalg.norm(usable - prototype, axis=1) / 255.0 for prototype in prototypes],
                axis=1,
            ).min(axis=1)
            feature.extend(float(np.percentile(distances, q)) for q in (2, 5, 10, 20, 40))
            feature.extend(float(np.mean(distances < threshold)) for threshold in (0.05, 0.08, 0.12, 0.16, 0.20))
    return np.asarray(feature, dtype=np.float32)


def _load_edge_model():
    """Load the compact colour-neighbour model once; fail closed if absent."""
    global _EDGE_MODEL
    if _EDGE_MODEL is not None:
        return _EDGE_MODEL
    model_path = __file__.replace("card_visual.py", "edge_rarity_knn.npz")
    try:
        with np.load(model_path, allow_pickle=False) as model:
            _EDGE_MODEL = (
                model["features"].astype(np.float32),
                model["labels"].astype(np.uint8),
                model["mean"].astype(np.float32),
                model["scale"].astype(np.float32),
            )
    except Exception:
        _EDGE_MODEL = False
    return _EDGE_MODEL


def _classify_edge_rarity(card: np.ndarray):
    """Return a class only when five close edge-colour neighbours all agree."""
    model = _load_edge_model()
    feature = _edge_rarity_feature(card)
    if not model or feature.size != 120:
        return None
    samples, labels, mean, scale = model
    standardized = (feature - mean) / scale
    distances = np.mean((samples - standardized) ** 2, axis=1)
    nearest = np.argpartition(distances, 4)[:5]
    nearest_labels = labels[nearest]
    label = int(nearest_labels[0])
    nearest_distance = float(distances[nearest].min())
    if nearest_distance > 0.10 or not np.all(nearest_labels == label):
        return None

    per_class = {rarity: float(distances[labels == index].min()) for index, rarity in enumerate(_EDGE_CLASSES) if np.any(labels == index)}
    ordered = sorted(per_class.items(), key=lambda item: item[1])
    margin = ordered[1][1] - ordered[0][1]
    confidence = max(0.0, min(1.0, 1.0 - nearest_distance / 0.10))
    return _EDGE_CLASSES[label], confidence, nearest_distance, margin, per_class


def classify_rarity(image: np.ndarray, box: Sequence[int]) -> RarityResult:
    """Classify rarity from frame colour for square or portrait card layouts."""
    x, y, width, height = clip_box(box, image.shape)
    aspect = width / max(height, 1)
    if width < 24 or height < 24 or aspect < 0.43 or aspect > 1.35:
        return RarityResult("unknown", 0.0, 99.0, 0.0, {}, [], "invalid")

    frame_candidates = _frame_colours(image[y : y + height, x : x + width, :3])
    valid_candidates = [(region, colour) for region, colour in frame_candidates if colour.size == 3]
    if not valid_candidates:
        return RarityResult("unknown", 0.0, 99.0, 0.0, {}, [], "invalid")

    evaluated = []
    for region, frame_bgr in valid_candidates:
        distances = _prototype_distances(frame_bgr, region)
        ordered = sorted(distances.items(), key=lambda item: item[1])
        best_rarity, best_distance = ordered[0]
        margin = ordered[1][1] - best_distance
        known = best_distance <= MAX_KNOWN_DISTANCE and margin >= MIN_DISTANCE_MARGIN

        # On portrait cards the left strip is authoritative for the rainbow
        # SSR and normal gold SR frames.  A strong gold result on the right is
        # preferred over a pale left result because score text is drawn there.
        rank = 0
        if known and region == "portrait-upper-left" and best_rarity in ("SSR", "SR"):
            rank = 30
        elif known and region == "portrait-upper-right" and best_rarity == "SR":
            rank = 20
        elif known and region == "portrait-upper-left":
            rank = 10
        elif known:
            rank = 5
        evaluated.append((rank, known, -best_distance, region, frame_bgr, distances, best_rarity, best_distance, margin))

    evaluated.sort(reverse=True, key=lambda item: (item[0], item[1], item[2]))
    _, known, _, region, frame_bgr, distances, best_rarity, best_distance, margin = evaluated[0]
    rarity = best_rarity if known else "unknown"
    distance_score = max(0.0, 1.0 - best_distance / MAX_KNOWN_DISTANCE)
    margin_score = max(0.0, min(1.0, margin / 0.25))
    confidence = float(min(distance_score, margin_score)) if known else 0.0
    result = RarityResult(
        rarity=rarity,
        confidence=confidence,
        best_distance=float(best_distance),
        margin=float(margin),
        distances={key: float(value) for key, value in distances.items()},
        signature=[float(value / 255.0) for value in frame_bgr],
        channel_order=f"BGR-{region}",
    )

    # Large score text can cover the top frame and make a rainbow/gold card
    # look like a pale N.  Only challenge a low-confidence portrait N, and only
    # promote it when five close edge-colour neighbours unanimously agree.
    if aspect >= 0.85 or rarity != "N" or confidence >= 0.55:
        return result
    edge = _classify_edge_rarity(image[y : y + height, x : x + width, :3])
    if edge is None or edge[0] not in ("SSR", "SR", "R"):
        return result
    edge_rarity, edge_confidence, edge_distance, edge_margin, edge_distances = edge
    return RarityResult(
        rarity=edge_rarity,
        confidence=edge_confidence,
        best_distance=edge_distance,
        margin=edge_margin,
        distances=edge_distances,
        signature=[],
        channel_order="BGR-portrait-edge-knn-low-N-override",
    )


def rarity_priority(rarity: str, extra_action: str) -> int:
    """Extra-action cards always win, ordered by their recognised rarity."""
    if extra_action == "yes":
        return {
            "LR": 1500,
            "SSR": 1400,
            "SR": 1300,
            "R": 1200,
            "N": 1100,
        }.get(rarity, 1000)
    return {"LR": 500, "SSR": 400, "SR": 300, "R": 200, "N": 100}.get(rarity, 0)


def intersection_over_union(first: Sequence[int], second: Sequence[int]) -> float:
    ax1, ay1, aw, ah = [float(v) for v in first]
    bx1, by1, bw, bh = [float(v) for v in second]
    ax2, ay2 = ax1 + aw, ay1 + ah
    bx2, by2 = bx1 + bw, by1 + bh
    width = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    height = max(0.0, min(ay2, by2) - max(ay1, by1))
    intersection = width * height
    union = aw * ah + bw * bh - intersection
    return intersection / union if union > 0 else 0.0

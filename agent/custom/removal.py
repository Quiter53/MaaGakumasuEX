"""Exact deck identity and shop deletion confirmation, without game input."""

import numpy as np

from .shop import ocr
from .card_visual import match_card_catalog, normalize_card_name

DECK_TITLE_OCR_MIN_SCORE = 0.80


def _bilinear_double(image):
    """Two-times half-pixel bilinear resize, without a new runtime dependency.

    Round after each separable pass, matching uint8 Pillow bilinear output.
    This is a bounded OCR retry only, not a new card-art recognition scale.
    """
    source = image.astype(np.uint16)
    horizontal = np.empty((source.shape[0], source.shape[1] * 2, source.shape[2]), np.uint16)
    previous = np.concatenate((source[:, :1], source[:, :-1]), axis=1)
    following = np.concatenate((source[:, 1:], source[:, -1:]), axis=1)
    horizontal[:, ::2] = (previous + 3 * source + 2) // 4
    horizontal[:, 1::2] = (3 * source + following + 2) // 4
    output = np.empty((source.shape[0] * 2, horizontal.shape[1], source.shape[2]), np.uint8)
    previous = np.concatenate((horizontal[:1], horizontal[:-1]), axis=0)
    following = np.concatenate((horizontal[1:], horizontal[-1:]), axis=0)
    output[::2] = (previous + 3 * horizontal + 2) // 4
    output[1::2] = (3 * horizontal + following + 2) // 4
    return output


def _double_title_crop(image, roi):
    """Resize just a detected title, preserving full-frame interpolation edges."""
    x, y, width, height = map(int, roi)
    # One extra input pixel supplies the bilinear neighbour at each ROI edge.
    # Trim it after resizing so this is byte-identical to resize-then-crop.
    left, top = max(0, x - 1), max(0, y - 1)
    right, bottom = min(image.shape[1], x + width + 1), min(image.shape[0], y + height + 1)
    doubled = _bilinear_double(image[top:bottom, left:right])
    offset_x, offset_y = (x - left) * 2, (y - top) * 2
    return np.ascontiguousarray(doubled[offset_y : offset_y + height * 2, offset_x : offset_x + width * 2])


def selected_card_box_matches(image, box):
    """Bind a detail read to the requested tile's orange selection corners.

    Card artwork and rarity frames are not selection evidence. Only the outer
    corner bands count, and all four corners must agree before OCR can be
    attached to this screen position.
    """
    if image is None or image.ndim != 3 or image.shape[2] < 3:
        return False
    try:
        x, y, width, height = map(int, box)
    except (TypeError, ValueError):
        return False
    if min(width, height) < 30:
        return False
    frame_height, frame_width = image.shape[:2]
    for corner_x, corner_y in ((x, y), (x + width, y), (x, y + height), (x + width, y + height)):
        left, top = corner_x - 14, corner_y - 14
        right, bottom = corner_x + 20, corner_y + 20
        if left < 0 or top < 0 or right > frame_width or bottom > frame_height:
            return False
        crop = image[top:bottom, left:right, :3]
        rows, columns = np.ogrid[top:bottom, left:right]
        outside = (columns < x + 2) | (columns >= x + width - 2) | (rows < y + 2) | (rows >= y + height - 2)
        blue, green, red = crop[..., 0], crop[..., 1], crop[..., 2]
        orange = (blue < 110) & (green >= 95) & (green <= 210) & (red > 225)
        if np.count_nonzero(orange & outside) < 35:
            return False
    return True


def read_selected_card(context, image, *, change=False, confirmation=False, roi=None):
    # Source titles start near y=265. Include padding above the text instead
    # of clipping its first pixels at 265; keep effects and the destination
    # outside this title band. All deck-card names use the requested .80 OCR
    # gate. Exact catalog matching and payment/button checks remain separate.
    source_title = roi is None and change and not confirmation
    min_score = DECK_TITLE_OCR_MIN_SCORE
    if roi is None:
        roi = [175, 650, 500, 365] if confirmation else ([175, 250, 490, 65] if change else [160, 100, 520, 290])
    exact = {}
    weak = {}
    for row in ocr(context, image, roi, [".*"]):
        match = match_card_catalog(row.text)
        if match and match.confidence == 1.0:
            key = normalize_card_name(match.matched_name)
            if float(row.score) >= min_score:
                exact[key] = match
            else:
                weak[key] = (row, match)
    if not exact and len(weak) == 1 and source_title and image is not None:
        key, (row, match) = next(iter(weak.items()))
        # A detector can clip/pad the text badly even when spelling is exact.
        # Re-read this one bounded title line, never the full effect panel or
        # a guessed tile. Low confidence itself still cannot authorize a card.
        box = list(map(int, row.box))
        if _inside(box, roi):
            x, y, width, height = box
            for scale, padding in ((1, 2), (1, 6), (2, 2)):
                left, top = max(0, x - padding), max(0, y - padding)
                right, bottom = min(image.shape[1], x + width + padding), min(image.shape[0], y + height + padding)
                location = [left, top, right - left, bottom - top]
                pixels = image
                if scale == 2:
                    pixels = _double_title_crop(image, location)
                    location = [0, 0, pixels.shape[1], pixels.shape[0]]
                detail = context.run_recognition(
                    "ProduceRecognitionPoint",
                    pixels,
                    pipeline_override={
                        "ProduceRecognitionPoint": {
                            "recognition": "OCR",
                            "roi": location,
                            "only_rec": True,
                            "expected": [".*"],
                            "order_by": "Horizontal",
                        }
                    },
                )
                rows = list(detail.filtered_results or []) if detail and detail.hit else []
                confirmed = {}
                for result in rows:
                    matched = match_card_catalog(result.text)
                    if float(result.score) >= min_score and matched and matched.confidence == 1.0:
                        confirmed[normalize_card_name(matched.matched_name)] = matched
                if confirmed:
                    if set(confirmed) != {key}:
                        return None
                    exact = confirmed
                    break
    if len(exact) != 1:
        return None
    key, match = next(iter(exact.items()))
    if change and source_title:
        # A disabled tile can update the description without changing the
        # orange selection or the source of the exchange diagram. Never
        # attach that description to a different, still-selected card.
        from .square_identity import match as match_square

        source = match_square(image, [170, 95, 142, 144])
        if source and normalize_card_name(source["name"]) != key:
            return None
    return dict(name=match.matched_name, name_key=key, rarity=match.rarity, name_confidence=1.0, rarity_confidence=1.0, identity_verified=True)


def read_delete_button(context, image, *, confirmation=False):
    """Use the original button templates, then exact text inside an enabled button."""
    roi = [350, 1085, 320, 135] if confirmation else [210, 1085, 460, 135]
    template = "produce/delete_confirm.png" if confirmation else "produce/delete.png"
    detail = context.run_recognition(
        "ProduceChooseDeleteClick",
        image,
        pipeline_override={"ProduceChooseDeleteClick": {"recognition": "TemplateMatch", "roi": roi, "template": [template], "threshold": 0.75}},
    )
    result = detail.best_result if detail and detail.hit else None
    if result is not None and isinstance(result.score, (int, float)) and result.score >= 0.75:
        box = list(map(int, result.box))
        if _inside(box, roi) and _enabled_button(image, box):
            return box
    rows = ocr(context, image, roi, [r"^削除$"])
    if len(rows) == 1 and float(rows[0].score) >= 0.90:
        box = list(map(int, rows[0].box))
        if _inside(box, roi) and _enabled_button(image, box):
            return box
    return None


def _inside(box, roi):
    x, y, w, h = box
    rx, ry, rw, rh = roi
    return w > 0 and h > 0 and rx <= x and ry <= y and x + w <= rx + rw and y + h <= ry + rh


def _enabled_button(image, box):
    if image is None:
        return False
    x, y, w, h = box
    crop = image[y : y + h, x : x + w, :3].astype("int16")
    if not crop.size:
        return False
    b, g, r = crop[..., 0], crop[..., 1], crop[..., 2]
    return float(((r > 220) & (g > 90) & (g < 205) & (b < 130)).mean()) >= 0.25


def read_delete_confirmation(context, image):
    """Require the modal title, question, same-card title, debit and buttons."""
    headers = ocr(context, image, [15, 450, 690, 400], [r"^スキルカード削除$"])
    if len(headers) != 1 or float(headers[0].score) < 0.90:
        return None
    # Card effect length changes the modal height. Anchor to its title rather
    # than the old y=585 question strip (which missed short starter cards).
    title_bottom = int(headers[0].box[1]) + int(headers[0].box[3])
    questions = ocr(context, image, [30, title_bottom + 15, 660, 115], [r"削除しますか"])
    if len(questions) != 1 or float(questions[0].score) < 0.90:
        return None
    confirm = read_delete_button(context, image, confirmation=True)
    cancel = ocr(context, image, [65, 1080, 300, 160], [r"^[×✕xX]?\s*キャンセル$"])
    card_top = max(title_bottom + 100, int(questions[0].box[1]) + int(questions[0].box[3]) + 25)
    if card_top >= 995:
        return None
    selected = read_selected_card(context, image, confirmation=True, roi=[175, card_top, 500, 995 - card_top])
    balances = sorted(
        (int(row.box[0]), int(row.text)) for row in ocr(context, image, [265, 995, 415, 115], [r"^\d{1,4}$"]) if str(row.text).isdigit()
    )
    if selected is None or confirm is None or len(cancel) != 1 or len(balances) != 2:
        return None
    before, after = (value for _, value in balances)
    if not 0 <= after < before:
        return None

    def center(row):
        x, y, w, h = map(int, row.box)
        return x + w // 2, y + h // 2

    return dict(
        selected=selected,
        before=before,
        after=after,
        confirm=(confirm[0] + confirm[2] // 2, confirm[1] + confirm[3] // 2),
        cancel=center(cancel[0]),
    )

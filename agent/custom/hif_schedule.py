"""HIF free-activity day and outing choices, independent of lesson policy."""

import re

from .frame_geometry import reference_frame

LABELS = {
    "outing": ("お出かけ", "おでかけ", "外出"),
    "supply": ("差し入れ", "活動支給", "支给", "支給"),
    "rest": ("休む", "休息"),
}


def _ocr(context, image, roi, expected):
    detail = context.run_recognition("ProduceRecognitionPoint", image, pipeline_override={
        "ProduceRecognitionPoint": {"recognition": "OCR", "roi": roi, "expected": expected}})
    return [row for row in (getattr(detail, "filtered_results", None) or [])
            if float(row.score) >= .85] if detail and detail.hit else []


def read_schedule(context, image):
    """Read activity tiles, never the calendar strip or reward effect prose.

    The third scheduled activity is identified by its outing/supply pair.
    The HUD counts DOWN to the next audition; its '3日' is not week three.
    """
    if not reference_frame(image):
        return {}
    labels = {label: kind for kind, values in LABELS.items() for label in values}
    found = {}
    for row in _ocr(context, image, [0, 800, 720, 350], ["^(?:" + "|".join(labels) + ")$"]):
        kind = labels.get(re.sub(r"\s+", "", str(row.text)))
        x, y, w, h = map(int, row.box)
        if kind and 0 <= x < x + w <= 720 and 800 <= y < y + h <= 1150 and 14 <= h <= 65:
            found.setdefault(kind, []).append([x, y, w, h])
    # Ambiguous duplicates are not click targets.
    return {kind: boxes[0] for kind, boxes in found.items() if len(boxes) == 1}


def outing_header(context, image):
    if not reference_frame(image):
        return None
    for row in _ocr(context, image, [0, 0, 240, 180], [r"^(?:お出かけ|おでかけ|外出)$"]):
        if re.sub(r"\s+", "", str(row.text)) in LABELS["outing"]:
            return list(row.box)
    return None


def read_outing_choices(context, image):
    if not outing_header(context, image):
        return []
    # These existing button templates cover neutral and attribute choices.
    # Collect all three rows, including three instances of the SAME template.
    from .reco.options_fallback import read_fallback_options

    return read_fallback_options(context, image)


def schedule_choice(strategy):
    choice = strategy.get("day_three_activity", "supply")
    return choice if choice in LABELS else "supply"


def outing_choice(strategy):
    try:
        return min(3, max(1, int(strategy.get("outing_option", 1))))
    except (TypeError, ValueError):
        return 1

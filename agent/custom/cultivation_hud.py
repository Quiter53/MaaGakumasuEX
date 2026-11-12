"""Numeric HUD readers shared by cultivation scenes, with explicit layouts."""

import re
import unicodedata

from utils import logger

from .frame_geometry import reference_frame
from .ocr_text import compact_ocr

# Schedule/work HUD sits below the shop HUD. Keep icons and adjacent rows out.
SCHEDULE_HEALTH_ROI = [328, 56, 122, 50]
SCHEDULE_POINTS_ROI = [328, 104, 114, 38]
SHOP_HEALTH_ROI = [328, 48, 122, 42]
SHOP_POINTS_ROI = [328, 94, 122, 42]

_DECORATIONS = "❤♡♥💗💖💓💕💞💟❣🖤🤍💛💚💙💜💔💘💝★☆◆◇●○◎◯※✦✧|()[]{}【】「」『』\ufe0e\ufe0f"


def numeric_text(value):
    """Remove edge decorations, never merge separate numbers or repair digits."""
    text = unicodedata.normalize("NFKC", str(value))
    # compact_ocr is also used for labels; numeric whitespace needs this guard.
    separated = "".join(" " if unicodedata.category(char) == "Cf" else char for char in text)
    if re.search(r"[0-9]\s+[0-9]", separated):
        return None
    return compact_ocr(text).strip(_DECORATIONS)


def numeric_rows_text(rows, *, min_score=0.90, fraction=False):
    parts = []
    for row in rows:
        text = numeric_text(row.text)
        if text == "":  # A separately detected heart/star is not a number.
            continue
        if text is None or float(row.score) < min_score:
            return None
        parts.append((text, getattr(row, "box", None)))
    if len(parts) == 1:
        return parts[0][0]
    # A slash anchors two operands. Bare integer fragments can also be two
    # independent values, so leave them unknown for the existing OCR retry.
    if not fraction or not parts or any(box is None or len(box) != 4 or min(box[2:]) <= 0 for _, box in parts):
        return None
    parts.sort(key=lambda part: part[1][0])
    if any(not re.fullmatch(r"[0-9/]+", text) or re.fullmatch(r"[0-9]+/[0-9]+", text) for text, _ in parts):
        return None
    first_box = parts[0][1]
    for (_, left), (_, right) in zip(parts, parts[1:]):
        overlap = min(first_box[1] + first_box[3], right[1] + right[3]) - max(first_box[1], right[1])
        gap = right[0] - left[0] - left[2]
        if overlap < min(first_box[3], right[3]) * 0.6 or not -2 <= gap <= max(3, min(left[3], right[3]) * 0.25):
            return None
    return "".join(text for text, _ in parts)


def parse_health_rows(rows):
    return _health_value(numeric_rows_text(rows, fraction=True))


def _health_value(text):
    match = re.fullmatch(r"([0-9]{1,2})/([0-9]{1,2})", text or "")
    if match:
        current, maximum = map(int, match.groups())
        if 0 <= current <= maximum <= 99 and maximum > 0:
            return current, maximum
    return None


def read_hud_line(context, image, roi, *, fraction=False):
    """Read one numeric line; preserve uncertainty rather than invent a value."""
    if not reference_frame(image):
        logger.debug(f"HUD 未读取：截图尺寸不符，shape={getattr(image, 'shape', None)}，roi={roi}")
        return None
    detail = context.run_recognition(
        "ProduceRecognitionPoint", image,
        pipeline_override={"ProduceRecognitionPoint": {
            "recognition": "OCR", "roi": roi, "expected": [r".*"], "only_rec": True, "order_by": "Horizontal",
        }},
    )
    rows = list(detail.filtered_results or []) if detail and detail.hit else []
    text = numeric_rows_text(rows, fraction=fraction)
    if text is None:
        logger.debug(f"HUD 未读取：roi={roi}，OCR={[(r.text, r.score) for r in rows]}")
        return None
    return text


def read_health(context, image, roi=SCHEDULE_HEALTH_ROI):
    text = read_hud_line(context, image, roi, fraction=True)
    value = _health_value(text)
    if value is not None:
        return value
    if text is not None:
        logger.debug(f"体力格式或范围不符：roi={roi}，text={text!r}")
    return None


def read_points(context, image, roi=SCHEDULE_POINTS_ROI):
    text = read_hud_line(context, image, roi)
    match = re.fullmatch(r"[Pp]?([0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)", text or "")
    if match:
        return int(match[1].replace(",", ""))
    if text is not None:
        logger.debug(f"P 点数格式不符：roi={roi}，text={text!r}")
    return None


def read_schedule_health(context, image):
    health = read_health(context, image)
    if health is None:
        return None
    current, maximum = health
    logger.info(f"体力: {current}/{maximum} ({current / maximum:.2%})")
    return {"current": current, "max": maximum, "ratio": current / maximum}

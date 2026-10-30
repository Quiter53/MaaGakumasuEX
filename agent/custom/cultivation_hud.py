"""Numeric HUD readers shared by cultivation scenes, with explicit layouts."""

import re
import unicodedata

from utils import logger

from .frame_geometry import reference_frame

# Schedule/work HUD sits below the shop HUD. Keep icons and adjacent rows out.
SCHEDULE_HEALTH_ROI = [328, 56, 122, 50]
SCHEDULE_POINTS_ROI = [328, 104, 114, 38]
SHOP_HEALTH_ROI = [328, 48, 122, 42]
SHOP_POINTS_ROI = [328, 94, 122, 42]


def read_hud_line(context, image, roi):
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
    if len(rows) != 1 or float(rows[0].score) < 0.90:
        logger.debug(f"HUD 未读取：roi={roi}，OCR={[(r.text, r.score) for r in rows]}")
        return None
    return unicodedata.normalize("NFKC", str(rows[0].text)).strip()


def read_health(context, image, roi=SCHEDULE_HEALTH_ROI):
    text = read_hud_line(context, image, roi)
    match = re.fullmatch(r"(\d{1,2})\s*/\s*(\d{1,2})", text or "")
    if match:
        current, maximum = map(int, match.groups())
        if 0 <= current <= maximum <= 99 and maximum > 0:
            return current, maximum
    if text is not None:
        logger.debug(f"体力格式或范围不符：roi={roi}，text={text!r}")
    return None


def read_points(context, image, roi=SCHEDULE_POINTS_ROI):
    text = read_hud_line(context, image, roi)
    if text is not None and re.fullmatch(r"(?:\d+|\d{1,3}(?:,\d{3})+)", text):
        return int(text.replace(",", ""))
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

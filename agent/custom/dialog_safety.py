"""Read modal identity before allowing recovery or reward-page input."""

import re

from .shop import ocr
from .ocr_text import compact_ocr, match_ocr_label, normalize_label


CONNECTION_TITLES = {
    "network": ["通信エラー", "通信错误", "通信錯誤", "网络错误", "網絡錯誤"],
    "date": ["日付変更", "日期变更", "日期變更", "日期变更通知", "日期變更通知"],
    "discard": ["廃棄確認", "废弃确认", "丟棄確認", "丢弃确认"],
}
CONNECTION_MESSAGES = {
    "network": ["通信中にエラーが発生しました"],
    "date": ["日付が変わりました"],
}


def connection_dialog(context, image, kind="any"):
    # The shared orange decoration is only a cheap candidate, never identity.
    overrides = None
    if kind in ("network", "date"):
        template, threshold = ("produce/disconnect_error.png", 0.82) if kind == "network" else ("date_change.png", 0.88)
        overrides = {"ProduceConnectionDialogTemplate": {"template": [template], "threshold": threshold}}
    candidate = context.run_recognition("ProduceConnectionDialogTemplate", image, pipeline_override=overrides)
    if not candidate or not candidate.hit:
        return None
    rows = ocr(context, image, [20, 800, 680, 310], [".*"])
    rows = [row for row in rows if float(row.score) >= 0.8]
    texts = [(compact_ocr(row.text), row) for row in rows]
    classified = [(text, row, match_ocr_label(text, CONNECTION_TITLES, min_score=0.8, min_margin=0.15)) for text, row in texts]
    if any(match and match.value == "discard" for _, _, match in classified):
        return None
    for text, row in texts:
        if kind in ("any", "network") and text in CONNECTION_TITLES["network"]:
            return {"kind": "network", "box": list(row.box)}
        if kind in ("any", "date") and text in CONNECTION_TITLES["date"]:
            if any(re.search(r"日付が変わりました|日期.{0,5}(变更|變更|改变|改變|更新)", body) and body != text for body, _ in texts):
                return {"kind": "date", "box": list(row.box)}
    # Fuzzy titles need a unique class, confident OCR and the associated
    # full message. A split long-vowel mark supplies no identity evidence.
    titles = [(row, match.value) for _, row, match in classified if match and match.value != "discard"]
    if len(titles) != 1:
        return None
    title, identity = titles[0]
    if kind not in ("any", identity) or float(title.score) < 0.95:
        return None
    bodies = [row for text, row in texts if float(row.score) >= 0.95 and _connection_message(text, identity)]
    if len(bodies) == 1 and _connection_message_layout(title.box, bodies[0].box):
        return {"kind": identity, "box": list(title.box)}
    return None


def _connection_message(text, identity):
    candidates = CONNECTION_MESSAGES[identity]
    # Equal normalized length permits a limited OCR substitution, while
    # refusing a truncated sentence, added question or negative suffix.
    if len(normalize_label(text)) not in {len(normalize_label(candidate)) for candidate in candidates}:
        return False
    match = match_ocr_label(text, CONNECTION_MESSAGES, min_score=0.92, min_margin=0.15)
    return match is not None and match.value == identity


def _connection_message_layout(title, body):
    """Associate adjacent left-aligned rows in the existing lower modal ROI."""
    for box in (title, body):
        if box is None or len(box) != 4:
            return False
        x, y, w, h = box
        if w <= 0 or h <= 0 or x < 20 or y < 800 or x + w > 700 or y + h > 1110:
            return False
    gap = body[1] - (title[1] + title[3])
    return 20 <= gap <= 120 and abs(body[0] - title[0]) <= 60 and body[0] + body[2] >= title[0] + title[2]


def drink_overlay_cancel(context, image, *, title_rows=None):
    """Return (overlay present, verified cancel box); never select Yes/Use."""
    # Supplied rows must belong to this exact frame. Never re-read the same
    # identity at a different scale and veto an already verified title.
    rows = title_rows if title_rows is not None else ocr(context, image, [15, 500, 680, 640], [r"^廃棄確認$", r"^[PＰ]?\s*ドリンク詳細$"])
    rows = [row for row in rows if float(row.score) >= 0.8]
    if not rows:
        return False, None
    discard = any(re.sub(r"\s+", "", row.text) == "廃棄確認" for row in rows)
    if discard:
        roi, expected = [65, 1090, 300, 150], [r"^いいえ$"]
    else:
        bottom = max(int(row.box[1]) + int(row.box[3]) for row in rows)
        roi = [65, bottom + 60, 300, max(1, min(1240, image.shape[0]) - bottom - 60)]
        expected = [r"^[×✕xX]?\s*キャンセル$"]
    buttons = [row for row in ocr(context, image, roi, expected) if float(row.score) >= 0.8]
    return True, list(buttons[0].box) if len(buttons) == 1 else None

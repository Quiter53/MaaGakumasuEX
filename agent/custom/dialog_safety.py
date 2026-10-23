"""Read modal identity before allowing recovery or reward-page input."""

import re

from .shop import ocr


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
    texts = [(re.sub(r"\s+", "", row.text), row) for row in rows]
    if any(text in ("廃棄確認", "废弃确认", "丟棄確認", "丢弃确认") for text, _ in texts):
        return None
    for text, row in texts:
        if kind in ("any", "network") and text in ("通信エラー", "通信错误", "通信錯誤", "网络错误", "網絡錯誤"):
            return {"kind": "network", "box": list(row.box)}
        if kind in ("any", "date") and text in ("日付変更", "日期变更", "日期變更", "日期变更通知", "日期變更通知"):
            if any(re.search(r"日付が変わりました|日期.{0,5}(变更|變更|改变|改變|更新)", body) and body != text for body, _ in texts):
                return {"kind": "date", "box": list(row.box)}
    return None


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

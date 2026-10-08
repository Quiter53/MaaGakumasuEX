"""Exact deck identity and shop deletion confirmation, without game input."""
from .card_visual import match_card_catalog, normalize_card_name
from .shop import ocr


def read_selected_card(context, image, *, change=False, confirmation=False, roi=None):
    # The change destination is above y=250; only read the owned source panel.
    if roi is None:
        roi = [175, 650, 500, 365] if confirmation else ([170, 265, 490, 350] if change else [160, 100, 520, 290])
    exact = {}
    for row in ocr(context, image, roi, [".*"]):
        if float(row.score) < .90:
            continue
        match = match_card_catalog(row.text)
        if match and match.confidence == 1.0:
            exact[normalize_card_name(match.matched_name)] = match
    if len(exact) != 1:
        return None
    key, match = next(iter(exact.items()))
    return dict(name=match.matched_name, name_key=key, rarity=match.rarity,
                name_confidence=1.0, rarity_confidence=1.0, identity_verified=True)


def read_delete_button(context, image, *, confirmation=False):
    """Use the original button templates, then exact text inside an enabled button."""
    roi = [350, 1085, 320, 135] if confirmation else [210, 1085, 460, 135]
    template = "produce/delete_confirm.png" if confirmation else "produce/delete.png"
    detail = context.run_recognition("ProduceChooseDeleteClick", image, pipeline_override={
        "ProduceChooseDeleteClick": {"recognition": "TemplateMatch", "roi": roi,
                                    "template": [template], "threshold": .75}})
    result = detail.best_result if detail and detail.hit else None
    if result is not None and isinstance(result.score, (int, float)) and result.score >= .75:
        box = list(map(int, result.box))
        if _inside(box, roi) and _enabled_button(image, box):
            return box
    rows = ocr(context, image, roi, [r"^削除$"])
    if len(rows) == 1 and float(rows[0].score) >= .90:
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
    crop = image[y:y + h, x:x + w, :3].astype("int16")
    if not crop.size:
        return False
    b, g, r = crop[..., 0], crop[..., 1], crop[..., 2]
    return float(((r > 220) & (g > 90) & (g < 205) & (b < 130)).mean()) >= .25


def read_delete_confirmation(context, image):
    """Require the modal title, question, same-card title, debit and buttons."""
    headers = ocr(context, image, [15, 450, 690, 400], [r"^スキルカード削除$"])
    if len(headers) != 1 or float(headers[0].score) < .90:
        return None
    # Card effect length changes the modal height. Anchor to its title rather
    # than the old y=585 question strip (which missed short starter cards).
    title_bottom = int(headers[0].box[1]) + int(headers[0].box[3])
    questions = ocr(context, image, [30, title_bottom + 15, 660, 115], [r"削除しますか"])
    if len(questions) != 1 or float(questions[0].score) < .90:
        return None
    confirm = read_delete_button(context, image, confirmation=True)
    cancel = ocr(context, image, [65, 1080, 300, 160], [r"^[×✕xX]?\s*キャンセル$"])
    card_top = max(title_bottom + 100, int(questions[0].box[1]) + int(questions[0].box[3]) + 25)
    if card_top >= 995:
        return None
    selected = read_selected_card(context, image, confirmation=True, roi=[175, card_top, 500, 995 - card_top])
    balances = sorted((int(row.box[0]), int(row.text)) for row in
                      ocr(context, image, [265, 995, 415, 115], [r"^\d{1,4}$"])
                      if str(row.text).isdigit())
    if selected is None or confirm is None or len(cancel) != 1 or len(balances) != 2:
        return None
    before, after = (value for _, value in balances)
    if not 0 <= after < before:
        return None
    def center(row):
        x, y, w, h = map(int, row.box)
        return x + w // 2, y + h // 2
    return dict(selected=selected, before=before, after=after,
                confirm=(confirm[0] + confirm[2] // 2, confirm[1] + confirm[3] // 2), cancel=center(cancel[0]))

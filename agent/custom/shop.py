"""Verified shop layouts and conservative drink identity/price readers."""
from .frame_geometry import reference_frame, clip_roi
import re
import unicodedata

import numpy as np


from .drink_policy import TARGET_NAMES as TARGET_DRINK_NAMES
INTERVAL_DRINK_SLOTS = [[374, 690, 126, 126], [528, 690, 126, 126]]
INTERVAL_CARD_SLOTS = [
    [x, y, 126, 126] for y, xs in ((498, (54, 210, 374, 528)), (690, (54, 210))) for x in xs
]
CONSULTATION_SLOTS = [[x, y, 126, 126] for y in (482, 670) for x in (66, 220, 374, 528)]
# Fixed 720x1280 shop HUD: exclude both icons and keep the two text rows apart.
SHOP_HEALTH_ROI = [328, 48, 122, 42]
SHOP_POINTS_ROI = [328, 94, 122, 42]


def consultation_grid_visible(image):
    """Joint eight-slot geometry and yellow P icons, not arbitrary white areas."""
    if not reference_frame(image):
        return False
    counts = [0, 0]
    for x, y, w, h in CONSULTATION_SLOTS:
        crop = image[y + h:y + h + 42, x:x + w, :3].astype(np.int16)
        # The interval price rows sit 16/20 px lower.  Match the P icon
        # inside the consultation pill, excluding the bottom edge/other row.
        icon = crop[6:30, :32]
        yellow = (icon[:, :, 0] < 140) & (icon[:, :, 1] > 130) & (icon[:, :, 2] > 150) & (icon[:, :, 2] > icon[:, :, 0] + 50)
        if float((crop.min(axis=2) > 215).mean()) >= .35 and int(yellow.sum()) >= 45:
            counts[y == 670] += 1
    return min(counts) >= 3


def read_consultation_desk_entry(context, image):
    """Allow re-entry only through a labelled exchange control on the desk."""
    rows = ocr(context, image, [30, 30, 660, 910], [r"^相談$", r"^(交換する|アイテム交換|Pポイント交換)$"])
    if not any(row.text == "相談" and float(row.score) >= .90 and int(row.box[1]) < 180 for row in rows):
        return None
    buttons = [row for row in rows if row.text != "相談" and float(row.score) >= .90
               and 180 <= int(row.box[0]) <= 500 and 420 <= int(row.box[1]) <= 850]
    return list(buttons[0].box) if len(buttons) == 1 else None


def drink_title(value):
    text = "".join(str(value).split())
    # Verified dakuten confusion in the native OCR; not a fuzzy name matcher.
    return {"センプリソーダ": "センブリソーダ", "プーストエキス": "ブーストエキス"}.get(text, text)


def ocr(context, image, roi, expected):
    roi = clip_roi(image, roi)
    if roi is None:
        return []
    detail = context.run_recognition("ProduceRecognitionPoint", image, pipeline_override={
        "ProduceRecognitionPoint": {"recognition": "OCR", "roi": roi,
                                    "expected": expected, "order_by": "Horizontal"}})
    return list(detail.filtered_results or []) if detail and detail.hit else []


def read_number(context, image, roi):
    values = []
    for result in ocr(context, image, roi, [r"^\d{1,4}$"]):
        text = str(result.text).strip()
        if re.fullmatch(r"\d{1,4}", text):
            values.append((int(result.box[0]), int(text)))
    return max(values, default=(0, None), key=lambda pair: pair[0])[1]


def parse_shop_price(text):
    """Accept the P icon joined to a price, never arbitrary embedded digits."""
    value = re.sub(r"\s+", "", str(text)).translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    match = re.fullmatch(r"[PＰp]?([1-9]\d{0,2})", value)
    return int(match[1]) if match else None


def price_grid_visible(image, slots):
    if not reference_frame(image):
        return False
    count = 0
    for x, y, w, h in slots:
        crop = image[y + h:y + h + 42, x:x + w, :3]
        if crop.size and float((crop.min(axis=2) > 215).mean()) > .22:
            count += 1
    return count >= 6


def is_interval_shop(context, image):
    # Do not infer the two-drink layout from price alone or a generic shop flag.
    if not price_grid_visible(image, INTERVAL_CARD_SLOTS + INTERVAL_DRINK_SLOTS):
        return False
    header = ocr(context, image, [0, 0, 220, 180], [r"^インタ[ー一][バパ]ル$"])
    # Live OCR can split the header and read the exit label as just "×終".
    exit_button = context.run_recognition("ProduceShoppingExit", image)
    if not (exit_button and exit_button.hit and exit_button.best_result):
        return False
    x, y, w, h = map(int, exit_button.best_result.box)
    if not (540 <= x <= 650 and 1020 <= y <= 1120):
        return False
    if header:
        return True
    # HIF-specific joint fallback: ordinary consultation has no customization.
    customize = ocr(context, image, [340, 870, 240, 110], [r"^(スキルカード)?カスタマイズ$"])
    refresh = ocr(context, image, [0, 1010, 220, 80], [r"^リフ.*ッシュ$"])
    return bool(customize and refresh)


def read_refresh(context, image, *, verified_shop_image=None):
    # A caller may already have checked this exact screenshot. Healing or
    # customization can return a new frame, which must pass the usual check.
    if (image is None or verified_shop_image is not image) and not is_interval_shop(context, image):
        return None
    rows = ocr(context, image, [0, 1010, 220, 80], [r"^リフ.*ッシュ$"])
    cost = read_number(context, image, [80, 1080, 110, 60])
    if len(rows) != 1 or cost is None or cost <= 0:
        return None
    x, y, w, h = map(int, rows[0].box)
    # Native OCR can include the refresh icon, moving the left edge to x=18.
    # Keep the whole click inside the verified lower-left button, not the grid.
    if not (10 <= x < 190 and 1020 <= y < 1090 and x + w <= 215):
        return None
    return {"point": (x + w // 2, y + h // 2), "cost": cost}


def _read_shop_hud(context, image, roi):
    """Recognize a known numeric line directly, without a text detector."""
    if not reference_frame(image):
        return None
    detail = context.run_recognition("ProduceRecognitionPoint", image, pipeline_override={
        "ProduceRecognitionPoint": {"recognition": "OCR", "roi": roi,
                                    "expected": [r".*"], "only_rec": True,
                                    "order_by": "Horizontal"}})
    rows = list(detail.filtered_results or []) if detail and detail.hit else []
    if len(rows) != 1 or float(rows[0].score) < .90:
        return None
    return unicodedata.normalize("NFKC", str(rows[0].text)).strip()


def read_interval_health(context, image):
    """Read only the current/max HUD, never a recovery preview or P points."""
    text = _read_shop_hud(context, image, SHOP_HEALTH_ROI)
    match = re.fullmatch(r"(\d{1,2})\s*/\s*(\d{1,2})", text or "")
    if not match:
        return None
    current, maximum = map(int, match.groups())
    return (current, maximum) if 0 <= current <= maximum <= 99 and maximum > 0 else None


def read_shop_points(context, image):
    text = _read_shop_hud(context, image, SHOP_POINTS_ROI)
    # Allow either plain digits or properly grouped thousands, never strip
    # arbitrary letters/signs into an apparently valid spending balance.
    if text is None or not re.fullmatch(r"(?:\d+|\d{1,3}(?:,\d{3})+)", text):
        return None
    return int(text.replace(",", ""))


def read_interval_heal_button(context, image):
    rows = ocr(context, image, [550, 870, 140, 115], [r"^回復$", r"^恢复$"])
    return list(rows[0].box) if len(rows) == 1 and float(rows[0].score) >= .90 else None


def _recovery_step_button(image, roi, plus):
    """Find the gray +/- glyph on a white button between HP and the cost row."""
    x, y, w, h = roi
    if h <= 0:
        return None
    pixels = image[y:y+h, x:x+w].astype(np.int16)
    mask = (pixels.min(2) >= 60) & (pixels.max(2) <= 175) & (pixels.max(2)-pixels.min(2) <= 18)
    yy, xx = np.where(mask)
    if not len(xx):
        return None
    left, right, top, bottom = int(xx.min()), int(xx.max())+1, int(yy.min()), int(yy.max())+1
    width, height = right-left, bottom-top
    if not 18 <= width <= 65 or not (18 <= height <= 65 if plus else 3 <= height <= 16):
        return None
    glyph = mask[top:bottom, left:right]
    cx, cy = width//2, height//2
    if glyph[max(0,cy-1):cy+2].mean() < .75:
        return None
    if plus and (not .7 <= width/height <= 1.4 or glyph[:,max(0,cx-1):cx+2].mean() < .75
                 or glyph[:max(1,height//4),:max(1,width//4)].mean() > .1):
        return None
    surround = pixels[max(0,top-10):min(h,bottom+10),max(0,left-10):min(w,right+10)]
    if (surround.min(2) > 215).mean() < .60:
        return None
    return [x+left, y+top, width, height]


def interval_heal_target(value):
    """Old booleans/select indices are not health thresholds."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return 20
    text = str(value).strip()
    return int(text) if re.fullmatch(r"(?:0|[1-9][0-9]?)", text) else 20


def read_interval_recovery(context, image, expected_health=None):
    from .interval_recovery import read_dialog
    return read_dialog(context, image, expected_health)


def selected_drink_matches(context, image, drink_id, *, confirmation=False):
    name = TARGET_DRINK_NAMES.get(drink_id)
    if name is None:
        return False
    roi = [160, 600, 520, 410] if confirmation else [180, 160, 510, 115]
    rows = ocr(context, image, roi, [".*"])
    # Exact normalized title; no fuzzy match between ordinary and special bottles.
    return any(drink_title(row.text) == name for row in rows)


def stock_signature(image, slots):
    """Only product interiors: animations/balance changes cannot fake refresh."""
    return np.concatenate([image[y + 12:y + h - 12:6, x + 12:x + w - 12:6, :3].reshape(-1)
                           for x, y, w, h in slots]).astype(np.int16)


def refresh_is_affordable(points, cost, reserve=75):
    return points is not None and cost is not None and cost > 0 and points >= cost + reserve


def choose_discard(owned, wanted_id, *, non_target_only=False):
    """Unknown titles are protected; only replace a verified non-target/duplicate."""
    if len(owned) != 4 or any(not item.get("verified") for item in owned.values()):
        return None
    non_targets = [slot for slot, item in owned.items() if item.get("id") is None]
    if non_targets:
        return min(non_targets)
    if non_target_only or wanted_id in {item.get("id") for item in owned.values()}:
        return None
    for slot, item in sorted(owned.items()):
        if sum(other.get("id") == item["id"] for other in owned.values()) > 1:
            return slot
    return None


def owned_targets_complete(owned):
    return len(owned) == 4 and all(item.get("verified") and item.get("id") for item in owned.values()) \
        and len({item["id"] for item in owned.values()}) == 4


def owned_detail_title(context, image):
    header = ocr(context, image, [15, 500, 680, 520], [r"^[PＰ]?\s*ドリンク詳細$"])
    if len(header) != 1 or float(header[0].score) < .90:
        return None
    bottom = int(header[0].box[1]) + int(header[0].box[3])
    # The panel moves with effect length and when a discard modal opens.
    # Read only its title line, not background prices or the panel header.
    rows = ocr(context, image, [170, bottom + 30, 500, 65], [".*"])
    names = {drink_title(row.text) for row in rows if float(row.score) >= .90
             and 170 <= int(row.box[0]) <= 420
             and re.search(r"[一-龯ぁ-ゖァ-ヺ]", drink_title(row.text))
             and re.fullmatch(r"[一-龯ぁ-ゖァ-ヺーA-Za-z0-9・]+", drink_title(row.text))
             and drink_title(row.text) not in ("捨てる", "ドリンク詳細", "Pドリンク詳細", "7詳細")
             and "スキルカード" not in drink_title(row.text)}
    if len(names) != 1:
        return None
    return next(iter(names))


def read_discard_confirmation(context, image, expected_name):
    """Exact anchored discard question and both buttons, never substring identity."""
    headers = ocr(context, image, [15, 780, 680, 360], [r"^廃棄確認$"])
    if len(headers) != 1 or float(headers[0].score) < .90:
        return None
    bottom = int(headers[0].box[1]) + int(headers[0].box[3])
    body = ocr(context, image, [30, bottom + 25, 650, 115], [r"^本当に.+を捨てますか[?？]$"])
    names = []
    for row in body:
        match = re.fullmatch(r"本当に(.+)を捨てますか[?？]", drink_title(row.text))
        if match and float(row.score) >= .90:
            names.append(drink_title(match.group(1)))
    yes = ocr(context, image, [380, 1090, 260, 150], [r"^はい$"])
    no = ocr(context, image, [65, 1090, 300, 150], [r"^いいえ$"])
    if names != [drink_title(expected_name)] or len(yes) != 1 or len(no) != 1:
        return None
    if min(float(yes[0].score), float(no[0].score)) < .90:
        return None
    return {"name": names[0], "yes": list(yes[0].box), "no": list(no[0].box)}


def owned_throw_box(context, image, expected_name):
    if owned_detail_title(context, image) != drink_title(expected_name):
        return None
    headers = ocr(context, image, [15, 500, 680, 520], [r"^[PＰ]?\s*ドリンク詳細$"])
    if len(headers) != 1:
        return None
    bottom = int(headers[0].box[1]) + int(headers[0].box[3])
    rows = ocr(context, image, [480, bottom + 25, 200, 90], [r"^捨てる$"])
    return list(rows[0].box) if len(rows) == 1 and float(rows[0].score) >= .90 else None


def shop_occupied(context, image):
    """Visible cyan filled circles supplement drink-ring templates on HIF."""
    from .produce.rewards import ProduceChooseRewardAuto
    occupied = ProduceChooseRewardAuto._scan_inventory_occupancy(context, image, {})
    for slot, (x, y) in enumerate(ProduceChooseRewardAuto.INVENTORY_SLOT_CENTERS):
        p = image[y - 22:y + 23, x - 24:x + 25, :3].astype(np.int16)
        if p.size and int(((p[:, :, 0] > 180) & (p[:, :, 1] > 160) & (p[:, :, 2] < 190)).sum()) >= 300:
            occupied.add(slot)
    return occupied

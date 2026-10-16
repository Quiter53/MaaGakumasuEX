"""Shared HIF customization observations and per-card completion policy.

Never infer completion from rarity or a dim button alone. The exported menu
limits and current remaining counts must agree before any purchase is offered.
"""
import re
import json
from pathlib import Path
from functools import lru_cache

import numpy as np

from .shop import ocr
from .card_visual import normalize_card_name
from .frame_geometry import reference_frame


@lru_cache(maxsize=1)
def catalog():
    root = Path(__file__).resolve().parents[2]
    path = root / "data/card_customizations.json"
    if not path.exists():
        path = root / "assets/data/card_customizations.json"
    data = json.loads(path.read_text("utf8"))["cards"]
    return {normalize_card_name(name): dict(value, name=name) for name, value in data.items()}


def targets(context):
    node = context.get_node_data("ProduceHIFCustomizationPolicy") or {}
    policy = node.get("attach", {})
    if not policy.get("enabled", False):
        return []
    rows = policy.get("targets", "[]")
    rows = json.loads(rows) if isinstance(rows, str) else rows
    if not isinstance(rows, list) or len(rows) > 64:
        raise ValueError("自定义目标配置无效")
    result = []
    for row in rows:
        key = normalize_card_name(row["name"])
        spec = catalog().get(key)
        counts = row.get("counts")
        if (not spec or not isinstance(counts, list) or len(counts) != len(spec["options"])
                or any(type(n) is not int or n < 0 or n > len(o["costs"]) for n, o in zip(counts, spec["options"]))
                or not 0 < sum(counts) <= spec["max"] or any(t["key"] == key for t in result)):
            raise ValueError("自定义目标次数或卡牌无效")
        result.append(dict(key=key, name=spec["name"], counts=counts, spec=spec))
    return result


def text(row):
    return re.sub(r"\s+", "", str(row.text)).translate(str.maketrans("０１２３４５６７８９", "0123456789"))


def rows(context, image, roi):
    return [r for r in ocr(context, image, roi, [".*"]) if float(r.score) >= .85]


def unique_control(observed, label):
    found = [list(r.box) for r in observed if text(r) == label]
    return found[0] if len(found) == 1 else None


def same_card_row(first, second):
    """Align fixed-size interior crops when stitching scrolling list rows.

    YOLO box jitter must not scale the artwork differently on every frame.
    Name equality alone is insufficient: several copies can occupy one row.
    """
    if len(first) != len(second):
        return False
    for a, b in zip(first, second):
        if a.get("name_key") and b.get("name_key") and a["name_key"] != b["name_key"]:
            return False
        left, right = a["pixels"].astype(np.int16), b["pixels"].astype(np.int16)
        if left.shape != (64, 64, 3) or right.shape != left.shape:
            return False
        sample = left[6:-6, 6:-6]
        if not any(float(np.abs(sample-right[6+dy:58+dy, 6+dx:58+dx]).mean()) <= 9
                   and float(np.quantile(np.abs(sample-right[6+dy:58+dy, 6+dx:58+dx]), .95)) <= 40
                   for dy in range(-5, 6) for dx in range(-5, 6)):
            return False
    return True


def count(observed, pattern):
    found = [int(m.group(1)) for r in observed if (m := re.fullmatch(pattern, text(r)))]
    return found[0] if len(found) == 1 else None


def selected_option(image, index):
    """Orange outline outside the menu tile; the colored tile is not selection."""
    x, y, w, h = 26 + 219 * index, 876, 226, 135
    # Match the complete four-sided frame within a small alignment window.
    # The orange option fill alone must never count as a selection outline.
    area = image[y-4:y+h+4, x-4:x+w+4, :3].astype(np.int16)
    b, g, r = area[:, :, 0], area[:, :, 1], area[:, :, 2]
    orange = (r > 225) & (g > 100) & (g < 220) & (b < 130) & (r-g > 35)
    for dy in range(0, 9, 2):
        for dx in range(0, 9, 2):
            aligned = orange[dy:dy+h, dx:dx+w]
            if all(float(strip.mean()) > .13 for strip in
                   (aligned[:10, :], aligned[-10:, :], aligned[:, :10], aligned[:, -10:])):
                return True
    return False


def read_page(context, image):
    if not reference_frame(image):
        return None
    bottom = rows(context, image, [40, 1070, 610, 145])
    back = unique_control(bottom, "一覧に戻る")
    list_back = unique_control(bottom, "もどる")
    open_button = unique_control(bottom, "カスタマイズ")
    quota = count(bottom, r"カスタマイズ可能スキルカードあと(\d+)枚")
    if not (back or (list_back and open_button and quota is not None)):
        return None
    title_rows = rows(context, image, [185, 100, 490, 110])
    known = [(normalize_card_name(text(r)), text(r)) for r in title_rows
             if normalize_card_name(text(r)) in catalog()]
    names = {key for key, _ in known}
    name = next(iter(names)) if len(names) == 1 else None
    upgraded = any(raw.endswith(("+", "＋")) for key, raw in known if key == name)
    page = dict(stage="menu" if back else "list", back=back or list_back,
                open=open_button, quota=quota, name=name, upgraded=upgraded)
    if not back:
        return page
    observed = rows(context, image, [30, 815, 650, 220])
    if not unique_control(observed, "カスタマイズメニュー"):
        return None
    page.update(execute=unique_control(bottom, "実行する"),
                total=count(bottom, r"合計あと(\d+)回"), options=[])
    if name is None:
        return page
    for index, option in enumerate(catalog()[name]["options"]):
        # Columns are retained when an option is disabled; never renumber them.
        left = 35 + index * 219
        local = [r for r in observed if left <= r.box[0] + r.box[2]/2 < left + 215]
        capped = any(text(r) == "カスタマイズ上限" for r in local)
        remaining = count(local, r"あと(\d+)回")
        price_rows = [r for r in local if 950 <= r.box[1] <= 980]
        price = count(price_rows, r"(?:P)?(\d+)")
        page["options"].append(dict(remaining=0 if capped else remaining, capped=capped, price=price,
                                    selected=selected_option(image, index), box=[left+8, 900, 195, 85]))
    if all(o["capped"] for o in page["options"]):
        page["total"] = 0
    return page


def next_purchase(target, page, points):
    """Return (status, option index). Unreadable is distinct from completed."""
    if page is None or page.get("stage") != "menu" or page.get("name") != target["key"] or not page.get("upgraded"):
        return "unknown", None
    spec = target["spec"]
    options = page.get("options", [])
    total = page.get("total")
    if total == 0:
        return "capped", None
    if total is None or len(options) != len(spec["options"]) or not 0 <= total <= spec["max"]:
        return "unknown", None
    done = []
    for option, base in zip(options, spec["options"]):
        remaining = option["remaining"]
        if remaining is None or not 0 <= remaining <= len(base["costs"]):
            return "unknown", None
        done.append(len(base["costs"]) - remaining)
    if sum(done) != spec["max"] - total:
        return "unknown", None
    for i, (desired, actual) in enumerate(zip(target["counts"], done)):
        if actual >= desired:
            continue
        price = spec["options"][i]["costs"][actual]
        if options[i]["price"] != price or points is None:
            return "unknown", None
        return ("buy" if points >= price else "budget"), i
    return "done", None


def entry(context, image):
    if not reference_frame(image):
        return None
    observed = rows(context, image, [310, 880, 270, 130])
    box = unique_control(observed, "カスタマイズ")
    quota = count(observed, r"カスタマイズ可能枚数あと(\d+)枚")
    return dict(box=box, quota=quota) if box and quota is not None and 0 <= quota <= 2 else None


def receipt(context, image, expected):
    for row in rows(context, image, [30, 950, 660, 150]):
        value = text(row)
        if value.endswith("をカスタマイズしました"):
            name = value.removesuffix("をカスタマイズしました")
            # Native OCR confuses the identical-looking final ヘ/へ glyph.
            if normalize_card_name(name).replace("ヘ", "へ") == expected.replace("ヘ", "へ"):
                return list(row.box)
    return None

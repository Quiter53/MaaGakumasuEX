"""HIF's attribute and follow-up choices share rows, but not the same step."""

import re

import numpy as np


HIF_COURSE_LABEL = r"^(?:(Vo|Da|Vi)\s*[.．・]\s*)?(?:公開)?レッスン$"


def read_hif_scores(context, image):
    """Read all three current/cap pairs on the daily or raised choice HUD."""
    for y, height in ((668, 80), (220, 250)):
        score, caps = {}, []
        for index, attribute in enumerate(("Vo", "Da", "Vi")):
            detail = context.run_recognition("ProduceRecognitionScore", image,
                pipeline_override={"ProduceRecognitionScore": {"recognition": "OCR",
                    "roi": [150 + index * 150, y, 136, height], "expected": []}})
            rows = sorted(list(getattr(detail, "all_results", None) or []) if detail else [], key=lambda row: row.box[1])
            texts = [re.sub(r"[\s,，]", "", str(row.text)).translate(str.maketrans("０１２３４５６７８９／", "0123456789/"))
                     for row in rows if float(row.score) >= .85]
            # The pink microphone badge joins Vo's number as § in the native
            # day-6 frame. Accept only that observed icon, not arbitrary text.
            texts = [value.removeprefix("§") for value in texts]
            pair = next((re.fullmatch(r"(\d{1,4})/(\d{2,4})", value) for value in texts
                         if re.fullmatch(r"(\d{1,4})/(\d{2,4})", value)), None)
            if pair:
                current, cap = map(int, pair.groups())
            else:
                currents = [int(value) for value in texts if re.fullmatch(r"\d{1,4}", value)]
                maxima = [int(value[1:]) for value in texts if re.fullmatch(r"/\d{2,4}", value)]
                if len(currents) != 1 or len(maxima) != 1:
                    break
                current, cap = currents[0], maxima[0]
            if not (0 <= current <= cap < 9999):
                break
            score[attribute] = current
            caps.append(cap)
        if len(score) == 3 and len(set(caps)) == 1:
            return {**score, "max": caps[0]}
    return None


def _next_targets(value):
    # MaaFramework 5.13 returns parsed targets, not the strings from JSON files.
    values = [value] if isinstance(value, (str, dict)) else value or []
    return [("[JumpBack]" if item.get("jump_back") else "")
            + ("[Anchor]" if item.get("anchor") else "") + item["name"]
            if isinstance(item, dict) else item for item in values]


def configure_hif_start_routes(context):
    """Insert HIF's final confirmation after all UI option overrides are merged."""
    if context is None:
        return False
    entry = context.get_node_data("ProduceEntryFlag")
    if not isinstance(entry, dict):
        return False
    names = _next_targets(entry.get("next", []))
    if not any(str(name).removeprefix("[JumpBack]") == "ProduceEntryHIF" for name in names):
        return False
    updates = {}
    for name in ("ProduceLoop", "ProduceChooseDifficulty", "ProduceChooseIdolNext"):
        node = context.get_node_data(name) or {}
        next_nodes = _next_targets(node.get("next", []))
        next_nodes = [item for item in next_nodes if item not in ("ProduceHIFBeginFlag", "ProduceHIFResumeFlag")]
        # Preserve network recovery first, then recognize this exact HIF page.
        index = 0
        while index < len(next_nodes) and "ProduceDisconnect" in next_nodes[index]:
            index += 1
        next_nodes.insert(index, "ProduceHIFBeginFlag")
        if name == "ProduceLoop":
            next_nodes.insert(index + 1, "ProduceHIFResumeFlag")
        if name == "ProduceLoop" and "ProduceHomeFlag" in next_nodes and "ProduceSkipPreparation" in next_nodes:
            # A resume option must not send a known home screen into cultivation.
            next_nodes.remove("ProduceHomeFlag")
            next_nodes.insert(next_nodes.index("ProduceSkipPreparation"), "ProduceHomeFlag")
        updates[name] = {"next": next_nodes}
    for name in ("ProduceUseItem", "ProduceLauncher"):
        updates[name] = {"recognition": {"type": "Custom", "param": {"custom_recognition": "ProduceHIFStart"}}}
    updates["ProduceLauncher"]["post_delay"] = 1500
    context.override_pipeline(updates)
    return True


def read_hif_start(context, image):
    """Require both the preparation header and its exact orange start control."""
    header = context.run_recognition("ProduceRecognitionPoint", image,
        pipeline_override={"ProduceRecognitionPoint": {"recognition": "OCR", "roi": [0, 0, 280, 110],
            "expected": ["開始確認", "开始确认"]}})
    if not (header and header.hit):
        return None
    button = context.run_recognition("ProduceRecognitionPoint", image,
        pipeline_override={"ProduceRecognitionPoint": {"recognition": "OCR", "roi": [200, 1010, 350, 170],
            "expected": ["^プロデュース開始$", "^开始培育$", "^開始培育$"]}})
    if not (button and button.hit):
        return None
    for row in button.filtered_results or []:
        text = re.sub(r"\s+", "", str(row.text))
        x, y, w, h = row.box
        if text in ("プロデュース開始", "开始培育", "開始培育") and 220 <= x <= 500 and 1030 <= y <= 1160 and w >= 80:
            return list(row.box)
    return None


def read_hif_resume(context, image):
    """Resume only on explicit cultivation pages, never a generic next button."""
    for name in ("ProduceHIFClassFlag", "ProduceHIFCourseFlag", "ProduceHIFChoicesFlag",
                 "ProduceHIFChangeFlag", "ProduceHIFChangeSourceFlag", "ProduceHIFIntervalShopFlag",
                 "ProduceHIFShopEntryFlag", "ProduceHIFShopFlag", "ProduceHIFSupplyFlag",
                 "ProduceCardsFlag", "ProduceChooseItemFlag", "ProduceChooseGetFlag",
                 "ProduceHIFFailedFlag", "ProduceKeepDrinkFlag", "ProduceSkipChatFlag", "ProduceGeneration"):
        result = context.run_recognition(name, image)
        if result and result.hit and result.best_result:
            return list(result.best_result.box)
    return None


def _segments(mask):
    edges = np.diff(np.r_[False, mask, False].astype(np.int8))
    return list(zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)))


def _attribute_masks(pixels):
    b, g, r = pixels[:, :, 0], pixels[:, :, 1], pixels[:, :, 2]
    return {
        "Vo": (r >= 190) & (g <= 145) & (b >= 80) & (r - b >= 30),
        "Da": (b >= 190) & (r <= 120) & (g >= 80),
        "Vi": (r >= 210) & (g >= 140) & (b <= 140),
    }


def _choice_bands(fill):
    mask = fill.mean(axis=1) >= .76
    # Text/badges can cut a one- or two-pixel gap through a shaded preview row.
    for start, end in _segments(~mask):
        if start > 0 and end < len(mask) and end - start <= 3:
            mask[start:end] = True
    bands = [(int(620 + start), int(620 + end)) for start, end in _segments(mask)
             if 35 <= end - start <= 100]
    if len(bands) != 3 or any(not 80 <= b[0] - a[0] <= 140 for a, b in zip(bands, bands[1:])):
        return []
    return bands


def hif_choice_rows(image):
    """Detect HIF's bright, aligned answer bars and their colored icons.

    The old lesson templates include the background gradient, which HIF changes.
    Do not relax their confidence globally: validate HIF's row geometry instead.
    Framework normalizes width to 720; integer rounding can produce a height
    of 1279/1281 instead of 1280. Geometry tolerates this two-pixel difference.
    """
    if image.ndim != 3 or image.shape[2] != 3 or image.shape[1] != 720 or abs(image.shape[0] - 1280) > 2:
        return []
    area = image[620:970, 120:610].astype(np.int16)
    bright = (area.min(axis=2) >= 165) & (area.max(axis=2) >= 225) & (area.mean(axis=2) >= 205)
    # A first tap opens the effect preview and fills the selected row with its
    # attribute color. It is still a choice screen, not a completed selection.
    colors = _attribute_masks(area)
    saturated = colors["Vo"] | colors["Da"] | colors["Vi"]
    bands = _choice_bands(bright | saturated)
    if not bands:
        # The two unselected choices are shaded during the preview. Only allow
        # this lower brightness when a solid attribute-colored selected row
        # accompanies the same validated three-row layout.
        shaded = (area.min(axis=2) >= 160) & (area.max(axis=2) >= 210) & (area.mean(axis=2) >= 180)
        bands = _choice_bands(shaded | saturated)
        if not any(saturated[start - 620:end - 620].mean() >= .45 for start, end in bands):
            return []
    if not bands:
        return []
    rows = []
    for start, end in bands:
        # Only text and left-side icons, never stamina/trouble badges on the right.
        pixels = image[start:end, 75:460].astype(np.int16)
        counts = {name: int(mask.sum()) for name, mask in _attribute_masks(pixels).items()}
        ranked = sorted(counts, key=counts.get, reverse=True)
        best, second = counts[ranked[0]], counts[ranked[1]]
        if best < 40 or best < second * 2:
            return []
        rows.append((ranked[0], [52, start, 616, end - start], best / sum(counts.values())))
    return rows


def read_hif_choice_kind(context, image):
    """Disambiguate the two-step course/class page when resuming mid-choice."""
    if not hif_choice_rows(image):
        return None
    detail = context.run_recognition("ProduceRecognitionPoint", image,
        pipeline_override={"ProduceRecognitionPoint": {"recognition": "OCR", "roi": [0, 0, 220, 180],
            "expected": ["^授業$", "^(公開)?レッスン$", "^上课$", "^课程$"]}})
    kinds = set()
    for row in getattr(detail, "filtered_results", None) or []:
        text = re.sub(r"\s+", "", row.text)
        if text in ("授業", "上课"):
            kinds.add("class")
        elif text in ("公開レッスン", "レッスン", "课程"):
            kinds.add("course")
    return kinds.pop() if len(kinds) == 1 else None


def read_hif_choices(context, image):
    """Only accept three validated rows on a HIF lesson/class screen."""
    rows = hif_choice_rows(image)
    if not rows:
        return None
    header = context.run_recognition(
        "ProduceRecognitionPoint", image,
        pipeline_override={"ProduceRecognitionPoint": {
            "recognition": "OCR", "roi": [0, 0, 200, 180],
            "expected": ["授業", "公開レッスン", "レッスン", "上课", "课程"],
        }},
    )
    if not (header and header.hit):
        return None
    names = {row[0] for row in rows}
    # Attribute choices have different icons. After choosing an attribute,
    # all three answers use that attribute's icon; sort by screen position.
    stage = "option" if len(names) == 1 else "attribute"
    return stage, rows


def read_hif_events(context, image, event_text):
    """Recognize an exact daily-event label with its adjacent attribute icon.

    Card descriptions also contain 'レッスン'. Substring OCR is not an event
    detector. Prefixed public-lesson tiles use a large central attribute icon;
    their lower-right bonus star must not be treated as the attribute badge.
    """
    if image.ndim != 3 or image.shape[2] != 3 or image.shape[1] != 720 or abs(image.shape[0] - 1280) > 2:
        return []
    expected = ["^授業$"] if event_text == "授業" else [HIF_COURSE_LABEL]
    detail = context.run_recognition(
        "ProduceRecognitionPoint", image,
        pipeline_override={"ProduceRecognitionPoint": {
            "recognition": "OCR", "roi": [0, 780, 720, 380], "expected": expected,
        }},
    )
    if not (detail and detail.hit):
        return []
    events = []
    for result in getattr(detail, "filtered_results", None) or []:
        match = re.fullmatch(expected[0], result.text.strip())
        if not match:
            continue
        x, y, width, height = map(int, result.box)
        center = x + width // 2
        if not (80 <= center <= 640 and 880 <= y <= 1110 and 14 <= height <= 65):
            continue
        prefix = match.group(1) if event_text != "授業" else None
        if prefix:
            # Public lessons say Vo./Da./Vi. explicitly. Verify the central
            # tile icon too, excluding the common bonus star and SP badge.
            pixels = image[y - 110:y - 60, center - 40:center + 40].astype(np.int16)
        else:
            # Keep the original plain-label/badge layout unchanged.
            pixels = image[y - 50:y - 20, center + 35:center + 65].astype(np.int16)
        counts = {name: int(mask.sum()) for name, mask in _attribute_masks(pixels).items()}
        ranked = sorted(counts, key=counts.get, reverse=True)
        best, second = counts[ranked[0]], counts[ranked[1]]
        if best < 80 or best < second * 2 or (prefix and prefix != ranked[0]):
            continue
        events.append((ranked[0], [center - 75, y - 130, 150, height + 140], best / sum(counts.values())))
    return sorted(events, key=lambda event: event[1][0])


def read_hif_shop_entry(context, image):
    """Recognize the green daily consultation tile, not the shop interior."""
    if image.ndim != 3 or image.shape[2] != 3 or image.shape[1] != 720 or abs(image.shape[0] - 1280) > 2:
        return None
    detail = context.run_recognition(
        "ProduceRecognitionPoint", image,
        pipeline_override={"ProduceRecognitionPoint": {
            "recognition": "OCR", "roi": [40, 920, 640, 220], "expected": ["^相談$"],
        }},
    )
    if not (detail and detail.hit):
        return None
    for result in getattr(detail, "filtered_results", None) or []:
        if result.text.strip() != "相談":
            continue
        x, y, width, height = map(int, result.box)
        center = x + width // 2
        if not (80 <= center <= 640 and 950 <= y <= 1100 and 25 <= width <= 150 and 14 <= height <= 65):
            continue
        # The large green icon above the label distinguishes the daily tile
        # from a dialog description or a button inside another screen.
        pixels = image[y - 125:y - 35, center - 55:center + 55].astype(np.int16)
        b, g, r = pixels[:, :, 0], pixels[:, :, 1], pixels[:, :, 2]
        if ((g >= 160) & (r <= 140) & (b <= 210) & (g - r >= 50)).mean() < .35:
            continue
        return [center - 75, y - 145, 150, height + 155]
    return None


HIF_CHANGE_SLOTS = ([159, 833, 124, 129], [297, 833, 124, 129], [435, 833, 127, 129])


def read_hif_change(context, image):
    """Recognize the destination-card step, not a general reward/deck screen."""
    if image.ndim != 3 or image.shape[2] != 3 or image.shape[1] != 720 or abs(image.shape[0] - 1280) > 2:
        return None
    # The three cyan card backgrounds are required alongside the exact prompt.
    # No fixed-slot fallback is permitted on an unrecognized page.
    for x, y, width, height in HIF_CHANGE_SLOTS:
        crop = image[y:y + height, x:x + width].astype(np.int16)
        b, g, r = crop[:, :, 0], crop[:, :, 1], crop[:, :, 2]
        if ((b >= 160) & (g >= 170) & (b - r >= 12)).mean() < .07:
            return None
    prompt = context.run_recognition(
        "ProduceRecognitionPoint", image,
        pipeline_override={"ProduceRecognitionPoint": {
            "recognition": "OCR", "roi": [70, 270, 590, 130],
            "expected": ["チェンジ.*獲得.*スキルカード.*選"],
        }},
    )
    return [list(box) for box in HIF_CHANGE_SLOTS] if prompt and prompt.hit else None


def is_hif_change_source(context, image):
    """Require the Change header and deck prompt before touching owned cards."""
    if image.ndim != 3 or image.shape[2] != 3 or image.shape[1] != 720 or abs(image.shape[0] - 1280) > 2:
        return False
    pixels = image[660:1100, 60:650].astype(np.int16)
    b, g, r = pixels[:, :, 0], pixels[:, :, 1], pixels[:, :, 2]
    if ((b >= 160) & (g >= 170) & (b - r >= 12)).mean() < .04:
        return False
    for roi, expected in (([0, 15, 300, 90], ["チェンジ"]),
                          ([20, 550, 680, 180], ["チェンジするスキルカード.*選択"])):
        detail = context.run_recognition(
            "ProduceRecognitionPoint", image,
            pipeline_override={"ProduceRecognitionPoint": {
                "recognition": "OCR", "roi": roi, "expected": expected,
            }},
        )
        if not (detail and detail.hit):
            return False
    return True

"""HIF's attribute and follow-up choices share rows, but not the same step."""

from .frame_geometry import reference_frame
import re

import numpy as np

from .reward_layout import reward_card_row


HIF_COURSE_LABEL = r"^(?:(Vo|Da|Vi)\s*[.．・]\s*)?(?:公開)?レッスン$"


def lesson_observation(stage, rows):
    return stage, tuple((row[0], tuple(map(int, row[1]))) for row in rows)


def lesson_observations_stable(previous, current, before=None, after=None):
    """Ignore border shimmer, but not moving buttons or changed attributes."""
    if previous is None or previous[0] != current[0] or len(previous[1]) != len(current[1]):
        return False
    geometry_stable = all(a[0] == b[0] and all(abs(x - y) <= tolerance
               for x, y, tolerance in zip(a[1], b[1], (3, 3, 4, 4)))
               for a, b in zip(previous[1], current[1]))
    if not geometry_stable or before is None or after is None:
        return geometry_stable
    if before.shape != after.shape:
        return False
    # Inspect the interiors, excluding shimmering borders and corner badges.
    # Geometry alone can stay fixed while a disabled/fading button animates.
    for _, (x, y, w, h) in current[1]:
        old = before[y + 12:y + h - 12:3, x + 28:x + w - 28:3].astype(np.float32)
        new = after[y + 12:y + h - 12:3, x + 28:x + w - 28:3].astype(np.float32)
        # The selected row's white chevrons animate over ~14% of its interior.
        # A fade/selection transition changes the body of the whole button.
        if not old.size or np.mean(np.abs(old - new).mean(axis=2) > 12) > .25:
            return False
    return True


def hif_course_sp_roi(image, rows, box):
    """Keep SP searches inside one row or one schedule column."""
    x, y, w, h = map(int, box)
    if w >= 400:  # Horizontal attribute-choice buttons, not daily tiles.
        return [x, y, w, h]
    centers = sorted({int(row[1][0] + row[1][2] // 2) for row in rows})
    center = x + w // 2
    left = max((other + center) // 2 for other in centers if other < center) if any(other < center for other in centers) else 0
    right = min((other + center) // 2 for other in centers if other > center) if any(other > center for other in centers) else image.shape[1]
    left, right = max(left, x - 35, 0), min(right, x + w + 35, image.shape[1])
    top, bottom = max(0, y - 170), min(image.shape[0], y + h)
    return [left, top, right - left, bottom - top]


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
    for name in ("ProduceHIFClassFlag", "ProduceHIFCourseFlag", "ProduceHIFChoicesFlag", "ProduceHIFOutingFlag",
                 "ProduceHIFChangeFlag", "ProduceHIFChangeSourceFlag", "ProduceHIFCustomizationFlag", "ProduceHIFIntervalShopFlag",
                 "ProduceHIFShopEntryFlag", "ProduceHIFShopFlag", "ProduceHIFSupplyFlag", "ProduceHIFSupplyReceiptFlag",
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
    Allow small capture-border differences; the three detected rows and
    attribute icons below, not exact image dimensions, establish the layout.
    """
    if not reference_frame(image):
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
    preview = any(saturated[start - 620:end - 620].mean() >= .45 for start, end in bands)
    rows = []
    for start, end in bands:
        # Only text and left-side icons, never stamina/trouble badges on the right.
        pixels = image[start:end, 75:460].astype(np.int16)
        counts = {name: int(mask.sum()) for name, mask in _attribute_masks(pixels).items()}
        ranked = sorted(counts, key=counts.get, reverse=True)
        best, second = counts[ranked[0]], counts[ranked[1]]
        if preview and best < 40:
            # The purple preview scrim also tints the unselected icons. In a
            # real Vi preview their blue channel exceeds the normal 140 cap.
            # Only use these color differences with a solid selected row and
            # a complete three-row layout; never inherit its attribute, since
            # the preceding attribute-choice step can have mixed icons.
            b, g, r = pixels[:, :, 0], pixels[:, :, 1], pixels[:, :, 2]
            tinted = {
                "Vo": (r >= 180) & (g <= 180) & (b - g >= 10) & (r - g >= 30) & (r - b >= 15),
                "Da": (b >= 190) & (r <= 180) & (g >= 110) & (b - r >= 30) & (g - r >= 15),
                "Vi": (r >= 200) & (g >= 150) & (b <= 200) & (r - b >= 30) & (g - b >= 10),
            }
            counts = {name: int(mask.sum()) for name, mask in tinted.items()}
            ranked = sorted(counts, key=counts.get, reverse=True)
            best, second = counts[ranked[0]], counts[ranked[1]]
        if best < 40 or best < second * 2:
            return []
        rows.append((ranked[0], [52, start, 616, end - start], best / sum(counts.values())))
    return rows


def read_hif_lesson_header(context, image):
    """Recognize the lesson page independently of button tint/row detection."""
    if not reference_frame(image):
        return None
    # HIF's lesson title sits in a white panel. Reject combat/reward frames
    # cheaply before template/OCR fallback; no extra OCR on each played card.
    panel = image[40:155:2, 32:192:2]
    if np.mean(panel.min(axis=2) >= 215) < .40:
        return None
    # The class header remains behind reward/change modals. Their white body
    # covers the central HUD; those pages belong to the reward handlers.
    modal = image[310:420:2, 180:540:2]
    if np.mean(modal.min(axis=2) >= 215) > .85:
        return None
    if _hif_class_header(context, image):
        return "class"
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


def read_hif_choice_kind(context, image):
    return read_hif_lesson_header(context, image)


def _read_hif_ocr_choices(context, image):
    """Independent fallback for long text splitting the shaded button fill.

    Caller must verify the lesson header. Locate three aligned text rows,
    then identify each attribute locally; never infer it from another row.
    Button wording is not an exact-match requirement.
    """
    detail = context.run_recognition('ProduceRecognitionPoint', image,
        pipeline_override={'ProduceRecognitionPoint': {'recognition': 'OCR',
            'roi': [40, 650, 550, 300], 'expected': []}})
    candidates = []
    for result in getattr(detail, 'all_results', None) or []:
        x, y, w, h = map(int, result.box)
        center = y + h // 2
        text = re.sub(r'\s+', '', str(result.text))
        if (float(result.score) >= .65 and 45 <= x <= 180 and w >= 100
                and 20 <= h <= 60 and 675 <= center <= 925
                and len(text) >= 3 and not re.fullmatch(r'[\d+\-/]+', text)):
            candidates.append((center, x, w, float(result.score)))
    groups = []
    for row in sorted(candidates):
        if groups and abs(row[0] - groups[-1][0]) <= 18:
            if row[2] > groups[-1][2]:
                groups[-1] = row
        else:
            groups.append(row)
    if len(groups) != 3 or any(not 80 <= b[0]-a[0] <= 125 for a,b in zip(groups, groups[1:])):
        return []
    rows = []
    for center, _, _, confidence in groups:
        # Include the left icon and selected-row background, excluding badges.
        pixels = image[center-25:center+25, 75:460].astype(np.int16)
        b,g,r = pixels[:,:,0],pixels[:,:,1],pixels[:,:,2]
        masks = _attribute_masks(pixels)
        tinted = {
            'Vo': (r >= 180)&(g <= 180)&(b-g >= 10)&(r-g >= 30)&(r-b >= 15),
            'Da': (b >= 190)&(r <= 180)&(g >= 110)&(b-r >= 30)&(g-r >= 15),
            'Vi': (r >= 200)&(g >= 150)&(b <= 200)&(r-b >= 30)&(g-b >= 10),
        }
        counts = {name: int((masks[name] | tinted[name]).sum()) for name in masks}
        ranked = sorted(counts, key=counts.get, reverse=True)
        if counts[ranked[0]] < 40 or counts[ranked[0]] < 2*counts[ranked[1]]:
            return []
        rows.append((ranked[0], [52, center-37, 616, 74], confidence))
    return rows


def read_hif_choices(context, image):
    """Only accept three validated rows on a HIF lesson/class screen."""
    rows = hif_choice_rows(image)
    if not rows:
        if not read_hif_lesson_header(context, image):
            return None
        rows = _read_hif_ocr_choices(context, image)
        if not rows:
            return None
    template_header = _hif_class_header(context, image)
    header = None if template_header else context.run_recognition(
        "ProduceRecognitionPoint", image,
        pipeline_override={"ProduceRecognitionPoint": {
            "recognition": "OCR", "roi": [0, 0, 200, 180],
            "expected": ["授業", "公開レッスン", "レッスン", "上课", "课程"],
        }},
    )
    if not template_header and not (header and header.hit):
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
    if not reference_frame(image):
        return []
    fast = _read_hif_template_events(context, image, event_text)
    if fast:
        return fast
    expected = ["^授業$"] if event_text == "授業" else [HIF_COURSE_LABEL]
    detail = context.run_recognition(
        "ProduceRecognitionPoint", image,
        pipeline_override={"ProduceRecognitionPoint": {
            "recognition": "OCR", "roi": [0, 780, image.shape[1], 380], "expected": expected,
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


def _binary_image(mask):
    return np.repeat((mask.astype(np.uint8) * 255)[:, :, None], 3, axis=2)


def _hif_class_header(context, image):
    from .event_templates import HIF_TEMPLATES, match_event_template, template_results
    # The same white game sprite is tinted purple in the HIF class header.
    pixels = image[:180, :180].astype(np.int16)
    b, g, r = pixels[:, :, 0], pixels[:, :, 1], pixels[:, :, 2]
    mask = (b > 150) & (r > 70) & (g < 190) & (b - g > 35)
    if mask.sum() < 80:
        return False
    normalized = np.zeros_like(image)
    normalized[:180, :180] = _binary_image(mask)
    detail = match_event_template(context, normalized, HIF_TEMPLATES['class_header'],
                                  [50, 65, 115, 90], threshold=.75)
    return bool(template_results(detail, .75))


def _read_hif_template_events(context, image, event_text):
    """Fast native template path, accepted only as a complete aligned trio.

    Templates are game-sprite alpha silhouettes. Normalize UI tint first so
    gradients/text do not become part of the icon template. Unfamiliar layouts
    fall back to exact-label OCR plus the original independent icon check.
    """
    from .event_templates import HIF_TEMPLATES, match_event_template, template_results
    roi = [65, 910, 590, 160]
    pixels = image[910:1070, 65:655].astype(np.int16)
    colors = _attribute_masks(pixels)
    normalized = np.zeros_like(image)
    events = []
    if event_text == '授業':
        mask = (pixels.min(2) > 205) & (pixels.max(2) - pixels.min(2) < 35)
        normalized[910:1070, 65:655] = _binary_image(mask)
        detail = match_event_template(context, normalized, HIF_TEMPLATES['class'], roi, threshold=.76)
        matches = template_results(detail, .76)
        if len(matches) != 3:
            return []
        for match in matches:
            x, y, w, h = map(int, match.box)
            center = x + w // 2 + 24
            badge = image[y + 40:y + 70, center + 35:center + 65].astype(np.int16)
            counts = {name: int(value.sum()) for name, value in _attribute_masks(badge).items()}
            ranked = sorted(counts, key=counts.get, reverse=True)
            if counts[ranked[0]] < 80 or counts[ranked[0]] < counts[ranked[1]] * 2:
                return []
            events.append((ranked[0], [center - 75, y - 40, 150, 174], float(match.score)))
    else:
        for attr in ('Vo', 'Da', 'Vi'):
            if colors[attr].sum() < 150:
                return []
            normalized[910:1070, 65:655] = _binary_image(colors[attr])
            detail = match_event_template(context, normalized, HIF_TEMPLATES[attr], roi, threshold=.83)
            matches = template_results(detail, .83)
            if len(matches) != 1:
                return []
            match = matches[0]
            x, y, w, h = map(int, match.box)
            center = x + w // 2
            # A public-course icon sits in a white tile, above its colored label.
            background = image[y - 3:y + h + 3, center - 65:center + 65].astype(np.int16)
            if ((background.min(2) >= 205) & (background.max(2) - background.min(2) < 35)).mean() < .50:
                return []
            events.append((attr, [center - 75, y - 12, 150, 164], float(match.score)))
    events.sort(key=lambda row: row[1][0])
    centers = [row[1][0] + 75 for row in events]
    tops = [row[1][1] for row in events]
    if {row[0] for row in events} != {'Vo', 'Da', 'Vi'} or any(not 100 <= x <= 620 for x in centers):
        return []
    if any(not 140 <= b - a <= 215 for a, b in zip(centers, centers[1:])) or max(tops) - min(tops) > 12:
        return []
    return events


def read_hif_shop_entry(context, image):
    """Recognize the green daily consultation tile, not the shop interior."""
    if not reference_frame(image):
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
    if not reference_frame(image):
        return None
    # Four event cards overlap all three old fixed ROIs. Cyan pixels alone
    # therefore cannot validate their positions. Require a complete detected
    # three/four-card row, shared with reward recognition (including badge trim).
    detected = context.run_recognition("ProduceRecognitionCards", image)
    row = reward_card_row([
        {"box": list(item.box), "detector_score": float(item.score)}
        for item in (detected.all_results or [])
        if item.label in ("cards", "suggestions")
    ]) if detected and detected.hit else []
    if not row:
        return None
    prompt = context.run_recognition(
        "ProduceRecognitionPoint", image,
        pipeline_override={"ProduceRecognitionPoint": {
            "recognition": "OCR", "roi": [70, 270, 590, 130],
            "expected": ["チェンジ.*獲得.*スキルカード.*選"],
        }},
    )
    return [item["box"] for item in row] if prompt and prompt.hit else None


def is_hif_change_source(context, image):
    """Require the Change header and deck prompt before touching owned cards."""
    if not reference_frame(image):
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


def read_hif_supply_receipt(context, image):
    """Recognize the gift receipt page by its title AND receive control.

    The schedule tile uses the same title at the bottom of the screen;
    it must continue through the configured activity chooser instead.
    """
    if not reference_frame(image):
        return None
    headers = _ocr(context, image, [0, 0, 240, 110], [r"^差し入れ$"])
    if not any(re.sub(r"\s+", "", str(row.text)) == "差し入れ" for row in headers):
        return None
    buttons = _ocr(context, image, [200, 1010, 350, 170], [r"^受け取る$"])
    boxes = []
    for row in buttons:
        x, y, w, h = map(int, row.box)
        if (re.sub(r"\s+", "", str(row.text)) == "受け取る"
                and 200 <= x < x + w <= 550 and 1010 <= y < y + h <= 1180):
            boxes.append([x, y, w, h])
    return boxes[0] if len(boxes) == 1 else None


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

"""Read the live owned-card drawer, then verify return to the original scene.

The bag glyph and four-column drawer are evidenced by 2026-10-16 DMM captures.
No state is carried between acquisitions, tasks, or cultivation runs.
"""

import re
import time
from collections import Counter
from dataclasses import dataclass

import numpy as np
from utils import logger

from .shop import ocr
from .card_visual import normalize_card_name, intersection_over_union
from .frame_geometry import reference_frame
from .square_identity import match


@dataclass
class Inspection:
    counts: dict | None
    restored: bool


def bag_box(context, image):
    if not reference_frame(image):
        return None
    detail = context.run_recognition(
        "ProduceRecognitionPoint",
        image,
        pipeline_override={
            "ProduceRecognitionPoint": dict(
                recognition="TemplateMatch", roi=[400, 1130, 305, 140], template=["produce/owned_bag.png"], threshold=0.85
            )
        },
    )
    rows = list(detail.filtered_results or []) if detail and detail.hit else []
    return list(rows[0].box) if len(rows) == 1 else None


def drawer_present(context, image):
    if not reference_frame(image):
        return None
    titles = ocr(context, image, [20, 20, 380, 80], [r"所持スキルカード", r"持有.*技能卡"])
    if not any(float(r.score) >= 0.80 for r in titles):
        return None
    return True


def drawer_info(context, image):
    if not drawer_present(context, image):
        return None
    rows = ocr(context, image, [45, 1000, 390, 95], [r"スキルカード", r"技能卡"])
    totals = []
    for row in rows:
        result = re.search(r"[（(]\s*(\d{1,3})\s*[)）]", row.text)
        if float(row.score) >= 0.80 and result and 1 <= int(result[1]) <= 120:
            totals.append(int(result[1]))
    return totals[0] if len(totals) == 1 else None


def close_box(context, image):
    rows = ocr(context, image, [220, 1110, 300, 110], [r"閉じる", r"关闭", r"關閉"])
    rows = [r for r in rows if float(r.score) >= 0.80]
    return list(rows[0].box) if len(rows) == 1 else None


def page_rows(context, image, *, read_names=True):
    """Use model anchors and four measured edges; geometry alone is never a tile."""
    detail = context.run_recognition("ProduceRecognitionCards", image)
    boxes = []
    for row in (detail.all_results or []) if detail and detail.hit else []:
        box = list(map(int, row.box))
        x, y, w, h = box
        if (
            25 <= x
            and x + w <= 695
            and 400 <= y
            and y + h <= 1010
            and 110 <= w <= 165
            and 110 <= h <= 165
            and 0.85 <= w / h <= 1.18
            and not any(intersection_over_union(box, b) > 0.45 for b in boxes)
        ):
            boxes.append(box)
    groups = []
    for box in sorted(boxes, key=lambda b: (b[1], b[0])):
        group = next((g for g in groups if abs(np.median([b[1] for b in g]) - box[1]) <= 24), None)
        if group is None:
            groups.append([box])
        else:
            group.append(box)
    # The model misses tiles and can include the grid toggle in a box. Each
    # proposed slot must independently have four straight frame edges.
    pixels = image[:, :, :3].astype(np.float32)
    verified = []
    anchors = [b for group in groups if len(group) >= 2 for b in group]
    if not anchors:
        return []
    size = round(float(np.median([b[2] for b in anchors])))
    origin = round(float(np.median([b[0] - round((b[0] - 48) / 160) * 160 for b in anchors])))
    ys = sorted(round(float(np.median([b[1] for b in group]))) for group in groups)
    gaps = [(b - a) / round((b - a) / 153) for a, b in zip(ys, ys[1:]) if round((b - a) / 153) >= 1]
    if gaps and all(abs(g - 153) <= 8 for g in gaps):
        pitch = float(np.median(gaps))
        y = ys[0] - pitch
        while y <= ys[-1] + pitch + 1:
            if 400 <= y and y + size <= 1010 and not any(abs(y - old) <= 12 for old in ys):
                groups.append([[origin, round(y), size, size]])
            y += pitch
    # A final row may contain just one tile. Its grid is anchored by other
    # rows, but all four real edges are still required independently.
    for group in sorted(groups, key=lambda g: np.median([b[1] for b in g])):
        y = round(float(np.median([b[1] for b in group])))
        cells = []
        for column in range(4):
            x = origin + 160 * column
            edges = []
            for axis, center in ((1, x), (1, x + size), (0, y), (0, y + size)):
                if axis == 1:
                    strip = pixels[y + 22 : y + size // 2 + 8, center - 9 : center + 10]
                    values = (np.abs(np.diff(strip, axis=1)).max(axis=2) > 22).mean(axis=0)
                else:
                    strip = pixels[center - 9 : center + 10, x + 22 : x + size - 22]
                    values = (np.abs(np.diff(strip, axis=0)).max(axis=2) > 22).mean(axis=1)
                hits = np.flatnonzero(values >= 0.65)
                if not len(hits):
                    break
                # Prefer the nearest strong edge, not interior artwork.
                index = min(hits, key=lambda v: abs(v - 8))
                edges.append(center - 9 + int(index) + 1)
            if len(edges) == 4:
                left, right, top, bottom = edges
                if 400 <= top and bottom <= 1010 and abs((right - left) - (bottom - top)) <= 8:
                    cells.append([left, top, right - left, bottom - top])
        if cells:
            verified.append(cells)
    groups = verified
    rows = []
    for group in groups:
        group.sort(key=lambda b: b[0])
        # A missing column must not become a short row in the middle of a page.
        if any(abs(b[0] - (48 + 160 * i)) > 14 for i, b in enumerate(group)):
            return []
        cells = []
        for box in group:
            x, y, w, h = box
            art = match(image, box) if read_names else None
            # Detector/edge jitter must not rescale the artwork on each frame.
            cx, cy = round(x+w/2), round(y+h/2)
            sample = image[cy-32:cy+32, cx-32:cx+32, :3].copy()
            cells.append(dict(box=box, name_key=normalize_card_name(art["name"]) if art else None, pixels=sample))
        rows.append(cells)
    if any(len(row) != 4 for row in rows[:-1]):
        return []
    return rows


def same_row(a, b):
    if len(a) != len(b):
        return False
    for x, y in zip(a, b):
        if x.get("name_key") and y.get("name_key") and x["name_key"] != y["name_key"]:
            return False
        left, right = x["pixels"], y["pixels"]
        if left.shape != right.shape:
            return False
        if left.shape == (64, 64, 3):
            sample = left[5:-5, 5:-5].astype(np.int16)
            matched = False
            for dy in (0, -1, 1, -2, 2, -3, 3, -4, 4):
                for dx in (0, -1, 1, -2, 2, -3, 3, -4, 4):
                    diff = np.abs(sample-right[5+dy:59+dy, 5+dx:59+dx].astype(np.int16))
                    if diff.mean() <= 9 and np.quantile(diff, .95) <= 40:
                        matched = True
                        break
                if matched:
                    break
            if not matched:
                return False
        elif np.abs(left.astype(float)-right.astype(float)).mean() >= .035:
            return False
    return True


def same_page(a, b):
    return bool(
        a and b and len(a) == len(b) and all(same_row(x, y) and
            all(max(abs(i-j) for i,j in zip(c["box"], d["box"])) <= 6 for c,d in zip(x,y))
            for x, y in zip(a, b))
    )


class RowCounts:
    """Merge only unambiguous overlapping rows; repeated swipes never add copies."""

    def __init__(self, total, wanted, *, row_match=same_row):
        self.total, self.wanted = total, set(wanted)
        self.row_match = row_match
        self.rows = []
        self.valid = True
        self.lower = Counter()

    def add(self, rows):
        page = Counter(c["name_key"] for r in rows for c in r if c["name_key"])
        self.lower |= page
        if not rows or not self.valid:
            return
        if not self.rows:
            self.rows = [[dict(c) for c in row] for row in rows]
        else:
            overlaps = [
                n for n in range(1, min(len(self.rows), len(rows)) + 1) if all(self.row_match(a, b) for a, b in zip(self.rows[-n:], rows[:n]))
            ]
            if len(overlaps) != 1:
                self.valid = False
                return
            overlap = overlaps[0]
            for old, new in zip(self.rows[-overlap:], rows[:overlap]):
                for a, b in zip(old, new):
                    if not a.get("name_key") and b.get("name_key"):
                        a["name_key"] = b["name_key"]
            self.rows += [[dict(c) for c in row] for row in rows[overlap:]]
        if self.total is not None and sum(map(len, self.rows)) > self.total:
            self.valid = False
            return
        self.lower |= Counter(c["name_key"] for r in self.rows for c in r if c["name_key"])

    def result(self):
        complete = (
            self.total is not None
            and self.valid
            and sum(map(len, self.rows)) == self.total
            and all(c["name_key"] for r in self.rows for c in r)
        )
        return {key: self.lower[key] for key in self.wanted if complete or self.lower[key] > 0}


class OwnedDeckReader:
    def __init__(self, context):
        self.context = context
        self.deadline = time.monotonic() + 75

    def capture(self):
        if self.context.tasker.stopping or time.monotonic() > self.deadline:
            raise InterruptedError
        return self.context.tasker.controller.post_screencap().wait().get()

    def click(self, box):
        if self.context.tasker.stopping:
            raise InterruptedError
        x, y, w, h = box
        self.context.tasker.controller.post_click(x + w // 2, y + h // 2).wait()
        time.sleep(0.3)

    def scroll(self, forward):
        from .scroll import scroll_list
        if self.context.tasker.stopping:
            raise InterruptedError
        # This drawer has 153px rows, unlike the 146px removal grid. Retain
        # a complete last row even when the first visible row is clipped.
        rows = getattr(self, "_scroll_rows", [])
        distance = min(306, max(60, min(c["box"][1] for c in rows[-1])-418)) if rows else 306
        # Keep the gesture inside card content, away from the right edge
        # and the lower-right grid-mode button.
        start, end = (930, 930-distance) if forward else (450, 450+distance)
        if not scroll_list(self.context, (600, start), (600, end)):
            raise InterruptedError("持有牌组滑动未完成")
        time.sleep(0.4)

    def stable(self):
        previous = None
        for _ in range(5):
            image = self.capture()
            total = drawer_info(self.context, image)
            if total is None:
                return None
            rows = page_rows(self.context, image, read_names=False)
            if same_page(previous, rows):
                self._scroll_rows = rows
                return image, total, rows
            previous = rows
            time.sleep(0.2)
        return None

    def resolve_unknowns(self, image, rows, wanted=None):
        from .removal import read_selected_card, selected_card_box_matches

        for row in rows:
            for cell in row:
                if not cell.get("name_key"):
                    art = match(image, cell["box"])
                    if art:
                        cell["name_key"] = normalize_card_name(art["name"])
        if wanted and set(wanted) <= {c["name_key"] for row in rows for c in row}:
            return rows
        for row in rows:
            for cell in row:
                if cell["name_key"]:
                    continue
                self.click(cell["box"])
                previous = None
                for _ in range(3):
                    image = self.capture()
                    if drawer_info(self.context, image) is None:
                        raise InterruptedError
                    current = (
                        read_selected_card(self.context, image, roi=[30, 110, 660, 275])
                        if selected_card_box_matches(image, cell["box"])
                        else None
                    )
                    if current and previous == current:
                        cell["name_key"] = current["name_key"]
                        break
                    previous = current
                    time.sleep(0.2)
        return rows

    def count_cards(self, current, wanted):
        # Presence is enough for a single-copy decision. Do not rewind
        # an already visible owned target just to count the entire deck.
        image, total, rows = current
        rows = self.resolve_unknowns(image, rows, wanted)
        visible = RowCounts(total, wanted)
        visible.add(rows)
        counts = visible.result()
        if len(counts) == len(set(wanted)):
            return counts
        # The drawer can remember a previous scroll position. Two stable
        # backward drags establish the top without assuming a fresh open.
        stationary = 0
        seen = []
        # Smaller overlap-preserving steps still cover the accepted
        # maximum of 120 cards (30 rows), within the same time budget.
        for _ in range(16):
            self.scroll(False)
            following = self.stable()
            if following is None:
                raise InterruptedError
            stationary = stationary + 1 if same_page(current[2], following[2]) else 0
            if stationary == 0 and sum(same_page(old, following[2]) for old in seen) >= 2:
                raise InterruptedError("持有牌库反复返回同一位置，停止重复回顶")
            seen.append(current[2])
            current = following
            if stationary >= 2:
                break
        else:
            raise InterruptedError
        tracker = RowCounts(current[1], wanted)
        stationary = 0
        for _ in range(16):
            image, total, rows = current
            if total != tracker.total:
                raise InterruptedError
            if stationary == 0:
                rows = self.resolve_unknowns(image, rows, wanted)
                tracker.add(rows)
            counts = tracker.result()
            if len(counts) == len(set(wanted)):
                # Positive observations already prove ownership. Zero is
                # emitted only after all total cards are accounted for.
                break
            if not tracker.valid:
                raise InterruptedError("持有牌库重叠行未确认，不将未知数量当作零")
            self.scroll(True)
            following = self.stable()
            if following is None:
                break
            stationary = stationary + 1 if same_page(current[2], following[2]) else 0
            if stationary >= 2:
                break
            if stationary == 0:
                current = following
            # Repeated bottom frames are not additional cards.
            if stationary:
                continue
        return counts

    def inspect(self, wanted, source_same):
        counts = None
        opened = False
        restored = False
        try:
            image = self.capture()
            if not source_same(image):
                return Inspection(None, False)
            box = bag_box(self.context, image)
            if box is None:
                return Inspection(None, True)
            self.click(box)
            opened = True
            logger.info("检查限一张卡牌的持有情况")
            current = None
            for _ in range(5):
                current = self.stable()
                if current:
                    break
                time.sleep(0.25)
            if current is None:
                raise InterruptedError
            counts = self.count_cards(current, wanted)
        except InterruptedError as error:
            logger.debug(f"持有牌库检查未完成：{error}")
        finally:
            if opened and not self.context.tasker.stopping:
                # Close only the evidenced drawer, then compare the source
                # tiles. Never hit an underlying Receive/Next button as a fallback.
                self.deadline = time.monotonic() + 8
                try:
                    stable = 0
                    for attempt in range(8):
                        image = self.capture()
                        if drawer_present(self.context, image):
                            box = close_box(self.context, image)
                            if box and attempt in (0, 3, 6):
                                self.click(box)
                            stable = 0
                        elif source_same(image):
                            stable += 1
                            if stable >= 2:
                                restored = True
                                break
                        time.sleep(0.25)
                except InterruptedError:
                    pass
        return Inspection(counts, restored)

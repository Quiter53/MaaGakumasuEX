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
from .card_visual import normalize_card_name, _resize_rgb_bilinear, intersection_over_union
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
            sample = _resize_rgb_bilinear(image[y + 20 : y + h - 20, x + 25 : x + w - 20], 24)
            cells.append(dict(box=box, name_key=normalize_card_name(art["name"]) if art else None, pixels=sample))
        rows.append(cells)
    if any(len(row) != 4 for row in rows[:-1]):
        return []
    return rows


def same_row(a, b):
    return len(a) == len(b) and all(
        (not x.get("name_key") or not y.get("name_key") or x["name_key"] == y["name_key"])
        and x["pixels"].shape == y["pixels"].shape
        and np.abs(x["pixels"] - y["pixels"]).mean() < 0.035
        for x, y in zip(a, b)
    )


def same_page(a, b):
    return bool(
        a and len(a) == len(b) and all(same_row(x, y) and max(abs(i - j) for i, j in zip(x[0]["box"], y[0]["box"])) <= 4 for x, y in zip(a, b))
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
        if self.context.tasker.stopping:
            raise InterruptedError
        start, end = (950, 500) if forward else (500, 950)
        self.context.tasker.controller.post_swipe(690, start, 690, end, 450).wait()
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
                return image, total, rows
            previous = rows
            time.sleep(0.2)
        return None

    def resolve_unknowns(self, image, rows):
        from .removal import read_selected_card, selected_card_box_matches

        for row in rows:
            for cell in row:
                art = match(image, cell["box"])
                if art:
                    cell["name_key"] = normalize_card_name(art["name"])
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
            # The drawer can remember a previous scroll position. Two stable
            # backward drags establish the top without assuming a fresh open.
            stationary = 0
            for _ in range(8):
                self.scroll(False)
                following = self.stable()
                if following is None:
                    raise InterruptedError
                stationary = stationary + 1 if same_page(current[2], following[2]) else 0
                current = following
                if stationary >= 2:
                    break
            else:
                raise InterruptedError
            tracker = RowCounts(current[1], wanted)
            stationary = 0
            for _ in range(9):
                image, total, rows = current
                if total != tracker.total:
                    raise InterruptedError
                if stationary == 0:
                    rows = self.resolve_unknowns(image, rows)
                    tracker.add(rows)
                counts = tracker.result()
                if len(counts) == len(set(wanted)):
                    # Positive observations already prove ownership. Zero is
                    # emitted only after all total cards are accounted for.
                    break
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
        except InterruptedError:
            pass
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

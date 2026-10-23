"""Scrollable overflow picker: identify every bottle, keep exactly four.

Rows are anchored by their checkbox, never by row height or unique drink ID.
Scrollbar position converts visible row coordinates into stable list positions.
No confirmation is permitted until the complete list and selection are verified.
"""

import re
import time
from dataclasses import dataclass

import numpy as np
from utils import logger

from .drink_policy import TARGET_NAMES
from .drink_visual import match
from .frame_geometry import reference_frame

TOP, BOTTOM = 272, 1068
HEIGHT = BOTTOM - TOP


class RetryOverflow(Exception):
    pass


def _runs(mask, gap=0):
    ends = np.flatnonzero(np.diff(np.r_[False, mask, False]))
    result = []
    for a, b in zip(ends[::2], ends[1::2]):
        if result and a - result[-1][1] <= gap:
            result[-1] = (result[-1][0], int(b))
        else:
            result.append((int(a), int(b)))
    return result


@dataclass
class Bottle:
    id: str
    name: str
    position: float
    y: int
    selected: bool
    source: str = ""


@dataclass
class View:
    rows: list
    offset: float
    maximum: float

    @property
    def at_top(self):
        return self.offset <= 4

    @property
    def at_bottom(self):
        return self.maximum - self.offset <= 4


def read_view(image):
    """Read the fixed list viewport, variable-height row anchors and bottle art."""
    if not reference_frame(image):
        raise RetryOverflow("饮料保留页尺寸尚未稳定")
    b, g, r = image[TOP:BOTTOM, 676:686, :3].transpose(2, 0, 1).astype(np.int16)
    thumb = (r > 80) & (r < 175) & (abs(r - g) < 15) & (abs(r - b) < 15)
    bars = [(a, b) for a, b in _runs(thumb.sum(axis=1) >= 2, gap=2) if b - a >= 80]
    if len(bars) != 1:
        # Five short rows may fit without scrolling. Treat a missing scrollbar
        # as an unscrollable view; collect() still requires both section titles
        # and all four owned bottles before any click.
        if bars:
            raise RetryOverflow("滚动条位置不唯一")
        offset = maximum = 0.0
    else:
        start, end = bars[0]
        length = end - start
        offset = start * HEIGHT / length
        maximum = max(0.0, (HEIGHT - length) * HEIGHT / length)
    b, g, r = image[TOP:BOTTOM, 610:647, :3].transpose(2, 0, 1).astype(np.int16)
    orange = (r > 235) & (g > 80) & (g < 185) & (b < 85)
    # The emulator's unchecked square is RGB(198,203,206), not neutral
    # gray: the old strict <8 channel delta excluded its actual fill.
    grey = (r > 170) & (r < 225) & (abs(r - g) <= 15) & (abs(r - b) <= 15) & (abs(g - b) <= 15)
    rows = []
    # A white tick cuts through the colored square. Requiring 15 colored
    # pixels on EVERY scanline splits valid emulator checkboxes into pieces.
    # Follow the sides of the square, then verify its full 2-D footprint.
    colored = orange | grey
    for start, end in _runs(colored.sum(axis=1) >= 6, gap=3):
        if not 22 <= end - start <= 34:
            continue
        columns = np.flatnonzero(colored[start:end].sum(axis=0) >= 4)
        if not len(columns):
            continue
        width = int(columns[-1] - columns[0] + 1)
        if not 20 <= width <= 34 or colored[start:end].sum() < 0.40 * width * (end - start):
            continue
        y = TOP + (start + end) // 2
        if y - 43 < TOP or y + 43 >= BOTTOM:
            continue  # clipped icon: next scroll view
        item = match(image, [60, y - 43, 86, 86], refine_ambiguous=True)
        if item is None:
            raise RetryOverflow(f"饮料图标未核实（列表纵坐标 {y}）")
        rows.append(Bottle(item["id"], item["name"], y + offset, y, bool(orange[start:end].sum() > 100)))
    if not rows:
        raise RetryOverflow("尚未看到完整饮料行")
    return View(rows, offset, maximum)


def same_view(a, b):
    return (
        abs(a.offset - b.offset) < 3
        and len(a.rows) == len(b.rows)
        and all(x.id == y.id and abs(x.position - y.position) < 3 and x.selected == y.selected for x, y in zip(a.rows, b.rows))
    )


def choose(bottles, targets):
    """New target > held target > new non-target > held non-target; duplicates count."""

    def rank(row):
        return (0 if row.id in targets else 2) + (0 if row.source == "new" else 1)

    return sorted(bottles, key=lambda row: (rank(row), row.position))[:4]


class OverflowPicker:
    def __init__(self, context):
        self.context = context
        self.controller = context.tasker.controller

    def capture(self):
        if self.context.tasker.stopping:
            raise RetryOverflow("任务正在停止")
        image = self.controller.post_screencap().wait().get()
        if not self.context.run_recognition("ProduceKeepDrinkFlag", image).hit:
            raise RetryOverflow("已离开饮料保留页")
        return image

    def stable(self):
        before = None
        reason = "饮料列表仍在滚动"
        for _ in range(5):
            image = self.capture()
            try:
                current = read_view(image)
            except RetryOverflow as error:
                reason = str(error)
                before = None
                time.sleep(0.12)
                continue
            if before is not None and same_view(before, current):
                return image, current
            before = current
            time.sleep(0.12)
        raise RetryOverflow(reason + "，连续截图未稳定")

    def scroll(self, down):
        from .scroll import scroll_list

        self.capture()  # never swipe a page that has disappeared
        if not scroll_list(self.context, (650, 1000 if down else 500), (650, 520 if down else 1000)):
            raise RetryOverflow("饮料列表滑动未完成")
        time.sleep(0.15)
        return self.stable()

    def top(self):
        image, view = self.stable()
        for _ in range(10):
            if view.at_top:
                return image, view
            previous = view.offset
            image, view = self.scroll(False)
            if view.offset >= previous - 3:
                raise RetryOverflow("列表未滚回顶部")
        raise RetryOverflow("列表超出滚动预算")

    def headings(self, image, offset):
        detail = self.context.run_recognition(
            "ProduceOverflowHeadings",
            image,
            pipeline_override={
                "ProduceOverflowHeadings": {
                    "recognition": "OCR",
                    "roi": [48, TOP, 440, HEIGHT],
                    "expected": [
                        "受け取った.*ドリンク",
                        "手持ちの.*ドリンク",
                        "(?:新|已)?[获獲]得.*[饮飲]料",
                        "(?:持有|持有中|已持有).*?[饮飲]料",
                    ],
                    "threshold": 0.85,
                }
            },
        )
        headings = {}
        for result in detail.filtered_results if detail and detail.hit else []:
            text = re.sub(r"\s", "", result.text)
            kind = "owned" if ("手持ち" in text or "持有" in text) else "new"
            if kind in headings:
                raise RetryOverflow("分组标题不唯一")
            headings[kind] = float(result.box[1] + result.box[3] + offset)
        return headings

    @staticmethod
    def locate(rows, row):
        hits = [(i, other) for i, other in enumerate(rows) if abs(other.position - row.position) < 10]
        if len(hits) != 1 or hits[0][1].id != row.id:
            raise RetryOverflow("滚动后的饮料位置或身份不一致")
        return hits[0]

    def collect(self, require_initial=False):
        image, view = self.top()
        rows = []
        headings = {}
        last = float("-inf")
        for _ in range(12):
            if view.offset <= last + 3:
                raise RetryOverflow("列表滚动没有推进")
            last = view.offset
            for kind, position in self.headings(image, view.offset).items():
                if kind in headings and abs(headings[kind] - position) > 10:
                    raise RetryOverflow("分组标题滚动位置不一致")
                headings[kind] = position
            for row in view.rows:
                near = [r for r in rows if abs(r.position - row.position) < 10]
                if near:
                    if len(near) != 1 or near[0].id != row.id or near[0].selected != row.selected:
                        raise RetryOverflow("跨页饮料身份或勾选状态发生变化")
                else:
                    rows.append(row)
            if view.at_bottom:
                break
            image, view = self.scroll(True)
        else:
            raise RetryOverflow("未核实列表底部")
        if set(headings) != {"new", "owned"} or not headings["new"] < headings["owned"]:
            raise RetryOverflow("未核实新获得/已持有分组")
        rows.sort(key=lambda r: r.position)
        for row in rows:
            if row.position < headings["new"]:
                raise RetryOverflow("饮料行在分组标题之外")
            row.source = "owned" if row.position > headings["owned"] else "new"
        if not 5 <= len(rows) <= 12 or not 1 <= sum(r.source == "owned" for r in rows) <= 4:
            raise RetryOverflow(
                f"完整候选数量未核实：共{len(rows)}瓶，"
                f"新获得{sum(r.source == 'new' for r in rows)}瓶，"
                f"已持有{sum(r.source == 'owned' for r in rows)}瓶"
            )
        if view.maximum == 0 and sum(r.source == "owned" for r in rows) != 4:
            raise RetryOverflow("无滚动条时未看到全部4瓶已持有饮料")
        if require_initial and sum(r.selected for r in rows) != 4:
            # A retry after a partially applied selection is allowed: all rows
            # were independently identified; selection state is not identity.
            logger.info("饮料保留：当前未勾满4瓶，按完整候选重新规划")
        return rows, image, view

    def toggle(self, row, wanted):
        for _ in range(3):
            image, view = self.stable()
            _, live = self.locate(view.rows, row)
            if live.selected == wanted:
                return
            if self.context.tasker.stopping:
                raise RetryOverflow("任务正在停止")
            self.controller.post_click(630, live.y).wait()
            time.sleep(0.15)
        _, view = self.stable()
        if self.locate(view.rows, row)[1].selected != wanted:
            raise RetryOverflow("勾选未生效，保留当前页面重试")

    def apply_pass(self, all_rows, wanted, select):
        image, view = self.top()
        last = float("-inf")
        seen = set()
        for _ in range(12):
            if view.offset <= last + 3:
                raise RetryOverflow("调整勾选时滚动没有推进")
            last = view.offset
            for row in view.rows:
                i, _ = self.locate(all_rows, row)
                seen.add(i)
                desired = i in wanted
                if desired == select and row.selected != desired:
                    self.toggle(row, desired)
            if view.at_bottom:
                break
            image, view = self.scroll(True)
        else:
            raise RetryOverflow("调整勾选未到列表底部")
        if len(seen) != len(all_rows):
            raise RetryOverflow("调整勾选时有候选未核实")

    def zero_remaining(self, image):
        detail = self.context.run_recognition(
            "ProduceOverflowRemaining",
            image,
            pipeline_override={
                "ProduceOverflowRemaining": {
                    "recognition": "OCR",
                    "roi": [250, 1185, 245, 50],
                    "expected": ["あと\\s*0\\s*個選択", "(?:还|還|剩).*?0.*?(?:选|選)"],
                    "threshold": 0.85,
                }
            },
        )
        return bool(detail and detail.hit)

    def run(self):
        logger.info("饮料保留：进入上限选择页，开始核实候选")
        rows, _, _ = self.collect(require_initial=True)
        chosen = choose(rows, set(TARGET_NAMES))
        wanted = {i for i, r in enumerate(rows) if any(r is c for c in chosen)}
        logger.info(
            "饮料保留：完整识别 "
            + str(len(rows))
            + " 瓶；计划保留 "
            + "/".join(("新获得" if r.source == "new" else "已持有") + ":" + r.name for r in chosen)
        )
        # Free slots before selecting. Never assume a failed click succeeded.
        if any(r.selected and i not in wanted for i, r in enumerate(rows)):
            self.apply_pass(rows, wanted, False)
        if any(not r.selected and i in wanted for i, r in enumerate(rows)):
            self.apply_pass(rows, wanted, True)
        final, image, _ = self.collect()
        if len(final) != len(rows):
            raise RetryOverflow("确认前候选数量变化")
        selected = set()
        for row in final:
            i, original = self.locate(rows, row)
            if original.source != row.source:
                raise RetryOverflow("确认前饮料分组变化")
            if row.selected:
                selected.add(i)
        if selected != wanted or len(selected) != 4 or not self.zero_remaining(image):
            raise RetryOverflow("最终4瓶勾选或剩余名额未核实，不提交")
        button = self.context.run_recognition("ProduceDrinkNoButton", image)
        if not button or not button.hit or not button.best_result:
            raise RetryOverflow("保留按钮未核实")
        if self.context.tasker.stopping:
            raise RetryOverflow("任务正在停止")
        x, y, w, h = button.best_result.box
        self.controller.post_click(x + w // 2, y + h // 2).wait()
        for _ in range(8):
            if self.context.tasker.stopping:
                return True
            time.sleep(0.2)
            image = self.controller.post_screencap().wait().get()
            if not self.context.run_recognition("ProduceKeepDrinkFlag", image).hit:
                logger.success("饮料保留：已确认4瓶并离开上限选择页")
                return True
        logger.warning("饮料保留：提交后页面尚未退出，交回流程重新核实")
        return True


def handle_overflow(context):
    try:
        return OverflowPicker(context).run()
    except RetryOverflow as error:
        logger.warning("饮料保留：" + str(error) + "；不盲点保留，不中止培育")
        return True

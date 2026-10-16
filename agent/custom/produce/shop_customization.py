"""Bounded customization transactions, called between healing and drinks."""
import time

from utils import logger
from custom import customization as vision
from custom.removal import selected_card_box_matches
from custom.deck_scan import normalize_deck_boxes, same_deck_view
from custom.owned_deck import RowCounts
from custom.card_visual import normalize_card_name
from custom.square_identity import match

from . import shared


class CustomizationSession:
    MAX_PAGES = 12

    def __init__(self, context, shop, *, recovery=False):
        self.context, self.shop = context, shop
        self.deadline = time.monotonic() + 180
        self.rules = [] if recovery else vision.targets(context)
        self.by_name = {t["key"]: t for t in self.rules}

    def capture(self):
        if self.context.tasker.stopping or time.monotonic() >= self.deadline:
            raise InterruptedError("自定义观察已结束")
        image = self.context.tasker.controller.post_screencap().wait().get()
        if shared._detect_disconnect_screen(self.context, image):
            self.shop._disconnect_detected = True
            raise InterruptedError("连接中断")
        return image

    def click(self, box):
        if not box or self.context.tasker.stopping:
            raise InterruptedError("按钮未确认")
        x, y, w, h = map(int, box)
        self.context.tasker.controller.post_click(x+w//2, y+h//2).wait()
        time.sleep(.25)

    def stable(self, stage=None, name=None):
        previous = None
        for _ in range(5):
            image = self.capture()
            page = vision.read_page(self.context, image)
            if page and (stage is None or page["stage"] == stage) and (name is None or page["name"] == name):
                signature = {k: v for k, v in page.items() if k not in ("back", "open", "execute")}
                if signature == previous:
                    return image, page
                previous = signature
            else:
                previous = None
            time.sleep(.15)
        raise InterruptedError("自定义页面未稳定")

    def grid(self):
        image, page = self.stable("list")
        detail = self.context.run_recognition("ProduceRecognitionCards", image)
        boxes = normalize_deck_boxes(image, [list(r.box) for r in (detail.all_results or [])], top=560, bottom=1050) if detail else []
        grouped = []
        for box in sorted(boxes, key=lambda b: (b[1], b[0])):
            x, y, w, h = box
            group = next((r for r in grouped if abs(r[0]["box"][1]-y) < 10), None)
            if group is None:
                group = []; grouped.append(group)
            art = match(image, box)
            cx, cy = round(x+w/2), round(y+h/2)
            group.append(dict(box=box, name_key=normalize_card_name(art["name"]) if art else None,
                              pixels=image[cy-32:cy+32, cx-32:cx+32, :3].copy()))
        for group in grouped:
            group.sort(key=lambda c: c["box"][0])
        if (not grouped or any(len(row) != 4 for row in grouped[:-1])
                or any(abs(c["box"][0]-(82+146*i)) > 16 for row in grouped for i, c in enumerate(row))):
            raise InterruptedError("自定义牌库未完整识别")
        return image, page, grouped

    def scroll(self, forward):
        if self.context.tasker.stopping:
            raise InterruptedError
        # Keep an overlapping row so the shared tracker can detect duplicates.
        start, end = (1000, 710) if forward else (710, 1000)
        self.context.tasker.controller.post_swipe(665, start, 665, end, 400).wait()
        time.sleep(.3)

    def rewind(self):
        current = self.grid()
        stationary = 0
        for _ in range(self.MAX_PAGES):
            self.scroll(False)
            following = self.grid()
            stationary = stationary + 1 if self.same_view(current, following) else 0
            current = following
            if stationary >= 2:
                return current
        raise InterruptedError("自定义牌库顶部未确认")

    @staticmethod
    def same_view(first, second):
        return same_deck_view(first[0], second[0],
                              [c["box"] for row in first[2] for c in row],
                              [c["box"] for row in second[2] for c in row], top=560)

    def open_card(self, cell):
        self.click(cell["box"])
        image, page = self.stable("list")
        if not selected_card_box_matches(image, cell["box"]):
            raise InterruptedError("选中卡牌位置未确认")
        if cell["name_key"] and page["name"] and cell["name_key"] != page["name"]:
            raise InterruptedError("卡图与卡名不一致")
        if not page["name"]:
            raise InterruptedError("选中卡名称未确认")
        if page["name"] not in self.by_name or not page["upgraded"]:
            return page, None
        self.click(page["open"])
        _, menu = self.stable("menu", page["name"])
        return page, menu

    def scan(self, wanted=None):
        current = self.rewind()
        tracker = RowCounts(None, self.by_name, row_match=vision.same_card_row)
        stationary = 0
        for _ in range(self.MAX_PAGES):
            image, _, groups = current
            for group in groups:
                for cell in group:
                    if cell["name_key"] and cell["name_key"] not in (self.by_name if wanted is None else {wanted}):
                        continue
                    page, menu = self.open_card(cell)
                    cell["name_key"] = page["name"]
                    if menu is None:
                        continue
                    target = self.by_name[menu["name"]]
                    status, index = vision.next_purchase(target, menu, 10**9)
                    if status == "unknown":
                        raise InterruptedError("卡牌自定义次数未核实")
                    if status == "buy":
                        if wanted is not None:
                            return target, menu
                        cell["work"] = dict(target=target, menu=menu)
                    self.click(menu["back"])
                    self.stable("list")
            tracker.add(groups)
            if not tracker.valid:
                raise InterruptedError("牌库翻页重叠未确认")
            self.scroll(True)
            following = self.grid()
            stationary = stationary + 1 if self.same_view(current, following) else 0
            if stationary >= 2:
                if wanted is not None:
                    return None
                jobs = [cell["work"] for row in tracker.rows for cell in row if cell.get("work")]
                order = {t["key"]: i for i, t in enumerate(self.rules)}
                return sorted(jobs, key=lambda job: order[job["target"]["key"]])
            current = following
        raise InterruptedError("自定义牌库扫描达到上限")

    def leave(self):
        """Only use the two customization-specific back controls."""
        for _ in range(5):
            if self.context.tasker.stopping:
                return None
            image = self.context.tasker.controller.post_screencap().wait().get()
            if shared._detect_disconnect_screen(self.context, image):
                self.shop._disconnect_detected = True
                return None
            page = vision.read_page(self.context, image)
            if page:
                try:
                    self.click(page["back"])
                except InterruptedError:
                    return None
            elif self.shop._is_shop_scene(self.context, image):
                return self.shop._wait_shop_ready(self.context)
            else:
                time.sleep(.25)
        return None

    def purchase(self, target, menu):
        image, menu = self.stable("menu", target["key"])
        points = self.shop._recognize_points(self.context, image)
        status, index = vision.next_purchase(target, menu, points)
        if status != "buy":
            return status
        option = menu["options"][index]
        self.click(option["box"])
        image, selected = self.stable("menu", target["key"])
        current_points = self.shop._recognize_points(self.context, image)
        if (vision.next_purchase(target, selected, current_points) != ("buy", index)
                or current_points != points or selected["total"] != menu["total"]
                or [o["remaining"] for o in selected["options"]] != [o["remaining"] for o in menu["options"]]
                or not selected["options"][index]["selected"]
                or sum(o["selected"] for o in selected["options"]) != 1):
            raise InterruptedError("自定义预览未确认")
        # Persist before input: an unconfirmed receipt never triggers a retry.
        self.shop._visit_state(self.context, custom_pending=True, custom_blocked=True)
        self.click(selected["execute"])
        seen_receipt = False
        confirmed = 0
        for _ in range(12):
            image = self.capture()
            toast = vision.receipt(self.context, image, target["key"])
            if toast:
                seen_receipt = True
                self.click(toast)
                continue
            after = self.shop._recognize_points(self.context, image)
            page = vision.read_page(self.context, image)
            decremented = bool(page and page.get("stage") == "menu" and page.get("name") == target["key"] and page.get("total") == menu["total"]-1
                               and len(page.get("options", [])) == len(menu["options"])
                               and all(after_option["remaining"] == before_option["remaining"] - (i == index)
                                       for i, (after_option, before_option) in enumerate(zip(page["options"], menu["options"]))))
            returned = page is None and self.shop._is_shop_scene(self.context, image)
            confirmed = confirmed+1 if after == points-option["price"] and (decremented or (seen_receipt and returned)) else 0
            if confirmed >= 2:
                self.shop._visit_state(self.context, custom_pending=False, custom_blocked=False)
                logger.success(f"自定义完成：{target['name']}，选项 {index+1}")
                return "bought"
            time.sleep(.3)
        raise InterruptedError("自定义扣款结果未确认")

    def run(self, image):
        """Return (shop frame, minimum next cost), or (None, None) on recovery."""
        if not self.rules or self.shop._visit_state(self.context).get("custom_blocked"):
            return image, None
        try:
            points = self.shop._recognize_points(self.context, image)
            floor = min(price for t in self.rules for n, option in zip(t["counts"], t["spec"]["options"])
                        if n for price in option["costs"])
            if points is None or points < floor:
                return image, None
            gate = vision.entry(self.context, image)
            if not gate or gate["quota"] == 0:
                return image, None
            self.click(gate["box"])
            self.stable("list")
            logger.info("检查目标卡的自定义进度")
            jobs = self.scan()
            for job in jobs:
                target, old = job["target"], job["menu"]
                while vision.next_purchase(target, old, 10**9)[0] == "buy":
                    index = vision.next_purchase(target, old, 10**9)[1]
                    next_cost = old["options"][index]["price"]
                    image = self.leave()
                    if image is None:
                        raise InterruptedError("自定义返回商店未确认")
                    gate = vision.entry(self.context, image)
                    if gate is None:
                        raise InterruptedError("自定义可用次数未确认")
                    points = self.shop._recognize_points(self.context, image)
                    if points is None or points < next_cost:
                        logger.info("积分不足，结束本轮自定义，继续商店流程")
                        return image, None
                    if gate["quota"] == 0:
                        return image, next_cost
                    self.click(gate["box"])
                    self.stable("list")
                    found = self.scan(target["key"])
                    if not found:
                        break
                    target, actual = found
                    outcome = self.purchase(target, actual)
                    if outcome != "bought":
                        return self.leave(), None
                    old = dict(actual, options=[dict(o) for o in actual["options"]])
                    index = vision.next_purchase(target, actual, 10**9)[1]
                    old["total"] -= 1
                    old["options"][index]["remaining"] -= 1
                    done = len(target["spec"]["options"][index]["costs"]) - old["options"][index]["remaining"]
                    if old["options"][index]["remaining"]:
                        old["options"][index]["price"] = target["spec"]["options"][index]["costs"][done]
            return self.leave(), None
        except (InterruptedError, ValueError, KeyError, TypeError) as error:
            self.shop._visit_state(self.context, custom_blocked=True)
            logger.warning("本轮自定义画面或扣款结果未确认，保留已完成进度并返回商店")
            logger.debug(f"自定义暂缓原因：{error}")
            return self.leave(), None

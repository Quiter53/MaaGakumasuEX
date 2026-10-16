"""Bounded customization transactions, called between healing and drinks."""
import time

from utils import logger
from custom import customization as vision
from custom.removal import selected_card_box_matches
from custom.deck_scan import deck_scroll_distance, normalize_deck_boxes, same_deck_view
from custom.owned_deck import RowCounts
from custom.card_visual import normalize_card_name
from custom.square_identity import match
from custom.frame_geometry import reference_frame

from . import shared


class CustomizationSession:
    MAX_PAGES = 12

    def __init__(self, context, shop, *, recovery=False):
        self.context, self.shop = context, shop
        self.deadline = time.monotonic() + 180
        self.rules = [] if recovery else vision.targets(context)
        self.by_name = {t["key"]: t for t in self.rules}
        self.reported_capped = set()
        self.finished = False
        self.awaiting_refresh = False
        self.last_menu = None
        self.guidance = False

    def read_page(self, image):
        if self.guidance:
            return vision.read_page(self.context, image, guidance=True)
        return vision.read_page(self.context, image)

    def capture(self):
        if self.context.tasker.stopping:
            raise InterruptedError("任务已停止")
        if time.monotonic() >= self.deadline:
            raise InterruptedError("自定义观察超过时间上限")
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
            page = self.read_page(image)
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

    def scroll(self, forward, current):
        from custom.scroll import scroll_list
        if self.context.tasker.stopping:
            raise InterruptedError
        boxes = [cell["box"] for row in current[2] for cell in row]
        distance = deck_scroll_distance(boxes, top=560, overlap=True)
        start, end = (1035, 1035-distance) if forward else (580, 580+distance)
        # Keep a complete overlap row. Releasing a fast drag immediately lets
        # Unity inertia skip that row even when the requested distance is small.
        if not scroll_list(self.context, (665, start), (665, end)):
            raise InterruptedError("自定义牌库滑动未完成")
        time.sleep(.35)

    def rewind(self):
        current = self.grid()
        stationary = 0
        for _ in range(self.MAX_PAGES):
            thumb = vision.scrollbar(current[0])
            if thumb is not None and thumb[0] <= 580:
                return current
            self.scroll(False, current)
            following = self.grid()
            stationary = stationary + 1 if self.same_view(current, following) else 0
            current = following
            if stationary >= 2:
                thumb = vision.scrollbar(current[0])
                if thumb is None or thumb[0] <= 580:
                    return current
                raise InterruptedError("自定义牌库尚未回到顶部")
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
        if page.get("capped"):
            if page["name"] not in self.reported_capped:
                logger.info(f"{self.by_name[page['name']]['name']}已达自定义上限，跳过")
                self.reported_capped.add(page["name"])
            return page, None
        if page.get("open_enabled") is not True:
            raise InterruptedError("自定义入口未启用，不点击灰色按钮")
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
                    if wanted is not None and menu["name"] != wanted:
                        self.click(menu["back"])
                        self.stable("list")
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
            thumb = vision.scrollbar(image)
            if thumb is not None and thumb[1] >= 1030:
                if wanted is not None:
                    return None
                jobs = [cell["work"] for row in tracker.rows for cell in row if cell.get("work")]
                order = {t["key"]: i for i, t in enumerate(self.rules)}
                return sorted(jobs, key=lambda job: order[job["target"]["key"]])
            self.scroll(True, current)
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
            page = self.read_page(image)
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
        logger.info(f"开始自定义：{target['name']}，选项 {index+1}")
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
        skip_count = 0
        for _ in range(12):
            image = self.capture()
            toast = vision.receipt(self.context, image, target["key"])
            if toast:
                seen_receipt = True
                self.click([20, 20, 5, 5])
                continue
            after = self.shop._recognize_points(self.context, image)
            page = self.read_page(image)
            decremented = vision.purchase_progress(menu, page, index)
            returned = page is None and self.shop._is_shop_scene(self.context, image)
            confirmed = confirmed+1 if after == points-option["price"] and (decremented or (seen_receipt and returned)) else 0
            if confirmed >= 2:
                self.shop._visit_state(self.context, custom_pending=False, custom_blocked=False)
                self.last_menu = page if decremented else None
                logger.success(f"自定义完成：{target['name']}，选项 {index+1}，剩余积分 {after}")
                return "bought"
            # Same neutral corner used for shop acquisition/reveal animations.
            # Only this submitted transaction owns the taps; never tap menus,
            # the returned shop, stopped/disconnected or invalid-size frames.
            if page is None and not returned and reference_frame(image) and skip_count < 3:
                self.click([20, 20, 5, 5])
                skip_count += 1
            time.sleep(.3)
        raise InterruptedError("自定义扣款结果未确认")

    def customize_card(self, target, menu):
        """Finish this physical card before leaving its menu; quota counts cards."""
        bought = False
        for _ in range(target["spec"]["max"] + 1):
            status, _ = vision.next_purchase(target, menu, 10**9)
            if status in ("done", "capped"):
                return "bought" if bought else status
            if status != "buy":
                raise InterruptedError("卡牌自定义进度未确认")
            self.last_menu = None
            outcome = self.purchase(target, menu)
            if outcome != "bought":
                if outcome == "unknown":
                    raise InterruptedError("卡牌自定义价格或进度未确认")
                return outcome
            bought = True
            # Do not leave/reselect between options. The first operation takes
            # one card slot; subsequent operations on this card take no slots.
            if self.last_menu is None:
                _, menu = self.stable("menu", target["key"])
            else:
                menu = self.last_menu
        raise InterruptedError("单卡自定义次数超过上限")

    def budget_end(self):
        self.finished = True
        logger.info("积分不足，保留已完成的自定义，继续商店饮料流程")
        return self.leave(), None

    def run_guidance(self):
        """Special guidance owns two card slots, no refresh or drink actions."""
        self.guidance = True
        try:
            if not self.rules or self.shop._visit_state(self.context).get("custom_blocked"):
                return
            _, initial = self.stable()
            if initial["stage"] == "menu":
                self.click(initial["back"])
            _, page = self.stable("list")
            if page["quota"] is None or not 0 <= page["quota"] <= 2:
                raise InterruptedError("特别指导剩余名额未确认")
            # A complete scan includes distinct physical copies in list order.
            # Missing/capped/already-satisfied targets are not pending jobs.
            jobs = self.scan() if page["quota"] > 0 else []
            for job in jobs:
                if page["quota"] == 0:
                    break
                found = self.scan(job["target"]["key"])
                if not found:
                    continue
                target, menu = found
                outcome = self.customize_card(target, menu)
                # Keep already completed operations if the next one is costly.
                if outcome == "budget":
                    logger.info("特别指导积分不足，保留已完成进度并结束指导")
                    break
                image, menu = self.stable("menu", target["key"])
                self.click(menu["back"])
                _, following = self.stable("list")
                if following["quota"] not in (page["quota"], page["quota"]-1):
                    raise InterruptedError("特别指导剩余名额变化未确认")
                page = following
        except (InterruptedError, ValueError, KeyError, TypeError) as error:
            if not self.context.tasker.stopping:
                self.shop._visit_state(self.context, custom_blocked=True)
                logger.warning("本次特别指导未完整确认，保留进度并结束指导")
                logger.debug(f"特别指导暂缓原因：{error}")
        finally:
            # Return from a card menu via its observed control. The existing
            # guidance exit node owns leaving the event, never a shop refresh.
            if not self.context.tasker.stopping:
                image = self.context.tasker.controller.post_screencap().wait().get()
                if not shared._detect_disconnect_screen(self.context, image):
                    page = self.read_page(image)
                    if page and page["stage"] == "menu":
                        try:
                            self.click(page["back"])
                        except InterruptedError:
                            pass  # A manual stop between the read and click.

    def run(self, image):
        """Return (shop frame, minimum next cost), or (None, None) on recovery."""
        if self.finished or not self.rules or self.shop._visit_state(self.context).get("custom_blocked"):
            return image, None
        self.deadline = time.monotonic() + 180
        try:
            points = self.shop._recognize_points(self.context, image)
            floor = min(price for t in self.rules for n, option in zip(t["counts"], t["spec"]["options"])
                        if n for price in option["costs"])
            if points is None or points < floor:
                self.finished = True
                return image, None
            gate = vision.entry(self.context, image)
            if self.awaiting_refresh and (not gate or gate["quota"] != 2):
                raise InterruptedError("刷新后的自定义名额未恢复，不重复刷新")
            self.awaiting_refresh = False
            if not gate or gate["quota"] == 0:
                return image, None
            self.click(gate["box"])
            self.stable("list")
            logger.info("检查目标卡的自定义进度")
            # With two available card slots, the highest-priority unfinished
            # target can be bought immediately in the menu just observed.
            # Before spending the final slot, keep the full plan so paid
            # refresh still requires positively observed unfinished work.
            if gate["quota"] > 1:
                for requested in self.rules:
                    found = self.scan(requested["key"])
                    if not found:
                        continue
                    target, menu = found
                    outcome = self.customize_card(target, menu)
                    if outcome == "budget":
                        return self.budget_end()
                    image = self.leave()
                    if outcome != "bought":
                        return image, None
                    if image is None:
                        raise InterruptedError("自定义返回商店未确认")
                    next_gate = vision.entry(self.context, image)
                    if not next_gate or next_gate["quota"] not in (gate["quota"], gate["quota"]-1):
                        raise InterruptedError("自定义完成后的额度未确认")
                    self.click(next_gate["box"])
                    self.stable("list")
                    break
                else:
                    self.finished = True
                    return self.leave(), None
            jobs = self.scan()
            for job in jobs:
                target, old = job["target"], job["menu"]
                status, index = vision.next_purchase(target, old, 10**9)
                if status != "buy":
                    continue
                next_cost = old["options"][index]["price"]
                image = self.leave()
                if image is None:
                    raise InterruptedError("自定义返回商店未确认")
                gate = vision.entry(self.context, image)
                if gate is None:
                    raise InterruptedError("自定义可用次数未确认")
                points = self.shop._recognize_points(self.context, image)
                if points is None:
                    raise InterruptedError("自定义余额未确认")
                if points < next_cost:
                    return self.budget_end()
                if gate["quota"] == 0:
                    self.awaiting_refresh = True
                    return image, next_cost
                self.click(gate["box"])
                self.stable("list")
                found = self.scan(target["key"])
                if not found:
                    continue
                target, actual = found
                outcome = self.customize_card(target, actual)
                if outcome == "budget":
                    return self.budget_end()
                if outcome != "bought":
                    return self.leave(), None
            self.finished = True
            return self.leave(), None

        except (InterruptedError, ValueError, KeyError, TypeError) as error:
            if self.context.tasker.stopping:
                return None, None
            self.shop._visit_state(self.context, custom_blocked=True)
            logger.warning("本轮自定义画面或扣款结果未确认，保留已完成进度并返回商店")
            logger.debug(f"自定义暂缓原因：{error}")
            return self.leave(), None

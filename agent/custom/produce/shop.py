"""Cultivation shop; behavior moved from the original action module."""

import time
from typing import Optional

import numpy as np
from utils import logger
from maa.context import Context
from custom.params import action_mode
from custom.card_rules import single_copy_cards, cultivation_rarity, cultivation_priority
from maa.custom_action import CustomAction
from custom.card_copies import needs_deck_check
from custom.card_visual import classify_rarity, rarity_priority, match_card_catalog, normalize_card_name, intersection_over_union
from custom.drink_policy import TARGET_NAMES
from custom.battle_drinks import BattleDrinkSession
from custom.frame_geometry import reference_frame
from maa.agent.agent_server import AgentServer

from . import shared
from .options import ProduceHIFAttributeBase


@AgentServer.custom_action("ProduceShoppingAuto")
class ProduceShoppingAuto(CustomAction):
    """Delete a weak card, buy whitelisted cards, then cheapest target drinks."""

    MAX_PURCHASE_ATTEMPTS = 16
    SHOP_ROI = [46, 480, 626, 530]
    DELETE_COST = 100
    CARD_SLOTS = [
        [66, 482, 126, 126],
        [220, 482, 126, 126],
        [374, 482, 126, 126],
        [528, 482, 126, 126],
    ]
    DRINK_SLOTS = [
        [66, 670, 126, 126],
        [220, 670, 126, 126],
        [374, 670, 126, 126],
        [528, 670, 126, 126],
    ]
    # The live Japanese shop places the blue delete button below the products.
    # Do not use the old text-template ROI around y=755..890: it overlaps the
    # second drink tile and caused the delete action to buy that drink instead.
    DELETE_ENTRY_ROI = [345, 940, 340, 130]
    DELETE_ENTRY_POINT = (516, 1007)
    # 商店删除/购买后弹出的偶像评论气泡（大面积白色圆角面板）不会自动消失，
    # 会一直盖住右下角「✕終了」退出按钮，导致 ProduceShoppingExit 识别超时。
    COMMENT_BUBBLE_BOX = (60, 975, 600, 110)
    COMMENT_BUBBLE_TAP_POINT = (360, 1020)

    @staticmethod
    def _visit_state(context, **updates):
        node = context.get_node_data("ProduceShoppingFlag")
        attach = node.get("attach", {}) if isinstance(node, dict) else {}
        state = dict(attach.get("automatic_visit", {})) if isinstance(attach, dict) else {}
        if updates:
            state.update(updates)
            context.override_pipeline({"ProduceShoppingFlag": {"attach": {"automatic_visit": state}}})
        return state

    @classmethod
    def _reset_visit(cls, context):
        """Close a visit only at a verified exit or an outside-shop boundary.

        A repeated shop callback, unknown frame or disconnect is not a new
        visit: those paths must retain the pending receipt/exit protections.
        """
        cls._visit_state(context, exit_pending=False, heal_pending=False, custom_pending=False, custom_blocked=False)
        cls._delete_cancel_state(context, pending=False)
        cls._shop_delete_visit(context, reset=True)

    def _finish_optional_shop(self, context):
        """Finish this visit after an uncertain optional purchase, without retrying it."""
        self._visit_state(context, exit_pending=True)
        for _ in range(6):
            if context.tasker.stopping:
                return True
            image = context.tasker.controller.post_screencap().wait().get()
            if shared._detect_disconnect_screen(context, image):
                return True
            # Close a freshly recognized cancel control before testing the
            # grid, which can remain visible behind a translucent modal.
            cancel = BattleDrinkSession._cancel_box(context, image)
            if cancel:
                self._click_product(context, cancel)
                time.sleep(0.4)
                continue
            if self._is_shop_scene(context, image):
                self._dismiss_comment_bubble(context)
                result = context.run_task("ProduceShoppingExit")
                if result and result.status.succeeded:
                    # The pipeline's ExitConfirm is DirectHit, not a receipt.
                    # Do not clear intent while a swallowed click left the
                    # same shop visible (which would restart optional spending).
                    gone = 0
                    for _ in range(4):
                        time.sleep(0.2)
                        frame = context.tasker.controller.post_screencap().wait().get()
                        if context.tasker.stopping or shared._detect_disconnect_screen(context, frame):
                            return True
                        gone = gone + 1 if not self._is_shop_scene(context, frame) else 0
                        if gone >= 2:
                            self._reset_visit(context)
                            break
                return True
            time.sleep(0.3)
        # A transition/disconnect may finish asynchronously. Keep exit intent
        # across action instances so reentry cannot purchase/refresh again.
        return True

    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        from .rewards import ProduceChooseRewardAuto

        if self._visit_state(context).get("exit_pending"):
            return self._finish_optional_shop(context)
        lists = shared._load_card_lists()
        blacklist = set(lists["blacklist"]["cards"]) if lists["blacklist"]["enabled"] else set()
        whitelist = set(lists["whitelist"]["cards"]) if lists["whitelist"]["enabled"] else set()
        replacement_section = lists.get("replacementList", {})
        replacement = set(replacement_section.get("cards", [])) if replacement_section.get("enabled") else set()
        blocked = {"drinks": [], "cards": []}
        attempts = 0
        self._disconnect_detected = False
        self._owned_drinks = None
        self._inventory_read_failed = False

        image = context.tasker.controller.post_screencap().wait().get()
        if not self._is_shop_scene(context, image) and not self._reenter_shop(context):
            if not self._disconnect_detected and not getattr(self, "_shop_departed", False):
                logger.warning("咨询购买终止: 无法进入商品页")
            return True
        if not self._dismiss_comment_bubble(context):
            return True
        image = self._wait_shop_ready(context)
        if image is None:
            return self._disconnect_detected or context.tasker.stopping or getattr(self, "_shop_departed", False)
        from custom.shop import is_interval_shop

        if is_interval_shop(context, image):
            # A fading/split header can initially hit the generic shop node.
            # Re-route before scanning the four-slot consultation layout.
            return ProduceHIFIntervalShoppingAuto().run(context, argv)
        skip_delete = bool(getattr(self, "_skip_delete", False))
        delete_enabled = self._allow_shop_delete(context)
        if not delete_enabled:
            skip_delete = True
            logger.info("普通商店删牌已关闭，保留积分用于购物")
        self._skip_delete = False
        if self._delete_cancel_state(context):
            # A Cancel can finish between callbacks. Do not enter deletion
            # again when the generic shop entry observes that same return.
            self._delete_cancel_state(context, pending=False)
            skip_delete = True
        if skip_delete:
            self._shop_delete_visit(context, image, checked=True)
        skip_delete = self._shop_delete_visit(context, image) or skip_delete
        logger.info(
            ("咨询策略: 仅删除 N/黑名单/R，无合适候选就跳过删除；" if delete_enabled else "咨询策略: ")
            + "购买白名单卡；否则只买目标中的最低价饮料"
        )
        points = self._recognize_points(context, image)

        delete_entry = self._find_shop_delete_entry(image) if delete_enabled else None
        from custom.shop import read_number

        delete_cost = read_number(context, image, [500, 950, 185, 90]) if delete_entry else None
        if not skip_delete and delete_entry and points is not None and delete_cost is not None and points >= delete_cost:
            # Mark before opening the list: a transition or resumed callback
            # must not restart this visit's scan after every purchase.
            self._shop_delete_visit(context, image, checked=True)
            delete_status = self._try_delete_owned_card(context, image, blacklist, whitelist, replacement)
            if delete_status in ("disconnected", "cancel_pending"):
                return True
            if delete_status == "uncertain":
                return self._finish_optional_shop(context)
            if delete_status in ("deleted", "skipped"):
                # Deletion's comment covers the price grid used by re-entry.
                # Dismiss it on the verified shop header before waiting for
                # that grid, otherwise all 12 re-entry probes necessarily fail.
                if not self._dismiss_comment_bubble(context):
                    return True
                if not self._reenter_shop(context):
                    if not self._disconnect_detected and not getattr(self, "_shop_departed", False):
                        logger.warning("咨询删除后无法回到商品页")
                    return True
                image = self._wait_shop_ready(context)
                if image is None:
                    return self._disconnect_detected or context.tasker.stopping or getattr(self, "_shop_departed", False)
                points = self._recognize_points(context, image)
        else:
            logger.info(
                "咨询本次访问已检查删牌，继续购物，不再打开删除列表"
                if skip_delete
                else f"咨询跳过删除: 入口={bool(delete_entry)} 积分={points} 费用={delete_cost}"
            )

        # The shop never changes stock unless the user presses refresh.  Scan
        # its four fixed card/drink slots once and retain them for this visit.
        cards = self._recognize_shop_cards(context, image, blocked["cards"])
        cards = self._resolve_shop_card_identities(context, cards)
        if self._disconnect_detected or context.tasker.stopping:
            return True
        drinks = self._recognize_drinks(context, image, blocked["drinks"])

        while attempts < self.MAX_PURCHASE_ATTEMPTS and not context.tasker.stopping:
            available_cards = [
                item
                for item in cards
                if item["box"] not in blocked["cards"]
                and item["name_key"] in whitelist
                and item["name_key"] not in blacklist
                and (not item.get("square_art_attempted") or item.get("square_art_trusted") or item.get("identity_verified"))
                and self._is_affordable(item, points)
            ]
            if available_cards:
                selected = max(
                    available_cards,
                    key=self._card_purchase_key,
                )
                if needs_deck_check(selected):
                    fresh = context.tasker.controller.post_screencap().wait().get()
                    checked, restored = ProduceChooseRewardAuto._limit_acquisition(
                        context, selected, cards, fresh, shared._load_card_lists(), purchase=True
                    )
                    if not restored:
                        return True
                    if checked is None:
                        blocked["cards"].append(selected["box"])
                        continue
                item_type = f"白名单卡牌 {selected['name']}"
                blocked_key = "cards"
                logger.info(
                    f"咨询白名单卡候选: name={selected['name']} price={selected['price']} rarity={selected['rarity']} box={selected['box']}"
                )
            else:
                available_drinks = [item for item in drinks if item["box"] not in blocked["drinks"] and self._is_affordable(item, points)]
                if not available_drinks:
                    logger.info("咨询购买完成: 没有可负担的目标饮料，保留饮料和积分")
                    break
                full = self._drink_inventory_full(context, image)
                if full is None:
                    return True if self._disconnect_detected or context.tasker.stopping else self._finish_optional_shop(context)
                if full:
                    room = self._full_inventory_has_non_target(context)
                    if room is None:
                        return True if self._disconnect_detected or context.tasker.stopping else self._finish_optional_shop(context)
                    if not room:
                        logger.info("咨询满栏均为目标饮料，保留现状和积分")
                        break
                selected = min(
                    available_drinks,
                    key=lambda item: (
                        item["price"] if item["price"] is not None else 9999,
                        item["box"][0],
                    ),
                )
                item_type = f"最低价目标饮料 {selected['name']}"
                blocked_key = "drinks"
                logger.info(f"咨询最低价目标饮料: name={selected['name']} price={selected['price']} box={selected['box']}")
                if full:
                    if not self._make_drink_space(context, selected["id"], non_target_only=True):
                        return True if self._disconnect_detected or context.tasker.stopping else self._finish_optional_shop(context)
                    image = self._wait_shop_ready(context)
                    if image is None or self._drink_inventory_full(context, image) is not False:
                        return (
                            True
                            if self._disconnect_detected or context.tasker.stopping or getattr(self, "_shop_departed", False)
                            else self._finish_optional_shop(context)
                        )

            attempts += 1
            status = self._purchase(
                context,
                selected["box"],
                item_type,
                is_drink=blocked_key == "drinks",
                drink_id=selected.get("id"),
                expected_price=selected["price"],
            )
            blocked[blocked_key].append(selected["box"])
            if status == "disconnected":
                return True
            if status == "bought":
                if blocked_key == "drinks":
                    self._owned_drinks = None
                if points is not None and selected["price"] is not None:
                    points = max(0, points - selected["price"])
                if self._reenter_shop(context):
                    if not self._dismiss_comment_bubble(context):
                        return True
                    image = self._wait_shop_ready(context)
                    if image is None:
                        return self._disconnect_detected or context.tasker.stopping or getattr(self, "_shop_departed", False)
                    points = self._recognize_points(context, image)
                    continue
                if not self._disconnect_detected and not getattr(self, "_shop_departed", False):
                    logger.warning("咨询购买后无法重新打开原商品页")
                if getattr(self, "_shop_departed", False):
                    return True
                break
            if status == "full":
                logger.info("咨询饮料栏已满，结束饮料购买，保留已有饮料")
                break
            if status == "no-funds":
                break
            if status == "uncertain":
                # Do not click a different product through an unknown modal.
                return self._finish_optional_shop(context)
            logger.warning(f"该商品当前无法购买，已跳过且不重复扫描: type={item_type}, box={selected['box']}")

        if attempts >= self.MAX_PURCHASE_ATTEMPTS:
            logger.warning("咨询购买达到安全上限，停止购买并退出，避免循环卡住")
        if not self._dismiss_comment_bubble(context):
            return True
        image = context.tasker.controller.post_screencap().wait().get()
        disconnect_node = shared._detect_disconnect_screen(context, image)
        if disconnect_node:
            self._disconnect_detected = True
            logger.warning(f"咨询收尾阶段检测到断线界面 {disconnect_node}，立即交回全局恢复流程")
            return True
        if self._is_shop_scene(context, image):
            context.run_task("ProduceShoppingExit")
        else:
            logger.info("咨询购买结束时已离开商品页，无需再次点击退出")
        return True

    def _drink_inventory_full(self, context, image):
        """Verify capacity without opening drinks; duplicate kinds may be bought."""
        from custom.shop import shop_occupied

        previous = shop_occupied(context, image)
        for _ in range(4):
            if context.tasker.stopping:
                return None
            time.sleep(0.2)
            image = context.tasker.controller.post_screencap().wait().get()
            if shared._detect_disconnect_screen(context, image):
                self._disconnect_detected = True
                return None
            occupied = shop_occupied(context, image)
            if occupied == previous:
                return len(occupied) >= 4
            previous = occupied
        logger.warning("商店饮料栏位未稳定，暂不购买或刷新")
        return None

    def _full_inventory_has_non_target(self, context):
        """Only verified occupied names can authorize replacing a full inventory."""
        owned = getattr(self, "_owned_drinks", None)
        if owned is None:
            owned = self._read_owned_drinks(context)
            self._owned_drinks = owned
        if owned is None or len(owned) != 4 or any(not item.get("verified") for item in owned.values()):
            self._inventory_read_failed = True
            logger.warning("满栏饮料身份未完整确认，不丢弃、不购买")
            return None
        return any(item.get("id") is None for item in owned.values())

    @staticmethod
    def _allow_shop_delete(context):
        return True

    @staticmethod
    def _is_affordable(item: dict, points: Optional[int]) -> bool:
        price = item.get("price")
        return points is not None and price is not None and 0 < price <= points

    @staticmethod
    def _recognize_points(context: Context, image) -> Optional[int]:
        from custom.shop import read_shop_points

        value = read_shop_points(context, image)
        if value is None:
            logger.debug("咨询积分识别失败，本帧不购买")
        else:
            logger.debug(f"咨询当前积分: {value}")
        return value

    @staticmethod
    def _recognize_price(context: Context, image, box: list) -> Optional[int]:
        from custom.shop import parse_shop_price

        x, y, width, height = box
        roi = [max(0, x - 8), y + height - 2, width + 16, 48]
        detail = context.run_recognition(
            "ProduceRecognitionPoint",
            image,
            pipeline_override={
                "ProduceRecognitionPoint": {
                    "roi": roi,
                    "expected": [],
                    "order_by": "Horizontal",
                }
            },
        )
        values = []
        for result in list(detail.all_results or detail.filtered_results or []) if detail else []:
            value = parse_shop_price(getattr(result, "text", ""))
            if value is None or float(getattr(result, "score", 1.0)) < 0.85:
                continue
            result_box = list(getattr(result, "box", []) or [])
            result_x = int(result_box[0]) if len(result_box) == 4 else 0
            if result_x < roi[0] and result_x <= roi[2]:
                result_x += roi[0]
            values.append((result_x, value))
        # Sale tiles contain old and new prices.  The live UI always prints the
        # payable sale price at the far right, so choose the rightmost number.
        if values:
            return max(values, key=lambda item: item[0])[1]
        # OCR can merge crossed-out and discounted prices (e.g. 75 + 52).
        # Read the payable right half again; never split an unknown long number.
        from custom.shop import read_number

        value = read_number(context, image, [x + width // 2, y + height - 2, width // 2 + 12, 48])
        return value if value is not None and 0 < value <= 999 else None

    @staticmethod
    def _slot_is_sold(image, box: list) -> bool:
        x, y, width, height = box
        crop = image[y + 12 : y + height - 12, x + 12 : x + width - 12, :3]
        if not crop.size:
            return True
        pixels = crop.astype(np.float32)
        maximum = pixels.max(axis=2)
        minimum = pixels.min(axis=2)
        saturation = np.divide(maximum - minimum, maximum, out=np.zeros_like(maximum), where=maximum > 0)
        white_ratio = float(((minimum > 205) & (saturation < 0.18)).mean())
        # Exchanged tiles are covered by a dark cyan veil and a white check.
        # Across saved live shops their mean is 170-182 and white ratio 0.12-0.15;
        # unsold products remain brighter than 195 or retain a white art field.
        return float(pixels.mean()) < 190 and white_ratio < 0.18

    @classmethod
    def _looks_like_shop_grid(cls, image) -> bool:
        visible_prices = 0
        for box in cls.CARD_SLOTS + cls.DRINK_SLOTS:
            x, y, width, height = box
            roi = image[y + height : y + height + 42, x : x + width, :3]
            if not roi.size:
                continue
            bright = (roi.min(axis=2) > 215).mean()
            if bright > 0.22:
                visible_prices += 1
        return visible_prices >= 4

    def _wait_shop_ready(self, context):
        """Do not scan through support-item acquisition effects on the shop."""
        from custom.shop import stock_signature, price_grid_visible

        previous = None
        taps = 0
        slots = self.CARD_SLOTS + self.DRINK_SLOTS
        for _ in range(12):
            if context.tasker.stopping:
                return None
            image = context.tasker.controller.post_screencap().wait().get()
            if shared._detect_disconnect_screen(context, image):
                self._disconnect_detected = True
                return None
            if price_grid_visible(image, slots):
                current = stock_signature(image, slots)
                if previous is not None and float(np.abs(current - previous).mean()) < 3:
                    return image
                previous = current
            else:
                previous = None
                # This template is confined to the unchanged yellow shop header.
                # Owned-drink/exchange dialogs blur it and cannot pass this guard.
                flag = context.run_recognition("ProduceShoppingFlag", image, pipeline_override={"ProduceShoppingFlag": {"focus": {}}})
                if flag and flag.hit and taps < 3:
                    logger.info("咨询商品区暂被获得效果遮挡，点击空白标题区并等待价格稳定")
                    context.tasker.controller.post_click(360, 360).wait()
                    taps += 1
            time.sleep(0.4)
        logger.warning("咨询商品价格区未稳定，停止扫描，不当作无目标饮料")
        return None

    @classmethod
    def _find_shop_delete_entry(cls, image) -> Optional[tuple[int, int]]:
        """Locate only the blue delete button in the fixed bottom shop band."""
        x, y, width, height = cls.DELETE_ENTRY_ROI
        crop = image[y : y + height, x : x + width, :3]
        if not crop.size:
            return None
        # Maa screenshots are BGR.  The Japanese delete button is saturated
        # cyan/blue; the product tiles above this ROI are deliberately excluded.
        blue = crop[:, :, 0].astype(np.int16)
        green = crop[:, :, 1].astype(np.int16)
        red = crop[:, :, 2].astype(np.int16)
        mask = (blue > 175) & (green > 85) & (green < 215) & (red < 135) & ((blue - red) > 70)
        ys, xs = np.where(mask)
        # Live reference screenshots contain roughly 10% blue pixels here.
        # Requiring a substantial component prevents clicking on incidental UI.
        if len(xs) < 1200:
            return None
        span_x = int(xs.max() - xs.min())
        span_y = int(ys.max() - ys.min())
        if span_x < 100 or span_y < 30:
            return None
        point = (x + int(np.median(xs)), y + int(np.median(ys)))
        if not (380 <= point[0] <= 660 and 970 <= point[1] <= 1045):
            return None
        return point

    def _try_delete_owned_card(
        self,
        context: Context,
        image,
        blacklist: set,
        whitelist: set,
        replacement: Optional[set] = None,
    ) -> str:
        entry_point = self._find_shop_delete_entry(image)
        if entry_point is None:
            logger.info("咨询未检测到蓝色删除入口，不点击商品区兜底")
            return "unavailable"
        else:
            logger.info(f"咨询识别到蓝色删除入口: point={entry_point}")
        self._delete_cancel_state(context, pending=False)  # A new deletion visit.
        context.tasker.controller.post_click(*entry_point).wait()

        delete_image = None
        entered_delete_page = False
        for _ in range(8):
            time.sleep(0.35)
            delete_image = context.tasker.controller.post_screencap().wait().get()
            disconnect_node = shared._detect_disconnect_screen(context, delete_image)
            if disconnect_node:
                self._disconnect_detected = True
                return "disconnected"
            flag = context.run_recognition(
                "ProduceChooseDeleteFlag",
                delete_image,
                pipeline_override={
                    "ProduceChooseDeleteFlag": {
                        "template": ["produce/delete_icon.png"],
                        "focus": {},
                    }
                },
            )
            if flag and flag.hit:
                entered_delete_page = True
                break
            # The icon template is localized too.  The fixed eight-price grid
            # disappearing is the reliable language-independent page signal.
            if not self._looks_like_shop_grid(delete_image):
                entered_delete_page = True
                break
        if not entered_delete_page:
            logger.info("咨询首次点击删除入口后页面未变化，固定坐标重试一次")
            context.tasker.controller.post_click(*self.DELETE_ENTRY_POINT).wait()
            time.sleep(0.9)
            delete_image = context.tasker.controller.post_screencap().wait().get()
            disconnect_node = shared._detect_disconnect_screen(context, delete_image)
            if disconnect_node:
                self._disconnect_detected = True
                return "disconnected"
            entered_delete_page = not self._looks_like_shop_grid(delete_image)
        if not entered_delete_page:
            logger.warning("咨询点击删除入口后商品网格仍未变化，确认当前不可用")
            return "unavailable"

        return self._delete_from_deck(context, blacklist, whitelist, replacement)

    def _delete_from_deck(self, context, blacklist, whitelist, replacement):
        from custom.removal import read_delete_button, read_delete_confirmation, selected_card_box_matches

        from .rewards import ProduceHIFChangeSourceAuto

        # Replacement-list permission applies to exchange only, never deletion.
        replacement = set()
        if context.tasker.stopping:
            return "cancel_pending"
        if self._delete_cancel_state(context):
            return self._cancel_delete(context)
        # Deletion always compares priorities; the user fast-hit switch only
        # affects exchange. Final identity/confirmation checks remain required.
        scanner = ProduceHIFChangeSourceAuto()
        scanner._interrupted = False
        scanner._removal_search_policy = (blacklist, whitelist, replacement, True)
        initial_page = scanner._rewind_source(context, change=False)
        if initial_page is None:
            if scanner._interrupted:
                self._disconnect_detected = True
                return "disconnected"
            logger.info("咨询删除牌库暂未稳定，跳过本次删除")
            return self._cancel_delete(context)
        selected = scanner._scan_source(context, blacklist, whitelist, replacement, change=False, initial_page=initial_page, strict=True)
        if context.tasker.stopping:
            return "uncertain"
        if scanner._interrupted:
            self._disconnect_detected = True
            return "disconnected"
        if selected is None:
            logger.info("咨询未找到可安全删除的 N、黑名单或 R，取消删除并继续商店；不删除替换名单牌")
            return self._cancel_delete(context)
        if not selected.get("detail_selected"):
            image = context.tasker.controller.post_screencap().wait().get()
            if not selected_card_box_matches(image, selected["box"]):
                self._click_product(context, selected["box"])
        time.sleep(0.4)
        image = context.tasker.controller.post_screencap().wait().get()
        if shared._detect_disconnect_screen(context, image):
            self._disconnect_detected = True
            return "disconnected"
        selected = self._verify_delete_selection(context, image, selected, blacklist, whitelist, replacement)
        if selected is None:
            logger.warning("咨询选中卡名或删除优先级未核实，取消删除，不提交未知牌")
            return self._cancel_delete(context)
        logger.info(
            f"咨询删除已核实: name={selected['name']} rarity={selected['rarity']} reason_rank={selected['delete_rank']} box={selected['box']}"
        )
        button = read_delete_button(context, image)
        if button is None:
            logger.warning("咨询删除页提交按钮未确认，保留现场")
            return "uncertain"
        if context.tasker.stopping:
            return "uncertain"
        self._click_product(context, button)
        invoice = None
        for _ in range(8):
            if context.tasker.stopping:
                return "uncertain"
            time.sleep(0.35)
            image = context.tasker.controller.post_screencap().wait().get()
            if shared._detect_disconnect_screen(context, image):
                self._disconnect_detected = True
                return "disconnected"
            current = read_delete_confirmation(context, image)
            if current and current["selected"]["name_key"] == selected["name_key"]:
                if invoice == current:
                    return self._commit_delete(context, current, selected)
                invoice = current
            else:
                invoice = None
        logger.warning("咨询删除确认弹窗未完整核实，不提交、不按成功处理")
        return "uncertain"

    @staticmethod
    def _shop_delete_visit(context, image=None, *, checked=None, reset=False):
        """Share the visit guard across new action instances and JumpBack calls.

        Keep original product artwork: sold veils and prices are not a new
        shop. A changed unsold product starts a fresh visit. HIF's verified
        schedule entry explicitly resets even if the next stock is identical.
        State lives only in the task context, never user configuration.
        """
        node = context.get_node_data("ProduceHIFDeleteFlag")
        attach = dict(node.get("attach", {})) if isinstance(node, dict) and isinstance(node.get("attach", {}), dict) else {}
        key = "shop_delete_visit"
        if reset:
            # Native override_pipeline merges attach objects recursively;
            # omitting a key does not erase the preceding visit's state.
            attach[key] = {"checked": False, "stock": []}
            context.override_pipeline({"ProduceHIFDeleteFlag": {"attach": attach}})
            return False
        old = attach.get(key)
        old = old if isinstance(old, dict) else {}
        # Called only after the consultation grid is confirmed stable.
        slots = ProduceShoppingAuto.CARD_SLOTS + ProduceShoppingAuto.DRINK_SLOTS
        stock = []
        if reference_frame(image):
            for box in slots:
                x, y, w, h = box
                if ProduceShoppingAuto._slot_is_sold(image, box):
                    stock.append(None)
                else:
                    stock.append(image[y + 25 : y + h - 25 : 12, x + 25 : x + w - 25 : 12, :3].astype(int).reshape(-1).tolist())
        previous = old.get("stock", [])
        changed = len(stock) == len(previous) == len(slots) and any(
            now is not None and (then is None or len(now) != len(then) or float(np.abs(np.asarray(now) - np.asarray(then)).mean()) > 12)
            for now, then in zip(stock, previous)
        )
        if changed:
            old = {}
        value = {"checked": bool(old.get("checked", False)), "stock": old.get("stock") or stock}
        if checked is not None:
            value["checked"] = bool(checked)
        if value != attach.get(key):
            attach[key] = value
            context.override_pipeline({"ProduceHIFDeleteFlag": {"attach": attach}})
        return value["checked"]

    @staticmethod
    def _delete_cancel_state(context, *, pending=None):
        """Task-context state shared by the shop and resumed-delete callbacks.

        Agent action instances can differ or outlive a task. Node attach data
        belongs to this pipeline context and is never saved to user settings.
        """
        node = context.get_node_data("ProduceHIFDeleteFlag")
        attach = node.get("attach", {}) if isinstance(node, dict) else {}
        attach = dict(attach) if isinstance(attach, dict) else {}
        key = "shop_delete_cancel_pending"
        if pending is not None:
            attach[key] = bool(pending)
            context.override_pipeline({"ProduceHIFDeleteFlag": {"attach": attach}})
        return bool(attach.get(key, False))

    def _cancel_delete(self, context):
        """Cancel only the deck's Cancel button; never use the generic OK route."""
        from custom.shop import ocr

        self._delete_cancel_state(context, pending=True)
        for attempt in range(7):
            if context.tasker.stopping:
                return "cancel_pending"
            image = context.tasker.controller.post_screencap().wait().get()
            if shared._detect_disconnect_screen(context, image):
                self._disconnect_detected = True
                return "disconnected"
            flag = context.run_recognition(
                "ProduceChooseDeleteFlag",
                image,
                pipeline_override={"ProduceChooseDeleteFlag": {"template": ["produce/delete_icon.png"], "focus": {}}},
            )
            if not (flag and flag.hit):
                if self._is_shop_scene(context, image):
                    self._delete_cancel_state(context, pending=False)
                    logger.info("咨询已取消删除，继续商店流程")
                    return "skipped"
            elif attempt in (0, 2, 4):
                buttons = ocr(context, image, [65, 1100, 290, 130], [r"^[×✕xX]?\s*(キャンセル|取消)$"])
                if len(buttons) == 1 and buttons[0].score >= 0.80 and not context.tasker.stopping:
                    self._click_product(context, list(buttons[0].box))
            time.sleep(0.3)
        logger.warning("咨询取消删除尚未确认，交回主流程重试，不提交删除、不结束培育")
        return "cancel_pending"

    def _commit_delete(self, context, invoice, selected):
        from custom.removal import read_delete_confirmation

        if context.tasker.stopping:
            return "uncertain"
        logger.info(f"咨询删除二次确认: {selected['name']} 积分={invoice['before']}→{invoice['after']}")
        context.tasker.controller.post_click(*invoice["confirm"]).wait()
        confirmations = 0
        for _ in range(10):
            if context.tasker.stopping:
                return "uncertain"
            time.sleep(0.4)
            image = context.tasker.controller.post_screencap().wait().get()
            if shared._detect_disconnect_screen(context, image):
                self._disconnect_detected = True
                return "disconnected"
            settled = self._recognize_points(context, image) == invoice["after"] and not read_delete_confirmation(context, image)
            confirmations = confirmations + 1 if settled else 0
            if confirmations >= 2:
                logger.success(f"咨询删除已确认扣款: name={selected['name']} rarity={selected['rarity']} reason_rank={selected['delete_rank']}")
                return "deleted"
        logger.warning("咨询删除扣款未确认，保留现场，不重复点击")
        return "uncertain"

    @staticmethod
    def _choose_delete_candidate(candidates: list, blacklist: set, whitelist: set, replacement: Optional[set] = None) -> Optional[dict]:
        """Shop deletion is limited to N > blacklist > R, without replacement overrides."""
        return ProduceShoppingAuto._choose_removal_candidate(candidates, blacklist, whitelist)

    @staticmethod
    def _choose_removal_candidate(candidates: list, blacklist: set, whitelist: set, replacement: Optional[set] = None) -> Optional[dict]:
        """N > blacklist > R > explicit replacement; unknown names are protected."""
        replacement = replacement or set()
        limited = single_copy_cards()
        eligible = []
        for candidate in candidates:
            item = dict(candidate)
            if item.get("selectable") is False:
                continue
            if not item.get("name_key") or item.get("name_confidence", 0) < 0.55:
                continue
            if item["name_key"] in limited:
                if (item.get("owned_count") or 0) > 1:
                    item["delete_rank"] = 4
                    eligible.append(item)
                continue
            if item["name_key"] in whitelist and item["name_key"] not in replacement:
                continue
            if (
                item.get("rarity_confidence", 1) < 0.30
                and not item.get("identity_verified", False)
                and not item.get("square_art_trusted", False)
            ):
                continue
            policy_rarity = cultivation_rarity(item["name_key"], item["rarity"])
            if policy_rarity == "N":
                item["delete_rank"] = 3
            elif item["name_key"] in blacklist:
                item["delete_rank"] = 2
            elif policy_rarity == "R":
                item["delete_rank"] = 1
            elif item["name_key"] in replacement and policy_rarity in ("SR", "SSR"):
                item["delete_rank"] = 0
            else:
                continue
            eligible.append(item)

        return max(eligible, key=ProduceShoppingAuto._removal_key, default=None)

    @staticmethod
    def _removal_key(item):
        return (
            item["delete_rank"],
            1 if item["delete_rank"] == 0 and cultivation_rarity(item["name_key"], item["rarity"]) == "SR" else 0,
            item["name_confidence"],
            -item["box"][1],
            -item["box"][0],
        )

    @staticmethod
    def _removal_hit(candidate, strict, limited):
        # Rank zero (explicit SR/SSR replacement) remains a fallback after
        # searching for N/blacklist/unprotected R. Limited cards themselves
        # require proven duplicates; other limited rules must not disable fast.
        return bool(candidate and ((not strict and candidate["delete_rank"] >= 1) or (not limited and candidate["delete_rank"] == 3)))

    def _verify_delete_selection(self, context, image, selected, blacklist, whitelist, replacement):
        """Read the selected card's upper detail panel before irreversible input."""
        from custom.shop import ocr
        from custom.removal import DECK_TITLE_OCR_MIN_SCORE, selected_card_box_matches

        if not selected_card_box_matches(image, selected["box"]):
            return None
        matches = []
        for row in ocr(context, image, [160, 100, 520, 290], [".*"]):
            if float(row.score) < DECK_TITLE_OCR_MIN_SCORE:
                continue
            matched = match_card_catalog(row.text)
            if matched and matched.confidence == 1.0:
                matches.append(matched)
        keys = {normalize_card_name(item.matched_name) for item in matches}
        if keys != {selected["name_key"]}:
            return None
        matched = matches[0]
        verified = dict(selected, name=matched.matched_name, rarity=matched.rarity, name_confidence=1.0, identity_verified=True)
        return self._choose_delete_candidate([verified], blacklist, whitelist, replacement)

    def _probe_removal_candidates(self, context, candidates, blacklist, whitelist, replacement, *, change=False, strict=True, incumbent=None):
        from custom.removal import read_selected_card, selected_card_box_matches
        from custom.deck_scan import scan_identity_known, removal_priority_bound

        self._deck_probe_count = 0
        limited = single_copy_cards()
        verified = [dict(item) for item in candidates]
        choose = self._choose_removal_candidate if change else self._choose_delete_candidate
        known = choose([item for item in verified if scan_identity_known(item)], blacklist, whitelist, replacement)
        if self._removal_hit(known, strict, limited):
            return verified
        # Strong art + frame evidence is sufficient for scanning. Only unclear
        # identities need detail probes; any unknown may conceal a priority N.
        # This does not bypass the final selected-card name check.
        potential = []
        for index, item in enumerate(verified):
            if item.get("selectable") is False:
                continue
            if scan_identity_known(item):
                continue
            tentative = dict(item, name_confidence=1.0, rarity_confidence=1.0)
            policy = choose([tentative], blacklist, whitelist, replacement)
            if item.get("frame_rarity") == "N" and item.get("rarity_confidence", 0) >= 0.55:
                # A confident frame is a probe ordering hint, never identity
                # proof. A wrong illustration match must not hide a starter.
                potential.append(((4, 0, 0, -item["box"][1], -item["box"][0]), index))
            elif policy:
                potential.append((self._removal_key(policy), index))
            else:
                potential.append((({"N": 3, "R": 1}.get(item.get("rarity"), 0), 0, 0, -item["box"][1], -item["box"][0]), index))
        for _, index in sorted(potential, reverse=True):
            item = verified[index]
            known = choose([c for c in verified if scan_identity_known(c)], blacklist, whitelist, replacement)
            if not strict and self._removal_hit(known, strict, limited):
                return verified
            leaders = [c for c in (known, incumbent) if c is not None]
            best_tier = max((self._removal_key(c)[:2] for c in leaders), default=(-1, 0))
            if not limited and removal_priority_bound(item, blacklist, replacement, change=change) <= best_tier:
                # A reliable SR/SSR frame cannot conceal an N or R. Names
                # only need probing when blacklist/replacement membership
                # could beat the current candidate, not to label every tile.
                continue
            if context.tasker.stopping:
                break
            cache = getattr(self, "_deck_identity_cache", None)
            page_image = getattr(self, "_deck_image", None)
            if cache is not None and page_image is not None:
                restored = cache.restore(page_image, [item])[0]
                if restored.get("identity_verified"):
                    verified[index] = restored
                    cached_policy = choose([restored], blacklist, whitelist, replacement)
                    if self._removal_hit(cached_policy, strict, limited):
                        return verified
                    continue
            for previous in verified:
                previous["detail_selected"] = False
            self._click_product(context, item["box"])
            self._deck_probe_count += 1
            exact, stable = None, 0
            for _ in range(4):
                if context.tasker.stopping:
                    return verified
                time.sleep(0.25)
                image = context.tasker.controller.post_screencap().wait().get()
                if shared._detect_disconnect_screen(context, image):
                    self._disconnect_detected = True
                    return []
                if not selected_card_box_matches(image, item["box"]):
                    exact, stable = None, 0
                    if _ == 1:
                        # A swallowed click must not attach the preceding
                        # detail to this tile. One bounded retry, then reject.
                        self._click_product(context, item["box"])
                        self._deck_probe_count += 1
                    continue
                current = read_selected_card(context, image, change=change)
                stable = stable + 1 if current and current == exact else 1 if current else 0
                exact = current
                if stable >= 2:
                    verified[index] = {**item, **exact, "detail_selected": True, "probe_failed": False}
                    if cache is not None and page_image is not None:
                        cache.remember(page_image, [verified[index]])
                    logger.debug(f"换出/删除详情核实: visual={item['name']} exact={exact['name']} rarity={exact['rarity']}")
                    policy = choose([verified[index]], blacklist, whitelist, replacement)
                    if self._removal_hit(policy, strict, limited):
                        return verified
                    break
            if stable < 2:
                verified[index]["name_confidence"] = 0.0
                verified[index]["probe_failed"] = True
                if not context.tasker.stopping:
                    shared._save_card_diagnostic(image, "deck-detail-unconfirmed", [dict(item)])
                logger.debug(f"换出/删除候选详情未确认，保护此牌: name={item['name']} box={item['box']}")
        return verified

    @classmethod
    def _comment_bubble_present(cls, image) -> bool:
        """商店页底部是否有偶像评论气泡（大面积纯白面板盖住退出按钮）。

        实测气泡出现时该区域白色占比约 0.93，正常商品页为 0.0。"""
        x, y, width, height = cls.COMMENT_BUBBLE_BOX
        frame_height, frame_width = image.shape[:2]
        crop = image[
            round(y * frame_height / 1280) : round((y + height) * frame_height / 1280),
            round(x * frame_width / 720) : round((x + width) * frame_width / 720),
            :3,
        ]
        if not crop.size:
            return False
        return float((crop.min(axis=2) > 238).mean()) >= 0.5

    def _dismiss_comment_bubble(self, context: Context) -> bool:
        """点掉商店底部的偶像评论气泡，避免盖住「✕終了」退出按钮。

        白面板检测本身不区分页面（奖励页/详情页底部也是白色面板），而
        _is_shop_scene 的价格药丸兜底又会被气泡干扰，因此这里直接用商店
        头部模板（ProduceShoppingFlag，气泡盖不到的 ROI）确认场景后再点击。"""
        from .rewards import ProduceChooseRewardAuto

        for attempt in range(3):
            if context.tasker.stopping:
                return False
            image = context.tasker.controller.post_screencap().wait().get()
            if shared._detect_disconnect_screen(context, image):
                self._disconnect_detected = True
                return False
            if not self._comment_bubble_present(image):
                return True
            detail = context.run_recognition("ProduceShoppingFlag", image, pipeline_override={"ProduceShoppingFlag": {"focus": {}}})
            if not (detail and detail.hit):
                return False
            if attempt == 0:
                logger.info("咨询：检测到偶像评论气泡盖住退出按钮，点击关闭")
            else:
                logger.debug(f"咨询评论气泡关闭重试: attempt={attempt + 1}")
            ProduceChooseRewardAuto._click_reward_point(context, image, self.COMMENT_BUBBLE_TAP_POINT)
            time.sleep(0.6)
        image = context.tasker.controller.post_screencap().wait().get()
        if context.tasker.stopping:
            return False
        if shared._detect_disconnect_screen(context, image):
            self._disconnect_detected = True
            return False
        if self._comment_bubble_present(image):
            logger.warning("咨询气泡三次点击后仍未关闭，交回页面识别，暂不购买或退出")
            return False
        return True

    @staticmethod
    def _delete_card_boxes(context: Context, image) -> list:
        detail = context.run_recognition("ProduceRecognitionCards", image)
        if not (detail and detail.hit):
            return []
        # "useless" is a battle-playability label, not a deck-removal rule.
        # Read all complete square tiles; selected details decide eligibility.
        candidates = []
        for result in detail.all_results:
            if result.label not in ("cards", "suggestions", "useless"):
                continue
            box = [int(value) for value in result.box]
            # Do not deduplicate raw oversized boxes: they may overlap a real
            # adjacent tile. Geometry repair/filtering precedes deduplication.
            candidates.append({"box": box})
        # Normalize to real card edges before filtering. The transparent shop
        # behind this pane can stretch a detector box over its price strip;
        # rejecting that raw box first loses an otherwise complete card.
        from custom.deck_scan import normalize_deck_boxes

        return normalize_deck_boxes(image, [item["box"] for item in candidates], top=380)

    def _recognize_delete_cards(self, context: Context, image, *, verified_boxes=None) -> list:
        from .rewards import ProduceChooseRewardAuto

        started = time.perf_counter()
        boxes = self._delete_card_boxes(context, image) if verified_boxes is None else verified_boxes
        evaluated = ProduceChooseRewardAuto()._evaluate(context, image, verified_boxes=boxes, read_extra_action=False, diagnostics=False)
        from custom.square_identity import resolve_selected

        evaluated = resolve_selected(context, image, evaluated)
        for item in evaluated:
            logger.debug(
                f"咨询删除候选: box={item['box']} name={item['name']} "
                f"name_confidence={item['name_confidence']:.3f} rarity={item['rarity']} "
                f"rarity_confidence={item['rarity_confidence']:.3f}"
            )
        from custom.deck_scan import scan_identity_known

        logger.debug(
            f"删除整页识别：{sum(scan_identity_known(i) for i in evaluated)}/{len(evaluated)} 张已确认，"
            f"匹配耗时={time.perf_counter() - started:.3f}s；其余才使用详情兜底"
        )
        return evaluated

    @staticmethod
    def _ocr_card_name(context: Context, image, box: list) -> tuple:
        x, y, width, height = box
        roi = [x, y + int(height * 0.70), width, max(28, int(height * 0.30))]
        detail = context.run_recognition(
            "ProduceRecognitionCardNameFallback",
            image,
            pipeline_override={
                "ProduceRecognitionCardNameFallback": {
                    "recognition": "OCR",
                    "roi": roi,
                    "expected": [],
                    "order_by": "Horizontal",
                }
            },
        )
        if not (detail and detail.hit):
            return "未知", "", 0.0
        pieces = []
        for result in list(detail.filtered_results or detail.all_results or []):
            result_box = list(getattr(result, "box", []) or [])
            result_x = int(result_box[0]) if len(result_box) == 4 else 0
            pieces.append((result_x, str(getattr(result, "text", ""))))
        raw = "".join(text for _, text in sorted(pieces))
        matched = match_card_catalog(raw)
        if matched is None:
            return "未知", "", 0.0
        return (
            matched.matched_name,
            normalize_card_name(matched.matched_name),
            matched.confidence,
        )

    @staticmethod
    def _click_product(context: Context, box: list) -> None:
        context.tasker.controller.post_click(box[0] + box[2] // 2, box[1] + box[3] // 2).wait()

    def _close_detail_and_reopen(self, context: Context) -> bool:
        """Keep scanning the unchanged list after an unavailable item."""
        return self._reenter_shop(context)

    @classmethod
    def _is_shop_scene(cls, context: Context, image) -> bool:
        detail = context.run_recognition("ProduceShoppingFlag", image, pipeline_override={"ProduceShoppingFlag": {"focus": {}}})
        if detail and detail.hit:
            return True
        # Selecting an item replaces the blank shop header, so the original
        # flag disappears even though the unchanged product grid is still on
        # screen.  The inline exchange/full buttons are reliable detail-state
        # markers and must not trigger a desk re-entry attempt.
        for marker in ("ProduceShoppingDrinkFull", "ProduceShoppingBuy"):
            detail = context.run_recognition(marker, image)
            if detail and detail.hit:
                return True
        # The old fallback ran 28 feature templates and cost 12-15 seconds on
        # every check.  Eight fixed white price pills identify the same product
        # grid in well under a millisecond, including sold-out/detail states.
        return cls._looks_like_shop_grid(image)

    def _reenter_shop(self, context: Context) -> bool:
        """Return from the consultation desk to the item exchange screen.

        A confirmed exchange closes the product list and returns to the desk.
        The notebook in the middle of that desk is the exchange entrance.  The
        fixed controller coordinate was verified from the live 720x1280 feed.
        """
        image = context.tasker.controller.post_screencap().wait().get()
        disconnect_node = shared._detect_disconnect_screen(context, image)
        if disconnect_node:
            self._disconnect_detected = True
            logger.warning(f"咨询重进商店时检测到断线界面 {disconnect_node}，立即交回全局恢复流程")
            return False
        if self._is_shop_scene(context, image):
            # The list sometimes stays open after an ordinary card exchange.
            # Its header and buttons are already recognisable while the tiles
            # are still fading/reflowing; scanning immediately can miss the
            # next SR/SSR card.  Use the same settle delay as a desk re-entry.
            time.sleep(0.9)
            return True
        for click_attempt in range(2):
            if context.tasker.stopping:
                return False
            time.sleep(0.8)
            context.tasker.controller.post_click(360, 680).wait()
            for _ in range(5):
                time.sleep(0.6)
                image = context.tasker.controller.post_screencap().wait().get()
                disconnect_node = shared._detect_disconnect_screen(context, image)
                if disconnect_node:
                    self._disconnect_detected = True
                    logger.warning(f"咨询重进商店时检测到断线界面 {disconnect_node}，立即交回全局恢复流程")
                    return False
                if self._is_shop_scene(context, image):
                    # Product tiles fade in after the header.  Scanning on the
                    # first header frame can incorrectly report no cards.
                    time.sleep(0.9)
                    logger.debug(f"咨询已确认商品页: attempt={click_attempt + 1}")
                    return True
        return False

    def _recognize_drinks(self, context: Context, image, blocked_boxes: list) -> list:
        from custom.shop import TARGET_DRINK_NAMES
        from custom.drink_visual import match as match_drink

        found = []
        for box in self.DRINK_SLOTS:
            if any(intersection_over_union(box, blocked) > 0.30 for blocked in blocked_boxes):
                continue
            if self._slot_is_sold(image, box):
                continue
            visual = match_drink(image, box)
            if visual is not None:
                if visual["id"] in TARGET_NAMES:
                    found.append({**visual, "price": self._recognize_price(context, image, box), "identity_score": visual["score"]})
                continue
            identities = []
            for drink_id in TARGET_NAMES:
                template = f"produce/drinks/{drink_id}.png"
                detail = context.run_recognition(
                    "ProduceRecognitionShopDrinks",
                    image,
                    pipeline_override={
                        "ProduceRecognitionShopDrinks": {
                            "recognition": "FeatureMatch",
                            "roi": box,
                            "template": [template],
                            "count": 8,
                            "ratio": 0.62,
                            "order_by": "Score",
                        }
                    },
                )
                for result in list(detail.filtered_results or []) if detail and detail.hit else []:
                    hit_box = list(result.box)
                    count = int(result.count)
                    if intersection_over_union(box, hit_box) >= 0.55:
                        identities.append((count, drink_id))
            identities.sort(reverse=True)
            if not identities or (len(identities) > 1 and identities[0][0] < identities[1][0] + 4):
                continue
            count, drink_id = identities[0]
            price = self._recognize_price(context, image, box)
            found.append({"box": list(box), "price": price, "id": drink_id, "name": TARGET_DRINK_NAMES[drink_id], "identity_score": count})
        logger.info("咨询饮料一次扫描: " + ", ".join(f"{item['name']} x={item['box'][0]} price={item['price']}" for item in found))
        return found

    def _read_owned_drinks(self, context):
        from custom.shop import TARGET_DRINK_NAMES, ocr, shop_occupied, owned_detail_title
        from custom.drink_visual import INVENTORY_BOXES, scan
        from custom.drink_visual import match as match_drink

        from .rewards import ProduceChooseRewardAuto

        image = context.tasker.controller.post_screencap().wait().get()
        occupied = shop_occupied(context, image)
        visual_before = scan(image, INVENTORY_BOXES)
        time.sleep(0.35)
        image = context.tasker.controller.post_screencap().wait().get()
        if occupied != shop_occupied(context, image):
            return None
        owned = {}
        for slot in sorted(occupied):
            if context.tasker.stopping or shared._detect_disconnect_screen(context, image):
                self._disconnect_detected = bool(shared._detect_disconnect_screen(context, image))
                return None
            visual = match_drink(image, INVENTORY_BOXES[slot])
            if visual and visual_before.get(slot, {}).get("id") == visual["id"]:
                owned[slot] = {
                    "id": visual["id"] if visual["id"] in TARGET_DRINK_NAMES else None,
                    "name": visual["name"],
                    "verified": True,
                    "visual_trusted": True,
                }
                continue
            context.tasker.controller.post_click(*ProduceChooseRewardAuto.INVENTORY_SLOT_CENTERS[slot]).wait()
            name, previous, stable, cancel = None, None, 0, []
            for _ in range(5):
                if context.tasker.stopping:
                    return None
                time.sleep(0.3)
                image = context.tasker.controller.post_screencap().wait().get()
                if shared._detect_disconnect_screen(context, image):
                    self._disconnect_detected = True
                    return None
                name = owned_detail_title(context, image)
                cancel = ocr(context, image, [60, 1090, 290, 150], [r"^[×✕Xx]?\s*キャンセル$"])
                stable = stable + 1 if name and name == previous and len(cancel) == 1 else 1 if name and len(cancel) == 1 else 0
                previous = name
                if stable >= 2:
                    break
            if len(cancel) != 1:
                logger.warning("商店持有饮料详情未确认，停止腾位，不点击捨てる")
                return None
            x, y, w, h = cancel[0].box
            context.tasker.controller.post_click(x + w // 2, y + h // 2).wait()
            time.sleep(0.4)
            image = context.tasker.controller.post_screencap().wait().get()
            if stable < 2:
                logger.warning(f"商店第{slot + 1}瓶名称未确认，保护该饮料")
                return None
            drink_id = next((key for key, value in TARGET_DRINK_NAMES.items() if value == name), None)
            owned[slot] = {"id": drink_id, "name": name, "verified": True}
        if owned:
            logger.info("商店持有饮料: " + "/".join(item["name"] for item in owned.values()))
        return owned

    def _make_drink_space(self, context, wanted_id, *, non_target_only=False):
        from custom.shop import shop_occupied, choose_discard, owned_throw_box, read_discard_confirmation

        from .rewards import ProduceChooseRewardAuto

        owned = getattr(self, "_owned_drinks", None)
        if owned is None:
            owned = self._read_owned_drinks(context)
            self._owned_drinks = owned
        if owned is None:
            self._inventory_read_failed = True
            return False
        slot = choose_discard(owned or {}, wanted_id, non_target_only=non_target_only)
        if slot is None:
            return False
        expected_name = owned[slot]["name"]
        image = context.tasker.controller.post_screencap().wait().get()
        if context.tasker.stopping or shared._detect_disconnect_screen(context, image):
            self._disconnect_detected = bool(shared._detect_disconnect_screen(context, image))
            return False
        context.tasker.controller.post_click(*ProduceChooseRewardAuto.INVENTORY_SLOT_CENTERS[slot]).wait()
        time.sleep(0.65)
        image = context.tasker.controller.post_screencap().wait().get()
        throw = owned_throw_box(context, image, expected_name)
        if throw is None:
            logger.warning("商店腾位饮料身份或捨てる按钮未确认，停止丢弃")
            self._inventory_read_failed = True
            return False
        x, y, w, h = throw
        context.tasker.controller.post_click(x + w // 2, y + h // 2).wait()
        confirmed, previous = None, None
        for _ in range(5):
            if context.tasker.stopping:
                return False
            time.sleep(0.3)
            image = context.tasker.controller.post_screencap().wait().get()
            if shared._detect_disconnect_screen(context, image):
                self._disconnect_detected = True
                return False
            confirmed = read_discard_confirmation(context, image, expected_name)
            if confirmed and previous and confirmed["name"] == previous["name"]:
                break
            previous = confirmed
        else:
            confirmed = None
        if confirmed is None:
            logger.warning("商店廃棄确认内容不匹配，停止丢弃")
            self._inventory_read_failed = True
            return False
        x, y, w, h = confirmed["yes"]
        context.tasker.controller.post_click(x + w // 2, y + h // 2).wait()
        stable = 0
        for _ in range(8):
            if context.tasker.stopping:
                return False
            time.sleep(0.35)
            image = context.tasker.controller.post_screencap().wait().get()
            if shared._detect_disconnect_screen(context, image):
                self._disconnect_detected = True
                return False
            settled = len(shop_occupied(context, image)) == 3 and not read_discard_confirmation(context, image, expected_name)
            stable = stable + 1 if settled else 0
            if stable >= 2:
                break
        else:
            logger.warning("商店未确认饮料栏位减少，不重复丢弃")
            self._inventory_read_failed = True
            return False
        logger.success(f"商店饮料腾位: 丢弃{expected_name}，准备补入目标饮料")
        self._owned_drinks = None
        return True

    @staticmethod
    def _card_purchase_key(item):
        return (cultivation_priority(item), -(item["price"] if item["price"] is not None else 9999), -item["box"][0])

    def _recognize_shop_cards(self, context: Context, image, blocked_boxes: list) -> list:
        from .battle import ProduceCardsAuto

        evaluated = []
        for index, box in enumerate(self.CARD_SLOTS, start=1):
            if any(intersection_over_union(box, blocked) > 0.30 for blocked in blocked_boxes):
                continue
            if self._slot_is_sold(image, box):
                continue
            rarity = classify_rarity(image, box)
            extra, extra_score, _ = ProduceCardsAuto._extra_action_status(context, image, box)
            from custom.square_identity import evidence as square_evidence
            from custom.square_identity import recognize as recognize_square

            square = recognize_square(image, box, rarity)
            visual = square
            resolved_rarity = visual.rarity if visual is not None else rarity.rarity
            if visual is not None and visual.extra_action:
                extra = "yes"
            item = {
                "box": list(box),
                "label": "shop-slot",
                **square_evidence(square),
                "square_art_attempted": True,
                "detector_score": 1.0,
                "name": visual.name if visual is not None else "未知",
                "name_key": normalize_card_name(visual.name) if visual is not None else "",
                "name_confidence": visual.confidence if visual is not None else 0.0,
                "rarity": resolved_rarity,
                "rarity_confidence": rarity.confidence,
                "rarity_distance": rarity.best_distance,
                "rarity_margin": rarity.margin,
                "frame_bgr": [round(value * 255) for value in rarity.signature],
                "extra_action": extra,
                "extra_score": extra_score,
                "priority": rarity_priority(resolved_rarity, extra),
                "price": self._recognize_price(context, image, box),
            }
            evaluated.append(item)
            shared._diagnostic_info(
                f"咨询卡牌识别[{index}] box={box} name={item['name']} "
                f"rarity={resolved_rarity} price={item['price']} "
                f"confidence={rarity.confidence:.3f} distance={rarity.best_distance:.3f} "
                f"margin={rarity.margin:.3f} frame_bgr={item['frame_bgr']} extra_action={extra} "
                f"extra_score={extra_score:.3f} priority={item['priority']}"
            )
        if any(item["rarity"] == "unknown" or item["rarity_confidence"] < 0.15 or 0.46 < item["extra_score"] < 0.52 for item in evaluated):
            shared._save_card_diagnostic(image, "shop", evaluated)
        return evaluated

    def _resolve_shop_card_identities(self, context, cards):
        """Probe only rejected tiles; normal whole-page recognition sends no input."""
        from custom.removal import read_selected_card, selected_card_box_matches

        for item in cards:
            if not item.get("square_art_attempted") or item.get("square_art_trusted") or item.get("identity_verified"):
                continue
            if context.tasker.stopping:
                break
            image = context.tasker.controller.post_screencap().wait().get()
            if shared._detect_disconnect_screen(context, image):
                self._disconnect_detected = True
                break
            if not self._is_shop_scene(context, image) or self._slot_is_sold(image, item["box"]):
                break
            self._click_product(context, item["box"])
            previous, stable = None, 0
            for _ in range(4):
                if context.tasker.stopping:
                    break
                time.sleep(0.25)
                image = context.tasker.controller.post_screencap().wait().get()
                if shared._detect_disconnect_screen(context, image):
                    self._disconnect_detected = True
                    return cards
                # Never attach stale detail from the preceding selected slot.
                if not selected_card_box_matches(image, item["box"]):
                    previous, stable = None, 0
                    continue
                current = read_selected_card(context, image)
                stable = stable + 1 if current and current == previous else 1 if current else 0
                previous = current
                if stable >= 2:
                    item.update(current)
                    item["priority"] = rarity_priority(item["rarity"], item.get("extra_action", "unknown"))
                    logger.info(f"商店卡图兜底确认：{item['name']} box={item['box']}")
                    break
            if stable < 2:
                logger.warning(f"商店卡图与详情均未确认，跳过此牌：box={item['box']}")
        return cards

    def _purchase(
        self,
        context: Context,
        box: list,
        item_type: str,
        *,
        is_drink: bool = False,
        drink_id: Optional[str] = None,
        expected_price: Optional[int] = None,
    ) -> str:
        if context.tasker.stopping:
            return "stopped"
        image = context.tasker.controller.post_screencap().wait().get()
        if shared._detect_disconnect_screen(context, image):
            self._disconnect_detected = True
            return "disconnected"
        if self._comment_bubble_present(image) and not self._dismiss_comment_bubble(context):
            return "disconnected" if self._disconnect_detected else "unavailable"
        before_points = self._recognize_points(context, image) if expected_price is not None else None
        if expected_price is not None and not self._is_affordable({"price": expected_price}, before_points):
            logger.warning("咨询即时积分或价格未确认，不点击购买")
            return "no-funds" if before_points is not None and before_points < expected_price else "unavailable"
        context.tasker.controller.post_click(box[0] + box[2] // 2, box[1] + box[3] // 2).wait()
        time.sleep(0.5)
        image = context.tasker.controller.post_screencap().wait().get()
        disconnect_node = shared._detect_disconnect_screen(context, image)
        if disconnect_node:
            self._disconnect_detected = True
            logger.warning(f"咨询点击商品后检测到断线界面 {disconnect_node}，立即交回全局恢复流程")
            return "disconnected"

        if is_drink:
            from custom.shop import selected_drink_matches

            if not selected_drink_matches(context, image, drink_id):
                logger.warning(f"咨询目标饮料标题未确认，取消购买: id={drink_id} box={box}")
                return "unavailable"
            full = context.run_recognition("ProduceShoppingDrinkFull", image)
            if full and full.hit:
                logger.info("目标饮料身份已确认，持有数已满，停止购买")
                # Capacity-full is an inline disabled exchange button, not a
                # modal dialog.  The same card/drink list is still clickable.
                return "full"

        buy = context.run_recognition("ProduceShoppingBuy", image)
        if not (buy and buy.hit and buy.best_result):
            logger.info(f"咨询购买失败: 未出现购买按钮, type={item_type}, box={box}")
            return "unavailable"
        buy_box = buy.best_result.box
        context.tasker.controller.post_click(buy_box[0] + buy_box[2] // 2, buy_box[1] + buy_box[3] // 2).wait()
        time.sleep(0.5)

        image = context.tasker.controller.post_screencap().wait().get()
        disconnect_node = shared._detect_disconnect_screen(context, image)
        if disconnect_node:
            self._disconnect_detected = True
            logger.warning(f"咨询购买确认时检测到断线界面 {disconnect_node}，立即交回全局恢复流程")
            return "disconnected"
        point_runout = context.run_recognition(
            "ProduceShoppingDecide",
            image,
            pipeline_override={"ProduceShoppingDecide": {"template": "produce/point_runout.png"}},
        )
        if point_runout and point_runout.hit and point_runout.best_result:
            decide_box = point_runout.best_result.box
            context.tasker.controller.post_click(
                decide_box[0] + decide_box[2] // 2,
                decide_box[1] + decide_box[3] // 2,
            ).wait()
            time.sleep(0.7)
            logger.info("咨询余额不足，停止后续购买")
            return "no-funds"

        decide = context.run_recognition(
            "ProduceShoppingDecide",
            image,
            pipeline_override={"ProduceShoppingDecide": {"template": "produce/shopping_exchange.png"}},
        )
        if decide and decide.hit and decide.best_result:
            from custom.shop import ocr

            exchange_header = ocr(context, image, [15, 520, 680, 470], ["交換確認"]) if is_drink else True
            if is_drink and (not exchange_header or not selected_drink_matches(context, image, drink_id, confirmation=True)):
                logger.warning("咨询确认框饮料身份不一致，取消交换")
                # Cancellation is confined to the exchange dialog footer.
                context.run_task("ProduceNIAButton")
                return "uncertain"
            decide_box = decide.best_result.box
            context.tasker.controller.post_click(
                decide_box[0] + decide_box[2] // 2,
                decide_box[1] + decide_box[3] // 2,
            ).wait()
            # Rare drinks show a full-screen acquisition description after
            # exchange.  A neutral tap closes it; on the consultation desk the
            # same coordinate is inert.  This prevents the overlay from being
            # mistaken for another product state.
            time.sleep(1.1)
            after_points, confirmed = None, 0
            for _ in range(10):
                if context.tasker.stopping:
                    return "stopped"
                image = context.tasker.controller.post_screencap().wait().get()
                if shared._detect_disconnect_screen(context, image):
                    self._disconnect_detected = True
                    return "disconnected"
                after_points = self._recognize_points(context, image)
                confirmed = confirmed + 1 if before_points is not None and after_points == before_points - expected_price else 0
                if confirmed >= 2 or expected_price is None:
                    break
                # The acquisition sheet can hide the balance until dismissed.
                # Waiting for the hidden balance before closing that sheet is
                # circular. Only this confirmed exchange owns these corner taps.
                if not self._is_shop_scene(context, image):
                    context.tasker.controller.post_click(22, 22).wait()
                time.sleep(0.35)
            if expected_price is not None and confirmed < 2:
                logger.warning(f"咨询未确认扣款，停止重购: before={before_points} after={after_points} price={expected_price}")
                return "uncertain"
            # Only after a verified debit, close the acquisition description.
            context.tasker.controller.post_click(360, 360).wait()
            time.sleep(0.7)
            logger.success(f"咨询购买完成: type={item_type}, item_box={box}")
            return "bought"

        logger.warning(f"咨询购买结果未知: 未识别到兑换确认或余额不足, type={item_type}, box={box}")
        return "uncertain"


class ProduceHIFShoppingAuto(ProduceShoppingAuto):
    """HIF consultation lifecycle: stable products or hand back without blind input."""

    _shop_departed = False

    @staticmethod
    def _allow_shop_delete(context):
        return (context.get_node_data("ProduceHIFStrategy") or {}).get("attach", {}).get("shop_delete", False) is True

    @classmethod
    def _is_shop_scene(cls, context, image):
        from custom.shop import consultation_grid_visible

        return consultation_grid_visible(image)

    def _wait_shop_ready(self, context):
        from custom.shop import stock_signature

        previous = None
        absent = taps = 0
        self._shop_departed = False
        for _ in range(10):
            if context.tasker.stopping:
                return None
            image = context.tasker.controller.post_screencap().wait().get()
            if shared._detect_disconnect_screen(context, image):
                self._disconnect_detected = True
                return None
            if self._is_shop_scene(context, image):
                current = stock_signature(image, self.CARD_SLOTS + self.DRINK_SLOTS)
                if previous is not None and float(np.abs(current - previous).mean()) < 3:
                    return image
                previous, absent = current, 0
            else:
                previous = None
                flag = context.run_recognition("ProduceShoppingFlag", image, pipeline_override={"ProduceShoppingFlag": {"focus": {}}})
                if not (flag and flag.hit):
                    absent += 1
                    if absent >= 2:
                        self._shop_departed = True
                        logger.debug("HIF咨询页面已离开或正在转场，交回培育主流程")
                        return None
                else:
                    absent = 0
                    if taps < 2:
                        context.tasker.controller.post_click(360, 360).wait()
                        taps += 1
            time.sleep(0.25)
        logger.warning("HIF咨询商品区仍未稳定，保留现场供重识别")
        return None

    def _reenter_shop(self, context):
        from custom.shop import read_consultation_desk_entry

        consecutive = absent = 0
        previous_desk = None
        clicked = False
        self._shop_departed = False
        for _ in range(12):
            if context.tasker.stopping:
                return False
            image = context.tasker.controller.post_screencap().wait().get()
            if shared._detect_disconnect_screen(context, image):
                self._disconnect_detected = True
                return False
            if self._is_shop_scene(context, image):
                absent = 0
                consecutive += 1
                if consecutive >= 2:
                    return True
            else:
                consecutive = 0
                # The HIF product list normally remains open after a purchase.
                # Never assume another page is the legacy consultation desk.
                if not clicked:
                    box = read_consultation_desk_entry(context, image)
                    if box:
                        absent = 0
                        if previous_desk == tuple(box):
                            self._click_product(context, box)
                            clicked = True
                        previous_desk = tuple(box)
                    else:
                        previous_desk = None
                        header = context.run_recognition("ProduceShoppingFlag", image, pipeline_override={"ProduceShoppingFlag": {"focus": {}}})
                        if header and header.hit:
                            # Acquired-item effects can hide prices while the
                            # header remains. A verified desk is handled above.
                            absent = 0
                            time.sleep(0.25)
                            continue
                        absent += 1
                        if absent >= 2:
                            self._shop_departed = True
                            return False
            time.sleep(0.25)
        self._shop_departed = True
        logger.debug("HIF咨询当前无已验证的商品页，交回培育主流程")
        return False


@AgentServer.custom_action("ProduceHIFIntervalShoppingAuto")
class ProduceHIFIntervalShoppingAuto(ProduceShoppingAuto):
    """The HIF interval has six cards and two drinks, and a paid refresh."""

    CARD_SLOTS = [[x, y, 126, 126] for y, xs in ((498, (54, 210, 374, 528)), (690, (54, 210))) for x in xs]
    DRINK_SLOTS = [[374, 690, 126, 126], [528, 690, 126, 126]]
    MAX_REFRESH_ATTEMPTS = 32

    @classmethod
    def _is_shop_scene(cls, context, image):
        from custom.shop import is_interval_shop

        return is_interval_shop(context, image)

    def _heal_before_drinks(self, context, image, *, spend_remaining=False):
        from custom.shop import interval_heal_target, read_interval_health, read_interval_recovery, read_interval_heal_button

        strategy = (context.get_node_data("ProduceHIFStrategy") or {}).get("attach", {})
        threshold = interval_heal_target(strategy.get("interval_heal_target", 20))
        stage = "离店" if spend_remaining else "入店"
        if getattr(self, "_heal_receipt_pending", False) or self._visit_state(context).get("heal_pending"):
            logger.warning("HIF轮间先前回血扣款结果尚未确认，本次离店不重复提交回血")
            return image

        health = read_interval_health(context, image)
        for _ in range(2):
            if health is not None or context.tasker.stopping:
                break
            time.sleep(0.2)
            image = context.tasker.controller.post_screencap().wait().get()
            if shared._detect_disconnect_screen(context, image):
                self._disconnect_detected = True
                return None
            if not self._is_shop_scene(context, image):
                return None
            health = read_interval_health(context, image)
        if context.tasker.stopping:
            return None
        if health is None:
            logger.warning(f"HIF轮间{stage}体力未读到，跳过本次回血检查")
            return image
        current, maximum = health
        logger.info(f"HIF轮间{stage}体力: {current}/{maximum}")
        if spend_remaining:
            threshold = maximum
        if current >= threshold or current == maximum:
            return image
        points = self._recognize_points(context, image)
        button = read_interval_heal_button(context, image)
        if points is None or points < 10 or not button:
            logger.info(f"HIF轮间{stage}回血入口或余额不足，跳过回血: 积分={points}")
            return image
        self._click_product(context, button)
        target = min(threshold, maximum)
        unreadable_quote = 0
        missing_dialog = 0
        unchanged_quote = 0
        deadline = time.monotonic() + 60
        pending = None
        previous_projected = None
        stable_quote = None
        dialog = None
        for _ in range(110):
            if context.tasker.stopping:
                return None
            if time.monotonic() >= deadline:
                logger.warning("HIF轮间回血调节达到时间上限，取消后继续自动流程")
                break
            time.sleep(0.25)
            frame = context.tasker.controller.post_screencap().wait().get()
            if shared._detect_disconnect_screen(context, frame):
                self._disconnect_detected = True
                return None
            dialog = read_interval_recovery(context, frame, health)
            if not dialog:
                missing_dialog += 1
                if missing_dialog >= 6:
                    break
                continue
            missing_dialog = 0
            quote = dialog["quote"]
            if not quote:
                unreadable_quote += 1
                if unreadable_quote < 3:
                    continue
                logger.warning(
                    f"HIF轮间回血预览连续三帧未通过校验：{dialog.get('reason')} "
                    f"字段={dialog.get('fields')} 小框={dialog.get('rois')}，取消本次回血"
                )
                break
            unreadable_quote = 0
            if quote["before"] != points or quote["maximum"] != maximum or quote["projected"] - quote["amount"] != current:
                logger.warning("HIF轮间回血预览与入店体力/余额不一致，取消本次回血")
                break
            signature = (quote["amount"], quote["after"])
            # Budget by purchased two-HP steps, not the displayed gain/cost
            # ratio, which changes when the final step is clipped at full HP.
            affordable_health = min(maximum, current + (points // 10) * 2)
            affordable_target = min(target, affordable_health)
            if affordable_target <= current:
                break
            # A stale animation frame is not evidence that the slider is capped.
            if pending and pending[1] == signature:
                unchanged_quote += 1
                if unchanged_quote == 3:
                    control = quote.get(pending[0])
                    if control:
                        self._click_product(context, control)
                if unchanged_quote >= 6:
                    logger.warning("HIF轮间回血调节重复点击后仍未推进，取消本次回血")
                    break
                continue
            unchanged_quote = 0
            direction = (
                "minus"
                if quote["after"] < 0 or quote["projected"] > affordable_target
                else "plus"
                if quote["projected"] < affordable_target
                else None
            )
            # If the game's step jumps across 20, keep the smallest observed
            # affordable value above it instead of oscillating on the slider.
            if pending and pending[0] == "plus" and previous_projected < target <= quote["projected"] and quote["after"] >= 0:
                direction = None
            step = abs(quote["projected"] - previous_projected) if previous_projected is not None else 0
            # A two-HP step can end below the arithmetic budget limit (e.g.
            # 21 HP + 35P permits +6, not +7). Accept the observed affordable
            # quote when the next proven step would exceed the actual budget.
            if (
                direction == "plus"
                and pending
                and pending[0] == "plus"
                and step
                and quote["after"] >= 0
                and min(maximum, quote["projected"] + step) > affordable_health
            ):
                direction = None
            if direction == "minus" and step and quote["after"] >= 0 and quote["projected"] - step < affordable_target:
                direction = None
            # If one upward step exceeds the budget, accept the best affordable
            # lower step instead of oscillating across the unaffordable value.
            if (
                direction == "plus"
                and pending
                and pending[0] == "minus"
                and step
                and quote["after"] >= 0
                and quote["projected"] + step > affordable_target
                and previous_projected > affordable_health
            ):
                direction = None
            # At low balance the game can hide the adjustment row altogether.
            # On exit, a twice-verified affordable gain is useful even when
            # its + control cannot be located. Never infer missing digits or
            # bypass the quote/receipt checks, and retain the entry HP target.
            if spend_remaining and direction == "plus" and not quote["plus"] and quote["after"] >= 0:
                direction = None
            if direction:
                control = quote[direction]
                if not control or pending == (direction, signature):
                    logger.warning(f"HIF轮间回血调节未就绪: 方向={direction} 预览={quote['projected']} 控件={control}")
                    break
                self._click_product(context, control)
                previous_projected = quote["projected"]
                pending, stable_quote = (direction, signature), None
                continue
            if not quote["confirm"]:
                logger.warning("HIF轮间回血确认按钮未识别，取消本次回血")
                break
            if stable_quote != signature:
                stable_quote = signature
                continue
            reason = "离店前使用剩余积分回血" if spend_remaining else f"体力低于{threshold}，先回血"
            logger.info(f"HIF轮间{reason}: {current}→{quote['projected']}，费用{points - quote['after']}")
            self._heal_receipt_pending = True
            self._visit_state(context, heal_pending=True)
            self._click_product(context, quote["confirm"])
            # Never submit the recovery twice on an unclear receipt.
            for _ in range(10):
                if context.tasker.stopping:
                    return None
                time.sleep(0.3)
                frame = context.tasker.controller.post_screencap().wait().get()
                if shared._detect_disconnect_screen(context, frame):
                    self._disconnect_detected = True
                    return None
                if self._is_shop_scene(context, frame):
                    actual = read_interval_health(context, frame)
                    balance = self._recognize_points(context, frame)
                    if actual == (quote["projected"], maximum) and balance == quote["after"]:
                        self._heal_receipt_pending = False
                        self._visit_state(context, heal_pending=False)
                        following = "准备退出商店" if spend_remaining else "开始拿饮料"
                        logger.success(f"HIF轮间回血完成: {actual[0]}/{maximum}，余额{balance}，{following}")
                        return frame
            break
        # Cancel only this explicitly recognized recovery dialog, then resume
        # shopping. Failure to read HP must not terminate unattended cultivation.
        frame = context.tasker.controller.post_screencap().wait().get()
        if context.tasker.stopping:
            return None
        if shared._detect_disconnect_screen(context, frame):
            self._disconnect_detected = True
            return None
        dialog = read_interval_recovery(context, frame)
        if dialog and dialog["cancel"]:
            self._click_product(context, dialog["cancel"])
        logger.warning("HIF轮间本次回血未完成，取消后继续原流程，不重复扣款")
        return self._wait_shop_ready(context)

    def _refresh_stock(self, context, image, points, refresh):
        from custom.shop import INTERVAL_CARD_SLOTS, stock_signature

        stock_slots = INTERVAL_CARD_SLOTS + self.DRINK_SLOTS
        before = stock_signature(image, stock_slots)
        context.tasker.controller.post_click(*refresh["point"]).wait()
        verified = False
        confirmations = 0
        for _ in range(8):
            if context.tasker.stopping:
                return None
            time.sleep(0.45)
            image = context.tasker.controller.post_screencap().wait().get()
            if shared._detect_disconnect_screen(context, image):
                self._disconnect_detected = True
                return None
            if not self._is_shop_scene(context, image):
                confirmations = 0
                continue
            after_points = self._recognize_points(context, image)
            changed = float(np.abs(stock_signature(image, stock_slots) - before).mean()) > 8
            confirmations = confirmations + 1 if changed and after_points == points - refresh["cost"] else 0
            if confirmations >= 2:
                verified = True
                break
        if not verified:
            logger.warning("HIF轮间刷新未确认库存和扣款变化，不重复点击")
            return None
        return image, after_points

    def run(self, context, argv):
        from custom.shop import read_refresh, refresh_is_affordable

        self._disconnect_detected = False
        if self._visit_state(context).get("exit_pending"):
            return self._finish_optional_shop(context)
        self._heal_receipt_pending = bool(self._visit_state(context).get("heal_pending"))
        self._owned_drinks = None
        self._inventory_read_failed = False
        refreshes, purchases, unreadable = 0, 0, 0
        bought_boxes = []
        health_checked = False
        customization_checked = False
        customization_session = None
        customization_minimum = None
        time.sleep(0.9)
        logger.info("HIF轮间商店: 买最低价目标，允许同种；满栏非目标腾位，全目标退出；无目标则刷新")
        while not context.tasker.stopping:
            # The verified purchase can still leave a fading acquisition veil.
            # Wait for this layout's eight stable price tiles before deciding
            # the shop has disappeared or scanning the next set of products.
            image = self._wait_shop_ready(context)
            if image is None:
                return True if self._disconnect_detected or context.tasker.stopping else self._finish_optional_shop(context)
            if not self._is_shop_scene(context, image):
                logger.warning("HIF轮间商店页面未确认，交回全局识别")
                return self._finish_optional_shop(context)
            verified_shop_image = image
            if not health_checked:
                health_checked = True
                image = self._heal_before_drinks(context, image)
                if image is None:
                    return True  # Let global recovery handle a transition/disconnect.
            if not customization_checked:
                from .shop_customization import CustomizationSession

                customization_checked = True
                try:
                    if customization_session is None:
                        customization_session = CustomizationSession(context, self)
                    image, minimum = customization_session.run(image)
                except (ValueError, KeyError, TypeError, OSError):
                    logger.warning("自定义设置无法读取，本次跳过自定义")
                    minimum = None
                if image is None:
                    return True
                # Finish this stock's target drinks BEFORE spending on refresh.
                customization_minimum = minimum
            points = self._recognize_points(context, image)
            full = self._drink_inventory_full(context, image)
            if full is None:
                return True if self._disconnect_detected or context.tasker.stopping else self._finish_optional_shop(context)
            drinks_complete = False
            if full:
                room = self._full_inventory_has_non_target(context)
                if room is None:
                    return True if self._disconnect_detected or context.tasker.stopping else self._finish_optional_shop(context)
                if not room:
                    drinks_complete = True
            drinks = [] if drinks_complete else self._recognize_drinks(context, image, bought_boxes)
            if points is None or any(item["price"] is None for item in drinks):
                unreadable += 1
                if unreadable <= 3:
                    logger.warning(f"HIF轮间余额或目标价格未识别，重读当前商品（{unreadable}/3），不当作买不起")
                    time.sleep(0.6)
                    continue
                logger.warning("HIF轮间价格重复识别失败，取消本次可选购物并自动离店")
                return self._finish_optional_shop(context)
            unreadable = 0
            available = [item for item in drinks if self._is_affordable(item, points)]
            if available:
                if purchases >= self.MAX_PURCHASE_ATTEMPTS:
                    logger.warning("HIF轮间购买达到安全上限")
                    break
                selected = min(available, key=lambda item: (item["price"], item["box"][0]))
                logger.info(f"HIF轮间最低价目标: {selected['name']} price={selected['price']}")
                if full:
                    if not self._make_drink_space(context, selected["id"], non_target_only=True):
                        return True if self._disconnect_detected or context.tasker.stopping else self._finish_optional_shop(context)
                    image = self._wait_shop_ready(context)
                    if image is None or self._drink_inventory_full(context, image) is not False:
                        return True if self._disconnect_detected or context.tasker.stopping else self._finish_optional_shop(context)
                status = self._purchase(
                    context, selected["box"], selected["name"], is_drink=True, drink_id=selected["id"], expected_price=selected["price"]
                )
                if status == "full":
                    logger.info("HIF轮间商店: 饮料栏已满，停止购买和刷新")
                    break
                if status == "disconnected":
                    return True
                if status != "bought":
                    logger.warning(f"HIF轮间购买未确认: {status}，不重复扣款或刷新")
                    return self._finish_optional_shop(context)
                purchases += 1
                bought_boxes.append(selected["box"])
                self._owned_drinks = None
                time.sleep(0.9)
                continue
            if customization_minimum is not None:
                refresh = read_refresh(context, image, verified_shop_image=verified_shop_image)
                if refresh and points >= refresh["cost"] + customization_minimum and refreshes < self.MAX_REFRESH_ATTEMPTS:
                    refreshed = self._refresh_stock(context, image, points, refresh)
                    if refreshed is None:
                        return True if self._disconnect_detected or context.tasker.stopping else self._finish_optional_shop(context)
                    image, _ = refreshed
                    refreshes += 1
                    bought_boxes.clear()
                    customization_checked = False
                    customization_minimum = None
                    logger.info("本批目标饮料已处理，刷新后继续按顺序自定义目标卡")
                    continue
                if customization_session is not None:
                    customization_session.finished = True
                customization_minimum = None
                logger.info("当前无法支付刷新和下一次自定义，结束本店自定义")
            if drinks_complete:
                logger.info("HIF轮间商店: 满栏均为目标饮料，保留现状并退出")
                break
            if drinks:
                logger.info(f"HIF轮间目标价格已确认但积分不足: 余额={points} 价格={[item['price'] for item in drinks]}，停止刷新和购买")
                break
            refresh = read_refresh(context, image, verified_shop_image=verified_shop_image)
            if not refresh or not refresh_is_affordable(points, refresh["cost"]):
                logger.info(f"HIF轮间无法继续刷新并保留购买预算: 积分={points} 刷新={refresh}")
                break
            if refreshes >= self.MAX_REFRESH_ATTEMPTS:
                logger.warning("HIF轮间刷新达到安全上限，停止")
                break
            refreshed = self._refresh_stock(context, image, points, refresh)
            if refreshed is None:
                return True if self._disconnect_detected or context.tasker.stopping else self._finish_optional_shop(context)
            image, after_points = refreshed
            customization_checked = False
            refreshes += 1
            bought_boxes.clear()
            logger.success(f"HIF轮间刷新确认: 本店{refreshes}次，费用{refresh['cost']}，余额{after_points}")
        if not context.tasker.stopping:
            image = context.tasker.controller.post_screencap().wait().get()
            if shared._detect_disconnect_screen(context, image):
                return True
            if self._is_shop_scene(context, image):
                image = self._heal_before_drinks(context, image, spend_remaining=True)
                if image is None or context.tasker.stopping or self._disconnect_detected:
                    return True
                if self._is_shop_scene(context, image):
                    return self._finish_optional_shop(context)
        return True


@AgentServer.custom_action("ProduceHIFCustomizeRecoveryAuto")
class ProduceHIFCustomizeRecoveryAuto(CustomAction):
    def run(self, context, argv):
        from .shop_customization import CustomizationSession

        shop = ProduceHIFIntervalShoppingAuto()
        shop._disconnect_detected = False
        # Returning from an unknown interrupted submission cannot authorize
        # another purchase. This visit remains blocked until a verified exit.
        shop._visit_state(context, custom_blocked=True)
        logger.info("恢复自定义页面，返回轮间商店；本次不重复提交自定义")
        CustomizationSession(context, shop, recovery=True).leave()
        return True


class GuidanceCustomizationState(ProduceShoppingAuto):
    """Keep guidance receipts separate from interval-shop visit state."""

    @staticmethod
    def _visit_state(context, **updates):
        node = context.get_node_data("ProduceGuideFlag") or {}
        state = dict(node.get("attach", {}).get("customization_state", {}))
        if updates:
            state.update(updates)
            context.override_pipeline({"ProduceGuideFlag": {"attach": {"customization_state": state}}})
        return state

    @classmethod
    def _is_shop_scene(cls, context, image):
        return False


@AgentServer.custom_action("ProduceGuideCustomizeAuto")
class ProduceGuideCustomizeAuto(CustomAction):
    def run(self, context, argv):
        from .shop_customization import CustomizationSession

        mode = action_mode(getattr(argv, "custom_action_param", ""))
        if mode == "reset":
            GuidanceCustomizationState._visit_state(context, custom_pending=False, custom_blocked=False, active=True)
            return True
        if mode == "done":
            GuidanceCustomizationState._visit_state(context, active=False)
            return True
        GuidanceCustomizationState._visit_state(context, active=True)
        try:
            CustomizationSession(context, GuidanceCustomizationState()).run_guidance()
        except (ValueError, KeyError, TypeError, OSError):
            logger.warning("特别指导设置无法读取，结束本次指导")
        return True


@AgentServer.custom_action("ProduceHIFDeleteAuto")
class ProduceHIFDeleteAuto(ProduceHIFShoppingAuto):
    """Resume shop deletion without the legacy first-card click route."""

    def run(self, context, argv):
        from custom.shop import ocr

        self._disconnect_detected = False
        if context.tasker.stopping:
            return True
        image = context.tasker.controller.post_screencap().wait().get()
        if shared._detect_disconnect_screen(context, image):
            return True
        # A modal left by an interrupted/old task must be cancelled before
        # scanning all priorities. Its existing selection is not authority.
        if ocr(context, image, [15, 450, 690, 400], [r"^スキルカード削除$"]):
            cancel = ocr(context, image, [65, 1080, 300, 160], [r"^[×✕xX]?\s*キャンセル$"])
            if len(cancel) != 1:
                logger.warning("咨询残留删除弹窗取消按钮未完整确认，保护卡牌并保留现场")
                return False
            self._click_product(context, list(cancel[0].box))
            time.sleep(0.5)
            image = context.tasker.controller.post_screencap().wait().get()
            if ocr(context, image, [15, 450, 690, 400], [r"^スキルカード削除$"]):
                return False
        lists = shared._load_card_lists()
        sets = [
            set(lists.get(key, {}).get("cards", [])) if lists.get(key, {}).get("enabled") else set()
            for key in ("blacklist", "whitelist", "replacementList")
        ]
        status = self._delete_from_deck(context, *sets) if self._allow_shop_delete(context) else self._cancel_delete(context)
        if status in ("disconnected", "cancel_pending") or context.tasker.stopping:
            return True
        if status not in ("deleted", "skipped"):
            return False
        if not self._dismiss_comment_bubble(context):
            return True
        if not self._reenter_shop(context):
            return bool(self._disconnect_detected or self._shop_departed or context.tasker.stopping)
        self._skip_delete = True
        return super().run(context, argv)


@AgentServer.custom_action("ProduceHIFEnterShopAuto")
class ProduceHIFEnterShopAuto(ProduceHIFAttributeBase):
    """Enter the daily shop tile; the shop's existing policy takes over later."""

    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        from custom.hif import read_hif_shop_entry

        previous = before = None
        clicks = missing = 0
        deadline = time.monotonic() + 14
        while time.monotonic() < deadline and not context.tasker.stopping:
            image = context.tasker.controller.post_screencap().wait().get()
            if shared._detect_disconnect_screen(context, image):
                return True
            box = read_hif_shop_entry(context, image)
            if box is None:
                if before is None:
                    return True
                changed = (
                    before.shape == image.shape
                    and np.abs(image[920:1110:4, 40:680:4].astype(np.float32) - before[920:1110:4, 40:680:4].astype(np.float32)).mean() > 8
                )
                missing = missing + 1 if changed else 0
                if missing >= 2:
                    logger.success("HIF咨询入口：已确认页面推进，交回商店策略")
                    return True
            else:
                missing = 0
                signature = tuple(box)
                if previous != signature:
                    previous = signature
                    time.sleep(0.45)
                    continue
                if clicks >= 3:
                    break
                if context.tasker.stopping:
                    return True
                if clicks == 0:
                    # The schedule tile was independently seen twice. This
                    # is a new visit even if its stock matches the old shop.
                    ProduceShoppingAuto._reset_visit(context)
                self._click_box(context, box, double=False)
                clicks += 1
                before, previous = image.copy(), None
                logger.info(f"HIF咨询入口：点击日程图块 box={box}，等待进入商店")
            time.sleep(0.65)
        if context.tasker.stopping:
            return True
        logger.warning("HIF咨询入口：重试后仍未确认页面推进，保留现场")
        return False


@AgentServer.custom_action("ProduceHIFShopAuto")
class ProduceHIFShopAuto(CustomAction):
    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        return ProduceHIFShoppingAuto().run(context, argv)

"""Cultivation rewards; behavior moved from the original action module."""

import time
from typing import Optional

import numpy as np
from utils import logger
from maa.context import Context
from custom.card_rules import single_copy_cards, cultivation_rarity, cultivation_priority, cultivation_extra_action
from maa.custom_action import CustomAction
from custom.card_copies import needs_deck_check, with_owned_counts, choose_after_check
from custom.card_visual import (
    classify_rarity,
    rarity_priority,
    match_card_visual,
    normalize_card_name,
    intersection_over_union,
    reconcile_visual_identity,
)
from custom.drink_policy import TARGET_NAMES
from custom.reward_counts import parse_remaining
from custom.reward_layout import reward_card_row
from custom.frame_geometry import reference_frame
from maa.agent.agent_server import AgentServer

from . import shared
from .options import ProduceHIFAttributeBase


@AgentServer.custom_action("ProduceChooseRewardAuto")
class ProduceChooseRewardAuto(CustomAction):
    """Choose drinks by the six-target policy, otherwise apply card policy."""

    # Canonical 720x1280 coordinates measured from the live DMM reward screen.
    # They are used only if the generic detector misses a short transition.
    REWARD_SLOTS = [
        [163, 828, 124, 124],
        [298, 828, 124, 124],
        [433, 828, 124, 124],
    ]
    REMOVE_BUTTON = (136, 1091)
    REMOVE_CONFIRM_BUTTON = (515, 1155)
    REFRESH_BUTTON = (603, 1091)
    ACTION_COUNT_ROIS = {
        "remove": [72, 1005, 150, 72],
        "refresh": [520, 1005, 150, 72],
    }
    MAX_REMOVE_ATTEMPTS = 8
    MAX_REFRESH_ATTEMPTS = 8
    # 以下均为 720x1280 实测坐标。用于弹层恢复：除去确认弹窗的点击可能被
    # 入场动画吞掉，残留的弹窗/详情页会让兜底点击点进卡牌详情，最终没有任何
    # 节点能识别该页面而触发 20 秒 reco 超时，导致整个培育任务失败。
    REMOVE_CONFIRM_DIALOG_ROI = (445, 1122, 600, 1196)  # 弹窗右下橙色「除去する」
    CARD_DETAIL_CLOSE_POINT = (294, 1188)               # 详情页「閉じる」中心
    REMOVE_CANCEL_POINT = (212, 1155)
    SELECT_BAR_CANCEL_POINT = (218, 32)                 # 选卡模式顶栏「キャンセル」
    SELECT_BAR_REMOVE_ROI = (432, 8, 612, 62)           # 选卡模式顶栏橙色「除去する」
    TARGET_DRINKS = {
        "340077": "特制初星精华",
        "767772": "初星黑醋",
        "264496": "初星汤",
        "235611": "Boost精华",
        "679991": "千振苏打",
        "429282": "初星乳清蛋白",
    }
    TARGET_DRINK_TEMPLATES = {
        key: f"produce/drinks/{key}.png" for key in TARGET_DRINKS
    }
    # The reward page must be classified before card recognition.  The six
    # target drinks are enough for selection, but not enough for page-type
    # detection because a reward can contain only non-target drinks.  Keep the
    # complete lightweight icon set here and match only inside the three reward
    # slots; the owned-drink row at the bottom is deliberately excluded.
    ALL_DRINK_TEMPLATE_IDS = (
        "174909", "196735", "198230", "235611", "242782", "264496",
        "299854", "337", "340077", "368325", "429282", "462413",
        "490974", "678359", "679991", "732062", "739375", "762145",
        "767772", "787073", "823789", "825262", "835592", "835785",
        "870793", "911307", "993206", "993957",
    )
    ALL_DRINK_TEMPLATES = [
        f"produce/drinks/{key}.png" for key in ALL_DRINK_TEMPLATE_IDS
    ]
    DRINK_REWARD_ROI = [100, 760, 520, 270]
    INVENTORY_SLOT_CENTERS = [(65, 1200), (147, 1200), (229, 1200), (311, 1200)]
    DRINK_RECO_ROI = [0, 740, 720, 540]
    DRINK_THROW_POINT = (605, 963)
    DRINK_THROW_CONFIRM_POINT = (515, 1155)

    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        image = context.tasker.controller.post_screencap().wait().get()
        if context.tasker.stopping:
            return True
        kind = self._reward_kind(context, image)
        if kind == "drink":
            if self._handle_drink_reward(context, image):
                return True
            # A card-title guard inside the drink handler can reject a late
            # feature-match false positive.  Continue with a fresh frame so
            # the card policy can recover in the same task pass.
            image = context.tasker.controller.post_screencap().wait().get()
            kind = self._reward_kind(context, image)

        if kind == "item":
            # Restore the original P-item path: first item -> receive.  Items
            # have no card blacklist/refresh policy and must not wait for YOLO.
            if not context.tasker.stopping:
                context.tasker.controller.post_click(225, 890).wait()
                logger.info("P道具奖励：按原流程选择第一项")
            return True
        if kind != "card":
            # A disappeared drink/item panel is not evidence of a card page.
            # The old recommendation/first-item fallback checks the LIVE
            # reward prompt before clicking; on a transition it just returns.
            return self._fallback_select(context, image)

        lists = shared._load_card_lists()
        blacklist = set(lists["blacklist"]["cards"]) if lists["blacklist"]["enabled"] else set()
        whitelist = set(lists["whitelist"]["cards"]) if lists["whitelist"]["enabled"] else set()
        logger.success(
            f"奖励牌策略: 黑名单={len(blacklist)} 白名单={len(whitelist)}；"
            "先除去黑名单，再以白名单/再动/SSR决定是否停止刷新"
        )

        image, evaluated = self._wait_for_stable_cards(context)
        if not evaluated:
            image, evaluated = self._recover_reward_page(
                context, allow_remove=shared._REWARD_BUDGETS["remove"].pending_before is not None
            )
        if not evaluated:
            logger.warning("奖励牌未稳定显示，交回原推荐/第一项兜底")
            return self._fallback_select(context, image)
        page_counts = self._sync_reward_action_counts(context, image, log_summary=True)

        # Removal and refresh counts are shared by the whole cultivation.  Once
        # the game proves an action is exhausted, later reward pages must not
        # spend several seconds trying the same disabled action again.
        remove_budget = shared._REWARD_BUDGETS["remove"]
        refresh_budget = shared._REWARD_BUDGETS["refresh"]
        if not remove_budget.can_attempt or page_counts["remove"] != remove_budget.remaining:
            logger.info("除去预算不可用或未确认，直接避开黑名单选择")
        else:
            for _ in range(self.MAX_REMOVE_ATTEMPTS):
                if context.tasker.stopping or not remove_budget.can_attempt:
                    break
                blocked = [item for item in evaluated if item["name_key"] in blacklist]
                if not blocked:
                    break
                selected = max(blocked, key=self._selection_key)
                before_remaining = remove_budget.remaining
                sequence = remove_budget.confirmed + 1
                logger.info(
                    f"奖励黑名单命中: {selected['name']}，执行卡牌除去 "
                    f"(本局第 {sequence} 次)"
                )
                self._click_card(context, selected)
                time.sleep(0.25)
                if context.tasker.stopping:
                    return True
                image = context.tasker.controller.post_screencap().wait().get()
                if shared._detect_disconnect_screen(context, image):
                    return True
                if not remove_budget.begin():
                    break
                context.tasker.controller.post_click(*self.REMOVE_BUTTON).wait()
                time.sleep(0.45)
                # Initial confirmation and delayed confirmation share the same
                # bounded recovery and the same counter acknowledgement.
                next_image, next_evaluated = self._recover_reward_page(context, allow_remove=True)
                if len(next_evaluated) < 3:
                    remove_budget.suspended = True
                    logger.warning("除去尚未核实，保留待确认次数并交回页面恢复")
                    return self._fallback_select(context, next_image)
                page_counts = self._stable_action_counts(context, next_image)
                after_remaining = page_counts["remove"]
                image, evaluated = next_image, next_evaluated
                if not remove_budget.confirm(before_remaining, after_remaining):
                    logger.info("除去剩余次数未确认减少，本次不计成功，继续选牌")
                    break
                logger.success(f"除去已核实：剩余 {before_remaining}→{after_remaining}，本局确认消耗 {remove_budget.confirmed} 次")

        refresh_attempt = 0
        while evaluated and not context.tasker.stopping:
            allowed = [item for item in evaluated if item["name_key"] not in blacklist]
            if not allowed:
                allowed = evaluated
                logger.warning("当前奖励牌均在黑名单且已无法除去，按优先级兜底避免卡死")

            white = [item for item in allowed if item["name_key"] in whitelist]
            if white:
                selected = max(white, key=self._selection_key)
                logger.success(f"奖励白名单命中: {selected['name']}，停止刷新并选择")
                return self._select(context, selected, "白名单", evaluated)

            keep = [
                item
                for item in allowed
                if self._stops_refresh(item)
            ]
            if keep:
                selected = max(keep, key=self._selection_key)
                logger.success(
                    f"奖励出现{'再动' if cultivation_extra_action(selected.get('name_key'), selected['extra_action']) == 'yes' else 'SSR'}牌，"
                    "停止刷新"
                )
                return self._select(context, selected, "刷新停止条件", evaluated)

            if not refresh_budget.can_attempt or page_counts["refresh"] != refresh_budget.remaining:
                logger.info("刷新预算不可用或未确认，按当前非黑名单牌选择")
                break

            if refresh_attempt >= self.MAX_REFRESH_ATTEMPTS:
                logger.info("已达到刷新安全上限，按当前非黑名单牌优先级选择")
                break

            previous_remaining = refresh_budget.remaining
            refresh_attempt += 1
            logger.info(
                "未出现白名单/再动/SSR，尝试再抽选 "
                f"(本页第 {refresh_attempt} 次，本局已用 "
                f"{refresh_budget.confirmed} 次)"
            )
            # A refresh clicked immediately after a removal can be ignored by
            # the game while the lower controls are still re-enabled.
            time.sleep(0.65)
            image = context.tasker.controller.post_screencap().wait().get()
            if context.tasker.stopping or shared._detect_disconnect_screen(context, image):
                return True
            if self._reward_overlay(context, image):
                return self._fallback_select(context, image)
            if not refresh_budget.begin():
                break
            context.tasker.controller.post_click(*self.REFRESH_BUTTON).wait()
            time.sleep(1.15)
            next_image, next_evaluated = self._wait_for_stable_cards(context)
            if len(next_evaluated) < 3:
                next_image, next_evaluated = self._recover_reward_page(context)
            if len(next_evaluated) < 3:
                refresh_budget.suspended = True
                logger.warning("再抽选后未等到奖励牌完整稳定，按推荐/第一项兜底避免卡住")
                return self._fallback_select(context, next_image)
            page_counts = self._stable_action_counts(context, next_image)
            current_remaining = page_counts["refresh"]
            image, evaluated = next_image, next_evaluated
            if not refresh_budget.confirm(previous_remaining, current_remaining):
                logger.info("刷新剩余次数未确认减少，本次不计成功，继续选牌")
                break
            logger.success(
                f"刷新已核实：剩余 {previous_remaining}→{current_remaining}，"
                f"本局确认消耗 {refresh_budget.confirmed} 次"
            )

        allowed = [item for item in evaluated if item["name_key"] not in blacklist]
        pool = allowed or evaluated
        if pool:
            selected = max(pool, key=self._selection_key)
            return self._select(context, selected, "再动与稀有度", evaluated)
        return self._fallback_select(context, image)

    @classmethod
    def _is_drink_reward(cls, context: Context, image) -> bool:
        return cls._reward_kind(context, image) == "drink"

    @classmethod
    def _reward_kind(cls, context: Context, image) -> str:
        """Positive evidence for each reward kind; a miss is never 'card'."""
        # Card rewards have an exact, highly stable title.  Check it before
        # the broad 28-template drink fallback, because event rewards can show
        # four cards and therefore offer more accidental feature matches.
        card_prompt = context.run_recognition("ProduceChooseCardRewardFlag", image)
        if card_prompt and card_prompt.hit:
            logger.info("奖励类型识别：卡牌（命中卡牌奖励标题）")
            return "card"

        prompt = context.run_recognition("ProduceChooseDrinkRewardFlag", image)
        if prompt and prompt.hit:
            logger.info("奖励类型识别：饮料（命中饮料奖励标题）")
            return "drink"

        item = context.run_recognition("ProduceChooseItemFlag", image)
        if item and item.hit:
            return "item"

        reject = context.run_recognition("ProduceChooseSkip", image)
        if reject and reject.hit:
            logger.info("奖励类型识别：饮料（命中满栏不领取控件）")
            return "drink"

        # On the supply animation there is no reward panel at all.  Do not
        # run 28 feature templates or the card recovery loop on that background.
        prompt = context.run_recognition("ProduceChooseGetFlag", image)
        if not (prompt and prompt.hit):
            return "unknown"
        rows = getattr(prompt, "filtered_results", None) or []
        for row in rows:
            text = "".join(str(getattr(row, "text", "")).split())
            if "アイテム" in text or "道具" in text:
                return "item"
            if "ドリンク" in text or "饮料" in text or "飲料" in text:
                return "drink"
            if "スキルカード" in text or "技能卡" in text:
                return "card"

        icons = context.run_recognition(
            "ProduceRecognitionShopDrinks",
            image,
            pipeline_override={
                "ProduceRecognitionShopDrinks": {
                    "recognition": "FeatureMatch",
                    "roi": cls.DRINK_REWARD_ROI,
                    "template": cls.ALL_DRINK_TEMPLATES,
                    "count": 5,
                    "ratio": 0.65,
                    "order_by": "Horizontal",
                }
            },
        )
        if icons and icons.hit:
            logger.info("奖励类型识别：饮料（奖励槽命中饮料图标）")
            return "drink"

        return "unknown"

    @classmethod
    def _normalize_result_box(cls, result, roi: list) -> list:
        box = [int(value) for value in list(getattr(result, "box", []) or [])]
        if len(box) != 4:
            return [0, 0, 0, 0]
        if box[0] + box[2] <= roi[2] and box[0] < roi[0]:
            box[0] += roi[0]
        if box[1] + box[3] <= roi[3] and box[1] < roi[1]:
            box[1] += roi[1]
        return box

    @classmethod
    def _scan_target_drinks(cls, context: Context, image) -> tuple[dict, dict]:
        """Scan each row per target; retain all duplicates without 7x template work.

        A FeatureMatch result has no template identity, so templates stay
        separate. Every box is assigned spatially before masking that slot for
        a bounded repeat. Never merge templates and guess their name by order.
        """
        reward: dict[int, dict] = {}
        inventory: dict[int, dict] = {}
        targets = dict(TARGET_NAMES)
        reward_centers = [x + w // 2 for x, _, w, _ in cls.REWARD_SLOTS]
        groups = (
            (reward, cls.DRINK_REWARD_ROI, reward_centers, 70, 180),
            (inventory, [10, 1150, 350, 100], [x for x, _ in cls.INVENTORY_SLOT_CENTERS], 38, 100),
        )
        for found, roi, centers, tolerance, max_size in groups:
            for drink_id, name in targets.items():
                template = f"produce/drinks/{drink_id}.png"
                pixels = image
                seen = set()
                for _ in range(len(centers) + 1):
                    if getattr(getattr(context, "tasker", None), "stopping", False):
                        return reward, inventory
                    detail = context.run_recognition("ProduceRecognitionShopDrinks", pixels, pipeline_override={
                        "ProduceRecognitionShopDrinks": {"recognition": "FeatureMatch", "roi": roi,
                            "template": [template], "count": 4, "ratio": .62, "order_by": "Horizontal"}})
                    new_slots = set()
                    for result in list(detail.filtered_results or []) if detail and detail.hit else []:
                        box = cls._normalize_result_box(result, roi)
                        cx, cy = box[0] + box[2] // 2, box[1] + box[3] // 2
                        count = int(getattr(result, "count", 0) or 0)
                        if not (20 <= box[2] <= max_size and 20 <= box[3] <= max_size
                                and roi[0] <= cx < roi[0] + roi[2] and roi[1] <= cy < roi[1] + roi[3]):
                            continue
                        index = min(range(len(centers)), key=lambda item: abs(cx - centers[item]))
                        if abs(cx - centers[index]) > tolerance or index in seen:
                            continue
                        new_slots.add(index)
                        if count > found.get(index, {}).get("score", 0):
                            found[index] = {"id": drink_id, "name": name, "score": count, "box": box}
                    if not new_slots:
                        break
                    seen.update(new_slots)
                    if len(seen) == len(centers):
                        break
                    if pixels is image:
                        pixels = image.copy()
                    # Mask only this template's already found slots. Other
                    # target names always see the unmodified original frame.
                    for index in new_slots:
                        left = roi[0] if index == 0 else (centers[index-1] + centers[index]) // 2
                        right = roi[0]+roi[2] if index+1 == len(centers) else (centers[index] + centers[index+1]) // 2
                        pixels[roi[1]:roi[1]+roi[3], left:right] = 0
        return reward, inventory

    @classmethod
    def _scan_inventory_occupancy(
        cls, context: Context, image, recognized: dict
    ) -> set[int]:
        occupied = set(recognized)
        roi = [10, 1150, 350, 100]
        detail = context.run_recognition("ProduceCheckDrinkButton", image)
        if detail and detail.hit:
            for result in list(detail.filtered_results or detail.all_results or []):
                box = cls._normalize_result_box(result, roi)
                center_x = box[0] + box[2] // 2
                index = min(
                    range(4),
                    key=lambda item: abs(
                        center_x - cls.INVENTORY_SLOT_CENTERS[item][0]
                    ),
                )
                if abs(center_x - cls.INVENTORY_SLOT_CENTERS[index][0]) <= 55:
                    occupied.add(index)
        return occupied

    @classmethod
    def _scan_drink_state(cls, context: Context, image) -> dict:
        from custom.drink_visual import INVENTORY_BOXES, scan
        all_reward = scan(image, cls.REWARD_SLOTS)
        all_inventory = scan(image, INVENTORY_BOXES)
        # On translucent item rewards, cyan stage/background pixels can shine
        # through an EMPTY inventory slot. Shop's broad cyan-area heuristic
        # then invents a bottle and triggers repeated full SIFT scans. Use
        # actual ring-template detections plus trusted bottle artwork here.
        occupied = cls._scan_inventory_occupancy(context, image, all_inventory)
        # The reward's reject selector is shown only at capacity. It remains
        # present when a bottle is selected and the reward heading disappears.
        # Identity matching is partial evidence, not an inventory count: a
        # coffee icon and its gray ring may both miss their visual thresholds.
        full_control = context.run_recognition("ProduceDrinkRewardFullFlag", image)
        capacity_confirmed = bool(full_control and full_control.hit)
        if capacity_confirmed:
            occupied = set(range(4))
        complete = len(all_reward) == len(cls.REWARD_SLOTS) and set(all_inventory) == occupied
        reward = {i: item for i, item in all_reward.items() if item['id'] in TARGET_NAMES}
        inventory = {i: item for i, item in all_inventory.items() if item['id'] in TARGET_NAMES}
        if not complete:
            old_reward, old_inventory = cls._scan_target_drinks(context, image)
            # A positively identified non-target must not become a target by
            # an old template's spurious feature correspondences.
            reward.update({i: item for i, item in old_reward.items() if i not in all_reward})
            inventory.update({i: item for i, item in old_inventory.items() if i not in all_inventory})
            occupied |= set(inventory)
        full_marker = context.run_recognition("ProduceKeepDrinkFlag", image)
        full = (len(occupied) >= 4 or bool(full_marker and full_marker.hit)
                or capacity_confirmed)
        return {
            "reward": reward,
            "inventory": inventory,
            "occupied": occupied,
            "full": full,
            "all_reward": all_reward,
            "all_inventory": all_inventory,
            "visual_complete": complete,
            "capacity_confirmed": capacity_confirmed,
            "unknown_inventory": occupied - set(all_inventory),
        }

    @staticmethod
    def _drink_state_signature(state: dict) -> tuple:
        return (
            tuple(sorted((index, item["id"]) for index, item in state["reward"].items())),
            tuple(sorted((index, item["id"]) for index, item in state["inventory"].items())),
            tuple(sorted(state["occupied"])),
            bool(state["full"]),
        )

    @classmethod
    def _drink_regions_unchanged(cls, before, after) -> bool:
        """Identity reuse is local to one reward pass and unchanged bottle rows."""
        if not isinstance(before, np.ndarray) or not isinstance(after, np.ndarray) or before.shape != after.shape:
            return False
        for x, y, w, h in (cls.DRINK_REWARD_ROI, [10, 1150, 350, 100]):
            first = before[y:y+h:2, x:x+w:2, :3]
            second = after[y:y+h:2, x:x+w:2, :3]
            if not first.size or float(np.abs(first.astype(np.int16) - second.astype(np.int16)).mean()) > 1.0:
                return False
        return True

    @classmethod
    def _stable_drink_state(cls, context: Context, first_image) -> tuple:
        image = first_image
        state = None
        for _ in range(3):
            if getattr(context.tasker, 'stopping', False):
                return image, {"stable": False, "aborted": True}
            state = state if state is not None else cls._scan_drink_state(context, image)
            time.sleep(0.2)
            next_image = context.tasker.controller.post_screencap().wait().get()
            if not state.get('visual_complete') and cls._drink_regions_unchanged(image, next_image):
                return next_image, state
            following = cls._scan_drink_state(context, next_image)
            same_icons = all(
                {i: item['id'] for i,item in state.get(key, {}).items()} ==
                {i: item['id'] for i,item in following.get(key, {}).items()}
                for key in ('all_reward', 'all_inventory'))
            # Unknown artwork must not make the animated empty inventory
            # background a blocker. Compare reward artwork locally and verify
            # inventory identity/occupancy again on the current frame.
            same_reward_pixels = all(
                float(np.abs(image[y+20:y+h-20:2,x+20:x+w-20:2,:3].astype(np.int16)
                             - next_image[y+20:y+h-20:2,x+20:x+w-20:2,:3].astype(np.int16)).mean()) <= 1.0
                for x,y,w,h in cls.REWARD_SLOTS) if image.shape == next_image.shape else False
            complete = state.get('visual_complete') and following.get('visual_complete')
            if (same_icons and (complete or same_reward_pixels)
                    and cls._drink_state_signature(state) == cls._drink_state_signature(following)):
                logger.info('饮料整页识别：奖励=' + '/'.join(i['name'] for i in following.get('all_reward',{}).values())
                            + '；持有=' + '/'.join(i['name'] for i in following.get('all_inventory',{}).values())
                            + ('；局部未识别，奖励图标与库存状态已复核' if not complete else ''))
                return next_image, following
            image, state = next_image, following
        logger.warning("饮料奖励图标或库存状态持续变化；交回流程重识别，不点击旧奖励位置")
        return image, {**state, "stable": False, "aborted": True}

    @classmethod
    def _click_reward_drink(cls, context: Context, index: int, reason: str) -> bool:
        image = context.tasker.controller.post_screencap().wait().get()
        if context.tasker.stopping or shared._detect_disconnect_screen(context, image):
            return True
        rejected = context.run_recognition("ProduceChooseGetFlag", image, pipeline_override={
            "ProduceChooseGetFlag": {"expected": ["P.?ドリンクを受け取らないで次へ進みます"],
                "focus": {}}})
        if rejected and rejected.hit:
            control = context.run_recognition("ProduceChooseSkip", image)
            if not (control and control.hit and control.best_result):
                logger.warning("奖励不领取状态尚未解除，保留现场重识别")
                return True
            x, y, w, h = map(int, control.best_result.box)
            context.tasker.controller.post_click(x + w // 2, y + h // 2).wait()
            time.sleep(.25)
        box = cls.REWARD_SLOTS[index]
        point = (box[0] + box[2] // 2, box[1] + box[3] // 2)
        context.tasker.controller.post_click(*point).wait()
        logger.success(f"饮料奖励选择: index={index + 1} reason={reason} point={point}")
        return True

    @classmethod
    def _skip_drink_reward(cls, context: Context, image, reason: str) -> bool:
        from custom.shop import ocr
        from custom.dialog_safety import drink_overlay_cancel
        # A discard may have opened another modal. Never reuse its old reward
        # screenshot or guess a checkbox coordinate underneath that modal.
        for _ in range(4):
            if context.tasker.stopping:
                return True
            image = context.tasker.controller.post_screencap().wait().get()
            if shared._detect_disconnect_screen(context, image):
                return True
            overlay, cancel = drink_overlay_cancel(context, image)
            if overlay:
                if cancel is None:
                    logger.warning("饮料弹窗取消按钮未确认，保留现场，不点击奖励页")
                    return True
                x, y, w, h = cancel
                context.tasker.controller.post_click(x + w // 2, y + h // 2).wait()
                time.sleep(.3)
                continue
            if not cls._is_drink_reward(context, image):
                logger.warning("当前未确认饮料奖励页，取消旧页面的不领取点击")
                return True
            rejected = context.run_recognition("ProduceChooseGetFlag", image, pipeline_override={
                "ProduceChooseGetFlag": {"expected": ["P.?ドリンクを受け取らないで次へ進みます"], "focus": {}}})
            if rejected and rejected.hit:
                buttons = ocr(context, image, [100, 1030, 520, 130], [r"^受け取らない$"])
                boxes = [list(row.box) for row in buttons if float(row.score) >= .8]
            else:
                detail = context.run_recognition("ProduceChooseSkip", image)
                boxes = [list(detail.best_result.box)] if detail and detail.hit and detail.best_result else []
            if len(boxes) != 1:
                logger.warning("饮料不领取控件未确认，等待重识别，不使用固定坐标")
                return True
            x, y, w, h = boxes[0]
            context.tasker.controller.post_click(x + w // 2, y + h // 2).wait()
            logger.info(f"饮料奖励选择不领取: reason={reason}")
            return True
        logger.warning("饮料弹窗尚未关闭，停止本次点击，等待重识别")
        return True

    @classmethod
    def _discard_receipt_state(cls, context, value=None, *, write=False):
        node = context.get_node_data('ProduceChooseDrinkFastFlag')
        attach = node.get('attach', {}) if isinstance(node, dict) else {}
        if write:
            context.override_pipeline({'ProduceChooseDrinkFastFlag': {'attach': {'discard_receipt': value}}})
        return attach.get('discard_receipt') if isinstance(attach, dict) else None

    @classmethod
    def _reward_inventory_count(cls, context, image):
        from custom.drink_visual import INVENTORY_BOXES, scan
        # Use the same artwork/ring evidence as the reward reader. The shop's
        # broad cyan mask can count the animated background as another bottle.
        return len(cls._scan_inventory_occupancy(context, image, scan(image, INVENTORY_BOXES)))

    @classmethod
    def _wait_discard_receipt(cls, context, before_count):
        from custom.shop import owned_detail_title
        stable = 0
        for _ in range(12):
            if context.tasker.stopping:
                return None
            time.sleep(.35)
            image = context.tasker.controller.post_screencap().wait().get()
            if shared._detect_disconnect_screen(context, image):
                return None
            settled = (not owned_detail_title(context, image)
                       and cls._is_drink_reward(context, image)
                       and cls._reward_inventory_count(context, image) == before_count - 1)
            stable = stable + 1 if settled else 0
            if stable >= 2:
                cls._discard_receipt_state(context, write=True)
                return True
        logger.warning('饮料丢弃已提交，结果尚未确认；保留待核状态，不重复删除或点击旧奖励位置')
        return None

    @classmethod
    def _delete_owned_drink(cls, context: Context, slot: int, *, expected_name=None, expected_non_target=False) -> Optional[bool]:
        from custom.shop import TARGET_DRINK_NAMES, owned_throw_box, owned_detail_title, read_discard_confirmation
        if context.tasker.stopping:
            return False
        before = context.tasker.controller.post_screencap().wait().get()
        full_control = context.run_recognition("ProduceDrinkRewardFullFlag", before)
        before_count = cls._reward_inventory_count(context, before)
        if full_control and full_control.hit:
            before_count = 4
        point = cls.INVENTORY_SLOT_CENTERS[slot]
        context.tasker.controller.post_click(*point).wait()
        previous, stable, name = None, 0, None
        for _ in range(5):
            if context.tasker.stopping:
                return False
            time.sleep(.3)
            image = context.tasker.controller.post_screencap().wait().get()
            if shared._detect_disconnect_screen(context, image):
                return False
            name = owned_detail_title(context, image)
            stable = stable + 1 if name and name == previous else 1 if name else 0
            previous = name
            if stable >= 2:
                break
        if stable < 2 or (expected_name and name != expected_name):
            logger.warning("饮料丢弃前名称未核实，不点击捨てる")
            return False
        if expected_non_target and name in TARGET_DRINK_NAMES.values():
            logger.warning("拟腾位饮料实际是目标饮料，取消丢弃并重新识别")
            return False
        throw = owned_throw_box(context, image, name)
        if throw is None:
            return False
        context.tasker.controller.post_click(throw[0] + throw[2] // 2, throw[1] + throw[3] // 2).wait()
        previous = None
        for _ in range(5):
            if context.tasker.stopping:
                return False
            time.sleep(.3)
            image = context.tasker.controller.post_screencap().wait().get()
            if shared._detect_disconnect_screen(context, image):
                return False
            confirmed = read_discard_confirmation(context, image, name)
            if confirmed and previous and confirmed["name"] == previous["name"]:
                box = confirmed["yes"]
                cls._discard_receipt_state(context, {'before_count': before_count, 'name': name, 'attempts': 0}, write=True)
                context.tasker.controller.post_click(box[0] + box[2] // 2, box[1] + box[3] // 2).wait()
                break
            previous = confirmed
        else:
            logger.warning("饮料廃棄确认未核实，不盲点确认")
            return False
        result = cls._wait_discard_receipt(context, before_count)
        if result:
            logger.success(f"已丢弃现有饮料: {name} slot={slot + 1}")
        return result

    @classmethod
    def _handle_drink_reward(cls, context: Context, image) -> bool:
        pending = cls._discard_receipt_state(context)
        if isinstance(pending, dict):
            if cls._wait_discard_receipt(context, pending['before_count']) is not True:
                pending = {**pending, 'attempts': pending.get('attempts', 0) + 1}
                cls._discard_receipt_state(context, pending, write=True)
                # A bounded retry may skip only a newly verified reward page,
                # never a modal or the pre-discard screenshot.
                fresh = context.tasker.controller.post_screencap().wait().get()
                from custom.shop import owned_detail_title
                if (pending['attempts'] >= 2 and not context.tasker.stopping
                        and not owned_detail_title(context, fresh) and cls._is_drink_reward(context, fresh)):
                    cls._discard_receipt_state(context, write=True)
                    return cls._skip_drink_reward(context, fresh, '丢弃结果未确认，结束本次奖励，不重复丢弃')
                return True
            image = context.tasker.controller.post_screencap().wait().get()
        image, state = cls._stable_drink_state(context, image)
        if state.get("aborted") or context.tasker.stopping:
            return True
        reward = state["reward"]
        inventory = state["inventory"]
        occupied = state["occupied"]
        full = state["full"]
        stable = state.get("stable", True)
        reward_text = ", ".join(
            f"{index + 1}:{item['name']}" for index, item in reward.items()
        )
        inventory_text = ", ".join(
            f"{index + 1}:{item['name']}" for index, item in inventory.items()
        )
        logger.info(
            "饮料奖励现场识别: "
            f"full={full} occupied={sorted(occupied)} "
            f"reward={{{reward_text}}} inventory={{{inventory_text}}}"
        )
        if state.get("capacity_confirmed") and state.get("unknown_inventory"):
            logger.info("饮料满栏控件已确认4栏占用；图标名称未确认栏位="
                        f"{sorted(i + 1 for i in state['unknown_inventory'])}，腾位前按详情核名")

        # Never keep clicking drink coordinates when the live page explicitly
        # says it is a card reward.  This second check covers a title that
        # finishes animating between page classification and stable sampling.
        if not reward:
            card_prompt = context.run_recognition("ProduceChooseCardRewardFlag", image)
            if card_prompt and card_prompt.hit:
                logger.warning(
                    "饮料奖励复核失败：奖励槽未识别到目标饮料且命中卡牌标题；"
                    "立即切回卡牌策略"
                )
                return False

        current_ids = {item["id"] for item in inventory.values()}
        target_indexes = sorted(
            reward,
            key=lambda index: (reward[index]["id"] in current_ids, index),
        )
        # Fill capacity before considering replacement. Multiple copies are
        # useful in exams; receiving one must never discard existing copies.
        if not full:
            if target_indexes:
                selected = target_indexes[0]
                return cls._click_reward_drink(
                    context,
                    selected,
                    "栏位未满，领取目标补空位，允许同种重复",
                )
            return cls._click_reward_drink(context, 0, "栏位未满且无目标，领取第一瓶")

        if not stable:
            if target_indexes:
                return cls._skip_drink_reward(
                    context, image, "满栏识别不稳定，不执行破坏性删除"
                )
            return cls._skip_drink_reward(context, image, "满栏且识别不稳定")

        all_target = len(inventory) == 4
        non_target_slots = sorted(occupied - set(inventory))
        if all_target:
            return cls._skip_drink_reward(context, image, "满栏均为目标饮料，保留全部库存，允许重复")
        if target_indexes:
            selected = target_indexes[0]
            if not non_target_slots:
                return cls._skip_drink_reward(context, image, "未确认非目标栏位，保留已有目标饮料")
            delete_slot = non_target_slots[0]
            deleted = cls._delete_owned_drink(context, delete_slot, expected_non_target=True)
            if deleted is None:
                return True
            if not deleted:
                return cls._skip_drink_reward(
                    context, image, "删除现有饮料失败，保守跳过"
                )
            return cls._click_reward_drink(
                context,
                selected,
                "满栏：只用非目标换成目标，保留已有同名饮料",
            )

        if not non_target_slots:
            return cls._skip_drink_reward(context, image, "未确认非目标栏位，保护现有饮料")
        delete_slot = non_target_slots[0]
        deleted = cls._delete_owned_drink(context, delete_slot, expected_non_target=True)
        if deleted is None:
            return True
        if not deleted:
            return cls._skip_drink_reward(context, image, "非目标饮料删除失败")
        return cls._click_reward_drink(
            context, 0, "满栏、奖励无目标：删除非目标后领取第一瓶"
        )

    def _evaluate(self, context: Context, image, *, verified_boxes=None, read_extra_action=True, diagnostics=True) -> list:
        from .battle import ProduceCardsAuto
        detection = context.run_recognition("ProduceRecognitionCards", image) if verified_boxes is None else None
        candidates = [{"box": box, "label": "verified-slot", "detector_score": 0.0}
                      for box in verified_boxes] if verified_boxes is not None else []
        if detection and detection.hit:
            candidates = ProduceCardsAuto._get_playable_candidates(
                detection.all_results, image.shape
            )
            candidates = reward_card_row(candidates)
        # An incomplete event row must be retried, never guessed as three fixed
        # slots. Explicit verified_boxes belong to other callers and stay intact.

        evaluated = []
        for index, candidate in enumerate(candidates, start=1):
            rarity = classify_rarity(image, candidate["box"])
            extra, extra_score, extra_roi = ProduceCardsAuto._extra_action_status(
                context, image, candidate["box"]
            ) if read_extra_action else ("unknown", 0.0, [])
            from custom.square_identity import evidence as square_evidence
            from custom.square_identity import recognize as recognize_square
            # Verified square slots cover both exchange pages and deletion.
            # Ordinary reward rows retain their existing rectangular matcher.
            square = recognize_square(image, candidate["box"], rarity) if verified_boxes is not None else None
            square_attempted = verified_boxes is not None
            visual = square if square_attempted else reconcile_visual_identity(match_card_visual(image, candidate["box"]), rarity)
            resolved_rarity = visual.rarity if visual is not None else rarity.rarity
            if visual is not None and visual.extra_action:
                extra = "yes"
            priority = rarity_priority(resolved_rarity, extra)
            evaluated.append(
                {
                    **candidate,
                    "name": visual.name if visual is not None else "未知",
                    "name_key": normalize_card_name(visual.name) if visual is not None else "",
                    "name_confidence": visual.confidence if visual is not None else 0.0,
                    "name_score": visual.score if visual is not None else 0.0,
                    "name_margin": visual.margin if visual is not None else 0.0,
                    **square_evidence(square),
                    "square_art_attempted": square_attempted,
                    "rarity": resolved_rarity,
                    "frame_rarity": rarity.rarity,
                    "rarity_confidence": rarity.confidence,
                    "rarity_distance": rarity.best_distance,
                    "rarity_margin": rarity.margin,
                    "frame_bgr": [round(value * 255) for value in rarity.signature],
                    "extra_action": extra,
                    "extra_score": extra_score,
                    "extra_roi": extra_roi,
                    "priority": priority,
                }
            )
            (shared._diagnostic_info if diagnostics else logger.debug)(
                f"奖励卡识别[{index}] box={candidate['box']} name={evaluated[-1]['name']} "
                f"name_score={evaluated[-1]['name_score']:.3f} "
                f"name_margin={evaluated[-1]['name_margin']:.3f} rarity={resolved_rarity} "
                f"confidence={rarity.confidence:.3f} distance={rarity.best_distance:.3f} "
                f"margin={rarity.margin:.3f} "
                f"frame_bgr={evaluated[-1]['frame_bgr']} extra_action={extra} "
                f"extra_score={extra_score:.3f} extra_roi={extra_roi} priority={priority}"
            )

        return evaluated

    @classmethod
    def _recognize_reward_action_remaining(
        cls, context: Context, image, action: str
    ) -> Optional[int]:
        """Read the small ``あとN回`` badge without depending on its language."""
        roi = cls.ACTION_COUNT_ROIS[action]
        detail = context.run_recognition(
            "ProduceRecognitionPoint",
            image,
            pipeline_override={
                "ProduceRecognitionPoint": {
                    "recognition": {
                        "type": "OCR",
                        "param": {"roi": roi, "expected": [], "order_by": "Horizontal"},
                    },
                }
            },
        )
        if not (detail and detail.hit):
            return None
        values = set()
        for result in list(detail.filtered_results or detail.all_results or []):
            value = parse_remaining(
                str(getattr(result, "text", "")),
                float(getattr(result, "score", 0.0) or 0.0),
            )
            if value is not None:
                values.add(value)
        return next(iter(values)) if len(values) == 1 else None

    @classmethod
    def _stable_action_counts(cls, context: Context, image) -> dict:
        first = {a: cls._recognize_reward_action_remaining(context, image, a)
                 for a in shared._REWARD_BUDGETS}
        if context.tasker.stopping:
            return {a: None for a in shared._REWARD_BUDGETS}
        time.sleep(0.2)
        second_image = context.tasker.controller.post_screencap().wait().get()
        second = {a: cls._recognize_reward_action_remaining(context, second_image, a)
                  for a in shared._REWARD_BUDGETS}
        return {a: first[a] if first[a] == second[a] else None for a in first}

    @classmethod
    def _sync_reward_action_counts(
        cls, context: Context, image, log_summary: bool = False
    ) -> dict:
        page_counts = cls._stable_action_counts(context, image)
        for action, remaining in page_counts.items():
            budget = shared._REWARD_BUDGETS[action]
            if budget.pending_before is not None:
                before = budget.pending_before
                if budget.confirm(before, remaining):
                    logger.success(f"恢复后{'除去' if action == 'remove' else '刷新'}已核实："
                                   f"剩余 {before}→{remaining}，本局确认消耗 {budget.confirmed} 次")
            else:
                budget.observe(remaining)
        if log_summary:
            for action, label in (("remove", "除去"), ("refresh", "刷新")):
                budget = shared._REWARD_BUDGETS[action]
                logger.info(f"{label}预算：确认消耗={budget.confirmed}，"
                            f"剩余={budget.remaining if budget.remaining is not None else '未知'}，"
                            f"暂停尝试={budget.suspended}")
        return page_counts

    def _wait_for_stable_cards(self, context: Context) -> tuple:
        previous_signature = None
        last_image = context.tasker.controller.post_screencap().wait().get()
        last_evaluated = []
        best_image = last_image
        best_evaluated = []
        for _ in range(14):
            if context.tasker.stopping:
                return last_image, []
            last_image = context.tasker.controller.post_screencap().wait().get()
            if shared._detect_disconnect_screen(context, last_image):
                return last_image, []
            # Reject overlays before YOLO: an overlay can contain several card
            # thumbnails, which must never be accepted as a reward hand.
            if self._reward_overlay(context, last_image):
                logger.info("奖励牌暂被弹层遮挡，先恢复页面")
                return last_image, []
            last_evaluated = self._evaluate(context, last_image)
            rarity_count = sum(item["rarity"] != "unknown" for item in last_evaluated)
            detected = all(item["label"] != "fixed-slot" for item in last_evaluated)
            ready = len(last_evaluated) >= 3 and (detected or rarity_count >= 3)
            signature = self._scene_signature(last_image, last_evaluated) if ready else None
            if ready:
                best_image, best_evaluated = last_image, last_evaluated
            if ready and self._reward_frames_stable(previous_signature, signature):
                return last_image, last_evaluated
            previous_signature = signature
            time.sleep(0.35)
        if best_evaluated:
            logger.warning("奖励牌动画超时但已取得完整有效帧，使用最后一个有效画面")
            return best_image, best_evaluated
        if last_evaluated:
            shared._save_card_diagnostic(last_image, "reward-unstable", last_evaluated)
        return last_image, []

    def _confirm_remove(self, context: Context) -> bool:
        """Retry only a freshly recognized remove dialog, at most three times."""
        for _ in range(3):
            if context.tasker.stopping:
                return False
            image = context.tasker.controller.post_screencap().wait().get()
            if shared._detect_disconnect_screen(context, image):
                return False
            if self._reward_overlay(context, image) != "remove":
                return True
            self._click_reward_point(context, image, self.REMOVE_CONFIRM_BUTTON)
            time.sleep(0.8)
        image = context.tasker.controller.post_screencap().wait().get()
        return (not context.tasker.stopping and not shared._detect_disconnect_screen(context, image)
                and self._reward_overlay(context, image) != "remove")

    @staticmethod
    def _orange_ratio(image, roi: tuple) -> float:
        """roi 为 720x1280 基准坐标，按实际帧尺寸等比缩放后统计橙色占比。"""
        height, width = image.shape[:2]
        x1, y1, x2, y2 = roi
        region = image[int(y1 * height / 1280):int(y2 * height / 1280),
                       int(x1 * width / 720):int(x2 * width / 720), :3]
        if not region.size:
            return 0.0
        blue, green, red = region[:, :, 0], region[:, :, 1], region[:, :, 2]
        orange = (red > 205) & (green > 75) & (green < 205) & (blue < 120)
        return float(orange.mean())

    @classmethod
    def _remove_dialog_present(cls, image) -> bool:
        """除去确认弹窗是否仍开着。实测该区域弹窗时占比约 0.87，
        正常奖励页/选卡页/详情页均为 0。"""
        ratio = cls._orange_ratio(image, cls.REMOVE_CONFIRM_DIALOG_ROI)
        shared._diagnostic_info(f"卡牌除去确认检测: orange_ratio={ratio:.3f}")
        return ratio >= 0.08

    @classmethod
    def _selection_bar_present(cls, image) -> bool:
        """选卡模式顶栏是否可见（右上「除去する」未被弹窗压暗时呈橙色）。"""
        return cls._orange_ratio(image, cls.SELECT_BAR_REMOVE_ROI) >= 0.08

    @staticmethod
    def _card_detail_open(context: Context, image) -> bool:
        reco = context.run_recognition("ProduceCloseCardDetail", image)
        return bool(reco and reco.hit)

    def _reward_overlay(self, context: Context, image) -> str:
        # Orange alone also matches receive/buy buttons. Require the button's
        # text before treating it as a destructive confirmation.
        if self._remove_dialog_present(image):
            detail = context.run_recognition("ProduceCancelRewardRemoveDialog", image)
            if detail and detail.hit:
                return "remove"
        if self._selection_bar_present(image):
            detail = context.run_recognition("ProduceCancelRewardSelection", image)
            if detail and detail.hit:
                return "selection"
        # The ordinary reward title avoids OCR on every stable reward frame.
        reward = context.run_recognition("ProduceChooseCardRewardFlag", image)
        if reward and reward.hit:
            return ""
        return "detail" if self._card_detail_open(context, image) else ""

    @staticmethod
    def _click_reward_point(context: Context, image, point: tuple) -> None:
        height, width = image.shape[:2]
        context.tasker.controller.post_click(
            round(point[0] * width / 720), round(point[1] * height / 1280)
        ).wait()

    def _recover_reward_page(self, context: Context, allow_remove: bool = False) -> tuple:
        """Restore a reward page with bounded clicks and no stale coordinates."""
        image = context.tasker.controller.post_screencap().wait().get()
        confirmed_dialog = False
        for _ in range(6):
            if context.tasker.stopping:
                return image, []
            image = context.tasker.controller.post_screencap().wait().get()
            if shared._detect_disconnect_screen(context, image):
                return image, []
            overlay = self._reward_overlay(context, image)
            if overlay == "remove":
                if allow_remove and not confirmed_dialog:
                    confirmed_dialog = True
                    logger.info("恢复：核实除去确认弹窗，最多补点三次")
                    if not self._confirm_remove(context):
                        return context.tasker.controller.post_screencap().wait().get(), []
                else:
                    logger.info("恢复：取消未完成的除去弹窗，避免重复提交")
                    self._click_reward_point(context, image, self.REMOVE_CANCEL_POINT)
                    time.sleep(0.6)
                continue
            if overlay == "detail":
                logger.info("恢复：检测到卡牌详情页，点击閉じる")
                self._click_reward_point(context, image, self.CARD_DETAIL_CLOSE_POINT)
                time.sleep(0.6)
                continue
            if overlay == "selection":
                logger.info("恢复：检测到选卡模式顶栏，点击キャンセル退出")
                self._click_reward_point(context, image, self.SELECT_BAR_CANCEL_POINT)
                time.sleep(0.6)
                continue
            image, evaluated = self._wait_for_stable_cards(context)
            if evaluated:
                return image, evaluated
            # A late entrance animation may show the dialog after the first
            # frame. Reclassify it within this recovery, not a new removal.
            if not self._reward_overlay(context, image):
                return image, []
        logger.warning("奖励弹层恢复达到重试上限，交回流程处理")
        return image, []

    @staticmethod
    def _scene_signature(image, evaluated: list) -> tuple:
        values = []
        for item in evaluated:
            x, y, width, height = item["box"]
            crop = image[y:y + height, x:x + width, :3]
            # A small spatial colour grid detects replacement/refresh even if
            # a card name is conservatively returned as unknown.  A single
            # global mean was too weak for visually different cards.
            raw_parts = []
            if crop.size:
                for row in range(3):
                    for column in range(3):
                        y1 = row * crop.shape[0] // 3
                        y2 = (row + 1) * crop.shape[0] // 3
                        x1 = column * crop.shape[1] // 3
                        x2 = (column + 1) * crop.shape[1] // 3
                        tile = crop[y1:y2, x1:x2]
                        raw_parts.extend((tile.mean(axis=(0, 1)) / 8).astype(int).tolist())
            raw = tuple(raw_parts)
            values.append((tuple(item["box"]), raw))
        return tuple(values)

    @staticmethod
    def _reward_frames_stable(previous, current) -> bool:
        if previous is None or current is None or len(previous) != len(current):
            return False
        for (old_box, old_pixels), (box, pixels) in zip(previous, current):
            if not pixels or len(old_pixels) != len(pixels):
                return False
            if max(abs(a - b) for a, b in zip(old_box, box)) > 3:
                return False
            delta = np.abs(np.asarray(old_pixels) - np.asarray(pixels))
            if delta.mean() > 1.0 or delta.max() > 4:
                return False
        return True

    @staticmethod
    def _stops_refresh(item: dict) -> bool:
        return cultivation_extra_action(item.get("name_key"), item["extra_action"]) == "yes" or cultivation_rarity(item.get("name_key"), item["rarity"]) == "SSR"

    @staticmethod
    def _selection_key(item: dict) -> tuple:
        return (
            cultivation_priority(item),
            item["name_confidence"],
            1 if item["label"] == "suggestions" else 0,
            item["detector_score"],
        )

    @staticmethod
    def _click_card(context: Context, item: dict) -> None:
        box = item["box"]
        context.tasker.controller.post_click(
            box[0] + box[2] // 2, box[1] + box[3] // 2
        ).wait()

    @classmethod
    def _limit_acquisition(cls, context, selected, candidates, image, lists, *, purchase=False):
        if not needs_deck_check(selected):
            return selected, True
        from custom.owned_deck import OwnedDeckReader, drawer_present
        signature = cls._scene_signature(image, candidates)
        def source_same(frame):
            return (reference_frame(frame) and not drawer_present(context, frame)
                    and cls._reward_frames_stable(signature, cls._scene_signature(frame, candidates)))
        wanted = single_copy_cards() & {c.get('name_key') for c in candidates}
        result = OwnedDeckReader(context).inspect(wanted, source_same)
        if not result.restored:
            logger.warning("牌组检查后尚未确认返回原页面，稍后重新识别")
            return None, False
        black = set(lists['blacklist']['cards']) if lists['blacklist']['enabled'] else set()
        white = set(lists['whitelist']['cards']) if lists['whitelist']['enabled'] else set()
        if purchase:
            chosen = selected if result.counts is not None and result.counts.get(selected['name_key']) == 0 else None
        else:
            chosen = choose_after_check(selected, candidates, result.counts, black, white, cls._selection_key)
            if chosen is None:
                safe = [c for c in candidates if c.get('name_key') and c['name_key'] not in black
                        and not needs_deck_check(c)]
                chosen = max([c for c in safe if c['name_key'] in white] or safe,
                             key=cls._selection_key, default=None)
        if chosen is None:
            logger.info("持牌数量尚未确认或已经持有，本次不拿这张限一牌")
        elif chosen is not selected:
            logger.info(f"限一规则：改选「{chosen['name']}」")
        elif result.counts and result.counts.get(selected['name_key'], 0) > 0:
            logger.info("其余可选牌均被排除，允许暂时拿重复牌，之后优先删换")
        # No coordinates survive a changed source. inspect verifies two frames,
        # then this last capture also guards a delayed animation before input.
        fresh = context.tasker.controller.post_screencap().wait().get()
        return (chosen, True) if source_same(fresh) else (None, False)

    def _select(self, context: Context, selected: dict, reason: str, candidates=None) -> bool:
        if context.tasker.stopping:
            return True
        image = context.tasker.controller.post_screencap().wait().get()
        if shared._detect_disconnect_screen(context, image):
            return True
        if self._reward_overlay(context, image):
            return self._fallback_select(context, image)
        selected, restored = self._limit_acquisition(context, selected, candidates or [selected], image, shared._load_card_lists())
        if selected is None or not restored:
            return False  # ProduceActionYield: re-recognize, never Receive.
        self._click_card(context, selected)
        logger.success(
            f"奖励卡选择: reason={reason} name={selected['name']} rarity={selected['rarity']} "
            f"extra_action={selected['extra_action']} priority={selected['priority']} "
            f"box={selected['box']}"
        )
        return True

    def _fallback_select(self, context: Context, image) -> bool:
        image = context.tasker.controller.post_screencap().wait().get()
        if context.tasker.stopping or shared._detect_disconnect_screen(context, image):
            logger.info("奖励选择已中断，交回任务停止或异常恢复流程")
            return True
        if self._reward_overlay(context, image):
            image, evaluated = self._recover_reward_page(context)
            if evaluated:
                self._sync_reward_action_counts(context, image)
            if (context.tasker.stopping or shared._detect_disconnect_screen(context, image)
                    or self._reward_overlay(context, image)):
                logger.warning("奖励弹层仍未关闭，交回恢复节点，本次不点击卡牌")
                return True
        # A scene can change during recovery. Never click the first reward on
        # a detail/shop/error page merely because card recognition failed.
        reward = context.run_recognition("ProduceChooseGetFlag", image)
        if not (reward and reward.hit):
            logger.info("已离开奖励页，交回流程重新识别")
            return True
        if single_copy_cards() and self._reward_kind(context, image) != "drink":
            logger.info("卡牌候选尚未确认，保留限一规则，交回页面重新识别")
            return False
        logger.warning("奖励内容无法可靠判定为卡牌；按推荐标记回退，仍未知则领取第一项")
        if self._click_recommendation(context, image, "ProduceChooseRecommend", -80):
            return True
        if self._click_recommendation(context, image, "ProduceChooseEventRecommend", 80):
            return True
        context.tasker.controller.post_click(225, 890).wait()
        logger.info("奖励选择回退: 第一项")
        return True

    @staticmethod
    def _click_recommendation(context: Context, image, node: str, y_offset: int) -> bool:
        detail = context.run_recognition(node, image)
        if not (detail and detail.hit and detail.best_result):
            return False
        box = detail.best_result.box
        context.tasker.controller.post_click(
            box[0] + box[2] // 2,
            box[1] + box[3] // 2 + y_offset,
        ).wait()
        logger.info(f"奖励选择回退: {node}, marker_box={box}")
        return True


@AgentServer.custom_action("ProduceHIFChangeAuto")
class ProduceHIFChangeAuto(ProduceHIFAttributeBase):
    """Select the change destination and hand the next observed step back."""

    def _resolve_target_identities(self, context, cards):
        """Open only uncertain destinations; never press Next while probing."""
        from custom.hif import read_hif_change
        from custom.removal import read_selected_card, selected_card_box_matches
        from custom.deck_scan import scan_identity_known
        for item in cards:
            if not item.get('square_art_attempted') or scan_identity_known(item):
                continue
            if context.tasker.stopping:
                return None
            image = context.tasker.controller.post_screencap().wait().get()
            if shared._detect_disconnect_screen(context, image):
                return None
            boxes = read_hif_change(context, image)
            if not boxes or not any(intersection_over_union(item['box'], box) > .8 for box in boxes):
                return None
            ProduceChooseRewardAuto._click_card(context, item)
            previous, stable = None, 0
            for _ in range(4):
                if context.tasker.stopping:
                    return None
                time.sleep(.25)
                image = context.tasker.controller.post_screencap().wait().get()
                if shared._detect_disconnect_screen(context, image) or not read_hif_change(context, image):
                    return None
                if not selected_card_box_matches(image, item['box']):
                    previous, stable = None, 0
                    continue
                # The selected destination's detail panel is below the exchange
                # diagram and above the card row. Require a unique exact title.
                current = read_selected_card(context, image, roi=[80, 490, 560, 300])
                stable = stable+1 if current and current == previous else 1 if current else 0
                previous = current
                if stable >= 2:
                    item.update(current)
                    item['priority'] = rarity_priority(item['rarity'], item.get('extra_action', 'unknown'))
                    logger.info(f"换牌目标卡图兜底确认：{item['name']} box={item['box']}")
                    break
        return cards

    @staticmethod
    def _destination(evaluated: list, lists: dict) -> dict:
        black = set(lists["blacklist"]["cards"]) if lists["blacklist"]["enabled"] else set()
        white = set(lists["whitelist"]["cards"]) if lists["whitelist"]["enabled"] else set()
        allowed = [item for item in evaluated if item["name_key"] not in black] or evaluated
        preferred = [item for item in allowed if item["name_key"] in white] or allowed
        return max(preferred, key=ProduceChooseRewardAuto._selection_key)

    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        from custom.hif import read_hif_change
        from custom.deck_scan import scan_identity_known

        reward = ProduceChooseRewardAuto()
        selected = None
        before = previous = None
        next_clicked = False
        target_clicked = False
        clicks = missing = 0
        deadline = time.monotonic() + 14
        for _ in range(24):
            if time.monotonic() >= deadline:
                break
            if context.tasker.stopping:
                return True
            image = context.tasker.controller.post_screencap().wait().get()
            if shared._detect_disconnect_screen(context, image):
                return True
            boxes = read_hif_change(context, image)
            if not boxes:
                # Disappearance alone can be animation. Require two changed
                # observations before declaring this step (not cultivation) done.
                changed = before is not None and image.shape == before.shape and np.abs(
                    image[280:1000:4, 70:650:4].astype(np.float32)
                    - before[280:1000:4, 70:650:4].astype(np.float32)
                ).mean() > 8
                missing = missing + 1 if next_clicked and changed else 0
                if missing >= 2:
                    logger.success("HIF换卡：已确认目标牌选择生效，重新识别后续页面")
                    return True
                time.sleep(.65)
                continue
            missing = 0
            evaluated = reward._evaluate(context, image, verified_boxes=boxes)
            if not evaluated:
                previous = None
                time.sleep(.45)
                continue
            signature = reward._scene_signature(image, evaluated)
            if not reward._reward_frames_stable(previous, signature):
                previous = signature
                time.sleep(.45)
                continue
            if selected is None:
                evaluated = self._resolve_target_identities(context, evaluated)
                if not evaluated:
                    return True
                if any(item.get('square_art_attempted') and not scan_identity_known(item) for item in evaluated):
                    logger.warning("换牌目标卡图与详情仍未全部确认，保留当前页面稍后重识别")
                    return True
                selected = self._destination(evaluated, shared._load_card_lists())
                inspect_start = time.monotonic()
                fresh = context.tasker.controller.post_screencap().wait().get() if needs_deck_check(selected) else image
                selected, restored = reward._limit_acquisition(context, selected, evaluated, fresh, shared._load_card_lists())
                deadline += time.monotonic() - inspect_start
                if selected is None or not restored:
                    return True
                image = context.tasker.controller.post_screencap().wait().get() if single_copy_cards() else image
                logger.info(f"HIF换卡目标：{selected['name']} rarity={selected['rarity']} "
                            f"extra_action={selected['extra_action']} box={selected['box']}")
            # The disabled Next button is brown; the enabled control is bright
            # orange. Require both the freshly recognized label and enabled fill.
            buttons = self._ocr(context, image, ["次へ"], [220, 1040, 280, 120])
            fill = image[1070:1100, 280:450].astype(np.int16)
            b, g, r = fill[:, :, 0], fill[:, :, 1], fill[:, :, 2]
            enabled = ((r >= 220) & (g >= 80) & (g <= 190) & (b <= 130)).mean() >= .4
            if context.tasker.stopping:
                return True
            if buttons and enabled and target_clicked:
                before = image.copy()
                self._click_box(context, list(buttons[0].box), double=False)
                next_clicked = True
            elif not next_clicked:
                reward._click_card(context, selected)
                target_clicked = True
            else:
                time.sleep(.65)
                continue
            clicks += 1
            if clicks >= 6:
                break
            time.sleep(.8)
        if context.tasker.stopping:
            return True
        now = time.monotonic()
        if now - getattr(self, "_target_retry_log", float("-inf")) >= 30:
            logger.warning("HIF换卡：目标页暂未确认推进，稍后重识别；任务继续运行，不取消换牌")
            self._target_retry_log = now
        return True  # JumpBack to HIF entry; this is not exchange success.


@AgentServer.custom_action("ProduceHIFChangeSourceAuto")
class ProduceHIFChangeSourceAuto(ProduceHIFAttributeBase):
    """Choose a disposable owned card using the shared removal policy."""

    @staticmethod
    def _choose_source_candidate(candidates: list, blacklist: set, whitelist: set, replacement: set) -> Optional[dict]:
        from .shop import ProduceShoppingAuto
        return ProduceShoppingAuto._choose_removal_candidate(candidates, blacklist, whitelist, replacement)

    @staticmethod
    def _boxes(results: list) -> list:
        boxes = []
        for result in results:
            box = [int(value) for value in result.box]
            x, y, width, height = box
            # Native first-page detections start at y=635..637 below the
            # y=580..603 source prompt. The former y>=655 cutoff dropped the
            # entire first row; the destination at y=97 remains excluded.
            if not (60 <= x and x + width <= 660 and 620 <= y and y + height <= 1100
                    and 90 <= width <= 145 and 90 <= height <= 145 and .8 <= width / height <= 1.2):
                continue
            if not any(intersection_over_union(box, other) >= .45 for other in boxes):
                boxes.append(box)
        return sorted(boxes, key=lambda box: (box[1], box[0]))

    @staticmethod
    def _source_frames_stable(previous, current) -> bool:
        """Match deck tiles spatially: one-pixel row jitter can reorder them."""
        if not previous or not current or len(previous) != len(current):
            return False
        remaining = list(current)
        paired = []
        for old_box, _ in previous:
            matches = [index for index, (box, _) in enumerate(remaining)
                       if max(abs(a - b) for a, b in zip(old_box, box)) <= 3]
            # Only a unique, unchanged complete tile may be paired. Missing,
            # moved or overlapping detections require another observation.
            if len(matches) != 1:
                return False
            paired.append(remaining.pop(matches[0]))
        return ProduceChooseRewardAuto._reward_frames_stable(previous, tuple(paired))

    def _page(self, context: Context, *, change=True) -> Optional[tuple]:
        from custom.hif import is_hif_change_source

        from .shop import ProduceShoppingAuto

        reward = ProduceChooseRewardAuto()
        previous = None
        # Both modes need the same stable pair. Allow the opening animation
        # to finish even in strict priority mode; a stable page exits early.
        for _ in range(15):
            if context.tasker.stopping:
                return None
            image = context.tasker.controller.post_screencap().wait().get()
            if shared._detect_disconnect_screen(context, image):
                self._interrupted = True
                return None
            if change:
                if not is_hif_change_source(context, image):
                    return None
                detection = context.run_recognition("ProduceRecognitionCards", image)
                boxes = self._boxes(detection.all_results) if detection and detection.hit else []
                from custom.deck_scan import complete_grid_rows
                boxes = complete_grid_rows(image, boxes, top=620)
                # Wait for geometry/art to settle before the expensive visual
                # identity pass. YOLO result order is not card identity.
                signature = reward._scene_signature(image, [{"box": box} for box in boxes])
                if boxes and self._source_frames_stable(previous, signature):
                    evaluated = reward._evaluate(context, image, verified_boxes=boxes, read_extra_action=False, diagnostics=False)
                    from custom.square_identity import resolve_selected
                    evaluated = resolve_selected(context, image, evaluated, change=True)
                    from custom.deck_scan import deck_card_disabled
                    evaluated = [dict(item, selectable=not deck_card_disabled(image, item['box']))
                                 for item in evaluated]
                    self._scroll_geometry = (boxes, 620)
                    return image, evaluated
            else:
                flag = context.run_recognition("ProduceChooseDeleteFlag", image, pipeline_override={
                    "ProduceChooseDeleteFlag": {"template": ["produce/delete_icon.png"],
                                                "focus": {}}})
                if not (flag and flag.hit):
                    return None
                shop = ProduceShoppingAuto()
                boxes = shop._delete_card_boxes(context, image)
                signature = reward._scene_signature(image, [{"box": box} for box in boxes])
                if boxes and self._source_frames_stable(previous, signature):
                    evaluated = shop._recognize_delete_cards(context, image, verified_boxes=boxes)
                    self._scroll_geometry = (boxes, 380)
                    return image, evaluated
            previous = signature
            if _ == 4:
                logger.info(f"{'换出' if change else '删除'}：牌库仍在变化，继续原地等待稳定，不点击、不翻页")
            time.sleep(.25)
        logger.warning(f"{'换出' if change else '删除'}牌组连续观察未稳定，停止当前动作并保留现场")
        return None

    @staticmethod
    def _same_page(first, second, *, top=620, first_boxes=None, second_boxes=None) -> bool:
        if first_boxes or second_boxes:
            from custom.deck_scan import same_deck_view
            return same_deck_view(first, second, first_boxes, second_boxes, top=top)
        if first.shape != second.shape:
            return False
        return np.abs(first[top:1090:4, 60:650:4].astype(np.float32)
                      - second[top:1090:4, 60:650:4].astype(np.float32)).mean() < 3

    def _scroll(self, context: Context, *, forward: bool) -> bool:
        from custom.deck_scan import deck_scroll_distance
        from maa.pipeline import JSwipe
        if context.tasker.stopping:
            return False
        boxes, top = getattr(self, '_scroll_geometry', ([], 620))
        distance = deck_scroll_distance(boxes, top=top, overlap=bool(single_copy_cards()))
        start, end = (1088, 1088-distance) if forward else (642, 642+distance)
        # PostSwipe releases immediately at full velocity. Unity's scroll
        # inertia can then carry the grid past an unseen row. Native end_hold
        # keeps the pointer down at the endpoint until that velocity decays.
        result = context.run_action_direct('Swipe', JSwipe(
            begin=[650, start, 1, 1], end=[[650, end, 1, 1]],
            duration=[900], end_hold=[350]))
        time.sleep(.35)
        return bool(result and result.success)

    def _rewind_source(self, context, *, change=True):
        """Resume from an observed top, never treat a paused bottom as page one."""
        from custom.deck_scan import deck_view_complete, scan_identity_known, deck_scrollbar_at_top

        from .shop import ProduceShoppingAuto
        if context.tasker.stopping:
            return None
        top = 620 if change else 380
        current = self._page(context) if change else self._page(context, change=False)
        if current is None:
            return None
        policy = getattr(self, '_removal_search_policy', None)
        if policy is not None:
            black, white, replacement, strict = policy
            choose = self._choose_source_candidate if change else ProduceShoppingAuto._choose_delete_candidate
            candidate = choose([item for item in current[1] if scan_identity_known(item)], black, white, replacement)
            if candidate and not single_copy_cards() and (not strict or candidate['delete_rank'] == 3):
                # No rewind is needed to establish optimality of an eligible
                # N, or to accept a fast-mode target already on screen.
                return current
        if deck_view_complete(current[0], [item['box'] for item in current[1]], change=change):
            logger.info(f"{'换出' if change else '删除'}搜索：完整牌库已显示（{len(current[1])} 张），不回顶、不试滑")
            return current
        at_top = deck_scrollbar_at_top(current[0], top=top)
        if at_top is True:
            return current
        saw_below_top = at_top is False
        stationary = 0
        for _ in range(6):
            if context.tasker.stopping:
                return None
            previous = current
            if self._scroll(context, forward=False) is False:
                return None
            current = self._page(context) if change else self._page(context, change=False)
            if current is None:
                return None
            at_top = deck_scrollbar_at_top(current[0], top=top)
            if at_top is True:
                logger.info(f"{'换出' if change else '删除'}搜索：已回到牌库顶部，开始全局筛选")
                return current
            saw_below_top = saw_below_top or at_top is False
            # Hidden/no scrollbar (including a deck fitting one view) needs
            # repeated unchanged observations. A visible lower thumb defeats
            # that fallback: swallowed drags must not masquerade as the top.
            if not saw_below_top and self._same_page(previous[0], current[0], top=top,
                                                    first_boxes=[item['box'] for item in previous[1]],
                                                    second_boxes=[item['box'] for item in current[1]]):
                stationary += 1
                if stationary >= 2:
                    return current
            else:
                stationary = 0
        logger.warning(f"{'换出' if change else '删除'}搜索：回到顶部未确认，不从局部牌库提交换出/删除")
        return None

    def _submit(self, context: Context, selected: dict) -> bool:
        from custom.hif import is_hif_change_source
        from custom.removal import read_selected_card, selected_card_box_matches
        from custom.deck_scan import deck_card_disabled

        self._source_unavailable = False
        if context.tasker.stopping:
            return True
        image = context.tasker.controller.post_screencap().wait().get()
        if shared._detect_disconnect_screen(context, image):
            self._interrupted = True
            return False
        if not is_hif_change_source(context, image):
            return False
        if deck_card_disabled(image, selected['box']):
            self._source_unavailable = True
            logger.info(f"换出候选「{selected['name']}」已置灰，重新筛选可选牌")
            return False
        if not selected.get("detail_selected"):
            # Resuming on an already-selected tile must not click it again:
            # this UI toggles selection off on a second click.
            if not selected_card_box_matches(image, selected['box']):
                ProduceChooseRewardAuto._click_card(context, selected)
        logger.info(f"HIF换出牌：{selected['name']} rarity={selected['rarity']} "
                    f"删除优先级={selected['delete_rank']} box={selected['box']}")
        clicked = False
        identity_frames = 0
        before = None
        missing = 0
        for _ in range(10):
            time.sleep(.6)
            if context.tasker.stopping:
                return True
            image = context.tasker.controller.post_screencap().wait().get()
            if shared._detect_disconnect_screen(context, image):
                return True
            if not is_hif_change_source(context, image):
                changed = before is not None and image.shape == before.shape and not self._same_page(before, image)
                missing = missing + 1 if clicked and changed else 0
                if missing >= 2:
                    logger.success("HIF换卡：已提交换出牌并确认页面推进")
                    return True
                continue
            missing = 0
            if clicked:
                continue
            if deck_card_disabled(image, selected['box']):
                self._source_unavailable = True
                logger.info(f"换出候选「{selected['name']}」已置灰，重新筛选可选牌")
                return False
            exact = read_selected_card(context, image, change=True) if selected_card_box_matches(image, selected['box']) else None
            identity_frames = identity_frames + 1 if exact and exact["name_key"] == selected["name_key"] and exact["rarity"] == selected["rarity"] else 0
            if identity_frames < 2:
                continue
            buttons = self._ocr(context, image, ["チェンジ"], [365, 1100, 290, 130])
            pixels = image[1140:1170, 420:585].astype(np.int16)
            b, g, r = pixels[:, :, 0], pixels[:, :, 1], pixels[:, :, 2]
            enabled = ((r >= 220) & (80 <= g) & (g <= 190) & (b <= 130)).mean() >= .4
            if buttons and enabled and not context.tasker.stopping:
                before = image.copy()
                self._click_box(context, list(buttons[0].box), double=False)
                clicked = True
        logger.warning("HIF换卡：换出提交尚未确认，不重复提交，交回主流程重识别")
        return False

    def _defer_source(self, context, reason):
        """A recognition miss is recoverable, not the end of cultivation.

        The HIF entry revisits this source scene via JumpBack. Return control
        without clicking Cancel or declaring a successful exchange. Each pass
        stays bounded and the stop button remains effective.
        """
        if not context.tasker.stopping and not self._interrupted:
            now = time.monotonic()
            previous = getattr(self, "_source_retry_log", None)
            if previous is None or previous[0] != reason or now - previous[1] >= 30:
                logger.warning(f"HIF换卡：{reason}，稍后重识别；任务继续运行，不取消换牌")
                self._source_retry_log = (reason, now)
            time.sleep(1.)
        return True

    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        self._interrupted = False
        lists = shared._load_card_lists()
        black = set(lists["blacklist"]["cards"]) if lists["blacklist"]["enabled"] else set()
        white = set(lists["whitelist"]["cards"]) if lists["whitelist"]["enabled"] else set()
        replacement_section = lists.get("replacementList", {})
        replacement = set(replacement_section.get("cards", [])) if replacement_section.get("enabled") else set()
        self._removal_search_policy = (black, white, replacement, False)
        for _ in range(2):
            initial_page = self._rewind_source(context)
            if initial_page is None:
                return self._defer_source(context, "牌库画面暂未稳定")
            selected = self._scan_source(context, black, white, replacement, initial_page=initial_page,
                                         strict=False)
            if selected is None:
                return self._defer_source(context, "本次扫描未定位到可换候选")
            if self._submit(context, selected):
                self._source_retry_log = None
                return True
            if not getattr(self, '_source_unavailable', False):
                break  # Uncertain submission must never be retried here.
        return self._defer_source(context, "候选核名或提交结果尚未确认")

    def _scan_source(self, context, black, white, replacement, *, change=True, initial_page=None, strict=True):
        from custom.deck_scan import DeckIdentityCache, deck_view_complete, scan_identity_known, removal_priority_bound, deck_scrollbar_position

        from .shop import ProduceShoppingAuto
        logger.info(f"{'换出' if change else '删除'}搜索：{'严格优先级' if strict else '快速命中'}模式")
        from custom.card_copies import RemovalCopies
        limited = single_copy_cards()
        copies = RemovalCopies(limited) if limited else None
        counts = {}
        identities = DeckIdentityCache()
        shop = ProduceShoppingAuto()
        shop._disconnect_detected = False
        choose = self._choose_source_candidate if change else shop._choose_delete_candidate
        best = None
        best_page = 0
        page = -1
        deadline = time.monotonic() + 90
        previous_image = None
        previous_boxes = None
        reached_bottom = False
        stationary_swipes = 0
        observations = []
        for scan in range(6):
            if time.monotonic() >= deadline:
                break
            current = initial_page if scan == 0 and initial_page is not None else (
                self._page(context) if change else self._page(context, change=False))
            if current is None:
                return None
            image, evaluated = current
            whole_deck = deck_view_complete(image, [item['box'] for item in evaluated], change=change)
            if previous_image is not None and self._same_page(previous_image, image, top=620 if change else 380,
                                                              first_boxes=previous_boxes,
                                                              second_boxes=[item['box'] for item in evaluated]):
                stationary_swipes += 1
                if stationary_swipes >= 2:
                    thumb = deck_scrollbar_position(image, top=620 if change else 380)
                    if thumb is None or thumb[1] >= 1080:
                        reached_bottom = True
                        break
                    # Two swallowed forward drags are not the bottom when
                    # the visible scrollbar still proves there is content
                    # below. Keep the existing bounded scan, then fail closed.
                    if scan == 5:
                        break
                # One failed drag is not proof that the deck has ended.
                if self._scroll(context, forward=True) is False:
                    return None
                continue
            stationary_swipes = 0
            page += 1
            evaluated = identities.restore(image, evaluated)
            reused = sum(bool(item.get("identity_verified")) for item in evaluated)
            shop._deck_identity_cache = identities
            shop._deck_image = image
            evaluated = shop._probe_removal_candidates(context, evaluated, black, white, replacement,
                                                       change=change, strict=strict, incumbent=best)
            identities.remember(image, evaluated)
            observations.append((image, [dict(item, detail_selected=False) for item in evaluated]))
            if shop._disconnect_detected:
                self._interrupted = True
                return None
            if copies:
                counts = copies.observe(image, evaluated)
                evaluated = with_owned_counts(evaluated, counts)
                # A second copy can prove that a card on an earlier page was
                # surplus. Re-rank all recorded pages with this transaction's
                # lower-bound counts, then reacquire its current position.
                best = None
                for number, (saved_image, items) in enumerate(observations):
                    known_items = [c for c in identities.restore(saved_image, items) if scan_identity_known(c)]
                    choice = choose(with_owned_counts(known_items, counts), black, white, replacement)
                    if choice and (best is None or shop._removal_key(choice) > shop._removal_key(best)):
                        best, best_page = choice, number
            verified = [item for item in evaluated if scan_identity_known(item)]
            candidate = choose(verified, black, white, replacement)
            if candidate and (best is None or ProduceShoppingAuto._removal_key(candidate) > ProduceShoppingAuto._removal_key(best)):
                best, best_page = candidate, page
            if candidate and candidate["delete_rank"] == 4:
                logger.info(f"优先清理限一牌的多余副本「{candidate['name']}」")
                return candidate
            if candidate and not limited and candidate["delete_rank"] == 3:
                logger.info(f"{'换出' if change else '删除'}搜索：已核实最高优先级 N 卡「{candidate['name']}」，停止翻页")
                return candidate
            if candidate and not strict and not limited:
                logger.info(f"快速命中「{candidate['name']}」，停止扫描，进入最终核名")
                return candidate
            logger.info(f"{'换出' if change else '删除'}搜索：第 {page + 1} 页候选={len(evaluated)} "
                        f"已核实复用={reused} 详情点击={getattr(shop, '_deck_probe_count', 0)} "
                        f"高可信免点={sum(scan_identity_known(item) and not item.get('identity_verified') for item in evaluated)} "
                        f"身份未确认={len(evaluated) - len(verified)} "
                        f"本页最优={candidate['name'] if candidate else '无'} "
                        f"全局最优={best['name'] if best else '无'}")
            if whole_deck:
                reached_bottom = True
                break
            if scan == 5:
                break
            if context.tasker.stopping:
                return None
            previous_image = image
            previous_boxes = [item['box'] for item in evaluated]
            if self._scroll(context, forward=True) is False:
                return None
        if context.tasker.stopping:
            return None
        if not reached_bottom and best is not None:
            logger.warning("换出/删除牌库扫描达到安全上限但尚未确认底部，保留现场，不提交低优先级牌")
            return None
        if reached_bottom:
            logger.info(f"{'换出' if change else '删除'}搜索：已确认牌库扫描完整，共扫描 {page + 1} 个视区")
        if not change and best is None:
            # No permitted target will be submitted. Replaying every old-page
            # identity cannot change that decision and only delays Cancel.
            logger.info("删除搜索：没有已识别的 N、黑名单或 R，跳过删除")
            return None
        # A card unreadable on an earlier overlapping page may have been
        # verified later. Reconcile every recorded observation, not a sticky
        # page-level failure bit that discards that newly acquired evidence.
        unresolved_pages = {number for number, (saved_image, items) in enumerate(observations)
                            if any(item.get('selectable') is not False and not scan_identity_known(item) and
                                   removal_priority_bound(item, black, replacement, change=change) >
                                   (ProduceShoppingAuto._removal_key(best)[:2] if best else (-1, 0))
                                   for item in identities.restore(saved_image, items))}
        if unresolved_pages:
            pages = [number + 1 for number in sorted(unresolved_pages)]
            if not change:
                # Keep the shop's irreversible deletion policy unchanged.
                logger.info(f"删除搜索：第 {pages} 页仍有卡牌身份未确认，本次跳过删除，保护卡牌")
                return None
            logger.info(f"换出搜索：第 {pages} 页存在未识别牌，保护未知牌；"
                        f"继续按已识别候选的全局最优「{best['name'] if best else '无'}」处理")
        if best is None:
            logger.info("换出搜索：未找到已识别的 N、黑名单、R 或替换名单卡，保护其余牌" if change
                        else "删除搜索：没有已识别的 N、黑名单或 R，跳过删除")
            return None
        # Walk back from the observed bottom, matching identity at each stop.
        # Never jump by a guessed page number: the clamped final swipe is
        # shorter and reversing it can overshoot a bookmarked first-row card.
        # Reacquisition is read-only; do NOT probe every unrelated card again.
        logger.info(f"换出/删除定位：返回查找「{best['name']}」，原记录第 {best_page + 1} 页")
        for retry in range(7):
            if context.tasker.stopping or time.monotonic() >= deadline:
                return None
            evaluated = identities.restore(image, evaluated)
            if copies:
                evaluated = with_owned_counts(evaluated, counts)
            matches = [item for item in evaluated if item["name_key"] == best["name_key"]
                       and item["rarity"] == best["rarity"] and scan_identity_known(item)]
            selected = choose(matches, black, white, replacement)
            if selected:
                return selected
            # Cache miss: at most probe the visually named target, not the
            # entire page. Its exact identity must still match the bookmark.
            possible = [item for item in evaluated if item.get("name_key") == best["name_key"]
                        and item.get('selectable') is not False
                        and item.get("name_confidence", 0) >= .55]
            if possible:
                shop._deck_image = image
                checked = shop._probe_removal_candidates(context, possible[:1], black, white, replacement, change=change)
                if shop._disconnect_detected:
                    self._interrupted = True
                    return None
                exact = [item for item in checked if item.get("identity_verified")
                         and item.get("name_key") == best["name_key"] and item.get("rarity") == best["rarity"]]
                selected = choose(with_owned_counts(exact, counts) if copies else exact, black, white, replacement)
                if selected:
                    return selected
            if retry == 6:
                break
            if self._scroll(context, forward=False) is False:
                return None
            current = self._page(context) if change else self._page(context, change=False)
            if current is None:
                return None
            image, evaluated = current
        logger.warning("换出/删除牌滚动返回后未重新确认目标，不使用旧坐标")
        return None

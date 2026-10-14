from ..frame_geometry import reference_frame, same_card_slot
import json
import os
import time
import random
from typing import Any, Dict, List, Optional

import numpy as np

from utils import logger
from maa.context import Context
from maa.custom_action import CustomAction
from maa.agent.agent_server import AgentServer
from ..reward_counts import RewardActionBudget, parse_remaining
from ..reward_layout import reward_card_row
from ..params import action_params
from ..battle_drinks import BattleDrinkSession
from ..drink_policy import TARGET_NAMES
from ..card_rules import RuleStore, rule_for, allowed as rule_allowed, sort_key as rule_sort_key

from ..card_visual import (
    classify_rarity,
    intersection_over_union,
    match_card_catalog,
    match_card_visual,
    normalize_card_name,
    rarity_priority,
    reconcile_visual_identity,
)


CARD_LISTS_PATH = os.path.join("config", "card_lists.json")
_DIAGNOSTIC_STATE = {"mtime": None, "enabled": False}
_REWARD_BUDGETS = {action: RewardActionBudget() for action in ("remove", "refresh")}


def _reset_produce_card_run_state() -> None:
    """Reset consumable reward actions exactly once for a new Produce run."""
    _REWARD_BUDGETS.update({action: RewardActionBudget() for action in ("remove", "refresh")})


def _diagnostics_enabled() -> bool:
    """Read the user-facing diagnostics switch; missing/invalid means off."""
    try:
        mtime = os.path.getmtime(CARD_LISTS_PATH)
        if _DIAGNOSTIC_STATE["mtime"] != mtime:
            with open(CARD_LISTS_PATH, "r", encoding="utf-8") as stream:
                value = json.load(stream)
            _DIAGNOSTIC_STATE["mtime"] = mtime
            _DIAGNOSTIC_STATE["enabled"] = bool(
                value.get("debugDiagnosticsEnabled", False)
                if isinstance(value, dict)
                else False
            )
    except Exception:
        _DIAGNOSTIC_STATE["mtime"] = None
        _DIAGNOSTIC_STATE["enabled"] = False
    return bool(_DIAGNOSTIC_STATE["enabled"])


def _diagnostic_info(message: str) -> None:
    """Keep verbose recognition details out of the normal user log."""
    if _diagnostics_enabled():
        logger.info(message)
    else:
        logger.debug(message)


def _load_card_lists() -> dict:
    """Load the user-managed lists without ever blocking cultivation."""
    empty = {
        "blacklist": {"enabled": False, "cards": []},
        "whitelist": {"enabled": False, "cards": []},
        "replacementList": {"enabled": False, "cards": []},
    }
    try:
        with open(CARD_LISTS_PATH, "r", encoding="utf-8") as stream:
            value = json.load(stream)
        if isinstance(value, dict):
            presets = value.get("presets", {})
            active = presets.get(value.get("activePreset")) if isinstance(presets, dict) else None
            if isinstance(active, dict):
                value = active
        result = {}
        for list_name in ("blacklist", "whitelist", "replacementList"):
            section = value.get(list_name, {}) if isinstance(value, dict) else {}
            cards = section.get("cards", []) if isinstance(section, dict) else []
            normalized = sorted(
                {
                    normalize_card_name(card)
                    for card in cards
                    if normalize_card_name(card)
                }
            )
            result[list_name] = {
                "enabled": bool(section.get("enabled", False)),
                "cards": normalized,
            }
        return result
    except Exception as error:
        logger.warning(f"读取卡牌黑白名单失败，已按未启用继续: {error}")
        return empty


@AgentServer.custom_action("ProduceLoadCardLists")
class ProduceLoadCardLists(CustomAction):
    """Print one deterministic list summary when a Produce task starts."""

    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        from ..memory_modes import initialize
        try:
            initialize(context)
        except (ValueError, TypeError) as error:
            logger.warning(f"培育模式设置无效：{error}；请先选择目标再开始")
            return False
        from ..hif import configure_hif_start_routes
        if configure_hif_start_routes(context):
            logger.info("HIF本战入口：已接入开始确认页，保留道具开关与准备步骤")
        loop = (context.get_node_data("ProduceLoop") or {}) if context is not None else {}
        limit = loop.get("max_hit")
        logger.info(f"本次任务培育次数: {limit if isinstance(limit, int) and 0 < limit < 4294967295 else '不限'}；"
                    "继续中的当前局计入一次，结算返回主页后检查次数，再决定是否准备下一局")
        lists = _load_card_lists()
        black = lists["blacklist"]
        white = lists["whitelist"]
        black_names = "/".join(black["cards"]) if black["cards"] else "（空）"
        white_names = "/".join(white["cards"]) if white["cards"] else "（空）"
        logger.success(
            f"已载入当前黑名单（{'启用' if black['enabled'] else '未启用'}）：{black_names}"
        )
        logger.success(
            f"白名单（{'启用' if white['enabled'] else '未启用'}）：{white_names}"
        )
        replacement = lists.get("replacementList", {"enabled": False, "cards": []})
        logger.success(f"替换名单（{'启用' if replacement['enabled'] else '未启用'}）："
                       + ("/".join(replacement["cards"]) or "（空）"))
        return True


@AgentServer.custom_action("ProduceResetRewardCounts")
class ProduceResetRewardCounts(CustomAction):
    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        _reset_produce_card_run_state()
        ProduceShoppingAuto._visit_state(context, exit_pending=False, heal_pending=False)
        ProduceChooseRewardAuto._discard_receipt_state(context, write=True)
        # max_hit is task-wide in MaaFramework; these are per-preparation
        # search budgets, unlike ProduceLoop's user-selected round count.
        for node in ("ProduceLastPage", "ProduceNextPage"):
            context.clear_hit_count(node)
        logger.info("新培育：重置奖励操作预算，等待本局剩余次数识别")
        return True


@AgentServer.custom_action("ProduceRecoverConnectionAuto")
class ProduceRecoverConnectionAuto(CustomAction):
    """Handle network retry and date-change dialogs without guessing state."""

    NETWORK_RETRY_POINT = (515, 1160)
    NETWORK_RETRY_POINTS = (
        NETWORK_RETRY_POINT,
        (570, 1160),
        (625, 1160),
        (480, 1160),
        (570, 1125),
        (625, 1125),
    )
    NETWORK_RETRY_RESET_SECONDS = 8.0
    DATE_CHANGE_POINT = (360, 1170)
    _network_retry_index = 0
    _network_retry_last_at = 0.0

    @classmethod
    def _next_network_retry_point(cls) -> tuple[tuple[int, int], int]:
        now = time.monotonic()
        if now - cls._network_retry_last_at > cls.NETWORK_RETRY_RESET_SECONDS:
            cls._network_retry_index = 0
        index = cls._network_retry_index % len(cls.NETWORK_RETRY_POINTS)
        cls._network_retry_index = (index + 1) % len(cls.NETWORK_RETRY_POINTS)
        cls._network_retry_last_at = now
        return cls.NETWORK_RETRY_POINTS[index], index

    @classmethod
    def _reset_network_retry_points(cls) -> None:
        cls._network_retry_index = 0
        cls._network_retry_last_at = 0.0

    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        image = context.tasker.controller.post_screencap().wait().get()

        # Communication errors can weakly resemble the orange date-change
        # banner. Always give the dedicated network template first refusal.
        network_error = context.run_recognition("ProduceDisconnectNetworkFlag", image)
        if network_error and network_error.hit:
            point, index = self._next_network_retry_point()
            if index == 0:
                logger.warning("网络重试：检测到通信错误，先点击右侧标准重试点")
            else:
                logger.warning(
                    f"网络重试：弹窗仍存在，枚举右侧重试按钮安全点 {index + 1}/"
                    f"{len(self.NETWORK_RETRY_POINTS)}: {point}"
                )
            context.tasker.controller.post_click(*point).wait()
            return True

        date_change = context.run_recognition("ProduceDateChangeFlag", image)
        if date_change and date_change.hit:
            self._reset_network_retry_points()
            logger.warning("换日恢复：检测到日期变更，点击返回标题按钮")
            context.tasker.controller.post_click(*self.DATE_CHANGE_POINT).wait()
            return True

        self._reset_network_retry_points()
        logger.warning("异常恢复：弹窗在点击前已经变化，跳过点击并重新识别当前页面")
        return True


def _save_card_diagnostic(image, scene: str, details: list) -> None:
    """Persist only uncertain live decisions as lossless raw arrays for review."""
    if not _diagnostics_enabled():
        return
    try:
        directory = os.path.join("debug", "card_strategy")
        os.makedirs(directory, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
        base = os.path.join(directory, f"{stamp}-{scene}")
        np.save(base + ".npy", image, allow_pickle=False)
        with open(base + ".json", "w", encoding="utf-8") as stream:
            json.dump(details, stream, ensure_ascii=False, indent=2)
        logger.warning(f"已保存低置信度卡牌现场: {base}.npy")
    except Exception as error:
        logger.warning(f"保存卡牌现场失败: {error}")


def _detect_disconnect_screen(context: Context, image) -> Optional[str]:
    """Detect the communication-error overlay inside a long custom action.

    Long-running custom actions must yield back to the pipeline when the game
    leaves their scene.  In particular, the blurred battle background behind
    the communication dialog still contains a readable health bar, so battle
    detection alone can otherwise wait forever.

    Do not test title/home here.  This helper runs in the hot path before every
    card decision and shop screenshot; two extra full-screen template matches
    made normal decisions take tens of seconds.  Once the error overlay is
    detected, the outer pipeline owns title/login/home recognition.
    """
    if image is not None and image.shape[1] > image.shape[0]:
        return None
    node = "ProduceDisconnectError"
    detail = context.run_recognition(node, image)
    if detail and detail.hit:
        return node
    return None


class ProduceChooseEventBase(CustomAction):
    """
    培育事件选择基类。

    子类需定义以下类属性：
    - SUGGESTION_CONFIG: 老师建议匹配配置
    - EVENT_CONFIG: 事件图片配置
    - RUN_TASK_MAP: 需要执行run_task的事件映射

    可覆盖的钩子方法：
    - _get_preference: 获取属性偏好设置
    - _choose_extra_before_attrs: SP之后、属性课程之前的额外优先事件
    - _choose_extra_after_outing: 外出之后的额外优先事件
    """

    # 子类需覆盖
    SUGGESTION_CONFIG: dict = {}
    EVENT_CONFIG: dict = {}
    RUN_TASK_MAP: dict = {}
    SUGGESTION_ROI: list = [270, 160, 350, 80]
    REST_COUNT_ROI = [580, 755, 135, 75]
    SP_TEMPLATE = "produce/sp.png"
    SP_TEMPLATE_PARAMS = {}

    # 阈值常量
    CLICK_DELAY = 0.5
    ACTION_DELAY = 3.0
    PREFERENCE_LIST = ["Da", "Vi", "Vo"]
    FIRST_NEAR_FULL_RATIO = 0.85
    ATTR_STOP_RATIO = 0.8
    LOW_HEALTH_RATIO = 0.2
    LOW_HEALTH_VALUE = 8

    def __init__(self):
        super().__init__()
        self.first = "Vi"
        self.second = "Da"

    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        logger.success("事件: 选择事件")

        preference = self._get_preference(context, argv)
        self.first = preference["first"]
        self.second = preference["second"]
        logger.info(f"第一属性: {self.first}, 第二属性: {self.second}")

        image = self._get_screenshot(context)

        suggestion = ""
        if context.get_node_data("ProduceSuggestion").get("enabled", True):
            suggestion = self._get_suggestion(context, image)

        health_data = self._get_health(context, image) or {"current": 34, "max": 34, "ratio": 1.0}
        points = self._get_current_points(context, image) or 0
        score = self._get_current_score(context, image) or {"Vo": 0, "Da": 0, "Vi": 0, "max": 1}
        events = self._get_available_events(context, image)

        best_event = self._choose_best_event(suggestion, health_data, points, score, events)
        if not best_event:
            logger.info("无可用事件")
            return True

        # 休息前检查：次数用完（检测到"あと0回"）时不可休息，改选其他事件
        if best_event.get("run_task") == "ProduceChooseRest" and not self._is_rest_available(context, image):
            logger.warning("休息次数已用完，改选其他事件")
            best_event = self._choose_best_event(suggestion, health_data, points, score, events, allow_rest=False)
            if not best_event:
                logger.info("无可用事件")
                return True

        box = best_event["box"]
        logger.info(f"选择事件: {best_event['name']}, 坐标: ({box[0] + box[2] // 2}, {box[1] + box[3] // 2})")

        return self._execute_event(context, best_event)

    def _get_preference(self, context: Context, argv: CustomAction.RunArg) -> dict:
        """获取偏好设置，默认从argv解析。"""
        try:
            params = json.loads(argv.custom_action_param)
            first = params.get("first") if isinstance(params, dict) else None
            second = params.get("second") if isinstance(params, dict) else None
            return {"first": first if first in self.PREFERENCE_LIST else "Vi", "second": second if second in self.PREFERENCE_LIST else "Da"}
        except (json.JSONDecodeError, AttributeError):
            logger.warning("偏好设置解析失败，使用默认值")
            return {"first": "Vi", "second": "Da"}

    def _is_rest_available(self, context: Context, image) -> bool:
        """检测休息是否可用：休息按钮剩余次数区域出现「あと0回」时不可休息。"""
        reco_detail = context.run_recognition(
            "ProduceRecognitionRestCount",
            image,
            pipeline_override={
                "ProduceRecognitionRestCount": {
                    "recognition": "OCR",
                    "roi": self.REST_COUNT_ROI,
                    "expected": ["あと[0０]回", "剩余[0０]次"],  # 兼容全角/半角 0
                }
            },
        )
        if reco_detail and reco_detail.hit:
            logger.info("休息次数已用完")
            return False
        return True

    def _choose_best_event(
        self, suggestion: str, health_data: dict, points: int, score: dict, events: list, allow_rest: bool = True
    ) -> Optional[dict]:
        """
        根据获取到的信息从可用事件中选择最佳事件。

        Returns:
            dict: {"name": str, "box": [x, y, w, h], "run_task": str}，None表示无事件可选
        """
        max_score = score.get("max", 1)
        first_ratio = score.get(self.first, 0) / max_score if max_score > 0 else 0
        second_ratio = score.get(self.second, 0) / max_score if max_score > 0 else 0
        is_first_near_full = first_ratio >= self.FIRST_NEAR_FULL_RATIO
        is_first_stopped = first_ratio >= self.ATTR_STOP_RATIO
        is_second_stopped = second_ratio >= self.ATTR_STOP_RATIO

        # 0. 低体力处理
        current_health = health_data["current"] if health_data else 0
        ratio_health = health_data["ratio"] if health_data else 1.0
        if current_health < self.LOW_HEALTH_VALUE or ratio_health < self.LOW_HEALTH_RATIO:
            go_out = self._find_event_by_name(events, "外出")
            if go_out and points >= 100:
                return self._make_event("外出", go_out)
            if allow_rest:
                logger.info("体力过低，选择休息")
                return {"name": "rest", "box": [0, 0, 0, 0], "run_task": "ProduceChooseRest"}
            # 休息不可用（次数用完），继续走后续优先级

        # 1. 老师建议
        suggestion_attr = self._parse_suggestion(suggestion)
        if suggestion_attr:
            event = self._find_attr_event(events, suggestion_attr)
            if event:
                return self._make_event(suggestion_attr, event)

        # 2. SP（第一属性，仅当属性未达80%时）
        if not is_first_stopped:
            event = self._find_attr_event(events, self.first, need_sp=True)
            if event:
                return self._make_event(f"{self.first}_SP", event)

        # 3. SP（第二属性，仅当属性未达80%时）
        if not is_second_stopped:
            event = self._find_attr_event(events, self.second, need_sp=True)
            if event:
                return self._make_event(f"{self.second}_SP", event)

        # 钩子：SP之后、属性课程之前的额外优先事件（NIA的"营业"）
        extra = self._choose_extra_before_attrs(events)
        if extra:
            return extra

        # 4. 第一属性课程（属性未达80%时）
        if not is_first_stopped:
            event = self._find_attr_event(events, self.first, need_sp=False)
            if event:
                return self._make_event(self.first, event)

        # 5. 第二属性课程（第一属性停止或快满时，且第二属性未达80%）
        if (is_first_stopped or is_first_near_full) and not is_second_stopped:
            event = self._find_attr_event(events, self.second, need_sp=False)
            if event:
                return self._make_event(self.second, event)

        # 6. 交谈/活动（优先交谈，不满足则活动）
        if points >= 100:
            event = self._find_event_by_name(events, "交谈")
            if event:
                return self._make_event("交谈", event)
        event = self._find_event_by_name(events, "活动")
        if event:
            return self._make_event("活动", event)

        # 7. 上课（部分子类无此事件，由EVENT_CONFIG控制）
        event = self._find_event_by_name(events, "上课")
        if event:
            return self._make_event("上课", event)

        # 8. 外出
        event = self._find_event_by_name(events, "外出")
        if event:
            return self._make_event("外出", event)

        # 钩子：外出之后、其他属性SP之前的额外优先事件（NIA的"指导"）
        extra = self._choose_extra_after_outing(events)
        if extra:
            return extra

        # 9. SP（其他属性）
        for attr in ["Vo", "Da", "Vi"]:
            if attr not in [self.first, self.second]:
                event = self._find_attr_event(events, attr, need_sp=True)
                if event:
                    return self._make_event(f"{attr}_SP", event)

        # 保底：从剩余事件中随机选择
        if events:
            fallback = self._find_any_event(events)
            if fallback:
                logger.warning(f"所有优先级未命中，保底选择: {fallback['name']}")
                return fallback

        return None

    def _choose_extra_before_attrs(self, events: list) -> Optional[dict]:
        """钩子：SP之后、属性课程之前的额外优先事件。子类可覆盖。"""
        return None

    def _choose_extra_after_outing(self, events: list) -> Optional[dict]:
        """钩子：外出之后的额外优先事件。子类可覆盖。"""
        return None

    def _execute_event(self, context: Context, event: dict) -> bool:
        """执行事件：双击坐标，等待动画后执行后续任务（如果有）。"""
        run_task = event.get("run_task")
        box = event["box"]

        x = box[0] + box[2] // 2
        y = box[1] + box[3] // 2
        context.tasker.controller.post_click(x, y).wait()
        time.sleep(self.CLICK_DELAY)
        context.tasker.controller.post_click(x, y).wait()
        time.sleep(self.ACTION_DELAY)
        if run_task:
            logger.info(f"执行任务{run_task}")
            context.run_task(run_task)
        return True

    def _find_attr_event(self, events: list, attr: str, need_sp: bool = False) -> Optional[list]:
        """查找指定属性事件的坐标。need_sp=True时只返回有SP标记的属性事件。"""
        for event in events:
            if attr in event:
                if need_sp and not event.get("SP"):
                    continue
                return event[attr]
        return None

    def _find_event_by_name(self, events: list, event_name: str) -> Optional[list]:
        """从事件列表中查找指定名称事件的坐标。"""
        for event in events:
            for key in event:
                if event_name in key:
                    return event[key]
        return None

    def _find_any_event(self, events: list) -> Optional[dict]:
        """从事件列表中获取任意第一个可用事件。"""
        for event in events:
            for key in event:
                if key not in ["SP"] and isinstance(event[key], list):
                    run_task = self.RUN_TASK_MAP.get(key, "")
                    return self._make_event(key, event[key], run_task=run_task)
        return None

    def _make_event(self, name: str, box: list, run_task: str = "") -> dict:
        """创建事件字典，run_task默认为空时从RUN_TASK_MAP自动获取。"""
        return {"name": name, "box": box, "run_task": run_task or self.RUN_TASK_MAP.get(name, "")}

    def _parse_suggestion(self, suggestion: str) -> Optional[str]:
        """从老师建议中解析属性名称，匹配SUGGESTION_CONFIG中的关键词。"""
        if not suggestion:
            return None
        for attr in ["Vo", "Da", "Vi"]:
            for keyword in self.SUGGESTION_CONFIG[attr].get("keyword", []):
                if keyword in suggestion:
                    return attr
        return None

    @staticmethod
    def _get_screenshot(context: Context):
        """获取屏幕截图"""
        return context.tasker.controller.post_screencap().wait().get()

    def _get_suggestion(self, context: Context, image) -> str:
        """获取建议课程"""
        reco_detail = context.run_recognition(
            "ProduceChooseEventSuggestion",
            image,
            pipeline_override={"ProduceChooseEventSuggestion": {"recognition": "OCR", "roi": self.SUGGESTION_ROI}},
        )

        if not (reco_detail and reco_detail.hit):
            return ""

        suggestion_text = "".join(item.text for item in reco_detail.filtered_results)
        logger.info(f"老师建议: {suggestion_text}")
        return suggestion_text

    @staticmethod
    def _get_health(context: Context, image) -> Optional[dict]:
        """获取体力比例"""
        reco_detail = context.run_recognition("ProduceRecognitionHealth", image)
        if not (reco_detail and reco_detail.hit):
            return None

        try:
            health_parts = reco_detail.best_result.text.split("/")
            current_health = int("".join(filter(str.isdigit, health_parts[0])))
            max_health = int("".join(filter(str.isdigit, health_parts[1])))
            ratio = current_health / max_health
            logger.info(f"体力: {current_health}/{max_health} ({ratio:.2%})")
            return {"current": current_health, "max": max_health, "ratio": ratio}
        except (ValueError, IndexError, ZeroDivisionError):
            logger.warning("体力数据解析失败")
            return None

    @staticmethod
    def _get_current_points(context: Context, image) -> Optional[int]:
        """获取当前积分"""
        reco_detail = context.run_recognition(
            "ProduceRecognitionPoint",
            image,
            pipeline_override={"ProduceRecognitionPoint": {"roi": [320, 90, 130, 54]}},
        )
        if not (reco_detail and reco_detail.hit):
            return None
        try:
            points = int(reco_detail.best_result.text.replace(",", ""))
            logger.info(f"积分: {points}")
            return points
        except ValueError:
            logger.warning("积分数据解析失败")
            return None

    @staticmethod
    def _get_current_score(context: Context, image) -> Optional[dict]:
        """获取当前得分"""
        score = {
            "Vo": 0,
            "Da": 0,
            "Vi": 0,
            "max": 0,
        }
        for i in range(3):
            reco_detail = context.run_recognition(
                "ProduceRecognitionScore",
                image,
                pipeline_override={"ProduceRecognitionScore": {"roi": [150 + i * 150, 668, 136, 80]}},
            )
            if reco_detail and reco_detail.hit and len(reco_detail.filtered_results) == 2:
                current_score = int("".join(filter(lambda c: c.isdigit(), reco_detail.filtered_results[0].text)))
                max_score = int("".join(filter(lambda c: c.isdigit(), reco_detail.filtered_results[1].text.replace("/", ""))))
                logger.debug(f"第{i + 1}列得分: {current_score} / {max_score}")
                score[["Vo", "Da", "Vi"][i]] = current_score
                score["max"] = max_score if score["max"] < max_score < 9999 else score["max"]
        try:
            logger.info(f"当前得分: Vo={score['Vo']}, Da={score['Da']}, Vi={score['Vi']}, Max={score['max']}")
            return score
        except ValueError:
            logger.warning("积分数据解析失败")
            return None

    def _get_available_events(self, context: Context, image) -> List[Dict[str, Any]]:
        """获取可用事件列表"""
        from ..event_templates import match_event_template
        available_events = []
        available_events_name = ""

        for event_name, event_img in self.EVENT_CONFIG.items():
            reco_detail = match_event_template(context, image, event_img, [0, 880, 720, 220])
            if reco_detail and reco_detail.hit:
                if event_name in ["Da", "Vi", "Vo"]:
                    available_events_name = "Vo, Da, Vi"
                    available_events = [
                        {"Vo": [190, 1000, 1, 1], "SP": self._get_sp_course(context, image, [70, 900, 80, 80])},
                        {"Da": [360, 1000, 1, 1], "SP": self._get_sp_course(context, image, [250, 900, 80, 80])},
                        {"Vi": [530, 1000, 1, 1], "SP": self._get_sp_course(context, image, [430, 900, 80, 80])},
                    ]
                    break
                available_events.append({event_name: reco_detail.best_result.box})
                available_events_name += f"{event_name}, "

        logger.info(f"可用事件: {available_events_name.rstrip(', ')}")
        logger.debug(available_events)
        return available_events

    def _get_sp_course(self, context: Context, image, sp_roi: List[int]) -> bool:
        """获取SP课程选择"""
        from ..event_templates import match_event_template
        reco_detail = match_event_template(context, image, self.SP_TEMPLATE, sp_roi,
            node="ProduceChooseEventSp", **self.SP_TEMPLATE_PARAMS)
        if reco_detail and reco_detail.hit:
            logger.debug(f"{sp_roi}存在SP课程")
            return True
        return False


@AgentServer.custom_action("ProduceChooseEventAuto")
class ProduceChooseEventAuto(ProduceChooseEventBase):
    """
    自动选择培育事件。

    选择优先级顺序（从高到低）：
    1. 低体力保护：体力 < 20% 或体力值 < 8时，优先外出恢复体力，其次休息
    2. 老师建议：根据OCR结果匹配Vo/Da/Vi属性关键词
    3. SP课程（第一属性，仅当该属性分数占比 < 80%时）
    4. SP课程（第二属性，仅当该属性分数占比 < 80%时）
    5. 第一属性课程（仅当该属性分数占比 < 80%时）
    6. 第二属性课程（仅当第一属性分数占比 >= 85%时，且第二属性占比 < 80%）
    7. 交谈/活动：优先交谈消耗points商店购物（需points >= 100），不满足则活动获得points
    8. 上课：提升Vo/Da/Vi
    9. 外出：恢复体力
    10. SP课程（非第一/第二属性）
    保底：以上均未命中时，从剩余事件中任选一个
    """

    SUGGESTION_CONFIG = {
        "Vo": {"img": "produce/Vo.png", "keyword": ["ボーカル", "唱歌"]},
        "Da": {"img": "produce/Da.png", "keyword": ["ダンス", "舞蹈"]},
        "Vi": {"img": "produce/Vi.png", "keyword": ["ビジュアル", "视觉"]},
        "体力": {"img": "produce/rest.png", "keyword": ["体力"]},
        "交谈": {"img": "produce/chat.png", "keyword": ["先生に相談して", "咨询"]},
    }

    EVENT_CONFIG = {
        "Vo": "produce/Vo.png",
        "Da": "produce/Da.png",
        "Vi": "produce/Vi.png",
        "交谈": "produce/chat.png",
        "上课": "produce/lesson.png",
        "活动": "produce/event.png",
        "外出": "produce/go_out.png",
    }

    RUN_TASK_MAP = {
        "交谈": "ProduceShoppingEntry",
    }

    def _get_preference(self, context: Context, argv: CustomAction.RunArg) -> dict:
        preference = context.get_node_data("ProduceChooseNIAEventFlag").get("action", {}).get("param", {}).get("custom_action_param", {})
        if "first" not in preference or "second" not in preference:
            logger.warning("偏好设置解析失败，使用默认值")
        return {"first": preference.get("first", "Vi"), "second": preference.get("second", "Da")}


@AgentServer.custom_action("ProduceChooseNIAEventAuto")
class ProduceChooseNIAEventAuto(ProduceChooseEventBase):
    """
    自动选择NIA培育事件。

    选择优先级顺序（从高到低）：
    1. 低体力保护：体力 < 20% 或体力值 < 8时，优先外出恢复体力，其次休息
    2. 老师建议：根据OCR结果匹配Vo/Da/Vi属性关键词
    3. SP课程（第一属性，仅当该属性分数占比 < 80%时）
    4. SP课程（第二属性，仅当该属性分数占比 < 80%时）
    5. 营业：获得score
    6. 第一属性课程（仅当该属性分数占比 < 80%时）
    7. 第二属性课程（仅当第一属性分数占比 >= 85%时，且第二属性占比 < 80%）
    8. 交谈/活动：优先交谈消耗points商店购物（需points >= 100），不满足则活动获得points
    9. 外出：恢复体力
    10. 指导
    11. SP课程（非第一/第二属性）
    保底：以上均未命中时，从剩余事件中任选一个
    """

    SUGGESTION_CONFIG = {
        "Vo": {"img": "produce/NIA/Vo.png", "keyword": ["ボーカル", "唱歌"]},
        "Da": {"img": "produce/NIA/Da.png", "keyword": ["ダンス", "舞蹈"]},
        "Vi": {"img": "produce/NIA/Vi.png", "keyword": ["ビジュアル", "视觉"]},
        "体力": {"img": "produce/rest.png", "keyword": ["体力"]},
        "指导": {"img": "produce/NIA/guide.png", "keyword": ["特别指導", "特别指导"]},
        "交谈": {"img": "produce/NIA/chat.png", "keyword": ["相談", "咨询"]},
    }

    EVENT_CONFIG = {
        "Vo": "produce/NIA/Vo.png",
        "Da": "produce/NIA/Da.png",
        "Vi": "produce/NIA/Vi.png",
        "交谈": "produce/NIA/chat.png",
        "活动": "produce/NIA/activity.png",
        "指导": "produce/NIA/guide.png",
        "外出": "produce/NIA/go_out.png",
        "工作": "produce/NIA/work.png",
    }

    RUN_TASK_MAP = {
        "交谈": "ProduceShoppingEntry",
        "指导": "ProduceGuideEntry",
        "工作": "ProduceWorkEntry",
    }

    def _choose_extra_before_attrs(self, events: list) -> Optional[dict]:
        """营业：获得score"""
        event = self._find_event_by_name(events, "工作")
        if event:
            return self._make_event("工作", event)
        return None

    def _choose_extra_after_outing(self, events: list) -> Optional[dict]:
        """指导（点击后需执行任务）"""
        event = self._find_event_by_name(events, "指导")
        if event:
            return self._make_event("指导", event)
        return None


@AgentServer.custom_action("ProduceCardsAuto")
class ProduceCardsAuto(CustomAction):
    """
    初、NIA、HIF 共用出牌策略：再动优先，同类按 SSR > SR > R > N。

    稀有度或再动无法确定时会写出完整日志，并回退到系统推荐牌/检测
    分数最高的牌。届いて！仅在总剩余 2 回合内使用；踮脚仅在总剩余 7 回合内使用。
    无法核实卡名时降级原策略，保持自动运行。
    """

    # 阈值常量
    CLICK_DELAY = 0.3
    TIME_OUT = 15.0
    RECOGNITION_STALL_TIMEOUT = 45.0
    POST_PLAY_RECOGNITION_DELAY = 1.5

    # 保留定制版对底部重叠手牌的兼容范围，并限制异常框的下界。
    CARD_Y_MIN = 780
    CARD_Y_MAX = 1000

    def _rule_preset(self):
        if not hasattr(self, '_rules_store'):
            self._rules_store = RuleStore()
        return self._rules_store.preset()

    def _held_until_late(self, name, remaining):
        return not rule_allowed(rule_for(self._rule_preset(), name), remaining)

    def _needs_rule_identity(self, remaining):
        return any(r['enabled'] and not rule_allowed(r, remaining)
                   for r in self._rule_preset()['rules'])

    def _play_candidate(self, context, image, candidates):
        selected = self._choose_card(context, image, candidates)
        if selected is not None:
            self._held_hand_check = None
            return self._play_a_card(context, selected["box"])
        # Only skip after three fresh, agreeing hands consisting entirely of
        # held cards (including repeatedly observed finisher hints).
        remaining = self._drinks.turns.read(context, image)
        key = (remaining, tuple(sorted(tuple(c["box"]) for c in candidates)))
        previous, count = getattr(self, "_held_hand_check", None) or (None, 0)
        agrees = (previous is not None and previous[0] == remaining
                  and len(previous[1]) == len(key[1])
                  and all(self._same_selected_box(a, b) for a, b in zip(previous[1], key[1])))
        count = count + 1 if agrees else 1
        self._held_hand_check = (key, count)
        self.start_time = time.time()
        if count >= 3:
            logger.info("连续三帧仅剩需暂缓的牌，自动跳过回合后继续")
            self._skip_held_round(context)
        return True

    def _skip_held_round(self, context):
        if context.tasker.stopping or self._disconnect_detected:
            return
        self._drinks.opening = False
        self._drinks.pending_card = None
        context.run_task("ProduceRecognitionSkipRound")
        self._wait_until_playable(context)
        self._held_hand_check = None
        self._held_selection = None
        self._held_reselections = 0
        self.start_time = time.time()

    def _defer_held_card(self, context, name, box, remaining, *, unconfirmed=False):
        """Leave the card selected so a fresh hand scan can select another card.

        This observation is used only while that same raised card is still
        visible. Bound repeated re-selections rather than looping or failing.
        """
        self._held_selection = (name, list(box), True) if unconfirmed else (name, list(box))
        self._held_reselections = getattr(self, "_held_reselections", 0) + 1
        self._drinks.pending_card = None
        label = '疑似受限牌暂缓' if unconfirmed else '大刀保留'
        logger.info(f"{label}：{name} 总剩余={remaining}，重新观察并改选其他牌")
        if self._held_reselections >= 6:
            logger.warning("大刀反复被选中，本回合自动跳过，继续后续回合")
            self._skip_held_round(context)
        self.start_time = time.time()
        return True

    def __init__(self):
        super().__init__()
        self.start_time = time.time()
        self._disconnect_detected = False

    def run(
        self,
        context: Context,
        argv: CustomAction.RunArg,
    ) -> bool:
        from ..hand_identity import hand_boxes

        self._disconnect_detected = False
        # 使用饮料
        if not self._wait_until_playable(context):
            return True
        if self._disconnect_detected:
            return True
        image = context.tasker.controller.post_screencap().wait().get()
        disconnect_node = _detect_disconnect_screen(context, image)
        if disconnect_node:
            logger.warning(f"出牌检测到断线界面 {disconnect_node}，交回全局恢复流程")
            return True
        try:
            self._drinks = BattleDrinkSession()
        except (OSError, ValueError, TypeError) as exc:
            logger.warning(f"饮料策略读取失败，本场保留饮料并继续出牌：{exc}")
            self._drinks = BattleDrinkSession(policy={})
        self._drink_error = False
        self._held_hand_check = None
        self._held_selection = None
        self._held_reselections = 0

        # 开始出牌
        self._selection_failures = 0
        self.start_time = time.time()
        unusable_confirmations = 0
        no_cards_confirmations = 0
        while True:
            # 处理手动终止任务
            if context.tasker.stopping:
                logger.info("任务中断")
                return True

            # 截图
            image = context.tasker.controller.post_screencap().wait().get()
            disconnect_node = _detect_disconnect_screen(context, image)
            if disconnect_node:
                logger.warning(f"出牌检测到断线界面 {disconnect_node}，交回全局恢复流程")
                return True

            # 通过检测体力槽判断是否处于出牌场景
            if not self._is_playing_card(context, image):
                logger.info("未检测到体力")
                logger.success("事件: 退出出牌")
                break

            if self._drink_step(context, image):
                if self._drink_error:
                    return False
                self.start_time = time.time()
                continue

            # 识别手牌
            raw_candidates = []
            self._identity_boxes = []
            reco_detail = context.run_recognition("ProduceRecognitionCards", image)
            if reco_detail and reco_detail.hit:
                # Filter all classes before counting, including unusable detections.
                self._identity_boxes = hand_boxes(reco_detail.all_results or [])
                results = [result for result in (reco_detail.all_results or [])
                           if self.CARD_Y_MIN <= result.box[1] <= self.CARD_Y_MAX]
                raw_candidates = self._get_playable_candidates(results, image.shape)
                candidates = self._get_full_hand_candidates(raw_candidates)
                if candidates:
                    unusable_confirmations = 0
                    no_cards_confirmations = 0
                    if not self._play_candidate(context, image, candidates):
                        return False
                elif any(result.label == "useless" for result in results):
                    # A card sliding at either screen edge is sometimes emitted
                    # as a narrow fragment.  The old geometry filter discarded
                    # it and trusted a simultaneous `useless` detection, which
                    # could skip a round despite a playable card being present.
                    relaxed = self._get_relaxed_hand_candidates(raw_candidates)
                    if relaxed:
                        unusable_confirmations = 0
                        no_cards_confirmations = 0
                        logger.warning(
                            "无可用牌判定被残缺可用卡框否决；优先尝试检测分数最高的残缺卡"
                        )
                        if not self._play_candidate(context, image, relaxed):
                            return False
                        continue

                    unusable_confirmations += 1
                    self._held_hand_check = None
                    no_cards_confirmations = 0
                    if unusable_confirmations < 3:
                        logger.warning(
                            f"检测器报告无可用牌，正在跨帧复核 ({unusable_confirmations}/3)"
                        )
                        time.sleep(0.35)
                        continue

                    diagnostic = [
                        {
                            "box": [int(value) for value in result.box],
                            "label": result.label,
                            "detector_score": float(getattr(result, "score", 0.0)),
                        }
                        for result in results
                    ]
                    _save_card_diagnostic(image, "no-playable", diagnostic)
                    logger.warning("连续三帧确认无可用牌，执行跳过回合")
                    self._drinks.opening = False
                    context.run_task("ProduceRecognitionSkipRound")
                    self._wait_until_playable(context)
                    self.start_time = time.time()
                    unusable_confirmations = 0
                else:
                    unusable_confirmations = 0
                    no_cards_confirmations = 0
                    self._held_hand_check = None

            else:
                self._held_hand_check = None
                reco_detail = context.run_recognition("ProduceRecognitionNoCards", image)
                if reco_detail and reco_detail.hit:
                    no_cards_confirmations += 1
                    unusable_confirmations = 0
                    if no_cards_confirmations < 3:
                        logger.warning(
                            f"未检测到手牌，正在跨帧复核 ({no_cards_confirmations}/3)"
                        )
                        time.sleep(0.35)
                        continue
                    _save_card_diagnostic(image, "no-cards", [])
                    logger.info("连续三帧确认无手牌")
                    self._drinks.opening = False
                    context.run_task("ProduceRecognitionSkipRound")
                    self._wait_until_playable(context)
                    self.start_time = time.time()
                    no_cards_confirmations = 0
                else:
                    # An animation frame can make both recognizers miss.  It is
                    # not evidence for skipping and must reset the confirmation.
                    unusable_confirmations = 0
                    no_cards_confirmations = 0

            elapsed = time.time() - self.start_time
            if elapsed >= self.TIME_OUT:
                # Only use current-frame playable evidence. Never click a stale
                # box or skip a turn merely because recognition timed out.
                relaxed = self._get_relaxed_hand_candidates(raw_candidates)
                if relaxed:
                    logger.warning("出牌等待超时，使用当前帧已确认的残缺可用牌")
                    if not self._play_candidate(context, image, relaxed):
                        return False
                    unusable_confirmations = no_cards_confirmations = 0
                elif elapsed >= self.RECOGNITION_STALL_TIMEOUT:
                    _save_card_diagnostic(image, "battle-stalled", [])
                    logger.warning("出牌识别暂未恢复，交回培育入口重新识别；不跳回合、不结束任务")
                    time.sleep(.5)
                    return True

            time.sleep(self.CLICK_DELAY)

        return True

    @staticmethod
    def _get_playable_candidates(results: list, image_shape) -> list:
        """Keep playable detections and merge duplicate boxes from ``all_results``."""
        screen_height, screen_width = image_shape[:2]
        raw = []
        for result in results:
            if result.label not in ("cards", "suggestions"):
                continue
            box = [int(value) for value in result.box]
            if box[2] < 20 or box[3] < 20:
                continue
            if box[0] + box[2] <= 0 or box[0] >= screen_width:
                continue
            if box[1] + box[3] <= 0 or box[1] >= screen_height:
                continue
            raw.append(
                {
                    "box": box,
                    "label": result.label,
                    "detector_score": float(getattr(result, "score", 0.0)),
                }
            )

        raw.sort(key=lambda item: item["detector_score"], reverse=True)
        unique = []
        for candidate in raw:
            duplicate = next(
                (
                    saved
                    for saved in unique
                    if intersection_over_union(candidate["box"], saved["box"]) >= 0.45
                ),
                None,
            )
            if duplicate is None:
                unique.append(candidate)
            elif candidate["label"] == "suggestions":
                duplicate["label"] = "suggestions"
        return sorted(unique, key=lambda item: item["box"][0])

    @staticmethod
    def _get_relaxed_hand_candidates(candidates: list) -> list:
        return [candidate for candidate in candidates
                if ProduceCardsAuto.CARD_Y_MIN <= candidate["box"][1] <= ProduceCardsAuto.CARD_Y_MAX
                and 20 <= candidate["box"][2] <= 220
                and 180 <= candidate["box"][3] <= 300
                and candidate["detector_score"] >= 0.65]

    @staticmethod
    def _get_full_hand_candidates(candidates: list) -> list:
        """Keep complete portrait cards in the bottom hand area."""
        return [
            candidate
            for candidate in candidates
            if ProduceCardsAuto.CARD_Y_MIN <= candidate["box"][1] <= ProduceCardsAuto.CARD_Y_MAX
            and 120 <= candidate["box"][2] <= 220
            and 180 <= candidate["box"][3] <= 300
        ]

    @staticmethod
    def _extra_action_status(context: Context, image, box: list) -> tuple:
        """Recognize the blue +1 glyph in the fixed left-side effect column."""
        x, y, width, height = box
        if width < 40 or height < 40:
            return "unknown", 0.0, [x, y, width, height]
        # Effect icons are bottom-aligned in portrait cards and vertically
        # stacked in square cards.  Only the left icon column is relevant; the
        # former broad ROI also included card art and caused intermittent hits.
        roi = [
            max(0, x),
            max(0, y + int(height * 0.18)),
            max(1, int(width * 0.32)),
            max(1, int(height * 0.70)),
        ]
        detail = context.run_recognition(
            "ProduceRecognitionExtraAction",
            image,
            pipeline_override={
                "ProduceRecognitionExtraAction": {
                    "roi": roi,
                    "threshold": 0.35,
                }
            },
        )
        if not (detail and detail.hit and detail.best_result):
            return "no", 0.0, roi
        score = float(getattr(detail.best_result, "score", 0.0))
        # Captured true +1 samples cover 0.546-0.716 with the old broad ROI;
        # ordinary cards produced no match.  The small uncertain band is logged
        # and saved without being promoted to an extra-action card.
        if score >= 0.52:
            return "yes", score, roi
        if score <= 0.46:
            return "no", score, roi
        return "unknown", score, roi

    @staticmethod
    def _apply_card_name_fallback(context: Context, image, evaluated: list) -> None:
        """Use one batched OCR pass to resolve visually uncertain cards.

        Card names remain readable when a disabled overlay washes out the
        rarity frame.  OCR is intentionally skipped when every visual result
        is already strong, so the normal fast path is unchanged.
        """
        uncertain = [
            item
            for item in evaluated
            if item["rarity"] == "unknown"
            or item["rarity_confidence"] < 0.15
            or (
                item["rarity"] == "N"
                and item["rarity_confidence"] < 0.58
            )
            or item["extra_action"] == "unknown"
        ]
        if not uncertain:
            return

        image_height, image_width = image.shape[:2]
        left = max(0, min(item["box"][0] for item in evaluated))
        right = min(
            image_width,
            max(item["box"][0] + item["box"][2] for item in evaluated),
        )
        top = max(
            0,
            min(
                item["box"][1] + int(item["box"][3] * 0.80)
                for item in evaluated
            ),
        )
        bottom = min(
            image_height,
            max(item["box"][1] + item["box"][3] for item in evaluated),
        )
        roi = [left, top, max(1, right - left), max(1, bottom - top)]
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
            logger.debug(f"卡名兜底OCR未命中 roi={roi}")
            return

        results = list(detail.filtered_results or detail.all_results or [])
        for item in uncertain:
            x, _, width, _ = item["box"]
            pieces = []
            for result in results:
                result_box = list(getattr(result, "box", []) or [])
                if len(result_box) != 4:
                    continue
                rx, _, rw, _ = result_box
                # Maa returns full-image coordinates.  Older framework builds
                # may return ROI-relative boxes, which are normalized here.
                if rx + rw <= roi[2] and rx < roi[0]:
                    rx += roi[0]
                overlap = max(0, min(x + width, rx + rw) - max(x, rx))
                if overlap >= min(width, rw) * 0.25:
                    pieces.append((rx, str(getattr(result, "text", ""))))
            raw_name = "".join(text for _, text in sorted(pieces))
            matched = match_card_catalog(raw_name)
            item["ocr_name"] = raw_name
            if matched is None:
                logger.debug(
                    f"卡名兜底未匹配 box={item['box']} raw={raw_name!r} roi={roi}"
                )
                continue

            previous = item["rarity"]
            # A catalog title meeting the score gate may correct a weak
            # visual result; lower scores may fill unknown only.
            if previous == "unknown" or (
                matched.confidence >= 0.999
                and (
                    item["rarity_confidence"] < 0.35
                    or (
                        previous == "N"
                        and item["rarity_confidence"] < 0.58
                    )
                )
            ):
                item["rarity"] = matched.rarity
                item["rarity_confidence"] = matched.confidence
                item["rarity_source"] = "OCR-card-catalog"
            if item["extra_action"] == "unknown" and matched.extra_action:
                item["extra_action"] = "yes"
                item["extra_score"] = matched.confidence
            item["catalog_name"] = matched.matched_name
            item["catalog_confidence"] = matched.confidence
            item["priority"] = rarity_priority(
                item["rarity"], item["extra_action"]
            )
            logger.info(
                f"卡名兜底 box={item['box']} raw={raw_name!r} "
                f"matched={matched.matched_name!r} score={matched.confidence:.3f} "
                f"rarity={previous}->{item['rarity']} "
                f"extra_action={item['extra_action']} priority={item['priority']}"
            )

    def _choose_card(self, context: Context, image, candidates: list) -> Optional[dict]:
        preset = self._rule_preset()
        session = getattr(self, "_drinks", None)
        remaining = None
        boxes = getattr(self, "_identity_boxes", [])
        self._selected_identity = None
        identity_results = {}
        identity_names = {}
        held = getattr(self, "_held_selection", None)
        raised_held = None
        if held:
            from ..battle_drinks import CardNameReader
            raised_held = CardNameReader().locate_raised(context, image, held[1])
            if raised_held is None or not self._same_selected_box(raised_held, held[1]):
                self._held_selection = None
                raised_held = None
        def identify(candidate):
            key = tuple(candidate["box"])
            if key in identity_names:
                return identity_names[key]
            if raised_held is not None and self._same_selected_box(candidate["box"], raised_held):
                # An OCR hint can exclude this still-selected slot before its
                # window, but must not become a confirmed name when it opens.
                if len(held) > 2 and held[2] and not self._held_until_late(held[0], remaining):
                    identity_names[key] = None
                    return None
                identity_names[key] = held[0]
                return held[0]
            if not session or not hasattr(session, "art"):
                return None
            result = session.art.read(image, candidate["box"], boxes)
            identity_results[tuple(candidate["box"])] = result
            if result and result.name:
                identity_names[key] = result.name
                return result.name
            # Unobscured names remain a fallback, never OCR a neighbour's
            # overlapping text after selection with stale hand coordinates.
            if result and result.mode == "full":
                identity_names[key] = session.names.read(context, image, candidate["box"])
                return identity_names[key]
            identity_names[key] = None
            return None
        if session and hasattr(session, "turns"):
            remaining = session.turns.read(context, image)
            allowed = []
            for candidate in candidates:
                name = identify(candidate)
                if self._held_until_late(name, remaining):
                    logger.info(f"时机保留：{name} 总剩余={remaining}，尚未到使用窗口")
                else:
                    allowed.append(candidate)
            candidates = allowed
            if self._held_until_late(session.pending_card, remaining):
                session.pending_card = None
            if not candidates:
                return None
        if session and session.pending_card:
            pinned = [candidate for candidate in candidates
                      if identify(candidate) == session.pending_card]
            if pinned:
                candidates = pinned
            else:
                # A unique tentative art name may locate a card for a fresh
                # raised-title check, but never authorizes drinking/playing.
                hinted = [candidate for candidate in candidates
                          if (result := identity_results.get(tuple(candidate["box"])))
                          and not result.name and result.candidate == session.pending_card]
                if len(hinted) == 1:
                    candidates = hinted
                    logger.info(f"喝药后目标牌卡图待核实，点选后读取完整标题：{session.pending_card}")
                else:
                    logger.warning(f"喝药后未重新定位目标牌「{session.pending_card}」，解除旧目标，按当前可用牌继续出牌")
                    session.pending_card = None
                    self._skip_named_drink_once = True
        evaluated = []
        for index, candidate in enumerate(candidates, start=1):
            rarity = classify_rarity(image, candidate["box"])
            extra_action, extra_score, extra_roi = self._extra_action_status(
                context, image, candidate["box"]
            )
            priority = rarity_priority(rarity.rarity, extra_action)
            item = {
                **candidate,
                "rarity": rarity.rarity,
                "rarity_confidence": rarity.confidence,
                "rarity_distance": rarity.best_distance,
                "rarity_margin": rarity.margin,
                "rarity_distances": rarity.distances,
                "frame_bgr": [round(value * 255) for value in rarity.signature],
                "rarity_source": rarity.channel_order,
                "extra_action": extra_action,
                "extra_score": extra_score,
                "extra_roi": extra_roi,
                "priority": priority,
            }
            evaluated.append(item)
            distance_text = ", ".join(
                f"{key}={value:.3f}" for key, value in rarity.distances.items()
            ) or "无"
            _diagnostic_info(
                f"卡牌识别[{index}] box={candidate['box']} "
                f"detector={candidate['label']}/{candidate['detector_score']:.3f} "
                f"rarity={rarity.rarity} confidence={rarity.confidence:.3f} "
                f"distance={rarity.best_distance:.3f} margin={rarity.margin:.3f} "
                f"source={rarity.channel_order} frame_bgr={item['frame_bgr']} "
                f"distances={{{distance_text}}} "
                f"extra_action={extra_action} "
                f"extra_score={extra_score:.3f} extra_roi={extra_roi} priority={priority}"
            )

        self._apply_card_name_fallback(context, image, evaluated)

        # Reuse identities already obtained for the finisher guard. No added
        # OCR, click-to-identify, or waiting in the battle hot path.
        for item in evaluated:
            box_key = tuple(item['box'])
            name = identity_names.get(box_key)
            if not name and item.get('catalog_confidence', 0) >= .999:
                name = item.get('catalog_name')
                identity_names[box_key] = name
            item['rule_name'] = name

        # A title recovered by catalog fallback must also obey timing rules.
        evaluated = [item for item in evaluated
                     if rule_allowed(rule_for(preset, item.get('rule_name')), remaining)]
        if not evaluated:
            return None
        fallback = all(item['rarity'] == 'unknown' for item in evaluated)
        needs_identity = any(r['enabled'] and not rule_allowed(r, remaining) for r in preset['rules'])
        def selection_key(item):
            key = rule_sort_key(preset, item.get('rule_name'), item['rarity'],
                                item['extra_action'], remaining, item['box'][0])
            # Preserve the existing recognition safety tie-break: when timing
            # is restricted, prefer a verified safe name over an unidentified
            # card at the exact same tier/score. Fully identified ties use slots.
            return (*key[:-1], bool(item.get('rule_name')) if needs_identity else False, key[-1])
        selected = max(evaluated, key=selection_key)
        if (
            selected["rarity"] == "unknown"
            or selected["rarity_confidence"] < 0.15
            or 0.46 < selected["extra_score"] < 0.52
        ):
            _save_card_diagnostic(image, "battle", evaluated)
        logger.success(
            f"出牌选择: rarity={selected['rarity']} "
            f"extra_action={selected['extra_action']} priority={selected['priority']} "
            f"box={selected['box']} fallback={fallback} "
            f"name={selected.get('rule_name')} preset={preset['name']} remaining={remaining} "
            f"rule_key={rule_sort_key(preset, selected.get('rule_name'), selected['rarity'], selected['extra_action'], remaining)}"
        )
        if session and hasattr(session, "art"):
            self._selected_identity = (tuple(selected["box"]), identify(selected))
        return selected

    def _drink_step(self, context, image, card_name=None):
        try:
            remaining = self._drinks.turns.read(context, image)
            if remaining is not None and remaining != self._drinks.last_log:
                extra=getattr(self._drinks.turns,'extra_turns',0)
                suffix=(f"（主回合 {self._drinks.turns.base_turns} + 追加 {extra}）"
                        if isinstance(extra,int) and extra else '')
                logger.info(f"战斗剩余回合：{remaining}{suffix}")
                self._drinks.last_log = remaining
            return self._drinks.prepare(context, image, self._wait_until_playable,
                                        remaining=remaining, card_name=card_name)
        except (RuntimeError, ValueError, OSError) as exc:
            if self._disconnect_detected:
                return True
            logger.error(f"饮料时机处理未完成：{exc}")
            self._drink_error = True
            return True

    @staticmethod
    def _same_selected_box(first, second):
        # This only associates hand slots; selection identity is checked
        # separately against the current tooltip before any play click.
        return same_card_slot(first, second)

    def _observe_selected_card(self, context, original_box):
        from ..battle_drinks import CardNameReader
        reader = CardNameReader()
        previous = None
        previous_frame = None
        for _ in range(9):
            if context.tasker.stopping:
                return None
            frame = context.tasker.controller.post_screencap().wait().get()
            if _detect_disconnect_screen(context, frame):
                self._disconnect_detected = True
                return None
            box = reader.locate_raised(context, frame, original_box)
            if previous_frame is not None and reader.same_selection(previous_frame, previous, frame, box):
                return frame, box
            previous, previous_frame = box, frame
            time.sleep(.18)
        return None

    def _confirm_card_submission(self, context, selected_frame, selected_box):
        """Retry only while the same selected card and tooltip remain visible.

        Disappearance means the click advanced (possibly into an animation),
        not that a new hand may be clicked. The caller waits for that hand.
        """
        from ..battle_drinks import CardNameReader
        reader = CardNameReader()
        previous = None
        previous_frame = None
        for attempt in range(10):
            if context.tasker.stopping:
                return False
            time.sleep(.15)
            frame = context.tasker.controller.post_screencap().wait().get()
            if _detect_disconnect_screen(context,frame):
                self._disconnect_detected = True
                return False
            box = reader.locate_raised(context,frame,selected_box)
            if box is None:
                return True
            same_title = reader.same_title_panel(selected_frame,frame)
            if (attempt == 4 and same_title and previous_frame is not None
                    and reader.same_selection(previous_frame,previous,frame,box)
                    and reader.same_selection(selected_frame,selected_box,frame,box)):
                logger.warning("确认出牌点击未生效，同一张选中牌仍在；按新位置补点一次")
                context.tasker.controller.post_click(box[0]+box[2]//2,box[1]+box[3]//2).wait()
            previous, previous_frame = box, frame
        logger.warning("选中牌尚未出手，稍后重新观察；不结束任务")
        return False

    def _recover_card_selection(self, context):
        """Re-observe instead of waiting for our own open tooltip to disappear."""
        if context.tasker.stopping or self._disconnect_detected:
            return True
        self._selection_failures = getattr(self, '_selection_failures', 0) + 1
        if self._selection_failures >= 3:
            frame = context.tasker.controller.post_screencap().wait().get()
            if _detect_disconnect_screen(context, frame):
                self._disconnect_detected = True
                return True
            skip = context.run_recognition('ProduceRecognitionSkipRound', frame)
            if skip and skip.hit:
                logger.warning('连续三次未能确认选中状态，自动跳过本回合后继续，不结束任务')
                self._skip_held_round(context)
                self._selection_failures = 0
        time.sleep(.35)
        self.start_time = time.time()
        return True

    def _play_a_card(self, context: Context, box: list) -> bool:
        """Select -> observe selection -> optional drinks -> confirm -> observe play."""
        from ..battle_drinks import CardNameReader
        if context.tasker.stopping:
            return True
        if getattr(self, "_drink_error", False):
            return False
        identity = getattr(self, "_selected_identity", None)
        self._selected_identity = None  # valid for exactly this selection
        original_box = list(box)
        before = context.tasker.controller.post_screencap().wait().get()
        if _detect_disconnect_screen(context,before):
            self._disconnect_detected = True
            return True
        # The previous confirm click can be swallowed. Do not "select" that
        # raised card again and then try to OCR it after it has flown away.
        already_selected = CardNameReader().locate_raised(context,before,box)
        if already_selected is None:
            context.tasker.controller.post_click(box[0]+box[2]//2,box[1]+box[3]//2).wait()
            time.sleep(.40)
        observed = self._observe_selected_card(context,original_box)
        if observed is None:
            logger.warning("未确认卡牌选中，可能仍在动画或点击未接收；重新观察当前手牌，不结束任务")
            return self._recover_card_selection(context)
        selected_frame, box = observed
        # At least .58s from the selection click plus recognition; the old
        # fixed .30s could hit while the card was still rising.
        if getattr(self, "_drinks", None) is not None:
            name = identity[1] if identity and identity[0] == tuple(original_box) else None
            remaining = self._drinks.turns.read(context, selected_frame) if hasattr(self._drinks, "turns") else None
            guard_active = hasattr(self._drinks, "turns")
            needs_guard_name = guard_active and self._needs_rule_identity(remaining)
            skip_named = getattr(self,'_skip_named_drink_once',False)
            self._skip_named_drink_once = False
            if name is None and (needs_guard_name or (not skip_named and self._drinks.needs_card_name(context,selected_frame))):
                previous = None
                previous_frame = None
                previous_hint = None
                previous_hint_frame = None
                suspected = None
                for attempt in range(5):
                    if context.tasker.stopping:
                        return True
                    observed = self._drinks.names.read_raised(context, selected_frame, original_box)
                    hint = getattr(self._drinks.names, 'last_raised_candidate', None)
                    # Two fresh, associated selections are required even to
                    # defer a suspected finisher; one weak OCR is insufficient.
                    suspected = None
                    if (isinstance(hint, dict) and isinstance(previous_hint, dict)
                            and hint['name'] == previous_hint['name']
                            and self._held_until_late(hint['name'], remaining)
                            and CardNameReader.same_selection(previous_hint_frame, previous_hint['box'], selected_frame, hint['box'])):
                        suspected = hint
                    previous_hint = hint
                    previous_hint_frame = selected_frame
                    if (observed and previous and observed['name'] == previous['name']
                            and CardNameReader.same_selection(previous_frame,previous['box'],selected_frame,observed['box'])):
                        name = observed['name']
                        box = observed['box']
                        break
                    previous = observed
                    previous_frame = selected_frame
                    if attempt < 4:
                        time.sleep(.20)
                        selected_frame = context.tasker.controller.post_screencap().wait().get()
                        if _detect_disconnect_screen(context, selected_frame):
                            self._disconnect_detected = True
                            return True
                if name is None:
                    if needs_guard_name and suspected:
                        logger.warning(f"连续核名疑似受限牌 {suspected['name']}（OCR={suspected['score']:.4f}），暂缓出牌并改选；不触发指定饮料")
                        return self._defer_held_card(context, suspected['name'], suspected['box'], remaining, unconfirmed=True)
                    if needs_guard_name:
                        logger.warning("卡名复核仍不确定，降级原出牌策略继续运行，本次不触发指定卡牌前饮料")
                    _save_card_diagnostic(selected_frame, 'battle-name-unconfirmed',
                                          [dict(box=box, reason='before-card drink requires identity')])
                    logger.warning('待出牌名称暂未确认，本次不触发指定卡牌前饮料；继续当前可用牌，不结束任务')
                    skip_named = True
            if name:
                logger.info(f"待出牌：{name}")
            if guard_active and self._held_until_late(name, remaining):
                return self._defer_held_card(context, name, box, remaining)
            if self._drinks.pending_card and name != self._drinks.pending_card:
                logger.warning("喝药后目标牌名称未确认，解除旧目标；按当前已确认的选中牌继续")
                self._drinks.pending_card = None
                skip_named = True
            if self._drink_step(context, selected_frame, card_name=None if skip_named else name):
                self.start_time = time.time()
                return not self._drink_error
            self._drinks.pending_card = None
            self._drinks.inspection_streak = 0
            self._drinks.opening = False
        if context.tasker.stopping:
            return True
        # OCR/drink checks can span an animation. Re-localize immediately
        # before committing; never click the old card position after it moved.
        for attempt in range(3):
            if context.tasker.stopping:
                return True
            current = context.tasker.controller.post_screencap().wait().get()
            if _detect_disconnect_screen(context,current):
                self._disconnect_detected = True
                return True
            current_box = CardNameReader().locate_raised(context,current,original_box)
            if CardNameReader.same_selection(selected_frame,box,current,current_box):
                break
            if attempt < 2:
                time.sleep(.15)
        else:
            logger.info("连续复核仍未确认原选中牌，重新观察当前手牌")
            return self._recover_card_selection(context)
        box = current_box
        if getattr(self, "_drinks", None) is not None and hasattr(self._drinks, "turns"):
            remaining = self._drinks.turns.read(context, current)
            if self._held_until_late(name, remaining):
                return self._defer_held_card(context, name, box, remaining)
        context.tasker.controller.post_click(
            box[0] + box[2] // 2,
            box[1] + box[3] // 2,
        ).wait()
        submitted = self._confirm_card_submission(context,current,box)
        logger.info("出牌点击已观察 耗时:{:.2f}秒".format(time.time() - self.start_time))
        if not submitted or self._disconnect_detected or context.tasker.stopping:
            self.start_time = time.time()
            return True
        self._held_selection = None
        self._held_reselections = 0
        self._selection_failures = 0
        playable = self._wait_until_playable(context,require_hand_stable=True)
        if playable and not self._disconnect_detected:
            time.sleep(self.POST_PLAY_RECOGNITION_DELAY)
        self.start_time = time.time()

        return True

    @staticmethod
    def _handle_move_cards(context: Context, image=None) -> bool:
        """
        一种处理移动卡片界面的笨方法

        Args:
            context: maa的Context类
            image: 截图

        Returns:
            bool: 如果出现了移动卡牌界面并处理成功，返回True；
                如果没有出现移动卡牌界面或处理过程出现问题，返回False。
        """
        if image is None:
            image = context.tasker.controller.post_screencap().wait().get()

        reco_detail = context.run_recognition("ProduceRecognitionChooseMoveCards", image)
        if reco_detail.hit:
            y = 450
            while y < 1100:
                # for x in [140, 285, 440, 585]:
                for x in [140, 285]:
                    context.tasker.controller.post_click(x, y).wait()
                    time.sleep(0.2)
                    context.tasker.controller.post_click(x, y).wait()
                    time.sleep(0.2)
                image = context.tasker.controller.post_screencap().wait().get()
                reco_detail = context.run_recognition("ProduceRecognitionChooseMoveCards", image)
                if not reco_detail.hit:
                    context.run_task("ProduceMoveCards")
                    return True
                else:
                    y = y + 100
                # 检测任务中止的情况，防止卡死，检测成功时返回False
                if context.tasker.stopping:
                    return False
        return False

    @staticmethod
    def _is_playing_card(context: Context, image=None) -> bool:
        """
        判断是否处于出牌场景

        Args:
            context: maa的Context类
            image: 截图

        Returns:
            bool: 如果处于出牌场景，返回True；否则返回False。
        """
        if image is None:
            image = context.tasker.controller.post_screencap().wait().get()

        reco_detail = context.run_recognition("ProduceRecognitionHealthFlag", image)
        if reco_detail and reco_detail.hit:
            return True
        # reco_detail = context.run_recognition('ProduceRecognitionChooseMoveCards', image)
        # if reco_detail and reco_detail.hit:
        #     return True
        return False

    def _wait_until_playable(self, context: Context, confirmation_count=1, *, require_hand_stable=False):
        """
        等待直到处于可出牌状态

        Args:
            context: maa的Context类
            confirmation_count：重复核对的次数，用来应对识别对象一闪而过的假True情况（主要存在于喝饮料的时候）

        Returns:
            bool: 处于出牌场景时，返回True；不处于出牌场景时，返回False
        """
        count_playable = 0
        count_exit = 0
        previous_hand = None
        drink_recovery = confirmation_count > 1
        wait_deadline = time.monotonic() + (4 if drink_recovery else 30)
        while True:
            if context.tasker.stopping:
                return False
            if time.monotonic() >= wait_deadline:
                if drink_recovery:
                    logger.warning("饮料使用后 4 秒内未恢复出牌，进入弹窗恢复检查")
                else:
                    logger.error("等待出牌页面超过 30 秒，保留现场")
                return False
            image = context.tasker.controller.post_screencap().wait().get()

            disconnect_node = _detect_disconnect_screen(context, image)
            if disconnect_node:
                self._disconnect_detected = True
                logger.warning(
                    f"出牌等待阶段检测到断线界面 {disconnect_node}，交回全局恢复流程"
                )
                return False

            # 通过跳过回合按钮检测是否处于可出牌状态
            reco_detail = context.run_recognition("ProduceRecognitionSkipRound", image)
            if reco_detail and reco_detail.hit:
                # A blurred battle remains visible through drink details.
                # Never accept that background as a playable hand.
                from ..battle_drinks import BattleDrinkSession
                cancel = BattleDrinkSession._cancel_box(context, image)
                if cancel:
                    count_playable = 0
                    # During consumption, leave modal recovery to close_detail.
                    # On entry/resume we own this stale modal and must close it;
                    # merely waiting here previously made the close path dead.
                    if not drink_recovery:
                        x, y, w, h = cancel
                        context.tasker.controller.post_click(x+w//2, y+h//2).wait()
                        time.sleep(.35)
                    time.sleep(.15)
                    continue
                if require_hand_stable:
                    from ..hand_identity import hand_boxes
                    from ..battle_drinks import CardNameReader
                    detail=context.run_recognition('ProduceRecognitionCards',image)
                    rows=list(detail.all_results or []) if detail and detail.hit else []
                    boxes=hand_boxes(rows)
                    flying=any(row.label in ('cards','suggestions','useless') and float(row.score)>=.5
                               and 120<=row.box[2]<=260 and 180<=row.box[3]<=350
                               and 300<=row.box[1]<740 for row in rows)
                    selected=CardNameReader._panel_title_roi(image) is not None
                    empty=(not boxes and not flying and not selected
                           and (no_cards:=context.run_recognition('ProduceRecognitionNoCards',image))
                           and no_cards.hit)
                    stable=((boxes or empty) and previous_hand is not None and len(boxes)==len(previous_hand)
                            and all(self._same_selected_box(a,b) for a,b in zip(boxes,previous_hand)))
                    previous_hand=boxes if (boxes or empty) and not flying and not selected else None
                    if flying or selected or not stable:
                        time.sleep(.18)
                        continue
                count_playable += 1
                if count_playable >= confirmation_count:
                    return True
            else:
                count_playable = 0

            if drink_recovery:
                # A fading drink modal briefly matches the generic cancel
                # button. Running ProduceButton re-recognizes it after it has
                # vanished and blocks for the pipeline's 20-second timeout.
                # Observe only here; failed use is recovered by close_detail.
                time.sleep(.12)
                continue

            # 检测血条是否存在，如连续n次检查不到血条，则认为已退出出牌场景
            # 如果识别时间太长，2次就够了，主要避免CLEAR效果遮住血条的情况
            # 如果识别时间太短，导致提前出函数，CLEAR转PERFECT的那一回合出牌计时会差很远。如果出现这种情况，就设置为3次
            reco_detail = context.run_recognition("ProduceRecognitionHealthFlag", image)
            if not (reco_detail and reco_detail.hit):
                count_exit += 1
                if count_exit >= 2:
                    return False
            else:
                count_exit = 0

            # 解决莫名其妙的误触问题
            reco_detail = context.run_recognition("ProduceButton", image)
            if reco_detail and reco_detail.hit:
                context.run_task("ProduceButton")

            # 处理移动卡片界面
            if self._handle_move_cards(context, image):
                count_playable = 0
                count_exit = 0

            # 检测任务中止的情况，防止卡死，检测成功时返回False
            if context.tasker.stopping:
                return False

            # 睡一会
            time.sleep(1)


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

        lists = _load_card_lists()
        blacklist = set(lists["blacklist"]["cards"]) if lists["blacklist"]["enabled"] else set()
        whitelist = set(lists["whitelist"]["cards"]) if lists["whitelist"]["enabled"] else set()
        logger.success(
            f"奖励牌策略: 黑名单={len(blacklist)} 白名单={len(whitelist)}；"
            "先除去黑名单，再以白名单/再动/SSR决定是否停止刷新"
        )

        image, evaluated = self._wait_for_stable_cards(context)
        if not evaluated:
            image, evaluated = self._recover_reward_page(
                context, allow_remove=_REWARD_BUDGETS["remove"].pending_before is not None
            )
        if not evaluated:
            logger.warning("奖励牌未稳定显示，交回原推荐/第一项兜底")
            return self._fallback_select(context, image)
        page_counts = self._sync_reward_action_counts(context, image, log_summary=True)

        # Removal and refresh counts are shared by the whole cultivation.  Once
        # the game proves an action is exhausted, later reward pages must not
        # spend several seconds trying the same disabled action again.
        remove_budget = _REWARD_BUDGETS["remove"]
        refresh_budget = _REWARD_BUDGETS["refresh"]
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
                if _detect_disconnect_screen(context, image):
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
                return self._select(context, selected, "白名单")

            keep = [
                item
                for item in allowed
                if item["extra_action"] == "yes" or item["rarity"] == "SSR"
            ]
            if keep:
                selected = max(keep, key=self._selection_key)
                logger.success(
                    f"奖励出现{'再动' if selected['extra_action'] == 'yes' else 'SSR'}牌，"
                    "停止刷新"
                )
                return self._select(context, selected, "刷新停止条件")

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
            if context.tasker.stopping or _detect_disconnect_screen(context, image):
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
            return self._select(context, selected, "再动与稀有度")
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
        from ..drink_visual import scan, INVENTORY_BOXES
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
        if context.tasker.stopping or _detect_disconnect_screen(context, image):
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
        from ..dialog_safety import drink_overlay_cancel
        from ..shop import ocr
        # A discard may have opened another modal. Never reuse its old reward
        # screenshot or guess a checkbox coordinate underneath that modal.
        for _ in range(4):
            if context.tasker.stopping:
                return True
            image = context.tasker.controller.post_screencap().wait().get()
            if _detect_disconnect_screen(context, image):
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
        from ..drink_visual import scan, INVENTORY_BOXES
        # Use the same artwork/ring evidence as the reward reader. The shop's
        # broad cyan mask can count the animated background as another bottle.
        return len(cls._scan_inventory_occupancy(context, image, scan(image, INVENTORY_BOXES)))

    @classmethod
    def _wait_discard_receipt(cls, context, before_count):
        from ..shop import owned_detail_title
        stable = 0
        for _ in range(12):
            if context.tasker.stopping:
                return None
            time.sleep(.35)
            image = context.tasker.controller.post_screencap().wait().get()
            if _detect_disconnect_screen(context, image):
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
        from ..shop import TARGET_DRINK_NAMES, owned_detail_title, owned_throw_box, read_discard_confirmation
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
            if _detect_disconnect_screen(context, image):
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
            if _detect_disconnect_screen(context, image):
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
                from ..shop import owned_detail_title
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
            from ..square_identity import recognize as recognize_square, evidence as square_evidence
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
            (_diagnostic_info if diagnostics else logger.debug)(
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
                 for a in _REWARD_BUDGETS}
        if context.tasker.stopping:
            return {a: None for a in _REWARD_BUDGETS}
        time.sleep(0.2)
        second_image = context.tasker.controller.post_screencap().wait().get()
        second = {a: cls._recognize_reward_action_remaining(context, second_image, a)
                  for a in _REWARD_BUDGETS}
        return {a: first[a] if first[a] == second[a] else None for a in first}

    @classmethod
    def _sync_reward_action_counts(
        cls, context: Context, image, log_summary: bool = False
    ) -> dict:
        page_counts = cls._stable_action_counts(context, image)
        for action, remaining in page_counts.items():
            budget = _REWARD_BUDGETS[action]
            if budget.pending_before is not None:
                before = budget.pending_before
                if budget.confirm(before, remaining):
                    logger.success(f"恢复后{'除去' if action == 'remove' else '刷新'}已核实："
                                   f"剩余 {before}→{remaining}，本局确认消耗 {budget.confirmed} 次")
            else:
                budget.observe(remaining)
        if log_summary:
            for action, label in (("remove", "除去"), ("refresh", "刷新")):
                budget = _REWARD_BUDGETS[action]
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
            if _detect_disconnect_screen(context, last_image):
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
            _save_card_diagnostic(last_image, "reward-unstable", last_evaluated)
        return last_image, []

    def _confirm_remove(self, context: Context) -> bool:
        """Retry only a freshly recognized remove dialog, at most three times."""
        for _ in range(3):
            if context.tasker.stopping:
                return False
            image = context.tasker.controller.post_screencap().wait().get()
            if _detect_disconnect_screen(context, image):
                return False
            if self._reward_overlay(context, image) != "remove":
                return True
            self._click_reward_point(context, image, self.REMOVE_CONFIRM_BUTTON)
            time.sleep(0.8)
        image = context.tasker.controller.post_screencap().wait().get()
        return (not context.tasker.stopping and not _detect_disconnect_screen(context, image)
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
        _diagnostic_info(f"卡牌除去确认检测: orange_ratio={ratio:.3f}")
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
            if _detect_disconnect_screen(context, image):
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
    def _selection_key(item: dict) -> tuple:
        return (
            item["priority"],
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

    def _select(self, context: Context, selected: dict, reason: str) -> bool:
        if context.tasker.stopping:
            return True
        image = context.tasker.controller.post_screencap().wait().get()
        if _detect_disconnect_screen(context, image):
            return True
        if self._reward_overlay(context, image):
            return self._fallback_select(context, image)
        self._click_card(context, selected)
        logger.success(
            f"奖励卡选择: reason={reason} name={selected['name']} rarity={selected['rarity']} "
            f"extra_action={selected['extra_action']} priority={selected['priority']} "
            f"box={selected['box']}"
        )
        return True

    def _fallback_select(self, context: Context, image) -> bool:
        image = context.tasker.controller.post_screencap().wait().get()
        if context.tasker.stopping or _detect_disconnect_screen(context, image):
            logger.info("奖励选择已中断，交回任务停止或异常恢复流程")
            return True
        if self._reward_overlay(context, image):
            image, evaluated = self._recover_reward_page(context)
            if evaluated:
                self._sync_reward_action_counts(context, image)
            if (context.tasker.stopping or _detect_disconnect_screen(context, image)
                    or self._reward_overlay(context, image)):
                logger.warning("奖励弹层仍未关闭，交回恢复节点，本次不点击卡牌")
                return True
        # A scene can change during recovery. Never click the first reward on
        # a detail/shop/error page merely because card recognition failed.
        reward = context.run_recognition("ProduceChooseGetFlag", image)
        if not (reward and reward.hit):
            logger.info("已离开奖励页，交回流程重新识别")
            return True
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
        node = context.get_node_data('ProduceShoppingFlag')
        attach = node.get('attach', {}) if isinstance(node, dict) else {}
        state = dict(attach.get('automatic_visit', {})) if isinstance(attach, dict) else {}
        if updates:
            state.update(updates)
            context.override_pipeline({'ProduceShoppingFlag': {'attach': {'automatic_visit': state}}})
        return state

    def _finish_optional_shop(self, context):
        """Finish this visit after an uncertain optional purchase, without retrying it."""
        self._visit_state(context, exit_pending=True)
        for _ in range(6):
            if context.tasker.stopping:
                return True
            image = context.tasker.controller.post_screencap().wait().get()
            if _detect_disconnect_screen(context, image):
                return True
            # Close a freshly recognized cancel control before testing the
            # grid, which can remain visible behind a translucent modal.
            cancel = BattleDrinkSession._cancel_box(context, image)
            if cancel:
                self._click_product(context, cancel)
                time.sleep(.4)
                continue
            if self._is_shop_scene(context, image):
                self._dismiss_comment_bubble(context)
                result = context.run_task('ProduceShoppingExit')
                if result and result.status.succeeded:
                    # The pipeline's ExitConfirm is DirectHit, not a receipt.
                    # Do not clear intent while a swallowed click left the
                    # same shop visible (which would restart optional spending).
                    gone = 0
                    for _ in range(4):
                        time.sleep(.2)
                        frame = context.tasker.controller.post_screencap().wait().get()
                        if context.tasker.stopping or _detect_disconnect_screen(context, frame):
                            return True
                        gone = gone + 1 if not self._is_shop_scene(context, frame) else 0
                        if gone >= 2:
                            self._visit_state(context, exit_pending=False, heal_pending=False)
                            break
                return True
            time.sleep(.3)
        # A transition/disconnect may finish asynchronously. Keep exit intent
        # across action instances so reentry cannot purchase/refresh again.
        return True

    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        if self._visit_state(context).get('exit_pending'):
            return self._finish_optional_shop(context)
        lists = _load_card_lists()
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
        image = context.tasker.controller.post_screencap().wait().get()
        if not self._dismiss_comment_bubble(context):
            return True
        image = self._wait_shop_ready(context)
        if image is None:
            return self._disconnect_detected or context.tasker.stopping or getattr(self, "_shop_departed", False)
        from ..shop import is_interval_shop
        if is_interval_shop(context, image):
            # A fading/split header can initially hit the generic shop node.
            # Re-route before scanning the four-slot consultation layout.
            return ProduceHIFIntervalShoppingAuto().run(context, argv)
        skip_delete = bool(getattr(self, "_skip_delete", False))
        self._skip_delete = False
        if self._delete_cancel_state(context):
            # A Cancel can finish between callbacks. Do not enter deletion
            # again when the generic shop entry observes that same return.
            self._delete_cancel_state(context, pending=False)
            skip_delete = True
        if skip_delete:
            self._shop_delete_visit(context, image, checked=True)
        skip_delete = self._shop_delete_visit(context, image) or skip_delete
        logger.info("咨询策略: 仅删除 N/黑名单/R，无合适候选就跳过删除；购买白名单卡；否则只买目标中的最低价饮料")
        points = self._recognize_points(context, image)

        delete_entry = self._find_shop_delete_entry(image)
        from ..shop import read_number
        delete_cost = read_number(context, image, [500, 950, 185, 90]) if delete_entry else None
        if not skip_delete and delete_entry and points is not None and delete_cost is not None and points >= delete_cost:
            # Mark before opening the list: a transition or resumed callback
            # must not restart this visit's scan after every purchase.
            self._shop_delete_visit(context, image, checked=True)
            delete_status = self._try_delete_owned_card(
                context, image, blacklist, whitelist, replacement
            )
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
            logger.info("咨询本次访问已检查删牌，继续购物，不再打开删除列表" if skip_delete else
                        f"咨询跳过删除: 入口={bool(delete_entry)} 积分={points} 费用={delete_cost}")

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
                    key=lambda item: (
                        item["priority"],
                        -(item["price"] if item["price"] is not None else 9999),
                        -item["box"][0],
                    ),
                )
                item_type = f"白名单卡牌 {selected['name']}"
                blocked_key = "cards"
                logger.info(
                    f"咨询白名单卡候选: name={selected['name']} price={selected['price']} "
                    f"rarity={selected['rarity']} box={selected['box']}"
                )
            else:
                available_drinks = [item for item in drinks
                                    if item["box"] not in blocked["drinks"] and self._is_affordable(item, points)]
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
                logger.info(
                    f"咨询最低价目标饮料: name={selected['name']} price={selected['price']} box={selected['box']}"
                )
                if full:
                    if not self._make_drink_space(context, selected["id"], non_target_only=True):
                        return True if self._disconnect_detected or context.tasker.stopping else self._finish_optional_shop(context)
                    image = self._wait_shop_ready(context)
                    if image is None or self._drink_inventory_full(context, image) is not False:
                        return True if self._disconnect_detected or context.tasker.stopping or getattr(self, "_shop_departed", False) else self._finish_optional_shop(context)

            attempts += 1
            status = self._purchase(context, selected["box"], item_type,
                is_drink=blocked_key == "drinks", drink_id=selected.get("id"), expected_price=selected["price"])
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
            logger.warning(
                f"该商品当前无法购买，已跳过且不重复扫描: type={item_type}, box={selected['box']}"
            )

        if attempts >= self.MAX_PURCHASE_ATTEMPTS:
            logger.warning("咨询购买达到安全上限，停止购买并退出，避免循环卡住")
        if not self._dismiss_comment_bubble(context):
            return True
        image = context.tasker.controller.post_screencap().wait().get()
        disconnect_node = _detect_disconnect_screen(context, image)
        if disconnect_node:
            self._disconnect_detected = True
            logger.warning(
                f"咨询收尾阶段检测到断线界面 {disconnect_node}，立即交回全局恢复流程"
            )
            return True
        if self._is_shop_scene(context, image):
            context.run_task("ProduceShoppingExit")
        else:
            logger.info("咨询购买结束时已离开商品页，无需再次点击退出")
        return True

    def _drink_inventory_full(self, context, image):
        """Verify capacity without opening drinks; duplicate kinds may be bought."""
        from ..shop import shop_occupied
        previous = shop_occupied(context, image)
        for _ in range(4):
            if context.tasker.stopping:
                return None
            time.sleep(.2)
            image = context.tasker.controller.post_screencap().wait().get()
            if _detect_disconnect_screen(context, image):
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
    def _is_affordable(item: dict, points: Optional[int]) -> bool:
        price = item.get("price")
        return points is not None and price is not None and 0 < price <= points

    @staticmethod
    def _recognize_points(context: Context, image) -> Optional[int]:
        detail = context.run_recognition(
            "ProduceRecognitionPoint",
            image,
            pipeline_override={"ProduceRecognitionPoint": {"roi": [320, 90, 130, 54]}},
        )
        if not (detail and detail.hit and detail.best_result):
            logger.warning("咨询积分识别失败，本帧不购买")
            return None
        try:
            value = int("".join(filter(str.isdigit, detail.best_result.text)))
            logger.info(f"咨询当前积分: {value}")
            return value
        except (TypeError, ValueError):
            logger.warning("咨询积分解析失败，本帧不购买")
            return None

    @staticmethod
    def _recognize_price(context: Context, image, box: list) -> Optional[int]:
        from ..shop import parse_shop_price
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
            if value is None or float(getattr(result, "score", 1.0)) < .85:
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
        from ..shop import read_number
        value = read_number(context, image, [x + width // 2, y + height - 2, width // 2 + 12, 48])
        return value if value is not None and 0 < value <= 999 else None

    @staticmethod
    def _slot_is_sold(image, box: list) -> bool:
        x, y, width, height = box
        crop = image[y + 12:y + height - 12, x + 12:x + width - 12, :3]
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
            roi = image[y + height:y + height + 42, x:x + width, :3]
            if not roi.size:
                continue
            bright = (roi.min(axis=2) > 215).mean()
            if bright > 0.22:
                visible_prices += 1
        return visible_prices >= 4

    def _wait_shop_ready(self, context):
        """Do not scan through support-item acquisition effects on the shop."""
        from ..shop import price_grid_visible, stock_signature
        previous = None
        taps = 0
        slots = self.CARD_SLOTS + self.DRINK_SLOTS
        for _ in range(12):
            if context.tasker.stopping:
                return None
            image = context.tasker.controller.post_screencap().wait().get()
            if _detect_disconnect_screen(context, image):
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
            time.sleep(.4)
        logger.warning("咨询商品价格区未稳定，停止扫描，不当作无目标饮料")
        return None

    @classmethod
    def _find_shop_delete_entry(cls, image) -> Optional[tuple[int, int]]:
        """Locate only the blue delete button in the fixed bottom shop band."""
        x, y, width, height = cls.DELETE_ENTRY_ROI
        crop = image[y:y + height, x:x + width, :3]
        if not crop.size:
            return None
        # Maa screenshots are BGR.  The Japanese delete button is saturated
        # cyan/blue; the product tiles above this ROI are deliberately excluded.
        blue = crop[:, :, 0].astype(np.int16)
        green = crop[:, :, 1].astype(np.int16)
        red = crop[:, :, 2].astype(np.int16)
        mask = (
            (blue > 175)
            & (green > 85)
            & (green < 215)
            & (red < 135)
            & ((blue - red) > 70)
        )
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
            disconnect_node = _detect_disconnect_screen(context, delete_image)
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
            disconnect_node = _detect_disconnect_screen(context, delete_image)
            if disconnect_node:
                self._disconnect_detected = True
                return "disconnected"
            entered_delete_page = not self._looks_like_shop_grid(delete_image)
        if not entered_delete_page:
            logger.warning("咨询点击删除入口后商品网格仍未变化，确认当前不可用")
            return "unavailable"

        return self._delete_from_deck(context, blacklist, whitelist, replacement)

    def _delete_from_deck(self, context, blacklist, whitelist, replacement):
        from ..removal import read_delete_confirmation, read_delete_button, selected_card_box_matches
        # Replacement-list permission applies to exchange only, never deletion.
        replacement = set()
        if context.tasker.stopping:
            return "cancel_pending"
        if self._delete_cancel_state(context):
            return self._cancel_delete(context)
        # Use the current shared fast-hit policy. Details are a reversible
        # identity probe, never authority to commit removal.
        scanner = ProduceHIFChangeSourceAuto()
        scanner._interrupted = False
        scanner._removal_search_policy = (blacklist, whitelist, replacement, False)
        initial_page = scanner._rewind_source(context, change=False)
        if initial_page is None:
            if scanner._interrupted:
                self._disconnect_detected = True
                return "disconnected"
            logger.info("咨询删除牌库暂未稳定，跳过本次删除")
            return self._cancel_delete(context)
        selected = scanner._scan_source(context, blacklist, whitelist, replacement, change=False,
                                        initial_page=initial_page, strict=False)
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
            if not selected_card_box_matches(image, selected['box']):
                self._click_product(context, selected["box"])
        time.sleep(0.4)
        image = context.tasker.controller.post_screencap().wait().get()
        if _detect_disconnect_screen(context, image):
            self._disconnect_detected = True
            return "disconnected"
        selected = self._verify_delete_selection(context, image, selected, blacklist, whitelist, replacement)
        if selected is None:
            logger.warning("咨询选中卡名或删除优先级未核实，取消删除，不提交未知牌")
            return self._cancel_delete(context)
        logger.info(f"咨询删除已核实: name={selected['name']} rarity={selected['rarity']} "
                    f"reason_rank={selected['delete_rank']} box={selected['box']}")
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
            time.sleep(.35)
            image = context.tasker.controller.post_screencap().wait().get()
            if _detect_disconnect_screen(context, image):
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
        attach = dict(node.get('attach', {})) if isinstance(node, dict) and isinstance(node.get('attach', {}), dict) else {}
        key = 'shop_delete_visit'
        if reset:
            # Native override_pipeline merges attach objects recursively;
            # omitting a key does not erase the preceding visit's state.
            attach[key] = {'checked': False, 'stock': []}
            context.override_pipeline({'ProduceHIFDeleteFlag': {'attach': attach}})
            return False
        old = attach.get(key)
        old = old if isinstance(old, dict) else {}
        # Called only after the consultation grid is confirmed stable.
        slots = ProduceShoppingAuto.CARD_SLOTS + ProduceShoppingAuto.DRINK_SLOTS
        stock = []
        if reference_frame(image):
            for box in slots:
                x,y,w,h = box
                if ProduceShoppingAuto._slot_is_sold(image, box):
                    stock.append(None)
                else:
                    stock.append(image[y+25:y+h-25:12,x+25:x+w-25:12,:3].astype(int).reshape(-1).tolist())
        previous = old.get('stock', [])
        changed = len(stock) == len(previous) == len(slots) and any(
            now is not None and (then is None or len(now) != len(then)
                                 or float(np.abs(np.asarray(now)-np.asarray(then)).mean()) > 12)
            for now,then in zip(stock,previous))
        if changed:
            old = {}
        value = {'checked': bool(old.get('checked', False)), 'stock': old.get('stock') or stock}
        if checked is not None:
            value['checked'] = bool(checked)
        if value != attach.get(key):
            attach[key] = value
            context.override_pipeline({'ProduceHIFDeleteFlag': {'attach': attach}})
        return value['checked']

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
        from ..shop import ocr
        self._delete_cancel_state(context, pending=True)
        for attempt in range(7):
            if context.tasker.stopping:
                return "cancel_pending"
            image = context.tasker.controller.post_screencap().wait().get()
            if _detect_disconnect_screen(context, image):
                self._disconnect_detected = True
                return "disconnected"
            flag = context.run_recognition("ProduceChooseDeleteFlag", image, pipeline_override={
                "ProduceChooseDeleteFlag": {"template": ["produce/delete_icon.png"],
                                            "focus": {}}})
            if not (flag and flag.hit):
                if self._is_shop_scene(context, image):
                    self._delete_cancel_state(context, pending=False)
                    logger.info("咨询已取消删除，继续商店流程")
                    return "skipped"
            elif attempt in (0, 2, 4):
                buttons = ocr(context, image, [65, 1100, 290, 130],
                              [r"^[×✕xX]?\s*(キャンセル|取消)$"])
                if len(buttons) == 1 and buttons[0].score >= .80 and not context.tasker.stopping:
                    self._click_product(context, list(buttons[0].box))
            time.sleep(.3)
        logger.warning("咨询取消删除尚未确认，交回主流程重试，不提交删除、不结束培育")
        return "cancel_pending"

    def _commit_delete(self, context, invoice, selected):
        from ..removal import read_delete_confirmation
        if context.tasker.stopping:
            return "uncertain"
        logger.info(f"咨询删除二次确认: {selected['name']} 积分={invoice['before']}→{invoice['after']}")
        context.tasker.controller.post_click(*invoice["confirm"]).wait()
        confirmations = 0
        for _ in range(10):
            if context.tasker.stopping:
                return "uncertain"
            time.sleep(.4)
            image = context.tasker.controller.post_screencap().wait().get()
            if _detect_disconnect_screen(context, image):
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
    def _choose_delete_candidate(candidates: list, blacklist: set, whitelist: set,
                                 replacement: Optional[set] = None) -> Optional[dict]:
        """Shop deletion is limited to N > blacklist > R, without replacement overrides."""
        return ProduceShoppingAuto._choose_removal_candidate(candidates, blacklist, whitelist)

    @staticmethod
    def _choose_removal_candidate(candidates: list, blacklist: set, whitelist: set,
                                  replacement: Optional[set] = None) -> Optional[dict]:
        """N > blacklist > R > explicit replacement; unknown names are protected."""
        replacement = replacement or set()
        eligible = []
        for candidate in candidates:
            item = dict(candidate)
            if not item.get("name_key") or item.get("name_confidence", 0) < .55:
                continue
            if item["name_key"] in whitelist and item["name_key"] not in replacement:
                continue
            if (item.get("rarity_confidence", 1) < .30 and not item.get("identity_verified", False)
                    and not item.get("square_art_trusted", False)):
                continue
            if item["rarity"] == "N":
                item["delete_rank"] = 3
            elif item["name_key"] in blacklist:
                item["delete_rank"] = 2
            elif item["rarity"] == "R":
                item["delete_rank"] = 1
            elif item["name_key"] in replacement and item["rarity"] in ("SR", "SSR"):
                item["delete_rank"] = 0
            else:
                continue
            eligible.append(item)

        return max(eligible, key=ProduceShoppingAuto._removal_key, default=None)

    @staticmethod
    def _removal_key(item):
        return (item["delete_rank"],
                1 if item["delete_rank"] == 0 and item["rarity"] == "SR" else 0,
                item["name_confidence"], -item["box"][1], -item["box"][0])

    def _verify_delete_selection(self, context, image, selected, blacklist, whitelist, replacement):
        """Read the selected card's upper detail panel before irreversible input."""
        from ..removal import DECK_TITLE_OCR_MIN_SCORE, selected_card_box_matches
        from ..shop import ocr
        if not selected_card_box_matches(image, selected['box']):
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
        verified = dict(selected, name=matched.matched_name, rarity=matched.rarity,
                        name_confidence=1.0, identity_verified=True)
        return self._choose_delete_candidate([verified], blacklist, whitelist, replacement)

    def _probe_removal_candidates(self, context, candidates, blacklist, whitelist, replacement, *, change=False,
                                  strict=True, incumbent=None):
        from ..removal import read_selected_card, selected_card_box_matches
        from ..deck_scan import scan_identity_known, removal_priority_bound
        self._deck_probe_count = 0
        verified = [dict(item) for item in candidates]
        choose = self._choose_removal_candidate if change else self._choose_delete_candidate
        known = choose([item for item in verified if scan_identity_known(item)], blacklist, whitelist, replacement)
        if known and (not strict or known["delete_rank"] == 3):
            return verified
        # Strong art + frame evidence is sufficient for scanning. Only unclear
        # identities need detail probes; any unknown may conceal a priority N.
        # This does not bypass the final selected-card name check.
        potential = []
        for index, item in enumerate(verified):
            if scan_identity_known(item):
                continue
            tentative = dict(item, name_confidence=1., rarity_confidence=1.)
            policy = choose([tentative], blacklist, whitelist, replacement)
            if item.get("frame_rarity") == "N" and item.get("rarity_confidence", 0) >= .55:
                # A confident frame is a probe ordering hint, never identity
                # proof. A wrong illustration match must not hide a starter.
                potential.append(((4, 0, 0, -item["box"][1], -item["box"][0]), index))
            elif policy:
                potential.append((self._removal_key(policy), index))
            else:
                potential.append((({"N": 3, "R": 1}.get(item.get("rarity"), 0), 0, 0,
                                   -item["box"][1], -item["box"][0]), index))
        for _, index in sorted(potential, reverse=True):
            item = verified[index]
            known = choose([c for c in verified if scan_identity_known(c)], blacklist, whitelist, replacement)
            if known and not strict:
                return verified
            leaders = [c for c in (known, incumbent) if c is not None]
            best_tier = max((self._removal_key(c)[:2] for c in leaders), default=(-1, 0))
            if removal_priority_bound(item, blacklist, replacement, change=change) <= best_tier:
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
                    if cached_policy and (not strict or cached_policy["delete_rank"] == 3):
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
                time.sleep(.25)
                image = context.tasker.controller.post_screencap().wait().get()
                if _detect_disconnect_screen(context, image):
                    self._disconnect_detected = True
                    return []
                if not selected_card_box_matches(image, item['box']):
                    exact, stable = None, 0
                    if _ == 1:
                        # A swallowed click must not attach the preceding
                        # detail to this tile. One bounded retry, then reject.
                        self._click_product(context, item['box'])
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
                    if policy and (not strict or policy["delete_rank"] == 3):
                        return verified
                    break
            if stable < 2:
                verified[index]["name_confidence"] = 0.
                verified[index]["probe_failed"] = True
                if not context.tasker.stopping:
                    _save_card_diagnostic(image, "deck-detail-unconfirmed", [dict(item)])
                logger.debug(f"换出/删除候选详情未确认，保护此牌: name={item['name']} box={item['box']}")
        return verified

    @classmethod
    def _comment_bubble_present(cls, image) -> bool:
        """商店页底部是否有偶像评论气泡（大面积纯白面板盖住退出按钮）。

        实测气泡出现时该区域白色占比约 0.93，正常商品页为 0.0。"""
        x, y, width, height = cls.COMMENT_BUBBLE_BOX
        frame_height, frame_width = image.shape[:2]
        crop = image[round(y * frame_height / 1280):round((y + height) * frame_height / 1280),
                     round(x * frame_width / 720):round((x + width) * frame_width / 720), :3]
        if not crop.size:
            return False
        return float((crop.min(axis=2) > 238).mean()) >= 0.5

    def _dismiss_comment_bubble(self, context: Context) -> bool:
        """点掉商店底部的偶像评论气泡，避免盖住「✕終了」退出按钮。

        白面板检测本身不区分页面（奖励页/详情页底部也是白色面板），而
        _is_shop_scene 的价格药丸兜底又会被气泡干扰，因此这里直接用商店
        头部模板（ProduceShoppingFlag，气泡盖不到的 ROI）确认场景后再点击。"""
        for attempt in range(3):
            if context.tasker.stopping:
                return False
            image = context.tasker.controller.post_screencap().wait().get()
            if _detect_disconnect_screen(context, image):
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
        if _detect_disconnect_screen(context, image):
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
        from ..deck_scan import normalize_deck_boxes
        return normalize_deck_boxes(image, [item['box'] for item in candidates], top=380)

    def _recognize_delete_cards(self, context: Context, image, *, verified_boxes=None) -> list:
        started = time.perf_counter()
        boxes = self._delete_card_boxes(context, image) if verified_boxes is None else verified_boxes
        evaluated = ProduceChooseRewardAuto()._evaluate(context, image, verified_boxes=boxes, read_extra_action=False, diagnostics=False)
        from ..square_identity import resolve_selected
        evaluated = resolve_selected(context, image, evaluated)
        for item in evaluated:
            logger.debug(f"咨询删除候选: box={item['box']} name={item['name']} "
                        f"name_confidence={item['name_confidence']:.3f} rarity={item['rarity']} "
                        f"rarity_confidence={item['rarity_confidence']:.3f}")
        from ..deck_scan import scan_identity_known
        logger.debug(f"删除整页识别：{sum(scan_identity_known(i) for i in evaluated)}/{len(evaluated)} 张已确认，"
                    f"匹配耗时={time.perf_counter()-started:.3f}s；其余才使用详情兜底")
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
        context.tasker.controller.post_click(
            box[0] + box[2] // 2, box[1] + box[3] // 2
        ).wait()

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
        disconnect_node = _detect_disconnect_screen(context, image)
        if disconnect_node:
            self._disconnect_detected = True
            logger.warning(
                f"咨询重进商店时检测到断线界面 {disconnect_node}，立即交回全局恢复流程"
            )
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
                disconnect_node = _detect_disconnect_screen(context, image)
                if disconnect_node:
                    self._disconnect_detected = True
                    logger.warning(
                        f"咨询重进商店时检测到断线界面 {disconnect_node}，立即交回全局恢复流程"
                    )
                    return False
                if self._is_shop_scene(context, image):
                    # Product tiles fade in after the header.  Scanning on the
                    # first header frame can incorrectly report no cards.
                    time.sleep(0.9)
                    logger.debug(f"咨询已确认商品页: attempt={click_attempt + 1}")
                    return True
        return False

    def _recognize_drinks(self, context: Context, image, blocked_boxes: list) -> list:
        from ..shop import TARGET_DRINK_NAMES
        from ..drink_visual import match as match_drink
        found = []
        for box in self.DRINK_SLOTS:
            if any(intersection_over_union(box, blocked) > 0.30 for blocked in blocked_boxes):
                continue
            if self._slot_is_sold(image, box):
                continue
            visual = match_drink(image, box)
            if visual is not None:
                if visual['id'] in TARGET_NAMES:
                    found.append({**visual, "price": self._recognize_price(context, image, box),
                                  "identity_score": visual['score']})
                continue
            identities = []
            for drink_id in TARGET_NAMES:
                template = f"produce/drinks/{drink_id}.png"
                detail = context.run_recognition("ProduceRecognitionShopDrinks", image,
                    pipeline_override={"ProduceRecognitionShopDrinks": {
                        "recognition": "FeatureMatch", "roi": box, "template": [template],
                        "count": 8, "ratio": .62, "order_by": "Score"}})
                for result in list(detail.filtered_results or []) if detail and detail.hit else []:
                    hit_box = list(result.box)
                    count = int(result.count)
                    if intersection_over_union(box, hit_box) >= .55:
                        identities.append((count, drink_id))
            identities.sort(reverse=True)
            if not identities or (len(identities) > 1 and identities[0][0] < identities[1][0] + 4):
                continue
            count, drink_id = identities[0]
            price = self._recognize_price(context, image, box)
            found.append({"box": list(box), "price": price, "id": drink_id,
                          "name": TARGET_DRINK_NAMES[drink_id], "identity_score": count})
        logger.info(
            "咨询饮料一次扫描: "
            + ", ".join(
                f"{item['name']} x={item['box'][0]} price={item['price']}" for item in found
            )
        )
        return found

    def _read_owned_drinks(self, context):
        from ..shop import TARGET_DRINK_NAMES, ocr, owned_detail_title, shop_occupied
        from ..drink_visual import scan, INVENTORY_BOXES, match as match_drink
        image = context.tasker.controller.post_screencap().wait().get()
        occupied = shop_occupied(context, image)
        visual_before = scan(image, INVENTORY_BOXES)
        time.sleep(.35)
        image = context.tasker.controller.post_screencap().wait().get()
        if occupied != shop_occupied(context, image):
            return None
        owned = {}
        for slot in sorted(occupied):
            if context.tasker.stopping or _detect_disconnect_screen(context, image):
                self._disconnect_detected = bool(_detect_disconnect_screen(context, image))
                return None
            visual = match_drink(image, INVENTORY_BOXES[slot])
            if visual and visual_before.get(slot, {}).get('id') == visual['id']:
                owned[slot] = {"id": visual['id'] if visual['id'] in TARGET_DRINK_NAMES else None,
                               "name": visual['name'], "verified": True, "visual_trusted": True}
                continue
            context.tasker.controller.post_click(*ProduceChooseRewardAuto.INVENTORY_SLOT_CENTERS[slot]).wait()
            name, previous, stable, cancel = None, None, 0, []
            for _ in range(5):
                if context.tasker.stopping:
                    return None
                time.sleep(.3)
                image = context.tasker.controller.post_screencap().wait().get()
                if _detect_disconnect_screen(context, image):
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
            time.sleep(.4)
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
        from ..shop import choose_discard, owned_throw_box, read_discard_confirmation, shop_occupied
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
        if context.tasker.stopping or _detect_disconnect_screen(context, image):
            self._disconnect_detected = bool(_detect_disconnect_screen(context, image))
            return False
        context.tasker.controller.post_click(*ProduceChooseRewardAuto.INVENTORY_SLOT_CENTERS[slot]).wait()
        time.sleep(.65)
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
            time.sleep(.3)
            image = context.tasker.controller.post_screencap().wait().get()
            if _detect_disconnect_screen(context, image):
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
            time.sleep(.35)
            image = context.tasker.controller.post_screencap().wait().get()
            if _detect_disconnect_screen(context, image):
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

    def _recognize_shop_cards(self, context: Context, image, blocked_boxes: list) -> list:
        evaluated = []
        for index, box in enumerate(self.CARD_SLOTS, start=1):
            if any(
                intersection_over_union(box, blocked) > 0.30
                for blocked in blocked_boxes
            ):
                continue
            if self._slot_is_sold(image, box):
                continue
            rarity = classify_rarity(image, box)
            extra, extra_score, _ = ProduceCardsAuto._extra_action_status(
                context, image, box
            )
            from ..square_identity import recognize as recognize_square, evidence as square_evidence
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
            _diagnostic_info(
                f"咨询卡牌识别[{index}] box={box} name={item['name']} "
                f"rarity={resolved_rarity} price={item['price']} "
                f"confidence={rarity.confidence:.3f} distance={rarity.best_distance:.3f} "
                f"margin={rarity.margin:.3f} frame_bgr={item['frame_bgr']} extra_action={extra} "
                f"extra_score={extra_score:.3f} priority={item['priority']}"
            )
        if any(
            item["rarity"] == "unknown"
            or item["rarity_confidence"] < 0.15
            or 0.46 < item["extra_score"] < 0.52
            for item in evaluated
        ):
            _save_card_diagnostic(image, "shop", evaluated)
        return evaluated

    def _resolve_shop_card_identities(self, context, cards):
        """Probe only rejected tiles; normal whole-page recognition sends no input."""
        from ..removal import read_selected_card, selected_card_box_matches
        for item in cards:
            if (not item.get('square_art_attempted') or item.get('square_art_trusted')
                    or item.get('identity_verified')):
                continue
            if context.tasker.stopping:
                break
            image = context.tasker.controller.post_screencap().wait().get()
            if _detect_disconnect_screen(context, image):
                self._disconnect_detected = True
                break
            if not self._is_shop_scene(context, image) or self._slot_is_sold(image, item['box']):
                break
            self._click_product(context, item['box'])
            previous, stable = None, 0
            for _ in range(4):
                if context.tasker.stopping:
                    break
                time.sleep(.25)
                image = context.tasker.controller.post_screencap().wait().get()
                if _detect_disconnect_screen(context, image):
                    self._disconnect_detected = True
                    return cards
                # Never attach stale detail from the preceding selected slot.
                if not selected_card_box_matches(image, item['box']):
                    previous, stable = None, 0
                    continue
                current = read_selected_card(context, image)
                stable = stable+1 if current and current == previous else 1 if current else 0
                previous = current
                if stable >= 2:
                    item.update(current)
                    item['priority'] = rarity_priority(item['rarity'], item.get('extra_action', 'unknown'))
                    logger.info(f"商店卡图兜底确认：{item['name']} box={item['box']}")
                    break
            if stable < 2:
                logger.warning(f"商店卡图与详情均未确认，跳过此牌：box={item['box']}")
        return cards

    def _purchase(self, context: Context, box: list, item_type: str, *, is_drink: bool = False,
                  drink_id: Optional[str] = None, expected_price: Optional[int] = None) -> str:
        if context.tasker.stopping:
            return "stopped"
        image = context.tasker.controller.post_screencap().wait().get()
        if _detect_disconnect_screen(context, image):
            self._disconnect_detected = True
            return "disconnected"
        if self._comment_bubble_present(image) and not self._dismiss_comment_bubble(context):
            return "disconnected" if self._disconnect_detected else "unavailable"
        before_points = self._recognize_points(context, image) if expected_price is not None else None
        if expected_price is not None and not self._is_affordable({"price": expected_price}, before_points):
            logger.warning("咨询即时积分或价格未确认，不点击购买")
            return "no-funds" if before_points is not None and before_points < expected_price else "unavailable"
        context.tasker.controller.post_click(
            box[0] + box[2] // 2, box[1] + box[3] // 2
        ).wait()
        time.sleep(0.5)
        image = context.tasker.controller.post_screencap().wait().get()
        disconnect_node = _detect_disconnect_screen(context, image)
        if disconnect_node:
            self._disconnect_detected = True
            logger.warning(
                f"咨询点击商品后检测到断线界面 {disconnect_node}，立即交回全局恢复流程"
            )
            return "disconnected"

        if is_drink:
            from ..shop import selected_drink_matches
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
        context.tasker.controller.post_click(
            buy_box[0] + buy_box[2] // 2, buy_box[1] + buy_box[3] // 2
        ).wait()
        time.sleep(0.5)

        image = context.tasker.controller.post_screencap().wait().get()
        disconnect_node = _detect_disconnect_screen(context, image)
        if disconnect_node:
            self._disconnect_detected = True
            logger.warning(
                f"咨询购买确认时检测到断线界面 {disconnect_node}，立即交回全局恢复流程"
            )
            return "disconnected"
        point_runout = context.run_recognition(
            "ProduceShoppingDecide",
            image,
            pipeline_override={
                "ProduceShoppingDecide": {"template": "produce/point_runout.png"}
            },
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
            pipeline_override={
                "ProduceShoppingDecide": {"template": "produce/shopping_exchange.png"}
            },
        )
        if decide and decide.hit and decide.best_result:
            from ..shop import ocr
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
                if _detect_disconnect_screen(context, image):
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
                time.sleep(.35)
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

    @classmethod
    def _is_shop_scene(cls, context, image):
        from ..shop import consultation_grid_visible
        return consultation_grid_visible(image)

    def _wait_shop_ready(self, context):
        from ..shop import stock_signature
        previous = None
        absent = taps = 0
        self._shop_departed = False
        for _ in range(10):
            if context.tasker.stopping:
                return None
            image = context.tasker.controller.post_screencap().wait().get()
            if _detect_disconnect_screen(context, image):
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
            time.sleep(.25)
        logger.warning("HIF咨询商品区仍未稳定，保留现场供重识别")
        return None

    def _reenter_shop(self, context):
        from ..shop import read_consultation_desk_entry
        consecutive = absent = 0
        previous_desk = None
        clicked = False
        self._shop_departed = False
        for _ in range(12):
            if context.tasker.stopping:
                return False
            image = context.tasker.controller.post_screencap().wait().get()
            if _detect_disconnect_screen(context, image):
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
                        header = context.run_recognition("ProduceShoppingFlag", image,
                            pipeline_override={"ProduceShoppingFlag": {"focus": {}}})
                        if header and header.hit:
                            # Acquired-item effects can hide prices while the
                            # header remains. A verified desk is handled above.
                            absent = 0
                            time.sleep(.25)
                            continue
                        absent += 1
                        if absent >= 2:
                            self._shop_departed = True
                            return False
            time.sleep(.25)
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
        from ..shop import is_interval_shop
        return is_interval_shop(context, image)

    def _heal_before_drinks(self, context, image, *, spend_remaining=False):
        from ..shop import read_interval_health, read_interval_heal_button, read_interval_recovery, interval_heal_target

        strategy = (context.get_node_data("ProduceHIFStrategy") or {}).get("attach", {})
        threshold = interval_heal_target(strategy.get("interval_heal_target", 20))
        stage = "离店" if spend_remaining else "入店"
        if getattr(self, '_heal_receipt_pending', False) or self._visit_state(context).get('heal_pending'):
            logger.warning("HIF轮间先前回血扣款结果尚未确认，本次离店不重复提交回血")
            return image

        health = read_interval_health(context, image)
        for _ in range(2):
            if health is not None or context.tasker.stopping:
                break
            time.sleep(.2)
            image = context.tasker.controller.post_screencap().wait().get()
            if _detect_disconnect_screen(context, image):
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
        if points is None or points < 5 or not button:
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
            time.sleep(.25)
            frame = context.tasker.controller.post_screencap().wait().get()
            if _detect_disconnect_screen(context, frame):
                self._disconnect_detected = True
                return None
            dialog = read_interval_recovery(context, frame, health)
            if not dialog:
                missing_dialog += 1
                if missing_dialog >= 6:
                    break
                continue
            missing_dialog = 0
            quote = dialog['quote']
            if not quote:
                unreadable_quote += 1
                if unreadable_quote < 3:
                    continue
                logger.warning(f"HIF轮间回血预览连续三帧未通过校验：{dialog.get('reason')} "
                               f"字段={dialog.get('fields')} 小框={dialog.get('rois')}，取消本次回血")
                break
            unreadable_quote = 0
            if quote['before'] != points or quote['maximum'] != maximum or quote['projected']-quote['amount'] != current:
                logger.warning("HIF轮间回血预览与入店体力/余额不一致，取消本次回血")
                break
            signature = (quote['amount'], quote['after'])
            affordable_target = min(target, current + points * quote['amount'] // (points - quote['after']))
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
            direction = ('minus' if quote['after'] < 0 or quote['projected'] > affordable_target
                         else 'plus' if quote['projected'] < affordable_target else None)
            # If the game's step jumps across 20, keep the smallest observed
            # affordable value above it instead of oscillating on the slider.
            if (pending and pending[0] == 'plus' and previous_projected < target <= quote['projected']
                    and quote['after'] >= 0):
                direction = None
            step = abs(quote['projected'] - previous_projected) if previous_projected is not None else 0
            # A two-HP step can end below the arithmetic budget limit (e.g.
            # 21 HP + 35P permits +6, not +7). Accept the observed affordable
            # quote when the next proven step would exceed the actual budget.
            if (direction == 'plus' and pending and pending[0] == 'plus' and step
                    and quote['after'] >= 0
                    and quote['projected'] + step > current + points * quote['amount'] // (points - quote['after'])):
                direction = None
            if direction == 'minus' and step and quote['after'] >= 0 and quote['projected'] - step < affordable_target:
                direction = None
            # If one upward step exceeds the budget, accept the best affordable
            # lower step instead of oscillating across the unaffordable value.
            if (direction == 'plus' and pending and pending[0] == 'minus' and step
                    and quote['after'] >= 0 and quote['projected'] + step > affordable_target
                    and previous_projected > current + points * quote['amount'] // (points - quote['after'])):
                direction = None
            # At low balance the game can hide the adjustment row altogether.
            # On exit, a twice-verified affordable gain is useful even when
            # its + control cannot be located. Never infer missing digits or
            # bypass the quote/receipt checks, and retain the entry HP target.
            if (spend_remaining and direction == 'plus' and not quote['plus']
                    and quote['after'] >= 0):
                direction = None
            if direction:
                control = quote[direction]
                if not control or pending == (direction, signature):
                    logger.warning(f"HIF轮间回血调节未就绪: 方向={direction} 预览={quote['projected']} 控件={control}")
                    break
                self._click_product(context, control)
                previous_projected = quote['projected']
                pending, stable_quote = (direction, signature), None
                continue
            if not quote['confirm']:
                logger.warning("HIF轮间回血确认按钮未识别，取消本次回血")
                break
            if stable_quote != signature:
                stable_quote = signature
                continue
            reason = "离店前使用剩余积分回血" if spend_remaining else f"体力低于{threshold}，先回血"
            logger.info(f"HIF轮间{reason}: {current}→{quote['projected']}，费用{points-quote['after']}")
            self._heal_receipt_pending = True
            self._visit_state(context, heal_pending=True)
            self._click_product(context, quote['confirm'])
            # Never submit the recovery twice on an unclear receipt.
            for _ in range(10):
                if context.tasker.stopping:
                    return None
                time.sleep(.3)
                frame = context.tasker.controller.post_screencap().wait().get()
                if _detect_disconnect_screen(context, frame):
                    self._disconnect_detected = True
                    return None
                if self._is_shop_scene(context, frame):
                    actual = read_interval_health(context, frame)
                    balance = self._recognize_points(context, frame)
                    if actual == (quote['projected'], maximum) and balance == quote['after']:
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
        if _detect_disconnect_screen(context, frame):
            self._disconnect_detected = True
            return None
        dialog = read_interval_recovery(context, frame)
        if dialog and dialog['cancel']:
            self._click_product(context, dialog['cancel'])
        logger.warning("HIF轮间本次回血未完成，取消后继续原流程，不重复扣款")
        return self._wait_shop_ready(context)

    def run(self, context, argv):
        from ..shop import read_refresh, stock_signature, refresh_is_affordable, INTERVAL_CARD_SLOTS
        self._disconnect_detected = False
        if self._visit_state(context).get("exit_pending"):
            return self._finish_optional_shop(context)
        self._heal_receipt_pending = bool(self._visit_state(context).get("heal_pending"))
        self._owned_drinks = None
        self._inventory_read_failed = False
        refreshes, purchases, unreadable = 0, 0, 0
        bought_boxes = []
        health_checked = False
        time.sleep(.9)
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
            if not health_checked:
                health_checked = True
                image = self._heal_before_drinks(context, image)
                if image is None:
                    return True  # Let global recovery handle a transition/disconnect.
            points = self._recognize_points(context, image)
            full = self._drink_inventory_full(context, image)
            if full is None:
                return True if self._disconnect_detected or context.tasker.stopping else self._finish_optional_shop(context)
            if full:
                room = self._full_inventory_has_non_target(context)
                if room is None:
                    return True if self._disconnect_detected or context.tasker.stopping else self._finish_optional_shop(context)
                if not room:
                    logger.info("HIF轮间商店: 满栏均为目标饮料，保留现状并退出")
                    break
            drinks = self._recognize_drinks(context, image, bought_boxes)
            if points is None or any(item["price"] is None for item in drinks):
                unreadable += 1
                if unreadable <= 3:
                    logger.warning(f"HIF轮间余额或目标价格未识别，重读当前商品（{unreadable}/3），不当作买不起")
                    time.sleep(.6)
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
                status = self._purchase(context, selected["box"], selected["name"], is_drink=True,
                                        drink_id=selected["id"], expected_price=selected["price"])
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
                time.sleep(.9)
                continue
            if drinks:
                logger.info(f"HIF轮间目标价格已确认但积分不足: 余额={points} "
                            f"价格={[item['price'] for item in drinks]}，停止刷新和购买")
                break
            refresh = read_refresh(context, image)
            if not refresh or not refresh_is_affordable(points, refresh["cost"]):
                logger.info(f"HIF轮间无法继续刷新并保留购买预算: 积分={points} 刷新={refresh}")
                break
            if refreshes >= self.MAX_REFRESH_ATTEMPTS:
                logger.warning("HIF轮间刷新达到安全上限，停止")
                break
            stock_slots = INTERVAL_CARD_SLOTS + self.DRINK_SLOTS
            before = stock_signature(image, stock_slots)
            context.tasker.controller.post_click(*refresh["point"]).wait()
            verified = False
            confirmations = 0
            for _ in range(8):
                if context.tasker.stopping:
                    return True
                time.sleep(.45)
                image = context.tasker.controller.post_screencap().wait().get()
                if _detect_disconnect_screen(context, image):
                    return True
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
                return self._finish_optional_shop(context)
            refreshes += 1
            bought_boxes.clear()
            logger.success(f"HIF轮间刷新确认: 本店{refreshes}次，费用{refresh['cost']}，余额{after_points}")
        if not context.tasker.stopping:
            image = context.tasker.controller.post_screencap().wait().get()
            if _detect_disconnect_screen(context, image):
                return True
            if self._is_shop_scene(context, image):
                image = self._heal_before_drinks(context, image, spend_remaining=True)
                if image is None or context.tasker.stopping or self._disconnect_detected:
                    return True
                if self._is_shop_scene(context, image):
                    return self._finish_optional_shop(context)
        return True


@AgentServer.custom_action("ProduceHIFFailedAuto")
class ProduceHIFFailedAuto(CustomAction):
    @staticmethod
    def _retry(context):
        # Reuse only the two verified buttons. End this nested task after
        # confirmation so JumpBack resumes HIF, not the generic ProduceEntry.
        disconnects = ['[JumpBack]ProduceDisconnectError', '[JumpBack]ProduceDisconnectTitle', '[JumpBack]ProduceDisconnectHome']
        result = context.run_task('ProduceHIFRetryButton', {
            'ProduceHIFRetryButton': {
                'recognition': {'type': 'TemplateMatch', 'param': {'roi': [16,1040,688,220], 'template': ['produce/challenge.png']}},
                'action': {'type': 'Click'},
                'next': [*disconnects, 'ProduceHIFRetryConfirm', 'ProduceHIFRetryBattleReady'], 'timeout': 10000},
            'ProduceHIFRetryConfirm': {
                'recognition': {'type': 'TemplateMatch', 'param': {'roi': [16,1040,688,220], 'template': ['produce/decide.png']}},
                'action': {'type': 'Click'}, 'next': [], 'timeout': 10000},
            'ProduceHIFRetryBattleReady': {
                'recognition': {'type': 'Custom', 'param': {'custom_recognition': 'ProduceCardsFlagAuto'}},
                'action': {'type': 'DoNothing'}, 'next': [], 'timeout': 10000,
            },
        })
        if context.tasker.stopping:
            return True
        if result and result.status.succeeded:
            logger.success('HIF失败重试已开启：再次挑战，继续HIF出牌流程')
            return True
        logger.warning('HIF再次挑战未完成确认，保留当前页面，不误选结束培育')
        return False

    def run(self, context, argv):
        # Only the explicit retry option controls this. ProduceHIFStrategy is
        # a disabled configuration holder, not a retry/scenario switch.
        node = context.get_node_data('ProduceHIFFailedFlag') or {}
        retry = node.get('attach', {}).get('retry_enabled', False) is True
        for attempt in range(5):
            if context.tasker.stopping:
                return True
            image = context.tasker.controller.post_screencap().wait().get()
            if _detect_disconnect_screen(context, image):
                return True
            flag = context.run_recognition("ProduceFailedFlag", image)
            if retry and flag and flag.hit:
                again = context.run_recognition('ProduceFailedRetry', image)
                if again and again.hit and again.best_result:
                    return self._retry(context)
                if attempt < 4:
                    time.sleep(.4)
                    continue
            stop = context.run_recognition("ProduceFailedStop", image)
            if flag and flag.hit and stop and stop.hit and stop.best_result:
                box = stop.best_result.box
                context.tasker.controller.post_click(box[0] + box[2] // 2, box[1] + box[3] // 2).wait()
                reason = '再次挑战入口不可用' if retry else '失败重试已关闭'
                logger.success(f"HIF未获胜，{reason}，结束培育并继续检查回忆")
                return True
            time.sleep(.4)
        logger.warning("HIF结束按钮未确认，保留现场，不误点再挑战")
        return False


@AgentServer.custom_action("ProduceChooseWorkAuto")
class ProduceChooseWorkAuto(CustomAction):
    """
    处理选择工作类型的窗口

    选择逻辑：
    - 体力 > 10 时：选择会扣除体力对应的工作（health_position 指定的位置）
      - 单个位置 [2] 或 [3]：选择对应的工作
      - 无位置或多位置 [2,3]：随机选择
    - 体力 <= 10 时：选择不扣除体力对应的工作
      - [2] → 选择第三个（位置3不扣体力）
      - [3] → 选择第二个（位置2不扣体力）
      - 其他情况 → 选择第一个
    """

    def run(
        self,
        context: Context,
        argv: CustomAction.RunArg,
    ) -> bool:
        first_roi = [100, 760]
        second_roi = [100, 880]
        third_roi = [100, 1000]
        image = context.tasker.controller.post_screencap().wait().get()
        health_data = self._get_health(context, image)
        current = health_data["current"] if health_data else 0

        health_position = self._get_health_position(context, image)

        if current > 10:
            if health_position and len(health_position) == 1:
                box = second_roi if health_position[0] == 2 else third_roi
            else:
                box = random.choice([first_roi, second_roi, third_roi])
        else:
            if health_position == [2]:
                box = third_roi
            elif health_position == [3]:
                box = second_roi
            else:
                box = first_roi
        context.tasker.controller.post_click(box[0], box[1]).wait()
        return True

    @staticmethod
    def _get_health(context: Context, image) -> Optional[dict]:
        """获取当前体力数值"""
        reco_detail = context.run_recognition("ProduceRecognitionHealth", image)
        if not (reco_detail and reco_detail.hit):
            return None

        try:
            health_parts = reco_detail.best_result.text.split("/")
            current_health = int("".join(filter(str.isdigit, health_parts[0])))
            max_health = int("".join(filter(str.isdigit, health_parts[1])))
            ratio = current_health / max_health
            logger.info(f"体力: {current_health}/{max_health} ({ratio:.2%})")
            return {"current": current_health, "max": max_health, "ratio": ratio}
        except (ValueError, IndexError, ZeroDivisionError):
            logger.warning("体力数据解析失败")
            return None

    @staticmethod
    def _get_health_position(context: Context, image) -> Optional[list]:
        """
        获取扣除体力的图标位置

        Returns:
            None: 未检测到扣体力图标
            [2]: 第二个位置扣体力
            [3]: 第三个位置扣体力
            [2,3]: 第二和第三个位置都扣体力
        """
        reco_detail = context.run_recognition(
            "ProduceRecognitionHealthFlag",
            image,
            pipeline_override={"ProduceRecognitionHealthFlag": {"roi": [370, 830, 320, 290]}},
        )
        if reco_detail.hit:
            position = []
            for result in reco_detail.filtered_results:
                health_box_y = result.box[1]
                if health_box_y > 970:
                    position.append(3)
                else:
                    position.append(2)
            return position
        return None


@AgentServer.custom_action("ProduceChooseOptionsAuto")
class ProduceChooseOptionsAuto(CustomAction):
    """
    处理选择选项的窗口

    根据当前得分自动选择属性选项：
    1. 获取第一、第二、第三属性的当前分数
    2. THIRD_ATTR_PROBABILITY 概率选择第三属性，剩余概率选择第二属性
    3. 在可用选项中执行双击选择
    4. 若目标选项不可用，按优先级 fallback：null > 第二属性 > 第三属性 > 随机
    """

    OPTIONS_CONFIG = {
        "Vo": "produce/choose_Vo.png",
        "Da": "produce/choose_Da.png",
        "Vi": "produce/choose_Vi.png",
        "null": "produce/choose_null.png",
    }
    CLICK_DELAY = 0.5
    THIRD_ATTR_PROBABILITY = 0.6  # 选择第三属性的概率，剩余概率选择第二属性
    THIRD_ATTR_FORCE_SECOND_THRESHOLD = 681

    def __init__(self):
        super().__init__()
        self.first = "Vi"
        self.second = "Da"

    def run(
        self,
        context: Context,
        argv: CustomAction.RunArg,
    ) -> bool:
        """
        执行自动选择选项

        Returns:
            bool: 选择成功返回 True，否则返回 False
        """
        preference = (
            context.get_node_data("ProduceChooseNIAEventFlag")
            .get("action", {})
            .get("param", {})
            .get("custom_action_param", {"first": "Vo", "second": "Vi"})
        )
        self.first = preference["first"]
        self.second = preference["second"]
        image = context.tasker.controller.post_screencap().wait().get()
        score = self._get_current_score(context, image) or {"Vo": 0, "Da": 0, "Vi": 0, "max": 1}
        params = action_params(argv.custom_action_param)
        fallback = params.get("consecutive_failure_fallback") is True
        if fallback:
            from ..reco.options_fallback import read_fallback_options

            # Recheck the current frame; never click cached coordinates after a scene change.
            rows = read_fallback_options(context, image)
            if not rows:
                logger.info("保底选项画面已变化，本次不点击，重新识别")
                return True
            options = [{name: box} for name, box, _ in rows]
        else:
            from ..reco.options_fallback import FAILURE_GATE

            FAILURE_GATE.reset()
            options = self._get_available_options(context, image)

        # 计算选择
        first_score = score.get(self.first, 0)
        second_score = score.get(self.second, 0)
        # 计算第三属性（Vo、Da、Vi 中非 first/second 的那个）
        all_attrs = ["Vo", "Da", "Vi"]
        third = next(attr for attr in all_attrs if attr != self.first and attr != self.second)
        third_score = score.get(third, 0)
        logger.info(f"第一属性 {self.first}={first_score}, 第二属性 {self.second}={second_score}, 第三属性 {third}={third_score}")

        # 第三属性达到控制线后，营业选项固定补第二属性，防止第三属性
        # 继续拉开差距；尚未超过控制线时保留原有随机分配。
        if third_score > self.THIRD_ATTR_FORCE_SECOND_THRESHOLD:
            choice = self.second
            logger.info(
                f"第三属性 {third}={third_score} > "
                f"{self.THIRD_ATTR_FORCE_SECOND_THRESHOLD}，营业固定选择第二属性: {choice}"
            )
        elif random.random() < self.THIRD_ATTR_PROBABILITY:
            choice = third
            logger.debug(f"随机数 < {self.THIRD_ATTR_PROBABILITY}，选择第三属性: {choice}")
        else:
            choice = self.second
            logger.debug(f"随机数 >= {self.THIRD_ATTR_PROBABILITY}，选择第二属性: {choice}")

        # 找到目标选项
        target_box = None
        for opt in options:
            if choice in opt:
                target_box = opt[choice]
                break

        # 如果目标选项不可用，尝试 fallback（null 优先）
        if target_box is None:
            logger.debug(f"选项 {choice} 不可用，尝试 fallback")
            # 按 null > 第二属性 > 第三属性 > 随机的优先级选择
            priority = ["null", self.second, third, None]
            for p in priority:
                if p is None:
                    # 最后从所有可用选项中随机选择
                    if options:
                        random_opt = random.choice(options)
                        choice = next(iter(random_opt.keys()))
                        target_box = random_opt[choice]
                        logger.debug(f"Fallback 随机选择: {choice}")
                    break
                for opt in options:
                    if p in opt:
                        target_box = opt[p]
                        choice = p
                        logger.debug(f"Fallback 选择 {p}")
                        break
                if target_box:
                    break

        # 执行双击
        if target_box:
            # box 格式为 [x, y, w, h]，计算中心点
            center_x = target_box[0] + target_box[2] // 2
            center_y = target_box[1] + target_box[3] // 2
            self._double_click(context, center_x, center_y)
            logger.info(f"已选择选项: {choice}, 坐标: ({center_x}, {center_y})")
        else:
            logger.warning("没有可用选项，无法选择")
            return True  # 没有选项可选时默认返回True，避免卡死在这里
        return True

    @staticmethod
    def _get_current_score(context: Context, image) -> Optional[dict]:
        """获取当前得分"""
        score = {
            "Vo": 0,
            "Da": 0,
            "Vi": 0,
            "max": 0,
        }
        roi0_list = [[70 + i * 230, 430, 136, 80] for i in range(3)]
        roi1_list = [[150 + i * 150, 325, 136, 80] for i in range(3)]
        first_reco = context.run_recognition(
            "ProduceRecognitionScore",
            image,
            pipeline_override={"ProduceRecognitionScore": {"roi": roi0_list[0]}},
        )
        use_roi_list = roi1_list if not (first_reco and first_reco.hit) else roi0_list

        for i in range(3):
            reco_detail = context.run_recognition(
                "ProduceRecognitionScore",
                image,
                pipeline_override={"ProduceRecognitionScore": {"roi": use_roi_list[i]}},
            )
            if reco_detail and reco_detail.hit and len(reco_detail.filtered_results) == 2:
                current_score = int("".join(filter(lambda c: c.isdigit(), reco_detail.filtered_results[0].text)))
                max_score = int("".join(filter(lambda c: c.isdigit(), reco_detail.filtered_results[1].text.replace("/", ""))))
                logger.debug(f"第{i + 1}列得分: {current_score} / {max_score}")
                score[["Vo", "Da", "Vi"][i]] = current_score
                score["max"] = max_score if score["max"] < max_score < 9999 else score["max"]
        try:
            logger.info(f"当前得分: Vo={score['Vo']}, Da={score['Da']}, Vi={score['Vi']}, Max={score['max']}")
            return score
        except ValueError:
            logger.warning("积分数据解析失败")
            return None

    def _get_available_options(self, context: Context, image) -> List[Dict[str, Any]]:
        """获取可用选项列表"""
        available_options = []
        available_options_name = ""

        for option_name, option_img in self.OPTIONS_CONFIG.items():
            reco_detail = context.run_recognition(
                "ProduceRecognitionWorkOptions",
                image,
                pipeline_override={"ProduceRecognitionWorkOptions": {"template": option_img, "focus": None}},
            )
            if reco_detail and reco_detail.hit:
                available_options.append({option_name: reco_detail.best_result.box})
                available_options_name += f"{option_name}, "

        logger.info(f"可用选项: {available_options_name.rstrip(', ')}")
        return available_options

    def _double_click(self, context: Context, x: int, y: int):
        """执行双击操作"""
        context.tasker.controller.post_click(x, y).wait()
        time.sleep(self.CLICK_DELAY)
        context.tasker.controller.post_click(x, y).wait()


@AgentServer.custom_action("ProduceChooseMirrorAuto")
class ProduceChooseMirrorAuto(CustomAction):
    """
    自动选择试镜挑战难度

    根据当前投票数在对应试镜的阈值列表中选择满足条件的最高难度：
    - 获取当前投票数和试镜各门槛分数及坐标
    - 选取满足 vote >= threshold 的最高门槛
    - 根据降档配置先跳过指定档数，再检测锁定，锁定则继续降档
    - 降档至 threshold=0 或找到未锁定选项为止
    """

    CLICK_DELAY = 0.5

    def run(
        self,
        context: Context,
        argv: CustomAction.RunArg,
    ) -> bool:
        image = context.tasker.controller.post_screencap().wait().get()
        vote = self._get_current_vote(context, image) or 1
        mirror = self._get_current_mirror(context, image)
        lowering_difficulty = self._get_lowering_difficulty(context, argv)

        # 门槛分数降序排列
        thresholds = sorted((int(k) for k in mirror.keys()), reverse=True)

        # 找到满足当前投票的最高门槛索引
        start_idx = 0
        for i, threshold in enumerate(thresholds):
            if vote >= threshold:
                start_idx = i
                break

        # 先降指定档数，再检查锁定，锁定则继续降档直至成功或为0档
        target_threshold = 0
        x = y = 0
        start = min(start_idx + lowering_difficulty, len(thresholds) - 1)
        for i in range(start, len(thresholds)):
            target_threshold = thresholds[i]
            target_box = mirror[str(target_threshold)]
            x = target_box[0] + target_box[2] // 2
            y = target_box[1] + target_box[3] // 2

            if target_threshold == 0:
                break
            if not self._check_lock(context, image, target_box):
                break

            logger.info(f"分数 {target_threshold:,} 已锁定，继续降档")

        # 在点击前判断当前是第几镜，若启用 focus 则点击后进入手动接管
        mirror_idx = self._get_focus_mirror_index(context, image)

        logger.info(f"当前投票: {vote:,}, 目标分数: {target_threshold:,}, 点击坐标: ({x}, {y - 20})")
        self._double_click(context, x, y - 20)

        if not mirror_idx:
            return True

        logger.info(f"检测到 focus 配置，进入手动接管模式（第{mirror_idx}镜）")
        context.run_action(
            "ProduceMirrorFocus",
            pipeline_override={
                "ProduceMirrorFocus": {
                    "focus": {
                        "Node.ActionNode.Succeeded": {
                            "content": f"[color:LimeGreen]已暂停自动培育进入手动接管状态（第{mirror_idx}镜）[/color]\n"
                            '退出手动接管请点击下方"确定"按钮',
                            "display": ["log", "modal", "notification"],
                        }
                    }
                }
            },
        )

        return True

    @staticmethod
    def _get_focus_mirror_index(context: Context, image) -> int:
        """获取当前试镜对应的 focus 镜号。"""
        node_data = context.get_node_data("ProduceMirrorFlag")
        if not node_data:
            return 0
        attach = node_data.get("attach", {})
        logger.debug(f"focus 配置: {attach}")
        # 仅当有 focus_x 为 true 时才识别
        has_focus = any(attach.get(f"focus_{i}", False) for i in range(1, 4))
        if not has_focus:
            return 0

        for i in range(1, 4):
            reco_detail = context.run_recognition(
                f"ProduceMirrorFlag_{i}",
                image,
                pipeline_override={
                    f"ProduceMirrorFlag_{i}": {
                        "recognition": "TemplateMatch",
                        "roi": [12, 630, 696, 530],
                        "template": f"produce/NIA/mirror_{i}.png",
                        "threshold": 0.9,
                    }
                },
            )
            if reco_detail and reco_detail.hit:
                return i if attach.get(f"focus_{i}", False) else 0
        return 0

    @staticmethod
    def _check_lock(context: Context, image, target_box) -> bool:
        """检查目标分数附近是否有锁定图标"""
        roi = [
            target_box[0] + 330,
            target_box[1] - 80,
            100,
            100,
        ]
        reco_detail = context.run_recognition(
            "ProduceRecognitionLock",
            image,
            pipeline_override={
                "ProduceRecognitionLock": {
                    "recognition": "TemplateMatch",
                    "roi": roi,
                    "template": "produce/lock.png",
                    "green_mask": True,
                }
            },
        )
        return reco_detail is not None and reco_detail.hit

    @staticmethod
    def _get_lowering_difficulty(context: Context, argv) -> int:
        """检查是否启动降档"""
        params = json.loads(argv.custom_action_param)
        lowering_level = params.get("level") if isinstance(params, dict) else None
        if not lowering_level:
            return 0
        else:
            logger.info(f"检测到启动降档，降低{lowering_level}档")
            return int(lowering_level)

    @staticmethod
    def _get_current_mirror(context: Context, image):
        """获取当前试镜"""
        mirror = {"0": [360, 1050, 1, 1]}
        reco_detail = context.run_recognition("ProduceRecognitionMirror", image)
        if reco_detail and reco_detail.hit:
            for result in reco_detail.filtered_results:
                box = result.box
                text = "".join(filter(lambda c: c.isdigit(), result.text.replace(",", "")))
                mirror[text] = [box[0], box[1], box[2], box[3]]
        mirror = dict(sorted(mirror.items(), key=lambda x: int(x[0])))
        logger.info(f"当前试镜门槛分数: {list(mirror.keys())}")
        return mirror

    @staticmethod
    def _get_current_vote(context: Context, image) -> Optional[int]:
        """获取当前投票"""
        reco_detail = context.run_recognition("ProduceRecognitionVote", image)
        if reco_detail and reco_detail.hit:
            try:
                vote = int(reco_detail.best_result.text.replace(",", ""))
                return vote
            except ValueError:
                logger.warning("投票数据解析失败")
                return None

    def _double_click(self, context: Context, x: int, y: int):
        """执行双击操作"""
        context.tasker.controller.post_click(x, y).wait()
        time.sleep(self.CLICK_DELAY)
        context.tasker.controller.post_click(x, y).wait()


class ProduceHIFAttributeBase(CustomAction):
    """Shared fixed HIF main-battle attribute selection for Shinozawa Hiro."""

    ATTR_LABELS = {
        "Vo": ("ボーカル", "Vo"),
        "Da": ("ダンス", "Da"),
        "Vi": ("ビジュアル", "Vi"),
    }
    @staticmethod
    def _strategy(context: Context) -> dict:
        return action_params((context.get_node_data("ProduceHIFStrategy") or {}).get("attach"))

    @staticmethod
    def _attribute_order(context: Context) -> dict:
        node = context.get_node_data("ProduceChooseNIAEventFlag") or {}
        preference = action_params(
            node.get("action", {})
            .get("param", {})
            .get("custom_action_param", {})
        )
        first = preference.get("first", "Vo")
        second = preference.get("second", "Da")
        if first not in ("Vo", "Da", "Vi"):
            first = "Vo"
        if second not in ("Vo", "Da", "Vi") or second == first:
            second = next(attr for attr in ("Vo", "Da", "Vi") if attr != first)
        third = next(attr for attr in ("Vo", "Da", "Vi") if attr not in (first, second))
        return {"first": first, "second": second, "third": third}

    @classmethod
    def _selected_attribute(cls, context: Context, setting: str) -> str:
        ordinal = cls._strategy(context).get(setting, "first")
        return cls._attribute_order(context).get(ordinal, "Vo")

    @staticmethod
    def _ocr(context: Context, image, expected: list[str], roi: list) -> list:
        detail = context.run_recognition(
            "ProduceRecognitionPoint",
            image,
            pipeline_override={
                "ProduceRecognitionPoint": {
                    "recognition": "OCR",
                    "roi": roi,
                    "expected": expected,
                    "order_by": "Horizontal",
                }
            },
        )
        if not (detail and detail.hit):
            return []
        return list(detail.filtered_results or detail.all_results or [])

    @staticmethod
    def _click_box(context: Context, box: list, double: bool = True) -> None:
        point = (box[0] + box[2] // 2, box[1] + box[3] // 2)
        context.tasker.controller.post_click(*point).wait()
        if double:
            time.sleep(0.3)
            context.tasker.controller.post_click(*point).wait()

    def _run_lesson(self, context: Context, event_text: str, label: str) -> bool:
        from ..hif import read_hif_choices, read_hif_events, read_hif_lesson_header, lesson_observation, lesson_observations_stable

        attribute = None
        try:
            option = min(3, max(1, int(self._strategy(context).get("lesson_option", 1))))
        except (TypeError, ValueError):
            option = 1
        previous = None
        previous_image = None
        clicked_stage = None
        clicked_image = None
        missing = 0
        clicks = 0
        deadline = time.monotonic() + 14
        if context.tasker.stopping:
            return True
        while time.monotonic() < deadline and not context.tasker.stopping:
            image = context.tasker.controller.post_screencap().wait().get()
            if _detect_disconnect_screen(context, image):
                return True  # Let the outer entry handle network/date recovery.
            choices = read_hif_choices(context, image)
            if choices:
                stage, rows = choices
                missing = 0
                # Border animations move detected edges by 1-2px even when
                # the button is ready. Attribute identity must still agree.
                signature = lesson_observation(stage, rows)
                if not lesson_observations_stable(previous, signature, previous_image, image):
                    previous = signature
                    previous_image = image.copy()
                    time.sleep(0.18)
                    continue
                if stage == "attribute":
                    attribute = attribute or self._lesson_attribute(context, image, rows, event_text)
                    if attribute is None:
                        return True
                    row = next((row for row in rows if row[0] == attribute), None)
                    if row is None:
                        logger.warning(f"HIF{label}: 未找到设置的属性 {attribute}，保留画面重识别")
                        return True
                else:
                    row = rows[option - 1]
                if clicked_stage == stage and clicks >= 3:
                    break
                box = list(row[1])
                self._click_box(context, box, double=False)
                clicks = clicks + 1 if clicked_stage == stage else 1
                clicked_stage, clicked_image = stage, image.copy()
                previous = None
                logger.info(f"HIF{label}: 点击{'属性 ' + attribute if stage == 'attribute' else '第 ' + str(option) + ' 选项'} "
                            f"box={box}，等待页面推进")
            elif events := read_hif_events(context, image, event_text):
                missing = 0
                signature = lesson_observation("event", events)
                if not lesson_observations_stable(previous, signature, previous_image, image):
                    previous = signature
                    previous_image = image.copy()
                    time.sleep(.18)
                    continue
                # Read course/SP state from the stable frame, not a transition frame.
                attribute = attribute or self._lesson_attribute(context, image, events, event_text)
                if attribute is None:
                    return True
                row = next((row for row in events if row[0] == attribute), None)
                if row is None:
                    logger.warning(f"HIF{label}: 当前事件没有设置的属性 {attribute}，交回主流程")
                    return True
                if clicked_stage == "event" and clicks >= 3:
                    break
                self._click_box(context, list(row[1]), double=False)
                clicks = clicks + 1 if clicked_stage == "event" else 1
                clicked_stage, clicked_image = "event", image.copy()
                previous = None
                logger.info(f"HIF{label}: 点击日程入口 {attribute} box={row[1]}，等待属性/选项页面")
            elif read_hif_lesson_header(context, image):
                # The preview retains its header even if a scrim/long label
                # defeats both row readers. Missing rows are NOT completion.
                previous = None
                missing = 0
                time.sleep(.35)
                continue
            elif clicked_image is not None:
                previous = None  # Occlusion breaks the consecutive-frame check.
                # A click is not a success. Require two changed, row-free frames;
                # animations/occlusions of an unchanged screen do not count.
                before = clicked_image[640:960:4, 20:700:4].astype(np.float32)
                after = image[640:960:4, 20:700:4].astype(np.float32)
                changed = before.shape == after.shape and np.mean(np.abs(before - after)) > 8
                missing = missing + 1 if changed else 0
                if missing >= 2:
                    selected = ('第 ' + str(option) + ' 选项' if clicked_stage == 'option'
                                else attribute + (' 日程入口' if clicked_stage == 'event' else ' 属性'))
                    logger.info(f"HIF{label}: {selected}点击后已离开课程页，交回主流程识别后续页面")
                    return True
            else:
                logger.info(f"HIF{label}: 当前未识别到三选项或已验证的日程入口，交回主流程")
                return True
            # Allow the click/preview animation, then require a fresh stable
            # pair before retrying. Missing rows are sampled independently.
            time.sleep(0.35 if missing else min(0.85, 0.45 + max(0, clicks - 1) * 0.2))
        if context.tasker.stopping:
            return True
        logger.warning(f"HIF{label}: 14秒内未确认课程选择完成（点击{clicks}次），保留画面；不再将漏识别当作成功或空转等待")
        return False

    def _lesson_attribute(self, context, image, rows, event_text):
        return self._selected_attribute(context, "lesson_attribute")


@AgentServer.custom_action("ProduceHIFChangeAuto")
class ProduceHIFChangeAuto(ProduceHIFAttributeBase):
    """Select the change destination and hand the next observed step back."""

    def _resolve_target_identities(self, context, cards):
        """Open only uncertain destinations; never press Next while probing."""
        from ..deck_scan import scan_identity_known
        from ..hif import read_hif_change
        from ..removal import read_selected_card, selected_card_box_matches
        for item in cards:
            if not item.get('square_art_attempted') or scan_identity_known(item):
                continue
            if context.tasker.stopping:
                return None
            image = context.tasker.controller.post_screencap().wait().get()
            if _detect_disconnect_screen(context, image):
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
                if _detect_disconnect_screen(context, image) or not read_hif_change(context, image):
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
        from ..hif import read_hif_change
        from ..deck_scan import scan_identity_known

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
            if _detect_disconnect_screen(context, image):
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
                selected = self._destination(evaluated, _load_card_lists())
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
        from ..hif import is_hif_change_source

        reward = ProduceChooseRewardAuto()
        previous = None
        # Both modes need the same stable pair. Allow the opening animation
        # to finish even in strict priority mode; a stable page exits early.
        for _ in range(15):
            if context.tasker.stopping:
                return None
            image = context.tasker.controller.post_screencap().wait().get()
            if _detect_disconnect_screen(context, image):
                self._interrupted = True
                return None
            if change:
                if not is_hif_change_source(context, image):
                    return None
                detection = context.run_recognition("ProduceRecognitionCards", image)
                boxes = self._boxes(detection.all_results) if detection and detection.hit else []
                from ..deck_scan import complete_grid_rows
                boxes = complete_grid_rows(image, boxes, top=620)
                # Wait for geometry/art to settle before the expensive visual
                # identity pass. YOLO result order is not card identity.
                signature = reward._scene_signature(image, [{"box": box} for box in boxes])
                if boxes and self._source_frames_stable(previous, signature):
                    evaluated = reward._evaluate(context, image, verified_boxes=boxes, read_extra_action=False, diagnostics=False)
                    from ..square_identity import resolve_selected
                    evaluated = resolve_selected(context, image, evaluated, change=True)
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
            from ..deck_scan import same_deck_view
            return same_deck_view(first, second, first_boxes, second_boxes, top=top)
        if first.shape != second.shape:
            return False
        return np.abs(first[top:1090:4, 60:650:4].astype(np.float32)
                      - second[top:1090:4, 60:650:4].astype(np.float32)).mean() < 3

    @staticmethod
    def _scroll(context: Context, *, forward: bool) -> None:
        # Drag only inside the observed deck pane; never cross the footer.
        start, end = (1035, 720) if forward else (720, 1035)
        context.tasker.controller.post_swipe(650, start, 650, end, 450).wait()
        time.sleep(.6)

    def _rewind_source(self, context, *, change=True):
        """Resume from an observed top, never treat a paused bottom as page one."""
        from ..deck_scan import deck_scrollbar_at_top, deck_view_complete, scan_identity_known
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
            if candidate and (not strict or candidate['delete_rank'] == 3):
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
            self._scroll(context, forward=False)
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
        from ..hif import is_hif_change_source
        from ..removal import read_selected_card, selected_card_box_matches

        if context.tasker.stopping:
            return True
        if not selected.get("detail_selected"):
            image = context.tasker.controller.post_screencap().wait().get()
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
            if _detect_disconnect_screen(context, image):
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
        lists = _load_card_lists()
        black = set(lists["blacklist"]["cards"]) if lists["blacklist"]["enabled"] else set()
        white = set(lists["whitelist"]["cards"]) if lists["whitelist"]["enabled"] else set()
        replacement_section = lists.get("replacementList", {})
        replacement = set(replacement_section.get("cards", [])) if replacement_section.get("enabled") else set()
        self._removal_search_policy = (black, white, replacement, False)
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
        return self._defer_source(context, "候选核名或提交结果尚未确认")

    def _scan_source(self, context, black, white, replacement, *, change=True, initial_page=None, strict=True):
        from ..deck_scan import (DeckIdentityCache, scan_identity_known, deck_scrollbar_position,
                                 deck_view_complete, removal_priority_bound)
        logger.info(f"{'换出' if change else '删除'}搜索：{'严格优先级' if strict else '快速命中'}模式")
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
                self._scroll(context, forward=True)
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
            verified = [item for item in evaluated if scan_identity_known(item)]
            candidate = choose(verified, black, white, replacement)
            if candidate and (best is None or ProduceShoppingAuto._removal_key(candidate) > ProduceShoppingAuto._removal_key(best)):
                best, best_page = candidate, page
            if candidate and candidate["delete_rank"] == 3:
                logger.info(f"{'换出' if change else '删除'}搜索：已核实最高优先级 N 卡「{candidate['name']}」，停止翻页")
                return candidate
            if candidate and not strict:
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
            self._scroll(context, forward=True)
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
                            if any(not scan_identity_known(item) and
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
            matches = [item for item in evaluated if item["name_key"] == best["name_key"]
                       and item["rarity"] == best["rarity"] and scan_identity_known(item)]
            selected = choose(matches, black, white, replacement)
            if selected:
                return selected
            # Cache miss: at most probe the visually named target, not the
            # entire page. Its exact identity must still match the bookmark.
            possible = [item for item in evaluated if item.get("name_key") == best["name_key"]
                        and item.get("name_confidence", 0) >= .55]
            if possible:
                shop._deck_image = image
                checked = shop._probe_removal_candidates(context, possible[:1], black, white, replacement, change=change)
                if shop._disconnect_detected:
                    self._interrupted = True
                    return None
                exact = [item for item in checked if item.get("identity_verified")
                         and item.get("name_key") == best["name_key"] and item.get("rarity") == best["rarity"]]
                selected = choose(exact, black, white, replacement)
                if selected:
                    return selected
            if retry == 6:
                break
            self._scroll(context, forward=False)
            current = self._page(context) if change else self._page(context, change=False)
            if current is None:
                return None
            image, evaluated = current
        logger.warning("换出/删除牌滚动返回后未重新确认目标，不使用旧坐标")
        return None


@AgentServer.custom_action("ProduceHIFDeleteAuto")
class ProduceHIFDeleteAuto(ProduceHIFShoppingAuto):
    """Resume shop deletion without the legacy first-card click route."""
    def run(self, context, argv):
        from ..shop import ocr
        self._disconnect_detected = False
        if context.tasker.stopping:
            return True
        image = context.tasker.controller.post_screencap().wait().get()
        if _detect_disconnect_screen(context, image):
            return True
        # A modal left by an interrupted/old task must be cancelled before
        # scanning all priorities. Its existing selection is not authority.
        if ocr(context, image, [15, 450, 690, 400], [r"^スキルカード削除$"]):
            cancel = ocr(context, image, [65, 1080, 300, 160], [r"^[×✕xX]?\s*キャンセル$"])
            if len(cancel) != 1:
                logger.warning("咨询残留删除弹窗取消按钮未完整确认，保护卡牌并保留现场")
                return False
            self._click_product(context, list(cancel[0].box))
            time.sleep(.5)
            image = context.tasker.controller.post_screencap().wait().get()
            if ocr(context, image, [15, 450, 690, 400], [r"^スキルカード削除$"]):
                return False
        lists = _load_card_lists()
        sets = [set(lists.get(key, {}).get("cards", [])) if lists.get(key, {}).get("enabled") else set()
                for key in ("blacklist", "whitelist", "replacementList")]
        status = self._delete_from_deck(context, *sets)
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


@AgentServer.custom_action("ProduceHIFChooseClassAuto")
class ProduceHIFChooseClassAuto(ProduceHIFAttributeBase):
    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        from ..hif import read_hif_choice_kind
        if context.tasker.stopping:
            return True
        image = context.tasker.controller.post_screencap().wait().get()
        if read_hif_choice_kind(context, image) == "course":
            return ProduceHIFChooseCourseAuto().run(context, argv)
        return self._run_lesson(context, "授業", "上课")


@AgentServer.custom_action("ProduceHIFChooseCourseAuto")
class ProduceHIFChooseCourseAuto(ProduceHIFAttributeBase):
    def _lesson_attribute(self, context, image, rows, event_text):
        from ..hif import hif_course_sp_roi
        from ..event_templates import HIF_TEMPLATES
        recognizer = ProduceChooseNIAEventAuto()
        recognizer.SP_TEMPLATE = HIF_TEMPLATES['SP']
        recognizer.SP_TEMPLATE_PARAMS = {'threshold': 0.85}
        order = self._attribute_order(context)
        courses = []
        for attribute, box, *_ in rows:
            if attribute not in order.values():
                continue
            # Only the verified course tile; never search other event icons.
            sp = recognizer._get_sp_course(context, image, hif_course_sp_roi(image, rows, box))
            courses.append({"attribute": attribute, "SP": sp})
        # HIF SP rewards take precedence over every normal course. Attribute
        # scores and teacher suggestions do not override this six-level order.
        available = "/".join(f"{course['attribute']}={'SP' if course['SP'] else '普通'}" for course in courses)
        for need_sp in (True, False):
            for ordinal, attribute in order.items():
                if any(course["attribute"] == attribute and course["SP"] == need_sp for course in courses):
                    priority = {"first": "第一", "second": "第二", "third": "第三"}[ordinal]
                    logger.info(f"HIF课程: 可选={available} 第一={order['first']} 第二={order['second']} "
                                f"第三={order['third']} 选择={attribute} 原因={priority}属性{'SP' if need_sp else '普通'}")
                    return attribute
        logger.warning("HIF课程: 未识别到可选属性，保留页面重识别")
        return None

    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        return self._run_lesson(context, "レッスン", "课程")


@AgentServer.custom_action("ProduceHIFChooseSupplyAuto")
class ProduceHIFChooseSupplyAuto(ProduceHIFAttributeBase):
    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        if context.tasker.stopping:
            return True
        image = context.tasker.controller.post_screencap().wait().get()
        # Match the schedule choice, not P-item effect prose such as
        # "活動支給・差し入れ選択時、" that appears during the reward animation.
        results = self._ocr(context, image, ["^差し入れ$"], [0, 800, 720, 350])
        results = [result for result in results
                   if str(result.text).strip() == "差し入れ" and result.box[1] >= 800]
        if not results:
            # The entry recognition can precede a scene transition. Missing
            # the choice on this fresh frame is normal, not a failed supply.
            return True
        box = list(results[0].box)
        self._click_box(context, box, double=False)
        logger.info("HIF支给：已点击日程中的差し入れ，交回流程识别")
        return True


@AgentServer.custom_action("ProduceHIFEnterShopAuto")
class ProduceHIFEnterShopAuto(ProduceHIFAttributeBase):
    """Enter the daily shop tile; the shop's existing policy takes over later."""

    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        from ..hif import read_hif_shop_entry

        previous = before = None
        clicks = missing = 0
        deadline = time.monotonic() + 14
        while time.monotonic() < deadline and not context.tasker.stopping:
            image = context.tasker.controller.post_screencap().wait().get()
            if _detect_disconnect_screen(context, image):
                return True
            box = read_hif_shop_entry(context, image)
            if box is None:
                if before is None:
                    return True
                changed = before.shape == image.shape and np.abs(
                    image[920:1110:4, 40:680:4].astype(np.float32)
                    - before[920:1110:4, 40:680:4].astype(np.float32)
                ).mean() > 8
                missing = missing + 1 if changed else 0
                if missing >= 2:
                    logger.success("HIF咨询入口：已确认页面推进，交回商店策略")
                    return True
            else:
                missing = 0
                signature = tuple(box)
                if previous != signature:
                    previous = signature
                    time.sleep(.45)
                    continue
                if clicks >= 3:
                    break
                if context.tasker.stopping:
                    return True
                if clicks == 0:
                    ProduceShoppingAuto._shop_delete_visit(context, reset=True)
                self._click_box(context, box, double=False)
                clicks += 1
                before, previous = image.copy(), None
                logger.info(f"HIF咨询入口：点击日程图块 box={box}，等待进入商店")
            time.sleep(.65)
        if context.tasker.stopping:
            return True
        logger.warning("HIF咨询入口：重试后仍未确认页面推进，保留现场")
        return False


@AgentServer.custom_action("ProduceHIFShopAuto")
class ProduceHIFShopAuto(CustomAction):
    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        return ProduceHIFShoppingAuto().run(context, argv)


@AgentServer.custom_action("ProduceKeepDrinkAuto")
class ProduceKeepDrinkAuto(CustomAction):
    """Keep four bottles by source-aware target priority in the overflow list."""

    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        from ..drink_overflow import handle_overflow
        return handle_overflow(context)

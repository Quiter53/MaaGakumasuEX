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

from ..card_visual import (
    classify_rarity,
    intersection_over_union,
    match_card_catalog,
    match_card_visual,
    normalize_card_name,
    rarity_priority,
)


CARD_LISTS_PATH = os.path.join("config", "card_lists.json")
_DIAGNOSTIC_STATE = {"mtime": None, "enabled": False}
_PRODUCE_CARD_RUN_STATE = {
    "reward_remove_exhausted": False,
    "reward_refresh_exhausted": False,
    "reward_remove_successes": 0,
    "reward_refresh_successes": 0,
    "reward_remove_remaining": None,
    "reward_refresh_remaining": None,
}


def _reset_produce_card_run_state() -> None:
    """Reset consumable reward actions exactly once for a new Produce run."""
    _PRODUCE_CARD_RUN_STATE.update(
        reward_remove_exhausted=False,
        reward_refresh_exhausted=False,
        reward_remove_successes=0,
        reward_refresh_successes=0,
        reward_remove_remaining=None,
        reward_refresh_remaining=None,
    )


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
    }
    try:
        with open(CARD_LISTS_PATH, "r", encoding="utf-8") as stream:
            value = json.load(stream)
        result = {}
        for list_name in ("blacklist", "whitelist"):
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
        _reset_produce_card_run_state()
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
        return True


@AgentServer.custom_action("ProduceRecoverConnectionAuto")
class ProduceRecoverConnectionAuto(CustomAction):
    """Handle network retry and date-change dialogs without guessing state."""

    NETWORK_RETRY_POINT = (515, 1160)
    DATE_CHANGE_POINT = (360, 1170)

    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        image = context.tasker.controller.post_screencap().wait().get()

        date_change = context.run_recognition("ProduceDateChangeFlag", image)
        if date_change and date_change.hit:
            logger.warning("换日恢复：检测到日期变更，点击返回标题按钮")
            context.tasker.controller.post_click(*self.DATE_CHANGE_POINT).wait()
            return True

        network_error = context.run_recognition("ProduceDisconnectNetworkFlag", image)
        if network_error and network_error.hit:
            logger.warning("网络重试：检测到通信错误，点击右侧重试")
            context.tasker.controller.post_click(*self.NETWORK_RETRY_POINT).wait()
            return True

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

    def _choose_best_event(self, suggestion: str, health_data: dict, points: int, score: dict, events: list) -> Optional[dict]:
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
            logger.info("体力过低，选择休息")
            return {"name": "rest", "box": [0, 0, 0, 0], "run_task": "ProduceChooseRest"}

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
        available_events = []
        available_events_name = ""

        for event_name, event_img in self.EVENT_CONFIG.items():
            reco_detail = context.run_recognition(
                "ProduceRecognitionEvent",
                image,
                pipeline_override={
                    "ProduceRecognitionEvent": {"recognition": "TemplateMatch", "template": event_img, "roi": [0, 880, 720, 220]}
                },
            )
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
        reco_detail = context.run_recognition(
            "ProduceChooseEventSp",
            image,
            pipeline_override={"ProduceChooseEventSp": {"recognition": "TemplateMatch", "template": "produce/sp.png", "roi": sp_roi}},
        )
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
        "工作": "ProduceWorkEntry",
        "指导": "ProduceGuideEntry",
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
    按“SSR再动 > SR再动 > SSR > SR > R”选择手牌。

    稀有度或再动无法确定时会写出完整日志，并回退到系统推荐牌/检测
    分数最高的牌，不会因为返回“未知”而卡住。
    """

    # 阈值常量
    CLICK_DELAY = 0.3
    TIME_OUT = 15.0
    POST_PLAY_RECOGNITION_DELAY = 1.5

    def __init__(self):
        super().__init__()
        self.start_time = time.time()
        self._disconnect_detected = False

    def run(
        self,
        context: Context,
        argv: CustomAction.RunArg,
    ) -> bool:
        self._disconnect_detected = False
        # 使用饮料
        self._wait_until_playable(context)
        if self._disconnect_detected:
            return True
        image = context.tasker.controller.post_screencap().wait().get()
        disconnect_node = _detect_disconnect_screen(context, image)
        if disconnect_node:
            logger.warning(f"出牌检测到断线界面 {disconnect_node}，交回全局恢复流程")
            return True
        reco_detail = context.run_recognition("ProduceCheckDrinkButton", image)
        hit_count = len(reco_detail.filtered_results)  # 获取饮料数
        logger.info(f"识别到{hit_count}瓶饮料")
        for _ in range(hit_count):
            context.run_task("ProduceUseDrink")
            self._wait_until_playable(context, 2)
            if self._disconnect_detected:
                return True

        # 开始出牌
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

            # 识别手牌
            reco_detail = context.run_recognition("ProduceRecognitionCards", image)
            if reco_detail and reco_detail.hit:
                results = reco_detail.all_results
                raw_candidates = self._get_playable_candidates(results, image.shape)
                candidates = self._get_full_hand_candidates(raw_candidates)
                if candidates:
                    unusable_confirmations = 0
                    no_cards_confirmations = 0
                    selected = self._choose_card(context, image, candidates)
                    self._play_a_card(context, selected["box"])
                elif any(result.label == "useless" for result in results):
                    # A card sliding at either screen edge is sometimes emitted
                    # as a narrow fragment.  The old geometry filter discarded
                    # it and trusted a simultaneous `useless` detection, which
                    # could skip a round despite a playable card being present.
                    relaxed = [
                        candidate
                        for candidate in raw_candidates
                        if candidate["box"][1] >= 780
                        and 20 <= candidate["box"][2] <= 220
                        and 180 <= candidate["box"][3] <= 300
                        and candidate["detector_score"] >= 0.65
                    ]
                    if relaxed:
                        unusable_confirmations = 0
                        no_cards_confirmations = 0
                        logger.warning(
                            "无可用牌判定被残缺可用卡框否决；优先尝试检测分数最高的残缺卡"
                        )
                        selected = self._choose_card(context, image, relaxed)
                        self._play_a_card(context, selected["box"])
                        continue

                    unusable_confirmations += 1
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
                    context.run_task("ProduceRecognitionSkipRound")
                    self._wait_until_playable(context)
                    self.start_time = time.time()
                    unusable_confirmations = 0

            else:
                reco_detail = context.run_recognition("ProduceRecognitionNoCards", image)
                if reco_detail.hit:
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
                    context.run_task("ProduceRecognitionSkipRound")
                    self._wait_until_playable(context)
                    self.start_time = time.time()
                    no_cards_confirmations = 0
                else:
                    # An animation frame can make both recognizers miss.  It is
                    # not evidence for skipping and must reset the confirmation.
                    unusable_confirmations = 0
                    no_cards_confirmations = 0

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
    def _get_full_hand_candidates(candidates: list) -> list:
        """Keep complete portrait cards in the bottom hand area."""
        return [
            candidate
            for candidate in candidates
            if candidate["box"][1] >= 780
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
            # An exact catalog title may correct a weak visual result.  A fuzzy
            # title is allowed to fill unknown only, never overturn a class.
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

    def _choose_card(self, context: Context, image, candidates: list) -> dict:
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

        best_priority = max(item["priority"] for item in evaluated)
        fallback = best_priority <= 0
        if fallback:
            logger.warning(
                "所有卡牌稀有度均为未知；安全回退到系统推荐牌，其次为检测分数最高牌"
            )
        selected = max(
            evaluated,
            key=lambda item: (
                item["priority"],
                1 if item["label"] == "suggestions" else 0,
                item["detector_score"],
            ),
        )
        if (
            selected["rarity"] == "unknown"
            or selected["rarity_confidence"] < 0.15
            or 0.46 < selected["extra_score"] < 0.52
        ):
            _save_card_diagnostic(image, "battle", evaluated)
        logger.success(
            f"出牌选择: rarity={selected['rarity']} "
            f"extra_action={selected['extra_action']} priority={selected['priority']} "
            f"box={selected['box']} fallback={fallback}"
        )
        return selected

    def _play_a_card(self, context: Context, box: list) -> bool:
        """
        出牌并处理移动卡牌界面

        Args:
            context: maa的Context类
            box: 点击范围，格式为[x, y, w, h]（x、y为点击范围左上角的坐标）

        Returns:
            bool: 如果执行没有问题，返回True；否则返回False。
        """

        context.tasker.controller.post_click(
            box[0] + box[2] // 2,
            box[1] + box[3] // 2,
        ).wait()
        time.sleep(self.CLICK_DELAY)
        context.tasker.controller.post_click(
            box[0] + box[2] // 2,
            box[1] + box[3] // 2,
        ).wait()
        logger.info("出牌 耗时:{:.2f}秒".format(time.time() - self.start_time))

        # The next hand is visible before it accepts clicks.  Use one bounded
        # fixed cooldown between strategy passes instead of repeated stability
        # recognition and click-acceptance polling.
        time.sleep(1.0)
        playable = self._wait_until_playable(context)
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

    def _wait_until_playable(self, context: Context, confirmation_count=1):
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
        while True:
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
                count_playable += 1
                if count_playable >= confirmation_count:
                    return True

            # 检测血条是否存在，如连续n次检查不到血条，则认为已退出出牌场景
            # 如果识别时间太长，2次就够了，主要避免CLEAR效果遮住血条的情况
            # 如果识别时间太短，导致提前出函数，CLEAR转PERFECT的那一回合出牌计时会差很远。如果出现这种情况，就设置为3次
            reco_detail = context.run_recognition("ProduceRecognitionHealthFlag", image)
            if not (reco_detail and reco_detail.hit):
                count_exit += 1
                if count_exit >= 2:
                    return False

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
        if self._is_drink_reward(context, image):
            if self._handle_drink_reward(context, image):
                return True
            # A card-title guard inside the drink handler can reject a late
            # feature-match false positive.  Continue with a fresh frame so
            # the card policy can recover in the same task pass.
            image = context.tasker.controller.post_screencap().wait().get()

        lists = _load_card_lists()
        blacklist = set(lists["blacklist"]["cards"]) if lists["blacklist"]["enabled"] else set()
        whitelist = set(lists["whitelist"]["cards"]) if lists["whitelist"]["enabled"] else set()
        logger.success(
            f"奖励牌策略: 黑名单={len(blacklist)} 白名单={len(whitelist)}；"
            "先除去黑名单，再以白名单/再动/SSR决定是否停止刷新"
        )

        image, evaluated = self._wait_for_stable_cards(context)
        if not evaluated:
            logger.warning("奖励牌未稳定显示，交回原推荐/第一项兜底")
            return self._fallback_select(context, image)
        self._sync_reward_action_counts(context, image, log_summary=True)

        # Removal and refresh counts are shared by the whole cultivation.  Once
        # the game proves an action is exhausted, later reward pages must not
        # spend several seconds trying the same disabled action again.
        if _PRODUCE_CARD_RUN_STATE["reward_remove_exhausted"]:
            logger.info("本局卡牌除去次数已耗尽，直接避开黑名单选择")
        else:
            for _ in range(self.MAX_REMOVE_ATTEMPTS):
                if _PRODUCE_CARD_RUN_STATE["reward_remove_exhausted"]:
                    break
                blocked = [item for item in evaluated if item["name_key"] in blacklist]
                if not blocked:
                    break
                selected = max(blocked, key=self._selection_key)
                old_signature = self._scene_signature(image, evaluated)
                sequence = _PRODUCE_CARD_RUN_STATE["reward_remove_successes"] + 1
                logger.info(
                    f"奖励黑名单命中: {selected['name']}，执行卡牌除去 "
                    f"(本局第 {sequence} 次)"
                )
                self._click_card(context, selected)
                time.sleep(0.25)
                context.tasker.controller.post_click(*self.REMOVE_BUTTON).wait()
                if not self._confirm_remove(context):
                    _PRODUCE_CARD_RUN_STATE["reward_remove_exhausted"] = True
                    logger.info("已确认本局卡牌除去不可用；后续奖励页不再尝试")
                    break
                self._record_reward_action_use("remove")
                logger.success(
                    "卡牌除去已确认，本局累计 "
                    f"{_PRODUCE_CARD_RUN_STATE['reward_remove_successes']} 次"
                )
                time.sleep(1.0)
                next_image, next_evaluated = self._wait_for_stable_cards(context)
                if len(next_evaluated) < 3:
                    logger.warning(
                        "除去已计入整局次数，但未等到奖励牌完整稳定；"
                        "按推荐/第一项兜底避免卡住"
                    )
                    return self._fallback_select(context, next_image)
                self._sync_reward_action_counts(context, next_image)
                new_signature = self._scene_signature(next_image, next_evaluated)
                image, evaluated = next_image, next_evaluated
                if old_signature == new_signature:
                    logger.info(
                        "卡牌除去已确认并计入整局次数，但新牌画面与旧画面一致；"
                        "停止本页继续除去，不据此误判次数耗尽"
                    )
                    break

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

            if _PRODUCE_CARD_RUN_STATE["reward_refresh_exhausted"]:
                logger.info("本局奖励刷新次数已耗尽，按当前非黑名单牌选择")
                break

            remaining = _PRODUCE_CARD_RUN_STATE["reward_refresh_remaining"]
            if remaining == 0:
                _PRODUCE_CARD_RUN_STATE["reward_refresh_exhausted"] = True
                logger.info("奖励页识别到刷新剩余 0 次，按当前非黑名单牌选择")
                break

            if refresh_attempt >= self.MAX_REFRESH_ATTEMPTS:
                logger.info("已达到刷新安全上限，按当前非黑名单牌优先级选择")
                break

            old_signature = self._scene_signature(image, evaluated)
            refresh_attempt += 1
            logger.info(
                "未出现白名单/再动/SSR，尝试再抽选 "
                f"(本页第 {refresh_attempt} 次，本局已用 "
                f"{_PRODUCE_CARD_RUN_STATE['reward_refresh_successes']} 次)"
            )
            # A refresh clicked immediately after a removal can be ignored by
            # the game while the lower controls are still re-enabled.
            time.sleep(0.65)
            context.tasker.controller.post_click(*self.REFRESH_BUTTON).wait()
            time.sleep(1.15)
            next_image, next_evaluated = self._wait_for_stable_cards(context)
            if len(next_evaluated) < 3:
                logger.warning("再抽选后未等到奖励牌完整稳定，按推荐/第一项兜底避免卡住")
                return self._fallback_select(context, next_image)
            previous_remaining = _PRODUCE_CARD_RUN_STATE["reward_refresh_remaining"]
            self._sync_reward_action_counts(context, next_image)
            current_remaining = _PRODUCE_CARD_RUN_STATE["reward_refresh_remaining"]
            new_signature = self._scene_signature(next_image, next_evaluated)
            image, evaluated = next_image, next_evaluated
            remaining_decreased = (
                previous_remaining is not None
                and current_remaining is not None
                and current_remaining < previous_remaining
            )
            if old_signature == new_signature and not remaining_decreased:
                logger.info(
                    "刷新点击后牌面和剩余次数均未变化；本次不计数，"
                    "停止本页刷新但不误记为整局耗尽"
                )
                break
            self._record_reward_action_use(
                "refresh",
                decrement_remaining=(
                    current_remaining is None
                    or (
                        previous_remaining is not None
                        and current_remaining >= previous_remaining
                    )
                ),
            )
            logger.success(
                "奖励刷新已确认，本局累计 "
                f"{_PRODUCE_CARD_RUN_STATE['reward_refresh_successes']} 次"
            )

        allowed = [item for item in evaluated if item["name_key"] not in blacklist]
        pool = allowed or evaluated
        if pool:
            selected = max(pool, key=self._selection_key)
            return self._select(context, selected, "再动与稀有度")
        return self._fallback_select(context, image)

    @classmethod
    def _is_drink_reward(cls, context: Context, image) -> bool:
        """Classify a reward page before any card detector is allowed to run.

        ``ProduceChooseSkip`` is visible only when the owned-drink row is full,
        so using it as the sole discriminator misclassified ordinary drink
        rewards as cards.  Prefer the dedicated prompt, retain the full-row
        marker as a cheap fallback, then feature-match every known drink icon
        strictly inside the three reward slots.
        """
        # Card rewards have an exact, highly stable title.  Check it before
        # the broad 28-template drink fallback, because event rewards can show
        # four cards and therefore offer more accidental feature matches.
        card_prompt = context.run_recognition("ProduceChooseCardRewardFlag", image)
        if card_prompt and card_prompt.hit:
            logger.info("奖励类型识别：卡牌（命中卡牌奖励标题）")
            return False

        prompt = context.run_recognition("ProduceChooseDrinkRewardFlag", image)
        if prompt and prompt.hit:
            logger.info("奖励类型识别：饮料（命中饮料奖励标题）")
            return True

        reject = context.run_recognition("ProduceChooseSkip", image)
        if reject and reject.hit:
            logger.info("奖励类型识别：饮料（命中满栏不领取控件）")
            return True

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
            return True

        logger.info("奖励类型识别：未命中饮料特征，进入卡牌奖励策略")
        return False

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
        """Recognize target identities in reward slots and the four owned slots."""
        reward: dict[int, dict] = {}
        inventory: dict[int, dict] = {}
        reward_centers = [
            (box[0] + box[2] // 2, box[1] + box[3] // 2)
            for box in cls.REWARD_SLOTS
        ]
        for drink_id, template in cls.TARGET_DRINK_TEMPLATES.items():
            detail = context.run_recognition(
                "ProduceRecognitionShopDrinks",
                image,
                pipeline_override={
                    "ProduceRecognitionShopDrinks": {
                        "recognition": "FeatureMatch",
                        "roi": cls.DRINK_RECO_ROI,
                        "template": [template],
                        "count": 4,
                        "ratio": 0.62,
                        "order_by": "Horizontal",
                    }
                },
            )
            if not (detail and detail.hit):
                continue
            results = list(detail.filtered_results or detail.all_results or [])
            for result in results:
                box = cls._normalize_result_box(result, cls.DRINK_RECO_ROI)
                if box[2] <= 0 or box[3] <= 0:
                    continue
                center = (box[0] + box[2] // 2, box[1] + box[3] // 2)
                count = int(getattr(result, "count", 0) or 0)
                if 760 <= center[1] <= 1040:
                    index = min(
                        range(3),
                        key=lambda item: abs(center[0] - reward_centers[item][0]),
                    )
                    if abs(center[0] - reward_centers[index][0]) <= 95:
                        previous = reward.get(index)
                        if previous is None or count > previous["score"]:
                            reward[index] = {
                                "id": drink_id,
                                "name": cls.TARGET_DRINKS[drink_id],
                                "score": count,
                                "box": box,
                            }
                elif center[1] >= 1090:
                    index = min(
                        range(4),
                        key=lambda item: abs(
                            center[0] - cls.INVENTORY_SLOT_CENTERS[item][0]
                        ),
                    )
                    if abs(center[0] - cls.INVENTORY_SLOT_CENTERS[index][0]) <= 55:
                        previous = inventory.get(index)
                        if previous is None or count > previous["score"]:
                            inventory[index] = {
                                "id": drink_id,
                                "name": cls.TARGET_DRINKS[drink_id],
                                "score": count,
                                "box": box,
                            }
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
        reward, inventory = cls._scan_target_drinks(context, image)
        occupied = cls._scan_inventory_occupancy(context, image, inventory)
        full_marker = context.run_recognition("ProduceKeepDrinkFlag", image)
        full = len(occupied) >= 4 or bool(full_marker and full_marker.hit)
        return {
            "reward": reward,
            "inventory": inventory,
            "occupied": occupied,
            "full": full,
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
    def _stable_drink_state(cls, context: Context, first_image) -> tuple:
        samples = []
        image = first_image
        for attempt in range(3):
            state = cls._scan_drink_state(context, image)
            samples.append((cls._drink_state_signature(state), image, state))
            if attempt:
                matching = [item for item in samples if item[0] == samples[-1][0]]
                if len(matching) >= 2:
                    return matching[-1][1], matching[-1][2]
            time.sleep(0.35)
            image = context.tasker.controller.post_screencap().wait().get()
        logger.warning("饮料奖励连续三帧识别结果不一致；停止删除并采用当前页保守选择")
        return samples[-1][1], {**samples[-1][2], "stable": False}

    @classmethod
    def _click_reward_drink(cls, context: Context, index: int, reason: str) -> bool:
        box = cls.REWARD_SLOTS[index]
        point = (box[0] + box[2] // 2, box[1] + box[3] // 2)
        context.tasker.controller.post_click(*point).wait()
        logger.success(f"饮料奖励选择: index={index + 1} reason={reason} point={point}")
        return True

    @classmethod
    def _skip_drink_reward(cls, context: Context, image, reason: str) -> bool:
        detail = context.run_recognition("ProduceChooseSkip", image)
        if detail and detail.hit and detail.best_result:
            box = list(detail.best_result.box)
            point = (box[0] + box[2] // 2, box[1] + box[3] // 2)
        else:
            point = (360, 1030)
        context.tasker.controller.post_click(*point).wait()
        logger.info(f"饮料奖励选择不领取: reason={reason}")
        return True

    @classmethod
    def _delete_owned_drink(cls, context: Context, slot: int) -> bool:
        point = cls.INVENTORY_SLOT_CENTERS[slot]
        context.tasker.controller.post_click(*point).wait()
        time.sleep(0.65)
        image = context.tasker.controller.post_screencap().wait().get()
        if cls._is_drink_reward(context, image):
            context.tasker.controller.post_click(*point).wait()
            time.sleep(0.65)
            image = context.tasker.controller.post_screencap().wait().get()
        if cls._is_drink_reward(context, image):
            logger.warning(f"饮料详情未打开，取消删除: slot={slot + 1}")
            return False

        throw = context.run_recognition(
            "ProduceRecognitionPoint",
            image,
            pipeline_override={
                "ProduceRecognitionPoint": {
                    "recognition": "OCR",
                    "roi": [430, 850, 270, 190],
                    "expected": ["捨てる"],
                    "order_by": "Horizontal",
                }
            },
        )
        if throw and throw.hit and throw.best_result:
            box = list(throw.best_result.box)
            throw_point = (box[0] + box[2] // 2, box[1] + box[3] // 2)
        else:
            throw_point = cls.DRINK_THROW_POINT
        context.tasker.controller.post_click(*throw_point).wait()
        time.sleep(0.65)

        for _ in range(5):
            image = context.tasker.controller.post_screencap().wait().get()
            if cls._is_drink_reward(context, image):
                logger.success(f"已丢弃现有饮料: slot={slot + 1}")
                return True
            confirm = context.run_recognition(
                "ProduceRecognitionPoint",
                image,
                pipeline_override={
                    "ProduceRecognitionPoint": {
                        "recognition": "OCR",
                        "roi": [300, 900, 400, 350],
                        "expected": ["捨てる", "はい", "決定", "OK"],
                        "order_by": "Horizontal",
                    }
                },
            )
            if confirm and confirm.hit and confirm.best_result:
                box = list(confirm.best_result.box)
                confirm_point = (box[0] + box[2] // 2, box[1] + box[3] // 2)
                context.tasker.controller.post_click(*confirm_point).wait()
            else:
                context.tasker.controller.post_click(*cls.DRINK_THROW_CONFIRM_POINT).wait()
            time.sleep(0.45)
        logger.warning(f"丢弃饮料后未返回奖励页: slot={slot + 1}")
        return False

    @classmethod
    def _handle_drink_reward(cls, context: Context, image) -> bool:
        image, state = cls._stable_drink_state(context, image)
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
        if not full:
            if target_indexes:
                selected = target_indexes[0]
                return cls._click_reward_drink(
                    context,
                    selected,
                    "栏位未满，优先当前没有的六类目标饮料",
                )
            return cls._click_reward_drink(context, 0, "栏位未满且无目标，领取第一瓶")

        if not stable:
            if target_indexes:
                return cls._skip_drink_reward(
                    context, image, "满栏识别不稳定，不执行破坏性删除"
                )
            return cls._skip_drink_reward(context, image, "满栏且识别不稳定")

        all_target = len(inventory) == 4
        non_target_slots = sorted(set(range(4)) - set(inventory))
        if target_indexes:
            selected = target_indexes[0]
            selected_id = reward[selected]["id"]
            same_slots = [
                index for index, item in inventory.items() if item["id"] == selected_id
            ]
            delete_slot = same_slots[0] if same_slots else (
                non_target_slots[0] if non_target_slots else None
            )
            if delete_slot is None:
                return cls._skip_drink_reward(
                    context, image, "四种不同目标饮料已完整，保持现状"
                )
            if not cls._delete_owned_drink(context, delete_slot):
                return cls._skip_drink_reward(
                    context, image, "删除现有饮料失败，保守跳过"
                )
            return cls._click_reward_drink(
                context,
                selected,
                "满栏：替换同名目标饮料" if same_slots else "满栏：非目标换成目标饮料",
            )

        if all_target:
            return cls._skip_drink_reward(
                context, image, "奖励无目标且现有四瓶均为目标"
            )
        delete_slot = non_target_slots[0]
        if not cls._delete_owned_drink(context, delete_slot):
            return cls._skip_drink_reward(context, image, "非目标饮料删除失败")
        return cls._click_reward_drink(
            context, 0, "满栏、奖励无目标：删除非目标后领取第一瓶"
        )

    def _evaluate(self, context: Context, image) -> list:
        detection = context.run_recognition("ProduceRecognitionCards", image)
        candidates = []
        if detection and detection.hit:
            candidates = ProduceCardsAuto._get_playable_candidates(
                detection.all_results, image.shape
            )
            # The generic detector also sees the two compact status panels at
            # the top of the reward screen.  Reward cards are the near-square
            # items in the lower half; keep only that stable live-screen area.
            candidates = [
                candidate
                for candidate in candidates
                if 700 <= candidate["box"][1] <= 1000
                and 90 <= candidate["box"][2] <= 180
                and 90 <= candidate["box"][3] <= 180
                and 0.70 <= candidate["box"][2] / max(candidate["box"][3], 1) <= 1.35
            ]
            # Normal rewards have three cards; event rewards can have four.
            # Keep the fourth detection instead of silently discarding it.
            candidates = candidates[:4]
        if not candidates:
            candidates = [
                {"box": box, "label": "fixed-slot", "detector_score": 0.0}
                for box in self.REWARD_SLOTS
                if box[0] + box[2] <= image.shape[1] and box[1] + box[3] <= image.shape[0]
            ]

        evaluated = []
        for index, candidate in enumerate(candidates, start=1):
            rarity = classify_rarity(image, candidate["box"])
            extra, extra_score, extra_roi = ProduceCardsAuto._extra_action_status(
                context, image, candidate["box"]
            )
            visual = match_card_visual(image, candidate["box"])
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
                    "rarity": resolved_rarity,
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
            _diagnostic_info(
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
                    "recognition": "OCR",
                    "roi": roi,
                    "expected": [],
                    "order_by": "Horizontal",
                }
            },
        )
        if not (detail and detail.hit):
            return None
        values = []
        for result in list(detail.filtered_results or detail.all_results or []):
            digits = "".join(
                character
                for character in str(getattr(result, "text", ""))
                if character.isdigit()
            )
            if not digits:
                continue
            value = int(digits)
            if 0 <= value <= 9:
                score = float(getattr(result, "score", 0.0) or 0.0)
                values.append((score, value))
        if not values:
            return None
        return max(values)[1]

    @classmethod
    def _sync_reward_action_counts(
        cls, context: Context, image, log_summary: bool = False
    ) -> None:
        for action in ("remove", "refresh"):
            remaining = cls._recognize_reward_action_remaining(
                context, image, action
            )
            if remaining is None:
                continue
            _PRODUCE_CARD_RUN_STATE[f"reward_{action}_remaining"] = remaining
            # A directly read badge is authoritative and can also clear an
            # earlier transient failure if the game later grants more uses.
            _PRODUCE_CARD_RUN_STATE[f"reward_{action}_exhausted"] = remaining == 0
        if log_summary:
            remove_remaining = _PRODUCE_CARD_RUN_STATE["reward_remove_remaining"]
            refresh_remaining = _PRODUCE_CARD_RUN_STATE["reward_refresh_remaining"]
            logger.info(
                "奖励操作整局计数: "
                f"除去已用={_PRODUCE_CARD_RUN_STATE['reward_remove_successes']} "
                f"剩余={remove_remaining if remove_remaining is not None else '未知'}；"
                f"刷新已用={_PRODUCE_CARD_RUN_STATE['reward_refresh_successes']} "
                f"剩余={refresh_remaining if refresh_remaining is not None else '未知'}"
            )

    @staticmethod
    def _record_reward_action_use(
        action: str, decrement_remaining: bool = True
    ) -> None:
        success_key = f"reward_{action}_successes"
        remaining_key = f"reward_{action}_remaining"
        exhausted_key = f"reward_{action}_exhausted"
        _PRODUCE_CARD_RUN_STATE[success_key] += 1
        remaining = _PRODUCE_CARD_RUN_STATE[remaining_key]
        if decrement_remaining and remaining is not None:
            remaining = max(0, remaining - 1)
            _PRODUCE_CARD_RUN_STATE[remaining_key] = remaining
        if remaining == 0:
            _PRODUCE_CARD_RUN_STATE[exhausted_key] = True

    def _wait_for_stable_cards(self, context: Context) -> tuple:
        previous_signature = None
        last_image = context.tasker.controller.post_screencap().wait().get()
        last_evaluated = []
        best_image = last_image
        best_evaluated = []
        for _ in range(14):
            last_image = context.tasker.controller.post_screencap().wait().get()
            last_evaluated = self._evaluate(context, last_image)
            named_count = sum(bool(item["name_key"]) for item in last_evaluated)
            rarity_count = sum(item["rarity"] != "unknown" for item in last_evaluated)
            ready = len(last_evaluated) >= 3 and named_count >= 2 and rarity_count >= 3
            signature = self._scene_signature(last_image, last_evaluated) if ready else None
            if ready:
                best_image, best_evaluated = last_image, last_evaluated
            if ready and signature == previous_signature:
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
        """Confirm the second-stage orange 'remove' dialog when it appears."""
        time.sleep(0.45)
        image = context.tasker.controller.post_screencap().wait().get()
        height, width = image.shape[:2]
        x1, y1 = int(width * 0.54), int(height * 0.84)
        x2, y2 = min(width, int(width * 0.90)), min(height, int(height * 0.96))
        roi = image[y1:y2, x1:x2, :3]
        if not roi.size:
            return False
        blue, green, red = [roi[:, :, index] for index in range(3)]
        orange = (red > 205) & (green > 75) & (green < 205) & (blue < 120)
        orange_ratio = float(orange.mean())
        _diagnostic_info(f"卡牌除去确认检测: orange_ratio={orange_ratio:.3f}")
        if orange_ratio < 0.08:
            return False
        context.tasker.controller.post_click(*self.REMOVE_CONFIRM_BUTTON).wait()
        return True

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
            values.append((item["name_key"], item["rarity"], item["extra_action"], raw))
        return tuple(values)

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
        self._click_card(context, selected)
        logger.success(
            f"奖励卡选择: reason={reason} name={selected['name']} rarity={selected['rarity']} "
            f"extra_action={selected['extra_action']} priority={selected['priority']} "
            f"box={selected['box']}"
        )
        return True

    def _fallback_select(self, context: Context, image) -> bool:
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
    """Delete a weak card, buy whitelisted cards, then cheapest drinks."""

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
    DELETE_DECK_CONFIRM_POINT = (350, 1162)
    DELETE_DIALOG_CONFIRM_POINT = (499, 1159)

    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        lists = _load_card_lists()
        blacklist = set(lists["blacklist"]["cards"]) if lists["blacklist"]["enabled"] else set()
        whitelist = set(lists["whitelist"]["cards"]) if lists["whitelist"]["enabled"] else set()
        logger.success(
            "咨询策略: 优先删除 N/黑名单/R；购买白名单卡；否则按价格从低到高购买饮料"
        )
        blocked = {"drinks": [], "cards": []}
        attempts = 0
        self._disconnect_detected = False

        image = context.tasker.controller.post_screencap().wait().get()
        if not self._is_shop_scene(context, image) and not self._reenter_shop(context):
            if not self._disconnect_detected:
                logger.warning("咨询购买终止: 无法进入商品页")
            return True
        image = context.tasker.controller.post_screencap().wait().get()
        points = self._recognize_points(context, image)

        if points is None or points >= self.DELETE_COST:
            delete_status = self._try_delete_owned_card(
                context, image, blacklist, whitelist
            )
            if delete_status == "disconnected":
                return True
            if delete_status == "deleted" and points is not None:
                points = max(0, points - self.DELETE_COST)
            if delete_status in ("deleted", "skipped"):
                if not self._reenter_shop(context):
                    if not self._disconnect_detected:
                        logger.warning("咨询删除后无法回到商品页")
                    return True
                image = context.tasker.controller.post_screencap().wait().get()
        else:
            logger.info(f"当前积分 {points} < 删除费用 {self.DELETE_COST}，跳过删除")

        # The shop never changes stock unless the user presses refresh.  Scan
        # its four fixed card/drink slots once and retain them for this visit.
        cards = self._recognize_shop_cards(context, image, blocked["cards"])
        drinks = self._recognize_drinks(context, image, blocked["drinks"])

        while attempts < self.MAX_PURCHASE_ATTEMPTS and not context.tasker.stopping:
            available_cards = [
                item
                for item in cards
                if item["box"] not in blocked["cards"]
                and item["name_key"] in whitelist
                and item["name_key"] not in blacklist
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
                available_drinks = [
                    item
                    for item in drinks
                    if item["box"] not in blocked["drinks"]
                    and self._is_affordable(item, points)
                ]
                if not available_drinks:
                    logger.info("咨询购买完成: 没有可负担的白名单卡或饮料")
                    break
                selected = min(
                    available_drinks,
                    key=lambda item: (
                        item["price"] if item["price"] is not None else 9999,
                        item["box"][0],
                    ),
                )
                item_type = "最低价饮料"
                blocked_key = "drinks"
                logger.info(
                    f"咨询最低价饮料: price={selected['price']} box={selected['box']}"
                )

            attempts += 1
            status = self._purchase(context, selected["box"], item_type)
            blocked[blocked_key].append(selected["box"])
            if status == "disconnected":
                return True
            if status == "bought":
                if points is not None and selected["price"] is not None:
                    points = max(0, points - selected["price"])
                if self._reenter_shop(context):
                    continue
                if not self._disconnect_detected:
                    logger.warning("咨询购买后无法重新打开原商品页")
                break
            if status == "full":
                logger.info("饮料持有数已满，停止购买饮料")
                blocked["drinks"] = [item["box"] for item in drinks]
                continue
            if status == "no-funds":
                break
            logger.warning(
                f"该商品当前无法购买，已跳过且不重复扫描: type={item_type}, box={selected['box']}"
            )

        if attempts >= self.MAX_PURCHASE_ATTEMPTS:
            logger.warning("咨询购买达到安全上限，停止购买并退出，避免循环卡住")
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

    @staticmethod
    def _is_affordable(item: dict, points: Optional[int]) -> bool:
        price = item.get("price")
        return points is None or price is None or price <= points

    @staticmethod
    def _recognize_points(context: Context, image) -> Optional[int]:
        detail = context.run_recognition(
            "ProduceRecognitionPoint",
            image,
            pipeline_override={"ProduceRecognitionPoint": {"roi": [320, 90, 130, 54]}},
        )
        if not (detail and detail.hit and detail.best_result):
            logger.warning("咨询积分识别失败，购买时保留按钮状态兜底")
            return None
        try:
            value = int("".join(filter(str.isdigit, detail.best_result.text)))
            logger.info(f"咨询当前积分: {value}")
            return value
        except (TypeError, ValueError):
            logger.warning("咨询积分解析失败，购买时保留按钮状态兜底")
            return None

    @staticmethod
    def _recognize_price(context: Context, image, box: list) -> Optional[int]:
        x, y, width, height = box
        roi = [max(0, x - 8), y + height - 2, width + 16, 48]
        detail = context.run_recognition(
            "ProduceRecognitionPoint",
            image,
            pipeline_override={
                "ProduceRecognitionPoint": {
                    "roi": roi,
                    "expected": "^\\d{1,3}$",
                    "order_by": "Horizontal",
                }
            },
        )
        if not (detail and detail.hit):
            return None
        values = []
        for result in list(detail.filtered_results or detail.all_results or []):
            text = "".join(filter(str.isdigit, str(getattr(result, "text", ""))))
            if not text:
                continue
            value = int(text)
            if not 0 < value <= 999:
                continue
            result_box = list(getattr(result, "box", []) or [])
            result_x = int(result_box[0]) if len(result_box) == 4 else 0
            if result_x < roi[0] and result_x <= roi[2]:
                result_x += roi[0]
            values.append((result_x, value))
        # Sale tiles contain old and new prices.  The live UI always prints the
        # payable sale price at the far right, so choose the rightmost number.
        return max(values, default=(0, None), key=lambda item: item[0])[1]

    @staticmethod
    def _slot_is_sold(image, box: list) -> bool:
        x, y, width, height = box
        crop = image[y + 12:y + height - 12, x + 12:x + width - 12, :3]
        if not crop.size:
            return True
        pixels = crop.astype(np.float32)
        maximum = pixels.max(axis=2)
        minimum = pixels.min(axis=2)
        saturation = np.where(maximum > 0, (maximum - minimum) / maximum, 0)
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
    ) -> str:
        entry_point = self._find_shop_delete_entry(image)
        if entry_point is None:
            entry_point = self.DELETE_ENTRY_POINT
            logger.warning(
                f"咨询删除按钮蓝色校验未命中，使用底部固定坐标 {entry_point} 并验证页面变化"
            )
        else:
            logger.info(f"咨询识别到蓝色删除入口: point={entry_point}")
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
                        "template": ["produce/delete_icon.png"]
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

        candidates = self._recognize_delete_cards(context, delete_image)
        eligible = []
        for item in candidates:
            if item["name_key"] in whitelist:
                continue
            if item["rarity"] == "N":
                item["delete_rank"] = 3
            elif item["name_key"] in blacklist:
                item["delete_rank"] = 2
            elif item["rarity"] == "R":
                item["delete_rank"] = 1
            else:
                continue
            eligible.append(item)

        if not eligible:
            logger.info("咨询删除页没有 N、黑名单或 R 卡，取消删除")
            context.run_task("ProduceNIAButton")
            return "skipped"

        selected = max(
            eligible,
            key=lambda item: (
                item["delete_rank"],
                item["name_confidence"],
                -item["box"][1],
                -item["box"][0],
            ),
        )
        self._click_product(context, selected["box"])
        time.sleep(0.4)
        image = context.tasker.controller.post_screencap().wait().get()
        confirm = self._recognize_delete_button(
            context,
            image,
            "produce/delete.png",
            [210, 1090, 300, 125],
            0.20,
        )
        if confirm and confirm.hit and confirm.best_result:
            confirm_box = list(confirm.best_result.box)
            confirm_point = (
                confirm_box[0] + confirm_box[2] // 2,
                confirm_box[1] + confirm_box[3] // 2,
            )
        else:
            confirm_point = self.DELETE_DECK_CONFIRM_POINT
            logger.info("咨询删除页按钮文字未命中，使用底部固定删除按钮")
        context.tasker.controller.post_click(*confirm_point).wait()
        time.sleep(0.45)
        image = context.tasker.controller.post_screencap().wait().get()
        final_confirm = self._recognize_delete_button(
            context,
            image,
            "produce/delete_confirm.png",
            [350, 1085, 320, 135],
            0.20,
        )
        if final_confirm and final_confirm.hit and final_confirm.best_result:
            final_box = list(final_confirm.best_result.box)
            context.tasker.controller.post_click(
                final_box[0] + final_box[2] // 2,
                final_box[1] + final_box[3] // 2,
            ).wait()
        elif self._orange_button_present(image, [350, 1085, 320, 135]):
            logger.info("咨询二次确认文字未命中，检测到橙色确认按钮并固定点击")
            context.tasker.controller.post_click(
                *self.DELETE_DIALOG_CONFIRM_POINT
            ).wait()
        else:
            logger.info("咨询未出现二次确认按钮，按删除已直接提交处理")
        time.sleep(0.8)
        logger.success(
            f"咨询删除卡牌: name={selected['name']} rarity={selected['rarity']} "
            f"reason_rank={selected['delete_rank']}"
        )
        return "deleted"

    @staticmethod
    def _recognize_delete_button(
        context: Context,
        image,
        template: str,
        roi: list,
        threshold: float,
    ):
        return context.run_recognition(
            "ProduceChooseDeleteClick",
            image,
            pipeline_override={
                "ProduceChooseDeleteClick": {
                    "template": [template],
                    "roi": roi,
                    "threshold": threshold,
                }
            },
        )

    @staticmethod
    def _orange_button_present(image, roi: list) -> bool:
        x, y, width, height = roi
        crop = image[y:y + height, x:x + width, :3]
        if not crop.size:
            return False
        blue, green, red = [crop[:, :, index] for index in range(3)]
        orange = (red > 195) & (green > 70) & (green < 210) & (blue < 135)
        return float(orange.mean()) >= 0.04

    def _recognize_delete_cards(self, context: Context, image) -> list:
        detail = context.run_recognition("ProduceRecognitionCards", image)
        if not (detail and detail.hit):
            return []
        candidates = ProduceCardsAuto._get_playable_candidates(
            detail.all_results, image.shape
        )
        candidates = [
            item
            for item in candidates
            if 180 <= item["box"][1] <= 1050
            and 60 <= item["box"][2] <= 240
            and 100 <= item["box"][3] <= 360
        ]
        evaluated = []
        for item in candidates:
            rarity = classify_rarity(image, item["box"])
            name, name_key, confidence = self._ocr_card_name(
                context, image, item["box"]
            )
            result = {
                **item,
                "name": name,
                "name_key": name_key,
                "name_confidence": confidence,
                "rarity": rarity.rarity,
            }
            evaluated.append(result)
            _diagnostic_info(
                f"咨询删除候选: box={item['box']} name={name} "
                f"name_confidence={confidence:.3f} rarity={rarity.rarity} "
                f"rarity_confidence={rarity.confidence:.3f}"
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
        context.tasker.controller.post_click(
            box[0] + box[2] // 2, box[1] + box[3] // 2
        ).wait()

    def _close_detail_and_reopen(self, context: Context) -> bool:
        """Keep scanning the unchanged list after an unavailable item."""
        return self._reenter_shop(context)

    @classmethod
    def _is_shop_scene(cls, context: Context, image) -> bool:
        detail = context.run_recognition("ProduceShoppingFlag", image)
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
                    logger.info(f"咨询购买后已重新进入商品页: attempt={click_attempt + 1}")
                    return True
        return False

    def _recognize_drinks(self, context: Context, image, blocked_boxes: list) -> list:
        found = []
        for box in self.DRINK_SLOTS:
            if any(intersection_over_union(box, blocked) > 0.30 for blocked in blocked_boxes):
                continue
            if self._slot_is_sold(image, box):
                continue
            price = self._recognize_price(context, image, box)
            found.append({"box": list(box), "price": price})
        logger.info(
            "咨询饮料一次扫描: "
            + ", ".join(
                f"x={item['box'][0]} price={item['price']}" for item in found
            )
        )
        return found

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
            visual = match_card_visual(image, box)
            resolved_rarity = visual.rarity if visual is not None else rarity.rarity
            if visual is not None and visual.extra_action:
                extra = "yes"
            item = {
                "box": list(box),
                "label": "shop-slot",
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

    def _purchase(self, context: Context, box: list, item_type: str) -> str:
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

        if item_type == "饮料":
            full = context.run_recognition("ProduceShoppingDrinkFull", image)
            if full and full.hit:
                logger.warning("饮料持有数已满，转入高稀有度卡牌阶段")
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
            decide_box = decide.best_result.box
            context.tasker.controller.post_click(
                decide_box[0] + decide_box[2] // 2,
                decide_box[1] + decide_box[3] // 2,
            ).wait()
            # Rare drinks show a full-screen acquisition description after
            # exchange.  A neutral tap closes it; on the consultation desk the
            # same coordinate is inert.  This prevents the overlay from being
            # mistaken for another product state.
            time.sleep(1.0)
            context.tasker.controller.post_click(360, 360).wait()
            time.sleep(0.7)
            logger.success(f"咨询购买完成: type={item_type}, item_box={box}")
            return "bought"

        logger.warning(f"咨询购买结果未知: 未识别到兑换确认或余额不足, type={item_type}, box={box}")
        return "unavailable"


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
    ATTR_FALLBACK_POINTS = {
        "Vo": (190, 1000),
        "Da": (360, 1000),
        "Vi": (530, 1000),
    }

    @staticmethod
    def _strategy(context: Context) -> dict:
        return (context.get_node_data("ProduceHIFStrategy") or {}).get("attach", {})

    @staticmethod
    def _attribute_order(context: Context) -> dict:
        node = context.get_node_data("ProduceChooseNIAEventFlag") or {}
        preference = (
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

    @classmethod
    def _enter_event_if_needed(
        cls, context: Context, image, event_text: str
    ) -> tuple[Any, bool]:
        labels = cls._ocr(context, image, [event_text], [0, 450, 720, 700])
        if not labels:
            return image, False
        box = list(labels[0].box)
        cls._click_box(context, box)
        time.sleep(1.0)
        return context.tasker.controller.post_screencap().wait().get(), True

    @classmethod
    def _choose_attribute(
        cls, context: Context, image, attribute: str
    ) -> tuple[bool, tuple[int, int]]:
        expected = list(cls.ATTR_LABELS[attribute])
        results = cls._ocr(context, image, expected, [0, 600, 720, 500])
        if results:
            box = list(results[0].box)
            point = (box[0] + box[2] // 2, box[1] + box[3] // 2)
            cls._click_box(context, box)
            return True, point
        point = cls.ATTR_FALLBACK_POINTS[attribute]
        context.tasker.controller.post_click(*point).wait()
        time.sleep(0.3)
        context.tasker.controller.post_click(*point).wait()
        return False, point


@AgentServer.custom_action("ProduceHIFChooseClassAuto")
class ProduceHIFChooseClassAuto(ProduceHIFAttributeBase):
    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        image = context.tasker.controller.post_screencap().wait().get()
        attribute = self._selected_attribute(context, "lesson_attribute")
        attr_words = [word for values in self.ATTR_LABELS.values() for word in values]
        has_attributes = bool(self._ocr(context, image, attr_words, [0, 600, 720, 500]))
        entered = False
        if not has_attributes:
            image, entered = self._enter_event_if_needed(context, image, "授業")
        found, point = self._choose_attribute(context, image, attribute)
        option = int(self._strategy(context).get("lesson_option", 1) or 1)
        option = min(3, max(1, option))
        time.sleep(1.1)
        option_point = (100, {1: 760, 2: 880, 3: 1000}[option])
        context.tasker.controller.post_click(*option_point).wait()
        logger.success(
            f"HIF本战上课: attribute={attribute} point={point} "
            f"ocr={found} entered={entered} option={option} option_point={option_point}"
        )
        return True


@AgentServer.custom_action("ProduceHIFChooseCourseAuto")
class ProduceHIFChooseCourseAuto(ProduceHIFAttributeBase):
    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        image = context.tasker.controller.post_screencap().wait().get()
        attribute = self._selected_attribute(context, "course_attribute")
        attr_words = [word for values in self.ATTR_LABELS.values() for word in values]
        has_attributes = bool(self._ocr(context, image, attr_words, [0, 600, 720, 500]))
        entered = False
        if not has_attributes:
            labels = self._ocr(
                context, image, ["公開レッスン", "レッスン"], [0, 450, 720, 700]
            )
            if labels:
                self._click_box(context, list(labels[0].box))
                time.sleep(1.0)
                image = context.tasker.controller.post_screencap().wait().get()
                entered = True
        found, point = self._choose_attribute(context, image, attribute)
        logger.success(
            f"HIF本战课程: attribute={attribute} point={point} "
            f"ocr={found} entered={entered}"
        )
        return True


@AgentServer.custom_action("ProduceHIFChooseSupplyAuto")
class ProduceHIFChooseSupplyAuto(ProduceHIFAttributeBase):
    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        image = context.tasker.controller.post_screencap().wait().get()
        results = self._ocr(context, image, ["差し入れ"], [0, 450, 720, 700])
        if not results:
            logger.warning("HIF本战外出/支给页未识别到差し入れ，保留画面等待下一轮")
            return True
        box = list(results[0].box)
        self._click_box(context, box)
        logger.success("HIF本战外出/支给固定选择: 差し入れ")
        return True


@AgentServer.custom_action("ProduceHIFShopAuto")
class ProduceHIFShopAuto(CustomAction):
    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        strategy = (context.get_node_data("ProduceHIFStrategy") or {}).get("attach", {})
        if not strategy.get("manual_shop", False):
            logger.info("HIF本战商店手动接管已关闭，继续自动咨询策略")
            context.run_task("ProduceShoppingFlag")
            return True
        logger.success("HIF本战检测到商店，进入玩家手动接管")
        context.run_action(
            "ProduceHIFShopFocus",
            pipeline_override={
                "ProduceHIFShopFocus": {
                    "focus": {
                        "Node.ActionNode.Succeeded": {
                            "content": "[color:LimeGreen]检测到HIF本战商店，已暂停自动培育[/color]\n"
                            '请手动完成商店操作并离开商店，再点击下方“确定”按钮恢复自动培育',
                            "display": ["log", "modal", "notification"],
                        }
                    }
                }
            },
        )
        logger.info("玩家已确认结束HIF商店手动接管，恢复画面识别")
        return True


@AgentServer.custom_action("ProduceKeepDrinkAuto")
class ProduceKeepDrinkAuto(CustomAction):
    """
    处理保留饮料的窗口
    原本是在ProduceButton节点直接按保留按钮处理
    但是需要处理只有2瓶饮料时，一次性获得2瓶饮料时不会自动勾选3瓶的特殊情况
    所以转为独立处理
    """

    def run(
        self,
        context: Context,
        argv: CustomAction.RunArg,
    ) -> bool:
        image = context.tasker.controller.post_screencap().wait().get()
        reco_detail = context.run_recognition("ProduceRecognitionUncheckedMark", image)
        if reco_detail.hit:
            for result in reco_detail.filtered_results:
                box = result.box
                context.tasker.controller.post_click(box[0] + int(box[2] / 2), box[1] + int(box[3] / 2)).wait()
                time.sleep(0.1)
            context.run_task("ProduceDrinkNoButton")
        return True

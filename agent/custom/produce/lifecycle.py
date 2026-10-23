"""Cultivation lifecycle; behavior moved from the original action module."""

import time

from utils import logger
from maa.context import Context
from maa.custom_action import CustomAction
from maa.agent.agent_server import AgentServer

from . import shared


@AgentServer.custom_action("ProduceLoadCardLists")
class ProduceLoadCardLists(CustomAction):
    """Print one deterministic list summary when a Produce task starts."""

    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        from custom.produce.memory.modes import initialize

        try:
            initialize(context)
        except (ValueError, TypeError) as error:
            logger.warning(f"培育模式设置无效：{error}；请先选择目标再开始")
            return False
        from custom.hif import configure_hif_start_routes

        if configure_hif_start_routes(context):
            logger.info("HIF本战入口：已接入开始确认页，保留道具开关与准备步骤")
        loop = (context.get_node_data("ProduceLoop") or {}) if context is not None else {}
        limit = loop.get("max_hit")
        logger.info(
            f"本次任务培育次数: {limit if isinstance(limit, int) and 0 < limit < 4294967295 else '不限'}；"
            "继续中的当前局计入一次，结算返回主页后检查次数，再决定是否准备下一局"
        )
        lists = shared._load_card_lists()
        black = lists["blacklist"]
        white = lists["whitelist"]
        black_names = "/".join(black["cards"]) if black["cards"] else "（空）"
        white_names = "/".join(white["cards"]) if white["cards"] else "（空）"
        logger.success(f"已载入当前黑名单（{'启用' if black['enabled'] else '未启用'}）：{black_names}")
        logger.success(f"白名单（{'启用' if white['enabled'] else '未启用'}）：{white_names}")
        replacement = lists.get("replacementList", {"enabled": False, "cards": []})
        logger.success(f"替换名单（{'启用' if replacement['enabled'] else '未启用'}）：" + ("/".join(replacement["cards"]) or "（空）"))
        return True


@AgentServer.custom_action("ProduceResetRewardCounts")
class ProduceResetRewardCounts(CustomAction):
    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        from .shop import ProduceShoppingAuto
        from .rewards import ProduceChooseRewardAuto

        shared._reset_produce_card_run_state()
        ProduceShoppingAuto._reset_visit(context)
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
                logger.warning(f"网络重试：弹窗仍存在，枚举右侧重试按钮安全点 {index + 1}/{len(self.NETWORK_RETRY_POINTS)}: {point}")
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


@AgentServer.custom_action("ProduceHIFFailedAuto")
class ProduceHIFFailedAuto(CustomAction):
    @staticmethod
    def _retry(context):
        # Reuse only the two verified buttons. End this nested task after
        # confirmation so JumpBack resumes HIF, not the generic ProduceEntry.
        disconnects = ["[JumpBack]ProduceDisconnectError", "[JumpBack]ProduceDisconnectTitle", "[JumpBack]ProduceDisconnectHome"]
        result = context.run_task(
            "ProduceHIFRetryButton",
            {
                "ProduceHIFRetryButton": {
                    "recognition": {"type": "TemplateMatch", "param": {"roi": [16, 1040, 688, 220], "template": ["produce/challenge.png"]}},
                    "action": {"type": "Click"},
                    "next": [*disconnects, "ProduceHIFRetryConfirm", "ProduceHIFRetryBattleReady"],
                    "timeout": 10000,
                },
                "ProduceHIFRetryConfirm": {
                    "recognition": {"type": "TemplateMatch", "param": {"roi": [16, 1040, 688, 220], "template": ["produce/decide.png"]}},
                    "action": {"type": "Click"},
                    "next": [],
                    "timeout": 10000,
                },
                "ProduceHIFRetryBattleReady": {
                    "recognition": {"type": "Custom", "param": {"custom_recognition": "ProduceCardsFlagAuto"}},
                    "action": {"type": "DoNothing"},
                    "next": [],
                    "timeout": 10000,
                },
            },
        )
        if context.tasker.stopping:
            return True
        if result and result.status.succeeded:
            logger.success("HIF失败重试已开启：再次挑战，继续HIF出牌流程")
            return True
        logger.warning("HIF再次挑战未完成确认，保留当前页面，不误选结束培育")
        return False

    def run(self, context, argv):
        # Only the explicit retry option controls this. ProduceHIFStrategy is
        # a disabled configuration holder, not a retry/scenario switch.
        node = context.get_node_data("ProduceHIFFailedFlag") or {}
        retry = node.get("attach", {}).get("retry_enabled", False) is True
        for attempt in range(5):
            if context.tasker.stopping:
                return True
            image = context.tasker.controller.post_screencap().wait().get()
            if shared._detect_disconnect_screen(context, image):
                return True
            flag = context.run_recognition("ProduceFailedFlag", image)
            if retry and flag and flag.hit:
                again = context.run_recognition("ProduceFailedRetry", image)
                if again and again.hit and again.best_result:
                    return self._retry(context)
                if attempt < 4:
                    time.sleep(0.4)
                    continue
            stop = context.run_recognition("ProduceFailedStop", image)
            if flag and flag.hit and stop and stop.hit and stop.best_result:
                box = stop.best_result.box
                context.tasker.controller.post_click(box[0] + box[2] // 2, box[1] + box[3] // 2).wait()
                reason = "再次挑战入口不可用" if retry else "失败重试已关闭"
                logger.success(f"HIF未获胜，{reason}，结束培育并继续检查回忆")
                return True
            time.sleep(0.4)
        logger.warning("HIF结束按钮未确认，保留现场，不误点再挑战")
        return False


@AgentServer.custom_action("ProduceSkipShowAuto")
class ProduceSkipShowAuto(CustomAction):
    """按固定坐标尝试右上角两次、左上角一次，并限制点击在当前画面内。（同步自上游 b3da0cb）"""

    TARGETS = ((1192, 80), (1192, 80), (20, 20))

    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        for target_x, target_y in self.TARGETS:
            if context.tasker.stopping:
                return True
            image = context.tasker.controller.post_screencap().wait().get()
            if image is None or image.size == 0:
                return False
            height, width = image.shape[:2]
            if height >= width:
                # 演出已结束或尚在切屏，交还流水线判断，避免点击结算页面。
                return True

            x = max(0, min(width - 1, target_x))
            y = max(0, min(height - 1, target_y))
            if not context.tasker.controller.post_click(x, y).wait().succeeded:
                return False
            time.sleep(0.3)

        return True

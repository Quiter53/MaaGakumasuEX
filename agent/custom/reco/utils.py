import time
from typing import Union, Optional

from utils import logger
from maa.define import RectType
from maa.context import Context
from maa.library import Library
from maa.agent.agent_server import AgentServer
from maa.custom_recognition import CustomRecognition

from ..params import action_params


@AgentServer.custom_recognition("ScreenRotateCheck")
class ScreenRotateCheck(CustomRecognition):
    """
    检测通过屏幕是否旋转判
    """

    def analyze(
        self,
        context: Context,
        argv: CustomRecognition.AnalyzeArg,
    ) -> Union[CustomRecognition.AnalyzeResult, Optional[RectType]]:
        image = argv.image
        height = image.shape[0]
        width = image.shape[1]
        context.run_task("Click_1")
        logger.debug(Library.version())
        if height < width:
            logger.debug("横屏")
            return CustomRecognition.AnalyzeResult(box=[0, 0, 1, 1], detail={"detail": "屏幕横屏"})
        return CustomRecognition.AnalyzeResult(box=None, detail={"detail": "屏幕竖屏"})


@AgentServer.custom_recognition("ProduceLoopGuard")
class ProduceLoopGuard(CustomRecognition):
    """
    子流程防卡死：只有在前面的有效节点都没命中、流程即将再次落到兜底点击或自循环时才会被识别到。
    同一段兜底连续超过 seconds 秒（两次评估间隔超过 idle_reset 秒视为新的一段）即命中，
    节点本身不做动作且没有 next，从而结束当前子流程，交回 JumpBack 父节点或 run_task 调用方重新判断页面。
    """

    DEFAULT_SECONDS = 60.0
    IDLE_RESET = 15.0
    clock = staticmethod(time.monotonic)
    _streaks: dict = {}

    @classmethod
    def reset(cls):
        cls._streaks.clear()

    def analyze(self, context: Context, argv: CustomRecognition.AnalyzeArg):
        params = action_params(getattr(argv, "custom_recognition_param", None))
        key = str(params.get("key") or getattr(argv, "node_name", "") or "default")
        limit = float(params.get("seconds", self.DEFAULT_SECONDS))
        now = self.clock()
        start, last = self._streaks.get(key, (now, None))
        if last is None or now - last > self.IDLE_RESET:
            start = now
        if now - start >= limit:
            self._streaks.pop(key, None)
            logger.warning(f"子流程 {key} 已连续 {now - start:.0f} 秒未识别到目标页面，结束子流程并交回上层重新判断")
            return CustomRecognition.AnalyzeResult(box=[0, 0, 1, 1], detail={"key": key, "elapsed": now - start})
        self._streaks[key] = (start, now)
        return None

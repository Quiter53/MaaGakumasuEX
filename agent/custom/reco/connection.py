import json
import time

from utils import logger
from maa.custom_action import CustomAction
from maa.agent.agent_server import AgentServer
from maa.custom_recognition import CustomRecognition

from ..shop import ocr
from ..dialog_safety import connection_dialog, drink_overlay_cancel


@AgentServer.custom_recognition("ProduceConnectionDialog")
class ProduceConnectionDialog(CustomRecognition):
    def analyze(self, context, argv):
        params = json.loads(argv.custom_recognition_param or "{}")
        found = connection_dialog(context, argv.image, params.get("kind", "any"))
        if found:
            return CustomRecognition.AnalyzeResult(box=found["box"], detail=found)
        return None


@AgentServer.custom_recognition("ProduceDrinkDiscardDialog")
class ProduceDrinkDiscardDialog(CustomRecognition):
    def analyze(self, context, argv):
        # This new cancellation route is independent of connection thresholds.
        candidate = context.run_recognition(
            "ProduceConnectionDialogTemplate", argv.image, pipeline_override={"ProduceConnectionDialogTemplate": {"threshold": 0.8}}
        )
        if not candidate or not candidate.hit:
            return None
        rows = ocr(context, argv.image, [15, 780, 680, 360], [r"^廃棄確認$"])
        if len(rows) == 1 and float(rows[0].score) >= 0.8:
            return CustomRecognition.AnalyzeResult(box=list(rows[0].box), detail={"kind": "discard"})
        return None


@AgentServer.custom_action("ProduceCancelDrinkDiscard")
class ProduceCancelDrinkDiscard(CustomAction):
    def run(self, context, argv):
        # A restarted task has no verified intended bottle; cancel, then let
        # the reward policy choose again. Never resume by confirming Yes.
        for attempt in range(3):
            if context.tasker.stopping:
                return True
            image = context.tasker.controller.post_screencap().wait().get()
            rows = None
            if attempt == 0:
                rows = ocr(context, image, [15, 780, 680, 360], [r"^廃棄確認$"])
                if len(rows) != 1 or float(rows[0].score) < 0.8:
                    return True
            present, box = drink_overlay_cancel(context, image, title_rows=rows)
            if not present or box is None:
                return True
            x, y, w, h = box
            context.tasker.controller.post_click(x + w // 2, y + h // 2).wait()
            logger.info("饮料弹窗恢复：取消未完成的丢弃/详情，重新判断奖励页")
            time.sleep(0.3)
        return True

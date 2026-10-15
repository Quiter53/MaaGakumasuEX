"""Keep story confirmation and the follow preference in separate dialog scopes."""
import json
import re
import time
import unicodedata

from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction
from maa.custom_recognition import CustomRecognition

from ..shop import ocr


def _text(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))


def _rows(context, image, roi):
    return [row for row in ocr(context, image, roi, [".*"]) if float(row.score) >= .8]


def _template(context, image, name, roi=None):
    override = {name: {"roi": roi}} if roi is not None else None
    result = context.run_recognition(name, image, pipeline_override=override)
    if result and result.hit and result.best_result is not None:
        return list(result.best_result.box)
    return None


def story_dialog(context, image):
    """A partial but exact heading holds the modal; only a full identity may click."""
    box = _template(context, image, "ProduceStoryDialogTemplate")
    if box is not None:
        return {"box": box, "ready": True}
    rows = _rows(context, image, [20, 780, 680, 330])
    headings = [row for row in rows if _text(row.text) in (
        "早送り確認", "快进确认", "快進確認", "跳过确认", "跳過確認")]
    if len(headings) != 1:
        return None
    heading = headings[0]
    hx, hy, hw, hh = heading.box
    # Observed JP question, with localized equivalents. No generic "confirm" match.
    question = any(
        hy + hh <= row.box[1] <= hy + 230
        and abs(row.box[0] - hx) < 100
        and re.fullmatch(r"早送りしますか[?？]?|(?:是否|要)(?:快进|快進|跳过|跳過)(?:剧情|劇情)?[?？]?", _text(row.text))
        for row in rows)
    return {"box": list(heading.box), "ready": question}


def story_yes(context, image, dialog):
    if not dialog["ready"]:
        return None
    # The affirmative control is on the right, below this dialog's content.
    bottom = max(1080, int(dialog["box"][1]) + int(dialog["box"][3]))
    if bottom >= 1260:
        return None
    roi = [360, bottom, 340, 1260 - bottom]
    box = _template(context, image, "ProduceStoryYesTemplate", roi)
    if box is not None:
        return box
    rows = [row for row in _rows(context, image, roi)
            if _text(row.text) in ("はい", "确认", "確認", "确定", "確定", "是")]
    return list(rows[0].box) if len(rows) == 1 else None


def follow_dialog(context, image):
    # Even when the preference is No, identity always uses the FOLLOW control.
    # A Cancel button, by itself, can never select this route again.
    follow = _template(context, image, "ProduceFollowButtonTemplate")
    if follow is None:
        return None
    labels = _rows(context, image, follow)
    if not any(re.fullmatch(r"[→➜]?フォローする|[→➜]?(?:关注|關注)", _text(row.text)) for row in labels):
        return None
    if story_dialog(context, image) is not None:
        return None
    # Require the two controls on the same row before allowing cancellation.
    x, y, w, h = follow
    cancel = _template(context, image, "ProduceFollowCancelTemplate", [20, max(0, y - 25), max(1, x - 30), h + 50])
    if cancel is not None and (cancel[0] + cancel[2] > x or abs(cancel[1] + cancel[3] / 2 - y - h / 2) > 40):
        cancel = None
    return {"box": follow, "cancel": cancel}


@AgentServer.custom_recognition("ProduceStoryDialog")
class ProduceStoryDialog(CustomRecognition):
    def analyze(self, context, argv):
        found = story_dialog(context, argv.image)
        if found is not None:
            return CustomRecognition.AnalyzeResult(box=found["box"], detail=found)
        return None


@AgentServer.custom_recognition("ProduceFollowDialog")
class ProduceFollowDialog(CustomRecognition):
    def analyze(self, context, argv):
        found = follow_dialog(context, argv.image)
        if found is not None:
            return CustomRecognition.AnalyzeResult(box=found["box"], detail=found)
        return None


@AgentServer.custom_action("ProduceSettlementDialogAction")
class ProduceSettlementDialogAction(CustomAction):
    def run(self, context, argv):
        params = json.loads(argv.custom_action_param or "{}")
        kind = params.get("kind")
        if kind not in ("story", "follow"):
            return True
        for attempt in range(3):
            if context.tasker.stopping:
                return True
            if attempt:
                time.sleep(.3)
            image = context.tasker.controller.post_screencap().wait().get()
            if image is None:
                continue
            if kind == "story":
                found = story_dialog(context, image)
                box = story_yes(context, image, found) if found else None
            else:
                found = follow_dialog(context, image)
                box = (found["box"] if params.get("follow", True) else found["cancel"]) if found else None
            if found is None:
                return True  # Scene changed; discard the previous recognition box.
            if box is not None:
                if context.tasker.stopping:
                    return True
                x, y, w, h = box
                context.tasker.controller.post_click(int(x + w / 2), int(y + h / 2)).wait()
                # At most one click per dispatch; post_delay gives animations time.
                return True
        # JumpBack returns to the caller. Never fail cultivation on an incomplete frame.
        return True

"""Pick the first non-conflicting rental support, without replacing the team."""

import re
import time
from dataclasses import dataclass

import numpy as np

from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction
from custom.ocr_text import normalize_label, match_ocr_label
from utils import logger
from .shared import _detect_disconnect_screen


@dataclass(frozen=True)
class Candidate:
    y: int
    duplicate: bool
    identity: tuple = ()


def is_duplicate(text):
    # The word "support" alone is not evidence of a conflict, even though its
    # edit similarity to the full badge is high.
    normalized = normalize_label(text)
    if not any(char in normalized for char in "重複复復"):
        return False
    return match_ocr_label(text, {
        "duplicate": ["サポート重複", "サポート重复", "支援卡重复", "支援卡重複", "支援重复", "支援重複"],
    }, min_score=0.8, short_exact=3) is not None


def candidates(rows, *, first_page=False):
    """Associate badge text with the level line on the same rental row."""
    levels, badges = [], []
    # OCR may split the Japanese label into two horizontally adjacent boxes.
    labels = []
    for row in rows:
        box = getattr(row, "box", None)
        if box and len(box) == 4 and box[0] < 320 and 560 <= box[1] < 980 and getattr(row, "score", 0) >= 0.6:
            labels.append(row)
    for row in labels:
        line = sorted((other for other in labels if abs(other.box[1] - row.box[1]) <= 10), key=lambda other: other.box[0])
        if is_duplicate("".join(other.text for other in line)):
            badges.append(int(row.box[1] + row.box[3] / 2))
    for row in rows:
        box = getattr(row, "box", None)
        if not box or len(box) != 4 or getattr(row, "score", 0) < 0.6:
            continue
        x, y, w, h = box
        center = int(y + h / 2)
        if not (560 <= center <= 980):
            continue
        text = normalize_label(row.text)
        if x >= 320 and re.fullmatch(r"p(?:[il1]v?|v)\d{1,3}", text):
            if not any(abs(center - old) < 60 for old in levels):
                levels.append(center)
        elif x < 320 and is_duplicate(row.text):
            badges.append(center)
    # An unreadable badge does not disable the usual first choice. Do not
    # invent additional rows, or reuse the initial anchor after scrolling.
    if first_page and not any(y < 740 for y in levels):
        levels.append(620)
    for y in badges:
        if not any(abs(y - old) <= 90 for old in levels):
            levels.append(y)
    def identity(y):
        names = [normalize_label(row.text) for row in rows
                 if getattr(row, "box", None) and row.box[0] < 320
                 and y - 90 <= row.box[1] < y - 10 and not is_duplicate(row.text)]
        # Many different owners share the same P level. A level alone must
        # never blacklist all those owners after one duplicate dialog.
        if not names:
            return ()
        return tuple(sorted(
            normalize_label(row.text) for row in rows
            if getattr(row, "box", None) and y - 90 <= row.box[1] <= y + 60
            and row.box[0] < 550 and normalize_label(row.text)
        ))
    return [Candidate(y, any(abs(y - badge) <= 90 for badge in badges), identity(y)) for y in sorted(levels)]


@AgentServer.custom_action("ProduceChooseRentalSupportAuto")
class ProduceChooseRentalSupportAuto(CustomAction):
    """One OCR per visible page; rejection only advances within that page."""

    def _capture(self, context):
        return context.tasker.controller.post_screencap().wait().get()

    @staticmethod
    def _hit(context, node, image):
        result = context.run_recognition(node, image)
        return bool(result and result.hit)

    def _cancel(self, context):
        # Do not run the old Cancel node: its next chain backs out of the list
        # and eventually selects the same first entry again.
        result = context.run_task("ProduceSupportDismissDuplicate")
        if not result or not result.status.succeeded:
            return False
        for _ in range(12):
            if context.tasker.stopping:
                return False
            image = self._capture(context)
            if _detect_disconnect_screen(context, image):
                return False
            if not self._hit(context, "ProduceChooseSupportReplace", image):
                return True
            time.sleep(0.15)
        return False

    def run(self, context, argv):
        deadline = time.monotonic() + 60
        first_page = True
        previous = None
        unchanged = 0
        rejected_identities = set()
        for page in range(12):
            if context.tasker.stopping or time.monotonic() >= deadline:
                return False
            image = self._capture(context)
            if _detect_disconnect_screen(context, image):
                return False
            if self._hit(context, "ProduceChooseSupportReplace", image):
                if not self._cancel(context):
                    return False
                image = self._capture(context)
            detail = context.run_recognition("ProduceSupportRentalText", image)
            rows = list(detail.all_results or []) if detail and detail.hit else []
            visible = candidates(rows, first_page=first_page)
            # Comparing only the list avoids animated backgrounds and the
            # selected-card detail panel hiding the end of scrolling.
            crop = image[570:990, 35:600].astype("int16")
            if previous is not None and previous.shape == crop.shape and np.mean(np.abs(crop - previous)) < 2:
                unchanged += 1
            else:
                unchanged = 0
            if unchanged >= 2:
                logger.error("租借列表已到底，未找到不重复的支援卡")
                return False
            previous = crop
            for candidate in visible:
                if candidate.duplicate or (candidate.identity and candidate.identity in rejected_identities):
                    continue
                if context.tasker.stopping or time.monotonic() >= deadline:
                    return False
                if not context.tasker.controller.post_click(420, candidate.y).wait().succeeded:
                    return False
                # Never select a second candidate until the first response is
                # known. In particular, never click through an unknown modal.
                rejected = False
                ready_count = 0
                for _ in range(24):
                    if context.tasker.stopping or time.monotonic() >= deadline:
                        return False
                    time.sleep(0.15)
                    image = self._capture(context)
                    if _detect_disconnect_screen(context, image):
                        return False
                    if self._hit(context, "ProduceChooseSupportReplace", image):
                        if not self._cancel(context):
                            return False
                        rejected = True
                        if candidate.identity:
                            rejected_identities.add(candidate.identity)
                        break
                    if self._hit(context, "ProduceDecide", image) or self._hit(context, "ProduceChooseSupportNext", image):
                        ready_count += 1
                        if ready_count >= 2:
                            logger.info("已选择不重复的租借支援卡")
                            return True
                    else:
                        ready_count = 0
                if not rejected:
                    logger.error("租借支援卡选择后未确认结果，保留当前页面")
                    return False
            # All visible candidates are marked or have raised the exact
            # duplicate dialog. Scroll only now, inside the rental list.
            if context.tasker.stopping:
                return False
            if not context.tasker.controller.post_swipe(600, 960, 600, 620, 350).wait().succeeded:
                return False
            time.sleep(0.3)
            first_page = False
        logger.error("未找到不重复的租借支援卡，请检查租借列表")
        return False

"""Named hold selection with bounded scans and observed multi-selection counts."""

import re
import time
import unicodedata

from .battle_drinks import BattleDrinkSession
from .battle_move import MovePicker, is_move_page, move_button, selected_title, tiles
from .card_rules import name_key
from .deck_scan import deck_card_disabled, same_deck_view
from .frame_geometry import reference_frame
from .removal import selected_card_box_matches
from .shop import ocr

GREEN_TEA = "ほっと緑茶"


def hold_applies(preset, drink_source=None):
    if not preset.get("hold", {}).get("cards"):
        return False
    if drink_source is not None:
        return drink_source == GREEN_TEA
    return preset.get("career") in ("全力", "强气")


def selection_count(context, frame):
    """The two-card move button exposes n/2 even before it is enabled."""
    values = set()
    for row in ocr(context, frame, [0, 1100, 720, 180], [r"[0-2０-２]\s*[/／]\s*[2２]"]):
        value = unicodedata.normalize("NFKC", row.text).strip()
        match = re.fullmatch(r"(?:(?:移動|移动)\s*)?([0-2])\s*/\s*2", value)
        if row.score >= 0.90 and match:
            values.add(int(match[1]))
    return values.pop() if len(values) == 1 else None


def is_hold_page(context, frame):
    if is_move_page(context, frame):
        return True
    if not reference_frame(frame):
        return False
    detail = context.run_recognition("ProduceRecognitionChooseMoveCards", frame)
    return bool(detail and detail.hit and move_button(context, frame) is not None)


class HoldPicker(MovePicker):
    """Reuse exact tile recognition and scrolling from the transfer picker."""

    def __init__(self, context, preset, remaining=None, *, source=None):
        super().__init__(context, preset, remaining, label="保留")
        self.source = source
        self.targets = [name_key(card) for card in preset["hold"]["cards"]]
        self.ordered = preset["hold"].get("ordered", False)
        self.multi = False
        self.selected_count = 0

    def is_page(self, frame):
        return is_hold_page(self.context, frame)

    def observe(self, box):
        frame = self.capture()
        if not is_hold_page(self.context, frame) or deck_card_disabled(frame, box):
            return None
        # On the two-card page a second click selects the focused card. A
        # scan must never toggle it merely because adjacent pages overlap.
        if not selected_card_box_matches(frame, box):
            self.click(box)
        previous = None
        for _ in range(5):
            time.sleep(0.15)
            frame = self.capture()
            if not is_hold_page(self.context, frame):
                raise RuntimeError("保留选牌页面已改变")
            if self.multi and selection_count(self.context, frame) != self.selected_count:
                raise RuntimeError("保留选中数量发生变化，请核对当前选牌")
            name = selected_title(self.context, frame) if selected_card_box_matches(frame, box) else None
            if name and name == previous:
                return name
            previous = name
        return None

    def scan(self, wanted=None):
        found = []
        for page in range(self.MAX_PAGES):
            frame = self.capture()
            if not is_hold_page(self.context, frame):
                raise RuntimeError("保留选牌页面已改变")
            boxes = tiles(self.context, frame)
            for box in boxes:
                name = self.observe(box)
                if not name:
                    continue
                if wanted is not None and name_key(name) == name_key(wanted):
                    return name, box
                if wanted is None and all(name_key(name) != name_key(old) for old in found):
                    found.append(name)
            before = self.capture()
            self.scroll()
            after = self.capture()
            if same_deck_view(before, after, boxes, tiles(self.context, after), top=390):
                return found if wanted is None else None
        raise RuntimeError("保留选牌未确认列表底部，请手动选择后继续")

    def rewind(self):
        for _ in range(self.MAX_PAGES + 1):
            before = self.capture()
            boxes = tiles(self.context, before)
            self.scroll(False)
            after = self.capture()
            if same_deck_view(before, after, boxes, tiles(self.context, after), top=390):
                return
        raise RuntimeError("保留选牌未确认列表顶部")

    def select_multi(self, name, box):
        frame = self.capture()
        if (
            not is_hold_page(self.context, frame)
            or selected_title(self.context, frame) != name
            or not selected_card_box_matches(frame, box)
            or selection_count(self.context, frame) != self.selected_count
        ):
            raise RuntimeError("保留选牌身份或数量未确认")
        self.click(box)
        expected = self.selected_count + 1
        stable = 0
        for _ in range(8):
            time.sleep(0.15)
            frame = self.capture()
            if not is_hold_page(self.context, frame):
                raise RuntimeError("保留选牌确认前页面已改变")
            count = selection_count(self.context, frame)
            if count not in (None, self.selected_count, expected):
                raise RuntimeError("保留选中数量异常")
            stable = stable + 1 if count == expected else 0
            if stable == 2:
                self.selected_count = expected
                return
        raise RuntimeError("保留卡牌未确认选中，请核对当前选牌")

    def finish(self):
        frame = self.capture()
        button = move_button(self.context, frame)
        if (
            not is_hold_page(self.context, frame)
            or selection_count(self.context, frame) != self.selected_count
            or self.selected_count == 0
            or button is None
            or not BattleDrinkSession.use_enabled(frame, button)
        ):
            raise RuntimeError("保留移动按钮尚未就绪")
        self.click(button)
        ready = 0
        for _ in range(20):
            time.sleep(0.15)
            frame = self.capture()
            battle = self.context.run_recognition("ProduceRecognitionSkipRound", frame)
            ready = ready + 1 if not is_hold_page(self.context, frame) and battle and battle.hit else 0
            if ready >= 2:
                return
        raise RuntimeError("保留提交后尚未恢复出牌，请核对当前页面")

    def run(self):
        previous = object()
        known_single_source = bool(self.source) and name_key(self.source) != "本領発揮"
        for _ in range(8):
            frame = self.capture()
            if not is_hold_page(self.context, frame):
                raise RuntimeError("保留选牌页未确认")
            count = selection_count(self.context, frame)
            if count not in (None, 0):
                raise RuntimeError("保留页已有选中卡牌，请完成当前选择后继续")
            if count == previous and (count is not None or known_single_source):
                self.multi = count is not None
                break
            previous = count
            time.sleep(0.15)
        else:
            raise RuntimeError("保留选牌数量未确认，请手动选择后继续")
        self.rewind()
        found = self.scan()
        matches = [name for name in found if name_key(name) in self.targets]
        if self.ordered:
            matches.sort(key=lambda name: self.targets.index(name_key(name)))
        if not matches:
            # An exact named fallback is useful for a one-card transfer. On
            # a two-card page, leave the user's selection for manual review.
            if self.multi or not found:
                raise RuntimeError("保留名单中的卡牌未找到，请手动选择后继续")
            matches = found[:1]
        chosen = matches[:2 if self.multi else 1]
        for name in chosen:
            self.rewind()
            located = self.scan(name)
            if located is None:
                raise RuntimeError("保留候选未能重新定位")
            actual, box = located
            if not self.multi:
                return self.commit(actual, box)
            self.select_multi(actual, box)
        self.finish()
        return chosen

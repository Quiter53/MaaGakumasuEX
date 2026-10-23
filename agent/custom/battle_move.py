"""Bounded black-vinegar transfer, with exact selected-title confirmation."""
import time
import numpy as np

from utils import logger
from .battle_drinks import CardNameReader, BattleDrinkSession
from .card_rules import name_key, sort_key
from .card_visual import _load_card_catalog
from .deck_scan import normalize_deck_boxes, deck_card_disabled, same_deck_view
from .removal import selected_card_box_matches
from .shop import ocr
from .frame_geometry import reference_frame


class MoveDisconnected(RuntimeError):
    """Return control to the existing battle reconnect flow."""


def move_button(context, image):
    rows = ocr(context, image, [200, 1100, 330, 140], [r'^移動$', r'^移动$'])
    hits = [r for r in rows if r.score >= .90]
    return list(hits[0].box) if len(hits) == 1 else None


def is_move_page(context, image):
    # Opaque detail panel is absent during ordinary battle. Avoid OCR in the
    # hot waiting loop unless a modal can actually be present.
    if not reference_frame(image) or (image[130:350, 40:680, :3].min(axis=2) > 180).mean() < .60:
        return False
    rows = ocr(context, image, [0, 20, 260, 85], [r'^移動$', r'^移动$'])
    return any(r.score >= .90 for r in rows) and move_button(context, image) is not None


def tiles(context, image):
    detail = context.run_recognition('ProduceRecognitionCards', image)
    boxes = [list(r.box) for r in detail.all_results or []
             if r.label in ('cards', 'suggestions', 'useless')] if detail and detail.hit else []
    found = normalize_deck_boxes(image, boxes, top=380, bottom=1090)
    # The existing card model misses the left tile in the supplied screenshot.
    # A detected row anchors the four-column grid, but each additional tile
    # requires its own four straight frame edges; empty grid cells stay empty.
    result = []
    for anchor in found:
        origin = anchor[0] - round((anchor[0]-82)/146)*146
        for column in range(4):
            box = square_outline(image, origin+column*146, anchor[1])
            if box and not any(abs(box[0]-b[0])<20 and abs(box[1]-b[1])<20 for b in result):
                result.append(box)
    return sorted(result, key=lambda b:(b[1]//30, b[0]))


def square_outline(image, x, y):
    x, y = int(x), int(y)
    if x < 50 or x+140 > 700 or y < 375 or y+140 > 1100:
        return None
    pixels = image[:,:,:3].astype(np.int16)
    def edges(vertical, expected):
        low, high = expected-18, expected+19
        if vertical:
            strip = pixels[y+25:y+95,low-1:high]
            scores = (np.abs(np.diff(strip,axis=1)).max(axis=2)>22).mean(axis=0)
        else:
            strip = pixels[low-1:high,x+25:x+95]
            scores = (np.abs(np.diff(strip,axis=0)).max(axis=2)>22).mean(axis=1)
        return [(low+i,float(s)) for i,s in enumerate(scores) if s>=.65]
    def pair(first,second):
        pairs=[(a,b,sa+sb) for a,sa in first for b,sb in second if 108<=b-a<=126]
        return max(pairs,key=lambda p:p[2])[:2] if pairs else None
    horizontal=pair(edges(True,x),edges(True,x+118))
    vertical=pair(edges(False,y),edges(False,y+118))
    if horizontal and vertical:
        left,right=horizontal;top,bottom=vertical
        if abs((right-left)-(bottom-top))<=8:
            return [left,top,right-left,bottom-top]
    return None


def selected_title(context, image):
    rows = ocr(context, image, [185, 100, 510, 88], [r'.*'])
    names = {CardNameReader._canonical(r.text) for r in rows if r.score >= .93}
    names.discard(None)
    return names.pop() if len(names) == 1 else None


class MovePicker:
    MAX_PAGES = 8
    MAX_SECONDS = 65

    def __init__(self, context, preset, remaining, target=None):
        self.context, self.preset = context, preset
        self.remaining, self.target = remaining, target
        self.deadline = time.monotonic() + self.MAX_SECONDS

    def capture(self):
        if self.context.tasker.stopping:
            raise InterruptedError('已停止取牌')
        if time.monotonic() >= self.deadline:
            raise RuntimeError('黑醋取牌超时，保留移动页，请手动选牌后继续')
        frame = self.context.tasker.controller.post_screencap().wait().get()
        from .produce.shared import _detect_disconnect_screen
        if _detect_disconnect_screen(self.context, frame):
            raise MoveDisconnected('黑醋取牌时断线，交回全局恢复流程')
        return frame

    def click(self, box):
        x, y, w, h = map(int, box)
        self.context.tasker.controller.post_click(x+w//2, y+h//2).wait()

    def observe(self, box):
        # Selection is reversible. Submission needs two fresh exact titles
        # and the orange brackets on this physical tile.
        frame = self.capture()
        if not is_move_page(self.context, frame) or deck_card_disabled(frame, box):
            return None
        self.click(box)
        previous = None
        for _ in range(5):
            time.sleep(.15)
            frame = self.capture()
            if not is_move_page(self.context, frame):
                raise RuntimeError('黑醋取牌页面改变，停止旧坐标操作')
            name = selected_title(self.context, frame) if selected_card_box_matches(frame, box) else None
            if name and name == previous:
                return name
            previous = name
        return None

    def commit(self, name, box):
        frame = self.capture()
        button = move_button(self.context, frame)
        if (not is_move_page(self.context, frame) or selected_title(self.context, frame) != name
                or not selected_card_box_matches(frame, box) or not button
                or not BattleDrinkSession.use_enabled(frame, button)):
            raise RuntimeError('黑醋移动确认未就绪，保留现场')
        self.click(button)
        last_click = time.monotonic()
        clicks = 1
        # Never click a second card or consume a second bottle while waiting.
        for _ in range(20):
            time.sleep(.15)
            frame = self.capture()
            if is_move_page(self.context, frame):
                button = move_button(self.context, frame)
                if (clicks < 3 and time.monotonic()-last_click >= .75 and button
                        and selected_title(self.context, frame) == name
                        and selected_card_box_matches(frame, box)
                        and BattleDrinkSession.use_enabled(frame, button)):
                    self.click(button)
                    clicks += 1
                    last_click = time.monotonic()
            else:
                ready = self.context.run_recognition('ProduceRecognitionSkipRound', frame)
                if ready and ready.hit:
                    logger.success(f'黑醋已移动卡牌：{name}')
                    return name
        raise RuntimeError('黑醋移动后未恢复手牌，保留现场，请检查后继续')

    def scroll(self, forward=True):
        start, end = (1010, 572) if forward else (572, 1010)
        self.context.tasker.controller.post_swipe(645, start, 645, end, 350).wait()
        time.sleep(.25)

    def run(self):
        # Initial modal entrance may take longer than one screenshot.
        for _ in range(15):
            frame = self.capture()
            if is_move_page(self.context, frame):
                break
            time.sleep(.15)
        else:
            raise RuntimeError('黑醋使用后未确认移动选牌页')
        catalog = _load_card_catalog()
        best = None
        pages = 0
        for page in range(self.MAX_PAGES):
            frame = self.capture()
            if not is_move_page(self.context, frame):
                raise RuntimeError('黑醋选牌页已变化')
            boxes = tiles(self.context, frame)
            for box in boxes:
                name = self.observe(box)
                if not name:
                    continue
                if self.target and name_key(name) == name_key(self.target):
                    return self.commit(name, box)
                info = catalog.get(name_key(name))
                if info:
                    rank = sort_key(self.preset, name, info['rarity'], info['extra'], self.remaining)
                    if best is None or rank > best[0]:
                        best = (rank, name)
            before = self.capture()
            self.scroll()
            pages += 1
            after = self.capture()
            if same_deck_view(before, after, boxes, tiles(self.context, after), top=390):
                break
        if best is None:
            raise RuntimeError('黑醋移动页没有可确认的卡牌，请手动选择后继续')
        logger.warning(f'黑醋目标未找到，按规则改选：{best[1]}')
        # Scroll back, then freshly locate the fallback; never reuse old-page
        # coordinates, and never rerun indefinitely if the card cannot be found.
        for _ in range(pages):
            self.capture()
            self.scroll(False)
        for page in range(self.MAX_PAGES):
            frame = self.capture()
            if not is_move_page(self.context, frame):
                raise RuntimeError('黑醋回找候选时页面改变')
            boxes = tiles(self.context, frame)
            for box in boxes:
                name = self.observe(box)
                if name == best[1]:
                    return self.commit(name, box)
            before = self.capture()
            self.scroll()
            after = self.capture()
            if same_deck_view(before, after, boxes, tiles(self.context, after), top=390):
                break
        raise RuntimeError('黑醋备选牌未能重新定位，请手动选择后继续')

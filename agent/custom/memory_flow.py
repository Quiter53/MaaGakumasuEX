"""One resumable settlement state machine for card and deck farming."""
from .frame_geometry import reference_frame
import time
import re
from dataclasses import dataclass

from utils import logger
from . import memory_modes as m


@dataclass(frozen=True)
class Decision:
    slots: frozenset = frozenset()
    matched: bool = False
    priority: tuple = (4, 0, 0)  # An unreadable candidate must not be discarded.
    unknown: bool = True


class MemoryFlow:
    def __init__(self):
        self.evaluated = {}
        self.attempts = {}
        self.rewind = True
        self.chosen = None
        self.rerolls = 0
        self.no_tickets = False
        self.pending = None
        self.leaving = False
        self.revealing = False

    def click(self, reader, box, signature):
        m.click(reader, box)
        self.pending = (signature, time.monotonic())

    def inspect(self, reader, kind, marker):
        from .memory_farming import evaluate, read_diamond
        cards, has_deck = reader.scan(m.SESSION.mode)
        if m.SESSION.mode == 'deck' and not has_deck:
            logger.info('本轮回忆无卡组，保存本轮回忆后继续培育，不计目标达标')
            return Decision(priority=(1, 0, 0), unknown=False)
        for target in m.SESSION.targets:
            for card in cards:
                if m.normalize_card_name(card.name) == target['name'] and (
                    (target['upgrade'] != 'any' and card.upgraded is None)
                    or (target['customMin'] > 0 and card.custom is None)
                ):
                    raise ValueError('目标卡强化或绿豆未确认')
        if m.SESSION.mode == 'card':
            candidate = evaluate(cards, read_diamond(reader, kind), m.SESSION)
            decision = Decision(candidate.slots, False, candidate.priority, False)
        else:
            matched = m.satisfies(cards, m.SESSION.targets, 'deck')
            decision = Decision(matched=matched, priority=(int(matched), 0, 0), unknown=False)
        # A scan can scroll or outlive a candidate transition. Never cache its
        # result under the old candidate's key before checking the new frame.
        _, texts = reader.capture()
        if m.screen_kind(texts) != kind or (kind == 'picker' and m.candidate_marker(texts) != marker):
            raise m.MemoryTransition('读取期间候选页面发生变化')
        logger.info('回忆检查：' + ' / '.join(c.name for c in cards) + f'；优先级={decision.priority[0]}')
        return decision

    def run(self, context):
        reader = m.MemoryReader(context)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            image, texts = reader.capture()
            kind = m.screen_kind(texts)
            marker = m.candidate_marker(texts) if kind == 'picker' else None
            signature = (kind, marker)
            text = m.text_join(texts)
            # Network recovery belongs to the outer pipeline. Keep all
            # decisions, spending limits and pending saves across that yield.
            if kind is None or kind == 'detail':
                for node in ('ProduceDisconnectError', 'ProduceDisconnectTitle', 'ProduceDisconnectHome'):
                    result = context.run_recognition(node, image)
                    if result and result.hit is True:
                        return True
            if self.leaving:
                if kind is None:
                    time.sleep(.2)
                    _, verify = reader.capture()
                    if m.screen_kind(verify) is None:
                        m.SESSION.saved_result = True
                        return True  # Progress is committed only at Home.
                    continue
                if kind == 'summary':
                    box = m.button(texts, '次へ|下一步|下一页')
                    if box and (not self.pending or time.monotonic()-self.pending[1] >= 1):
                        self.click(reader, box, signature)
                    else:
                        time.sleep(.2)
                    continue
            # The upstream corner tap advances the reveal even when TAP is
            # not drawn yet. Never run it over candidate/confirmation sheets.
            if kind == 'reveal' or (kind is None and self.revealing):
                if reference_frame(image):
                    m.click(reader, [20, 20, 5, 5])
                else:
                    time.sleep(.2)
                self.revealing = True
                continue
            if self.pending and signature == self.pending[0] and time.monotonic()-self.pending[1] < 1:
                time.sleep(.15)
                continue
            if re.search('(?:チケット|再生成券|再生成票).*(?:不足|ありません|足り|不够)', text, re.S):
                box = m.button(texts, '閉じる|关闭|OK|確認|确认|キャンセル|取消', bottom=700)
                if box:
                    self.no_tickets = True
                    self.click(reader, box, signature)
                    continue
            if kind in ('summary', 'picker'):
                self.revealing = False
                # Missing candidate numbering prevents a reliable comparison;
                # keep the currently displayed candidate without claiming a hit.
                if kind == 'picker' and marker is None:
                    self.chosen = signature
                    self.evaluated[signature] = Decision()
                if kind == 'picker' and self.rewind and marker is not None:
                    arrow = m.candidate_arrow(image, 'left')
                    if arrow:
                        self.click(reader, arrow, signature)
                        continue
                    self.rewind = False
                if signature not in self.evaluated:
                    try:
                        decision = self.inspect(reader, kind, marker)
                    except m.MemoryTransition:
                        # A candidate switch invalidates this observation; it
                        # must not spend the stable-candidate unreadable budget.
                        time.sleep(.25)
                        continue
                    except (ValueError, OSError) as error:
                        self.attempts[signature] = self.attempts.get(signature, 0) + 1
                        if self.attempts[signature] < 3:
                            logger.info(f'回忆读取复核 {self.attempts[signature]}/3：{error}')
                            time.sleep(.25)
                            continue
                        # Keep the current memory, spend no more tickets, and
                        # continue the batch. Unknown never satisfies a target.
                        logger.warning(f'回忆未能完整读取：{error}；保留当前候选并自动继续，不计达标')
                        decision = Decision()
                        if kind == 'picker':
                            self.chosen = signature
                    self.evaluated[signature] = decision
                    m.SESSION.result_seen = True
                    # The reader may scroll. Button coordinates must come from
                    # a fresh frame, never the pre-scan screen.
                    image, texts = reader.capture()
                    if m.screen_kind(texts) != kind or (kind == 'picker' and m.candidate_marker(texts) != marker):
                        self.evaluated.pop(signature, None)
                        if self.chosen == signature:
                            self.chosen = None
                        continue
                decision = self.evaluated[signature]
                if kind == 'picker':
                    if self.chosen is None:
                        arrow = m.candidate_arrow(image, 'right')
                        if arrow:
                            self.click(reader, arrow, signature)
                            continue
                        self.chosen = max((k for k in self.evaluated if k[0] == 'picker'), key=lambda k: self.evaluated[k].priority)
                    if signature != self.chosen:
                        arrow = m.candidate_arrow(image, 'left')
                        if arrow:
                            self.click(reader, arrow, signature)
                            continue
                        # Re-evaluate navigation after a lost/disabled arrow.
                        self.chosen = None
                        self.rewind = True
                        time.sleep(.25)
                        continue
                    box = m.button(texts, '決定|确定|決定する')
                else:
                    reroll = m.button(texts, '再生成')
                    keep = (decision.unknown or decision.priority[0] > 0 or self.no_tickets
                            or not m.SESSION.reroll or self.rerolls >= 20 or not reroll)
                    if keep:
                        m.SESSION.pending_targets = set(decision.slots)
                        m.SESSION.matched = decision.matched
                        box = m.button(texts, '次へ|下一步|下一页')
                        if box:
                            self.leaving = True
                    else:
                        box = reroll
                        self.rerolls += 1
                        self.evaluated.clear()
                        self.attempts.clear()
                        self.chosen = None
                        self.rewind = True
            elif kind == 'confirm':
                if self.chosen is None:
                    box = m.button(texts, 'キャンセル|取消')
                    self.rewind = True
                else:
                    box = m.button(texts, '生成する|生成|确定')
                    if box:
                        self.evaluated = {k: v for k, v in self.evaluated.items() if k[0] == 'picker'}
                        self.revealing = True
            elif kind == 'reroll_confirm':
                allowed = (m.SESSION.reroll and not self.no_tickets
                           and not re.search('ジュエル|購入|购买|充值', text)
                           and re.search('チケット|再生成券|再生成票', text))
                if allowed:
                    box = m.button(texts, '再生成|再生成する|使用する|使用|生成する')
                    self.revealing = bool(box)
                else:
                    box = m.button(texts, 'キャンセル|取消|閉じる|关闭', bottom=700)
                    self.no_tickets = True
            elif kind == 'generation':
                box = m.button(texts, '生成', bottom=700)
                self.revealing = bool(box)
            else:
                # Yield unknown non-animation scenes to the outer dispatcher.
                # A transient footer fade can look like a saved-detail page.
                return True
            if box:
                self.click(reader, box, signature)
            else:
                time.sleep(.25)
        return True


def handle(context):
    if m.SESSION.flow is None:
        m.SESSION.flow = MemoryFlow()
    return m.SESSION.flow.run(context)

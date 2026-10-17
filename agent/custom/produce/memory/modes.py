"""Task-local memory farming. Read a whole result before allowing any exit.

Card identity, enhancement and custom count are independent observations.
HIF ability artwork never belongs to the memory deck. Unknown is not a miss.
"""
import re
import json
import time
from pathlib import Path
from functools import lru_cache
from dataclasses import asdict, dataclass

import numpy as np
from utils import logger
from custom.params import action_params
from custom.card_visual import match_card_catalog, normalize_card_name
from custom.square_identity import match as match_square
from custom.frame_geometry import reference_frame


class MemoryTransition(ValueError):
    """The page changed while reading; return to the settlement dispatcher."""


@dataclass(frozen=True)
class Card:
    name: str
    upgraded: bool | None
    custom: int | None
    origin: str = 'none'


@lru_cache(maxsize=1)
def catalog():
    path=Path(__file__).resolve().parents[4]/'config/card_library.json'
    return {normalize_card_name(c['name']):c for c in json.loads(path.read_text(encoding='utf-8'))['cards']}


def custom_limit(name):
    card=catalog().get(normalize_card_name(name),{})
    return card.get('customLimit',{'R':2,'SR':2,'SSR':1}.get(card.get('rarity'),0))


@lru_cache(maxsize=1)
def plus_templates():
    with np.load(Path(__file__).with_name('memory_badges.npz')) as data:
        return [data[key] for key in data.files]


def parse_targets(raw, mode):
    if isinstance(raw, str):
        raw = json.loads(raw)
    if not isinstance(raw, list) or not raw or len(raw) > (6 if mode == 'deck' else 64):
        raise ValueError('请至少选择一张目标卡；卡组最多六张，回忆目标最多64张')
    result = []
    seen = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError('目标卡配置格式错误')
        name = normalize_card_name(item.get('name', ''))
        slot, count = item.get('slot'), item.get('customMin', 0)
        upgrade = item.get('upgrade', 'upgraded')
        if (not name or type(slot) is not int or not 0 <= slot < (6 if mode == 'deck' else 64)
                or slot in seen or name not in catalog() or type(count) is not int or not 0 <= count <= custom_limit(name)
                or upgrade not in ('any', 'base', 'upgraded')):
            raise ValueError('目标卡名称、格子、强化状态或绿豆数量无效')
        seen.add(slot)
        origin=catalog()[name].get('origin','none')
        required=('idol' if slot==0 else 'support' if slot==1 else 'none') if mode=='deck' else 'none'
        if origin != required:
            raise ValueError('目标卡来源与卡组格子不一致')
        if required=='none' and catalog()[name]['rarity']=='LR':
            raise ValueError('LR 无法作为回忆获得卡或普通卡组目标，请重新选择')
        result.append(dict(name=name, slot=slot, customMin=count, upgrade=upgrade))
    if mode=='card' and len({(t['name'],t['upgrade'],t['customMin']) for t in result})!=len(result):
        raise ValueError('回忆目标条件重复，请移除重复项')
    return result


def card_satisfies(card, target, mode):
    origin = 'idol' if target['slot'] == 0 else 'support' if target['slot'] == 1 else 'none'
    if normalize_card_name(card.name) != target['name'] or (mode == 'deck' and card.origin != origin):
        return False
    upgrade = target['upgrade']
    if upgrade != 'any' and card.upgraded is not (upgrade == 'upgraded'):
        return False
    return target['customMin'] == 0 or (card.custom is not None and card.custom >= target['customMin'])


def satisfies(cards, targets, mode):
    """One-to-one multiset matching: a physical card cannot satisfy two slots."""
    if mode=='card':return any(card_satisfies(card,target,mode) for card in cards for target in targets)
    def visit(index, used):
        return index == len(targets) or any(
            visit(index + 1, used | {i}) for i, card in enumerate(cards)
            if i not in used and card_satisfies(card, targets[index], mode))
    return bool(targets) and visit(0, set())


def ocr(context, image, roi=None, expected=None):
    param = {'roi': roi or [0, 0, image.shape[1], image.shape[0]]}
    if expected is not None:
        param['expected'] = expected
    detail = context.run_recognition('ProduceMemoryOCR', image, {'ProduceMemoryOCR': {'recognition': {'type': 'OCR', 'param': param}}})
    return detail.raw_detail.get('all', []) if detail else []


def text_join(texts):
    return '\n'.join(t.get('text', '') for t in texts)


def has_memory_title(texts):
    return any(s in text_join(texts) for s in (
        'メモリー変換', '獲得するメモリー', 'メモリー確定確認', 'メモリー再生成',
        '回忆转换', '选择要获得的回忆', '回忆确定确认', '回忆再生成'))


def screen_title_regions(image):
    """Locate broad title panels without running OCR on ordinary battle frames."""
    if not reference_frame(image):
        return []

    def neutral(roi):
        x, y, w, h = roi
        a = image[y:y+h:4, x:x+w:4, :3].astype(np.int16)
        return float(((a.max(2)-a.min(2) < 20) & (a.mean(2) > 170)).mean())

    regions = []
    if neutral([0, 410, 720, 65]) > .75:
        regions.append([0, 390, image.shape[1], 100])
    if neutral([60, 30, 600, 115]) > .75:
        regions.append([60, 30, 600, 115])
    a = image[875:925:4, 60:620:4, :3].astype(np.int16)
    if ((a[:, :, 2] > 220) & (a[:, :, 1] > 100) & (a[:, :, 1] < 220) & (a[:, :, 0] < 150)).mean() > .4:
        regions.append([0, 830, image.shape[1], 180])
    return regions


def screen_ocr(context, image, title=None):
    """Read page titles locally; full-frame detection downsamples small text.

    Reuse only observations of this image, never a previous screen's title.
    Read controls at their own scale as well; a title alone is not a button.
    """
    texts = ocr(context, image)
    regions = [title[0]] if title is not None else screen_title_regions(image)
    panel = False
    def replace_region(roi, rows):
        x, y, w, h = roi
        def inside(row):
            rx, ry, rw, rh = row['box']
            return x <= rx+rw/2 < x+w and y <= ry+rh/2 < y+h
        return [row for row in texts if not inside(row)] + list(rows)

    for roi in regions:
        rows = title[1] if title is not None else ocr(context, image, roi)
        if not has_memory_title(rows):
            continue
        texts = replace_region(roi, rows)
        panel = True
    if panel:
        # Whole-frame OCR remains useful for content. Page tabs and controls
        # need local detection; full-frame downsampling must not decide absence.
        text = text_join(texts)
        if 'メモリー変換' in text or '回忆转换' in text:
            controls = [[40, 580, 640, 85], [0, 1060, image.shape[1], min(190, image.shape[0]-1060)]]
        elif '獲得するメモリー' in text or '选择要获得的回忆' in text:
            controls = [[40, 145, 640, 85], [0, 960, image.shape[1], min(290, image.shape[0]-960)]]
        else:
            controls = [[0, 1010, image.shape[1], min(240, image.shape[0]-1010)]]
        for roi in controls:
            if roi[3] > 0:
                rows = ocr(context, image, roi)
                if rows:
                    texts = replace_region(roi, rows)
    return texts


def settlement_destination(context, image, *, leaving=False):
    """Positive destination evidence; an empty OCR result proves nothing."""
    if not reference_frame(image):
        return None
    nodes = ('ProduceBackHome', 'ProduceFinished')
    if leaving:
        nodes += ('ProduceFollow', 'ProduceSkipChatFlag')
    for node in nodes:
        result = context.run_recognition(node, image)
        if result and result.hit is True:
            return node
    return None


def screen_kind(texts):
    text = text_join(texts)
    if 'メモリー確定確認' in text or '回忆确定确认' in text:
        return 'confirm'
    if ('メモリー再生成' in text or '回忆再生成' in text) and ('キャンセル' in text or '取消' in text):
        return 'reroll_confirm'
    if ('獲得するメモリー' in text and '選択' in text) or '选择要获得的回忆' in text:
        return 'picker'
    if re.search(r'MEMORY',text,re.I) and any(t.get('text','').strip()=='生成' for t in texts):
        return 'generation'
    if (any(t.get('text','').strip().upper()=='TAP' and t['box'][1]>850 for t in texts)
            and re.search(r'H\.?I\.?F|アビリティ|スキルカード|メモリー|MEMORY|パラメータ|獲得',text,re.I)):
        return 'reveal'
    if ('メモリー変換' in text or '回忆转换' in text) and ('プロデュース' in text or '培育' in text):
        return 'summary' if button(texts,'次へ|下一步|下一页') or button(texts,'再生成') else 'detail'
    return None


def square_boxes(image, top=220, bottom=1120):
    """Closed neutral outlines locate square thumbnails without a battle model."""
    a = image[top:bottom:2, ::2, :3].astype(np.int16)
    mask = (a.max(2)-a.min(2) < 40) & (a.max(2) < 180) & (a.mean(2) > 35)
    height, width = mask.shape
    visited = np.zeros_like(mask)
    boxes = []
    for sy, sx in zip(*np.nonzero(mask)):
        if visited[sy, sx]:
            continue
        pending = [(int(sx), int(sy))]; visited[sy, sx] = True
        x0 = x1 = int(sx); y0 = y1 = int(sy); count = 0
        while pending:
            x, y = pending.pop(); count += 1
            x0=min(x0,x);x1=max(x1,x);y0=min(y0,y);y1=max(y1,y)
            for xx, yy in ((x-1,y),(x+1,y),(x,y-1),(x,y+1),(x-1,y-1),(x-1,y+1),(x+1,y-1),(x+1,y+1)):
                if 0 <= xx < width and 0 <= yy < height and mask[yy,xx] and not visited[yy,xx]:
                    visited[yy,xx]=True;pending.append((xx,yy))
        w,h=(x1-x0+1)*2,(y1-y0+1)*2
        if 76 <= w <= 175 and .90 <= w/h <= 1.10 and count >= (w+h)*.5:
            boxes.append([x0*2,top+y0*2,w,h])
    return sorted(boxes,key=lambda b:(round(b[1]/20),b[0]))


def badge_state(context, image, box, title=None):
    x,y,w,h=map(int,box)
    if min(w,h)<76 or y+h>image.shape[0] or x+w>image.shape[1]:
        return None,None
    # Upgrade cross is red/orange and white, in the middle of the right edge.
    from custom.card_visual import _resize_rgb_bilinear
    a=(_resize_rgb_bilinear(image[y:y+h,x:x+w,:3],144)*255).astype(np.int16)[38:99,104:144]
    error=255.
    for ref in plus_templates():
        size=ref.shape[0];mask=ref[:,:,3]>240
        windows=np.lib.stride_tricks.sliding_window_view(a,(size,size),axis=(0,1)).transpose(0,1,3,4,2)
        diff=np.abs(windows-ref[None,None,:,:,:3].astype(np.int16)).mean(axis=-1)
        error=min(error,float(diff[:,:,mask].mean(axis=-1).min()))
    upgraded=True if error<30 else False if error>48 else None
    if title is not None:
        title_plus=bool(re.search(r'[+＋]\s*$',title))
        if upgraded is not title_plus:
            return None,None
    # The custom count is an emerald CIRCLE near the lower centre. The
    # lower-right yellow/green HEART is stamina cost and must never be read.
    bx,by=x+int(.34*w),y+int(.63*h)
    br,bb=x+int(.65*w),y+int(.95*h)
    a=image[by:bb,bx:br,:3].astype(np.int16)
    green=(a[:,:,1]>130)&(a[:,:,1]-a[:,:,2]>65)&(a[:,:,1]-a[:,:,0]>45)
    if green.sum()<max(8,w*h*.002):
        return upgraded,0
    rows,cols=np.nonzero(green)
    left=max(0,bx+int(cols.min())-3);upper=max(0,by+int(rows.min())-3)
    right=min(br,bx+int(cols.max())+4);lower=min(bb,by+int(rows.max())+4)
    small=image[upper:lower,left:right]
    scaled=np.repeat(np.repeat(small,4,axis=0),4,axis=1)
    digits=[]
    for item in ocr(context,scaled):
        text=item.get('text','').strip()
        if re.fullmatch(r'[1-9]',text):digits.append(int(text))
    return upgraded,digits[0] if len(set(digits))==1 else None


def read_card(context, image, box, title=None):
    art=match_square(image,box)
    if art is None:
        return None
    if title is not None:
        exact=match_card_catalog(title)
        if exact is None or normalize_card_name(exact.matched_name)!=normalize_card_name(art['name']) or exact.confidence<.9:
            return None
    enhanced,custom=badge_state(context,image,box,title)
    return Card(art['name'],enhanced,custom)


class MemoryReader:
    def __init__(self,context):
        self.context=context;self.controller=context.tasker.controller
        path=Path(__file__).resolve().parents[4]/'config/card_library.json'
        self.origins={normalize_card_name(c['name']):c.get('origin','none') for c in json.loads(path.read_text(encoding='utf-8'))['cards']}

    def capture(self):
        if self.context.tasker.stopping:raise InterruptedError
        image=self.controller.post_screencap().wait().get()
        return image,screen_ocr(self.context,image)

    def scroll(self,up,kind):
        from custom.scroll import scroll_list
        # Only the memory content pane; header and footer stay untouched.
        top=710 if kind in ('summary','detail') else 290
        if not scroll_list(self.context, (600,top+350 if up else top+40), (600,top+40 if up else top+350)):
            raise MemoryTransition('回忆列表滑动未完成，重新确认页面')
        time.sleep(.25)

    def primary(self,image,texts):
        for heading in texts:
            if not re.search('獲得可能スキルカード|可获得技能卡',heading.get('text','')):continue
            _,hy,_,hh=heading['box']
            for title in texts:
                tx,ty,tw,th=title['box']
                if not (185<tx<260 and hy+hh<ty<hy+hh+90):continue
                # Real memory thumbnails are 144px, anchored beside the title.
                uncertain=None
                for dy in (4,6,2,0,8,-2):
                    card=read_card(self.context,image,[46,ty+dy,144,144],title['text'])
                    if card and card.upgraded is not None:return card
                    uncertain=card or uncertain
                if uncertain:return uncertain
        return None

    @staticmethod
    def primary_visible(texts):
        """A visible primary heading is not a reason to scroll on an OCR miss."""
        return any(re.search('獲得可能スキルカード|可获得技能卡',t.get('text','')) for t in texts)

    @staticmethod
    def pane_signature(image,kind):
        # Compare only the scrolling pane, excluding animated idol/background,
        # fixed footer and the scrollbar. Downsample to suppress small shimmer.
        top,bottom=(710,1100) if kind in ('summary','detail') else (290,950)
        return image[top:bottom:4,35:570:4,:3].astype(np.int16)

    def scan(self,mode):
        image,texts=self.capture();kind=screen_kind(texts)
        if kind not in ('summary','picker','detail'):raise MemoryTransition('回忆页面正在切换')
        if mode=='deck':return self.deck(image,texts,kind)
        tab=next((t['box'] for t in texts if re.fullmatch('プロデュース|培育|Produce',t['text'])),None)
        if tab:
            x,y,w,h=tab
            a=image[y:y+h,x:x+w,:3].astype(np.int16)
            if ((a[:,:,2]>200)&(a[:,:,1]<175)&(a[:,:,0]<100)).sum()<10:
                click(self,tab);image,texts=self.capture()
        # Scroll only while the primary is off screen. A visible but unreadable
        # card must be retried in place, not dragged away on each attempt.
        primary=None
        for attempt in range(5):
            primary=self.primary(image,texts)
            if primary:break
            if self.primary_visible(texts) or attempt==4:break
            before=self.pane_signature(image,kind)
            self.scroll(False,kind);image,texts=self.capture()
            if screen_kind(texts)!=kind:raise MemoryTransition('滚动时回忆页面发生切换')
            after=self.pane_signature(image,kind)
            if before.shape==after.shape and np.mean(np.abs(after-before))<.8:
                primary=self.primary(image,texts)
                break
        if not primary:
            if screen_kind(texts)!=kind:raise MemoryTransition('读取时回忆页面发生切换')
            raise ValueError('回忆获得卡尚未可靠识别')
        if mode=='card':
            time.sleep(.15)
            check_image,check_texts=self.capture()
            if self.primary(check_image,check_texts)!=primary:
                raise MemoryTransition('回忆卡两帧识别不一致，等待页面稳定')
            return [primary],True
        raise ValueError('未知回忆读取模式')

    def deck(self,image,texts,kind):
        tab=next((t['box'] for t in texts if re.fullmatch('ステージ|舞台|Stage',t['text'])),None)
        if tab is None:raise ValueError('未定位到 Stage 页签')
        x,y,w,h=tab
        background=image[y:y+h,max(0,x-24):max(0,x-8),:3]
        if background.size and float(background.mean())<215:
            return [],False  # The teacher-week Stage tab is explicitly disabled.
        region=image[y:y+h,x:x+w,:3].astype(np.int16)
        orange=((region[:,:,2]>200)&(region[:,:,1]>50)&(region[:,:,1]<175)&(region[:,:,0]<100)).sum()
        if orange<10:
            click(self,tab);image,texts=self.capture()
        last=None
        for _ in range(7):
            rows=[t for t in texts if re.fullmatch('スキルカード|技能卡|技能卡牌',t['text'])]
            top=min((t['box'][1]+t['box'][3] for t in rows),default=665 if kind=='detail' else 690 if kind=='summary' else 230)
            bottom=1160 if kind=='detail' else 1115 if kind=='summary' else 950
            boxes=[b for b in square_boxes(image,top=top,bottom=bottom) if b[2]>=125 and b[0]<680]
            # Six complete distinct positions on a single frame are required;
            # never duplicate a card merely because it reappeared after scrolling.
            boxes.sort(key=lambda b:(round(b[1]/30),b[0]))
            if len(boxes)==6:
                cards=[]
                for i,box in enumerate(boxes):
                    card=read_card(self.context,image,box)
                    if not card:raise ValueError(f'卡组第 {i+1} 张未可靠识别，保留当前回忆')
                    cards.append(Card(card.name,card.upgraded,card.custom,'idol' if i==0 else 'support' if i==1 else 'none'))
                time.sleep(.15);check,_=self.capture()
                again=[read_card(self.context,check,b) for b in boxes]
                if [(c.name,c.upgraded,c.custom) for c in cards]!=[(c.name,c.upgraded,c.custom) if c else None for c in again]:
                    raise ValueError('六张卡组两帧识别不一致')
                return cards,True
            crop=image[top:bottom]
            if last is not None and last.shape==crop.shape and np.mean(np.abs(last.astype(float)-crop))<.6:break
            last=crop.copy();self.scroll(True,kind);image,texts=self.capture()
        raise ValueError('未取得完整六张卡组，保留回忆等待复核')


class Session:
    def __init__(self):
        self.mode='normal';self.targets=[];self.reroll=False;self.matched=False;self.rounds=0;self.result_seen=False
        self.collected=set();self.pending_targets=set();self.saved_result=False;self.progress_path=None;self.flow=None
    def configure(self,params):
        self.__init__();self.mode=params.get('mode','normal')
        if self.mode not in ('normal','card','deck'):raise ValueError('未知培育模式')
        if self.mode!='normal':self.targets=parse_targets(params.get('targets','[]'),self.mode)
        self.reroll=params.get('reroll',self.mode!='normal') is True

    @property
    def enabled(self):
        return self.mode!='normal' or self.reroll


SESSION=Session()


def initialize(context):
    node=context.get_node_data('ProduceMemoryPolicy') or {}
    params=action_params(node.get('action',{}).get('param',{}).get('custom_action_param',node.get('custom_action_param',{})))
    SESSION.configure(params)
    if SESSION.mode=='card':
        from custom.produce.memory.farming import load_progress
        load_progress(SESSION)
    context.override_pipeline({'ProduceLoop':{'enabled':True},'ProduceMemoryFlag':{'enabled':SESSION.enabled}})
    if SESSION.mode!='normal':
        context.override_pipeline({'ProduceLoop':{'max_hit':4294967295},'ProduceMemoryFlag':{'enabled':True}})
        logger.info(f"培育模式：{'刷取卡牌回忆' if SESSION.mode=='card' else '刷取卡组'}；目标 {len(SESSION.targets)} 张，绿豆按下限判断；允许再生成票：{SESSION.reroll}")
    elif SESSION.reroll:
        logger.info('再生成回忆已开启：每轮最多使用一次再生成票，优先保留钻石词条、更多绿豆及更高优先级卡牌')


def go_home(context):
    # This action is reached only after the actual Home template matched.
    if SESSION.flow is not None and SESSION.flow.leaving and SESSION.result_seen:
        SESSION.saved_result = True
    if SESSION.mode=='normal':
        SESSION.result_seen=False
        SESSION.saved_result=False;SESSION.pending_targets=set();SESSION.flow=None
        logger.info('培育结算完成，已返回主页')
        return True
    SESSION.rounds+=1
    if SESSION.mode=='card' and SESSION.saved_result:
        from custom.produce.memory.farming import save_progress
        SESSION.collected.update(SESSION.pending_targets)
        SESSION.matched=len(SESSION.collected)==len(SESSION.targets)
        save_progress(SESSION)
        remaining=[t['name'] for t in SESSION.targets if t['slot'] not in SESSION.collected]
        logger.info(f'回忆收集进度: {len(SESSION.collected)}/{len(SESSION.targets)}；剩余: '+(' / '.join(remaining) or '无'))
    if SESSION.matched and (SESSION.mode=='card' or SESSION.saved_result):
        logger.success(f'回忆目标已达标并完成结算，共检查 {SESSION.rounds} 轮；停止继续培育')
        context.override_pipeline({'ProduceLoop':{'enabled':False}})
    elif not SESSION.result_seen:
        logger.warning('本轮回忆未完整读取，不计目标达标，自动继续下一轮')
    else:logger.info(f'第 {SESSION.rounds} 轮回忆未达标，继续下一轮培育')
    SESSION.result_seen=False
    SESSION.saved_result=False;SESSION.pending_targets=set();SESSION.flow=None
    return True


def button(texts,pattern,*,bottom=900):
    hits=[t for t in texts if t['box'][1]>=bottom and re.fullmatch(pattern,t.get('text','').strip())]
    return hits[0]['box'] if len(hits)==1 else None


def click(reader,box):
    x,y,w,h=box;reader.controller.post_click(x+w//2,y+h//2).wait();time.sleep(.35)


def candidate_marker(texts):
    for t in texts:
        text=re.sub(r'\s+','',t.get('text',''))
        if re.fullmatch(r'再生成(?:前|\d+回目)',text):return text
    return None


def candidate_arrow(image,direction):
    x=190 if direction=='left' else 470
    a=image[990:1060,x:x+60,:3]
    return [x,990,60,65] if a.size and (a.min(2)>240).mean()>.5 else None


def handle_memory(context):
    from custom.produce.memory.flow import handle
    return handle(context)

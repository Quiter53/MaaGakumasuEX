"""Small-ROI battle observations and conservative, per-battle drink scheduling.

No card model replacement, no full-screen OCR, no persisted screen coordinates.
Only the chosen card is read; unknown names never trigger a named-card rule.
"""
from .frame_geometry import reference_frame, same_card_slot
import re
import time
import unicodedata

import numpy as np
from loguru import logger

from .card_visual import _load_card_catalog, normalize_card_name, intersection_over_union
from .hand_identity import HandIdentityReader
from .drink_policy import CATALOG, due, load_policy
from .shop import drink_title, ocr, owned_detail_title


def signature(image, roi):
    x, y, w, h = roi
    return image[y:y+h:3, x:x+w:3, :3].copy()


def same(a, b):
    return a is not None and a.shape == b.shape and np.abs(a.astype(np.int16)-b.astype(np.int16)).mean() < 1.0


def parse_turn(rows):
    values = []
    for row in rows:
        text = unicodedata.normalize("NFKC", row.text).strip()
        if float(row.score) >= .90 and re.fullmatch(r"[1-9][0-9]?", text):
            value = int(text)
            if value <= 99:
                values.append(value)
    return values[0] if len(values) == 1 else None


class TurnReader:
    # Actual 720x1280 battle frames: 残りターン header y=0..36,
    # digit centered at (62, 90). Excludes next-turn buffs and score multiplier.
    ROI = [20, 58, 75, 65]
    BONUS_ROI = [90, 118, 70, 45]

    def __init__(self):
        self._crop = None
        self._bonus_crop = None
        self._value = None
        self._pending = None
        self._hits = 0
        self._anchor = False
        self.calls = 0
        self.base_turns = None
        self.extra_turns = None

    @classmethod
    def _bonus_signature(cls, image):
        x,y,w,h=cls.BONUS_ROI
        region=image[y:y+h,x:x+w,:3].astype(np.int16)
        b,g,r=region[:,:,0],region[:,:,1],region[:,:,2]
        # Only the cyan +N glyph inside its white pill. Ignore animated stage
        # colors outside the pill and the attribute percentage above it.
        blue=(b>180)&(g>125)&(r<150)&(b-r>65)
        white=region.min(axis=2)>225
        return blue.astype(np.uint8) if blue.sum()>=12 and white.mean()>.20 else np.zeros(blue.shape,np.uint8)

    def _read_bonus(self, context, image, mask):
        if not mask.any():
            return 0
        rows=ocr(context,image,self.BONUS_ROI,[r'^[+＋]\s*[0-9０-９]{1,2}$'])
        def parse(rows):
            values=[]
            for row in rows:
                text=unicodedata.normalize('NFKC',row.text).replace(' ','')
                if float(row.score)>=.90 and re.fullmatch(r'\+[1-9][0-9]?',text):
                    values.append(int(text[1:]))
            return values[0] if len(values)==1 else None
        value=parse(rows)
        if value is not None:
            return value
        # The text detector sometimes includes the pill border (+1 scores
        # 0.77..0.89 in the reported run). Read the cyan glyphs directly at
        # the existing 0.90 gate, just as the main digit has an only_rec path.
        x,y,w,h=self.BONUS_ROI
        # Stage highlights can also be cyan. Locate the connected white pill
        # first, then bound only glyph pixels inside it.
        white=(image[y:y+h,x:x+w,:3].min(2)>225)
        visited=np.zeros_like(white)
        largest=[]
        for sy,sx in zip(*np.nonzero(white)):
            if visited[sy,sx]:
                continue
            stack=[(int(sy),int(sx))];component=[];visited[sy,sx]=True
            while stack:
                cy,cx=stack.pop();component.append((cy,cx))
                for ny,nx in ((cy-1,cx),(cy+1,cx),(cy,cx-1),(cy,cx+1)):
                    if 0<=ny<h and 0<=nx<w and white[ny,nx] and not visited[ny,nx]:
                        visited[ny,nx]=True;stack.append((ny,nx))
            if len(component)>len(largest):
                largest=component
        if len(largest)<w*h*.20:
            return None
        bounds=np.asarray(largest)
        top,left=bounds.min(0);bottom,right=bounds.max(0)+1
        glyphs=mask[top:bottom,left:right]
        if not glyphs.any():
            return None
        ys,xs=np.nonzero(glyphs)
        minx,miny=int(left+xs.min()),int(top+ys.min())
        maxx,maxy=int(left+xs.max()),int(top+ys.max())
        left,top=max(0,minx-3),max(0,miny-3)
        right,bottom=min(w,maxx+4),min(h,maxy+4)
        roi=[x+left,y+top,right-left,bottom-top]
        detail=context.run_recognition('ProduceRecognitionPoint',image,pipeline_override={
            'ProduceRecognitionPoint': {'recognition':'OCR','roi':roi,
                                       'expected':[r'^[+＋]\s*[0-9０-９]{1,2}$'],'only_rec':True}})
        return parse(list(detail.filtered_results or []) if detail and detail.hit else [])

    def read(self, context, image):
        if not reference_frame(image):
            return None
        crop = signature(image, self.ROI)
        bonus_crop=self._bonus_signature(image)
        if self._value is not None and same(self._crop, crop) and np.array_equal(self._bonus_crop,bonus_crop):
            return self._value
        # Never return an old digit once its region changed (extra turns can
        # increase remaining turns; do not assume a monotonic countdown).
        self._value = None
        self.base_turns=self.extra_turns=None
        if not self._anchor:
            headers = ocr(context, image, [0, 0, 175, 135], [r"[残曳]りター[ンシ]", r"剩余.*回合", r"剩餘.*回合"])
            self._anchor = any(float(row.score) >= .80 and int(row.box[1]) < 42 for row in headers)
            if not self._anchor:
                # Header may disappear behind stage lights. Caller has already
                # verified battle mode; require the fixed dark digit disc too.
                disc=image[55:128,20:100,:3]
                if not ((disc.max(axis=2)<155).mean()>.50 and (disc.min(axis=2)>185).mean()>.025):
                    return None
        self.calls += 1
        value = parse_turn(ocr(context, image, self.ROI, [r"^[0-9０-９]{1,2}$"]))
        if value is None:
            # The circular border can be detected as a leading 1 (9 -> 19).
            # Recognition-only reads the bounded digit crop without that box.
            detail = context.run_recognition('ProduceRecognitionPoint', image, pipeline_override={
                'ProduceRecognitionPoint': {'recognition': 'OCR', 'roi': self.ROI,
                                           'expected': [r'^[0-9０-９]{1,2}$'], 'only_rec': True}})
            rows = list(detail.filtered_results or []) if detail and detail.hit else []
            value = parse_turn(rows)
        bonus=self._read_bonus(context,image,bonus_crop)
        reading=(value,bonus) if value is not None and bonus is not None else None
        self._hits = self._hits + 1 if reading is not None and reading == self._pending else 1
        self._pending = reading
        if reading is not None and self._hits >= 2:
            self.base_turns,self.extra_turns=reading
            self._value, self._crop = value+bonus, crop
            self._bonus_crop=bonus_crop
        return self._value


class CardDetection:
    """Share only detector output within one explicitly captured frame.

    Create a new instance after every screencap, even if a controller reuses
    its image buffer. Selection geometry and title checks are never cached.
    """
    def __init__(self, context, image):
        self.context = context
        self.image = image
        self._loaded = False
        self._detail = None

    def read(self):
        if not self._loaded:
            self._detail = self.context.run_recognition('ProduceRecognitionCards', self.image)
            self._loaded = True
        return self._detail


class CardNameReader:
    RAISED_SINGLE_MIN_SCORE = .95
    RAISED_AGREEMENT_MIN_SCORE = .80

    def __init__(self):
        self._cache = {}
        self.calls = 0
        self.last_raised_candidate = None

    @staticmethod
    def _line(context,image,roi):
        detail=context.run_recognition("ProduceRecognitionPoint",image,pipeline_override={
            "ProduceRecognitionPoint":{"recognition":"OCR","roi":roi,"expected":[r".*"],"only_rec":True}})
        return list(detail.filtered_results or []) if detail and detail.hit else []

    @staticmethod
    def _canonical(text):
        catalog=_load_card_catalog()
        query=normalize_card_name(text)
        item=catalog.get(query)
        if item is None:
            def kana_key(value):
                value=unicodedata.normalize("NFD",value).translate(str.maketrans("ァィゥェォャュョッ", "アイウエオヤユヨツ"))
                return "".join(ch for ch in value if ch not in "\u3099\u309a")
            matches=[entry for title,entry in catalog.items() if kana_key(title)==kana_key(query)]
            if len(matches)==1:
                item=matches[0]
        return item["name"] if item else None

    def read(self, context, image, box):
        x, y, w, h = map(int, box)
        # Only text fully inside the chosen card's horizontal extent counts.
        # A narrow overlapped fragment is not sufficient name evidence.
        if w < 100 or h < 160:
            return None
        roi = [max(0,x), max(0,y+int(h*.73)-60), min(w,720-max(0,x)), min(145,1280-max(0,y+int(h*.73)-60))]
        crop = signature(image, roi)
        key = tuple(roi)
        previous = self._cache.get(key)
        if previous and same(previous[0],crop):
            return previous[1]
        self.calls += 1
        rows = ocr(context,image,roi,[r".*"])
        names = set()
        for row in rows:
            rx, _, rw, _ = map(int,row.box)
            if float(row.score) < .93 or rx < x-3 or rx+rw > x+w+3:
                continue
            name=self._canonical(row.text)
            if name:
                names.add(name)
        if not names:
            # The measured bottom 31 px strip contains only a full card title.
            # Recognition-only avoids text detection merging adjacent cards.
            line_roi=[max(0,x+4),min(1240,y+h-34),max(1,min(w-8,716-max(0,x))),31]
            for row in self._line(context,image,line_roi):
                name=self._canonical(row.text) if float(row.score)>=.95 else None
                if name:
                    names.add(name)
        name = names.pop() if len(names)==1 else None
        self._cache[key] = (crop,name)
        # Coordinates are only cache keys, never reused for clicking.
        if len(self._cache)>24:
            self._cache = {key:(crop,name)}
        return name


    @staticmethod
    def _panel_title_roi(image):
        """Find the selected-card bubble's top border, not text in effects/history."""
        if not reference_frame(image):
            return None
        rgb = image[250:860, 82:640, :3].astype(np.int16)
        gray = (rgb.max(2)-rgb.min(2) < 12) & (rgb.min(2) > 175) & (rgb.max(2) < 235)
        white = rgb.min(2) > 240
        # Search the border locally, keeping the returned title ROI in the
        # original frame coordinates. Vectorized row checks avoid a slow
        # Python loop for every pixel on every battle frame.
        for dx in (0, -1, 1, -2, 2, -3, 3, -4, 4, -5, 5, -6, 6, -7, 7, -8, 8):
            start = 8+dx
            g = gray[:, start:start+542]
            v = white[:, start:start+542]
            left = np.r_[0., np.cumsum(g[:, :5].mean(1))]
            right = np.r_[0., np.cumsum(g[:, 534:539].mean(1))]
            hits = np.flatnonzero((g[:530, 30:510].mean(1) > .80)
                & (v[8:538, 30:470].mean(1) > .90)
                & ((left[80:610]-left[18:548])/62 > .65)
                & ((right[80:610]-right[18:548])/62 > .65))
            if len(hits):
                return [120+dx, 250+int(hits[0])+8, 480, 48]
        return None

    @classmethod
    def same_title_panel(cls, first_image, second_image):
        """Compare title content relative to its border, allowing tiny shifts.

        A changed glyph must not disappear in the large white background's
        mean error. Bound both overall error and error on the text pixels.
        """
        p = cls._panel_title_roi(first_image)
        q = cls._panel_title_roi(second_image)
        if p is None or q is None or max(abs(a-b) for a,b in zip(p,q)) > 8:
            return False
        x,y,w,h = p
        a = first_image[y+8:y+h-8, x+8:x+w-8, :3].astype(np.int16)
        x,y,w,h = q
        for dy in (0,-1,1,-2,2):
            for dx in (0,-1,1,-2,2):
                b = second_image[y+8+dy:y+h-8+dy, x+8+dx:x+w-8+dx, :3].astype(np.int16)
                if a.shape != b.shape or not a.size:
                    continue
                delta = np.abs(a-b)
                ink = (a.min(2)<180) | (b.min(2)<180)
                if delta.mean() < 1.5 and (not ink.any() or (delta.max(2)[ink]>24).mean() < .06):
                    return True
        return False

    @staticmethod
    def _selected_badge(image, box):
        cx=int(box[0]+box[2]/2)
        badge=image[1110:1155,max(0,cx-48):min(720,cx+48),:3].astype(np.int16)
        return bool(badge.size and ((badge[:,:,2]>210)&(badge[:,:,1]>100)
                    &(badge[:,:,1]<235)&(badge[:,:,0]<125)).mean()>.12)

    @classmethod
    def same_selection(cls, first_image, first, second_image, second):
        """Bind bounded detector jitter to a stable tooltip and SELECT badge.

        Compare only the opaque title panel, never the animated stage or the
        floating score overlay above the card. Other hand positions stay out
        of tolerance. Geometry alone never grants relaxed selection matching.
        """
        if first is None or second is None:return False
        dx,dy,dw=[abs(a-b) for a,b in zip(first[:3],second[:3])]
        strict=max(dx,dy,dw)<=5
        if not strict and not same_card_slot(first,second):return False
        p=cls._panel_title_roi(first_image);q=cls._panel_title_roi(second_image)
        if p is None and q is None:return strict
        if p is None or q is None or not cls.same_title_panel(first_image,second_image):return False
        return strict or (cls._selected_badge(first_image,first) and cls._selected_badge(second_image,second))

    def locate_raised(self, context, image, original_box, detection=None):
        """Confirm selection geometry independently of card-name OCR.

        A card already selected on the previous pass must not be clicked as
        if it were resting. Require tooltip + SELECT badge for that case.
        Flying cards in the middle of the stage are not selected hand cards.
        """
        if not reference_frame(image):
            return None
        ox, oy, ow, oh = map(int, original_box)
        panel = self._panel_title_roi(image)
        # Recommended cards can have an inflated resting detector box. Their
        # measured rise is only 12-17px. Require the actual orange SELECT badge
        # as well as the tooltip before accepting this smaller displacement.
        cx = int(ox+ow/2)
        selected_badge = self._selected_badge(image,original_box)
        minimum_rise = 8 if panel is not None and selected_badge else 18
        detail = (detection.read() if detection is not None and detection.context is context and detection.image is image
                  else context.run_recognition('ProduceRecognitionCards', image))
        raised = []
        for row in detail.all_results or [] if detail and detail.hit else []:
            x, y, w, h = map(int, row.box)
            if (row.label not in ('cards', 'suggestions', 'useless') or float(row.score) < .50
                    or not (140 <= w <= 260 and 180 <= h <= 350)
                    or not (max(740, oy-180) <= y <= (oy+12 if panel is not None and selected_badge else oy-minimum_rise))
                    or abs((x+w/2)-(ox+ow/2)) > max(35, ow*.25)
                    or abs(w-ow) > ow*.4
                    or x < 0 or y < 0 or x+w > image.shape[1] or y+h > image.shape[0]):
                continue
            candidate = [x, y, w, h]
            if not any(intersection_over_union(candidate, old) > .70 for old in raised):
                raised.append(candidate)
        if not raised and panel is not None and selected_badge and 140 <= ow <= 260 and 180 <= oh <= 350:
            # Trouble cards may disappear from YOLO after being selected.
            # Bind a fresh, exact SELECT label to this hand position, together
            # with the visible tooltip and orange badge. Never confirm from an
            # old detection or from a tooltip alone. The badge anchors the card
            # bottom; the last resting detection supplies only its size.
            left, right = max(0, cx-90), min(720, cx+90)
            labels = [row for row in ocr(context, image, [left, 1100, right-left, 65], [r'^SELECT$'])
                      if str(row.text).strip().upper() == 'SELECT' and float(row.score) >= .95
                      and len(row.box) == 4 and 30 <= row.box[2] <= 150 and 12 <= row.box[3] <= 50
                      and 1100 <= row.box[1] <= 1145
                      and abs(row.box[0]+row.box[2]/2-cx) <= max(35, ow*.25)]
            if len(labels) == 1:
                sx, sy, sw, _ = map(int, labels[0].box)
                x, y = round(sx+sw/2-ow/2), sy-4-oh
                if 0 <= x <= 720-ow and 740 <= y <= oy+12:
                    return [x, y, ow, oh]
        if len(raised) != 1:
            return None
        return raised[0]

    def read_raised(self, context, image, original_box, *, detection=None):
        """Read only the freshly associated selection, never a stale strip."""
        self.last_raised_candidate = None
        box = self.locate_raised(context, image, original_box, detection)
        if box is None:
            return None
        panel = self._panel_title_roi(image)
        x, y, w, h = box
        # A selected card exposes a large, isolated title in its white tooltip.
        # This is much more legible than the 31px overlapping hand strip. The
        # border anchors the ROI above all effect text (which can name cards).
        if panel is not None:
            catalog = _load_card_catalog()
            names = set()
            rows = ocr(context, image, panel, [r'.*'])
            for row in rows:
                entry = catalog.get(normalize_card_name(row.text)) if float(row.score) >= self.RAISED_SINGLE_MIN_SCORE else None
                if entry:
                    names.add(entry['name'])
            if len(names) == 1:
                return dict(name=names.pop(), box=box)
            # Low-confidence exact text is a deferral hint only. It must never
            # become a drink trigger or an accepted identity on its own.
            hints = {entry['name']: float(row.score) for row in rows
                     if float(row.score) >= self.RAISED_AGREEMENT_MIN_SCORE
                     and (entry := catalog.get(normalize_card_name(row.text)))}
            if len(hints) == 1:
                hint = next(iter(hints))
                self.last_raised_candidate = dict(name=hint, box=box, score=hints[hint])
            # The strip is an independent route with the existing .95 gate.
            # Do not block it merely because the tooltip scored poorly.
            strip = self._line(context, image, [x+4, y+h-34, w-8, 31])
            strip_strong = {entry['name'] for row in strip
                            if float(row.score) >= self.RAISED_SINGLE_MIN_SCORE
                            and (entry := catalog.get(normalize_card_name(row.text)))}
            if not names and len(strip_strong) == 1 and (not hints or set(hints) == strip_strong):
                name = next(iter(strip_strong))
                logger.debug(f'待出牌标题条独立核名：{name}，说明标题=' + repr([(r.text,round(float(r.score),4)) for r in rows]))
                return dict(name=name, box=box)
            # User-selected .80 gate applies only to exact two-region agreement;
            # the caller still requires two frames of the same selection.
            panel_names = {entry['name'] for row in rows
                           if float(row.score) >= self.RAISED_AGREEMENT_MIN_SCORE
                           and (entry := catalog.get(normalize_card_name(row.text)))}
            if not names and len(panel_names) == 1:
                strip_names = {entry['name'] for row in strip
                               if float(row.score) >= self.RAISED_AGREEMENT_MIN_SCORE
                               and (entry := catalog.get(normalize_card_name(row.text)))}
                if strip_names == panel_names:
                    name = next(iter(panel_names))
                    logger.debug(f'待出牌双区域精确核名：{name}，门槛={self.RAISED_AGREEMENT_MIN_SCORE:.2f}，说明标题='
                                 + repr([(r.text,round(float(r.score),4)) for r in rows])
                                 + '；选中牌标题=' + repr([(r.text,round(float(r.score),4)) for r in strip]))
                    return dict(name=name,box=box)
            logger.debug('待出牌核名未通过：说明标题=' + repr([(r.text,round(float(r.score),4)) for r in rows])
                         + '；选中牌标题=' + repr([(r.text,round(float(r.score),4)) for r in strip]))
            return None
        roi = [x+4, y+h-34, w-8, 31]
        names = set()
        catalog = _load_card_catalog()
        for row in self._line(context, image, roi):
            entry = catalog.get(normalize_card_name(row.text)) if float(row.score) >= self.RAISED_SINGLE_MIN_SCORE else None
            if entry:
                names.add(entry['name'])
        if len(names) != 1:
            return None
        return dict(name=names.pop(), box=box)



class BattleDrinkSession:
    SLOT_ROIS = [[x-35,1160,70,80] for x in (62,147,232,317)]

    def __init__(self, *, policy=None):
        self.policy=load_policy() if policy is None else dict(policy)
        self.turns=TurnReader()
        self.names=CardNameReader()
        self.art=HandIdentityReader()
        self.opening=True
        self.inventory={}
        self.last_log=None
        self.pending_card=None
        self.inspection_streak=0
        self.final_drain=False
        self.deferred={}
        self.current_turn=None

    def needs_card_name(self, context, image):
        """A configured named-card rule matters only for a held bottle."""
        if self.pending_card:
            return True
        if not any(rule['timing']=='before_card' for rule in self.policy.values()):
            return False
        for index in self.occupied(context,image):
            if index in self.deferred:
                continue
            cached=self.inventory.get(index)
            if not cached or not same(cached[0],signature(image,self.SLOT_ROIS[index])):
                return True  # Unknown held bottle: inspect the name conservatively.
            if cached[1] is not None and self.policy[cached[1]]['timing']=='before_card':
                return True
        return False

    @staticmethod
    def _cancel_box(context, frame):
        rows=ocr(context,frame,[65,1020,320,210],
                 [r'^[×xX✕✖]?\s*キャンセル$',r'^[×xX✕✖]?\s*取消$',r'^閉じる$'])
        valid=[row for row in rows if float(row.score)>=.90]
        return list(valid[0].box) if len(valid)==1 else None

    def close_detail(self,context,wait_playable):
        """Recover a failed/disabled drink without turning it into task failure."""
        clicks=0
        next_click=0
        clear=0
        for _ in range(10):
            if context.tasker.stopping:
                return False
            frame=context.tasker.controller.post_screencap().wait().get()
            box=self._cancel_box(context,frame)
            if box and clicks < 3 and time.monotonic() >= next_click:
                x,y,w,h=map(int,box)
                context.tasker.controller.post_click(x+w//2,y+h//2).wait()
                clicks+=1
                next_click=time.monotonic()+.45
                time.sleep(.20)
                continue
            # A click may not have opened any dialog. Do not demand a cancel
            # button from an already playable board.
            modal=ocr(context,frame,[15,650,680,390],[r'[PＰ]?\s*ドリンク詳細',r'饮料详情',r'飲料詳情'])
            ready=context.run_recognition('ProduceRecognitionSkipRound',frame)
            clear=clear+1 if not box and not modal and ready and ready.hit else 0
            if clear>=2:
                return bool(wait_playable(context))
            time.sleep(.15)
        return False

    @staticmethod
    def use_enabled(frame,box):
        x,y,w,h=map(int,box)
        patch=frame[max(0,y):y+h,max(0,x):x+w,:3].astype(np.int16)
        if not patch.size:
            return False
        b,g,r=patch[:,:,0],patch[:,:,1],patch[:,:,2]
        return float(((r>190)&(g>75)&(g<225)&(b<140)&(r-g>25)).mean())>.12

    @staticmethod
    def occupied(context,image):
        result=context.run_recognition("ProduceCheckDrinkButton",image)
        slots=set()
        if result and result.hit:
            for row in result.filtered_results or []:
                x,y,w,h=map(int,row.box)
                # MaaFramework returns screen coordinates.
                if 1140 <= y+h//2 <= 1260:
                    index=min(range(4),key=lambda i:abs((62+85*i)-(x+w//2)))
                    if abs((62+85*index)-(x+w//2))<42:
                        slots.add(index)
        return sorted(slots)

    def identify(self, context, image, index, wait_playable):
        crop=signature(image,self.SLOT_ROIS[index])
        cached=self.inventory.get(index)
        if cached and same(cached[0],crop):
            return cached[1]
        # Read the exact selected bottle title once per changed icon. This is
        # slower only on first encounter; never classify a price as a name.
        context.tasker.controller.post_click(62+85*index,1200).wait()
        name=None
        for _ in range(8):
            if context.tasker.stopping:
                return None
            time.sleep(.15)
            frame=context.tasker.controller.post_screencap().wait().get()
            name=owned_detail_title(context,frame)
            if name:
                break
        if not self.close_detail(context,wait_playable):
            raise RuntimeError("饮料详情取消按钮未确认，保留现场")
        key=next((key for key,row in CATALOG.items() if drink_title(row["jp"])==drink_title(name)),None)
        self.inventory[index]=(crop,key)
        return key

    def prepare(self, context, image, wait_playable, *, remaining=None, card_name=None):
        """Use at most one bottle, then caller MUST discard its old hand frame.

        Return True even after inspection without consumption: opening details
        may collapse a selected hand card, invalidating its coordinates.
        """
        if not self.policy:
            return False
        slots=self.occupied(context,image)
        if context.tasker.stopping:
            return False
        if remaining is not None and remaining!=self.current_turn:
            if self.current_turn is not None:
                self.deferred.clear()
                self.inspection_streak=0
            self.current_turn=remaining
        self.final_drain = bool(slots) and (self.final_drain or remaining==1)
        if self.final_drain:
            remaining=1
        all_opening=all(r["timing"]=="opening" for r in self.policy.values())
        for index in slots:
            if index in self.deferred:
                continue
            rule=None
            key=None
            if remaining==1 or (all_opening and self.opening):
                rule={"timing":"opening"}
            else:
                cached=self.inventory.get(index)
                crop=signature(image,self.SLOT_ROIS[index])
                if not cached or not same(cached[0],crop):
                    from .drink_visual import match
                    # Battle bottles sit above the reward inventory row.
                    # Sharing reward y=1170 clips the sampled art on 1279px captures.
                    visual=match(image,self.SLOT_ROIS[index])
                    if visual:
                        cached=(crop,visual['id'])
                        self.inventory[index]=cached
                if not cached or not same(cached[0],crop):
                    self.inspection_streak+=1
                    if self.inspection_streak>8:
                        self.deferred[index]=True
                        logger.warning('饮料图标未确认，本回合跳过该瓶，继续出牌')
                        continue
                    if self.identify(context,image,index,wait_playable) is None:
                        self.deferred[index]=True
                    return True
                key=cached[1]
                if key is None:
                    continue
                rule=self.policy[key]
            if not due(rule,opening=self.opening,remaining=remaining,card_name=card_name):
                continue
            logger.info(f"饮料使用条件满足：{CATALOG[key]['jp'] if key else '末回合/开局饮料'} "
                        f"时机={rule['timing']} 剩余回合={remaining} 待出牌={card_name or '无'}")
            drink_started=time.monotonic()
            context.tasker.controller.post_click(62+85*index,1200).wait()
            use=None
            previous_use=None
            for _ in range(8):
                if context.tasker.stopping:
                    return False
                time.sleep(.15)
                frame=context.tasker.controller.post_screencap().wait().get()
                detail=context.run_recognition("ProduceUseDrinkConfirm",frame)
                if detail and detail.hit and detail.best_result:
                    if key and owned_detail_title(context,frame)!=CATALOG[key]["jp"]:
                        continue
                    box=list(detail.best_result.box)
                    if self.use_enabled(frame,box):
                        if previous_use is not None and max(abs(a-b) for a,b in zip(box,previous_use))<=3:
                            use=box
                            break
                        previous_use=box
                    else:
                        previous_use=None
                else:
                    previous_use=None
            if use is None:
                if self.close_detail(context,wait_playable):
                    self.deferred[index]=True
                    logger.warning('饮料暂不可使用或详情未确认，已返回出牌页，本回合跳过该瓶')
                    return True
                raise RuntimeError("饮料详情未能关闭，保留现场")
            x,y,w,h=map(int,use)
            # Detail controls render before their entrance animation accepts
            # input. Two stable observations plus a short settle avoids a lost click.
            time.sleep(.25)
            context.tasker.controller.post_click(x+w//2,y+h//2).wait()
            if not wait_playable(context,2):
                if self.close_detail(context,wait_playable):
                    self.deferred[index]=True
                    logger.warning('饮料操作未完成，已恢复出牌，本回合不再尝试该瓶')
                    return True
                raise RuntimeError("喝药后未确认回到出牌页")
            after=context.tasker.controller.post_screencap().wait().get()
            after_slots=self.occupied(context,after)
            if len(after_slots)>=len(slots):
                if self.close_detail(context,wait_playable):
                    self.deferred[index]=True
                    logger.warning('未确认饮料消耗，本回合不重复点击，继续出牌')
                    return True
                raise RuntimeError("喝药后持有数量未减少且无法恢复出牌页")
            self.inventory.clear()
            self.deferred.clear()
            self.inspection_streak=0
            self.names=CardNameReader()
            logger.success(f"已确认饮料消耗：{CATALOG[key]['jp'] if key else '当前饮料'}，持有数量 {len(slots)}→{len(after_slots)}，耗时 {time.monotonic()-drink_started:.2f} 秒")
            if rule["timing"]=="before_card" and remaining!=1:
                self.pending_card=card_name
            return True
        return False

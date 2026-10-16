"""Recovery dialog OCR: locate first, read isolated fields, never repair digits."""
from .frame_geometry import reference_frame
import re
import unicodedata
import numpy as np


def normalized(text):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(text)))


def groups(indices):
    if not len(indices):
        return []
    return [(int(g[0]), int(g[-1])+1) for g in np.split(indices, np.where(np.diff(indices)>2)[0]+1)]


def rows(context, image, roi, *, line=False):
    x,y,w,h=map(int,roi)
    if min(x,y)<0 or min(w,h)<=0 or x+w>image.shape[1] or y+h>image.shape[0]:
        return []
    result=context.run_recognition('ProduceRecognitionPoint',image,pipeline_override={
        'ProduceRecognitionPoint': {'recognition':'OCR','roi':[x,y,w,h],
            'expected': ['.*'], 'order_by':'Horizontal', 'only_rec':line}})
    return [r for r in (result.filtered_results or []) if float(r.score)>=.90] if result and result.hit else []


def one(context,image,roi,pattern,*,line=False):
    found=[(r, re.fullmatch(pattern, normalized(r.text))) for r in rows(context,image,roi,line=line)]
    found=[(r,m) for r,m in found if m]
    return found[0] if len(found)==1 else (None,None)


def fraction_roi(image, roi):
    """Locate the green/cyan heart; return only the fraction to its right."""
    x,y,w,h=map(int,roi)
    p=image[y:y+h,x:x+w,:3].astype(np.int16)
    if p.shape[:2]!=(h,w):return None
    b,g,r=p[:,:,0],p[:,:,1],p[:,:,2]
    color=(g>155)&(g-r>45)
    green=(g>170)&(g-r>35)&(g-b>25)
    candidates=[]
    # Separate vertical bands first: a green HP track above the heart must not
    # merge with it in a column projection. Only the compact icon is excluded.
    for top,bottom in groups(np.where(color.sum(1)>=2)[0]):
        if not 10<=bottom-top<=50:continue
        for left,right in groups(np.where(color[top:bottom].sum(0)>=2)[0]):
            if 15<=right-left<=55 and green[top:bottom,left:right].sum()>=15:
                candidates.append((left,right))
    if len(candidates)!=1:return None
    _,right=candidates[0]
    # Leave the heart's outline behind, preserving the gap before the first digit.
    start=right+3
    if not 0<start<w-25:return None
    return [x+start,y,w-start,h]


def read_fraction(context,image,roi):
    crop=fraction_roi(image,roi)
    if crop is None:return None
    _,m=one(context,image,crop,r'(\d{1,2})/(\d{1,2})',line=True)
    if not m:
        # Retry text detection only inside the same icon-free numeric crop.
        _,m=one(context,image,crop,r'(\d{1,2})/(\d{1,2})')
    if not m:return None
    value,maximum=map(int,m.groups())
    return (value,maximum) if 0<=value<=maximum<=99 and maximum>0 else None


def point_row(image, start):
    """Locate the yellow P coin below the HP row, independently of slider height."""
    start=max(0,int(start))
    p=image[start: min(image.shape[0],start+280),60:175,:3].astype(np.int16)
    if not p.size:return None
    b,g,r=p[:,:,0],p[:,:,1],p[:,:,2]
    yellow=(r>150)&(g>140)&(b<140)&(r-b>60)&(g-b>50)
    found=[]
    for top,bottom in groups(np.where(yellow.sum(1)>=8)[0]):
        cols=np.where(yellow[top:bottom].sum(0)>=3)[0]
        if len(cols) and 25<=bottom-top<=80 and 25<=cols[-1]-cols[0]+1<=80:
            found.append((start+top,start+bottom))
    return found[0] if len(found)==1 else None


def read_dialog(context,image,expected_health=None):
    from .shop import _recovery_step_button
    if not reference_frame(image):
        return None
    # Locate the wide orange dialog heading without OCR-ing the whole screen.
    p=image[180:950,35:670,:3].astype(np.int16)
    b,g,r=p[:,:,0],p[:,:,1],p[:,:,2]
    yellow=(r>210)&(g>130)&(g<240)&(b<150)&(r-b>70)
    bands=[(a+180,z+180) for a,z in groups(np.where(yellow.sum(1)>300)[0]) if 20<=z-a<=100]
    heading=None
    for top,bottom in bands:
        row,m=one(context,image,[35,top,340,bottom-top],r'(体力回復確認|体力恢复确认|體力回復確認)')
        if m:
            if heading is not None:return None
            heading=(top,bottom)
    if heading is None:return None
    top,bottom=heading
    # Footer uses the dialog anchor even when a numerical field is unreadable.
    footer_y=min(image.shape[0]-130, bottom+440)
    cancel,_=one(context,image,[90,footer_y,255,min(130,image.shape[0]-footer_y)],r'[×✕X]?(キャンセル|取消|いいえ)')
    confirm,_=one(context,image,[415,footer_y,235,min(130,image.shape[0]-footer_y)],r'(回復する|回復|恢复|はい)')
    result={'cancel':list(cancel.box) if cancel else None,'quote':None,'reason':'fields', 'rois':{}}
    # Read digits directly in title-relative boxes. Neither the HP track nor
    # its fill color is a prerequisite. Isolate the heart before numeric OCR.
    hp_roi=[275,bottom+175,205,70]
    amount_roi=[295,bottom+80,140,80]
    hp=read_fraction(context,image,hp_roi)
    amount_row,amount_match=one(context,image,amount_roi,r'\+(\d{1,2})')
    question_roi=[45,bottom+16,620,70]
    _,question=one(context,image,question_roi,r'.*体力を(\d{1,2})回復.*')
    before_roi=[285,bottom+310,115,115]
    after_roi=[455,bottom+310,130,115]
    coin=point_row(image,bottom+245)
    if coin:
        row_y=max(0,coin[0]-10)
        row_h=coin[1]-coin[0]+20
        before_roi=[285,row_y,115,row_h]
        after_roi=[455,row_y,130,row_h]
    before_row,before_match=one(context,image,before_roi,r'(\d{1,4})')
    after_row,after_match=one(context,image,after_roi,r'(-?\d{1,4})')
    result['rois']=dict(hp=fraction_roi(image,hp_roi),amount=amount_roi,question=question_roi,
                        before=before_roi,after=after_roi)
    result['fields']=dict(hp=hp,amount=amount_match[1] if amount_match else None,
        question=question[1] if question else None,before=before_match[1] if before_match else None,
        after=after_match[1] if after_match else None)
    if hp is None or not all((amount_match,question,before_match,after_match)):
        return result
    amount=int(amount_match[1]);before=int(before_match[1]);after=int(after_match[1])
    projected,maximum=hp
    # HIF charges 10P per two-HP step. At full HP the displayed gain is
    # clipped, but the last step still costs 10P (20/33 -> +13 costs 70P).
    cost = ((amount + 1) // 2) * 10 if projected == maximum else 5 * amount
    if (int(question[1])!=amount or not 0<amount<=projected<=maximum
            or before-after!=cost or before<=0
            or abs(before_row.box[1]-after_row.box[1])>20
            or (expected_health is not None and expected_health!=(projected-amount,maximum))):
        result['reason']='cross_check';return result
    controls_y=bottom+235
    controls_h=int(coin[0] if coin else min(before_row.box[1],after_row.box[1]))-20-controls_y
    result['quote']=dict(amount=amount,projected=projected,maximum=maximum,before=before,after=after,
        confirm=list(confirm.box) if confirm else None,
        plus=_recovery_step_button(image,[540,controls_y,140,controls_h],True),
        minus=_recovery_step_button(image,[50,controls_y,140,controls_h],False))
    result['reason']='ok'
    return result


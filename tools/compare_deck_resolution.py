"""Offline ablation: original matcher versus original-resolution library art.

No controller, no game input. Experimental scores are not probabilities.
"""
import argparse
import json
import sys
from pathlib import Path
import time

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'agent'))
from custom.card_visual import match_card_visual, _resize_rgb_bilinear


def descriptors(images, size, masked):
    mask = np.zeros((size, size), bool)
    mask[round(size*.10):round(size*.77), round(size*.22):round(size*.78)] = True
    if masked:
        mask[round(size*.45):, :round(size*.47)] = False
        mask[round(size*.43):, round(size*.72):] = False
    pixels = images[:, mask, :]
    centered = pixels - pixels.mean(axis=1, keepdims=True)
    norm = np.sqrt((centered ** 2).sum(axis=(1, 2), keepdims=True)) + 1e-8
    return (centered / norm).reshape(len(images), -1), mask


def main():
    p=argparse.ArgumentParser(); p.add_argument('portable',type=Path); p.add_argument('image',type=Path)
    p.add_argument('--crop',nargs=4,type=int,default=[1,45,721,1325]); args=p.parse_args()
    frame=np.asarray(Image.open(args.image).convert('RGB').crop(args.crop))[:,:,::-1].copy()
    entries=json.loads((args.portable/'config/card_library.json').read_text(encoding='utf-8'))['cards']
    originals=[np.asarray(Image.open(args.portable/e['image']).convert('RGB'))[:,:,::-1] for e in entries]
    boxes=[[x,y,118,118] for y in (639,786,933) for x in (82,229,376,523)]
    report=[{'box':b,'old':None} for b in boxes]
    for b,r in zip(boxes,report):
        old=match_card_visual(frame,b)
        if old:r['old']={'name':old.name,'confidence':round(old.confidence,3)}
    for size in (32,64):
        for masked in (False,True):
            templates=np.stack([_resize_rgb_bilinear(im,size) for im in originals])
            vectors,mask=descriptors(templates,size,masked)
            start=time.perf_counter()
            for b,r in zip(boxes,report):
                x,y,w,h=b
                target=_resize_rgb_bilinear(frame[y:y+h,x:x+w],size)
                vector,_=descriptors(target[None],size,masked)
                scores=(vectors @ vector[0])
                order=np.argsort(scores)[::-1]; first=order[0]
                second=next(i for i in order if entries[i]['name']!=entries[first]['name'])
                r[f'{size}_mask{int(masked)}']={'name':entries[first]['name'],'score':round(float(scores[first]),3),'margin':round(float(scores[first]-scores[second]),3)}
            print('TIMING',size,masked,round((time.perf_counter()-start)*1000))
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()

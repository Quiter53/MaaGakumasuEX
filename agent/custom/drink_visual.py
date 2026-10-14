"""Cached all-catalog bottle retrieval; scores are similarity, not probability.

Read-only. Acquisition/discard/use policies and final title checks stay outside.
The packaged atlas is deterministically built from existing drink PNGs.
"""
from functools import lru_cache
from pathlib import Path
from zipfile import BadZipFile

import numpy as np
from loguru import logger

from .drink_policy import CATALOG

MIN_SCORE = .78
MIN_MARGIN = .14
INVENTORY_BOXES = [[x-35, 1170, 70, 80] for x in (65, 147, 229, 311)]
_Y, _X = np.mgrid[0:40, 0:40].astype(np.float32) / 39
_MASK = (_X > .28) & (_X < .72) & (_Y > .18) & (_Y < .85)
_TRANSFORMS = np.array([(s, dx, dy) for s in (.7, .8, .9, 1., 1.1, 1.2)
                        for dx in (-.1, -.05, 0, .05, .1)
                        for dy in (-.1, -.05, 0, .05, .1, .15)], np.float32)


def _unit(a):
    a = a - a.mean(axis=(-2, -1), keepdims=True)
    return a / np.maximum(np.sqrt((a*a).sum(axis=(-2, -1), keepdims=True)), 1e-8)


@lru_cache(maxsize=1)
def gallery():
    try:
        with np.load(Path(__file__).with_name('drink_fingerprints.npz'), allow_pickle=False) as data:
            ids, images = data['ids'], data['templates']
            if (ids.dtype.kind != 'U' or images.dtype != np.uint8
                    or images.shape != (len(ids), 40, 40, 3) or set(ids) != set(CATALOG)):
                raise ValueError('invalid drink atlas')
            reference = _unit(images[:, _MASK].astype(np.float32)).reshape(len(ids), -1)
            return ids, np.ascontiguousarray(reference)
    except (OSError, ValueError, KeyError, TypeError, BadZipFile, EOFError):
        logger.warning('饮料图标索引无法加载，回退原识别与详情核实')
        return None


def _queries(image, box, transforms):
    x, y, w, h = box
    s, dx, dy = transforms.T[:, :, None]
    sx = x+w*((_X[_MASK][None]-.5)*s+.5+dx)
    sy = y+h*((_Y[_MASK][None]-.5)*s+.5+dy)
    ix, iy = sx.astype(np.int32), sy.astype(np.int32)
    ax, ay = (sx-ix.astype(np.float32))[..., None], (sy-iy.astype(np.float32))[..., None]
    pixels = (image[iy, ix, :3]*(1-ax)*(1-ay) + image[iy, ix+1, :3]*ax*(1-ay)
              + image[iy+1, ix, :3]*(1-ax)*ay + image[iy+1, ix+1, :3]*ax*ay)[..., ::-1]
    return np.ascontiguousarray(_unit(pixels).reshape(len(transforms), -1).T)


def match(image, box):
    if not isinstance(image, np.ndarray) or image.ndim != 3 or image.shape[2] < 3:
        return None
    x, y, w, h = map(int, box)
    if min(w, h) < 45 or max(w, h) > 170 or not .65 <= w/h <= 1.4:
        return None
    # The bounded refinement may sample a few pixels beyond the nominal ROI.
    if min(x, y) < 5 or x+w+5 >= image.shape[1] or y+h*1.15+1 >= image.shape[0]:
        return None
    model = gallery()
    if model is None:
        return None
    ids, reference = model
    scores = reference @ _queries(image, (x,y,w,h), _TRANSFORMS)
    top = np.argsort(scores.max(axis=1))[-3:]
    bases = _TRANSFORMS[scores[top].argmax(axis=1)]
    fine = np.array([(s+ds, dx+ddx, dy+ddy) for s,dx,dy in bases
                     for ds in (-.04,-.02,0,.02,.04) for ddx in (-.025,0,.025)
                     for ddy in (-.025,0,.025)], np.float32)
    ranks = np.maximum(scores.max(axis=1), (reference @ _queries(image, (x,y,w,h), fine)).max(axis=1))
    first, second = np.argsort(ranks)[-2:][::-1]
    score, margin = float(ranks[first]), float(ranks[first]-ranks[second])
    if np.isfinite(score) and (score < MIN_SCORE or margin < MIN_MARGIN):
        # Narrow artwork peaks can sit between coarse grid points. Refining
        # only one pose per identity locks onto a wrong local peak (smoothie).
        # Retry several poses without lowering identity acceptance thresholds.
        seeds = np.concatenate([_TRANSFORMS[np.argsort(scores[i])[-4:]] for i in top])
        fine = np.unique(np.array([(s+ds, dx+ddx, dy+ddy) for s,dx,dy in seeds
                                  for ds in (-.04,-.02,0,.02,.04)
                                  for ddx in (-.025,0,.025) for ddy in (-.025,0,.025)],np.float32),axis=0)
        ranks = np.maximum(ranks,(reference @ _queries(image,(x,y,w,h),fine)).max(axis=1))
        first, second = np.argsort(ranks)[-2:][::-1]
        score, margin = float(ranks[first]), float(ranks[first]-ranks[second])
    if not np.isfinite(score) or score < MIN_SCORE or margin < MIN_MARGIN:
        return None
    key = str(ids[first])
    return dict(id=key, name=CATALOG[key]['jp'], box=list(box), score=score,
                margin=margin, visual_trusted=True)


def scan(image, boxes):
    return {i: result for i, box in enumerate(boxes) if (result := match(image, box)) is not None}

"""Fit rejection gates on augmented reference art, not game test screenshots.

This is metric-retrieval calibration, NOT YOLO/neural-network retraining.
Calibration and synthetic holdout use disjoint card identities and RNG seeds.
Existing gallery remains the reference database for both identity groups.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import types

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"agent"))
package = types.ModuleType("custom")
package.__path__ = [str(ROOT/"agent/custom")]
sys.modules.setdefault("custom", package)
from custom.hand_identity import HandIdentityReader, visible_mask


def samples(reader, seed, holdout, per_mode):
    rng = np.random.default_rng(seed)
    templates, names, _, _ = reader.model
    unique = sorted(set(names))
    selected = {name for i,name in enumerate(unique) if (i % 3 == 0) == holdout}
    indexes = np.flatnonzero(np.isin(names, list(selected)))
    records = []
    for mode in ("full", "local"):
        for n in range(per_mode):
            index = int(rng.choice(indexes))
            query = templates[index].copy()
            # Full gallery enrollment is allowed, but perturbed query images
            # and identities used to fit gates do not enter the holdout set.
            query = np.roll(query, tuple(rng.integers(-1,2,size=2)), axis=(0,1))
            query = query*rng.uniform(.55,1.1)+rng.uniform(-.025,.04)
            query += rng.normal(0, .015, query.shape)
            query = np.clip(query,0,1)
            if n % 4 == 0:
                xx, yy = rng.integers(5,23,size=2)
                query[yy:yy+7,xx:xx+8] = rng.random(3)
            # Explicit negatives: no identifiable card, mixed/transition frame.
            negative = n % 5 == 0
            if negative:
                if n % 10 == 0:
                    query = rng.uniform(0,1,query.shape).astype(np.float32)
                else:
                    query[:] = rng.random(3)
            box = [0,0,189,249]
            boxes = [box]
            if mode == "local":
                boxes += [[int(rng.choice([122,164])),0,189,249]]
            mask = visible_mask(box, boxes)
            result = reader.rank(query,mask,mode)
            name,score,margin = result
            records.append(dict(mode=mode,score=score,margin=margin,
                                correct=bool(not negative and name==names[index]),negative=negative))
        print(f"{'holdout' if holdout else 'train'} {mode}: {per_mode}",flush=True)
    return records


def metrics(records, gates):
    result = {}
    for mode in ("full","local"):
        rows = [r for r in records if r["mode"]==mode]
        g = gates[mode]
        accepted = [r for r in rows if r["score"]>=g["score"] and r["margin"]>=g["margin"]]
        result[mode] = dict(total=len(rows),positive=sum(not r["negative"] for r in rows),
                            correct=sum(r["correct"] for r in accepted),
                            wrong=sum(not r["correct"] for r in accepted),unknown=len(rows)-len(accepted))
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--per-mode",type=int,default=400)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--report",type=Path,required=True)
    args=parser.parse_args()
    reader=HandIdentityReader()
    training=samples(reader,11011,False,args.per_mode)
    gates={}
    for mode in ("full","local"):
        rows=[r for r in training if r["mode"]==mode]
        options=[]
        for score in np.arange(.40,.91,.025):
            for margin in np.arange(.07,.41,.01):
                accepted=[r for r in rows if r["score"]>=score and r["margin"]>=margin]
                if any(not r["correct"] for r in accepted):
                    continue
                options.append((len(accepted),-score-margin,float(score),float(margin)))
        if not options:
            raise RuntimeError("No safe calibration threshold; do not export")
        _,_,score,margin=max(options)
        gates[mode]=dict(score=round(score,4),margin=round(margin,4))
    holdout=samples(reader,21011,True,args.per_mode//2)
    digest=hashlib.sha256((ROOT/"agent/custom/card_fingerprints.npz").read_bytes()).hexdigest()
    model=dict(version=1,kind="dense-artwork-metric-rejection-calibration",gallery_sha256=digest,
               seeds=[11011,21011],gates=gates)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(model,indent=2),encoding="utf-8")
    report=dict(model=model,train=metrics(training,gates),holdout=metrics(holdout,gates),
                limitation="Synthetic calibration is not real-game accuracy. No neural detector weights were trained.")
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps(report),flush=True)


if __name__=="__main__":
    main()

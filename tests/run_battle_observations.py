"""Native OCR replay; custom controller refuses all input. Reports measured cost."""
import argparse
import dataclasses
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image
from maa.library import Library


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("portable",type=Path)
    parser.add_argument("images",nargs="+",type=Path)
    parser.add_argument("--identity-report",type=Path)
    args=parser.parse_args()
    Library.open(args.portable/"runtimes/win-x64/native")
    Library.version()
    from maa.resource import Resource
    from maa.tasker import Tasker
    from maa.controller import CustomController
    from maa.pipeline import JOCR, JNeuralNetworkDetect, JTemplateMatch
    resource=Resource()
    assert resource.post_bundle(args.portable/"resource/base").wait().status.succeeded
    class NoInput(CustomController):
        def connect(self): return True
        def request_uuid(self): return "battle-replay-no-input"
        def get_features(self): return 0
        def screencap(self): return np.zeros((1280,720,3),np.uint8)
        def click(self,*args): raise AssertionError("Replay cannot click")
    controller=NoInput(); assert controller.post_connection().wait().status.succeeded
    tasker=Tasker(); assert tasker.bind(resource,controller)
    class Context:
        def run_recognition(self,name,image,pipeline_override=None):
            node=resource.get_node_data(name)
            override=(pipeline_override or {}).get(name,{})
            kind=override.get("recognition",node["recognition"]["type"])
            cls={"OCR":JOCR,"NeuralNetworkDetect":JNeuralNetworkDetect,"TemplateMatch":JTemplateMatch}[kind]
            fields={f.name for f in dataclasses.fields(cls)}
            params={k:v for k,v in {**node["recognition"]["param"],**override}.items() if k in fields}
            if cls is JTemplateMatch:
                for key in ("template","threshold"):
                    if key in params and not isinstance(params[key],list): params[key]=[params[key]]
            return tasker.post_recognition(kind,cls(**params),image).wait().get().nodes[0].recognition
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"agent"))
    with patch("maa.agent.agent_server.AgentServer.custom_action", lambda _: lambda cls: cls), \
         patch("maa.agent.agent_server.AgentServer.custom_recognition", lambda _: lambda cls: cls):
        from custom.battle_drinks import TurnReader,CardNameReader,BattleDrinkSession
        from custom.hand_identity import HandIdentityReader,hand_boxes
        from custom.card_visual import normalize_card_name
    context=Context()
    identity=HandIdentityReader()
    identity_rows=[]
    labels=json.loads((Path(__file__).parent/"fixtures/hand_identity_labels.json").read_text(encoding="utf-8"))["frames"]
    for path in args.images:
        src=Image.open(path).convert("RGB")
        if src.size != (720,1280): src=src.crop((8,31,src.width-8,src.height-8)).resize((720,1280))
        frame=np.asarray(src)[:,:,::-1].copy()
        if args.identity_report:
            truth=next(row for row in labels if row["file"]==path.name)
            detected=context.run_recognition("ProduceRecognitionCards",frame)
            boxes=hand_boxes(detected.all_results or [])
            for x,name in zip(truth["xs"],truth["names"]):
                nearby=[b for b in boxes if abs(b[0]-x)<25]
                box=nearby[0] if len(nearby)==1 else None
                start=time.perf_counter()
                result=identity.read(frame,box,boxes) if box else None
                predicted=result.name if result else None
                fallback=None
                if predicted is None and result and result.mode=="full":
                    fallback=CardNameReader().read(context,frame,box)
                outcome="unknown" if predicted is None else "correct" if normalize_card_name(predicted)==normalize_card_name(name) else "wrong"
                identity_rows.append(dict(file=path.name,count=len(truth["xs"]),detected_count=len(boxes),box=box,
                                          expected=name,outcome=outcome,ocr_fallback=fallback,ms=round((time.perf_counter()-start)*1000,2),
                                          result=dataclasses.asdict(result) if result else None))
            continue
        from custom.shop import ocr
        print("TURN_RAW", [(r.text,r.score,list(r.box)) for r in ocr(context,frame,[0,0,175,135],[r".*"])])
        print("HEADER_RAW",[(r.text,r.score) for r in ocr(context,frame,[0,0,175,42],[r".*"])])
        print("DIGIT_RAW",[(r.text,r.score) for r in ocr(context,frame,TurnReader.ROI,[r".*"])])
        print("OCCUPIED",BattleDrinkSession.occupied(context,frame))
        print("TITLE_RAW", [(r.text,r.score,list(r.box)) for r in ocr(context,frame,[0,1040,720,105],[r".*"])])
        turns=TurnReader(); names=CardNameReader(); start=time.perf_counter()
        turns.read(context,frame); remaining=turns.read(context,frame)
        ms=(time.perf_counter()-start)*1000
        start=time.perf_counter(); turns.read(context,frame); cached_ms=(time.perf_counter()-start)*1000
        detected=context.run_recognition("ProduceRecognitionCards",frame)
        results=[]
        for card in detected.all_results or []:
            if card.label not in ("cards","suggestions") or not 780<=card.box[1]<=1000: continue
            start=time.perf_counter(); name=names.read(context,frame,card.box)
            results.append({"box":list(card.box),"name":name,"ms":round((time.perf_counter()-start)*1000,1)})
        print(json.dumps({"image":path.name,"remaining":remaining,"turn_ms":round(ms,1),"cached_ms":round(cached_ms,2),"cards":results},ensure_ascii=False))
    if args.identity_report:
        groups={}
        for row in identity_rows:
            group=groups.setdefault(str(row["count"]),dict(total=0,correct=0,wrong=0,unknown=0,missed=0))
            group["total"]+=1
            group[row["outcome"]]+=1
            group["missed"]+=int(row["box"] is None)
        args.identity_report.parent.mkdir(parents=True,exist_ok=True)
        args.identity_report.write_text(json.dumps(dict(groups=groups,records=identity_rows),ensure_ascii=False,indent=2),encoding="utf-8")
        print(json.dumps(groups,ensure_ascii=False))


if __name__=="__main__": main()

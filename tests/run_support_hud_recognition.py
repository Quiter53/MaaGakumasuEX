"""Replay rental support and numeric HUD feedback; all input stays offline."""
import argparse, dataclasses, hashlib, json, statistics, sys, time
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
import numpy as np
from PIL import Image
from maa.library import Library

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--cases', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
out = args.output
out.mkdir(parents=True, exist_ok=True)
version = Library.version()
from maa.toolkit import Toolkit
from maa.controller import CustomController
from maa.resource import Resource
from maa.tasker import Tasker
from maa.pipeline import JOCR, JTemplateMatch, JNeuralNetworkDetect, JCustomRecognition
Toolkit.init_option(str(out))
resource = Resource()
resource.use_cpu()
assert resource.post_bundle(str(root / 'assets/resource/base')).wait().succeeded
frame = np.zeros((1280, 720, 3), np.uint8)
clicks = []
class Offline(CustomController):
    def connect(self): return True
    def request_uuid(self): return 'ab-offline-only'
    def screencap(self): return frame.copy()
    def get_features(self): return 0
    def click(self, x, y): clicks.append([x,y]); return True
    def swipe(self, *args):
        global frame
        swipes.append(list(args)); frame=loaded[1]; return True
controller = Offline()
assert controller.post_connection().wait().succeeded
tasker = Tasker()
assert tasker.bind(resource, controller)
class Context:
    def __init__(self): self.tasker = NS(controller=controller, stopping=False)
    def run_recognition(self, name, image, pipeline_override=None):
        node = (resource.get_node_data(name) or {'recognition':{'type':(pipeline_override or {}).get(name,{}).get('recognition','OCR'),'param':{}}})['recognition']
        override = (pipeline_override or {}).get(name, {})
        kind = override.get('recognition', node['type'])
        params = {**node['param'], **override}
        cls = {'OCR': JOCR, 'TemplateMatch': JTemplateMatch, 'NeuralNetworkDetect': JNeuralNetworkDetect, 'Custom': JCustomRecognition}[kind]
        params = {k:v for k,v in params.items() if k in {f.name for f in dataclasses.fields(cls)}}
        if cls is JTemplateMatch:
            for key in ('template','threshold'):
                if key in params and not isinstance(params[key], list): params[key] = [params[key]]
        return tasker.post_recognition(kind, cls(**params), image).wait().get().nodes[0].recognition
sys.path.insert(0, str(root/'agent'))
def register(name):
    def decorator(cls):
        assert resource.register_custom_recognition(name, cls())
        return cls
    return decorator
with patch('maa.agent.agent_server.AgentServer.custom_action', lambda _:lambda cls:cls), patch('maa.agent.agent_server.AgentServer.custom_recognition', register):
    from custom.action.produce import ProduceCardsAuto
context = Context()



from custom.produce.support import ProduceChooseRentalSupportAuto
from custom.cultivation_hud import read_health, read_points
from custom.produce.events import ProduceChooseNIAEventAuto
import subprocess
spec = json.loads(args.cases.read_text('utf-8'))
def read_frame(item):
    im=Image.open(item['path']).convert('RGB')
    if item.get('crop'): im=im.crop(item['crop']).resize((720,1280))
    assert im.width==720 and im.height in (1279,1280)
    return np.asarray(im)[:,:,::-1].copy()
class RealContext(Context):
    def __init__(self): super().__init__(); self.tasks=[]
    def get_node_data(self,name):return resource.get_node_data(name) or {}
    def override_pipeline(self,data):return resource.override_pipeline(data)
    def run_task(self,name):
        self.tasks.append(name)
        if name=='ProduceSupportDismissDuplicate':return tasker.post_task(name).wait().get()
        return NS(status=NS(succeeded=True))  # record scene handoff, no game
loaded=[read_frame(item) for item in spec['rental']]
records=[]
for i,expected_y,expected_swipes in [(0,830,0),(1,645,0),(2,645,1)]:
    frame=loaded[i];clicks.clear();swipes=[]
    start=time.perf_counter()
    result=ProduceChooseRentalSupportAuto().run(RealContext(),NS())
    assert result and len(clicks)==1 and abs(clicks[0][1]-expected_y)<15 and len(swipes)==expected_swipes
    records.append({'case':'rental-'+str(i),'clicks':clicks.copy(),'swipes':swipes.copy(),'seconds':time.perf_counter()-start})
frame=read_frame(spec['popup']);clicks.clear()
ctx=RealContext();action=ProduceChooseRentalSupportAuto()
assert action._hit(ctx,'ProduceChooseSupportReplace',frame)
assert not action._cancel(ctx)  # static screenshot cannot close; must stop
assert len(clicks)==1 and clicks[0][0]<380 and clicks[0][1]>1050
records.append({'case':'frozen-popup','clicks':clicks.copy(),'returnsFalse':True})
for item in spec['hud']:
    frame=read_frame(item)
    assert read_health(ctx,frame,item['healthRoi'])==tuple(item['health'])
    assert read_points(ctx,frame,item['pointsRoi'])==item['points']
    records.append({'case':item['name'],'health':item['health'],'points':item['points']})
frame=read_frame(spec['schedule']);clicks.clear();ctx=RealContext()
with patch('custom.produce.events.time.sleep'):
    assert ProduceChooseNIAEventAuto().run(ctx,NS(custom_action_param='{}'))
assert ctx.tasks==['ProduceGuideEntry'] and len(clicks)==2 and all(290<=x<=430 and 930<=y<=1060 for x,y in clicks)
records.append({'case':'schedule-guide-action','clicks':clicks.copy(),'tasks':ctx.tasks})
for item in spec['negative']:
    frame=read_frame(item)
    assert not action._hit(ctx,'ProduceChooseSupportReplace',frame)
    assert read_health(ctx,frame) is None
    records.append({'case':'negative','path':item['path']})
report={'passed':True,'sourceCommit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),
 'cases':records,'framework':version,'inputs':'real screenshot pixels; list transition after swipe simulated; all clicks captured offline',
 'scriptSha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
 'manifestSha256':hashlib.sha256(args.cases.read_bytes()).hexdigest(),
 'imageHashes':{item['path']:hashlib.sha256(Path(item['path']).read_bytes()).hexdigest()
                for item in spec['rental']+spec['hud']+spec['negative']+[spec['popup'],spec['schedule']]},
 'sourceHashes':{str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [root/'agent/custom/produce/support.py',root/'agent/custom/cultivation_hud.py',root/'assets/resource/base/pipeline/Produce.json']}}
(out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')
print(json.dumps(report,ensure_ascii=False),flush=True)

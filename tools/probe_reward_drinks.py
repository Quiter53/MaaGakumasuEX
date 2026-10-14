"""Read-only MaaFramework reward-drink scan replay. Never connects to a game."""
import argparse
import dataclasses
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PIL import Image
from maa.library import Library


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('portable', type=Path)
    parser.add_argument('image', type=Path)
    parser.add_argument('--crop', type=int, nargs=4)
    parser.add_argument('--duplicate', action='store_true', help='Copy a real inventory bottle into its neighbour to test duplicate masking')
    args = parser.parse_args()
    Library.open(args.portable / 'runtimes/win-x64/native')
    Library.version()
    from maa.controller import CustomController
    from maa.resource import Resource
    from maa.tasker import Tasker
    from maa.pipeline import JFeatureMatch

    class ReadOnlyController(CustomController):
        def connect(self): return True
        def request_uuid(self): return 'reward-drink-offline'
        def screencap(self): return np.zeros((1280, 720, 3), np.uint8)
        def get_features(self): return 0
        def start_app(self, *args): return False
        def stop_app(self, *args): return False
        def click(self, *args): return False
        def swipe(self, *args): return False
        def touch_down(self, *args): return False
        def touch_move(self, *args): return False
        def touch_up(self, *args): return False
        def click_key(self, *args): return False
        def input_text(self, *args): return False
        def key_down(self, *args): return False
        def key_up(self, *args): return False

    resource = Resource()
    assert resource.post_bundle(args.portable / 'resource/base').wait().status.succeeded
    controller = ReadOnlyController()
    assert controller.post_connection().wait().status.succeeded
    tasker = Tasker()
    assert tasker.bind(resource, controller)
    source = Image.open(args.image).convert('RGB')
    if args.crop:
        source = source.crop(tuple(args.crop))
    frame = np.asarray(source.resize((720, 1280)))[:, :, ::-1].copy()
    if args.duplicate:
        frame[1150:1250, 273:349] = frame[1150:1250, 191:267]
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent'))
    with patch('maa.agent.agent_server.AgentServer.custom_action', lambda _: lambda cls: cls), \
         patch('maa.agent.agent_server.AgentServer.custom_recognition', lambda _: lambda cls: cls):
        from custom.action.produce import ProduceChooseRewardAuto as Action
        from custom.drink_policy import TARGET_NAMES

    class Context:
        calls = 0
        tasker = SimpleNamespace(stopping=False, controller=SimpleNamespace(
            post_screencap=lambda: SimpleNamespace(wait=lambda: SimpleNamespace(get=lambda: frame.copy()))))

        def run_recognition(self, name, pixels, pipeline_override=None):
            self.calls += 1
            if not pipeline_override:
                recognition = resource.get_node_object(name).recognition
                detail = tasker.post_recognition(recognition.type, recognition.param, pixels).wait().get()
                return detail.nodes[0].recognition
            parameters = dict((pipeline_override or {}).get(name, {}))
            allowed = {field.name for field in dataclasses.fields(JFeatureMatch)}
            parameters = {key: value for key, value in parameters.items() if key in allowed}
            detail = tasker.post_recognition('FeatureMatch', JFeatureMatch(**parameters), pixels).wait().get()
            result = detail.nodes[0].recognition
            if result.hit:
                print(json.dumps(dict(roi=parameters['roi'], template=parameters['template'],
                                      rows=[str(row) for row in result.filtered_results]), ensure_ascii=False), flush=True)
            return result

    context = Context()
    start = time.perf_counter()
    hits = []
    slots = list(Action.REWARD_SLOTS) + [[x-38, 1150, 76, 100] for x, _ in Action.INVENTORY_SLOT_CENTERS]
    for index, roi in enumerate(slots):
        for drink_id in TARGET_NAMES:
            detail = context.run_recognition('ProduceRecognitionShopDrinks', frame, pipeline_override={
                'ProduceRecognitionShopDrinks': {'roi': roi, 'template': [f'produce/drinks/{drink_id}.png'],
                                                'count': 4, 'ratio': .62}})
            if detail.hit:
                for row in detail.filtered_results:
                    box = Action._normalize_result_box(row, roi)
                    x,y,w,h = box
                    if w > 0 and h > 0 and roi[0] <= x+w//2 <= roi[0]+roi[2] and roi[1] <= y+h//2 <= roi[1]+roi[3]:
                        hits.append((index, drink_id, box, row.count))
    print(json.dumps(dict(mode='baseline', seconds=time.perf_counter()-start, calls=context.calls,
                         hits=hits), ensure_ascii=False), flush=True)
    context.calls = 0
    start = time.perf_counter()
    result = Action._scan_target_drinks(context, frame)
    print(json.dumps(dict(mode='runtime', seconds=time.perf_counter()-start, calls=context.calls,
                         result=result), ensure_ascii=False), flush=True)
    context.calls = 0
    start = time.perf_counter()
    _, state = Action._stable_drink_state(context, frame)
    print(json.dumps(dict(mode='runtime-stable', seconds=time.perf_counter()-start, calls=context.calls,
                         state=state), ensure_ascii=False, default=list), flush=True)


if __name__ == '__main__':
    main()

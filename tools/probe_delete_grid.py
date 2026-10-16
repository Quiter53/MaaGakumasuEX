"""Replay original-model deck boxes from saved screenshots; no live input."""
import argparse
import dataclasses
import json
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image
from maa.library import Library


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('portable', type=Path)
    parser.add_argument('image', type=Path)
    parser.add_argument('--crop', type=int, nargs=4)
    parser.add_argument('--top', type=int, default=380)
    parser.add_argument('--identities', action='store_true')
    args = parser.parse_args()
    Library.open(args.portable / 'runtimes/win-x64/native')
    Library.version()
    from maa.controller import CustomController
    from maa.resource import Resource
    from maa.tasker import Tasker
    from maa.pipeline import JNeuralNetworkDetect

    source = Image.open(args.image).convert('RGB')
    if args.crop:
        source = source.crop(tuple(args.crop))
    image = np.asarray(source.resize((720, 1280)))[:, :, ::-1].copy()

    class ReadOnlyController(CustomController):
        def connect(self): return True
        def request_uuid(self): return 'deck-grid-offline'
        def screencap(self): return image.copy()
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
    params = resource.get_node_data('ProduceRecognitionCards')['recognition']['param']
    fields = {field.name for field in dataclasses.fields(JNeuralNetworkDetect)}
    params = {key: value for key, value in params.items() if key in fields}
    found = tasker.post_recognition('NeuralNetworkDetect', JNeuralNetworkDetect(**params), image).wait().get()
    raw = [dict(label=r.label, score=r.score, box=list(r.box)) for r in found.nodes[0].recognition.all_results]
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent'))
    with patch('maa.agent.agent_server.AgentServer.custom_action', lambda _: lambda cls: cls), \
         patch('maa.agent.agent_server.AgentServer.custom_recognition', lambda _: lambda cls: cls):
        from custom.deck_scan import complete_grid_rows, normalize_deck_boxes, deck_view_complete, deck_scrollbar_position
    boxes = [item['box'] for item in raw if item['label'] in ('cards', 'suggestions', 'useless')
             and 50 <= item['box'][0] and item['box'][0] + item['box'][2] <= 675
             and args.top <= item['box'][1] and item['box'][1] + item['box'][3] <= 1100
             and 90 <= item['box'][2] <= 145 and 90 <= item['box'][3] <= 145
             and .80 <= item['box'][2] / item['box'][3] <= 1.25]
    old = complete_grid_rows(image, boxes, top=args.top)
    final = normalize_deck_boxes(image, [item['box'] for item in raw
                                 if item['label'] in ('cards', 'suggestions', 'useless')], top=args.top)
    print(json.dumps(dict(raw=raw, filtered=boxes, old_complete=old, complete=final,
                          old_count=len(old), count=len(final),
                          complete_view=deck_view_complete(image, final, change=args.top==620),
                          scrollbar=deck_scrollbar_position(image, top=args.top)), ensure_ascii=False))
    if args.identities:
        from custom.produce.rewards import ProduceChooseRewardAuto
        from custom.deck_scan import scan_identity_known
        items = ProduceChooseRewardAuto()._evaluate(None, image, verified_boxes=final,
                                                   read_extra_action=False, diagnostics=False)
        print(json.dumps(dict(identities=[dict(name=item['name'], box=item['box'],
              known=scan_identity_known(item), confidence=item['name_confidence'],
              score=item.get('square_art_score', 0), margin=item.get('square_art_margin', 0),
              frame_rarity=item['frame_rarity'], rarity=item['rarity']) for item in items],
              known_count=sum(scan_identity_known(item) for item in items)), ensure_ascii=False))


if __name__ == '__main__':
    main()

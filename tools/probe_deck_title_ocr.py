"""Read-only native OCR comparison; input methods refuse all game interaction."""
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
    parser.add_argument('--numpy', action='store_true', help='Use the runtime dependency-free 2x bilinear retry')
    parser.add_argument('--runtime', action='store_true', help='Run the actual source title identity reader, then exit')
    args = parser.parse_args()
    Library.open(args.portable / 'runtimes/win-x64/native')
    Library.version()
    from maa.controller import CustomController
    from maa.resource import Resource
    from maa.tasker import Tasker
    from maa.pipeline import JOCR

    native = np.load(args.image, allow_pickle=False) if args.image.suffix == '.npy' else None
    source = Image.open(args.image).convert('RGB') if native is None else None
    if args.crop and source is not None:
        source = source.crop(tuple(args.crop))

    class ReadOnlyController(CustomController):
        def connect(self): return True
        def request_uuid(self): return 'deck-title-offline'
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

    if args.numpy or args.runtime:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent'))
        # Import the exact runtime helper without registering live actions.
        with patch('maa.agent.agent_server.AgentServer.custom_action', lambda _: lambda cls: cls), \
             patch('maa.agent.agent_server.AgentServer.custom_recognition', lambda _: lambda cls: cls):
            from custom.removal import _double_title_crop, read_selected_card

    class ReadOnlyContext:
        def run_recognition(self, name, pixels, pipeline_override=None):
            parameters = dict((pipeline_override or {}).get(name, {}))
            allowed = {field.name for field in dataclasses.fields(JOCR)}
            parameters = {key: value for key, value in parameters.items() if key in allowed}
            detail = tasker.post_recognition('OCR', JOCR(**parameters), pixels).wait().get()
            result = detail.nodes[0].recognition
            print(json.dumps(dict(mode='runtime-read', shape=list(pixels.shape), parameters=parameters,
                                  rows=[str(row) for row in result.all_results]), ensure_ascii=False), flush=True)
            return result

    def read(image, roi, only=False):
        detail = tasker.post_recognition('OCR', JOCR(roi=roi, expected=['.*'], only_rec=only), image).wait().get()
        return detail.nodes[0].recognition.all_results

    for height in ((native.shape[0],) if native is not None else (1279, 1280)):
        frame = native if native is not None else np.asarray(source.resize((720, height)))[:, :, ::-1].copy()
        if args.runtime:
            identity = read_selected_card(ReadOnlyContext(), frame, change=True)
            print(json.dumps(dict(height=height, mode='runtime-identity', identity=identity), ensure_ascii=False), flush=True)
            continue
        detected = read(frame, [175, 250, 490, 65])
        print(json.dumps(dict(height=height, mode='detect', rows=[str(row) for row in detected]), ensure_ascii=False), flush=True)
        for row in detected:
            x, y, w, h = map(int, row.box)
            for padding in (0, 2, 4, 6, 8):
                roi = [x-padding, y-padding, w+padding*2, h+padding*2]
                for scale in (1, 2):
                    pixels = frame
                    location = roi
                    if scale == 2:
                        if args.numpy:
                            pixels = _double_title_crop(frame, roi)
                            location = [0, 0, pixels.shape[1], pixels.shape[0]]
                        else:
                            pixels = np.asarray(Image.fromarray(frame).resize((1440, height*2), Image.Resampling.BILINEAR))
                            location = [v*2 for v in roi]
                    result = read(pixels, location, only=True)
                    print(json.dumps(dict(height=height, mode='only_rec', padding=padding, scale=scale,
                                          rows=[str(r) for r in result]), ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()

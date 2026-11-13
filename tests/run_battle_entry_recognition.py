"""Replay the real battle entry on Lab images; no game connection or input."""
import argparse
import dataclasses
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PIL import Image
from maa.library import Library


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixtures', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--feedback', type=Path)
    parser.add_argument('--crop', type=int, nargs=4)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    args.output.mkdir(parents=True, exist_ok=True)
    Library.version()
    from maa.toolkit import Toolkit
    from maa.controller import CustomController
    from maa.resource import Resource
    from maa.tasker import Tasker
    from maa.pipeline import JOCR, JTemplateMatch, JNeuralNetworkDetect, JCustomRecognition
    Toolkit.init_option(str(args.output))
    resource = Resource()
    resource.use_cpu()
    assert resource.post_bundle(str(root / 'assets/resource/base')).wait().succeeded
    frame = np.zeros((1280, 720, 3), np.uint8)
    clicks = []

    class Offline(CustomController):
        def connect(self): return True
        def request_uuid(self): return 'lab-battle-entry-offline'
        def screencap(self): return frame.copy()
        def get_features(self): return 0
        def click(self, x, y):
            clicks.append([x, y])  # Captured only; never forwarded to a device.
            return True
        def swipe(self, *args): raise AssertionError('Unexpected swipe')

    controller = Offline()
    assert controller.post_connection().wait().succeeded
    tasker = Tasker()
    assert tasker.bind(resource, controller)

    class Context:
        def __init__(self):
            self.tasker = SimpleNamespace(controller=controller, stopping=False)

        def run_recognition(self, name, image, pipeline_override=None):
            node = resource.get_node_data(name)['recognition']
            override = (pipeline_override or {}).get(name, {})
            kind = override.get('recognition', node['type'])
            param = {**node['param'], **override}
            cls = {'OCR': JOCR, 'TemplateMatch': JTemplateMatch, 'NeuralNetworkDetect': JNeuralNetworkDetect,
                   'Custom': JCustomRecognition}[kind]
            param = {k: v for k, v in param.items() if k in {f.name for f in dataclasses.fields(cls)}}
            if cls is JTemplateMatch:
                for key in ('template', 'threshold'):
                    if key in param and not isinstance(param[key], list):
                        param[key] = [param[key]]
            return tasker.post_recognition(kind, cls(**param), image).wait().get().nodes[0].recognition

    sys.path.insert(0, str(root / 'agent'))
    def register(name):
        def decorator(cls):
            assert resource.register_custom_recognition(name, cls())
            return cls
        return decorator
    with patch('maa.agent.agent_server.AgentServer.custom_action', lambda _: lambda cls: cls), \
         patch('maa.agent.agent_server.AgentServer.custom_recognition', register):
        from custom.produce.battle import ProduceCardsAuto
    cases = [(p, None, True) for p in sorted((args.fixtures / 'hand').glob('*.png'))]
    assert len(cases) >= 10, 'Lab hand fixtures missing'
    cases.append((args.fixtures / 'deck/owned-drawer-24.png', None, False))
    if args.feedback:
        assert args.crop, 'Feedback desktop screenshot requires an observed client crop'
        cases.append((args.feedback, args.crop, True))
    context = Context()
    records = []
    for path, crop, expected in cases:
        with Image.open(path) as image:
            image = image.convert('RGB')
            if crop:
                image = image.crop(crop).resize((720, 1279))
            assert image.width == 720 and image.height in (1279, 1280)
            frame = np.asarray(image)[:, :, ::-1].copy()
        old = context.run_recognition('ProduceRecognitionSkipRound', frame, {'ProduceRecognitionSkipRound': {
            'recognition': 'TemplateMatch', 'roi': [590, 720, 130, 120], 'threshold': 0.7,
            'template': ['produce/skip_round.png', 'produce/skip_round_1.png', 'produce/skip_round_2.png']}})
        actual = context.run_recognition('ProduceRecognitionSkipRound', frame)
        assert bool(actual.hit) == expected, path.name
        recorded_click = None
        if expected:
            action = ProduceCardsAuto()
            action._rule_preset = lambda: {'rules': [], 'hand': []}
            before = len(clicks)
            assert action._wait_until_playable(context), path.name
            assert len(clicks) == before, 'Entry observation attempted a click'
            assert tasker.post_task('ProduceRecognitionSkipRound').wait().succeeded
            assert len(clicks) == before + 1
            recorded_click = clicks[-1]
            assert 620 <= recorded_click[0] <= 710 and 746 <= recorded_click[1] <= 825
        record = {'file': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                  'resampledFeedback': bool(crop), 'expected': expected, 'oldHit': bool(old.hit),
                  'oldScores': [r.score for r in old.all_results], 'newHit': bool(actual.hit),
                  'ocr': [{'text': r.text, 'score': r.score, 'box': list(r.box)} for r in actual.all_results],
                  'simulatedClick': recorded_click}
        records.append(record)
        print(json.dumps(record, ensure_ascii=False), flush=True)
    report = {'sourceCommit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(),
              'pipelineSha256': hashlib.sha256((root / 'assets/resource/base/pipeline/ProduceUtils.json').read_bytes()).hexdigest(),
              'passed': True, 'cases': records, 'input': 'offline controller only; no game input'}
    (args.output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), 'utf-8')


if __name__ == '__main__':
    main()

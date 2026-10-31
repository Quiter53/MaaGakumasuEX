"""Prepare the verified portable dependency baseline used by build_ex_release.py.

The baseline is an already published EX portable package. Its ZIP digest is pinned
here, so a replaced or modified release asset is rejected instead of silently used.
The archive is extracted into a new, empty directory (never an install or dev tree).
Use Python 3.12+. Requires `gh` (with GITHUB_TOKEN / GH_TOKEN) unless --zip is given.
"""
import argparse
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path
from zipfile import ZipFile

# tag -> (asset name, SHA-256 of the published ZIP, source commit recorded in PORTABLE_BUILD.json)
BASELINES = {
    'v2.1.5': ('MaaGakumasuEX-v2.1.5-win-x64-portable.zip',
               'df107b53a0bb93f863c7d2fa913d7dfb8181d1b5b2881361845d9332dcf34123',
               'd08174d3d6c0aecb34b8343397a8f19749a1e947'),
}
REPOSITORY = 'Quiter53/MaaGakumasuEX'


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def prepare(tag, output, archive=None):
    name, expected, commit = BASELINES[tag]
    if output.exists():
        raise FileExistsError(output)
    with tempfile.TemporaryDirectory() as tmp:
        if archive is None:
            subprocess.run(['gh', 'release', 'download', tag, '-R', REPOSITORY, '-p', name, '-D', tmp], check=True)
            archive = Path(tmp) / name
        actual = digest(archive)
        if actual != expected:
            raise ValueError(f'baseline digest mismatch: {actual} != {expected}')
        output.mkdir(parents=True)
        with ZipFile(archive) as z:
            for member in z.namelist():
                target = (output / member).resolve()
                if not target.is_relative_to(output.resolve()):
                    raise ValueError(f'unsafe path in archive: {member}')
            z.extractall(output)
    meta = json.loads((output / 'PORTABLE_BUILD.json').read_text('utf8'))
    if meta.get('sourceCommit') != commit or meta.get('framework') != '5.13.0':
        raise ValueError(f'unexpected baseline metadata: {meta}')
    print(json.dumps({'baseline': tag, 'sha256': expected, 'sourceCommit': commit, 'output': str(output)}))


if __name__ == '__main__':
    a = argparse.ArgumentParser()
    a.add_argument('--tag', default='v2.1.5', choices=sorted(BASELINES))
    a.add_argument('--output', type=Path, required=True)
    a.add_argument('--zip', type=Path, help='use a local copy of the published ZIP')
    o = a.parse_args()
    prepare(o.tag, o.output, o.zip)

"""Manually relay an already-built package through GitHub's own runner.

Used when the maintainer can reach the Git API but not uploads.github.com.
This uploads staged assets only; channel promotion remains a separate action.
"""
import base64
import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen
from zipfile import ZipFile

REPOSITORY = 'Quiter53/MaaGakumasuEX'
API = 'https://api.github.com/repos/' + REPOSITORY


def request(url, data=None, content_type='application/json'):
    headers = {'Authorization': 'Bearer ' + os.environ['GITHUB_TOKEN'],
               'Accept': 'application/vnd.github+json', 'User-Agent': 'MaaGakumasuEX-local-package',
               'Content-Type': content_type}
    if hasattr(data, 'fileno'):
        headers['Content-Length'] = str(os.fstat(data.fileno()).st_size)
    with urlopen(Request(url, data=data, headers=headers), timeout=180) as response:
        return json.load(response)


def blob(sha):
    if not re.fullmatch('[0-9a-f]{40}', sha):
        raise ValueError('Invalid manifest/chunk Git SHA')
    entry = request(API + '/git/blobs/' + sha)
    if entry['encoding'] != 'base64':
        raise ValueError('Unexpected blob encoding')
    data = base64.b64decode(entry['content'])
    actual = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
    if actual != sha:
        raise ValueError('Git blob hash mismatch')
    return data


def main():
    manifest = json.loads(blob(os.environ['PACKAGE_MANIFEST_BLOB']))
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    version = json.loads(Path('assets/interface.json').read_text('utf-8'))['version']
    if manifest['repository'] != REPOSITORY or manifest['sourceCommit'] != commit or manifest['version'] != version:
        raise ValueError('Package source does not match the selected checkout')
    release = request(API + '/releases/' + str(int(manifest['releaseId'])))
    if release['tag_name'] != version:
        raise ValueError('Release tag mismatch')
    archive_name = f'MaaGakumasuEX-{version}-win-x64-portable.zip'
    if {f['name'] for f in manifest['files']} != {archive_name, archive_name + '.sha256'}:
        raise ValueError('Expected the versioned portable ZIP and checksum')
    upload_url = release['upload_url'].split('{', 1)[0]
    if not upload_url.startswith('https://uploads.github.com/repos/' + REPOSITORY + '/'):
        raise ValueError('Unexpected GitHub upload URL')
    with tempfile.TemporaryDirectory() as directory:
        for entry in manifest['files']:
            path = Path(directory) / entry['name']
            digest = hashlib.sha256()
            with path.open('wb') as stream:
                for part in entry['chunks']:
                    data = blob(part['gitSha'])
                    if len(data) != part['size'] or hashlib.sha256(data).hexdigest() != part['sha256']:
                        raise ValueError('Package chunk mismatch')
                    stream.write(data)
                    digest.update(data)
            if path.stat().st_size != entry['size'] or digest.hexdigest() != entry['sha256']:
                raise ValueError('Reconstructed package mismatch')
            if entry['name'] == archive_name:
                with ZipFile(path) as archive:
                    metadata = json.loads(archive.read('PORTABLE_BUILD.json'))
                    if metadata['sourceCommit'] != commit or metadata['version'] != version:
                        raise ValueError('Portable metadata mismatch')
            staged_name = entry['name'] + '.staged-' + commit[:7]
            existing = next((a for a in release['assets'] if a['name'] == staged_name), None)
            if existing is None:
                with path.open('rb') as stream:
                    existing = request(upload_url + '?name=' + quote(staged_name), stream,
                                       'application/zip' if entry['name'] == archive_name else 'text/plain')
            if existing['state'] != 'uploaded' or existing['size'] != entry['size'] or existing.get('digest') != 'sha256:' + entry['sha256']:
                raise ValueError('Uploaded asset hash mismatch')
            print(json.dumps({'asset': staged_name, 'bytes': existing['size'], 'sha256': entry['sha256'], 'verified': True}), flush=True)


if __name__ == '__main__':
    main()

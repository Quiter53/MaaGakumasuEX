"""The distribution contains runtime inputs, never an installed dev workspace."""
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile


def test_portable_layout_and_private_settings_boundary(tmp_path):
    source = Path(__file__).resolve().parents[1] / 'tools/build_ex_release.py'
    spec = importlib.util.spec_from_file_location('release_builder', source)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    repo, runtime, target = (tmp_path/n for n in ('repo','installed','portable'))
    def write(root, name, data='test'):
        path = root/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data, encoding='utf8')
    for name in builder.RUNTIME_FILES:
        write(runtime,name)
    for name in ('docs/private.md','AGENTS.md','README.md','CUSTOM_CHANGELOG_zh-CN.md',
                 'config/instances/private.json','config/config.json','logs/log.txt',
                 'en-US/MFAAvalonia.Core.resources.dll','libs/MFAAvalonia.Core.dll'):
        write(runtime,name,'must not ship')
    write(runtime,'python/Lib/pkg/__init__.py','')
    write(repo,'agent/main.py')
    write(repo,'agent/custom/reco/README.md','developer note')
    write(repo,'assets/interface.json','{"version":"v2.0.3"}')
    for name in builder.DEFAULTS:
        write(repo,'assets/config/'+name,'{}')
    for name in ('LICENSE','requirements.txt'):
        write(repo,name)
    core = repo/'built/MFAAvalonia.Core.dll'
    write(repo,'built/MFAAvalonia.Core.dll','new core')
    for language in ('en-US','ja-JP','zh-Hant'):
        write(repo,f'built/{language}/MFAAvalonia.Core.resources.dll',language)
        write(runtime,f'libs/locales/{language}/MFAAvalonia.Core.resources.dll','old translation')
    with patch.object(builder,'__file__',str(repo/'tools/build_ex_release.py')), \
         patch.object(builder.subprocess,'check_output',return_value='commit\n'):
        builder.build(runtime,core,target,prerelease=True)
    with ZipFile(tmp_path/'portable.zip') as archive:
        names = set(archive.namelist())
        assert archive.testzip() is None
        assert not any(n.startswith(('docs/','logs/','en-US/','config/instances/')) for n in names)
        assert not {'AGENTS.md','README.md','RELEASE_NOTES.md','CUSTOM_CHANGELOG_zh-CN.md',
                    'FILES_SHA256.json','MFAAvalonia.Core.dll','agent/custom/reco/README.md'} & names
        assert archive.read('libs/MFAAvalonia.Core.dll') == b'new core'
        for language in ('en-US','ja-JP','zh-Hant'):
            assert archive.read(f'libs/locales/{language}/MFAAvalonia.Core.resources.dll').decode() == language
        assert archive.read('python/Lib/pkg/__init__.py') == b''
        assert json.loads(archive.read('config/config.json')) == {
            'ResourceUpdateChannelIndex':1,'UIUpdateChannelIndex':1,'ResourceUpdateChannelInitialized':True}
        assert json.loads(archive.read('config/card_lists.json')) == {
            'debugDiagnosticsEnabled':False,'detailedLogsEnabled':False}
        assert names == set(json.loads((tmp_path/'portable.zip.files.json').read_text('utf8')))

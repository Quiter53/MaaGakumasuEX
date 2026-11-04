"""The distribution contains runtime inputs, never an installed dev workspace."""
import importlib.util
import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile
import pytest


def dependency_manifest(entries, portable=False):
    target = '.NETCoreApp,Version=v10.0' + ('/win-x64' if portable else '')
    return {'runtimeTarget': {'name': target}, 'targets': {target: entries},
            'libraries': {key: {'type': 'project' if key.startswith('MFAAvalonia.') else 'package'} for key in entries}}


@pytest.mark.parametrize('prerelease', [False, True])
def test_portable_layout_and_private_settings_boundary(tmp_path, prerelease):
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
    baseline = dependency_manifest({'MFAAvalonia.Core/1.1.0': {'runtime': {'MFAAvalonia.Core.dll': {}}}}, portable=True)
    write(runtime, 'MFAAvalonia.deps.json', json.dumps(baseline))
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
    write(repo, 'built/MFAAvalonia.Core.deps.json', json.dumps(dependency_manifest({
        'MFAAvalonia.Core/1.1': {'runtime': {'MFAAvalonia.Core.dll': {}}}})))
    for language in ('en-US','ja-JP','zh-Hant'):
        write(repo,f'built/{language}/MFAAvalonia.Core.resources.dll',language)
        write(runtime,f'libs/locales/{language}/MFAAvalonia.Core.resources.dll','old translation')
    with patch.object(builder,'__file__',str(repo/'tools/build_ex_release.py')), \
         patch.object(builder.subprocess,'check_output',return_value='commit\n'):
        builder.build(runtime,core,target,prerelease=prerelease)
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
            'ResourceUpdateChannelIndex':2,'UIUpdateChannelIndex':2,'ResourceUpdateChannelInitialized':True,
            'EnablePrereleaseUpdates':False}
        assert json.loads(archive.read('PORTABLE_BUILD.json'))['prerelease'] == prerelease
        assert json.loads(archive.read('config/card_lists.json')) == {
            'debugDiagnosticsEnabled':False,'detailedLogsEnabled':False}
        assert names == set(json.loads((tmp_path/'portable.zip.files.json').read_text('utf8')))


@pytest.fixture
def managed_dependencies(tmp_path):
    source = Path(__file__).resolve().parents[1] / 'tools/build_ex_release.py'
    spec = importlib.util.spec_from_file_location('release_builder_dependencies', source)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    runtime, built, target = (tmp_path / name for name in ('baseline', 'built', 'staged'))
    for directory in (runtime, built, target / 'libs', built / 'runtimes/win/lib/net10.0'):
        directory.mkdir(parents=True)
    core = built / 'MFAAvalonia.Core.dll'
    core.write_bytes(b'new core')
    old = dependency_manifest({
        'MFAAvalonia.Desktop/1.0': {'dependencies': {'MailKit': '4.14.1', 'MFAAvalonia.Core': '1.1.0'}},
        'MFAAvalonia.Core/1.1.0': {'dependencies': {'MailKit': '4.14.1'}, 'runtime': {'MFAAvalonia.Core.dll': {}}},
        'MailKit/4.14.1': {'runtime': {'lib/net8.0/MailKit.dll': {}}},
        'System.Security.Cryptography.Pkcs/8.0.1': {'runtime': {'runtimes/win/lib/net8.0/System.Security.Cryptography.Pkcs.dll': {}}},
        'SamePackage/1.0': {'runtime': {'lib/net8.0/SamePackage.dll': {}}},
        'Maa.Framework.Native/5.8.0': {'native': {'runtimes/win-x64/native/MaaFramework.dll': {}}},
        'libloader': {'runtime': {'libloader.dll': {}}},
    }, portable=True)
    new = dependency_manifest({
        'MFAAvalonia.Core/1.1': {'dependencies': {'MailKit': '4.16.0', 'System.Security.Cryptography.Pkcs': '10.0.13'},
                               'runtime': {'MFAAvalonia.Core.dll': {}}},
        'MailKit/4.16.0': {'dependencies': {'NewDependency': '2.0'}, 'runtime': {'lib/net10.0/MailKit.dll': {'assemblyVersion': '4.16.0.0'}}},
        'System.Security.Cryptography.Pkcs/10.0.13': {
            'runtime': {'lib/net10.0/System.Security.Cryptography.Pkcs.dll': {'assemblyVersion': '10.0.0.0'}},
            'runtimeTargets': {'runtimes/win/lib/net10.0/System.Security.Cryptography.Pkcs.dll': {
                'assetType': 'runtime', 'rid': 'win', 'assemblyVersion': '10.0.0.0', 'fileVersion': '10.0.1326.0'}}},
        'NewDependency/2.0': {'runtime': {'lib/net10.0/NewDependency.dll': {}}},
        'SamePackage/1.0': {'runtime': {'lib/net8.0/SamePackage.dll': {}}},
        'Maa.Framework.Native/5.8.0': {'native': {'runtimes/win-x64/native/MaaFramework.dll': {}}},
        'SkiaSharp.NativeAssets.Linux/3.119.1': {'runtimeTargets': {
            'runtimes/linux-x64/native/libSkiaSharp.so': {'assetType': 'native', 'rid': 'linux-x64'}}},
    })
    (runtime / 'MFAAvalonia.deps.json').write_text(json.dumps(old), encoding='utf8')
    core.with_suffix('.deps.json').write_text(json.dumps(new), encoding='utf8')
    for name, content in [('MailKit.dll', 'new mail'), ('NewDependency.dll', 'new dependency'),
                          ('SamePackage.dll', 'do not replace'), ('System.Security.Cryptography.Pkcs.dll', 'generic pkcs'),
                          ('runtimes/win/lib/net10.0/System.Security.Cryptography.Pkcs.dll', 'windows pkcs')]:
        (built / name).write_text(content, encoding='utf8')
    for name in ('MailKit.dll', 'SamePackage.dll', 'MaaFramework.dll'):
        (target / 'libs' / name).write_text('baseline', encoding='utf8')
    return builder, runtime, core, target, old, new


def test_changed_and_new_managed_packages_ship_with_matching_dependency_graph(managed_dependencies):
    builder, runtime, core, target, old, new = managed_dependencies
    builder.sync_managed_dependencies(runtime, core, target)
    manifest = json.loads((target / 'MFAAvalonia.deps.json').read_text('utf8'))
    entries = manifest['targets'][manifest['runtimeTarget']['name']]
    assert 'MailKit/4.14.1' not in entries and 'MailKit/4.14.1' not in manifest['libraries']
    assert entries['MailKit/4.16.0']['dependencies']['NewDependency'] == '2.0'
    assert entries['MFAAvalonia.Desktop/1.0']['dependencies']['MailKit'] == '4.16.0'
    assert entries['MFAAvalonia.Core/1.1.0']['dependencies']['System.Security.Cryptography.Pkcs'] == '10.0.13'
    assert (target / 'libs/MailKit.dll').read_text('utf8') == 'new mail'
    assert (target / 'libs/NewDependency.dll').read_text('utf8') == 'new dependency'
    assert (target / 'libs/System.Security.Cryptography.Pkcs.dll').read_text('utf8') == 'windows pkcs'
    assert 'runtimeTargets' not in entries['System.Security.Cryptography.Pkcs/10.0.13']
    assert entries['Maa.Framework.Native/5.8.0'] == old['targets'][old['runtimeTarget']['name']]['Maa.Framework.Native/5.8.0']
    assert entries['libloader'] == old['targets'][old['runtimeTarget']['name']]['libloader']
    assert (target / 'libs/SamePackage.dll').read_text('utf8') == 'baseline'
    assert (target / 'libs/MaaFramework.dll').read_text('utf8') == 'baseline'
    assert not any('Linux' in name for name in entries)


@pytest.mark.parametrize('manifest', ['built', 'baseline'])
def test_missing_dependency_manifest_stops_packaging(managed_dependencies, manifest):
    builder, runtime, core, target, old, new = managed_dependencies
    path = core.with_suffix('.deps.json') if manifest == 'built' else runtime / 'MFAAvalonia.deps.json'
    path.unlink()
    with pytest.raises(FileNotFoundError):
        builder.sync_managed_dependencies(runtime, core, target)
    assert (target / 'libs/MailKit.dll').read_text('utf8') == 'baseline'


def test_missing_windows_assembly_cannot_fall_back_to_generic_or_old_binary(managed_dependencies):
    builder, runtime, core, target, old, new = managed_dependencies
    (core.parent / 'runtimes/win/lib/net10.0/System.Security.Cryptography.Pkcs.dll').unlink()
    with pytest.raises(FileNotFoundError, match='CopyLocalLockFileAssemblies'):
        builder.sync_managed_dependencies(runtime, core, target)
    assert (target / 'libs/MailKit.dll').read_text('utf8') == 'baseline'
    assert not (target / 'MFAAvalonia.deps.json').exists()


def test_new_native_dependency_is_rejected_before_any_managed_replacement(managed_dependencies):
    builder, runtime, core, target, old, new = managed_dependencies
    entries = new['targets'][new['runtimeTarget']['name']]
    entries['MailKit/4.16.0']['native'] = {'runtimes/win-x64/native/new-native.dll': {}}
    core.with_suffix('.deps.json').write_text(json.dumps(new), encoding='utf8')
    with pytest.raises(ValueError, match='separately verified baseline'):
        builder.sync_managed_dependencies(runtime, core, target)
    assert (target / 'libs/MailKit.dll').read_text('utf8') == 'baseline'


@pytest.mark.parametrize('rid', [None, 'win', 'win-x64'])
def test_native_only_windows_package_cannot_be_silently_ignored(managed_dependencies, rid):
    builder, runtime, core, target, old, new = managed_dependencies
    entries = new['targets'][new['runtimeTarget']['name']]
    asset = 'runtimes/win-x64/native/NativeOnly.dll'
    entries['NativeOnly/2.0'] = {'native': {asset: {}}} if rid is None else {
        'runtimeTargets': {asset: {'assetType': 'native', 'rid': rid}}}
    new['libraries']['NativeOnly/2.0'] = {'type': 'package'}
    core.with_suffix('.deps.json').write_text(json.dumps(new), encoding='utf8')
    with pytest.raises(ValueError, match='separately verified baseline: NativeOnly/2.0'):
        builder.sync_managed_dependencies(runtime, core, target)
    assert (target / 'libs/MailKit.dll').read_text('utf8') == 'baseline'
    assert not (target / 'MFAAvalonia.deps.json').exists()


def test_unresolved_transitive_dependency_stops_packaging(managed_dependencies):
    builder, runtime, core, target, old, new = managed_dependencies
    entries = new['targets'][new['runtimeTarget']['name']]
    entries['MailKit/4.16.0']['dependencies']['MissingPackage'] = '1.0'
    core.with_suffix('.deps.json').write_text(json.dumps(new), encoding='utf8')
    with pytest.raises(ValueError, match='Unresolved managed dependencies'):
        builder.sync_managed_dependencies(runtime, core, target)
    assert (target / 'libs/MailKit.dll').read_text('utf8') == 'baseline'


def test_non_rid_dependency_target_has_no_stale_library_versions(managed_dependencies):
    builder, runtime, core, target, old, new = managed_dependencies
    neutral_name = '.NETCoreApp,Version=v10.0'
    old['targets'][neutral_name] = deepcopy(old['targets'][old['runtimeTarget']['name']])
    (runtime / 'MFAAvalonia.deps.json').write_text(json.dumps(old), encoding='utf8')
    builder.sync_managed_dependencies(runtime, core, target)
    manifest = json.loads((target / 'MFAAvalonia.deps.json').read_text('utf8'))
    for entries in manifest['targets'].values():
        assert 'MailKit/4.14.1' not in entries
        assert entries['MFAAvalonia.Desktop/1.0']['dependencies']['MailKit'] == '4.16.0'
        assert entries['MFAAvalonia.Core/1.1.0']['dependencies']['System.Security.Cryptography.Pkcs'] == '10.0.13'
        assert set(entries) <= manifest['libraries'].keys()


def test_rid_build_uses_flattened_platform_assembly(managed_dependencies):
    builder, runtime, core, target, old, new = managed_dependencies
    entries = new['targets'][new['runtimeTarget']['name']]
    pkcs = entries['System.Security.Cryptography.Pkcs/10.0.13']
    windows_asset, metadata = next(iter(pkcs.pop('runtimeTargets').items()))
    pkcs['runtime'] = {windows_asset: {key: value for key, value in metadata.items() if key not in ('rid', 'assetType')}}
    new['runtimeTarget']['name'] += '/win-x64'
    new['targets'] = {new['runtimeTarget']['name']: entries}
    core.with_suffix('.deps.json').write_text(json.dumps(new), encoding='utf8')
    (core.parent / 'System.Security.Cryptography.Pkcs.dll').write_text('flattened windows pkcs', encoding='utf8')
    (core.parent / windows_asset).unlink()
    builder.sync_managed_dependencies(runtime, core, target)
    assert (target / 'libs/System.Security.Cryptography.Pkcs.dll').read_text('utf8') == 'flattened windows pkcs'

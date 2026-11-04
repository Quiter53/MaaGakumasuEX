"""Stage an EX release from a verified portable dependency baseline.

Use Python 3.12+. Never put machine config or logs in a public release archive.
The --runtime directory is an installed, validated dependency baseline, and
--ui-core must be built from this repository's native-ui source.
"""
import argparse
import hashlib
import json
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

EXCLUDE={'debug','logs','backup','backups','temp','dev','rollback','__pycache__','.git'}
DEFAULTS=('card_library.json','card_lists.json','card_rules.json','drink_policy.json')
RUNTIME_DIRS=('libs','python','runtimes','plugins')
RUNTIME_FILES=('MaaGakumasu.exe','MFAAvalonia.dll','MFAAvalonia.deps.json',
               'MFAAvalonia.runtimeconfig.json','libloader.dll',
               'DependencySetup_依赖库安装_win.bat','logo.ico')

def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def windows_managed_assets(entry):
    """Resolve the managed assets that the win-x64 host would select."""
    assets = {Path(path).name: (path, metadata) for path, metadata in entry.get('runtime', {}).items()
              if path.endswith('.dll')}
    for rid in ('win', 'win-x64'):
        for path, metadata in entry.get('runtimeTargets', {}).items():
            if metadata.get('assetType') == 'runtime' and metadata.get('rid') == rid and path.endswith('.dll'):
                assets[Path(path).name] = (path, {key: value for key, value in metadata.items() if key not in ('rid', 'assetType')})
    return assets


def sync_managed_dependencies(runtime, core, target):
    """Refresh changed managed packages while retaining the native runtime baseline."""
    built = json.loads(core.with_suffix('.deps.json').read_text('utf8'))
    portable = json.loads((runtime / 'MFAAvalonia.deps.json').read_text('utf8'))
    built_target = built['targets'][built['runtimeTarget']['name']]
    built_for_windows = built['runtimeTarget']['name'].endswith('/win-x64')
    target_name = portable['runtimeTarget']['name']
    if not target_name.endswith('/win-x64'):
        raise ValueError('The portable dependency baseline must target win-x64')
    entries = portable['targets'][target_name]
    original = {key.rsplit('/', 1)[0]: key for key in entries}
    replacements = {}
    copies = {}
    for key, entry in built_target.items():
        name = key.rsplit('/', 1)[0]
        if built['libraries'][key]['type'] != 'package' or original.get(name) == key:
            continue
        if entry.get('native') or any(
                asset.get('assetType') == 'native' and asset.get('rid') in ('win', 'win-x64')
                for asset in entry.get('runtimeTargets', {}).values()):
            raise ValueError(f'Native dependency changes require a separately verified baseline: {key}')
        assets = windows_managed_assets(entry)
        if not assets:
            continue
        if name.startswith('Maa.'):
            raise ValueError(f'Native dependency changes require a separately verified baseline: {key}')
        updated = deepcopy(entry)
        updated.pop('runtimeTargets', None)
        updated['runtime'] = {}
        for filename, (asset, metadata) in assets.items():
            relative = Path(asset)
            compiled = core.parent / relative if asset.startswith('runtimes/') and not built_for_windows else core.parent / filename
            if not compiled.resolve().is_relative_to(core.parent.resolve()):
                raise ValueError(f'Dependency path escapes the build output: {asset}')
            if not compiled.is_file():
                raise FileNotFoundError(f'Missing compiled dependency {compiled}; build with CopyLocalLockFileAssemblies=true')
            if filename in copies and copies[filename] != compiled:
                raise ValueError(f'Conflicting compiled dependency: {filename}')
            copies[filename] = compiled
            updated['runtime'][asset] = metadata
        replacements[name] = (key, updated)

    for name, (key, entry) in replacements.items():
        for old_key in list(portable['libraries']):
            if old_key.rsplit('/', 1)[0] == name:
                del portable['libraries'][old_key]
        portable['libraries'][key] = deepcopy(built['libraries'][key])

    built_core = next((entry for key, entry in built_target.items() if key.rsplit('/', 1)[0] == 'MFAAvalonia.Core'), None)
    if 'MFAAvalonia.Core' not in original or built_core is None:
        raise ValueError('Both dependency manifests must describe MFAAvalonia.Core')
    for target_entries in portable['targets'].values():
        if not target_entries:
            continue
        for name, (key, entry) in replacements.items():
            for old_key in list(target_entries):
                if old_key.rsplit('/', 1)[0] == name:
                    del target_entries[old_key]
            target_entries[key] = deepcopy(entry)
        versions = {key.rsplit('/', 1)[0]: key.rsplit('/', 1)[1] for key in target_entries if '/' in key}
        for key, entry in target_entries.items():
            if key.rsplit('/', 1)[0] == 'MFAAvalonia.Core':
                # Cross-platform build-only dependencies have no entry in the Windows package.
                entry['dependencies'] = {name: versions[name] for name in built_core.get('dependencies', {}) if name in versions}
            for name in entry.get('dependencies', {}):
                if name in replacements:
                    entry['dependencies'][name] = versions[name]
        for key, entry in replacements.values():
            missing = set(entry.get('dependencies', {})) - versions.keys()
            if missing:
                raise ValueError(f'Unresolved managed dependencies for {key}: {sorted(missing)}')
    # Validate every input before changing any DLL or manifest in the staged package.
    for filename, compiled in copies.items():
        shutil.copy2(compiled, target / 'libs' / filename)
    (target / 'MFAAvalonia.deps.json').write_text(json.dumps(portable, ensure_ascii=False, indent=2), encoding='utf8')


def build(runtime,core,target,prerelease=False):
    repo=Path(__file__).resolve().parents[1]
    if target.exists():raise FileExistsError(target)
    # Explicit runtime inputs prevent installed developer files or private
    # settings from silently entering future distribution archives.
    target.mkdir(parents=True)
    for name in RUNTIME_DIRS:
        if (runtime/name).exists():
            shutil.copytree(runtime/name,target/name,
                ignore=shutil.ignore_patterns(*EXCLUDE,'*.pyc','*.pyo','*.bak'))
    for name in RUNTIME_FILES:shutil.copy2(runtime/name,target/name)
    shutil.copytree(repo/'agent',target/'agent',
        ignore=shutil.ignore_patterns('__pycache__','*.pyc','*.md'))
    shutil.copytree(repo/'assets',target,dirs_exist_ok=True)
    # Replace the original dependency instead of shadowing it at the root.
    shutil.copy2(core,target/'libs/MFAAvalonia.Core.dll')
    sync_managed_dependencies(runtime, core, target)
    for language in ('en-US', 'ja-JP', 'zh-Hant'):
        satellite = core.parent / language / 'MFAAvalonia.Core.resources.dll'
        if not satellite.exists():
            raise FileNotFoundError(satellite)
        destination=target/'libs/locales'/language
        destination.mkdir(parents=True,exist_ok=True)
        shutil.copy2(satellite, destination/satellite.name)
    for name in ('LICENSE','requirements.txt'):shutil.copy2(repo/name,target/name)
    (target/'appsettings.json').write_text('{}\n',encoding='utf8')
    config=target/'config';config.mkdir(exist_ok=True)
    version=json.loads((target/'interface.json').read_text('utf8'))['version']
    for p in list(config.iterdir()):
        if p.name not in DEFAULTS:
            assert p.resolve().is_relative_to(config.resolve())
            if p.is_dir():shutil.rmtree(p)
            else:p.unlink()
    (config/'pip_config.json').write_text(json.dumps({'enable_pip_update':False,'enable_pip_install':False,'last_version':version}),encoding='utf8')
    (config/'maa_option.json').write_text(json.dumps({'save_draw':False,'save_on_error':False}),encoding='utf8')
    (config/'config.json').write_text(json.dumps({
        'ResourceUpdateChannelIndex':2,
        'UIUpdateChannelIndex':2,
        'ResourceUpdateChannelInitialized':True,
        'EnablePrereleaseUpdates':False,
    }),encoding='utf8')
    path=config/'card_lists.json';lists=json.loads(path.read_text('utf8'));lists['debugDiagnosticsEnabled']=False;lists['detailedLogsEnabled']=False;path.write_text(json.dumps(lists,ensure_ascii=False,indent=2),encoding='utf8')
    for name in ('FILES_SHA256.json','PORTABLE_BUILD.json'):
        (target/name).unlink(missing_ok=True)
    model=target/'resource/base/model/detect/cards.onnx'
    native=target/'runtimes/win-x64/native';sdk=target/'python/Lib/site-packages/maa/bin'
    for p in sdk.rglob('*.dll'):assert digest(p)==digest(native/p.relative_to(sdk)),p
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip()
    meta={'name':'MaaGakumasu EX','version':version,'buildRevision':version+'-ex-'+commit[:7],'repository':'https://github.com/Quiter53/MaaGakumasuEX',
          'framework':'5.13.0','uiCoreSha256':digest(core),'sourceCommit':commit,
          'privateDataIncluded':False,'prerelease':prerelease}
    (target/'PORTABLE_BUILD.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf8')
    pack(target)

def pack(target):
    files=sorted(p for p in target.rglob('*') if p.is_file() and not EXCLUDE.intersection(p.relative_to(target).parts)
                 and p.suffix not in ('.pyc','.pyo','.bak') and p.name!='FILES_SHA256.json')
    hashes={p.relative_to(target).as_posix():digest(p) for p in files}
    output=target.parent/(target.name+'.zip')
    # Build evidence stays outside the user's installation directory.
    manifest=output.with_name(output.name+'.files.json')
    manifest.write_text(json.dumps(hashes,ensure_ascii=False,indent=2),encoding='utf8')
    with ZipFile(output,'x',ZIP_DEFLATED,compresslevel=6) as z:
        for p in files:z.write(p,p.relative_to(target).as_posix())
    with ZipFile(output) as z:
        assert z.testzip() is None
        assert len(z.namelist())==len(set(z.namelist()))==len(files)
        for p in files:assert hashlib.sha256(z.read(p.relative_to(target).as_posix())).hexdigest()==digest(p)
    sha=digest(output);output.with_name(output.name+'.sha256').write_text(sha+'  '+output.name+'\n',encoding='ascii')
    print(json.dumps({'archive':str(output),'files':len(files),'sha256':sha,'crcAndAllHashesVerified':True}),flush=True)

if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('--runtime',type=Path,required=True);a.add_argument('--ui-core',type=Path,required=True);a.add_argument('--output',type=Path,required=True);a.add_argument('--prerelease',action='store_true');o=a.parse_args();build(o.runtime,o.ui_core,o.output,o.prerelease)

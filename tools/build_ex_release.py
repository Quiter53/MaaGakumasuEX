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
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

EXCLUDE={'debug','logs','backup','backups','temp','dev','rollback','__pycache__','.git'}
DEFAULTS=('card_library.json','card_lists.json','card_rules.json','drink_policy.json')

def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def build(runtime,core,target,prerelease=False):
    repo=Path(__file__).resolve().parents[1]
    if target.exists():raise FileExistsError(target)
    shutil.copytree(runtime,target,ignore=shutil.ignore_patterns(*EXCLUDE,'*.pyc','*.pyo','*.bak'))
    # This directory has just been created by us. All deletions stay inside it.
    for name in ('config','agent','resource','tasks','lang','data','card_library'):
        dest=(target/name).resolve();assert dest.is_relative_to(target.resolve())
        if dest.exists():shutil.rmtree(dest)
    shutil.copytree(repo/'agent',target/'agent',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    shutil.copytree(repo/'assets',target,dirs_exist_ok=True)
    shutil.copy2(core,target/'MFAAvalonia.Core.dll')
    for language in ('en-US', 'ja-JP', 'zh-Hant'):
        satellite = core.parent / language / 'MFAAvalonia.Core.resources.dll'
        if not satellite.exists():
            raise FileNotFoundError(satellite)
        (target/language).mkdir(exist_ok=True)
        shutil.copy2(satellite, target/language/satellite.name)
    for name in ('README.md','LICENSE','requirements.txt'):shutil.copy2(repo/name,target/name)
    (target/'appsettings.json').write_text('{}\n',encoding='utf8')
    config=target/'config';config.mkdir(exist_ok=True)
    version=json.loads((target/'interface.json').read_text('utf8'))['version']
    notes=repo/'docs/releases'/f'{version}.md'
    for p in list(config.iterdir()):
        if p.name not in DEFAULTS:
            assert p.resolve().is_relative_to(config.resolve())
            if p.is_dir():shutil.rmtree(p)
            else:p.unlink()
    (config/'pip_config.json').write_text(json.dumps({'enable_pip_update':False,'enable_pip_install':False,'last_version':version}),encoding='utf8')
    (config/'maa_option.json').write_text(json.dumps({'save_draw':False,'save_on_error':False}),encoding='utf8')
    (config/'config.json').write_text(json.dumps({
        'ResourceUpdateChannelIndex':1 if prerelease else 2,
        'UIUpdateChannelIndex':1 if prerelease else 2,
        'ResourceUpdateChannelInitialized':True,
    }),encoding='utf8')
    path=config/'card_lists.json';lists=json.loads(path.read_text('utf8'));lists['debugDiagnosticsEnabled']=False;lists['detailedLogsEnabled']=False;path.write_text(json.dumps(lists,ensure_ascii=False,indent=2),encoding='utf8')
    for name in ('FILES_SHA256.json','PORTABLE_BUILD.json'):
        (target/name).unlink(missing_ok=True)
    model=target/'resource/base/model/detect/cards.onnx'
    native=target/'runtimes/win-x64/native';sdk=target/'python/Lib/site-packages/maa/bin'
    for p in sdk.rglob('*.dll'):assert digest(p)==digest(native/p.relative_to(sdk)),p
    meta={'name':'MaaGakumasu EX','version':version,'buildRevision':version+'-ex','repository':'https://github.com/Quiter53/MaaGakumasuEX',
          'framework':'5.13.0','uiCoreSha256':digest(core),'sourceCommit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip(),
          'privateDataIncluded':False,'prerelease':prerelease,'validation':f'docs/releases/{version}.md; offline/native verification, no new live cultivation'}
    (target/'PORTABLE_BUILD.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf8')
    shutil.copy2(notes,target/'RELEASE_NOTES.md')
    pack(target)

def pack(target):
    files=sorted(p for p in target.rglob('*') if p.is_file() and not EXCLUDE.intersection(p.relative_to(target).parts)
                 and p.suffix not in ('.pyc','.pyo','.bak') and p.name!='FILES_SHA256.json')
    hashes={p.relative_to(target).as_posix():digest(p) for p in files}
    manifest=target/'FILES_SHA256.json';manifest.write_text(json.dumps(hashes,ensure_ascii=False,indent=2),encoding='utf8');files.append(manifest)
    output=target.parent/(target.name+'.zip')
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

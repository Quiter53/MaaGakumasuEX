"""Build a clean portable from the custom UI baseline and merged resources.

Never modify the source runtime. Use its current presets and installed SDK,
and ship the SAME native SDK on both sides of the Agent connection.
"""

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline", type=Path)
    parser.add_argument("runtime", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--pack-only", action="store_true")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    target = args.output.resolve()
    if args.pack_only:
        pack(target)
        return
    if target.exists():
        raise FileExistsError(target)
    target.mkdir(parents=True)
    excluded = {"debug", "logs", "backup", "backups", "temp", "dev", "rollback"}
    with ZipFile(args.baseline) as archive:
        for entry in archive.infolist():
            rel = Path(entry.filename)
            if not (target / rel).resolve().is_relative_to(target):
                raise ValueError(entry.filename)
            if entry.is_dir() or rel.parts[0] in excluded or "__pycache__" in rel.parts:
                continue
            if rel.parts[0] in {"agent", "resource", "tasks", "lang", "docs"}:
                continue
            if rel.as_posix().startswith("python/Lib/site-packages/maa"):
                continue
            if rel.as_posix().startswith("runtimes/win-x64/native/") or rel.as_posix().startswith("plugins/"):
                continue
            archive.extract(entry, target)
    for source, destination in [(repo / "agent", target / "agent"), (repo / "assets", target), (repo / "docs", target / "docs")]:
        shutil.copytree(source, destination, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in ("requirements.txt", "README.md", "CUSTOM_CHANGELOG_zh-CN.md"):
        shutil.copy2(repo / name, target / name)
    # User presets are deliberately preserved; debug remains opt-in.
    shutil.copytree(args.runtime / "config", target / "config", dirs_exist_ok=True)
    site = args.runtime / "python/Lib/site-packages"
    metadata = list(site.glob("maafw-*.dist-info"))
    if len(metadata) != 1 or metadata[0].name != "maafw-5.13.0.dist-info":
        raise RuntimeError("Expected exactly one installed maafw 5.13.0 distribution")
    for source in (site / "maa", metadata[0]):
        shutil.copytree(source, target / "python/Lib/site-packages" / source.name,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    native = target / "runtimes/win-x64/native"
    shutil.copytree(site / "maa/bin", native)
    if (native / "plugins").exists():
        shutil.copytree(native / "plugins", target / "plugins/win-x64")
    for name, updates in {
        "card_lists.json": {"debugDiagnosticsEnabled": False},
        "maa_option.json": {"save_draw": False, "save_on_error": False},
        "pip_config.json": {"last_version": "v1.5.0", "enable_pip_update": False},
    }.items():
        path = target / "config" / name
        data = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
        data.update(updates)
        dump(path, data)
    dump(target / "PORTABLE_BUILD.json", {
        "version": "1.5.0", "upstream": "SuperWaterGod/MaaGakumasu@v1.5.0",
        "framework": "5.13.0", "customUI": "preserved from v1.4.8 portable",
        "diagnosticsDefault": False, "presets": "current local presets preserved",
        "validation": "See merge report; full live cultivation not verified",
    })
    shutil.copy2(repo / "docs/zh_cn/v1.5.0合并说明.md", target / "自定义版更新说明.txt")
    print(f"STAGED={target}", flush=True)


def pack(target: Path):
    output = target.with_name(target.name + ".zip")
    excluded = {"debug", "logs", "backup", "backups", "temp", "dev", "rollback"}
    files = [p for p in target.rglob("*") if p.is_file()
             and p.relative_to(target).parts[0] not in excluded
             and "__pycache__" not in p.parts and p.suffix not in {".pyc", ".pyo"}]
    for path in files:
        name = path.relative_to(target).as_posix()
        if any(0xE000 <= ord(c) <= 0xF8FF or c == "\ufffd" for c in name):
            raise ValueError(f"Invalid filename: {name}")
    assert (target / "requirements.txt").read_text().splitlines()[0] == "maafw==5.13.0"
    native = target / "runtimes/win-x64/native"
    sdk = target / "python/Lib/site-packages/maa/bin"
    for source in sdk.rglob("*.dll"):
        assert source.read_bytes() == (native / source.relative_to(sdk)).read_bytes(), source
    with ZipFile(output, "x", ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(files):
            archive.write(path, path.relative_to(target).as_posix())
    with ZipFile(output) as archive:
        assert archive.testzip() is None
        assert len(archive.namelist()) == len(set(archive.namelist()))
        for path in files:
            name = path.relative_to(target).as_posix()
            assert hashlib.sha256(archive.read(name)).digest() == hashlib.sha256(path.read_bytes()).digest(), name
            if not name.isascii():
                assert archive.getinfo(name).flag_bits & 0x800
    print(json.dumps({"archive": str(output), "bytes": output.stat().st_size,
                      "files": len(files), "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}, ensure_ascii=False))


if __name__ == "__main__":
    main()

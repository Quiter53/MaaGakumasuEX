"""Publish a versioned draft only after asset size/hash verification."""
import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("tag")
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--notes", type=Path, help="UTF-8 release notes for this version")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    target = "Quiter53/MaaGakumasu-card-strategy-custom"
    result = subprocess.run(["git", "credential", "fill"], cwd=repo,
        input="protocol=https\nhost=github.com\n\n", capture_output=True, text=True, check=True,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"})
    credential = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    token = credential["password"]

    def api(path, data=None, method=None, content_type="application/json"):
        url = path if path.startswith("https://") else "https://api.github.com/repos/" + target + path
        if isinstance(data, dict):
            data = json.dumps(data, ensure_ascii=False).encode("utf-8")
        request = Request(url, data=data, method=method, headers={
            "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
            "User-Agent": "MaaGakumasu-custom-release", "Content-Type": content_type})
        with urlopen(request, timeout=600) as response:
            return json.load(response)

    sha = hashlib.sha256(args.artifact.read_bytes()).hexdigest()
    size = args.artifact.stat().st_size
    try:
        release = api("/releases/tags/" + args.tag)
    except HTTPError as error:
        if error.code != 404 or args.verify_only:
            raise
        release = None
    if not args.verify_only:
        assert not subprocess.check_output(["git", "status", "--porcelain"], cwd=repo), "Dirty working tree"
        commit = subprocess.check_output(["git", "rev-parse", args.tag + "^{commit}"], cwd=repo, text=True).strip()
        body = (
            "## 更新内容\n\n"
            "- 合并官方 v1.5.0，保留黑白名单界面、卡牌、饮料与 HIF 定制策略。\n"
            "- 两端框架统一为 MaaFramework 5.13.0，固定依赖并校验启动版本。\n"
            "- 正常选项阈值不变；连续失败至少 5 次且持续 6 秒，确认三条按钮布局与画面稳定后才启用保底。\n"
            "- 保底点击前重新识别，沿用原属性选择策略；原有预设保留，调试诊断默认关闭。\n\n"
            "## 验证\n\n37 项单元测试、三层资源加载及 Agent 握手通过。保底策略尚待实机验证，本版为预发布。\n\n"
            f"文件：`{args.artifact.name}`\n\n大小：{size / 1024**2:.2f} MiB\n\nSHA-256：`{sha}`\n")
        if args.notes:
            body = args.notes.read_text(encoding="utf-8-sig").strip() + (
                f"\n\n## 文件校验\n\n文件：`{args.artifact.name}`\n\n"
                f"大小：{size / 1024**2:.2f} MiB\n\nSHA-256：`{sha}`\n")
        if release is None:
            release = api("/releases", {"tag_name": args.tag, "target_commitish": commit,
                "name": args.tag, "body": body, "draft": True, "prerelease": True})
        existing = next((a for a in release["assets"] if a["name"] == args.artifact.name), None)
        if existing is None:
            print("Uploading " + args.artifact.name, flush=True)
            api(release["upload_url"].split("{", 1)[0] + "?name=" + quote(args.artifact.name),
                args.artifact.read_bytes(), content_type="application/zip")
    release = api("/releases/" + str(release["id"]))
    asset = next(a for a in release["assets"] if a["name"] == args.artifact.name)
    assert asset["size"] == size and asset["state"] == "uploaded"
    assert asset.get("digest") == "sha256:" + sha, "Remote digest mismatch or unavailable"
    if not args.verify_only and release["draft"]:
        release = api("/releases/" + str(release["id"]), {"draft": False}, method="PATCH")
    assert not release["draft"] and release["name"] == args.tag
    print(json.dumps({"release": release["html_url"], "download": asset["browser_download_url"],
                      "bytes": size, "sha256": sha, "verified": True}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

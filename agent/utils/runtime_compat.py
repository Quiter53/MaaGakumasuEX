"""Dependency and IPC argument checks that do not load native libraries."""

import re
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def framework_pin(requirements: Path) -> str | None:
    for line in requirements.read_text(encoding="utf-8-sig").splitlines():
        match = re.fullmatch(r"maafw\s*==\s*([0-9]+\.[0-9]+\.[0-9]+)\s*(?:#.*)?", line.strip())
        if match:
            return match.group(1)
    return None


def framework_dependency_ready(requirements: Path) -> bool:
    expected = framework_pin(requirements)
    if expected is None:
        return False
    try:
        return version("maafw") == expected
    except PackageNotFoundError:
        return False


def agent_identifier(arguments: list[str]) -> str:
    for argument in arguments:
        if argument.startswith("socket_id=") and argument.split("=", 1)[1]:
            return argument.split("=", 1)[1]
    for argument in arguments:
        if argument and "=" not in argument:
            return argument
    raise ValueError("缺少 Agent socket_id，无法启动连接")

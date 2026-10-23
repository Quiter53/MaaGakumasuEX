"""Normalize optional MaaFramework action parameters without coercing flags."""

import json


def action_params(value) -> dict:
    if isinstance(value, str):
        try:
            value = json.loads(value) if value.strip() else None
        except (ValueError, TypeError):
            return {}
    return value if isinstance(value, dict) else {}


def action_mode(value, key: str = "mode") -> str:
    """Read a string mode from ``{"mode": ...}``; legacy bare strings stay accepted.

    MaaFramework serializes ``custom_action_param`` as JSON, so a bare pipeline
    string ``"reset"`` reaches the agent as ``'"reset"'``.
    """
    params = action_params(value)
    if key in params:
        return str(params[key])
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (ValueError, TypeError):
            return value.strip()
        return decoded if isinstance(decoded, str) else ""
    return ""

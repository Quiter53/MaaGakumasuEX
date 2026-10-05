"""Normalize optional MaaFramework action parameters without coercing flags."""

import json


def action_params(value) -> dict:
    if isinstance(value, str):
        try:
            value = json.loads(value) if value.strip() else None
        except (ValueError, TypeError):
            return {}
    return value if isinstance(value, dict) else {}

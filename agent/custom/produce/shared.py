"""Cultivation shared; behavior moved from the original action module."""

import os
import json
import time
from typing import Optional

import numpy as np
from utils import logger
from maa.context import Context
from custom.card_lists import CardListStore
from custom.reward_counts import RewardActionBudget

CARD_LISTS_PATH = os.path.join("config", "card_lists.json")


_DIAGNOSTIC_STATE = {"mtime": None, "enabled": False}


_REWARD_BUDGETS = {action: RewardActionBudget() for action in ("remove", "refresh")}


def _reset_produce_card_run_state() -> None:
    """Reset consumable reward actions exactly once for a new Produce run."""
    _REWARD_BUDGETS.update({action: RewardActionBudget() for action in ("remove", "refresh")})


def _diagnostics_enabled() -> bool:
    """Read the user-facing diagnostics switch; missing/invalid means off."""
    try:
        mtime = os.path.getmtime(CARD_LISTS_PATH)
        if _DIAGNOSTIC_STATE["mtime"] != mtime:
            with open(CARD_LISTS_PATH, "r", encoding="utf-8") as stream:
                value = json.load(stream)
            _DIAGNOSTIC_STATE["mtime"] = mtime
            _DIAGNOSTIC_STATE["enabled"] = bool(value.get("debugDiagnosticsEnabled", False) if isinstance(value, dict) else False)
    except Exception:
        _DIAGNOSTIC_STATE["mtime"] = None
        _DIAGNOSTIC_STATE["enabled"] = False
    return bool(_DIAGNOSTIC_STATE["enabled"])


def _diagnostic_info(message: str) -> None:
    """Keep verbose recognition details out of the normal user log."""
    logger.debug(message)


_CARD_LIST_STORE = CardListStore()


def _load_card_lists() -> dict:
    return _CARD_LIST_STORE.load(CARD_LISTS_PATH)


def _save_card_diagnostic(image, scene: str, details: list) -> None:
    """Persist only uncertain live decisions as lossless raw arrays for review."""
    if not _diagnostics_enabled():
        return
    try:
        directory = os.path.join("debug", "card_strategy")
        os.makedirs(directory, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
        base = os.path.join(directory, f"{stamp}-{scene}")
        np.save(base + ".npy", image, allow_pickle=False)
        with open(base + ".json", "w", encoding="utf-8") as stream:
            json.dump(details, stream, ensure_ascii=False, indent=2)
        logger.warning(f"已保存低置信度卡牌现场: {base}.npy")
    except Exception as error:
        logger.warning(f"保存卡牌现场失败: {error}")


def _detect_disconnect_screen(context: Context, image) -> Optional[str]:
    """Detect the communication-error overlay inside a long custom action.

    Long-running custom actions must yield back to the pipeline when the game
    leaves their scene.  In particular, the blurred battle background behind
    the communication dialog still contains a readable health bar, so battle
    detection alone can otherwise wait forever.

    Do not test title/home here.  This helper runs in the hot path before every
    card decision and shop screenshot; two extra full-screen template matches
    made normal decisions take tens of seconds.  Once the error overlay is
    detected, the outer pipeline owns title/login/home recognition.
    """
    if image is not None and image.shape[1] > image.shape[0]:
        return None
    node = "ProduceDisconnectError"
    detail = context.run_recognition(node, image)
    if detail and detail.hit:
        return node
    return None

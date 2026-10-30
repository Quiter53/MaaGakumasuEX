"""Versioned card-list snapshots; a failed save must not disable live rules."""

import copy
import json
from pathlib import Path

from utils import logger

from .card_visual import normalize_card_name


class CardListStore:
    def __init__(self):
        self.path = None
        self.version = None
        self.failed_version = None
        self.value = None

    @staticmethod
    def normalize(value):
        if not isinstance(value, dict):
            raise ValueError("卡牌名单必须为对象")
        presets = value.get("presets", {})
        active = presets.get(value.get("activePreset")) if isinstance(presets, dict) else None
        if isinstance(active, dict):
            value = active
        result = {}
        for name in ("blacklist", "whitelist", "replacementList"):
            section = value.get(name, {})
            if not isinstance(section, dict) or not isinstance(section.get("cards", []), list):
                raise ValueError(f"{name} 格式错误")
            cards = section.get("cards", [])
            if any(not isinstance(card, str) for card in cards):
                raise ValueError(f"{name} 包含非文字卡名")
            result[name] = {
                "enabled": bool(section.get("enabled", False)),
                "cards": sorted({key for card in cards if (key := normalize_card_name(card))}),
            }
        return result

    def load(self, path):
        path = Path(path).resolve()
        if path != self.path:
            self.path, self.version, self.failed_version, self.value = path, None, None, None
        version = ("unreadable",)
        try:
            stat = path.stat()
            version = (stat.st_mtime_ns, stat.st_size)
            if version != self.version and (version, "invalid") != self.failed_version:
                with path.open("r", encoding="utf-8") as stream:
                    value = self.normalize(json.load(stream))
                self.value, self.version, self.failed_version = value, version, None
        except (OSError, ValueError) as error:
            failure = (version, "invalid" if isinstance(error, ValueError) else type(error).__name__)
            if self.failed_version != failure:
                if self.value is not None or not isinstance(error, FileNotFoundError):
                    policy = "保留上次有效名单" if self.value is not None else "尚无有效名单"
                    logger.warning(f"读取卡牌名单失败，{policy}: {error}")
                self.failed_version = failure
        return copy.deepcopy(self.value if self.value is not None else self.normalize({}))

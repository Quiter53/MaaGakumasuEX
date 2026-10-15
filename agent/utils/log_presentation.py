"""Console presentation only. Full original records remain in the debug log."""
import json
import re
from pathlib import Path

_cached = (None, False)


def detailed_logs_enabled():
    global _cached
    path = Path("config/card_lists.json")
    try:
        stat = path.stat()
        stamp = (str(path.resolve()), stat.st_mtime_ns, stat.st_size)
        if stamp != _cached[0]:
            doc = json.loads(path.read_text(encoding="utf8"))
            _cached = (stamp, isinstance(doc, dict) and doc.get("detailedLogsEnabled") is True)
        return _cached[1]
    except (OSError, ValueError, TypeError):
        _cached = (None, False)
        return False


def concise_message(message, level):
    if level in ("DEBUG", "TRACE"):
        return None
    # These are bookkeeping, repeated probes and framework internals.
    if any(word in message for word in ("AgentServer", "已加载修订", "pip", "奖励操作预算", "已保存低置信度卡牌现场", "保存卡牌现场失败")):
        return None
    message = re.sub(r"[,，；;]\s*(?:坐标|box|roi|score|margin|source|来源|置信度|候选|耗时|参数|原因)[：:=].*", "", message, flags=re.I)
    message = re.sub(r"\s*耗时[:：].*", "", message)
    if re.search(r"\b[A-Za-z_][\w.-]*\s*=|\b(?:Produce\w+|OCR|ONNX|Traceback|Exception|PID)\b|[A-Za-z]:[\\/]|\.py\b|\{['\"]", message):
        if level in ("ERROR", "CRITICAL"):
            return "操作未完成，请查看详细日志。"
        if level == "WARNING":
            return "当前画面暂未确认，正在重新检查。"
        return None
    return message.strip() or None

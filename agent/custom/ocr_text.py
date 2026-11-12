"""Post-process OCR strings; never infer identity from the highest score alone."""

import unicodedata
from difflib import SequenceMatcher
from typing import Mapping, NamedTuple, Sequence


def compact_ocr(value: str) -> str:
    """Normalize width and spacing, keeping punctuation for field parsers."""
    return "".join(
        char for char in unicodedata.normalize("NFKC", str(value or ""))
        if not char.isspace() and unicodedata.category(char) != "Cf"
        and not (0xFE00 <= ord(char) <= 0xFE0F or 0xE0100 <= ord(char) <= 0xE01EF)
    )


def normalize_label(value: str) -> str:
    """Remove label decoration; numerical fields must use their own parser."""
    return "".join(char for char in compact_ocr(value).casefold() if char.isalnum())


class OcrMatch(NamedTuple):
    value: str
    score: float
    runner_up: float


def match_ocr_label(
    value: str, candidates: Mapping[str, Sequence[str]], *,
    min_score: float = 0.8, min_margin: float = 0.1, short_exact: int = 3,
) -> OcrMatch | None:
    """Rank known semantic groups, rejecting weak or ambiguous observations.

    Multiple spellings of the same action belong to one group. A short label
    cannot lose a letter: there is too little evidence to distinguish it.
    """
    query = normalize_label(value)
    if not query:
        return None
    ranked = []
    for key, aliases in candidates.items():
        scores = []
        for alias in aliases:
            target = normalize_label(alias)
            if not target:
                continue
            if query == target:
                scores.append(1.0)
            elif min(len(query), len(target)) > short_exact:
                scores.append(SequenceMatcher(None, query, target, autojunk=False).ratio())
        ranked.append((max(scores, default=0.0), key))
    ranked.sort(reverse=True)
    if not ranked:
        return None
    best, key = ranked[0]
    second = ranked[1][0] if len(ranked) > 1 else 0.0
    if best < min_score or best - second < min_margin or best <= second:
        return None
    return OcrMatch(key, best, second)

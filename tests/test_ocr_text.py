"""Text-only OCR normalization and ambiguity contracts."""

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("ocr_text", Path(__file__).resolve().parents[1] / "agent/custom/ocr_text.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_width_spacing_and_numeric_punctuation():
    assert module.compact_ocr("♡ ３４ ／ ４０\u200b") == "♡34/40"
    assert module.compact_ocr("❤️ ３４／４０") == "❤34/40"
    assert module.normalize_label("【ＳＵＧＡＲ　Flavor】") == "sugarflavor"


def test_unique_fuzzy_label_and_same_group_aliases():
    hit = module.match_ocr_label("通信エラ", {"network": ["通信エラー", "通信エラー！"], "date": ["日付変更"]})
    assert hit.value == "network" and hit.score == 8 / 9


def test_highest_alone_is_not_evidence():
    assert module.match_ocr_label("まったく別の文章", {"network": ["通信エラー"]}) is None
    assert module.match_ocr_label("abcde", {"one": ["abcdf"], "two": ["abcdg"]}) is None
    assert module.match_ocr_label("同じ名前", {"one": ["同じ名前"], "two": ["同じ名前！"]}) is None


def test_short_names_and_empty_observations():
    assert module.match_ocr_label("現て", {"wanted": ["見て"]}) is None
    assert module.match_ocr_label("♡", {"wanted": ["♡"]}) is None
    assert module.match_ocr_label("", {"wanted": ["通信エラー"]}) is None
    assert module.match_ocr_label("文字", {}) is None
    assert module.match_ocr_label("【見て】", {"wanted": ["見て"]}).value == "wanted"

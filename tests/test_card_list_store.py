import json
from pathlib import Path
from unittest.mock import patch

from custom.card_lists import CardListStore


def write(path, cards):
    path.write_text(json.dumps({"blacklist": {"enabled": True, "cards": cards}}), encoding="utf-8")


def test_unchanged_file_read_once_and_snapshot_is_isolated(tmp_path):
    path = tmp_path / "lists.json"
    write(path, ["blocked"])
    store = CardListStore()
    real_open = Path.open
    with patch.object(Path, "open", autospec=True, side_effect=real_open) as opened:
        first = store.load(path)
        first["blacklist"]["cards"].clear()
        assert store.load(path)["blacklist"]["cards"] == ["blocked"]
        assert opened.call_count == 1


def test_incomplete_save_keeps_rules_and_next_save_updates(tmp_path):
    path = tmp_path / "lists.json"
    write(path, ["blocked"])
    store = CardListStore()
    good = store.load(path)
    for content in ('{', '[]', '{"blacklist": {"cards": "broken"}}'):
        path.write_text(content, encoding="utf-8")
        assert store.load(path) == good
        with patch.object(Path, "open", side_effect=AssertionError("unchanged invalid file reread")):
            assert store.load(path) == good
    path.unlink()
    assert store.load(path) == good
    write(path, ["new blocked"])
    assert store.load(path)["blacklist"]["cards"] != good["blacklist"]["cards"]


def test_missing_file_is_empty_but_different_path_does_not_inherit(tmp_path):
    path = tmp_path / "a.json"
    store = CardListStore()
    assert not store.load(path)["blacklist"]["enabled"]
    write(path, ["blocked"])
    assert store.load(path)["blacklist"]["enabled"]
    assert not store.load(tmp_path / "b.json")["blacklist"]["enabled"]


def test_temporary_read_error_is_retried_without_content_change(tmp_path):
    path = tmp_path / "lists.json"
    write(path, ["blocked"])
    store = CardListStore()
    with patch.object(Path, "open", side_effect=PermissionError("writer holds file")):
        assert not store.load(path)["blacklist"]["enabled"]
    assert store.load(path)["blacklist"]["cards"] == ["blocked"]

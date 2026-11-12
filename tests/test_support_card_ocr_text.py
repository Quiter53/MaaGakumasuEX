"""Support-card identities after text normalization and candidate ranking."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
from custom.action.SupportCards import SupportCardsAuto


def test_normalized_and_fuzzy_support_name_preserves_star():
    action = SupportCardsAuto()
    cards = {"SUGAR FLAVOR": "s_card-3-0029", "ENDLESS DANCE": "s_card-3-0030"}
    assert action.match_card("【ｓｕｇａｒ flavor】", 2, cards) == "3-29-2"
    assert action.match_card("SUGAR FLAV0R", 2, cards) == "3-29-2"


def test_ambiguous_support_name_does_not_export_arbitrary_id():
    cards = {"abcdf": "s_card-3-0029", "abcdg": "s_card-3-0030"}
    assert SupportCardsAuto().match_card("abcde", 1, cards) is None
    assert SupportCardsAuto().match_card("unrelated", 1, cards) is None
    assert SupportCardsAuto().match_card("", 1, cards) is None


def test_exact_identity_and_same_id_aliases():
    cards = {"原名": "s_card-3-0029", "SUGAR FLAVOR": "s_card-3-0029", "Sugar Flavor!": "s_card-3-0029"}
    assert SupportCardsAuto().match_card("原名", 4, cards) == "3-29-4"
    assert SupportCardsAuto().match_card("sugarflavor", 4, cards) == "3-29-4"

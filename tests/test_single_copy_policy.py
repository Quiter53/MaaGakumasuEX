"""On-demand ownership checks and the explicit all-blacklisted exception."""

import sys
from types import SimpleNamespace as NS
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
from custom import card_rules as rules
from custom.card_copies import needs_deck_check, with_owned_counts, choose_after_check, self_exchange_target


@pytest.fixture
def limited(monkeypatch):
    doc = rules.defaults()
    rule = dict(rules.active_preset(doc)["rules"][0], card="限一R", tier="SSR", singleCopy=True)
    rules.active_preset(doc)["rules"] = [rule]
    monkeypatch.setattr(rules, "_cultivation_store", NS(preset=lambda: rules.active_preset(doc)))
    return rule


def c(name, rank):
    return dict(name_key=name, rank=rank)


def test_check_only_the_card_about_to_be_taken(limited):
    assert needs_deck_check(c("限一R", 400))
    assert not needs_deck_check(c("別SSR", 400))
    limited["enabled"] = False
    assert not needs_deck_check(c("限一R", 400))


@pytest.mark.parametrize("owned", [0, 1, 2])
def test_owned_card_yields_to_nonblack_alternative(limited, owned):
    selected, other = c("限一R", 400), c("別SR", 300)
    result = choose_after_check(selected, [selected, other], {"限一R": owned}, set(), {"限一R"}, lambda x: x["rank"])
    assert result is (selected if owned == 0 else other)


def test_all_other_choices_blacklisted_allows_duplicate(limited):
    selected, blocked = c("限一R", 400), c("黒SSR", 500)
    assert choose_after_check(selected, [selected, blocked], {"限一R": 1}, {"黒SSR"}, set(), lambda x: x["rank"]) is selected


def test_unknown_count_is_not_zero(limited):
    selected = c("限一R", 400)
    assert choose_after_check(selected, [selected], None, set(), set(), lambda x: x["rank"]) is None
    assert choose_after_check(selected, [selected], {}, set(), set(), lambda x: x["rank"]) is None


@pytest.mark.parametrize('owned', [1, 2])
def test_exchange_keeps_owned_target_instead_of_taking_other_card(limited, owned):
    selected, other = c('限一R',400), c('別SR',300)
    assert choose_after_check(selected,[selected,other],{'限一R':owned},set(),set(),
                              lambda x:x['rank'],exchange=True) is selected


def test_exchange_unknown_ownership_does_not_authorize_self_swap(limited):
    selected=c('限一R',400)
    assert choose_after_check(selected,[selected],None,set(),set(),lambda x:x['rank'],exchange=True) is None


def test_count_annotations_do_not_mutate_recognition(limited):
    raw = c("限一R", 400)
    marked = with_owned_counts([raw], {"限一R": 2})
    assert marked[0]["owned_count"] == 2 and "owned_count" not in raw


def test_old_rule_files_default_to_off_and_invalid_types_rejected():
    doc = rules.defaults()
    assert rules.validate(doc)
    rule = rules.active_preset(doc)["rules"][0]
    rule["singleCopy"] = "true"
    with pytest.raises(ValueError, match="限一张"):
        rules.validate(doc)


from unittest.mock import Mock, patch

import numpy as np
from custom.owned_deck import RowCounts, Inspection, OwnedDeckReader
from custom.card_copies import RemovalCopies
from custom.produce.shop import ProduceShoppingAuto as Shop
from custom.produce.rewards import ProduceChooseRewardAuto as Reward
from custom.produce.rewards import ProduceHIFChangeSourceAuto as Source


def row(tag, names=None, y=400):
    return [
        dict(name_key=n, box=[48 + i * 160, y, 140, 140], pixels=np.full((24, 24, 3), tag + i * 0.04))
        for i, n in enumerate(names or [f"{tag}-{i}" for i in range(4)])
    ]


def test_overlapping_and_repeated_pages_never_inflate_count():
    a, b, c, d = row(0.1), row(0.3, ["限一R", "a", "b", "c"]), row(0.5), row(0.7, ["限一R", "d", "e", "f"])
    tracker = RowCounts(16, {"限一R", "absent"})
    tracker.add([a, b, c])
    assert tracker.result() == {"限一R": 1}
    tracker.add([b, c, d])
    tracker.add([b, c, d])
    assert tracker.result() == {"限一R": 2, "absent": 0}


def test_ambiguous_overlap_and_unknown_names_never_prove_absence():
    a = row(0.2)
    tracker = RowCounts(16, {"absent"})
    tracker.add([a, a, a])
    tracker.add([a, a, a])
    assert not tracker.valid and tracker.result() == {}
    tracker = RowCounts(4, {"absent"})
    tracker.add([row(0.5, [None, "a", "b", "c"])])
    assert tracker.result() == {}


def recognized(name="限一R", rarity="R", x=82, y=390):
    return dict(
        name=name,
        name_key=name,
        rarity=rarity,
        frame_rarity=rarity,
        name_confidence=1.0,
        rarity_confidence=1.0,
        identity_verified=True,
        priority=400 if name == "限一R" else 300,
        label="cards",
        detector_score=0.99,
        box=[x, y, 116, 116],
        extra_action="no",
    )


@pytest.mark.parametrize("count", [None, 0, 1])
def test_last_or_unconfirmed_copy_survives_all_lists(limited, count):
    target = dict(recognized(), owned_count=count)
    assert Shop._choose_removal_candidate([target], {"限一R"}, set(), {"限一R"}) is None


def test_surplus_beats_n_and_white_protection(limited):
    target = dict(recognized(), owned_count=2)
    starter = recognized("starter", "N")
    chosen = Shop._choose_removal_candidate([starter, target], set(), {"限一R"})
    assert chosen["name"] == "限一R" and chosen["delete_rank"] == 4
    assert Shop._choose_delete_candidate([starter, target], set(), {"限一R"})["name"] == "限一R"
    limited["singleCopy"] = False
    assert Shop._choose_removal_candidate([starter, target], set(), {"限一R"})["name"] == "starter"


def scene():
    image = np.zeros((1280, 720, 3), np.uint8)
    context = Mock()
    context.tasker.stopping = False
    context.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value = image
    return context, image


def lists(black=()):
    return dict(blacklist=dict(enabled=True, cards=list(black)), whitelist=dict(enabled=False, cards=[]))


def test_normal_acquisition_does_not_open_deck(limited):
    ctx, image = scene()
    card = recognized("other", "SR")
    with patch("custom.owned_deck.OwnedDeckReader.inspect") as inspect:
        assert Reward._limit_acquisition(ctx, card, [card], image, lists()) == (card, True)
    inspect.assert_not_called()
    ctx.tasker.controller.post_click.assert_not_called()


@pytest.mark.parametrize("owned,black,expect", [(0, (), "限一R"), (1, (), "other"), (1, ("other",), "限一R")])
def test_runtime_acquisition_uses_live_ownership(limited, owned, black, expect):
    ctx, image = scene()
    a, b = recognized(), recognized("other", "SR", x=228)
    with (
        patch("custom.owned_deck.OwnedDeckReader.inspect", return_value=Inspection({"限一R": owned}, True)) as inspect,
        patch("custom.owned_deck.drawer_present", return_value=False),
    ):
        chosen, restored = Reward._limit_acquisition(ctx, a, [a, b], image, lists(black))
    assert chosen["name"] == expect and restored
    assert inspect.call_args.args[0] == {"限一R"}


@pytest.mark.parametrize('owned', [0,1,2])
def test_runtime_exchange_marks_only_confirmed_owned_target(limited, owned):
    ctx,image=scene();a,b=recognized(),recognized('other','SR',x=228)
    with patch('custom.owned_deck.OwnedDeckReader.inspect',return_value=Inspection({'限一R':owned},True)), \
         patch('custom.owned_deck.drawer_present',return_value=False):
        chosen,restored=Reward._limit_acquisition(ctx,a,[a,b],image,lists(),exchange=True)
    assert restored and chosen['name_key']=='限一R'
    assert bool(chosen.get('self_exchange')) == (owned>0)
    assert 'self_exchange' not in a


def test_source_only_self_swap_can_consume_last_white_promoted_card(limited):
    a=recognized();starter=recognized('starter','N');action=Source()
    assert Shop._choose_removal_candidate([a],set(),{'限一R'}) is None
    action._self_exchange_key='限一R'
    chosen=action._choose_for_exchange([starter,a],set(),{'限一R'},set())
    assert chosen['name_key']=='限一R' and chosen['delete_rank']==5
    assert Shop._choose_delete_candidate([a],set(),set()) is None


def test_source_does_not_substitute_other_card_when_same_card_disabled_or_unknown(limited):
    action=Source();action._self_exchange_key='限一R'
    for target in [dict(recognized(),selectable=False),dict(recognized(),identity_verified=False,name_confidence=0.)]:
        assert action._choose_for_exchange([target,recognized('starter','N')],set(),set(),set()) is None


def test_self_exchange_scans_past_other_pages_and_does_not_require_duplicate_count(limited):
    action=Source();action._self_exchange_key='限一R';ctx,_=scene()
    first=np.zeros((1280,720,3),np.uint8);second=np.full_like(first,50)
    target=recognized(y=800);starter=recognized('starter','N',y=650)
    with patch.object(action,'_page',side_effect=[(first,[starter]),(second,[target])]), \
         patch.object(action,'_scroll') as scroll:
        chosen=action._scan_source(ctx,set(),{'限一R'},set(),strict=False)
    assert chosen['name_key']=='限一R' and chosen['delete_rank']==5
    scroll.assert_called_once_with(ctx,forward=True)


def test_self_exchange_requires_actual_destination_to_match_intent(limited):
    action=Source();action._self_exchange_key='限一R'
    image=np.zeros((1280,720,3),np.uint8)
    for observed,expected in [(None,False),({'name':'other'},False),({'name':'限一R'},True)]:
        with patch('custom.square_identity.match',return_value=observed):
            assert action._self_exchange_matches(image) is expected


def test_self_exchange_intent_round_trips_and_clears_without_user_config():
    ctx=Mock();node={}
    def update(overrides):
        node.update(overrides['ProduceHIFChangeSourceFlag']);return True
    ctx.override_pipeline.side_effect=update;ctx.get_node_data.side_effect=lambda _:node
    assert self_exchange_target(ctx) is None
    assert self_exchange_target(ctx,'限一R',write=True)
    assert self_exchange_target(ctx)=='限一R'
    assert self_exchange_target(ctx,None,write=True)
    assert self_exchange_target(ctx) is None


def test_source_clears_intent_only_after_confirmed_submission(limited):
    for committed in [True,False]:
        action=Source();ctx,image=scene()
        ctx.get_node_data.return_value={'attach':{'self_exchange_target':'限一R'}}
        with patch('custom.produce.shared._load_card_lists',return_value=lists()), \
             patch.object(action,'_rewind_source',return_value=(image,[])), \
             patch.object(action,'_scan_source',return_value=recognized()), \
             patch.object(action,'_submit',return_value=committed),patch('time.sleep'):
            assert action.run(ctx,NS()) is True
        assert ctx.override_pipeline.called is committed


def test_changed_destination_prevents_source_click(limited):
    action=Source();action._self_exchange_key='限一R';ctx,image=scene()
    with patch('custom.produce.shared._detect_disconnect_screen',return_value=False), \
         patch('custom.hif.is_hif_change_source',return_value=True), \
         patch('custom.square_identity.match',return_value={'name':'other'}):
        assert action._submit(ctx,recognized()) is False
    ctx.tasker.controller.post_click.assert_not_called()


def test_unrestored_source_never_clicks_reward(limited):
    ctx, image = scene()
    a = recognized()
    with (
        patch("custom.owned_deck.OwnedDeckReader.inspect", return_value=Inspection({"限一R": 0}, False)),
        patch("custom.produce.shared._detect_disconnect_screen", return_value=False),
        patch.object(Reward, "_reward_overlay", return_value=False),
    ):
        assert Reward()._select(ctx, a, "test", [a]) is False
    ctx.tasker.controller.post_click.assert_not_called()


@pytest.mark.parametrize("owned,expected", [(0, True), (1, False), (None, False)])
def test_shop_does_not_buy_owned_or_unknown_limited_card(limited, owned, expected):
    ctx, image = scene()
    a = recognized()
    with (
        patch("custom.owned_deck.OwnedDeckReader.inspect", return_value=Inspection({} if owned is None else {"限一R": owned}, True)),
        patch("custom.owned_deck.drawer_present", return_value=False),
    ):
        chosen, restored = Reward._limit_acquisition(ctx, a, [a], image, lists(), purchase=True)
    assert (chosen is a) == expected and restored


def test_open_failure_closes_drawer_without_readable_total():
    ctx, _ = scene()
    reader = OwnedDeckReader(ctx)
    with (
        patch.object(reader, "capture", side_effect=["source", "drawer", "source", "source"]),
        patch.object(reader, "stable", return_value=None),
        patch.object(reader, "click") as click,
        patch("custom.owned_deck.bag_box", return_value=[500, 1150, 40, 50]),
        patch("custom.owned_deck.drawer_present", side_effect=lambda c, i: i == "drawer"),
        patch("custom.owned_deck.close_box", return_value=[300, 1140, 100, 30]),
        patch("custom.owned_deck.time.sleep"),
    ):
        result = reader.inspect({"limited"}, lambda i: i == "source")
    assert result.counts is None and result.restored
    assert click.call_count == 2


def test_precise_removal_scanner_does_not_stop_at_n_before_later_duplicates(limited):
    ctx, image = scene()
    later = image.copy()
    later[400:800] = 60
    first = [recognized("starter", "N")]
    second = [recognized(x=82), recognized(x=228)]
    source = Source()
    with (
        patch.object(source, "_page", return_value=(later, second)),
        patch.object(source, "_scroll") as scroll,
        patch.object(source, "_same_page", return_value=False),
        patch("custom.deck_scan.deck_view_complete", return_value=False),
        patch.object(Shop, "_probe_removal_candidates", side_effect=lambda ctx, cards, *a, **kw: cards),
    ):
        chosen = source._scan_source(ctx, set(), {"限一R"}, set(), strict=True, initial_page=(image, first))
    assert chosen["name"] == "限一R" and chosen["owned_count"] == 2
    assert chosen["delete_rank"] == 4 and scroll.call_count == 1


def test_real_drawer_grid_recovers_model_miss_and_overlay_box():
    from PIL import Image
    from custom.owned_deck import page_rows

    image = np.asarray(Image.open(Path(__file__).parent / "fixtures/deck/owned-drawer-24.png").convert("RGB"))[:, :, ::-1].copy()
    raw = [
        [49, 407, 139, 148],
        [49, 711, 140, 153],
        [50, 863, 139, 144],
        [209, 406, 141, 149],
        [209, 559, 141, 150],
        [209, 712, 141, 154],
        [209, 867, 141, 141],
        [233, 1113, 251, 85],
        [370, 406, 141, 149],
        [370, 559, 140, 150],
        [370, 711, 141, 153],
        [370, 866, 141, 142],
        [530, 559, 142, 151],
        [531, 407, 141, 150],
        [531, 713, 141, 151],
        [531, 847, 142, 160],
    ]
    ctx = Mock()
    ctx.run_recognition.return_value = NS(hit=True, all_results=[NS(box=b) for b in raw])
    rows = page_rows(ctx, image)
    assert len(rows) == 4 and all(len(r) == 4 for r in rows)
    assert rows[1][0]["name_key"] == "夏夜に咲く思い出"
    assert rows[-1][-1]["name_key"] == "幸せのおまじない"
    tracker = RowCounts(24, {"not visible"})
    tracker.add(rows)
    assert tracker.result() == {}  # Sixteen visible tiles are not the whole deck.


def test_drawer_recovery_requires_title_and_close_not_only_button():
    from custom.reco.produce import ProduceCloseOwnedDeck

    ctx = Mock()
    argv = NS(image=np.zeros((1280, 720, 3), np.uint8))
    with patch("custom.owned_deck.drawer_present", return_value=False), patch("custom.owned_deck.close_box") as close:
        assert ProduceCloseOwnedDeck().analyze(ctx, argv) is None
        close.assert_not_called()
    with patch("custom.owned_deck.drawer_present", return_value=True), patch("custom.owned_deck.close_box", return_value=[300, 1140, 100, 30]):
        assert ProduceCloseOwnedDeck().analyze(ctx, argv) == [300, 1140, 100, 30]


def test_complete_scrolling_scan_closes_and_verifies_return():
    ctx, _ = scene()
    reader = OwnedDeckReader(ctx)
    a, b, c, d = row(0.1, y=405), row(0.3, y=558), row(0.5, y=711), row(0.7, y=864)
    first = ("grid", 16, [a, b, c])
    second = ("grid", 16, [b, c, d])
    with (
        patch.object(reader, "capture", side_effect=["source", "drawer", "source", "source"]),
        patch.object(reader, "stable", side_effect=[first, first, first, second]),
        patch.object(reader, "resolve_unknowns", side_effect=lambda image, rows, wanted=None: rows),
        patch.object(reader, "scroll") as scroll,
        patch.object(reader, "click") as click,
        patch("custom.owned_deck.bag_box", return_value=[500, 1150, 40, 50]),
        patch("custom.owned_deck.drawer_present", side_effect=lambda c, i: i == "drawer"),
        patch("custom.owned_deck.close_box", return_value=[300, 1140, 100, 30]),
        patch("custom.owned_deck.time.sleep"),
    ):
        result = reader.inspect({"absent"}, lambda i: i == "source")
    assert result.counts == {"absent": 0} and result.restored
    assert [call.args[0] for call in scroll.call_args_list] == [False, False, True]
    assert click.call_count == 2


def test_unknown_overlap_identity_is_reconciled_without_adding_tiles():
    a = row(0.2, [None, "a", "b", "c"])
    b = row(0.2, ["target", "a", "b", "c"])
    tracker = RowCounts(4, {"target", "absent"})
    tracker.add([a])
    tracker.add([b])
    assert tracker.result() == {"target": 1, "absent": 0}
    assert a[0]["name_key"] is None

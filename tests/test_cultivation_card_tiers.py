"""Tier promotions apply across cultivation without corrupting card identity."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent'))
from custom import card_rules as rules
from custom.action.produce import ProduceChooseRewardAuto as Reward
from custom.action.produce import ProduceHIFChangeAuto as Change
from custom.action.produce import ProduceShoppingAuto as Shop
from custom.card_visual import rarity_priority
from custom.memory_flow import normal_priority


def card(name='昇格R', rarity='R', extra='no'):
    return dict(name=name, name_key=rules.name_key(name), rarity=rarity,
                extra_action=extra, priority=rarity_priority(rarity, extra),
                name_confidence=1., rarity_confidence=1., identity_verified=True,
                label='cards', detector_score=.99, box=[80, 620, 120, 120])


@pytest.fixture
def policy(monkeypatch):
    document = rules.defaults()
    preset = rules.active_preset(document)
    promoted = dict(preset['rules'][0], card='昇格R', tier='SSR', enabled=True,
                    limited=True, min=1, max=2, points=[[30,0],[1,0]])
    preset['rules'] = [promoted]
    monkeypatch.setattr(rules, '_cultivation_store', NS(preset=lambda: rules.active_preset(document)))
    return document, promoted


def test_reward_and_change_use_promoted_tier_without_white_list(policy):
    promoted, ordinary = card(), card('普通SR', 'SR')
    lists = {key:dict(enabled=False,cards=[]) for key in ('blacklist','whitelist')}
    assert max([ordinary,promoted], key=Reward._selection_key) is promoted
    assert Change._destination([ordinary,promoted],lists) is promoted
    assert Reward._stops_refresh(promoted)
    assert promoted['rarity']=='R' and promoted['priority']==200


def test_extra_action_and_real_lr_still_outrank_promoted_card(policy):
    assert Reward._selection_key(card('再動R',extra='yes')) > Reward._selection_key(card())
    assert Reward._selection_key(card('実LR','LR')) > Reward._selection_key(card())


@pytest.mark.parametrize('tier', ['SR','SSR'])
def test_promoted_r_is_protected_in_exchange_and_deletion(policy,tier):
    policy[1]['tier']=tier
    promoted=card()
    assert Shop._choose_removal_candidate([promoted],set(),set()) is None
    assert Shop._choose_delete_candidate([promoted],set(),set()) is None
    ordinary=card('普通R')
    selected=Shop._choose_removal_candidate([promoted,ordinary],set(),set())
    assert selected['name']=='普通R'


def test_explicit_black_and_replacement_lists_keep_their_authority(policy):
    promoted=card()
    assert Shop._choose_removal_candidate([promoted],{'昇格R'},set())['delete_rank']==2
    assert Shop._choose_removal_candidate([promoted],set(),set(),{'昇格R'})['delete_rank']==0
    assert Shop._choose_removal_candidate([promoted],set(),{'昇格R'},{'昇格R'})['delete_rank']==0
    assert Shop._choose_delete_candidate([promoted],set(),set(),{'昇格R'}) is None


def test_replacement_tie_uses_effective_rarity_but_retains_real_rarity(policy):
    promoted, sr=card(), card('実SR','SR')
    selected=Shop._choose_removal_candidate([promoted,sr],set(),set(),{'昇格R','実SR'})
    assert selected['name']=='実SR'
    selected=Shop._choose_removal_candidate([promoted],set(),set(),{'昇格R'})
    assert selected['rarity']=='R'


def test_shop_ranking_uses_promotion_and_preserves_price_tiebreak(policy):
    promoted=dict(card(),price=100)
    sr=dict(card('実SR','SR'),price=50)
    cheaper_ssr=dict(card('実SSR','SSR'),price=80)
    assert Shop._card_purchase_key(promoted)>Shop._card_purchase_key(sr)
    assert Shop._card_purchase_key(cheaper_ssr)>Shop._card_purchase_key(promoted)


def test_disabled_rule_and_changed_active_preset_restore_original_behavior(policy):
    document,rule=policy
    rule['enabled']=False
    assert rules.cultivation_priority(card())==200
    assert not Reward._stops_refresh(card())
    assert Shop._choose_removal_candidate([card()],set(),set())['delete_rank']==1
    rule['enabled']=True
    document['active']='career-0'
    assert rules.cultivation_priority(card())==200


def test_unknown_identity_no_promotion_and_plus_name_uses_same_rule(policy):
    assert rules.cultivation_rarity('', 'R')=='R'
    assert rules.cultivation_rarity('昇格R', 'unknown')=='unknown'
    assert rules.cultivation_rarity('昇格R＋', 'R')=='SSR'
    assert rules.cultivation_rarity('昇格R', 'LR')=='LR'


def test_memory_tier_does_not_change_diamond_custom_or_custom_limit(policy):
    catalog={'昇格R':dict(rarity='R',extra=False),'実SR':dict(rarity='SR',extra=False)}
    with patch('custom.memory_modes.catalog',return_value=catalog):
        # Real R still supports two customizations after promotion to SSR.
        promoted=normal_priority([NS(name='昇格R',custom=2)],False)
        normal=normal_priority([NS(name='実SR',custom=2)],False)
        diamond=normal_priority([NS(name='実SR',custom=0)],True)
        assert promoted==(0,2,400) and promoted>normal
        assert diamond>promoted


def test_saved_rule_changes_reload_for_all_nonbattle_consumers(tmp_path,monkeypatch):
    document=rules.defaults()
    preset=rules.active_preset(document)
    preset['rules']=[dict(preset['rules'][0],card='昇格R',tier='SSR')]
    path=tmp_path/'rules.json'
    path.write_text(json.dumps(document),encoding='utf8')
    monkeypatch.setattr(rules,'_cultivation_store',rules.RuleStore(path))
    assert rules.cultivation_priority(card())==400
    preset['rules'][0]['enabled']=False
    path.write_text(json.dumps(document,indent=2),encoding='utf8')
    assert rules.cultivation_priority(card())==200

"""Vinegar complements hand contents; it never overrides card ranking."""
import copy
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import numpy as np
import pytest

from custom import card_rules as rules
from custom.battle_combo import NATIONAL, NationalCombo
from custom.battle_drinks import BattleDrinkSession, signature
from custom.battle_move import MovePicker, MoveDisconnected
from custom.drink_policy import CATALOG, VINEGAR_ID, defaults, due, normalize

TARGET = 'スポットライト'


@pytest.fixture
def preset():
    value = rules.active_preset(rules.defaults())
    rule = copy.deepcopy(value['rules'][0])
    rule.update(card=NATIONAL, comboTarget=TARGET, limited=False, tier='R')
    value['rules'] = [rule]
    return value


@pytest.mark.parametrize('selected,hand,expected', [
    (NATIONAL,[NATIONAL,'other'],TARGET),
    (TARGET,[TARGET,'other'],NATIONAL),
    (NATIONAL,[NATIONAL,TARGET],None),
    (TARGET,[NATIONAL,TARGET],None),
    (NATIONAL,[NATIONAL,None],None),
    ('other',[NATIONAL,'other'],None),
    (NATIONAL,[],None),
    (NATIONAL+'＋',[NATIONAL+'+',TARGET+'+'],None),
])
def test_fetch_uses_full_current_hand(preset,selected,hand,expected):
    assert NationalCombo().fetch_target(preset,selected,hand,4)==expected


def test_clear_or_disable_target_restores_normal_drink_behavior(preset):
    combo=NationalCombo()
    preset['rules'][0]['enabled']=False
    assert combo.fetch_target(preset,NATIONAL,[NATIONAL],1) is None
    preset['rules'][0].update(enabled=True,comboTarget='')
    assert combo.fetch_target(preset,NATIONAL,[NATIONAL],1) is None


def test_failed_pair_does_not_loop_through_more_bottles(preset):
    combo=NationalCombo();combo.fetch_attempted=True
    assert combo.fetch_target(preset,NATIONAL,[NATIONAL],1) is None
    combo.submitted()
    assert combo.fetch_target(preset,NATIONAL,[NATIONAL],1)==TARGET


def test_next_card_after_confirmed_national_skips_only_vinegar(preset):
    combo=NationalCombo();combo.submitted(NATIONAL+'+')
    assert combo.fetch_target(preset,TARGET,[TARGET],1) is None
    # Inspecting/clicking a card without successful play does not consume this guard.
    assert combo.fetch_target(preset,TARGET,[TARGET],1) is None
    combo.submitted('other')
    assert combo.fetch_target(preset,TARGET,[TARGET],1)==NATIONAL


def test_no_pin_or_hidden_order_is_created(preset):
    combo=NationalCombo()
    before=rules.sort_key(preset,NATIONAL,'SSR',True,3)
    combo.fetch_attempted=True;combo.submitted()
    assert rules.sort_key(preset,NATIONAL,'SSR',True,3)==before
    assert not hasattr(combo,'next_card')
    assert before < rules.sort_key(preset,'SR再動','SR',True,3)
    assert before > rules.sort_key(preset,TARGET,'SSR',False,3)


def test_actual_battle_choice_keeps_user_priority_after_fetch(preset):
    from custom.produce.battle import ProduceCardsAuto
    action=ProduceCardsAuto();action._rules_store=NS(preset=lambda:preset)
    cards=[dict(box=[60+i*200,880,180,240],label='cards',detector_score=.99) for i in range(3)]
    names=[TARGET,NATIONAL,'シュプレヒコール']
    action._identity_boxes=[c['box'] for c in cards]
    action._drinks=NS(turns=NS(read=lambda *_:3),art=NS(read=lambda _,b,*args:NS(name=names[(b[0]-60)//200],mode='full')),pending_card=None)
    rarity=NS(rarity='SSR',confidence=.99,best_distance=0,margin=.5,distances={},signature=[.1,.2,.3],channel_order='BGR')
    frame=np.zeros((1280,720,3),np.uint8)
    with patch('custom.produce.battle.classify_rarity',return_value=rarity), \
         patch.object(action,'_extra_action_status',side_effect=lambda ctx,img,b:('no' if b[0]==60 else 'yes',1,[])), \
         patch.object(action,'_apply_card_name_fallback'):
        action._combo.fetch_attempted=True
        assert action._choose_card(Mock(),frame,cards)['rule_name']=='シュプレヒコール'
        assert action._choose_card(Mock(),frame,cards[:2])['rule_name']==NATIONAL
        preset['rules'].append(dict(preset['rules'][0],card=TARGET,comboTarget='',tier='SSR',customExtraAction=True))
        # Even a conflicting target-above-National setting remains authoritative.
        assert action._choose_card(Mock(),frame,cards[:2])['rule_name']==TARGET


@pytest.mark.parametrize('tier',['R','SR'])
def test_ssr_demotion_is_battle_only(preset,tier):
    rule=preset['rules'][0];rule['tier']=tier
    assert rules.effective_tier('SSR',rule)==rules.TIERS.index(tier)
    with patch.object(rules,'cultivation_preset',return_value=preset):
        assert rules.cultivation_rarity(NATIONAL,'SSR')=='SSR'
    rule['enabled']=False
    assert rules.effective_tier('SSR',rule)==3


@pytest.mark.parametrize('old',['opening','before_card','last_turns','rule_combo'])
def test_old_vinegar_timings_migrate_without_final_turn_override(old):
    rule=normalize({'drinks':{VINEGAR_ID:dict(timing=old,target=False)}})[VINEGAR_ID]
    assert rule['timing']=='rule_combo' and not rule['target']
    assert not due(rule,opening=True,remaining=1,card_name=NATIONAL)


def test_rule_validation_rejects_self_pair_and_non_national_owner():
    doc=rules.defaults();rule=rules.active_preset(doc)['rules'][0]
    rule.update(card=NATIONAL,comboTarget=NATIONAL)
    with pytest.raises(ValueError):rules.validate(doc)
    rule.update(card=TARGET,comboTarget=NATIONAL)
    with pytest.raises(ValueError):rules.validate(doc)


@pytest.mark.parametrize('remaining',[1,4])
def test_vinegar_without_fetch_never_uses_even_at_final_turn(remaining):
    session=BattleDrinkSession();context=Mock();context.tasker.stopping=False
    frame=np.zeros((1280,720,3),np.uint8)
    session.inventory[0]=(signature(frame,session.SLOT_ROIS[0]),VINEGAR_ID)
    with patch.object(session,'occupied',return_value=[0]):
        assert not session.prepare(context,frame,Mock(),remaining=remaining,card_name=NATIONAL)
    context.tasker.controller.post_click.assert_not_called()


def test_vinegar_transfer_runs_before_waiting_for_battle_and_never_pins_card():
    session=BattleDrinkSession();context=Mock();context.tasker.stopping=False
    frame=np.zeros((1280,720,3),np.uint8);frame[1100:1165,400:580]=[20,140,255]
    session.inventory[0]=(signature(frame,session.SLOT_ROIS[0]),VINEGAR_ID)
    context.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value=frame
    context.run_recognition.return_value=NS(hit=True,best_result=NS(box=[400,1100,180,65]))
    events=[]
    def move(name):events.append(('move',name))
    def wait(*args):events.append(('wait',));return True
    with patch.object(session,'occupied',side_effect=[[0],[]]),patch('custom.battle_drinks.time.sleep'), \
         patch('custom.battle_drinks.owned_detail_title',return_value=CATALOG[VINEGAR_ID]['jp']):
        assert session.prepare(context,frame,wait,remaining=4,card_name=TARGET,combo_fetch=NATIONAL,move_picker=move)
    assert events==[('move',NATIONAL),('wait',)]
    assert session.pending_card is None


@pytest.mark.parametrize('target,expected',[(TARGET,TARGET),('missing',NATIONAL)])
def test_move_picker_exact_target_or_highest_rule_fallback(preset,target,expected):
    context=Mock();context.tasker.stopping=False
    picker=MovePicker(context,preset,2,target)
    frame=np.zeros((1280,720,3),np.uint8)
    boxes=[[80,400,118,118],[226,400,118,118]]
    with patch.object(picker,'capture',return_value=frame),patch.object(picker,'scroll'), \
         patch('custom.battle_move.is_move_page',return_value=True),patch('custom.battle_move.tiles',return_value=boxes), \
         patch('custom.battle_move.same_deck_view',return_value=True), \
         patch.object(picker,'observe',side_effect=lambda b:TARGET if b[0]==80 else NATIONAL), \
         patch.object(picker,'commit',side_effect=lambda n,b:n) as commit:
        assert picker.run()==expected
    assert commit.call_count==1


def test_no_recognized_move_candidate_stops_without_random_submission(preset):
    picker=MovePicker(Mock(),preset,1,TARGET)
    frame=np.zeros((1280,720,3),np.uint8)
    with patch.object(picker,'capture',return_value=frame),patch.object(picker,'scroll'), \
         patch('custom.battle_move.is_move_page',return_value=True),patch('custom.battle_move.tiles',return_value=[]), \
         patch('custom.battle_move.same_deck_view',return_value=True),patch.object(picker,'commit') as commit:
        with pytest.raises(RuntimeError,match='没有可确认'):picker.run()
    commit.assert_not_called()


def test_vanished_selection_does_not_submit(preset):
    picker=MovePicker(Mock(),preset,1,TARGET)
    frame=np.zeros((1280,720,3),np.uint8)
    with patch.object(picker,'capture',return_value=frame),patch.object(picker,'click') as click, \
         patch('custom.battle_move.move_button',return_value=[300,1140,90,40]), \
         patch('custom.battle_move.is_move_page',return_value=True), \
         patch('custom.battle_move.selected_title',return_value='other'):
        with pytest.raises(RuntimeError,match='未就绪'):picker.commit(TARGET,[80,400,118,118])
    click.assert_not_called()


@pytest.mark.parametrize('resume',[False,True])
@pytest.mark.parametrize('interrupted',[False,True])
def test_move_disconnect_and_stop_return_to_original_control_flow(preset,resume,interrupted):
    from custom.produce.battle import ProduceCardsAuto
    action=ProduceCardsAuto();action._rules_store=NS(preset=lambda:preset)
    action._drinks=Mock();action._drinks.turns.read.return_value=3
    action._drink_error=False
    context=Mock();context.tasker.stopping=False
    frame=np.zeros((1280,720,3),np.uint8)
    context.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value=frame
    def fail(*args,**kwargs):
        if interrupted:
            context.tasker.stopping=True
            raise InterruptedError('stopped')
        raise MoveDisconnected('disconnected')
    with patch('custom.battle_move.MovePicker.run',side_effect=fail), \
         patch('custom.battle_move.is_move_page',return_value=True), \
         patch('custom.produce.battle.shared._detect_disconnect_screen',return_value=None):
        if resume:
            assert not action._wait_until_playable(context)
        else:
            action._drinks.prepare.side_effect=fail
            assert action._drink_step(context,frame,NATIONAL)
    assert action._disconnect_detected is (not interrupted)
    assert not action._drink_error

"""Deck targets are unordered requirements on one saved memory, not UI slots."""
import itertools
import json
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import numpy as np
import pytest

from custom.produce.memory import modes as m
from custom.produce.memory.flow import MemoryFlow
from test_normal_memory_reroll import Settlement

ROOT = Path(__file__).resolve().parents[1]
TARGETS = [dict(name=name,slot=i+2,upgrade='upgraded',customMin=count)
           for i,(name,count) in enumerate([('シュプレヒコール',0),('願いの力',2),('脚光',1)])]
CARDS = [m.Card('role',True,0,'idol'),m.Card('support',True,0,'support'),
         m.Card('シュプレヒコール',True,0),m.Card('願いの力',True,2),m.Card('脚光',True,1),m.Card('other',False,0)]


@pytest.fixture
def session():
    catalog={m.normalize_card_name(c['name']):c for c in json.loads((ROOT/'assets/config/card_library.json').read_text('utf-8'))['cards']}
    value=m.Session()
    with patch.object(m,'catalog',return_value=catalog),patch.object(m,'SESSION',value):
        value.configure(dict(mode='deck',targets=TARGETS,reroll=True))
        yield value


@pytest.mark.parametrize('ordinary',list(itertools.permutations(CARDS[2:])))
def test_user_targets_match_every_ordinary_card_order(session,ordinary):
    assert m.satisfies(CARDS[:2]+list(ordinary),session.targets,'deck')


@pytest.mark.parametrize('index,replacement',[(2,m.Card('other',True,2)),(3,m.Card('願いの力',True,1)),
                                           (4,m.Card('脚光',False,1)),(4,m.Card('脚光',True,None))])
def test_missing_card_upgrade_or_beans_cannot_pass(session,index,replacement):
    cards=list(CARDS);cards[index]=replacement
    assert not m.satisfies(cards,session.targets,'deck')


def test_duplicates_need_distinct_physical_cards_and_roles_stay_distinct(session):
    target=dict(name='願いの力',upgrade='upgraded',customMin=2,slot=2)
    assert not m.satisfies(CARDS,[target,{**target,'slot':3}],'deck')
    assert m.satisfies(CARDS+[CARDS[3]],[target,{**target,'slot':3}],'deck')
    assert not m.satisfies(CARDS,[{**target,'slot':0}],'deck')


class DeckSettlement(Settlement):
    def __init__(self,decks,has_deck=True,**kwargs):
        super().__init__(decks,[False]*len(decks),**kwargs)
        self.has_deck=has_deck
    def scan(self,mode):
        assert mode=='deck'
        has_deck=self.has_deck[self.index] if isinstance(self.has_deck,list) else self.has_deck
        return self.cards[self.index],has_deck


def test_matched_deck_saves_then_stops_only_at_home(session):
    driver=DeckSettlement([CARDS,CARDS]);flow=driver.run()
    assert driver.stage=='home' and driver.spends==0
    assert session.matched and session.saved_result
    session.flow=flow
    m.go_home(driver.context)
    driver.context.override_pipeline.assert_called_once_with({'ProduceLoop':{'enabled':False}})


def test_decks_cannot_accumulate_matches_across_candidates(session):
    first=list(CARDS);first[4]=m.Card('other',False,0)
    second=list(CARDS);second[3]=m.Card('other',False,0)
    driver=DeckSettlement([first,second]);flow=driver.run()
    assert driver.stage=='home' and driver.spends==1 and not session.matched
    session.flow=flow;m.go_home(driver.context)
    driver.context.override_pipeline.assert_not_called()


def test_reroll_keeps_complete_target_deck(session):
    first=list(CARDS);first[3]=m.Card('願いの力',True,1)
    driver=DeckSettlement([first,CARDS]);driver.run()
    assert driver.stage=='home' and driver.chosen==1 and driver.spends==1 and session.matched


def test_unreadable_deck_keeps_current_and_does_not_spend(session):
    driver=DeckSettlement([CARDS,CARDS])
    driver.scan=Mock(side_effect=ValueError('one card is unknown'))
    driver.run()
    assert driver.stage=='home' and driver.spends==0 and not session.matched
    assert driver.scan.call_count==3


def test_disabled_stage_rerolls_when_target_is_unmet(session):
    driver=DeckSettlement([[],[]],has_deck=False);driver.run()
    assert driver.stage=='home' and driver.spends==1 and not session.matched


def test_disabled_stage_rerolls_and_saves_matching_deck(session):
    driver=DeckSettlement([[],CARDS],has_deck=[False,True])
    driver.run()
    assert driver.stage=='home' and driver.spends==1 and driver.chosen==1
    assert session.matched and session.saved_result


def test_matching_original_deck_beats_rerolled_candidate_without_stage(session):
    driver=DeckSettlement([CARDS,[]],has_deck=[True,False],stage='picker')
    driver.run()
    assert driver.stage=='home' and driver.spends==0 and driver.chosen==0
    assert session.matched and session.saved_result


def test_disabled_stage_respects_disabled_reroll(session):
    session.reroll=False
    driver=DeckSettlement([[],[]],has_deck=False);driver.run()
    assert driver.stage=='home' and driver.spends==0 and not session.matched


def test_confirmed_matching_copy_is_not_vetoed_by_uncertain_duplicate(session):
    cards=list(CARDS);cards[-1]=m.Card('願いの力',None,None)
    driver=DeckSettlement([cards,cards]);driver.run()
    assert driver.stage=='home' and driver.spends==0 and session.matched


@pytest.mark.parametrize('ticket_text',['チケットが不足しています','チケットを購入 ジュエル'])
def test_ticket_shortage_or_paid_prompt_keeps_current_deck(session,ticket_text):
    cards=list(CARDS);cards[4]=m.Card('脚光',False,0)
    driver=DeckSettlement([cards,CARDS],ticket_text=ticket_text);driver.run()
    assert driver.stage=='home' and driver.spends==0 and not session.matched


def test_restarting_in_confirmation_returns_to_compare_decks(session):
    first=list(CARDS);first[4]=m.Card('other',False,0)
    driver=DeckSettlement([first,CARDS],stage='confirm');driver.run()
    assert driver.clicks[0]==('confirm',600)
    assert driver.stage=='home' and driver.chosen==1 and session.matched


def test_committed_deck_state_survives_callback_boundary(session):
    first=list(CARDS);first[4]=m.Card('other',False,0)
    driver=DeckSettlement([first,CARDS],stage='picker')
    click=driver.click
    def pause(reader,box):
        click(reader,box)
        if box[0]==400:driver.clock+=40
    driver.click=pause
    flow=driver.run()
    assert flow.finalizing and not session.saved_result
    driver.click=click;driver.run(flow)
    assert driver.stage=='home' and driver.spends==0 and session.matched and session.saved_result


def rows(marker=None):
    result=[dict(text='メモリー変換 プロデュース',box=[40,420,250,30]),
            dict(text='ステージ',box=[455,610,80,25]),dict(text='次へ',box=[300,1140,100,35])]
    if marker is not None:
        result[0]['text']='獲得するメモリーを選択'
        result.append(dict(text=marker,box=[300,1000,120,30]))
    return result


def frame(selected=True,disabled=False):
    image=np.full((1280,720,3),245,np.uint8)
    if disabled:image[610:635,431:447]=180
    elif selected:image[610:635,455:535]=[0,100,255]
    return image


def reader():
    r=object.__new__(m.MemoryReader)
    r.context=Mock();r.context.tasker.stopping=False;r.controller=r.context.tasker.controller
    r.scroll=Mock();return r


def test_swallowed_stage_click_never_scrolls_produce_tab():
    r=reader();image=frame(False);text=rows();r.capture=Mock(return_value=(image,text))
    with patch.object(m,'click'),patch.object(m,'square_boxes',return_value=[]),patch.object(m.time,'sleep'):
        with pytest.raises(ValueError):r.deck(image,text,'summary')
    r.scroll.assert_not_called()


@pytest.mark.parametrize('changed', [[],rows('再生成1回目')])
def test_scrolling_stops_when_page_or_candidate_changes(changed):
    r=reader();image=frame();text=rows('再生成前')
    r.capture=Mock(return_value=(image,changed))
    with patch.object(m,'square_boxes',return_value=[]),patch.object(m.time,'sleep'):
        with pytest.raises(m.MemoryTransition):r.deck(image,text,'picker')
    assert r.scroll.call_count==1


def test_second_frame_must_still_be_same_candidate():
    r=reader();image=frame();text=rows('再生成前')
    r.capture=Mock(return_value=(image,rows('再生成1回目')))
    boxes=[[50+(i%4)*155,750+(i//4)*160,144,144] for i in range(6)]
    with patch.object(m,'square_boxes',return_value=boxes),patch.object(m,'read_card',return_value=CARDS[2]),patch.object(m.time,'sleep'):
        with pytest.raises(m.MemoryTransition):r.deck(image,text,'picker')


def test_disabled_stage_must_be_confirmed_on_fresh_frame():
    r=reader();image=frame(disabled=True);text=rows()
    r.capture=Mock(return_value=(frame(),text))
    with patch.object(m.time,'sleep'):
        with pytest.raises(ValueError):r.deck(image,text,'summary')


@pytest.mark.parametrize('missing_index',[0,1,5])
def test_unselected_card_identity_does_not_block_confirmed_targets(session,missing_index):
    r=reader();image=frame();text=rows();r.capture=Mock(return_value=(image,text))
    boxes=[[50+(i%4)*155,750+(i//4)*160,144,144] for i in range(6)]
    def read(context,frame,box):
        return None if boxes.index(box)==missing_index else CARDS[boxes.index(box)]
    with patch.object(m,'square_boxes',return_value=boxes),patch.object(m,'read_card',side_effect=read),patch.object(m.time,'sleep'):
        cards,has=r.deck(image,text,'summary')
    assert has and m.satisfies(cards,session.targets,'deck')


def test_unreadable_required_card_is_still_rejected(session):
    r=reader();image=frame();text=rows();r.capture=Mock(return_value=(image,text))
    boxes=[[50+(i%4)*155,750+(i//4)*160,144,144] for i in range(6)]
    def read(context,frame,box):
        return None if boxes.index(box)==3 else CARDS[boxes.index(box)]
    with patch.object(m,'square_boxes',return_value=boxes),patch.object(m,'read_card',side_effect=read),patch.object(m.time,'sleep'):
        with pytest.raises(ValueError):r.deck(image,text,'summary')

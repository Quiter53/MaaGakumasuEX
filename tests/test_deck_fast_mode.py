"""Fast exchange is independent of unrelated single-copy rules; deletion isn't."""
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import numpy as np
import pytest
from PIL import Image

from test_single_copy_policy import limited, row, recognized, scene
from test_shop_recovery_contracts import context
from custom.owned_deck import OwnedDeckReader, same_row, same_page, RowCounts, page_rows
from custom.produce.rewards import ProduceHIFChangeSourceAuto as Source
from custom.produce.shop import ProduceShoppingAuto as Shop

ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('name,rarity,black', [('starter','N',set()),('bad','SSR',{'bad'}),('spare','R',set())])
def test_fast_exchange_stops_on_eligible_visible_card_even_with_other_single_copy_rules(limited,name,rarity,black):
    ctx,image=scene(); action=Source()
    cards=[recognized(name,rarity)]
    action._removal_search_policy=(black,set(),set(),False)
    with patch.object(action,'_page',return_value=(image,cards)), patch.object(action,'_scroll') as scroll:
        initial=action._rewind_source(ctx)
        selected=action._scan_source(ctx,black,set(),set(),initial_page=initial,strict=False)
    assert selected['name_key']==name
    scroll.assert_not_called()


def test_fast_mode_preserves_white_last_limited_promoted_and_disabled_cards(limited):
    ctx,image=scene(); action=Source()
    cards=[recognized('white','N'),recognized(),dict(recognized('disabled','N'),selectable=False)]
    with patch.object(action,'_scroll') as scroll, patch('custom.deck_scan.deck_view_complete',return_value=True):
        assert action._scan_source(ctx,set(),{'white'},set(),initial_page=(image,cards),strict=False) is None
    scroll.assert_not_called()
    limited['singleCopy']=False
    assert Shop._choose_removal_candidate([recognized()],set(),set()) is None  # Promoted R is SSR.


@pytest.mark.parametrize('fast', [True,False])
def test_switch_reaches_exchange_scanner_and_rewind(fast):
    ctx=context(); action=Source(); image=ctx.tasker.controller.post_screencap().wait().get()
    ctx.override_pipeline({'ProduceHIFChangeSourceFlag':{'attach':{'fast_hit':fast}}})
    with patch('custom.produce.shared._load_card_lists',return_value={k:dict(enabled=False,cards=[]) for k in ('blacklist','whitelist','replacementList')}), \
         patch.object(action,'_rewind_source',return_value=(image,[])), \
         patch.object(action,'_scan_source',return_value=recognized('N','N')) as scan, \
         patch.object(action,'_submit',return_value=True):
        assert action.run(ctx,NS())
    assert scan.call_args.kwargs['strict'] is not fast
    assert action._removal_search_policy[-1] is not fast


def test_delete_always_requests_precise_comparison():
    ctx=context(); shop=Shop()
    with patch.object(Source,'_rewind_source',return_value=(None,[])), \
         patch.object(Source,'_scan_source',return_value=None) as scan, \
         patch.object(shop,'_cancel_delete',return_value='skipped'):
        assert shop._delete_from_deck(ctx,set(),set(),set())=='skipped'
    assert scan.call_args.kwargs['strict'] is True


def test_switch_is_shared_before_stamina_and_default_on():
    doc=json.loads((ROOT/'assets/tasks/produce.json').read_text('utf8'))
    cn=json.loads((ROOT/'assets/tasks/produce_cn.json').read_text('utf8'))
    for d in (doc,cn):
        options=d['task'][0]['option']
        assert options[options.index('使用体力药')-1]=='快速命中模式'
    switch=doc['option']['快速命中模式']
    assert switch['default_case']=='Yes'
    assert [c['pipeline_override']['ProduceHIFChangeSourceFlag']['attach']['fast_hit'] for c in switch['cases']]==[True,False]
    assert '快速命中模式' not in cn['option']  # Shared definition, no conflicting copy.
    for lang in ('zh-CN','zh-Hant','en-US','ja-JP'):
        labels=json.loads((ROOT/f'assets/lang/{lang}.json').read_text('utf8'))
        assert labels['快速命中模式'] and labels['快速命中模式说明']


def test_drawer_uses_content_drag_instead_of_incident_edge():
    record=json.loads((ROOT/'tests/fixtures/deck/owned-rewind-20261017.json').read_text('utf8'))
    assert len(record['gestures'])==16
    ctx=context();ctx.run_action_direct.return_value=NS(success=True);reader=OwnedDeckReader(ctx)
    with patch('time.sleep'):
        for forward in (False,True):
            reader.scroll(forward)
            _,gesture=ctx.run_action_direct.call_args.args
            assert gesture.begin[0]==600 and gesture.end[0][0]==600
            assert gesture.begin[0]!=record['gestures'][0]['begin'][0]
            assert 400<min(gesture.begin[1],gesture.end[0][1])<max(gesture.begin[1],gesture.end[0][1])<945


def test_fixed_inner_samples_tolerate_detector_jitter_without_merging_different_cards():
    image=np.asarray(Image.open(ROOT/'tests/fixtures/deck/owned-drawer-24.png').convert('RGB'))[:,:,::-1].copy()
    a=row(.1)
    for i,c in enumerate(a): c['pixels']=image[445:509,86+i*160:150+i*160].copy()
    b=deepcopy(a)
    for i,c in enumerate(b):
        c['pixels']=image[447:511,83+i*160:147+i*160].copy();c['box'][0]-=3;c['box'][1]+=2
    assert same_row(a,b) and same_page([a],[b])
    b[0]['pixels']=b[1]['pixels'].copy()
    assert not same_row(a,b)


def reader_with_pages(pages):
    reader=OwnedDeckReader(context())
    reader.scroll=Mock();reader.stable=Mock(side_effect=pages)
    reader.resolve_unknowns=Mock(side_effect=lambda image,rows,wanted=None:rows)
    return reader


def test_visible_owned_target_skips_rewind_and_forward_scan():
    current=(None,26,[row(.1,['target','b','c','d'])])
    reader=reader_with_pages([])
    assert reader.count_cards(current,{'target'})=={'target':1}
    reader.scroll.assert_not_called()


def test_missing_target_is_zero_only_after_complete_overlapping_deck_scan():
    a,b,c,d,e=[row(x) for x in (.1,.3,.5,.7,.9)]
    top=(None,20,[a,b,c]);bottom=(None,20,[c,d,e])
    reader=reader_with_pages([top,top,bottom])
    assert reader.count_cards(top,{'missing'})=={'missing':0}
    assert [call.args[0] for call in reader.scroll.call_args_list]==[False,False,True]


def test_oscillating_rewind_stops_without_inventing_absence():
    a=(None,26,[row(.1)]);b=(None,26,[row(.3,y=440)])
    reader=reader_with_pages([b,a,b,a,b,a])
    with pytest.raises(InterruptedError,match='反复'):
        reader.count_cards(a,{'missing'})
    assert reader.scroll.call_count<16


def test_presence_shortcut_still_closes_and_verifies_original_scene():
    ctx=context();reader=OwnedDeckReader(ctx)
    with patch.object(reader,'capture',side_effect=['source','drawer','source','source']), \
         patch.object(reader,'stable',return_value=(None,26,[row(.1,['target','b','c','d'])])), \
         patch.object(reader,'resolve_unknowns',side_effect=lambda image,rows,wanted=None:rows), \
         patch.object(reader,'scroll') as scroll,patch.object(reader,'click') as click, \
         patch('custom.owned_deck.bag_box',return_value=[500,1150,40,40]), \
         patch('custom.owned_deck.drawer_present',side_effect=lambda ctx,image:image=='drawer'), \
         patch('custom.owned_deck.close_box',return_value=[300,1140,100,30]),patch('time.sleep'):
        result=reader.inspect({'target'},lambda image:image=='source')
    assert result.restored and result.counts=={'target':1}
    assert click.call_count==2;scroll.assert_not_called()

"""Exercise the real per-card planner and interval-shop loop against game state.

Only visual reads and game inputs are simulated; budgets, quota decisions,
remaining options, refresh decisions and drink ordering run production code.
"""
from contextlib import ExitStack
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from test_customization import target, menu
from test_shop_recovery_contracts import context
from custom import customization as vision
from custom.produce.shop import (ProduceHIFIntervalShoppingAuto, GuidanceCustomizationState,
                                 ProduceGuideCustomizeAuto, ProduceShoppingAuto, ProduceHIFShoppingAuto)
from custom.produce.shop_customization import CustomizationSession


class ShopGame:
    def __init__(self, points, counts=(2, 0, 1), quota=2):
        self.points, self.quota = points, quota
        self.rules = [target('輝くキミへ', [1, 0]), target('仕切り直し', [1, 0]), target(counts=list(counts))]
        self.cards = {
            '輝くキミへ': menu('輝くキミへ', (1, 1), 1, (100, 40)),
            '仕切り直し': menu('仕切り直し', (1, 1), 1, (20, 70)),
            '幸せのおまじない': menu(),
        }
        self.used, self.events = set(), []
        self.drunk = False
        self.drink_count, self.drink_limit = 0, 1
        self.refresh_cost = 10
        self.refresh_restores_quota = True
        self.ctx = context(); self.shop = ProduceHIFIntervalShoppingAuto()
        self.image = self.ctx.tasker.controller.post_screencap().wait().get()

    def purchase(self, session, rule, before):
        page = self.cards[rule['key']]
        status, index = vision.next_purchase(rule, page, self.points)
        if status != 'buy': return status
        if rule['key'] not in self.used:
            assert self.quota > 0, 'cannot customize a new card without quota'
            self.quota -= 1
            self.used.add(rule['key'])
        self.points -= page['options'][index]['price']
        self.events.append(('custom', rule['key'], index + 1))
        page['total'] -= 1
        page['options'][index]['remaining'] -= 1
        if page['total'] == 0:
            for o in page['options']: o.update(remaining=0, capped=True)
        else:
            spec = rule['spec']['options'][index]
            done = len(spec['costs']) - page['options'][index]['remaining']
            if page['options'][index]['remaining']:
                page['options'][index]['price'] = spec['costs'][done]
        session.last_menu = deepcopy(page)
        return 'bought'

    def scan(self, session, wanted=None):
        jobs = [dict(target=t, menu=deepcopy(self.cards[t['key']])) for t in self.rules
                if t['key'] in self.cards and vision.next_purchase(t, self.cards[t['key']], 10**9)[0] == 'buy']
        if wanted is None: return jobs
        found = next((job for job in jobs if job['target']['key'] == wanted), None)
        return (found['target'], found['menu']) if found else None

    def refresh(self, *args):
        self.events.append(('refresh', self.refresh_cost))
        self.points -= self.refresh_cost
        if self.refresh_restores_quota: self.quota = 2
        self.used.clear()
        self.drunk = False
        return self.image, self.points

    def drink(self, *args, **kwargs):
        assert self.points >= 5
        self.points -= 5; self.drunk = True
        self.drink_count += 1
        self.events.append(('drink',))
        return 'bought'

    def run(self):
        with ExitStack() as stack:
            def p(*args, **kwargs): return stack.enter_context(patch(*args, **kwargs))
            def obj(*args, **kwargs): return stack.enter_context(patch.object(*args, **kwargs))
            p('time.sleep')
            p('custom.produce.shared._detect_disconnect_screen', return_value=False)
            obj(vision, 'targets', return_value=self.rules)
            obj(vision, 'entry', side_effect=lambda *a: dict(box=[1, 1, 10, 10], quota=self.quota))
            obj(CustomizationSession, 'scan', autospec=True, side_effect=self.scan)
            obj(CustomizationSession, 'purchase', autospec=True, side_effect=self.purchase)
            obj(CustomizationSession, 'leave', return_value=self.image)
            obj(CustomizationSession, 'click'); obj(CustomizationSession, 'stable')
            obj(self.shop, '_wait_shop_ready', return_value=self.image)
            obj(self.shop, '_is_shop_scene', return_value=True)
            obj(self.shop, '_heal_before_drinks', return_value=self.image)
            obj(self.shop, '_recognize_points', side_effect=lambda *a: self.points)
            obj(self.shop, '_drink_inventory_full', side_effect=lambda *a: self.drink_count >= self.drink_limit)
            obj(self.shop, '_full_inventory_has_non_target', return_value=False)
            obj(self.shop, '_recognize_drinks', side_effect=lambda *a: [] if self.drunk else [dict(id=1, name='drink', price=5, box=[1, 1, 10, 10])])
            obj(self.shop, '_purchase', side_effect=self.drink)
            obj(self.shop, '_finish_optional_shop', side_effect=lambda *a: self.events.append(('exit',)) or True)
            p('custom.shop.read_refresh', side_effect=lambda *a, **kw: dict(cost=self.refresh_cost, point=[1, 1]))
            obj(self.shop, '_refresh_stock', side_effect=self.refresh)
            assert self.shop.run(self.ctx, SimpleNamespace())
        return self.events


def test_three_cards_with_abundant_points_buy_current_drinks_before_refresh():
    game = ShopGame(10000)
    assert game.run() == [('custom', '輝くキミへ', 1), ('custom', '仕切り直し', 1),
                          ('drink',), ('refresh', 10), ('custom', '幸せのおまじない', 1),
                          ('custom', '幸せのおまじない', 1), ('custom', '幸せのおまじない', 3), ('exit',)]
    assert game.quota == 1  # Three operations on the R card took just one slot.


@pytest.mark.parametrize('points,refreshes,steps', [(159, 0, 2), (169, 0, 2), (170, 0, 2), (175, 1, 3), (215, 1, 4), (235, 1, 5)])
def test_refresh_budget_includes_next_step_and_partial_card_budget_stops_normally(points, refreshes, steps):
    game = ShopGame(points)
    events = game.run()
    assert sum(e[0] == 'refresh' for e in events) == refreshes
    assert sum(e[0] == 'custom' for e in events) == steps
    assert events[-1] == ('exit',)
    assert game.points >= 0


def test_second_card_unaffordable_goes_to_drinks_without_refresh():
    game = ShopGame(115)
    assert game.run() == [('custom', '輝くキミへ', 1), ('drink',), ('exit',)]
    assert game.cards['仕切り直し']['total'] == 1


def test_r_card_first_finishes_three_operations_using_one_slot():
    game = ShopGame(10000)
    game.rules = [game.rules[2], game.rules[0], game.rules[1]]
    events = game.run()
    assert events[:6] == [('custom', '幸せのおまじない', 1), ('custom', '幸せのおまじない', 1),
                         ('custom', '幸せのおまじない', 3), ('custom', '輝くキミへ', 1), ('drink',), ('refresh', 10)]


def test_partial_card_runs_out_of_points_before_leaving_menu():
    game = ShopGame(65)
    game.rules = [game.rules[2], game.rules[0]]
    assert game.run() == [('custom', '幸せのおまじない', 1), ('drink',), ('exit',)]
    assert game.cards['幸せのおまじない']['options'][0]['remaining'] == 2


def test_new_visit_reads_partial_progress_and_only_buys_missing_options():
    game = ShopGame(10000)
    game.rules = [game.rules[2]]
    game.cards['幸せのおまじない'] = menu(remaining=(2, 1, 1), total=2)
    assert game.run() == [('custom', '幸せのおまじない', 1), ('custom', '幸せのおまじない', 3), ('drink',), ('exit',)]


def test_missing_and_capped_targets_do_not_trigger_customization_refresh():
    game = ShopGame(10000)
    del game.cards['輝くキミへ']; del game.cards['幸せのおまじない']
    game.cards['仕切り直し'] = menu('仕切り直し', (0, 0), 0, (None, None))
    assert game.run() == [('drink',), ('exit',)]


def test_refresh_with_unrestored_quota_is_not_repeated():
    game = ShopGame(10000); game.refresh_restores_quota = False
    events = game.run()
    assert sum(e[0] == 'refresh' for e in events) == 1
    assert sum(e[0] == 'custom' for e in events) == 2
    assert events[-1] == ('exit',)


def test_one_remaining_slot_can_finish_all_steps_of_same_card():
    game = ShopGame(10000, quota=1); game.rules = [game.rules[2]]
    assert game.run() == [('custom', '幸せのおまじない', 1), ('custom', '幸せのおまじない', 1),
                          ('custom', '幸せのおまじない', 3), ('drink',), ('exit',)]
    assert game.quota == 0


def guidance(game):
    """Use the production event planner with the same distinct-card economy."""
    shop = GuidanceCustomizationState()
    with patch.object(vision, 'targets', return_value=game.rules):
        session = CustomizationSession(game.ctx, shop)
    def stable(stage=None, name=None):
        return game.image, deepcopy(game.cards[name]) if name else dict(stage='list', quota=game.quota)
    with patch('time.sleep'), patch('custom.produce.shared._detect_disconnect_screen', return_value=False), \
         patch.object(session, 'stable', side_effect=stable), patch.object(session, 'click'), \
         patch.object(session, 'read_page', return_value=dict(stage='list')), \
         patch.object(session, 'scan', side_effect=lambda wanted=None: game.scan(session, wanted)), \
         patch.object(session, 'purchase', side_effect=lambda t,m: game.purchase(session,t,m)):
        session.run_guidance()
    return session


def test_guidance_two_cards_only_even_with_unlimited_budget():
    game = ShopGame(10000); game.rules = [game.rules[2], game.rules[0], game.rules[1]]
    guidance(game)
    assert game.events == [('custom', '幸せのおまじない', 1), ('custom', '幸せのおまじない', 1),
                           ('custom', '幸せのおまじない', 3), ('custom', '輝くキミへ', 1)]
    assert game.quota == 0
    assert not GuidanceCustomizationState._visit_state(game.ctx).get('custom_blocked')


@pytest.mark.parametrize('points,steps', [(0,0),(65,1),(85,2),(115,3)])
def test_guidance_partial_card_budget_preserves_progress_without_drinks_or_refresh(points, steps):
    game = ShopGame(points); game.rules = [game.rules[2], game.rules[0]]
    guidance(game)
    assert len(game.events) == steps
    assert all(e[0] == 'custom' for e in game.events)
    assert game.points >= 0


def test_guidance_skips_capped_and_missing_and_resumes_existing_counts():
    game = ShopGame(10000)
    game.cards['輝くキミへ'] = menu('輝くキミへ', (0,0), 0, (None,None))
    del game.cards['仕切り直し']
    game.cards['幸せのおまじない'] = menu(remaining=(2,1,1), total=2)
    guidance(game)
    assert game.events == [('custom', '幸せのおまじない', 1), ('custom', '幸せのおまじない', 3)]
    assert game.quota == 1


def test_guidance_zero_quota_and_disabled_policy_spend_nothing():
    for disabled in (False, True):
        game = ShopGame(10000, quota=0 if not disabled else 2)
        if disabled: game.rules = []
        guidance(game)
        assert game.events == []


def test_guidance_state_is_separate_and_new_event_resets_blocked_receipt():
    game = ShopGame(10000)
    game.shop._visit_state(game.ctx, custom_blocked=True, custom_pending=True)
    guidance(game)
    assert len(game.events) == 2
    GuidanceCustomizationState._visit_state(game.ctx, custom_blocked=True, custom_pending=True)
    game.events.clear(); guidance(game); assert game.events == []
    ProduceGuideCustomizeAuto().run(game.ctx, SimpleNamespace(custom_action_param='reset'))
    state = GuidanceCustomizationState._visit_state(game.ctx)
    assert state == dict(custom_blocked=False, custom_pending=False, active=True)
    assert game.shop._visit_state(game.ctx)['custom_pending']
    ProduceGuideCustomizeAuto().run(game.ctx, SimpleNamespace(custom_action_param='done'))
    assert not GuidanceCustomizationState._visit_state(game.ctx)['active']


@pytest.mark.parametrize('enabled', [False, True])
def test_hif_normal_shop_delete_switch_controls_actual_entry_without_changing_base_shop(enabled):
    ctx = context(); shop = ProduceHIFShoppingAuto()
    ctx.override_pipeline({'ProduceHIFStrategy': {'attach': {'shop_delete': enabled}}})
    image = ctx.tasker.controller.post_screencap().wait().get()
    with ExitStack() as stack:
        p=lambda *a,**kw: stack.enter_context(patch(*a,**kw))
        obj=lambda *a,**kw: stack.enter_context(patch.object(*a,**kw))
        p('time.sleep'); p('custom.produce.shared._detect_disconnect_screen', return_value=False)
        p('custom.produce.shared._load_card_lists', return_value={k:dict(enabled=False,cards=[]) for k in ('blacklist','whitelist','replacementList')})
        p('custom.shop.is_interval_shop', return_value=False)
        p('custom.shop.read_number', return_value=50)
        for name,result in [('_wait_shop_ready',image),('_dismiss_comment_bubble',True),('_recognize_points',100),
                            ('_recognize_shop_cards',[]),('_recognize_drinks',[]),('_drink_inventory_full',False),
                            ('_reenter_shop',True),('_finish_optional_shop',True)]:
            obj(shop,name,return_value=result)
        entry=obj(shop,'_find_shop_delete_entry',return_value=[10,10,10,10])
        remove=obj(shop,'_try_delete_owned_card',return_value='skipped')
        assert shop.run(ctx,SimpleNamespace())
        assert bool(entry.call_count) == enabled
        assert bool(remove.call_count) == enabled
    assert ProduceShoppingAuto._allow_shop_delete(ctx) is True


def test_hif_shop_delete_defaults_off_in_ui_and_runtime():
    import json
    from test_customization import ROOT
    task=json.loads((ROOT/'assets/tasks/produce.json').read_text('utf8'))
    assert task['option']['普通商店删牌']['default_case']=='No'
    assert not ProduceHIFShoppingAuto._allow_shop_delete(context())


def test_five_targets_use_three_batches_buying_each_stock_before_refresh():
    game = ShopGame(100000); game.drink_limit = 3
    for name,spec in vision.catalog().items():
        if name in game.cards: continue
        game.rules.append(target(name, [1]+[0]*(len(spec['options'])-1)))
        game.cards[name] = menu(name, tuple(len(o['costs']) for o in spec['options']), spec['max'],
                               tuple(o['costs'][0] for o in spec['options']))
        if len(game.rules)==5: break
    events = game.run()
    assert [e[1] for e in events if e[0]=='custom'] == [r['key'] for r in game.rules for _ in range(sum(r['counts']))]
    assert [e[0] for e in events] == ['custom','custom','drink','refresh','custom','custom','custom','custom','drink','refresh','custom','drink','exit']


def test_disabled_shop_delete_residual_list_only_cancels():
    from custom.produce.shop import ProduceHIFDeleteAuto
    ctx=context(); shop=ProduceHIFDeleteAuto()
    with patch('custom.produce.shared._detect_disconnect_screen',return_value=False), \
         patch('custom.produce.shared._load_card_lists',return_value={}), patch('custom.shop.ocr',return_value=[]), \
         patch.object(shop,'_delete_from_deck') as delete, patch.object(shop,'_cancel_delete',return_value='cancel_pending') as cancel:
        assert shop.run(ctx,SimpleNamespace())
    delete.assert_not_called(); cancel.assert_called_once_with(ctx)

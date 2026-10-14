"""Real editor contract, persistent bridge and battle selection regressions."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

AGENT = Path(os.environ.get('RULES_TEST_AGENT', Path(__file__).resolve().parents[1] / 'agent'))
sys.path.insert(0, str(AGENT))
from custom import card_rules as rules

tempfile.tempdir = str(Path(__file__).resolve().parents[1] / 'debug/card-rules-tests')
Path(tempfile.tempdir).mkdir(parents=True, exist_ok=True)


class CardRuleTests(unittest.TestCase):
    def setUp(self):
        self.doc = rules.defaults()
        self.preset = rules.active_preset(self.doc)
        self.rule = self.preset['rules'][0]

    def test_default_timing_and_upgrade_names(self):
        for name, limit in [('届いて！', 2), ('せのびの魔法', 7)]:
            for turn in range(1, 31):
                r = rules.rule_for(self.preset, name + '＋')
                self.assertEqual(rules.allowed(r, turn), turn <= limit)
            for turn in (None, True, 0, -1):
                self.assertFalse(rules.allowed(r, turn))

    def test_removed_disabled_and_minimum_window(self):
        self.rule['min'] = 2
        self.assertFalse(rules.allowed(self.rule, 1))
        self.rule['enabled'] = False
        self.assertTrue(rules.allowed(self.rule, 20))
        self.assertIsNone(rules.rule_for(self.preset, '届いて!'))
        self.preset['rules'] = []
        self.assertTrue(rules.allowed(rules.rule_for(self.preset, '届いて!'), 20))

    def test_order_extra_tier_curve_stable(self):
        self.rule.update(limited=False, tier='SSR', points=[[12,100],[1,100]])
        key = lambda n, rare, extra=False, slot=0: rules.sort_key(self.preset,n,rare,extra,5,slot)
        self.assertGreater(key('unknown','N',True), key('届いて！','R'))
        self.assertGreater(key('届いて！','R'), key('other','SSR'))
        self.assertGreater(key('other','LR'),key('届いて！','R'))
        self.assertGreater(key('other','SSR',slot=0),key('other','SSR',slot=1))
        self.rule['enabled']=False
        self.assertLess(key('届いて！','R'), key('other','SR'))

    def test_no_implicit_scoring_or_opener_bias(self):
        self.preset['rules']=[]
        keys=[rules.sort_key(self.preset,n,'SSR',False,2) for n in ('届いて！','せのびの魔法','メリクリで～す♪','イメトレ')]
        self.assertTrue(all(k==keys[0] for k in keys))

    def test_curves_and_endpoints(self):
        self.rule['points']=[[10,0],[2,100]]
        self.assertEqual(rules.priority(self.rule,6),50)
        self.assertEqual(rules.priority(self.rule,30),0)
        self.assertEqual(rules.priority(self.rule,1),100)
        self.rule['mode']='step'
        self.assertEqual(rules.priority(self.rule,3),0)
        self.assertEqual(rules.priority(self.rule,2),100)
        self.assertEqual(rules.priority(self.rule,None),50)

    def test_invalid_documents(self):
        changes=[lambda d:d.update(version=99),lambda d:d.update(active='missing'),
                 lambda d:d['presets'].pop(),lambda d:d['presets'][2]['rules'][0].update(min=8,max=2),
                 lambda d:d['presets'][2]['rules'][0].update(points=[[2,1],[2,4]]),
                 lambda d:d['presets'][2]['rules'][0].update(enabled='false'),
                 lambda d:d['presets'][2]['rules'][0].update(points=[[1,float('nan')],[2,50]]),
                 lambda d:d['presets'][2]['hand'].clear(),
                 lambda d:d['presets'][2]['rules'].append(copy.deepcopy(d['presets'][2]['rules'][0]))]
        for change in changes:
            with self.subTest(change=change):
                doc=copy.deepcopy(self.doc);change(doc)
                with self.assertRaises(ValueError):rules.validate(doc)

    def test_store_reload_retains_last_good(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'rules.json';store=rules.RuleStore(path)
            self.assertEqual(store.preset()['name'],'干劲')
            self.doc['active']='career-0';path.write_text(json.dumps(self.doc),encoding='utf8')
            self.assertEqual(store.preset()['name'],'好调')
            path.write_text('invalid',encoding='utf8')
            self.assertEqual(store.preset()['name'],'好调')
            self.assertIsNotNone(store.error)

    def test_native_python_math_equivalence(self):
        path = os.environ.get('CARD_RULE_VECTORS')
        if not path:
            self.skipTest('Run CardRulesUi first and set CARD_RULE_VECTORS to its native-policy-vectors.json')
        vectors=json.loads(Path(path).read_text(encoding='utf8'))
        self.assertEqual(len(vectors),3600)
        for v in vectors:
            r,t=v['rule'],v['turn']
            self.assertEqual(v['result'],[rules.allowed(r,t),rules.effective_tier(v['rarity'],r),rules.priority(r,t)])
        print(f'Native/Python equivalence: {len(vectors)} cases')


class BattleRuleIntegrationTests(unittest.TestCase):
    def setUp(self):
        import numpy as np
        from custom.action.produce import ProduceCardsAuto
        self.action=ProduceCardsAuto()
        self.doc=rules.defaults()
        self.action._rules_store=NS(preset=lambda:rules.active_preset(self.doc))
        self.image=np.zeros((1280,720,3),dtype=np.uint8)
        self.cards=[dict(box=[60+i*130,880,120,240],label='cards',detector_score=.9) for i in range(3)]
        self.names=['届いて！','せのびの魔法','イメトレ']
        self.action._drinks=NS(turns=NS(read=lambda *_:5),art=NS(read=lambda _,b,*args:NS(name=self.names[(b[0]-60)//130],mode='full')),pending_card=None)
        self.rarity=NS(rarity='SR',confidence=.9,best_distance=.1,margin=.3,distances={},signature=[.1,.2,.3],channel_order='BGR')

    def choose(self):
        with patch('custom.action.produce.classify_rarity',return_value=self.rarity),patch.object(self.action,'_extra_action_status',return_value=('no',0,[])),patch.object(self.action,'_apply_card_name_fallback'):
            return self.action._choose_card(Mock(),self.image,self.cards)

    def test_real_candidate_timing_and_priority(self):
        self.assertEqual(self.choose()['box'],self.cards[1]['box'])
        r=self.doc['presets'][2]['rules'][1];r.update(points=[[12,0],[1,0]])
        self.assertEqual(self.choose()['box'],self.cards[2]['box'])
        r.update(tier='SSR')
        self.assertEqual(self.choose()['box'],self.cards[1]['box'])

    def test_switch_and_disable_remove_hardcoded_guards(self):
        self.doc['active']='career-0'
        self.assertEqual(self.choose()['box'],self.cards[0]['box'])
        self.assertFalse(self.action._held_until_late('届いて!',12))
        self.doc['active']='career-2';self.doc['presets'][2]['rules'][0]['enabled']=False
        self.assertEqual(self.choose()['box'],self.cards[0]['box'])

    def test_guard_applies_arbitrary_card_and_unknown_remaining(self):
        r=self.doc['presets'][2]['rules'][0];r.update(card='イメトレ',min=3,max=4)
        self.assertTrue(self.action._held_until_late('イメトレ＋',5))
        self.assertFalse(self.action._held_until_late('イメトレ',3))
        self.assertTrue(self.action._held_until_late('イメトレ',None))
        self.assertTrue(self.action._needs_rule_identity(1))

    def test_all_blocked_returns_none_without_playing(self):
        self.action._drinks.turns.read=lambda *_:10
        self.cards=self.cards[:2]
        self.assertIsNone(self.choose())


if __name__=='__main__':unittest.main(verbosity=2)

"""Normal cultivation spends once, compares memories and retains its round limit."""
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'agent'))
from custom import memory_modes as m
from custom.memory_actions import ProduceMemoryRecognition
from custom.memory_flow import MemoryFlow, normal_priority


CATALOG = {
    'R再動': dict(rarity='R', extra=True),
    'SR再動': dict(rarity='SR', extra=True),
    'SSR通常': dict(rarity='SSR', extra=False),
}


class Settlement:
    """In-memory screen driver; never binds a game or real controller."""
    def __init__(self, cards, diamonds, stage='summary', ticket_text='チケットを1枚使用'):
        self.cards, self.diamonds = cards, diamonds
        self.stage, self.index = stage, 0
        self.ticket_text = ticket_text
        self.spends = 0
        self.reroll_requests = 0
        self.chosen = None
        self.clicks = []
        self.context = Mock()
        self.context.tasker.stopping = False
        self.context.run_recognition.return_value = None
        self.frame = np.zeros((1280, 720, 3), np.uint8)
        self.clock = 0

    def now(self):
        self.clock += .1
        return self.clock

    @staticmethod
    def row(text, code=1):
        return dict(text=text, box=[code, 1000, 10, 10])

    def capture(self):
        if self.context.tasker.stopping:
            raise InterruptedError
        if self.stage == 'summary':
            texts = [self.row('メモリー変換 プロデュース'), self.row('再生成', 100), self.row('次へ', 500)]
        elif self.stage == 'reroll_confirm':
            texts = [self.row('メモリー再生成'), self.row(self.ticket_text),
                     self.row('キャンセル', 600), self.row('再生成する', 200)]
        elif self.stage == 'picker':
            texts = [self.row('獲得するメモリーを選択'),
                     self.row('再生成前' if self.index == 0 else '再生成1回目'), self.row('決定', 300)]
        elif self.stage == 'confirm':
            texts = [self.row('メモリー確定確認'), self.row('生成する', 400), self.row('キャンセル', 600)]
        elif self.stage == 'reveal':
            texts = [self.row('MEMORY'), self.row('TAP')]
        else:
            texts = []
        return self.frame, texts

    def scan(self, mode):
        assert mode == 'card', 'Normal settlement must inspect the obtainable card, not the stage deck'
        return [self.cards[self.index]], True

    def arrow(self, image, direction):
        if self.stage == 'picker' and ((direction == 'left' and self.index > 0) or (direction == 'right' and self.index == 0)):
            return [10 if direction == 'left' else 20, 990, 10, 10]
        return None

    def click(self, reader, box):
        code = box[0]
        self.clicks.append((self.stage, code))
        if self.stage == 'reveal':
            self.stage = 'summary'
        elif code == 100:
            self.reroll_requests += 1
            self.stage = 'reroll_confirm'
        elif code == 200:
            self.spends += 1
            self.stage, self.index = 'picker', 0
        elif code in (10, 20):
            self.index += -1 if code == 10 else 1
        elif code == 300:
            self.chosen = self.index
            self.stage = 'confirm'
        elif code == 400:
            self.stage = 'reveal'
        elif code == 500:
            self.stage = 'home'
        elif code == 600:
            self.stage = 'summary'
        else:
            raise AssertionError((self.stage, box))

    def run(self, flow=None):
        flow = flow or MemoryFlow()
        with patch.object(m, 'MemoryReader', return_value=self), \
             patch.object(m, 'candidate_arrow', side_effect=self.arrow), \
             patch.object(m, 'click', side_effect=self.click), \
             patch('custom.memory_farming.read_diamond', side_effect=lambda *_: self.diamonds[self.index]), \
             patch('custom.memory_flow.time.sleep'), \
             patch('custom.memory_flow.time.monotonic', side_effect=self.now):
            assert flow.run(self.context)
        return flow


class NormalMemoryRerollTests(unittest.TestCase):
    def setUp(self):
        self.session = m.Session()
        self.session.configure(dict(mode='normal', reroll=True))
        self.session_patch = patch.object(m, 'SESSION', self.session)
        self.session_patch.start()
        self.addCleanup(self.session_patch.stop)
        self.catalog_patch = patch.object(m, 'catalog', return_value=CATALOG)
        self.catalog_patch.start()
        self.addCleanup(self.catalog_patch.stop)

    def test_ranking_is_diamond_then_beans_then_extra_action_then_rarity(self):
        cases = [
            (m.Card('SSR通常', False, 1), False, m.Card('R再動', False, 0), True),
            (m.Card('SSR通常', False, 1), True, m.Card('R再動', False, 2), True),
            (m.Card('SSR通常', False, 1), False, m.Card('R再動', False, 2), False),
            (m.Card('SSR通常', False, 1), True, m.Card('R再動', False, 1), True),
            (m.Card('R再動', False, 1), True, m.Card('SR再動', False, 1), True),
        ]
        for a, da, b, db in cases:
            with self.subTest(a=a, b=b, diamond=(da, db)):
                self.assertGreater(normal_priority([b], db), normal_priority([a], da))

    def test_unknown_values_are_not_ranked_as_zero(self):
        for card, diamond in [(m.Card('R再動', False, None), True),
                              (m.Card('R再動', False, 1), None),
                              (m.Card('unknown', False, 0), False),
                              (m.Card('SSR通常', False, 2), True)]:
            with self.subTest(card=card, diamond=diamond), self.assertRaises(ValueError):
                normal_priority([card], diamond)

    def test_complete_flow_spends_once_and_saves_best_candidate_in_both_directions(self):
        for diamonds, expected in [([False, True], 1), ([True, False], 0), ([True, True], 0), ([False, False], 0)]:
            with self.subTest(diamonds=diamonds):
                game = Settlement([m.Card('R再動', False, 1)] * 2, diamonds)
                game.run()
                self.assertEqual((1, 1, expected, 'home'), (game.reroll_requests, game.spends, game.chosen, game.stage))
                self.assertTrue(self.session.saved_result)

    def test_full_flow_green_count_and_extra_rarity_tiebreak(self):
        pairs = [(m.Card('SR再動', False, 1), m.Card('R再動', False, 2)),
                 (m.Card('SSR通常', False, 1), m.Card('R再動', False, 1)),
                 (m.Card('R再動', False, 1), m.Card('SR再動', False, 1))]
        for cards in pairs:
            with self.subTest(cards=cards):
                game = Settlement(cards, [True, True])
                game.run()
                self.assertEqual((1, 1, 'home'), (game.spends, game.chosen, game.stage))

    def test_resuming_picker_does_not_spend_again(self):
        game = Settlement([m.Card('R再動', False, 1)] * 2, [False, True], stage='picker')
        game.index = 1
        game.run()
        self.assertEqual((0, 0, 1, 'home'), (game.reroll_requests, game.spends, game.chosen, game.stage))

    def test_resuming_ticket_confirmation_spends_only_once(self):
        game = Settlement([m.Card('R再動', False, 1)] * 2, [False, True], stage='reroll_confirm')
        game.run()
        self.assertEqual((0, 1, 1, 'home'), (game.reroll_requests, game.spends, game.chosen, game.stage))

    def test_stopping_before_observation_sends_no_click(self):
        game = Settlement([m.Card('R再動', False, 1)] * 2, [False, True])
        game.context.tasker.stopping = True
        with self.assertRaises(InterruptedError):
            game.run()
        self.assertFalse(game.clicks)

    def test_card_farming_still_keeps_an_initial_target_without_spending(self):
        self.session.configure(dict(mode='card', targets=[dict(name='R再動', slot=0, upgrade='any', customMin=0)]))
        game = Settlement([m.Card('R再動', False, 1)] * 2, [False, True])
        game.run()
        self.assertEqual((0, 'home', {0}), (game.spends, game.stage, self.session.pending_targets))
        m.go_home(game.context)
        self.assertTrue(self.session.matched)
        game.context.override_pipeline.assert_called_once_with({'ProduceLoop': {'enabled': False}})

    def test_card_farming_still_rerolls_a_miss_and_collects_a_target(self):
        self.session.configure(dict(mode='card', targets=[dict(name='SR再動', slot=0, upgrade='any', customMin=0)]))
        game = Settlement([m.Card('R再動', False, 1), m.Card('SR再動', False, 1)], [False, False])
        game.run()
        self.assertEqual((1, 1, 'home', {0}), (game.spends, game.chosen, game.stage, self.session.pending_targets))

    def test_unknown_primary_keeps_memory_without_spending(self):
        game = Settlement([m.Card('R再動', False, None)] * 2, [True, True])
        game.run()
        self.assertEqual((0, 0, 'home'), (game.spends, game.reroll_requests, game.stage))

    def test_unreadable_picker_keeps_current_candidate_without_another_ticket(self):
        game = Settlement([m.Card('R再動', False, 1), m.Card('R再動', False, None)], [True, True])
        game.run()
        self.assertEqual((1, 1, 'home'), (game.spends, game.chosen, game.stage))

    def test_ticket_shortage_or_paid_dialog_cancels_and_finishes(self):
        for text in ('チケットが不足しています', 'チケットをジュエルで購入', '再生成票不足', '再生成票 钻石购买'):
            with self.subTest(text=text):
                game = Settlement([m.Card('R再動', False, 1)] * 2, [False, True], ticket_text=text)
                game.run()
                self.assertEqual((0, 'home'), (game.spends, game.stage))

    def test_lagging_ticket_confirmation_is_not_submitted_twice_across_callbacks(self):
        game = Settlement([m.Card('R再動', False, 1)] * 2, [False, True], stage='reroll_confirm')
        real_click = game.click
        def lag(reader, box):
            real_click(reader, box)
            game.stage = 'reroll_confirm'
        game.click = lag
        flow = game.run()
        game.run(flow)
        self.assertEqual(1, game.spends)

    def test_candidate_transition_does_not_cache_old_identity(self):
        game = Settlement([m.Card('R再動', False, 1)] * 2, [False, True], stage='picker')
        flow = MemoryFlow()
        def change(*_):
            game.index = 1
            return True
        with patch('custom.memory_farming.read_diamond', side_effect=change):
            with self.assertRaises(m.MemoryTransition):
                flow.inspect(game, 'picker', '再生成前')
        self.assertFalse(flow.evaluated)

    def test_normal_default_off_and_recognition_does_no_work(self):
        self.session.configure({'mode': 'normal'})
        self.assertFalse(self.session.enabled)
        with patch.object(m, 'ocr') as ocr:
            self.assertIsNone(ProduceMemoryRecognition().analyze(Mock(), NS(image=None)))
        ocr.assert_not_called()

    def test_normal_on_initialization_preserves_cultivation_count(self):
        ctx = Mock()
        ctx.get_node_data.return_value = {'custom_action_param': {'mode': 'normal', 'reroll': True}}
        m.initialize(ctx)
        ctx.override_pipeline.assert_called_once_with({'ProduceLoop': {'enabled': True}, 'ProduceMemoryFlag': {'enabled': True}})
        self.assertTrue(self.session.enabled)

    def test_home_resets_one_round_state_without_changing_limit_or_switch(self):
        self.session.flow = MemoryFlow()
        self.session.flow.rerolls = 1
        self.session.saved_result = self.session.result_seen = True
        ctx = Mock()
        self.assertTrue(m.go_home(ctx))
        self.assertIsNone(self.session.flow)
        self.assertFalse(self.session.result_seen or self.session.saved_result)
        self.assertTrue(self.session.reroll)
        ctx.override_pipeline.assert_not_called()
        game = Settlement([m.Card('R再動', False, 1)] * 2, [False, True])
        game.run()
        self.assertEqual(1, game.spends)

    def test_task_options_and_translations_cover_both_resources(self):
        for filename in ('produce.json', 'produce_cn.json'):
            options = json.loads((ROOT/'assets/tasks'/filename).read_text('utf8'))['option']
            normal, card, deck = options['培育模式']['cases']
            self.assertEqual(['培育次数', '再生成回忆'], normal['option'])
            self.assertNotIn('再生成回忆', card['option'] + deck['option'])
            switch = options['再生成回忆']
            self.assertEqual('switch', switch['type'])
            self.assertEqual('No', switch['default_case'])
            self.assertEqual([True, False], [c['pipeline_override']['ProduceMemoryPolicy']['custom_action_param']['reroll'] for c in switch['cases']])
            for mode in ('回忆卡选择', '卡组选择'):
                self.assertTrue(options[mode]['pipeline_override']['ProduceMemoryPolicy']['custom_action_param']['reroll'])
        base = json.loads((ROOT/'assets/resource/base/pipeline/Produce.json').read_text('utf8'))
        self.assertFalse(base['ProduceMemoryPolicy']['action']['param']['custom_action_param']['reroll'])
        for lang in ('zh-CN', 'zh-Hant', 'en-US', 'ja-JP'):
            labels = json.loads((ROOT/'assets/lang'/f'{lang}.json').read_text('utf8'))
            self.assertTrue(labels['再生成回忆'] and labels['再生成回忆说明'])


if __name__ == '__main__':
    unittest.main()

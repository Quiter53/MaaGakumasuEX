"""Recorded story OCR and scoped follow decisions, without any game input."""
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'agent'))
from custom.reco import settlement_dialogs as m


def row(text, box, score=.99):
    return NS(text=text, box=box, score=score)


# 2026-10-15 03:21:34.503, maafw.bak.2026.10.15-05.46.42.850.log.
RECORDED = [row('早送り確認', [54, 917, 166, 38], .999896),
            row('未読のコミュです', [57, 1004, 195, 27], .999394),
            row('早送りしますか?', [58, 1040, 189, 27], .989951)]
YES = [420, 1134, 222, 55]
CANCEL = [105, 1134, 222, 55]
FOLLOW = [420, 1134, 214, 55]


def context():
    ctx = Mock()
    ctx.tasker.stopping = False
    ctx.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value = np.zeros((1280, 720, 3), np.uint8)
    ctx.run_recognition.return_value = None
    return ctx


def args(kind='story', follow=True):
    return NS(custom_action_param=json.dumps(dict(kind=kind, follow=follow)))


class SettlementDialogTests(unittest.TestCase):
    def test_recorded_heading_and_question_recover_template_miss(self):
        with patch.object(m, 'ocr', return_value=RECORDED):
            self.assertEqual({'box': RECORDED[0].box, 'ready': True}, m.story_dialog(context(), None))

    def test_exact_heading_holds_partial_modal_without_confirming(self):
        with patch.object(m, 'ocr', return_value=RECORDED[:1]):
            found = m.story_dialog(context(), None)
            self.assertFalse(found['ready'])
            self.assertIsNone(m.story_yes(context(), None, found))

    def test_body_alone_and_other_dialogs_are_not_story(self):
        for texts in (RECORDED[1:], [row(t, [54, 917, 200, 38]) for t in
                                    ('廃棄確認', '削除確認', 'メモリー再生成', '通信エラー', '日付変更')], []):
            with self.subTest(texts=texts), patch.object(m, 'ocr', return_value=texts):
                self.assertIsNone(m.story_dialog(context(), None))

    def test_question_must_be_below_same_heading(self):
        for box in ([58, 500, 200, 30], [400, 1040, 200, 30]):
            with self.subTest(box=box), patch.object(m, 'ocr', return_value=[RECORDED[0], row('早送りしますか?', box)]):
                self.assertFalse(m.story_dialog(context(), None)['ready'])

    def test_low_score_heading_cannot_confirm(self):
        with patch.object(m, 'ocr', return_value=[row('早送り確認', RECORDED[0].box, .79), *RECORDED[1:]]):
            self.assertIsNone(m.story_dialog(context(), None))

    def test_original_story_template_still_works_without_ocr(self):
        ctx = context(); ctx.run_recognition.return_value = NS(hit=True, best_result=NS(box=[54, 917, 267, 176]))
        with patch.object(m, 'ocr') as read:
            self.assertTrue(m.story_dialog(ctx, None)['ready'])
            read.assert_not_called()

    def test_affirmative_is_scoped_to_right_of_current_modal(self):
        ctx = context()
        with patch.object(m, 'ocr', return_value=[row('はい', YES)]):
            self.assertEqual(YES, m.story_yes(ctx, None, {'box': RECORDED[0].box, 'ready': True}))
        self.assertEqual([360, 1080, 340, 180], ctx.run_recognition.call_args.kwargs['pipeline_override']['ProduceStoryYesTemplate']['roi'])

    def test_ambiguous_or_negative_buttons_do_not_confirm(self):
        for labels in ([row('キャンセル', CANCEL)], [row('はい', YES), row('はい', [500, 1200, 80, 30])], []):
            with self.subTest(labels=labels), patch.object(m, 'ocr', return_value=labels):
                self.assertIsNone(m.story_yes(context(), None, {'box': RECORDED[0].box, 'ready': True}))

    def test_cancel_alone_never_selects_follow_even_when_disabled(self):
        def templates(ctx, image, name, roi=None):
            return CANCEL if name == 'ProduceFollowCancelTemplate' else None
        with patch.object(m, '_template', side_effect=templates), patch.object(m, 'ocr') as read:
            self.assertIsNone(m.follow_dialog(context(), None))
            read.assert_not_called()

    def test_true_follow_recognized_for_both_preferences(self):
        def templates(ctx, image, name, roi=None):
            return {'ProduceFollowButtonTemplate': FOLLOW, 'ProduceFollowCancelTemplate': CANCEL}.get(name)
        with patch.object(m, '_template', side_effect=templates), patch.object(m, 'ocr', return_value=[row('フォローする', FOLLOW)]):
            self.assertEqual({'box': FOLLOW, 'cancel': CANCEL}, m.follow_dialog(context(), None))

    def test_false_template_match_requires_follow_label(self):
        with patch.object(m, '_template', return_value=FOLLOW), patch.object(m, 'ocr', return_value=[row('はい', YES)]):
            self.assertIsNone(m.follow_dialog(context(), None))

    def test_visible_story_vetoes_follow_candidate_in_background(self):
        with patch.object(m, '_template', return_value=FOLLOW), patch.object(m, 'ocr', return_value=[row('フォローする', FOLLOW)]):
            self.assertIsNone(m.follow_dialog(context(), None))

    def test_cancel_must_share_follow_row(self):
        def templates(ctx, image, name, roi=None):
            return {'ProduceFollowButtonTemplate': FOLLOW, 'ProduceFollowCancelTemplate': [100, 800, 220, 55]}.get(name)
        with patch.object(m, '_template', side_effect=templates), patch.object(m, 'ocr', return_value=[row('フォローする', FOLLOW)]):
            self.assertIsNone(m.follow_dialog(context(), None)['cancel'])

    def test_follow_setting_changes_only_action_not_identity(self):
        options = json.loads((ROOT / 'assets/tasks/produce.json').read_text('utf8'))['option']['启用关注租借']
        self.assertEqual('Yes', options['default_case'])
        for case in options['cases']:
            with self.subTest(case=case['name']):
                override = case['pipeline_override']['ProduceFollow']
                self.assertEqual({'custom_action_param'}, set(override))
                self.assertEqual({'kind': 'follow', 'follow': case['name'] == 'Yes'}, override['custom_action_param'])

    def test_both_follow_actions_click_current_scoped_button(self):
        for enabled, expected in ((True, (527, 1161)), (False, (216, 1161))):
            ctx = context()
            with self.subTest(enabled=enabled), patch.object(m, 'follow_dialog', return_value={'box': FOLLOW, 'cancel': CANCEL}):
                self.assertTrue(m.ProduceSettlementDialogAction().run(ctx, args('follow', enabled)))
            ctx.tasker.controller.post_click.assert_called_once_with(*expected)

    def test_story_ignores_follow_setting_and_clicks_only_yes(self):
        for enabled in (True, False):
            ctx = context()
            with self.subTest(enabled=enabled), patch.object(m, 'story_dialog', return_value={'box': RECORDED[0].box, 'ready': True}), \
                 patch.object(m, 'story_yes', return_value=YES):
                self.assertTrue(m.ProduceSettlementDialogAction().run(ctx, args('story', enabled)))
            ctx.tasker.controller.post_click.assert_called_once_with(531, 1161)

    def test_scene_change_discards_stale_recognition(self):
        for kind in ('story', 'follow'):
            ctx = context()
            with self.subTest(kind=kind), patch.object(m, f'{kind}_dialog', return_value=None):
                self.assertTrue(m.ProduceSettlementDialogAction().run(ctx, args(kind)))
            ctx.tasker.controller.post_click.assert_not_called()

    def test_missing_button_retries_three_fresh_frames_without_failure_or_click(self):
        ctx = context(); frames = [object(), object(), object()]
        ctx.tasker.controller.post_screencap.return_value.wait.return_value.get.side_effect = frames
        with patch.object(m, 'story_dialog', return_value={'box': RECORDED[0].box, 'ready': True}) as identify, \
             patch.object(m, 'story_yes', return_value=None), patch.object(m.time, 'sleep'):
            self.assertTrue(m.ProduceSettlementDialogAction().run(ctx, args()))
        self.assertEqual(frames, [call.args[1] for call in identify.call_args_list])
        ctx.tasker.controller.post_click.assert_not_called()

    def test_button_appearing_on_retry_is_used_once(self):
        ctx = context()
        with patch.object(m, 'story_dialog', return_value={'box': RECORDED[0].box, 'ready': True}), \
             patch.object(m, 'story_yes', side_effect=[None, YES]), patch.object(m.time, 'sleep'):
            self.assertTrue(m.ProduceSettlementDialogAction().run(ctx, args()))
        self.assertEqual(2, ctx.tasker.controller.post_screencap.call_count)
        ctx.tasker.controller.post_click.assert_called_once_with(531, 1161)

    def test_missing_follow_cancel_never_falls_back_to_follow(self):
        ctx = context()
        with patch.object(m, 'follow_dialog', return_value={'box': FOLLOW, 'cancel': None}), patch.object(m.time, 'sleep'):
            self.assertTrue(m.ProduceSettlementDialogAction().run(ctx, args('follow', False)))
        ctx.tasker.controller.post_click.assert_not_called()

    def test_stopping_does_not_capture_or_click(self):
        ctx = context(); ctx.tasker.stopping = True
        self.assertTrue(m.ProduceSettlementDialogAction().run(ctx, args()))
        ctx.tasker.controller.post_screencap.assert_not_called()
        ctx.tasker.controller.post_click.assert_not_called()

    def test_stop_during_recognition_prevents_click(self):
        ctx = context()
        def find(*a):
            ctx.tasker.stopping = True
            return YES
        with patch.object(m, 'story_dialog', return_value={'box': RECORDED[0].box, 'ready': True}), patch.object(m, 'story_yes', side_effect=find):
            self.assertTrue(m.ProduceSettlementDialogAction().run(ctx, args()))
        ctx.tasker.controller.post_click.assert_not_called()

    def test_all_story_routes_jump_back_with_no_generic_yes_successor(self):
        base = json.loads((ROOT / 'assets/resource/base/pipeline/Produce.json').read_text('utf8'))
        utils = json.loads((ROOT / 'assets/resource/base/pipeline/ProduceUtils.json').read_text('utf8'))
        order = base['ProduceFinished']['next']
        self.assertLess(order.index('[JumpBack]ProduceSkipChatFlag'), order.index('[JumpBack]ProduceFollow'))
        for node in (base['ProduceFollow'], utils['ProduceSkipChatFlag']):
            self.assertEqual([], node['next'])
            self.assertGreaterEqual(node['post_delay'], 500)
        for path in (ROOT / 'assets/resource/base/pipeline').glob('*.json'):
            for node in json.loads(path.read_text('utf8')).values():
                for successor in node.get('next', []):
                    if 'ProduceSkipChatFlag' in successor:
                        self.assertEqual('[JumpBack]ProduceSkipChatFlag', successor)


if __name__ == '__main__':
    unittest.main()

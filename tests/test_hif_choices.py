"""HIF two-step selection, resumability and swallowed-click regression tests."""

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
from custom.hif import hif_choice_rows, read_hif_choices, read_hif_events
from custom.produce.options import ProduceHIFChooseClassAuto, ProduceHIFChooseCourseAuto
from custom.produce.events import ProduceChooseNIAEventAuto


def frame(names=("Vo", "Vo", "Vo"), selected=None, dim=False):
    image = np.zeros((1280, 720, 3), np.uint8)
    colors = {"Vo": (145, 50, 244), "Da": (240, 150, 20), "Vi": (40, 180, 240)}
    for index, (name, y) in enumerate(zip(names, (661, 761, 862))):
        image[y:y + 74, 52:668] = colors[name] if selected == index else ((221, 175, 174) if dim else (250, 220, 250))
        image[y + 16:y + 35, 80:110] = colors[name]
    return image


def event_frame():
    image = np.zeros((1279, 720, 3), np.uint8)
    for center, color in zip((195, 358, 520), ((145, 50, 244), (240, 150, 20), (40, 180, 240))):
        image[929:1103, center-75:center+75] = (230, 130, 150)
        image[1009:1039, center+35:center+65] = color
    return image


def event_results(text="授業"):
    return [SimpleNamespace(box=[center-29, 1059, 58, 34], text=text) for center in (195, 358, 520)]


def public_lesson_frame():
    image = np.zeros((1279, 720, 3), np.uint8)
    for center, y, color in zip((172, 356, 542), (1078, 1076, 1077),
                              ((145, 50, 244), (240, 150, 20), (40, 180, 240))):
        image[y-130:y+30, center-75:center+75] = 250
        image[y-110:y-60, center-40:center+40] = color
        # All three have the same orange bonus marker, not an attribute badge.
        image[y-50:y-20, center+35:center+65] = (40, 180, 240)
    return image


def public_lesson_results(separator=".", lesson="公開レッスン"):
    return [SimpleNamespace(box=[center-85, y, 170, 28], text=f"{name}{separator}{lesson}")
            for name, center, y in zip(("Vo", "Da", "Vi"), (172, 356, 542), (1078, 1076, 1077))]


class Done:
    def __init__(self, image=None): self.image = image
    def wait(self): return self
    def get(self): return self.image


class Controller:
    def __init__(self, *, stage="option", swallow=0, advance=True, height=1280, preview=False):
        self.stage, self.swallow, self.advance = stage, swallow, advance
        self.height = height
        self.preview, self.selected = preview, None
        self.clicks = []

    def post_screencap(self):
        names = ("Vo", "Da", "Vi") if self.stage == "attribute" else ("Vo", "Vo", "Vo")
        return Done(frame(names, self.selected, dim=self.selected is not None)[:self.height]
                    if self.stage != "done" else np.zeros((self.height, 720, 3), np.uint8))

    def post_click(self, x, y):
        self.clicks.append((x, y))
        if self.swallow:
            self.swallow -= 1
        elif self.preview and self.selected is None:
            self.selected = min(range(3), key=lambda i: abs((698, 798, 899)[i] - y))
        elif self.advance:
            self.stage = "option" if self.stage == "attribute" else "done"
            self.selected = None
        return Done()


class HIFChoicesTest(unittest.TestCase):
    def context(self, controller, **settings):
        context = Mock()
        context.tasker = SimpleNamespace(controller=controller, stopping=False)
        context.get_node_data.side_effect = lambda name: (
            {"attach": settings} if name == "ProduceHIFStrategy" else
            {"action": {"param": {"custom_action_param": {"first": "Da", "second": "Vi"}}}}
        )
        context.run_recognition.return_value = SimpleNamespace(hit=True)
        return context

    def run_action(self, controller, action=None, **settings):
        action = action or ProduceHIFChooseClassAuto()
        with patch("time.sleep"), patch("custom.produce.shared._detect_disconnect_screen", return_value=False), \
             patch.object(ProduceHIFChooseCourseAuto, "_lesson_attribute", return_value="Da"):
            return action.run(self.context(controller, **settings), SimpleNamespace())

    def test_same_attribute_rows_are_options_not_attribute_selection(self):
        context = Mock()
        context.run_recognition.return_value = SimpleNamespace(hit=True)
        stage, rows = read_hif_choices(context, frame())
        self.assertEqual("option", stage)
        self.assertEqual([661, 761, 862], [row[1][1] for row in rows])

    def test_different_attributes_are_the_first_step(self):
        context = Mock()
        context.run_recognition.return_value = SimpleNamespace(hit=True)
        self.assertEqual("attribute", read_hif_choices(context, frame(("Vo", "Da", "Vi")))[0])

    def test_native_frame_height_rounding_keeps_valid_rows(self):
        context = Mock()
        context.run_recognition.return_value = SimpleNamespace(hit=True)
        for height in (1278, 1279, 1280, 1281, 1282):
            with self.subTest(height=height):
                image = np.zeros((height, 720, 3), np.uint8)
                image[:min(height, 1280)] = frame()[:min(height, 1280)]
                stage, rows = read_hif_choices(context, image)
                self.assertEqual("option", stage)
                self.assertEqual([661, 761, 862], [row[1][1] for row in rows])

    def test_incompatible_resolution_and_invalid_channels_are_rejected(self):
        for shape in ((720, 1280, 3), (1200, 720, 3), (1280, 720), (1280, 720, 4)):
            with self.subTest(shape=shape):
                self.assertEqual([], hif_choice_rows(np.zeros(shape, np.uint8)))

    def test_live_1279_frame_can_resume_and_advance(self):
        controller = Controller(height=1279)
        self.assertTrue(self.run_action(controller, lesson_option=1))
        self.assertEqual([(360, 698)], controller.clicks)
        self.assertEqual("done", controller.stage)

    def test_selected_effect_preview_is_not_a_completed_selection(self):
        context = Mock()
        context.run_recognition.return_value = SimpleNamespace(hit=True)
        for attr in ("Vo", "Da", "Vi"):
            with self.subTest(attribute=attr):
                stage, rows = read_hif_choices(context, frame((attr,) * 3, selected=0, dim=True))
                self.assertEqual("option", stage)
                self.assertEqual(3, len(rows))

    def test_dim_rows_without_selected_preview_are_rejected(self):
        self.assertEqual([], hif_choice_rows(frame(dim=True)))

    def test_preview_requires_observed_confirmation_before_success(self):
        controller = Controller(height=1279, preview=True)
        self.assertTrue(self.run_action(controller, lesson_option=1))
        self.assertEqual([(360, 698), (360, 698)], controller.clicks)
        self.assertEqual("done", controller.stage)

    def test_resume_from_existing_preview_can_confirm(self):
        controller = Controller(height=1279, preview=True)
        controller.selected = 0
        self.assertTrue(self.run_action(controller, lesson_option=1))
        self.assertEqual([(360, 698)], controller.clicks)
        self.assertEqual("done", controller.stage)

    def test_two_steps_each_require_preview_and_confirmation(self):
        controller = Controller(stage="attribute", height=1279, preview=True)
        self.assertTrue(self.run_action(controller, lesson_attribute="first", lesson_option=3))
        self.assertEqual([(360, 798), (360, 798), (360, 899), (360, 899)], controller.clicks)
        self.assertEqual("done", controller.stage)

    def test_unresponsive_preview_has_bounded_retry_and_no_success_log(self):
        controller = Controller(height=1279, preview=True, advance=False)
        with patch("custom.produce.shared.logger.success") as success:
            self.assertFalse(self.run_action(controller, lesson_option=1))
        self.assertEqual(3, len(controller.clicks))
        success.assert_not_called()

    def test_unrelated_page_and_missing_row_never_match(self):
        context = Mock()
        context.run_recognition.return_value = SimpleNamespace(hit=False)
        self.assertIsNone(read_hif_choices(context, frame()))
        image = frame()
        image[761:835] = 0
        self.assertFalse(hif_choice_rows(image))
        context.run_recognition.reset_mock()
        self.assertIsNone(read_hif_choices(context, np.zeros((1280, 720, 3), np.uint8)))
        context.run_recognition.assert_not_called()

    def test_ambiguous_icon_colors_do_not_choose(self):
        image = frame()
        image[677:696, 110:140] = (240, 150, 20)
        self.assertFalse(hif_choice_rows(image))

    def test_resume_at_option_screen_clicks_configured_row_center(self):
        controller = Controller()
        self.run_action(controller, lesson_option=2)
        self.assertEqual([(360, 798)], controller.clicks)
        self.assertEqual("done", controller.stage)

    def test_attribute_then_option_uses_unified_setting(self):
        for action in (ProduceHIFChooseClassAuto(), ProduceHIFChooseCourseAuto()):
            with self.subTest(action=type(action).__name__):
                controller = Controller(stage="attribute")
                self.run_action(controller, action, lesson_attribute="first", course_attribute="third", lesson_option=3)
                self.assertEqual([(360, 798), (360, 899)], controller.clicks)
                self.assertEqual("done", controller.stage)

    def test_swallowed_click_is_retried_but_never_reported_as_immediate_success(self):
        controller = Controller(swallow=1)
        self.run_action(controller, lesson_option=1)
        self.assertEqual([(360, 698), (360, 698)], controller.clicks)
        self.assertEqual("done", controller.stage)

    def test_unchanged_screen_has_bounded_retry_and_no_success_log(self):
        controller = Controller(advance=False)
        with patch("custom.produce.shared.logger.success") as success:
            self.assertFalse(self.run_action(controller, lesson_option=1))
        self.assertEqual(3, len(controller.clicks))
        success.assert_not_called()

    def test_stopping_does_not_click(self):
        controller = Controller()
        context = self.context(controller)
        context.tasker.stopping = True
        ProduceHIFChooseClassAuto().run(context, SimpleNamespace())
        self.assertFalse(controller.clicks)

    def test_exact_event_labels_and_icons_select_all_three_attributes(self):
        context = Mock()
        context.run_recognition.return_value = SimpleNamespace(hit=True, filtered_results=event_results())
        self.assertEqual(["Vo", "Da", "Vi"], [row[0] for row in read_hif_events(context, event_frame(), "授業")])
        self.assertEqual([], read_hif_events(context, event_frame(), "レッスン"))

    def test_plain_lesson_entries_keep_original_badge_recognition(self):
        context = Mock()
        for text in ("公開レッスン", "レッスン"):
            with self.subTest(text=text):
                context.run_recognition.return_value = SimpleNamespace(hit=True, filtered_results=event_results(text))
                self.assertEqual(["Vo", "Da", "Vi"],
                                 [row[0] for row in read_hif_events(context, event_frame(), "レッスン")])

    def test_prefixed_public_lessons_use_central_icons_not_common_bonus_star(self):
        context = Mock()
        context.run_recognition.return_value = SimpleNamespace(hit=True, filtered_results=public_lesson_results())
        events = read_hif_events(context, public_lesson_frame(), "レッスン")
        self.assertEqual(["Vo", "Da", "Vi"], [row[0] for row in events])
        self.assertEqual([(172, 1032), (356, 1030), (542, 1031)],
                         [(box[0]+box[2]//2, box[1]+box[3]//2) for _, box, _ in events])
        self.assertEqual([], read_hif_events(context, public_lesson_frame(), "授業"))

    def test_prefixed_lesson_punctuation_and_spacing_variants(self):
        context = Mock()
        for separator in (".", "．", "・", " . "):
            for lesson in ("公開レッスン", "レッスン"):
                with self.subTest(separator=separator, lesson=lesson):
                    context.run_recognition.return_value = SimpleNamespace(
                        hit=True, filtered_results=public_lesson_results(separator, lesson))
                    self.assertEqual(["Vo", "Da", "Vi"],
                                     [row[0] for row in read_hif_events(context, public_lesson_frame(), "レッスン")])

    def test_prefixed_lesson_requires_matching_unambiguous_attribute_icon(self):
        context = Mock()
        context.run_recognition.return_value = SimpleNamespace(hit=True, filtered_results=public_lesson_results()[:1])
        for icon in (0, (240, 150, 20)):
            with self.subTest(icon=icon):
                image = public_lesson_frame()
                image[968:1018, 132:212] = icon
                self.assertEqual([], read_hif_events(context, image, "レッスン"))
        image = public_lesson_frame()
        image[968:1018, 132:172] = (240, 150, 20)
        self.assertEqual([], read_hif_events(context, image, "レッスン"))

    def test_prefixed_lesson_rejects_descriptions_invalid_attributes_and_off_tile_labels(self):
        context = Mock()
        for text in ("Vo.公開レッスン開始時", "次のVo.公開レッスン", "HP.公開レッスン", "Vo/公開レッスン", "Vo.授業"):
            context.run_recognition.return_value = SimpleNamespace(
                hit=True, filtered_results=[SimpleNamespace(box=[87, 1078, 170, 28], text=text)])
            self.assertEqual([], read_hif_events(context, public_lesson_frame(), "レッスン"))
        for box in ([87, 300, 170, 28], [650, 1078, 170, 28], [87, 1078, 170, 80]):
            context.run_recognition.return_value = SimpleNamespace(
                hit=True, filtered_results=[SimpleNamespace(box=box, text="Vo.公開レッスン")])
            self.assertEqual([], read_hif_events(context, public_lesson_frame(), "レッスン"))

    def test_prefixed_lesson_handles_height_rounding_and_ocr_miss(self):
        context = Mock()
        context.run_recognition.return_value = SimpleNamespace(hit=True, filtered_results=public_lesson_results())
        for height in (1278, 1279, 1280, 1281, 1282):
            image = np.zeros((height, 720, 3), np.uint8)
            image[:min(height, 1279)] = public_lesson_frame()[:min(height, 1279)]
            self.assertEqual(["Vo", "Da", "Vi"], [row[0] for row in read_hif_events(context, image, "レッスン")])
        context.run_recognition.return_value = SimpleNamespace(hit=False, filtered_results=public_lesson_results())
        self.assertEqual([], read_hif_events(context, public_lesson_frame(), "レッスン"))

    def test_card_description_is_not_a_lesson_entrance_even_when_ocr_reports_hit(self):
        context = Mock()
        for text in ("重複不可レッスン中1回", "レッスン開始時", "授業後"):
            context.run_recognition.return_value = SimpleNamespace(hit=True, filtered_results=event_results(text))
            self.assertEqual([], read_hif_events(context, event_frame(), "レッスン"))
            self.assertEqual([], read_hif_events(context, event_frame(), "授業"))

    def test_event_label_without_attribute_badge_is_not_clickable(self):
        context = Mock()
        context.run_recognition.return_value = SimpleNamespace(hit=True, filtered_results=event_results())
        self.assertEqual([], read_hif_events(context, np.zeros((1279,720,3),np.uint8), "授業"))

    def event_controller(self, advance=True):
        class EventController(Controller):
            def post_screencap(self):
                return Done(event_frame()) if self.stage == "event" else super().post_screencap()
            def post_click(self, x, y):
                if self.stage == "event":
                    self.clicks.append((x,y))
                    if self.advance:
                        self.stage = "attribute"
                    return Done()
                return super().post_click(x,y)
        return EventController(stage="event", advance=advance)

    def run_event(self, controller):
        context = self.context(controller, lesson_attribute="first", lesson_option=3)
        context.run_recognition.side_effect = lambda *args, **kwargs: SimpleNamespace(
            hit=True, filtered_results=event_results() if controller.stage == "event" else [])
        with patch("time.sleep"), patch("custom.produce.shared._detect_disconnect_screen", return_value=False):
            return ProduceHIFChooseClassAuto().run(context, SimpleNamespace())

    def test_daily_event_then_attribute_then_option_uses_configured_setting(self):
        controller = self.event_controller()
        self.assertTrue(self.run_event(controller))
        self.assertEqual([(358,1016),(360,798),(360,899)], controller.clicks)
        self.assertEqual("done", controller.stage)

    def test_unresponsive_daily_event_has_bounded_retry(self):
        controller = self.event_controller(advance=False)
        with patch("custom.produce.shared.logger.success") as success:
            self.assertFalse(self.run_event(controller))
        self.assertEqual(3,len(controller.clicks))
        success.assert_not_called()

    def test_public_lesson_entry_then_policy_attribute_then_configured_option(self):
        controller = self.event_controller()
        original_screencap = controller.post_screencap
        controller.post_screencap = lambda: Done(public_lesson_frame()) if controller.stage == "event" else original_screencap()
        context = self.context(controller, lesson_attribute="first", lesson_option=3)
        context.run_recognition.side_effect = lambda *args, **kwargs: SimpleNamespace(
            hit=True, filtered_results=public_lesson_results() if controller.stage == "event" else [])
        with patch("time.sleep"), patch("custom.produce.shared._detect_disconnect_screen", return_value=False), \
             patch.object(ProduceHIFChooseCourseAuto, "_lesson_attribute", return_value="Da"):
            self.assertTrue(ProduceHIFChooseCourseAuto().run(context, SimpleNamespace()))
        self.assertEqual([(356, 1030), (360, 798), (360, 899)], controller.clicks)
        self.assertEqual("done", controller.stage)

    def test_course_sp_decision_uses_stable_entry_frame_then_keeps_attribute_and_option(self):
        controller = self.event_controller()
        original_screencap = controller.post_screencap
        observations = []

        def screencap():
            if controller.stage != "event":
                return original_screencap()
            image = public_lesson_frame()
            observations.append(image)
            image[0, 0] = len(observations)
            return Done(image)

        controller.post_screencap = screencap
        context = self.context(controller, lesson_attribute="third", lesson_option=3)
        original_node = context.get_node_data.side_effect
        context.get_node_data.side_effect = lambda name: (
            {"action": {"param": {"custom_action_param": {"first":"Vo", "second":"Da"}}}}
            if name == "ProduceChooseNIAEventFlag" else original_node(name))
        context.run_recognition.side_effect = lambda *args, **kwargs: SimpleNamespace(
            hit=True, filtered_results=public_lesson_results() if controller.stage == "event" else [])
        # The SP marker appears only on the second observation of the entrance.
        def sp(context, image, roi):
            return int(image[0, 0, 0]) >= 2 and 200 <= roi[0] < 400

        with patch("time.sleep"), \
             patch("custom.produce.shared._detect_disconnect_screen", return_value=False), \
             patch.object(ProduceChooseNIAEventAuto, "_get_sp_course", side_effect=sp) as marker:
            self.assertTrue(ProduceHIFChooseCourseAuto().run(context, SimpleNamespace()))
        self.assertEqual([(356, 1030), (360, 798), (360, 899)], controller.clicks)
        self.assertEqual("done", controller.stage)
        self.assertEqual(3, marker.call_count)
        self.assertTrue(all(int(call.args[1][0, 0, 0]) == 2 for call in marker.call_args_list))

    def test_unrelated_page_hands_back_without_clicking_description(self):
        controller = Controller(stage="done")
        self.assertTrue(self.run_action(controller))
        self.assertFalse(controller.clicks)

    def test_duplicate_ui_removed_and_resume_node_present(self):
        tasks = json.loads((ROOT / "assets/tasks/produce.json").read_text(encoding="utf-8"))
        self.assertNotIn("HIF本战课程属性", tasks["option"])
        hif = json.loads((ROOT / "assets/resource/base/pipeline/ProduceHIF.json").read_text(encoding="utf-8"))
        self.assertIn("[JumpBack]ProduceHIFChoicesFlag", hif["ProduceEntryHIF"]["next"])
        self.assertNotIn("course_attribute", hif["ProduceHIFStrategy"]["attach"])
        self.assertEqual("ProduceHIFClassEvent", hif["ProduceHIFClassFlag"]["recognition"]["param"]["custom_recognition"])
        self.assertEqual("ProduceHIFCourseEvent", hif["ProduceHIFCourseFlag"]["recognition"]["param"]["custom_recognition"])

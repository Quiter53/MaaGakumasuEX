import sys
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
from custom import customization as c
from custom.produce.shop_customization import CustomizationSession


def target(name="幸せのおまじない", counts=None):
    return dict(key=name, name=name, counts=counts or [2, 0, 1], spec=c.catalog()[name])


def menu(name="幸せのおまじない", remaining=(3, 1, 1), total=3, prices=(40, 70, 20)):
    return dict(stage="menu", name=name, upgraded=True, total=total, execute=[460, 1140, 120, 35], back=[160, 1140, 150, 35],
                options=[dict(remaining=n, price=price, selected=False, capped=False, box=[40+i*219, 900, 195, 85])
                         for i, (n, price) in enumerate(zip(remaining, prices))])


def test_config_is_off_by_default_and_bad_limits_do_not_guess():
    context = Mock(); context.get_node_data.return_value = {}
    assert c.targets(context) == []
    for counts in ([3, 1, 0], [4, 0, 0], [-1, 0, 0], [1], [True, 0, 0], [0, 0, 0]):
        context.get_node_data.return_value = {"attach": {"enabled": True, "targets": [{"name": "幸せのおまじない", "counts": counts}]}}
        with pytest.raises(ValueError): c.targets(context)


def test_partial_customization_only_buys_missing_counts():
    assert c.next_purchase(target(), menu(), 119) == ("buy", 0)
    assert c.next_purchase(target(), menu(remaining=(2, 1, 1), total=2), 79) == ("buy", 0)
    assert c.next_purchase(target(), menu(remaining=(1, 1, 1), total=1, prices=(70, 70, 20)), 39) == ("buy", 2)
    assert c.next_purchase(target(), menu(remaining=(1, 1, 0), total=0), 19) == ("capped", None)


def test_completed_different_option_is_counted_against_total_cap():
    assert c.next_purchase(target(counts=[1, 0, 0]), menu(remaining=(2, 0, 1), total=1), 300) == ("done", None)
    assert c.next_purchase(target(), menu(remaining=(2, 0, 1), total=1), 19) == ("budget", 0)


@pytest.mark.parametrize("change", [
    {"name": "仕切り直し"}, {"upgraded": False}, {"total": None}, {"total": 2}, {"options": []},
])
def test_ambiguous_menu_never_authorizes_purchase(change):
    page = menu(); page.update(change)
    assert c.next_purchase(target(), page, 1000)[0] == "unknown"


def test_missing_counts_and_unexpected_price_are_not_completion():
    page = menu(); page["options"][0]["remaining"] = None
    assert c.next_purchase(target(), page, 1000)[0] == "unknown"
    page = menu(); page["options"][0]["price"] = 20
    assert c.next_purchase(target(), page, 1000)[0] == "unknown"
    assert c.next_purchase(target(), menu(), None)[0] == "unknown"


def test_all_gray_maxed_card_is_skipped_without_readable_price():
    page = menu("輝くキミへ", remaining=(0, 0), total=0, prices=(None, None))
    assert c.next_purchase(target("輝くキミへ", [1, 0]), page, 19) == ("capped", None)


def test_numbered_options_never_shift_when_first_is_disabled():
    page = menu(remaining=(0, 1, 1), total=0)
    assert c.next_purchase(target(), page, 200)[1] is None


def session():
    context, shop = Mock(), Mock()
    context.get_node_data.return_value = {}
    context.tasker.stopping = False
    obj = CustomizationSession(context, shop)
    obj.balance = 119  # Observed on the shop before opening the obscured menu.
    obj.rules = [target()]; obj.by_name = {"幸せのおまじない": obj.rules[0]}
    return obj, context, shop


def test_no_receipt_means_only_one_execute_and_persistent_block():
    obj, context, shop = session()
    image = np.zeros((1280, 720, 3), np.uint8)
    page = menu(); selected = deepcopy(page); selected["options"][0]["selected"] = True
    obj.stable = Mock(side_effect=[(image, page), (image, selected)])
    obj.capture = Mock(return_value=image); obj.click = Mock()
    shop._recognize_points.return_value = 119
    shop._is_shop_scene.return_value = False
    with patch.object(c, "receipt", return_value=None), patch.object(c, "read_page", return_value=selected), patch("custom.produce.shop_customization.time.sleep"):
        with pytest.raises(InterruptedError): obj.purchase(target(), page)
    assert obj.click.call_args_list[-1].args[0] == page["execute"]
    assert obj.click.call_count == 2
    shop._visit_state.assert_called_once_with(context, custom_pending=True, custom_blocked=True)


def test_verified_menu_progress_keeps_debit_pending_until_shop():
    obj, context, shop = session(); image = np.zeros((1280, 720, 3), np.uint8)
    page = menu(); selected = deepcopy(page); selected["options"][0]["selected"] = True
    after = menu(remaining=(2, 1, 1), total=2)
    obj.stable = Mock(side_effect=[(image, page), (image, selected)])
    obj.capture = Mock(return_value=image); obj.click = Mock()
    shop._recognize_points.side_effect = AssertionError("obscured menu must not read balance")
    with patch.object(c, "receipt", return_value=None), patch.object(c, "read_page", return_value=after), patch("custom.produce.shop_customization.time.sleep"):
        assert obj.purchase(target(), page) == "bought"
    assert shop._visit_state.call_args.kwargs == {"custom_pending": True, "custom_blocked": True}
    assert obj.balance == 79 and obj.balance_pending


def test_wrong_selection_does_not_click_execute():
    obj, context, shop = session(); image = np.zeros((1280, 720, 3), np.uint8)
    page = menu(); selected = deepcopy(page); selected["options"][1]["selected"] = True
    obj.stable = Mock(side_effect=[(image, page), (image, selected)]); obj.click = Mock()
    shop._recognize_points.return_value = 119
    with pytest.raises(InterruptedError): obj.purchase(target(), page)
    assert obj.click.call_count == 1
    shop._visit_state.assert_not_called()


def test_disabled_and_blocked_sessions_never_enter_card_list():
    obj, context, shop = session(); obj.rules = []; image = object()
    assert obj.run(image) == (image, None)
    context.tasker.controller.post_click.assert_not_called()
    obj.rules = [target()]; shop._visit_state.return_value = {"custom_blocked": True}
    assert obj.run(image) == (image, None)
    context.tasker.controller.post_click.assert_not_called()


def test_task_switch_and_recovery_routes_preserve_existing_options():
    task = json.loads((ROOT / "assets/tasks/produce.json").read_text("utf8"))
    cn = json.loads((ROOT / "assets/tasks/produce_cn.json").read_text("utf8"))
    for document in (task, cn):
        options = document["task"][0]["option"]
        assert options[options.index("跳过选择偶像")+1] == "启用自动自定义"
    switch = task["option"]["启用自动自定义"]
    assert switch["default_case"] == "No"
    assert switch["cases"][0]["option"] == ["自定义卡牌设置"]
    data = json.loads((ROOT / "assets/resource/base/pipeline/ProduceHIF.json").read_text("utf8"))
    assert data["ProduceHIFCustomizationPolicy"]["attach"]["enabled"] is False
    route = data["ProduceEntryHIF"]["next"]
    assert route.index("[JumpBack]ProduceHIFCustomizationFlag") < route.index("[JumpBack]ProduceHIFIntervalShopFlag")


def test_limits_are_card_specific_and_all_options_have_prices():
    assert c.catalog()["幸せのおまじない"]["max"] == 3
    assert c.catalog()["仕切り直し"]["max"] == 1
    assert len(c.catalog()["仕切り直し"]["options"]) == 2
    for spec in c.catalog().values():
        assert 1 <= spec["max"] <= 3
        assert 1 <= len(spec["options"]) <= 3
        assert all(all(p > 0 for p in option["costs"]) for option in spec["options"])


def test_refresh_requested_only_for_known_unfinished_work():
    obj, context, shop = session(); image = object()
    t = target(counts=[3, 0, 0]); other = target("輝くキミへ", [1, 0])
    obj.rules = [t, other]; obj.by_name = {row["key"]: row for row in obj.rules}
    first = menu(); second = menu(other['key'], remaining=(1, 1), total=1, prices=(100, 40))
    obj.scan = Mock(return_value=[dict(target=t, menu=first), dict(target=other, menu=second)])
    obj.scan.side_effect = [[dict(target=t, menu=first), dict(target=other, menu=second)], (t, first)]
    obj.customize_card = Mock(return_value="bought"); obj.stable = Mock(); obj.click = Mock(); obj.leave = Mock(return_value=image)
    shop._visit_state.return_value = {}; shop._recognize_points.return_value = 1000
    gates = [dict(box=[1, 1, 10, 10], quota=n) for n in (1, 1, 0)]
    with patch.object(c, "entry", side_effect=gates):
        assert obj.run(image) == (image, 100)
    assert obj.customize_card.call_count == 1
    assert obj.awaiting_refresh


def test_empty_plan_never_requests_paid_refresh():
    obj, context, shop = session(); image = object()
    obj.scan = Mock(return_value=[]); obj.stable = Mock(); obj.click = Mock(); obj.leave = Mock(return_value=image)
    shop._visit_state.return_value = {}; shop._recognize_points.return_value = 119
    with patch.object(c, "entry", return_value=dict(box=[1, 1, 10, 10], quota=2)):
        assert obj.run(image) == (image, None)


def test_unaffordable_priority_target_does_not_spend_on_lower_targets():
    obj, context, shop = session(); image = object()
    first = target("輝くキミへ", [1, 0]); later = target("仕切り直し", [1, 0])
    obj.rules = [first, later]
    obj.scan = Mock(return_value=[dict(target=first, menu=menu(first["key"], remaining=(1, 1), total=1, prices=(100, 40)))])
    obj.purchase = Mock(); obj.stable = Mock(); obj.click = Mock(); obj.leave = Mock(return_value=image)
    shop._visit_state.return_value = {}; shop._recognize_points.return_value = 79
    with patch.object(c, "entry", return_value=dict(box=[1, 1, 10, 10], quota=1)):
        assert obj.run(image) == (image, None)
    obj.purchase.assert_not_called()


def test_too_few_points_does_not_open_dimmed_deck():
    obj, context, shop = session(); image = object()
    shop._visit_state.return_value = {}; shop._recognize_points.return_value = 19
    assert obj.run(image) == (image, None)
    context.tasker.controller.post_click.assert_not_called()


@pytest.mark.parametrize("frame,stage,name,total", [
    (2, None, None, None), (3, "list", None, None),
    (4, "list", "仕切り直し", None), (5, "list", "幸せのおまじない", None),
    (6, "menu", "幸せのおまじない", 3), (7, "menu", "幸せのおまじない", 3),
    (8, "menu", "仕切り直し", 1), (9, None, None, None),
    (10, "menu", "輝くキミへ", 0), (11, "list", "輝くキミへ", None),
])
def test_recorded_maa_ocr_reads_page_without_confusing_shop(frame, stage, name, total):
    recorded = json.loads((ROOT / "tests/fixtures/customization/ocr.json").read_text("utf8"))[str(frame)]
    def observed(context, image, roi, expected):
        return [SimpleNamespace(**row) for row in recorded.get(",".join(map(str, roi)), [])]
    with patch.object(c, "ocr", side_effect=observed):
        page = c.read_page(Mock(), np.zeros((1280, 720, 3), np.uint8))
    if stage is None:
        assert page is None
    else:
        assert (page["stage"], page["name"], page.get("total")) == (stage, name, total)
        if name:
            assert page["upgraded"]


@pytest.mark.parametrize("frame,expected", [(6, None), (7, 0), (8, 1), (10, None)])
def test_real_selection_outline_tolerates_three_pixel_offsets(frame, expected):
    from PIL import Image
    crop = np.asarray(Image.open(ROOT / f"tests/fixtures/customization/menu-{frame}.png").convert("RGB"))[:, :, ::-1]
    original = np.zeros((1280, 720, 3), np.uint8)
    original[865:1020] = crop
    for dx in (-3, 0, 3):
        for dy in (-3, 0, 3):
            image = np.roll(original, (dy, dx), axis=(0, 1))
            assert [i for i in range(3) if c.selected_option(image, i)] == ([] if expected is None else [expected]), (frame, dx, dy)


def test_other_option_decrement_is_not_a_valid_receipt():
    obj, context, shop = session(); image = np.zeros((1280, 720, 3), np.uint8)
    page = menu(); selected = deepcopy(page); selected["options"][0]["selected"] = True
    after = menu(remaining=(2, 0, 1), total=2)
    obj.stable = Mock(side_effect=[(image, page), (image, selected)])
    obj.capture = Mock(return_value=image); obj.click = Mock()
    shop._recognize_points.side_effect = [119, 119] + [79] * 12
    with patch.object(c, "receipt", return_value=None), patch.object(c, "read_page", return_value=after), patch("custom.produce.shop_customization.time.sleep"):
        with pytest.raises(InterruptedError): obj.purchase(target(), page)
    assert obj.click.call_count == 2
    shop._visit_state.assert_called_once_with(context, custom_pending=True, custom_blocked=True)


@pytest.mark.parametrize("balance,after_name,total,capped,success", [
    (205, "輝くキミへ", 0, True, True),
    (238, "輝くキミへ", 0, True, False),
    (205, "仕切り直し", 0, True, False),
    (205, "輝くキミへ", 1, True, False),
    (205, "輝くキミへ", 0, False, False),
])
def test_last_customization_global_cap_requires_named_menu_and_exact_debit(balance, after_name, total, capped, success):
    obj, context, shop = session(); image = np.zeros((1280, 720, 3), np.uint8)
    # Guidance still has its original per-operation currency observations.
    obj.guidance = True
    t = target("輝くキミへ", [1, 0])
    page = menu(t["key"], remaining=(1, 1), total=1, prices=(100, 40))
    selected = deepcopy(page); selected["options"][0]["selected"] = True
    after = menu(after_name, remaining=(0, 0), total=total, prices=(100, 40))
    for option in after["options"]: option["capped"] = capped
    obj.stable = Mock(side_effect=[(image, page), (image, selected)])
    obj.capture = Mock(return_value=image); obj.click = Mock()
    shop._recognize_points.side_effect = [305, 305] + [balance] * 12
    with patch.object(c, "receipt", return_value=None), patch.object(c, "read_page", return_value=after), patch("custom.produce.shop_customization.time.sleep"):
        if success:
            assert obj.purchase(t, page) == "bought"
            assert obj.capture.call_count == 2
        else:
            with pytest.raises(InterruptedError): obj.purchase(t, page)
            assert shop._visit_state.call_count == 1
    assert obj.click.call_count == 2


@pytest.mark.parametrize("returned,shape,skip_count", [(False, (1280, 720, 3), 3), (True, (1280, 720, 3), 0), (False, (720, 1280, 3), 0)])
def test_pending_animation_corner_taps_are_bounded_and_balance_alone_never_confirms(returned, shape, skip_count):
    obj, context, shop = session(); image = np.zeros(shape, np.uint8)
    page = menu(); selected = deepcopy(page); selected["options"][0]["selected"] = True
    obj.stable = Mock(side_effect=[(image, page), (image, selected)])
    obj.capture = Mock(return_value=image); obj.click = Mock()
    shop._recognize_points.side_effect = [119, 119] + [79] * 12
    shop._is_shop_scene.return_value = returned
    with patch.object(c, "receipt", return_value=None), patch.object(c, "read_page", return_value=None), patch("custom.produce.shop_customization.time.sleep"):
        with pytest.raises(InterruptedError): obj.purchase(target(), page)
    assert [call.args[0] for call in obj.click.call_args_list] == [page["options"][0]["box"], page["execute"]] + [[20, 20, 5, 5]] * skip_count
    shop._visit_state.assert_called_once_with(context, custom_pending=True, custom_blocked=True)


def test_animation_skip_then_stable_menu_finishes_without_reexecuting():
    obj, context, shop = session(); image = np.zeros((1280, 720, 3), np.uint8)
    page = menu(); selected = deepcopy(page); selected["options"][0]["selected"] = True
    after = menu(remaining=(2, 1, 1), total=2)
    obj.stable = Mock(side_effect=[(image, page), (image, selected)])
    obj.capture = Mock(return_value=image); obj.click = Mock()
    shop._recognize_points.side_effect = [119, 119, None, 79, 79]
    shop._is_shop_scene.return_value = False
    with patch.object(c, "receipt", return_value=None), patch.object(c, "read_page", side_effect=[None, after, after]), patch("custom.produce.shop_customization.time.sleep"):
        assert obj.purchase(target(), page) == "bought"
    assert [call.args[0] for call in obj.click.call_args_list] == [page["options"][0]["box"], page["execute"], [20, 20, 5, 5]]


@pytest.mark.parametrize("stopping,disconnected", [(True, False), (False, True)])
def test_stop_or_disconnect_after_execute_never_taps_animation(stopping, disconnected):
    obj, context, shop = session(); image = np.zeros((1280, 720, 3), np.uint8)
    page = menu(); selected = deepcopy(page); selected["options"][0]["selected"] = True
    obj.stable = Mock(side_effect=[(image, page), (image, selected)])
    shop._recognize_points.return_value = 119
    def click(box):
        if box == page["execute"]: context.tasker.stopping = stopping
    obj.click = Mock(side_effect=click)
    context.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value = image
    with patch("custom.produce.shared._detect_disconnect_screen", return_value=disconnected), pytest.raises(InterruptedError):
        obj.purchase(target(), page)
    assert obj.click.call_count == 2


def test_point_observations_do_not_spam_normal_logs():
    from custom.produce.shop import ProduceHIFIntervalShoppingAuto
    ctx = Mock()
    ctx.run_recognition.return_value = SimpleNamespace(hit=True, filtered_results=[SimpleNamespace(text="205", score=.99)])
    image = np.zeros((1280,720,3), np.uint8)
    with patch("custom.produce.shop.logger") as log:
        for _ in range(12):
            assert ProduceHIFIntervalShoppingAuto._recognize_points(ctx, image) == 205
        ctx.run_recognition.return_value = None
        assert ProduceHIFIntervalShoppingAuto._recognize_points(ctx, image) is None
        log.info.assert_not_called(); log.warning.assert_not_called()
        assert log.debug.call_count == 13


def test_real_interval_flow_heals_then_customizes_then_checks_drinks():
    from test_shop_recovery_contracts import context
    from custom.produce.shop import ProduceHIFIntervalShoppingAuto
    ctx = context(); shop = ProduceHIFIntervalShoppingAuto()
    image = ctx.tasker.controller.post_screencap().wait().get()
    events = []
    def heal(*args, **kwargs):
        events.append("surplus" if kwargs.get("spend_remaining") else "heal")
        return image
    with patch("time.sleep"), patch("custom.produce.shared._detect_disconnect_screen", return_value=False), \
         patch("custom.shop.read_refresh", return_value=None), \
         patch.object(shop, "_wait_shop_ready", return_value=image), \
         patch.object(shop, "_is_shop_scene", return_value=True), \
         patch.object(shop, "_recognize_points", return_value=300), \
         patch.object(shop, "_drink_inventory_full", side_effect=lambda *a: events.append("drinks") or False), \
         patch.object(shop, "_recognize_drinks", return_value=[]), \
         patch.object(shop, "_dismiss_comment_bubble", return_value=True), \
         patch.object(shop, "_heal_before_drinks", side_effect=heal), \
         patch.object(CustomizationSession, "run", side_effect=lambda *a: (events.append("custom") or image, None)):
        assert shop.run(ctx, SimpleNamespace())
    assert events == ["heal", "custom", "drinks", "surplus"]
    ctx.run_task.assert_called_once_with("ProduceShoppingExit")


def test_real_grid_detection_jitter_and_selection_do_not_duplicate_owned_cards():
    from custom.owned_deck import RowCounts
    path = ROOT / "tests/fixtures/customization"
    metadata = json.loads((path / "grid.json").read_text("utf8"))
    with np.load(path / "grid-interiors.npz", allow_pickle=False) as samples:
        frames = {}
        for frame, rows in metadata.items():
            for index, cell in enumerate(cell for row in rows for cell in row):
                cell["pixels"] = samples[frame][index]
            frames[frame] = rows
    for first, second in (("3", "4"), ("3", "5"), ("4", "5")):
        tracker = RowCounts(None, [], row_match=c.same_card_row)
        tracker.add(frames[first]); tracker.add(frames[second])
        assert tracker.valid
        assert sum(map(len, tracker.rows)) == 12
    shifted = deepcopy(frames["3"])
    shifted[0][0]["pixels"] = shifted[0][1]["pixels"]
    assert not c.same_card_row(frames["3"][0], shifted[0])
    # Scrolling down by two rows keeps the bottom row as an overlap.
    tracker = RowCounts(None, [], row_match=c.same_card_row)
    tracker.add(frames["3"])
    tracker.add([frames["4"][2], frames["4"][0]])
    assert tracker.valid and sum(map(len, tracker.rows)) == 16


def test_recorded_success_toast_only_confirms_the_named_card():
    recorded = json.loads((ROOT / "tests/fixtures/customization/ocr.json").read_text("utf8"))
    image = np.zeros((1280, 720, 3), np.uint8)
    for frame, areas in recorded.items():
        def observed(context, image, roi, expected):
            return [SimpleNamespace(**row) for row in areas.get(",".join(map(str, roi)), [])]
        with patch.object(c, "ocr", side_effect=observed):
            assert bool(c.receipt(Mock(), image, "輝くキミへ")) == (frame == "9")
            assert c.receipt(Mock(), image, "幸せのおまじない") is None


def test_incident_menu_was_readable_and_offered_requested_purchase():
    recorded = json.loads((ROOT / "tests/fixtures/customization/incident-menu.json").read_text("utf8"))
    def observed(context, image, roi, expected):
        return [SimpleNamespace(**row) for row in recorded.get(",".join(map(str, roi)), [])]
    with patch.object(c, "ocr", side_effect=observed):
        page = c.read_page(Mock(), np.zeros((1280, 720, 3), np.uint8))
    assert c.next_purchase(target("輝くキミへ", [1, 0]), page, 395) == ("buy", 0)
    assert [o["price"] for o in page["options"]] == [100, 40]


def test_actual_incident_capped_menu_confirms_final_customization():
    recorded = json.loads((ROOT / "tests/fixtures/customization/incident-capped.json").read_text("utf8"))
    def observed(context, image, roi, expected):
        return [SimpleNamespace(**row) for row in recorded.get(",".join(map(str, roi)), [])]
    with patch.object(c, "ocr", side_effect=observed):
        after = c.read_page(Mock(), np.zeros((1280, 720, 3), np.uint8))
    before = menu("輝くキミへ", remaining=(1, 1), total=1, prices=(100, 40))
    assert after["total"] == 0 and all(o["capped"] for o in after["options"])
    assert c.purchase_progress(before, after, 0)
    # The same global-cap display cannot prove a one-step purchase from two.
    before["total"] = 2
    assert not c.purchase_progress(before, after, 0)


@pytest.mark.parametrize("frame", [4, 5, 11])
def test_captured_list_preview_blocks_capped_entry_before_click(frame):
    fixture = ROOT / "tests/fixtures/customization"
    recorded = json.loads((fixture / "list-preview-ocr.json").read_text("utf8"))[str(frame)]
    image = np.zeros((1280, 720, 3), np.uint8)
    with np.load(fixture / f"list-{frame}.npz") as data:
        image[385:545, 30:680] = data["preview"]
        image[1120:1205, 375:640] = data["button"]
    def observed(context, image, roi, expected):
        return [SimpleNamespace(**row) for row in recorded.get(",".join(map(str, roi)), [])]
    with patch.object(c, "ocr", side_effect=observed):
        page = c.read_page(Mock(), image)
    assert page["capped"] == (frame == 11)
    assert page["open_enabled"] == (frame != 11)
    for dx in (-3, 0, 3):
        assert c.open_button_enabled(np.roll(image, dx, axis=1), page['open']) == (frame != 11)
    obj, ctx, shop = session()
    obj.by_name[page['name']] = target(page['name'], [1, 0] if frame != 5 else [1, 0, 0])
    obj.click = Mock(); obj.stable = Mock(side_effect=[(image, page), (image, menu())])
    with patch('custom.produce.shop_customization.selected_card_box_matches', return_value=True):
        result = obj.open_card(dict(name_key=page['name'], box=[82, 567, 116, 116]))
    assert obj.click.call_count == (1 if frame == 11 else 2)
    if frame == 11: assert result == (page, None)


@pytest.mark.parametrize("enabled", [False, None])
def test_disabled_or_unknown_entry_is_not_clicked_or_treated_as_completed(enabled):
    obj, context, shop = session(); obj.click = Mock()
    page = dict(name=target()['key'], upgraded=True, capped=False, open_enabled=enabled, open=[400, 1140, 170, 35])
    obj.stable = Mock(return_value=(None, page))
    with patch('custom.produce.shop_customization.selected_card_box_matches', return_value=True), pytest.raises(InterruptedError):
        obj.open_card(dict(name_key=page['name'], box=[82, 567, 116, 116]))
    assert obj.click.call_count == 1  # Only select the card; never its dim entry.


def test_guidance_list_exit_distinguishes_it_from_interval_list():
    from custom.produce.shop import GuidanceCustomizationState
    from custom.reco.hif import ProduceGuideCustomization, ProduceHIFCustomization
    from test_shop_recovery_contracts import context
    ctx = context(); image = np.zeros((1280,720,3),np.uint8)
    def observed(label):
        return [SimpleNamespace(text=t,box=b,score=.99) for t,b in [
            (label,[100,1140,100,40]),('カスタマイズ',[450,1140,160,40]),
            ('カスタマイズ可能スキルカードあと2枚',[380,1080,260,30])]]
    args=SimpleNamespace(image=image)
    with patch.object(c,'ocr',return_value=observed('終了')):
        assert c.read_page(ctx,image) is None
        assert c.read_page(ctx,image,guidance=True)['guidance']
        assert ProduceGuideCustomization().analyze(ctx,args)
        assert ProduceHIFCustomization().analyze(ctx,args) is None
    with patch.object(c,'ocr',return_value=observed('もどる')):
        assert ProduceGuideCustomization().analyze(ctx,args) is None
        assert ProduceHIFCustomization().analyze(ctx,args)
    with patch.object(c,'read_page',return_value=menu()):
        assert ProduceGuideCustomization().analyze(ctx,args) is None
        GuidanceCustomizationState._visit_state(ctx,active=True)
        assert ProduceGuideCustomization().analyze(ctx,args)


@pytest.mark.parametrize("ys", [(567, 713, 859), (625, 771, 917), (671, 817)])
def test_customization_drag_retains_a_full_row_and_brakes_before_release(ys):
    obj, context, shop = session()
    groups = [[dict(box=[82+146*i, y, 116, 116]) for i in range(4)] for y in ys]
    current = (None, None, groups)
    context.run_action_direct.return_value = SimpleNamespace(success=True)
    with patch("custom.produce.shop_customization.time.sleep"):
        for forward in (True, False):
            obj.scroll(forward, current)
            kind, drag = context.run_action_direct.call_args.args
            distance = abs(drag.begin[1]-drag.end[0][1])
            assert kind == "Swipe" and drag.duration[0] >= 900 and drag.end_hold[0] >= 350
            assert 0 < distance <= 292
            assert 560 < drag.begin[1] < 1050 and 560 < drag.end[0][1] < 1050
            if forward:
                # A fully visible old row survives in the new viewport.
                assert any(560 <= y-distance and y-distance+116 <= 1050 for y in ys)
    context.tasker.controller.post_swipe.assert_not_called()


@pytest.mark.parametrize("stopping,success", [(True, True), (False, False)])
def test_customization_interrupted_drag_never_continues_scanning(stopping, success):
    obj, context, shop = session()
    context.tasker.stopping = stopping
    context.run_action_direct.return_value = SimpleNamespace(success=success)
    with patch("custom.produce.shop_customization.time.sleep"), pytest.raises(InterruptedError):
        obj.scroll(True, (None, None, []))
    if stopping:
        context.run_action_direct.assert_not_called()


def test_target_on_next_page_is_kept_until_scan_finishes_with_overlap():
    obj, context, shop = session()
    t = target("輝くキミへ", [1, 0]); obj.rules = [t]; obj.by_name = {t["key"]: t}
    # Reuse the captured card interiors: row 3 overlaps the next viewport.
    path = ROOT / "tests/fixtures/customization"
    groups = json.loads((path / "grid.json").read_text("utf8"))["3"]
    with np.load(path / "grid-interiors.npz", allow_pickle=False) as samples:
        for i, cell in enumerate(c for row in groups for c in row):
            cell["pixels"] = samples["3"][i]
            cell["name_key"] = "other"
    last = deepcopy(groups[0]); last[0]["name_key"] = t["key"]
    first = (None, None, groups)
    following = (None, None, [deepcopy(groups[-1]), last])
    obj.rewind = Mock(return_value=first)
    obj.grid = Mock(return_value=following)
    obj.scroll = Mock(); obj.click = Mock(); obj.stable = Mock()
    obj.same_view = lambda a, b: a is b
    page = menu(t["key"], remaining=(1, 1), total=1, prices=(100, 40))
    obj.open_card = Mock(return_value=(dict(name=t["key"]), page))
    jobs = obj.scan()
    assert len(jobs) == 1 and jobs[0]["target"] == t
    # Inspecting repeated views must not duplicate the planned transaction.
    assert obj.scroll.call_count == 3


def scrollbar_frame():
    image = np.zeros((1280, 720, 3), np.uint8)
    with np.load(ROOT / 'tests/fixtures/customization/scrollbar.npz') as data:
        image[550:1045, 690:715] = data['top']
    return image


def test_captured_customization_thumb_proves_top_without_any_swipe():
    image = scrollbar_frame()
    thumb = c.scrollbar(image)
    assert thumb is not None and 560 <= thumb[0] <= 580 and 780 <= thumb[1] <= 800
    obj, context, shop = session()
    current = (image, {}, [])
    obj.grid = Mock(return_value=current); obj.scroll = Mock()
    assert obj.rewind() is current
    obj.scroll.assert_not_called()


def test_gray_track_and_missing_thumb_do_not_prove_top():
    image = scrollbar_frame()
    image[550:790, 690:715] = 0
    assert c.scrollbar(image) is None


def test_visible_middle_thumb_vetoes_stationary_top_fallback():
    obj, context, shop = session()
    current = (None, {}, [])
    obj.grid = Mock(return_value=current); obj.scroll = Mock(); obj.same_view = Mock(return_value=True)
    with patch.object(c, 'scrollbar', return_value=(690, 900)), pytest.raises(InterruptedError):
        obj.rewind()
    assert obj.scroll.call_count == 2


def test_unknown_thumb_retains_two_observation_fallback():
    obj, context, shop = session()
    current = (None, {}, [])
    obj.grid = Mock(return_value=current); obj.scroll = Mock(); obj.same_view = Mock(return_value=True)
    with patch.object(c, 'scrollbar', return_value=None):
        assert obj.rewind() is current
    assert obj.scroll.call_count == 2


def test_single_job_finishes_without_reopening_or_post_purchase_scan():
    obj, context, shop = session(); image = object(); t = obj.rules[0]
    calls = Mock()
    obj.scan = calls.scan; obj.customize_card = calls.purchase; obj.leave = calls.leave
    obj.stable = Mock(); obj.click = Mock()
    calls.scan.side_effect = [[dict(target=t, menu=menu())], (t, menu())]
    calls.purchase.return_value = 'bought'; calls.leave.return_value = image
    shop._visit_state.return_value = {}; shop._recognize_points.return_value = 1000
    gates = [dict(box=[1, 1, 10, 10], quota=n) for n in (2, 1)]
    with patch.object(c, 'entry', side_effect=gates):
        assert obj.run(image) == (image, None)
    assert [call[0] for call in calls.mock_calls] == ['scan', 'scan', 'purchase', 'leave']
    calls.scan.assert_any_call(t['key'])
    calls.purchase.assert_called_once()
    assert obj.finished
    obj.scan.reset_mock(); obj.click.reset_mock()
    assert obj.run(image) == (image, None)
    obj.scan.assert_not_called(); obj.click.assert_not_called()


def test_plan_does_not_skip_expensive_high_priority_target():
    obj, context, shop = session(); image = object()
    t = target('輝くキミへ', [1, 0]); lower = target('仕切り直し', [1, 0])
    obj.rules = [t, lower]
    page = menu(t['key'], remaining=(1,1), total=1, prices=(100,40))
    obj.scan = Mock(return_value=[dict(target=t, menu=page)]); obj.customize_card = Mock(return_value='budget')
    obj.stable = Mock(); obj.click = Mock(); obj.leave = Mock(return_value=image)
    shop._visit_state.return_value = {}; shop._recognize_points.return_value = 79
    with patch.object(c, 'entry', return_value=dict(box=[1,1,10,10], quota=2)):
        assert obj.run(image) == (image, None)
    obj.scan.assert_called_once_with()
    obj.customize_card.assert_not_called()


def test_stopping_does_not_report_a_recognition_failure_or_block_the_shop():
    obj, context, shop = session(); image = object()
    shop._visit_state.return_value = {}; shop._recognize_points.return_value = 1000
    obj.click = Mock(); obj.stable = Mock()
    def stop(*args):
        context.tasker.stopping = True
        raise InterruptedError('任务已停止')
    obj.scan = stop; obj.leave = Mock()
    with patch.object(c, 'entry', return_value=dict(box=[1,1,10,10], quota=2)), \
         patch('custom.produce.shop_customization.logger.warning') as warning:
        assert obj.run(image) == (None, None)
    warning.assert_not_called(); obj.leave.assert_not_called()
    shop._visit_state.assert_called_once_with(context)


def test_named_search_never_buys_another_rule_found_by_unknown_art():
    obj, context, shop = session()
    other = target('輝くキミへ', [1,0]); obj.by_name[other['key']] = other
    cell = dict(name_key=None, box=[82, 567, 116, 116])
    obj.rewind = Mock(return_value=(None, {}, [[cell]]))
    page = menu(other['key'], remaining=(1,1), total=1, prices=(100,40))
    obj.open_card = Mock(return_value=(dict(name=other['key']), page))
    obj.click = Mock(); obj.stable = Mock(); obj.scroll = Mock()
    with patch.object(c, 'scrollbar', return_value=(800,1037)):
        assert obj.scan(obj.rules[0]['key']) is None
    obj.click.assert_called_once_with(page['back'])
    obj.scroll.assert_not_called()


def test_confirmed_bottom_does_not_probe_by_swiping_again():
    obj, context, shop = session()
    cell = dict(name_key='other', box=[82, 567, 116, 116])
    obj.rewind = Mock(return_value=(None, {}, [[cell]]))
    obj.open_card = Mock(); obj.scroll = Mock()
    with patch.object(c, 'scrollbar', return_value=(800,1037)):
        assert obj.scan() == []
    obj.open_card.assert_not_called(); obj.scroll.assert_not_called()

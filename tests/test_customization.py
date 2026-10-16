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


def test_verified_debit_and_remaining_decrement_clear_pending():
    obj, context, shop = session(); image = np.zeros((1280, 720, 3), np.uint8)
    page = menu(); selected = deepcopy(page); selected["options"][0]["selected"] = True
    after = menu(remaining=(2, 1, 1), total=2)
    obj.stable = Mock(side_effect=[(image, page), (image, selected)])
    obj.capture = Mock(return_value=image); obj.click = Mock()
    shop._recognize_points.side_effect = [119, 119, 79, 79]
    with patch.object(c, "receipt", return_value=None), patch.object(c, "read_page", return_value=after), patch("custom.produce.shop_customization.time.sleep"):
        assert obj.purchase(target(), page) == "bought"
    assert shop._visit_state.call_args.kwargs == {"custom_pending": False, "custom_blocked": False}


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
    t = target(counts=[3, 0, 0]); obj.rules = [t]; obj.by_name = {t["key"]: t}
    first = menu(); second = menu(remaining=(2, 1, 1), total=2)
    obj.scan = Mock(side_effect=[[dict(target=t, menu=first)], (t, first), (t, second)])
    obj.purchase = Mock(return_value="bought"); obj.stable = Mock(); obj.click = Mock(); obj.leave = Mock(return_value=image)
    shop._visit_state.return_value = {}; shop._recognize_points.return_value = 1000
    gates = [dict(box=[1, 1, 10, 10], quota=n) for n in (2, 2, 1, 0)]
    with patch.object(c, "entry", side_effect=gates):
        assert obj.run(image) == (image, 70)
    assert obj.purchase.call_count == 2


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
    with patch.object(c, "entry", return_value=dict(box=[1, 1, 10, 10], quota=2)):
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

"""Replay shop screenshots using the native recognizers; never send input."""
import argparse
import dataclasses
import json
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image
from maa.library import Library


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("portable", type=Path)
    parser.add_argument("images", type=Path, nargs="+")
    parser.add_argument("--crop", type=int, nargs=4, help="Observed game-client rectangle inside a desktop screenshot")
    parser.add_argument("--delete-confirmation", action="store_true")
    parser.add_argument("--delete-cancel", action="store_true", help="Check deletion Cancel recognition without sending input")
    parser.add_argument("--expected-delete-name", default="夏夜に咲く思い出")
    parser.add_argument("--deck-candidates", action="store_true", help="Replay square deck-card recognition without input")
    parser.add_argument("--owned-drink", action="store_true", help="Replay owned drink title and discard dialog without input")
    parser.add_argument("--shop-scene", action="store_true", help="Replay HIF shop geometry and generic button recognition without input")
    parser.add_argument("--expect-shop", choices=("yes", "no"))
    parser.add_argument("--reward-drinks", action="store_true")
    args = parser.parse_args()
    root = args.portable.resolve()
    Library.open(root / "runtimes/win-x64/native")
    Library.version()
    from maa.resource import Resource
    from maa.tasker import Tasker
    from maa.controller import CustomController
    from maa.pipeline import JOCR, JTemplateMatch, JFeatureMatch, JNeuralNetworkDetect, JCustomRecognition

    resource = Resource()
    assert resource.post_bundle(root / "resource/base").wait().status.succeeded
    frame = None

    class ReadOnlyController(CustomController):
        def connect(self): return True
        def request_uuid(self): return "shop-saved-frame"
        def screencap(self): return frame.copy()
        def get_features(self): return 0
        def start_app(self, *args): return False
        def stop_app(self, *args): return False
        def click(self, *args): return False
        def swipe(self, *args): return False
        def touch_down(self, *args): return False
        def touch_move(self, *args): return False
        def touch_up(self, *args): return False
        def click_key(self, *args): return False
        def input_text(self, *args): return False
        def key_down(self, *args): return False
        def key_up(self, *args): return False

    controller = ReadOnlyController()
    assert controller.post_connection().wait().status.succeeded
    tasker = Tasker()
    assert tasker.bind(resource, controller)

    class Context:
        def run_recognition(self, name, image, pipeline_override=None):
            override = (pipeline_override or {}).get(name, {})
            node = resource.get_node_data(name)
            kind = override.get("recognition", node["recognition"]["type"])
            param = {**node["recognition"]["param"], **override}
            cls = {"OCR": JOCR, "TemplateMatch": JTemplateMatch, "FeatureMatch": JFeatureMatch,
                   "NeuralNetworkDetect": JNeuralNetworkDetect}[kind]
            fields = {f.name for f in dataclasses.fields(cls)}
            param = {k: v for k, v in param.items() if k in fields}
            for key in (("template", "threshold") if cls is JTemplateMatch else ("template",)):
                if key in param and not isinstance(param[key], list):
                    param[key] = [param[key]]
            return tasker.post_recognition(kind, cls(**param), image).wait().get().nodes[0].recognition

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
    with patch("maa.agent.agent_server.AgentServer.custom_action", lambda _: lambda cls: cls), \
         patch("maa.agent.agent_server.AgentServer.custom_recognition", lambda _: lambda cls: cls):
        from custom.action.produce import ProduceShoppingAuto, ProduceChooseRewardAuto, ProduceHIFIntervalShoppingAuto
        from custom.shop import is_interval_shop, read_refresh, selected_drink_matches, owned_detail_title, shop_occupied, ocr, read_discard_confirmation, consultation_grid_visible
        from custom.hif import read_hif_start, read_hif_scores
        from custom.removal import read_delete_confirmation, read_selected_card
        from custom.reco.hif import ProduceHIFConsultationShop
    context = Context()
    if args.shop_scene:
        assert resource.register_custom_recognition("ProduceHIFConsultationShop", ProduceHIFConsultationShop())
    for path in args.images:
        src = Image.open(path).convert("RGB")
        bounds = tuple(args.crop) if args.crop else (1, 31, src.width - 1, src.height - 1)
        frame = np.asarray(src.crop(bounds).resize((720, 1279)))[:, :, ::-1].copy()
        print("FRAME", path.name)
        if args.delete_cancel:
            flag = context.run_recognition("ProduceChooseDeleteFlag", frame, {"ProduceChooseDeleteFlag": {
                "template": ["produce/delete_icon.png"], "focus": {"Node.Recognition.Succeeded": ""}}})
            buttons = ocr(context, frame, [65, 1100, 290, 130], [r"^[×✕xX]?\s*(キャンセル|取消)$"])
            print("DELETE_FLAG", flag.hit, "CANCEL", [(r.text, r.score, list(r.box)) for r in buttons])
            assert flag.hit and len(buttons) == 1 and buttons[0].score >= .80
            assert not ProduceShoppingAuto._is_shop_scene(context, frame), "Deletion overlay must not count as a returned shop"
            continue
        if args.reward_drinks:
            route = context.run_recognition("ProduceChooseGetFlag", frame, {"ProduceChooseGetFlag": {
                "expected": ["受け取る(.+)を選んでください", "P.?ドリンクを受け取らないで次へ進みます"]}})
            print("REWARD_ROUTE", route.hit, str(route.best_result))
            assert route.hit
            assert ProduceChooseRewardAuto._is_drink_reward(context, frame)
            checkbox = context.run_recognition("ProduceChooseSkip", frame)
            skip_button = ocr(context, frame, [100, 1030, 520, 130], [r"^受け取らない$"])
            print("REJECT_CONTROL", checkbox.hit, str(checkbox.best_result), "SKIP_SUBMIT", [(r.text, list(r.box)) for r in skip_button])
            assert checkbox.hit and len(skip_button) == 1
            state = ProduceChooseRewardAuto._scan_drink_state(context, frame)
            print("DRINK_STATE", state)
            assert state["full"] and state["occupied"] == {0, 1, 2, 3}
            assert state["reward"][0]["id"] == "767772"
            assert state["reward"][1]["id"] == "429282"
            assert state["inventory"][2]["id"] == "429282"
            assert set(state["inventory"]) == {2}
            continue
        if args.shop_scene:
            visible = consultation_grid_visible(frame)
            print("HIF_SHOP_GRID", visible, "OLD_WHITE_GRID", ProduceShoppingAuto._looks_like_shop_grid(frame))
            native = tasker.post_recognition("Custom", JCustomRecognition("ProduceHIFConsultationShop"), frame).wait().get().nodes[0].recognition
            print("NATIVE_CUSTOM_SHOP", native.hit, str(native.best_result))
            assert native.hit == visible
            if args.expect_shop:
                assert visible == (args.expect_shop == "yes"), path
            # Compare against the native old template set on the exact same frame.
            old = context.run_recognition("ProduceButton", frame)
            base_templates = resource.get_node_data("ProduceButton")["recognition"]["param"]["template"]
            new = context.run_recognition("ProduceButton", frame, {"ProduceButton": {
                "template": [s for s in base_templates if s != "produce/shop_exit.png"]}})
            print("BUTTON_OLD", old.hit, str(old.best_result))
            print("BUTTON_HIF", new.hit, str(new.best_result))
            if visible:
                assert old.hit and not new.hit, "Stable shop must not be exited by the HIF generic button"
            continue
        if args.owned_drink:
            title = owned_detail_title(context, frame)
            print("OWNED_TITLE", title)
            print("DISCARD_CONFIRMATION", read_discard_confirmation(context, frame, "初星ホエイプロテイン"))
            print("DETAIL_CANCEL",[(r.text,list(r.box)) for r in ocr(context,frame,[60,1090,290,150],[r"^[×✕Xx]?\s*キャンセル$"])])
            expected = {"protein-detail-live.png":"初星ホエイプロテイン", "097.png":"初星黒酢",
                        "boost-detail-live.png":"ブーストエキス", "boost-discard-live.png":"ブーストエキス",
                        "codex-clipboard-0fada230-3f6c-4e33-b442-7279f59b7d54.png":"初星ホエイプロテイン"}
            if path.name in expected:
                assert title == expected[path.name], (path,title)
            if path.name == "codex-clipboard-0fada230-3f6c-4e33-b442-7279f59b7d54.png":
                assert read_discard_confirmation(context, frame, "初星ホエイプロテイン")
                assert read_discard_confirmation(context, frame, "ブーストエキス") is None
            if path.name == "boost-discard-live.png":
                confirmed = read_discard_confirmation(context, frame, "ブーストエキス")
                print("BOOST_DISCARD",confirmed)
                assert confirmed
                assert read_discard_confirmation(context, frame, "初星ホエイプロテイン") is None
            for roi in ([15,500,690,550], [100,500,600,600], [15,850,680,350]):
                data = context.run_recognition("ProduceRecognitionPoint", frame,
                    {"ProduceRecognitionPoint": {"recognition":"OCR", "roi":roi, "expected":[]}})
                print("OWNED_OCR",roi,[(r.text,r.score,list(r.box)) for r in data.all_results])
            continue
        if args.deck_candidates:
            found = ProduceShoppingAuto()._recognize_delete_cards(context, frame)
            print("DECK_CANDIDATES", [{key:item[key] for key in ("name","rarity","box","name_confidence")} for item in found])
            assert found, "No saved deck candidates recognized"
            continue
        if args.delete_confirmation:
            for template in ("produce/delete.png", "produce/delete_confirm.png"):
                hit = context.run_recognition("ProduceChooseDeleteClick", frame,
                    {"ProduceChooseDeleteClick": {"recognition": "TemplateMatch", "roi": [210,1080,460,160],
                     "template": [template], "threshold": .20}})
                print("DELETE_TEMPLATE", template, hit.hit, [str(r) for r in hit.filtered_results])
            invoice = read_delete_confirmation(context, frame)
            print("DELETE_CONFIRMATION", invoice)
            print("DELETE_SELECTED", read_selected_card(context, frame))
            if not invoice:
                for roi in ([15,480,690,260], [30,585,660,115], [375,1080,290,160], [65,1080,300,160],
                            [175,650,500,365], [265,995,415,115]):
                    data = context.run_recognition("ProduceRecognitionPoint", frame,
                        {"ProduceRecognitionPoint": {"recognition":"OCR", "roi":roi, "expected":[]}})
                    print("DELETE_OCR", roi, [(r.text,r.score,list(r.box)) for r in data.all_results])
            assert invoice and invoice["selected"]["name"] == args.expected_delete_name
            assert (invoice["before"], invoice["after"]) == (420, 358)
            continue
        if path.name in ("01-start-day6.png", "02-class-options.png"):
            scores = read_hif_scores(context, frame)
            print("HIF_SCORES", scores)
            if scores is None:
                for y, height in ((668, 80), (220, 250)):
                    for i in range(3):
                        data = context.run_recognition("ProduceRecognitionScore", frame,
                            {"ProduceRecognitionScore": {"recognition":"OCR", "roi":[150+i*150,y,136,height], "expected":[]}})
                        print("SCORE_OCR", y, i, [(row.text, row.score, list(row.box)) for row in data.all_results])
            assert scores and scores["max"] == 3200, "HIF current/cap pairs incomplete"
            continue
        if path.name == "produce-start-preparation-repro.png":
            start = read_hif_start(context, frame)
            print("HIF_START", start)
            assert start, "HIF start confirmation not recognized"
            continue
        print("INTERVAL", is_interval_shop(context, frame), "REFRESH", read_refresh(context, frame))
        print("OWNED_TITLE", owned_detail_title(context, frame))
        print("SELECTED", [key for key in ProduceChooseRewardAuto.TARGET_DRINKS if selected_drink_matches(context, frame, key)])
        print("CONFIRM", [key for key in ProduceChooseRewardAuto.TARGET_DRINKS if selected_drink_matches(context, frame, key, confirmation=True)])
        for index, (x, y) in enumerate(ProduceChooseRewardAuto.INVENTORY_SLOT_CENTERS):
            p = frame[y-22:y+23, x-24:x+25].astype(np.int16)
            print("CYAN", index, ((p[:,:,0]>180)&(p[:,:,1]>160)&(p[:,:,2]<190)).sum())
        _, inventory = ProduceChooseRewardAuto._scan_target_drinks(context, frame)
        print("INVENTORY", inventory)
        print("OCCUPIED", ProduceChooseRewardAuto._scan_inventory_occupancy(context, frame, inventory))
        if "shop" in path.name:
            shop = ProduceHIFIntervalShoppingAuto() if is_interval_shop(context, frame) else ProduceShoppingAuto()
            found = shop._recognize_drinks(context, frame, [])
            print("PRODUCT_TARGETS", found, "SHOP_OCCUPIED", shop_occupied(context, frame))
            expected = {"ordinary-shop-grid-stable.png": {"767772"}, "ordinary-shop-sold.png": set(),
                        "hif-interval-shop.png": {"235611"}, "hif-interval-shop-sold.png": set(),
                        "hif-interval-shop-no-target.png": set(),
                        "hif-interval-shop-two-targets.png": {"429282", "679991"}}
            if path.name in expected:
                assert {item["id"] for item in found} == expected[path.name], (path, found)
                assert all(item["price"] and item["price"] > 0 for item in found), (path, found)
                if path.name == "hif-interval-shop-two-targets.png":
                    assert min(found, key=lambda item: item["price"])["id"] == "679991"
        if path.name == "097.png":
            assert owned_detail_title(context, frame) == "初星黒酢"
        if path.name == "hif-interval-shop-no-target.png":
            assert read_refresh(context, frame)["cost"] == 10
        if path.name in ("092.png", "102.png"):
            assert ocr(context, frame, [15, 520, 680, 470], ["交換確認"])
            assert selected_drink_matches(context, frame, "235611" if path.name == "092.png" else "679991", confirmation=True)
        if path.name == "098.png":
            assert ocr(context, frame, [15, 780, 680, 280], ["廃棄確認"])
            assert any("初星黒酢" in r.text for r in ocr(context, frame, [30, 970, 650, 130], [".*"]))
            assert ocr(context, frame, [380, 1090, 260, 150], ["^はい$"])
        if path.name == "hif-round2-end-or-retry.png":
            assert context.run_recognition("ProduceFailedFlag", frame).hit
            assert context.run_recognition("ProduceFailedStop", frame).hit
        for name in ("ProduceShoppingBuy", "ProduceShoppingDecide", "ProduceShoppingExit", "ProduceShoppingDrinkFull", "ProduceFailedFlag", "ProduceFailedStop"):
            hit = context.run_recognition(name, frame)
            print("MARKER", name, hit.hit, [str(r) for r in hit.filtered_results])
        for roi in ([0, 0, 220, 180], [0, 1010, 720, 190], [0, 1010, 220, 80], [180, 160, 510, 100],
                    [15, 780, 680, 280], [30, 970, 650, 130], [380, 1090, 260, 150]):
            detail = context.run_recognition("ProduceRecognitionPoint", frame, {"ProduceRecognitionPoint": {"recognition": "OCR", "roi": roi, "expected": [".*"]}})
            print("OCR", roi, [str(r) for r in detail.all_results])
        boxes = [[374, 690, 126, 126], [528, 690, 126, 126]] if "interval" in path.name else ProduceShoppingAuto.DRINK_SLOTS
        for box in boxes:
            print("SLOT", box, "price", ProduceShoppingAuto._recognize_price(context, frame, box), "sold", ProduceShoppingAuto._slot_is_sold(frame, box))
            for drink_id, template in ProduceChooseRewardAuto.TARGET_DRINK_TEMPLATES.items():
                detail = context.run_recognition("ProduceRecognitionShopDrinks", frame, {"ProduceRecognitionShopDrinks": {"recognition": "FeatureMatch", "roi": box, "template": [template], "count": 4, "ratio": .62}})
                if detail.hit:
                    print("TARGET", drink_id, [str(r) for r in detail.filtered_results])


if __name__ == "__main__":
    main()

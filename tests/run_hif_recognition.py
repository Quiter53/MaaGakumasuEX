"""Use the real framework on saved HIF frames; never send game input."""

import argparse
import dataclasses
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PIL import Image
from maa.library import Library


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("portable", type=Path)
    parser.add_argument("image", type=Path)
    parser.add_argument("--window", action="store_true")
    parser.add_argument("--crop", type=int, nargs=4, help="Observed game client rectangle in a desktop screenshot")
    parser.add_argument("--source-page", action="store_true", help="Replay source-page stability only; no game input")
    parser.add_argument("--detail-title-probe", action="store_true", help="Compare read-only source title ROIs")
    parser.add_argument("--height", type=int, default=1280, help="Replay the native frame height, including integer rounding")
    args = parser.parse_args()
    root = args.portable.resolve()
    Library.open(root / "runtimes/win-x64/native")
    Library.version()  # Keep subsequent Agent module imports from changing DLL mode.
    from maa.controller import CustomController
    from maa.resource import Resource
    from maa.tasker import Tasker
    from maa.pipeline import JOCR, JTemplateMatch, JNeuralNetworkDetect

    resource = Resource()
    assert resource.post_bundle(root / "resource/base").wait().status.succeeded
    source = Image.open(args.image).convert("RGB")
    if args.crop:
        source = source.crop(tuple(args.crop))
    if args.window:
        source = source.crop((1, 31, source.width - 1, source.height - 1))
    image = np.asarray(source.resize((720, args.height)))[:, :, ::-1].copy()
    print("FRAME_SHAPE", image.shape)

    class ReadOnlyController(CustomController):
        def connect(self): return True
        def request_uuid(self): return "hif-saved-frame"
        def screencap(self): return image.copy()
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

    class ReadOnlyContext:
        def __init__(self):
            self.tasker = SimpleNamespace(controller=controller, stopping=False)

        def run_recognition(self, name, image, pipeline_override=None):
            override = (pipeline_override or {}).get(name, {})
            node = resource.get_node_data(name)
            kind = override.get("recognition", node["recognition"]["type"])
            param = {**node["recognition"]["param"], **override}
            cls = {"OCR": JOCR, "TemplateMatch": JTemplateMatch, "NeuralNetworkDetect": JNeuralNetworkDetect}[kind]
            fields = {field.name for field in dataclasses.fields(cls)}
            param = {key: value for key, value in param.items() if key in fields}
            if cls is JTemplateMatch:
                for key in ("template", "threshold"):
                    if not isinstance(param[key], list):
                        param[key] = [param[key]]
            detail = tasker.post_recognition(kind, cls(**param), image).wait().get()
            result = detail.nodes[0].recognition
            print(json.dumps({"name": name, "hit": result.hit,
                              "results": [str(r) for r in result.all_results]}, ensure_ascii=False))
            return result

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
    # This standalone check calls the real native recognizers directly; it does
    # not start an Agent server or register game-control actions.
    with patch("maa.agent.agent_server.AgentServer.custom_action", lambda _: lambda cls: cls), \
         patch("maa.agent.agent_server.AgentServer.custom_recognition", lambda _: lambda cls: cls):
        from custom.hif import HIF_COURSE_LABEL, read_hif_choices, read_hif_change, is_hif_change_source, read_hif_events, read_hif_shop_entry
        from custom.action.produce import ProduceHIFAttributeBase, ProduceChooseRewardAuto, ProduceHIFChangeSourceAuto, ProduceShoppingAuto

    if args.detail_title_probe:
        from custom.shop import ocr
        for roi in ([170, 265, 490, 350], [175, 250, 490, 65], [175, 250, 420, 65], [180, 255, 420, 55]):
            print('TITLE_ROI', roi, [(r.text, r.score) for r in ocr(ReadOnlyContext(), image, roi, ['.*'])])
        return

    if args.source_page:
        context = ReadOnlyContext()
        assert is_hif_change_source(context, image), "Saved frame is not a verified source page"
        # Recovery is isolated from this offline frame; all input methods on
        # the controller refuse input, even if another helper attempted it.
        with patch("custom.action.produce._detect_disconnect_screen", return_value=False):
            page = ProduceHIFChangeSourceAuto()._page(context)
        assert page is not None, "Real native detections did not stabilize"
        saved, cards = page
        signature = ProduceChooseRewardAuto._scene_signature(saved, cards)
        assert ProduceHIFChangeSourceAuto._source_frames_stable(signature, tuple(reversed(signature)))
        assert len(cards) > 0 and any(card["rarity"] == "N" for card in cards)
        jittered = [{"box": list(card["box"])} for card in cards]
        # This screenshot reproduces the live first-row N/SSR ordering change
        # when the N tile moves up just one pixel (637 -> 636).
        normal = next(card for card in jittered if card["box"][0] == 228 and card["box"][1] < 650)
        normal["box"][1] -= 1
        jittered.sort(key=lambda card: (card["box"][1], card["box"][0]))
        changed = ProduceChooseRewardAuto._scene_signature(saved, jittered)
        assert not ProduceChooseRewardAuto._reward_frames_stable(signature, changed)
        assert ProduceHIFChangeSourceAuto._source_frames_stable(signature, changed)
        print("SOURCE_PAGE_STABLE", json.dumps({"cards": len(cards), "reordered_stable": True,
              "one_pixel_row_jitter": "old check failed; source check passed",
              "normal_candidates": [card["name"] for card in cards if card["rarity"] == "N"],
              "input": "read-only; identity verification and submit not exercised"}, ensure_ascii=False))
        return

    print("HIF_RESULT", read_hif_choices(ReadOnlyContext(), image))
    print("HIF_EVENT_LABELS", ProduceHIFAttributeBase._ocr(
        ReadOnlyContext(), image, ["^授業$", HIF_COURSE_LABEL], [0, 780, 720, 380]))
    print("HIF_CLASS_EVENTS", read_hif_events(ReadOnlyContext(), image, "授業"))
    print("HIF_COURSE_EVENTS", read_hif_events(ReadOnlyContext(), image, "レッスン"))
    print("HIF_SHOP_ENTRY", read_hif_shop_entry(ReadOnlyContext(), image))
    boxes = read_hif_change(ReadOnlyContext(), image)
    print("HIF_CHANGE_RESULT", boxes)
    if boxes:
        print("HIF_NEXT", ProduceHIFAttributeBase._ocr(ReadOnlyContext(), image, ["次へ"], [220, 1040, 280, 120]))
        print("HIF_CHANGE_CARDS", [{key: item[key] for key in ("name", "rarity", "extra_action", "priority")}
                                   for item in ProduceChooseRewardAuto()._evaluate(ReadOnlyContext(), image, verified_boxes=boxes)])
    print("HIF_SOURCE_PROMPT", ProduceHIFAttributeBase._ocr(ReadOnlyContext(), image, ["チェンジ.*スキルカード.*選"], [20, 550, 680, 180]))
    detected = ReadOnlyContext().run_recognition("ProduceRecognitionCards", image)
    print("DETECTED_CARDS", [str(r) for r in detected.all_results])
    print("HIF_SOURCE_RESULT", is_hif_change_source(ReadOnlyContext(), image))
    if is_hif_change_source(ReadOnlyContext(), image):
        from custom.deck_scan import complete_grid_rows
        cards = ProduceChooseRewardAuto()._evaluate(ReadOnlyContext(), image,
            verified_boxes=complete_grid_rows(image, ProduceHIFChangeSourceAuto._boxes(detected.all_results), top=620), read_extra_action=False)
        from custom.deck_scan import scan_identity_known
        print("SOURCE_SQUARE_SCAN_COUNTS", {"cards": len(cards), "high_confidence_no_probe": sum(scan_identity_known(item) for item in cards)})
        from custom.removal import read_selected_card, selected_card_box_matches
        print("SOURCE_DETAIL_IDENTITY", read_selected_card(ReadOnlyContext(), image, change=True))
        print("SOURCE_SELECTED_BOXES", [item['box'] for item in cards if selected_card_box_matches(image, item['box'])])
        print("SOURCE_SCAN_COUNTS", {"cards": len(cards), "high_confidence_no_probe": sum(scan_identity_known(item) for item in cards)})
        print("SOURCE_CARDS", [{**{key:item[key] for key in ("name","name_key","rarity","box", "name_confidence", "name_score", "name_margin", "frame_rarity", "rarity_confidence")},
                                "scan_known": scan_identity_known(item)} for item in cards])
        lists = json.loads((root / "config/card_lists.json").read_text(encoding="utf-8"))
        from custom.card_visual import normalize_card_name
        black = {normalize_card_name(n) for n in lists["blacklist"]["cards"]} if lists["blacklist"]["enabled"] else set()
        white = {normalize_card_name(n) for n in lists["whitelist"]["cards"]} if lists["whitelist"]["enabled"] else set()
        print("SOURCE_SELECTED", ProduceShoppingAuto._choose_delete_candidate(cards, black, white))


if __name__ == "__main__":
    main()

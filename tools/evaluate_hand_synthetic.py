"""合成出牌区离线评估：真实出牌截图 + MaaGakumasuEX-Lab 的 game-assets 卡图。

只读、不连接游戏。真实牌位截图提供卡框、分数、图标与角标，只把与该牌自身原画一致的卡面区域
替换为另一张卡的原画（与游戏相同的整卡缩放），叠放成 1–5 张手牌（实测 123/164/216 px 步长，后者遮挡前者）。

- identity：在给定牌框下评估 HandIdentityReader 的卡名（接受/拒识/错认）
- detector：用 MaaFramework 原生 NeuralNetworkDetect（默认 cards.onnx，CPU）评估牌框召回与出牌候选

注意：合成卡面与参考图库同源，结果偏乐观；它衡量的是遮挡/扰动下的稳健性，不等同于实机准确率。
样本量决定可声称的上限：0 错误 / N 个样本时，95% 置信的错误率上限约为 3/N。

用法：
  python tools/evaluate_hand_synthetic.py identity --lab ../MaaGakumasuEX-Lab --per-k 200 --hard --workers 8 --output out.json
  python tools/evaluate_hand_synthetic.py detector --lab ../MaaGakumasuEX-Lab --per-k 100 --hard --output out.json
"""

import sys
import json
import types
import random
import argparse
from pathlib import Path
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor

import numpy as np
from PIL import Image, ImageFilter

REPO = Path(__file__).resolve().parents[1]
LAB = None
sys.path.insert(0, str(REPO / "agent"))
_pkg = types.ModuleType("custom")
_pkg.__path__ = [str(REPO / "agent/custom")]
sys.modules.setdefault("custom", _pkg)
from custom.card_visual import normalize_card_name  # noqa: E402

LAYOUTS = {1: [265], 2: [157, 373], 3: [48, 264, 481], 4: [19, 184, 348, 512], 5: [19, 142, 266, 389, 512]}
Y, W, H = 884, 188, 248


def load_rgb(p):
    return np.asarray(Image.open(p).convert("RGB"))


def catalog():
    by = {}
    for it in json.loads((LAB / "game-assets/catalog.json").read_text(encoding="utf-8")):
        if it.get("category") == "cards" and it.get("cardNames"):
            key = normalize_card_name(it["cardNames"][0]).rstrip("+")
            by.setdefault(key, []).append(LAB / "game-assets" / it["path"])
    return by


def art_size(w, h):
    return w, round(h * 0.865)


def build_frames(cat, det):
    labels = json.loads((REPO / "tests/fixtures/hand_identity_labels.json").read_text(encoding="utf-8"))["frames"]
    frames = []
    for f in labels:
        if len(f["xs"]) > 3:
            continue
        img = load_rgb(LAB / "fixtures/hand" / f["file"])
        boxes = sorted([r["box"] for r in det[f["file"]] if 780 <= r["box"][1] <= 1000], key=lambda b: b[0])
        for box, name in zip(boxes, f["names"]):
            x, y, w, h = box
            crop = img[y : y + h, x : x + w].astype(np.float32)
            aw, ah = art_size(w, h)
            best = None
            for path in cat.get(normalize_card_name(name).rstrip("+"), []):
                raw = np.asarray(Image.open(path).convert("RGB").resize((aw, ah), Image.Resampling.BILINEAR), np.float32)
                diff = np.abs(crop[:ah] - raw).mean(axis=2)
                score = np.median(diff)
                if best is None or score < best[0]:
                    alpha = np.asarray(Image.open(path).convert("RGBA").resize((aw, ah), Image.Resampling.BILINEAR), np.float32)[..., 3] / 255
                    best = (score, diff, alpha)
            if best is None:
                continue
            mask = np.zeros((h, w), np.float32)
            m = (best[1] < 28).astype(np.uint8) * 255
            m = np.asarray(Image.fromarray(m).filter(ImageFilter.MedianFilter(5)), np.float32) / 255
            mask[:ah] = m
            transparent = best[2] < 0.1
            bg = crop[:ah][transparent].mean(axis=0) if transparent.sum() > 200 else np.array([235, 240, 245], np.float32)
            frames.append(dict(crop=crop, mask=mask, bg=bg, name=name, file=f["file"], fit=float(best[0]), cover=float(m.mean())))
    # Consensus art window (the hexagonal art area is the same frame geometry on every card).
    ref = min(frames, key=lambda f: f["fit"])  # best-aligned real card defines the art window
    win_img = Image.fromarray((ref["mask"] * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(9)).filter(ImageFilter.MinFilter(9))
    window = np.asarray(win_img.resize((64, 64), Image.Resampling.BILINEAR), np.float32) / 255 > 0.5
    keep = np.zeros((32, 32), bool)  # badge/icon zones keep the real crop (same zones hand_identity ignores)
    keep[:9, :17] = True
    keep[12:, :7] = True
    keep[23:, 23:] = True
    for f in frames:
        h, w = f["crop"].shape[:2]
        aw, ah = art_size(w, h)
        win = np.asarray(Image.fromarray(window.astype(np.uint8) * 255).resize((w, h), Image.Resampling.NEAREST)) > 0
        kp = np.asarray(Image.fromarray(keep.astype(np.uint8) * 255).resize((aw, ah), Image.Resampling.NEAREST)) > 0
        m = win.copy()
        m[ah:] = False
        m[:ah][kp] = False
        f["mask"] = m.astype(np.float32)
    return frames


def band_background(rng):
    files = sorted((LAB / "fixtures/hand").glob("*.png"))
    img = load_rgb(files[rng.randrange(len(files))]).copy()
    src = img[560 : 560 + 300].copy()  # stage above the hand, same scene lighting
    img[860:1160] = src
    return img


def compose(frames, cat, names, rng, k, jitter=2, bright=0.08, hard=False):
    img = band_background(rng).astype(np.float32)
    boxes, truth = [], []
    for i, x0 in enumerate(LAYOUTS[k]):
        fr = frames[rng.randrange(len(frames))]
        name = names[rng.randrange(len(names))]
        path = cat[name][rng.randrange(len(cat[name]))]
        h, w = fr["crop"].shape[:2]
        aw, ah = art_size(w, h)
        src = Image.open(path).convert("RGBA")
        if hard:  # game-side rescale/offset/blur mismatch
            sc = rng.uniform(0.97, 1.03)
            dx, dy = rng.randint(-2, 2), rng.randint(-2, 2)
            big = src.resize((round(aw * sc), round(ah * sc)), Image.Resampling.BICUBIC)
            canvas = Image.new("RGBA", (aw, ah), (255, 255, 255, 0))
            canvas.paste(big, ((aw - big.width) // 2 + dx, (ah - big.height) // 2 + dy))
            src = canvas.filter(ImageFilter.GaussianBlur(rng.uniform(0, 0.7)))
        rgba = np.asarray(src.resize((aw, ah), Image.Resampling.BILINEAR), np.float32)
        a = rgba[..., 3:] / 255
        raw = rgba[..., :3] * a + fr["bg"] * (1 - a)
        card = fr["crop"].copy()
        m = fr["mask"][:ah, :, None]
        card[:ah] = card[:ah] * (1 - m) + raw * m
        if hard:
            card = 255 * (np.clip(card, 0, 255) / 255) ** rng.uniform(0.9, 1.1)
        card *= 1 + rng.uniform(-bright, bright)
        x = int(np.clip(x0 + rng.randint(-jitter, jitter), 0, 720 - w))
        y = Y + rng.randint(-1, 1)
        img[y : y + h, x : x + w] = card
        boxes.append([x, y, w, h])
        truth.append(name)
    img += np.random.default_rng(rng.randrange(1 << 30)).normal(0, 3.0 if hard else 2.0, img.shape)
    return np.clip(img, 0, 255).astype(np.uint8), boxes, truth


def real_hand_boxes(lab):
    """Real fixture card boxes: the labelled x positions with the measured 188x248 card size at y=884."""
    labels = json.loads((REPO / "tests/fixtures/hand_identity_labels.json").read_text(encoding="utf-8"))["frames"]
    return {f["file"]: [{"box": [x, 884, 188, 248]} for x in f["xs"]] for f in labels}


def _identity_worker(job):
    global LAB
    lab, per_k, seed, hard = job
    LAB = Path(lab)
    from custom.hand_identity import HandIdentityReader

    cat = catalog()
    frames = build_frames(cat, real_hand_boxes(LAB))
    names, rng, reader = sorted(cat), random.Random(seed), HandIdentityReader()
    stats, wrong = defaultdict(Counter), []
    for k in range(1, 6):
        for _ in range(per_k):
            img, boxes, truth = compose(frames, cat, names, rng, k, hard=hard)
            bgr = img[:, :, ::-1].copy()
            for i, (box, name) in enumerate(zip(boxes, truth)):
                result = reader.read(bgr, box, boxes)
                group = stats[f"{k}-occluded" if k >= 4 and i < k - 1 else str(k)]
                group["total"] += 1
                same = lambda value: value is not None and normalize_card_name(value).rstrip("+") == name
                if result is None or result.name is None:
                    group["unknown"] += 1
                elif same(result.name):
                    group["correct"] += 1
                else:
                    group["wrong"] += 1
                    wrong.append(dict(k=k, slot=i, truth=name, got=result.name, mode=result.mode, score=result.score, margin=result.margin))
                group["top1"] += int(result is not None and same(result.candidate))
    return {key: dict(value) for key, value in stats.items()}, wrong


def run_identity(args):
    jobs = [(str(args.lab), max(1, args.per_k // args.workers), args.seed + i * 7919, args.hard) for i in range(args.workers)]
    total, wrong = defaultdict(Counter), []
    with ProcessPoolExecutor(args.workers) as pool:
        for stats, bad in pool.map(_identity_worker, jobs):
            for key, value in stats.items():
                total[key].update(value)
            wrong += bad
    return {"stats": {k: dict(v) for k, v in sorted(total.items())}, "wrong": wrong}


def run_detector(args):
    global LAB
    LAB = Path(args.lab)
    from maa.tasker import Tasker
    from maa.toolkit import Toolkit
    from maa.pipeline import JNeuralNetworkDetect
    from maa.resource import Resource
    from maa.controller import CustomController
    from custom.hand_identity import hand_boxes

    Toolkit.init_option(str(Path(args.output).resolve().parent))

    class ReadOnly(CustomController):
        def connect(self):
            return True

        def request_uuid(self):
            return "offline-synthetic-hand"

        def screencap(self):
            return np.zeros((1280, 720, 3), np.uint8)

        def get_features(self):
            return 0

        def click(self, *a):
            raise RuntimeError("Input disabled")

        def swipe(self, *a):
            raise RuntimeError("Input disabled")

    resource = Resource()
    resource.use_cpu()
    assert resource.post_bundle(str(args.bundle)).wait().succeeded, "resource load failed"
    controller = ReadOnly()
    controller.post_connection().wait()
    tasker = Tasker()
    tasker.bind(resource, controller)
    params = JNeuralNetworkDetect(**resource.get_node_data("ProduceRecognitionCards")["recognition"]["param"])
    cat = catalog()
    frames = build_frames(cat, real_hand_boxes(LAB))
    names, rng = sorted(cat), random.Random(args.seed)
    stats, fails = defaultdict(Counter), []
    for k in range(1, 6):
        for _ in range(args.per_k):
            img, boxes, _ = compose(frames, cat, names, rng, k, hard=args.hard)
            detail = tasker.post_recognition("NeuralNetworkDetect", params, img[:, :, ::-1].copy()).wait().get()
            rows = detail.nodes[0].recognition.all_results
            found = hand_boxes(rows)
            matched = sum(any(abs(b[0] - g[0]) <= 15 and abs(b[1] - g[1]) <= 15 for b in found) for g in boxes)
            group = stats[str(k)]
            group["hands"] += 1
            group["cards"] += k
            group["found"] += matched
            group["extra"] += max(0, len(found) - matched)
            group["exact"] += int(matched == k and len(found) == k)
            if not (matched == k and len(found) == k) and len(fails) < 50:
                fails.append(dict(k=k, truth=boxes, detected=[(r.label, list(r.box), r.score) for r in rows]))
    return {"stats": {k: dict(v) for k, v in stats.items()}, "fails": fails}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", choices=("identity", "detector"))
    parser.add_argument("--lab", type=Path, required=True, help="MaaGakumasuEX-Lab checkout (fixtures/hand, game-assets)")
    parser.add_argument("--bundle", type=Path, default=REPO / "assets/resource/base")
    parser.add_argument("--per-k", type=int, default=100)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--hard", action="store_true", help="add art rescale/offset/blur/gamma/noise perturbations")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run_identity(args) if args.mode == "identity" else run_detector(args)
    report["scope"] = "synthetic composites of real battle frames and game-asset art; optimistic, not live-game accuracy"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report["stats"], ensure_ascii=False))


if __name__ == "__main__":
    main()

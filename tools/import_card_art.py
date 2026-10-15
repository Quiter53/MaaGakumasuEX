"""Enroll verified local card artwork in every shared picker/identity gallery.

Input is a snapshot of the public pcard API and a directory of extracted PNGs
named by assetId. Existing rows and artwork variants are retained byte-for-byte
in the arrays. This updates reference data, not the generic card-box ONNX.
"""
import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter


def enroll(repo: Path, metadata: Path, artwork: Path):
    read = lambda p: json.loads(p.read_text(encoding="utf8"))
    library_path = repo / "assets/config/card_library.json"
    catalog_path = repo / "agent/custom/card_catalog.json"
    library, catalog = read(library_path), read(catalog_path)
    existing = {c["cardId"] for c in library["cards"]}
    rows = [c for c in read(metadata) if c["id"] not in existing
            and c.get("upgradeCount") == 0 and not c.get("libraryHidden")
            and (artwork / (c["assetId"] + ".png")).exists()]
    if not rows:
        raise ValueError("No new verified artwork to import")
    runtime = repo / "agent/custom"
    with np.load(runtime / "card_fingerprints.npz", allow_pickle=False) as data:
        fingerprints = {k: data[k].copy() for k in data.files}
    small, names, rarities, extras = [], [], [], []
    for c in rows:
        name = c["name"]
        key = "".join(name.strip().replace("＋", "+").rstrip("+").split())
        rarity = {1: "N", 2: "R", 3: "SR", 4: "SSR", 100: "LR"}[c["rarity"]]
        extra = any("CardPlayCount" in str(d.get("targetId", "")) or
                    "スキルカード使用数追加" in str(d.get("text", ""))
                    for d in c.get("produceDescriptions", []))
        with Image.open(artwork / (c["assetId"] + ".png")) as original:
            image = original.convert("RGB")
        filename = c["id"] + ".webp"
        image.resize((96, 96), Image.Resampling.LANCZOS).save(
            repo / "assets/card_library" / filename, "WEBP", quality=82, method=3)
        small.append(np.asarray(image.resize((32, 32), Image.Resampling.LANCZOS)
                                .filter(ImageFilter.GaussianBlur(.25))))
        names.append(name); rarities.append(rarity); extras.append(extra)
        library["cards"].append(dict(key=key, name=name, rarity=rarity, extra=extra,
            cardId=c["id"], assetId=c["assetId"], image="card_library/"+filename,
            planType=c["planType"], category=c["category"],
            origin="idol" if c.get("originIdolCardId") else "support" if c.get("originSupportCardId") else "none",
            isConversion=c["isConversion"], unlockProducerLevel=c["unlockProducerLevel"]))
        catalog["entries"][key] = dict(name=name, rarity=rarity, extra=extra)
    for key, added in dict(templates=small, names=names, rarities=rarities, extra=extras).items():
        fingerprints[key] = np.concatenate((fingerprints[key], np.asarray(added)))
    np.savez_compressed(runtime / "card_fingerprints.npz", **fingerprints)
    for path, document in [(library_path, library), (catalog_path, catalog)]:
        path.write_text(json.dumps(document, ensure_ascii=False, separators=(",", ":")), encoding="utf8")
    return names


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("metadata", type=Path)
    p.add_argument("artwork", type=Path)
    a = p.parse_args()
    print(enroll(Path(__file__).resolve().parents[1], a.metadata, a.artwork))

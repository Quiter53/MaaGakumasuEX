"""Build shared UI/runtime customization limits from the public pcard export."""
import json
import argparse
from pathlib import Path


def build(rows, library):
    cards = {}
    by_id = {c["cardId"]: c["name"] for c in library["cards"]}
    for card in rows:
        if card["id"] not in by_id or card.get("upgradeCount") != 1 or not card.get("maxCustomizeCount"):
            continue
        options = []
        for effects in card.get("customizeEffects", []):
            effects = sorted(effects, key=lambda e: e["customizeCount"])
            if [e["customizeCount"] for e in effects] != list(range(1, len(effects) + 1)):
                raise ValueError(f"Non-contiguous customization levels: {card['id']}")
            label = effects[0].get("description") or " / ".join(
                e.get("growEffectDescription", {}).get("produceCardCustomizeDescription", "")
                for e in effects[0].get("growEffects", []))
            options.append({"label": label, "costs": [e["producePoint"] for e in effects]})
        if not 1 <= len(options) <= 3 or any(not o["costs"] or min(o["costs"]) <= 0 for o in options):
            raise ValueError(f"Invalid customization menu: {card['id']}")
        cards[by_id[card["id"]]] = {"max": card["maxCustomizeCount"], "options": options}
    return {"version": 1, "source": "https://gkms.idolism.org/pcard", "cards": cards}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("metadata", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    document = build(json.loads(args.metadata.read_text("utf8")), json.loads((root / "assets/config/card_library.json").read_text("utf8")))
    (root / "assets/data/card_customizations.json").write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", "utf8")
    print(f"Customization metadata: {len(document['cards'])} cards")

"""Build deterministic reward-routing fixtures from bundled images.

This helper only prepares .npy arrays for the MaaFramework integration check;
it does not modify production resources.
"""

import argparse
from pathlib import Path

import numpy as np
from PIL import Image


def rgb_array(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGB"), dtype=np.uint8)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("portable", type=Path)
    parser.add_argument("card_reward_png", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    # Maa's pipeline coordinates use the 720 x 1280 reference canvas.
    card = Image.open(args.card_reward_png).convert("RGB")
    card = card.resize((720, 1280), Image.Resampling.LANCZOS)
    np.save(args.output / "card_reward_720x1280.npy", np.asarray(card, dtype=np.uint8))

    # Construct a drink reward page with real bundled drink artwork at the
    # three reward-slot locations. The rest stays deliberately featureless.
    canvas = np.full((1280, 720, 3), 232, dtype=np.uint8)
    for drink_id, x in zip((174909, 196735, 198230), (145, 305, 465)):
        icon = rgb_array(
            args.portable / "resource" / "base" / "image" / "produce" / "drinks" / f"{drink_id}.png"
        )
        h, w = icon.shape[:2]
        y = 820
        canvas[y : y + h, x : x + w] = icon
    np.save(args.output / "drink_reward_720x1280.npy", canvas)


if __name__ == "__main__":
    main()

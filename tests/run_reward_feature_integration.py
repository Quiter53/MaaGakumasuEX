"""Run MaaFramework feature matching against deterministic reward fixtures."""

import argparse
from pathlib import Path

import numpy as np

from maa.library import Library
from maa.pipeline import JFeatureMatch, JRecognitionType
from maa.resource import Resource
from maa.tasker import Tasker


DRINK_IDS = (
    174909, 196735, 198230, 235611, 242782, 264496, 299854, 337,
    340077, 368325, 429282, 462413, 490974, 678359, 679991, 732062,
    739375, 762145, 767772, 787073, 823789, 825262, 835592, 835785,
    870793, 911307, 993206, 993957,
)


def recognize(tasker: Tasker, path: Path) -> bool:
    image = np.load(path)
    params = JFeatureMatch(
        template=[f"produce/drinks/{drink_id}.png" for drink_id in DRINK_IDS],
        roi=(100, 760, 520, 270),
        count=5,
        ratio=0.65,
        order_by="Horizontal",
    )
    detail = tasker.post_recognition(JRecognitionType.FeatureMatch, params, image).wait().get()
    return bool(detail and detail.nodes and detail.nodes[-1].recognition.hit)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("portable", type=Path)
    parser.add_argument("fixtures", type=Path)
    args = parser.parse_args()

    resource = Resource()
    status = resource.post_bundle(args.portable / "resource" / "base").wait().status
    if not status.succeeded:
        raise RuntimeError("failed to load base resource bundle")

    tasker = Tasker()
    # Direct recognition needs the resource handle but no live controller.
    tasker._resource_holder = resource
    if not Library.framework().MaaTaskerBindResource(tasker._handle, resource._handle):
        raise RuntimeError("failed to bind resource")

    drink_hit = recognize(tasker, args.fixtures / "drink_reward_720x1280.npy")
    card_hit = recognize(tasker, args.fixtures / "card_reward_720x1280.npy")
    print(f"DRINK_REWARD_HIT={drink_hit}")
    print(f"CARD_REWARD_FALSE_POSITIVE={card_hit}")
    if not drink_hit or card_hit:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

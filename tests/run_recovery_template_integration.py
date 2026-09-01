"""Verify recovery templates against normalized live screenshots."""

import argparse
from pathlib import Path

import numpy as np

from maa.library import Library
from maa.pipeline import JRecognitionType, JTemplateMatch
from maa.resource import Resource
from maa.tasker import Tasker


def recognize(tasker: Tasker, screenshot: Path, template: str, roi: tuple[int, int, int, int], label: str) -> None:
    image = np.load(screenshot)
    params = JTemplateMatch(template=[template], roi=roi, threshold=[0.82])
    detail = tasker.post_recognition(JRecognitionType.TemplateMatch, params, image).wait().get()
    recognition = detail.nodes[-1].recognition
    print(f"{label}_HIT={recognition.hit}")
    if recognition.best_result:
        print(f"{label}_SCORE={recognition.best_result.score:.6f}")
        print(f"{label}_BOX={recognition.best_result.box}")
    if not recognition.hit:
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("resource", type=Path)
    parser.add_argument("date_screenshot", type=Path)
    parser.add_argument("--network-screenshot", type=Path)
    args = parser.parse_args()

    resource = Resource()
    status = resource.post_bundle(args.resource).wait().status
    if not status.succeeded:
        raise RuntimeError("failed to load resource bundle")

    tasker = Tasker()
    tasker._resource_holder = resource
    if not Library.framework().MaaTaskerBindResource(tasker._handle, resource._handle):
        raise RuntimeError("failed to bind resource")

    recognize(
        tasker,
        args.date_screenshot,
        "date_change.png",
        (0, 850, 720, 250),
        "DATE_CHANGE",
    )
    if args.network_screenshot:
        recognize(
            tasker,
            args.network_screenshot,
            "produce/disconnect_error.png",
            (0, 850, 720, 180),
            "NETWORK_ERROR",
        )


if __name__ == "__main__":
    main()

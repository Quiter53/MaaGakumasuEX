"""Work UI options must reach the matching job without overriding each other."""

import json
import sys
from itertools import product
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

from custom.reco.work import WorkChooseAuto, WorkChooseTimeAuto


@pytest.mark.parametrize("show_choice,live_choice", list(product(range(4), repeat=2)))
def test_work_duration_options_keep_each_job_route(show_choice, live_choice):
    interface = json.loads((ROOT / "assets/interface.json").read_text("utf-8"))
    nodes = json.loads((ROOT / "assets/resource/base/pipeline/Work.json").read_text("utf-8"))
    overrides = {}
    for option, choice in (("迷你演唱会工作时长", show_choice), ("直播活动工作时长", live_choice)):
        overrides.update(interface["option"][option]["cases"][choice]["pipeline_override"])
    for name, override in overrides.items():
        nodes[name].update(override)

    for kind, job, choice in (("show", "Show", show_choice), ("live", "Live", live_choice)):
        confirm = nodes[f"WorkChoose{job}Confirm"]
        if choice == 0:
            entry = confirm["next"][0]
            node = nodes[entry]
            assert node["recognition"]["param"]["custom_recognition_param"] == {"kind": kind}
            assert node["next"] == [f"WorkChoose{job}Time", "[JumpBack]DecideButton"]
        else:
            assert confirm["next"] == [f"WorkChoose{job}Time", "[JumpBack]DecideButton"]
            assert nodes[f"WorkChoose{job}Time"]["expected"] == str(choice * 4)


@pytest.mark.parametrize("kind,job", (("show", "Show"), ("live", "Live")))
def test_auto_duration_updates_only_the_selected_job(kind, job):
    context = Mock()
    context.override_pipeline.return_value = True
    result = WorkChooseTimeAuto().analyze(context, SimpleNamespace(custom_recognition_param=json.dumps({"kind": kind})))
    assert result is not None
    assert list(context.override_pipeline.call_args.args[0]) == [f"WorkChoose{job}Time"]


@pytest.mark.parametrize("working", (False, True))
def test_auto_idol_handles_one_readable_affinity(working):
    context = Mock()
    box = [100, 800, 40, 20]
    context.run_recognition.side_effect = [
        None,
        None,
        SimpleNamespace(hit=True, filtered_results=[SimpleNamespace(box=box, text="10/20")]),
        SimpleNamespace(hit=working),
    ]
    with patch("custom.reco.work.wait_settled"):
        result = WorkChooseAuto().analyze(context, SimpleNamespace(image=None))
    if working:
        assert result is None
    else:
        assert result.box == box

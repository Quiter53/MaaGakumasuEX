"""Connect the GUI-side native client to the real agent without game input."""

import argparse
import json
import os
import subprocess
from pathlib import Path

from maa.library import Library


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("portable", type=Path)
    parser.add_argument("log", type=Path)
    args = parser.parse_args()
    root = args.portable.resolve()
    Library.open(root / "runtimes/win-x64/native")
    from maa.agent_client import AgentClient
    from maa.resource import Resource

    resource = Resource()
    client = AgentClient()
    assert client.bind(resource)
    client.set_timeout(15000)
    identifier = client.identifier
    with args.log.open("w", encoding="utf-8") as output:
        process = subprocess.Popen(
            [str(root / "python/python.exe"), "-u", str(root / "agent/main.py"),
             identifier, f"socket_id={identifier}", "instance_name=合并启动验证"],
            cwd=root, stdout=output, stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            assert client.connect(), "Agent handshake failed; inspect server log"
            actions = client.custom_action_list
            for name in ("ProduceCardsAuto", "ProduceChooseRewardAuto", "ProduceRecoverConnectionAuto", "ProduceHIFChangeAuto", "ProduceHIFChangeSourceAuto", "ProduceHIFEnterShopAuto"):
                assert name in actions, (name, actions)
            assert "ProduceHIFChange" in client.custom_recognition_list
            assert "ProduceHIFChangeSource" in client.custom_recognition_list
            assert "ProduceHIFClassEvent" in client.custom_recognition_list
            assert "ProduceHIFCourseEvent" in client.custom_recognition_list
            assert "ProduceHIFShopEntry" in client.custom_recognition_list
            assert "ProduceHIFIntervalShop" in client.custom_recognition_list
            assert "ProduceHIFConsultationShop" in client.custom_recognition_list
            assert "ProduceHIFStart" in client.custom_recognition_list
            assert "ProduceHIFResume" in client.custom_recognition_list
            assert "ProduceHIFIntervalShoppingAuto" in actions
            assert "ProduceHIFDeleteAuto" in actions
            assert "ProduceHIFDelete" in client.custom_recognition_list
            assert "ProduceHIFFailedAuto" in actions
            print(json.dumps({"nativeVersion": Library.version(), "connected": client.connected,
                              "actions": len(actions), "recognitions": len(client.custom_recognition_list)}, ensure_ascii=False))
            client.disconnect()
            process.wait(timeout=10)
            assert process.returncode == 0, process.returncode
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)


if __name__ == "__main__":
    main()

"""Run with the portable Python: isolated Agent IPC, no game controller/input."""
import os
import subprocess
import sys
from pathlib import Path

from maa.library import Library
from maa.agent_client import AgentClient
from maa.resource import Resource


def main():
    root = Path(sys.argv[1]).resolve()
    Library.open(root/'runtimes/win-x64/native')
    resource = Resource()
    assert resource.post_bundle(root/'resource/base').wait().status.succeeded
    assert resource.get_node_data('ProduceCardsFlag')['action']['param']['custom_action'] == 'ProduceCardsAuto'
    assert resource.get_node_data('ProduceMemoryPolicy')['action']['param']['custom_action'] == 'ProduceMemoryInspect'
    client = AgentClient()
    assert client.bind(resource)
    assert client.set_timeout(10000)
    code = (
        "import sys,runpy; from pathlib import Path; from maa.library import Library; "
        "root=Path.cwd(); Library.open(root/'runtimes/win-x64/native'); "
        "sys.path.insert(0,str(root/'agent')); "
        "entry=runpy.run_path(str(root/'agent/main.py')); "
        "sys.argv=['main.py',sys.argv[1]]; entry['agent']()"
    )
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)
    env['PYTHONIOENCODING'] = 'utf-8'
    child = subprocess.Popen([str(root/'python/python.exe'), '-B', '-c', code, client.identifier],
                             cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        assert client.connect(), 'Agent IPC connection failed'
        assert client.alive
        print('PORTABLE_AGENT_IPC_OK; EX_CARD_AND_MEMORY_NODES_OK', flush=True)
        assert client.disconnect()
        output, _ = child.communicate(timeout=15)
        print(output.decode('utf-8', errors='replace'))
        assert child.returncode == 0, child.returncode
        print('PORTABLE_AGENT_CLEAN_EXIT_OK')
    finally:
        if child.poll() is None:
            client.disconnect()
            try:
                child.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                child.terminate()  # Only our isolated smoke-test child.
                child.communicate(timeout=5)


if __name__ == '__main__':
    main()

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QLockFile
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from nodera.instance import RestartServer, acquire_instance, instance_name
from nodera.models import ClientError


def run_peer(app, name, path, busy=False):
    source = """
import json,sys
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QLockFile
from nodera.instance import acquire_instance,request_restart
from nodera.models import ClientError
app=QApplication([])
lock=QLockFile(sys.argv[2])
try:
    result=request_restart(sys.argv[1]) if sys.argv[3]=='busy' else acquire_instance(lock,sys.argv[1],timeout=2)
    print(json.dumps({'reconnect':result,'locked':not QLockFile(sys.argv[2]).tryLock(0)}))
except ClientError:
    print(json.dumps({'busy':True}))
finally:
    lock.unlock()
"""
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    worker = subprocess.Popen(
        [sys.executable, "-c", source, name, path, "busy" if busy else "restart"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    deadline = time.monotonic() + 7
    while worker.poll() is None and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(10)
    output, errors = worker.communicate(timeout=2)
    assert worker.returncode == 0, errors
    return json.loads(output)


def test_restart_handover_waits_for_lock_and_retains_connection_intent(tmp_path):
    app = QApplication.instance() or QApplication([])
    path = str(tmp_path / "app.lock")
    old_lock, new_lock = QLockFile(path), QLockFile(path)
    assert old_lock.tryLock(0)
    stopped = []

    def shutdown():
        stopped.append(True)
        old_lock.unlock()

    name = instance_name(tmp_path)
    server = RestartServer(name, lambda: True, shutdown)
    try:
        assert run_peer(app, name, path) == {"reconnect": True, "locked": True}
        assert stopped == [True]
        assert run_peer(app, name, path, busy=True) == {"busy": True}
        assert stopped == [True]
    finally:
        new_lock.unlock()
        server.server.close()
        server.deleteLater()
        app.processEvents()


def test_first_launch_acquires_lock_without_handover(tmp_path):
    lock = QLockFile(str(tmp_path / "app.lock"))
    assert acquire_instance(lock, "Nodera-test-" + uuid4().hex) is False
    lock.unlock()


def test_old_process_without_ipc_is_not_terminated(tmp_path, monkeypatch):
    old_lock = QLockFile(str(tmp_path / "app.lock"))
    new_lock = QLockFile(str(tmp_path / "app.lock"))
    assert old_lock.tryLock(0)
    monkeypatch.setattr("nodera.instance.request_restart", lambda name: None)
    try:
        with pytest.raises(ClientError, match="старая версия"):
            acquire_instance(new_lock, "missing")
        assert not new_lock.tryLock(0)
    finally:
        old_lock.unlock()

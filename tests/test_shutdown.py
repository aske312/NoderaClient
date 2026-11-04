import os
import subprocess
import sys

import pytest

from nodera.bundled_core import bundled_location


@pytest.mark.skipif(os.name != "nt" or not bundled_location().is_file(), reason="Windows core")
def test_hidden_tray_exit_stops_owned_components_and_preserves_unrelated_process(tmp_path):
    unrelated = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    script = r'''
import json, os, subprocess, sys, time
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from nodera.models import Profile, Settings
from nodera.storage import Store
from nodera.system_proxy import SystemProxy
from nodera.ui import MainWindow

root = Path(sys.argv[1])
original = {'flags': 9, 'server': '', 'bypass': '', 'pac': ''}
state = root / 'fake-proxy.json'
state.write_text(json.dumps(original))
class FileProxy:
    def read(self):
        return json.loads(state.read_text())
    def write(self, values):
        state.write_text(json.dumps(values))
watcher_code = """
import json,sys
from pathlib import Path
import nodera.system_proxy as module
root=Path(sys.argv[2])
class FileProxy:
    def read(self):
        return json.loads((root/'fake-proxy.json').read_text())
    def write(self, values):
        (root/'fake-proxy.json').write_text(json.dumps(values))
module.WindowsProxy=FileProxy
module.watch_parent(int(sys.argv[1]),root,sys.argv[3])
"""
watchers = []
def guard(token):
    process = subprocess.Popen(
        [sys.executable, '-c', watcher_code, str(os.getpid()), str(root), token],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    proxy._guard_process = process
    watchers.append(process)
    ready = root / ('proxy-ready-' + token)
    deadline = time.monotonic() + 10
    while not ready.exists() and time.monotonic() < deadline:
        assert process.poll() is None
        time.sleep(0.03)
    assert ready.exists()
    ready.unlink()

app = QApplication([])
settings = Settings(tun=False, system_proxy=True, geo_auto=False, update_hours=0, client_location_attempted=True)
profile = Profile('Shutdown test', '', {'proxies': [{'name': 'Direct', 'type': 'direct'}], 'rules': ['MATCH,DIRECT']}, '')
window = MainWindow(Store(root), settings, profile)
proxy = SystemProxy(root, FileProxy(), guard)
window.core.system_proxy = proxy
window.core.start(profile, settings)
core_process = window.core.process
window.set_connected('test')
window.shutdown_complete.connect(app.quit)
window.show()
window.hide()
QTimer.singleShot(0, window.exit_app)
assert app.exec() == 0
assert core_process.poll() is not None
assert watchers and all(process.poll() is not None for process in watchers)
assert proxy._guard_process is None and not proxy.pending
assert json.loads(state.read_text()) == original
assert not window.inspections and not window.task and not window.geo_task and not window.poll_task
assert all(not timer.isActive() for timer in window.findChildren(QTimer))
print('Clean tray exit: core stopped, guard stopped, proxy restored')
'''
    try:
        result = subprocess.run(
            [sys.executable, "-c", script, str(tmp_path)],
            capture_output=True,
            text=True,
            timeout=45,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "Clean tray exit" in result.stdout
        assert unrelated.poll() is None
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=5)

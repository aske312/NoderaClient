import copy
import ctypes
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from nodera.models import ClientError
from nodera.system_proxy import OptionList, SystemProxy, WindowsProxy

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows proxy and DPAPI")

ORIGINAL = {
    "flags": 13,
    "server": "old.example:8080",
    "bypass": "*.office",
    "pac": "https://office.example/proxy.pac",
}


class MemoryProxy:
    def __init__(self):
        self.values = copy.deepcopy(ORIGINAL)

    def read(self):
        return dict(self.values)

    def write(self, values):
        self.values = dict(values)


def test_transaction_restores_all_preferences_and_encrypts_backup(tmp_path):
    backend = MemoryProxy()
    tokens = []
    proxy = SystemProxy(tmp_path, backend, tokens.append)
    proxy.enable(7897)
    assert backend.values["server"] == "127.0.0.1:7897"
    assert backend.values["flags"] == 3
    assert backend.values["pac"] == ""
    assert len(tokens) == 1
    assert b"office.example" not in proxy.path.read_bytes()
    proxy.restore()
    assert backend.values == ORIGINAL
    assert not proxy.pending
    proxy.restore()


def test_guard_start_failure_leaves_windows_unchanged(tmp_path):
    backend = MemoryProxy()

    def fail(token):
        raise ClientError("guard failed")

    proxy = SystemProxy(tmp_path, backend, fail)
    with pytest.raises(ClientError, match="guard failed"):
        proxy.enable(7890)
    assert backend.values == ORIGINAL
    assert not proxy.pending


def test_failed_partial_write_rolls_back_and_allows_retry(tmp_path):
    backend = MemoryProxy()
    write = backend.write

    def partial(values):
        if values["server"].startswith("127."):
            backend.values["server"] = values["server"]
            raise ClientError("write failed")
        write(values)

    backend.write = partial
    proxy = SystemProxy(tmp_path, backend, lambda _: None)
    with pytest.raises(ClientError, match="write failed"):
        proxy.enable(7890)
    assert backend.values == ORIGINAL
    assert not proxy.pending


def test_old_guard_does_not_restore_new_session(tmp_path):
    backend = MemoryProxy()
    tokens = []
    proxy = SystemProxy(tmp_path, backend, tokens.append)
    proxy.enable(7890)
    proxy.restore()
    proxy.enable(7891)
    proxy.restore(tokens[0])
    assert backend.values["server"] == "127.0.0.1:7891"
    assert proxy.pending
    # A restarted UI can recover the same encrypted journal.
    SystemProxy(tmp_path, backend).restore()
    assert backend.values == ORIGINAL


def test_external_edits_are_preserved(tmp_path):
    backend = MemoryProxy()
    proxy = SystemProxy(tmp_path, backend, lambda _: None)
    proxy.enable(7890)
    backend.values = {"flags": 1, "server": "user.example:9000", "bypass": "custom", "pac": ""}
    proxy.restore()
    assert backend.values["server"] == "user.example:9000"
    assert backend.values["flags"] == 1
    assert backend.values["bypass"] == "custom"


def test_native_snapshot_is_read_only():
    # Exercises ctypes layouts and freeing WinINet strings, without changing Windows.
    values = WindowsProxy().read()
    assert type(values["flags"]) is int
    assert all(isinstance(values[key], str) for key in ("server", "bypass", "pac"))


def test_native_write_marshals_flags_and_strings_without_changing_windows():
    calls = []

    class FakeWininet:
        def InternetSetOptionW(self, handle, option, pointer, size):
            calls.append(option)
            if option == 75:
                listing = ctypes.cast(pointer, ctypes.POINTER(OptionList)).contents
                assert listing.count == 4
                assert [listing.options[i].kind for i in range(4)] == [1, 2, 3, 4]
                assert listing.options[0].value.number == ORIGINAL["flags"]
                for index, key in enumerate(("server", "bypass", "pac"), 1):
                    assert ctypes.wstring_at(listing.options[index].value.text) == ORIGINAL[key]
            return True

    proxy = WindowsProxy.__new__(WindowsProxy)
    proxy.api = FakeWininet()
    proxy.write(ORIGINAL)
    assert calls == [75, 95, 37]


def test_watchdog_recovers_after_owner_is_killed_without_touching_windows(tmp_path):
    state = tmp_path / "fake-proxy.json"

    class FileProxy:
        def read(self):
            return json.loads(state.read_text())

        def write(self, values):
            state.write_text(json.dumps(values))

    state.write_text(json.dumps(ORIGINAL))
    backend = FileProxy()
    proxy = SystemProxy(tmp_path, backend, lambda _: None)
    proxy.enable(7890)
    token = proxy.read_journal()["token"]
    owner = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    watcher_code = """
import json, sys
from pathlib import Path
import nodera.system_proxy as module
root = Path(sys.argv[2])
class FileProxy:
    def read(self):
        return json.loads((root / 'fake-proxy.json').read_text())
    def write(self, values):
        (root / 'fake-proxy.json').write_text(json.dumps(values))
module.WindowsProxy = FileProxy
module.watch_parent(int(sys.argv[1]), root, sys.argv[3])
"""
    watcher = subprocess.Popen(
        [sys.executable, "-c", watcher_code, str(owner.pid), str(tmp_path), token],
        cwd=Path(__file__).resolve().parents[1],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        deadline = time.monotonic() + 8
        while not (tmp_path / f"proxy-ready-{token}").exists() and time.monotonic() < deadline:
            assert watcher.poll() is None
            time.sleep(0.05)
        assert (tmp_path / f"proxy-ready-{token}").exists()
        owner.kill()
        owner.wait(timeout=3)
        assert watcher.wait(timeout=5) == 0
        assert backend.read() == ORIGINAL
        assert not proxy.pending
    finally:
        for process in (owner, watcher):
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)

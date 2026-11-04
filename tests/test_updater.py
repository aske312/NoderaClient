import hashlib

import httpx
import pytest

from nodera.models import ClientError
from nodera.updater import REPOSITORY, Release, check_release, download_release, launch_installer


def release_data(content=b"installer"):
    return {
        "tag_name": "v1.2.3",
        "draft": False,
        "prerelease": False,
        "assets": [
            {
                "name": "Nodera-Setup.exe",
                "browser_download_url": f"https://github.com/{REPOSITORY}/releases/download/v1.2.3/Nodera-Setup.exe",
                "size": len(content),
                "digest": "sha256:" + hashlib.sha256(content).hexdigest(),
            }
        ],
    }


def test_stable_newer_release_download_and_execution_recheck(tmp_path, monkeypatch):
    content = b"installer"
    release = check_release(
        "1.2.2", httpx.MockTransport(lambda _: httpx.Response(200, json=release_data(content)))
    )
    path = download_release(
        release, tmp_path, httpx.MockTransport(lambda _: httpx.Response(200, content=content))
    )
    assert path.read_bytes() == content
    calls = []
    monkeypatch.setattr("nodera.updater.subprocess.Popen", lambda *a, **kw: calls.append(a))
    launch_installer(path, release)
    assert len(calls) == 1
    path.write_bytes(b"tampered!")
    with pytest.raises(ClientError):
        launch_installer(path, release)
    assert len(calls) == 1


@pytest.mark.parametrize("change", ["digest", "url", "size", "prerelease", "version"])
def test_untrusted_or_incomplete_metadata_is_rejected(change):
    data = release_data()
    if change == "digest":
        data["assets"][0].pop("digest")
    elif change == "url":
        data["assets"][0]["browser_download_url"] = "https://example.org/malicious.exe"
    elif change == "size":
        data["assets"][0]["size"] = 10**12
    elif change == "prerelease":
        data["prerelease"] = True
    else:
        data["tag_name"] = "v1.3.0-rc1"
    with pytest.raises(ClientError):
        check_release("1.0.0", httpx.MockTransport(lambda _: httpx.Response(200, json=data)))


@pytest.mark.parametrize("current", ["1.2.3", "1.10.0", "2.0.0"])
def test_no_downgrades_or_reinstall(current):
    assert (
        check_release(
            current, httpx.MockTransport(lambda _: httpx.Response(200, json=release_data()))
        )
        is None
    )


@pytest.mark.parametrize("content", [b"bad", b"too much data", b"tampered!"])
def test_failed_download_preserves_previous_installer_and_removes_partial(tmp_path, content):
    release = Release(
        "1.2.3", "https://github.com/test", hashlib.sha256(b"installer").hexdigest(), 9
    )
    folder = tmp_path / "updates" / release.version
    folder.mkdir(parents=True)
    (folder / release.filename).write_bytes(b"previous")
    with pytest.raises(ClientError):
        download_release(
            release, tmp_path, httpx.MockTransport(lambda _: httpx.Response(200, content=content))
        )
    assert (folder / release.filename).read_bytes() == b"previous"
    assert not list(folder.glob("*.download"))


def test_cancelled_download_does_not_replace_installer(tmp_path):
    import threading

    cancelled = threading.Event()

    class Body(httpx.SyncByteStream):
        def __iter__(self):
            yield b"first"
            cancelled.set()
            yield b"last"

    release = Release(
        "1.2.3", "https://github.com/test", hashlib.sha256(b"firstlast").hexdigest(), 9
    )
    with pytest.raises(ClientError, match="отменена"):
        download_release(
            release,
            tmp_path,
            httpx.MockTransport(lambda _: httpx.Response(200, stream=Body())),
            cancelled=cancelled,
        )
    assert not list((tmp_path / "updates/1.2.3").iterdir())

import json
import os

import pytest

from scripts import launch_test


@pytest.fixture
def project(tmp_path):
    (tmp_path / "main.py").write_text("print('app')")
    (tmp_path / "pyproject.toml").write_text("[project]")
    (tmp_path / "nodera/resources").mkdir(parents=True)
    (tmp_path / "nodera/ui.py").write_text("theme = 'dark'")
    (tmp_path / "nodera/resources/icon.svg").write_text("<svg/>")
    return tmp_path


def packaged_build(project, fingerprint):
    executable = project / "dist/builds/example/Nodera/Nodera.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"test executable")
    resources = executable.parent / "_internal/nodera/resources"
    resources.mkdir(parents=True)
    (resources / "build-info.json").write_text(
        json.dumps({"channel": "test", "source_fingerprint": fingerprint})
    )
    launch_test.record_build(executable, project)
    return executable


def test_launcher_detects_content_changes_even_when_timestamp_is_unchanged(project):
    original = launch_test.source_fingerprint(project, environment=[])
    source = project / "nodera/ui.py"
    timestamp = source.stat().st_mtime_ns
    source.write_text("theme = 'mint'")
    os.utime(source, ns=(timestamp, timestamp))
    assert launch_test.source_fingerprint(project, environment=[]) != original


def test_generated_metadata_and_user_data_do_not_trigger_rebuild(project):
    original = launch_test.source_fingerprint(project, environment=[])
    (project / "nodera/resources/build-info.json").write_text('{"generated": true}')
    (project / "artifacts").mkdir()
    (project / "artifacts/settings.json").write_text('{"user": "changed"}')
    assert launch_test.source_fingerprint(project, environment=[]) == original
    (project / "nodera/resources/icon.svg").write_text("<svg>changed</svg>")
    assert launch_test.source_fingerprint(project, environment=[]) != original


def test_dependency_updates_trigger_rebuild(project):
    assert launch_test.source_fingerprint(
        project, environment=[("Qt", "6.8")]
    ) != launch_test.source_fingerprint(project, environment=[("Qt", "6.9")])


def test_cache_requires_matching_packaged_metadata_and_existing_executable(project):
    executable = packaged_build(project, "current")
    assert launch_test.current_build("current", project) == executable
    assert launch_test.current_build("changed", project) is None
    executable.unlink()
    assert launch_test.current_build("current", project) is None


def test_cache_cannot_launch_a_path_outside_build_directory(project):
    state = project / "artifacts/test-launcher/latest.json"
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({"executable": str(project / "main.py"), "fingerprint": "same"}))
    assert launch_test.current_build("same", project) is None


@pytest.mark.skipif(os.name != "nt", reason="Windows launcher lock")
def test_failed_rebuild_does_not_launch_a_stale_binary(project, monkeypatch):
    packaged_build(project, "old")
    monkeypatch.setattr(launch_test, "ROOT", project)
    monkeypatch.setattr(launch_test, "source_fingerprint", lambda: "new")
    monkeypatch.setattr(
        launch_test,
        "current_build",
        lambda fingerprint: None,
    )
    monkeypatch.setattr(launch_test.sys, "argv", ["launch_test.py"])
    monkeypatch.setattr(launch_test.os, "startfile", lambda _: pytest.fail("Stale app launched"))

    def failed(**kwargs):
        raise RuntimeError("Build failed")

    monkeypatch.setattr(launch_test, "build", failed)
    with pytest.raises(RuntimeError, match="Build failed"):
        launch_test.main()

import json
import subprocess
import tomllib
from pathlib import Path

import pytest

from nodera import __version__, versioning


def test_packaging_reads_the_product_version_from_the_application():
    project = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    assert "version" in project["project"]["dynamic"]
    assert "version" not in project["project"]
    assert project["tool"]["setuptools"]["dynamic"]["version"]["attr"] == "nodera.__version__"


def test_git_revision_reads_real_local_commit_and_handles_missing_history(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)
    assert versioning.git_revision(tmp_path) == ""
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "--allow-empty",
            "-qm",
            "test",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    expected = subprocess.run(
        ["git", "rev-parse", "--short=12", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert versioning.git_revision(tmp_path) == expected


@pytest.mark.parametrize(
    "metadata", [None, [], {"version": "old", "build": "123"}, {"version": __version__, "build": 3}]
)
def test_invalid_build_metadata_does_not_break_startup(tmp_path, monkeypatch, metadata):
    (tmp_path / "resources").mkdir()
    (tmp_path / "resources/build-info.json").write_text(json.dumps(metadata))
    monkeypatch.setattr(versioning, "__file__", str(tmp_path / "versioning.py"))
    monkeypatch.setattr(versioning.sys, "frozen", True, raising=False)
    assert versioning.build_info()["version"] == __version__
    assert versioning.build_info()["build"] == "no-commit"


def test_frozen_build_keeps_release_version_and_separate_identity(tmp_path, monkeypatch):
    info = {"version": __version__, "build": "abc123def456", "revision": "abc123def456"}
    (tmp_path / "resources").mkdir()
    (tmp_path / "resources/build-info.json").write_text(json.dumps(info))
    monkeypatch.setattr(versioning, "__file__", str(tmp_path / "versioning.py"))
    monkeypatch.setattr(versioning.sys, "frozen", True, raising=False)
    assert versioning.build_info() == info

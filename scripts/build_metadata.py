"""Generate a build ID and Windows file metadata without changing the release version."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from launch_test import source_fingerprint
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

from nodera import __version__
from nodera.versioning import git_revision

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", choices=("release", "test"), default="release")
    arguments = parser.parse_args()
    now = datetime.now(timezone.utc)
    revision = git_revision(ROOT)
    info = {
        "version": __version__,
        "build": revision or "no-commit",
        "artifact_id": now.strftime("%Y%m%d-%H%M%S-%f"),
        "created_at": now.isoformat(),
        "revision": revision,
        "channel": arguments.channel,
    }
    if arguments.channel == "test":
        info["source_fingerprint"] = source_fingerprint(ROOT)
    (ROOT / "nodera/resources/build-info.json").write_text(json.dumps(info), encoding="utf-8")
    numbers = (*map(int, __version__.split(".")), 0)
    version = VSVersionInfo(
        ffi=FixedFileInfo(
            filevers=numbers,
            prodvers=numbers,
            mask=0x3F,
            flags=0x8,
            OS=0x40004,
            fileType=1,
            subtype=0,
            date=(0, 0),
        ),
        kids=[
            StringFileInfo(
                [
                    StringTable(
                        "040904B0",
                        [
                            StringStruct(key, value)
                            for key, value in (
                                ("CompanyName", "Nodera"),
                                ("FileDescription", "Nodera"),
                                ("FileVersion", __version__),
                                ("ProductName", "Nodera"),
                                ("ProductVersion", __version__),
                                ("PrivateBuild", info["build"]),
                                ("OriginalFilename", "Nodera.exe"),
                            )
                        ],
                    )
                ]
            ),
            VarFileInfo([VarStruct("Translation", [1033, 1200])]),
        ],
    )
    (ROOT / "build").mkdir(exist_ok=True)
    (ROOT / "build/version-info.txt").write_text(str(version), encoding="utf-8")
    print(json.dumps(info))


if __name__ == "__main__":
    main()

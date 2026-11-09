"""Collect notices from the runtime distributions used by this exact build."""

import json
import shutil
import sys
from importlib.metadata import distribution
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = (
    "PySide6-Essentials",
    "shiboken6",
    "httpx",
    "httpcore",
    "anyio",
    "h11",
    "idna",
    "certifi",
    "PyYAML",
    "typing_extensions",
    "cryptography",
    "cffi",
    "pycparser",
)


def main():
    destination = ROOT / "build/licenses"
    destination.mkdir(parents=True, exist_ok=True)
    for source in (ROOT / "LICENSE", *sorted((ROOT / "licenses").glob("*.txt"))):
        shutil.copy2(source, destination / source.name)
    for language in ("ru", "en"):
        source = ROOT / f"packaging/license-{language}.txt"
        shutil.copy2(source, destination / source.name)
    index = [
        "Nodera — licenses and source information",
        "Original Nodera code: MIT; see LICENSE.",
        "Source: https://github.com/aske312/NoderaClient",
        "Qt/PySide6/Shiboken are used under their applicable LGPLv3 terms.",
        "The supplied Qt libraries are dynamically loaded and may be replaced",
        "with compatible modified versions. No extra restriction on reverse",
        "engineering needed to debug such modifications is imposed by Nodera.",
        "See LGPL-3.0-only.txt and GPL-3.0-only.txt.",
        "Exact-version upstream sources are available at:",
    ]
    for name in PACKAGES:
        dist = distribution(name)
        folder = destination / name
        folder.mkdir(exist_ok=True)
        files = [
            entry
            for entry in dist.files or []
            if entry.parts[0].endswith(".dist-info")
            and any(
                part.lower().startswith(("license", "copying", "notice")) for part in entry.parts
            )
        ]
        if not files:
            raise RuntimeError(f"Runtime package has no license notice: {name}")
        for entry in files:
            source = Path(dist.locate_file(entry))
            if source.is_file():
                relative = Path(*entry.parts[1:])
                target = folder / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        license_name = dist.metadata.get("License-Expression") or dist.metadata.get("License", "")
        index.append(f"{name} {dist.version}: {license_name}")
        index.extend(dist.metadata.get_all("Project-URL", []))
        if name == "PySide6-Essentials":
            index.extend(
                (
                    f"https://code.qt.io/cgit/pyside/pyside-setup.git/?h=v{dist.version}",
                    f"https://download.qt.io/archive/qt/{'.'.join(dist.version.split('.')[:2])}/{dist.version}/submodules/",
                )
            )
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file():
        raise RuntimeError("The Python runtime license was not found")
    shutil.copy2(python_license, destination / "Python-LICENSE.txt")
    core = json.loads((ROOT / "nodera/resources/mihomo.json").read_text(encoding="utf-8"))
    index.extend(
        (
            f"Mihomo {core['version']}: GPL-3.0; license and source archive in third_party/mihomo.",
            f"Source: {core['source_url']}",
            "Geodata: license, upstream notices and sources in geodata/.",
            "Map: Natural Earth, public domain; see nodera/resources/map-NOTICE.txt.",
            "Flags: MIT; see nodera/resources/flags-LICENSE.txt and flags-NOTICE.txt.",
        )
    )
    (destination / "THIRD-PARTY-NOTICES.txt").write_text("\n".join(index) + "\n", encoding="utf-8")
    print("Prepared runtime license texts and third-party notices")


if __name__ == "__main__":
    main()

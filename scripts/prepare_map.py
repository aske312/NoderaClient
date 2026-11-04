"""Regenerate the checked-in offline map and flags from pinned public sources."""

import io
import json
import re
import tarfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
SOURCES = [
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/ne_110m_land.geojson",
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/ne_50m_admin_0_countries.geojson",
    "https://codeload.github.com/lipis/flag-icons/tar.gz/refs/tags/v7.3.2",
]


def main():
    contents = []
    for index, source in enumerate(SOURCES):
        cached = ROOT / "artifacts" / f"map-source-{index}"
        if cached.exists():
            contents.append(cached.read_bytes())
        else:
            response = httpx.get(source, timeout=60, follow_redirects=True)
            response.raise_for_status()
            contents.append(response.content)
    rings = []
    for feature in json.loads(contents[0])["features"]:
        geometry = feature["geometry"]
        polygons = geometry["coordinates"]
        if geometry["type"] == "Polygon":
            polygons = [polygons]
        for polygon in polygons:
            rings.append([[round(x, 2), round(y, 2)] for x, y in polygon[0]])
    countries = {}
    for feature in json.loads(contents[1])["features"]:
        props = feature["properties"]
        code = props["ISO_A2_EH"]
        if not re.fullmatch("[A-Z]{2}", code):
            continue
        countries[code] = {
            "name": props["NAME_RU"] or props["NAME_EN"],
            "aliases": list(
                {
                    props[key]
                    for key in ("NAME_EN", "NAME_RU", "NAME", "NAME_LONG")
                    if props.get(key)
                }
            ),
            "lon": round(props["LABEL_X"], 2),
            "lat": round(props["LABEL_Y"], 2),
        }
    resource = ROOT / "nodera" / "resources"
    (resource / "flags").mkdir(exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(contents[2]), mode="r:gz") as archive:
        for member in archive.getmembers():
            filename = Path(member.name).name
            if "/flags/4x3/" in member.name and filename.removesuffix(".svg").upper() in countries:
                (resource / "flags" / filename).write_bytes(archive.extractfile(member).read())
        license_file = next(item for item in archive.getmembers() if item.name.endswith("/LICENSE"))
        (resource / "flags-LICENSE.txt").write_bytes(archive.extractfile(license_file).read())
    (resource / "world-map.json").write_text(
        json.dumps(
            {"land": rings, "countries": countries}, ensure_ascii=False, separators=(",", ":")
        ),
        encoding="utf-8",
    )
    (resource / "map-NOTICE.txt").write_text(
        "Map: Natural Earth v5.1.2, public domain. Land and country label coordinates.\n"
        "https://www.naturalearthdata.com/about/terms-of-use/\n"
        "Flags: flag-icons v7.3.2, MIT, copyright (c) 2013 Panayiotis Lipiridis.\n"
        "https://github.com/lipis/flag-icons\nSee flags-LICENSE.txt.\n",
        encoding="utf-8",
    )
    print(f"Prepared offline world map and {len(list((resource / 'flags').glob('*.svg')))} flags.")


if __name__ == "__main__":
    main()

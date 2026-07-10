#!/usr/bin/env python3
"""Tile the normalized editions into docs/tiles/{year}/ (XYZ, PNG).

Max zoom per edition is derived from the raster's native ground resolution
(the 2003 print at 2.5 ft/px tiles deeper than the 1929 scan at 11 ft/px).
Also writes docs/tiles/index.json with per-edition zoom range and WGS84
bounds for the viewer.
"""
import json
import math
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GEOREF = ROOT / "work" / "georef"
TILES = ROOT / "docs" / "tiles"

MIN_ZOOM = 11
MAX_ZOOM_CAP = 17
EDITIONS = ["1929", "1939", "1949", "1958", "1963", "1976", "1991", "2003",
            "2020", "2024"]


def info(path):
    return json.loads(subprocess.run(
        ["gdalinfo", "-json", str(path)], capture_output=True, text=True,
        check=True).stdout)


def native_max_zoom(path):
    """Zoom whose mercator resolution first exceeds the raster's own."""
    i = info(path)
    gt = i["geoTransform"]
    # ground resolution in meters (CRS is ftUS)
    res_m = abs(gt[1]) * 0.3048006096
    lat = i["wgs84Extent"]["coordinates"][0][0][1]
    for z in range(MAX_ZOOM_CAP, MIN_ZOOM, -1):
        merc_res = 156543.03392 * math.cos(math.radians(lat)) / (2 ** z)
        if merc_res >= res_m * 0.75:
            return z
    return MIN_ZOOM + 1


def wgs84_bounds(path):
    coords = info(path)["wgs84Extent"]["coordinates"][0]
    xs = [c[0] for c in coords]
    ys = [c[1] for c in coords]
    return [min(xs), min(ys), max(xs), max(ys)]


def main():
    TILES.mkdir(parents=True, exist_ok=True)
    only = sys.argv[1:]
    index = {}
    for year in EDITIONS:
        src = GEOREF / f"{year}.tif"
        if not src.exists():
            print(f"MISSING {src}", file=sys.stderr)
            continue
        maxz = native_max_zoom(src)
        out = TILES / year
        if only and year not in only:
            pass
        elif (out / str(maxz)).exists():
            print(f"SKIP {year} (tiles exist)")
        else:
            print(f"=== {year}: z{MIN_ZOOM}-{maxz}", flush=True)
            subprocess.run(
                ["gdal2tiles.py", "--xyz", "-z", f"{MIN_ZOOM}-{maxz}",
                 "-r", "bilinear", "-w", "none", "--processes", "8",
                 "-q", str(src), str(out)], check=True)
        index[year] = {
            "path": f"tiles/{year}",
            "minzoom": MIN_ZOOM,
            "maxzoom": maxz,
            "bounds": wgs84_bounds(src),
        }
    with open(TILES / "index.json", "w") as f:
        json.dump(index, f, indent=1)
    print(f"wrote {TILES / 'index.json'} ({len(index)} editions)")


if __name__ == "__main__":
    main()

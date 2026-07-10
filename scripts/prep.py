#!/usr/bin/env python3
"""Normalize the hand-georeferenced source TIFs into work/georef/{year}.tif
(RGBA, EPSG:2284, tiled DEFLATE) and resize legend crops into docs/legends/.

Sources live in the parent directory (see sources/manifest.tsv); they were
georeferenced in QGIS in 2022 and already carry EPSG:2284 north-up
geotransforms, so this is band normalization (gray -> RGB, add alpha), not
real warping. A manifest source of "work:<name>" refers to a file this
pipeline produced (the 1963 edition, georeferenced by georef_1963.py).
"""
import csv
import json
import subprocess
import sys
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT.parent  # ../ = Documents/Planning/Historical Zoning
GEOREF = ROOT / "work" / "georef"
LEGENDS = ROOT / "docs" / "legends"
LEGEND_MAX_W = 1400


def manifest():
    with open(ROOT / "sources" / "manifest.tsv") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def bands(path):
    info = json.loads(subprocess.run(
        ["gdalinfo", "-json", str(path)], capture_output=True, text=True,
        check=True).stdout)
    return [b["colorInterpretation"] for b in info["bands"]]


def prep_pdf(year, src):
    """GeoPDF (the 2024 city sheet): render at 400 dpi, crop to the
    neatline (page margins outside it carry bogus georeferencing), warp
    Web Mercator -> EPSG:2284."""
    out = GEOREF / f"{year}.tif"
    if out.exists():
        print(f"SKIP {year} (exists)")
        return
    import os
    env = {**os.environ, "GDAL_PDF_DPI": "400"}
    info = json.loads(subprocess.run(
        ["gdalinfo", "-json", str(src)], capture_output=True, text=True,
        check=True, env=env).stdout)
    neat = info["metadata"][""]["NEATLINE"]
    coords = [tuple(map(float, p.split()))
              for p in neat.split("((")[1].rstrip("))").split(",")]
    xs, ys = [c[0] for c in coords], [c[1] for c in coords]
    subprocess.run(
        ["gdalwarp", "-t_srs", "EPSG:2284", "-r", "bilinear", "-dstalpha",
         "-te_srs", info["coordinateSystem"]["wkt"],
         "-te", str(min(xs)), str(min(ys)), str(max(xs)), str(max(ys)),
         "-co", "TILED=YES", "-co", "COMPRESS=DEFLATE", "-overwrite",
         str(src), str(out)], check=True, env=env)
    print(f"OK {out.name}")


def prep_raster(year, src):
    if src.suffix.lower() == ".pdf":
        prep_pdf(year, src)
        return
    out = GEOREF / f"{year}.tif"
    if out.exists():
        print(f"SKIP {year} (exists)")
        return
    interp = bands(src)
    args = ["gdalwarp", "-t_srs", "EPSG:2284", "-r", "bilinear",
            "-co", "TILED=YES", "-co", "COMPRESS=DEFLATE",
            "-co", "PHOTOMETRIC=RGB", "-overwrite"]
    tmp = None
    if interp[0] == "Gray":
        # duplicate the gray band into RGB (keep an existing alpha band)
        tmp = GEOREF / f"_{year}_rgb.vrt"
        b = ["-b", "1", "-b", "1", "-b", "1"] + \
            (["-b", "2"] if "Alpha" in interp else [])
        subprocess.run(["gdal_translate", "-q", "-of", "VRT", *b,
                        str(src), str(tmp)], check=True)
        src = tmp
    if "Alpha" not in interp:
        args.append("-dstalpha")
    subprocess.run([*args, str(src), str(out)], check=True)
    if tmp:
        tmp.unlink()
    print(f"OK {out.name}")


def prep_legend(year, src):
    out = LEGENDS / f"{year}.png"
    if out.exists():
        return
    img = cv2.imread(str(src), cv2.IMREAD_UNCHANGED)
    if img is None:
        print(f"LEGEND READ FAIL {src}", file=sys.stderr)
        return
    h, w = img.shape[:2]
    if w > LEGEND_MAX_W:
        s = LEGEND_MAX_W / w
        img = cv2.resize(img, (LEGEND_MAX_W, round(h * s)),
                         interpolation=cv2.INTER_AREA)
    cv2.imwrite(str(out), img, [cv2.IMWRITE_PNG_COMPRESSION, 9])
    print(f"OK legends/{out.name} ({out.stat().st_size // 1024} KB)")


def main():
    GEOREF.mkdir(parents=True, exist_ok=True)
    LEGENDS.mkdir(parents=True, exist_ok=True)
    only = sys.argv[1:]
    for row in manifest():
        year = row["year"]
        if only and year not in only:
            continue
        src = row["source"]
        src = GEOREF / src[5:] if src.startswith("work:") else SRC / src
        if src.exists():
            if src != GEOREF / f"{year}.tif":
                prep_raster(year, src)
        else:
            print(f"MISSING {src}", file=sys.stderr)
        if row["legend"].startswith("work:"):
            continue  # legend produced by another step (2024: sheet crop)
        leg = SRC / row["legend"]
        if leg.exists():
            prep_legend(year, leg)
        else:
            print(f"MISSING legend {leg}", file=sys.stderr)


if __name__ == "__main__":
    main()

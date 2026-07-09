#!/usr/bin/env python3
"""Build the two vector district overlays for the viewer:

  docs/data/zoning_2003.geojson — the 2003 ordinance's districts (as
      amended; in force through 2023), dissolved by zone from the local
      Zoning_2003 shapefile (../../dump/Shapefiles).
  docs/data/zoning_2024.geojson — the Feb-2024 Development Code
      districts, pulled from the city Open Data ArcGIS service and
      dissolved by zone.

Each feature carries {zone, color} — colors baked here so the viewer
stays palette-free. Palettes come from the old Observable notebook
(zoningColors2013 for the 2003 code, zoningLegendColors for 2024).
"""
import json
import subprocess
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "data"
SHP_2003 = (ROOT.parent.parent / "dump" / "Shapefiles" / "Zoning_2003" /
            "Zoning_2003.shp")
SVC_2024 = ("https://gisweb.charlottesville.org/cvgisweb/rest/services/"
            "OpenData/OpenDataLayers/MapServer/202/query")

COLORS_2003 = {
    "R-1": "#ffffaa", "R-1U": "#ffffaa", "R-1S": "#ffff00",
    "R-1SU": "#ffff00", "R-2": "#ffaa00", "R-2U": "#ffaa00",
    "R-3": "#ff5500", "UMD": "#ff5500", "UHD": "#ff5500",
    "MLTP": "#ff8866", "MR": "#ff8866", "PUD": "#55ffaa",
    "B-1": "#ffaaaa", "B-2": "#ff0000", "B-3": "#aa0000",
    "D": "#ff55ff", "DE": "#ff55ff", "DN": "#ff55ff", "WME": "#ff55ff",
    "WMW": "#ff55ff", "WMN": "#ff55ff", "WMS": "#ff55ff", "CC": "#ff55ff",
    "URB": "#ff55ff", "HS": "#ff55ff", "HW": "#ff55ff", "NCC": "#ff55ff",
    "CH": "#ff55ff", "SS": "#ff55ff", "CD": "#ff55ff", "WSD": "#ff55ff",
    "WS": "#ff55ff", "ES": "#ff55ff",
    "M-I": "#aaaaaa", "IC": "#aaaaaa",
}
COLORS_2024 = {
    "R-A": "#F1E3C0", "R-B": "#FEE33A", "R-C": "#E5BF22",
    "RN-A": "#D9C79A",
    "RX-3": "#F9A424", "RX-5": "#C67B29",
    "CX-3": "#BC7562", "CX-5": "#954B37", "CX-8": "#683527",
    "NX-3": "#BC62A1", "NX-5": "#A04484", "NX-8": "#81376A",
    "NX-10": "#642B52", "DX": "#471F3A",
    "IX-5": "#8BA4BB", "IX-8": "#6586A4",
    "CV": "#88C29C", "CM": "#58897B",
}
FALLBACK = "#bbbbbb"


def strip_2003(zone):
    """Historic (H) / corridor (C) suffixed districts share the base
    district's color (the notebook's zone_strip_historic_map)."""
    z = zone.strip()
    for suffix in ("HC", "H", "C"):
        base = z.removesuffix(suffix)
        if base != z and base in COLORS_2003:
            return base
    return z


def dissolve(src, zone_field, layer):
    """zone -> WGS84 geojson features, dissolved + lightly simplified."""
    tmp = OUT / "_dissolve_tmp.geojson"
    tmp.unlink(missing_ok=True)
    subprocess.run([
        "ogr2ogr", "-f", "GeoJSON", "-t_srs", "EPSG:4326",
        # -simplify applies in TARGET units — degrees here (1e-5 deg ~ 3.6 ft)
        "-simplify", "0.00001", "-lco", "COORDINATE_PRECISION=5",
        "-dialect", "sqlite",
        "-sql", f'SELECT "{zone_field}" AS zone, ST_Union(geometry) AS '
                f'geometry FROM "{layer}" GROUP BY "{zone_field}"',
        str(tmp), str(src)], check=True)
    feats = json.loads(tmp.read_text())["features"]
    tmp.unlink()
    return feats


def build_2003():
    feats = dissolve(SHP_2003, "ZONE", "Zoning_2003")
    for f in feats:
        zone = f["properties"]["zone"] or ""
        base = strip_2003(zone)
        f["properties"] = {"zone": zone,
                           "color": COLORS_2003.get(base, FALLBACK)}
    write(feats, OUT / "zoning_2003.geojson")


def fetch_2024():
    """Page through the ArcGIS service (maxRecordCount 2000)."""
    feats, offset = [], 0
    while True:
        url = (f"{SVC_2024}?where=1%3D1&outFields=CurrentZoning"
               f"&outSR=4326&f=geojson&resultOffset={offset}")
        with urllib.request.urlopen(url) as r:
            page = json.load(r)
        feats += page["features"]
        if not page.get("exceededTransferLimit") and \
                not page.get("properties", {}).get("exceededTransferLimit"):
            break
        offset += len(page["features"])
    return feats


def build_2024():
    raw = OUT / "_zoning_2024_raw.geojson"
    raw.write_text(json.dumps(
        {"type": "FeatureCollection", "features": fetch_2024()}))
    feats = dissolve(raw, "CurrentZoning", "_zoning_2024_raw")
    raw.unlink()
    for f in feats:
        zone = (f["properties"]["zone"] or "").strip()
        f["properties"] = {"zone": zone,
                           "color": COLORS_2024.get(zone, FALLBACK)}
    write(feats, OUT / "zoning_2024.geojson")


def write(feats, path):
    path.write_text(json.dumps(
        {"type": "FeatureCollection", "features": feats},
        separators=(",", ":")))
    grey = [z for f in feats
            if f["properties"]["color"] == FALLBACK
            for z in [f["properties"]["zone"]]]
    print(f"{path.name}: {len(feats)} zones, "
          f"{path.stat().st_size / 1e6:.1f} MB"
          + (f"  UNMAPPED: {grey}" if grey else ""))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    build_2003()
    build_2024()


if __name__ == "__main__":
    main()

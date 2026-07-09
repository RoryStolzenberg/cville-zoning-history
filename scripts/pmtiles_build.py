#!/usr/bin/env python3
"""Pack each edition's XYZ tile tree into a single .pmtiles archive.

docs/tiles/{year}/z/x/y.png  ->  docs/tiles/{year}.pmtiles

PMTiles serves the whole pyramid from one static file via HTTP range
requests — no tile server. Route: mb-util (dir -> mbtiles) then the
go-pmtiles CLI (mbtiles -> pmtiles).

Requires: .venv/bin/mb-util, pmtiles binary (PMTILES env or on PATH).
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TILES = ROOT / "docs" / "tiles"
MBUTIL = ROOT / ".venv" / "bin" / "mb-util"
PMTILES = os.environ.get("PMTILES", "pmtiles")


def main():
    index = json.loads((TILES / "index.json").read_text())
    only = sys.argv[1:]
    for year, meta in sorted(index.items()):
        if only and year not in only:
            continue
        src = TILES / year
        out = TILES / f"{year}.pmtiles"
        if out.exists():
            print(f"SKIP {year} (exists)")
            continue
        if not src.is_dir():
            print(f"MISSING {src}", file=sys.stderr)
            continue
        b = meta["bounds"]
        (src / "metadata.json").write_text(json.dumps({
            "name": f"Charlottesville Zoning {year}",
            "format": "png",
            "minzoom": str(meta["minzoom"]),
            "maxzoom": str(meta["maxzoom"]),
            "bounds": f"{b[0]},{b[1]},{b[2]},{b[3]}",
            "type": "overlay",
        }))
        with tempfile.TemporaryDirectory() as td:
            mb = Path(td) / f"{year}.mbtiles"
            subprocess.run([str(MBUTIL), "--silent", "--image_format=png",
                            "--scheme=xyz", str(src), str(mb)], check=True)
            subprocess.run([PMTILES, "convert", str(mb), str(out)],
                           check=True)
        (src / "metadata.json").unlink()
        print(f"OK {out.name}  {out.stat().st_size / 1e6:.1f} MB", flush=True)


if __name__ == "__main__":
    main()

# Charlottesville Historical Zoning Map Viewer

Static MapLibre web viewer for Charlottesville's zoning map editions
(1929–2020) plus vector district overlays (2003 code, current 2024 code).
Fork of the Arlington GLUP viewer
(~/Documents/GitHub/sandbox-2026/arlington-glup); replaces the old
Mapbox/Observable notebook
(observablehq.com/@rorystolzenberg/historical-zoning-maps-charlottesville).

## Memory files
- `memory/2026-07-09-port-notes.md` — source inventory, port decisions
  (READ FIRST in new sessions)

## Layout
- Sources live in the PARENT directory (`../` = Documents/Planning/
  Historical Zoning) — hand-georeferenced GeoTIFFs (QGIS, EPSG:2284
  NAD83 / Virginia South ftUS) + legend crops. `sources/manifest.tsv`
  maps year → files there.
- `scripts/` — prep.py (warp + legend prep), georef_1963.py, vectors.py,
  tiles.py, pmtiles_build.py
- `work/` — georef/ (normalized RGBA GeoTIFFs), qa/ (blends)
- `docs/` — static viewer for GitHub Pages (tiles/*.pmtiles committed,
  XYZ dirs gitignored; legends/ per-year legend images; data/ vector
  overlays)
- Python: `.venv/bin/python` (opencv, mbutil); GDAL via CLI tools

# Charlottesville Historical Zoning Maps

Browse every zoning map edition of Charlottesville, VA — 1929, 1939,
1949, 1958, 1963, 1976, 1991, 2003 and 2020 — georeferenced and
overlaid on a modern basemap, plus vector district layers for the 2003
ordinance (in force through 2023) and the current (Feb 2024)
Development Code.

Static site: MapLibre GL + PMTiles (no tile server). Timeline, opacity
slider, side-by-side swipe compare, per-year printed legends, and
clickable vector districts.

Successor to the Observable notebook
[Historical Zoning Maps of Charlottesville](https://observablehq.com/@rorystolzenberg/historical-zoning-maps-charlottesville);
viewer forked from the
[Arlington GLUP history viewer](https://github.com/RoryStolzenberg/arlington-glup-history).

## Pipeline

Source scans were cleaned and hand-georeferenced in QGIS (EPSG:2284);
the 1963 edition was georeferenced here (`scripts/georef_1963.py`).

    scripts/prep.py           # normalize sources -> work/georef, legends
    scripts/georef_1963.py    # the one edition QGIS never got
    scripts/vectors.py        # 2003 + 2024 district GeoJSONs
    scripts/tiles.py          # XYZ pyramids per edition
    scripts/pmtiles_build.py  # XYZ -> docs/tiles/{year}.pmtiles

Sources live in the parent directory (see `sources/manifest.tsv`);
map scans originally from City records / cvillepedia.

Local preview: `npx serve docs` (needs HTTP range request support;
`python -m http.server` won't work with pmtiles).

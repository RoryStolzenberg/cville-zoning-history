# Cville zoning viewer — port notes (2026-07-09)

Porting the 2022 Observable/Mapbox viewer to the Arlington GLUP viewer
stack (static MapLibre + pmtiles, GitHub Pages).

PUBLISHED (2026-07-09):
- Live: https://rorystolzenberg.github.io/cville-zoning-history/
- Repo: github.com/RoryStolzenberg/cville-zoning-history (Pages from
  /docs on main; gh account RoryStolzenberg — `gh auth switch`).
- pmtiles 117 MB total (pngquant 414→181 MB XYZ before packing).
- Gotcha: repo path contains a space → pip script shebangs break;
  invoke .venv scripts via `.venv/bin/python <script>` (see
  pmtiles_build.py mb-util call).
- gdal2tiles: system GDAL 3.4 + venv numpy 2 print a scary osgeo
  _ARRAY_API traceback that is NON-FATAL (tiles verified complete).

## Source inventory (../ = Documents/Planning/Historical Zoning)
All hand-georeferenced in QGIS (2022), EPSG:2284 (VA South ftUS),
north-up geotransforms — no warping work needed, just normalization.
Chosen per year (see sources/manifest.tsv):
- 1929: RGB 11.3 ft/px (small scan — best available)
- 1939: gray 3.1 ft/px ("superseded 1949" print)
- 1949: gray 3.1 ft/px
- 1958: gray+alpha 11.2 ft/px (full-res 63 MB PNG exists but was never
  georeferenced; the "smallerer" one was)
- 1963: NOT georeferenced in 2022 — we do it now ("pg 2 - cleaned.png").
  Never appeared in the Observable notebook.
- 1976: RGB 4.1 ft/px
- 1991: RGB 4.7 ft/px — the georeferenced file is "1991 Zoning
  Map_modified.tif" (no _georeferenced suffix; don't be fooled)
- 2003: use "_georeferenced_deflate.tif" (lossless full-res 2.5 ft/px;
  the _smallerer/_smallererer JPEG variants are degraded, _smallererer
  even lost its CRS)
- 2020: RGB 4.7 ft/px

Legend crops exist for every year incl. 1963 (PNG, some 1–3 MB —
resized to ≤900 px wide into docs/legends/).

## Vector overlays (user-directed, replaces notebook's stale "Current")
- 2003 code (in force through 2023):
  ../../dump/Shapefiles/Zoning_2003 shapefile. Color palette =
  notebook's zoningColors2013 + zone_strip_historic_map (historic/
  corridor suffixes H/C stripped to base zone).
- Current (Feb 2024 Development Code): pull from Cville Open Data
  Portal (like the notebook did for parcels). Palette = notebook's
  zoningLegendColors (R-A…DX/IX/CV/CM).

## Round 2 (2026-07-10, user-driven changes)
- User caught 1963 ~1000 ft off at the Rivanna hook — the v1
  "verification" searched only ±480 ft so every patch locked onto the
  nearest WRONG street. LESSON: a bounded-search displacement metric is
  blind to offsets beyond its bound; verify with capture radius >> the
  plausible error.
- Phase correlation is fully unusable on the 1963 hatched litho (offset
  AND response are hatch-period aliases: readings -97 to +4200 ft on
  warps that wide-capture placed within 100 ft). Fine on all other
  editions.
- v2 pipeline (georef_1963.py): TIGER intersection geo coords
  (spatialite ST_Intersection of named roads) + 2 hand-read sheet px
  anchors -> similarity seed -> iterative TIGER patch snap (translation
  passes via displacement-histogram MODE; RANSAC similarity "polish"
  REJECTED — clustered inliers drag in bogus scale) -> marginal outlier
  filter (NOT RANSAC: it picks a spatial cluster and the fit
  extrapolates its local distortion) -> order-2 LSQ warp (NOT TPS:
  exact interpolation bakes in snap noise). Verified median (-32,+48)
  ft wide-capture; trusted-editions floor is ±30 ft.
- Vector overlays REMOVED (user prefers real map sheets; compare
  covers it). 2024 edition added from the city GeoPDF
  (ZoningMap_2024.pdf, Web Mercator + neatline, prep.py renders at
  400 dpi + crops to neatline); verified -1,-2 ft vs 2020.
- All 10 editions verified vs TIGER: <=25 ft except 1939/1949 (resp
  ~0, offsets 137-189 ft, hatched B&W like 1963 — phase corr may be
  aliasing there too; NOT yet wide-capture-verified. TODO check).
- Legend panel embiggened (1400px assets, wider panel, click ->
  lightbox). 2024 legend crop must come from the WARPED tif frame,
  not the raw PDF page (coords differ).

## 1963 georeferencing v1 (2026-07-09) — what failed (kept for lessons)
- FAILED: SIFT (hatch texture → degenerate homography with repeated dst
  points — check pairwise dst distances, not inlier count/rms!);
  gradient/street/blob phase-corr sweeps; bbox-template coarse. Global
  phase-corr offsets on hatched lithos are HATCH-PERIOD ALIASES —
  inconsistent across reference years is the tell.
- WORKED: 2-point manual seed (Meadow Ck/Rivanna confluence + US-250
  crossing, gridded-crop workflow) → 2x tight-search grid template
  matching, SIMILARITY fit only → TPS. Verify = median LOCAL patch
  displacement vs 3 trusted editions (trusted-pair noise floor: median
  ±30 ft, MAD 150-270 ft). Accepted at median 80-210 ft — the litho's
  own drafting distortion; flag to user, redo by hand in QGIS if it
  bugs anyone.

## Old notebook facts
- 8 editions as Mapbox-hosted tilesets under rorystolzenberg.*;
  legends hotlinked from cvillepedia.org (now committed locally).
- Mapbox token + tilesets stay live but nothing here depends on them.

## Decisions
- Viewer chrome forked from GLUP: timeline, opacity, swipe compare,
  hash state. Parcel click-history DROPPED (no per-parcel
  classification here — 1929–58 are B&W hatched prints).
- Legend panel shows the per-year legend IMAGE (not generated chips).
- go-pmtiles binary was lost with the old session scratchpad —
  re-download from github.com/protomaps/go-pmtiles releases.

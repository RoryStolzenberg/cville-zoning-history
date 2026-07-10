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
- CACHE TRAP: after redeploying changed pmtiles, the user's browser
  keeps serving cached range requests — a "still broken" report right
  after a fix may be stale tiles. Check md5(live) == md5(local) and a
  fresh headless browser BEFORE re-diagnosing; ask for hard refresh.
- work/ref/ref_water.tif (make_ref.sh): Rivanna water polygons as a
  second reference — the 1963+ city boundary follows the river, so it
  verifies sheet east edges where 1963-era roads are too sparse.
  Ink-vs-water overlay at the hook shows the accepted 1963 warp within
  ~150-250 ft there (sheet's own county-area drafting is the limit).

## Round 3 (2026-07-10): the v2 warp was ~2,200 ft EAST everywhere
- Rory annotated a truth render (purple box = TIGER hook, green box =
  drawn hook) and said "you basically just have to translate it left".
  His green box was CLIPPED at his screenshot edge, so the box-to-box
  delta (-1062 ft) was a LOWER BOUND — the real total was ~-2170.
- ROOT CAUSE of both shipped failures: the two hand-read v2 anchors
  were misidentified sheet intersections (hatch lookalikes ~2200 ft
  west of the real ones), and every lattice-snap + street-metric
  "verification" locked one alias over. Street-grid metrics CANNOT
  catch whole-lattice aliases — they share the alias. Proof render:
  the old warp put open rural land at UVA's geo location.
- THE FIX (in georef_1963.py): pure translation HOOK_SHIFT =
  (-2170 E, +120 N) ft after the order-2 warp, pinning the drawn
  Rivanna meander (city-boundary band on the west bank) to TIGER
  water. Measured two ways, agreeing within 80 ft: (a) the meander's
  sharp SW V-tip, drawn vs TIGER; (b) translation-only ICP of the
  extracted band vs the water outline near the hook. Confirmed by
  1958 ink-to-ink patches (the only 2 with ncc>0.3 read <350 ft).
- The cand3 river-pin TPS (street GCPs + river pins) was WRONG-headed:
  it pinned the river correctly while holding the aliased street GCPs,
  shearing the map unreadably. When a rubber sheet needs ~2000 ft of
  local pull, suspect the GLOBAL placement first.
- Registering a user screenshot to geo coords by fitting its saturated
  reference linework (the red water outline) to the known ref raster
  works well (~45 ft residual) — good trick for turning user
  annotations into measurements. Watch for boxes clipped at image
  edges.
- Translation-only ICP diverges from a >1000 ft init when the moving
  curve includes non-river boundary stretches; restrict to the hook
  neighborhood AND init from a point feature (the V-tip) first.

## Round 4 (2026-07-10): "scaling seems off" -> text-anchor method
- After the hook-lock translation the user still saw scale error at
  the river. The instrument that finally worked: NAMED-INTERSECTION
  anchors — spatialite ST_Intersection gives TIGER geo for two named
  streets; render the warp there with a crosshair + TIGER roads; READ
  THE SHEET'S PRINTED STREET NAMES to find the drawn intersection.
  Street names cannot alias. Everything else on this sheet can.
- Result: core/N/W/S were fine; two real pockets — Locust Grove (NE)
  ~650 ft north, Belmont (SE) ~380 ft south (trapezoidal east-side
  stretch = his "scaling"). Fixed with TPS through measured knots + a
  ZERO RING of knots around the unmeasured margins so the correction
  stays local. Order-2 on the same knots extrapolates to -12,000 ft at
  sheet corners — never fit an unconstrained polynomial to local
  corrections.
- gradient-NCC displacement fields vs 1958 are only usable as
  MUTUALLY-CONSISTENT CLUSTERS; isolated matches (even ncc 0.5) can be
  false locks, neighbors 1,000 ft apart "measuring" 2,000 ft apart is
  the tell. Also: 1959-63 annexation areas were redrafted between the
  1958/1963 editions — no valid correspondence there at all.
- The wide-capture street verify in georef_1963.py was demoted to
  REPORT ONLY: it certified the 2,200-ft-off warp and then flagged the
  text-verified warp at (+352,+144). gdaltransform -tps probing showed
  the actual TPS deformation downtown was <50 ft — the metric, not the
  warp, was wrong. Acceptance is now the _c*.png crosshair renders +
  _corr_hook.png water fill.
- Known residuals shipped: W Main x 7th SW and Avon x Levy read
  ~200-330 ft off (pre-existing engraving distortion, present before
  and after the correction; same class as the 1958 hand-warp's local
  wobble). More hand knots would stack cleanly if it ever matters.

## Round 5 (2026-07-10): meander pins shipped; Locust Grove punted
- User caught two defects I'd presented as clean: (1) the drawn
  meander is ENGRAVED FATTER than the real one — red sliced through
  the peninsula; (2) Locust Grove's drawn grid is a different SIZE
  than TIGER's (scale mismatch, not offset).
- (1) FIXED: band-to-water river pins (identity-safe — no street
  naming). CRITICAL: raw dense pins (22 @ ~800 ft) FOLD the TPS —
  swirl/bullseye artifacts appear near steep pin gradients. Shipping
  set = chain-median smoothed, >=1200 ft spacing, gradient vs nearby
  knots capped at 50% of separation. 7 pins. Meander now ~100-200 ft.
- (2) UNRESOLVED and reverted after THREE failed iterations: each
  round I confidently identified drawn Locust/St Charles/Sheridan/
  North differently and warped the map to the new misreading. The
  tell: the "residual" at North Ave stayed ~300 ft after every
  correction — I was re-identifying the next street over each time.
  The drawn pocket also has a real BEARING divergence (drawn Locust
  splays from TIGER Locust northward). Renders are at engraving
  resolution — zooming can't improve my label reads. NEXT STEP: ask
  Rory to identify the wide drawn diagonal in
  work/qa/_pocket_colors.png (one glance for him); his answer converts
  mechanically into corridor pins (traced centerline -> nearest point
  on that street's TIGER raster, then smooth/thin/cap as above).
- Composing corrections across TPS generations: total(P) =
  TPS_old_deform(P) [gdaltransform -tps on the GCP file] + residual
  measured on the new render. Exact at measured points; lets every
  rebuild stay a SINGLE TPS from the shift-only stage (2 resamples).

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

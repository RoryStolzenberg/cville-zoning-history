#!/usr/bin/env python3
"""SUPERSEDED 2026-07-10: Rory hand-georeferenced the 1963 sheet in QGIS
("1963 Zoning Map pg 2 - cleaned_modified.tif" + .points in ../), and
the manifest now feeds that file through prep.py like every other
edition. This script and its lessons are kept for the record — the
automated result reached ~100-200 ft but took six rounds of correction
and two user interventions to get there; ~30 hand GCPs beat it in an
evening.

Original goal: georeference the 1963 zoning map (the one edition the
2022 QGIS batch skipped) directly against the TIGER street network.

History (details in memory/2026-07-09-port-notes.md): every automated
coarse-alignment route false-locked on this hatched litho — SIFT
(degenerate many-to-one homographies), gradient/street/blob
phase-correlation sweeps, template sweeps. A first shipped attempt
(2-point river seed + fine grid matching vs the 1958 sheet) LOOKED
verified but was ~1,000 ft off with TPS distortion: the verification
searched only ±480 ft, so every patch locked onto the nearest wrong
street — bounded-search displacement metrics are blind to offsets
beyond their bound. The user caught it at the Rivanna hook.

What works, and what this script does:
 1. Seed similarity from TWO hand-verified anchors: sheet px of street
    intersections whose geo coordinates come from the TIGER shapefile
    itself (spatialite ST_Intersection of named roads — no eyeballing
    on the modern side).
 2. Resample the sheet onto the TIGER 16 ft/px grid; iteratively snap
    large street-mask patches (2400 ft context) to the TIGER raster:
    two translation-only passes using the displacement HISTOGRAM MODE
    (robust to the periodic grid's false peaks), then a similarity
    RANSAC polish, then a final residual harvest.
 3. Final snap pairs become GCPs -> order-2 warp at full resolution.
 4. Hook-lock translation: the street-lattice machinery above is blind
    to whole-lattice aliases (it shipped ~2,200 ft east twice); the
    drawn Rivanna meander pinned to the TIGER water polygon fixes the
    absolute placement. See the HOOK_SHIFT comment in main().
 5. Verify: wide-capture (±2560 ft) local street displacement vs TIGER.

Outputs: work/georef/1963.tif, work/qa/1963_vs_tiger.jpg
"""
import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT.parent / "1963 Zoning Map pg 2 - cleaned.png"
GEOREF = ROOT / "work" / "georef"
REF = ROOT / "work" / "ref" / "ref_streets.tif"
QA = ROOT / "work" / "qa"

# TIGER grid (see make_ref.sh)
RX0, TR, RY1 = 11470000, 16.0, 3922000

# Anchors: full-res sheet px <-> EPSG:2284 geo of the same intersection.
# Geo from TIGER: ogrinfo -dialect sqlite ST_Intersection of the named
# roads. Sheet px read off gridded crops (street names printed on sheet).
ANCHORS_PX = np.float64([[3272, 1752],    # Rugby Ave x Rose Hill Dr
                         [2437, 2157]])   # University Ave x Rugby Rd
ANCHORS_GEO = np.float64([[11487610.6, 3904187.4],
                          [11481921.3, 3901055.9]])

N_GCPS = 45


def run(cmd, **kw):
    subprocess.run([str(c) for c in cmd], check=True, **kw)


def streets(img, sigma=3.0):
    """Soft mask of bright thin structures — the street network."""
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (13, 13))
    th = cv2.morphologyEx(img, cv2.MORPH_TOPHAT, k)
    _, bw = cv2.threshold(th, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    soft = cv2.GaussianBlur(bw.astype(np.float32) / 255, (0, 0), sigma)
    return soft / (soft.max() + 1e-6)


def sheet_mask(gray, sigma=3.0):
    """Dual-polarity street mask for the 1963 sheet.

    Inside the hatched city, streets are BRIGHT lines on dark fill
    (top-hat). Outside the boundary, county roads are thin DARK lines
    on white paper (black-hat) — without them the fit has zero control
    east of the city and the polynomial extrapolates freely at the
    Rivanna (the second user-caught misplacement, ~1500 ft at the
    hook). Black-hat responses count only where local ink density is
    low, so city hatching can't flood the mask."""
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (13, 13))
    bright = streets(gray, sigma)
    bh = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, k)
    _, bw = cv2.threshold(bh, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    ink = cv2.GaussianBlur((255 - gray).astype(np.float32), (0, 0), 25)
    outside = (ink < 40).astype(np.float32)
    dark = cv2.GaussianBlur(bw.astype(np.float32) / 255 * outside,
                            (0, 0), sigma)
    dark /= dark.max() + 1e-6
    return np.maximum(bright, dark)


def raster_bbox(path):
    info = json.loads(subprocess.run(
        ["gdalinfo", "-json", str(path)], capture_output=True, text=True,
        check=True).stdout)
    (x0, y0), (x1, y1) = info["cornerCoordinates"]["upperLeft"], \
        info["cornerCoordinates"]["lowerRight"]
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)


def on_grid(tif, bbox):
    x0, y0, x1, y1 = bbox
    grid = GEOREF / "_grid_tmp.tif"
    grid.unlink(missing_ok=True)
    run(["gdalwarp", "-q", "-te", x0, y0, x1, y1, "-tr", TR, TR,
         "-r", "bilinear", tif, grid])
    img = cv2.imread(str(grid), cv2.IMREAD_GRAYSCALE)
    grid.unlink(missing_ok=True)
    return img


def snap(tst, refm, P=75, SR=60, minsc=0.2, stride=90):
    """TIGER patches (2P px context) template-matched into the sheet
    mask; returns (sheet grid px, TIGER grid px) correspondences."""
    a_pts, b_pts = [], []
    for cy in range(P + SR, refm.shape[0] - P - SR, stride):
        for cx in range(P + SR, refm.shape[1] - P - SR, stride):
            tpl = refm[cy - P:cy + P, cx - P:cx + P]
            if tpl.std() < 0.08:
                continue
            wnd = tst[cy - P - SR:cy + P + SR, cx - P - SR:cx + P + SR]
            if wnd.std() < 0.05:
                continue
            res = cv2.matchTemplate(wnd, tpl, cv2.TM_CCOEFF_NORMED)
            _, score, _, loc = cv2.minMaxLoc(res)
            if score < minsc:
                continue
            b_pts.append([cx, cy])
            a_pts.append([cx + loc[0] - SR, cy + loc[1] - SR])
    return np.float32(a_pts), np.float32(b_pts)


def main():
    QA.mkdir(parents=True, exist_ok=True)
    if not REF.exists():
        sys.exit("work/ref/ref_streets.tif missing — run scripts/make_ref.sh")

    # seed similarity sheet px -> geo (complex fit through the anchors;
    # both frames y-down, so N is negated)
    zpx = ANCHORS_PX[:, 0] + 1j * ANCHORS_PX[:, 1]
    zgeo = ANCHORS_GEO[:, 0] - 1j * ANCHORS_GEO[:, 1]
    a = (zgeo[1] - zgeo[0]) / (zpx[1] - zpx[0])
    b = zgeo[0] - a * zpx[0]
    print(f"seed: {abs(a):.3f} ft/px, rot {np.degrees(np.angle(a)):+.2f} deg",
          flush=True)

    ref = cv2.imread(str(REF), cv2.IMREAD_GRAYSCALE)
    refsoft = cv2.GaussianBlur(ref.astype(np.float32) / 255, (0, 0), 2.5)
    refsoft /= refsoft.max() + 1e-6
    img = cv2.imread(str(SRC), cv2.IMREAD_GRAYSCALE)

    # Mg2s: TIGER grid px -> sheet px, through the seed
    c = 1.0 / a
    d = (complex(RX0, -RY1) - b) * c
    Mg2s = np.float64([[(c * TR).real, (c * 1j * TR).real, d.real],
                       [(c * TR).imag, (c * 1j * TR).imag, d.imag]])
    gh, gw = refsoft.shape
    warp0 = cv2.warpAffine(img, cv2.invertAffineTransform(Mg2s), (gw, gh))

    A_upd = np.eye(3)
    tst = streets(warp0)
    # translation passes: histogram mode beats RANSAC here — the
    # periodic grid floods the raw matches with one-block-off pairs
    for it, (SR, minsc) in enumerate([(90, 0.18), (30, 0.2)]):
        a_pts, b_pts = snap(tst, refsoft, SR=SR, minsc=minsc)
        disp = b_pts - a_pts
        H, xe, ye = np.histogram2d(disp[:, 0], disp[:, 1],
                                   bins=np.arange(-SR - 4, SR + 5, 8))
        i, j = np.unravel_index(np.argmax(H), H.shape)
        sel = (np.abs(disp[:, 0] - (xe[i] + 4)) < 12) & \
              (np.abs(disp[:, 1] - (ye[j] + 4)) < 12)
        shift = disp[sel].mean(axis=0)
        print(f"pass{it}: {len(a_pts)} matches -> shift "
              f"({shift[0] * TR:+.0f},{shift[1] * TR:+.0f})ft", flush=True)
        A_upd = np.float64([[1, 0, shift[0]], [0, 1, shift[1]],
                            [0, 0, 1]]) @ A_upd
        tst = streets(cv2.warpAffine(warp0, A_upd[:2], (gw, gh)))
    # NO similarity polish here: a RANSAC similarity fitted to the few
    # (clustered) high-score snaps drags in a bogus ~1% scale change.
    # The seed scale/rotation come from surveyed anchors and are more
    # trustworthy; the order-2 GCP fit below absorbs true residuals.
    # final residual harvest -> GCP pairs
    a_pts, b_pts = snap(tst, refsoft, SR=15, minsc=0.22, stride=70)
    r = b_pts - a_pts
    print(f"residual: n={len(r)} median "
          f"({np.median(r[:, 0]) * TR:+.0f},{np.median(r[:, 1]) * TR:+.0f})ft "
          f"mad ({np.median(np.abs(r[:, 0])) * TR:.0f},"
          f"{np.median(np.abs(r[:, 1])) * TR:.0f})ft", flush=True)

    # Marginal outlier rejection only (deviation from the median
    # displacement). RANSAC is the WRONG filter here: it selects a
    # spatially clustered consensus and the polynomial then
    # extrapolates that cluster's local distortion across the sheet.
    # Order-2 LSQ over the full spread averages the ~160 ft snap noise
    # while absorbing smooth paper/lens distortion (TPS would
    # interpolate the noise exactly — local wobble).
    disp = b_pts - a_pts
    med = np.median(disp, axis=0)
    mad = np.median(np.abs(disp - med), axis=0)
    keep = (np.abs(disp - med) <= 3 * mad + 2).all(axis=1)
    print(f"gcp filter: {int(keep.sum())}/{len(a_pts)} pairs survive",
          flush=True)
    a_pts, b_pts = a_pts[keep], b_pts[keep]
    gcps = []
    Ainv = np.linalg.inv(A_upd)
    for (ax, ay), (bx, by) in zip(a_pts, b_pts):
        g1 = Ainv @ np.array([ax, ay, 1.0])
        sx, sy = (Mg2s @ np.array([g1[0], g1[1], 1.0]))[:2]
        gx, gy = RX0 + bx * TR, RY1 - by * TR
        gcps += ["-gcp", f"{sx:.2f}", f"{sy:.2f}", f"{gx:.2f}", f"{gy:.2f}"]
    n = len(gcps) // 5
    order = "2"
    print(f"{n} GCPs -> order {order}", flush=True)
    if n < 30:
        sys.exit("insufficient GCPs")

    tmp = GEOREF / "_1963_gcp.tif"
    out = GEOREF / "1963.tif"
    run(["gdal_translate", "-q", "-a_srs", "EPSG:2284", *gcps, SRC, tmp])
    run(["gdalwarp", "-q", "-order", order, "-t_srs", "EPSG:2284",
         "-r", "bilinear", "-dstalpha", "-co", "COMPRESS=DEFLATE",
         "-co", "TILED=YES", "-overwrite", tmp, out])
    tmp.unlink()

    # ---- hook-lock translation ------------------------------------
    # The two hand-read anchors above were misidentified on the sheet
    # (the hatch is full of lookalike intersections), and every
    # downstream lattice snap then locked one alias over, leaving the
    # whole order-2 warp displaced ~2,200 ft east — street-grid metrics
    # cannot detect this class of error because they share the alias.
    # The Rivanna hook is the one alias-free feature: the drawn meander
    # (thick city-boundary band on the west bank, river channel just
    # east of it) pins to the TIGER water polygon. The correspondence
    # was measured two independent ways — the meander's sharp SW V-tip
    # (drawn vs TIGER) and a translation-only ICP of the extracted
    # boundary band against the water outline near the hook — agreeing
    # within 80 ft. Pure translation: the engraving needs no local
    # warping (a TPS that pinned the river while keeping the aliased
    # street GCPs sheared the map unreadably), and the shifted sheet
    # then matches the hand-georeferenced 1958 sibling within ~100-350
    # ft at every patch the two editions' inks correlate on.
    HOOK_SHIFT_E, HOOK_SHIFT_N = -2170.0, 120.0   # ft, EPSG:2284
    info = json.loads(subprocess.run(
        ["gdalinfo", "-json", str(out)], check=True,
        capture_output=True, text=True).stdout)
    (ulx, uly), (lrx, lry) = (info["cornerCoordinates"]["upperLeft"],
                              info["cornerCoordinates"]["lowerRight"])
    run(["gdal_edit.py", "-a_ullr", ulx + HOOK_SHIFT_E, uly + HOOK_SHIFT_N,
         lrx + HOOK_SHIFT_E, lry + HOOK_SHIFT_N, out])

    # ---- residual TPS correction ----------------------------------
    # After the hook-lock, six named intersections were located on the
    # warp by READING THE SHEET'S PRINTED STREET NAMES (alias-proof,
    # unlike any grid metric) against TIGER intersection coordinates.
    # Core/N/W/S were within reading precision, but two margin pockets
    # carry real drafting/warp distortion: Locust Grove (NE) sits
    # ~650 ft north, Belmont (SE) ~380 ft south (both corroborated by
    # mutually-consistent gradient-NCC patches against the trusted
    # hand-georeferenced 1958 sibling). Knots below = (geo E, geo N,
    # correction E ft, correction N ft) applied via TPS; the zero ring
    # holds every unmeasured margin in place so the correction stays
    # local instead of extrapolating (order-2 fits reach -12,000 ft at
    # the sheet corners on these same knots — do not "simplify" this
    # back to a polynomial).
    KNOTS = [
        (11487611, 3904187,    0,    0),   # Rose Hill Dr x Rugby Ave
        (11485570, 3901813,    0,    0),   # Preston Ave x Grady Ave
        (11486108, 3897086,    0,    0),   # Cherry Ave x Ridge St
        (11492514, 3897676, -100,  120),   # Meade Ave x Chesapeake St
        (11494016, 3900624,  -40, -664),   # Locust Grove pocket
        (11494176, 3898544,  -48, -280),   # NE-to-hook transition
        (11491296, 3893904,   64,  392),   # Belmont
        (11492256, 3893904,   56,  376),
        (11493056, 3893904,   48,  376),
        (11493056, 3894864,   72,  376),
        (11494016, 3893904, -160,  208),   # SE edge
        # River pins: the drawn city-boundary band snapped to the TIGER
        # water outline (band extraction + nearest-boundary, identity-
        # safe — no street naming involved), then chain-median smoothed,
        # thinned to >=1200 ft spacing, and gradient-capped against the
        # Locust Grove knot. The raw 22-pin set folds the TPS (swirl
        # artifacts NW of the meander) — do not densify without the
        # smoothing/thinning/gradient steps.
        (11496232, 3903280,    8, -293),
        (11495752, 3901608,  217, -559),
        (11494528, 3900560,  123, -758),
        (11494120, 3899208,  130, -423),
        (11496696, 3898528,   10, -315),
        (11495272, 3898144,  -29, -177),
        (11496968, 3897328, -253,  -31),
        # Locust Grove spine pins: the drawn Locust Ave centerline
        # (identity confirmed by Rory — it is the wide diagonal the
        # St Charles TIGER line was riding, NOT the street the Locust
        # TIGER line crossed) pinned to TIGER Locust Ave. The vectors
        # grow along the street because the drawn neighborhood is
        # rotated/oversized relative to reality; spine pins encode the
        # rotation. South fork value is a junction-to-junction read at
        # the labeled EAST HIGH intersection (captures along-street
        # displacement); the rest are perpendicular snaps.
        (11491127, 3899474, -116, -371),
        (11491610, 3900740,  126, -446),
        (11492210, 3901820,  175, -789),
        (11493200, 3903050,  320, -921),
        (11478000, 3906000, 0, 0), (11484000, 3909000, 0, 0),  # zero ring
        (11491000, 3908500, 0, 0), (11497000, 3905500, 0, 0),
        (11498500, 3901000, 0, 0), (11498000, 3895500, 0, 0),
        (11496500, 3890500, 0, 0), (11490000, 3888500, 0, 0),
        (11483000, 3889500, 0, 0), (11477000, 3893500, 0, 0),
        (11475500, 3899500, 0, 0),
    ]
    ulx2, uly2 = ulx + HOOK_SHIFT_E, uly + HOOK_SHIFT_N
    ps = info["geoTransform"][1]
    cgcps = []
    for gx, gy, dx, dy in KNOTS:
        cgcps += ["-gcp", f"{(gx - ulx2) / ps:.2f}", f"{(uly2 - gy) / ps:.2f}",
                  f"{gx + dx:.2f}", f"{gy + dy:.2f}"]
    ctmp = GEOREF / "_1963_corr_gcp.tif"
    run(["gdal_translate", "-q", "-a_srs", "EPSG:2284", *cgcps, out, ctmp])
    run(["gdalwarp", "-q", "-overwrite", "-tps", "-t_srs", "EPSG:2284",
         "-r", "bilinear", "-dstalpha", "-co", "COMPRESS=DEFLATE",
         "-co", "TILED=YES", ctmp, out])
    ctmp.unlink()

    # ---- acceptance: wide-capture local displacement vs TIGER ----
    # (Phase correlation is USELESS on this sheet in offset AND
    # response: hatch-period aliasing produced readings from -97 to
    # +4200 ft on warps the displacement metric places within 100 ft.
    # The ±2560 ft template search cannot be fooled by the ~1000 ft
    # error class that shipped before.)
    bbox = raster_bbox(GEOREF / "1929.tif")     # core city, on every sheet
    x0, y0, x1, y1 = bbox
    c0, r0 = int((x0 - RX0) / TR), int((RY1 - y1) / TR)
    c1, r1 = int((x1 - RX0) / TR), int((RY1 - y0) / TR)
    refc = refsoft[r0:r1, c0:c1]
    tstc = streets(on_grid(out, bbox))
    disps = []
    P, SR = 80, 160
    for cy in range(P + SR, tstc.shape[0] - P - SR, 120):
        for cx in range(P + SR, tstc.shape[1] - P - SR, 120):
            tpl = tstc[cy - P:cy + P, cx - P:cx + P]
            if tpl.std() < 0.05:
                continue
            wnd = refc[cy - P - SR:cy + P + SR, cx - P - SR:cx + P + SR]
            res = cv2.matchTemplate(wnd, tpl, cv2.TM_CCOEFF_NORMED)
            _, score, _, loc = cv2.minMaxLoc(res)
            if score < 0.2:
                continue
            disps.append(((loc[0] - SR) * TR, (loc[1] - SR) * TR))
    dd = np.array(disps)
    # REPORT ONLY — this metric is built from the same street-lattice
    # matching that certified a 2,200-ft-misplaced warp as (-32,+48) ft
    # and then flagged a text-anchor-verified warp at (+352,+144); its
    # patch set and peak choices are unstable at the +/-300 ft scale.
    # Real acceptance = the named-intersection crosshair renders and
    # the Rivanna water-fill render (work/qa/_c*.png, _corr_hook.png):
    # street NAMES printed on the sheet cannot alias.
    print(f"street-lattice displacement (REPORT ONLY, see comment): "
          f"n={len(dd)} median "
          f"({np.median(dd[:, 0]):+.0f},{np.median(dd[:, 1]):+.0f})ft",
          flush=True)
    h = min(tstc.shape[0], refc.shape[0])
    w = min(tstc.shape[1], refc.shape[1])
    blend = cv2.addWeighted(tstc[:h, :w], 0.5, refc[:h, :w], 0.5, 0)
    cv2.imwrite(str(QA / "1963_vs_tiger.jpg"), (blend * 255).astype(np.uint8),
                [cv2.IMWRITE_JPEG_QUALITY, 88])
    print("OK work/georef/1963.tif", flush=True)


if __name__ == "__main__":
    main()

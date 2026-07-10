#!/usr/bin/env python3
"""Georeference the 1963 zoning map (the one edition the 2022 QGIS batch
skipped) directly against the TIGER street network.

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
 3. Final snap pairs become GCPs -> gdalwarp -tps at full resolution.
 4. Verify: street-mask phase correlation vs TIGER on the core-city
    grid (same test all other editions pass at <=25 ft) + wide-capture
    (±2500 ft) local displacement. Both must pass.

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
    print(f"verify wide displacement: n={len(dd)} median "
          f"({np.median(dd[:, 0]):+.0f},{np.median(dd[:, 1]):+.0f})ft",
          flush=True)
    ok = (len(dd) >= 25 and abs(np.median(dd[:, 0])) <= 120 and
          abs(np.median(dd[:, 1])) <= 120)
    h = min(tstc.shape[0], refc.shape[0])
    w = min(tstc.shape[1], refc.shape[1])
    blend = cv2.addWeighted(tstc[:h, :w], 0.5, refc[:h, :w], 0.5, 0)
    cv2.imwrite(str(QA / "1963_vs_tiger.jpg"), (blend * 255).astype(np.uint8),
                [cv2.IMWRITE_JPEG_QUALITY, 88])
    if not ok:
        out.rename(GEOREF / "1963_REJECTED.tif")
        sys.exit("verification failed — kept as 1963_REJECTED.tif")
    print("OK work/georef/1963.tif", flush=True)


if __name__ == "__main__":
    main()

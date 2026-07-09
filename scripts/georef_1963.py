#!/usr/bin/env python3
"""Georeference the 1963 zoning map (the one edition the 2022 QGIS batch
skipped) against the hand-georeferenced 1958 edition.

Every automated coarse-alignment route failed on this pair — worth
remembering why (see memory/2026-07-09-port-notes.md):
  * SIFT: hatch texture matches many-to-one; findHomography returns a
    degenerate H whose "inliers" collapse onto repeated points.
  * Gradient / street-mask / footprint-blob phase-correlation sweeps and
    bbox-template matching: the periodic street grid plus shared legend
    furniture produce false locks at wrong scales; global phase
    correlation offsets on these hatched prints are HATCH-PERIOD ALIASES
    (inconsistent across reference editions), so they can't even verify.

What works: a 2-point hand-picked similarity seed (unmistakable
hydrology features, read off gridded crops), then two passes of grid
template matching on street masks with a TIGHT search window and a
SIMILARITY fit (no homography — a flat scan is rotation+scale+shift, and
wide searches let the periodic grid hijack the consensus). Acceptance is
median LOCAL patch displacement vs three trusted editions, benchmarked
against the trusted editions' own pairwise noise floor (median ±30 ft,
MAD 150–270 ft). The accepted 1963 warp measures median 80–210 ft —
limited by the litho's own drafting/paper distortion.

Outputs: work/georef/1963.tif, work/qa/1963_vs_{ref}.jpg
"""
import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from corr_match import _thumb, _grid_pass

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT.parent / "1963 Zoning Map pg 2 - cleaned.png"
GEOREF = ROOT / "work" / "georef"
QA = ROOT / "work" / "qa"

ANCHOR = "1958"                    # same engraved base map as 1963
VERIFY = ["1958", "1976", "2003"]

MATCH_MAX_DIM = 4000
N_GCPS = 60
VERIFY_TR = 16                     # verification grid resolution, ft/px

# Hand-picked seed correspondences (px in the load_gray frames below):
# Meadow Creek / Rivanna confluence and the US-250 Rivanna crossing —
# unmistakable hydrology, immune to the periodic street grid.
SEED_1963 = np.float32([[3710, 1265], [3770, 1740]])
SEED_1958 = np.float32([[2958, 987], [2995, 1398]])


def run(cmd, **kw):
    subprocess.run([str(c) for c in cmd], check=True, **kw)


def load_gray(path, max_dim=MATCH_MAX_DIM):
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise RuntimeError(f"cannot read {path}")
    h, w = img.shape
    scale = min(1.0, max_dim / max(h, w))
    if scale < 1.0:
        img = cv2.resize(img, (round(w * scale), round(h * scale)),
                         interpolation=cv2.INTER_AREA)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(16, 16))
    return clahe.apply(img), scale


def geotransform_of(path):
    info = json.loads(subprocess.run(
        ["gdalinfo", "-json", str(path)], capture_output=True, text=True,
        check=True).stdout)
    return info["geoTransform"]


def raster_bbox(path):
    info = json.loads(subprocess.run(
        ["gdalinfo", "-json", str(path)], capture_output=True, text=True,
        check=True).stdout)
    (x0, y0), (x1, y1) = info["cornerCoordinates"]["upperLeft"], \
        info["cornerCoordinates"]["lowerRight"]
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)


def streets(img, sigma=3.0):
    """Soft mask of bright thin structures — the street network."""
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (13, 13))
    th = cv2.morphologyEx(img, cv2.MORPH_TOPHAT, k)
    _, bw = cv2.threshold(th, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    soft = cv2.GaussianBlur(bw.astype(np.float32) / 255, (0, 0), sigma)
    return soft / (soft.max() + 1e-6)


def on_grid(tif, bbox):
    x0, y0, x1, y1 = bbox
    grid = GEOREF / "_grid_tmp.tif"
    grid.unlink(missing_ok=True)
    run(["gdalwarp", "-q", "-te", x0, y0, x1, y1, "-tr", VERIFY_TR,
         VERIFY_TR, "-r", "bilinear", tif, grid])
    img = cv2.imread(str(grid), cv2.IMREAD_GRAYSCALE)
    grid.unlink(missing_ok=True)
    return img


def spread_pick(pts_src, n, img_shape):
    h, w = img_shape
    cells = int(np.ceil(np.sqrt(n)))
    picked, used = [], set()
    for i, s in enumerate(pts_src):
        c = (int(s[0] / w * cells), int(s[1] / h * cells))
        if c in used:
            continue
        used.add(c)
        picked.append(i)
        if len(picked) >= n:
            break
    return picked


def seed_similarity():
    (x0, y0), (x1, y1) = SEED_1963
    (u0, v0), (u1, v1) = SEED_1958
    a = complex(u1 - u0, v1 - v0) / complex(x1 - x0, y1 - y0)
    M = np.array([[a.real, -a.imag, 0], [a.imag, a.real, 0], [0, 0, 1.0]])
    M[:2, 2] = np.float32([u0, v0]) - (M[:2, :2] @ np.float32([x0, y0]))
    print(f"seed: scale={abs(a):.4f} rot={np.degrees(np.angle(a)):+.2f}deg",
          flush=True)
    return M


def local_displacement(tst, ref, patch=64, search=30, min_score=0.15):
    """Median (dx, dy) in feet of street-mask patches template-matched
    between two rasters on the common verify grid. Immune to the
    hatch-period aliasing that defeats global phase correlation."""
    disps = []
    P, SR = patch, search
    for cy in range(P + SR, tst.shape[0] - P - SR, 96):
        for cx in range(P + SR, tst.shape[1] - P - SR, 96):
            tpl = tst[cy - P:cy + P, cx - P:cx + P]
            if tpl.std() < 0.05:
                continue
            wnd = ref[cy - P - SR:cy + P + SR, cx - P - SR:cx + P + SR]
            res = cv2.matchTemplate(wnd, tpl, cv2.TM_CCOEFF_NORMED)
            _, score, _, loc = cv2.minMaxLoc(res)
            if score < min_score:
                continue
            disps.append(((loc[0] - SR) * VERIFY_TR,
                          (loc[1] - SR) * VERIFY_TR))
    return np.array(disps)


def main():
    QA.mkdir(parents=True, exist_ok=True)
    src_img, scale = load_gray(SRC)
    dst_img, dscale = load_gray(GEOREF / f"{ANCHOR}.tif")
    gt = geotransform_of(GEOREF / f"{ANCHOR}.tif")

    dst_w, ds = _thumb(dst_img, 3500)
    dst_g = streets(dst_w)
    dh, dw = dst_g.shape
    M_f = np.diag([ds, ds, 1.0]) @ seed_similarity()
    A = None
    for it, (search, thresh) in enumerate([(300, 15.0), (260, 8.0)]):
        warp = cv2.warpPerspective(src_img, M_f, (dw, dh))
        a_pts, b_pts = _grid_pass(streets(warp), dst_g, search, 0.08)
        A, mask = cv2.estimateAffinePartial2D(
            a_pts, b_pts, cv2.RANSAC, ransacReprojThreshold=thresh)
        inl = mask.ravel().astype(bool)
        print(f"iter{it}: {len(a_pts)} raw, {int(inl.sum())} inliers, "
              f"scale upd {np.hypot(A[0, 0], A[0, 1]):.4f}", flush=True)
        a_pts, b_pts = a_pts[inl], b_pts[inl]
        M_f = np.vstack([A, [0, 0, 1]]) @ M_f

    # a_pts live in the last warped frame; pull back to original src px
    M_prev = np.linalg.inv(np.vstack([A, [0, 0, 1]])) @ M_f
    src_pts = cv2.perspectiveTransform(
        a_pts.reshape(-1, 1, 2), np.linalg.inv(M_prev)).reshape(-1, 2)
    dst_pts = b_pts / ds
    idx = spread_pick(src_pts, N_GCPS, src_img.shape)
    print(f"final: {len(src_pts)} pts, {len(idx)} coverage cells", flush=True)
    if len(idx) < 15:
        sys.exit("insufficient GCP coverage")

    gcps = []
    for i in idx:
        sx, sy = src_pts[i] / scale
        ax, ay = dst_pts[i] / dscale
        gx = gt[0] + ax * gt[1] + ay * gt[2]
        gy = gt[3] + ax * gt[4] + ay * gt[5]
        gcps += ["-gcp", f"{sx:.2f}", f"{sy:.2f}", f"{gx:.3f}", f"{gy:.3f}"]
    tmp_gcp = GEOREF / "_1963_gcp.tif"
    out = GEOREF / "1963.tif"
    run(["gdal_translate", "-q", "-a_srs", "EPSG:2284", *gcps, SRC, tmp_gcp])
    run(["gdalwarp", "-q", "-tps", "-t_srs", "EPSG:2284", "-r", "bilinear",
         "-dstalpha", "-co", "COMPRESS=DEFLATE", "-co", "TILED=YES",
         "-overwrite", tmp_gcp, out])
    tmp_gcp.unlink()

    bbox = raster_bbox(GEOREF / "1929.tif")   # core city, on every sheet
    tst = streets(on_grid(out, bbox))
    worst = 0.0
    for ref_year in VERIFY:
        ref_img = on_grid(GEOREF / f"{ref_year}.tif", bbox)
        d = local_displacement(tst, streets(ref_img))
        mx, my = np.median(d[:, 0]), np.median(d[:, 1])
        worst = max(worst, abs(mx), abs(my))
        print(f"verify vs {ref_year}: n={len(d)} median "
              f"({mx:+.0f},{my:+.0f})ft", flush=True)
        blend = cv2.addWeighted(on_grid(out, bbox), 0.5, ref_img, 0.5, 0)
        cv2.imwrite(str(QA / f"1963_vs_{ref_year}.jpg"), blend,
                    [cv2.IMWRITE_JPEG_QUALITY, 88])
    if worst > 300:
        out.rename(GEOREF / "1963_REJECTED.tif")
        sys.exit(f"median displacement {worst:.0f} ft — rejected")
    print("OK work/georef/1963.tif", flush=True)


if __name__ == "__main__":
    main()

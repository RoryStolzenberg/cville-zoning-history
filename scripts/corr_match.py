#!/usr/bin/env python3
"""Correlation-based matcher for map pairs whose symbology differs too much
for SIFT (e.g. 1983 vs 1987 scans).

Stage 1 (coarse): brute-force rotation x scale sweep; at each candidate the
source gradient thumbnail is rotated/scaled and phase-correlated against the
target gradient thumbnail. A second sweep refines around the winning cell,
yielding a global similarity transform.

Stage 2 (fine): two iterations of grid template matching (TM_CCOEFF_NORMED on
gradient images). Iteration 1 searches wide, fits a homography, and re-warps;
iteration 2 searches tight. RANSAC filters the final correspondences into
(src_px, dst_px) full-resolution point pairs — the same shape the SIFT path
produces.
"""
import cv2
import numpy as np

COARSE_DIM = 700           # thumbnail max dim for the rotation/scale search
FINE_DIM = 3500            # working resolution for grid template matching
TEMPLATE = 240            # template patch size, px (big: street grids are periodic)
GRID = 20                  # candidate grid (GRID x GRID patch centers)
RANSAC_THRESH = 12.0       # px at FINE_DIM scale


def gradient(img, sigma=1.5):
    img = cv2.GaussianBlur(img, (0, 0), sigma)
    gx = cv2.Sobel(img, cv2.CV_32F, 1, 0)
    gy = cv2.Sobel(img, cv2.CV_32F, 0, 1)
    mag = cv2.magnitude(gx, gy)
    hi = np.percentile(mag, 99)
    return np.clip(mag / (hi + 1e-6), 0, 1).astype(np.float32)


def streets(img, sigma=3.0):
    """Soft mask of bright thin structures — the street network.

    Streets are drawn as light lines on darker fills in every GLUP edition,
    making this far more style-invariant than raw gradients (which are
    dominated by text glyphs, fill boundaries and halftone stipple). The
    final blur turns NCC into a chamfer-like line matcher.
    """
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (13, 13))
    th = cv2.morphologyEx(img, cv2.MORPH_TOPHAT, k)
    _, bw = cv2.threshold(th, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    soft = cv2.GaussianBlur(bw.astype(np.float32) / 255, (0, 0), sigma)
    hi = soft.max()
    return soft / (hi + 1e-6)


def _thumb(img, max_dim):
    h, w = img.shape
    s = max_dim / max(h, w)
    out = cv2.resize(img, (round(w * s), round(h * s)),
                     interpolation=cv2.INTER_AREA)
    return out, s


def _pad_to(img, size):
    h, w = img.shape
    out = np.zeros((size, size), np.float32)
    out[:h, :w] = img
    return out


def _street_thumb(img, max_dim):
    """streets() computed at 3x thumbnail scale (so the top-hat kernel spans
    real street widths), then downsampled. Returns (float image, scale)."""
    mid, s_mid = _thumb(img, max_dim * 3)
    st = streets(mid, sigma=2.0)
    out = cv2.resize(st, (round(st.shape[1] / 3), round(st.shape[0] / 3)),
                     interpolation=cv2.INTER_AREA)
    return out, s_mid / 3


def _sweep(src_g, dst_p, win, size, scales, angles, keep=6):
    """Rotation x scale sweep; returns top-`keep` candidates (deduped by
    angle/scale cell), best first."""
    cands = []
    for sc in scales:
        h, w = src_g.shape
        sw, sh = round(w * sc), round(h * sc)
        if max(sw, sh) > size:
            continue
        scaled = cv2.resize(src_g, (sw, sh), interpolation=cv2.INTER_AREA)
        for ang in angles:
            M = cv2.getRotationMatrix2D((sw / 2, sh / 2), ang, 1.0)
            cos, sin = abs(M[0, 0]), abs(M[0, 1])
            nw, nh = int(sh * sin + sw * cos), int(sh * cos + sw * sin)
            if max(nw, nh) > size:
                continue
            M[0, 2] += nw / 2 - sw / 2
            M[1, 2] += nh / 2 - sh / 2
            rot = cv2.warpAffine(scaled, M, (nw, nh))
            (dx, dy), resp = cv2.phaseCorrelate(_pad_to(rot, size), dst_p, win)
            cands.append((resp, ang, sc, -dx, -dy, sw, sh, nw, nh))
    cands.sort(reverse=True)
    picked = []
    for c in cands:
        if any(abs(c[1] - p[1]) < 6 and abs(c[2] / p[2] - 1) < 0.08
               for p in picked):
            continue
        picked.append(c)
        if len(picked) >= keep:
            break
    return picked


def coarse_align(src_gray, dst_gray, n_candidates=4):
    """Candidate similarity transforms via rotation/scale sweep with phase
    correlation on gradient thumbnails. Several candidates are returned;
    the fine stage + caller's validator decide which is real.

    Returns list of (3x3 matrix mapping src px -> dst px at full input
    resolution, response), best first.
    """
    dst_t, ds = _thumb(dst_gray, COARSE_DIM)
    dst_g = gradient(dst_t, 1.0)
    size = int(COARSE_DIM * 1.5)
    dst_p = _pad_to(dst_g, size)
    win = cv2.createHanningWindow((size, size), cv2.CV_32F)

    src_t, ss = _thumb(src_gray, COARSE_DIM)
    src_g = gradient(src_t, 1.0)

    rough = _sweep(src_g, dst_p, win, size,
                   np.geomspace(0.6, 1.7, 15), np.arange(-180, 180, 3),
                   keep=n_candidates)
    out = []
    for _, ang0, sc0, *_ in rough:
        refined = _sweep(src_g, dst_p, win, size,
                         sc0 * np.linspace(0.94, 1.06, 9),
                         ang0 + np.arange(-3.5, 3.6, 0.5), keep=1)
        if not refined:
            continue
        resp, ang, sc, dx, dy, sw, sh, nw, nh = refined[0]
        # Compose full-res src -> dst: src_full ->(ss*sc) scaled -> rotate
        # with expansion offset -> phase shift -> dst_full (/ds)
        M1 = np.array([[ss * sc, 0, 0], [0, ss * sc, 0], [0, 0, 1]])
        R = cv2.getRotationMatrix2D((sw / 2, sh / 2), ang, 1.0)
        R[0, 2] += nw / 2 - sw / 2
        R[1, 2] += nh / 2 - sh / 2
        M2 = np.vstack([R, [0, 0, 1]])
        M3 = np.array([[1, 0, dx], [0, 1, dy], [0, 0, 1]])
        M4 = np.array([[1 / ds, 0, 0], [0, 1 / ds, 0], [0, 0, 1]])
        out.append((M4 @ M3 @ M2 @ M1, resp))
    return out


def _grid_pass(src_g, dst_g, search, min_score):
    """Template-match a grid of patches from src_g inside dst_g windows.

    Both images are at FINE_DIM scale and already aligned by the current
    transform. Returns (pts_in_warped_frame, pts_in_dst_frame, scores).
    """
    dh, dw = dst_g.shape
    half_t, half_s = TEMPLATE // 2, search // 2
    a_pts, b_pts = [], []
    for gy in range(GRID):
        for gx in range(GRID):
            cx = int((gx + 0.5) * dw / GRID)
            cy = int((gy + 0.5) * dh / GRID)
            if (cx - half_s < 0 or cy - half_s < 0 or
                    cx + half_s >= dw or cy + half_s >= dh):
                continue
            tpl = src_g[cy - half_t:cy + half_t, cx - half_t:cx + half_t]
            if tpl.std() < 0.03:      # blank paper / margin
                continue
            wnd = dst_g[cy - half_s:cy + half_s, cx - half_s:cx + half_s]
            if wnd.std() < 0.03:
                continue
            res = cv2.matchTemplate(wnd, tpl, cv2.TM_CCOEFF_NORMED)
            _, score, _, loc = cv2.minMaxLoc(res)
            if score < min_score:
                continue
            a_pts.append([cx, cy])
            b_pts.append([cx - half_s + loc[0] + half_t,
                          cy - half_s + loc[1] + half_t])
    return np.float32(a_pts), np.float32(b_pts)


def dominant_angle(mask):
    """Dominant street-grid orientation of a street mask, degrees in [0,90).

    Street grids are orthogonal, so orientation collapses mod 90."""
    g = cv2.GaussianBlur(mask, (0, 0), 2.0)
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1)
    mag = cv2.magnitude(gx, gy).ravel()
    ang = (np.degrees(np.arctan2(gy, gx)).ravel()) % 90.0
    hist, edges = np.histogram(ang, bins=90, range=(0, 90), weights=mag)
    return float(edges[int(np.argmax(hist))])


def tiger_coarse(src_gray, tiger_soft, tiger_tr=16.0):
    """Coarse candidates for matching a sheet against the TIGER street grid.

    Phase correlation fails here (sheet vs county-only reference = partial
    overlap), so: rotation hypotheses come from dominant street-grid
    orientations (mod 90), scale hypotheses from plausible sheet widths,
    and translation from TM_CCOEFF_NORMED template matching at 64 ft/px.

    Returns [(3x3 matrix src px -> tiger grid px, score)], best first.
    """
    factor = 4                       # search at tiger_tr * factor ft/px
    t_small = cv2.resize(tiger_soft, None, fx=1 / factor, fy=1 / factor,
                         interpolation=cv2.INTER_AREA)
    th, tw = t_small.shape

    src_t, s_thumb = _thumb(src_gray, 2200)
    src_st = streets(src_t)

    # brute force: orientation histograms are unreliable (sheets mix the
    # county's ~37-degree grid with DC's north-aligned grid across the river)
    angles = np.arange(0, 360, 3)
    widths_ft = (30000, 34000, 38000, 42000, 47000, 53000, 60000, 68000)

    cands = []
    for ang in angles:
        for wft in widths_ft:
            # scale so the sheet's width spans wft feet on the search grid
            sc = (wft / (tiger_tr * factor)) / src_st.shape[1]
            h, w = src_st.shape
            M = cv2.getRotationMatrix2D((w / 2, h / 2), ang, sc)
            cos, sin = abs(M[0, 0]), abs(M[0, 1])
            nw, nh = int(h * sin + w * cos) + 1, int(h * cos + w * sin) + 1
            M[0, 2] += nw / 2 - w / 2
            M[1, 2] += nh / 2 - h / 2
            canvas_w = max(nw, tw + 8)
            canvas_h = max(nh, th + 8)
            rot = cv2.warpAffine(src_st, M, (canvas_w, canvas_h))
            res = cv2.matchTemplate(rot, t_small, cv2.TM_CCOEFF_NORMED)
            _, score, _, loc = cv2.minMaxLoc(res)
            # compose src full px -> tiger grid px (at tiger_tr)
            A = np.vstack([M, [0, 0, 1]])
            T1 = np.diag([s_thumb, s_thumb, 1.0])          # full -> thumb
            T2 = np.array([[1, 0, -loc[0]], [0, 1, -loc[1]], [0, 0, 1.0]])
            T3 = np.diag([factor, factor, 1.0])            # search -> grid px
            cands.append((T3 @ T2 @ A @ T1, float(score), ang, wft))
    cands.sort(key=lambda c: -c[1])
    for M, score, ang, wft in cands[:6]:
        print(f"  tiger coarse: score={score:.3f} ang={ang:.1f} "
              f"width={wft}ft", flush=True)
    return [(c[0], c[1]) for c in cands]


def fine_match(src_gray, dst_gray, M_coarse):
    """Iterative grid template matching after coarse warp.

    Returns (src_pts, dst_pts) at full input resolution, or (None, None).
    """
    dst_w, ds = _thumb(dst_gray, FINE_DIM)
    dst_g = streets(dst_w)
    dh, dw = dst_g.shape
    S = np.diag([ds, ds, 1.0])

    M = S @ M_coarse   # src full px -> dst FINE_DIM px
    # iter 0: wide search, tolerant RANSAC (coarse residual is systematic and
    # can reach ~200 px at sheet edges); iter 1: tight, after re-warp.
    # Low score floors: RANSAC consensus, not absolute score, separates
    # true matches from false peaks.
    for it, (search, min_score, ransac_px) in enumerate(
            [(640, 0.08, 35.0), (300, 0.08, RANSAC_THRESH)]):
        src_warp = cv2.warpPerspective(
            src_gray, M.astype(np.float64), (dw, dh))
        src_g = streets(src_warp)
        a_pts, b_pts = _grid_pass(src_g, dst_g, search, min_score)
        print(f"  fine iter{it}: {len(a_pts)} raw grid matches", flush=True)
        if len(a_pts) < 12:
            return None, None
        H, mask = cv2.findHomography(a_pts, b_pts, cv2.RANSAC, ransac_px)
        if H is None or mask is None:
            return None, None
        inl = mask.ravel().astype(bool)
        print(f"  fine iter{it}: {int(inl.sum())} inliers", flush=True)
        if inl.sum() < 12:
            return None, None
        a_pts, b_pts = a_pts[inl], b_pts[inl]
        M = H @ M   # refine alignment for the next (tighter) pass

    # a_pts live in the *last warped frame*; pull back to original src px
    M_prev = np.linalg.inv(H) @ M           # transform used in final pass
    src_pts = cv2.perspectiveTransform(
        a_pts.reshape(-1, 1, 2), np.linalg.inv(M_prev)).reshape(-1, 2)
    return src_pts, b_pts / ds


def corr_match(src_gray, dst_gray, validate=None, min_points=12):
    """Full pipeline. Tries each coarse candidate until fine matching yields
    >= min_points that pass `validate` (a callback on (src_pts, dst_pts)).

    Returns (src_pts, dst_pts, coarse_response) at full input resolution,
    or (None, None, 0.0)."""
    for M, resp in coarse_align(src_gray, dst_gray):
        print(f"  coarse candidate: response={resp:.3f}", flush=True)
        src_pts, dst_pts = fine_match(src_gray, dst_gray, M)
        if src_pts is None or len(src_pts) < min_points:
            continue
        if validate is not None and not validate(src_pts, dst_pts):
            print("  candidate rejected by validator", flush=True)
            continue
        return src_pts, dst_pts, resp
    return None, None, 0.0

"""
Independent forward-model re-implementation used ONLY by the verifier. Not
imported from solution/ or generator/ -- transcribed separately from the
task's governing equations so the check never trusts agent-controlled code.

PDE:      dc/dt = D * c_xx - v * c_x - k * c ,   x in [0, L], t in [0, T]
Robin BC:  D * c_x(0,t) = h0 * (c(0,t) - cL)
          -D * c_x(L,t) = hL * (c(L,t) - cR)
IC:       c(x,0) = c_base + A0 * exp(-((x - x0)**2) / (2 * w**2))
"""
import numpy as np
from scipy.linalg import expm


def build_chebyshev(npts):
    order = npts - 1
    idx = np.arange(order + 1)
    pts = np.cos(np.pi * idx / order)
    sign = (-1.0) ** idx
    sign[0] *= 2.0
    sign[-1] *= 2.0
    P = np.tile(pts, (order + 1, 1)).T
    Q = P - P.T
    Diff = np.outer(sign, 1.0 / sign) / (Q + np.eye(order + 1))
    Diff -= np.diag(Diff.sum(axis=1))
    return Diff, pts


def recompute_concentration(D, v, k, h0, hL, L, cL, cR, ic, sensors_x, times_t, npts=128):
    Draw, canonical = build_chebyshev(npts)
    grid = L / 2.0 * (1.0 - canonical)
    Gx = (-2.0 / L) * Draw
    Gxx = Gx @ Gx
    n = npts

    Lop = D * Gxx - v * Gx - k * np.eye(n)

    eq_left = D * Gx[0, :].copy()
    eq_left[0] -= h0
    b_left = -h0 * cL

    eq_right = -D * Gx[-1, :].copy()
    eq_right[-1] -= hL
    b_right = -hL * cR

    inner = np.arange(1, n - 1)
    Bmat = np.array([[eq_left[0], eq_left[-1]], [eq_right[0], eq_right[-1]]])
    Cmat = np.vstack([eq_left[inner], eq_right[inner]])
    if not np.all(np.isfinite(Bmat)):
        return None
    try:
        cond = np.linalg.cond(Bmat)
    except np.linalg.LinAlgError:
        return None
    if cond > 1e14:
        return None
    Binv = np.linalg.inv(Bmat)
    Pmat = -Binv @ Cmat
    qvec = Binv @ np.array([b_left, b_right])

    Lii = Lop[np.ix_(inner, inner)]
    Lib = Lop[np.ix_(inner, [0, n - 1])]
    Ared = Lii + Lib @ Pmat
    bred = Lib @ qvec
    if not np.all(np.isfinite(Ared)) or not np.all(np.isfinite(bred)):
        return None

    m = len(inner)
    Aaug = np.zeros((m + 1, m + 1))
    Aaug[:m, :m] = Ared
    Aaug[:m, m] = bred

    ic0 = ic["c_base"] + ic["A0"] * np.exp(-((grid - ic["x0"]) ** 2) / (2.0 * ic["w"] ** 2))
    y0 = np.concatenate([ic0[inner], [1.0]])

    hist = np.zeros((n, len(times_t)))
    for j, t in enumerate(times_t):
        yt = y0 if t == 0.0 else expm(Aaug * t) @ y0
        if not np.all(np.isfinite(yt)):
            return None
        cin = yt[:m]
        bnd = Pmat @ cin + qvec
        row = np.empty(n)
        row[0] = bnd[0]
        row[-1] = bnd[1]
        row[inner] = cin
        hist[:, j] = row

    xhat = 1.0 - 2.0 * grid / L
    weights = (-1.0) ** np.arange(n)
    weights = weights.astype(float)
    weights[0] *= 0.5
    weights[-1] *= 0.5
    targets = 1.0 - 2.0 * np.asarray(sensors_x) / L
    M = np.zeros((len(targets), n))
    for r, xv in enumerate(targets):
        diff = xv - xhat
        exact = np.where(np.abs(diff) < 1e-13)[0]
        if exact.size:
            M[r, exact[0]] = 1.0
        else:
            terms = weights / diff
            M[r, :] = terms / terms.sum()
    return M @ hist

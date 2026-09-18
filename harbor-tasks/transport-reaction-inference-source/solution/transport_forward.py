"""
Forward-model evaluator used by the OFFICIAL SOLUTION to fit (D, v, k, h0, hL).
Independently transcribed from the task's governing equations (not imported
from the generator or verifier). See instruction.md for the equations.
"""
import numpy as np
from scipy.linalg import expm


def _cheb(n_points):
    N = n_points - 1
    j = np.arange(N + 1)
    x = np.cos(np.pi * j / N)
    c = (-1.0) ** j
    c[0] *= 2.0
    c[-1] *= 2.0
    X = np.tile(x, (N + 1, 1)).T
    dX = X - X.T
    Dm = np.outer(c, 1.0 / c) / (dX + np.eye(N + 1))
    Dm -= np.diag(Dm.sum(axis=1))
    return Dm, x


class SpectralSolver:
    def __init__(self, L, n_points=128):
        self.L = float(L)
        self.n = int(n_points)
        Dm, xhat = _cheb(self.n)
        self.grid = self.L / 2.0 * (1.0 - xhat)
        self.Dx = (-2.0 / self.L) * Dm
        self.Dxx = self.Dx @ self.Dx
        xh = 1.0 - 2.0 * self.grid / self.L
        bw = (-1.0) ** np.arange(self.n)
        bw = bw.astype(float)
        bw[0] *= 0.5
        bw[-1] *= 0.5
        self._xh = xh
        self._bw = bw

    def _interp_matrix(self, targets):
        tt = 1.0 - 2.0 * np.asarray(targets) / self.L
        M = np.zeros((len(tt), self.n))
        for r, xv in enumerate(tt):
            diff = xv - self._xh
            exact = np.where(np.abs(diff) < 1e-13)[0]
            if exact.size:
                M[r, exact[0]] = 1.0
            else:
                terms = self._bw / diff
                M[r, :] = terms / terms.sum()
        return M

    def predict(self, D, v, k, h0, hL, cL, cR, ic, sensors_x, times_t):
        n = self.n
        Dx, Dxx = self.Dx, self.Dxx
        Aop = D * Dxx - v * Dx - k * np.eye(n)

        left = D * Dx[0, :].copy()
        left[0] -= h0
        left_rhs = -h0 * cL

        right = -D * Dx[-1, :].copy()
        right[-1] -= hL
        right_rhs = -hL * cR

        interior = np.arange(1, n - 1)
        Bmat = np.array([[left[0], left[-1]], [right[0], right[-1]]])
        Cmat = np.vstack([left[interior], right[interior]])
        # Guard: extreme parameter combinations (e.g. huge Peclet number
        # v*L/D) can make the boundary-elimination matrix Bmat ill
        # conditioned or the propagation operator's norm astronomically
        # large. Rather than let inv()/expm() blow up or hang, fail fast
        # with a NaN-flagged sentinel that the caller turns into a large
        # (but finite) residual, steering the optimizer away.
        if not np.all(np.isfinite(Bmat)) or np.linalg.cond(Bmat) > 1e14:
            return None
        Binv = np.linalg.inv(Bmat)
        P = -Binv @ Cmat
        q = Binv @ np.array([left_rhs, right_rhs])

        Aii = Aop[np.ix_(interior, interior)]
        Aib = Aop[np.ix_(interior, [0, n - 1])]
        Ared = Aii + Aib @ P
        bred = Aib @ q

        if not np.all(np.isfinite(Ared)):
            return None

        m = len(interior)
        Aaug = np.zeros((m + 1, m + 1))
        Aaug[:m, :m] = Ared
        Aaug[:m, m] = bred

        ic_full = ic["c_base"] + ic["A0"] * np.exp(-((self.grid - ic["x0"]) ** 2) / (2.0 * ic["w"] ** 2))
        y0 = np.concatenate([ic_full[interior], [1.0]])

        history = np.zeros((n, len(times_t)))
        for j, t in enumerate(times_t):
            yt = y0 if t == 0.0 else expm(Aaug * t) @ y0
            if not np.all(np.isfinite(yt)):
                return None
            cint = yt[:m]
            bnd = P @ cint + q
            row = np.empty(n)
            row[0] = bnd[0]
            row[-1] = bnd[1]
            row[interior] = cint
            history[:, j] = row

        M = self._interp_matrix(sensors_x)
        return M @ history

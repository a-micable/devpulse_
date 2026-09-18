"""
Ground-truth forward model for the 1D advection-diffusion-reaction transport
task. Used ONLY by the hidden-case generator to synthesize observations from
a true interior parameter vector. This module is never shipped to the agent.

PDE:      dc/dt = D * c_xx - v * c_x - k * c ,   x in [0, L], t in [0, T]
Robin BC:  D * c_x(0,t) = h0 * (c(0,t) - cL)      (left exchange with reservoir cL)
          -D * c_x(L,t) = hL * (c(L,t) - cR)      (right exchange with reservoir cR)
IC:       c(x,0) = c_base + A0 * exp(-((x - x0)**2) / (2 * w**2))

Method: Chebyshev spectral collocation in x + exact algebraic elimination of
the two Robin boundary degrees of freedom + exact-in-time propagation of the
resulting affine linear ODE via the augmented matrix exponential. No time
discretization error; spatial (spectral) error decays geometrically in N.
"""
import numpy as np
from scipy.linalg import expm


def chebyshev_diff_matrix(n_points):
    N = n_points - 1
    if N == 0:
        return np.zeros((1, 1)), np.array([1.0])
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


class TrueForwardModel:
    def __init__(self, domain_length, n_points=128):
        self.L = float(domain_length)
        self.n = int(n_points)
        Dm, xhat = chebyshev_diff_matrix(self.n)
        self.grid = self.L / 2.0 * (1.0 - xhat)  # grid[0]=0 .. grid[-1]=L
        scale = -2.0 / self.L
        self.Dx = scale * Dm
        self.Dxx = self.Dx @ self.Dx

    def _barycentric_weights(self):
        Ntot = self.n
        xhat = 1.0 - 2.0 * self.grid / self.L
        w = (-1.0) ** np.arange(Ntot)
        w = w.astype(float)
        w[0] *= 0.5
        w[-1] *= 0.5
        return xhat, w

    def _interp(self, targets):
        xhat, w = self._barycentric_weights()
        tt = 1.0 - 2.0 * np.asarray(targets) / self.L
        M = np.zeros((len(tt), self.n))
        for r, xv in enumerate(tt):
            diff = xv - xhat
            exact = np.where(np.abs(diff) < 1e-13)[0]
            if exact.size:
                M[r, exact[0]] = 1.0
            else:
                terms = w / diff
                M[r, :] = terms / terms.sum()
        return M

    def run(self, D, v, k, h0, hL, cL, cR, ic_params, eval_times):
        n = self.n
        Dx, Dxx = self.Dx, self.Dxx
        L_op = D * Dxx - v * Dx - k * np.eye(n)

        left_row = D * Dx[0, :].copy()
        left_row[0] -= h0
        left_rhs = -h0 * cL

        right_row = -D * Dx[-1, :].copy()
        right_row[-1] -= hL
        right_rhs = -hL * cR

        interior = np.arange(1, n - 1)
        B = np.array([[left_row[0], left_row[-1]], [right_row[0], right_row[-1]]])
        C = np.vstack([left_row[interior], right_row[interior]])
        Binv = np.linalg.inv(B)
        P_bc = -Binv @ C
        q_bc = Binv @ np.array([left_rhs, right_rhs])

        L_ii = L_op[np.ix_(interior, interior)]
        L_ib = L_op[np.ix_(interior, [0, n - 1])]
        A_reduced = L_ii + L_ib @ P_bc
        b_reduced = L_ib @ q_bc

        m = len(interior)
        A_aug = np.zeros((m + 1, m + 1))
        A_aug[:m, :m] = A_reduced
        A_aug[:m, m] = b_reduced

        ic_full = ic_params["c_base"] + ic_params["A0"] * np.exp(
            -((self.grid - ic_params["x0"]) ** 2) / (2.0 * ic_params["w"] ** 2)
        )
        y0 = np.concatenate([ic_full[interior], [1.0]])

        full_history = np.zeros((n, len(eval_times)))
        for j, t in enumerate(eval_times):
            yt = y0 if t == 0.0 else expm(A_aug * t) @ y0
            c_int = yt[:m]
            bnd = P_bc @ c_int + q_bc
            row = np.empty(n)
            row[0] = bnd[0]
            row[-1] = bnd[1]
            row[interior] = c_int
            full_history[:, j] = row
        return full_history

    def at_sensors(self, D, v, k, h0, hL, cL, cR, ic_params, sensors_x, eval_times):
        grid_vals = self.run(D, v, k, h0, hL, cL, cR, ic_params, eval_times)
        M = self._interp(sensors_x)
        return M @ grid_vals

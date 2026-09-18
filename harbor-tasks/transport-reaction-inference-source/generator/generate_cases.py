"""
Deterministic hidden-case generator for the transport-reaction inference
task. NEVER shipped to the agent (stays outside environment/ and out of the
zip's agent-visible paths). Produces:
  - data/public_examples/example_XX.json  (a few fully worked, visible cases)
  - tests/hidden_cases/case_XX.json         (private hidden cases + truth)

Every accepted case passes two automated screens before being written:
  1. Numerical convergence: the forward solve at the production resolution
     must agree with a higher-resolution solve to within 1e-10 (guarantees
     the 1e-9 verifier tolerance is met with large margin, and rules out
     numerically pathological parameter/geometry combinations).
  2. Identifiability: the Jacobian of the stacked observation vector with
     respect to the 5 unknown parameters, evaluated at the true parameter
     vector, must have full column rank (5) with a bounded condition number,
     so the hidden case has a numerically well-posed, locally unique inverse
     solution.
"""
import json
import os
import numpy as np
from physics_gen import TrueForwardModel

RNG_SEED = 20260918
PROD_N = 128
CHECK_N = 176
BOUNDS = {
    "D": (1e-10, 1e-2),
    "v": (1e-6, 1e-2),
    "k": (1e-6, 10.0),
    "h0": (1e-8, 1e-2),
    "hL": (1e-8, 1e-2),
}
PARAM_ORDER = ["D", "v", "k", "h0", "hL"]
MAX_PECLET = 400.0          # v*L/D cap: keeps the boundary layer resolvable
CONVERGENCE_TOL = 1e-10      # required agreement between PROD_N and CHECK_N
COND_MAX = 1e8


def sample_case(rng):
    L = float(rng.uniform(0.3, 1.8))
    while True:
        logD = rng.uniform(np.log10(BOUNDS["D"][0]), np.log10(BOUNDS["D"][1]))
        logv = rng.uniform(np.log10(BOUNDS["v"][0]), np.log10(BOUNDS["v"][1]))
        D, v = 10.0 ** logD, 10.0 ** logv
        pe = v * L / D
        if 0.2 <= pe <= MAX_PECLET:
            break
    logk = rng.uniform(np.log10(BOUNDS["k"][0]), np.log10(BOUNDS["k"][1]))
    k = 10.0 ** logk
    h0 = 10.0 ** rng.uniform(np.log10(BOUNDS["h0"][0]), np.log10(BOUNDS["h0"][1]))
    hL = 10.0 ** rng.uniform(np.log10(BOUNDS["hL"][0]), np.log10(BOUNDS["hL"][1]))

    lam = v / L + D / L ** 2 + k
    T = float(rng.uniform(1.5, 4.0) / lam)

    cL = float(rng.uniform(0.0, 0.05))
    cR = float(rng.uniform(0.0, 0.05))
    ic = dict(
        A0=float(rng.uniform(0.5, 1.5)),
        x0=float(rng.uniform(0.2 * L, 0.8 * L)),
        w=float(rng.uniform(0.05, 0.15) * L),
        c_base=float(rng.uniform(0.0, 0.05)),
    )

    n_sensors = int(rng.integers(6, 10))
    sensors_x = np.sort(rng.uniform(0.0, L, size=n_sensors))
    sensors_x[0] = 0.0
    sensors_x[-1] = L

    n_times = int(rng.integers(6, 10))
    times_t = np.sort(rng.uniform(0.02 * T, T, size=n_times))

    truth = dict(D=D, v=v, k=k, h0=h0, hL=hL)
    return dict(L=L, T=T, cL=cL, cR=cR, ic=ic, sensors_x=sensors_x, times_t=times_t), truth


def forward_vector(case, params, n_points):
    fm = TrueForwardModel(case["L"], n_points=n_points)
    vals = fm.at_sensors(
        params["D"], params["v"], params["k"], params["h0"], params["hL"],
        case["cL"], case["cR"], case["ic"], case["sensors_x"], case["times_t"],
    )
    return vals


def check_convergence(case, truth):
    v_prod = forward_vector(case, truth, PROD_N)
    v_check = forward_vector(case, truth, CHECK_N)
    if not (np.all(np.isfinite(v_prod)) and np.all(np.isfinite(v_check))):
        return False, np.inf, v_prod
    err = float(np.max(np.abs(v_prod - v_check)))
    return err < CONVERGENCE_TOL, err, v_prod


def check_identifiability(case, truth):
    base = forward_vector(case, truth, PROD_N).ravel()
    cols = []
    for p in PARAM_ORDER:
        lo, hi = BOUNDS[p]
        h = max(abs(truth[p]) * 1e-4, (hi - lo) * 1e-8)
        pert = dict(truth)
        pert[p] = truth[p] + h
        vp = forward_vector(case, pert, PROD_N).ravel()
        cols.append((vp - base) / h)
    J = np.column_stack(cols)
    if not np.all(np.isfinite(J)):
        return False, 0.0, np.inf
    s = np.linalg.svd(J, compute_uv=False)
    if s[-1] <= 0:
        return False, 0.0, np.inf
    cond = s[0] / s[-1]
    rank_ok = np.sum(s > 1e-8 * s[0]) == 5
    return (rank_ok and cond < COND_MAX), float(s[-1]), float(cond)


def make_cases(n_hidden, n_public, seed=RNG_SEED):
    rng = np.random.default_rng(seed)
    accepted = []
    attempts = 0
    while len(accepted) < n_hidden + n_public and attempts < 20000:
        attempts += 1
        case, truth = sample_case(rng)
        ok_conv, err, obs_prod = check_convergence(case, truth)
        if not ok_conv:
            continue
        ok_id, smin, cond = check_identifiability(case, truth)
        if not ok_id:
            continue
        sigma = float(max(1e-4, 0.01 * np.max(np.abs(obs_prod))))
        accepted.append(dict(case=case, truth=truth, observations=obs_prod, sigma=sigma,
                              conv_err=err, cond=cond, smin=smin))
    if len(accepted) < n_hidden + n_public:
        raise RuntimeError(f"Only generated {len(accepted)} valid cases in {attempts} attempts")
    return accepted[:n_public], accepted[n_public:n_public + n_hidden]


def serialize_input(idx, entry, prefix):
    c = entry["case"]
    payload = {
        "case_id": f"{prefix}_{idx:02d}",
        "L": c["L"],
        "T": c["T"],
        "cL": c["cL"],
        "cR": c["cR"],
        "initial_condition": c["ic"],
        "bounds": BOUNDS,
        "param_order": PARAM_ORDER,
        "sensors_x": c["sensors_x"].tolist(),
        "times_t": c["times_t"].tolist(),
        "sigma": entry["sigma"],
        "observations_c": entry["observations"].tolist(),
    }
    return payload


def serialize_truth(idx, entry, prefix):
    return {
        "case_id": f"{prefix}_{idx:02d}",
        "true_params": entry["truth"],
        "reference_objective": 0.0,  # noiseless data: true params give J=0 exactly
        "convergence_check_abs_err": entry["conv_err"],
        "jacobian_condition_number": entry["cond"],
    }


def main(out_root):
    public, hidden = make_cases(n_hidden=12, n_public=2)

    # Agent-visible: public worked examples (input + reference output, so
    # the agent can validate its own I/O format and forward model). Lives
    # inside environment/ because that directory is the Docker build
    # context for environment/Dockerfile -- COPY paths outside it are not
    # visible to that build.
    pub_dir = os.path.join(out_root, "environment", "examples")
    # Agent-visible: hidden-case INPUTS ONLY (the observations the agent
    # must actually fit -- these are not secret, only the true parameter
    # values and verifier tolerances are). Also lives inside environment/
    # for the same build-context reason.
    agent_cases_dir = os.path.join(out_root, "environment", "cases")
    # Verifier-only (separate environment build context): hidden-case
    # inputs bundled together with their private truth files.
    hid_dir = os.path.join(out_root, "tests", "hidden_cases")
    os.makedirs(pub_dir, exist_ok=True)
    os.makedirs(agent_cases_dir, exist_ok=True)
    os.makedirs(hid_dir, exist_ok=True)

    for idx, entry in enumerate(public):
        inp = serialize_input(idx, entry, "example")
        truth = serialize_truth(idx, entry, "example")
        output_ref = {
            inp["case_id"]: {
                **truth["true_params"],
                "predicted_c": entry["observations"].tolist(),
                "standardized_residuals": np.zeros_like(entry["observations"]).tolist(),
                "objective_J": 0.0,
            }
        }
        with open(os.path.join(pub_dir, f"example_{idx:02d}_input.json"), "w") as f:
            json.dump(inp, f, indent=2)
        with open(os.path.join(pub_dir, f"example_{idx:02d}_reference_output.json"), "w") as f:
            json.dump(output_ref, f, indent=2)

    for idx, entry in enumerate(hidden):
        inp = serialize_input(idx, entry, "case")
        truth = serialize_truth(idx, entry, "case")
        with open(os.path.join(agent_cases_dir, f"case_{idx:02d}_input.json"), "w") as f:
            json.dump(inp, f, indent=2)
        with open(os.path.join(hid_dir, f"case_{idx:02d}_input.json"), "w") as f:
            json.dump(inp, f, indent=2)
        with open(os.path.join(hid_dir, f"case_{idx:02d}_truth.json"), "w") as f:
            json.dump(truth, f, indent=2)

    print(f"Wrote {len(public)} public examples and {len(hidden)} hidden cases.")
    for idx, entry in enumerate(hidden):
        print(f"  hidden case_{idx:02d}: conv_err={entry['conv_err']:.2e} cond={entry['cond']:.2e} "
              f"D={entry['truth']['D']:.3e} v={entry['truth']['v']:.3e} k={entry['truth']['k']:.3e} "
              f"h0={entry['truth']['h0']:.3e} hL={entry['truth']['hL']:.3e}")


if __name__ == "__main__":
    import sys
    main(sys.argv[1] if len(sys.argv) > 1 else ".")

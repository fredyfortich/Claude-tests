"""Stage 1b: fit a pinhole camera to the measured anchors (fSpy-style step for a perspective source).

Unknowns: camera position, yaw, pitch, focal length (roll fixed at 0). The 3D side of each anchor
comes from the spec, so residuals measure how well the spec explains the image.
One camera per view in meta.views; writes each camera and its per-anchor residuals back into the spec.
"""
import numpy as np
from scipy.optimize import least_squares

from camera import Camera, load_spec, resolve_anchor, save_spec

W, H = 1376, 768


def fit_view(spec, view):
    anchors = view["anchors"]
    P = np.array([resolve_anchor(spec, a) for a in anchors], float)
    px = np.array([a["px"] for a in anchors], float)
    w = np.sqrt(np.array([a.get("w", 1.0) for a in anchors], float))
    roll = view.get("fit_roll", False)

    def make(x):
        return Camera(x[:3], x[3], x[4], x[5], (W, H), x[6] if roll else 0.0)

    def residuals(x):
        return ((make(x).project(P) - px) * w[:, None]).ravel()

    seed = view.get("camera")
    if seed:
        x0 = [*seed["C"], np.radians(seed["yaw_deg"]), np.radians(seed["pitch_deg"]), seed["f_px"]]
    else:
        x0 = [0, -250000, 60000, 0.1, 0.22, 6000.0]
    lo, hi = [-1e6, -1e6, 0, -0.8, -0.6, 500], [1e6, 0, 1e6, 0.8, 1.2, 20000]
    scale = [1e4, 1e4, 1e4, 0.1, 0.1, 1e3]
    if roll:
        x0, lo, hi, scale = x0 + [0.0], lo + [-0.2], hi + [0.2], scale + [0.02]
    x0 = np.clip(x0, np.array(lo) + 1e-6, np.array(hi) - 1e-6)
    sol = least_squares(residuals, x0, bounds=(lo, hi), x_scale=scale)
    cam = make(sol.x)
    err = np.linalg.norm(cam.project(P) - px, axis=1)
    meta = cam.to_meta()
    meta["rms_px"] = round(float(np.sqrt(np.mean(err ** 2))), 1)
    meta["note"] = "fitted by tools/fit_camera.py; single view, plan depth (Y) weakly constrained"
    view["camera"] = meta
    for a, e in zip(anchors, err):
        a["residual_px"] = round(float(e), 1)
    return meta


def main():
    spec = load_spec()
    for view in spec["meta"]["views"]:
        meta = fit_view(spec, view)
        print(view["id"], "camera", {k: meta[k] for k in ("C", "yaw_deg", "pitch_deg", "roll_deg", "f_px", "rms_px")})
        for a in view["anchors"]:
            print("  %-22s residual %5.1f px" % (a["id"], a["residual_px"]))
    save_spec(spec)


if __name__ == "__main__":
    main()

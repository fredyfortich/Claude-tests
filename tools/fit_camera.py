"""Stage 1b: fit a pinhole camera to the measured anchors (fSpy-style step for a perspective source).

Unknowns: camera position, yaw, pitch, focal length (roll fixed at 0). The 3D side of each anchor
comes from the spec, so residuals measure how well the spec explains the image.
Writes meta.camera and per-anchor residuals back into the spec.
"""
import numpy as np
from scipy.optimize import least_squares

from camera import Camera, load_spec, resolve_anchor, save_spec

W, H = 1376, 768


def main():
    spec = load_spec()
    anchors = spec["meta"]["anchors"]
    P = np.array([resolve_anchor(spec, a) for a in anchors], float)
    px = np.array([a["px"] for a in anchors], float)
    w = np.sqrt(np.array([a.get("w", 1.0) for a in anchors], float))

    def make(x):
        return Camera(x[:3], x[3], x[4], x[5], (W, H))

    def residuals(x):
        return ((make(x).project(P) - px) * w[:, None]).ravel()

    x0 = np.array([0, -250000, 60000, 0.1, 0.22, 6000.0])
    lo = [-1e6, -1e6, 0, -0.8, 0.0, 1000]
    hi = [1e6, 0, 1e6, 0.8, 1.2, 20000]
    sol = least_squares(residuals, x0, bounds=(lo, hi), x_scale=[1e4, 1e4, 1e4, 0.1, 0.1, 1e3])
    cam = make(sol.x)
    err = np.linalg.norm(cam.project(P) - px, axis=1)
    meta = cam.to_meta()
    meta["rms_px"] = round(float(np.sqrt(np.mean(err ** 2))), 1)
    meta["note"] = "fitted by tools/fit_camera.py; near-orthographic lens -> depth (Y) weakly constrained"
    spec["meta"]["camera"] = meta
    for a, e in zip(anchors, err):
        a["residual_px"] = round(float(e), 1)
    save_spec(spec)
    print("camera", meta)
    for a in anchors:
        print("  %-22s residual %5.1f px" % (a["id"], a["residual_px"]))


if __name__ == "__main__":
    main()

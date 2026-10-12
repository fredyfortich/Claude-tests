"""Stage 2: deterministic overlay of the JSON spec on the source image (user checkpoint).

Projects the built geometry through the fitted camera, colour-coded by layer, with
approximate (≈) dimensions, level tags, anchor residuals and per-element confidence.
"""
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from camera import OUT, Camera, load_spec, mvm_core, resolve_anchor, view_image  # noqa: E402

COLORS = {
    "Roof": "#ffb000", "Structure": "#ff3b30", "Facade::Glass": "#00c8ff", "Facade::Opaque": "#c08040",
    "Circulation::Core": "#b45cff", "Circulation::Stairs": "#ff7ad9", "Interior elements": "#7dff6b",
    "Massing": "#ffffff",
}


def draw_poly(ax, cam, pts, color, lw=1.0, alpha=0.9, closed=False):
    p = np.asarray(pts, float)
    if closed:
        p = np.vstack([p, p[:1]])
    xy = cam.project(p)
    ax.plot(xy[:, 0], xy[:, 1], color=color, lw=lw, alpha=alpha, solid_capstyle="round")


def label(ax, cam, xyz, text, color="white", size=6.5, dy=0):
    u, v = cam.project([xyz])[0]
    ax.text(u, v + dy, text, color=color, fontsize=size, ha="center", va="center",
            bbox=dict(boxstyle="round,pad=0.15", fc="black", ec="none", alpha=0.6))


def dim(ax, cam, a, b, text, color="#ffe066"):
    draw_poly(ax, cam, [a, b], color, lw=0.9)
    for p in (a, b):
        draw_poly(ax, cam, [[p[0], p[1], p[2] - 500], [p[0], p[1], p[2] + 500]], color, lw=0.9)
    label(ax, cam, [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2], text, color, 6.5, -7)


def main():
    spec = load_spec()
    prims = mvm_core.build(spec)
    for view in spec["meta"]["views"]:
        draw_view(spec, prims, view)


def draw_view(spec, prims, view):
    cam = Camera.from_meta(view["camera"])
    img = Image.open(view_image(view)).convert("RGB")
    w, h = img.size
    fig = plt.figure(figsize=(w / 100.0, h / 100.0), dpi=200)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.imshow(np.asarray(img) * 0.55 / 255.0)
    ax.set_xlim(0, w)
    ax.set_ylim(h, 0)
    ax.axis("off")

    for p in prims:
        col = COLORS.get(p["layer"], "white")
        if p["kind"] == "lines":
            for a, b in p["segs"]:
                draw_poly(ax, cam, [a, b], col, lw=0.25, alpha=0.45)
        elif p["kind"] == "pipe":
            draw_poly(ax, cam, p["pts"], col, lw=1.2)
        elif p["kind"] == "mesh" and "gutter" in p["name"]:
            draw_poly(ax, cam, p["v"][1::3], "#ffffff", lw=1.6)
        elif p["kind"] == "extrude":
            if p["layer"] == "Massing":
                continue
            base = np.asarray(p["pts"], float)
            top = base + np.asarray(p["vec"], float)
            closed = p.get("closed", True)
            draw_poly(ax, cam, base, col, lw=0.7, closed=closed)
            draw_poly(ax, cam, top, col, lw=0.7, closed=closed)
            step = max(1, len(base) // 8)
            for k in range(0, len(base), step):
                draw_poly(ax, cam, [base[k], top[k]], col, lw=0.5, alpha=0.6)

    lv = dict((l["name"], l["elev"]) for l in spec["levels"])
    mod = spec["canopy"]["module"]
    shells = spec["canopy"]["shells"]
    for s in shells:
        z = s.get("apex", mod["apex"])
        label(ax, cam, [s["c"][0], s["c"][1], z + 900], "%s  c%.1f" % (s["id"], s["conf"]), "#ffb000", 6.5)

    xs = [s["c"][0] - s.get("R", mod["R"]) for s in shells] + [s["c"][0] + s.get("R", mod["R"]) for s in shells]
    x0, x1 = min(xs), max(xs)
    zt = mod["apex"] + 2600
    dim(ax, cam, [x0, 0, zt], [x1, 0, zt], "≈ %d mm canopy width" % round(x1 - x0, -2))
    front = sorted([s for s in shells if abs(s["c"][1]) <= 3000], key=lambda s: s["c"][0])
    for a, b in zip(front, front[1:]):
        dim(ax, cam, [a["c"][0], -9000, -1200], [b["c"][0], -9000, -1200], "≈ %d" % round(b["c"][0] - a["c"][0], -2))

    xl = x0 - 1500
    for name in ("L-1", "L00", "L01", "L02"):
        z = lv[name]
        draw_poly(ax, cam, [[xl, 0, z], [xl + 2500, 0, z]], "#ffe066", lw=1.0)
        label(ax, cam, [xl - 2200, 0, z], "%s %+d" % (name, z), "#ffe066", 6.5)
    for name, z in (("eave", mod["eave"]), ("apex", mod["apex"])):
        draw_poly(ax, cam, [[xl, 0, z], [xl + 2500, 0, z]], "#ffb000", lw=1.0)  # module values; per-shell overrides in spec
        label(ax, cam, [xl + 5000, 0, z], "%s %+d" % (name, z), "#ffb000", 6.5)
    xr = x1 + 1200
    dim(ax, cam, [xr, 0, lv["L00"]], [xr, 0, lv["L01"]], "≈ %d" % (lv["L01"] - lv["L00"]))
    dim(ax, cam, [xr, 0, lv["L01"]], [xr, 0, lv["L02"]], "≈ %d" % (lv["L02"] - lv["L01"]))

    for e in spec["elements"]:
        if e["id"] in ("Pool_walls", "Lounge_seat_ring", "Glass_L01", "Core_West_stone_drum", "Bastion_planter",
                       "Stair_West_landscape", "Deck_L00"):
            o = e.get("outline")
            if o and o["type"] == "circle":
                c, txt = o["c"] + [0], "%s Ø≈%d c%.1f" % (e["id"], 2 * o["r"], e["conf"])
            elif o:
                pts = np.asarray(mvm_core.outline(o, mvm_core.make_ctx(spec)))
                c = list(pts.mean(axis=0)) + [0]
                ext = pts.max(axis=0) - pts.min(axis=0)
                txt = "%s ≈%dx%d c%.1f" % (e["id"], round(ext[0], -2), round(ext[1], -2), e["conf"])
            else:
                c, txt = e["start"] + [0], "%s c%.1f" % (e["id"], e["conf"])
            ztag = e.get("z", [e.get("top", 0)])[-1]
            c[2] = lv[ztag] if isinstance(ztag, str) else ztag
            label(ax, cam, c, txt, "#7dffd4", 5.5)

    for a in view["anchors"]:
        m = np.asarray(a["px"], float)
        q = cam.project([resolve_anchor(spec, a)])[0]
        ax.plot([m[0], q[0]], [m[1], q[1]], color="#ff00ff", lw=0.8)
        ax.plot(m[0], m[1], "+", color="#00ffff", ms=5, mew=1.0)
        ax.plot(q[0], q[1], "o", color="#ff00ff", ms=2.5)

    c = view["camera"]
    ax.text(8, h - 10, "STAGE 2 CHECKPOINT  |  camera f=%.0f px  pitch=%.1f°  yaw=%.1f°  anchor rms=%.1f px  |  "
            "+ measured  o projected  |  ≈ mm, conf per element" % (c["f_px"], c["pitch_deg"], c["yaw_deg"], c["rms_px"]),
            color="white", fontsize=6.5, va="bottom",
            bbox=dict(boxstyle="round,pad=0.25", fc="black", ec="none", alpha=0.7))
    y = 12
    for k, v in COLORS.items():
        if k == "Massing":
            continue
        ax.text(w - 8, y, k, color=v, fontsize=6.5, ha="right", va="top",
                bbox=dict(boxstyle="round,pad=0.15", fc="black", ec="none", alpha=0.6))
        y += 15
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "stage2_overlay_%s.png" % view["id"])
    fig.savefig(path, dpi=200)
    plt.close(fig)
    print("wrote", path)


if __name__ == "__main__":
    main()

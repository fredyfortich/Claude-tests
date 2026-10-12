"""Plan diagram: canopy cells (eaves and valleys) over the L01 slab, pods, cores and tree columns."""
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from camera import OUT, load_spec, mvm_core  # noqa: E402

STYLE = {
    "Deck_L00": ("#c9c2b4", 0.8, "-"), "Slab_L01": ("#d9480f", 2.0, "-"), "Railing_L01": ("#1c7ed6", 0.8, "--"),
    "Glass_L00": ("#5c7cfa", 1.0, ":"), "Glass_L01_W": ("#1c7ed6", 1.6, "-"), "Glass_L01_E": ("#1c7ed6", 1.6, "-"),
    "Core_West_stone_drum": ("#7048e8", 1.6, "-"), "Core_East_service": ("#7048e8", 1.6, "-"),
    "Pool_walls": ("#15aabf", 1.2, "-"), "Planter_round_E": ("#2f9e44", 1.0, "-"), "Lounge_seat_ring": ("#2f9e44", 1.0, "-"),
}


def main():
    spec = load_spec()
    ctx = mvm_core.make_ctx(spec)
    can = mvm_core.Canopy(spec["canopy"])
    fig, ax = plt.subplots(figsize=(14, 9), dpi=150)
    for e in spec["elements"]:
        if e["id"] not in STYLE or "outline" not in e:
            continue
        col, lw, ls = STYLE[e["id"]]
        pts = mvm_core.outline(e["outline"], ctx)
        if e.get("inset"):
            pts = mvm_core.offset_closed(pts, e["inset"])
        P = np.asarray(pts + pts[:1]) / 1000.0
        ax.plot(P[:, 0], P[:, 1], color=col, lw=lw, ls=ls, label=e["id"])
    for i, s in enumerate(can.shells):
        for nb, run in can.boundary_runs(i):
            R = np.asarray(run)[:, :2] / 1000.0
            if nb == -1:
                ax.plot(R[:, 0], R[:, 1], color="#f08c00", lw=1.2)
            elif nb > i:
                ax.plot(R[:, 0], R[:, 1], color="#868e96", lw=2.4, alpha=0.7)
        cx, cy = np.asarray(s["c"]) / 1000.0
        ax.plot(cx, cy, "o", color="#e03131", ms=7)
        ax.annotate("%s  apex %.1f" % (s["id"], s["apex"] / 1000.0), (cx, cy), xytext=(6, 6), textcoords="offset points",
                    fontsize=9, color="#e03131")
    rows = mvm_core.dome_report(spec)
    txt = "\n".join("%s  cell %5.1f m²  prominence %4.2f m" % (r["id"], r["cell_m2"], r["prominence_mm"] / 1000.0) for r in rows)
    ax.text(0.01, 0.99, txt, transform=ax.transAxes, va="top", fontsize=8, family="monospace",
            bbox=dict(boxstyle="round", fc="white", ec="#adb5bd"))
    ax.plot([], [], color="#f08c00", lw=1.2, label="canopy eave")
    ax.plot([], [], color="#868e96", lw=2.4, label="valley / gutter")
    ax.plot([], [], "o", color="#e03131", label="tree column / shell centre")
    ax.set_aspect("equal")
    ax.grid(True, lw=0.3, alpha=0.5)
    ax.set_xlabel("X (m) along the facade")
    ax.set_ylabel("Y (m) away from the lake")
    ax.set_title("Plan at L01: six canopy cells over the rounded upper-floor slab")
    ax.legend(loc="lower right", fontsize=7, ncol=2)
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "plan_L01.png")
    fig.tight_layout()
    fig.savefig(path)
    print("wrote", path)


if __name__ == "__main__":
    main()

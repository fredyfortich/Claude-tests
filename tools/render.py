"""Stage 4: matched-camera render vs the source, plus one aerial view.

Small numpy z-buffer rasteriser: a deterministic viewport capture for comparing against the
image, not a presentation renderer. Transparent layers (canopy infill, glass) blend in a second pass.
"""
import math
import os

import numpy as np
from PIL import Image

from camera import IMAGE, OUT, Camera, load_spec, mvm_core

STYLE = {  # rgb, alpha
    "Roof": ((248, 238, 218), 1.0), "skin": ((255, 244, 222), 0.22), "gutter": ((255, 255, 255), 1.0),
    "Structure": ((234, 226, 212), 1.0), "Facade::Glass": ((150, 190, 222), 0.35),
    "Circulation::Core": ((214, 196, 170), 1.0), "Circulation::Stairs": ((226, 214, 196), 1.0),
    "Interior elements": ((240, 222, 192), 1.0),
    "water": ((90, 150, 190), 0.55), "lagoon": ((40, 60, 75), 0.6),
}
LIGHT = np.array([-0.45, -0.55, 0.70]) / np.linalg.norm([-0.45, -0.55, 0.70])


def look_at(C, T, f, size):
    d = np.asarray(T, float) - np.asarray(C, float)
    return Camera(C, math.atan2(d[0], d[1]), math.atan2(-d[2], math.hypot(d[0], d[1])), f, size)


def keyhole(outer, holes):
    """Cap with holes as one bridged simple polygon."""
    poly = [list(p) for p in outer]
    for h in holes:
        h = [list(p) for p in reversed(h)]
        P, H = np.asarray(poly)[:, :2], np.asarray(h)[:, :2]
        i, j = np.unravel_index(np.argmin(((P[:, None, :] - H[None, :, :]) ** 2).sum(-1)), (len(P), len(H)))
        poly = poly[:i + 1] + h[j:] + h[:j + 1] + poly[i:]
    return poly


def ear_clip(P3):
    """Triangulate a planar (possibly concave, bridged) polygon; returns index triples."""
    P3 = np.asarray(P3, float)
    n_vec = np.zeros(3)
    for a in range(len(P3)):
        n_vec += np.cross(P3[a], P3[(a + 1) % len(P3)])
    drop = int(np.argmax(np.abs(n_vec)))
    P = np.delete(P3, drop, axis=1)
    if n_vec[drop] < 0:
        P = P[:, ::-1]
    idx = list(range(len(P)))
    tris = []

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    guard = 0
    while len(idx) > 3 and guard < 10 * len(P):
        guard += 1
        for k in range(len(idx)):
            i0, i1, i2 = idx[k - 1], idx[k], idx[(k + 1) % len(idx)]
            a, b, c = P[i0], P[i1], P[i2]
            if cross(a, b, c) <= 1e-9:
                continue
            ok = True
            for j in idx:
                q = P[j]
                if j in (i0, i1, i2) or (q == a).all() or (q == b).all() or (q == c).all():
                    continue
                if cross(a, b, q) >= 0 and cross(b, c, q) >= 0 and cross(c, a, q) >= 0:
                    ok = False
                    break
            if ok:
                tris.append((i0, i1, i2))
                idx.pop(k)
                break
        else:
            break
    if len(idx) == 3:
        tris.append(tuple(idx))
    return tris


def triangles(prims):
    out = []

    def add_poly(V, style):
        if len(V) == 3:
            out.append((V, style))
        elif len(V) == 4:
            out.append((V[[0, 1, 2]], style))
            out.append((V[[0, 2, 3]], style))
        else:
            for t in ear_clip(V):
                out.append((V[list(t)], style))

    for p in prims:
        k, lay, name = p["kind"], p["layer"], p["name"]
        if lay == "Massing" or k in ("lines", "polyline"):
            continue
        style = "skin" if name.endswith("_skin") else "gutter" if "gutter" in name else lay
        if k in ("mesh", "pipe"):
            v, f = (p["v"], p["f"]) if k == "mesh" else mvm_core.pipe_mesh(p["pts"], p["r"][0], p["r"][1], 10)
            V = np.asarray(v, float)
            for fc in f:
                add_poly(V[fc], style)
        elif k == "extrude":
            vec = np.asarray(p["vec"], float)
            closed = p.get("closed", True)
            for loop in [p["pts"]] + list(p.get("holes", [])):
                L = np.asarray(loop, float)
                n = len(L)
                for a in range(n if closed else n - 1):
                    b = (a + 1) % n
                    add_poly(np.array([L[a], L[b], L[b] + vec, L[a] + vec]), style)
            if closed and p.get("cap", True):
                cap = np.asarray(keyhole(p["pts"], p.get("holes", [])), float)
                add_poly(cap, style)
                add_poly(cap + vec, style)
    return out


def context_triangles(spec):
    """Render-only context (no model geometry): pool water and the site lagoon / base plate."""
    out = []
    for e in spec["elements"]:
        if e["id"] == "Pool_walls":
            water = mvm_core.offset_closed(mvm_core.outline(e["outline"], mvm_core.make_ctx(spec)), e["t"])
            V = np.asarray(mvm_core.lift(water, -150), float)
            out += [(V[list(t)], "water") for t in ear_clip(V)]
    site = spec["meta"].get("context", {}).get("site_plate")
    if site:
        V = np.asarray(mvm_core.lift(site["outline_xy"], site["top"]), float)
        out += [(V[list(t)], "lagoon") for t in ear_clip(V)]
    return out


def render(cam, prims, spec=None, bg=(18, 18, 20), ss=2):
    W, H = cam.w * ss, cam.h * ss
    big = Camera(cam.C, cam.yaw, cam.pitch, cam.f * ss, (W, H), cam.roll)
    color = np.zeros((H, W, 3)) + np.array(bg, float)
    zbuf = np.full((H, W), np.inf)
    tris = triangles(prims) + (context_triangles(spec) if spec else [])
    opaque, clear = [], []
    for T, style in tris:
        d = big.depth(T)
        if d.min() <= 1000:
            continue
        nrm = np.cross(T[1] - T[0], T[2] - T[0])
        nn = np.linalg.norm(nrm)
        if nn < 1e-9:
            continue
        rgb, alpha = STYLE.get(style, ((230, 230, 230), 1.0))
        col = np.array(rgb, float) * (0.42 + 0.58 * abs(nrm @ LIGHT) / nn)
        item = (big.project(T), d, col, alpha)
        (opaque if alpha >= 1.0 else clear).append(item)
    clear.sort(key=lambda t: -t[1].mean())
    for items, transparent in ((opaque, False), (clear, True)):
        for uv, d, col, alpha in items:
            x0, y0 = np.floor(uv.min(0)).astype(int)
            x1, y1 = np.ceil(uv.max(0)).astype(int)
            x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, W - 1), min(y1, H - 1)
            if x0 > x1 or y0 > y1:
                continue
            xs, ys = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
            (ax, ay), (bx, by), (cx, cy) = uv
            den = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
            if abs(den) < 1e-12:
                continue
            w0 = ((by - cy) * (xs - cx) + (cx - bx) * (ys - cy)) / den
            w1 = ((cy - ay) * (xs - cx) + (ax - cx) * (ys - cy)) / den
            w2 = 1 - w0 - w1
            inside = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
            if not inside.any():
                continue
            z = 1.0 / (w0 / d[0] + w1 / d[1] + w2 / d[2])
            zb = zbuf[y0:y1 + 1, x0:x1 + 1]
            hit = inside & (z < zb - 1.0)
            tile = color[y0:y1 + 1, x0:x1 + 1]
            if transparent:
                tile[hit] = tile[hit] * (1 - alpha) + col * alpha
            else:
                tile[hit] = col
                zb[hit] = z[hit]
    img = Image.fromarray(np.clip(color, 0, 255).astype(np.uint8))
    return img.resize((cam.w, cam.h), Image.LANCZOS)


def main():
    spec = load_spec()
    prims = mvm_core.build(spec)
    cam = Camera.from_meta(spec["meta"]["camera"])
    src = Image.open(IMAGE).convert("RGB")
    matched = render(cam, prims, spec)
    comp = Image.new("RGB", (src.width, src.height * 2 + 8), (0, 0, 0))
    comp.paste(src, (0, 0))
    comp.paste(matched, (0, src.height + 8))
    os.makedirs(OUT, exist_ok=True)
    comp.save(os.path.join(OUT, "stage4_compare.png"))
    aerial = look_at([42000, -50000, 30000], [0, 3000, 2000], 1700, (1600, 1000))
    render(aerial, prims, spec).save(os.path.join(OUT, "render_aerial.png"))
    print("wrote stage4_compare.png, render_aerial.png")


if __name__ == "__main__":
    main()

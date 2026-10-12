# -*- coding: utf-8 -*-
"""
MVM core: spec (JSON, mm) -> geometry primitives.

Pure Python (CPython 3 / IronPython 2.7), no Rhino imports, so one geometry
engine feeds the RhinoCommon build script, the Grasshopper component and the
rhino3dm export. Coordinates are mm, world XYZ, Z up.

Primitive kinds (every primitive also carries "layer" and "name"):
  mesh      v=[[x,y,z]..], f=[[i,j,k] or [i,j,k,l]..]
  lines     segs=[[[x,y,z],[x,y,z]]..]          centrelines (lattice members)
  polyline  pts=[[x,y,z]..], closed=bool
  extrude   pts=planar profile, closed=bool (default True), holes=[profiles], vec=[dx,dy,dz], cap=bool,
            smooth=bool (closed profile came from a blob: adapters may fit a periodic curve),
            holes_smooth=[bool] (the same, per hole)
  pipe      pts=path polyline, r=[r_start, r_end]
"""
import math

PREFIX = "MVM_"

# Fixed layer standard (methodology): no project prefixes.
LAYERS = [
    ("Massing", (190, 190, 190)),
    ("Structure", (95, 95, 95)),
    ("Roof", (225, 195, 140)),
    ("Facade", (0, 0, 0)),
    ("Facade::Glass", (100, 165, 215)),
    ("Facade::Opaque", (185, 150, 115)),
    ("Circulation", (0, 0, 0)),
    ("Circulation::Core", (165, 125, 90)),
    ("Circulation::Stairs", (205, 120, 60)),
    ("Interior elements", (150, 110, 165)),
]


# ---------------------------------------------------------------- vectors --
def v_add(a, b):
    return [a[0] + b[0], a[1] + b[1], a[2] + b[2]]


def v_sub(a, b):
    return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]


def v_mul(a, s):
    return [a[0] * s, a[1] * s, a[2] * s]


def v_len(a):
    return math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])


def v_unit(a):
    n = v_len(a)
    return [a[0] / n, a[1] / n, a[2] / n] if n > 1e-12 else [0.0, 0.0, 0.0]


def v_cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


# --------------------------------------------------------------- outlines --
def _signed_area(pts):
    s = 0.0
    for i in range(len(pts)):
        x0, y0 = pts[i][0], pts[i][1]
        x1, y1 = pts[(i + 1) % len(pts)][0], pts[(i + 1) % len(pts)][1]
        s += x0 * y1 - x1 * y0
    return 0.5 * s


def ensure_ccw(pts):
    return list(pts) if _signed_area(pts) > 0 else list(reversed(pts))


def _dedupe(pts, tol=1e-6):
    out = []
    for p in pts:
        if not out or abs(p[0] - out[-1][0]) > tol or abs(p[1] - out[-1][1]) > tol:
            out.append([p[0], p[1]])
    if len(out) > 1 and abs(out[0][0] - out[-1][0]) <= tol and abs(out[0][1] - out[-1][1]) <= tol:
        out.pop()
    return out


def fillet_poly(pts_r, seg_per_90=6):
    """Closed polygon [[x, y, r], ..] -> polyline with a circular fillet of radius r at each vertex."""
    n = len(pts_r)
    out = []
    for i in range(n):
        a, b, c = pts_r[i - 1], pts_r[i], pts_r[(i + 1) % n]
        r = b[2] if len(b) > 2 else 0.0
        u = v_unit([a[0] - b[0], a[1] - b[1], 0.0])
        w = v_unit([c[0] - b[0], c[1] - b[1], 0.0])
        cosang = max(-1.0, min(1.0, u[0] * w[0] + u[1] * w[1]))
        ang = math.acos(cosang)
        if r <= 0 or ang < 1e-3 or ang > math.pi - 1e-3:
            out.append([b[0], b[1]])
            continue
        d = r / math.tan(ang / 2.0)
        t1 = [b[0] + u[0] * d, b[1] + u[1] * d]
        t2 = [b[0] + w[0] * d, b[1] + w[1] * d]
        m = v_unit([u[0] + w[0], u[1] + w[1], 0.0])
        h = r / math.sin(ang / 2.0)
        o = [b[0] + m[0] * h, b[1] + m[1] * h]
        a1 = math.atan2(t1[1] - o[1], t1[0] - o[0])
        a2 = math.atan2(t2[1] - o[1], t2[0] - o[0])
        sweep = a2 - a1
        while sweep > math.pi:
            sweep -= 2 * math.pi
        while sweep < -math.pi:
            sweep += 2 * math.pi
        k = max(2, int(math.ceil(abs(sweep) / (math.pi / 2) * seg_per_90)))
        for j in range(k + 1):
            t = a1 + sweep * j / float(k)
            out.append([o[0] + r * math.cos(t), o[1] + r * math.sin(t)])
    return _dedupe(out)


def circle_pts(c, r, seg=72):
    return [[c[0] + r * math.cos(2 * math.pi * i / seg), c[1] + r * math.sin(2 * math.pi * i / seg)] for i in range(seg)]


def keepout_poly(k, tcfg, ctx):
    """Plan footprint a tree crown must stay out of: the outline grown by the clearance, or shrunk by "inset"."""
    return offset_closed(outline(k["outline"], ctx), k.get("inset", -tcfg.get("clearance", 400)))


def make_ctx(spec):
    """Reference table for parametric outlines: shell centres and element outlines by id."""
    return {"shells": dict((s["id"], s["c"]) for s in spec["canopy"]["shells"]),
            "elements": dict((e["id"], e["outline"]) for e in spec.get("elements", []) if "outline" in e)}


def _ref_outline(key, ctx):
    if not ctx or key not in ctx["elements"]:
        raise ValueError("unresolved outline reference: " + str(key))
    return ctx["elements"][key]


def _centre(o, ctx):
    """Centre of a shape: explicit "c", or "at" a shell id / element id, plus an optional "offset"."""
    if "at" in o:
        key = o["at"]
        if ctx and key in ctx["shells"]:
            c = ctx["shells"][key]
        else:
            ref = _ref_outline(key, ctx)
            if ref["type"] == "circle":
                c = _centre(ref, ctx)
            else:
                pts = outline(ref, ctx)
                c = [sum(p[0] for p in pts) / float(len(pts)), sum(p[1] for p in pts) / float(len(pts))]
    else:
        c = o["c"]
    off = o.get("offset", [0, 0])
    return [c[0] + off[0], c[1] + off[1]]


def outline(o, ctx=None):
    """Outline dict -> closed CCW [[x, y], ..].
    Types: rrect, circle (c or at), poly (vertices may carry a fillet radius), blob (smooth union of
    shapes; blob members may also be capsules), ref (another element's outline, optionally grown)."""
    t = o["type"]
    if t == "circle":
        return circle_pts(_centre(o, ctx), _radius(o, ctx), o.get("seg", 72))
    if t == "rrect":
        x0, x1 = o["x"]
        y0, y1 = o["y"]
        r = o.get("r", [0, 0, 0, 0])
        if not isinstance(r, list):
            r = [r, r, r, r]
        return ensure_ccw(fillet_poly([[x0, y0, r[0]], [x1, y0, r[1]], [x1, y1, r[2]], [x0, y1, r[3]]]))
    if t == "poly":
        return ensure_ccw(fillet_poly(o["pts"]))
    if t == "blob":
        if ctx is None:
            return _blob(o, ctx)
        cache = ctx.setdefault("_blob_cache", {})
        key = repr(o)
        if key not in cache:
            cache[key] = _blob(o, ctx)
        return [list(p) for p in cache[key]]
    if t == "ref":
        pts = outline(_ref_outline(o["at"], ctx), ctx)
        return offset_closed(pts, -o["grow"]) if o.get("grow") else pts
    raise ValueError("unknown outline type: " + str(t))


def is_smooth(o, ctx):
    """True when an outline resolves to a blob, so adapters can fit a periodic curve."""
    while o["type"] == "ref":
        o = _ref_outline(o["at"], ctx)
    return o["type"] == "blob"


def _radius(o, ctx):
    r = o.get("r")
    if r is None:
        r = _ref_outline(o["at"], ctx)["r"]
    return r + o.get("grow", 0.0)


# ---------------------------------------------------- blob (smooth union) --
def _sdf(o, ctx):
    """Shape -> signed distance function f(x, y), negative inside; "grow" expands the shape."""
    t = o["type"]
    grow = o.get("grow", 0.0)
    if t == "circle":
        c, r = _centre(o, ctx), _radius(o, ctx)
        return lambda x, y: math.hypot(x - c[0], y - c[1]) - r
    if t == "rrect":
        x0, x1 = o["x"]
        y0, y1 = o["y"]
        rr = o.get("r", [0, 0, 0, 0])
        if not isinstance(rr, list):
            rr = [rr, rr, rr, rr]
        cx, cy, hx, hy = (x0 + x1) / 2.0, (y0 + y1) / 2.0, (x1 - x0) / 2.0, (y1 - y0) / 2.0

        def f(x, y):
            qx, qy = x - cx, y - cy
            r = rr[2] if qx > 0 and qy > 0 else rr[1] if qx > 0 else rr[3] if qy > 0 else rr[0]
            ax, ay = abs(qx) - hx + r, abs(qy) - hy + r
            return min(max(ax, ay), 0.0) + math.hypot(max(ax, 0.0), max(ay, 0.0)) - r - grow
        return f
    if t == "ref":
        ref = dict(_ref_outline(o["at"], ctx))
        ref["grow"] = ref.get("grow", 0.0) + grow
        return _sdf(ref, ctx)
    if t == "capsule":
        a, b = _centre(o["a"], ctx), _centre(o["b"], ctx)
        r = o["r"] + grow
        dx, dy = b[0] - a[0], b[1] - a[1]
        L2 = dx * dx + dy * dy or 1e-9

        def cap(x, y):
            t = max(0.0, min(1.0, ((x - a[0]) * dx + (y - a[1]) * dy) / L2))
            return math.hypot(x - a[0] - t * dx, y - a[1] - t * dy) - r
        return cap
    pts = outline(o, ctx)

    def g(x, y):
        best = None
        n = len(pts)
        for i in range(n):
            ax, ay = pts[i]
            bx, by = pts[(i + 1) % n]
            dx, dy = bx - ax, by - ay
            L2 = dx * dx + dy * dy
            t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / L2))
            d = math.hypot(x - ax - t * dx, y - ay - t * dy)
            best = d if best is None else min(best, d)
        return (-best if point_in_poly(x, y, pts) else best) - grow
    return g


def _bbox(o, ctx):
    t = o["type"]
    if t == "circle":
        c, r = _centre(o, ctx), _radius(o, ctx)
        return [c[0] - r, c[1] - r, c[0] + r, c[1] + r]
    if t == "ref":
        b = _bbox(_ref_outline(o["at"], ctx), ctx)
        g = o.get("grow", 0.0)
        return [b[0] - g, b[1] - g, b[2] + g, b[3] + g]
    if t == "capsule":
        a, b = _centre(o["a"], ctx), _centre(o["b"], ctx)
        r = o["r"] + o.get("grow", 0.0)
        return [min(a[0], b[0]) - r, min(a[1], b[1]) - r, max(a[0], b[0]) + r, max(a[1], b[1]) + r]
    pts = outline(o, ctx)
    g = o.get("grow", 0.0)
    return [min(p[0] for p in pts) - g, min(p[1] for p in pts) - g, max(p[0] for p in pts) + g, max(p[1] for p in pts) + g]


def _smin(a, b, k):
    if k <= 0:
        return min(a, b)
    h = max(k - abs(a - b), 0.0) / k
    return min(a, b) - h * h * k * 0.25


def _blob(o, ctx):
    """Smooth union of shapes (polynomial smooth-min, fillet ~ "smooth" mm), contoured by marching squares."""
    fs = [_sdf(s, ctx) for s in o["shapes"]]
    k = float(o.get("smooth", 2000))
    step = float(o.get("grid", 200))

    def field(x, y):
        d = fs[0](x, y)
        for f in fs[1:]:
            d = _smin(d, f(x, y), k)
        return d

    boxes = [_bbox(s, ctx) for s in o["shapes"]]
    pad = k + 3 * step
    x0, y0 = min(b[0] for b in boxes) - pad, min(b[1] for b in boxes) - pad
    nx = int((max(b[2] for b in boxes) + pad - x0) / step) + 2
    ny = int((max(b[3] for b in boxes) + pad - y0) / step) + 2
    val = [[field(x0 + i * step, y0 + j * step) for i in range(nx)] for j in range(ny)]

    def edge_pt(key):
        kind, i, j = key
        i2, j2 = (i + 1, j) if kind == "h" else (i, j + 1)
        a, b = val[j][i], val[j2][i2]
        t = a / (a - b) if a != b else 0.5
        return [x0 + (i + t * (i2 - i)) * step, y0 + (j + t * (j2 - j)) * step]

    nbr = {}

    def link(p, q):
        nbr.setdefault(p, []).append(q)
        nbr.setdefault(q, []).append(p)

    for j in range(ny - 1):
        for i in range(nx - 1):
            va, vb, vc, vd = val[j][i], val[j][i + 1], val[j + 1][i + 1], val[j + 1][i]
            case = (va < 0) | ((vb < 0) << 1) | ((vc < 0) << 2) | ((vd < 0) << 3)
            if case in (0, 15):
                continue
            e = [("h", i, j), ("v", i + 1, j), ("h", i, j + 1), ("v", i, j)]
            centre_in = (va + vb + vc + vd) < 0
            pairs = {1: [(3, 0)], 2: [(0, 1)], 3: [(3, 1)], 4: [(1, 2)], 6: [(0, 2)], 7: [(3, 2)], 8: [(2, 3)],
                     9: [(0, 2)], 11: [(1, 2)], 12: [(1, 3)], 13: [(0, 1)], 14: [(3, 0)],
                     5: [(3, 2), (0, 1)] if centre_in else [(3, 0), (1, 2)],
                     10: [(0, 3), (1, 2)] if centre_in else [(0, 1), (2, 3)]}[case]
            for a, b in pairs:
                link(e[a], e[b])

    loops, seen = [], set()
    for start in nbr:
        if start in seen:
            continue
        loop, prev, cur = [], None, start
        while cur not in seen:
            seen.add(cur)
            loop.append(edge_pt(cur))
            nxt = [q for q in nbr[cur] if q != prev and q not in seen]
            if not nxt:
                break
            prev, cur = cur, nxt[0]
        if len(loop) > 2:
            loops.append(loop)
    best = max(loops, key=lambda L: abs(_signed_area(L)))
    simple = ensure_ccw(_simplify_closed(best, float(o.get("tol", 15))))
    return _densify_closed(simple, float(o.get("max_seg", 1000)))


def _densify_closed(pts, max_seg):
    """Split long segments so a periodic spline through the vertices stays on the polygon."""
    out = []
    n = len(pts)
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        k = max(1, int(math.ceil(math.hypot(b[0] - a[0], b[1] - a[1]) / max_seg)))
        for j in range(k):
            out.append([a[0] + (b[0] - a[0]) * j / float(k), a[1] + (b[1] - a[1]) * j / float(k)])
    return out


def _simplify_closed(pts, tol):
    """Douglas-Peucker on a closed polyline."""
    def dp(seq):
        if len(seq) < 3:
            return seq
        ax, ay = seq[0]
        bx, by = seq[-1]
        dx, dy = bx - ax, by - ay
        L = math.hypot(dx, dy) or 1e-9
        far, idx = -1.0, 0
        for i in range(1, len(seq) - 1):
            d = abs(dy * (seq[i][0] - ax) - dx * (seq[i][1] - ay)) / L
            if d > far:
                far, idx = d, i
        if far <= tol:
            return [seq[0], seq[-1]]
        return dp(seq[:idx + 1])[:-1] + dp(seq[idx:])
    n = len(pts)
    far = max(range(n), key=lambda i: (pts[i][0] - pts[0][0]) ** 2 + (pts[i][1] - pts[0][1]) ** 2)
    out = dp(pts[:far + 1])[:-1] + dp(pts[far:] + [pts[0]])[:-1]
    return out if len(out) >= 3 else pts


def offset_closed(pts, d):
    """Miter offset of a closed CCW polyline; d > 0 moves inward."""
    pts = _dedupe(pts)
    n = len(pts)
    out = []
    for i in range(n):
        a, b, c = pts[i - 1], pts[i], pts[(i + 1) % n]
        e1 = v_unit([b[0] - a[0], b[1] - a[1], 0.0])
        e2 = v_unit([c[0] - b[0], c[1] - b[1], 0.0])
        n1 = [-e1[1], e1[0]]
        n2 = [-e2[1], e2[0]]
        m = v_unit([n1[0] + n2[0], n1[1] + n2[1], 0.0])
        cos_half = max(0.2, m[0] * n1[0] + m[1] * n1[1])
        out.append([b[0] + m[0] * d / cos_half, b[1] + m[1] * d / cos_half])
    return out


def point_in_poly(x, y, pts):
    inside = False
    n = len(pts)
    for i in range(n):
        x0, y0 = pts[i][0], pts[i][1]
        x1, y1 = pts[(i + 1) % n][0], pts[(i + 1) % n][1]
        if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
            inside = not inside
    return inside


def lift(pts2d, z):
    return [[p[0], p[1], z] for p in pts2d]


# ------------------------------------------------------------------ meshes --
def tube_mesh(segs, radius, sides=6):
    """Open tubes around straight segments, merged into one mesh."""
    v, f = [], []
    for p, q in segs:
        axis = v_unit(v_sub(q, p))
        ref = [0.0, 0.0, 1.0] if abs(axis[2]) < 0.9 else [1.0, 0.0, 0.0]
        u = v_unit(v_cross(axis, ref))
        w = v_cross(axis, u)
        base = len(v)
        for end in (p, q):
            for k in range(sides):
                t = 2 * math.pi * k / sides
                off = v_add(v_mul(u, radius * math.cos(t)), v_mul(w, radius * math.sin(t)))
                v.append(v_add(end, off))
        for k in range(sides):
            k2 = (k + 1) % sides
            f.append([base + k, base + k2, base + sides + k2, base + sides + k])
    return v, f


def pipe_mesh(pts, r0, r1, sides=12):
    """Capped tube along a polyline, radius tapering r0 -> r1 (parallel-transport frames)."""
    n = len(pts)
    lens = [0.0]
    for k in range(1, n):
        lens.append(lens[-1] + v_len(v_sub(pts[k], pts[k - 1])))
    total = lens[-1] or 1.0
    tang = [v_unit(v_sub(pts[min(n - 1, k + 1)], pts[max(0, k - 1)])) for k in range(n)]
    ref = [0.0, 0.0, 1.0] if abs(tang[0][2]) < 0.9 else [1.0, 0.0, 0.0]
    u = v_unit(v_cross(tang[0], ref))
    v, f = [], []
    for k in range(n):
        if k:
            u = v_unit(v_cross(v_cross(tang[k], u), tang[k]))
        w = v_cross(tang[k], u)
        r = r0 + (r1 - r0) * lens[k] / total
        for s in range(sides):
            t = 2 * math.pi * s / sides
            v.append(v_add(pts[k], v_add(v_mul(u, r * math.cos(t)), v_mul(w, r * math.sin(t)))))
    for k in range(n - 1):
        for s in range(sides):
            s2 = (s + 1) % sides
            f.append([k * sides + s, k * sides + s2, (k + 1) * sides + s2, (k + 1) * sides + s])
    for k, c in ((0, pts[0]), (n - 1, pts[-1])):
        v.append(list(c))
        ci = len(v) - 1
        for s in range(sides):
            a, b = k * sides + s, k * sides + (s + 1) % sides
            f.append([ci, b, a] if k == 0 else [ci, a, b])
    return v, f


def _strip(ring_a, ring_b):
    """Triangulate between two closed rings of (angle, node_id), angles ascending."""
    na, nb = len(ring_a), len(ring_b)
    if na == 1:
        return [[ring_a[0][1], ring_b[j][1], ring_b[(j + 1) % nb][1]] for j in range(nb)]
    a0 = ring_a[0][0]
    j0 = min(range(nb), key=lambda j: abs(math.atan2(math.sin(ring_b[j][0] - a0), math.cos(ring_b[j][0] - a0))))
    b = ring_b[j0:] + ring_b[:j0]
    bang = [b[0][0]]
    for j in range(1, nb):
        x = b[j][0]
        while x <= bang[-1]:
            x += 2 * math.pi
        bang.append(x)
    while bang[0] - a0 > math.pi:
        bang = [x - 2 * math.pi for x in bang]
    while a0 - bang[0] > math.pi:
        bang = [x + 2 * math.pi for x in bang]
    aang = [x if x >= a0 else x + 2 * math.pi for x in [r[0] for r in ring_a]]
    tris = []
    i = j = 0
    while i < na or j < nb:
        an = aang[i + 1] if i + 1 < na else a0 + 2 * math.pi
        bn = bang[j + 1] if j + 1 < nb else bang[0] + 2 * math.pi
        if j >= nb or (i < na and an <= bn):
            tris.append([ring_a[i % na][1], ring_a[(i + 1) % na][1], b[j % nb][1]])
            i += 1
        else:
            tris.append([ring_a[i % na][1], b[(j + 1) % nb][1], b[j % nb][1]])
            j += 1
    return tris


# ------------------------------------------------------------------ canopy --
def shell_params(cfg):
    """Resolved shell parameters: module values, per-shell overrides and the rim tilt.
    Tilt: the rim lies on an inclined plane through the shell centre at the eave height; it is "rise"
    higher at the rim on the side "u" points to and "rise" lower opposite. Modes: global (one plan
    direction dir_deg), radial (from "centre": sense "out" = rims high away from it, "in" = rims high
    toward it), none. "slope" (rise / R) overrides "rise" so every shell tilts by the same angle.
    A shell's own "tilt" dict overrides the module tilt."""
    mod = cfg["module"]
    tilt = mod.get("tilt", {"mode": "none"})
    out = []
    for s in cfg["shells"]:
        t = dict(tilt)
        t.update(s.get("tilt", {}))
        mode = t.get("mode", "global" if "dir_deg" in t else "none")
        u, rise = [0.0, 0.0], float(t.get("rise", 0.0))
        if mode == "global" or "dir_deg" in s.get("tilt", {}):
            a = math.radians(t.get("dir_deg", 270.0))
            u = [math.cos(a), math.sin(a)]
        elif mode == "radial":
            c0 = t.get("centre", [0.0, 0.0])
            d = [s["c"][0] - c0[0], s["c"][1] - c0[1]]
            n = math.hypot(d[0], d[1])
            if n > 1e-6:
                sign = -1.0 if t.get("sense", "out") == "in" else 1.0
                u = [sign * d[0] / n, sign * d[1] / n]
            if t.get("blend_dir_deg") is not None:
                a = math.radians(t["blend_dir_deg"])
                w = float(t.get("blend", 0.5))
                v = [(1 - w) * u[0] + w * math.cos(a), (1 - w) * u[1] + w * math.sin(a)]
                n = math.hypot(v[0], v[1]) or 1.0
                u = [v[0] / n, v[1] / n]
        else:
            rise = 0.0
        if mode != "none" and "slope" in t:
            rise = float(t["slope"]) * s.get("R", mod["R"])
        out.append({
            "id": s["id"], "c": s["c"],
            "R": s.get("R", mod["R"]), "apex": s.get("apex", mod["apex"]),
            "eave": s.get("eave", mod["eave"]), "p": s.get("p", mod["profile_p"]),
            "k": s.get("k", mod.get("profile_k", 1.0)), "u": u, "rise": rise,
        })
    return out


def eave_at(sh, x, y):
    """Rim height of a shell above plan point (x, y): its inclined rim plane."""
    return sh["eave"] + sh["rise"] * ((x - sh["c"][0]) * sh["u"][0] + (y - sh["c"][1]) * sh["u"][1]) / sh["R"]


class Canopy(object):
    """Cluster of umbrella shells. The roof is the upper envelope of the domes,
    so shells meet along valleys (gutters) and the plan reads as a Voronoi-like tiling."""

    def __init__(self, cfg):
        mod = cfg["module"]
        self.cfg = cfg
        self.mod = mod
        self.shells = shell_params(cfg)
        self.nb_samples = int(mod.get("boundary_samples", 144))
        self.bounds = [self._boundary(i) for i in range(len(self.shells))]

    def dome(self, i, x, y):
        s = self.shells[i]
        r = math.hypot(x - s["c"][0], y - s["c"][1])
        if r > s["R"]:
            return None
        # bell profile: k = 1 gives a plain dome, k > 1 flares the eave (inflection, thin cantilevered lip);
        # the eave term is the inclined rim plane, so the apex stays put while the rim tilts
        e = eave_at(s, x, y)
        return e + (s["apex"] - e) * (1.0 - (r / s["R"]) ** s["p"]) ** s["k"]

    def _rival(self, i, x, y):
        zi = self.dome(i, x, y)
        if zi is None:
            return -2
        for j in range(len(self.shells)):
            if j != i:
                zj = self.dome(j, x, y)
                if zj is not None and zj > zi + 1e-6:
                    return j
        return -1

    def _ray(self, i, th):
        s = self.shells[i]
        cx, cy, R = s["c"][0], s["c"][1], s["R"]
        ct, st = math.cos(th), math.sin(th)
        steps = 96
        prev = 0.0
        for k in range(1, steps + 1):
            r = R * k / float(steps)
            if self._rival(i, cx + r * ct, cy + r * st) >= 0:
                lo, hi = prev, r
                for _ in range(30):
                    mid = 0.5 * (lo + hi)
                    if self._rival(i, cx + mid * ct, cy + mid * st) >= 0:
                        hi = mid
                    else:
                        lo = mid
                return lo, self._rival(i, cx + hi * ct, cy + hi * st)
            prev = r
        return R, -1

    def _boundary(self, i):
        n = self.nb_samples
        return [self._ray(i, 2 * math.pi * k / n) for k in range(n)]

    def rmax(self, i, th):
        n = self.nb_samples
        u = (th % (2 * math.pi)) / (2 * math.pi) * n
        k = int(math.floor(u)) % n
        t = u - math.floor(u)
        return self.bounds[i][k][0] * (1 - t) + self.bounds[i][(k + 1) % n][0] * t

    def point(self, i, r, th):
        s = self.shells[i]
        x = s["c"][0] + r * math.cos(th)
        y = s["c"][1] + r * math.sin(th)
        z = self.dome(i, x, y)
        return [x, y, z if z is not None else eave_at(s, x, y)]

    def boundary_runs(self, i):
        """Split the cell boundary into runs: (-1, pts) eave arcs, (j, pts) valley shared with shell j."""
        n = self.nb_samples
        b = self.bounds[i]
        start = 0
        for k in range(n):
            if b[k][1] != b[k - 1][1]:
                start = k
                break
        runs = []
        cur, pts = None, []
        for m in range(n + 1):
            k = (start + m) % n
            th = 2 * math.pi * k / n
            p = self.point(i, b[k][0], th)
            if cur is None:
                cur, pts = b[k][1], [p]
            elif b[k][1] == cur and m < n:
                pts.append(p)
            else:
                pts.append(p)
                runs.append((cur, pts))
                cur, pts = b[k][1], [p]
        return runs

    # ---- geometry ----
    def skin(self, i, rings=12):
        n = self.nb_samples
        v = [self.point(i, 0.0, 0.0)]
        f = []
        for k in range(1, rings + 1):
            t = k / float(rings)
            for j in range(n):
                th = 2 * math.pi * j / n
                v.append(self.point(i, t * self.bounds[i][j][0], th))
        for j in range(n):
            f.append([0, 1 + j, 1 + (j + 1) % n])
        for k in range(1, rings):
            a0, b0 = 1 + (k - 1) * n, 1 + k * n
            for j in range(n):
                j2 = (j + 1) % n
                f.append([a0 + j, b0 + j, b0 + j2, a0 + j2])
        return v, f

    def lattice(self, i):
        """Triangulated polar net (planar triangles, ~member_len members), clipped to the cell."""
        mod = self.mod
        rings = int(mod.get("rings", 7))
        L = float(mod.get("member_len", 1500))
        nodes = [self.point(i, 0.0, 0.0)]
        prev = [(0.0, 0)]
        tris = []
        r_mean = sum(b[0] for b in self.bounds[i]) / len(self.bounds[i])
        for k in range(1, rings + 1):
            t = k / float(rings)
            nk = max(6, int(round(2 * math.pi * t * r_mean / L)))
            off = (0.5 if k % 2 else 0.0) * 2 * math.pi / nk
            ring = []
            for m in range(nk):
                th = off + 2 * math.pi * m / nk
                th = th % (2 * math.pi)
                nodes.append(self.point(i, t * self.rmax(i, th), th))
                ring.append((th, len(nodes) - 1))
            ring.sort()
            tris += _strip(prev, ring)
            prev = ring
        edges = set()
        for a, b, c in tris:
            for p, q in ((a, b), (b, c), (c, a)):
                edges.add((min(p, q), max(p, q)))
        segs = [[nodes[p], nodes[q]] for p, q in sorted(edges)]
        return nodes, tris, segs


def _ribbon(pts, width, depth):
    """V-channel strip following a 3D polyline (gutter)."""
    v, f = [], []
    n = len(pts)
    for k in range(n):
        a = pts[max(0, k - 1)]
        b = pts[min(n - 1, k + 1)]
        tng = v_unit([b[0] - a[0], b[1] - a[1], 0.0])
        nrm = [-tng[1], tng[0], 0.0]
        p = pts[k]
        v.append([p[0] + nrm[0] * width / 2, p[1] + nrm[1] * width / 2, p[2] + depth])
        v.append([p[0], p[1], p[2]])
        v.append([p[0] - nrm[0] * width / 2, p[1] - nrm[1] * width / 2, p[2] + depth])
    for k in range(n - 1):
        a, b = 3 * k, 3 * (k + 1)
        f.append([a, b, b + 1, a + 1])
        f.append([a + 1, b + 1, b + 2, a + 2])
    return v, f


def _bezier(p0, p1, p2, n=10):
    out = []
    for k in range(n + 1):
        t = k / float(n)
        a, b, c = (1 - t) ** 2, 2 * t * (1 - t), t * t
        out.append([a * p0[j] + b * p1[j] + c * p2[j] for j in range(3)])
    return out


# ------------------------------------------------------------------ build --
def build(spec):
    """Spec dict -> list of primitives."""
    prims = []
    lv = dict((l["name"], l["elev"]) for l in spec["levels"])
    ctx = make_ctx(spec)

    def z_of(val):
        return float(lv[val]) if isinstance(val, str) else float(val)

    def add(kind, layer, name, **kw):
        d = {"kind": kind, "layer": layer, "name": PREFIX + name}
        d.update(kw)
        prims.append(d)

    # ---- canopy (Roof) ----
    can_cfg = spec["canopy"]
    can = Canopy(can_cfg)
    mod = can_cfg["module"]
    for i, s in enumerate(can.shells):
        sid = s["id"]
        if mod.get("skin", True):
            v, f = can.skin(i)
            add("mesh", "Roof", sid + "_skin", v=v, f=f)
        nodes, tris, segs = can.lattice(i)
        add("lines", "Roof", sid + "_lattice_axes", segs=segs)
        v, f = tube_mesh(segs, mod.get("member_d", 140) / 2.0)
        add("mesh", "Roof", sid + "_lattice", v=v, f=f)
        for nb, pts in can.boundary_runs(i):
            if nb == -1 and len(pts) > 1:
                add("pipe", "Roof", sid + "_eave_beam", pts=pts, r=[mod.get("edge_d", 260) / 2.0] * 2)
            elif nb > i and len(pts) > 1:
                v, f = _ribbon(pts, mod.get("gutter_w", 900), mod.get("gutter_depth", -150))
                add("mesh", "Roof", "gutter_" + sid + "_" + can.shells[nb]["id"], v=v, f=f)

    # ---- tree columns (Structure) ----
    tcfg = spec["trees"]
    keepout = []
    for k in tcfg.get("keepout", []):
        keepout.append((keepout_poly(k, tcfg, ctx), z_of(k["top"]) + tcfg.get("clearance", 400)))
    def clashes(br):
        for p in br:
            for poly, ztop in keepout:
                if p[2] < ztop and point_in_poly(p[0], p[1], poly):
                    return True
        return False

    for i, s in enumerate(can.shells):
        cx, cy = s["c"]
        z0 = z_of(tcfg["base"])
        zb = z_of(tcfg["branch_z"])
        n_b = int(tcfg.get("branches", 6))
        rho = tcfg.get("reach", 0.5)
        # Crowns adapt to the enclosure: a clashing branch first shortens its reach;
        # only if that fails is the whole crown lifted above the roof keepout.
        for _ in range(2):
            branches, ok = [], True
            for k in range(n_b):
                th = math.radians(tcfg.get("branch_rot", 15)) + 2 * math.pi * k / n_b
                r_full = min(rho * s["R"], 0.85 * can.rmax(i, th))
                for frac in (1.0, 0.8, 0.6, 0.45, 0.3):
                    p2 = can.point(i, r_full * frac, th)
                    p2[2] -= mod.get("member_d", 140)
                    p1 = [cx, cy, zb + tcfg.get("lift", 0.35) * (p2[2] - zb)]
                    br = _bezier([cx, cy, zb], p1, p2)
                    if not clashes(br):
                        break
                else:
                    ok = False
                branches.append(br)
            if ok or not keepout:
                break
            zb = max(zb, max(zt for _, zt in keepout))
        add("pipe", "Structure", "T_" + s["id"] + "_trunk", pts=[[cx, cy, z0], [cx, cy, zb + 200]],
            r=[tcfg["trunk_d"][0] / 2.0, tcfg["trunk_d"][1] / 2.0])
        for k, br in enumerate(branches):
            add("pipe", "Structure", "T_" + s["id"] + "_branch_" + str(k + 1), pts=br,
                r=[tcfg["branch_d"][0] / 2.0, tcfg["branch_d"][1] / 2.0])

    # ---- building, site and fit-out elements ----
    for e in spec["elements"]:
        k, layer, name = e["kind"], e["layer"], e["id"]
        if k in ("slab", "solid", "mass"):
            if k == "slab":
                z0, z1 = z_of(e["top"]) - e["t"], z_of(e["top"])
            else:
                z0, z1 = z_of(e["z"][0]), z_of(e["z"][1])
            holes = [lift(outline(h, ctx), z0) for h in e.get("holes", [])]
            add("extrude", layer, name, pts=lift(outline(e["outline"], ctx), z0), holes=holes,
                vec=[0, 0, z1 - z0], cap=True, smooth=is_smooth(e["outline"], ctx),
                holes_smooth=[is_smooth(h, ctx) for h in e.get("holes", [])])
        elif k == "ring":
            z0, z1 = z_of(e["z"][0]), z_of(e["z"][1])
            out = outline(e["outline"], ctx)
            smooth = is_smooth(e["outline"], ctx)
            add("extrude", layer, name, pts=lift(out, z0), holes=[lift(offset_closed(out, e["t"]), z0)],
                vec=[0, 0, z1 - z0], cap=True, smooth=smooth, holes_smooth=[smooth])
        elif k == "glass":
            z0, z1 = z_of(e["z"][0]), z_of(e["z"][1])
            pts = outline(e["outline"], ctx)
            if e.get("inset"):
                pts = offset_closed(pts, e["inset"])
            add("extrude", layer, name, pts=lift(pts, z0), holes=[], vec=[0, 0, z1 - z0], cap=False,
                smooth=is_smooth(e["outline"], ctx))
        elif k == "panel":
            z0, z1 = z_of(e["z"][0]), z_of(e["z"][1])
            for n, path in enumerate(e["paths"]):
                add("extrude", layer, name + "_" + str(n + 1), pts=lift(path, z0), holes=[],
                    vec=[0, 0, z1 - z0], cap=False, closed=False)
        elif k == "stair":
            prims.extend(_stair(e, z_of))
        elif k == "box":
            cx, cy = e["c"]
            dx, dy = e["size"][0] / 2.0, e["size"][1] / 2.0
            a = math.radians(e.get("rot", 0))
            ca, sa = math.cos(a), math.sin(a)
            pts = [[cx + ca * x - sa * y, cy + sa * x + ca * y] for x, y in ((-dx, -dy), (dx, -dy), (dx, dy), (-dx, dy))]
            z0 = z_of(e["z"])
            add("extrude", layer, name, pts=lift(pts, z0), holes=[], vec=[0, 0, e["size"][2]], cap=True)
        else:
            raise ValueError("unknown element kind: " + k)
    return prims


def _stair(e, z_of):
    """Straight flight as a stepped solid: side profile extruded across the width."""
    x0, y0 = e["start"]
    z0, z1 = z_of(e["z"][0]), z_of(e["z"][1])
    n = int(round((z1 - z0) / e.get("riser", 170)))
    rise = (z1 - z0) / n
    tread = e.get("tread", 300)
    a = math.radians(e["dir"])
    d = [math.cos(a), math.sin(a), 0.0]
    side = [-d[1], d[0], 0.0]
    prof = [[x0, y0, z0 - e.get("waist", 250)]]
    for k in range(n):
        s0 = k * tread
        prof.append([x0 + d[0] * s0, y0 + d[1] * s0, z0 + (k + 1) * rise])
        prof.append([x0 + d[0] * (s0 + tread), y0 + d[1] * (s0 + tread), z0 + (k + 1) * rise])
    prof.append([x0 + d[0] * n * tread, y0 + d[1] * n * tread, z1 - e.get("waist", 250)])
    w = e["width"]
    return [{"kind": "extrude", "layer": e["layer"], "name": PREFIX + e["id"], "pts": _dedupe3(prof),
             "holes": [], "vec": v_mul(side, w), "cap": True}]


def _dedupe3(pts):
    out = []
    for p in pts:
        if not out or v_len(v_sub(p, out[-1])) > 1e-6:
            out.append(p)
    return out


def summarize(prims):
    counts = {}
    for p in prims:
        counts[p["layer"]] = counts.get(p["layer"], 0) + 1
    return counts


def clearance_report(spec, step=500.0):
    """Lowest canopy point above each tree keepout (the enclosure roof), sampled on a grid."""
    lv = dict((l["name"], l["elev"]) for l in spec["levels"])
    can = Canopy(spec["canopy"])
    ctx = make_ctx(spec)
    out = []
    for k in spec["trees"].get("keepout", []):
        poly = keepout_poly(k, spec["trees"], ctx)
        top = lv[k["top"]] if isinstance(k["top"], str) else float(k["top"])
        xs = [p[0] for p in poly]
        ys = [p[1] for p in poly]
        worst, uncovered, x = None, 0, min(xs)
        while x <= max(xs):
            y = min(ys)
            while y <= max(ys):
                if point_in_poly(x, y, poly):
                    zs = [z for z in (can.dome(i, x, y) for i in range(len(can.shells))) if z is not None]
                    if zs:
                        d = max(zs) - top
                        worst = d if worst is None else min(worst, d)
                    else:
                        uncovered += 1
                y += step
            x += step
        out.append("canopy over %s min %d mm (%d pts open sky)" % (k.get("id", "keepout"), worst if worst is not None else -1, uncovered))
    return "; ".join(out)


def dome_report(spec):
    """Per shell: plan cell area and prominence (apex minus its highest valley). A dome reads as distinct
    when it has a real cell and stands clearly above the valleys around it."""
    can = Canopy(spec["canopy"])
    rows = []
    for i, s in enumerate(can.shells):
        b = can.bounds[i]
        n = len(b)
        area = sum(0.5 * b[k][0] ** 2 * (2 * math.pi / n) for k in range(n))
        valley = [p[2] for nb, pts in can.boundary_runs(i) if nb >= 0 for p in pts]
        top = max(valley) if valley else s["eave"]
        rows.append({"id": s["id"], "cell_m2": round(area / 1e6, 1), "prominence_mm": int(round(s["apex"] - top))})
    return rows

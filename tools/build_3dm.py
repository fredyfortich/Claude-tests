"""Stage 3 (offline): spec -> out/mvm_pavilion.3dm with rhino3dm, no Rhino licence needed.

Same core and layer standard as the RhinoCommon build script. Pipes become tapered tube meshes
(rhino3dm has no pipe solver); slabs, walls, glass and cores stay light Extrusion objects.
"""
import os

import rhino3dm as r3

from camera import OUT, load_spec, mvm_core


def polyline_curve(pts, closed=True):
    pl = r3.Point3dList()
    for p in list(pts) + ([pts[0]] if closed else []):
        pl.Add(float(p[0]), float(p[1]), float(p[2]))
    return r3.PolylineCurve(pl)


def to_mesh(v, f):
    m = r3.Mesh()
    for p in v:
        m.Vertices.Add(float(p[0]), float(p[1]), float(p[2]))
    for face in f:
        if len(face) == 3:
            m.Faces.AddFace(face[0], face[1], face[2])
        else:
            m.Faces.AddFace(face[0], face[1], face[2], face[3])
    m.Normals.ComputeNormals()
    m.Compact()
    return m


def extrusion(p):
    """Closed planar profile (+ holes) extruded along vec; returns an Extrusion or a Mesh (open profiles)."""
    pts, vec = p["pts"], p["vec"]
    if not p.get("closed", True):
        n = len(pts)
        v = [list(q) for q in pts] + [[q[0] + vec[0], q[1] + vec[1], q[2] + vec[2]] for q in pts]
        return to_mesh(v, [[k, k + 1, n + k + 1, n + k] for k in range(n - 1)])
    h = mvm_core.v_len(vec)
    target = [sum(q[i] for q in pts) / len(pts) + vec[i] for i in range(3)]
    for sign in (1, -1):
        ex = r3.Extrusion.Create(polyline_curve(pts), sign * h, p.get("cap", True))
        bb = ex.GetBoundingBox()
        lo, hi = (bb.Min.X, bb.Min.Y, bb.Min.Z), (bb.Max.X, bb.Max.Y, bb.Max.Z)
        inside = all(lo[i] - 1 <= target[i] <= hi[i] + 1 for i in range(3))
        if inside:
            break
    if p.get("holes"):
        inv = ex.GetProfileTransformation(0).TryGetInverse()
        inv = inv[1] if isinstance(inv, tuple) else inv
        for hole in p["holes"]:
            c = polyline_curve(list(reversed(hole)))
            c.Transform(inv)
            ex.AddInnerProfile(c)
    return ex


def main():
    spec = load_spec()
    prims = mvm_core.build(spec)
    model = r3.File3dm()
    model.Settings.ModelUnitSystem = r3.UnitSystem.Millimeters
    idx = {}
    for path, col in mvm_core.LAYERS:
        lay = r3.Layer()
        parts = path.split("::")
        lay.Name = parts[-1]
        lay.Color = (col[0], col[1], col[2], 255)
        if len(parts) > 1:
            lay.ParentLayerId = model.Layers[idx[parts[0]]].Id
        idx[path] = model.Layers.Add(lay)

    for p in prims:
        att = r3.ObjectAttributes()
        att.LayerIndex = idx[p["layer"]]
        att.Name = p["name"]
        k = p["kind"]
        if k == "mesh":
            model.Objects.AddMesh(to_mesh(p["v"], p["f"]), att)
        elif k == "lines":
            for a, b in p["segs"]:
                model.Objects.AddLine(r3.Point3d(*a), r3.Point3d(*b), att)
        elif k == "pipe":
            v, f = mvm_core.pipe_mesh(p["pts"], p["r"][0], p["r"][1])
            model.Objects.AddMesh(to_mesh(v, f), att)
        elif k == "polyline":
            model.Objects.AddCurve(polyline_curve(p["pts"], p.get("closed", False)), att)
        elif k == "extrude":
            g = extrusion(p)
            if isinstance(g, r3.Mesh):
                model.Objects.AddMesh(g, att)
            else:
                model.Objects.AddExtrusion(g, att)
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "mvm_pavilion.3dm")
    model.Write(path, 8)
    counts = mvm_core.summarize(prims)
    print("wrote", path, "objects", len(model.Objects), "prims per layer", counts)


if __name__ == "__main__":
    main()

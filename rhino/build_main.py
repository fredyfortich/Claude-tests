# -*- coding: utf-8 -*-
# ---- Rhino build entry point (single MCP call or Rhino 8 ScriptEditor) ----
# Methodology rules: one script, MVM_ prefix on every object, purge earlier MVM_ objects first
# (hidden, locked and switched-off layers included) so reruns never duplicate, fixed layer standard,
# and a short REPORT instead of a scene dump.
import Rhino
import System

REPORT_AS_ERROR = False      # True for MCP servers that only return success/failure (rhinoceros_operator)
BUILD_LATTICE_AXES = True    # ~2k centrelines for piping / Revit framing
BUILD_LATTICE_MESH = True    # light tube mesh for viewing
BUILD_SKIN = True            # canopy infill (ETFE / glass) as mesh


def ensure_layer(rdoc, path, rgb):
    idx = rdoc.Layers.FindByFullPath(path, -1)
    if idx >= 0:
        lay = rdoc.Layers[idx]
        if lay.IsLocked or not lay.IsVisible:
            lay.IsLocked = False
            lay.IsVisible = True
            rdoc.Layers.Modify(lay, idx, True)
        return idx
    parts = path.split("::")
    lay = Rhino.DocObjects.Layer()
    lay.Name = parts[-1]
    lay.Color = color(rgb)
    if len(parts) > 1:
        lay.ParentLayerId = rdoc.Layers[ensure_layer(rdoc, "::".join(parts[:-1]), (0, 0, 0))].Id
    return rdoc.Layers.Add(lay)


def purge(rdoc):
    st = Rhino.DocObjects.ObjectEnumeratorSettings()
    st.NormalObjects = True
    st.LockedObjects = True
    st.HiddenObjects = True
    st.VisibleFilter = False
    st.IncludeLights = False
    st.IncludeGrips = False
    ids = [o.Id for o in rdoc.Objects.GetObjectList(st) if (o.Attributes.Name or "").startswith(PREFIX)]
    for i in ids:
        rdoc.Objects.Unlock(i, True)
        rdoc.Objects.Show(i, True)
        rdoc.Objects.Delete(i, True)
    return len(ids)


def main():
    rdoc = Rhino.RhinoDoc.ActiveDoc
    k = unit_scale(rdoc)
    tol = rdoc.ModelAbsoluteTolerance
    layer_idx = dict((path, ensure_layer(rdoc, path, rgb)) for path, rgb in LAYERS)
    deleted = purge(rdoc)
    prims = build(SPEC)
    added, solids, closed_expected, failed = {}, 0, 0, []
    for p in prims:
        name = p["name"]
        if p["kind"] == "lines" and not BUILD_LATTICE_AXES:
            continue
        if name.endswith("_lattice") and not BUILD_LATTICE_MESH:
            continue
        if name.endswith("_skin") and not BUILD_SKIN:
            continue
        try:
            geos = to_rhino(p, k, tol)
        except Exception as ex:
            failed.append(name + ": " + str(ex))
            continue
        att = Rhino.DocObjects.ObjectAttributes()
        att.LayerIndex = layer_idx[p["layer"]]
        att.Name = name
        for g in geos:
            if isinstance(g, Rhino.Geometry.Brep):
                rdoc.Objects.AddBrep(g, att)
                if p["kind"] == "pipe" or (p["kind"] == "extrude" and p.get("cap", True) and p.get("closed", True)):
                    closed_expected += 1
                    solids += 1 if g.IsSolid else 0
            elif isinstance(g, Rhino.Geometry.Mesh):
                rdoc.Objects.AddMesh(g, att)
            else:
                rdoc.Objects.AddCurve(g, att)
            added[p["layer"]] = added.get(p["layer"], 0) + 1
    rdoc.Views.Redraw()
    report = "REPORT: purged %d | added %s | solids %d/%d | %s | failed %d %s" % (
        deleted, ", ".join("%s %d" % (lay, n) for lay, n in sorted(added.items())), solids, closed_expected,
        clearance_report(SPEC), len(failed), "; ".join(failed[:3]))
    if REPORT_AS_ERROR:
        raise Exception(report)
    print(report)


main()

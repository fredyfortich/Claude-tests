# -*- coding: utf-8 -*-
# ---- Grasshopper entry point (Rhino 8 Script component, Python 3; also GhPython in Rhino 7) ----
# Inputs (all optional, Item access unless noted; numbers in mm, points in model units):
#   spec_path  str      path to pavilion_spec.json (blank = spec embedded below)
#   centers    Point3d  List access. Canopy shell centres; replaces S1.. in order, extra points add shells
#   R, eave, apex, profile_k            canopy module (mm / exponent)
#   rings, member_len                    gridshell density
#   branches, branch_z, reach            tree columns
#   L01, L02                             level elevations (mm)
#   skin       bool     build the infill mesh (default True)
# Outputs: roof, lattice, structure, glass, opaque, cores, stairs, interior, massing, report
import copy
import json

import Rhino


def _given(name):
    v = globals().get(name)
    return v is not None and v != ""


def _spec():
    spec = json.load(open(spec_path)) if _given("spec_path") else copy.deepcopy(SPEC)
    mod = spec["canopy"]["module"]
    for key in ("R", "eave", "apex", "profile_k", "rings", "member_len"):
        if _given(key):
            mod[key] = globals()[key]
    for key in ("branches", "branch_z", "reach"):
        if _given(key):
            spec["trees"][key] = globals()[key]
    for lv in spec["levels"]:
        if _given(lv["name"]):
            lv["elev"] = globals()[lv["name"]]
    if _given("skin"):
        mod["skin"] = bool(skin)
    if _given("centers") and len(centers):
        k = unit_scale(Rhino.RhinoDoc.ActiveDoc)
        shells = spec["canopy"]["shells"]
        for n, p in enumerate(centers):
            xy = [p.X / k, p.Y / k]
            if n < len(shells):
                shells[n]["c"] = xy
            else:
                shells.append({"id": "S%d" % (n + 1), "c": xy, "conf": 1.0})
        del shells[len(centers):]
    return spec


_spec_now = _spec()
_k = unit_scale(Rhino.RhinoDoc.ActiveDoc)
_tol = Rhino.RhinoDoc.ActiveDoc.ModelAbsoluteTolerance
_out = dict((n, []) for n in ("roof", "lattice", "structure", "glass", "opaque", "cores", "stairs", "interior", "massing"))
_route = {"Roof": "roof", "Structure": "structure", "Facade::Glass": "glass", "Facade::Opaque": "opaque",
          "Circulation::Core": "cores", "Circulation::Stairs": "stairs", "Interior elements": "interior",
          "Massing": "massing"}
for _p in build(_spec_now):
    _key = "lattice" if _p["kind"] == "lines" else _route[_p["layer"]]
    _out[_key].extend(to_rhino(_p, _k, _tol))

roof, lattice, structure = _out["roof"], _out["lattice"], _out["structure"]
glass, opaque, cores, stairs = _out["glass"], _out["opaque"], _out["cores"], _out["stairs"]
interior, massing = _out["interior"], _out["massing"]
report = "%s | %s" % (", ".join("%s %d" % (n, len(v)) for n, v in sorted(_out.items())), clearance_report(_spec_now))

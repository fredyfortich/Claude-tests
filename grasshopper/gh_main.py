# -*- coding: utf-8 -*-
# ---- Grasshopper entry point (Rhino 8 Script component, Python 3; also GhPython in Rhino 7) ----
# Inputs (all optional, Item access unless noted; numbers in mm, points in model units):
#   spec_path  str      path to pavilion_spec.json (blank = spec embedded below)
#   centers    Point3d  List access. Canopy shell centres; replaces S1.. in order, extra points add shells
#   R, eave, apex, profile_k            canopy module (mm / exponent)
#   tilt_rise, tilt_dir                 inclined rims: rise (mm) at the high side, plan direction (deg, 270 = lake)
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
    if _given("tilt_rise") or _given("tilt_dir"):
        t = dict(mod.get("tilt", {"mode": "global"}))
        if _given("tilt_rise"):
            t["rise"] = float(tilt_rise)
        if _given("tilt_dir"):
            t["dir_deg"] = float(tilt_dir)
            if t.get("mode") not in ("global", "radial"):
                t["mode"] = "global"
        mod["tilt"] = t
    if _given("centers") and len(centers):
        k = unit_scale(Rhino.RhinoDoc.ActiveDoc)
        shells = spec["canopy"]["shells"]
        for n, p in enumerate(centers):
            xy = [p.X / k, p.Y / k]
            if n < len(shells):
                shells[n]["c"] = xy
            else:
                shells.append({"id": "S%d" % (n + 1), "c": xy, "conf": 1.0})
        # fewer points drop trailing shells, except shells the slab, planters or keepouts refer to by id
        used = _referenced_ids(spec)
        shells[:] = [sh for n, sh in enumerate(shells) if n < len(centers) or sh["id"] in used]
    return spec


def _referenced_ids(node, found=None):
    found = set() if found is None else found
    if isinstance(node, dict):
        if "at" in node:
            found.add(node["at"])
        for v in node.values():
            _referenced_ids(v, found)
    elif isinstance(node, list):
        for v in node:
            _referenced_ids(v, found)
    return found


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

"""Smoke-test the generated Rhino / Grasshopper scripts without Rhino.

Installs a minimal stand-in for the RhinoCommon calls the adapter uses, runs both scripts, and checks
that every primitive converts and lands on a standard layer. It catches Python and logic errors,
not RhinoCommon behaviour - the real check is one run inside Rhino 8.
"""
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Pt(object):
    def __init__(self, x=0, y=0, z=0):
        self.X, self.Y, self.Z = x, y, z

    def __add__(self, v):
        return Pt(self.X + v.X, self.Y + v.Y, self.Z + v.Z)


class Seq(list):
    def Add(self, *a):
        self.append(a[0] if len(a) == 1 else a)
        return len(self) - 1

    AddFace = Add

    @property
    def Count(self):
        return len(self)


class Geo(object):
    IsSolid = True

    def __init__(self, *a):
        self.args = a
        self.Domain = types.SimpleNamespace(Min=0.0, Max=1.0)
        self.PointAtStart = Pt()
        self.Faces = [self]

    def CreateExtrusion(self, path, cap):
        return Brep()

    def ToBrep(self):
        return Brep()


class Brep(Geo):
    @staticmethod
    def CreatePipe(rail, params, radii, blend, cap, fit, tol, ang):
        assert len(params) == 2 and len(radii) == 2 and radii[0] > 0
        return [Brep()]

    @staticmethod
    def CreatePlanarBreps(loops, tol):
        assert len(loops) >= 1
        return [Brep()]


class Mesh(Geo):
    def __init__(self):
        Geo.__init__(self)
        self.Vertices, self.Faces = Seq(), Seq()
        self.Normals = types.SimpleNamespace(ComputeNormals=lambda: True)

    def Compact(self):
        return True


class Curve(Geo):
    @staticmethod
    def CreateInterpolatedCurve(pts, degree, knot_style=None):
        assert len(pts) >= 3
        return Curve()


class Table(object):
    def __init__(self):
        self.items, self.added = [], []

    def FindByFullPath(self, path, missing):
        names = [l.FullPath for l in self.items]
        return names.index(path) if path in names else missing

    def Add(self, lay):
        parent = [l for l in self.items if l.Id == lay.ParentLayerId]
        lay.FullPath = (parent[0].FullPath + "::" if parent else "") + lay.Name
        lay.Id = len(self.items) + 1
        self.items.append(lay)
        return len(self.items) - 1

    def __getitem__(self, i):
        return self.items[i]


class Objects(object):
    def __init__(self):
        self.added = []

    def GetObjectList(self, st):
        return []

    def _add(self, g, att):
        self.added.append((type(g).__name__, att.LayerIndex, att.Name))

    AddBrep = AddMesh = AddCurve = _add


def install():
    doc = types.SimpleNamespace(Layers=Table(), Objects=Objects(), ModelUnitSystem="mm", ModelAbsoluteTolerance=0.01,
                                Views=types.SimpleNamespace(Redraw=lambda: None))
    Layer = lambda: types.SimpleNamespace(Name="", Color=None, ParentLayerId=0, IsLocked=False, IsVisible=True)
    rg = types.SimpleNamespace(Point3d=Pt, Vector3d=Pt, Mesh=Mesh, LineCurve=Curve, PolylineCurve=Curve, Curve=Curve,
                               Brep=Brep, Surface=types.SimpleNamespace(CreateExtrusion=lambda c, v: Geo()),
                               PipeCapMode=types.SimpleNamespace(Flat=1, Round=2),
                               CurveKnotStyle=types.SimpleNamespace(ChordPeriodic=5))
    rhino = types.ModuleType("Rhino")
    rhino.Geometry = rg
    rhino.RhinoDoc = types.SimpleNamespace(ActiveDoc=doc)
    rhino.RhinoMath = types.SimpleNamespace(UnitScale=lambda a, b: 1.0)
    rhino.UnitSystem = types.SimpleNamespace(Millimeters="mm")
    rhino.DocObjects = types.SimpleNamespace(Layer=Layer, ObjectAttributes=lambda: types.SimpleNamespace(),
                                             ObjectEnumeratorSettings=lambda: types.SimpleNamespace())
    system = types.ModuleType("System")
    system.Drawing = types.SimpleNamespace(Color=types.SimpleNamespace(FromArgb=lambda r, g, b: (r, g, b)))
    generic = types.ModuleType("System.Collections.Generic")
    generic.List = types.SimpleNamespace(__getitem__=None)
    generic.List = type("ListFactory", (), {"__class_getitem__": classmethod(lambda cls, t: Seq)})
    sys.modules.update({"Rhino": rhino, "Rhino.Geometry": rg, "System": system,
                        "System.Collections": types.ModuleType("System.Collections"),
                        "System.Collections.Generic": generic})
    return doc


def run(rel, extra=None):
    doc = install()
    g = {"__name__": "__main__"}
    g.update(extra or {})
    printed = []
    g["print"] = lambda *a: printed.append(" ".join(str(x) for x in a))
    with open(os.path.join(ROOT, rel)) as fh:
        exec(compile(fh.read(), rel, "exec"), g)
    return doc, g, printed


def main():
    doc, g, printed = run("rhino/mvm_build_rhino.py")
    report = printed[-1]
    assert " failed 0 " in report, report
    layers = [l.FullPath for l in doc.Layers.items]
    assert layers[:3] == ["Massing", "Structure", "Roof"], layers
    names = set(n for _, _, n in doc.Objects.added)
    assert all(n.startswith("MVM_") for n in names)
    used = sorted(set(layers[i] for _, i, _ in doc.Objects.added))
    print("rhino build OK:", len(doc.Objects.added), "objects on", used)
    print(report)
    doc, g, _ = run("grasshopper/mvm_pavilion_gh.py", {"apex": 12000, "branches": 5})
    print("grasshopper OK:", g["report"])


if __name__ == "__main__":
    main()

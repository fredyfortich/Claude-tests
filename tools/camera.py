"""Pinhole camera shared by the Stage 1 fit, the Stage 2 overlay and the Stage 4 matched render."""
import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "core"))
import mvm_core  # noqa: E402

SPEC = os.path.join(ROOT, "spec", "pavilion_spec.json")
IMAGE = os.path.join(ROOT, "spec", "source_image.webp")
OUT = os.path.join(ROOT, "out")


def load_spec(path=SPEC):
    with open(path) as fh:
        return json.load(fh)


def basis(yaw, pitch, roll=0.0):
    """Right, up, forward vectors. yaw 0 looks along +Y, pitch > 0 looks down."""
    d = np.array([np.sin(yaw) * np.cos(pitch), np.cos(yaw) * np.cos(pitch), -np.sin(pitch)])
    r = np.array([np.cos(yaw), -np.sin(yaw), 0.0])
    u = np.cross(r, d)
    if roll:
        c, s = np.cos(roll), np.sin(roll)
        r, u = c * r + s * u, -s * r + c * u
    return r, u, d


class Camera(object):
    def __init__(self, C, yaw, pitch, f, size, roll=0.0):
        self.C = np.asarray(C, float)
        self.yaw, self.pitch, self.roll, self.f = yaw, pitch, roll, f
        self.w, self.h = size
        self.r, self.u, self.d = basis(yaw, pitch, roll)

    @classmethod
    def from_meta(cls, m):
        return cls(m["C"], np.radians(m["yaw_deg"]), np.radians(m["pitch_deg"]), m["f_px"], m["size"],
                   np.radians(m.get("roll_deg", 0.0)))

    def to_meta(self):
        return {"C": [round(float(x)) for x in self.C], "yaw_deg": round(float(np.degrees(self.yaw)), 3),
                "pitch_deg": round(float(np.degrees(self.pitch)), 3), "roll_deg": round(float(np.degrees(self.roll)), 3),
                "f_px": round(float(self.f), 1), "size": [self.w, self.h]}

    def depth(self, P):
        return (np.asarray(P, float) - self.C) @ self.d

    def project(self, P):
        """(N, 3) mm -> (N, 2) px."""
        q = np.atleast_2d(np.asarray(P, float)) - self.C
        z = q @ self.d
        return np.stack([self.w / 2 + self.f * (q @ self.r) / z, self.h / 2 - self.f * (q @ self.u) / z], axis=1)

    def ray_to_plane(self, px, z):
        u, v = px
        dirv = self.d + self.r * (u - self.w / 2) / self.f - self.u * (v - self.h / 2) / self.f
        t = (z - self.C[2]) / dirv[2]
        return self.C + t * dirv


def resolve_anchor(spec, a):
    """Anchor 3D point: explicit xyz, or a reference into the canopy/trees spec."""
    if "xyz" in a:
        return a["xyz"]
    kind, sid, what = a["ref"].split(":")
    mod = spec["canopy"]["module"]
    sh = [s for s in spec["canopy"]["shells"] if s["id"] == sid][0]
    cx, cy = sh["c"]
    R, eave, apex = sh.get("R", mod["R"]), sh.get("eave", mod["eave"]), sh.get("apex", mod["apex"])
    if kind == "tree" and what == "base":
        lv = dict((l["name"], l["elev"]) for l in spec["levels"])
        b = spec["trees"]["base"]
        return [cx, cy, lv[b] if isinstance(b, str) else b]
    if kind == "shell":
        return {"apex": [cx, cy, apex], "eave_left": [cx - R, cy, eave], "eave_right": [cx + R, cy, eave],
                "eave_front": [cx, cy - R, eave]}[what]
    raise ValueError(a["ref"])


def dumps(o, ind=0, width=150):
    """Readable JSON: anything that fits on one line stays on one line."""
    flat = json.dumps(o, separators=(", ", ": "))
    if len(flat) + ind <= width or not isinstance(o, (dict, list)) or not o:
        return flat
    pad = " " * (ind + 2)
    if isinstance(o, dict):
        body = (",\n").join(pad + json.dumps(k) + ": " + dumps(v, ind + 2, width) for k, v in o.items())
        return "{\n" + body + "\n" + " " * ind + "}"
    body = (",\n").join(pad + dumps(v, ind + 2, width) for v in o)
    return "[\n" + body + "\n" + " " * ind + "]"


def save_spec(spec, path=SPEC):
    with open(path, "w") as fh:
        fh.write(dumps(spec) + "\n")

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from scipy.sparse import csr_matrix
from scipy.spatial import cKDTree

from . import config, elevation
from .build import CLASSES

M_LAT = 111320.0
# cost multipliers per profile: [foot, res, tert, sec, path(unpaved), steps]
MULT = {
    "road": [1.0, 1.1, 1.4, 2.5, 1.3, 5.0],
    "mixed": [1.0, 1.2, 1.6, 3.0, 1.0, 5.0],
    "trail": [1.1, 1.4, 2.0, 4.0, 0.7, 3.0],
}
GREEN_BONUS = {"road": 0.1, "mixed": 0.2, "trail": 0.3}
PROFILES = tuple(MULT)
TERRAINS = ("any", "flat", "hilly")


class Graph:
    @classmethod
    def load(cls, path: str | Path | None = None) -> Graph:
        path = Path(path or config.BUILD_DIR)
        z = np.load(path / "graph.npz")
        meta = json.loads((path / "meta.json").read_text())
        return cls(dict(z), meta)

    def __init__(self, d: dict, meta: dict):
        self.meta = meta
        for k, v in d.items():
            setattr(self, k, v)
        self.n_nodes = len(self.node_lat)
        self.n_edges = len(self.eu)
        self.node_index = {int(r): i for i, r in enumerate(self.node_ref)}
        self._coslat = float(np.cos(np.radians(self.node_lat.mean())))
        self.xy = np.column_stack([self.node_lon * M_LAT * self._coslat, self.node_lat * M_LAT])
        self.tree = cKDTree(self.xy)
        self._build_arcs()
        self._cost_cache: dict = {}

    def _build_arcs(self):
        E = self.n_edges
        eid = np.arange(E)
        u = np.concatenate([self.eu[self.edge_fwd], self.ev[self.edge_rev]]).astype("int64")
        v = np.concatenate([self.ev[self.edge_fwd], self.eu[self.edge_rev]]).astype("int64")
        edge = np.concatenate([eid[self.edge_fwd], eid[self.edge_rev]])
        direction = np.concatenate([np.zeros(self.edge_fwd.sum(), "int8"), np.ones(self.edge_rev.sum(), "int8")])
        order = np.lexsort((v, u))
        self.arc_u, self.arc_v = u[order], v[order]
        self.arc_edge, self.arc_dir = edge[order], direction[order]
        self.n_arcs = len(order)
        self.arc_key = self.arc_u * self.n_nodes + self.arc_v
        self.indptr = np.concatenate([[0], np.cumsum(np.bincount(self.arc_u, minlength=self.n_nodes))]).astype("int32")
        self.indices = self.arc_v.astype("int32")
        # reverse arc of the same edge (for the both-direction reuse penalty)
        pos = {(int(e), int(d)): a for a, (e, d) in enumerate(zip(self.arc_edge, self.arc_dir))}
        self.arc_rev = np.array([pos.get((int(e), 1 - int(d)), -1) for e, d in zip(self.arc_edge, self.arc_dir)])
        e = self.arc_edge
        fwd = self.arc_dir == 0
        self.arc_len = self.edge_len[e].astype("float64")
        self.arc_climb = np.where(fwd, self.climb_f[e], self.desc_f[e]).astype("float64")
        self.arc_desc = np.where(fwd, self.desc_f[e], self.climb_f[e]).astype("float64")
        self.arc_maxup = np.where(fwd, self.maxup_f[e], self.maxdown_f[e]).astype("float64")
        self.arc_cls = self.edge_cls[e]
        self.arc_paved = self.edge_paved[e]
        self.arc_lit = self.edge_lit[e]
        self.arc_green = self.edge_green[e]
        self.arc_bt = self.edge_bridge[e] | self.edge_tunnel[e]
        self.arc_grade = (self.arc_climb - self.arc_desc) / np.maximum(self.arc_len, 1.0)

    # --- lookups ---------------------------------------------------------
    def csr_structure(self):
        return csr_matrix((np.ones(self.n_arcs), self.indices, self.indptr), shape=(self.n_nodes,) * 2)

    def csr(self, cost: np.ndarray) -> csr_matrix:
        return csr_matrix((cost, self.indices, self.indptr), shape=(self.n_nodes,) * 2, copy=False)

    def arc_between(self, u: int, v: int) -> int:
        k = int(u) * self.n_nodes + int(v)
        i = int(np.searchsorted(self.arc_key, k))
        return i if i < self.n_arcs and self.arc_key[i] == k else -1

    def has_arc(self, u: int, v: int) -> bool:
        return self.arc_between(u, v) >= 0

    def edge_cls_name(self, e: int) -> str:
        return CLASSES[int(self.edge_cls[e])]

    def snap(self, lat: float, lon: float) -> int:
        _, i = self.tree.query([lon * M_LAT * self._coslat, lat * M_LAT])
        return int(i)

    def to_xy(self, lat, lon):
        return np.asarray(lon) * M_LAT * self._coslat, np.asarray(lat) * M_LAT

    # --- costs -----------------------------------------------------------
    def cost(self, profile: str = "mixed", terrain: str = "any", lit: bool = False) -> np.ndarray:
        key = (profile, terrain, lit)
        c = self._cost_cache.get(key)
        if c is not None:
            return c
        m = np.array(MULT[profile])
        cls = self.arc_cls.astype(int)
        mult = m[cls].copy()
        # a paved path/track behaves like a footway; unpaved footways are not penalised extra
        paved_path = (cls == 4) & self.arc_paved
        mult[paved_path] = m[0]
        mult *= 1 - GREEN_BONUS[profile] * self.arc_green
        if lit:
            mult = np.where(self.arc_lit, mult, mult * 1.5)
        if terrain == "flat":
            mult *= 1 + 10.0 * (self.arc_climb + self.arc_desc) / np.maximum(self.arc_len, 1.0)
        elif terrain == "hilly":
            mult *= np.maximum(0.35, 1 - 4.0 * self.arc_climb / np.maximum(self.arc_len, 1.0))
        c = np.maximum(self.arc_len * mult, 0.05 * self.arc_len + 1e-3)
        self._cost_cache[key] = c
        return c

    # --- routes ----------------------------------------------------------
    def arcs_from_nodes(self, nodes) -> list[int]:
        nodes = np.asarray(nodes, dtype="int64")
        keys = nodes[:-1] * self.n_nodes + nodes[1:]
        idx = np.searchsorted(self.arc_key, keys)
        if (idx >= self.n_arcs).any() or (self.arc_key[np.minimum(idx, self.n_arcs - 1)] != keys).any():
            raise ValueError("path uses a non-existent arc")
        return idx.tolist()

    def arc_geometry(self, a: int):
        e = int(self.arc_edge[a])
        s, t = int(self.geom_off[e]), int(self.geom_off[e + 1])
        la, lo, z, d = self.geom_lat[s:t], self.geom_lon[s:t], self.geom_z[s:t], self.geom_s[s:t]
        if self.arc_dir[a] == 1:
            la, lo, z, d = la[::-1], lo[::-1], z[::-1], d[-1] - d[::-1]
        return la, lo, z, d

    def route_profile(self, arcs):
        """Joined lat/lon/elevation/cumulative-metres along a list of arcs."""
        las, los, zs, ss = [], [], [], []
        base = 0.0
        for i, a in enumerate(arcs):
            la, lo, z, d = self.arc_geometry(a)
            if i > 0:
                la, lo, z, d = la[1:], lo[1:], z[1:], d[1:]
            las.append(la)
            los.append(lo)
            zs.append(z)
            ss.append(d + base)
            base += self.arc_len[a]
        if not arcs:
            return (np.zeros(0),) * 4
        return np.concatenate(las), np.concatenate(los), np.concatenate(zs), np.concatenate(ss)

    def route_stats(self, arcs) -> dict:
        arcs = np.asarray(arcs, dtype=int)
        ln = self.arc_len[arcs]
        total = float(ln.sum()) or 1.0
        _la, _lo, z, s = self.route_profile(list(arcs))
        gain, loss = elevation.gain_loss(z, 3.0)
        edges, counts = np.unique(self.arc_edge[arcs], return_counts=True)
        elen = self.edge_len[edges].astype("float64")
        retrace = float((elen * np.maximum(counts - 1, 0)).sum())
        return {
            "km": total / 1000.0,
            "gain": gain,
            "loss": loss,
            "paved_pct": 100 * float(ln[self.arc_paved[arcs]].sum()) / total,
            "green_pct": 100 * float((ln * self.arc_green[arcs]).sum()) / total,
            "lit_pct": 100 * float(ln[self.arc_lit[arcs]].sum()) / total,
            "retrace_pct": 100 * retrace / total,
            "max_grade": elevation.max_grade(z, s) if len(arcs) else 0.0,
        }

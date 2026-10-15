from __future__ import annotations

from pathlib import Path

import numpy as np

M_LAT = 111320.0


class Dem:
    """Whole tile in memory; bilinear sampling with pixel-centre registration."""

    def __init__(self, z: np.ndarray, left: float, top: float, dx: float, dy: float):
        z = z.astype("float64")
        bad = ~np.isfinite(z) | (z < -500)
        if bad.any():
            z[bad] = np.nan
            fill = np.nanmean(z) if np.isfinite(z).any() else 0.0
            z = np.where(np.isnan(z), fill, z)
        self.z = z
        self.left, self.top, self.dx, self.dy = left, top, dx, dy

    @classmethod
    def open(cls, path: str | Path) -> Dem:
        import rasterio

        with rasterio.open(path) as ds:
            z = ds.read(1)
            t = ds.transform
            nodata = ds.nodata
        if nodata is not None:
            z = np.where(z == nodata, np.nan, z)
        return cls(z, t.c, t.f, t.a, -t.e)

    def sample(self, lat, lon) -> np.ndarray:
        lat = np.asarray(lat, dtype="float64")
        lon = np.asarray(lon, dtype="float64")
        fx = (lon - self.left) / self.dx - 0.5
        fy = (self.top - lat) / self.dy - 0.5
        h, w = self.z.shape
        fx = np.clip(fx, 0, w - 1.000001)
        fy = np.clip(fy, 0, h - 1.000001)
        x0 = fx.astype(int)
        y0 = fy.astype(int)
        tx, ty = fx - x0, fy - y0
        z = self.z
        return (
            z[y0, x0] * (1 - tx) * (1 - ty)
            + z[y0, x0 + 1] * tx * (1 - ty)
            + z[y0 + 1, x0] * (1 - tx) * ty
            + z[y0 + 1, x0 + 1] * tx * ty
        )


def densify(lat: np.ndarray, lon: np.ndarray, max_step: float = 20.0):
    """Insert points so no step exceeds max_step metres; returns lat, lon, cumulative metres."""
    lat = np.asarray(lat, dtype="float64")
    lon = np.asarray(lon, dtype="float64")
    coslat = np.cos(np.radians(lat.mean()))
    dy = np.diff(lat) * M_LAT
    dx = np.diff(lon) * M_LAT * coslat
    seg = np.hypot(dx, dy)
    out_la, out_lo, out_s = [lat[:1]], [lon[:1]], [np.zeros(1)]
    base = 0.0
    for i, d in enumerate(seg):
        n = max(1, int(np.ceil(d / max_step - 1e-9)))
        t = np.arange(1, n + 1) / n
        out_la.append(lat[i] + (lat[i + 1] - lat[i]) * t)
        out_lo.append(lon[i] + (lon[i + 1] - lon[i]) * t)
        out_s.append(base + d * t)
        base += d
    return np.concatenate(out_la), np.concatenate(out_lo), np.concatenate(out_s)


def edge_profile(dem: Dem, lat, lon, interpolate: bool = False, max_step: float = 20.0):
    """Densified (lat, lon, z). Interior points get a light [1,2,1] smoothing, endpoints stay
    on the DEM so edges sharing a node agree. Bridges/tunnels are linear between endpoints."""
    dl, dlo, s = densify(lat, lon, max_step)
    z = dem.sample(dl, dlo)
    if interpolate and len(z) > 2:
        z = z[0] + (z[-1] - z[0]) * (s / s[-1] if s[-1] > 0 else np.linspace(0, 1, len(z)))
    elif len(z) > 2:
        sm = z.copy()
        sm[1:-1] = 0.25 * z[:-2] + 0.5 * z[1:-1] + 0.25 * z[2:]
        z = sm
    return dl, dlo, z


def max_grade(z, s, window: float = 100.0) -> float:
    """Steepest sustained climb in percent over `window` metres. Per-sample grades on a 30 m DEM
    spike to 40-60 % on short banks, which no runner would recognise as the route's max grade."""
    z = np.asarray(z, dtype="float64")
    s = np.asarray(s, dtype="float64")
    if len(z) < 2 or s[-1] <= 0:
        return 0.0
    w = min(window, float(s[-1]))
    grid = np.arange(0.0, float(s[-1]) + 1e-9, 10.0)
    zg = np.interp(grid, s, z)
    k = max(1, round(w / 10.0))
    if len(zg) <= k:
        return max(0.0, float(zg[-1] - zg[0]) / float(s[-1]) * 100)
    return max(0.0, float((zg[k:] - zg[:-k]).max()) / (k * 10.0) * 100)


def grade_variability(z, s, window: float = 40.0) -> float:
    """Std of the grade over `window`-metre steps along a profile. Measured on the profile so a climb
    mapped as one long way and one split into many short ways are judged alike."""
    z = np.asarray(z, dtype="float64")
    s = np.asarray(s, dtype="float64")
    if len(z) < 2 or s[-1] < 2 * window:
        return 0.0
    grid = np.arange(0.0, float(s[-1]) + 1e-9, window)
    gr = np.diff(np.interp(grid, s, z)) / window
    return float(gr.std())


def gain_loss(z, hyst: float = 3.0) -> tuple[float, float]:
    """Cumulative ascent/descent with hysteresis: an excursion only counts once it exceeds hyst
    from the last turning point, the way watches filter barometer/GPS noise."""
    z = np.asarray(z, dtype="float64")
    if len(z) < 2:
        return 0.0, 0.0
    gain = loss = 0.0
    lo = hi = z[0]
    trend = 0
    turn = ext = z[0]
    for v in z[1:]:
        if trend == 0:
            lo, hi = min(lo, v), max(hi, v)
            if v - lo >= hyst:
                trend, turn, ext = 1, lo, v
            elif hi - v >= hyst:
                trend, turn, ext = -1, hi, v
        elif trend == 1:
            if v > ext:
                ext = v
            elif ext - v >= hyst:
                gain += ext - turn
                turn, ext, trend = ext, v, -1
        else:
            if v < ext:
                ext = v
            elif v - ext >= hyst:
                loss += turn - ext
                turn, ext, trend = ext, v, 1
    if trend == 1:
        gain += ext - turn
    elif trend == -1:
        loss += turn - ext
    return float(gain), float(loss)

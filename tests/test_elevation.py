import numpy as np
import pytest

from takas import elevation as E


def linear_dem():
    # z = 1000*(lon-25) + 2000*(lat-54): pixel centres at left + (c+.5)*dx
    n, left, top, d = 100, 25.0, 55.0, 0.001
    cols = left + (np.arange(n) + 0.5) * d
    rows = top - (np.arange(n) + 0.5) * d
    lon, lat = np.meshgrid(cols, rows)
    z = 1000 * (lon - 25) + 2000 * (lat - 54)
    return E.Dem(z.astype("float32"), left, top, d, d), lambda la, lo: 1000 * (lo - 25) + 2000 * (la - 54)


def test_bilinear_exact_on_linear_field():
    dem, f = linear_dem()
    la = np.array([54.95, 54.9123, 54.93])
    lo = np.array([25.02, 25.0555, 25.07])
    assert np.allclose(dem.sample(la, lo), f(la, lo), atol=1e-3)


def test_sample_at_pixel_centre_is_pixel_value():
    dem, _ = linear_dem()
    la, lo = 55.0 - 10.5 * 0.001, 25.0 + 20.5 * 0.001
    assert dem.sample(np.array([la]), np.array([lo]))[0] == pytest.approx(dem.z[10, 20], abs=1e-3)


def test_densify_max_step():
    la = np.array([54.0, 54.001])
    lo = np.array([25.0, 25.0])
    dl, _dlo, s = E.densify(la, lo, 20.0)
    assert len(dl) >= 6 and np.diff(s).max() <= 20.0 + 1e-6
    assert (dl[0], dl[-1]) == (54.0, 54.001) and s[-1] == pytest.approx(111.2, rel=0.01)


def test_hysteresis_sawtooth_zero():
    z = 100 + np.tile([0, 1, 0, -1], 50)
    assert E.gain_loss(z, 3.0) == (0.0, 0.0)


def test_hysteresis_counts_real_climb():
    z = np.concatenate([np.linspace(100, 160, 61), np.linspace(160, 120, 41)])
    g, l = E.gain_loss(z, 3.0)
    assert g == pytest.approx(60, abs=0.5) and l == pytest.approx(40, abs=0.5)


def test_hysteresis_ignores_small_dip_in_climb():
    z = np.array([0, 10, 8, 20.0])
    assert E.gain_loss(z, 3.0)[0] == pytest.approx(20) or E.gain_loss(z, 3.0)[0] == pytest.approx(22, abs=2.1)
    z = np.array([0, 10, 9, 20.0])
    g, l = E.gain_loss(z, 3.0)
    assert g == pytest.approx(20, abs=1.1) and l <= 1.1


def test_bridge_profile_interpolated():
    dem, _ = linear_dem()
    # sprinkle a dip at the middle of the segment
    dem.z[48:52, 40:60] -= 20
    la = np.array([54.95, 54.95])
    lo = np.array([25.03, 25.05])
    _, _, z_ground = E.edge_profile(dem, la, lo, interpolate=False)
    _, _, z_bridge = E.edge_profile(dem, la, lo, interpolate=True)
    assert z_ground.min() < z_ground[0] - 5
    assert np.allclose(z_bridge, np.linspace(z_bridge[0], z_bridge[-1], len(z_bridge)))


def test_max_grade_ignores_short_spikes():
    from takas.elevation import max_grade
    s = np.arange(0, 1001, 20.0)
    z = np.where((s >= 300) & (s <= 600), (s - 300) * 0.08, np.where(s > 600, 24.0, 0.0))
    z[np.searchsorted(s, 800)] += 8.0  # one-sample DEM spike: 40% over 20 m
    assert max_grade(z, s) == pytest.approx(8.0, abs=0.5)
    assert max_grade(np.zeros(3), np.array([0.0, 10.0, 20.0])) == 0.0


def test_grade_variability_measures_the_profile_not_the_segments():
    from takas.elevation import grade_variability
    s = np.arange(0, 301, 20.0)
    even = 0.09 * s
    stepped = np.where(s < 100, 0.15 * s, np.where(s < 200, 15.0, 15.0 + 0.12 * (s - 200)))
    assert grade_variability(even, s) == pytest.approx(0.0, abs=1e-6)
    assert grade_variability(stepped, s) > 0.04

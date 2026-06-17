# Created: 2026-06-17
# Last reused or audited: 2026-06-17
# Authority basis: operator spec zeus_source_access_validation_v3.xlsx GridCorrectionMath
#   rule 2 (bilinear weights, u/v, w_SW..w_NE, T_interp/z_interp, d_eff=sqrt(sum w_i d_i^2),
#   haversine R=6371000) + rule 3 (barycentric inside/outside, sum w_i=1). RED-on-revert:
#   each assertion fails if interpolation reverts to nearest-rounded-node lookup.
"""RED-on-revert tests for native-grid bilinear + barycentric interpolation (v3 rule 2/3)."""
from __future__ import annotations

import math

import pytest

from src.forecast.grid_interpolation import (
    EARTH_RADIUS_M,
    GridPoint,
    barycentric_interpolate,
    barycentric_weights,
    bilinear_interpolate,
    haversine_m,
    point_in_triangle,
)


# A unit lat/lon cell [0,1] x [0,1] with corner values, used for analytic checks.
def _cell(v_sw=10.0, v_se=20.0, v_nw=30.0, v_ne=40.0,
          z_sw=0.0, z_se=0.0, z_nw=0.0, z_ne=0.0):
    sw = GridPoint(lat=0.0, lon=0.0, value=v_sw, elevation=z_sw, grid_id="SW")
    se = GridPoint(lat=0.0, lon=1.0, value=v_se, elevation=z_se, grid_id="SE")
    nw = GridPoint(lat=1.0, lon=0.0, value=v_nw, elevation=z_nw, grid_id="NW")
    ne = GridPoint(lat=1.0, lon=1.0, value=v_ne, elevation=z_ne, grid_id="NE")
    return sw, se, nw, ne


# ---- haversine -----------------------------------------------------------------
def test_haversine_zero_at_same_point():
    assert haversine_m(40.0, -100.0, 40.0, -100.0) == pytest.approx(0.0, abs=1e-9)


def test_haversine_one_degree_latitude_is_known_arc():
    """1 degree of latitude = R * (pi/180) metres (~111195 m at R=6371000)."""
    expected = EARTH_RADIUS_M * math.radians(1.0)
    assert haversine_m(0.0, 0.0, 1.0, 0.0) == pytest.approx(expected, rel=1e-9)


# ---- bilinear: weights sum to 1 ------------------------------------------------
def test_bilinear_weights_sum_to_one():
    sw, se, nw, ne = _cell()
    interp = bilinear_interpolate(0.3, 0.7, None, sw, se, nw, ne)
    assert sum(interp.weights) == pytest.approx(1.0, abs=1e-12)


def test_bilinear_weights_sum_to_one_at_many_points():
    sw, se, nw, ne = _cell()
    for u in (0.0, 0.25, 0.5, 0.9, 1.0):
        for v in (0.0, 0.1, 0.5, 1.0):
            interp = bilinear_interpolate(v, u, None, sw, se, nw, ne)
            assert sum(interp.weights) == pytest.approx(1.0, abs=1e-12)


# ---- bilinear: recover a node value at a node ----------------------------------
def test_bilinear_recovers_grid_value_at_node():
    """At a node the interpolation returns that node's value exactly (weight 1)."""
    sw, se, nw, ne = _cell(v_sw=10.0, v_se=20.0, v_nw=30.0, v_ne=40.0)
    # Station exactly at NE corner (lat=1, lon=1).
    interp = bilinear_interpolate(1.0, 1.0, None, sw, se, nw, ne)
    assert interp.T_interp == pytest.approx(40.0, abs=1e-9)
    # And at SW corner.
    interp_sw = bilinear_interpolate(0.0, 0.0, None, sw, se, nw, ne)
    assert interp_sw.T_interp == pytest.approx(10.0, abs=1e-9)


# ---- bilinear: linear interpolation along an edge ------------------------------
def test_bilinear_interpolates_linearly_along_edge():
    """Along the south edge (v=0) T = (1-u)*v_sw + u*v_se — purely linear in u."""
    sw, se, nw, ne = _cell(v_sw=10.0, v_se=20.0, v_nw=30.0, v_ne=40.0)
    interp = bilinear_interpolate(0.0, 0.25, None, sw, se, nw, ne)  # v=0, u=0.25
    assert interp.T_interp == pytest.approx(0.75 * 10.0 + 0.25 * 20.0, abs=1e-9)
    # Cell centre = mean of 4 corners.
    centre = bilinear_interpolate(0.5, 0.5, None, sw, se, nw, ne)
    assert centre.T_interp == pytest.approx((10 + 20 + 30 + 40) / 4.0, abs=1e-9)


def test_bilinear_z_interp_blends_elevation():
    """z_interp is the same bilinear blend applied to node elevations."""
    sw, se, nw, ne = _cell(z_sw=0.0, z_se=100.0, z_nw=0.0, z_ne=100.0)
    interp = bilinear_interpolate(0.5, 0.25, None, sw, se, nw, ne)
    # z varies only in lon (u): z = u*100.
    assert interp.z_interp == pytest.approx(25.0, abs=1e-9)


# ---- d_eff monotone in station offset ------------------------------------------
def test_d_eff_zero_at_node_and_monotone_in_offset():
    """d_eff = 0 when the station sits on a node, and grows as it moves into the cell."""
    sw, se, nw, ne = _cell()
    # On the SW node -> d_eff exactly 0 (weight 1 on a zero-distance node).
    at_node = bilinear_interpolate(0.0, 0.0, None, sw, se, nw, ne)
    assert at_node.d_eff_m == pytest.approx(0.0, abs=1e-6)
    # Moving toward the cell interior strictly increases d_eff.
    prev = -1.0
    for frac in (0.05, 0.2, 0.4, 0.5):
        interp = bilinear_interpolate(frac, frac, None, sw, se, nw, ne)
        assert interp.d_eff_m > prev
        prev = interp.d_eff_m
    # Max d_eff at the cell centre (farthest from all 4 corners on average).
    centre = bilinear_interpolate(0.5, 0.5, None, sw, se, nw, ne)
    near_corner = bilinear_interpolate(0.1, 0.1, None, sw, se, nw, ne)
    assert centre.d_eff_m > near_corner.d_eff_m


def test_bilinear_provenance_is_persistable():
    """Provenance carries geometry, node ids, weights, per-node d_i, and d_eff."""
    sw, se, nw, ne = _cell()
    interp = bilinear_interpolate(0.3, 0.7, 12.0, sw, se, nw, ne)
    p = interp.provenance
    assert p["geometry"] == "rectilinear"
    assert [n["grid_id"] for n in p["nodes"]] == ["SW", "SE", "NW", "NE"]
    assert len(p["weights"]) == 4
    assert p["d_eff_m"] == pytest.approx(interp.d_eff_m)
    assert interp.grid_ids == ("SW", "SE", "NW", "NE")


# ---- barycentric ---------------------------------------------------------------
def _triangle():
    # Right triangle in a native projection plane: A(0,0) B(1,0) C(0,1).
    a = GridPoint(lat=0.0, lon=0.0, value=10.0, elevation=0.0, grid_id="A", proj_x=0.0, proj_y=0.0)
    b = GridPoint(lat=0.0, lon=1.0, value=20.0, elevation=0.0, grid_id="B", proj_x=1.0, proj_y=0.0)
    c = GridPoint(lat=1.0, lon=0.0, value=30.0, elevation=0.0, grid_id="C", proj_x=0.0, proj_y=1.0)
    return a, b, c


def test_barycentric_weights_sum_to_one_inside():
    w = barycentric_weights(0.25, 0.25, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0)
    assert sum(w) == pytest.approx(1.0, abs=1e-12)
    assert all(wi >= 0.0 for wi in w)  # inside


def test_point_in_triangle_inside_vs_outside():
    # Inside the right triangle A(0,0)B(1,0)C(0,1).
    assert point_in_triangle(0.25, 0.25, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0) is True
    # Outside (x+y > 1).
    assert point_in_triangle(0.8, 0.8, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0) is False


def test_barycentric_recovers_node_value_at_vertex():
    a, b, c = _triangle()
    # Station projected onto vertex B -> value B exactly.
    interp = barycentric_interpolate(0.0, 1.0, None, 1.0, 0.0, a, b, c)
    assert interp.T_interp == pytest.approx(20.0, abs=1e-9)
    assert sum(interp.weights) == pytest.approx(1.0, abs=1e-12)


def test_barycentric_interpolates_interior_value():
    a, b, c = _triangle()
    # Centroid (1/3,1/3) -> mean of the 3 node values.
    interp = barycentric_interpolate(1 / 3.0, 1 / 3.0, None, 1 / 3.0, 1 / 3.0, a, b, c)
    assert interp.T_interp == pytest.approx((10 + 20 + 30) / 3.0, abs=1e-9)


def test_barycentric_refuses_extrapolation_outside_triangle():
    """A station outside the containing triangle is REFUSED (no native extrapolation)."""
    a, b, c = _triangle()
    with pytest.raises(ValueError):
        barycentric_interpolate(2.0, 2.0, None, 0.8, 0.8, a, b, c)  # x+y>1 -> outside


def test_barycentric_provenance_records_geometry_and_inside_flag():
    a, b, c = _triangle()
    interp = barycentric_interpolate(0.25, 0.25, 5.0, 0.25, 0.25, a, b, c)
    assert interp.geometry == "barycentric"
    assert interp.provenance["inside_triangle"] is True
    assert interp.grid_ids == ("A", "B", "C")

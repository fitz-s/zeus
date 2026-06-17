# Created: 2026-06-17
# Last reused or audited: 2026-06-17
# Authority basis: operator spec zeus_source_access_validation_v3.xlsx GridCorrectionMath
#   rule 2 (rectilinear/bilinear) + rule 3 (irregular/rotated barycentric) + the
#   haversine formula (R=6371000) + d_eff = sqrt(sum w_i d_i^2). Operator message:
#   native-grid INTERPOLATION replaces nearest-rounded-coordinate lookup; persist
#   grid ids + weights as provenance. Build-only (no live-fusion wiring).
"""grid_interpolation — native-grid bilinear + barycentric interpolators (v3 rule 2/3).

THE NATIVE-GRID INTERPOLATORS. The defect this replaces: the old path snaps the
station to the NEAREST ROUNDED grid coordinate and reads that single cell's value, so
a station 11-16 km from its native cell centre is served a value from the wrong place
(the cold-center / coarse-cell error the operator named). v3 instead INTERPOLATES the
native grid at the precise station coordinate and reports an EFFECTIVE distance d_eff
that the representativeness-variance module turns into added Sigma — never a hand shift.

TWO GRID GEOMETRIES (operator rules 2 and 3):

  Rule 2 — RECTILINEAR (regular lat/lon, e.g. ECMWF/GFS): the 4 surrounding grid points
    (lambda0 < lambda < lambda1, phi0 < phi < phi1). With
        u = (lambda - lambda0)/(lambda1 - lambda0) ;  v = (phi - phi0)/(phi1 - phi0)
        w_SW=(1-u)(1-v)  w_SE=u(1-v)  w_NW=(1-u)v  w_NE=u v
    T_interp = sum w_i T_i ;  z_interp = sum w_i z_i ;  d_eff = sqrt(sum w_i d_i^2),
    d_i = haversine great-circle metres from the station to grid point i.

  Rule 3 — IRREGULAR / ROTATED (ICON etc.): do NOT lat/lon snap. Find the CONTAINING
    TRIANGLE in the native projection and BARYCENTRIC-interpolate
        T_interp = w1 T1 + w2 T2 + w3 T3 ,  sum w_i = 1 (the barycentric coords).
    The barycentric weights are computed in the projection plane the grid lives in
    (rotated/native x-y); d_eff is still the haversine RSS over the 3 triangle nodes so
    it stays a true geographic distance. Persist grid ids + weights.

WHAT EVERY INTERPOLATION RETURNS — a ``GridInterpolation``:
    T_interp, z_interp (interpolated 2 m temperature and grid-cell elevation),
    d_eff_m (effective station<->grid distance in metres), grid_ids + weights, and a
    persistable ``provenance`` dict (geometry, node coords/ids/weights, d_i, d_eff).
    z_interp and d_eff are exactly the two scalars the representativeness module needs
    (it consumes d_eff and |z_station - z_interp|); T_interp feeds station_correction.

PURITY: pure math. No network, no DB writes. The provenance dict is RETURNED for a
caller to persist; this module never persists it itself.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Sequence

EARTH_RADIUS_M = 6371000.0  # R in the operator haversine (metres).


def haversine_m(
    lat1: float, lon1: float, lat2: float, lon2: float
) -> float:
    """Great-circle distance in METRES (operator rule 2 haversine, R=6371000).

    d = 2R * asin( sqrt( sin^2((phi2-phi1)/2) + cos phi1 cos phi2 sin^2((lambda2-lambda1)/2) ) ).
    Inputs are decimal DEGREES (lat=phi, lon=lambda). The clamp on the sqrt argument
    guards floating-point overshoot above 1.0 for antipodal-ish pairs.
    """
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2.0) ** 2
    )
    a = min(1.0, max(0.0, a))
    return 2.0 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


@dataclass(frozen=True)
class GridPoint:
    """One native-grid node. ``lat``/``lon`` are its true geographic coordinates (for
    haversine), ``value`` is the field value (2 m temperature), ``elevation`` is the
    grid-cell surface elevation z_i (metres), ``grid_id`` is the persistable node id.
    For irregular grids ``proj_x``/``proj_y`` are the node's coordinates in the grid's
    native projection plane (where the barycentric triangle is solved); for rectilinear
    grids they are unused.
    """

    lat: float
    lon: float
    value: float
    elevation: float
    grid_id: str
    proj_x: float | None = None
    proj_y: float | None = None


@dataclass(frozen=True)
class GridInterpolation:
    """The interpolated quantities + provenance for one station<->grid interpolation.

    ``T_interp`` interpolated field value (2 m temperature).
    ``z_interp``  interpolated grid-cell elevation (metres) — feeds |z_station - z_interp|.
    ``d_eff_m``   effective station<->grid distance = sqrt(sum w_i d_i^2) (metres).
    ``grid_ids``  ordered node ids (provenance / persistence).
    ``weights``   ordered interpolation weights (sum to 1).
    ``geometry``  "rectilinear" or "barycentric".
    ``provenance`` a fully persistable dict (node ids/coords/weights, per-node d_i, d_eff).
    """

    T_interp: float
    z_interp: float
    d_eff_m: float
    grid_ids: tuple[str, ...]
    weights: tuple[float, ...]
    geometry: str
    provenance: dict[str, Any] = field(default_factory=dict)


def _d_eff(weights: Sequence[float], d_i: Sequence[float]) -> float:
    """d_eff = sqrt(sum_i w_i d_i^2). The weights are the interpolation weights, so a
    node the station sits ON (weight 1, d_i 0) gives d_eff 0; a station far inside a
    coarse cell inherits a large d_eff. This is the operator's effective distance, NOT
    a nearest-node distance, so it is monotone in how far the station is from the cell.
    """
    return math.sqrt(sum(w * (d * d) for w, d in zip(weights, d_i)))


def bilinear_interpolate(
    station_lat: float,
    station_lon: float,
    station_elevation: float | None,
    sw: GridPoint,
    se: GridPoint,
    nw: GridPoint,
    ne: GridPoint,
) -> GridInterpolation:
    """Rectilinear bilinear interpolation (operator rule 2).

    The 4 nodes are the corners of the containing cell: SW=(lambda0,phi0),
    SE=(lambda1,phi0), NW=(lambda0,phi1), NE=(lambda1,phi1) with lambda0<lambda1 and
    phi0<phi1. We derive lambda0/lambda1/phi0/phi1 from the node coordinates so the
    caller need only hand the 4 corners in SW/SE/NW/NE roles.

    u = (lambda - lambda0)/(lambda1 - lambda0) ; v = (phi - phi0)/(phi1 - phi0)
    w_SW=(1-u)(1-v) w_SE=u(1-v) w_NW=(1-u)v w_NE=u v  (sum == 1 identically).
    T_interp/z_interp = sum w_i {value, elevation}_i ; d_eff = sqrt(sum w_i d_i^2).

    Raises ``ValueError`` for a degenerate cell (zero span) — that means the caller
    passed coincident corners, which would divide by zero.
    """
    lon0 = sw.lon
    lon1 = se.lon
    lat0 = sw.lat
    lat1 = nw.lat
    dlon = lon1 - lon0
    dlat = lat1 - lat0
    if dlon == 0.0 or dlat == 0.0:
        raise ValueError("degenerate rectilinear cell (zero lon or lat span)")
    u = (station_lon - lon0) / dlon
    v = (station_lat - lat0) / dlat

    w_sw = (1.0 - u) * (1.0 - v)
    w_se = u * (1.0 - v)
    w_nw = (1.0 - u) * v
    w_ne = u * v
    nodes = (sw, se, nw, ne)
    weights = (w_sw, w_se, w_nw, w_ne)

    t_interp = sum(w * n.value for w, n in zip(weights, nodes))
    z_interp = sum(w * n.elevation for w, n in zip(weights, nodes))
    d_i = tuple(
        haversine_m(station_lat, station_lon, n.lat, n.lon) for n in nodes
    )
    d_eff = _d_eff(weights, d_i)

    grid_ids = tuple(n.grid_id for n in nodes)
    provenance = {
        "geometry": "rectilinear",
        "station": {"lat": station_lat, "lon": station_lon, "elevation": station_elevation},
        "cell_bounds": {"lon0": lon0, "lon1": lon1, "lat0": lat0, "lat1": lat1},
        "u": u,
        "v": v,
        "nodes": [
            {
                "role": role,
                "grid_id": n.grid_id,
                "lat": n.lat,
                "lon": n.lon,
                "value": n.value,
                "elevation": n.elevation,
                "weight": w,
                "d_i_m": d,
            }
            for role, n, w, d in zip(("SW", "SE", "NW", "NE"), nodes, weights, d_i)
        ],
        "weights": list(weights),
        "d_i_m": list(d_i),
        "d_eff_m": d_eff,
    }
    return GridInterpolation(
        T_interp=t_interp,
        z_interp=z_interp,
        d_eff_m=d_eff,
        grid_ids=grid_ids,
        weights=weights,
        geometry="rectilinear",
        provenance=provenance,
    )


def barycentric_weights(
    px: float, py: float, ax: float, ay: float, bx: float, by: float, cx: float, cy: float
) -> tuple[float, float, float]:
    """Barycentric coordinates of point P=(px,py) w.r.t. triangle A,B,C in the plane.

    Solved in the native projection plane the irregular grid lives in (the operator
    says: do NOT lat/lon snap — find the containing triangle in native projection).
    Returns (w_a, w_b, w_c) with w_a + w_b + w_c == 1. P is INSIDE the triangle iff all
    three are >= 0 (with a small tolerance applied by the caller). Raises ``ValueError``
    for a degenerate (zero-area) triangle.
    """
    det = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
    if det == 0.0:
        raise ValueError("degenerate triangle (zero area) — cannot solve barycentric")
    w_a = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / det
    w_b = ((cy - ay) * (px - cx) + (ax - cx) * (py - cy)) / det
    w_c = 1.0 - w_a - w_b
    return w_a, w_b, w_c


# How far outside the triangle a barycentric weight may go before we call the point
# "outside" (numerical slack on a node/edge).
BARYCENTRIC_INSIDE_TOL = 1e-9


def point_in_triangle(
    px: float, py: float, ax: float, ay: float, bx: float, by: float, cx: float, cy: float,
    tol: float = BARYCENTRIC_INSIDE_TOL,
) -> bool:
    """True iff P is inside (or on the boundary of) triangle A,B,C, within ``tol``."""
    w_a, w_b, w_c = barycentric_weights(px, py, ax, ay, bx, by, cx, cy)
    return w_a >= -tol and w_b >= -tol and w_c >= -tol


def barycentric_interpolate(
    station_lat: float,
    station_lon: float,
    station_elevation: float | None,
    station_proj_x: float,
    station_proj_y: float,
    n1: GridPoint,
    n2: GridPoint,
    n3: GridPoint,
    *,
    require_inside: bool = True,
    tol: float = BARYCENTRIC_INSIDE_TOL,
) -> GridInterpolation:
    """Irregular/rotated-grid barycentric interpolation over the CONTAINING triangle
    (operator rule 3).

    The triangle nodes carry both their native projection coordinates (``proj_x``,
    ``proj_y`` — where the barycentric weights are solved) AND their true geographic
    lat/lon (for the haversine d_eff). The station's projection coordinates
    (``station_proj_x``/``y``) are supplied by the caller's projection of the precise
    station coordinate (we never lat/lon snap).

    T_interp = w1 T1 + w2 T2 + w3 T3 ; z_interp likewise ; sum w_i == 1.
    d_eff = sqrt(sum w_i d_i^2) over the 3 nodes' haversine distances.

    When ``require_inside`` and the station is OUTSIDE the triangle (a negative
    barycentric weight beyond ``tol``), raise ``ValueError`` — extrapolating a native
    grid past its containing simplex is exactly the silent error v3 forbids; the caller
    must pick the correct containing triangle first.
    """
    if n1.proj_x is None or n2.proj_x is None or n3.proj_x is None:
        raise ValueError("barycentric interpolation requires proj_x/proj_y on all 3 nodes")

    w1, w2, w3 = barycentric_weights(
        station_proj_x,
        station_proj_y,
        n1.proj_x, n1.proj_y,  # type: ignore[arg-type]
        n2.proj_x, n2.proj_y,  # type: ignore[arg-type]
        n3.proj_x, n3.proj_y,  # type: ignore[arg-type]
    )
    inside = (w1 >= -tol) and (w2 >= -tol) and (w3 >= -tol)
    if require_inside and not inside:
        raise ValueError(
            f"station outside containing triangle (w=({w1:.6g},{w2:.6g},{w3:.6g})); "
            f"native-grid extrapolation is forbidden — select the correct triangle"
        )

    nodes = (n1, n2, n3)
    weights = (w1, w2, w3)
    t_interp = sum(w * n.value for w, n in zip(weights, nodes))
    z_interp = sum(w * n.elevation for w, n in zip(weights, nodes))
    d_i = tuple(
        haversine_m(station_lat, station_lon, n.lat, n.lon) for n in nodes
    )
    d_eff = _d_eff(weights, d_i)

    grid_ids = tuple(n.grid_id for n in nodes)
    provenance = {
        "geometry": "barycentric",
        "station": {
            "lat": station_lat,
            "lon": station_lon,
            "elevation": station_elevation,
            "proj_x": station_proj_x,
            "proj_y": station_proj_y,
        },
        "inside_triangle": inside,
        "nodes": [
            {
                "grid_id": n.grid_id,
                "lat": n.lat,
                "lon": n.lon,
                "proj_x": n.proj_x,
                "proj_y": n.proj_y,
                "value": n.value,
                "elevation": n.elevation,
                "weight": w,
                "d_i_m": d,
            }
            for n, w, d in zip(nodes, weights, d_i)
        ],
        "weights": list(weights),
        "d_i_m": list(d_i),
        "d_eff_m": d_eff,
    }
    return GridInterpolation(
        T_interp=t_interp,
        z_interp=z_interp,
        d_eff_m=d_eff,
        grid_ids=grid_ids,
        weights=weights,
        geometry="barycentric",
        provenance=provenance,
    )

# Created: 2026-09-25
# Last reused/audited: 2026-10-01
# Authority basis: docs/authority/statistical_calibration_addendum_2026-06-13.md D2 (CAR
#   interval-widening); docs/operations/current/plans/ens_boundary_interval_2026-09-25.md.
"""Antibodies for interval-censored ENS boundary-member admission.

Every test here fails with the change reverted: the classifier, the persisted bounds,
the shared admission predicate, the supremum-sigma shape and the plausibility floors
are each new behaviour. The exact-point regression test pins today's output.
"""
from __future__ import annotations

import itertools
import json
import math
import re
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import ingest_grib_to_snapshots as ingest  # type: ignore  # noqa: E402
from src.contracts.snapshot_ingest_contract import normalize_low_boundary_evidence  # noqa: E402
from src.data import forecast_extrema_authority as authority  # noqa: E402
from src.data import replacement_forecast_materializer as materializer  # noqa: E402
from src.types.metric_identity import HIGH_LOCALDAY_MAX, LOW_LOCALDAY_MIN  # noqa: E402
from tests.test_ingest_grib_source_run_context import (  # noqa: E402
    _complete_low_window_payload,
)
from tests.test_opendata_writes_v2_table import _boundary_high_payload  # noqa: E402

UTC = timezone.utc
INTERVAL = "INTERVAL_CENSORED_TARGET_LOCAL_DAY"


def _majority_low_payload() -> dict:
    """Complete-window NYC LOW row whose 30 members are boundary-colder (intervals)."""
    payload = _complete_low_window_payload(
        "NYC", "America/New_York", "2026-09-29", "2026-09-27T00:00:00+00:00"
    )
    for member in payload["members"]:
        inner = float(member["inner_min_native_unit"])
        member["boundary_min_native_unit"] = inner - 1.5 if member["member"] < 30 else inner + 1.0
    payload["boundary_policy"] = {"boundary_ambiguous": True, "ambiguous_member_count": 30}
    return normalize_low_boundary_evidence(payload)


def _high_payload() -> dict:
    payload = _boundary_high_payload()
    payload.update(
        physical_quantity="mx2t3_local_calendar_day_max",
        causality={"status": "OK"},
        aggregation_window_hours=3,
    )
    return payload


def _boundary_high_interval_payload() -> dict:
    payload = _high_payload()
    for member in payload["members"][:5]:
        member["boundary_max_native_unit"] = 71.5
        for window in member["native_windows"]:
            if window["value_native_unit"] == 69.0:
                window["value_native_unit"] = 71.5
    return payload


def _evidence(payload: dict, metric) -> tuple[dict, dict]:
    evidence = ingest._contract_evidence_fields(payload, metric, source_id="ecmwf_open_data")
    provenance = json.loads(ingest._provenance_json(payload, metric, contract_evidence=evidence))
    return evidence, provenance


# --- ingest classification + persistence -------------------------------------------------


def test_majority_low_interval_row_persists_bounds_and_never_contributes() -> None:
    payload = _majority_low_payload()
    evidence, provenance = _evidence(payload, LOW_LOCALDAY_MIN)

    assert evidence["forecast_window_attribution_status"] == INTERVAL
    assert evidence["contributes_to_target_extrema"] == 0  # leakage law: no point extreme
    bounds = provenance["member_interval_bounds"]
    assert bounds["revision"] == authority.MEMBER_INTERVAL_BOUNDS_REVISION
    assert len(bounds["bounds"]) == 51
    member0 = payload["members"][0]
    assert bounds["bounds"][0] == [
        member0["boundary_min_native_unit"], member0["inner_min_native_unit"]
    ]
    member40 = payload["members"][40]
    assert bounds["bounds"][40] == [member40["inner_min_native_unit"]] * 2
    # Point members stay null for every boundary-colder member (unchanged law).
    assert all(member["value_native_unit"] is None for member in payload["members"][:30])


def test_high_boundary_exceeds_inner_row_is_interval_with_boundary_upper() -> None:
    payload = _boundary_high_interval_payload()
    evidence, provenance = _evidence(payload, HIGH_LOCALDAY_MAX)

    assert evidence["forecast_window_attribution_status"] == INTERVAL
    assert evidence["contributes_to_target_extrema"] == 0
    bounds = provenance["member_interval_bounds"]["bounds"]
    assert bounds[0] == [70.0, 71.5]
    assert bounds[10] == [70.0, 70.0]


def _issued_after_day_start(payload: dict) -> dict:
    """Re-issue 6 h later: the run cannot see the elapsed start of the local day."""
    issue = datetime.fromisoformat(payload["issue_time_utc"]) + timedelta(hours=6)
    payload["issue_time_utc"] = issue.isoformat()

    def shift(step_range: str) -> str:
        start, end = map(int, step_range.split("-"))
        return f"{start - 6}-{end - 6}"

    for member in payload["members"]:
        for key in ("inner_step_ranges", "boundary_step_ranges"):
            member[key] = [shift(r) for r in member[key] if int(r.split("-")[0]) >= 6]
        windows = [w for w in member["native_windows"] if w["start_step_hours"] >= 6]
        for window in windows:
            window["start_step_hours"] -= 6
            window["end_step_hours"] -= 6
        member["native_windows"] = windows
        member["boundary_max_native_unit"] = max(
            w["value_native_unit"] for w in windows
            if f"{w['start_step_hours']}-{w['end_step_hours']}" in member["boundary_step_ranges"]
        )
    return payload


@pytest.mark.parametrize("defect", ["issued_inside_day", "nonfinite", "causality"])
def test_unrecoverable_high_ambiguity_stays_excluded(defect: str) -> None:
    payload = _boundary_high_interval_payload()
    if defect == "issued_inside_day":
        # Issued after local-day start: the run owns only the remaining window
        # (Day0 suffix law), so its native windows have no gap there; whole-day
        # interval bounds still cannot exist for it (D2 is whole-day only).
        payload = _issued_after_day_start(payload)
        assert ingest._high_local_day_max_boundary_certificate(payload)["reasons"] == [
            "boundary_can_exceed_inner",
        ]
    elif defect == "nonfinite":
        payload["members"][7]["inner_max_native_unit"] = float("nan")
    else:
        payload["causality"] = {"status": "N/A_CAUSAL_DAY_ALREADY_STARTED"}
    evidence, provenance = _evidence(payload, HIGH_LOCALDAY_MAX)

    assert evidence["forecast_window_attribution_status"] != INTERVAL
    assert evidence["contributes_to_target_extrema"] == 0
    assert "member_interval_bounds" not in provenance


def test_low_interval_requires_whole_day_native_coverage() -> None:
    payload = _majority_low_payload()
    payload["members"][-1]["inner_step_ranges"].pop(2)
    evidence, provenance = _evidence(payload, LOW_LOCALDAY_MIN)

    assert evidence["forecast_window_attribution_status"] == "AMBIGUOUS_CROSSES_LOCAL_DAY_BOUNDARY"
    assert "member_interval_bounds" not in provenance


def test_exact_rows_keep_exact_classification() -> None:
    evidence, provenance = _evidence(_high_payload(), HIGH_LOCALDAY_MAX)
    assert evidence["forecast_window_attribution_status"] == "FULLY_INSIDE_TARGET_LOCAL_DAY"
    assert evidence["contributes_to_target_extrema"] == 1
    assert "member_interval_bounds" not in provenance


# --- the shared admission predicate ------------------------------------------------------


def _eligibility_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE s (id INTEGER, contributes_to_target_extrema INTEGER, boundary_ambiguous INTEGER,"
        " causality_status TEXT, forecast_window_attribution_status TEXT, provenance_json TEXT)"
    )
    bounds = json.dumps({"member_interval_bounds": {"revision": "ens_member_interval_bounds_v1"}})
    stale = json.dumps({"member_interval_bounds": {"revision": "retired"}})
    rows = [
        (1, 1, 0, "OK", "FULLY_INSIDE_TARGET_LOCAL_DAY", "{}"),
        (2, 0, 1, "REJECTED_BOUNDARY_AMBIGUOUS", INTERVAL, bounds),
        (3, 0, 0, "OK", INTERVAL, bounds),
        (4, 0, 1, "REJECTED_BOUNDARY_AMBIGUOUS", "AMBIGUOUS_CROSSES_LOCAL_DAY_BOUNDARY", bounds),
        (5, 0, 0, "OK", "UNKNOWN", bounds),
        (6, 0, 0, "N/A_CAUSAL_DAY_ALREADY_STARTED", INTERVAL, bounds),
        (7, 1, 0, "OK", "AMBIGUOUS_CROSSES_LOCAL_DAY_BOUNDARY", "{}"),
        (8, 0, 0, "OK", INTERVAL, "{}"),
        (9, 0, 0, "OK", INTERVAL, stale),
        (10, 0, 0, "OK", INTERVAL, "not json"),
    ]
    conn.executemany("INSERT INTO s VALUES (?,?,?,?,?,?)", rows)
    return conn


def test_shared_predicate_admits_exact_and_interval_rows_only() -> None:
    conn = _eligibility_db()
    admitted = {
        row[0]
        for row in conn.execute(
            f"SELECT id FROM s WHERE {authority.current_evidence_ensemble_eligibility_sql()}"
        )
    }
    exact = {
        row[0]
        for row in conn.execute(f"SELECT id FROM s WHERE {authority.exact_ensemble_eligibility_sql()}")
    }
    # Status is the admission key (single writer persists bounds with it); rows 8-10
    # carry the status without usable bounds and are refused by the shape reader.
    assert admitted == {1, 2, 3, 8, 9, 10}
    assert exact == {1}


_ADMISSION_SITES = {
    # site -> the shared-predicate calls it must make
    "src/data/replacement_input_hwm.py": ("current_evidence_ensemble_eligibility_sql(",),
    "src/data/replacement_cycle_advance_trigger.py": ("current_evidence_ensemble_eligibility_sql(",),
    "src/ingest/forecast_live_daemon.py": ("current_evidence_ensemble_eligibility_sql(",),
    # Selector seeks each class on its own partial index, then takes the newer row.
    "src/data/replacement_forecast_materializer.py": (
        "exact_ensemble_eligibility_sql(",
        "interval_ensemble_eligibility_sql(",
    ),
}


@pytest.mark.parametrize("relative", sorted(_ADMISSION_SITES))
def test_replacement_chain_sites_call_the_shared_predicate(relative: str) -> None:
    """A site that re-inlines FULLY_INSIDE-only SQL silently re-darkens interval families."""
    text = (ROOT / relative).read_text(encoding="utf-8")
    for call in _ADMISSION_SITES[relative]:
        assert call in text
    inline = re.findall(
        r"forecast_window_attribution_status\s*=\s*'FULLY_INSIDE_TARGET_LOCAL_DAY'", text
    )
    # Only the materializer's legacy exact partial-index DDL may keep the literal.
    assert len(inline) == (1 if relative.endswith("replacement_forecast_materializer.py") else 0)


# --- conservative second-moment shape ----------------------------------------------------

_SHAPE_KW = dict(
    snapshot_id=7,
    source_cycle_time="2026-09-27T00:00:00+00:00",
    source_available_at="2026-09-27T08:00:00+00:00",
    provider_values_c={"ecmwf_ifs": 20.4, "icon_global": 19.6},
    provider_weights={"ecmwf_ifs": 0.5, "icon_global": 0.5},
    provider_cycles={
        "ecmwf_ifs": "2026-09-27T00:00:00+00:00",
        "icon_global": "2026-09-27T00:00:00+00:00",
    },
)


def _bounds() -> tuple[tuple[float, float], ...]:
    out = []
    for i in range(51):
        base = 18.0 + 0.08 * i
        out.append((base - (0.9 if i % 3 == 0 else 0.0), base))
    return tuple(out)


def test_interval_sigma_is_the_supremum_over_consistent_assignments() -> None:
    bounds = _bounds()
    center = 20.0
    shape = materializer._interval_censored_evidence_shape(
        member_bounds_c=bounds, center_c=center, **_SHAPE_KW
    )
    assert shape.interval_censored_member_count == 17
    assert shape.member_bounds_c == bounds
    rng = __import__("random").Random(0)
    worst_sigma = worst_center = 0.0
    for _ in range(400):
        xs = [lo + (hi - lo) * rng.choice((0.0, 1.0, rng.random())) for lo, hi in bounds]
        point = materializer._current_evidence_shape_from_values(
            members_c=xs, center_c=center, **_SHAPE_KW
        )
        worst_sigma = max(worst_sigma, point.predictive_sigma_c)
        worst_center = max(worst_center, point.center_sigma_c)
    assert shape.predictive_sigma_c >= worst_sigma - 1e-12
    assert shape.center_sigma_c >= worst_center - 1e-12
    # The supremum is attained by a consistent assignment (no invented width).
    witness = materializer._current_evidence_shape_from_values(
        members_c=shape.members_c, center_c=center, **_SHAPE_KW
    )
    assert math.isclose(witness.predictive_sigma_c, shape.predictive_sigma_c, rel_tol=0, abs_tol=1e-12)
    assert all(lo <= x <= hi for x, (lo, hi) in zip(shape.members_c, bounds))
    payload = shape.as_payload()
    assert payload["interval_censored_member_count"] == 17
    assert "member_bounds_c" not in payload


def test_exact_point_shape_is_byte_identical_to_today() -> None:
    members = [18.0 + 0.08 * i for i in range(51)]
    shape = materializer._current_evidence_shape_from_values(
        members_c=members, center_c=19.7, **_SHAPE_KW
    )
    assert getattr(shape, "member_bounds_c", None) is None
    # Pinned from a clean `git archive origin/live` (14244e742) on identical inputs.
    assert json.dumps(shape.as_payload(), sort_keys=True) == _EXACT_PAYLOAD_PIN


_EXACT_PAYLOAD_PIN = (
    '{"between_cohort_status": "SIMULTANEOUS_PROVEN", "center_sigma_c": 0.49212756728683693,'
    ' "effective_provider_count": 2.0, "ens_center_delta_raw_c": -0.3000000000000007,'
    ' "ensemble_center_delta_c": 0.3000000000000007, "ensemble_member_mean_c": 20.0,'
    ' "ensemble_within_sigma_c": 1.1775681155103799, "member_count": 51,'
    ' "member_values_hash": "bd476de13996eae496ffd8ab7928e266f94db803994f992884a7d51ff0d98e64",'
    ' "predictive_sigma_c": 1.3140268896284684, "provider_between_sigma_c": 0.4999999999999993,'
    ' "provider_count": 2, "semantics_revision": "ensemble_center_scenarios_v4",'
    ' "shape_hash": "4d1b830b730cbc14dc80cc4a3ba7ddc4681b47203c4157d0d8dd5778d00249d3",'
    ' "shape_lag_hours": 0.0, "snapshot_id": 7, "source_available_at": "2026-09-27T08:00:00+00:00",'
    ' "source_cycle_time": "2026-09-27T00:00:00+00:00", "translation_applied": false}'
)


# --- finite-evidence plausibility floors -------------------------------------------------


def _bins():
    return tuple(
        SimpleNamespace(bin_id=f"b{k}", lower_c=float(k), upper_c=float(k), center_c=float(k))
        for k in range(15, 24)
    )


def test_interval_hit_counts_dominate_every_consistent_assignment() -> None:
    bounds = _bounds()
    lowers = tuple(lo for lo, _ in bounds)
    uppers = tuple(hi for _, hi in bounds)
    plaus = materializer._current_evidence_member_hit_counts(
        bins=_bins(), half_step=0.5, rounding_rule="wmo_half_up",
        members_c=lowers, member_upper_c=uppers,
    )
    for pick in itertools.islice(itertools.product((0, 1), repeat=51), 0, 64):
        xs = [b[p] for b, p in zip(bounds, pick)]
        hits = materializer._current_evidence_member_hit_counts(
            bins=_bins(), half_step=0.5, rounding_rule="wmo_half_up", members_c=xs,
        )
        assert all(hits[k] <= plaus[k] for k in hits)
    exact = materializer._current_evidence_member_hit_counts(
        bins=_bins(), half_step=0.5, rounding_rule="wmo_half_up", members_c=uppers,
    )
    assert sum(plaus.values()) > sum(exact.values())


def test_interval_tail_floors_dominate_point_floors_of_the_witness() -> None:
    bounds = _bounds()
    shape = materializer._interval_censored_evidence_shape(
        member_bounds_c=bounds, center_c=20.0, **_SHAPE_KW
    )
    kwargs = dict(
        mu_star=20.0, predictive_sigma_c=shape.predictive_sigma_c, bins=_bins(),
        half_step=0.5, rounding_rule="wmo_half_up", members_c=shape.members_c, metric="low",
    )
    point = materializer._current_evidence_tail_ucb_floors(**kwargs)
    interval = materializer._current_evidence_tail_ucb_floors(member_bounds_c=bounds, **kwargs)
    assert all(interval[k] >= point[k] - 1e-15 for k in point)
    assert any(interval[k] > point[k] + 1e-9 for k in point)


def _band_bins(center: float) -> tuple:
    lo = round(center) - 4
    edges = [SimpleNamespace(bin_id="L", lower_c=None, upper_c=float(lo - 1), center_c=float(lo - 1))]
    edges += [
        SimpleNamespace(bin_id=f"b{k}", lower_c=float(k), upper_c=float(k), center_c=float(k))
        for k in range(lo, lo + 9)
    ]
    edges.append(SimpleNamespace(bin_id="R", lower_c=float(lo + 9), upper_c=None, center_c=float(lo + 9)))
    return tuple(edges)


def _served_band(shape, *, mu: float, bins: tuple, bounds=None) -> dict[str, float]:
    """q_ucb through the production bootstrap + finite-evidence stress, at the shape's sigma."""
    from src.calibration.emos import bin_probability_settlement

    raw = {
        b.bin_id: bin_probability_settlement(
            mu, shape.predictive_sigma_c, b.lower_c, b.upper_c,
            half_step=0.5, rounding_rule="wmo_half_up",
        )
        for b in bins
    }
    total = sum(raw.values())
    kwargs = dict(
        mu_star=mu, center_sigma_c=shape.center_sigma_c,
        predictive_sigma_c=shape.predictive_sigma_c, bins=bins, half_step=0.5,
        q_point={k: v / total for k, v in raw.items()}, rounding_rule="wmo_half_up",
        day0_metric="low", evidence_members_c=shape.members_c,
    )
    if bounds is not None:
        kwargs["evidence_member_bounds_c"] = bounds
    return materializer._build_fused_q_bounds(**kwargs)[1]


def _shape_kw(mu: float) -> dict:
    cycle = "2026-09-27T00:00:00+00:00"
    return dict(
        snapshot_id=7, source_cycle_time=cycle, source_available_at="2026-09-27T08:00:00+00:00",
        provider_values_c={"ecmwf_ifs": mu + 0.2, "icon_global": mu - 0.2},
        provider_weights={"ecmwf_ifs": 0.5, "icon_global": 0.5},
        provider_cycles={"ecmwf_ifs": cycle, "icon_global": cycle},
    )


@pytest.mark.parametrize(
    "bounds,mu",
    [
        # Reviewer counterexample (2026-09-25): two separated clusters around mu.
        (tuple([(-2.0, -0.51)] * 26 + [(0.51, 2.0)] * 25), 0.0),
        # Wide shared intervals: the feasible ENS mean spans 4 degC.
        (tuple((19.75 + 0.002 * i, 23.75 + 0.002 * i) for i in range(51)), 19.6),
    ],
)
def test_interval_q_ucb_dominates_every_feasible_point_assignment(bounds, mu) -> None:
    """Served interval q_ucb >= the served q_ucb of every enumerated consistent assignment."""
    import random

    bins = _band_bins(mu)
    interval_shape = materializer._interval_censored_evidence_shape(
        member_bounds_c=bounds, center_c=mu, **_shape_kw(mu)
    )
    interval_ucb = _served_band(interval_shape, mu=mu, bins=bins, bounds=bounds)
    rng = random.Random(0)
    split = sorted(v for bound in bounds for v in bound)[len(bounds)]
    assignments = [
        [lo for lo, _ in bounds],
        [hi for _, hi in bounds],
        [min(max(split, lo), hi) for lo, hi in bounds],
        [hi if lo < 0 else lo for lo, hi in bounds],
    ]
    assignments += [
        [lo + (hi - lo) * rng.choice((0.0, 1.0, rng.random())) for lo, hi in bounds]
        for _ in range(80)
    ]
    for xs in assignments:
        point_shape = materializer._current_evidence_shape_from_values(
            members_c=xs, center_c=mu, **_shape_kw(mu)
        )
        point_ucb = _served_band(point_shape, mu=mu, bins=bins)
        for bin_id, value in point_ucb.items():
            assert interval_ucb[bin_id] >= value - 1e-9, (bin_id, interval_ucb[bin_id], value)


def test_interval_floor_terms_each_bound_their_scenario() -> None:
    """Each sup term covers the scenario it claims: feasible ENS centers, then center draws."""
    import numpy as np

    bounds = tuple([(-2.0, -0.51)] * 26 + [(0.51, 2.0)] * 25)
    mu = 0.0
    bins = _band_bins(mu)
    shape = materializer._interval_censored_evidence_shape(
        member_bounds_c=bounds, center_c=mu, **_shape_kw(mu)
    )
    mean_lo, mean_hi, sd_lo, sd_hi = materializer._interval_member_scenario_ranges(bounds)
    floors = materializer._current_evidence_tail_ucb_floors(
        mu_star=mu, predictive_sigma_c=shape.predictive_sigma_c, bins=bins, half_step=0.5,
        rounding_rule="wmo_half_up", members_c=shape.members_c, metric="low",
        member_bounds_c=bounds,
    )
    # b0 = [-0.5, 0.5): an ENS center at 0 (feasible: mean range spans it) at the
    # smallest feasible spread puts far more mass there than the witness center does.
    at_zero = materializer._sup_normal_bin_mass(
        -0.5, 0.5, center_lo=0.0, center_hi=0.0, sd_lo=sd_lo, sd_hi=sd_lo
    )
    assert mean_lo < 0.0 < mean_hi
    assert floors["b0"] >= at_zero - 1e-12 > 0.6

    # Far one-sided intervals: the witness sits at the upper ends, but a consistent
    # assignment puts the ENS mean (and, with every member equal, a zero spread) at
    # the lower ends; that point scenario is certain for its bin.
    far = tuple((22.0 + 0.01 * i, 25.0 + 0.01 * i) for i in range(51))
    far_shape = materializer._interval_censored_evidence_shape(
        member_bounds_c=far, center_c=19.0, **_shape_kw(19.0)
    )
    far_floors = materializer._current_evidence_tail_ucb_floors(
        mu_star=19.0, predictive_sigma_c=far_shape.predictive_sigma_c, bins=_band_bins(19.0),
        half_step=0.5, rounding_rule="wmo_half_up", members_c=far_shape.members_c,
        metric="low", member_bounds_c=far,
    )
    witness_mean = sum(far_shape.members_c) / len(far_shape.members_c)
    assert witness_mean > 25.0
    assert far_floors["b22"] == pytest.approx(1.0)

    # Bootstrap: a draw's center may sit anywhere between mu* and mu* + center_sigma z.
    z = np.random.default_rng(materializer._QLCB_SEED).standard_normal(
        materializer._QLCB_BOOTSTRAP_DRAWS
    )
    lcb, ucb = materializer._build_fused_q_bounds(
        mu_star=mu, center_sigma_c=shape.center_sigma_c,
        predictive_sigma_c=shape.predictive_sigma_c, bins=bins, half_step=0.5,
        q_point={b.bin_id: 0.0 for b in bins}, rounding_rule="wmo_half_up",
        day0_metric="low", evidence_members_c=shape.members_c,
        evidence_member_bounds_c=bounds,
    )
    draw_sups = [
        materializer._sup_center_draw_mass(
            -0.5, 0.5, mu_star=mu, draw_z=float(zz), center_sigma_hi=shape.center_sigma_c,
            sd_lo=sd_lo, sd_hi=shape.predictive_sigma_c,
        )
        for zz in z
    ]
    assert ucb["b0"] >= float(np.percentile(draw_sups, 95.0)) - 1e-9


def test_interval_q_ucb_covers_bootstrap_center_draws_of_every_assignment() -> None:
    """Tight ENS intervals far from mu*: a point assignment's bootstrap moves the center.

    Its center draws sweep toward the ENS mean with the assignment's own center sigma, so
    a bin near the ENS cluster gets bootstrap mass that no finite-evidence floor carries.
    """

    bounds = tuple((20.0 + 0.004 * i, 20.2 + 0.004 * i) for i in range(51))
    mu = 18.0
    bins = _band_bins(mu)
    interval_shape = materializer._interval_censored_evidence_shape(
        member_bounds_c=bounds, center_c=mu, **_shape_kw(mu)
    )
    interval_ucb = _served_band(interval_shape, mu=mu, bins=bins, bounds=bounds)
    floors = materializer._current_evidence_tail_ucb_floors(
        mu_star=mu, predictive_sigma_c=interval_shape.predictive_sigma_c, bins=bins,
        half_step=0.5, rounding_rule="wmo_half_up", members_c=interval_shape.members_c,
        metric="low", member_bounds_c=bounds,
    )
    for xs in ([lo for lo, _ in bounds], [hi for _, hi in bounds]):
        point_shape = materializer._current_evidence_shape_from_values(
            members_c=xs, center_c=mu, **_shape_kw(mu)
        )
        point_ucb = _served_band(point_shape, mu=mu, bins=bins)
        for bin_id, value in point_ucb.items():
            assert interval_ucb[bin_id] >= value - 1e-9, (bin_id, interval_ucb[bin_id], value)
    # The draw-sup term is what carries it: at least one bin exceeds every finite floor,
    # and each bin's q_ucb covers the 95th percentile of its per-draw sup.
    import numpy as np

    assert any(interval_ucb[b.bin_id] > floors[b.bin_id] + 0.05 for b in bins)
    z = np.random.default_rng(materializer._QLCB_SEED).standard_normal(
        materializer._QLCB_BOOTSTRAP_DRAWS
    )
    sd_lo = materializer._interval_member_scenario_ranges(bounds)[2]
    for b in bins:
        low = -math.inf if b.lower_c is None else b.lower_c - 0.5
        high = math.inf if b.upper_c is None else b.upper_c + 0.5
        draw_sups = [
            materializer._sup_center_draw_mass(
                low, high, mu_star=mu, draw_z=float(zz),
                center_sigma_hi=interval_shape.center_sigma_c, sd_lo=sd_lo,
                sd_hi=interval_shape.predictive_sigma_c,
            )
            for zz in z
        ]
        assert interval_ucb[b.bin_id] >= float(np.percentile(draw_sups, 95.0)) - 1e-9, b.bin_id


@pytest.mark.parametrize(
    "k,floor_c",
    [(1.0, None), (0.6, None), (0.6, 1.9), (1.4, 0.4), (0.25, 3.0)],
)
def test_served_interval_sigma_dominates_every_assignment_through_the_ladder(k, floor_c) -> None:
    """HIGH 2: k(tau) and the floors act on sigma alone, monotone non-decreasing.

    k(tau) depends on (unit, metric, lead bucket, city), never on the member values, and
    the step/settlement floors are max(); so served(sigma) = max(k*sigma, floors) is
    monotone and sup_x served(sigma(x)) = served(sup_x sigma(x)) = served(sigma_sup).
    Checked through the production q builder: the served q of the interval shape is the
    q of the max served sigma over enumerated consistent assignments.
    """
    import random

    from src.calibration.emos import bin_probability_settlement

    bounds = tuple([(-2.0, -0.51)] * 26 + [(0.51, 2.0)] * 25)
    mu = 0.0
    bins = _band_bins(mu)

    def served_sigma(sigma_pred: float) -> float:
        sigma = sigma_pred * k if (k != 1.0 and k > 0.0) else sigma_pred
        return max(sigma, floor_c) if floor_c is not None else sigma

    def served_q(sigma_pred: float) -> dict[str, float]:
        q, _, _ = materializer._build_scaled_normal_uniform_q(
            mu=mu, sigma_pred=sigma_pred, k=k, uniform_w=0.0, floor_steps=0.0,
            bins=bins, half_step=0.5, rounding_rule="wmo_half_up", day0_obs_extreme_c=None,
            settlement_step_c=1.0, settlement_sigma_floor_c=floor_c, city_unit="C",
            metric="low",
        )
        return q

    interval = materializer._interval_censored_evidence_shape(
        member_bounds_c=bounds, center_c=mu, **_shape_kw(mu)
    )
    rng = random.Random(0)
    worst = 0.0
    for _ in range(200):
        xs = [lo + (hi - lo) * rng.choice((0.0, 1.0, rng.random())) for lo, hi in bounds]
        point = materializer._current_evidence_shape_from_values(
            members_c=xs, center_c=mu, **_shape_kw(mu)
        )
        worst = max(worst, served_sigma(point.predictive_sigma_c))
    assert served_sigma(interval.predictive_sigma_c) >= worst - 1e-12
    # The production builder serves exactly the ladder's sigma. Open-ended bins may be
    # capped at their un-floored mass, so compare interior bins' relative shape.
    q_interval = served_q(interval.predictive_sigma_c)
    interior = [b for b in bins if b.lower_c is not None and b.upper_c is not None]
    reference = {
        b.bin_id: bin_probability_settlement(
            mu, served_sigma(interval.predictive_sigma_c), b.lower_c, b.upper_c,
            half_step=0.5, rounding_rule="wmo_half_up",
        )
        for b in interior
    }
    ref_total = sum(reference.values())
    served_total = sum(q_interval[b.bin_id] for b in interior)
    for b in interior:
        assert q_interval[b.bin_id] / served_total == pytest.approx(
            reference[b.bin_id] / ref_total, abs=1e-9
        )


@pytest.mark.parametrize(
    "error,deferred",
    [("database is locked", True), ("database table is locked", True), ("no such table: x", False)],
)
def test_frontier_index_bootstrap_contention_defers_instead_of_failing(
    tmp_path, monkeypatch, error: str, deferred: bool
) -> None:
    """First-time partial-index DDL runs outside the LIVE lock; BUSY/LOCKED must retry."""
    import scripts.materialize_replacement_forecast_live as cli
    from tests.test_replacement_forecast_materializer import (
        _anchor,
        _bins,
        _current_baseline_data_version,
        _precision_guard,
    )

    payload = {
        "city": "Shanghai",
        "city_id": "Shanghai",
        "city_timezone": "Asia/Shanghai",
        "target_date": "2026-06-07",
        "temperature_metric": "high",
        "source_cycle_time": "2026-06-06T00:00:00+00:00",
        "computed_at": "2026-06-06T04:00:00+00:00",
        "expires_at": "2026-06-06T06:00:00+00:00",
        "baseline_source_run_id": "b0-run",
        "baseline_data_version": _current_baseline_data_version("high"),
        "baseline_source_available_at": "2026-06-06T02:00:00+00:00",
        "openmeteo_source_run_id": "om9-run",
        "openmeteo_source_available_at": "2026-06-06T03:00:00+00:00",
        "openmeteo_payload_json": "anchor.json",
        "precision_metadata_json": "precision.json",
        "bins": [{"bin_id": "warm", "lower_c": 20.0, "upper_c": 30.0}],
    }
    input_json = tmp_path / "request.json"
    input_json.write_text(json.dumps(payload), encoding="utf-8")
    (tmp_path / "anchor.json").write_text("{}", encoding="utf-8")
    (tmp_path / "precision.json").write_text("{}", encoding="utf-8")
    conn = sqlite3.connect(":memory:")
    committed: list[bool] = []

    def contended_bootstrap(_connection: sqlite3.Connection) -> None:
        raise sqlite3.OperationalError(error)

    monkeypatch.setattr(
        cli, "extract_openmeteo_ecmwf_ifs9_localday_anchor", lambda *_a, **_k: _anchor()
    )
    monkeypatch.setattr(cli, "OpenMeteoIfs9PrecisionMetadata", lambda **_k: object())
    monkeypatch.setattr(
        cli, "evaluate_openmeteo_ecmwf_ifs9_precision_guard", lambda _m: _precision_guard()
    )
    monkeypatch.setattr(cli, "_bins", lambda _payload: _bins())
    monkeypatch.setattr(cli, "_ensure_replacement_frontier_indexes", contended_bootstrap)
    monkeypatch.setattr(
        cli,
        "_prepare_live_schema_and_manifest",
        lambda *_a, **_k: cli._DurablePreparationReceipt(
            schema_ready=True, anchor_artifact_id=None, manifest_committed=False
        ),
    )
    monkeypatch.setattr(
        cli, "_commit_from_read_snapshot", lambda *_a, **_k: committed.append(True)
    )

    @contextmanager
    def writer_lock():
        yield

    returncode, response = cli._materialize(
        input_json, commit=True, init_schema=False, conn=conn, writer_lock=writer_lock
    )
    conn.close()

    assert committed == []
    assert returncode == 2
    if deferred:
        assert response["reason_codes"] == ["REPLACEMENT_FORECAST_WRITE_DEFERRED"]
        assert response["retry_safe"] is True
    else:
        assert "reason_codes" not in response


def test_sup_normal_bin_mass_bounds_the_grid() -> None:
    """The closed-form (center, spread) sup is >= a dense grid over both ranges."""
    from src.calibration.emos import bin_probability_settlement

    # The last case pins the interior spread optimum s* (not either range end):
    # bin [2.5, 3.5) seen from c=0 peaks at s* = sqrt((b^2-a^2)/(2 ln(b/a))) ~ 2.99.
    cases = [(-0.5, 0.5, -1.2, 0.8, 0.3, 2.0), (1.5, 2.5, -1.0, 0.5, 0.2, 1.4),
             (-math.inf, -2.5, -1.0, 1.0, 0.1, 3.0), (3.5, math.inf, 0.0, 0.0, 0.0, 1.0),
             (2.5, 3.5, 0.0, 0.0, 0.5, 8.0)]
    for low, high, c_lo, c_hi, s_lo, s_hi in cases:
        sup = materializer._sup_normal_bin_mass(
            low, high, center_lo=c_lo, center_hi=c_hi, sd_lo=s_lo, sd_hi=s_hi
        )
        for i in range(41):
            c = c_lo + (c_hi - c_lo) * i / 40
            for j in range(41):
                s = max(s_lo + (s_hi - s_lo) * j / 40, 1e-9)
                lo_c = None if not math.isfinite(low) else low + 0.5
                hi_c = None if not math.isfinite(high) else high - 0.5
                mass = bin_probability_settlement(c, s, lo_c, hi_c, half_step=0.5)
                assert sup >= mass - 1e-12, (low, high, c, s, sup, mass)
    # Interior optimum is attained, and strictly above both range ends.
    interior = materializer._sup_normal_bin_mass(
        2.5, 3.5, center_lo=0.0, center_hi=0.0, sd_lo=0.5, sd_hi=8.0
    )
    ends = [
        bin_probability_settlement(0.0, s, 3.0, 3.0, half_step=0.5) for s in (0.5, 8.0)
    ]
    assert interior > max(ends) + 0.02



# --- reader: persisted bounds reach the shape, legacy rows fail closed -------------------


def _selector_db(*, status: str, provenance: dict, members: list) -> tuple[sqlite3.Connection, object]:
    from tests.test_replacement_forecast_materializer import (
        _conn,
        _current_baseline_data_version,
        _dt,
        _ensure_source_run_coverage_table,
        _ensure_source_run_table,
        _prepared_target_frontier,
        _set_target_frontier_coverage,
    )
    from src.state.source_run_repo import write_source_run

    conn = _conn()
    _ensure_source_run_table(conn)
    _ensure_source_run_coverage_table(conn)
    write_source_run(
        conn, source_run_id="ens-run", source_id="ecmwf_open_data",
        track="mx2t6_high_short_horizon", release_calendar_key="ecmwf_open_data:mx2t6_high:short",
        source_cycle_time=_dt(0), source_available_at=_dt(3), fetch_finished_at=_dt(3),
        captured_at=_dt(3), imported_at=_dt(3), status="SUCCESS",
        completeness_status="COMPLETE", partial_run=False,
    )
    conn.execute(
        """
        INSERT INTO ensemble_snapshots (
            snapshot_id, city, target_date, temperature_metric, physical_quantity,
            observation_field, issue_time, available_at, fetch_time, lead_hours,
            members_json, model_version, dataset_id, source_id, source_cycle_time,
            source_available_at, source_run_id, forecast_window_attribution_status,
            contributes_to_target_extrema, causality_status, boundary_ambiguous,
            members_unit, provenance_json, authority
        ) VALUES (101, 'Shanghai', '2026-06-07', 'high', 'mx2t3_local_calendar_day_max',
                  'high_temp', '2026-06-06T00:00:00+00:00', '2026-06-06T03:00:00+00:00',
                  '2026-06-06T03:00:00+00:00', 24, ?, 'ecmwf_ens', ?, 'ecmwf_open_data',
                  '2026-06-06T00:00:00+00:00', '2026-06-06T03:00:00+00:00', 'ens-run',
                  ?, 0, 'OK', 0, 'degC', ?, 'VERIFIED')
        """,
        (json.dumps(members), _current_baseline_data_version("high"), status, json.dumps(provenance)),
    )
    _set_target_frontier_coverage(
        conn, snapshot_id=101, coverage_id="ens-coverage-101", source_run_id="ens-run",
        track="mx2t6_high_short_horizon", release_key="ecmwf_open_data:mx2t6_high:short",
    )
    return conn, _prepared_target_frontier(101).request


def _interval_provenance() -> dict:
    bounds = [[24.0 + 0.05 * i, 24.0 + 0.05 * i + (1.2 if i < 8 else 0.0)] for i in range(51)]
    return {"member_interval_bounds": {
        "revision": authority.MEMBER_INTERVAL_BOUNDS_REVISION, "unit": "C", "bounds": bounds,
    }}


def test_interval_row_with_bounds_is_selected_and_shaped_from_bounds() -> None:
    from src.data.replacement_input_hwm import latest_eligible_ensemble_input_cycle

    provenance = _interval_provenance()
    conn, request = _selector_db(
        status=INTERVAL, provenance=provenance, members=[24.0 + 0.05 * i for i in range(51)]
    )
    identity = materializer.read_current_evidence_snapshot_identity(conn, request, metric="high")
    assert identity is not None and identity.snapshot_id == 101
    assert identity.member_bounds == tuple(
        tuple(b) for b in provenance["member_interval_bounds"]["bounds"]
    )
    cycle = latest_eligible_ensemble_input_cycle(
        conn, city="Shanghai", target_date="2026-06-07", metric="high",
        decision_time=request.computed_at,
    )
    assert cycle == datetime(2026, 6, 6, 0, tzinfo=UTC)
    shape = materializer._read_current_evidence_shape(
        conn, request, metric="high",
        provider_values_c={"ecmwf_ifs": 25.4, "icon_global": 25.0},
        provider_weights={"ecmwf_ifs": 0.5, "icon_global": 0.5},
        center_c=25.2,
        provider_cycles={"ecmwf_ifs": "2026-06-06T00:00:00+00:00",
                         "icon_global": "2026-06-06T00:00:00+00:00"},
    )
    assert shape is not None
    assert shape.interval_censored_member_count == 8
    assert shape.member_bounds_c is not None


@pytest.mark.parametrize("provenance", [{}, {"member_interval_bounds": {"revision": "retired"}}])
def test_interval_status_row_without_current_bounds_fails_closed(provenance: dict) -> None:
    """A status without usable bounds never yields a shape (no point fallback)."""
    conn, request = _selector_db(status=INTERVAL, provenance=provenance, members=[None] * 51)
    assert materializer.read_current_evidence_snapshot_identity(conn, request, metric="high") is None


@pytest.mark.parametrize("interval_is_newer", [True, False])
def test_selector_takes_the_newest_row_across_exact_and_interval(interval_is_newer: bool) -> None:
    """Exact and interval rows compete on one newest-cycle order; neither class shadows."""
    from tests.test_replacement_forecast_materializer import (
        _current_baseline_data_version,
        _set_target_frontier_coverage,
    )

    conn, request = _selector_db(
        status=INTERVAL, provenance=_interval_provenance(),
        members=[24.0 + 0.05 * i for i in range(51)],
    )
    # The newer row sits at the 00Z carrier cycle, the older at 23Z (both within the
    # <= carrier-cycle bound; one row per cycle under the canonical unique key).
    exact_cycle = "2026-06-05T23:00:00+00:00" if interval_is_newer else "2026-06-06T00:00:00+00:00"
    if not interval_is_newer:
        conn.execute(
            "UPDATE ensemble_snapshots SET issue_time = ?, source_cycle_time = ? WHERE snapshot_id = 101",
            ("2026-06-05T23:00:00+00:00", "2026-06-05T23:00:00+00:00"),
        )
    conn.execute(
        """
        INSERT INTO ensemble_snapshots (
            snapshot_id, city, target_date, temperature_metric, physical_quantity,
            observation_field, issue_time, available_at, fetch_time, lead_hours,
            members_json, model_version, dataset_id, source_id, source_cycle_time,
            source_available_at, source_run_id, forecast_window_attribution_status,
            contributes_to_target_extrema, causality_status, boundary_ambiguous,
            members_unit, provenance_json, authority
        ) VALUES (102, 'Shanghai', '2026-06-07', 'high', 'mx2t3_local_calendar_day_max',
                  'high_temp', ?, '2026-06-06T03:00:00+00:00', '2026-06-06T03:00:00+00:00',
                  24, '[25.0]', 'ecmwf_ens', ?, 'ecmwf_open_data', ?,
                  '2026-06-06T03:00:00+00:00', 'ens-run',
                  'FULLY_INSIDE_TARGET_LOCAL_DAY', 1, 'OK', 0, 'degC', '{}', 'VERIFIED')
        """,
        (exact_cycle, _current_baseline_data_version("high"), exact_cycle),
    )
    _set_target_frontier_coverage(
        conn, snapshot_id=102, coverage_id="ens-coverage-102", source_run_id="ens-run",
        track="mx2t6_high_short_horizon", release_key="ecmwf_open_data:mx2t6_high:short",
    )
    conn.execute(
        "UPDATE source_run_coverage SET snapshot_ids_json = '[101]' WHERE coverage_id = 'ens-coverage-101'"
    )
    identity = materializer.read_current_evidence_snapshot_identity(conn, request, metric="high")
    assert identity is not None
    assert identity.snapshot_id == (101 if interval_is_newer else 102)
    assert (identity.member_bounds is not None) is interval_is_newer


def test_majority_ambiguous_legacy_row_stays_excluded() -> None:
    from src.data.replacement_input_hwm import latest_eligible_ensemble_input_cycle

    conn, request = _selector_db(
        status="AMBIGUOUS_CROSSES_LOCAL_DAY_BOUNDARY", provenance={}, members=[None] * 51
    )
    assert materializer.read_current_evidence_snapshot_identity(conn, request, metric="high") is None
    assert latest_eligible_ensemble_input_cycle(
        conn, city="Shanghai", target_date="2026-06-07", metric="high",
        decision_time=request.computed_at,
    ) is None


def _row(status: str, provenance: dict) -> dict:
    return {"forecast_window_attribution_status": status, "provenance_json": json.dumps(provenance)}


@pytest.mark.parametrize("with_bounds", [True, False])
def test_coverage_marks_interval_row_live_eligible_only_with_bounds(with_bounds: bool) -> None:
    import tempfile

    from src.data.ecmwf_open_data import _write_source_authority_chain
    from src.state.db import init_schema_forecasts
    from tests.test_opendata_observed_members_aggregation import _DATA_VERSION, _insert_snapshot

    with tempfile.TemporaryDirectory() as tmp:
        conn = sqlite3.connect(Path(tmp) / "forecasts.db")
        conn.row_factory = sqlite3.Row
        init_schema_forecasts(conn)
        run = "ecmwf_open_data:mx2t6_high:2026-05-30T00Z:interval"
        _insert_snapshot(
            conn, snapshot_id=41, city="London", target_date="2026-05-31", source_run_id=run,
            members_json=json.dumps([None] * 51), contributes=0, attribution_status=INTERVAL,
            boundary_ambiguous=1, ambiguous_member_count=51,
            local_day_start_utc="2026-05-30T23:00:00+00:00",
        )
        provenance = {"manifest_sha256": "m" * 64}
        if with_bounds:
            provenance.update(_interval_provenance())
        conn.execute(
            "UPDATE ensemble_snapshots SET provenance_json = ? WHERE snapshot_id = 41",
            (json.dumps(provenance),),
        )
        cycle = datetime(2026, 5, 30, 0, tzinfo=UTC)
        _write_source_authority_chain(
            conn, summary={"written": 1, "errors": 0}, status="ok", source_run_id=run,
            source_cycle_time=cycle, source_release_time=cycle,
            release_calendar_key="2026-05-30T00Z", forecast_track="mx2t6_high",
            data_version=_DATA_VERSION, computed_at=cycle,
        )
        coverage = conn.execute(
            "SELECT observed_members, completeness_status, readiness_status FROM"
            " source_run_coverage WHERE source_run_id = ?", (run,),
        ).fetchone()
        conn.close()
    if with_bounds:
        assert (coverage["observed_members"], coverage["readiness_status"]) == (51, "LIVE_ELIGIBLE")
        assert coverage["completeness_status"] == "COMPLETE"
    else:
        assert coverage["readiness_status"] == "BLOCKED"


def test_bounds_parser_fails_closed_on_legacy_and_malformed_rows() -> None:
    good = {"member_interval_bounds": {"revision": authority.MEMBER_INTERVAL_BOUNDS_REVISION,
                                       "unit": "C", "bounds": [[1.0, 2.0]] * 51}}
    assert authority.member_interval_bounds_from_row(_row(INTERVAL, good)) == ((1.0, 2.0),) * 51
    assert authority.member_interval_bounds_from_row(_row("FULLY_INSIDE_TARGET_LOCAL_DAY", good)) is None
    assert authority.member_interval_bounds_from_row(_row(INTERVAL, {})) is None  # legacy hash-only row
    for bad in ([[2.0, 1.0]] * 51, [[1.0, 2.0]] * 50, [[1.0, float("inf")]] * 51):
        broken = {"member_interval_bounds": dict(good["member_interval_bounds"], bounds=bad)}
        assert authority.member_interval_bounds_from_row(
            _row(INTERVAL, json.loads(json.dumps(broken, allow_nan=True)))
        ) is None


# --- Day0 owned-window (suffix) law for runs issued inside the target day -----------------


def test_owned_window_is_the_one_shared_day0_suffix_law() -> None:
    from src.data.forecast_target_contract import intervals_cover_window, owned_window_start_utc

    start = datetime(2026, 9, 30, 16, tzinfo=UTC)
    end = start + timedelta(days=1)

    def owned(at):
        return owned_window_start_utc(day_start_utc=start, day_end_utc=end, remaining_from_utc=at)

    assert owned(None) == start
    assert owned(start - timedelta(hours=4)) == start  # not started: whole day
    assert owned(start + timedelta(hours=8)) == start + timedelta(hours=8)  # in progress
    assert owned(end) == start  # ended: whole-day law
    windows = [(start + timedelta(hours=h), start + timedelta(hours=h + 3)) for h in (8, 11, 14, 20)]
    assert not intervals_cover_window(windows, start=start + timedelta(hours=8), end=end)
    windows.append((start + timedelta(hours=17), start + timedelta(hours=20)))
    windows.append((start + timedelta(hours=23), start + timedelta(hours=26)))
    assert intervals_cover_window(windows, start=start + timedelta(hours=8), end=end)
    assert not intervals_cover_window(windows, start=start, end=end)


def test_ens_and_om9_suffix_law_call_the_one_owned_window() -> None:
    source = (ROOT / "src/data/openmeteo_ecmwf_ifs9_anchor.py").read_text()
    body = source.split("def _assert_complete_localday_hourly_slots", 1)[1].split("\ndef ", 1)[0]
    assert "owned_window_start_utc(" in body
    ingest_source = (ROOT / "scripts/ingest_grib_to_snapshots.py").read_text()
    remaining = ingest_source.split("def _remaining_window_start", 1)[1].split("\ndef ", 1)[0]
    assert "owned_window_start_utc(" in remaining


def _exact_high_issued_inside_day() -> dict:
    return _issued_after_day_start(_high_payload())


def test_high_run_issued_inside_day_certifies_only_its_remaining_window() -> None:
    payload = _exact_high_issued_inside_day()
    certificate = ingest._high_local_day_max_boundary_certificate(payload)
    assert certificate["status"] == "REMAINING_EXACT"
    assert certificate["owned_window_start_utc"] == payload["issue_time_utc"]
    evidence, provenance = _evidence(payload, HIGH_LOCALDAY_MAX)
    # A remaining-window extreme is a different random variable from the daily
    # extreme: typed, never a point contributor, never interval bounds.
    assert evidence["forecast_window_attribution_status"] == authority.REMAINING_WINDOW_ATTRIBUTION_STATUS
    assert evidence["contributes_to_target_extrema"] == 0
    assert evidence["forecast_window_start_utc"] == payload["issue_time_utc"]
    assert "member_interval_bounds" not in provenance


def test_remaining_window_still_requires_no_gap_inside_the_owned_window() -> None:
    payload = _exact_high_issued_inside_day()
    payload["members"][3]["inner_step_ranges"].pop(2)
    certificate = ingest._high_local_day_max_boundary_certificate(payload)
    assert certificate["status"] == "UNKNOWN"
    assert "native_interval_gap" in certificate["reasons"]


def test_pre_start_run_keeps_the_full_day_certificate() -> None:
    payload = _high_payload()
    assert ingest._remaining_window_start(payload) is None
    assert ingest._high_local_day_max_boundary_certificate(payload)["status"] == "EXACT"
    payload["members"][0]["inner_step_ranges"].pop(0)
    certificate = ingest._high_local_day_max_boundary_certificate(payload)
    assert certificate["status"] == "UNKNOWN"
    assert "native_interval_gap" in certificate["reasons"]
    assert "owned_window_start_utc" not in certificate


def test_low_run_issued_inside_day_covers_only_its_remaining_window() -> None:
    target = "2026-09-29"  # NYC local day starts 04Z
    inside = _complete_low_window_payload("NYC", "America/New_York", target, "2026-09-29T12:00:00+00:00")
    assert ingest._low_native_windows_cover_day(
        inside, city_timezone="America/New_York", target_date=target,
    )
    pre_start = _complete_low_window_payload("NYC", "America/New_York", target, "2026-09-29T00:00:00+00:00")
    for member in pre_start["members"]:
        for key in ("inner_step_ranges", "boundary_step_ranges"):
            member[key] = [r for r in member[key] if r[0] >= 12]
    assert not ingest._low_native_windows_cover_day(
        pre_start, city_timezone="America/New_York", target_date=target,
    )


def test_classifier_types_remaining_window_rows_as_current_evidence_not_point() -> None:
    row = {
        "contributes_to_target_extrema": 0,
        "forecast_window_attribution_status": authority.REMAINING_WINDOW_ATTRIBUTION_STATUS,
        "boundary_ambiguous": 0,
    }
    eligibility = authority.classify_forecast_extrema_authority(row).eligibility
    assert eligibility == authority.ForecastExtremaEligibility.REMAINING_WINDOW_CONTRIBUTOR
    assert eligibility in authority.CURRENT_EVIDENCE_ELIGIBILITIES
    assert eligibility not in authority.POINT_EXTREMA_ELIGIBILITIES
    for damaged in (dict(row, boundary_ambiguous=1),
                    dict(row, forecast_window_attribution_status="UNKNOWN")):
        assert (authority.classify_forecast_extrema_authority(damaged).eligibility
                == authority.ForecastExtremaEligibility.NON_CONTRIBUTOR)


def _authority_chain_db(tmp: str, status: str, *, contributes: int, provenance: dict | None = None):
    from src.data.ecmwf_open_data import _write_source_authority_chain
    from src.state.db import init_schema_forecasts
    from tests.test_opendata_observed_members_aggregation import _DATA_VERSION, _insert_snapshot

    conn = sqlite3.connect(Path(tmp) / "forecasts.db")
    conn.row_factory = sqlite3.Row
    init_schema_forecasts(conn)
    run = "ecmwf_open_data:mx2t6_high:2026-05-30T00Z:remaining"
    _insert_snapshot(
        conn, snapshot_id=51, city="London", target_date="2026-05-31", source_run_id=run,
        members_json=json.dumps([20.0] * 51), contributes=contributes, attribution_status=status,
        local_day_start_utc="2026-05-30T23:00:00+00:00",
    )
    if provenance is not None:
        conn.execute("UPDATE ensemble_snapshots SET provenance_json = ? WHERE snapshot_id = 51",
                     (json.dumps(provenance),))
    cycle = datetime(2026, 5, 30, 0, tzinfo=UTC)
    _write_source_authority_chain(
        conn, summary={"written": 1, "errors": 0}, status="ok", source_run_id=run,
        source_cycle_time=cycle, source_release_time=cycle,
        release_calendar_key="2026-05-30T00Z", forecast_track="mx2t6_high",
        data_version=_DATA_VERSION, computed_at=cycle,
    )
    return conn, run, _DATA_VERSION


@pytest.mark.parametrize(
    "status,contributes,current,point",
    [
        ("REMAINING_WINDOW_TARGET_LOCAL_DAY", 0, True, False),
        ("FULLY_INSIDE_TARGET_LOCAL_DAY", 1, True, True),
        ("UNKNOWN", 0, False, False),
    ],
)
def test_writer_and_reader_share_one_extrema_classifier(status, contributes, current, point) -> None:
    import tempfile

    from src.data.executable_forecast_reader import read_executable_forecast_snapshot
    from src.data.forecast_target_contract import ForecastTargetScope

    with tempfile.TemporaryDirectory() as tmp:
        conn, run, version = _authority_chain_db(tmp, status, contributes=contributes)
        coverage = conn.execute(
            "SELECT readiness_status FROM source_run_coverage WHERE source_run_id = ?", (run,)
        ).fetchone()
        scope = ForecastTargetScope(
            city_id="LONDON", city_name="London", city_timezone="Europe/London",
            target_local_date=datetime(2026, 5, 31).date(), temperature_metric="high",
            source_cycle_time=datetime(2026, 5, 30, 0, tzinfo=UTC), data_version=version,
            target_window_start_utc=datetime(2026, 5, 30, 23, tzinfo=UTC),
            target_window_end_utc=datetime(2026, 5, 31, 23, tzinfo=UTC),
            required_step_hours=(24,), market_refs=(),
        )
        # A non-ECMWF source id skips the grid-surface revision gate that precedes
        # the extrema gate, so this exercises the classifier decision alone.
        conn.execute("UPDATE ensemble_snapshots SET source_id = 'extrema_gate_probe'")
        reads = {
            required: read_executable_forecast_snapshot(
                conn, scope=scope, source_id="extrema_gate_probe", source_run_id=run,
                point_extrema_required=required,
            ).reason_code
            for required in (True, False)
        }
        conn.close()
    blocked = "EXECUTABLE_FORECAST_NON_CONTRIBUTING_EXTREMA"
    assert (coverage["readiness_status"] == "LIVE_ELIGIBLE") is current
    assert (reads[False] != blocked) is current
    # A point daily-extreme consumer never reads a remaining-window row's members.
    assert (reads[True] != blocked) is point


def test_interval_row_writer_and_classifier_agree() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        conn, run, _version = _authority_chain_db(
            tmp, INTERVAL, contributes=0,
            provenance={"manifest_sha256": "m" * 64, **_interval_provenance()},
        )
        row = dict(conn.execute("SELECT * FROM ensemble_snapshots WHERE snapshot_id = 51").fetchone())
        coverage = conn.execute(
            "SELECT readiness_status FROM source_run_coverage WHERE source_run_id = ?", (run,)
        ).fetchone()
        conn.close()
    assert (authority.classify_forecast_extrema_authority(row).eligibility
            == authority.ForecastExtremaEligibility.INTERVAL_CONTRIBUTOR)
    assert coverage["readiness_status"] == "LIVE_ELIGIBLE"


def test_only_the_day0_base_read_admits_current_evidence_rows() -> None:
    source = (ROOT / "src/engine/event_reactor_adapter.py").read_text()
    body = source.split("def _executable_forecast_reader_authority_block_reason", 1)[1].split("\ndef ", 1)[0]
    assert "point_extrema_required=not allow_latest" in body


# --- after day end: a remaining-window row over [tau, day end) is current evidence --------
# Shanghai 2026-06-07 local day is [06-06T16Z, 06-07T16Z). The 00Z selector row
# sits in it; we type it REMAINING_WINDOW [06-07T00Z, day end) as the writer does
# for a run issued after local midnight.

REMAINING = authority.REMAINING_WINDOW_ATTRIBUTION_STATUS
_DAY_START, _DAY_END = "2026-06-06T16:00:00+00:00", "2026-06-07T16:00:00+00:00"


def _remaining_db(window_start: str = "2026-06-07T00:00:00+00:00"):
    conn, request = _selector_db(status=REMAINING, provenance={}, members=[25.0] * 51)
    conn.execute(
        "UPDATE ensemble_snapshots SET local_day_start_utc=?, forecast_window_start_utc=?,"
        " forecast_window_end_utc=? WHERE snapshot_id=101",
        (_DAY_START, window_start, _DAY_END),
    )
    conn.execute(
        "UPDATE ensemble_snapshots SET issue_time=?, source_cycle_time=? WHERE snapshot_id=101",
        ("2026-06-07T00:00:00+00:00", "2026-06-07T00:00:00+00:00"),
    )
    conn.execute("UPDATE source_run SET source_cycle_time=? WHERE source_run_id='ens-run'",
                 ("2026-06-07T00:00:00+00:00",))
    # The run's coverage stays live through the post-day decisions under test.
    conn.execute("UPDATE source_run_coverage SET expires_at='2026-06-08T00:00:00+00:00'")
    return conn, request


def _mark(conn, *, decision: str, tau: str | None):
    from src.data.replacement_input_hwm import _latest_eligible_ensemble_input_mark

    return _latest_eligible_ensemble_input_mark(
        conn, city="Shanghai", target_date="2026-06-07", metric="high",
        decision_time=datetime.fromisoformat(decision), day0_remaining_from_iso=tau,
    )


def test_hwm_without_tau_never_admits_a_remaining_window_row() -> None:
    conn, _request = _remaining_db()
    assert _mark(conn, decision="2026-06-07T16:30:00+00:00", tau=None) is None


def test_hwm_with_in_day_tau_after_day_end_admits_the_remaining_row() -> None:
    conn, _request = _remaining_db()
    assert _mark(conn, decision="2026-06-07T16:30:00+00:00", tau="2026-06-07T15:05:00+00:00") == (
        101, datetime(2026, 6, 7, 0, tzinfo=UTC))


@pytest.mark.parametrize(("decision", "tau"), [
    ("2026-06-07T15:30:00+00:00", "2026-06-07T15:05:00+00:00"),   # day still open
    ("2026-06-07T16:30:00+00:00", "2026-06-07T16:00:00+00:00"),   # tau at day end
    ("2026-06-07T16:30:00+00:00", "2026-06-06T23:00:00+00:00"),   # window starts after tau
])
def test_hwm_falls_through_to_the_old_rule(decision: str, tau: str) -> None:
    conn, _request = _remaining_db()
    assert _mark(conn, decision=decision, tau=tau) is None


def test_hwm_tau_none_sql_is_byte_identical(monkeypatch) -> None:
    """The no-tau statement and binds are exactly the pre-change ones."""
    from src.data import replacement_input_hwm as hwm

    conn, _request = _remaining_db()
    seen: list[tuple[str, tuple]] = []

    class Spy:
        def __init__(self, inner):
            self.inner = inner

        def execute(self, sql, params=()):
            seen.append((sql, tuple(params)))
            return self.inner.execute(sql, params)

        def __getattr__(self, name):
            return getattr(self.inner, name)

    hwm._latest_eligible_ensemble_input_mark(
        Spy(conn), city="Shanghai", target_date="2026-06-07", metric="high",
        decision_time=datetime(2026, 6, 7, 16, 30, tzinfo=UTC))
    final_sql, final_params = seen[-1]
    assert f"{authority.current_evidence_ensemble_eligibility_sql()} AND" in final_sql
    assert authority.remaining_window_after_day_end_sql() not in final_sql
    assert "2026-06-07T15:05:00+00:00" not in final_params


def test_materializer_selector_serves_the_remaining_row_with_request_tau() -> None:
    from dataclasses import replace as dc_replace

    conn, request = _remaining_db()
    request = dc_replace(
        request, source_cycle_time=datetime(2026, 6, 7, 0, tzinfo=UTC),
        computed_at=datetime(2026, 6, 7, 16, 30, tzinfo=UTC),
        day0_observed_extreme_observation_time="2026-06-07T15:05:00+00:00",
    )
    row = materializer._current_evidence_snapshot_row(conn, request, metric="high", select_sql="snapshot_id")
    assert row is not None and row[0] == 101
    blind = dc_replace(request, day0_observed_extreme_observation_time=None)
    assert materializer._current_evidence_snapshot_row(conn, blind, metric="high", select_sql="snapshot_id") is None


def test_cycle_advance_family_carrier_moves_to_the_remaining_row_only_with_tau() -> None:
    from src.data import replacement_cycle_advance_trigger as trigger

    conn, _request = _remaining_db()
    decision = datetime(2026, 6, 7, 16, 30, tzinfo=UTC)

    def latest(*_a, **_k):
        return SimpleNamespace(source_cycle_time=datetime(2026, 6, 7, 0, tzinfo=UTC))

    def expected(_metric):
        return {"openmeteo_ifs9_anchor": SimpleNamespace(source_id="om", data_version="v")}

    kwargs = dict(city="Shanghai", target_date="2026-06-07", metric="high", decision_time=decision,
                  expected_identity=expected, latest_manifest=latest)
    assert trigger.family_materializable_cycle(conn, (object(),), **kwargs) == (None, ())
    assert trigger.family_materializable_cycle(
        conn, (object(),), **kwargs, day0_remaining_from_iso="2026-06-07T15:05:00+00:00",
    ) == (datetime(2026, 6, 7, 0, tzinfo=UTC), ())
    assert trigger._day0_observation_reseed_cycle(
        conn, city="Shanghai", target_date="2026-06-07", metric="high",
        consumed_cycle=datetime(2026, 6, 6, 12, tzinfo=UTC),
        family_cycle=datetime(2026, 6, 7, 0, tzinfo=UTC), decision_time=decision,
        day0_remaining_from_iso="2026-06-07T15:05:00+00:00",
    ) == datetime(2026, 6, 7, 0, tzinfo=UTC)


def test_every_hwm_caller_threads_the_same_tau_parser() -> None:
    import inspect
    from src.data import replacement_cycle_advance_trigger as trigger
    from src.data import replacement_forecast_bundle_reader as reader
    from src.data import replacement_forecast_live_materialization_queue as queue
    from src.data import replacement_forecast_seed_discovery as discovery
    from src.data import replacement_fusion_upgrade_trigger as upgrade

    trig = inspect.getsource(trigger)
    assert trig.count("day0_remaining_from_iso=day0_remaining_from_iso_of(") == 6
    assert trig.count("day0_remaining_from_iso=day0_remaining_from_iso,") == 3
    q = inspect.getsource(queue)
    assert "day0_remaining_from_iso=day0_tau,\n            )" in q
    assert 'seed.get("day0_observed_extreme_observation_time")' in q
    assert 'day0_seed_payload.get("day0_observed_extreme_observation_time")' in inspect.getsource(discovery)
    assert "day0_remaining_from_iso_of(current_state.get(\"observed_at_utc\"))" in inspect.getsource(upgrade)
    assert "day0_remaining_from_iso=_frozen_day0_remaining_from(provenance)" in inspect.getsource(reader)


def test_held_continuity_tau_is_the_rows_frozen_tau_only() -> None:
    from src.data.replacement_forecast_bundle_reader import _frozen_day0_remaining_from

    def prov(*taus):
        return {"bayes_precision_fusion": {"current_value_serving": {
            f"m{i}": {"physical_response": {"frozen_product_identity": (
                {} if tau is None else {"day0_remaining_from": tau})}} for i, tau in enumerate(taus)}}}

    tau = "2026-06-07T15:05:00+00:00"
    assert _frozen_day0_remaining_from(prov(tau)) == tau
    assert _frozen_day0_remaining_from(prov(tau, "2026-06-07T23:05:00+08:00")) == tau
    assert _frozen_day0_remaining_from(prov(tau, None)) is None
    assert _frozen_day0_remaining_from(prov(tau, "2026-06-07T14:00:00+00:00")) is None
    assert _frozen_day0_remaining_from(prov(None)) is None
    assert _frozen_day0_remaining_from({}) is None
    no_frozen = {"bayes_precision_fusion": {"current_value_serving": {"icon": {"physical_response": {}}}}}
    assert _frozen_day0_remaining_from(no_frozen) is None

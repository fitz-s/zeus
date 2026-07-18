# Created: 2026-07-18
# Last reused/audited: 2026-07-18
# Authority basis: docs/evidence/upstream_physical_2026_07_17/day0_mechanism_first_principles_audit.md §7
#   (post-peak served P(new extreme beyond obs) 0.314 vs realized 0.070, 4.50x; LOW eve 0.409 vs 0.000)
#   + day0_percity_diurnal_timing.md; docs/operations/current/plans/upstream_data_physical_2026-07-17.md
#   "T0-1 FIX DESIGN" (remaining-window Day0 CENTER correction, fail-open, one probability world).
"""T0-1 slice 1: remaining-window Day0 center correction.

Unit: _day0_remaining_center_delta_c reads the anchor family's (ecmwf_ifs) hourly
vector and licenses delta = elapsed share of the day's forecast extreme; every
absence/error fails OPEN to (0.0, None, None) so serving stays byte-identical.
Behavior: the corrected mu moves beyond-obs mass into the straddle bin in BOTH the
point q and the bootstrap bounds; non-Day0 materialization is byte-identical with a
delta-bearing vector present (the machinery must not even run).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

import src.data.replacement_forecast_materializer as materializer_mod
from src.data.day0_hourly_vectors import _TABLE_DDL
from src.data.replacement_forecast_materializer import (
    _build_fused_q_bounds,
    _build_scaled_normal_uniform_q,
    _day0_remaining_center_delta_c,
)
from tests.test_replacement_forecast_materializer import (
    _TemperatureBin,
    _conn as _materializer_conn,
    _dt,
    _install_live_fusion,
    _request as _materializer_request,
)

UTC = timezone.utc

# Real Helsinki-shaped diurnal profile (peak 25.5 at local h=16, trough 17.0 at h=4-5).
_TEMPS = [
    18.4, 18.6, 18.2, 17.2, 17.0, 17.0, 18.0, 19.4, 20.5, 21.0, 22.0, 22.6,
    23.6, 23.6, 24.1, 24.6, 25.5, 22.6, 21.7, 21.4, 19.2, 18.8, 18.5, 18.0,
]
_TARGET = "2026-07-18"
_CAPTURED = "2026-07-18T05:00:00+00:00"


def _vector_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(_TABLE_DDL)
    return conn


def _insert_vector(
    conn: sqlite3.Connection,
    *,
    vector_id: str = "v-test",
    temps: list = _TEMPS,
    captured_at: str = _CAPTURED,
    city: str = "Helsinki",
    target_date: str = _TARGET,
    timezone_name: str = "Europe/Helsinki",
    model: str = "ecmwf_ifs",
) -> None:
    times = [f"{target_date}T{h:02d}:00" for h in range(len(temps))]
    conn.execute(
        "INSERT INTO day0_hourly_vectors (vector_id, model, city, target_date, "
        "timezone_name, captured_at, endpoint, request_hash, times_json, temps_c_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            vector_id, model, city, target_date, timezone_name, captured_at,
            "test-endpoint", "test-hash", json.dumps(times), json.dumps(temps),
        ),
    )


def _stub_request(city: str = "Helsinki", target_date: str = _TARGET):
    # The helper reads only city + target_date from the request.
    return SimpleNamespace(city=city, target_date=target_date)


def _utc(hour: int, minute: int = 0, day: int = 18) -> datetime:
    return datetime(2026, 7, day, hour, minute, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Unit: _day0_remaining_center_delta_c
# ---------------------------------------------------------------------------

def test_high_post_peak_delta_is_whole_max_minus_remaining_max() -> None:
    conn = _vector_conn()
    _insert_vector(conn)
    # local 18:00 (Helsinki UTC+3) = 15:00Z: remaining h18..h23, max 21.7; whole max 25.5.
    delta, vector_id, hours = _day0_remaining_center_delta_c(
        conn, _stub_request(), metric="high", computed_at_utc=_utc(15)
    )
    assert delta == pytest.approx(25.5 - 21.7)
    assert vector_id == "v-test"
    assert hours == pytest.approx(6.0)


def test_high_pre_peak_delta_is_zero() -> None:
    conn = _vector_conn()
    _insert_vector(conn)
    # local 10:00 = 07:00Z: remaining window still contains the peak -> delta 0.
    delta, vector_id, hours = _day0_remaining_center_delta_c(
        conn, _stub_request(), metric="high", computed_at_utc=_utc(7)
    )
    assert delta == 0.0
    assert vector_id == "v-test"
    assert hours == pytest.approx(14.0)


def test_low_post_trough_delta_is_remaining_min_minus_whole_min() -> None:
    conn = _vector_conn()
    _insert_vector(conn)
    # local 10:00 = 07:00Z: trough (17.0, h4-5) elapsed; remaining min 18.0 (h23).
    delta, vector_id, _hours = _day0_remaining_center_delta_c(
        conn, _stub_request(), metric="low", computed_at_utc=_utc(7)
    )
    assert delta == pytest.approx(18.0 - 17.0)
    assert vector_id == "v-test"


def test_low_pre_trough_delta_is_zero() -> None:
    conn = _vector_conn()
    _insert_vector(conn)
    # local 02:00 = 2026-07-17T23:00Z: trough still ahead -> delta 0.
    delta, _vector_id, _hours = _day0_remaining_center_delta_c(
        conn, _stub_request(), metric="low", computed_at_utc=_utc(23, day=17)
    )
    assert delta == 0.0


def test_no_remaining_hours_uses_last_entry_maximal_shrink() -> None:
    conn = _vector_conn()
    _insert_vector(conn)
    # local next-day 00:30 = 21:30Z: day over; remaining = LAST entry (18.0 at h23).
    delta, vector_id, hours = _day0_remaining_center_delta_c(
        conn, _stub_request(), metric="high", computed_at_utc=_utc(21, 30)
    )
    assert delta == pytest.approx(25.5 - 18.0)
    assert vector_id == "v-test"
    assert hours == 0.0


def test_no_vector_fails_open() -> None:
    conn = _vector_conn()
    assert _day0_remaining_center_delta_c(
        conn, _stub_request(), metric="high", computed_at_utc=_utc(15)
    ) == (0.0, None, None)


def test_wrong_model_fails_open() -> None:
    conn = _vector_conn()
    _insert_vector(conn, model="icon_global")
    assert _day0_remaining_center_delta_c(
        conn, _stub_request(), metric="high", computed_at_utc=_utc(15)
    ) == (0.0, None, None)


def test_future_capture_is_not_causal() -> None:
    conn = _vector_conn()
    _insert_vector(conn, captured_at="2026-07-18T16:00:00+00:00")
    assert _day0_remaining_center_delta_c(
        conn, _stub_request(), metric="high", computed_at_utc=_utc(15)
    ) == (0.0, None, None)


def test_latest_causal_capture_wins() -> None:
    conn = _vector_conn()
    stale = list(_TEMPS)
    stale[16] = 30.0  # older capture with a different peak
    _insert_vector(conn, vector_id="v-old", temps=stale, captured_at="2026-07-18T04:00:00+00:00")
    _insert_vector(conn, vector_id="v-new")
    delta, vector_id, _hours = _day0_remaining_center_delta_c(
        conn, _stub_request(), metric="high", computed_at_utc=_utc(15)
    )
    assert vector_id == "v-new"
    assert delta == pytest.approx(25.5 - 21.7)


def test_city_match_is_case_insensitive() -> None:
    conn = _vector_conn()
    _insert_vector(conn, city="HELSINKI")
    delta, vector_id, _hours = _day0_remaining_center_delta_c(
        conn, _stub_request(city="helsinki"), metric="high", computed_at_utc=_utc(15)
    )
    assert vector_id == "v-test"
    assert delta > 0.0


def test_all_remaining_null_fails_open() -> None:
    conn = _vector_conn()
    temps = list(_TEMPS)
    for h in range(18, 24):
        temps[h] = None
    _insert_vector(conn, temps=temps)
    assert _day0_remaining_center_delta_c(
        conn, _stub_request(), metric="high", computed_at_utc=_utc(15)
    ) == (0.0, None, None)


def test_fewer_than_two_entries_fails_open() -> None:
    conn = _vector_conn()
    temps: list = [None] * 24
    temps[12] = 24.0
    _insert_vector(conn, temps=temps)
    assert _day0_remaining_center_delta_c(
        conn, _stub_request(), metric="high", computed_at_utc=_utc(15)
    ) == (0.0, None, None)


def test_unparseable_times_fail_open() -> None:
    conn = _vector_conn()
    conn.execute(
        "INSERT INTO day0_hourly_vectors (vector_id, model, city, target_date, "
        "timezone_name, captured_at, endpoint, request_hash, times_json, temps_c_json) "
        "VALUES ('v-bad', 'ecmwf_ifs', 'Helsinki', ?, 'Europe/Helsinki', ?, 'e', 'h', ?, ?)",
        (_TARGET, _CAPTURED, json.dumps(["garbage"] * 3), json.dumps([20.0, 21.0, 22.0])),
    )
    assert _day0_remaining_center_delta_c(
        conn, _stub_request(), metric="high", computed_at_utc=_utc(15)
    ) == (0.0, None, None)


def test_bad_timezone_fails_open() -> None:
    conn = _vector_conn()
    _insert_vector(conn, timezone_name="No/Such_Zone")
    assert _day0_remaining_center_delta_c(
        conn, _stub_request(), metric="high", computed_at_utc=_utc(15)
    ) == (0.0, None, None)


# ---------------------------------------------------------------------------
# Behavior: the corrected mu moves beyond-obs mass into the straddle bin.
# ---------------------------------------------------------------------------

def _bins() -> tuple[_TemperatureBin, ...]:
    return (
        _TemperatureBin("cool", upper_c=20.0, center_c=19.0),
        _TemperatureBin("warm", lower_c=21.0, upper_c=30.0),
        _TemperatureBin("hot", lower_c=31.0, center_c=32.0),
    )


def _day0_q(mu: float) -> dict[str, float]:
    q, _capped, _uniform = _build_scaled_normal_uniform_q(
        mu=mu, sigma_pred=2.0, k=1.0, uniform_w=0.0, floor_steps=0.0,
        bins=_bins(), half_step=0.5, rounding_rule="wmo_half_up",
        day0_obs_extreme_c=26.0, settlement_step_c=1.0,
        settlement_sigma_floor_c=None, city_unit="C", metric="high",
    )
    return q


def test_corrected_mu_shifts_point_mass_from_beyond_obs_to_straddle() -> None:
    q_raw = _day0_q(25.0)
    q_corrected = _day0_q(23.0)  # delta = 2.0 applied
    assert q_corrected["hot"] < q_raw["hot"]
    assert q_corrected["warm"] > q_raw["warm"]
    assert q_corrected["cool"] == pytest.approx(0.0)  # obs already absorbed below-obs bins


def test_corrected_mu_shifts_bound_mass_from_beyond_obs_to_straddle() -> None:
    q_raw = _day0_q(25.0)
    q_corrected = _day0_q(23.0)
    lcb_raw, ucb_raw = _build_fused_q_bounds(
        mu_star=25.0, center_sigma_c=0.35, predictive_sigma_c=2.0, bins=_bins(),
        half_step=0.5, q_point=q_raw, day0_observed_extreme_c=26.0, day0_metric="high",
    )
    lcb_c, ucb_c = _build_fused_q_bounds(
        mu_star=23.0, center_sigma_c=0.35, predictive_sigma_c=2.0, bins=_bins(),
        half_step=0.5, q_point=q_corrected, day0_observed_extreme_c=26.0, day0_metric="high",
    )
    assert ucb_c["hot"] < ucb_raw["hot"]
    assert lcb_c["warm"] >= lcb_raw["warm"]
    for bin_id, q_pt in q_corrected.items():
        assert lcb_c[bin_id] <= q_pt + 1e-12 <= ucb_c[bin_id] + 1e-12


# ---------------------------------------------------------------------------
# Wiring: the served Day0 q consumes the delta; non-Day0 is byte-identical.
# ---------------------------------------------------------------------------

def _day0_request():
    return _materializer_request(
        computed_at=_dt(18),
        expires_at=datetime(2026, 6, 7, 2, tzinfo=UTC),
        day0_observed_extreme_c=26.0,
        day0_observed_extreme_source="wu_api",
        day0_observed_extreme_observation_time=_dt(17, 55).isoformat(),
        day0_observed_extreme_sample_count=12,
    )


def _served_row(conn, posterior_id: int):
    return conn.execute(
        "SELECT q_json, q_lcb_json, q_ucb_json, posterior_identity_hash, provenance_json "
        "FROM forecast_posteriors WHERE posterior_id = ?",
        (posterior_id,),
    ).fetchone()


def test_wired_delta_drops_beyond_obs_mass_and_stamps_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_live_fusion(monkeypatch)

    # delta = 0 baseline (no vector table in the canonical test conn -> fail-open path).
    conn_zero = _materializer_conn()
    result_zero = materializer_mod.materialize_replacement_forecast_live(conn_zero, _day0_request())
    assert result_zero.ok is True
    row_zero = _served_row(conn_zero, result_zero.posterior_id)
    q_zero = json.loads(row_zero["q_json"])
    prov_zero = json.loads(row_zero["provenance_json"])
    assert "day0_remaining_center_delta_c" not in prov_zero  # delta 0 => absent = inert

    # delta = 2.0 fired.
    monkeypatch.setattr(
        materializer_mod,
        "_day0_remaining_center_delta_c",
        lambda conn, request, *, metric, computed_at_utc: (2.0, "v1", 3.0),
    )
    conn_delta = _materializer_conn()
    result_delta = materializer_mod.materialize_replacement_forecast_live(conn_delta, _day0_request())
    assert result_delta.ok is True
    row_delta = _served_row(conn_delta, result_delta.posterior_id)
    q_delta = json.loads(row_delta["q_json"])
    q_lcb = json.loads(row_delta["q_lcb_json"])
    q_ucb = json.loads(row_delta["q_ucb_json"])
    prov_delta = json.loads(row_delta["provenance_json"])

    # Served beyond-obs mass drops; straddle gains (post-peak collapse toward obs).
    assert q_delta["hot"] < q_zero["hot"]
    assert q_delta["warm"] > q_zero["warm"]
    assert q_delta["cool"] == pytest.approx(0.0)
    # One probability world: bounds bracket the served corrected point per bin.
    for bin_id, q_pt in q_delta.items():
        assert q_lcb[bin_id] <= q_pt + 1e-12
        assert q_ucb[bin_id] + 1e-12 >= q_pt
    # Provenance stamps the fired delta beside day0_conditioning.
    assert prov_delta["day0_remaining_center_delta_c"] == pytest.approx(2.0)
    assert prov_delta["day0_remaining_vector_id"] == "v1"
    assert prov_delta["day0_remaining_hours"] == pytest.approx(3.0)
    assert prov_delta["day0_conditioning"]["observed_extreme_c"] == 26.0


def test_non_day0_byte_identical_with_delta_bearing_vector_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_live_fusion(monkeypatch)
    request = _materializer_request()  # computed_at _dt(4) precedes the target local day

    # Real helper + a delta-bearing causal vector persisted on the SAME conn.
    conn_vec = _materializer_conn()
    conn_vec.execute(_TABLE_DDL)
    _insert_vector(
        conn_vec, city="Shanghai", target_date="2026-06-07",
        timezone_name="Asia/Shanghai", captured_at="2026-06-06T03:00:00+00:00",
        temps=[f + 20.0 for f in range(-2, 8)] + [24.0] * 14,
    )
    result_vec = materializer_mod.materialize_replacement_forecast_live(conn_vec, request)
    assert result_vec.ok is True
    row_vec = _served_row(conn_vec, result_vec.posterior_id)

    # No vector machinery at all.
    conn_plain = _materializer_conn()
    result_plain = materializer_mod.materialize_replacement_forecast_live(conn_plain, request)
    assert result_plain.ok is True
    row_plain = _served_row(conn_plain, result_plain.posterior_id)

    # The delta machinery must not even run on non-Day0.
    def _must_not_run(conn, request, *, metric, computed_at_utc):
        raise AssertionError("delta helper invoked on a non-Day0 materialization")

    monkeypatch.setattr(materializer_mod, "_day0_remaining_center_delta_c", _must_not_run)
    conn_guard = _materializer_conn()
    result_guard = materializer_mod.materialize_replacement_forecast_live(conn_guard, request)
    assert result_guard.ok is True
    row_guard = _served_row(conn_guard, result_guard.posterior_id)

    for column in ("q_json", "q_lcb_json", "q_ucb_json", "posterior_identity_hash"):
        assert row_vec[column] == row_plain[column]
        assert row_guard[column] == row_plain[column]
    assert "day0_remaining_center_delta_c" not in json.loads(row_plain["provenance_json"])

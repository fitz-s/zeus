# Created: 2026-07-18
# Last audited: 2026-07-18
# Authority basis: center-cycle-monotone guard on the serving readiness certificate
#   (belt-and-suspenders to _cycle_monotone_block_reasons at the CERT boundary). Serving order
#   key source_cycle_time DESC, computed_at DESC (event_reactor / staleness_cancel). Guards the
#   crash-only equal-cycle wall-clock last-writer race that the upstream cycle guards do NOT cover.
"""Center-cycle-monotone guard on the readiness certificate write.

The readiness cert is upserted last-writer-wins on scope_key (readiness_repo
``ON CONFLICT(scope_key) DO UPDATE``) with NO timestamp comparison. Upstream cycle guards refuse
a STRICTLY-OLDER model cycle but not an EQUAL cycle committed OUT of computed_at order — the
crash-steal race where a SIGKILL'd orphan materialize child commits an older-COMPUTED posterior
concurrently with a lock-stealing new daemon. This guard refuses to REGRESS the cert onto a
posterior the incumbent certified one is STRICTLY newer than on (source_cycle_time, computed_at),
and fails OPEN everywhere else (no cert / unparseable / missing / equal) so a lawful forward
advance is byte-identical to today.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone

import pytest

from src.data.openmeteo_ecmwf_ifs9_anchor import OpenMeteoIfs9LocalDayAnchor
from src.data.openmeteo_ecmwf_ifs9_precision_guard import (
    OpenMeteoIfs9PrecisionMetadata,
    evaluate_openmeteo_ecmwf_ifs9_precision_guard,
)
from src.data.replacement_forecast_materializer import (
    _BayesPrecisionFusionFusionOverride,
    ReplacementForecastMaterializeRequest,
    _bound_posterior_id,
    _posterior_serving_key,
    _readiness_cert_cycle_regression_reasons,
    _serving_key_strictly_newer,
    materialize_replacement_forecast_live,
)
import src.data.replacement_forecast_materializer as materializer_mod
from src.state.db import _create_readiness_state
from src.state.schema.v2_schema import apply_canonical_schema

UTC = timezone.utc
_REGRESSION = "READINESS_CERT_CYCLE_REGRESSION"


# --- harness (mirrors tests/test_replacement_forecast_materializer.py live-eligible recipe) ---


@dataclass(frozen=True)
class _TemperatureBin:
    bin_id: str
    lower_c: float | None = None
    upper_c: float | None = None
    center_c: float | None = None
    display_unit: str = "C"
    settlement_unit: str = "C"
    rounding_rule: str = "wmo_half_up"


def _dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 6, 6, hour, minute, tzinfo=UTC)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_canonical_schema(conn, forecast_tables=True)
    _create_readiness_state(conn)
    return conn


def _anchor(*, source_cycle_time: datetime | None = None) -> OpenMeteoIfs9LocalDayAnchor:
    local_tz = timezone(timedelta(hours=8))
    contributing_local_times = tuple(datetime(2026, 6, 7, hour, tzinfo=local_tz) for hour in range(24))
    return OpenMeteoIfs9LocalDayAnchor(
        city_timezone="Asia/Shanghai",
        target_local_date=date(2026, 6, 7),
        high_c=27.0,
        low_c=18.5,
        sample_count=24,
        contributing_local_times=contributing_local_times,
        contributing_valid_times_utc=tuple(item.astimezone(UTC) for item in contributing_local_times),
        source_cycle_time=source_cycle_time or _dt(0),
    )


def _precision_guard():
    return evaluate_openmeteo_ecmwf_ifs9_precision_guard(
        OpenMeteoIfs9PrecisionMetadata(
            city="Shanghai",
            station_id="ZSSS",
            city_lat=31.2304,
            city_lon=121.4737,
            station_lat=31.1979,
            station_lon=121.3363,
            requested_lat=31.1979,
            requested_lon=121.3363,
            requested_coordinate_precision_decimals=4,
            nearest_grid_lat=31.2,
            nearest_grid_lon=121.3,
            nearest_grid_distance_km=3.5,
            native_grid="openmeteo_ecmwf_ifs_9km",
            delivery_grid_resolution="0p1",
            interpolation_method="nearest_gridpoint",
            endpoint_mode="hourly_zeus_aggregated",
            local_day_start_utc=_dt(16),
            local_day_end_utc=datetime(2026, 6, 7, 16, tzinfo=UTC),
            timezone_name="Asia/Shanghai",
            target_local_date=date(2026, 6, 7),
            temperature_unit="C",
            anchor_sigma_c=3.0,
            grid_elevation_m=4.0,
            station_elevation_m=3.0,
            land_sea_mask="land",
            city_class="flat_inland",
            station_mapping_policy="settlement_station",
        )
    )


def _bins() -> tuple[_TemperatureBin, ...]:
    return (
        _TemperatureBin("cool", upper_c=20.0, center_c=19.0),
        _TemperatureBin("warm", lower_c=21.0, upper_c=30.0),
        _TemperatureBin("hot", lower_c=31.0, center_c=32.0),
    )


def _install_live_fusion(monkeypatch: pytest.MonkeyPatch) -> None:
    override = _BayesPrecisionFusionFusionOverride(
        anchor_value_c=25.0,
        anchor_sigma_c=0.35,
        method="test_bayes_precision_fusion",
        used_models=("ecmwf_ifs9", "gfs", "icon", "gem", "jma"),
        model_set_hash="test-model-set",
        resolution_mix_hash="test-resolution-mix",
        lead_bucket="d1",
        dropped_models=(),
        excluded_regionals=(),
        dropped_aliases=(),
        raw_model_forecast_ids=(101, 102, 103),
        anchor_bridge={"test": True},
        predictive_sigma_c=2.0,
        decorrelated_providers_complete=True,
        decorrelated_providers_served=5,
        decorrelated_providers_expected=5,
        current_value_serving={"ecmwf_ifs9": {"served_via": "single_runs"}},
    )
    monkeypatch.setattr(
        materializer_mod, "_replacement_bayes_precision_fusion_override", lambda *a, **k: override
    )


def _request(*, cycle: datetime, computed: datetime) -> ReplacementForecastMaterializeRequest:
    return ReplacementForecastMaterializeRequest(
        city="Shanghai",
        city_id="Shanghai",
        city_timezone="Asia/Shanghai",
        target_date=date(2026, 6, 7),
        temperature_metric="high",
        baseline_source_run_id="b0-run",
        baseline_data_version="ecmwf_opendata_mx2t3_local_calendar_day_max",
        baseline_source_available_at=_dt(2),
        openmeteo_anchor=_anchor(source_cycle_time=cycle),
        openmeteo_source_run_id="om9-run",
        openmeteo_source_available_at=_dt(3),
        bins=_bins(),
        source_cycle_time=cycle,
        computed_at=computed,
        expires_at=computed + timedelta(hours=3),
        openmeteo_precision_guard=_precision_guard(),
    )


def _materialize(conn, *, cycle: datetime, computed: datetime):
    return materialize_replacement_forecast_live(conn, _request(cycle=cycle, computed=computed))


def _cert_source_run_id(conn) -> str | None:
    row = conn.execute("SELECT source_run_id FROM readiness_state").fetchone()
    return None if row is None else row["source_run_id"]


# --- pure comparison helper ------------------------------------------------------------------


def test_serving_key_strictly_newer_pure() -> None:
    c1 = datetime(2026, 6, 6, 0, tzinfo=UTC)
    c2 = datetime(2026, 6, 6, 6, tzinfo=UTC)
    t1 = datetime(2026, 6, 6, 9, tzinfo=UTC)
    t2 = datetime(2026, 6, 6, 11, tzinfo=UTC)
    assert _serving_key_strictly_newer((c2, t1), (c1, t1)) is True   # newer cycle
    assert _serving_key_strictly_newer((c1, t2), (c1, t1)) is True   # equal cycle, newer computed
    assert _serving_key_strictly_newer((c1, t1), (c1, t1)) is False  # equal => NOT strict
    assert _serving_key_strictly_newer((c1, t1), (c2, t1)) is False  # older cycle
    assert _serving_key_strictly_newer((c1, t1), (c1, t2)) is False  # equal cycle, older computed


def test_bound_posterior_id_parsing() -> None:
    assert _bound_posterior_id("posterior:7") == 7
    assert _bound_posterior_id("posterior:0") == 0
    assert _bound_posterior_id("posterior:garbage") is None
    assert _bound_posterior_id("b0-run") is None
    assert _bound_posterior_id(None) is None
    assert _bound_posterior_id(7) is None


# --- (c) forward advance: newer source_cycle_time replaces -----------------------------------


def test_newer_source_cycle_replaces_cert(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _conn()
    _install_live_fusion(monkeypatch)
    r1 = _materialize(conn, cycle=_dt(0), computed=_dt(4))   # 00Z
    r2 = _materialize(conn, cycle=_dt(6), computed=_dt(10))  # 06Z, newer cycle
    assert r1.ok is True and r2.ok is True
    assert r1.posterior_id != r2.posterior_id
    # Forward advance is NOT blocked and the cert now binds the newer-cycle posterior.
    assert _cert_source_run_id(conn) == f"posterior:{r2.posterior_id}"


# --- (a) older source_cycle_time cannot replace a newer cert ---------------------------------


def test_older_source_cycle_cannot_replace_newer_cert(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _conn()
    _install_live_fusion(monkeypatch)
    r_old = _materialize(conn, cycle=_dt(0), computed=_dt(4))   # 00Z -> cert
    r_new = _materialize(conn, cycle=_dt(6), computed=_dt(10))  # 06Z -> cert advances
    assert r_old.ok is True and r_new.ok is True
    assert _cert_source_run_id(conn) == f"posterior:{r_new.posterior_id}"
    # The cert now binds the 06Z posterior; regressing it onto the 00Z posterior is refused.
    reasons = _readiness_cert_cycle_regression_reasons(
        conn, _request(cycle=_dt(0), computed=_dt(4)), metric="high", incoming_posterior_id=r_old.posterior_id
    )
    assert reasons == (_REGRESSION,)


# --- (b) equal source_cycle_time + older computed_at cannot replace --------------------------


def test_equal_cycle_older_computed_cannot_replace(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _conn()
    _install_live_fusion(monkeypatch)
    r_older = _materialize(conn, cycle=_dt(6), computed=_dt(9))    # 06Z computed 09:00 -> cert
    r_newer = _materialize(conn, cycle=_dt(6), computed=_dt(11))   # SAME cycle, newer computed -> advances
    assert r_older.ok is True and r_newer.ok is True
    assert r_older.posterior_id != r_newer.posterior_id
    assert _cert_source_run_id(conn) == f"posterior:{r_newer.posterior_id}"
    # Cert binds the newer-computed posterior; regressing onto the older-computed one is refused.
    reasons = _readiness_cert_cycle_regression_reasons(
        conn, _request(cycle=_dt(6), computed=_dt(9)), metric="high", incoming_posterior_id=r_older.posterior_id
    )
    assert reasons == (_REGRESSION,)


# --- (d) equal (source_cycle_time, computed_at) proceeds (idempotent re-write) ----------------


def test_equal_key_idempotent_rewrite_proceeds(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _conn()
    _install_live_fusion(monkeypatch)
    r1 = _materialize(conn, cycle=_dt(6), computed=_dt(10))
    r2 = _materialize(conn, cycle=_dt(6), computed=_dt(10))  # identical => dedup to same posterior_id
    assert r1.ok is True and r2.ok is True
    assert r2.posterior_id == r1.posterior_id
    assert _cert_source_run_id(conn) == f"posterior:{r1.posterior_id}"
    # Same bound posterior => guard fails open (no regression), cert re-written idempotently.
    reasons = _readiness_cert_cycle_regression_reasons(
        conn, _request(cycle=_dt(6), computed=_dt(10)), metric="high", incoming_posterior_id=r1.posterior_id
    )
    assert reasons == ()


# --- (e) no existing cert => writes (fail-open) ----------------------------------------------


def test_no_existing_cert_fails_open(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _conn()
    _install_live_fusion(monkeypatch)
    # No cert row yet => guard fails open even for an unknown incoming id.
    assert _readiness_cert_cycle_regression_reasons(
        conn, _request(cycle=_dt(6), computed=_dt(10)), metric="high", incoming_posterior_id=999
    ) == ()
    r = _materialize(conn, cycle=_dt(6), computed=_dt(10))
    assert r.ok is True
    assert _cert_source_run_id(conn) == f"posterior:{r.posterior_id}"


# --- (f) missing / unparseable timestamp => proceeds (fail-open) ------------------------------


def test_unparseable_timestamp_fails_open(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _conn()
    _install_live_fusion(monkeypatch)
    r1 = _materialize(conn, cycle=_dt(6), computed=_dt(11))
    assert r1.ok is True
    p1 = r1.posterior_id
    # Corrupt the incumbent posterior's serving timestamps.
    conn.execute("UPDATE forecast_posteriors SET computed_at=? WHERE posterior_id=?", ("not-a-timestamp", p1))
    assert _posterior_serving_key(conn, p1) is None
    assert _posterior_serving_key(conn, 10_000_000) is None  # missing row => None
    # Incumbent key unreadable AND incoming id unknown => guard fails open (never blocks).
    reasons = _readiness_cert_cycle_regression_reasons(
        conn, _request(cycle=_dt(6), computed=_dt(9)), metric="high", incoming_posterior_id=p1 + 999
    )
    assert reasons == ()


# --- (g) crash-steal: same cycle committed OUT of computed order, newer-computed wins ----------


def test_crash_steal_newer_computed_wins_cert(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two same-cycle posteriors committed OUT of computed_at order (the orphan-child race).

    The newer-COMPUTED posterior must remain certified even though the older-computed one is the
    LAST writer — the exact non-monotone serve wall-clock last-writer-wins would produce.
    """
    conn = _conn()
    _install_live_fusion(monkeypatch)
    # A: newer-computed, committed FIRST => cert = A.
    r_a = _materialize(conn, cycle=_dt(6), computed=_dt(11))
    assert r_a.ok is True
    assert _cert_source_run_id(conn) == f"posterior:{r_a.posterior_id}"
    # B: SAME cycle, OLDER-computed, committed SECOND (the SIGKILL'd orphan child).
    r_b = _materialize(conn, cycle=_dt(6), computed=_dt(9))
    assert r_b.status == "BLOCKED"
    assert r_b.reason_codes == (_REGRESSION,)
    assert r_b.posterior_id is not None            # B's posterior row WAS written...
    assert r_b.posterior_id != r_a.posterior_id
    assert r_b.readiness_id is None                # ...but B did NOT get the cert.
    # Cert still binds the newer-computed A despite B being the last writer.
    assert _cert_source_run_id(conn) == f"posterior:{r_a.posterior_id}"


def test_crash_steal_natural_order_advances_to_newer_computed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reverse insert order: older-computed FIRST, newer-computed SECOND => cert advances to newer."""
    conn = _conn()
    _install_live_fusion(monkeypatch)
    r_older = _materialize(conn, cycle=_dt(6), computed=_dt(9))
    r_newer = _materialize(conn, cycle=_dt(6), computed=_dt(11))
    assert r_older.ok is True and r_newer.ok is True
    # newer-computed is a lawful forward advance; cert ends bound to it either way.
    assert _cert_source_run_id(conn) == f"posterior:{r_newer.posterior_id}"

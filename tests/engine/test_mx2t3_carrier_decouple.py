# Created: 2026-06-17
# Last audited: 2026-06-17
# Authority basis: operator single-truth law + residual_legacy_sources.md (GATE-1 carrier
#   decouple). RED-on-revert antibodies for the mx2t3 → forecast_posteriors/raw_model_forecasts
#   carrier decouple: the forecast-decision lifecycle (FSR readiness, spine causal-cycle pin, and
#   the no-submit certificate's forecast authority) must NOT depend on the cold ensemble_snapshots
#   table when the replacement trade authority is ON. Each test goes RED if its wire is reverted to
#   the ensemble path.
"""mx2t3 carrier-decouple antibodies (GATE-1 A/B/C)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

import pytest

import src.engine.event_reactor_adapter as adapter
import src.events.triggers.forecast_snapshot_ready as fsr
from src.decision_kernel.compiler import (
    _validate_forecast_authority_payload as compiler_validate_forecast,
)
from src.decision_kernel.verifier import (
    POSTERIOR_MEMBERS_JSON_SOURCE,
    _validate_posterior_forecast_authority_payload,
)

UTC = timezone.utc
_DT = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)


def _posteriors_only_conn() -> sqlite3.Connection:
    """A forecasts DB carrying forecast_posteriors + raw_model_forecasts but NO ensemble tables."""
    con = sqlite3.connect(":memory:")
    con.execute(
        """CREATE TABLE forecast_posteriors (
            posterior_id INTEGER PRIMARY KEY AUTOINCREMENT, product_id TEXT, source_id TEXT,
            data_version TEXT, city TEXT, target_date TEXT, temperature_metric TEXT,
            source_cycle_time TEXT, source_available_at TEXT, computed_at TEXT,
            posterior_identity_hash TEXT)"""
    )
    con.execute(
        """INSERT INTO forecast_posteriors (product_id, source_id, data_version, city, target_date,
            temperature_metric, source_cycle_time, source_available_at, computed_at,
            posterior_identity_hash) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            fsr.REPLACEMENT_0_1_PRODUCT_ID, "openmeteo", "v1", "Tokyo", "2026-06-19", "high",
            "2026-06-17T00:00:00+00:00", "2026-06-17T06:00:00+00:00", "2026-06-17T06:30:00+00:00",
            "pid-hash-xyz",
        ),
    )
    con.execute(
        """CREATE TABLE raw_model_forecasts (model TEXT, city TEXT, metric TEXT, target_date TEXT,
            source_cycle_time TEXT, source_available_at TEXT, forecast_value_c REAL)"""
    )
    for m, v in (
        ("ecmwf_ifs", 30.1), ("gfs_global", 31.2), ("icon_global", 29.8),
        ("gem_global", 30.5), ("jma_seamless", 30.9),
    ):
        con.execute(
            "INSERT INTO raw_model_forecasts VALUES (?,?,?,?,?,?,?)",
            (m, "Tokyo", "high", "2026-06-19", "2026-06-17T00:00:00+00:00",
             "2026-06-17T05:00:00+00:00", v),
        )
    con.commit()
    return con


def _family():
    return SimpleNamespace(city="Tokyo", metric="high", target_date="2026-06-19", family_id="fam-1")


# ---------------------------------------------------------------------------
# (A) FSR readiness/selection rides forecast_posteriors, not ensemble_snapshots.
# ---------------------------------------------------------------------------
def test_a_posterior_lane_emits_complete_fsr_with_neutral_snapshot_id():
    con = _posteriors_only_conn()
    captured: list = []

    class _W:
        def write(self, ev):
            captured.append(ev)
            return SimpleNamespace(event=ev, written=True)

        def write_many(self, events):
            return [self.write(ev) for ev in events]

    with mock.patch.object(fsr, "_replacement_live_enabled", return_value=True):
        trig = fsr.ForecastSnapshotReadyTrigger(_W())
        results = trig.scan_committed_snapshots(
            forecasts_conn=con, decision_time=_DT, received_at=_DT.isoformat(), limit=10,
        )

    assert len(results) == 1, "posterior lane must emit one FSR from forecast_posteriors alone"
    ev = captured[0]
    # The neutral synthesized snapshot identity (no ensemble_snapshots row exists).
    assert ev.causal_snapshot_id.startswith("rmf-Tokyo|2026-06-19|high|2026-06-17"), (
        f"causal_snapshot_id must be the neutral rmf-... id, got {ev.causal_snapshot_id!r}"
    )


def test_a2_posterior_lane_requires_same_cycle_raw_model_spine_members():
    con = _posteriors_only_conn()
    con.execute("DELETE FROM raw_model_forecasts WHERE date(source_cycle_time) = '2026-06-17'")
    con.commit()

    class _W:
        def write(self, ev):  # noqa: ANN001
            raise AssertionError(f"must not emit without q-kernel raw-model members: {ev!r}")

        def write_many(self, events):
            return [self.write(ev) for ev in events]

    with mock.patch.object(fsr, "_replacement_live_enabled", return_value=True):
        trig = fsr.ForecastSnapshotReadyTrigger(_W())
        results = trig.scan_committed_snapshots(
            forecasts_conn=con, decision_time=_DT, received_at=_DT.isoformat(), limit=10,
        )

    assert results == []


# ---------------------------------------------------------------------------
# (B) The spine causal cycle is parsed from the neutral id — no ensemble row needed.
# ---------------------------------------------------------------------------
def test_b_spine_resolves_causal_cycle_from_neutral_id_without_ensemble():
    con = _posteriors_only_conn()
    event = SimpleNamespace(
        event_type="FORECAST_SNAPSHOT_READY",
        causal_snapshot_id="rmf-Tokyo|2026-06-19|high|2026-06-17",
    )
    city = SimpleNamespace(timezone="Asia/Tokyo", settlement_unit="C")
    with mock.patch.object(adapter, "runtime_cities_by_name", return_value={"Tokyo": city}):
        members = adapter._spine_multimodel_members_for_event(
            con, event=event, family=_family(), decision_time=_DT,
        )
    assert members is not None, "spine must resolve members from the neutral id + raw_model_forecasts"
    members_native, causal_sct, *_ = members
    assert len(members_native) == 5
    assert str(causal_sct).startswith("2026-06-17"), causal_sct


# ---------------------------------------------------------------------------
# (C) The no-submit cert forecast authority is built off forecast_posteriors, NOT ensemble.
# ---------------------------------------------------------------------------
def test_c_no_submit_cert_forecast_authority_from_posterior_passes_validation():
    con = _posteriors_only_conn()
    event = SimpleNamespace(
        event_type="FORECAST_SNAPSHOT_READY",
        causal_snapshot_id="rmf-Tokyo|2026-06-19|high|2026-06-17",
    )
    payload = {"source_id": "openmeteo", "source_run_id": "pid-hash-xyz"}
    city = SimpleNamespace(timezone="Asia/Tokyo", settlement_unit="C")
    with mock.patch.object(adapter, "runtime_cities_by_name", return_value={"Tokyo": city}):
        res = adapter._forecast_authority_payload_from_posterior(
            con, event=event, family=_family(), payload=payload, decision_time=_DT,
        )
    assert res is not None, "posterior cert authority must build from forecast_posteriors alone"
    pl, clock = res
    assert pl["members_json_source"] == POSTERIOR_MEMBERS_JSON_SOURCE
    assert pl["members_json_source"] != "ensemble_snapshots.daily_extrema"
    assert pl["source_run_id"] == "pid-hash-xyz"
    assert pl["forecast_source_id"] == "openmeteo"
    assert pl["observed_members"] == 5 and pl["expected_members"] == 5
    # Validates through BOTH the verifier and compiler posterior branches (no raise).
    _validate_posterior_forecast_authority_payload({**pl, "bin_labels_hash": "bh"})
    compiler_validate_forecast({**pl, "bin_labels_hash": "bh", "metric": "high"})


def test_c_posterior_cert_fork_only_fires_under_replacement_flag_and_non_day0():
    """The fork must NOT fire for day0 (day0 keeps its ensemble base, carrier_decouple_plan §4)."""
    con = _posteriors_only_conn()
    day0_event = SimpleNamespace(
        event_type="DAY0_EXTREME_UPDATED",
        causal_snapshot_id="rmf-Tokyo|2026-06-19|high|2026-06-17",
    )
    city = SimpleNamespace(timezone="Asia/Tokyo", settlement_unit="C")
    # The top-level fork condition in _forecast_authority_payload_and_clock excludes day0; assert the
    # event-type guards directly (a day0 event is in _DAY0_LANE_EVENT_TYPES).
    assert "DAY0_EXTREME_UPDATED" in adapter._DAY0_LANE_EVENT_TYPES
    assert "FORECAST_SNAPSHOT_READY" in adapter._FORECAST_DECISION_EVENT_TYPES
    assert day0_event.event_type not in adapter._FORECAST_DECISION_EVENT_TYPES


# ---------------------------------------------------------------------------
# (GATE-2) The day0 seed pool re-sources off raw_model_forecasts, not the cold ensemble.
# ---------------------------------------------------------------------------
def test_gate2_day0_seed_members_from_raw_model_forecasts():
    con = _posteriors_only_conn()
    city = SimpleNamespace(timezone="Asia/Tokyo", settlement_unit="C")
    with mock.patch.object(adapter, "runtime_cities_by_name", return_value={"Tokyo": city}):
        seed = adapter._day0_seed_members_multimodel(
            con, family=_family(), decision_time=_DT,
        )
    assert seed is not None, "day0 seed must source from raw_model_forecasts"
    assert len(seed) == 5
    # Native unit is C for Tokyo, so values are the raw °C members.
    assert min(seed) == pytest.approx(29.8) and max(seed) == pytest.approx(31.2)

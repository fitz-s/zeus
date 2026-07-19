# Lifecycle: created=2026-06-17; last_reviewed=2026-07-19; last_reused=2026-07-19
# Purpose: Prove replacement forecast carriers do not fall back to legacy ensemble authority.
# Reuse: Re-audit readiness-to-posterior binding before changing replacement FSR selection.
# Authority basis: operator single-truth law + residual_legacy_sources.md (GATE-1 carrier
#   decouple). RED-on-revert antibodies for the mx2t3 → forecast_posteriors/raw_model_forecasts
#   carrier decouple: the forecast-decision lifecycle (FSR readiness, spine causal-cycle pin, and
#   the no-submit certificate's forecast authority) must NOT depend on the cold ensemble_snapshots
#   table when the replacement trade authority is ON. Each test goes RED if its wire is reverted to
#   the ensemble path.
"""mx2t3 carrier-decouple antibodies (GATE-1 A/B/C)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

import pytest

import src.engine.event_reactor_adapter as adapter
import src.engine.replacement_forecast_hook_factory as hook_factory
import src.events.triggers.forecast_snapshot_ready as fsr
from src.decision_kernel.compiler import (
    _validate_forecast_authority_payload as compiler_validate_forecast,
)
from src.decision_kernel.verifier import (
    POSTERIOR_MEMBERS_JSON_SOURCE,
    _validate_posterior_forecast_authority_payload,
)
from src.decision_kernel.errors import CertificateVerificationError
from src.decision_kernel.canonicalization import stable_hash

UTC = timezone.utc
_DT = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)


def _posteriors_only_conn() -> sqlite3.Connection:
    """A forecasts DB carrying forecast_posteriors + raw_model_forecasts but NO ensemble tables."""
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute(
        """CREATE TABLE forecast_posteriors (
            posterior_id INTEGER PRIMARY KEY AUTOINCREMENT, product_id TEXT, source_id TEXT,
            data_version TEXT, city TEXT, target_date TEXT, temperature_metric TEXT,
            source_cycle_time TEXT, source_available_at TEXT, computed_at TEXT,
            posterior_identity_hash TEXT, family_id TEXT, bin_topology_hash TEXT,
            q_json TEXT, q_lcb_json TEXT, q_ucb_json TEXT, provenance_json TEXT,
            runtime_layer TEXT, training_allowed INTEGER)"""
    )
    topology = [{"bin_id": "30C", "lower_c": 30.0, "upper_c": 30.0}]
    topology_hash = stable_hash(topology)
    con.execute(
        """INSERT INTO forecast_posteriors (product_id, source_id, data_version, city, target_date,
            temperature_metric, source_cycle_time, source_available_at, computed_at,
            posterior_identity_hash, family_id, bin_topology_hash, q_json, q_lcb_json,
            q_ucb_json, provenance_json, runtime_layer, training_allowed)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            fsr.REPLACEMENT_0_1_PRODUCT_ID, fsr.REPLACEMENT_SOURCE_ID,
            fsr.REPLACEMENT_HIGH_DATA_VERSION, "Tokyo", "2026-06-19", "high",
            "2026-06-17T00:00:00+00:00", "2026-06-17T06:00:00+00:00", "2026-06-17T06:30:00+00:00",
            "a" * 64, "fam-1", topology_hash, '{"30C":1.0}', '{"30C":0.8}',
            '{"30C":1.0}',
            json.dumps(
                {
                    "bin_topology": topology,
                    "replacement_q_mode": "FUSED_NORMAL_FULL",
                    "q_lcb_basis": "fused_center_bootstrap_p05",
                    "q_ucb_json_role": "fused_center_bootstrap_ucb",
                    "q_lcb_bootstrap_draws": 200,
                    "q_bootstrap_samples_hash": "b" * 64,
                },
                sort_keys=True,
            ),
            "live",
            0,
        ),
    )
    posterior_id = con.execute("SELECT posterior_id FROM forecast_posteriors").fetchone()[0]
    con.execute(
        """CREATE TABLE readiness_state (
            readiness_id TEXT, scope_type TEXT, strategy_key TEXT, source_id TEXT,
            data_version TEXT, status TEXT,
            city TEXT, target_local_date TEXT, temperature_metric TEXT,
            computed_at TEXT, expires_at TEXT, dependency_json TEXT)"""
    )
    con.execute(
        "INSERT INTO readiness_state VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "replacement-readiness:tokyo-high",
            "strategy",
            fsr.REPLACEMENT_STRATEGY_KEY,
            fsr.REPLACEMENT_SOURCE_ID,
            fsr.REPLACEMENT_HIGH_DATA_VERSION,
            fsr.REPLACEMENT_READY_STATUS,
            "Tokyo",
            "2026-06-19",
            "high",
            "2026-06-17T06:30:00+00:00",
            "2026-06-18T00:00:00+00:00",
            json.dumps(
                {
                    "dependencies": [
                        {
                            "role": "soft_anchor_posterior",
                            "source_id": fsr.REPLACEMENT_SOURCE_ID,
                            "product_id": fsr.REPLACEMENT_0_1_PRODUCT_ID,
                            "data_version": fsr.REPLACEMENT_HIGH_DATA_VERSION,
                            "status": fsr.REPLACEMENT_READY_STATUS,
                            "source_available_at": "2026-06-17T06:30:00+00:00",
                            "posterior_id": posterior_id,
                        }
                    ]
                }
            ),
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


def _indexed_raw_model_conn() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.execute(
        """CREATE TABLE raw_model_forecasts (
            endpoint TEXT, model TEXT, city TEXT, target_date TEXT, metric TEXT,
            source_cycle_time TEXT, source_available_at TEXT, forecast_value_c REAL
        )"""
    )
    con.execute(
        """CREATE INDEX idx_raw_model_forecasts_endpoint_family_cycle_members
           ON raw_model_forecasts(
               endpoint, city, target_date, metric, source_cycle_time,
               source_available_at, model
           )"""
    )
    con.executemany(
        "INSERT INTO raw_model_forecasts VALUES (?,?,?,?,?,?,?,?)",
        (
            ("single_runs", "a", "Tokyo", "2026-06-19", "high", "2026-06-17T00:00:00+00:00", "2026-06-17T01:00:00+00:00", 30.0),
            ("single_runs", "a", "Tokyo", "2026-06-19", "high", "2026-06-17T06:00:00+00:00", "2026-06-17T06:30:00+00:00", 31.0),
            ("single_runs", "b", "Tokyo", "2026-06-19", "high", "2026-06-17T00:00:00+00:00", "2026-06-17T01:00:00+00:00", 29.0),
            ("single_runs", "c", "Tokyo", "2026-06-19", "high", "2026-06-17T00:00:00+00:00", "2026-06-17T01:00:00+00:00", 32.0),
            ("hourly", "wrong-endpoint", "Tokyo", "2026-06-19", "high", "2026-06-18T00:00:00+00:00", "2026-06-17T02:00:00+00:00", 99.0),
            ("single_runs", "null-latest", "Tokyo", "2026-06-19", "high", "2026-06-18T00:00:00+00:00", "2026-06-17T02:00:00+00:00", None),
            ("single_runs", "future", "Tokyo", "2026-06-19", "high", "2026-06-18T00:00:00+00:00", "2026-06-17T13:00:00+00:00", 98.0),
        ),
    )
    return con


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


def test_a2b_posterior_lane_requires_live_runtime_schema():
    con = _posteriors_only_conn()
    con.execute("ALTER TABLE forecast_posteriors DROP COLUMN runtime_layer")

    with mock.patch.object(fsr, "_replacement_live_enabled", return_value=True):
        trig = fsr.ForecastSnapshotReadyTrigger(SimpleNamespace())
        assert trig.build_committed_snapshot_events(
            forecasts_conn=con,
            decision_time=_DT,
            received_at=_DT.isoformat(),
            limit=10,
        ) == []


def test_a3_reactor_readiness_is_exact_scope_metric_and_point_in_time():
    con = _posteriors_only_conn()
    con.execute(
        """INSERT INTO readiness_state VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "replacement-readiness:future",
            "strategy",
            fsr.REPLACEMENT_STRATEGY_KEY,
            fsr.REPLACEMENT_SOURCE_ID,
            fsr.REPLACEMENT_HIGH_DATA_VERSION,
            fsr.REPLACEMENT_READY_STATUS,
            "Tokyo",
            "2026-06-19",
            "high",
            "2026-06-17T08:00:00-05:00",
            "2026-06-18T00:00:00+00:00",
            '{"dependencies":[]}',
        ),
    )
    con.execute(
        """INSERT INTO readiness_state VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "replacement-readiness:wrong-scope",
            "global",
            fsr.REPLACEMENT_STRATEGY_KEY,
            fsr.REPLACEMENT_SOURCE_ID,
            fsr.REPLACEMENT_HIGH_DATA_VERSION,
            fsr.REPLACEMENT_READY_STATUS,
            "Tokyo",
            "2026-06-19",
            "high",
            "2026-06-17T11:00:00+00:00",
            "2026-06-18T00:00:00+00:00",
            '{"dependencies":[]}',
        ),
    )
    readiness = hook_factory._latest_replacement_readiness(
        con,
        city="Tokyo",
        target_date="2026-06-19",
        temperature_metric="high",
        decision_time=_DT,
    )
    assert readiness is not None
    assert readiness.readiness_id == "replacement-readiness:tokyo-high"
    assert hook_factory._latest_replacement_readiness(
        con,
        city="Tokyo",
        target_date="2026-06-19",
        temperature_metric="low",
        decision_time=_DT,
    ) is None

    con.execute(
        "DELETE FROM readiness_state WHERE readiness_id IN (?, ?)",
        ("replacement-readiness:future", "replacement-readiness:wrong-scope"),
    )
    con.execute(
        "UPDATE readiness_state SET dependency_json='{' WHERE readiness_id=?",
        ("replacement-readiness:tokyo-high",),
    )
    assert hook_factory._latest_replacement_readiness(
        con,
        city="Tokyo",
        target_date="2026-06-19",
        temperature_metric="high",
        decision_time=_DT,
    ) is None


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
    payload = {"source_id": "openmeteo", "source_run_id": "a" * 64}
    city = SimpleNamespace(timezone="Asia/Tokyo", settlement_unit="C")
    with mock.patch.object(adapter, "runtime_cities_by_name", return_value={"Tokyo": city}):
        res = adapter._forecast_authority_payload_from_posterior(
            con, event=event, family=_family(), payload=payload, decision_time=_DT,
        )
    assert res is not None, "posterior cert authority must build from forecast_posteriors alone"
    pl, clock = res
    assert pl["members_json_source"] == POSTERIOR_MEMBERS_JSON_SOURCE
    assert pl["members_json_source"] != "ensemble_snapshots.daily_extrema"
    assert pl["source_run_id"] == "a" * 64
    assert pl["forecast_source_id"] == "openmeteo"
    assert pl["observed_members"] == 5 and pl["expected_members"] == 5
    # Validates through BOTH the verifier and compiler posterior branches (no raise).
    _validate_posterior_forecast_authority_payload({**pl, "bin_labels_hash": "bh"})
    compiler_validate_forecast({**pl, "bin_labels_hash": "bh", "metric": "high"})

    source_clock = {
        **pl,
        "expected_members": 2,
        "observed_members": 2,
        "posterior_model_count_basis": "source_clock_configured_sources",
        "posterior_completeness_status": "GRID_CAP10_LIVE_READY",
        "posterior_configured_sources": ("ecmwf_ifs", "ukmo_global_deterministic_10km"),
        "posterior_served_sources": ("ecmwf_ifs", "ukmo_global_deterministic_10km"),
        "posterior_missing_sources": (),
        "posterior_walkforward_pass": True,
        "posterior_configured_model_count": 2,
        "posterior_served_model_count": 2,
        "applied_validations": (
            *pl["applied_validations"],
            "source_clock_configured_source_completeness",
        ),
    }
    source_clock_verifier = {**source_clock, "bin_labels_hash": "bh"}
    source_clock_compiler = {
        **source_clock,
        "bin_labels_hash": "bh",
        "metric": "high",
    }
    _validate_posterior_forecast_authority_payload(source_clock_verifier)
    compiler_validate_forecast(source_clock_compiler)

    source_clock_certificate_fields = {
        "posterior_model_count_basis",
        "posterior_completeness_status",
        "posterior_configured_sources",
        "posterior_served_sources",
        "posterior_missing_sources",
        "posterior_walkforward_pass",
        "posterior_configured_model_count",
        "posterior_served_model_count",
    }
    legacy_two = {
        key: value
        for key, value in source_clock.items()
        if key not in source_clock_certificate_fields
    }
    legacy_two["applied_validations"] = pl["applied_validations"]
    with pytest.raises(
        CertificateVerificationError,
        match="below posterior decorrelated-model floor",
    ):
        _validate_posterior_forecast_authority_payload(
            {**legacy_two, "bin_labels_hash": "bh"}
        )
    with pytest.raises(ValueError, match="below posterior decorrelated-model floor"):
        compiler_validate_forecast(
            {**legacy_two, "bin_labels_hash": "bh", "metric": "high"}
        )

    incomplete = {
        **source_clock,
        "posterior_served_sources": ("ecmwf_ifs",),
        "posterior_served_model_count": 1,
    }
    with pytest.raises(
        CertificateVerificationError,
        match="configured-source completeness invalid",
    ):
        _validate_posterior_forecast_authority_payload(
            {**incomplete, "bin_labels_hash": "bh"}
        )
    with pytest.raises(ValueError, match="configured-source completeness invalid"):
        compiler_validate_forecast(
            {**incomplete, "bin_labels_hash": "bh", "metric": "high"}
        )


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


def test_gate2_day0_seed_uses_bounded_production_index_queries():
    con = _indexed_raw_model_conn()
    statements: list[str] = []
    con.set_trace_callback(statements.append)
    city = SimpleNamespace(timezone="Asia/Tokyo", settlement_unit="C")
    with mock.patch.object(adapter, "runtime_cities_by_name", return_value={"Tokyo": city}):
        seed = adapter._day0_seed_members_multimodel(
            con, family=_family(), decision_time=_DT,
        )
    con.set_trace_callback(None)

    assert sorted(seed or ()) == [29.0, 31.0, 32.0]
    raw_queries = [
        statement
        for statement in statements
        if "FROM raw_model_forecasts" in statement
    ]
    assert len(raw_queries) == 2
    assert all("date(source_cycle_time)" not in statement for statement in raw_queries)
    assert all("endpoint = 'single_runs'" in statement for statement in raw_queries)
    latest_query = next(
        statement
        for statement in raw_queries
        if "ORDER BY source_cycle_time DESC" in statement
    )
    member_query = next(
        statement
        for statement in raw_queries
        if "SELECT model, source_cycle_time, forecast_value_c" in statement
    )
    assert "source_cycle_time >= '2026-06-17'" in member_query
    assert "source_cycle_time < '2026-06-18'" in member_query
    for query in (latest_query, member_query):
        plan = con.execute("EXPLAIN QUERY PLAN " + query).fetchall()
        details = [str(row[3]) for row in plan]
        assert any(
            "idx_raw_model_forecasts_endpoint_family_cycle_members" in detail
            for detail in details
        )
        assert all("TEMP B-TREE" not in detail for detail in details)

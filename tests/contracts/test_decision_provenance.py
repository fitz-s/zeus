# Created: 2026-06-11
# Last reused or audited: 2026-06-11
# Authority basis: docs/evidence/settlement_guard/2026-06-11_decision_provenance_plan.md
#   — OPERATOR LAW 2026-06-11 ~13:20Z: every order-decision receipt (ACCEPTED and REJECTED, every
#   stage) must carry a complete, queryable provenance envelope (data combination, per-input ages,
#   time-to-settlement, economics, FULL untruncated rejection reason). Everything queryable.
"""RELATIONSHIP + unit tests for the DecisionProvenanceEnvelope.

Cross-module invariant (builder -> NoTradeRegretEvent.envelope_json -> ledger INSERT -> column):
  A rejection written through the real reactor's regret path carries an envelope_json that records
  the FULL rejection reason (no storage truncation) AND a populated time-to-settlement, computed
  from the same world-DB truths the decision saw. The money path is byte-identical whether or not
  the envelope is built (observability only — it never gates).
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

import pytest

from src.contracts.decision_provenance import (
    build_decision_provenance_envelope,
    envelope_to_json,
)

UTC = timezone.utc
DECISION = datetime(2026, 6, 11, 13, 0, 0, tzinfo=UTC)


# --- synthetic truths (minimal schemas; live DBs are never touched) ----------------------------


@dataclass(frozen=True)
class _FakeBundle:
    posterior_id: int
    city: str
    target_date: str
    temperature_metric: str
    data_version: str
    source_cycle_time: str
    source_available_at: str
    computed_at: str
    dependency_json: Mapping[str, Any]
    provenance_json: Mapping[str, Any]


def _forecast_conn_with_anchor() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE raw_forecast_artifacts (
            artifact_id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id TEXT NOT NULL,
            source_cycle_time TEXT NOT NULL,
            source_available_at TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            artifact_metadata_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO raw_forecast_artifacts
            (source_id, source_cycle_time, source_available_at, captured_at, artifact_metadata_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            "openmeteo_ecmwf_ifs_9km",
            "2026-06-11T00:00:00+00:00",
            "2026-06-11T11:00:00+00:00",
            "2026-06-11T11:30:00+00:00",
            json.dumps(
                {
                    "source_run_id": "openmeteo-anchor-Helsinki-high-20260611T000000Z",
                    "run_authority": "run_pinned_single_runs",
                    "openmeteo_endpoint": "single_runs_api",
                }
            ),
        ),
    )
    conn.commit()
    return conn


def _bundle() -> _FakeBundle:
    return _FakeBundle(
        posterior_id=1752,
        city="Helsinki",
        target_date="2026-06-12",
        temperature_metric="high",
        data_version="openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_high_v1",
        source_cycle_time="2026-06-11T00:00:00+00:00",
        source_available_at="2026-06-11T11:00:00+00:00",
        computed_at="2026-06-11T12:40:00+00:00",
        dependency_json={
            "openmeteo_ifs9_anchor": "openmeteo-anchor-Helsinki-high-20260611T000000Z",
            "baseline_b0": "ecmwf_open_data:mx2t6_high:2026-06-11T00Z",
        },
        provenance_json={
            "replacement_q_mode": "FUSED_NORMAL_PARTIAL",
            "u0r_fusion": {
                "method": "bayes_precision_fusion",
                "used_models": ["ecmwf_ifs", "gem_global", "jma_seamless"],
                "dropped_models": ["gfs_global"],
                "excluded_regionals": [],
                "decorrelated_providers_served": 5,
                "decorrelated_providers_expected": 6,
            },
            "staleness_violations": [],
        },
    )


def _snapshot_row() -> dict[str, Any]:
    return {
        "snapshot_id": "snap-helsinki-1",
        "captured_at": "2026-06-11T12:55:00+00:00",
        "orderbook_top_bid": "0.41",
        "orderbook_top_ask": "0.44",
        "market_end_at": "2026-06-12T21:59:00+00:00",
        "condition_id": "0xcond",
    }


# --- unit: builder assembles every block from existing truths -----------------------------------


def test_builder_assembles_ages_settlement_and_fusion():
    fc = _forecast_conn_with_anchor()
    try:
        env = build_decision_provenance_envelope(
            fc,
            None,
            bundle=_bundle(),
            decision_time=DECISION,
            condition_id="0xcond",
            token_id="tok-1",
            executable_snapshot_row=_snapshot_row(),
            economics={"q_live": 0.62, "q_lcb_5pct": 0.55, "c_fee_adjusted": 0.44, "trade_score": 0.18},
            direction="NO",
            rejection={"stage": "TRADE_SCORE", "reason": "TRADE_SCORE_NON_POSITIVE:score=-0.02"},
        )
    finally:
        fc.close()

    assert env["posterior_id"] == 1752
    assert env["q_mode"] == "FUSED_NORMAL_PARTIAL"
    # data combination
    assert env["fusion_instruments"]["used_models"] == ["ecmwf_ifs", "gem_global", "jma_seamless"]
    assert env["fusion_instruments"]["dropped_models"] == ["gfs_global"]
    # anchor transport run_authority resolved from the raw artifact metadata
    anchor = env["anchor_transport"]["openmeteo_ifs9_anchor"]
    assert anchor["run_authority"] == "run_pinned_single_runs"
    # per-input ages: anchor cycle is 13h old at the 13:00 decision
    anchor_ages = env["per_input_ages"]["openmeteo_ifs9_anchor"]
    assert anchor_ages["cycle_age_h"] == pytest.approx(13.0, abs=0.01)
    assert anchor_ages["available_age_h"] == pytest.approx(2.0, abs=0.01)
    assert anchor_ages["capture_age_h"] == pytest.approx(1.5, abs=0.01)
    # posterior computed age
    assert env["posterior_computed_age_h"] == pytest.approx(20.0 / 60.0, abs=0.01)
    # time-to-settlement: Helsinki local-day end of 2026-06-12 is a real future instant
    tts = env["time_to_settlement"]
    assert isinstance(tts["local_day_end_utc"], str) and tts["local_day_end_utc"].startswith("2026-06-12")
    assert isinstance(tts["hours_to_local_day_end"], float) and tts["hours_to_local_day_end"] > 0
    assert tts["market_end_at"] == "2026-06-12T21:59:00+00:00"
    assert isinstance(tts["hours_to_market_end"], float) and tts["hours_to_market_end"] > 0
    # book
    assert env["book"]["best_bid"] == pytest.approx(0.41)
    assert env["book"]["best_ask"] == pytest.approx(0.44)
    assert env["book"]["age_s"] == pytest.approx(300.0, abs=1.0)
    # economics + derived edge
    assert env["economics"]["edge"] == pytest.approx(0.62 - 0.44, abs=1e-6)
    assert env["economics"]["fee_model"] == "0.05*p*(1-p)*shares"
    # round-trips to canonical JSON
    assert isinstance(envelope_to_json(env), str)


def test_builder_is_fail_soft_with_no_truths():
    # No conns, no bundle, no snapshot — must produce UNAVAILABLE markers, never raise.
    env = build_decision_provenance_envelope(
        None, None, bundle=None, decision_time=DECISION,
        rejection={"stage": "EVENT_FILTER", "reason": "X"},
    )
    assert str(env["posterior_id"]).startswith("UNAVAILABLE")
    assert str(env["anchor_transport"]).startswith("UNAVAILABLE")
    assert env["economics"]["q_live"] is None
    assert env["rejection"]["reason"] == "X"


def test_full_reason_is_never_truncated_at_storage():
    # 4000-char reason round-trips byte-identical through the envelope and its canonical JSON.
    huge = "REASON_DETAIL:" + ("ZpathBoundary" * 300)
    env = build_decision_provenance_envelope(
        None, None, bundle=None, decision_time=DECISION,
        rejection={"stage": "DECISION_CERTIFICATE", "reason": huge},
    )
    assert env["rejection"]["reason"] == huge
    recovered = json.loads(envelope_to_json(env))
    assert recovered["rejection"]["reason"] == huge


# --- relationship: a real reactor rejection carries the envelope on the regret row --------------


def _import_reactor_harness():
    # Reuse the events-test harness so the relationship is exercised through the REAL reactor path.
    import importlib

    return importlib.import_module("tests.events.test_reactor")


def test_rejection_through_reactor_carries_envelope_with_full_reason_and_settlement():
    harness = _import_reactor_harness()
    conn, store = harness._store()
    event = harness._day0_event()
    store.insert_or_ignore(event)
    # gates=False -> the event is rejected at SOURCE_TRUTH (the earliest stage; no bundle yet).
    reactor, rejected, _submitted = harness._reactor(store, gates=False)
    result = reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=UTC))
    assert result.rejected == 1
    assert rejected and rejected[0][1] == "SOURCE_TRUTH"

    row = conn.execute(
        "SELECT rejection_stage, rejection_reason, envelope_json "
        "FROM no_trade_regret_events WHERE event_id = ?",
        (event.event_id,),
    ).fetchone()
    assert row is not None
    stage, reason, envelope_json = row[0], row[1], row[2]
    assert envelope_json, "regret row must carry a decision-provenance envelope"
    env = json.loads(envelope_json)
    # FULL rejection reason preserved verbatim in the envelope.
    assert env["rejection"]["stage"] == stage == "SOURCE_TRUTH"
    assert env["rejection"]["reason"] == reason
    # time-to-settlement is populated from the event's city/target_date (Chicago / 2026-05-24).
    tts = env["time_to_settlement"]
    assert isinstance(tts["local_day_end_utc"], str) and tts["local_day_end_utc"].startswith("2026-05-2")
    assert isinstance(tts["hours_to_local_day_end"], float)


def test_money_path_byte_identical_when_envelope_builder_disabled(monkeypatch):
    """The envelope is observability: disabling the builder changes NO decision surface.

    With the builder monkeypatched to raise, the rejection still records the SAME typed columns
    (stage / reason / economics) and the SAME rejected-event outcome; only envelope_json goes NULL.
    """
    harness = _import_reactor_harness()

    def _run_once() -> tuple[Any, ...]:
        conn, store = harness._store()
        event = harness._day0_event()
        store.insert_or_ignore(event)
        reactor, rejected, submitted = harness._reactor(store, gates=False)
        result = reactor.process_pending(decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=UTC))
        row = conn.execute(
            "SELECT rejection_stage, rejection_reason, q_live, trade_score, envelope_json "
            "FROM no_trade_regret_events WHERE event_id = ?",
            (event.event_id,),
        ).fetchone()
        return (result.rejected, tuple(rejected), tuple(submitted), row[0], row[1], row[2], row[3], row[4])

    with_envelope = _run_once()

    # Force the builder to fail for the second run: the rejection write must be UNAFFECTED.
    import src.contracts.decision_provenance as dp_mod

    def _boom(*_a, **_k):
        raise RuntimeError("builder disabled")

    monkeypatch.setattr(dp_mod, "build_decision_provenance_envelope", _boom)
    without_envelope = _run_once()

    # Everything except envelope_json (index 7) is byte-identical.
    assert with_envelope[:7] == without_envelope[:7]
    # The decision surface (stage/reason/economics) is unchanged; only the envelope differs.
    assert without_envelope[7] is None  # builder disabled -> NULL envelope, decision intact

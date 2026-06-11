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


# --- call-site threading (production starvation fix, operator verification 2026-06-11) ----------
#
# Live row Karachi|2026-06-12|high @13:54:40Z proved the builder fired but the regret call site
# starved the data-combination half (anchor_transport/fusion/dependency/book all UNAVAILABLE)
# because bundle/forecast_conn/snapshot_row never reached the reactor. The fix threads a
# provenance_capture dict down the adapter chain and attaches the assembled envelope to EVERY
# receipt in the public-builder wrapper; the reactor MERGES the final rejection into it.


def test_adapter_capture_binds_replacement_bundle_before_gates(monkeypatch):
    """RELATIONSHIP: the served bundle is captured at the bind, BEFORE the q-mode/bounds gates,
    so every rejection raised after the read still carries the exact data combination examined."""
    from types import SimpleNamespace

    from src.config import settings
    from src.data import replacement_forecast_bundle_reader as reader
    from src.engine import event_reactor_adapter as adapter
    from src.engine import replacement_forecast_hook_factory as hook_factory
    from src.contracts.execution_price import ExecutionPrice
    from src.types.market import Bin

    feature_flags = dict(settings._data.get("feature_flags", {}))
    feature_flags["openmeteo_ecmwf_ifs9_aifs_soft_anchor_trade_authority_enabled"] = True
    monkeypatch.setitem(settings._data, "feature_flags", feature_flags)
    served_bundle = SimpleNamespace(
        posterior_id=777,
        product_id="openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_v1",
        q={"bin-27": 0.20, "bin-28": 0.80},
        q_lcb=None,
        provenance_json={
            "replacement_q_mode": "FUSED_NORMAL_FULL",
            "q_shape": "fused_normal_direct",
            "bin_topology": [
                {"bin_id": "bin-27", "lower_c": 27.0, "upper_c": 27.0},
                {"bin_id": "bin-28", "lower_c": 28.0, "upper_c": 28.0},
            ],
        },
    )
    monkeypatch.setattr(hook_factory, "_latest_replacement_readiness", lambda *a, **k: object())
    monkeypatch.setattr(
        reader,
        "read_replacement_forecast_bundle",
        lambda *a, **k: SimpleNamespace(ok=True, bundle=served_bundle, reason_code="READY"),
    )
    family = SimpleNamespace(
        city="Testopolis",
        target_date="2026-06-09",
        metric="high",
        candidates=(
            SimpleNamespace(
                condition_id="cond-27", yes_token_id="yes-27", no_token_id="no-27",
                bin=Bin(low=27.0, high=27.0, unit="C", label="27°C"),
            ),
            SimpleNamespace(
                condition_id="cond-28", yes_token_id="yes-28", no_token_id="no-28",
                bin=Bin(low=28.0, high=28.0, unit="C", label="28°C"),
            ),
        ),
    )
    capture: dict[str, Any] = {}
    try:
        adapter._replacement_authority_probability_and_fdr_proof(
            event=SimpleNamespace(event_type="FORECAST_SNAPSHOT_READY"),
            payload={},
            family=family,
            conn=object(),
            native_costs={
                ("cond-27", "buy_yes"): (None, ExecutionPrice(0.30, "ask", fee_deducted=True, currency="probability_units"), 0.30, None, None),
                ("cond-28", "buy_yes"): (None, ExecutionPrice(0.55, "ask", fee_deducted=True, currency="probability_units"), 0.55, None, None),
                ("cond-27", "buy_no"): (None, ExecutionPrice(0.70, "ask", fee_deducted=True, currency="probability_units"), 0.70, None, None),
                ("cond-28", "buy_no"): (None, ExecutionPrice(0.45, "ask", fee_deducted=True, currency="probability_units"), 0.45, None, None),
            },
            decision_time=DECISION,
            promotion_evidence=None,
            capital_objective_evidence=None,
            provenance_capture=capture,
        )
    except Exception:  # noqa: BLE001 — gates after the bind may reject; capture must survive
        pass
    assert capture.get("replacement_bundle") is served_bundle, (
        "the served bundle must be captured at the bind, before any downstream gate"
    )


_PROV_MARKET_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS market_events (
    market_slug TEXT, city TEXT, target_date TEXT, temperature_metric TEXT,
    condition_id TEXT, token_id TEXT, range_label TEXT, range_low REAL, range_high REAL,
    outcome TEXT, created_at TEXT
)
"""

_PROV_SNAPSHOTS_DDL = """
CREATE TABLE IF NOT EXISTS executable_market_snapshots (
    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    condition_id TEXT NOT NULL, market_slug TEXT, market_start_at TEXT, market_end_at TEXT,
    orderbook_top_bid REAL, orderbook_top_ask REAL, captured_at TEXT NOT NULL,
    freshness_deadline TEXT, active INTEGER DEFAULT 1, closed INTEGER DEFAULT 0
)
"""


def test_real_post_snapshot_rejection_carries_populated_book_and_settlement():
    """RELATIONSHIP through the REAL public builder: a rejection fired AFTER the executable
    snapshot bind (OPENING_INERTIA_MARKET_TOO_OLD) carries an envelope whose book and
    time-to-settlement are POPULATED (not UNAVAILABLE) — the production-starvation pin."""
    from datetime import timedelta

    from src.engine.event_reactor_adapter import build_event_bound_no_submit_receipt
    from src.events.opportunity_event import make_opportunity_event
    from src.riskguard.risk_level import RiskLevel

    condition_id = "0xprovenance001"
    now = datetime(2026, 6, 9, 12, 0, 0, tzinfo=UTC)
    opened = now - timedelta(hours=30)  # 30h old -> OPENING_INERTIA_MARKET_TOO_OLD for buy_no
    market_end = "2026-07-01T22:59:00+00:00"

    trade_conn = sqlite3.connect(":memory:")
    trade_conn.execute(_PROV_SNAPSHOTS_DDL)
    trade_conn.execute(
        """
        INSERT INTO executable_market_snapshots
            (condition_id, market_start_at, market_end_at, orderbook_top_bid, orderbook_top_ask,
             captured_at, freshness_deadline, active, closed)
        VALUES (?, ?, ?, 0.35, 0.40, ?, ?, 1, 0)
        """,
        (
            condition_id,
            opened.isoformat(),
            market_end,
            (now - timedelta(seconds=10)).isoformat(),
            (now + timedelta(hours=6)).isoformat(),
        ),
    )
    trade_conn.commit()
    topo_conn = sqlite3.connect(":memory:")
    topo_conn.execute(_PROV_MARKET_EVENTS_DDL)
    topo_conn.execute(
        """
        INSERT INTO market_events
            (market_slug, city, target_date, temperature_metric, condition_id, token_id,
             range_label, range_low, range_high, outcome, created_at)
        VALUES ('london-max-2026-07-01', 'London', '2026-07-01', 'max', ?, '0xtok',
                '>30°C', 30.0, NULL, 'YES', ?)
        """,
        (condition_id, opened.isoformat()),
    )
    topo_conn.commit()

    event = make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key=condition_id,
        source="test",
        observed_at="2026-06-09T12:00:00+00:00",
        available_at="2026-06-09T12:00:00+00:00",
        received_at="2026-06-09T12:00:00+00:00",
        causal_snapshot_id="snap-001",
        payload={
            "condition_id": condition_id,
            "direction": "buy_no",
            "city": "London",
            "target_date": "2026-07-01",
            "metric": "max",
            "temperature_metric": "max",
            "market_slug": "london-max-2026-07-01",
        },
    )
    receipt = build_event_bound_no_submit_receipt(
        event=event,
        trade_conn=trade_conn,
        topology_conn=topo_conn,
        forecast_conn=topo_conn,
        calibration_conn=topo_conn,
        decision_time=now,
        get_current_level=lambda: RiskLevel.GREEN,
    )
    assert receipt.submitted is False
    assert "OPENING_INERTIA_MARKET_TOO_OLD" in (receipt.reason or "")
    assert receipt.envelope_json, "every adapter receipt must carry the provenance envelope"
    env = json.loads(receipt.envelope_json)
    # book POPULATED from the captured snapshot row (the production-starved half)
    assert env["book"]["best_bid"] == pytest.approx(0.35)
    assert env["book"]["best_ask"] == pytest.approx(0.40)
    assert env["book"]["snapshot_id"] is not None
    assert isinstance(env["book"]["age_s"], float)
    # time-to-settlement: BOTH halves populated (market_end_at came with the snapshot)
    tts = env["time_to_settlement"]
    assert isinstance(tts["hours_to_local_day_end"], float)
    assert tts["market_end_at"] == market_end
    assert isinstance(tts["hours_to_market_end"], float)
    # pre-bundle on this fixture (no replacement posterior in the conns): honesty markers
    assert str(env["posterior_id"]).startswith("UNAVAILABLE")
    # rejection is merged later by the reactor; the adapter materials carry none yet
    assert env["rejection"] is None


def test_reactor_merges_rejection_into_adapter_envelope_materials():
    """RELATIONSHIP: reactor._write_regret MERGES the final {stage, reason FULL TEXT} into the
    adapter-attached materials — the populated data-combination half survives to the regret row."""
    import importlib

    harness = importlib.import_module("tests.events.test_reactor")
    from src.events.reactor import EventSubmissionReceipt

    conn, store = harness._store()
    event = harness._day0_event()
    store.insert_or_ignore(event)
    reactor, _rejected, _submitted = harness._reactor(store, gates=False)

    fc = _forecast_conn_with_anchor()
    try:
        materials = build_decision_provenance_envelope(
            fc,
            None,
            bundle=_bundle(),
            decision_time=DECISION,
            condition_id="0xcond",
            token_id="tok-1",
            executable_snapshot_row=_snapshot_row(),
            economics={"q_live": 0.62, "q_lcb_5pct": 0.55, "c_fee_adjusted": 0.44, "trade_score": 0.18},
            direction="NO",
            rejection=None,
        )
    finally:
        fc.close()
    receipt = EventSubmissionReceipt(
        False,
        event.event_id,
        event.causal_snapshot_id,
        reason="KELLY_SIZE_BELOW_VENUE_MINIMUM:size=0.83:min=1.00",
        envelope_json=envelope_to_json(materials),
    )
    huge_reason = "KELLY_SIZE_BELOW_VENUE_MINIMUM:" + ("detail," * 400)
    reactor._write_regret(event, "KELLY", huge_reason, receipt=receipt, decision_time=DECISION)

    row = conn.execute(
        "SELECT rejection_reason, envelope_json FROM no_trade_regret_events WHERE event_id = ?",
        (event.event_id,),
    ).fetchone()
    assert row is not None and row[1]
    env = json.loads(row[1])
    # the POPULATED materials survive (not rebuilt/starved)
    assert env["posterior_id"] == 1752
    assert env["fusion_instruments"]["used_models"] == ["ecmwf_ifs", "gem_global", "jma_seamless"]
    assert env["anchor_transport"]["openmeteo_ifs9_anchor"]["run_authority"] == "run_pinned_single_runs"
    assert isinstance(env["per_input_ages"]["openmeteo_ifs9_anchor"]["cycle_age_h"], float)
    assert isinstance(env["time_to_settlement"]["hours_to_local_day_end"], float)
    # and the final rejection was MERGED in, FULL text
    assert env["rejection"]["stage"] == "KELLY"
    assert env["rejection"]["reason"] == huge_reason == row[0]


def test_receipt_json_always_excludes_envelope_json():
    """Hash-stability antibody: the envelope (decision_time-dependent ages) must NEVER enter
    receipt_json/receipt_hash — a retried event would otherwise raise EdliReceiptHashDriftError.
    The envelope's canonical home is the envelope_json COLUMN."""
    from src.events.no_submit_receipts import _receipt_json
    from src.events.reactor import EventSubmissionReceipt

    base = dict(
        submitted=False,
        event_id="evt-1",
        causal_snapshot_id="snap-1",
        final_intent_id="intent-1",
        side_effect_status="NO_SUBMIT",
        proof_accepted=True,
    )
    without_env = EventSubmissionReceipt(**base)
    with_env = EventSubmissionReceipt(**base, envelope_json='{"decision_time":"2026-06-11T13:00:00+00:00"}')
    j_without = _receipt_json(without_env)
    j_with = _receipt_json(with_env)
    assert "envelope_json" not in j_with
    assert j_with == j_without, "receipt_json must be byte-identical with or without the envelope"


# --- antibody: envelope building NEVER mutates conn.row_factory (Task #42, row_factory isolation) --
#
# Same class as the PRAGMA busy_timeout leak that caused the 2026-06-11 claim storm: a mutation to
# a connection-global attribute is visible to every other thread/coroutine sharing that connection.
# The fix (cursor-local row_factory) is verified here: a sentinel factory set before the call
# must be identical to the factory observed after the call, even on exception paths.


def _sentinel_factory(cursor, row):  # noqa: ARG001
    """Sentinel row_factory — returns rows unchanged; presence is detectable by identity."""
    return row


def _forecast_conn_for_isolation_test() -> sqlite3.Connection:
    """Minimal forecast DB with raw_forecast_artifacts to exercise the full anchor/ages paths."""
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
            json.dumps({
                "source_run_id": "openmeteo-anchor-isolation-test",
                "run_authority": "run_pinned_single_runs",
                "openmeteo_endpoint": "single_runs_api",
            }),
        ),
    )
    conn.commit()
    return conn


def test_envelope_building_leaves_forecast_conn_row_factory_untouched():
    """ANTIBODY (Task #42): build_decision_provenance_envelope must NOT mutate forecast_conn's
    row_factory.  A sentinel factory set before the call must be present after it, including
    through the anchor-transport and per-input-ages code paths that previously save/restored it."""
    fc = _forecast_conn_for_isolation_test()
    fc.row_factory = _sentinel_factory  # sentinel — identity observable by 'is'

    bundle = _FakeBundle(
        posterior_id=99,
        city="Helsinki",
        target_date="2026-06-12",
        temperature_metric="high",
        data_version="v1",
        source_cycle_time="2026-06-11T00:00:00+00:00",
        source_available_at="2026-06-11T11:00:00+00:00",
        computed_at="2026-06-11T12:40:00+00:00",
        dependency_json={"openmeteo_ifs9_anchor": "openmeteo-anchor-isolation-test"},
        provenance_json={"replacement_q_mode": "FUSED_NORMAL_PARTIAL"},
    )

    build_decision_provenance_envelope(
        fc,
        None,
        bundle=bundle,
        decision_time=DECISION,
        rejection={"stage": "TRADE_SCORE", "reason": "isolation_test"},
    )

    assert fc.row_factory is _sentinel_factory, (
        "forecast_conn.row_factory was mutated by envelope building — "
        "cursor-local row_factory must be used instead"
    )
    fc.close()


def test_envelope_building_leaves_forecast_conn_row_factory_untouched_on_query_error():
    """ANTIBODY exception path (Task #42): even when the artifact query raises, the sentinel
    row_factory must survive — cursor-local isolation removes the need for try/finally restore."""
    fc = sqlite3.connect(":memory:")
    # raw_forecast_artifacts table absent -> every query raises sqlite3.OperationalError;
    # the old save/restore try/finally would still restore; the cursor-local approach needs
    # NO finally because the connection was never touched.
    fc.row_factory = _sentinel_factory

    bundle = _FakeBundle(
        posterior_id=88,
        city="Helsinki",
        target_date="2026-06-12",
        temperature_metric="high",
        data_version="v1",
        source_cycle_time="2026-06-11T00:00:00+00:00",
        source_available_at="2026-06-11T11:00:00+00:00",
        computed_at="2026-06-11T12:40:00+00:00",
        dependency_json={"openmeteo_ifs9_anchor": "openmeteo-anchor-missing-table"},
        provenance_json={"replacement_q_mode": "FUSED_NORMAL_PARTIAL"},
    )

    # Must not raise; must not mutate fc.row_factory.
    env = build_decision_provenance_envelope(
        fc,
        None,
        bundle=bundle,
        decision_time=DECISION,
        rejection={"stage": "EVENT_FILTER", "reason": "exception_path_test"},
    )

    assert fc.row_factory is _sentinel_factory, (
        "forecast_conn.row_factory was mutated on the exception path — "
        "cursor-local row_factory eliminates this risk entirely"
    )
    # Fail-soft: the builder must still produce UNAVAILABLE markers, not raise.
    assert str(env["anchor_transport"]).startswith("UNAVAILABLE") or isinstance(
        env["anchor_transport"], dict
    )
    fc.close()

# Created: 2026-05-23
# Last reused/audited: 2026-05-23
# Authority basis: OBS-AUTHORITY-FOUNDATION (auditability foundation for the
#                  live-stall day0_nowcast_entry root cause). FIX-1 + FIX-2.
# Lifecycle: created=2026-05-23; last_reviewed=2026-05-23; last_reused=never
# Purpose: Relationship tests for the settlement-day observation authority
#          foundation (FIX-1: authority row persisted at decision time;
#          FIX-2: day0_context_json stamped on opportunity_fact).
# Reuse: Run when changing cycle_runtime observation-authority capture,
#        log_settlement_day_observation_authority, or log_opportunity_fact
#        day0_context_json path.
"""Relationship tests for the settlement-day observation authority foundation.

These verify the CROSS-MODULE invariants, not just functions:

  FIX-1 — when cycle_runtime fetches a day0/settlement observation for a
  candidate, an auditable settlement_day_observation_authority row is persisted
  (the runtime observation object, previously invisible in the DB), and every
  EdgeDecision the candidate produces references that authority row's id.

  FIX-2 — log_opportunity_fact persists the per-edge day0 observation-lock
  classification (day0_context_json) so an operator can tell whether a
  tradeable-price day0 edge is observation-locked, forecast-upside, or wrong.

Observability only: these assert persistence/linkage, NOT any change to trade
behavior, reject gates, or the price floor.
"""
import json
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace
import pytest

from src.state import db as dbmod
from src.state.ledger import load_architecture_kernel_sql

# (no unittest.mock needed — tests drive the real builder + writer + stamp seam)


@pytest.fixture
def trade_conn(monkeypatch):
    """In-memory trade DB with the trade-class schema applied, wired so the
    durable writers reuse this exact connection.

    Applies BOTH the architecture kernel SQL (opportunity_fact + its new
    observation_authority_id / day0_context_json columns) AND _TRADE_CLASS_DDL
    (settlement_day_observation_authority — a trade-only table created by
    init_schema_trade_only, NOT by the world kernel).
    """
    conn = sqlite3.connect(":memory:")
    conn.executescript(load_architecture_kernel_sql())
    conn.executescript(dbmod._TRADE_CLASS_DDL)
    # Force log_*  writers (which call get_trade_connection unless the passed
    # conn is "verified trade") to reuse this in-memory connection.
    monkeypatch.setattr(dbmod, "_is_verified_trade_connection", lambda c: c is conn)
    yield conn
    conn.close()


def _tokyo_city():
    """City-like object for the day0 classifier.

    The day0 truth classifier (evaluator.day0_high_truth_classification_for_edge)
    calls SettlementSemantics.for_city(candidate.city), which requires a City
    OBJECT exposing settlement_source_type / settlement_unit / wu_station — not a
    bare city-name string. MarketCandidate.city is typed `City` in production;
    earlier fixtures passed the string "Tokyo", so the classifier fell through to
    its defensive `settlement_semantics_unavailable` fallback and never produced
    the real classification. Mirrors Tokyo's runtime config (wu_icao / °C / RJTT).
    """
    return SimpleNamespace(
        name="Tokyo",
        settlement_source_type="wu_icao",
        settlement_unit="C",
        wu_station="RJTT",
    )


# ---------------------------------------------------------------------------
# FIX-1 — runtime observation authority persisted before day0 evaluation,
# and the resulting EdgeDecision references the authority id.
# ---------------------------------------------------------------------------

def test_runtime_observation_authority_persisted_before_day0_evaluation(trade_conn):
    """Relationship: the runtime observation object -> a persisted authority row,
    and the decision-stamp seam links every EdgeDecision back to it.

    The authority is built from the SAME Day0ObservationContext-shaped object the
    cycle fetches (build_settlement_day_observation_authority_row is the exact
    code the runtime calls), persisted via the durable writer, and then the
    candidate->decision stamp invariant is exercised: a candidate carrying the
    written authority_id propagates it to every EdgeDecision it produces (the
    loop in execute_discovery_phase). This isolates the relationship my change
    owns without rebuilding the full discovery harness.
    """
    from src.engine.cycle_runtime import build_settlement_day_observation_authority_row
    from src.engine.evaluator import EdgeDecision

    city = SimpleNamespace(name="Tokyo", timezone="Asia/Tokyo", settlement_unit="C")
    observation = SimpleNamespace(
        current_temp=27.0,
        high_so_far=28.0,
        low_so_far=18.0,
        source="wu_icao",
        observation_time="2026-05-23T15:00:00+09:00",
        unit="C",
        causality_status="OK",
        station_id="RJTT",
        sample_count=16,
        first_sample_time="2026-05-23T00:00:00+09:00",
        last_sample_time="2026-05-23T15:00:00+09:00",
        coverage_status="OK",
        observation_available_at="2026-05-23T06:05:00Z",
    )
    decision_time = datetime(2026, 5, 23, 9, 0, tzinfo=timezone.utc)

    # Step 1: the runtime builds the authority row from the observation it
    # fetched (the exact production builder).
    row = build_settlement_day_observation_authority_row(
        city=city,
        target_date="2026-05-23",
        temperature_metric="high",
        decision_time=decision_time,
        market_phase="settlement_day",
        observation=observation,
        coverage_status="OK",
        recorded_at=decision_time.isoformat(),
    )
    authority_id = row["authority_id"]

    # Step 2: it is persisted durably (previously invisible runtime obs object).
    res = dbmod.log_settlement_day_observation_authority(trade_conn, **row)
    assert res["status"] == "written", res

    persisted = trade_conn.execute(
        "SELECT authority_id, city, target_date, high_so_far, coverage_status, "
        "source_authorized_for_settlement, local_date_matches_target, "
        "persisted_surface_available FROM settlement_day_observation_authority"
    ).fetchall()
    assert len(persisted) == 1
    pid, city_name, target_date, high_so_far, coverage, src_auth, local_match, surface = persisted[0]
    assert pid == authority_id
    assert city_name == "Tokyo"
    assert target_date == "2026-05-23"
    assert high_so_far == 28.0
    assert coverage == "OK"
    assert src_auth == 1
    assert local_match == 1
    assert surface == 1

    # Step 3: the candidate carries the authority id, and the PRODUCTION
    # decision-stamp helper (the exact code execute_discovery_phase calls)
    # propagates it to every EdgeDecision the candidate produces. Driving the
    # real helper means deleting the stamp from the cycle breaks this test.
    from src.engine.cycle_runtime import stamp_observation_authority_id_onto_decisions

    candidate = SimpleNamespace(observation_authority_id=authority_id)
    decisions = [
        EdgeDecision(should_trade=False, decision_id="d1", decision_snapshot_id="s1"),
        EdgeDecision(should_trade=True, decision_id="d2", decision_snapshot_id="s2"),
    ]
    stamp_observation_authority_id_onto_decisions(candidate, decisions)

    assert all(d.observation_authority_id == authority_id for d in decisions), (
        "every EdgeDecision must reference the candidate's observation authority id"
    )


def test_runtime_observation_authority_captures_missing_case(trade_conn):
    """Relationship: a settlement-day obs fetch that FAILS still leaves an
    auditable authority row (persisted_surface_available=0, coverage MISSING).

    This is the key auditability gain — the 'we tried and got nothing' fact was
    previously invisible because the candidate is dropped before any durable
    opportunity_fact write. The runtime calls the same builder with
    observation=None and the availability status from the failure.
    """
    from src.engine.cycle_runtime import build_settlement_day_observation_authority_row

    city = SimpleNamespace(name="Jeddah", timezone="Asia/Riyadh", settlement_unit="C")
    decision_time = datetime(2026, 5, 23, 9, 0, tzinfo=timezone.utc)

    row = build_settlement_day_observation_authority_row(
        city=city,
        target_date="2026-05-23",
        temperature_metric="high",
        decision_time=decision_time,
        market_phase="settlement_day",
        observation=None,
        coverage_status="DATA_UNAVAILABLE",  # _availability_status_for_exception output
        recorded_at=decision_time.isoformat(),
    )
    res = dbmod.log_settlement_day_observation_authority(trade_conn, **row)
    assert res["status"] == "written", res

    rows = trade_conn.execute(
        "SELECT city, coverage_status, persisted_surface_available, freshness_status, "
        "high_so_far FROM settlement_day_observation_authority"
    ).fetchall()
    assert len(rows) == 1, f"missing-case authority row not persisted (got {len(rows)})"
    city_name, coverage, surface, freshness, high = rows[0]
    assert city_name == "Jeddah"
    assert coverage == "MISSING"  # normalized from DATA_UNAVAILABLE
    assert surface == 0
    assert freshness == "MISSING"
    assert high is None


# ---------------------------------------------------------------------------
# FIX-2 — day0 truth classification persisted on the opportunity row.
# ---------------------------------------------------------------------------

def test_day0_truth_classification_persisted(trade_conn):
    """Relationship: observed_high below the candidate bin low -> classification
    'observation_floor_plus_forecast_upside' (a day0_nowcast_entry, NOT
    observation-locked), and the opportunity row's day0_context_json includes
    the classification + observed_high + bin bounds.
    """
    observation = SimpleNamespace(
        high_so_far=5.0,    # observed high
        low_so_far=-2.0,
        current_temp=4.0,
        source="wu_icao",
        observation_time="2026-05-23T12:00:00Z",
    )
    edge_bin = SimpleNamespace(
        low=10.0,           # bin low ABOVE observed high -> forecast-upside
        high=15.0,
        is_open_high=False,
        is_open_low=False,
        is_shoulder=False,
        label="10-15F",
    )
    edge = SimpleNamespace(
        direction="buy_yes",
        bin=edge_bin,
        p_model=0.4,
        p_market=0.1,
        edge=0.3,
        ci_lower=0.2,
        ci_upper=0.5,
    )
    candidate = SimpleNamespace(
        city=_tokyo_city(),
        target_date="2026-05-23",
        temperature_metric="high",
        observation=observation,
        discovery_mode="day0_capture",
        hours_to_resolution=6.0,
        name="Tokyo",
    )
    decision = SimpleNamespace(
        decision_id="dec-fix2",
        edge=edge,
        strategy_key="day0_nowcast_entry",
        selected_method="",
        entry_method="",
        decision_snapshot_id="snap-fix2",
        availability_status="ok",
        observation_authority_id="auth-fix2",
        alpha=0.0,
    )

    res = dbmod.log_opportunity_fact(
        trade_conn,
        candidate=candidate,
        decision=decision,
        should_trade=True,
        rejection_stage="",
        rejection_reasons=None,
        recorded_at="2026-05-23T09:00:00Z",
    )
    assert res["status"] == "written", res

    row = trade_conn.execute(
        "SELECT day0_context_json, observation_authority_id, strategy_key "
        "FROM opportunity_fact WHERE decision_id = ?",
        ("dec-fix2",),
    ).fetchone()
    assert row is not None
    ctx = json.loads(row[0])

    assert ctx["day0_truth_classification"] == "observation_floor_plus_forecast_upside"
    assert ctx["observed_high_so_far"] == 5.0
    assert ctx["observed_low_so_far"] == -2.0
    assert ctx["candidate_bin_low"] == 10.0
    assert ctx["candidate_bin_high"] == 15.0
    assert ctx["settlement_capture_eligible"] is False  # not observation-locked
    assert ctx["observation_authority_id"] == "auth-fix2"
    # The FK column links the opportunity row to the authority row.
    assert row[1] == "auth-fix2"


def test_day0_truth_classification_observation_locked_is_eligible(trade_conn):
    """Sibling case: observed_high already inside/above an open-high bin ->
    'observation_locked' and settlement_capture_eligible True. Guards that the
    eligibility flag is not hardcoded False."""
    observation = SimpleNamespace(
        high_so_far=30.0,
        low_so_far=20.0,
        current_temp=29.0,
        source="wu_icao",
        observation_time="2026-05-23T12:00:00Z",
    )
    edge_bin = SimpleNamespace(
        low=28.0,
        high=None,
        is_open_high=True,    # open-high bin; observed 30 >= 28 -> locked
        is_open_low=False,
        is_shoulder=False,
        label="28F+",
    )
    edge = SimpleNamespace(
        direction="buy_yes", bin=edge_bin, p_model=0.6, p_market=0.4,
        edge=0.2, ci_lower=0.3, ci_upper=0.6,
    )
    candidate = SimpleNamespace(
        city=_tokyo_city(), target_date="2026-05-23", temperature_metric="high",
        observation=observation, discovery_mode="day0_capture",
        hours_to_resolution=4.0, name="Tokyo",
    )
    decision = SimpleNamespace(
        decision_id="dec-locked", edge=edge, strategy_key="settlement_capture",
        selected_method="", entry_method="", decision_snapshot_id="snap-locked",
        availability_status="ok", observation_authority_id="auth-locked", alpha=0.0,
    )
    dbmod.log_opportunity_fact(
        trade_conn, candidate=candidate, decision=decision, should_trade=True,
        rejection_stage="", rejection_reasons=None, recorded_at="2026-05-23T09:00:00Z",
    )
    row = trade_conn.execute(
        "SELECT day0_context_json FROM opportunity_fact WHERE decision_id = ?",
        ("dec-locked",),
    ).fetchone()
    ctx = json.loads(row[0])
    assert ctx["day0_truth_classification"] == "observation_locked"
    assert ctx["settlement_capture_eligible"] is True

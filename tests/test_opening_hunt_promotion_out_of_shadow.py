# Created: 2026-06-09
# Last reused or audited: 2026-06-09
# Authority basis: Operator directive 2026-06-09 ('全部打开，这些shadow only的策略一辈子
#   都不会主动打开，把这些gate都删了') — promote the opening-hunt research candidates
#   OpeningInertiaRelaxation and OpeningStaleQuoteFOK out of shadow purgatory.
#   + /tmp/flag_implementation_review.md §4 (OpeningStaleQuoteFOK never instantiated)
# Purpose: Antibody tests pinning the structural promotion. These make the
#   "shadow-only forever, never instantiated" failure category UNCONSTRUCTABLE:
#   the two candidates MUST appear in the live candidate pipeline list and MUST
#   reach evaluation (emit a decision/no_trade row), not be silently dropped.
#   Iron rule preserved: refuted shoulder_sell stays registry-blocked.
"""Relationship/antibody tests: opening-hunt candidates out of shadow purgatory.

The candidate pipeline (`shadow_candidate_dispatch._build_candidate_list`) is the
instantiation point for candidate-class strategies. Before promotion,
OpeningStaleQuoteFOK was NEVER instantiated there (pure shadow purgatory) and
OpeningInertiaRelaxation carried "NEVER live" purgatory wording. These tests pin:

  (i)   both candidates are instantiated in the pipeline (wired, not dropped);
  (ii)  OpeningStaleQuoteFOK is instantiable and emits a candidate decision on a
        synthetic stale-book fixture (reaches evaluation, writes a row);
  (iii) OpeningInertiaRelaxation reaches evaluation under the live capture flag
        (not silently dropped at the candidate-status gate);
  (iv)  REGRESSION: refuted shoulder_sell stays registry-blocked (iron rule).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

from src.contracts.decision_natural_key import make_decision_natural_key


# ---------------------------------------------------------------------------
# Shared minimal DDL (decision_events + no_trade_events), mirrors L1 hook test.
# ---------------------------------------------------------------------------

_DECISION_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS decision_events (
    market_slug         TEXT NOT NULL,
    temperature_metric  TEXT NOT NULL,
    target_date         TEXT NOT NULL,
    observation_time    TEXT NOT NULL,
    decision_seq        INTEGER NOT NULL,
    condition_id        TEXT,
    decision_event_id   TEXT,
    decision_time       TEXT NOT NULL,
    outcome             TEXT NOT NULL,
    side                TEXT NOT NULL,
    strategy_key        TEXT NOT NULL,
    cycle_id            TEXT,
    cycle_iteration     INTEGER,
    p_posterior         REAL,
    edge                REAL,
    target_size_usd     REAL,
    target_price        REAL,
    forecast_time              TEXT,
    provider_reported_time     TEXT,
    observation_available_at   TEXT NOT NULL DEFAULT '',
    polymarket_end_anchor_source TEXT NOT NULL DEFAULT 'unknown_legacy',
    first_member_observed_time TEXT,
    run_complete_time          TEXT,
    zeus_submit_intent_time    TEXT,
    venue_ack_time             TEXT,
    first_inclusion_block_time TEXT,
    finality_confirmed_time    TEXT,
    clock_skew_estimate_ms_at_submit INTEGER,
    raw_orderbook_hash_transition_delta_ms INTEGER,
    schema_version INTEGER NOT NULL,
    source         TEXT NOT NULL,
    PRIMARY KEY (market_slug, temperature_metric, target_date, observation_time, decision_seq)
)
"""

_NO_TRADE_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS no_trade_events (
    market_slug         TEXT NOT NULL,
    temperature_metric  TEXT NOT NULL,
    target_date         TEXT NOT NULL,
    observation_time    TEXT NOT NULL,
    decision_seq        INTEGER NOT NULL,
    reason              TEXT NOT NULL,
    reason_detail       TEXT,
    strategy_key        TEXT,
    event_source        TEXT,
    shadow_runtime      INTEGER,
    observed_at         TEXT NOT NULL,
    schema_version      INTEGER NOT NULL,
    schema_compatibility TEXT NOT NULL DEFAULT 'current',
    PRIMARY KEY (market_slug, temperature_metric, target_date, observation_time, decision_seq)
)
"""

_DT = datetime(2026, 6, 9, 18, 10, 0)
_OBS_TIME = "2026-06-09T18:00:00+00:00"


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(_DECISION_EVENTS_DDL)
    conn.execute(_NO_TRADE_EVENTS_DDL)
    conn.commit()
    return conn


def _make_context(conn):
    from src.strategy.candidates import CandidateContext

    nk = make_decision_natural_key(
        market_slug="test-market-NYC-high-2026-06-09",
        temperature_metric="high",  # type: ignore[arg-type]
        target_date="2026-06-09",
        observation_time=_OBS_TIME,
        decision_seq=0,
    )
    # analysis filled per-test
    return nk


# ---------------------------------------------------------------------------
# (i) Both candidates are instantiated in the live candidate pipeline.
# ---------------------------------------------------------------------------

def test_opening_candidates_wired_into_pipeline() -> None:
    """Antibody: OpeningInertiaRelaxation AND OpeningStaleQuoteFOK both appear in
    the candidate pipeline. Before promotion, c2 was NEVER instantiated here."""
    from src.engine.shadow_candidate_dispatch import _build_candidate_list

    keys = [c.strategy_key for c in _build_candidate_list()]
    assert "opening_inertia_relaxation" in keys, (
        "OpeningInertiaRelaxation must be instantiated in the candidate pipeline"
    )
    assert "c2_opening_stale_fok" in keys, (
        "OpeningStaleQuoteFOK must be instantiated in the candidate pipeline "
        "(was shadow-purgatory: never wired before 2026-06-09 promotion)"
    )


def test_opening_stale_fok_executable_alpha_promoted() -> None:
    """OpeningStaleQuoteFOK executable_alpha flipped True on promotion. The §14
    EV theorem (p⁻ − a0 − phi > 0) and FOK no-fill=0-loss remain the economic
    gate; executable_alpha is the promotion descriptor, not the economic gate."""
    from src.strategy.candidates import OpeningStaleQuoteFOK

    cand = OpeningStaleQuoteFOK()
    assert cand.metadata.executable_alpha is True


# ---------------------------------------------------------------------------
# (ii) OpeningStaleQuoteFOK instantiable + emits a candidate on a synthetic
#      stale-book fixture (reaches evaluation, writes a decision_events row).
# ---------------------------------------------------------------------------

def test_opening_stale_fok_emits_candidate_on_synthetic_stale_book() -> None:
    """Synthetic stale-book + positive opening edge → enter; row written.

    Calibration p_hat=0.90, cal_p_hats near 0.90 with outcomes=1 → p_lo≈0.79,
    which beats stale ask=0.40. info_event observed, book hash never transitioned
    (stale). This proves c2 reaches evaluation and emits, not silently dropped."""
    from src.strategy.candidates import OpeningStaleQuoteFOK

    conn = _make_conn()
    nk = _make_context(conn)
    from src.strategy.candidates import CandidateContext

    analysis = SimpleNamespace(
        p_hat=0.90,
        cal_p_hats=[0.89, 0.90, 0.91, 0.92, 0.93],
        cal_outcomes=[1, 1, 1, 1, 1],
        info_event_observed=True,
        stale_quote_price=Decimal("0.40"),
        book_hash="hash-unchanged",
        book_hash_transition_delta_ms=None,  # never transitioned = stale
    )
    ctx = CandidateContext(natural_key=nk, observed_at=_OBS_TIME, analysis=analysis)

    dec = OpeningStaleQuoteFOK().evaluate(context=ctx, conn=conn, decision_time=_DT)

    assert dec.outcome == "enter", (
        f"Expected enter on positive-edge stale-book fixture, got {dec.outcome} "
        f"(reason={getattr(dec, 'reason', None)})"
    )
    assert dec.strategy_key == "c2_opening_stale_fok"
    row = conn.execute(
        "SELECT strategy_key FROM decision_events"
    ).fetchone()
    assert row is not None and row["strategy_key"] == "c2_opening_stale_fok"


def test_opening_stale_fok_no_trade_carries_c2_provenance() -> None:
    """When c2 data-gates (no calibration), its no_trade row carries the
    c2_opening_stale_fok strategy provenance (not 'unknown_candidate')."""
    from src.strategy.candidates import OpeningStaleQuoteFOK, CandidateContext

    conn = _make_conn()
    nk = _make_context(conn)
    analysis = SimpleNamespace(
        p_hat=0.5,
        cal_p_hats=[],   # data-gated: empty calibration → UNWIRED
        cal_outcomes=[],
    )
    ctx = CandidateContext(natural_key=nk, observed_at=_OBS_TIME, analysis=analysis)

    dec = OpeningStaleQuoteFOK().evaluate(context=ctx, conn=conn, decision_time=_DT)
    assert dec.outcome == "no_trade"

    row = conn.execute(
        "SELECT strategy_key, reason_detail FROM no_trade_events"
    ).fetchone()
    assert row is not None
    assert row["strategy_key"] == "c2_opening_stale_fok", (
        "c2 no_trade rows must carry c2 provenance, not unknown_candidate"
    )


# ---------------------------------------------------------------------------
# (iii) OpeningInertiaRelaxation reaches evaluation under the live capture flag
#       (dispatched, not silently dropped at the candidate-status gate).
# ---------------------------------------------------------------------------

def test_opening_inertia_relaxation_reaches_evaluation_under_capture(monkeypatch) -> None:
    """Flag ON + OpeningInertiaRelaxation enter fixture → decision_events row.
    This proves the candidate flows into the live evaluation dispatch and is not
    dropped at the strategy-status gate (promotion out of shadow purgatory)."""
    import src.engine.shadow_candidate_dispatch as scd
    from src.strategy.candidates import OpeningInertiaRelaxation

    monkeypatch.setattr(scd, "shadow_candidate_capture_enabled", lambda: True)
    monkeypatch.setattr(scd, "_ALL_SHADOW_CANDIDATES", [OpeningInertiaRelaxation()])

    conn = _make_conn()
    # Enter fixture: p_lo≈0.79 beats ask=0.40 → buy_yes EV positive.
    analysis = SimpleNamespace(
        p_hat=0.90,
        cal_p_hats=[0.89, 0.90, 0.91, 0.92, 0.93],
        cal_outcomes=[1, 1, 1, 1, 1],
        ask=0.40,
        no_ask=None,
        no_p_lower=None,
    )
    nk = make_decision_natural_key(
        market_slug="test-market-NYC-high-2026-06-09",
        temperature_metric="high",  # type: ignore[arg-type]
        target_date="2026-06-09",
        observation_time=_OBS_TIME,
        decision_seq=0,
    )

    scd.dispatch_shadow_candidates(
        analysis=analysis,
        natural_key=nk,
        observed_at=_OBS_TIME,
        world_conn=conn,
        decision_time=_DT,
    )

    rows = conn.execute(
        "SELECT strategy_key, source FROM decision_events"
    ).fetchall()
    assert len(rows) == 1, (
        f"OpeningInertiaRelaxation must reach evaluation and emit a row, got {len(rows)}"
    )
    assert rows[0]["strategy_key"] == "opening_inertia_relaxation"
    assert rows[0]["source"] == "shadow_decision"


# ---------------------------------------------------------------------------
# (iv) REGRESSION (iron rule): refuted shoulder_sell stays registry-blocked.
# ---------------------------------------------------------------------------

def test_shoulder_sell_stays_blocked_after_opening_promotion() -> None:
    """Iron rule: promoting opening-hunt candidates must NOT revive the
    settlement-REFUTED shoulder_sell. It remains registry-blocked and absent
    from both the boot and runtime live allowlists."""
    from src.strategy.strategy_profile import (
        get,
        live_allowed_keys,
        live_safe_keys,
    )

    profile = get("shoulder_sell")
    assert profile.live_status == "blocked"
    assert profile.is_runtime_live() is False
    assert profile.is_boot_allowed() is False
    assert "shoulder_sell" not in live_allowed_keys()
    assert "shoulder_sell" not in live_safe_keys()

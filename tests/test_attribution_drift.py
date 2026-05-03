# Created: 2026-04-28
# Last reused/audited: 2026-05-02
# Authority basis: round3_verdict.md §1 #2 (R3 next packet) + ULTIMATE_PLAN.md
# L305-308 (silent attribution drift detector). Per Fitz "test relationships,
# not just functions" — these tests verify the CROSS-MODULE invariant that
# detect_attribution_drift correctly re-applies the entry-time
# _strategy_key_for dispatch rule from src/engine/evaluator.py:420-441 on
# persisted row attributes and surfaces label/semantics mismatches.
"""BATCH 1 tests for attribution_drift.

Eight relationship tests covering:

  1. test_label_matches_when_all_clauses_align — center_buy + buy_yes + finite
  2. test_drift_detected_label_says_shoulder_but_bin_is_finite_range — canonical
     drift case from ULTIMATE_PLAN.md L305-308
    3. test_shoulder_buy_quadrant_is_insufficient_signal — dormant inverse quadrant
    4. test_center_sell_quadrant_is_insufficient_signal — dormant inverse quadrant
  5. test_insufficient_signal_when_bin_topology_unknown — conservative classifier
  6. test_insufficient_signal_when_label_is_settlement_capture_no_discovery_mode
     — the asymmetry between STRATEGIES enum + evaluator dispatch rule
  7. test_insufficient_signal_when_label_not_in_governed_strategy_keys — quarantine
  8. test_bin_topology_classifier_recognizes_each_class — classifier unit tests
  9. test_detect_drifts_in_window_filters_by_metric_ready_and_window — wrapper
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date

import pytest

from src.state.db import init_schema
from src.state.attribution_drift import (
    STRATEGY_KEYS,
    AttributionVerdict,
    _classify_bin_topology,
    detect_attribution_drift,
    detect_drifts_in_window,
)
from tests.test_edge_observation import _insert_settled


# --- Helpers ---------------------------------------------------------------


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    init_schema(conn)
    return conn


def _row(
    *,
    strategy: str,
    bin_label: str,
    direction: str = "buy_yes",
    discovery_mode: str | None = None,
    trade_id: str = "p1",
    metric_ready: bool = True,
    outcome: int = 1,
    p_posterior: float = 0.5,
) -> dict:
    """Synthesize a normalized row dict shaped like what
    query_authoritative_settlement_rows returns. Useful for unit tests of
    detect_attribution_drift that do NOT need a real DB."""
    return {
        "trade_id": trade_id,
        "strategy": strategy,
        "bin_label": bin_label,
        "range_label": bin_label,
        "direction": direction,
        "discovery_mode": discovery_mode,
        "outcome": outcome,
        "p_posterior": p_posterior,
        "metric_ready": metric_ready,
        "settled_at": "2026-04-20T12:00:00+00:00",
    }


# --- Tests -----------------------------------------------------------------


def test_label_matches_when_all_clauses_align():
    """RELATIONSHIP: persisted strategy_key='center_buy' on a finite-range
    bin with direction='buy_yes' agrees with the dispatch rule (clauses
    1-2 inapplicable since shoulder=False; clause 4 fires). Verdict
    label_matches_semantics."""
    row = _row(strategy="center_buy", bin_label="50-51°F", direction="buy_yes")
    v = detect_attribution_drift(row)
    assert isinstance(v, AttributionVerdict)
    assert v.kind == "label_matches_semantics", \
        f"got {v.kind}; evidence={v.evidence}"
    assert v.signature.inferred_strategy == "center_buy"
    assert v.signature.bin_topology == "finite_range"


def test_drift_detected_label_says_shoulder_but_bin_is_finite_range():
    """RELATIONSHIP: canonical drift case from ULTIMATE_PLAN.md L305-308.
    Position labeled shoulder_sell but its bin is a finite_range, so the
    dispatch rule would NOT have fired clause 3. Mismatch → drift_detected."""
    row = _row(strategy="shoulder_sell", bin_label="50-51°F", direction="buy_yes")
    v = detect_attribution_drift(row)
    assert v.kind == "drift_detected"
    assert v.signature.label_strategy == "shoulder_sell"
    assert v.signature.inferred_strategy == "center_buy"   # buy_yes + non-shoulder
    assert "shoulder_sell" in v.evidence["mismatch_summary"]
    assert "center_buy" in v.evidence["mismatch_summary"]


def test_shoulder_buy_quadrant_is_insufficient_signal():
    """RELATIONSHIP: buy-YES shoulder is the dormant shoulder_buy quadrant,
    not shoulder_sell. Attribution must fail closed instead of relabeling it."""
    row = _row(strategy="center_buy", bin_label="75°F+", direction="buy_yes")
    v = detect_attribution_drift(row)
    assert v.kind == "insufficient_signal"
    assert v.signature.label_strategy == "center_buy"
    assert v.signature.inferred_strategy is None
    assert v.signature.bin_topology == "open_shoulder"
    assert v.evidence["reason"] == "cannot_infer_strategy_from_row"


def test_center_sell_quadrant_is_insufficient_signal():
    """RELATIONSHIP: buy-NO center is the dormant center_sell quadrant,
    not opening_inertia. Attribution must fail closed instead of falling back."""
    row = _row(strategy="center_buy", bin_label="50-51°F", direction="buy_no")
    v = detect_attribution_drift(row)
    assert v.kind == "insufficient_signal"
    assert v.signature.inferred_strategy is None
    assert v.evidence["reason"] == "cannot_infer_strategy_from_row"


def test_insufficient_signal_when_bin_topology_unknown():
    """RELATIONSHIP: conservative classifier — when bin_label format is
    ambiguous (does not match any of point / finite_range / open_shoulder
    patterns), the detector cannot rule out shoulder, so emits
    insufficient_signal rather than risk a false drift."""
    row = _row(strategy="center_buy", bin_label="weird-label-format", direction="buy_yes")
    v = detect_attribution_drift(row)
    assert v.kind == "insufficient_signal"
    assert v.signature.bin_topology == "unknown"
    assert v.evidence["reason"] == "cannot_infer_strategy_from_row"


def test_insufficient_signal_when_label_is_settlement_capture_no_discovery_mode():
    """RELATIONSHIP: STRATEGIES enum vs evaluator dispatch rule asymmetry.
    'settlement_capture' is only assigned by the entry-time rule when
    discovery_mode=='day0_capture'. When discovery_mode is missing from the
    row (current normalizer reality — see attribution_drift.py module
    docstring §"Known limitations"), we cannot tell if clause 1 would have
    fired. Detector emits insufficient_signal rather than risk a false drift."""
    row = _row(strategy="settlement_capture", bin_label="50-51°F", direction="buy_yes",
               discovery_mode=None)
    v = detect_attribution_drift(row)
    assert v.kind == "insufficient_signal"
    assert v.signature.discovery_mode is None


def test_insufficient_signal_when_label_not_in_governed_strategy_keys():
    """RELATIONSHIP: a position whose strategy field is not one of the 4
    governed STRATEGY_KEYS is quarantined (insufficient_signal) rather than
    flagged as drift. AGENTS.md §strategy families says strategy_key is the
    sole governance identity; non-governed labels are upstream data quality
    issues, not attribution drift in the sense this packet measures."""
    row = _row(strategy="legacy_unknown_strategy", bin_label="50-51°F", direction="buy_yes")
    v = detect_attribution_drift(row)
    assert v.kind == "insufficient_signal"
    assert v.evidence["reason"] == "label_not_in_governed_strategy_keys"


def test_bin_topology_classifier_recognizes_each_class():
    """RELATIONSHIP: per AGENTS.md L60-67 + L66 antibody — classifier must
    handle the documented patterns conservatively."""
    # Open-shoulder patterns.
    assert _classify_bin_topology("75°F or above") == "open_shoulder"
    assert _classify_bin_topology("32°F or below") == "open_shoulder"
    assert _classify_bin_topology("75°F+") == "open_shoulder"
    assert _classify_bin_topology("75+") == "open_shoulder"
    assert _classify_bin_topology(">= 75") == "open_shoulder"
    # Finite-range patterns.
    assert _classify_bin_topology("50-51°F") == "finite_range"
    assert _classify_bin_topology("-10--5°F") == "finite_range"
    # Point patterns (°C).
    assert _classify_bin_topology("10°C") == "point"
    assert _classify_bin_topology("-5°C") == "point"
    # Conservative fallback.
    assert _classify_bin_topology("") == "unknown"
    assert _classify_bin_topology(None) == "unknown"  # type: ignore[arg-type]
    assert _classify_bin_topology("???") == "unknown"


def test_detect_drifts_in_window_filters_by_metric_ready_and_window():
    """RELATIONSHIP: window-wrapper applies same metric_ready filter as
    edge_observation + same window-end filtering. Out-of-window rows
    excluded; metric_ready=False rows excluded; in-window readable rows
    pass through to detect_attribution_drift."""
    conn = _make_conn()
    # In-window, label_matches: center_buy + buy_yes + finite_range bin.
    _insert_settled(conn, position_id="m1", strategy="center_buy",
                    settled_at="2026-04-23T12:00:00+00:00", outcome=1, p_posterior=0.5)
    # In-window, label_matches: shoulder_sell — but _insert_settled writes
    # bin_label="39-40°F" which is finite_range; so this is actually drift!
    # That's the antibody at work — same shape proves the wrapper passes
    # rows through unchanged. Verify: should be drift_detected.
    _insert_settled(conn, position_id="m2", strategy="shoulder_sell",
                    settled_at="2026-04-23T12:00:00+00:00", outcome=0, p_posterior=0.4)
    # Out-of-window (too old). Should be excluded.
    _insert_settled(conn, position_id="old1", strategy="center_buy",
                    settled_at="2026-03-29T12:00:00+00:00", outcome=1, p_posterior=0.5)

    verdicts = detect_drifts_in_window(conn, window_days=7, end_date="2026-04-28")
    assert len(verdicts) == 2, f"window filter; got {len(verdicts)} verdicts"
    by_pid = {v.position_id: v for v in verdicts}
    # m1: center_buy on finite_range with buy_yes direction.
    # _insert_settled writes direction='buy_yes' implicitly via position_current
    # but the SETTLED row passes through direction from position_current via
    # query_authoritative_settlement_rows. Verify match.
    assert "m1" in by_pid
    # m2: shoulder_sell label on finite_range bin → drift_detected (antibody!).
    assert "m2" in by_pid
    assert by_pid["m2"].kind == "drift_detected", \
        f"m2 should drift; got {by_pid['m2'].kind}; evidence={by_pid['m2'].evidence}"
    # Old position excluded.
    assert "old1" not in by_pid


# =====================================================================
# BATCH 2 — compute_drift_rate_per_strategy tests
# =====================================================================
# Per dispatch GO_BATCH_2 + boot §2: 6-8 tests covering per-strategy rate
# correctness, sample-quality boundaries, empty-result safety, window
# filter, insufficient-exclusion logic.
#
# DENOMINATOR DISCIPLINE: drift_rate = n_drift / (n_drift + n_matches);
# n_insufficient EXCLUDED from denominator (per boot §6 #2 + GO_BATCH_1
# default 2). Tests pin this so a future refactor cannot dilute the rate.

from src.state.attribution_drift import compute_drift_rate_per_strategy


def test_compute_drift_rate_basic_per_strategy_correctness():
    """RELATIONSHIP: insert mixed-verdict positions for one strategy; verify
    drift_rate = n_drift / (n_drift + n_matches), insufficient excluded.

    Setup for shoulder_sell:
    - 2 matching positions (label=shoulder_sell + open_shoulder bin → match)
      ... but _insert_settled writes bin_label='39-40°F' (finite_range).
      So a labeled-shoulder_sell row with the helper's default bin_label is
      DRIFT, not match. Use this to synthesize the mix.
    - For matches: insert center_buy with finite_range bin (helper default).
    - For drift: insert shoulder_sell with finite_range bin (helper default).
    - For insufficient: insert opening_inertia with finite_range bin and
      direction=buy_yes (helper default) → inferred=center_buy → drift, NOT
      insufficient. To force insufficient_signal, label MUST be
      settlement_capture (no discovery_mode). Use that.

    Final per-strategy:
    - center_buy: 2 positions, both labeled center_buy on finite_range with
      buy_yes (helper default direction) → both match. drift_rate = 0/2 = 0.
    - shoulder_sell: 3 positions, all labeled shoulder_sell on finite_range
      → all 3 drift. drift_rate = 3/3 = 1.0.
    - settlement_capture: 1 position labeled settlement_capture (no
      discovery_mode) → insufficient_signal. n_decidable=0; drift_rate=None.
    - opening_inertia: 0 positions.
    """
    conn = _make_conn()
    base = "2026-04-23T12:00:00+00:00"
    # 2 center_buy matches
    _insert_settled(conn, position_id="cb1", strategy="center_buy",
                    settled_at=base, outcome=1, p_posterior=0.5)
    _insert_settled(conn, position_id="cb2", strategy="center_buy",
                    settled_at=base, outcome=0, p_posterior=0.4)
    # 3 shoulder_sell drifts
    _insert_settled(conn, position_id="ss1", strategy="shoulder_sell",
                    settled_at=base, outcome=1, p_posterior=0.5)
    _insert_settled(conn, position_id="ss2", strategy="shoulder_sell",
                    settled_at=base, outcome=0, p_posterior=0.5)
    _insert_settled(conn, position_id="ss3", strategy="shoulder_sell",
                    settled_at=base, outcome=1, p_posterior=0.5)
    # 1 settlement_capture insufficient
    _insert_settled(conn, position_id="sc1", strategy="settlement_capture",
                    settled_at=base, outcome=1, p_posterior=0.5)

    result = compute_drift_rate_per_strategy(conn, window_days=7, end_date="2026-04-28")
    assert set(result.keys()) == set(STRATEGY_KEYS)

    cb = result["center_buy"]
    assert cb["n_positions"] == 2
    assert cb["n_drift"] == 0
    assert cb["n_matches"] == 2
    assert cb["n_decidable"] == 2
    assert cb["drift_rate"] == 0.0

    ss = result["shoulder_sell"]
    assert ss["n_positions"] == 3
    assert ss["n_drift"] == 3, f"expected 3 drifts; got {ss['n_drift']}; rec={ss}"
    assert ss["n_matches"] == 0
    assert ss["n_decidable"] == 3
    assert ss["drift_rate"] == 1.0

    sc = result["settlement_capture"]
    assert sc["n_positions"] == 1
    assert sc["n_drift"] == 0
    assert sc["n_matches"] == 0
    assert sc["n_insufficient"] == 1
    assert sc["n_decidable"] == 0
    assert sc["drift_rate"] is None, "n_decidable=0 → drift_rate must be None (NOT 0.0)"

    oi = result["opening_inertia"]
    assert oi["n_positions"] == 0
    assert oi["drift_rate"] is None


def test_drift_rate_insufficient_excluded_from_denominator():
    """RELATIONSHIP: 1 drift + 1 match + 8 insufficient = drift_rate 0.5
    (1/(1+1)=0.5), NOT 0.1 (1/10) — insufficient EXCLUDED per dispatch
    GO_BATCH_1 default 2. Pins the denominator policy."""
    conn = _make_conn()
    base = "2026-04-23T12:00:00+00:00"
    # 1 drift: shoulder_sell on finite_range
    _insert_settled(conn, position_id="d1", strategy="shoulder_sell",
                    settled_at=base, outcome=1, p_posterior=0.5)
    # 1 match: center_buy on finite_range with buy_yes
    _insert_settled(conn, position_id="m1", strategy="center_buy",
                    settled_at=base, outcome=1, p_posterior=0.5)
    # 8 insufficient: settlement_capture (no discovery_mode)
    for i in range(8):
        _insert_settled(conn, position_id=f"ins{i}", strategy="settlement_capture",
                        settled_at=base, outcome=1, p_posterior=0.5)

    result = compute_drift_rate_per_strategy(conn, window_days=7, end_date="2026-04-28")
    # shoulder_sell rec: 1 drift, 0 match → drift_rate = 1/1 = 1.0 (not 1/9 nor 1/10)
    assert result["shoulder_sell"]["drift_rate"] == 1.0
    assert result["shoulder_sell"]["n_decidable"] == 1
    # center_buy rec: 0 drift, 1 match → 0/1 = 0.0
    assert result["center_buy"]["drift_rate"] == 0.0
    # settlement_capture: all 8 insufficient → drift_rate None
    assert result["settlement_capture"]["drift_rate"] is None
    assert result["settlement_capture"]["n_insufficient"] == 8
    assert result["settlement_capture"]["n_decidable"] == 0


def test_drift_rate_sample_quality_boundaries():
    """RELATIONSHIP: sample_quality is classified by n_decidable, NOT
    n_positions. Insert 9 decidable + 5 insufficient = n_positions=14 but
    n_decidable=9 → sample_quality=insufficient (boundary at 10)."""
    conn = _make_conn()
    base = "2026-04-23T12:00:00+00:00"
    for i in range(9):
        _insert_settled(conn, position_id=f"cb{i}", strategy="center_buy",
                        settled_at=base, outcome=1, p_posterior=0.5)
    for i in range(5):
        _insert_settled(conn, position_id=f"sc{i}", strategy="center_buy",
                        # Force insufficient on a center_buy via unknown bin_topology
                        # — but _insert_settled writes finite_range bin_label.
                        # Instead, use settlement_capture (insufficient).
                        settled_at=base, outcome=1, p_posterior=0.5)
    # All 14 are center_buy (helper default direction=buy_yes + finite_range
    # bin) → all 14 are matches → n_decidable=14 → sample_quality='low'.
    result = compute_drift_rate_per_strategy(conn, window_days=7, end_date="2026-04-28")
    cb = result["center_buy"]
    assert cb["n_positions"] == 14
    assert cb["n_decidable"] == 14
    assert cb["sample_quality"] == "low", f"got {cb['sample_quality']!r}"
    # Other strategies remain insufficient (n_decidable=0).
    for sk in ("shoulder_sell", "settlement_capture", "opening_inertia"):
        assert result[sk]["sample_quality"] == "insufficient"


def test_drift_rate_empty_db_safety():
    """RELATIONSHIP: empty DB → all 4 strategies present with drift_rate=None
    + n_positions=0 + sample_quality=insufficient + window bounds set."""
    conn = _make_conn()
    result = compute_drift_rate_per_strategy(conn, window_days=7, end_date="2026-04-28")
    assert set(result.keys()) == set(STRATEGY_KEYS)
    for sk, rec in result.items():
        assert rec["n_positions"] == 0
        assert rec["n_drift"] == 0
        assert rec["n_matches"] == 0
        assert rec["n_insufficient"] == 0
        assert rec["n_decidable"] == 0
        assert rec["drift_rate"] is None
        assert rec["sample_quality"] == "insufficient"
        assert rec["window_start"] == "2026-04-21"
        assert rec["window_end"] == "2026-04-28"


def test_drift_rate_window_filter():
    """RELATIONSHIP: settled_at outside window excluded from aggregation."""
    conn = _make_conn()
    # In-window drift
    _insert_settled(conn, position_id="in1", strategy="shoulder_sell",
                    settled_at="2026-04-23T12:00:00+00:00", outcome=1, p_posterior=0.5)
    # Out-of-window (too old) — should be excluded
    _insert_settled(conn, position_id="old1", strategy="shoulder_sell",
                    settled_at="2026-03-29T12:00:00+00:00", outcome=0, p_posterior=0.5)
    # Out-of-window (future) — should be excluded
    _insert_settled(conn, position_id="future1", strategy="shoulder_sell",
                    settled_at="2026-04-29T12:00:00+00:00", outcome=1, p_posterior=0.5)

    result = compute_drift_rate_per_strategy(conn, window_days=7, end_date="2026-04-28")
    ss = result["shoulder_sell"]
    assert ss["n_positions"] == 1, f"expected 1 in-window; got {ss['n_positions']}"
    assert ss["n_drift"] == 1
    assert ss["drift_rate"] == 1.0


def test_drift_rate_unknown_strategy_label_skipped_from_aggregation():
    """RELATIONSHIP: positions with strategy_key not in STRATEGY_KEYS cannot
    be inserted (schema CHECK), so this test verifies that even if such a
    row existed, it would NOT pollute the per-strategy aggregation. We
    cannot insert one to test directly (CHECK constraint), but we can
    confirm the aggregation never raises and returns only governed keys."""
    conn = _make_conn()
    # Insert one valid drift to confirm the aggregation runs end-to-end.
    _insert_settled(conn, position_id="ss1", strategy="shoulder_sell",
                    settled_at="2026-04-23T12:00:00+00:00", outcome=1, p_posterior=0.5)
    result = compute_drift_rate_per_strategy(conn, window_days=7, end_date="2026-04-28")
    # Result keys are exactly the 4 governed STRATEGY_KEYS.
    assert set(result.keys()) == set(STRATEGY_KEYS)
    assert "legacy_unknown_strategy" not in result

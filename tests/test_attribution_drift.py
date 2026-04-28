# Created: 2026-04-28
# Last reused/audited: 2026-04-28
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
  3. test_drift_detected_label_says_center_buy_but_bin_is_shoulder — symmetric
  4. test_drift_detected_label_says_center_buy_but_direction_is_buy_no — direction
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


def test_drift_detected_label_says_center_buy_but_bin_is_shoulder():
    """RELATIONSHIP: symmetric drift — labeled center_buy but bin is a
    shoulder. Dispatch rule clause 3 fires → would assign shoulder_sell.
    Mismatch → drift_detected."""
    row = _row(strategy="center_buy", bin_label="75°F+", direction="buy_yes")
    v = detect_attribution_drift(row)
    assert v.kind == "drift_detected"
    assert v.signature.label_strategy == "center_buy"
    assert v.signature.inferred_strategy == "shoulder_sell"
    assert v.signature.bin_topology == "open_shoulder"


def test_drift_detected_label_says_center_buy_but_direction_is_buy_no():
    """RELATIONSHIP: dispatch rule clause 4 only triggers center_buy when
    direction=='buy_yes'. A position labeled center_buy with direction=buy_no
    would have fallen through to clause 5 (opening_inertia). Drift."""
    row = _row(strategy="center_buy", bin_label="50-51°F", direction="buy_no")
    v = detect_attribution_drift(row)
    assert v.kind == "drift_detected"
    assert v.signature.inferred_strategy == "opening_inertia"


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

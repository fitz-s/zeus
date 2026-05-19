# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: /Users/leofitz/Downloads/codereview-may19-2.md P0-2
# Lifecycle: created=2026-05-19; last_reviewed=2026-05-19; last_reused=never
# Purpose: Antibody — executable forecast reader must enforce
#          source-clock causality: source_available_at and captured_at
#          must be <= decision_time. Without these checks, decisions
#          can consume evidence published after the decision boundary
#          (causality leak).
"""Antibody: decision-time causality for executable forecast.

Root cause (codereview-may19-2 P0-2): pre-fix, the executable forecast reader
removed the broken `entry_computed_at > now` clause but added no replacement
"available before decision" check. The remaining causal-order checks only
verified intra-row ordering (capture <= producer <= entry computed). A frozen
decision_time could consume a source_run with source_available_at > decision_time
(forecast published AFTER decision recorded) — evidence from the future.

The clean fix uses TWO clocks that are NOT subject to UPSERT rewriting:
  source_available_at — NWP run publication time, frozen at source-run insert
  captured_at         — Zeus ingest fetch time, frozen at source-run insert
Both are compared against the cycle's `now` (= decision_time.astimezone(UTC)).

Antibody contracts (sed-flip verifiable):
  S1: source_available_at > now → BLOCKED "SOURCE_AVAILABLE_AFTER_DECISION_TIME"
  S2: captured_at > now → BLOCKED "SOURCE_CAPTURED_AFTER_DECISION_TIME"
  S3: both <= now → reader proceeds past the causality gate

Tests are source-text-level because the full reader fixture requires extensive
DB setup; the textual assertions catch sed-flip regression (the lines being
deleted from the file).
"""

from __future__ import annotations

from pathlib import Path


_READER = Path(__file__).resolve().parents[1] / "src" / "data" / "executable_forecast_reader.py"


def _read():
    return _READER.read_text()


def test_s1_source_available_after_decision_time_block_present():
    """S1: SOURCE_AVAILABLE_AFTER_DECISION_TIME block must exist in reader.
    Sed-flip: deleting the `if source_available_at > now: ... ` line → RED."""
    text = _read()
    assert "SOURCE_AVAILABLE_AFTER_DECISION_TIME" in text, (
        "P0-2 FAIL: SOURCE_AVAILABLE_AFTER_DECISION_TIME block code is missing. "
        "Pre-decision causality is no longer enforced for source publication time. "
        "Trades can consume evidence published AFTER decision_time."
    )
    assert "source_available_at > now" in text, (
        "P0-2 FAIL: literal `source_available_at > now` check is missing — "
        "the causality predicate has been removed."
    )


def test_s2_source_captured_after_decision_time_block_present():
    """S2: SOURCE_CAPTURED_AFTER_DECISION_TIME block must exist in reader.
    Sed-flip: deleting the `if captured_at > now: ... ` line → RED."""
    text = _read()
    assert "SOURCE_CAPTURED_AFTER_DECISION_TIME" in text, (
        "P0-2 FAIL: SOURCE_CAPTURED_AFTER_DECISION_TIME block code is missing. "
        "Pre-decision causality is no longer enforced for ingest capture time. "
        "Trades can consume evidence captured AFTER decision_time."
    )
    assert "captured_at > now" in text, (
        "P0-2 FAIL: literal `captured_at > now` check is missing — "
        "the captured-time causality predicate has been removed."
    )


def test_s3_causality_block_uses_existing_now_param():
    """S3: the new checks must use the existing `now = decision_time.astimezone(UTC)`
    binding (line ~617). Avoids cross-clock comparison with writer-side UPSERT
    timestamps (the bug that killed the prior `entry_computed_at > now` clause)."""
    text = _read()
    assert "now = decision_time.astimezone(UTC)" in text, (
        "P0-2 FAIL: `now = decision_time.astimezone(UTC)` derivation is missing — "
        "the new causality checks reference `now` and require that binding."
    )


def test_s4_old_broken_check_stays_absent():
    """S4 (regression guard): the original broken `entry_computed_at > now`
    clause must NOT come back. That cross-clock check (writer UPSERT vs
    consumer cycle frozen `now`) caused universal READINESS_TIMING_ORDER_INVALID
    on 2026-05-19 (decision_log 1116/1117). The replacement uses
    source-clock-frozen `source_available_at` / `captured_at` instead."""
    text = _read()
    # The diff that broke entries removed the `or entry_computed_at > now` from
    # the READINESS_TIMING_ORDER_INVALID predicate. That removal must stay.
    bad_pattern = "or entry_computed_at > now"
    assert bad_pattern not in text, (
        f"P0-2 regression guard FAIL: `{bad_pattern}` was reintroduced. "
        f"Cross-clock comparison (writer UPSERT vs cycle frozen now) will "
        f"again reject every market with READINESS_TIMING_ORDER_INVALID."
    )

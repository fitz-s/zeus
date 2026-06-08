# Lifecycle: created=2026-06-08; last_reviewed=2026-06-08; last_reused=2026-06-08
# Purpose: BLOCKER 5 — a missing persisted current capture must produce a logged reason and single-anchor path, never a silent network fetch inside the q path.
# Reuse: Run with pytest; update if the missing-capture handling or logging contract in the U0R fusion override changes.
# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: BLOCKER 5 — if the persisted CURRENT capture is missing, the q path must NOT
#   silently network-fetch. It must fall back to the single-anchor path (override returns None ->
#   byte-identical) WITH a logged reason, so a missing capture is observable, never papered over.
#   Fitz Constraint #3 (immune system: a missing dependency surfaces as a reason, not silence).
"""BLOCKER 5 — a missing persisted current capture falls back to single-anchor WITH a reason.

When raw_model_forecasts has NO current single_runs rows for this cycle (the download did not
run / failed), the override must NOT network-fetch in the q path. It returns None (the existing
single-anchor posterior runs byte-identically) and logs an explicit reason. This proves the q is
never built from un-persisted network values, even in the degraded case.
"""
from __future__ import annotations

import json
import logging
from datetime import date

import pytest

import src.data.replacement_forecast_materializer as mod
from tests.test_u0r_history_provider_materializer_wiring import (
    _conn,
    _disable_other_layers,
    _enable_fusion,
    _request,
    _reset_override_seams,  # noqa: F401 autouse
    _row,
    _seed_history,
)
from tests.test_u0r_materializer_uses_persisted_current_rows_not_network import CURRENT_MODELS


def test_missing_current_capture_falls_back_to_single_anchor_with_reason(monkeypatch, caplog) -> None:
    _disable_other_layers(monkeypatch)
    _enable_fusion(monkeypatch)
    conn = _conn()
    # Seed HISTORY (previous_runs) but NO current single_runs rows -> the current capture is
    # missing for this cycle.
    _seed_history(conn, decision=date(2026, 6, 7), models=CURRENT_MODELS)

    # A network live_fetch that RAISES if invoked -> proves no silent network-fetch on the
    # missing-capture path.
    def _exploding_fetch(*a, **k):
        raise AssertionError("missing current capture must NOT trigger a network fetch")

    mod._replacement_u0r_fusion_override._live_fetch = _exploding_fetch

    with caplog.at_level(logging.WARNING, logger="zeus.replacement_u0r_fusion"):
        pid = mod._insert_posterior(conn, _request(), metric="high", anchor_id=1)

    # Single-anchor path: no u0r_fusion block in provenance (byte-identical to flag-off).
    prov = json.loads(_row(conn, pid)["provenance_json"])
    assert "u0r_fusion" not in prov, "missing current capture must fall back to single-anchor"

    # The reason is logged (observable, not silent).
    msgs = " ".join(rec.getMessage().lower() for rec in caplog.records)
    assert "current" in msgs and ("missing" in msgs or "capture" in msgs), (
        f"a missing-current-capture reason must be logged; got: {msgs!r}"
    )


def test_byte_identical_to_single_anchor_when_capture_missing(monkeypatch) -> None:
    """The missing-capture fallback must produce the SAME posterior as the flag-off single-anchor
    path (q + identity hash), proving the fallback is byte-identical, not a degraded variant."""
    _disable_other_layers(monkeypatch)

    # Baseline: fusion OFF, single-anchor.
    conn_base = _conn()
    base = _row(conn_base, mod._insert_posterior(conn_base, _request(), metric="high", anchor_id=1))

    # Fusion ON but current capture missing -> must equal the baseline.
    _enable_fusion(monkeypatch)
    conn = _conn()
    _seed_history(conn, decision=date(2026, 6, 7), models=CURRENT_MODELS)
    got = _row(conn, mod._insert_posterior(conn, _request(), metric="high", anchor_id=1))

    assert got["q_json"] == base["q_json"]
    assert got["posterior_identity_hash"] == base["posterior_identity_hash"]
    assert got["posterior_config_hash"] == base["posterior_config_hash"]

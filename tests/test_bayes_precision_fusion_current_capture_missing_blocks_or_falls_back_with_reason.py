# Lifecycle: created=2026-06-08; last_reviewed=2026-06-08; last_reused=2026-06-08
# Purpose: BLOCKER 5 — a missing persisted current capture must produce a logged reason and single-anchor path, never a silent network fetch inside the q path.
# Reuse: Run with pytest; update if the missing-capture handling or logging contract in the BAYES_PRECISION_FUSION fusion override changes.
# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: BLOCKER 5 — if the persisted CURRENT capture is missing, the q path must NOT
#   silently network-fetch. It must fall back to the single-anchor path (override returns None ->
#   byte-identical) WITH a logged reason, so a missing capture is observable, never papered over.
#   Fitz Constraint #3 (immune system: a missing dependency surfaces as a reason, not silence).
"""BLOCKER 5 — a missing persisted BPF extras capture serves current-anchor Normal WITH a reason.

When raw_model_forecasts has NO current single_runs rows for this cycle (the download did not
run / failed), the override must NOT network-fetch in the q path. It serves the current OM9
anchor through the same settlement-preimage Normal + bootstrap-q_lcb carrier used by fused rows,
with explicit ANCHOR_ONLY_CURRENT provenance. This proves the q is never built from un-persisted
network values, while redecision still advances to the latest anchor cycle.
"""
from __future__ import annotations

import json
import logging
from datetime import date

import pytest

import src.config as cfg
import src.data.replacement_forecast_materializer as mod
from tests.test_bayes_precision_fusion_history_provider_materializer_wiring import (
    _conn,
    _disable_other_layers,
    _enable_fusion,
    _request,
    _reset_override_seams,  # noqa: F401 autouse
    _row,
    _seed_history,
)
from tests.test_bayes_precision_fusion_materializer_uses_persisted_current_rows_not_network import CURRENT_MODELS


def test_missing_current_capture_writes_anchor_only_current_with_reason(monkeypatch, caplog) -> None:
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

    mod._replacement_bayes_precision_fusion_override._live_fetch = _exploding_fetch

    with caplog.at_level(logging.WARNING, logger="zeus.replacement_bayes_precision_fusion"):
        pid = mod._insert_posterior(conn, _request(), metric="high", anchor_id=1)

    # Current-anchor path: explicitly live-carried and auditable, not legacy member-vote fallback.
    prov = json.loads(_row(conn, pid)["provenance_json"])
    assert prov["replacement_q_mode"] == "ANCHOR_ONLY_CURRENT"
    assert prov["capture_status"] == "ANCHOR_ONLY_CURRENT"
    assert prov["q_shape"] == "fused_normal_direct"
    assert prov["q_lcb_basis"] == "fused_center_bootstrap_p05"
    assert prov["bayes_precision_fusion"]["method"] == "anchor_only_current"

    # The reason is logged (observable, not silent).
    msgs = " ".join(rec.getMessage().lower() for rec in caplog.records)
    assert "current" in msgs and "anchor_only_current" in msgs and ("missing" in msgs or "capture" in msgs), (
        f"a missing-current-capture reason must be logged; got: {msgs!r}"
    )


def test_anchor_only_current_requires_fusion_on_and_writes_bounded_live_carrier(monkeypatch) -> None:
    """The missing-capture path must not silently degrade to a flag-off single-anchor product.

    Flag-off no longer writes a live posterior. With fusion ON, the same missing-capture case
    should publish a bounded current-anchor posterior so the 12Z cycle can flow into live
    redecision while preserving provenance that BPF extras were unavailable.
    """
    _disable_other_layers(monkeypatch)

    # Baseline: fusion OFF is not a live product.
    monkeypatch.setitem(cfg.settings["edli"], "replacement_0_1_bayes_precision_fusion_enabled", False)
    conn_base = _conn()
    assert mod._insert_posterior(conn_base, _request(), metric="high", anchor_id=1) is None

    # Fusion ON but current capture missing -> distinct live carrier at the same anchor cycle.
    _enable_fusion(monkeypatch)
    conn = _conn()
    _seed_history(conn, decision=date(2026, 6, 7), models=CURRENT_MODELS)
    got = _row(conn, mod._insert_posterior(conn, _request(), metric="high", anchor_id=1))

    got_prov = json.loads(got["provenance_json"])
    assert got_prov["replacement_q_mode"] == "ANCHOR_ONLY_CURRENT"
    assert got["q_lcb_json"] is not None
    assert got["q_ucb_json"] is not None

# Created: 2026-05-28
# Last reused or audited: 2026-05-28
# Authority basis: Operator pre-MC re-audit Blocker B3 (2026-05-28). The selective
#   driver's A-cohort replay-equivalence check (selective_refit_from_manifest.py:155)
#   currently calls `_evaluate_cohort(error_model_source="recompute", ...)` which
#   delegates to `_recompute_error_model` (replay_equivalence_full_transport.py:234)
#   using `_MIN_LIVE_N_RECOMPUTE = 5`. The canonical producer writes rows under
#   `DEFAULT_MIN_LIVE_N = 20`, so the replay does NOT compare against the persisted
#   STAGING row — it compares against a re-fit under a DIFFERENT activeness gate.
#   The A_REUSE_PENDING_REPLAY verdict is therefore not a true domain match.
#
#   Fix: replay must operate in db mode against the persisted STAGING rows, gated by
#   the current gate_set_hash AND per-snapshot target_month — the same canonical-read
#   contract the LIVE reader uses. Per-snapshot because the row coverage is month-
#   scoped; a single per-cohort model load cannot honour that.
"""B3 — replay equivalence must load persisted STAGING rows via the canonical contract."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


def test_run_replay_for_a_cohorts_passes_db_mode_to_evaluate_cohort(monkeypatch, tmp_path):
    """The selective driver must dispatch replay in db mode, not recompute mode.

    Pre-fix: selective_refit_from_manifest.py:155 hardcodes
    `error_model_source="recompute"` → RED.
    """
    from scripts import selective_refit_from_manifest as driver
    from scripts import replay_equivalence_full_transport as harness

    captured: list[dict] = []

    def fake_evaluate_cohort(**kw):
        captured.append(kw)
        return SimpleNamespace(
            city=kw["city_name"], metric=kw["metric"], season=kw["season"],
            n_sampled=1, n_errors=0, max_abs_diff=0.0, pct_argmax_match=100.0,
            mean_brier_delta=0.0, pass_verdict=True, fail_reason="",
        )

    monkeypatch.setattr(harness, "_evaluate_cohort", fake_evaluate_cohort)
    monkeypatch.setattr(harness, "_open_readonly",
                        lambda p: MagicMock(spec=sqlite3.Connection))

    db_path = tmp_path / "staging.db"
    db_path.touch()  # _open_readonly is monkeypatched; existence is enough.

    a_rows = [{"city": "Atlanta", "season": "MAM", "metric": "high"}]
    driver._run_replay_for_a_cohorts(a_rows, db_path, n_per_cohort=5, n_mc=1000)

    assert captured, "_evaluate_cohort was never called"
    for kw in captured:
        assert kw["error_model_source"] == "db", (
            f"replay must run in db mode (persisted STAGING), not {kw['error_model_source']!r}. "
            f"recompute mode uses _MIN_LIVE_N_RECOMPUTE=5 which does not match the producer's "
            f"DEFAULT_MIN_LIVE_N=20 → A-cohort verdict is invalid."
        )
        assert kw["model_db_conn"] is not None, (
            "db mode requires a non-None model_db_conn for the per-snapshot canonical read"
        )


def test_load_error_model_from_db_uses_canonical_contract(monkeypatch):
    """Per-snapshot error model load MUST go through `read_bias_model` with all
    four canonical filters (family, authority='STAGING' for replay, gate_set_hash,
    target_month). Raw SELECT against model_bias_ens without these filters can
    return stale-gate / off-month / VERIFIED-leaked rows.

    Pre-fix: replay_equivalence_full_transport._load_error_model_from_db (line 271)
    does a bare SELECT with only (city, metric, season) ORDER BY recorded_at DESC.
    → RED on filter assertions.
    """
    from scripts import replay_equivalence_full_transport as harness

    spy_calls: list[dict] = []

    def fake_read(conn, **kw):
        spy_calls.append(kw)
        # Return a valid model so reconstruction succeeds.
        return {
            "bias_c": 0.0, "bias_sd_c": 0.5, "residual_sd_c": 1.0,
            "heterogeneity_var_c2": 0.04, "correction_strength": 0.0,
            "effective_bias_c": 0.0, "total_residual_sd_c": 1.0,
        }

    monkeypatch.setattr("src.calibration.ens_bias_repo.read_bias_model", fake_read)
    # The replay harness performs a lazy `from src.calibration.ens_error_model import
    # current_gate_set_hash` INSIDE _load_error_model_from_db, so the monkeypatch must
    # target the source module, not the consumer.
    monkeypatch.setattr(
        "src.calibration.ens_error_model.current_gate_set_hash",
        lambda: "deadc0de",
    )

    conn = MagicMock(spec=sqlite3.Connection)
    # The post-fix signature accepts a target_month; pre-fix it does not, so this
    # call shape itself fails → RED.
    harness._load_error_model_from_db(
        conn, city_name="Atlanta", metric="high", season="MAM", target_month=5,
    )

    assert spy_calls, "read_bias_model was never invoked — replay still uses raw SELECT"
    kw = spy_calls[0]
    # All four canonical-contract filters must be present.
    assert kw.get("error_model_family") == "full_transport_v1", (
        f"missing error_model_family filter: {kw}"
    )
    assert kw.get("authority") == "STAGING", (
        f"replay must read STAGING rows (the rebuild has not promoted yet): {kw}"
    )
    assert kw.get("require_gate_set_hash") == "deadc0de", (
        f"missing gate_set_hash filter (stale-gate rows will leak): {kw}"
    )
    assert kw.get("target_month") == 5, (
        f"target_month must be threaded from the snapshot, not omitted: {kw}"
    )

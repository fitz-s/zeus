# Created: 2026-06-16
# Last reused or audited: 2026-06-19
# Lifecycle: created=2026-06-16; last_reviewed=2026-06-19; last_reused=2026-06-19
# Authority basis: docs/evidence/timing_audit/capture_reactor_stall_rootcause_2026-06-16.md
#   (PRIMARY/CODE fix) + docs/evidence/timing_audit/impl_flat_threshold_capture_fix_2026-06-16.md.
#   BAYES_PRECISION_FUSION_SPEC §6 F1 (the q-path consumes the persisted single_runs capture).
# Purpose: Relationship tests for BPF extras coverage completeness and fixpoint termination.
# Reuse: Run when replacement_forecast_production BPF extras capture, coverage, or cycle selection changes.
"""Coverage-aware BPF extras self-healing gate (_extras_cycle_incomplete) + termination.

These tests pin the 2026-06-16 fix that replaced the coverage-BLIND flat row-count gate
(``COUNT(*) WHERE source_cycle_time=? < 200``) with a per-(city, metric, target_date)
single_runs coverage probe against the SAME plan the fan-out builds from. The flat gate
declared a cycle "complete" once the near-day (lead=0) leg alone exceeded 200 rows, stranding
the still-uncaptured lead+1/lead+2 scopes -> q-path CAPTURE_MISSING -> legacy q_shape.

Proven here:
  (a) a cycle with a FULL near-day leg but MISSING lead+1 scopes is INCOMPLETE (gate re-runs);
  (b) a cycle with ALL planned scopes captured is COMPLETE (gate skips -> terminates);
  (c) an UNSERVABLE-upstream residual does NOT loop forever: once a fan-out pass lands 0 new
      rows while still incomplete, the per-cycle fixpoint latch flips the gate to
      complete-with-gap (terminates), and the latch auto-clears when the cycle advances.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest

import src.data.replacement_forecast_production as prod

UTC = timezone.utc
_CYCLE = datetime(2026, 6, 16, 0, 0, tzinfo=UTC)
_CYCLE_ISO = _CYCLE.isoformat()


# --- minimal fixtures -------------------------------------------------------------------------


@dataclass(frozen=True)
class _PlanRow:
    city: str
    temperature_metric: str
    target_date: str


@dataclass(frozen=True)
class _Plan:
    rows: tuple[_PlanRow, ...]


def _make_forecast_db(tmp_path: Path) -> Path:
    """A forecast_db carrying ONLY the columns the gate's coverage probe reads from
    raw_model_forecasts (city, metric, target_date, source_cycle_time, endpoint)."""
    db = tmp_path / "zeus-forecasts.db"
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            """
            CREATE TABLE raw_model_forecasts (
                raw_model_forecast_id INTEGER PRIMARY KEY AUTOINCREMENT,
                model TEXT NOT NULL,
                city TEXT NOT NULL,
                target_date TEXT NOT NULL,
                metric TEXT NOT NULL,
                source_cycle_time TEXT NOT NULL,
                endpoint TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()
    return db


def _insert_single_runs(db: Path, *, city: str, metric: str, target_date: str, models: list[str]) -> None:
    conn = sqlite3.connect(db)
    try:
        for m in models:
            conn.execute(
                "INSERT INTO raw_model_forecasts (model, city, target_date, metric,"
                " source_cycle_time, endpoint) VALUES (?, ?, ?, ?, ?, 'single_runs')",
                (m, city, target_date, metric, _CYCLE_ISO),
            )
        conn.commit()
    finally:
        conn.close()


# Near-day (lead=0) scope: target_date == cycle date. Six cities -> a "full" near-day leg.
_NEAR_DAY = "2026-06-16"
_LEAD1 = "2026-06-17"
_NEAR_DAY_CITIES = ["Lucknow", "Madrid", "Manila", "Mexico City", "Miami", "Moscow"]
_LEAD1_CITIES = ["Lucknow", "Madrid", "Manila", "Mexico City", "Miami", "Moscow"]
_MODELS = ["ecmwf_ifs", "gfs_global", "icon_global", "jma_seamless"]


def _plan_full_two_leads() -> _Plan:
    rows = [
        _PlanRow(c, "high", _NEAR_DAY) for c in _NEAR_DAY_CITIES
    ] + [
        _PlanRow(c, "high", _LEAD1) for c in _LEAD1_CITIES
    ]
    return _Plan(tuple(rows))


@pytest.fixture
def _redirect_health(tmp_path, monkeypatch):
    """Point the scheduler-health latch read AND write at a tmp file (no real state writes)."""
    health = tmp_path / "scheduler_jobs_health.json"
    monkeypatch.setattr(
        "src.observability.scheduler_health._SCHEDULER_HEALTH_PATH", health, raising=False
    )

    # _extras_fixpoint_latched imports state_path from src.config at call time.
    import src.config as _cfg

    monkeypatch.setattr(
        _cfg, "state_path", lambda name: health if name == "scheduler_jobs_health.json" else _cfg.runtime_state_path(name)
    )
    return health


@pytest.fixture
def _cfg_with_db(tmp_path, monkeypatch):
    db = _make_forecast_db(tmp_path)
    monkeypatch.setattr(prod, "_probe_resolved_available_cycle", lambda: _CYCLE)
    monkeypatch.setattr(
        prod, "_probe_resolved_bayes_precision_fusion_extras_cycle", lambda: _CYCLE
    )
    monkeypatch.setattr(
        "src.data.replacement_forecast_current_target_plan.build_replacement_forecast_current_target_plan",
        lambda *a, **k: _plan_full_two_leads(),
    )
    return {"forecast_db": str(db)}, db


# --- (a) full near-day, missing lead+1 -> INCOMPLETE ------------------------------------------


def test_full_near_day_missing_lead1_is_incomplete(_cfg_with_db, _redirect_health):
    """The exact root-cause scenario, at a scale that DEFEATS the old flat 200-row gate.

    Every near-day scope is captured with a wide model set so the near-day leg alone exceeds
    the old _EXTRAS_COMPLETE_THRESHOLD=200 rows (the leg that wrongly tripped the flat gate to
    'complete'). The lead+1 scopes have NO single_runs row. The coverage-aware gate MUST still
    be incomplete so the fan-out re-runs and fills lead+1 — this is the regression guard: the
    deleted flat gate would have returned False (complete) here and stranded lead+1.
    """
    cfg, db = _cfg_with_db
    many_models = [f"m{i:03d}" for i in range(40)]  # 6 cities × 40 = 240 near-day rows (> 200)
    for c in _NEAR_DAY_CITIES:
        _insert_single_runs(db, city=c, metric="high", target_date=_NEAR_DAY, models=many_models)
    # Sanity: the near-day leg alone is past the old flat floor, yet lead+1 is empty.
    conn = sqlite3.connect(db)
    try:
        near_day_rows = conn.execute(
            "SELECT COUNT(*) FROM raw_model_forecasts WHERE source_cycle_time=? AND target_date=?",
            (_CYCLE_ISO, _NEAR_DAY),
        ).fetchone()[0]
    finally:
        conn.close()
    assert near_day_rows > 200, "fixture must exceed the old flat floor to be a real guard"
    # lead+1 entirely uncaptured -> coverage-aware gate is incomplete (flat gate would skip).
    assert prod._extras_cycle_incomplete(cfg, _CYCLE) is True


# --- (b) all planned scopes captured -> COMPLETE (terminates) ---------------------------------


def test_all_planned_scopes_captured_is_complete(_cfg_with_db, _redirect_health):
    cfg, db = _cfg_with_db
    for c in _NEAR_DAY_CITIES:
        _insert_single_runs(db, city=c, metric="high", target_date=_NEAR_DAY, models=_MODELS)
    for c in _LEAD1_CITIES:
        _insert_single_runs(db, city=c, metric="high", target_date=_LEAD1, models=_MODELS)
    assert prod._extras_cycle_incomplete(cfg, _CYCLE) is False


def test_no_planned_scopes_is_complete(_cfg_with_db, _redirect_health, monkeypatch):
    """No open markets -> empty plan -> nothing to capture -> complete (not fail-open True)."""
    cfg, _ = _cfg_with_db
    monkeypatch.setattr(
        "src.data.replacement_forecast_current_target_plan.build_replacement_forecast_current_target_plan",
        lambda *a, **k: _Plan(()),
    )
    assert prod._extras_cycle_incomplete(cfg, _CYCLE) is False


# --- (c) unservable upstream does NOT loop forever (fixpoint terminates) -----------------------


def test_unservable_residual_terminates_via_fixpoint(_cfg_with_db, _redirect_health):
    """lead+1 is permanently unservable for THIS cycle (upstream never publishes it).

    Tick 1: near-day captured, lead+1 missing -> gate INCOMPLETE (correct: try to fill it).
    A fan-out pass then lands 0 NEW rows (nothing servable) -> _record_extras_fixpoint LATCHES.
    Tick 2: still missing, but the latch is set -> gate COMPLETE-WITH-GAP (terminates the loop)
            instead of re-running forever every 5-min tick.
    """
    cfg, db = _cfg_with_db
    for c in _NEAR_DAY_CITIES:
        _insert_single_runs(db, city=c, metric="high", target_date=_NEAR_DAY, models=_MODELS)

    # Tick 1: incomplete (lead+1 absent), no latch yet.
    assert prod._extras_fixpoint_latched(_CYCLE) is False
    assert prod._extras_cycle_incomplete(cfg, _CYCLE) is True

    # The fan-out ran and produced ZERO new rows (lead+1 unservable this cycle) -> latch.
    prod._record_extras_fixpoint(cfg, _CYCLE, written=0)
    assert prod._extras_fixpoint_latched(_CYCLE) is True

    # Tick 2: the gap persists, but the gate now SKIPS (complete-with-gap) -> loop terminates.
    assert prod._extras_cycle_incomplete(cfg, _CYCLE) is False


def test_fixpoint_does_not_suppress_missing_held_position_scope(
    _cfg_with_db, _redirect_health, monkeypatch
):
    """A held-position family must keep healing even after ordinary scopes hit fixpoint."""
    cfg, db = _cfg_with_db
    for c in _NEAR_DAY_CITIES:
        _insert_single_runs(db, city=c, metric="high", target_date=_NEAR_DAY, models=_MODELS)

    prod._record_extras_fixpoint(cfg, _CYCLE, written=0)
    assert prod._extras_fixpoint_latched(_CYCLE) is True
    monkeypatch.setattr(
        prod,
        "_held_position_extras_missing_scopes",
        lambda _cfg, missing: {("Lucknow", "high", _LEAD1)} & set(missing),
    )

    assert prod._extras_cycle_incomplete(cfg, _CYCLE) is True


def test_held_position_missing_scope_uses_extras_tuple_order(tmp_path):
    """Held families are (city,target_date,metric); extras gaps are (city,metric,target_date)."""
    trade_db = tmp_path / "zeus_trades.db"
    conn = sqlite3.connect(trade_db)
    conn.execute(
        """
        CREATE TABLE position_current (
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            shares REAL,
            chain_shares REAL,
            cost_basis_usd REAL,
            size_usd REAL,
            chain_cost_basis_usd REAL,
            chain_state TEXT,
            phase TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO position_current VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("Kuala Lumpur", "2026-06-21", "high", 5.0, 5.0, 0.06, 0.06, 0.06, "synced", "active"),
    )
    conn.commit()
    conn.close()

    missing = {
        ("Kuala Lumpur", "high", "2026-06-21"),
        ("Busan", "high", "2026-06-21"),
    }

    assert prod._held_position_extras_missing_scopes({"trades_db": str(trade_db)}, missing) == {
        ("Kuala Lumpur", "high", "2026-06-21")
    }


def test_progress_unlatches_so_servable_data_keeps_healing(_cfg_with_db, _redirect_health):
    """A latch must NOT freeze a cycle that is still making progress: if a later pass lands new
    rows (written>0), the latch clears and the gate resumes re-running until coverage is full."""
    cfg, db = _cfg_with_db
    for c in _NEAR_DAY_CITIES:
        _insert_single_runs(db, city=c, metric="high", target_date=_NEAR_DAY, models=_MODELS)

    # A zero-progress pass latches...
    prod._record_extras_fixpoint(cfg, _CYCLE, written=0)
    assert prod._extras_fixpoint_latched(_CYCLE) is True

    # ...then lead+1 starts to arrive (a later pass landed rows) -> a progress record un-latches.
    _insert_single_runs(db, city="Lucknow", metric="high", target_date=_LEAD1, models=_MODELS)
    prod._record_extras_fixpoint(cfg, _CYCLE, written=4)
    assert prod._extras_fixpoint_latched(_CYCLE) is False
    # Coverage is still partial (only 1 of 6 lead+1 cities) -> gate re-runs (keeps healing).
    assert prod._extras_cycle_incomplete(cfg, _CYCLE) is True


def test_latch_auto_clears_when_cycle_advances(_cfg_with_db, _redirect_health):
    """The latch is keyed on the cycle ISO: a NEWER cycle is never blocked by an older cycle's
    unservable-gap latch (cross-cycle termination bound B — complete-with-gap is C-scoped)."""
    cfg, db = _cfg_with_db
    for c in _NEAR_DAY_CITIES:
        _insert_single_runs(db, city=c, metric="high", target_date=_NEAR_DAY, models=_MODELS)
    prod._record_extras_fixpoint(cfg, _CYCLE, written=0)
    assert prod._extras_fixpoint_latched(_CYCLE) is True

    newer = datetime(2026, 6, 16, 6, 0, tzinfo=UTC)  # next 6h cycle
    assert prod._extras_fixpoint_latched(newer) is False


def test_probe_error_fails_open(_cfg_with_db, _redirect_health, monkeypatch):
    """Any coverage-probe error -> the gate fails OPEN (run the extras), never silently skips."""
    cfg, _ = _cfg_with_db

    def _boom(*a, **k):
        raise RuntimeError("plan build exploded")

    monkeypatch.setattr(
        "src.data.replacement_forecast_current_target_plan.build_replacement_forecast_current_target_plan",
        _boom,
    )
    assert prod._extras_cycle_incomplete(cfg, _CYCLE) is True


def test_failsoft_skip_does_not_latch(_cfg_with_db, _redirect_health):
    """A TRANSIENT fan-out fail-soft (no rows, no progress) must NOT be mistaken for an
    unservable fixpoint. _record_extras_fixpoint latches on written==0+incomplete, but the
    CALL SITE only records it on the DOWNLOADED status — a FAILSOFT_SKIPPED carries no
    written_row_count and is transient, so the call site must skip the record entirely.

    This test pins the call-site contract directly: a FAILSOFT status -> no latch written ->
    the gate keeps re-running (self-healing), distinguishing transient error from unservable.
    """
    cfg, db = _cfg_with_db
    for c in _NEAR_DAY_CITIES:
        _insert_single_runs(db, city=c, metric="high", target_date=_NEAR_DAY, models=_MODELS)
    # Simulate the call-site decision for a FAILSOFT report: the guard
    #   `_bpf_status == "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED"`
    # means _record_extras_fixpoint is NOT invoked, so no latch is written.
    assert prod._extras_fixpoint_latched(_CYCLE) is False
    # The gate stays incomplete (lead+1 absent, no latch) -> keeps healing on the next tick.
    assert prod._extras_cycle_incomplete(cfg, _CYCLE) is True
    # Contrast: a DOWNLOADED-status zero-progress pass DOES latch (the unservable case).
    prod._record_extras_fixpoint(cfg, _CYCLE, written=0)
    assert prod._extras_fixpoint_latched(_CYCLE) is True


def test_unresolved_extras_probe_marks_capture_health_failed(_cfg_with_db, _redirect_health):
    cfg, _ = _cfg_with_db

    prod._record_bayes_precision_fusion_capture_health(
        cfg,
        {"status": "BAYES_PRECISION_FUSION_EXTRA_CYCLE_PROBE_UNRESOLVED_SKIP"},
    )

    health = json.loads(_redirect_health.read_text())
    capture = health["bayes_precision_fusion_capture"]
    assert capture["status"] == "FAILED"
    assert capture["last_failure_reason"] == "BAYES_PRECISION_FUSION_EXTRA_CYCLE_PROBE_UNRESOLVED_SKIP"


def test_downloaded_extras_records_fixpoint_and_success_health(_cfg_with_db, _redirect_health):
    cfg, db = _cfg_with_db
    for c in _NEAR_DAY_CITIES:
        _insert_single_runs(db, city=c, metric="high", target_date=_NEAR_DAY, models=_MODELS)

    prod._record_bayes_precision_fusion_capture_health(
        cfg,
        {
            "status": "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED",
            "cycle": _CYCLE_ISO,
            "written_row_count": 0,
            "global_models_unavailable": [],
        },
    )

    health = json.loads(_redirect_health.read_text())
    capture = health["bayes_precision_fusion_capture"]
    assert capture["status"] == "OK"
    assert capture["business_liveness"] == {
        "extras_fixpoint_cycle": _CYCLE_ISO,
        "extras_fixpoint_latched": True,
    }


# --- end-to-end through the real poll call site -----------------------------------------------


def _wire_poll(monkeypatch, tmp_path, *, download_report):
    """Drive _replacement_cycle_availability_poll_if_needed past the leg-fetch (made a no-op:
    holdings already current) into the extras block, with the BPF capture flag ON, the plan
    injected, and the BPF downloader returning `download_report`. Returns the cfg used."""
    import src.config as _cfg
    import src.data.replacement_cycle_availability as rca
    import src.data.bayes_precision_fusion_download as dl_mod

    db = _make_forecast_db(tmp_path)
    # Leg-fetch no-op: the anchor is already held at _CYCLE so fetch_*_cycle resolves to None
    # (branch A False) and the extras decision falls to branch B (the coverage gate).
    monkeypatch.setattr(rca, "probe_anchor_available_any", lambda c, **k: c <= _CYCLE)
    monkeypatch.setattr(rca, "probe_openmeteo_single_run_available", lambda c, **k: c <= _CYCLE)
    monkeypatch.setattr(prod, "_per_leg_downloaded_cycle", lambda d, sid: _CYCLE)
    monkeypatch.setattr(prod, "_probe_resolved_available_cycle", lambda: _CYCLE)
    monkeypatch.setattr(
        prod, "_probe_resolved_bayes_precision_fusion_extras_cycle", lambda: _CYCLE
    )
    monkeypatch.setattr(
        "src.data.replacement_forecast_current_target_plan.build_replacement_forecast_current_target_plan",
        lambda *a, **k: _plan_full_two_leads(),
    )
    monkeypatch.setitem(_cfg.settings["edli"], "replacement_0_1_bayes_precision_fusion_capture_enabled", True)
    monkeypatch.setattr(
        dl_mod, "download_bayes_precision_fusion_extra_raw_inputs", lambda **k: dict(download_report)
    )
    # near-day captured, lead+1 absent -> coverage incomplete this cycle.
    for c in _NEAR_DAY_CITIES:
        _insert_single_runs(db, city=c, metric="high", target_date=_NEAR_DAY, models=_MODELS)
    cfg = {
        "download_current_targets_enabled": True,
        "forecast_db": db,
        "download_output_dir": tmp_path,
        "download_release_lag_hours": 14.0,
    }
    return cfg


def test_callsite_downloaded_zero_progress_latches_then_skips(tmp_path, monkeypatch, _redirect_health):
    """End-to-end: a DOWNLOADED pass that writes 0 rows while still incomplete -> the poll
    latches the fixpoint -> a SECOND poll skips the fan-out (complete-with-gap). Proves the loop
    terminates through the real wiring, not just the helper in isolation."""
    cfg = _wire_poll(
        monkeypatch, tmp_path,
        download_report={"status": "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED", "written_row_count": 0},
    )
    # Tick 1: incomplete -> fan-out runs -> 0 written -> latched.
    r1 = prod._replacement_cycle_availability_poll_if_needed(cfg)
    assert r1["bayes_precision_fusion_extras_status"] == "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED"
    assert prod._extras_fixpoint_latched(_CYCLE) is True
    # Tick 2: still incomplete, but latched -> the gate SKIPS the fan-out (loop terminated).
    r2 = prod._replacement_cycle_availability_poll_if_needed(cfg)
    assert r2["bayes_precision_fusion_extras_status"] == "EXTRAS_CURRENT_CYCLE_COMPLETE_SKIPPED"


def test_callsite_failsoft_does_not_latch(tmp_path, monkeypatch, _redirect_health):
    """End-to-end: a fail-soft fan-out (transient) must NOT latch, so the next poll re-runs."""
    cfg = _wire_poll(
        monkeypatch, tmp_path,
        download_report={"status": "BAYES_PRECISION_FUSION_EXTRA_CAPTURE_FAILSOFT_SKIPPED", "error": "boom"},
    )
    r1 = prod._replacement_cycle_availability_poll_if_needed(cfg)
    assert r1["bayes_precision_fusion_extras_status"] == "BAYES_PRECISION_FUSION_EXTRA_CAPTURE_FAILSOFT_SKIPPED"
    # No latch written (transient) -> the next tick still re-runs the fan-out (self-healing).
    assert prod._extras_fixpoint_latched(_CYCLE) is False
    r2 = prod._replacement_cycle_availability_poll_if_needed(cfg)
    assert r2["bayes_precision_fusion_extras_status"] == "BAYES_PRECISION_FUSION_EXTRA_CAPTURE_FAILSOFT_SKIPPED"

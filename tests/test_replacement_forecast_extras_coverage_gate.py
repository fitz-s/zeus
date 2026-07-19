# Created: 2026-06-16
# Last reused or audited: 2026-07-19
# Lifecycle: created=2026-06-16; last_reviewed=2026-07-19; last_reused=2026-07-19
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
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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


def test_source_cycle_full_local_day_geometry_is_timezone_aware() -> None:
    cycle = datetime(2026, 7, 18, 0, 0, tzinfo=UTC)

    assert prod._source_cycle_can_cover_full_local_day(
        cycle=cycle,
        target_date="2026-07-18",
        timezone_name="Europe/Paris",
    )
    assert prod._source_cycle_can_cover_full_local_day(
        cycle=cycle,
        target_date="2026-07-18",
        timezone_name="America/New_York",
    )
    assert not prod._source_cycle_can_cover_full_local_day(
        cycle=cycle,
        target_date="2026-07-18",
        timezone_name="Asia/Manila",
    )
    assert not prod._source_cycle_can_cover_full_local_day(
        cycle=cycle,
        target_date="2026-07-18",
        timezone_name="Pacific/Auckland",
    )
    assert prod._source_cycle_can_cover_full_local_day(
        cycle=cycle,
        target_date="2026-07-19",
        timezone_name="Asia/Manila",
    )


def test_source_clock_does_not_retry_structurally_partial_day0(
    tmp_path, monkeypatch
) -> None:
    import src.data.bayes_precision_fusion_download as dl
    import src.data.openmeteo_model_updates as updates
    import src.data.replacement_forecast_current_target_plan as target_plan
    import src.data.replacement_forecast_seed_discovery as seed_discovery
    import src.strategy.live_inference.source_clock_city_weights as city_weights

    cycle = datetime(2026, 7, 18, 0, 0, tzinfo=UTC)

    class _Report:
        updated_sources = ("ecmwf_ifs",)
        affected_cities = ("Manila",)

        def as_dict(self):
            return {
                "updated_sources": list(self.updated_sources),
                "affected_cities": list(self.affected_cities),
            }

    monkeypatch.setitem(
        prod.settings["edli"],
        "replacement_0_1_bayes_precision_fusion_capture_enabled",
        True,
    )
    monkeypatch.setattr(dl, "bayes_precision_fusion_quota_cooldown_seconds", lambda: 0)
    monkeypatch.setattr(
        updates,
        "read_model_updates_jsonl",
        lambda _path: (
            updates.OpenMeteoModelUpdate(
                model="ecmwf_ifs",
                last_run_initialisation_time=cycle,
                last_run_availability_time=cycle,
            ),
        ),
    )
    monkeypatch.setattr(
        target_plan,
        "replacement_forecast_current_target_keys",
        lambda _path: (
            target_plan.ReplacementForecastTargetKey(
                "Manila", "2026-07-18", "high"
            ),
        ),
    )
    monkeypatch.setattr(seed_discovery, "held_position_family_priorities", lambda: {})
    monkeypatch.setattr(
        city_weights,
        "affected_cities_for_source_updates",
        lambda _sources: {"Manila"},
    )
    monkeypatch.setattr(
        dl,
        "download_bayes_precision_fusion_extra_raw_inputs",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("partial Day0 must not consume source-clock quota")
        ),
    )

    report = prod._download_bayes_precision_fusion_source_clock_raw_inputs_if_needed(
        {"forecast_db": str(_make_forecast_db(tmp_path))},
        source_clock_report=_Report(),
        max_wall_clock_seconds=1.0,
    )

    assert report["status"] == "SOURCE_CLOCK_BPF_SCOPED_NO_TARGETS"
    assert report["missing_target_count"] == 1
    assert report["actionable_missing_target_count"] == 0
    assert report["structurally_unservable_target_count"] == 1
    assert report["structurally_unservable_by_source"] == {"ecmwf_ifs": 1}


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


def test_retryable_transport_extras_marks_capture_health_failed(_cfg_with_db, _redirect_health):
    cfg, _ = _cfg_with_db

    prod._record_bayes_precision_fusion_capture_health(
        cfg,
        {
            "status": "BAYES_PRECISION_FUSION_EXTRA_TRANSPORT_RETRYABLE",
            "transport_errors": ["single_runs:Paris:2026-06-25:connection timed out"],
        },
    )

    health = json.loads(_redirect_health.read_text())
    capture = health["bayes_precision_fusion_capture"]
    assert capture["status"] == "FAILED"
    assert capture["last_failure_reason"] == "BAYES_PRECISION_FUSION_EXTRA_TRANSPORT_RETRYABLE"


def test_quota_transport_extras_marks_capture_health_degraded(_cfg_with_db, _redirect_health):
    cfg, _ = _cfg_with_db

    prod._record_bayes_precision_fusion_capture_health(
        cfg,
        {
            "status": "BAYES_PRECISION_FUSION_EXTRA_TRANSPORT_RETRYABLE",
            "transport_errors": ["single_runs:Paris:2026-06-25:Open-Meteo quota exhausted"],
            "cooldown_seconds": 311,
        },
    )

    health = json.loads(_redirect_health.read_text())
    capture = health["bayes_precision_fusion_capture"]
    assert capture["status"] == "SKIPPED"
    assert capture["last_skip_reason"] == "BAYES_PRECISION_FUSION_EXTRA_TRANSPORT_RETRYABLE"
    assert capture["business_liveness"] == {
        "transport_degraded": True,
        "transport_degradation_reason": "BAYES_PRECISION_FUSION_EXTRA_TRANSPORT_RETRYABLE",
        "quota_cooldown_seconds": 311,
    }


def test_quota_cooldown_extras_marks_capture_health_degraded(_cfg_with_db, _redirect_health):
    cfg, _ = _cfg_with_db

    prod._record_bayes_precision_fusion_capture_health(
        cfg,
        {
            "status": "BAYES_PRECISION_FUSION_EXTRA_QUOTA_COOLDOWN_SKIPPED",
            "cooldown_seconds": 241,
        },
    )

    health = json.loads(_redirect_health.read_text())
    capture = health["bayes_precision_fusion_capture"]
    assert capture["status"] == "SKIPPED"
    assert capture["last_skip_reason"] == "BAYES_PRECISION_FUSION_EXTRA_QUOTA_COOLDOWN_SKIPPED"
    assert capture["business_liveness"] == {
        "transport_degraded": True,
        "transport_degradation_reason": "BAYES_PRECISION_FUSION_EXTRA_QUOTA_COOLDOWN_SKIPPED",
        "quota_cooldown_seconds": 241,
    }


def test_source_clock_scoped_capture_skips_heavy_fanout_during_quota_cooldown(
    tmp_path, monkeypatch
) -> None:
    """A source-clock poll inside Open-Meteo cooldown must not re-run the full target fan-out."""

    import src.data.bayes_precision_fusion_download as dl

    class _Report:
        updated_sources = ("ecmwf_ifs",)
        affected_cities = ("Amsterdam",)

        def as_dict(self):
            return {
                "updated_sources": list(self.updated_sources),
                "affected_cities": list(self.affected_cities),
            }

    monkeypatch.setitem(
        prod.settings["edli"],
        "replacement_0_1_bayes_precision_fusion_capture_enabled",
        True,
    )
    monkeypatch.setattr(dl, "bayes_precision_fusion_quota_cooldown_seconds", lambda: 241)
    monkeypatch.setattr(
        dl,
        "download_bayes_precision_fusion_extra_raw_inputs",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("cooldown should skip scoped BPF fan-out")
        ),
    )

    report = prod._download_bayes_precision_fusion_source_clock_raw_inputs_if_needed(
        {"forecast_db": str(tmp_path / "zeus-forecasts.db")},
        source_clock_report=_Report(),
        max_wall_clock_seconds=1.0,
    )

    assert report == {
        "status": "SOURCE_CLOCK_BPF_SCOPED_QUOTA_COOLDOWN_SKIPPED",
        "updated_sources": ("ecmwf_ifs",),
        "affected_cities": ("Amsterdam",),
        "cooldown_seconds": 241,
    }


def test_source_clock_scoped_capture_refuses_unresolved_source_cycle(
    tmp_path, monkeypatch
) -> None:
    import src.data.bayes_precision_fusion_download as dl
    import src.data.openmeteo_model_updates as updates

    class _Report:
        updated_sources = ("ecmwf_ifs",)
        affected_cities = ("Amsterdam",)

        def as_dict(self):
            return {
                "updated_sources": list(self.updated_sources),
                "affected_cities": list(self.affected_cities),
                "model_updates_path": str(tmp_path / "missing-updates.jsonl"),
            }

    monkeypatch.setitem(
        prod.settings["edli"],
        "replacement_0_1_bayes_precision_fusion_capture_enabled",
        True,
    )
    monkeypatch.setattr(dl, "bayes_precision_fusion_quota_cooldown_seconds", lambda: 0)
    monkeypatch.setattr(updates, "read_model_updates_jsonl", lambda _path: ())
    monkeypatch.setattr(
        dl,
        "download_bayes_precision_fusion_extra_raw_inputs",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("an unresolved source cycle must never be guessed")
        ),
    )

    report = prod._download_bayes_precision_fusion_source_clock_raw_inputs_if_needed(
        {"forecast_db": str(tmp_path / "zeus-forecasts.db")},
        source_clock_report=_Report(),
        max_wall_clock_seconds=1.0,
    )

    assert report == {
        "status": "SOURCE_CLOCK_BPF_SCOPED_CYCLE_UNRESOLVED_SKIP",
        "updated_sources": ("ecmwf_ifs",),
        "affected_cities": ("Amsterdam",),
        "unresolved_sources": ("ecmwf_ifs",),
    }


def test_source_clock_scoped_capture_prioritizes_held_families(
    tmp_path, monkeypatch
) -> None:
    import src.data.bayes_precision_fusion_download as dl
    import src.data.openmeteo_model_updates as updates
    import src.data.replacement_forecast_current_target_plan as target_plan
    import src.data.replacement_forecast_seed_discovery as seed_discovery

    class _Report:
        updated_sources = ("ecmwf_ifs",)
        affected_cities = ("Paris", "Seoul")

        def as_dict(self):
            return {
                "updated_sources": list(self.updated_sources),
                "affected_cities": list(self.affected_cities),
            }

    keys = (
        target_plan.ReplacementForecastTargetKey("Paris", "2026-07-16", "high"),
        target_plan.ReplacementForecastTargetKey("Seoul", "2026-07-17", "high"),
        target_plan.ReplacementForecastTargetKey("Seoul", "2026-07-16", "high"),
    )
    seen: list[tuple[str, str, str]] = []
    priority_active = [False]

    class _PriorityLane:
        def __enter__(self):
            priority_active[0] = True

        def __exit__(self, *_exc):
            priority_active[0] = False

    monkeypatch.setitem(
        prod.settings["edli"],
        "replacement_0_1_bayes_precision_fusion_capture_enabled",
        True,
    )
    monkeypatch.setattr(dl, "bayes_precision_fusion_quota_cooldown_seconds", lambda: 0)
    monkeypatch.setattr(
        dl,
        "bayes_precision_fusion_source_clock_quota_priority",
        _PriorityLane,
    )
    monkeypatch.setattr(
        updates,
        "read_model_updates_jsonl",
        lambda _path: (
            updates.OpenMeteoModelUpdate(
                model="ecmwf_ifs",
                last_run_initialisation_time=_CYCLE,
                last_run_availability_time=_CYCLE,
            ),
        ),
    )
    monkeypatch.setattr(
        target_plan,
        "replacement_forecast_current_target_keys",
        lambda _path: keys,
    )
    monkeypatch.setattr(
        seed_discovery,
        "held_position_family_priorities",
        lambda: {("Seoul", "2026-07-17", "high"): 0},
    )
    def _download(**kwargs):
        assert priority_active[0] is True
        seen.extend(
            (target.city, target.target_date, target.metric)
            for target in kwargs["targets"]
        )
        return {
            "status": "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED",
            "written_row_count": 1,
        }

    monkeypatch.setattr(dl, "download_bayes_precision_fusion_extra_raw_inputs", _download)

    report = prod._download_bayes_precision_fusion_source_clock_raw_inputs_if_needed(
        {
            "forecast_db": str(tmp_path / "zeus-forecasts.db"),
            "source_clock_fanout_workers": 4,
        },
        source_clock_report=_Report(),
        max_wall_clock_seconds=5.0,
    )

    assert seen == [
        ("Seoul", "2026-07-17", "high"),
        ("Seoul", "2026-07-16", "high"),
        ("Paris", "2026-07-16", "high"),
    ]
    assert report["priority_probe_families"] == (
        ("Seoul", "2026-07-17"),
        ("Seoul", "2026-07-16"),
    )
    assert report["status"] == (
        "SOURCE_CLOCK_SCOPED_BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED"
    )


def test_source_clock_scoped_capture_batches_city_dates_into_priority_request(
    tmp_path, monkeypatch
) -> None:
    import src.data.bayes_precision_fusion_download as dl
    import src.data.openmeteo_model_updates as updates
    import src.data.replacement_forecast_current_target_plan as target_plan
    import src.data.replacement_forecast_seed_discovery as seed_discovery
    import src.strategy.live_inference.source_clock_city_weights as city_weights

    class _Report:
        updated_sources = ("ecmwf_ifs",)
        affected_cities = ("Amsterdam", "London", "Paris", "Seoul")

        def as_dict(self):
            return {
                "updated_sources": list(self.updated_sources),
                "affected_cities": list(self.affected_cities),
            }

    keys = tuple(
        target_plan.ReplacementForecastTargetKey(city, "2026-07-16", metric)
        for city in _Report.affected_cities
        for metric in ("high", "low")
    )
    lock = threading.Lock()
    active = 0
    max_active = 0
    seen: list[tuple[tuple[str, str], ...]] = []

    monkeypatch.setitem(
        prod.settings["edli"],
        "replacement_0_1_bayes_precision_fusion_capture_enabled",
        True,
    )
    monkeypatch.setattr(dl, "bayes_precision_fusion_quota_cooldown_seconds", lambda: 0)
    monkeypatch.setattr(
        updates,
        "read_model_updates_jsonl",
        lambda _path: (
            updates.OpenMeteoModelUpdate(
                model="ecmwf_ifs",
                last_run_initialisation_time=_CYCLE,
                last_run_availability_time=_CYCLE,
            ),
        ),
    )
    monkeypatch.setattr(
        target_plan,
        "replacement_forecast_current_target_keys",
        lambda _path: keys,
    )
    monkeypatch.setattr(seed_discovery, "held_position_family_priorities", lambda: {})
    monkeypatch.setattr(
        city_weights,
        "affected_cities_for_source_updates",
        lambda _sources: _Report.affected_cities,
    )

    def _download(**kwargs):
        nonlocal active, max_active
        group = tuple((target.city, target.metric) for target in kwargs["targets"])
        with lock:
            active += 1
            max_active = max(max_active, active)
            seen.append(group)
        time.sleep(0.04)
        with lock:
            active -= 1
        return {
            "status": "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED",
            "target_count": len(kwargs["targets"]),
            "written_row_count": len(kwargs["targets"]),
            "committed_families": tuple(
                sorted(
                    {
                        (target.city, target.target_date, target.metric)
                        for target in kwargs["targets"]
                    }
                )
            ),
            "global_models_expected": 1,
            "global_models_unavailable": [],
        }

    monkeypatch.setattr(dl, "download_bayes_precision_fusion_extra_raw_inputs", _download)

    report = prod._download_bayes_precision_fusion_source_clock_raw_inputs_if_needed(
        {
            "forecast_db": str(tmp_path / "zeus-forecasts.db"),
            "source_clock_fanout_workers": 4,
        },
        source_clock_report=_Report(),
        max_wall_clock_seconds=1.0,
    )

    assert max_active == 1
    assert report["fanout_workers"] == 0
    assert report["priority_probe_source"] == "ecmwf_ifs"
    assert report["priority_probe_families"] == tuple(
        (city, "2026-07-16") for city in _Report.affected_cities
    )
    assert report["target_count"] == 8
    assert report["written_row_count"] == 8
    assert report["global_models_expected"] == 1
    assert report["fanout_errors"] == ()
    assert all({metric for _, metric in group} == {"high", "low"} for group in seen)
    assert report["status"] == (
        "SOURCE_CLOCK_SCOPED_BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED"
    )


def test_source_clock_scoped_capture_caps_priority_location_batch(
    tmp_path, monkeypatch
) -> None:
    import src.data.bayes_precision_fusion_download as dl
    import src.data.openmeteo_model_updates as updates
    import src.data.replacement_forecast_current_target_plan as target_plan
    import src.data.replacement_forecast_seed_discovery as seed_discovery
    import src.strategy.live_inference.source_clock_city_weights as city_weights
    from src.config import cities_by_name

    cities = tuple(sorted(cities_by_name)[:30])

    class _Report:
        updated_sources = ("ecmwf_ifs",)
        affected_cities = cities

        def as_dict(self):
            return {
                "updated_sources": list(self.updated_sources),
                "affected_cities": list(self.affected_cities),
            }

    keys = tuple(
        target_plan.ReplacementForecastTargetKey(city, "2026-07-17", metric)
        for city in cities
        for metric in ("high", "low")
    )
    calls: list[tuple[str, ...]] = []

    monkeypatch.setitem(
        prod.settings["edli"],
        "replacement_0_1_bayes_precision_fusion_capture_enabled",
        True,
    )
    monkeypatch.setattr(dl, "bayes_precision_fusion_quota_cooldown_seconds", lambda: 0)
    monkeypatch.setattr(
        updates,
        "read_model_updates_jsonl",
        lambda _path: (
            updates.OpenMeteoModelUpdate(
                model="ecmwf_ifs",
                last_run_initialisation_time=_CYCLE,
                last_run_availability_time=_CYCLE,
            ),
        ),
    )
    monkeypatch.setattr(
        target_plan,
        "replacement_forecast_current_target_keys",
        lambda _path: keys,
    )
    monkeypatch.setattr(seed_discovery, "held_position_family_priorities", lambda: {})
    monkeypatch.setattr(
        city_weights,
        "affected_cities_for_source_updates",
        lambda _sources: cities,
    )

    def _download(**kwargs):
        target_cities = tuple(dict.fromkeys(target.city for target in kwargs["targets"]))
        calls.append(target_cities)
        return {
            "status": "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED",
            "target_count": len(kwargs["targets"]),
            "written_row_count": len(kwargs["targets"]),
            "global_models_expected": 1,
            "global_models_unavailable": [],
        }

    monkeypatch.setattr(dl, "download_bayes_precision_fusion_extra_raw_inputs", _download)

    report = prod._download_bayes_precision_fusion_source_clock_raw_inputs_if_needed(
        {
            "forecast_db": str(tmp_path / "zeus-forecasts.db"),
            "source_clock_fanout_workers": 4,
        },
        source_clock_report=_Report(),
        max_wall_clock_seconds=1.0,
    )

    assert tuple(map(len, calls)) == (25, 5)
    assert report["priority_probe_families"] == tuple(
        (city, "2026-07-17") for city in cities[:25]
    )
    assert report["target_count"] == 60


def test_source_clock_scoped_capture_interleaves_sources_and_notifies_commits(
    tmp_path, monkeypatch
) -> None:
    import src.data.bayes_precision_fusion_download as dl
    import src.data.openmeteo_model_updates as updates
    import src.data.replacement_forecast_current_target_plan as target_plan
    import src.data.replacement_forecast_seed_discovery as seed_discovery
    import src.strategy.live_inference.source_clock_city_weights as city_weights

    sources = ("ecmwf_ifs", "icon_global")
    cities = ("Amsterdam", "London", "Paris", "Seoul")

    class _Report:
        updated_sources = sources
        affected_cities = cities

        def as_dict(self):
            return {
                "updated_sources": list(self.updated_sources),
                "affected_cities": list(self.affected_cities),
            }

    keys = tuple(
        target_plan.ReplacementForecastTargetKey(city, "2026-07-17", metric)
        for city in cities
        for metric in ("high", "low")
    )
    starts: list[str] = []
    notifications: list[tuple[str, dict[str, object]]] = []
    lock = threading.Lock()
    fanout_started = threading.Event()
    release_priority = threading.Event()
    slow_callback_started = threading.Event()
    priority_callback_started = threading.Event()

    monkeypatch.setitem(
        prod.settings["edli"],
        "replacement_0_1_bayes_precision_fusion_capture_enabled",
        True,
    )
    monkeypatch.setattr(dl, "bayes_precision_fusion_quota_cooldown_seconds", lambda: 0)
    monkeypatch.setattr(
        updates,
        "read_model_updates_jsonl",
        lambda _path: tuple(
            updates.OpenMeteoModelUpdate(
                model=source,
                last_run_initialisation_time=_CYCLE,
                last_run_availability_time=_CYCLE,
            )
            for source in sources
        ),
    )
    monkeypatch.setattr(
        target_plan,
        "replacement_forecast_current_target_keys",
        lambda _path: keys,
    )
    monkeypatch.setattr(seed_discovery, "held_position_family_priorities", lambda: {})
    monkeypatch.setattr(
        city_weights,
        "affected_cities_for_source_updates",
        lambda _sources: set(cities),
    )

    def _download(**kwargs):
        source = tuple(kwargs["models"])[0]
        with lock:
            starts.append(source)
            if len(starts) > 1:
                fanout_started.set()
        if source == "ecmwf_ifs":
            assert release_priority.wait(0.5), (
                "a stalled priority source must not block an independent source commit"
            )
        return {
            "status": "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED",
            "target_count": len(kwargs["targets"]),
            "written_row_count": len(kwargs["targets"]),
            "committed_families": tuple(
                sorted(
                    {
                        (target.city, target.target_date, target.metric)
                        for target in kwargs["targets"]
                    }
                )
            ),
            "global_models_expected": 1,
            "global_models_unavailable": [],
        }

    monkeypatch.setattr(dl, "download_bayes_precision_fusion_extra_raw_inputs", _download)

    def _notify(source, task_report):
        if not notifications:
            assert fanout_started.wait(0.5), (
                "remaining provider I/O must start before the priority materialization callback"
            )
        if source == "icon_global":
            slow_callback_started.set()
            release_priority.set()
            assert priority_callback_started.wait(0.5), (
                "a slow source callback must not block an independent priority callback"
            )
        else:
            assert slow_callback_started.wait(0.5)
            priority_callback_started.set()
        with lock:
            notifications.append((source, dict(task_report)))

    report = prod._download_bayes_precision_fusion_source_clock_raw_inputs_if_needed(
        {
            "forecast_db": str(tmp_path / "zeus-forecasts.db"),
            "source_clock_fanout_workers": 4,
        },
        source_clock_report=_Report(),
        max_wall_clock_seconds=1.0,
        on_source_commit=_notify,
    )

    assert starts[0] == "ecmwf_ifs"
    assert starts[1:] == ["icon_global"]
    assert {source for source, _report in notifications} == set(sources)
    assert all(report["committed_families"] for _source, report in notifications)
    assert all(
        set(report["committed_families"]) <= {
            (city, "2026-07-17", metric)
            for city in cities
            for metric in ("high", "low")
        }
        for _source, report in notifications
    )
    assert report["source_commit_notifications"] == len(notifications)
    assert report["source_commit_notification_errors"] == ()
    assert report["priority_probe_source"] == "ecmwf_ifs"
    assert report["status"] == (
        "SOURCE_CLOCK_SCOPED_BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED"
    )


def test_source_clock_scoped_capture_does_not_wait_past_deadline_for_commit_callback(
    tmp_path, monkeypatch
) -> None:
    import src.data.bayes_precision_fusion_download as dl
    import src.data.openmeteo_model_updates as updates
    import src.data.replacement_forecast_current_target_plan as target_plan
    import src.data.replacement_forecast_seed_discovery as seed_discovery
    import src.strategy.live_inference.source_clock_city_weights as city_weights

    class _Report:
        updated_sources = ("ecmwf_ifs",)
        affected_cities = ("Paris",)

        def as_dict(self):
            return {
                "updated_sources": list(self.updated_sources),
                "affected_cities": list(self.affected_cities),
            }

    key = target_plan.ReplacementForecastTargetKey(
        "Paris", "2026-07-17", "high"
    )
    callback_started = threading.Event()
    release_callback = threading.Event()
    callback_done = threading.Event()

    monkeypatch.setitem(
        prod.settings["edli"],
        "replacement_0_1_bayes_precision_fusion_capture_enabled",
        True,
    )
    monkeypatch.setattr(dl, "bayes_precision_fusion_quota_cooldown_seconds", lambda: 0)
    monkeypatch.setattr(
        updates,
        "read_model_updates_jsonl",
        lambda _path: (
            updates.OpenMeteoModelUpdate(
                model="ecmwf_ifs",
                last_run_initialisation_time=_CYCLE,
                last_run_availability_time=_CYCLE,
            ),
        ),
    )
    monkeypatch.setattr(
        target_plan,
        "replacement_forecast_current_target_keys",
        lambda _path: (key,),
    )
    monkeypatch.setattr(seed_discovery, "held_position_family_priorities", lambda: {})
    monkeypatch.setattr(
        city_weights,
        "affected_cities_for_source_updates",
        lambda _sources: ("Paris",),
    )
    monkeypatch.setattr(
        dl,
        "download_bayes_precision_fusion_extra_raw_inputs",
        lambda **_kwargs: {
            "status": "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED",
            "target_count": 1,
            "written_row_count": 1,
            "committed_families": (("Paris", "2026-07-17", "high"),),
            "global_models_expected": 1,
            "global_models_unavailable": [],
        },
    )

    def _notify(_source, _task_report):
        callback_started.set()
        release_callback.wait(1.0)
        callback_done.set()

    started = time.monotonic()
    report = prod._download_bayes_precision_fusion_source_clock_raw_inputs_if_needed(
        {
            "forecast_db": str(tmp_path / "zeus-forecasts.db"),
            "source_clock_fanout_workers": 1,
        },
        source_clock_report=_Report(),
        max_wall_clock_seconds=0.05,
        on_source_commit=_notify,
    )
    elapsed = time.monotonic() - started

    assert callback_started.is_set()
    assert elapsed < 0.3
    assert report["source_commit_notifications"] == 0
    assert report["source_commit_notifications_pending"] == 1
    assert report["source_commit_notification_errors"] == ()

    release_callback.set()
    assert callback_done.wait(0.5)


def test_source_clock_scoped_capture_reuses_inflight_download_after_deadline(
    tmp_path, monkeypatch
) -> None:
    import src.data.bayes_precision_fusion_download as dl
    import src.data.openmeteo_model_updates as updates
    import src.data.replacement_forecast_current_target_plan as target_plan
    import src.data.replacement_forecast_seed_discovery as seed_discovery
    import src.strategy.live_inference.source_clock_city_weights as city_weights

    class _Report:
        updated_sources = ("ecmwf_ifs",)
        affected_cities = ("Paris",)

        def as_dict(self):
            return {
                "updated_sources": list(self.updated_sources),
                "affected_cities": list(self.affected_cities),
            }

    key = target_plan.ReplacementForecastTargetKey(
        "Paris", "2026-07-17", "high"
    )
    fetch_started = threading.Event()
    release_fetch = threading.Event()
    fetch_done = threading.Event()
    calls = 0

    monkeypatch.setitem(
        prod.settings["edli"],
        "replacement_0_1_bayes_precision_fusion_capture_enabled",
        True,
    )
    monkeypatch.setattr(dl, "bayes_precision_fusion_quota_cooldown_seconds", lambda: 0)
    monkeypatch.setattr(
        updates,
        "read_model_updates_jsonl",
        lambda _path: (
            updates.OpenMeteoModelUpdate(
                model="ecmwf_ifs",
                last_run_initialisation_time=_CYCLE,
                last_run_availability_time=_CYCLE,
            ),
        ),
    )
    monkeypatch.setattr(
        target_plan,
        "replacement_forecast_current_target_keys",
        lambda _path: (key,),
    )
    monkeypatch.setattr(seed_discovery, "held_position_family_priorities", lambda: {})
    monkeypatch.setattr(
        city_weights,
        "affected_cities_for_source_updates",
        lambda _sources: ("Paris",),
    )

    def _download(**_kwargs):
        nonlocal calls
        calls += 1
        fetch_started.set()
        release_fetch.wait(1.0)
        fetch_done.set()
        return {
            "status": "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED",
            "target_count": 1,
            "written_row_count": 1,
            "global_models_expected": 1,
            "global_models_unavailable": [],
        }

    monkeypatch.setattr(dl, "download_bayes_precision_fusion_extra_raw_inputs", _download)
    cfg = {
        "forecast_db": str(tmp_path / "zeus-forecasts.db"),
        "source_clock_fanout_workers": 1,
    }

    started = time.monotonic()
    first = prod._download_bayes_precision_fusion_source_clock_raw_inputs_if_needed(
        cfg,
        source_clock_report=_Report(),
        max_wall_clock_seconds=0.05,
    )
    first_elapsed = time.monotonic() - started
    second = prod._download_bayes_precision_fusion_source_clock_raw_inputs_if_needed(
        cfg,
        source_clock_report=_Report(),
        max_wall_clock_seconds=0.05,
    )

    assert fetch_started.is_set()
    assert first_elapsed < 0.3
    assert first["source_results"]["ecmwf_ifs"]["status"] == (
        "SOURCE_CLOCK_SOURCE_TIMEBOXED_INCOMPLETE"
    )
    assert second["source_results"]["ecmwf_ifs"]["status"] == (
        "SOURCE_CLOCK_SOURCE_TIMEBOXED_INCOMPLETE"
    )
    assert calls == 1

    release_fetch.set()
    assert fetch_done.wait(0.5)


def test_source_clock_scoped_capture_fans_out_only_exact_cycle_gaps(
    tmp_path, monkeypatch
) -> None:
    import src.data.bayes_precision_fusion_download as dl
    import src.data.openmeteo_model_updates as updates
    import src.data.replacement_forecast_current_target_plan as target_plan
    import src.data.replacement_forecast_seed_discovery as seed_discovery
    import src.strategy.live_inference.source_clock_city_weights as city_weights

    db = _make_forecast_db(tmp_path)
    _insert_single_runs(
        db,
        city="Paris",
        metric="high",
        target_date=_LEAD1,
        models=["ecmwf_ifs"],
    )

    class _Report:
        updated_sources = ("ecmwf_ifs",)
        affected_cities = ("Paris", "Seoul")

        def as_dict(self):
            return {
                "updated_sources": list(self.updated_sources),
                "affected_cities": list(self.affected_cities),
            }

    keys = (
        target_plan.ReplacementForecastTargetKey("Paris", _LEAD1, "high"),
        target_plan.ReplacementForecastTargetKey("Seoul", _LEAD1, "high"),
    )
    seen: list[tuple[str, ...]] = []
    monkeypatch.setitem(
        prod.settings["edli"],
        "replacement_0_1_bayes_precision_fusion_capture_enabled",
        True,
    )
    monkeypatch.setattr(dl, "bayes_precision_fusion_quota_cooldown_seconds", lambda: 0)
    monkeypatch.setattr(
        updates,
        "read_model_updates_jsonl",
        lambda _path: (
            updates.OpenMeteoModelUpdate(
                model="ecmwf_ifs",
                last_run_initialisation_time=_CYCLE,
                last_run_availability_time=_CYCLE,
            ),
        ),
    )
    monkeypatch.setattr(
        target_plan,
        "replacement_forecast_current_target_keys",
        lambda _path: keys,
    )
    monkeypatch.setattr(seed_discovery, "held_position_family_priorities", lambda: {})
    monkeypatch.setattr(
        city_weights,
        "affected_cities_for_source_updates",
        lambda _sources: {"Paris", "Seoul"},
    )

    def _download(**kwargs):
        targets = tuple(kwargs["targets"])
        seen.append(tuple(target.city for target in targets))
        return {
            "status": "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED",
            "target_count": len(targets),
            "written_row_count": len(targets),
            "committed_families": tuple(
                (target.city, target.target_date, target.metric)
                for target in targets
            ),
            "global_models_expected": 1,
            "global_models_unavailable": [],
        }

    monkeypatch.setattr(dl, "download_bayes_precision_fusion_extra_raw_inputs", _download)
    report = prod._download_bayes_precision_fusion_source_clock_raw_inputs_if_needed(
        {"forecast_db": str(db)},
        source_clock_report=_Report(),
        max_wall_clock_seconds=1.0,
    )

    assert seen == [("Seoul",)]
    assert report["planned_target_count"] == 2
    assert report["covered_target_count"] == 1
    assert report["missing_target_count"] == 1
    assert report["target_count"] == 1

    _insert_single_runs(
        db,
        city="Seoul",
        metric="high",
        target_date=_LEAD1,
        models=["ecmwf_ifs"],
    )
    monkeypatch.setattr(
        dl,
        "download_bayes_precision_fusion_extra_raw_inputs",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("complete source cycle must not fan out")
        ),
    )
    complete = prod._download_bayes_precision_fusion_source_clock_raw_inputs_if_needed(
        {"forecast_db": str(db)},
        source_clock_report=_Report(),
        max_wall_clock_seconds=1.0,
    )

    assert complete["status"] == "SOURCE_CLOCK_BPF_SCOPED_NO_TARGETS"
    assert complete["planned_target_count"] == 2
    assert complete["covered_target_count"] == 2
    assert complete["missing_target_count"] == 0


def test_source_clock_scoped_capture_isolates_source_cycle_and_cities(
    tmp_path, monkeypatch
) -> None:
    import src.data.bayes_precision_fusion_download as dl
    import src.data.openmeteo_model_updates as updates
    import src.data.replacement_forecast_current_target_plan as target_plan
    import src.data.replacement_forecast_seed_discovery as seed_discovery
    import src.strategy.live_inference.source_clock_city_weights as city_weights

    ecmwf_cycle = datetime(2026, 7, 16, 0, 0, tzinfo=UTC)
    icon_cycle = datetime(2026, 7, 16, 6, 0, tzinfo=UTC)

    class _Report:
        updated_sources = ("ecmwf_ifs", "icon_global")
        affected_cities = ("Paris", "Seoul")

        def as_dict(self):
            return {
                "updated_sources": list(self.updated_sources),
                "affected_cities": list(self.affected_cities),
            }

    keys = (
        target_plan.ReplacementForecastTargetKey("Paris", "2026-07-17", "high"),
        target_plan.ReplacementForecastTargetKey("Seoul", "2026-07-17", "high"),
    )
    seen: dict[str, tuple[datetime, tuple[str, ...]]] = {}

    monkeypatch.setitem(
        prod.settings["edli"],
        "replacement_0_1_bayes_precision_fusion_capture_enabled",
        True,
    )
    monkeypatch.setattr(dl, "bayes_precision_fusion_quota_cooldown_seconds", lambda: 0)
    monkeypatch.setattr(
        updates,
        "read_model_updates_jsonl",
        lambda _path: (
            updates.OpenMeteoModelUpdate(
                model="ecmwf_ifs",
                last_run_initialisation_time=ecmwf_cycle,
                last_run_availability_time=ecmwf_cycle,
            ),
            updates.OpenMeteoModelUpdate(
                model="icon_global",
                last_run_initialisation_time=icon_cycle,
                last_run_availability_time=icon_cycle,
            ),
        ),
    )
    monkeypatch.setattr(
        target_plan,
        "replacement_forecast_current_target_keys",
        lambda _path: keys,
    )
    monkeypatch.setattr(seed_discovery, "held_position_family_priorities", lambda: {})
    monkeypatch.setattr(
        city_weights,
        "affected_cities_for_source_updates",
        lambda sources: {
            "ecmwf_ifs": ("Paris",),
            "icon_global": ("Seoul",),
        }[tuple(sources)[0]],
    )

    def _download(**kwargs):
        source = tuple(kwargs["models"])[0]
        seen[source] = (
            kwargs["cycle"],
            tuple(target.city for target in kwargs["targets"]),
        )
        return {
            "status": (
                "BAYES_PRECISION_FUSION_EXTRA_TRANSPORT_RETRYABLE"
                if source == "icon_global"
                else "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED"
            ),
            "target_count": len(kwargs["targets"]),
            "written_row_count": int(source == "ecmwf_ifs"),
            "transport_errors": (
                ("single_runs:Seoul:rate limited",)
                if source == "icon_global"
                else ()
            ),
        }

    monkeypatch.setattr(dl, "download_bayes_precision_fusion_extra_raw_inputs", _download)

    report = prod._download_bayes_precision_fusion_source_clock_raw_inputs_if_needed(
        {
            "forecast_db": str(tmp_path / "zeus-forecasts.db"),
            "source_clock_fanout_workers": 1,
        },
        source_clock_report=_Report(),
        max_wall_clock_seconds=1.0,
    )

    assert seen == {
        "ecmwf_ifs": (ecmwf_cycle, ("Paris",)),
        "icon_global": (icon_cycle, ("Seoul",)),
    }
    assert report["status"] == (
        "SOURCE_CLOCK_SCOPED_BAYES_PRECISION_FUSION_EXTRA_TRANSPORT_RETRYABLE"
    )
    assert report["source_results"]["ecmwf_ifs"]["status"] == (
        "SOURCE_CLOCK_SOURCE_RAW_INPUTS_DOWNLOADED"
    )
    assert report["source_results"]["icon_global"]["status"] == (
        "SOURCE_CLOCK_SOURCE_TRANSPORT_RETRYABLE"
    )

    monkeypatch.setattr(
        dl,
        "download_bayes_precision_fusion_extra_raw_inputs",
        lambda **_kwargs: {"status": "UNRECOGNIZED_DOWNLOAD_RESULT"},
    )
    unknown = prod._download_bayes_precision_fusion_source_clock_raw_inputs_if_needed(
        {
            "forecast_db": str(tmp_path / "zeus-forecasts.db"),
            "source_clock_fanout_workers": 1,
        },
        source_clock_report=_Report(),
        max_wall_clock_seconds=1.0,
    )

    assert unknown["status"] == "SOURCE_CLOCK_BPF_SCOPED_CAPTURE_FAILSOFT_SKIPPED"
    assert {
        result["status"] for result in unknown["source_results"].values()
    } == {"SOURCE_CLOCK_SOURCE_CAPTURE_FAILSOFT_SKIPPED"}

    monkeypatch.setattr(
        dl,
        "download_bayes_precision_fusion_extra_raw_inputs",
        lambda **kwargs: {
            "status": "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED",
            "global_models_unavailable": list(kwargs["models"]),
        },
    )
    incomplete = prod._download_bayes_precision_fusion_source_clock_raw_inputs_if_needed(
        {
            "forecast_db": str(tmp_path / "zeus-forecasts.db"),
            "source_clock_fanout_workers": 1,
        },
        source_clock_report=_Report(),
        max_wall_clock_seconds=1.0,
    )

    assert incomplete["status"] == (
        "SOURCE_CLOCK_SCOPED_BAYES_PRECISION_FUSION_EXTRA_TRANSPORT_RETRYABLE"
    )
    assert {
        result["status"] for result in incomplete["source_results"].values()
    } == {"SOURCE_CLOCK_SOURCE_TRANSPORT_RETRYABLE"}

    monkeypatch.setattr(
        city_weights,
        "affected_cities_for_source_updates",
        lambda _sources: (),
    )
    monkeypatch.setattr(
        dl,
        "download_bayes_precision_fusion_extra_raw_inputs",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("a source without mapped cities must not fan out")
        ),
    )
    no_targets = prod._download_bayes_precision_fusion_source_clock_raw_inputs_if_needed(
        {"forecast_db": str(tmp_path / "zeus-forecasts.db")},
        source_clock_report=_Report(),
        max_wall_clock_seconds=1.0,
    )

    assert no_targets["status"] == "SOURCE_CLOCK_BPF_SCOPED_NO_TARGETS"


def test_source_clock_scoped_capture_stops_queued_tasks_after_quota_abort(
    tmp_path, monkeypatch
) -> None:
    import src.data.bayes_precision_fusion_download as dl
    import src.data.openmeteo_model_updates as updates
    import src.data.replacement_forecast_current_target_plan as target_plan
    import src.data.replacement_forecast_seed_discovery as seed_discovery
    import src.strategy.live_inference.source_clock_city_weights as city_weights

    class _Report:
        updated_sources = ("ecmwf_ifs", "icon_global")
        affected_cities = ("Paris", "Seoul")

        def as_dict(self):
            return {
                "updated_sources": list(self.updated_sources),
                "affected_cities": list(self.affected_cities),
                "source_runs": {
                    source: {
                        "initialisation_time": _CYCLE.isoformat(),
                        "availability_time": _CYCLE.isoformat(),
                        "update_interval_seconds": 3600,
                    }
                    for source in self.updated_sources
                },
            }

    keys = (
        target_plan.ReplacementForecastTargetKey("Paris", "2026-07-17", "high"),
        target_plan.ReplacementForecastTargetKey("Seoul", "2026-07-17", "high"),
    )
    called: list[str] = []

    monkeypatch.setitem(
        prod.settings["edli"],
        "replacement_0_1_bayes_precision_fusion_capture_enabled",
        True,
    )
    monkeypatch.setattr(dl, "bayes_precision_fusion_quota_cooldown_seconds", lambda: 0)
    monkeypatch.setattr(
        updates,
        "read_model_updates_jsonl",
        lambda _path: tuple(
            updates.OpenMeteoModelUpdate(
                model=source,
                last_run_initialisation_time=_CYCLE,
                last_run_availability_time=_CYCLE,
            )
            for source in _Report.updated_sources
        ),
    )
    monkeypatch.setattr(
        target_plan,
        "replacement_forecast_current_target_keys",
        lambda _path: keys,
    )
    monkeypatch.setattr(seed_discovery, "held_position_family_priorities", lambda: {})
    monkeypatch.setattr(
        city_weights,
        "affected_cities_for_source_updates",
        lambda sources: {
            "ecmwf_ifs": ("Paris",),
            "icon_global": ("Seoul",),
        }[tuple(sources)[0]],
    )

    def _download(**kwargs):
        source = tuple(kwargs["models"])[0]
        called.append(source)
        return {
            "status": "BAYES_PRECISION_FUSION_EXTRA_TRANSPORT_RETRYABLE",
            "target_count": len(kwargs["targets"]),
            "written_row_count": 0,
            "transport_errors": ("single_runs:Paris:429",),
            "transport_aborted_remaining_targets": True,
            "single_runs_request_cycles": {source: _CYCLE.isoformat()},
        }

    monkeypatch.setattr(dl, "download_bayes_precision_fusion_extra_raw_inputs", _download)

    report = prod._download_bayes_precision_fusion_source_clock_raw_inputs_if_needed(
        {
            "forecast_db": str(tmp_path / "zeus-forecasts.db"),
            "source_clock_fanout_workers": 1,
        },
        source_clock_report=_Report(),
        max_wall_clock_seconds=1.0,
    )

    assert called == ["ecmwf_ifs"]
    assert report["transport_aborted_remaining_targets"] is True
    assert report["priority_probe_transport_aborted"] is True
    assert report["source_results"]["icon_global"]["status"] == (
        "SOURCE_CLOCK_SOURCE_TRANSPORT_RETRYABLE"
    )
    assert report["status"] == (
        "SOURCE_CLOCK_SCOPED_BAYES_PRECISION_FUSION_EXTRA_TRANSPORT_RETRYABLE"
    )


def test_source_clock_scoped_capture_terminalizes_deterministic_client_error(
    tmp_path, monkeypatch
) -> None:
    import src.data.bayes_precision_fusion_download as dl
    import src.data.openmeteo_model_updates as updates
    import src.data.replacement_forecast_current_target_plan as target_plan
    import src.data.replacement_forecast_seed_discovery as seed_discovery
    import src.strategy.live_inference.source_clock_city_weights as city_weights

    class _Report:
        updated_sources = ("ukmo_uk_deterministic_2km",)
        affected_cities = ("London",)

        def as_dict(self):
            return {
                "updated_sources": list(self.updated_sources),
                "affected_cities": list(self.affected_cities),
                "source_runs": {
                    "ukmo_uk_deterministic_2km": {
                        "initialisation_time": _CYCLE.isoformat(),
                        "availability_time": _CYCLE.isoformat(),
                        "update_interval_seconds": 3600,
                    }
                },
            }

    monkeypatch.setitem(
        prod.settings["edli"],
        "replacement_0_1_bayes_precision_fusion_capture_enabled",
        True,
    )
    monkeypatch.setattr(dl, "bayes_precision_fusion_quota_cooldown_seconds", lambda: 0)
    monkeypatch.setattr(
        updates,
        "read_model_updates_jsonl",
        lambda _path: (
            updates.OpenMeteoModelUpdate(
                model="ukmo_uk_deterministic_2km",
                last_run_initialisation_time=_CYCLE + timedelta(hours=1),
                last_run_availability_time=_CYCLE + timedelta(hours=1),
                update_interval_seconds=3600,
            ),
        ),
    )
    monkeypatch.setattr(
        target_plan,
        "replacement_forecast_current_target_keys",
        lambda _path: (
            target_plan.ReplacementForecastTargetKey("London", "2026-07-17", "high"),
        ),
    )
    monkeypatch.setattr(seed_discovery, "held_position_family_priorities", lambda: {})
    monkeypatch.setattr(
        city_weights,
        "affected_cities_for_source_updates",
        lambda _sources: ("London",),
    )
    monkeypatch.setattr(
        dl,
        "download_bayes_precision_fusion_extra_raw_inputs",
        lambda **_kwargs: {
            "status": "BAYES_PRECISION_FUSION_EXTRA_TRANSPORT_RETRYABLE",
            "target_count": 1,
            "written_row_count": 0,
            "transport_errors": (
                "single_runs:London:Client error '400 Bad Request' for url 'https://example.invalid'",
            ),
            "transport_outcomes": (
                {
                    "status_code": 400,
                    "retry_class": "terminal",
                    "retry_after_seconds": None,
                    "reason": "http_400",
                    "body_sha256": "deadbeef",
                },
            ),
            "global_models_unavailable": ["ukmo_uk_deterministic_2km"],
            "single_runs_request_cycles": {
                "ukmo_uk_deterministic_2km": _CYCLE.isoformat()
            },
        },
    )

    report = prod._download_bayes_precision_fusion_source_clock_raw_inputs_if_needed(
        {
            "forecast_db": str(tmp_path / "zeus-forecasts.db"),
            "source_clock_fanout_workers": 1,
        },
        source_clock_report=_Report(),
        max_wall_clock_seconds=1.0,
    )

    assert report["status"] == (
        "SOURCE_CLOCK_SCOPED_BAYES_PRECISION_FUSION_EXTRA_PERMANENT_FAILURE"
    )
    result = report["source_results"]["ukmo_uk_deterministic_2km"]
    assert result["status"] == "SOURCE_CLOCK_SOURCE_PERMANENT_FAILURE"
    assert result["cycle"] == _CYCLE.isoformat()
    assert result["permanent_errors"] == result["transport_errors"]
    assert result["permanent_outcomes"] == result["transport_outcomes"]


def test_source_transport_error_terminalization_excludes_ambiguous_statuses() -> None:
    assert prod._source_transport_error_is_nonretryable("Client error '400 Bad Request'")
    assert prod._source_transport_error_is_nonretryable(
        {"status_code": 400, "retry_class": "terminal"},
    )
    assert not prod._source_transport_error_is_nonretryable(
        {"status_code": 400, "retry_class": "conditional"},
    )
    assert prod._source_transport_error_is_nonretryable(
        "Client error '400 Bad Request': invalid parameter models"
    )
    assert prod._source_transport_error_is_nonretryable("status_code=422 invalid request")
    assert prod._source_transport_error_is_nonretryable("Client error '404 Not Found'")
    assert not prod._source_transport_error_is_nonretryable("HTTP 408")
    assert not prod._source_transport_error_is_nonretryable("HTTP 429")
    assert not prod._source_transport_error_is_nonretryable("HTTP 503")
    assert not prod._source_transport_error_is_nonretryable("Server error '503 Unavailable'")
    assert not prod._source_transport_error_is_nonretryable("HTTP/1.1 429")
    assert not prod._source_transport_error_is_nonretryable("connection reset")
    assert not prod._source_transport_error_is_nonretryable(
        "Client error '400 Bad Request'; connection reset"
    )
    assert not prod._source_transport_error_is_nonretryable(
        "batched Client error '400 Bad Request'; fallback HTTP 429"
    )


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
        "trades_db": tmp_path / "empty-zeus-trades.db",
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

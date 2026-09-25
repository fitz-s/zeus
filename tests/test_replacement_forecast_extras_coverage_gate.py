# Created: 2026-06-16
# Last reused or audited: 2026-09-25
# Lifecycle: created=2026-06-16; last_reviewed=2026-09-25; last_reused=2026-09-25
# Authority basis: docs/evidence/timing_audit/capture_reactor_stall_rootcause_2026-06-16.md
#   (PRIMARY/CODE fix) + docs/evidence/timing_audit/impl_flat_threshold_capture_fix_2026-06-16.md;
#   8979df299 (proven-final exact-run gaps remain incomplete when the model has any real miss).
# Audit verdict: CURRENT_REUSABLE — downloader, source-clock wrapper/cursor, and fixpoint contracts
#   checked against current code and the 2026-09-25 end-to-end coverage regression.
#   BAYES_PRECISION_FUSION_SPEC §6 F1 (the q-path consumes the persisted single_runs capture).
# Purpose: Relationship tests for causal BPF capture coverage and retry admission.
# Reuse: Run when replacement_forecast_production BPF extras capture, coverage, or cycle selection changes.
"""Coverage-aware BPF extras self-healing gate (_extras_cycle_incomplete) + termination.

These tests pin the 2026-06-16 fix that replaced the coverage-BLIND flat row-count gate
(``COUNT(*) WHERE source_cycle_time=? < 200``) with a per-(city, metric, target_date)
single_runs coverage probe. Current capture qualification uses metadata-pinned model runs,
causal clocks, physical coverage and the existing coherent-cohort selector.

Proven here:
  (a) a cycle with a FULL near-day leg but MISSING lead+1 scopes is INCOMPLETE (gate re-runs);
  (b) one provider family is partial; two causal, coherent families can complete;
  (c) a zero-write pass cannot permanently suppress later recovery on the same cycle.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

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
        conn.execute(
            "CREATE INDEX idx_raw_model_forecasts_endpoint_family_cycle_members "
            "ON raw_model_forecasts "
            "(endpoint, city, target_date, metric, source_cycle_time, model)"
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


def _current_source_clock_db(tmp_path: Path) -> Path:
    db = tmp_path / "current-source-clock.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            """CREATE TABLE raw_model_forecasts (
                raw_model_forecast_id INTEGER PRIMARY KEY, model TEXT, city TEXT,
                target_date TEXT, metric TEXT, source_cycle_time TEXT,
                source_available_at TEXT, captured_at TEXT, recorded_at TEXT,
                forecast_value_c REAL, lead_days INTEGER, endpoint TEXT,
                coverage_status TEXT
            )"""
        )
    return db


def _current_source_clock_row(
    db: Path, model: str, run: datetime, *, endpoint: str = "single_runs",
    available: datetime | None = None, captured: datetime | None = None,
    recorded: datetime | None = None, coverage: str = "COVERED",
    city: str = "Denver", target_date: str = "2026-09-25",
    metric: str = "high",
) -> None:
    available = available or run + timedelta(minutes=5)
    captured = captured or available + timedelta(minutes=5)
    recorded = recorded or captured + timedelta(minutes=1)
    with sqlite3.connect(db) as conn:
        conn.execute(
            """INSERT INTO raw_model_forecasts
                (model,city,target_date,metric,source_cycle_time,source_available_at,
                 captured_at,recorded_at,forecast_value_c,lead_days,endpoint,coverage_status)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (model,city,target_date,metric,run.isoformat(),available.isoformat(),
             captured.isoformat(),recorded.isoformat(),25.0,2,endpoint,coverage),
        )


def _current_source_clock_metadata(
    monkeypatch, latest: dict[str, datetime], *, ends: dict[str, datetime] | None = None,
) -> None:
    from src.data import openmeteo_model_updates as updates

    monkeypatch.setattr(
        updates, "read_model_updates_jsonl",
        lambda _path: tuple(
            updates.OpenMeteoModelUpdate(
                model=model,
                last_run_initialisation_time=run,
                last_run_availability_time=run + timedelta(minutes=5),
                raw={
                    "last_run_initialisation_time": run.isoformat(),
                    "data_end_time": (ends or {}).get(
                        model, datetime(2026, 9, 26, 12, tzinfo=UTC)
                    ).isoformat(),
                },
            ) for model, run in latest.items()
        ),
    )


def _current_source_clock_missing(
    db: Path, *, decision_time: datetime, target_date: str = "2026-09-25",
    city: str = "Denver",
    cohort_backtrack_candidates: dict | None = None,
) -> set[tuple[str, str, str]]:
    result = prod._extras_coverage_missing(
        {"forecast_db": db},
        datetime(2026, 9, 23, 0, tzinfo=UTC),
        decision_time=decision_time,
        capture_rows=(_PlanRow(city, "high", target_date),),
        held_priority={},
        cohort_backtrack_candidates=cohort_backtrack_candidates,
    )
    assert result is not None and result[1] == 1
    return result[0]


def test_current_source_clock_pair_can_span_distinct_metadata_pinned_runs(
    tmp_path, monkeypatch,
) -> None:
    db = _current_source_clock_db(tmp_path)
    icon = datetime(2026, 9, 23, 6, tzinfo=UTC)
    nbm = datetime(2026, 9, 23, 8, tzinfo=UTC)
    _current_source_clock_metadata(
        monkeypatch, {"icon_global": icon, "ncep_nbm_conus": nbm},
    )
    _current_source_clock_row(db, "icon_global", icon)
    _current_source_clock_row(db, "ncep_nbm_conus", nbm)

    assert _current_source_clock_missing(
        db, decision_time=datetime(2026, 9, 23, 10, tzinfo=UTC),
    ) == set()


def test_metadata_advance_does_not_make_old_coherent_pair_current(
    tmp_path, monkeypatch,
) -> None:
    db = _current_source_clock_db(tmp_path)
    old = datetime(2026, 9, 23, 0, tzinfo=UTC)
    latest = old + timedelta(hours=6)
    _current_source_clock_metadata(
        monkeypatch, {"ecmwf_ifs": latest, "ukmo_global_deterministic_10km": old},
    )
    _current_source_clock_row(db, "ecmwf_ifs", old)
    _current_source_clock_row(db, "ukmo_global_deterministic_10km", old)

    assert _current_source_clock_missing(
        db, decision_time=datetime(2026, 9, 23, 10, tzinfo=UTC),
    ) == {("Denver", "high", "2026-09-25")}


def test_asynchronous_six_hour_pair_is_missing_without_coherent_single_runs(
    tmp_path, monkeypatch,
) -> None:
    db = _current_source_clock_db(tmp_path)
    old = datetime(2026, 9, 23, 0, tzinfo=UTC)
    icon = old + timedelta(hours=6)
    _current_source_clock_metadata(
        monkeypatch, {"icon_global": icon, "ukmo_global_deterministic_10km": old},
    )
    _current_source_clock_row(db, "icon_global", icon)
    _current_source_clock_row(db, "ukmo_global_deterministic_10km", old)
    candidates: dict = {}
    assert _current_source_clock_missing(
        db, decision_time=old + timedelta(hours=10),
        cohort_backtrack_candidates=candidates,
    ) == {("Denver", "high", "2026-09-25")}
    assert candidates == {("Denver", "high", "2026-09-25"): ("icon_global", old)}


def test_latest_center_and_older_single_runs_cohort_both_count(
    tmp_path, monkeypatch,
) -> None:
    db = _current_source_clock_db(tmp_path)
    icon = datetime(2026, 9, 23, 6, tzinfo=UTC)
    ecmwf = icon + timedelta(hours=6)
    _current_source_clock_metadata(
        monkeypatch, {"icon_global": icon, "ecmwf_ifs": ecmwf},
    )
    _current_source_clock_row(db, "icon_global", icon)
    _current_source_clock_row(db, "ecmwf_ifs", ecmwf)
    _current_source_clock_row(db, "ecmwf_ifs", icon)
    assert _current_source_clock_missing(
        db, decision_time=ecmwf + timedelta(hours=1),
    ) == set()


def test_external_metadata_families_cannot_complete_configured_scheme(
    tmp_path, monkeypatch,
) -> None:
    from src.strategy.live_inference import source_clock_city_weights as weights

    db = _current_source_clock_db(tmp_path)
    run = datetime(2026, 9, 23, 0, tzinfo=UTC)
    _current_source_clock_metadata(monkeypatch, {
        model: run for model in (
            "icon_global", "ukmo_global_deterministic_10km",
            "ecmwf_ifs", "ncep_nbm_conus",
        )
    })
    monkeypatch.setattr(
        weights, "scheme_for_city",
        lambda _city, *, metric: SimpleNamespace(weights={
            "icon_global": 0.5, "ukmo_global_deterministic_10km": 0.5,
        }),
    )
    for model in ("ecmwf_ifs", "ncep_nbm_conus", "icon_global"):
        _current_source_clock_row(db, model, run)
    decision = run + timedelta(hours=10)
    assert _current_source_clock_missing(db, decision_time=decision) == {
        ("Denver", "high", "2026-09-25")
    }
    _current_source_clock_row(db, "ukmo_global_deterministic_10km", run)
    assert _current_source_clock_missing(db, decision_time=decision) == set()


@pytest.mark.parametrize("city", ("London", "Milan"))
def test_d2_preferred_source_cannot_hold_d2_fallback_capture_in_debt(
    tmp_path, monkeypatch, city,
) -> None:
    from src.strategy.live_inference import source_clock_city_weights as weights

    db = _current_source_clock_db(tmp_path)
    run = datetime(2026, 9, 23, 0, tzinfo=UTC)
    decision = run + timedelta(hours=10)
    monkeypatch.setattr(
        weights, "scheme_for_city",
        lambda _city, *, metric: SimpleNamespace(weights={
            "ecmwf_ifs": 0.5, "icon_d2": 0.5,
        }),
    )
    _current_source_clock_metadata(monkeypatch, {
        model: run for model in (
            "ecmwf_ifs", "icon_d2", "icon_eu", "ukmo_global_deterministic_10km",
        )
    })
    for model in ("ecmwf_ifs", "icon_eu", "ukmo_global_deterministic_10km"):
        _current_source_clock_row(db, model, run, city=city)
    assert _current_source_clock_missing(db, city=city, decision_time=decision) == set()


def test_preferred_capture_debt_reappears_when_metadata_becomes_requestable(
    tmp_path, monkeypatch,
) -> None:
    from src.strategy.live_inference import source_clock_city_weights as weights

    db = _current_source_clock_db(tmp_path)
    run = datetime(2026, 9, 23, 0, tzinfo=UTC)
    decision = run + timedelta(hours=10)
    monkeypatch.setattr(
        weights, "scheme_for_city",
        lambda _city, *, metric: SimpleNamespace(weights={
            "icon_global": 0.5, "ukmo_global_deterministic_10km": 0.5,
        }),
    )
    _current_source_clock_metadata(monkeypatch, {
        "ecmwf_ifs": run, "ukmo_global_deterministic_10km": run,
    })
    for model in ("ecmwf_ifs", "ukmo_global_deterministic_10km"):
        _current_source_clock_row(db, model, run)
    assert _current_source_clock_missing(db, decision_time=decision) == set()
    _current_source_clock_metadata(monkeypatch, {
        "ecmwf_ifs": run, "icon_global": run,
        "ukmo_global_deterministic_10km": run,
    })
    assert _current_source_clock_missing(db, decision_time=decision) == {
        ("Denver", "high", "2026-09-25")
    }
    _current_source_clock_row(db, "icon_global", run)
    assert _current_source_clock_missing(db, decision_time=decision) == set()


def test_short_metadata_horizon_admits_only_proven_target_backtrack(
    tmp_path, monkeypatch,
) -> None:
    db = _current_source_clock_db(tmp_path)
    midnight = datetime(2026, 9, 23, 0, tzinfo=UTC)
    latest_icon = midnight + timedelta(hours=3)
    _current_source_clock_metadata(
        monkeypatch,
        {"icon_eu": latest_icon, "ukmo_global_deterministic_10km": midnight},
        ends={"icon_eu": datetime(2026, 9, 24, 10, tzinfo=UTC)},
    )
    for model in ("icon_eu", "ukmo_global_deterministic_10km"):
        _current_source_clock_row(db, model, midnight, city="Amsterdam")
    assert _current_source_clock_missing(
        db, city="Amsterdam", decision_time=midnight + timedelta(hours=10),
    ) == set()


def test_day0_partial_physical_capture_does_not_complete_pair(
    tmp_path, monkeypatch,
) -> None:
    db = _current_source_clock_db(tmp_path)
    run = datetime(2026, 9, 23, 0, tzinfo=UTC)
    _current_source_clock_metadata(monkeypatch, {
        "icon_global": run, "ukmo_global_deterministic_10km": run,
    })
    _current_source_clock_row(db, "icon_global", run, target_date="2026-09-23")
    _current_source_clock_row(
        db, "ukmo_global_deterministic_10km", run,
        target_date="2026-09-23", coverage="PARTIAL",
    )
    assert _current_source_clock_missing(
        db, target_date="2026-09-23", decision_time=run + timedelta(hours=10),
    ) == {("Denver", "high", "2026-09-23")}


def test_active_rotation_repairs_cohort_archive_and_keeps_commit_on_normal_failure(
    tmp_path, monkeypatch,
) -> None:
    from src.data import bayes_precision_fusion_download as downloader

    db = _current_source_clock_db(tmp_path)
    old = datetime(2026, 9, 23, 0, tzinfo=UTC)
    latest = old + timedelta(hours=6)
    _current_source_clock_metadata(
        monkeypatch, {"icon_global": latest, "ukmo_global_deterministic_10km": old},
    )
    _current_source_clock_row(db, "icon_global", latest)
    _current_source_clock_row(db, "ukmo_global_deterministic_10km", old)
    monkeypatch.setattr(downloader, "bayes_precision_fusion_quota_cooldown_seconds", lambda: 0)
    calls: list[dict] = []

    def download(**kwargs):
        calls.append(kwargs)
        if kwargs.get("frozen_source_runs"):
            frozen = kwargs["frozen_source_runs"]["icon_global"]
            assert isinstance(frozen, downloader._DerivedOffGridSingleRunsRun)
            assert frozen.run == old
            assert kwargs["models"] == ("icon_global",)
            assert kwargs["include_previous_runs"] is False
            assert kwargs["prune_after"] is False
            assert [(t.city, t.metric, t.target_date) for t in kwargs["targets"]] == [
                ("Denver", "high", "2026-09-25")
            ]
            _current_source_clock_row(db, "icon_global", old)
            return {
                "status": "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED",
                "attempted_target_group_count": 1,
                "written_row_count": 1,
                "committed_families": (("Denver", "2026-09-25", "high"),),
            }
        raise RuntimeError("normal fanout failed after archive commit")

    monkeypatch.setattr(downloader, "download_bayes_precision_fusion_extra_raw_inputs", download)
    cfg = {"forecast_db": db, "bpf_extra_rotation_state_path": tmp_path / "rotation.json"}
    report = prod._download_bayes_precision_fusion_extra_raw_inputs_if_needed(
        cfg, max_wall_clock_seconds=5.0, planning_cycle=old,
        capture_target_scopes=(("Denver", "2026-09-25", "high"),),
    )
    assert len(calls) == 2
    assert report["written_row_count"] == 1
    assert report["committed_families"] == (("Denver", "2026-09-25", "high"),)
    assert report["target_rotation_attempted_group_count"] == 1
    assert report["target_rotation_last_attempted_group"] == ("Denver", "2026-09-25")
    assert _current_source_clock_missing(
        db, decision_time=datetime.now(UTC),
    ) == set()


def test_active_wrapper_unbounded_legacy_call_does_not_compare_none_budget(
    tmp_path, monkeypatch,
) -> None:
    from src.data import bayes_precision_fusion_download as downloader

    db = _current_source_clock_db(tmp_path)
    cycle = datetime(2026, 9, 23, 0, tzinfo=UTC)
    monkeypatch.setattr(downloader, "bayes_precision_fusion_quota_cooldown_seconds", lambda: 0)
    calls: list[dict] = []
    monkeypatch.setattr(
        downloader, "download_bayes_precision_fusion_extra_raw_inputs",
        lambda **kwargs: calls.append(kwargs) or {
            "status": "BAYES_PRECISION_FUSION_EXTRA_TRANSPORT_RETRYABLE",
            "attempted_target_group_count": 1, "written_row_count": 0,
        },
    )
    report = prod._download_bayes_precision_fusion_extra_raw_inputs_if_needed(
        {"forecast_db": db, "bpf_extra_rotation_state_path": tmp_path / "rotation.json"},
        max_wall_clock_seconds=None, planning_cycle=cycle,
        capture_target_scopes=(("Denver", "2026-09-25", "high"),),
    )
    assert report["status"] == "BAYES_PRECISION_FUSION_EXTRA_TRANSPORT_RETRYABLE"
    assert report["target_rotation_attempted_group_count"] == 1
    assert len(calls) == 1


def test_completed_scope_does_not_latch_new_metric_or_metadata_run(
    tmp_path, monkeypatch,
) -> None:
    from src.data import bayes_precision_fusion_download as downloader

    db = _current_source_clock_db(tmp_path)
    old = datetime(2026, 9, 23, 0, tzinfo=UTC)
    later = old + timedelta(hours=6)
    _current_source_clock_metadata(monkeypatch, {
        "icon_global": old, "ukmo_global_deterministic_10km": old,
    })
    for model in ("icon_global", "ukmo_global_deterministic_10km"):
        _current_source_clock_row(db, model, old)
    monkeypatch.setattr(downloader, "bayes_precision_fusion_quota_cooldown_seconds", lambda: 0)
    calls: list[tuple] = []
    monkeypatch.setattr(
        downloader, "download_bayes_precision_fusion_extra_raw_inputs",
        lambda **kwargs: calls.append(tuple(kwargs["targets"])) or {
            "status": "BAYES_PRECISION_FUSION_EXTRA_TRANSPORT_RETRYABLE",
            "attempted_target_group_count": 1, "written_row_count": 0,
        },
    )
    cfg = {"forecast_db": db, "bpf_extra_rotation_state_path": tmp_path / "rotation.json"}
    scopes = (("Denver", "2026-09-25", "high"),)
    report = prod._download_bayes_precision_fusion_extra_raw_inputs_if_needed(
        cfg, max_wall_clock_seconds=5.0, planning_cycle=old,
        capture_target_scopes=scopes,
    )
    assert report["status"] == "BAYES_PRECISION_FUSION_EXTRA_NO_TARGETS"
    assert calls == []

    # The same planning cycle gains a LOW market and a new provider run. Both
    # scopes now need fresh current-center evidence, irrespective of old success.
    _current_source_clock_metadata(monkeypatch, {
        "icon_global": later, "ukmo_global_deterministic_10km": old,
    })
    report = prod._download_bayes_precision_fusion_extra_raw_inputs_if_needed(
        cfg, max_wall_clock_seconds=5.0, planning_cycle=old,
        capture_target_scopes=(*scopes, ("Denver", "2026-09-25", "low")),
    )
    assert report["target_rotation_attempted_group_count"] == 1
    assert len(calls) == 1
    assert {(target.metric, target.target_date) for target in calls[0]} == {
        ("high", "2026-09-25"), ("low", "2026-09-25"),
    }


def test_slow_archive_preserves_normal_current_attempt_within_parent_budget(
    tmp_path, monkeypatch,
) -> None:
    from src.data import bayes_precision_fusion_download as downloader

    db = _current_source_clock_db(tmp_path)
    old = datetime(2026, 9, 23, 0, tzinfo=UTC)
    _current_source_clock_metadata(
        monkeypatch, {"icon_global": old + timedelta(hours=6),
                      "ukmo_global_deterministic_10km": old},
    )
    _current_source_clock_row(db, "icon_global", old + timedelta(hours=6))
    _current_source_clock_row(db, "ukmo_global_deterministic_10km", old)
    monkeypatch.setattr(downloader, "bayes_precision_fusion_quota_cooldown_seconds", lambda: 0)
    clock = [100.0]
    monkeypatch.setattr(prod.time, "monotonic", lambda: clock[0])
    calls: list[str] = []

    def download(**kwargs):
        if kwargs.get("frozen_source_runs"):
            calls.append("archive")
            assert 0 < kwargs["max_wall_clock_seconds"] <= 2.5
            clock[0] += kwargs["max_wall_clock_seconds"]
        else:
            calls.append("current")
            assert kwargs["max_wall_clock_seconds"] >= 2.5
        return {
            "status": "BAYES_PRECISION_FUSION_EXTRA_TRANSPORT_RETRYABLE",
            "attempted_target_group_count": 1, "written_row_count": 0,
        }

    monkeypatch.setattr(downloader, "download_bayes_precision_fusion_extra_raw_inputs", download)
    report = prod._download_bayes_precision_fusion_extra_raw_inputs_if_needed(
        {"forecast_db": db, "bpf_extra_rotation_state_path": tmp_path / "rotation.json"},
        max_wall_clock_seconds=5.0, planning_cycle=old,
        capture_target_scopes=(("Denver", "2026-09-25", "high"),),
    )
    assert calls == ["archive", "current"]
    assert report["target_rotation_attempted_group_count"] == 1
    assert report["committed_families"] == ()


def test_archive_exception_preserves_current_capture_and_commit_wake_evidence(
    tmp_path, monkeypatch,
) -> None:
    from src.data import bayes_precision_fusion_download as downloader

    db = _current_source_clock_db(tmp_path)
    old = datetime(2026, 9, 23, 0, tzinfo=UTC)
    _current_source_clock_metadata(monkeypatch, {
        "icon_global": old + timedelta(hours=6),
        "ukmo_global_deterministic_10km": old,
    })
    _current_source_clock_row(db, "icon_global", old + timedelta(hours=6))
    _current_source_clock_row(db, "ukmo_global_deterministic_10km", old)
    monkeypatch.setattr(downloader, "bayes_precision_fusion_quota_cooldown_seconds", lambda: 0)
    clock = [100.0]
    monkeypatch.setattr(prod.time, "monotonic", lambda: clock[0])
    calls: list[str] = []

    def download(**kwargs):
        if kwargs.get("frozen_source_runs"):
            calls.append("archive")
            clock[0] += kwargs["max_wall_clock_seconds"]
            raise RuntimeError("archive transport rejected")
        calls.append("current")
        assert kwargs["max_wall_clock_seconds"] >= 2.5
        return {
            "status": "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED",
            "attempted_target_group_count": 1, "written_row_count": 1,
            "committed_families": (("Denver", "2026-09-25", "high"),),
        }

    monkeypatch.setattr(downloader, "download_bayes_precision_fusion_extra_raw_inputs", download)
    report = prod._download_bayes_precision_fusion_extra_raw_inputs_if_needed(
        {"forecast_db": db, "bpf_extra_rotation_state_path": tmp_path / "rotation.json"},
        max_wall_clock_seconds=5.0, planning_cycle=old,
        capture_target_scopes=(("Denver", "2026-09-25", "high"),),
    )
    assert calls == ["archive", "current"]
    assert report["status"] == "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED"
    assert report["coherent_archive_capture"] == {
        "status": "EXCEPTION_NO_RECEIPT",
        "error": "archive transport rejected",
        "attempted_target_group_count": 0,
    }
    assert report["written_row_count"] == 1
    assert report["committed_families"] == (("Denver", "2026-09-25", "high"),)
    assert report["target_rotation_attempted_group_count"] == 1


def test_cohort_archive_real_http_parser_commits_only_proven_old_run(
    tmp_path, monkeypatch,
) -> None:
    from src.data import bayes_precision_fusion_download as downloader
    from src.data import openmeteo_client
    from src.state.schema.v2_schema import ensure_replacement_forecast_live_schema

    db = tmp_path / "forecast.db"
    with sqlite3.connect(db) as conn:
        ensure_replacement_forecast_live_schema(conn)
    old = datetime(2026, 9, 23, 0, tzinfo=UTC)
    latest = old + timedelta(hours=6)
    _current_source_clock_metadata(monkeypatch, {
        "icon_global": latest, "ukmo_global_deterministic_10km": old,
    })
    for metric in ("high", "low"):
        _current_source_clock_row(db, "icon_global", latest, metric=metric)
        _current_source_clock_row(db, "ukmo_global_deterministic_10km", old, metric=metric)
    monkeypatch.setattr(downloader, "bayes_precision_fusion_quota_cooldown_seconds", lambda: 0)
    requested_runs: list[str] = []

    def fetch(_url, params, **_kwargs):
        requested_runs.append(params["run"])
        assert params["run"] == "2026-09-23T00:00"
        return {
            "hourly": {
                "time": [f"2026-09-25T{hour:02d}:00" for hour in range(24)],
                "temperature_2m": [20.0 + hour / 10 for hour in range(24)],
            },
            "hourly_units": {"temperature_2m": "C"},
        }

    monkeypatch.setattr(openmeteo_client, "fetch", fetch)
    original_download = downloader.download_bayes_precision_fusion_extra_raw_inputs
    normal_attempts: list[dict] = []

    def download(**kwargs):
        if kwargs.get("frozen_source_runs"):
            return original_download(**kwargs)
        normal_attempts.append(kwargs)
        return {
            "status": "BAYES_PRECISION_FUSION_EXTRA_TIMEBOXED_INCOMPLETE",
            "timeboxed_incomplete": True,
            "attempted_target_group_count": 0, "written_row_count": 0,
        }

    monkeypatch.setattr(downloader, "download_bayes_precision_fusion_extra_raw_inputs", download)
    report = prod._download_bayes_precision_fusion_extra_raw_inputs_if_needed(
        {"forecast_db": db, "bpf_extra_rotation_state_path": tmp_path / "rotation.json"},
        max_wall_clock_seconds=5.0, planning_cycle=old,
        capture_target_scopes=(
            ("Denver", "2026-09-25", "high"),
            ("Denver", "2026-09-25", "low"),
        ),
    )
    assert requested_runs == ["2026-09-23T00:00"]
    assert len(normal_attempts) == 1
    assert report["written_row_count"] == 2
    assert set(map(tuple, report["committed_families"])) == {
        ("Denver", "2026-09-25", metric) for metric in ("high", "low")
    }
    assert report["target_rotation_attempted_group_count"] == 1
    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            "SELECT metric,source_cycle_time,source_available_at,captured_at,coverage_status "
            "FROM raw_model_forecasts WHERE model='icon_global' AND source_cycle_time=?",
            (old.isoformat(),),
        ).fetchall()
    assert {row[0] for row in rows} == {"high", "low"}
    assert all(row[1] == old.isoformat() and row[4] == "COVERED" for row in rows)
    assert all(old <= datetime.fromisoformat(row[2]) <= datetime.fromisoformat(row[3]) for row in rows)


def test_unproved_nonstandard_archive_is_not_scheduled(tmp_path, monkeypatch) -> None:
    db = _current_source_clock_db(tmp_path)
    old = datetime(2026, 9, 23, 0, tzinfo=UTC)
    _current_source_clock_metadata(monkeypatch, {
        "ncep_nbm_conus": old + timedelta(hours=8),
        "ukmo_global_deterministic_10km": old,
    })
    _current_source_clock_row(db, "ncep_nbm_conus", old + timedelta(hours=8))
    _current_source_clock_row(db, "ukmo_global_deterministic_10km", old)
    candidates: dict = {}
    assert _current_source_clock_missing(
        db, decision_time=old + timedelta(hours=10),
        cohort_backtrack_candidates=candidates,
    ) == {("Denver", "high", "2026-09-25")}
    assert candidates == {}


@pytest.mark.parametrize("defect", (
    "previous_only", "unknown_metadata", "future_available", "future_capture",
    "future_recorded", "partial_coverage", "stale_cycle", "same_family",
    "subsecond_future_available", "subsecond_future_capture",
    "subsecond_future_recorded",
))
def test_current_source_clock_gate_rejects_unproved_inputs(
    tmp_path, monkeypatch, defect,
) -> None:
    db = _current_source_clock_db(tmp_path)
    run = datetime(2026, 9, 23, 0, tzinfo=UTC)
    decision = run + timedelta(hours=10)
    models = (
        ("icon_global", "icon_eu")
        if defect == "same_family"
        else ("ecmwf_ifs", "ukmo_global_deterministic_10km")
    )
    _current_source_clock_metadata(
        monkeypatch,
        {model: run for model in models if not (defect == "unknown_metadata" and model == models[1])},
    )
    _current_source_clock_row(db, models[0], run)
    kwargs = {
        "endpoint": "previous_runs" if defect == "previous_only" else "single_runs",
        "coverage": "PARTIAL" if defect == "partial_coverage" else "COVERED",
    }
    if defect == "future_available":
        kwargs["available"] = decision + timedelta(minutes=1)
    elif defect == "future_capture":
        kwargs["captured"] = decision + timedelta(minutes=1)
    elif defect == "future_recorded":
        kwargs["recorded"] = decision + timedelta(minutes=1)
    elif defect == "subsecond_future_available":
        kwargs["available"] = decision + timedelta(milliseconds=500)
        kwargs["captured"] = decision - timedelta(minutes=1)
        kwargs["recorded"] = decision - timedelta(minutes=1)
    elif defect == "subsecond_future_capture":
        kwargs["captured"] = decision + timedelta(milliseconds=500)
        kwargs["recorded"] = decision - timedelta(minutes=1)
    elif defect == "subsecond_future_recorded":
        kwargs["recorded"] = decision + timedelta(milliseconds=500)
    _current_source_clock_row(db, models[1], run, **kwargs)
    if defect == "stale_cycle":
        decision = run + timedelta(hours=31)
    assert _current_source_clock_missing(db, decision_time=decision) == {
        ("Denver", "high", "2026-09-25")
    }


def test_source_cycle_local_decision_window_is_timezone_aware() -> None:
    cycle = datetime(2026, 7, 18, 0, 0, tzinfo=UTC)
    decision_time = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)

    assert prod._source_cycle_can_cover_local_decision_window(
        cycle=cycle,
        target_date="2026-07-18",
        timezone_name="Europe/Paris",
        decision_time=decision_time,
    )
    assert prod._source_cycle_can_cover_local_decision_window(
        cycle=cycle,
        target_date="2026-07-18",
        timezone_name="America/New_York",
        decision_time=decision_time,
    )
    assert prod._source_cycle_can_cover_local_decision_window(
        cycle=cycle,
        target_date="2026-07-18",
        timezone_name="Asia/Manila",
        decision_time=decision_time,
    )
    assert not prod._source_cycle_can_cover_local_decision_window(
        cycle=cycle,
        target_date="2026-07-18",
        timezone_name="Pacific/Auckland",
        decision_time=decision_time,
    )
    assert prod._source_cycle_can_cover_local_decision_window(
        cycle=cycle,
        target_date="2026-07-19",
        timezone_name="Asia/Manila",
        decision_time=decision_time,
    )
    assert not prod._source_cycle_can_cover_local_decision_window(
        cycle=datetime(2026, 7, 18, 18, 0, tzinfo=UTC),
        target_date="2026-07-18",
        timezone_name="America/New_York",
        decision_time=decision_time,
    )


def test_extras_coverage_includes_current_day0_remaining_window(
    tmp_path, monkeypatch
) -> None:
    import src.data.replacement_forecast_current_target_plan as target_plan

    cycle = datetime(2026, 8, 5, 0, tzinfo=UTC)
    decision_time = datetime(2026, 8, 5, 6, tzinfo=UTC)
    db = _make_forecast_db(tmp_path)
    monkeypatch.setattr(
        target_plan,
        "build_replacement_forecast_current_target_plan",
        lambda _path: _Plan(
            rows=(
                _PlanRow("Tokyo", "high", "2026-08-05"),
                _PlanRow("Tokyo", "high", "2026-08-06"),
            )
        ),
    )

    missing, planned = prod._extras_coverage_missing(
        {"forecast_db": db},
        cycle,
        decision_time=decision_time,
    )

    assert planned == 2
    assert missing == {
        ("Tokyo", "high", "2026-08-05"),
        ("Tokyo", "high", "2026-08-06"),
    }


def test_extras_coverage_includes_held_day0_missing_from_market_plan(
    tmp_path, monkeypatch
) -> None:
    import src.data.replacement_forecast_current_target_plan as target_plan
    import src.data.replacement_forecast_seed_discovery as seed_discovery

    cycle = datetime(2026, 7, 18, 0, tzinfo=UTC)
    decision_time = datetime(2026, 7, 18, 6, tzinfo=UTC)
    db = _make_forecast_db(tmp_path)
    monkeypatch.setattr(
        target_plan,
        "build_replacement_forecast_current_target_plan",
        lambda _db: _Plan(
            rows=(
                _PlanRow(
                    city="Tokyo",
                    target_date="2026-07-19",
                    temperature_metric="high",
                ),
            )
        ),
    )
    monkeypatch.setattr(
        seed_discovery,
        "held_position_family_priorities",
        lambda: {("Manila", "2026-07-18", "high"): 0},
    )

    missing, planned = prod._extras_coverage_missing(
        {"forecast_db": db},
        cycle,
        decision_time=decision_time,
    )

    assert planned == 2
    assert missing == {
        ("Manila", "high", "2026-07-18"),
        ("Tokyo", "high", "2026-07-19"),
    }


def test_source_clock_attempts_current_day0_remaining_window(
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
        lambda _path, **_kwargs: (),
    )
    monkeypatch.setattr(
        seed_discovery,
        "held_position_family_priorities",
        lambda: {("Manila", "2026-07-18", "high"): 0},
    )
    monkeypatch.setattr(
        city_weights,
        "affected_cities_for_source_updates",
        lambda _sources: {"Manila"},
    )
    captured: list[object] = []

    def _download(**kwargs):
        captured.extend(kwargs["targets"])
        return {
            "status": "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED",
            "written_row_count": 1,
        }

    monkeypatch.setattr(
        dl,
        "download_bayes_precision_fusion_extra_raw_inputs",
        _download,
    )

    report = prod._download_bayes_precision_fusion_source_clock_raw_inputs_if_needed(
        {"forecast_db": str(_make_forecast_db(tmp_path))},
        source_clock_report=_Report(),
        max_wall_clock_seconds=1.0,
        decision_time=datetime(2026, 7, 18, 6, tzinfo=UTC),
    )

    assert report["status"] == (
        "SOURCE_CLOCK_SCOPED_BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED"
    )
    assert report["missing_target_count"] == 1
    assert report["actionable_missing_target_count"] == 1
    assert report["structurally_unservable_target_count"] == 0
    assert report["structurally_unservable_by_source"] == {"ecmwf_ifs": 0}
    assert [
        (target.city, target.target_date, target.metric)
        for target in captured
    ] == [("Manila", "2026-07-18", "high")]


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
    monkeypatch.setattr(prod, "_probe_resolved_available_cycle", lambda **_kwargs: _CYCLE)
    monkeypatch.setattr(
        prod, "_probe_resolved_bayes_precision_fusion_extras_cycle", lambda **_kwargs: _CYCLE
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


def test_unstamped_planned_scopes_are_not_causal_completion(_cfg_with_db, _redirect_health):
    cfg, db = _cfg_with_db
    for c in _NEAR_DAY_CITIES:
        _insert_single_runs(db, city=c, metric="high", target_date=_NEAR_DAY, models=_MODELS)
    for c in _LEAD1_CITIES:
        _insert_single_runs(db, city=c, metric="high", target_date=_LEAD1, models=_MODELS)
    # These legacy fixture rows lack possession/physical coverage clocks and
    # cannot prove the modern metadata-pinned current-center pair.
    assert prod._extras_cycle_incomplete(cfg, _CYCLE) is True


def test_one_provider_family_does_not_complete_a_scope(
    _cfg_with_db, _redirect_health
):
    cfg, db = _cfg_with_db
    for row in _plan_full_two_leads().rows:
        _insert_single_runs(
            db,
            city=row.city,
            metric=row.temperature_metric,
            target_date=row.target_date,
            models=["icon_global", "icon_eu"],
        )

    assert prod._extras_cycle_incomplete(cfg, _CYCLE) is True


def test_no_planned_scopes_is_complete(_cfg_with_db, _redirect_health, monkeypatch):
    """No open markets -> empty plan -> nothing to capture -> complete (not fail-open True)."""
    cfg, _ = _cfg_with_db
    monkeypatch.setattr(
        "src.data.replacement_forecast_current_target_plan.build_replacement_forecast_current_target_plan",
        lambda *a, **k: _Plan(()),
    )
    assert prod._extras_cycle_incomplete(cfg, _CYCLE) is False


# --- (c) zero writes do not establish permanent unservability -------------------------------


def test_zero_write_does_not_prove_missing_scopes_unservable(_cfg_with_db, _redirect_health):
    """One zero-write pass is diagnostic, not permanent completion of a gap."""
    cfg, db = _cfg_with_db
    for c in _NEAR_DAY_CITIES:
        _insert_single_runs(db, city=c, metric="high", target_date=_NEAR_DAY, models=_MODELS)

    # Tick 1: incomplete (lead+1 absent), no latch yet.
    assert prod._extras_fixpoint_latched(_CYCLE) is False
    assert prod._extras_cycle_incomplete(cfg, _CYCLE) is True

    # A zero write can be transport failure; quota/backoff separately bound retry.
    prod._record_extras_fixpoint(cfg, _CYCLE, written=0)
    assert prod._extras_fixpoint_latched(_CYCLE) is False
    assert prod._extras_cycle_incomplete(cfg, _CYCLE) is True


def test_fixpoint_does_not_suppress_missing_held_position_scope(
    _cfg_with_db, _redirect_health, monkeypatch
):
    """A held-position family must keep healing even after ordinary scopes hit fixpoint."""
    cfg, db = _cfg_with_db
    for c in _NEAR_DAY_CITIES:
        _insert_single_runs(db, city=c, metric="high", target_date=_NEAR_DAY, models=_MODELS)

    prod._record_extras_fixpoint(cfg, _CYCLE, written=0)
    assert prod._extras_fixpoint_latched(_CYCLE) is False
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


def test_progress_keeps_servable_data_healing(_cfg_with_db, _redirect_health):
    """Zero-write diagnosis and subsequent progress both leave missing scopes retryable."""
    cfg, db = _cfg_with_db
    for c in _NEAR_DAY_CITIES:
        _insert_single_runs(db, city=c, metric="high", target_date=_NEAR_DAY, models=_MODELS)

    # A zero-progress observation remains retryable.
    prod._record_extras_fixpoint(cfg, _CYCLE, written=0)
    assert prod._extras_fixpoint_latched(_CYCLE) is False

    # Then lead+1 starts to arrive; the diagnostic records the progress.
    _insert_single_runs(db, city="Lucknow", metric="high", target_date=_LEAD1, models=_MODELS)
    prod._record_extras_fixpoint(cfg, _CYCLE, written=4)
    assert prod._extras_fixpoint_latched(_CYCLE) is False
    # Coverage is still partial (only 1 of 6 lead+1 cities) -> gate re-runs (keeps healing).
    assert prod._extras_cycle_incomplete(cfg, _CYCLE) is True


def test_zero_write_diagnostic_never_blocks_cycle_advance(_cfg_with_db, _redirect_health):
    """Neither the current nor a newer planning cycle inherits zero-write admission."""
    cfg, db = _cfg_with_db
    for c in _NEAR_DAY_CITIES:
        _insert_single_runs(db, city=c, metric="high", target_date=_NEAR_DAY, models=_MODELS)
    prod._record_extras_fixpoint(cfg, _CYCLE, written=0)
    assert prod._extras_fixpoint_latched(_CYCLE) is False

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
    """A transient fail-soft or a zero-write success leaves missing scopes retryable."""
    cfg, db = _cfg_with_db
    for c in _NEAR_DAY_CITIES:
        _insert_single_runs(db, city=c, metric="high", target_date=_NEAR_DAY, models=_MODELS)
    # Simulate the call-site decision for a FAILSOFT report: the guard
    #   `_bpf_status == "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED"`
    # means _record_extras_fixpoint is NOT invoked, so no latch is written.
    assert prod._extras_fixpoint_latched(_CYCLE) is False
    # The gate stays incomplete (lead+1 absent, no latch) -> keeps healing on the next tick.
    assert prod._extras_cycle_incomplete(cfg, _CYCLE) is True
    # A DOWNLOADED-status zero-progress pass is still not unservability proof.
    prod._record_extras_fixpoint(cfg, _CYCLE, written=0)
    assert prod._extras_fixpoint_latched(_CYCLE) is False
    assert prod._extras_cycle_incomplete(cfg, _CYCLE) is True


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


@pytest.mark.parametrize("priority_cooldown", [0, 37])
def test_source_clock_scoped_capture_prioritizes_held_families(
    tmp_path, monkeypatch, priority_cooldown
) -> None:
    import threading

    import src.data.bayes_precision_fusion_download as dl
    import src.data.openmeteo_model_updates as updates
    import src.data.replacement_forecast_current_target_plan as target_plan
    import src.data.replacement_forecast_seed_discovery as seed_discovery
    import src.strategy.live_inference.source_clock_city_weights as city_weights

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
    seen: list[tuple[str, str, str, str]] = []
    active_lane = threading.local()

    class _PriorityLane:
        def __enter__(self):
            active_lane.name = "priority"

        def __exit__(self, *_exc):
            active_lane.name = None

    class _CriticalLane:
        def __enter__(self):
            active_lane.name = "critical"

        def __exit__(self, *_exc):
            active_lane.name = None

    monkeypatch.setitem(
        prod.settings["edli"],
        "replacement_0_1_bayes_precision_fusion_capture_enabled",
        True,
    )
    monkeypatch.setattr(
        dl,
        "bayes_precision_fusion_quota_cooldown_seconds",
        lambda: priority_cooldown,
    )
    monkeypatch.setattr(
        dl,
        "bayes_precision_fusion_held_quota_cooldown_seconds",
        lambda: 0,
    )
    monkeypatch.setattr(
        dl,
        "bayes_precision_fusion_source_clock_quota_priority",
        _PriorityLane,
    )
    monkeypatch.setattr(
        dl,
        "bayes_precision_fusion_held_quota_priority",
        _CriticalLane,
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
        lambda _path, **_kwargs: keys,
    )
    monkeypatch.setattr(
        seed_discovery,
        "held_position_family_priorities",
        lambda: {("Seoul", "2026-07-17", "high"): 0},
    )
    monkeypatch.setattr(
        city_weights,
        "affected_cities_for_source_updates",
        lambda _sources: {"Paris", "Seoul"},
    )
    def _download(**kwargs):
        lane = active_lane.name
        assert lane in {"critical", "priority"}
        seen.extend(
            (target.city, target.target_date, target.metric, lane)
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

    expected_seen = [
        ("Seoul", "2026-07-17", "high", "critical"),
        ("Seoul", "2026-07-16", "high", "critical"),
    ]
    if priority_cooldown == 0:
        expected_seen.append(("Paris", "2026-07-16", "high", "priority"))
    assert seen == expected_seen
    assert report["priority_probe_families"] == (
        ("Seoul", "2026-07-17"),
        ("Seoul", "2026-07-16"),
    )
    assert report["status"] == (
        "SOURCE_CLOCK_SCOPED_BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED"
        if priority_cooldown == 0
        else "SOURCE_CLOCK_SCOPED_BAYES_PRECISION_FUSION_EXTRA_TRANSPORT_RETRYABLE"
    )


def test_source_clock_scoped_capture_drains_held_family_across_sources_before_broad_fanout(
    tmp_path, monkeypatch
) -> None:
    import src.data.bayes_precision_fusion_download as dl
    import src.data.replacement_forecast_current_target_plan as target_plan
    import src.data.replacement_forecast_seed_discovery as seed_discovery
    import src.strategy.live_inference.source_clock_city_weights as city_weights

    sources = ("ecmwf_ifs", "icon_global")

    class _Report:
        updated_sources = sources
        affected_cities = ("Seoul", "Wellington")

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
        target_plan.ReplacementForecastTargetKey(
            "Wellington", "2026-07-17", "high"
        ),
        target_plan.ReplacementForecastTargetKey("Seoul", "2026-07-17", "high"),
    )
    calls: list[tuple[str, str]] = []
    lock = threading.Lock()
    held_sources: set[str] = set()
    all_held_started = threading.Event()
    held_completed: set[str] = set()
    all_held_completed = threading.Event()

    monkeypatch.setitem(
        prod.settings["edli"],
        "replacement_0_1_bayes_precision_fusion_capture_enabled",
        True,
    )
    monkeypatch.setattr(dl, "bayes_precision_fusion_quota_cooldown_seconds", lambda: 0)
    monkeypatch.setattr(
        target_plan,
        "replacement_forecast_current_target_keys",
        lambda _path, **_kwargs: keys,
    )
    monkeypatch.setattr(
        seed_discovery,
        "held_position_family_priorities",
        lambda: {("Wellington", "2026-07-17", "high"): 0},
    )
    monkeypatch.setattr(
        city_weights,
        "affected_cities_for_source_updates",
        lambda _sources: {"Seoul", "Wellington"},
    )

    def _download(**kwargs):
        source = tuple(kwargs["models"])[0]
        cities = tuple(dict.fromkeys(target.city for target in kwargs["targets"]))
        assert len(cities) == 1
        city = cities[0]
        with lock:
            calls.append((source, city))
            if city == "Wellington":
                held_sources.add(source)
                if held_sources == set(sources):
                    all_held_started.set()
        if city == "Wellington":
            assert all_held_started.wait(0.5)
            with lock:
                held_completed.add(source)
                if held_completed == set(sources):
                    all_held_completed.set()
        if city == "Seoul":
            assert all_held_completed.is_set(), (
                "broad source I/O must wait for the held-family tranche to terminate"
            )
        return {
            "status": "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED",
            "target_count": len(kwargs["targets"]),
            "written_row_count": len(kwargs["targets"]),
            "committed_families": tuple(
                (target.city, target.target_date, target.metric)
                for target in kwargs["targets"]
            ),
            "global_models_expected": 1,
            "global_models_unavailable": [],
            "single_runs_request_cycles": {source: _CYCLE.isoformat()},
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

    assert {source for source, city in calls[:2] if city == "Wellington"} == set(sources)
    assert all(city == "Wellington" for _source, city in calls[:2])
    assert all(city == "Seoul" for _source, city in calls[2:])
    assert report["priority_probe_sources"] == sources
    assert report["priority_probe_families"] == (("Wellington", "2026-07-17"),)
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
        lambda _path, **_kwargs: keys,
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
    assert report["committed_families"] == tuple(
        sorted(
            (city, "2026-07-16", metric)
            for city in _Report.affected_cities
            for metric in ("high", "low")
        )
    )
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
        lambda _path, **_kwargs: keys,
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
        lambda _path, **_kwargs: keys,
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
        lambda _path, **_kwargs: (key,),
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
        lambda _path, **_kwargs: (key,),
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
        lambda _path, **_kwargs: keys,
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


def test_source_clock_coverage_probe_reads_only_current_scopes_in_batches(
    tmp_path, monkeypatch
) -> None:
    """Coverage inspection must not materialize a source-cycle's historical rows.

    The source-cycle contains 651 rows, but only 251 current target scopes.  The
    exact-scope read therefore has to return 251 rows and split the candidate
    set across two bounded SQL batches.  Every current scope is already covered,
    so the test also proves this read reduction never sends a completed target
    to the downloader.
    """
    import src.data.bayes_precision_fusion_download as dl
    import src.data.openmeteo_model_updates as updates
    import src.data.replacement_forecast_current_target_plan as target_plan
    import src.data.replacement_forecast_seed_discovery as seed_discovery
    import src.state.db as state_db
    import src.strategy.live_inference.source_clock_city_weights as city_weights

    db = _make_forecast_db(tmp_path)
    current_cities = tuple(f"CurrentScope{index:03d}" for index in range(251))
    irrelevant_cities = tuple(f"HistoricalScope{index:03d}" for index in range(400))
    conn = sqlite3.connect(db)
    try:
        conn.executemany(
            "INSERT INTO raw_model_forecasts (model, city, target_date, metric,"
            " source_cycle_time, endpoint) VALUES ('ecmwf_ifs', ?, ?, 'high', ?, 'single_runs')",
            [
                (city, "2026-07-17", _CYCLE_ISO)
                for city in (*current_cities, *irrelevant_cities)
            ],
        )
        conn.commit()
    finally:
        conn.close()

    class _CoverageReadConnection:
        def __init__(self, path: Path) -> None:
            self._conn = sqlite3.connect(path)
            self.plan_details: list[str] = []
            self.returned_row_count = 0

        def execute(self, query, parameters=()):
            if "raw_model_forecasts" not in query:
                return self._conn.execute(query, parameters)
            self.plan_details.extend(
                str(row[-1])
                for row in self._conn.execute(
                    f"EXPLAIN QUERY PLAN {query}", parameters
                )
            )
            rows = tuple(self._conn.execute(query, parameters))
            self.returned_row_count += len(rows)
            return iter(rows)

        def close(self) -> None:
            self._conn.close()

    coverage_connection = _CoverageReadConnection(db)

    class _Report:
        updated_sources = ("ecmwf_ifs",)
        affected_cities = current_cities

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
                last_run_initialisation_time=_CYCLE,
                last_run_availability_time=_CYCLE,
            ),
        ),
    )
    monkeypatch.setattr(
        target_plan,
        "replacement_forecast_current_target_keys",
        lambda _path, **_kwargs: tuple(
            target_plan.ReplacementForecastTargetKey(
                city, "2026-07-17", "high"
            )
            for city in current_cities
        ),
    )
    monkeypatch.setattr(seed_discovery, "held_position_family_priorities", lambda: {})
    monkeypatch.setattr(
        state_db,
        "_connect_read_only",
        lambda _path: coverage_connection,
    )
    monkeypatch.setattr(
        city_weights,
        "affected_cities_for_source_updates",
        lambda _sources: current_cities,
    )
    monkeypatch.setattr(
        dl,
        "download_bayes_precision_fusion_extra_raw_inputs",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("covered current scopes must not fan out")
        ),
    )

    report = prod._download_bayes_precision_fusion_source_clock_raw_inputs_if_needed(
        {"forecast_db": str(db)},
        source_clock_report=_Report(),
        max_wall_clock_seconds=1.0,
    )

    assert report["status"] == "SOURCE_CLOCK_BPF_SCOPED_NO_TARGETS"
    assert report["planned_target_count"] == 251
    assert report["covered_target_count"] == 251
    assert report["missing_target_count"] == 0
    assert coverage_connection.returned_row_count == 251
    assert coverage_connection.returned_row_count < 651
    assert len(
        [detail for detail in coverage_connection.plan_details if "SEARCH forecast" in detail]
    ) == 2
    assert all(
        "idx_raw_model_forecasts_endpoint_family_cycle_members" in detail
        and "endpoint=? AND city=? AND target_date=? AND metric=?"
        in detail
        and "source_cycle_time=?" in detail
        for detail in coverage_connection.plan_details
        if "SEARCH forecast" in detail
    )


def test_source_clock_market_root_keeps_western_day0_at_utc_midnight(
    tmp_path, monkeypatch,
) -> None:
    import src.data.bayes_precision_fusion_download as downloader
    import src.data.replacement_forecast_seed_discovery as seed_discovery
    import src.strategy.live_inference.source_clock_city_weights as city_weights

    db = _make_forecast_db(tmp_path)
    decision_time = datetime(2026, 9, 24, 1, tzinfo=UTC)
    cycle = datetime(2026, 9, 23, 18, tzinfo=UTC)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE market_events(city TEXT,target_date TEXT,"
            "temperature_metric TEXT,token_id TEXT,range_label TEXT)"
        )
        conn.executemany(
            "INSERT INTO market_events VALUES(?,?,?,?,?)",
            [(city, "2026-09-23", metric, "token", "range")
             for city in ("Dallas", "Amsterdam")
             for metric in ("high", "low")],
        )
        conn.execute("CREATE TABLE source_run(source_run_id TEXT,source_cycle_time TEXT)")
        conn.execute(
            "CREATE TABLE source_run_coverage(source_run_id TEXT,source_id TEXT,"
            "city TEXT,target_local_date TEXT,temperature_metric TEXT,"
            "data_version TEXT,computed_at TEXT)"
        )

    class _Report:
        updated_sources = ("ecmwf_ifs",)
        affected_cities = ("Dallas", "Amsterdam")

        def as_dict(self):
            return {
                "updated_sources": self.updated_sources,
                "affected_cities": self.affected_cities,
                "source_runs": {
                    "ecmwf_ifs": {
                        "initialisation_time": cycle.isoformat(),
                        "availability_time": cycle.isoformat(),
                        "update_interval_seconds": 3600,
                    },
                },
            }

    monkeypatch.setitem(
        prod.settings["edli"],
        "replacement_0_1_bayes_precision_fusion_capture_enabled", True,
    )
    monkeypatch.setattr(downloader, "bayes_precision_fusion_quota_cooldown_seconds", lambda: 0)
    monkeypatch.setattr(seed_discovery, "held_position_family_priorities", lambda: {})
    monkeypatch.setattr(
        city_weights, "affected_cities_for_source_updates",
        lambda _sources: {"Dallas", "Amsterdam"},
    )
    captured: list[object] = []
    monkeypatch.setattr(
        downloader, "download_bayes_precision_fusion_extra_raw_inputs",
        lambda **kwargs: captured.extend(kwargs["targets"]) or {
            "status": "BAYES_PRECISION_FUSION_EXTRA_TRANSPORT_RETRYABLE",
            "target_count": len(kwargs["targets"]),
            "written_row_count": 0,
        },
    )
    prod._download_bayes_precision_fusion_source_clock_raw_inputs_if_needed(
        {"forecast_db": str(db)},
        source_clock_report=_Report(),
        max_wall_clock_seconds=5.0,
        decision_time=decision_time,
    )
    assert {(target.city, target.target_date, target.metric) for target in captured} == {
        ("Dallas", "2026-09-23", "high"),
        ("Dallas", "2026-09-23", "low"),
    }


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
        lambda _path, **_kwargs: keys,
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
        lambda _path, **_kwargs: keys,
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
        lambda _path, **_kwargs: (
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


@pytest.mark.parametrize("shape", [
    "metadata_only", "superseded_fetched", "real_miss",
    "mixed_final_and_real_miss", "mixed_final_and_transport_error",
])
def test_source_clock_cursor_and_fixpoint_follow_downloader_completeness(
    tmp_path, monkeypatch, shape,
) -> None:
    """Live 2026-09-25: sources whose only gaps were metadata-proven or superseded
    stayed TRANSPORT_RETRYABLE every poll. A global model with such a gap was
    never counted as served, so it also landed in global_models_unavailable.
    The real downloader, the wrapper verdict, the cursor predicate and the
    fixpoint gate must all read that one report. Only HTTP is fake.

    metadata_only: the latest run cannot reach the target; no HTTP is sent.
    superseded_fetched: a fetched older run returns a horizon-shaped partial, a
    final gap. real_miss: the latest run's partial is retryable. Mixed cases
    combine a metadata-final far scope with a retryable parser or fetch miss on
    a near scope for the same global model.
    """
    import src.data.bayes_precision_fusion_download as dl
    import src.data.openmeteo_client as client
    import src.data.openmeteo_model_updates as updates
    import src.data.replacement_forecast_current_target_plan as target_plan
    import src.data.replacement_forecast_seed_discovery as discovery
    import src.data.source_clock_update_probe as probe
    import src.strategy.live_inference.source_clock_city_weights as weights
    from src.state.schema.v2_schema import ensure_replacement_forecast_live_schema

    latest = datetime(2026, 9, 25, 0, tzinfo=UTC)
    available = latest + timedelta(hours=4)
    now = latest + timedelta(hours=5)
    near, far = "2026-09-26", "2026-09-27"
    if shape == "superseded_fetched":
        # Global, with a verified 6-hourly archive: a latest horizon short of
        # the target backtracks to the older run, which is fetched.
        model, target, data_end = "ukmo_global_deterministic_10km", near, datetime(2026, 9, 26, 12, tzinfo=UTC)
    elif shape == "metadata_only":
        # Global, no archive backtrack: the horizon proof needs no request.
        model, target, data_end = "icon_global", far, datetime(2026, 9, 27, 12, tzinfo=UTC)
    elif shape in {"mixed_final_and_real_miss", "mixed_final_and_transport_error"}:
        # The far scope is final from metadata; the near scope is still
        # requestable and must keep this same global model unavailable.
        model, target, data_end = "icon_global", near, datetime(2026, 9, 27, 12, tzinfo=UTC)
    else:
        model, target, data_end = "icon_global", near, datetime(2026, 9, 30, tzinfo=UTC)
    targets = (near, far) if shape.startswith("mixed_final_and_") else (target,)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz or UTC)

    monkeypatch.setattr(dl, "datetime", FixedDatetime)
    dl._EXACT_RUN_UNMATERIALIZABLE_MEMO.clear()
    db = tmp_path / "zeus-forecasts.db"
    with sqlite3.connect(db) as conn:
        ensure_replacement_forecast_live_schema(conn)
    metadata = tmp_path / "updates.jsonl"
    updates.write_model_updates_jsonl(metadata, (updates.OpenMeteoModelUpdate(
        model=model, last_run_initialisation_time=latest,
        last_run_availability_time=available, update_interval_seconds=21600,
        raw={"last_run_initialisation_time": latest.timestamp(),
             "data_end_time": data_end.timestamp()},
    ),))
    monkeypatch.setattr(probe, "DEFAULT_MODEL_UPDATES_JSONL", metadata)
    monkeypatch.setitem(prod.settings["edli"], "replacement_0_1_bayes_precision_fusion_capture_enabled", True)
    monkeypatch.setattr(dl, "bayes_precision_fusion_quota_cooldown_seconds", lambda: 0)
    monkeypatch.setattr(discovery, "held_position_family_priorities", lambda: {})
    monkeypatch.setattr(weights, "affected_cities_for_source_updates", lambda _: ("Amsterdam",))
    monkeypatch.setattr(target_plan, "replacement_forecast_current_target_keys", lambda _, **_k: tuple(
        target_plan.ReplacementForecastTargetKey("Amsterdam", scope, "high")
        for scope in targets
    ))
    seen: list[str] = []

    def fetch(_url, params, **_kwargs):
        seen.append(params["run"])
        if shape == "mixed_final_and_transport_error":
            raise RuntimeError("temporary fetch failure")
        # Truncated before the late-day sample: a horizon-shaped parser gap.
        # Mixed targets fetch only the requestable near scope; the far scope
        # is excluded before HTTP by its metadata-proven horizon gap.
        response_target = near if shape.startswith("mixed_final_and_") else target
        return {"hourly": {"time": [f"{response_target}T{hour:02d}:00" for hour in range(11)],
                           "temperature_2m": [10.0] * 11},
                "hourly_units": {"temperature_2m": "C"}}

    monkeypatch.setattr(client, "fetch", fetch)
    frozen = {"updated_sources": [model], "affected_cities": ["Amsterdam"],
              "source_runs": {model: {"initialisation_time": latest.isoformat(),
                  "availability_time": available.isoformat(), "update_interval_seconds": 21600}}}

    class Report:
        def as_dict(self):
            return frozen

    real_download = dl.download_bayes_precision_fusion_extra_raw_inputs
    downloader_reports: list[dict[str, object]] = []

    def download(**kwargs):
        downloader_reports.append(real_download(**kwargs))
        return downloader_reports[-1]

    monkeypatch.setattr(dl, "download_bayes_precision_fusion_extra_raw_inputs", download)

    report = prod._download_bayes_precision_fusion_source_clock_raw_inputs_if_needed(
        {"forecast_db": str(db), "source_clock_fanout_workers": 1},
        source_clock_report=Report(), max_wall_clock_seconds=5, decision_time=now,
    )

    # 1. The real downloader report.
    (downloaded,) = downloader_reports
    reasons = {g["reason"].split(":")[0] for g in downloaded["exact_run_unmaterializable"]}
    assert reasons == {
        "metadata_only": {"metadata"},
        "superseded_fetched": {"superseded_run"},
        "real_miss": {"ValueError"},
        "mixed_final_and_real_miss": {"metadata", "ValueError"},
        "mixed_final_and_transport_error": {"metadata"},
    }[shape]
    expected_runs = {
        "metadata_only": (),
        "superseded_fetched": (latest - timedelta(hours=6), latest - timedelta(hours=12)),
        "real_miss": (latest,),
        "mixed_final_and_real_miss": (latest,),
        "mixed_final_and_transport_error": (latest,),
    }[shape]
    assert sorted(seen) == sorted(run.strftime("%Y-%m-%dT%H:%M") for run in expected_runs)
    if shape == "mixed_final_and_transport_error":
        assert downloaded["transport_errors"]
        assert any("temporary fetch failure" in error for error in downloaded["transport_errors"])
    assert downloaded["written_row_count"] == 0
    complete = shape in {"metadata_only", "superseded_fetched"}
    assert downloaded["global_models_unavailable"] == ([] if complete else [model])
    # 2. The wrapper verdict and the cursor predicate read that report.
    result = report["source_results"][model]
    assert result["status"] == (
        "SOURCE_CLOCK_SOURCE_RAW_INPUTS_DOWNLOADED" if complete
        else "SOURCE_CLOCK_SOURCE_TRANSPORT_RETRYABLE"
    )
    assert probe.source_clock_scoped_download_cursor_sources(
        report, source_clock_report=frozen,
    ) == ((model,) if complete else ())
    # 3. The extras fixpoint gate reads the same report.
    assert prod._extras_fixpoint_admits(downloaded) is complete


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


@pytest.mark.parametrize("complete_candidate", [False, True])
def test_source_target_candidate_keeps_trigger_and_committed_cycles_distinct(
    tmp_path, monkeypatch, complete_candidate
) -> None:
    import src.data.bayes_precision_fusion_download as dl
    import src.data.openmeteo_model_updates as updates
    import src.data.replacement_forecast_current_target_plan as target_plan
    import src.data.replacement_forecast_seed_discovery as seed_discovery
    import src.strategy.live_inference.source_clock_city_weights as city_weights

    class _Report:
        updated_sources = ("icon_eu",)
        affected_cities = ("Moscow",)

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
                model="icon_eu",
                last_run_initialisation_time=_CYCLE,
                last_run_availability_time=_CYCLE,
                update_interval_seconds=3600,
            ),
        ),
    )
    monkeypatch.setattr(
        target_plan,
        "replacement_forecast_current_target_keys",
        lambda _path, **_kwargs: (
            target_plan.ReplacementForecastTargetKey(
                "Moscow", "2026-07-17", "high"
            ),
        ),
    )
    monkeypatch.setattr(seed_discovery, "held_position_family_priorities", lambda: {})
    monkeypatch.setattr(
        city_weights,
        "affected_cities_for_source_updates",
        lambda _sources: ("Moscow",),
    )
    monkeypatch.setattr(
        dl,
        "download_bayes_precision_fusion_extra_raw_inputs",
        lambda **_kwargs: {
            "status": (
                "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED"
                if complete_candidate
                else "BAYES_PRECISION_FUSION_EXTRA_EXACT_RUN_UNMATERIALIZABLE"
            ),
            "target_count": 1,
            "written_row_count": int(complete_candidate),
            "exact_run_unmaterializable": () if complete_candidate else (
                {
                    "model": "icon_eu",
                    "city": "Moscow",
                    "target_date": "2026-07-17",
                    "source_cycle_time": _CYCLE.isoformat(),
                    "reason": "ValueError:partial local-day coverage",
                },
            ),
            "single_runs_request_cycles": {"icon_eu": _CYCLE.isoformat()},
            "single_runs_advertised_trigger_cycles": {"icon_eu": _CYCLE.isoformat()},
            "single_runs_target_request_cycles": {
                "icon_eu|Moscow|2026-07-17": ((_CYCLE - timedelta(hours=6)).isoformat(),),
            },
            "single_runs_written_cycles": (
                {"icon_eu|Moscow|2026-07-17": ((_CYCLE - timedelta(hours=6)).isoformat(),)}
                if complete_candidate else {}
            ),
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

    from src.data.source_clock_update_probe import source_clock_scoped_download_cursor_sources

    expected = "RAW_INPUTS_DOWNLOADED" if complete_candidate else "TRANSPORT_RETRYABLE"
    assert report["status"] == f"SOURCE_CLOCK_SCOPED_BAYES_PRECISION_FUSION_EXTRA_{expected}"
    result = report["source_results"]["icon_eu"]
    assert result["status"] == f"SOURCE_CLOCK_SOURCE_{expected}"
    assert result["cycle"] == _CYCLE.isoformat()
    assert result["advertised_trigger_cycles"] == (_CYCLE.isoformat(),)
    candidate = (_CYCLE - timedelta(hours=6)).isoformat()
    assert result["single_runs_target_request_cycles"] == {
        "icon_eu|Moscow|2026-07-17": (candidate,),
    }
    assert result["single_runs_written_cycles"] == (
        {"icon_eu|Moscow|2026-07-17": (candidate,)} if complete_candidate else {}
    )
    frozen = {"source_runs": {"icon_eu": {"initialisation_time": _CYCLE.isoformat()}}}
    assert source_clock_scoped_download_cursor_sources(
        report, source_clock_report=frozen,
    ) == (("icon_eu",) if complete_candidate else ())
    if not complete_candidate:
        assert result["exact_run_unmaterializable"][0]["city"] == "Moscow"


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
        "extras_fixpoint_latched": False,
        "extras_zero_progress_observed": True,
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
    monkeypatch.setattr(prod, "_probe_resolved_available_cycle", lambda **_kwargs: _CYCLE)
    monkeypatch.setattr(
        prod, "_probe_resolved_bayes_precision_fusion_extras_cycle", lambda **_kwargs: _CYCLE
    )
    monkeypatch.setattr(
        "src.data.replacement_forecast_current_target_plan.build_replacement_forecast_current_target_plan",
        lambda *a, **k: _plan_full_two_leads(),
    )
    monkeypatch.setattr(
        "src.data.replacement_forecast_current_target_plan.replacement_forecast_current_target_keys",
        lambda *a, **k: _plan_full_two_leads().rows,
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
        "bpf_extra_rotation_state_path": tmp_path / "bpf-extra-rotation.json",
    }
    return cfg


def test_callsite_zero_progress_retries_same_cycle_missing_scope(tmp_path, monkeypatch, _redirect_health):
    """A zero-write pass cannot strand the same planning cycle on the next poll."""
    cfg = _wire_poll(
        monkeypatch, tmp_path,
        download_report={"status": "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED", "written_row_count": 0},
    )
    # Tick 1: incomplete -> fan-out runs -> 0 written.
    r1 = prod._replacement_cycle_availability_poll_if_needed(cfg)
    assert r1["bayes_precision_fusion_extras_status"] == "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED"
    assert prod._extras_fixpoint_latched(_CYCLE) is False
    # Tick 2: still incomplete and the fan-out remains reachable.
    r2 = prod._replacement_cycle_availability_poll_if_needed(cfg)
    assert r2["bayes_precision_fusion_extras_status"] == "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED"


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


def test_frozen_source_clock_capture_uses_complete_target_candidates_end_to_end(
    tmp_path, monkeypatch,
) -> None:
    """Real wrapper, target planner, HTTP parser, writer and cursor share one identity."""
    import src.data.bayes_precision_fusion_download as dl
    import src.data.openmeteo_client as client
    import src.data.openmeteo_model_updates as updates
    import src.data.replacement_forecast_current_target_plan as target_plan
    import src.data.replacement_forecast_seed_discovery as discovery
    import src.data.source_clock_update_probe as probe
    import src.strategy.live_inference.source_clock_city_weights as weights
    from src.state.schema.v2_schema import ensure_replacement_forecast_live_schema

    latest = datetime(2026, 9, 23, 3, tzinfo=UTC)
    available = latest + timedelta(hours=3, minutes=2)
    now = latest + timedelta(hours=3, minutes=42)
    old = latest - timedelta(hours=3)
    older = old - timedelta(hours=6)
    target = "2026-09-25"

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz or UTC)

    monkeypatch.setattr(dl, "datetime", FixedDatetime)
    db = tmp_path / "zeus-forecasts.db"
    with sqlite3.connect(db) as conn:
        ensure_replacement_forecast_live_schema(conn)
    metadata = tmp_path / "updates.jsonl"
    updates.write_model_updates_jsonl(metadata, (updates.OpenMeteoModelUpdate(
        model="icon_eu", last_run_initialisation_time=latest,
        last_run_availability_time=available, update_interval_seconds=10800,
        raw={"last_run_initialisation_time": latest.timestamp(),
             "data_end_time": datetime(2026, 9, 24, 10, tzinfo=UTC).timestamp()},
    ),))
    monkeypatch.setattr(probe, "DEFAULT_MODEL_UPDATES_JSONL", metadata)
    monkeypatch.setitem(prod.settings["edli"], "replacement_0_1_bayes_precision_fusion_capture_enabled", True)
    monkeypatch.setattr(dl, "bayes_precision_fusion_quota_cooldown_seconds", lambda: 0)
    monkeypatch.setattr(discovery, "held_position_family_priorities", lambda: {})
    monkeypatch.setattr(weights, "affected_cities_for_source_updates", lambda _: ("Amsterdam",))
    monkeypatch.setattr(target_plan, "replacement_forecast_current_target_keys", lambda _, **_kwargs: tuple(
        target_plan.ReplacementForecastTargetKey("Amsterdam", target, metric)
        for metric in ("high", "low")
    ))
    seen = []

    def fetch(_url, params, **_kwargs):
        seen.append(params["run"])
        temperatures = ([None] * 24 if params["run"] == latest.strftime("%Y-%m-%dT%H:%M")
                        else [10.0 + hour / 10.0 for hour in range(24)])
        return {"hourly": {"time": [f"{target}T{hour:02d}:00" for hour in range(24)],
                           "temperature_2m": temperatures},
                "hourly_units": {"temperature_2m": "C"}}

    monkeypatch.setattr(client, "fetch", fetch)
    frozen = {"updated_sources": ["icon_eu"], "affected_cities": ["Amsterdam"],
              "source_runs": {"icon_eu": {"initialisation_time": latest.isoformat(),
                  "availability_time": available.isoformat(), "update_interval_seconds": 10800}}}
    class Report:
        def as_dict(self):
            return frozen

    report = prod._download_bayes_precision_fusion_source_clock_raw_inputs_if_needed(
        {"forecast_db": str(db), "source_clock_fanout_workers": 1},
        source_clock_report=Report(), max_wall_clock_seconds=5, decision_time=now,
    )
    assert set(seen) == {run.strftime("%Y-%m-%dT%H:%M") for run in (old, older)}
    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT source_cycle_time,source_available_at,captured_at,metric FROM raw_model_forecasts").fetchall()
    assert len(rows) == 4
    assert {row[0] for row in rows} == {old.isoformat(), older.isoformat()}
    assert {(row[1], row[2]) for row in rows} == {(now.isoformat(), now.isoformat())}
    assert {row[3] for row in rows} == {"high", "low"}
    result = report["source_results"]["icon_eu"]
    assert result["cycle"] == latest.isoformat()
    assert result["advertised_trigger_cycles"] == (latest.isoformat(),)
    assert result["single_runs_written_cycles"] == {
        f"icon_eu|Amsterdam|{target}": tuple(sorted((old.isoformat(), older.isoformat()))),
    }
    assert probe.source_clock_scoped_download_cursor_sources(report, source_clock_report=frozen) == ("icon_eu",)

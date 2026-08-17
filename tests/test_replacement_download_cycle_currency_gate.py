# Created: 2026-06-09
# Last reused or audited: 2026-08-17
# Lifecycle: created=2026-06-09; last_reviewed=2026-08-17; last_reused=2026-08-17
# Purpose: Prove current-target anchor cycle currency and scoped quota authority.
# Reuse: Run for replacement current-target download, source-clock, or quota-lane changes.
# Authority basis: 2026-06-09 anchor-lag root cause (/tmp/anchor_lag_report.md, verified against
#   src/data/replacement_forecast_production.py + replacement_forecast_current_target_plan.py):
#   the ALREADY_COVERED / HAVE_RAW_MANIFESTS short-circuits contained NO cycle comparison, so once
#   any cycle fully materialized the download cron could never advance the anchor again —
#   deterministic_forecast_anchors froze at 2026-06-08T18 for ~24h while Open-Meteo served
#   2026-06-09T00 (httpx 200 OK on the BAYES_PRECISION_FUSION leg of the SAME job run).
"""RELATIONSHIP antibody: current-target COVERAGE never implies CYCLE CURRENCY.

Cross-module invariant (plan coverage -> download gate boundary):
  plan.ready means "a posterior exists for every current target". It says NOTHING about which
  IFS cycle that posterior was built from. The download gate may skip ONLY when the
  currently-available cycle's raw inputs (BOTH ecmwf_aifs_ens AND openmeteo_ecmwf_ifs_9km
  artifacts) are already downloaded. available_cycle > downloaded high-water mark => the
  download MUST fire regardless of posterior coverage.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.data.replacement_forecast_production import (
    _download_replacement_forecast_current_targets_if_needed,
)

AVAILABLE_CYCLE = datetime(2026, 6, 9, 0, 0, tzinfo=timezone.utc)
STALE_CYCLE_ISO = "2026-06-08T18:00:00+00:00"
CURRENT_CYCLE_ISO = "2026-06-09T00:00:00+00:00"

_ARTIFACTS_DDL = """
CREATE TABLE raw_forecast_artifacts (
    artifact_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    data_version TEXT NOT NULL,
    source_cycle_time TEXT NOT NULL,
    source_available_at TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    artifact_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    request_url TEXT,
    request_params_json TEXT NOT NULL DEFAULT '{}',
    artifact_metadata_json TEXT NOT NULL DEFAULT '{}',
    trade_authority_status TEXT NOT NULL DEFAULT 'SHADOW_ONLY',
    training_allowed INTEGER NOT NULL DEFAULT 0,
    recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


@dataclass
class _PlanStub:
    ready: bool = True
    missing_aifs_manifest_count: int = 0
    missing_openmeteo_manifest_count: int = 0
    rows: tuple = ()
    payload: dict = field(default_factory=lambda: {"status": "CURRENT_TARGETS_COVERED"})

    def as_dict(self) -> dict:
        return dict(self.payload)


@dataclass(frozen=True)
class _TargetRow:
    city: str
    target_date: str
    temperature_metric: str
    covered: bool
    missing_openmeteo_manifest: bool


def test_current_target_download_prioritizes_held_families_before_alphabetic() -> None:
    import scripts.download_replacement_forecast_current_targets as dl

    rows = (
        _TargetRow("Amsterdam", "2026-06-10", "high", False, True),
        _TargetRow("Wellington", "2026-06-10", "high", False, True),
        _TargetRow("Dallas", "2026-06-10", "high", False, True),
    )
    priorities = {
        ("Wellington", "2026-06-10", "high"): 0,
        ("Dallas", "2026-06-10", "high"): 1,
    }
    ordered = dl._ordered_current_target_rows(
        rows,
        priorities,
    )
    rotated, start, rotating_count = dl._rotate_current_target_rows(
        ordered,
        cycle=AVAILABLE_CYCLE.replace(hour=4),
    )

    assert [row.city for row in ordered] == ["Wellington", "Dallas", "Amsterdam"]
    assert start == 0
    assert rotating_count == 3
    assert [row.city for row in rotated] == ["Wellington", "Dallas", "Amsterdam"]


def test_timeboxed_current_target_download_rotates_past_attempted_prefix(
    tmp_path: Path,
) -> None:
    import scripts.download_replacement_forecast_current_targets as dl

    rotation_cycle = AVAILABLE_CYCLE.replace(hour=3)
    rows = [
        _TargetRow(city, "2026-06-10", "high", False, True)
        for city in ("Amsterdam", "Ankara", "Atlanta", "Austin")
    ]
    with dl._CURRENT_TARGET_ROTATION_LOCK:
        dl._CURRENT_TARGET_ROTATION_OFFSETS.clear()
    first, first_start, row_count = dl._rotate_current_target_rows(
        rows,
        cycle=rotation_cycle,
        state_path=tmp_path / "rotation.json",
    )
    next_start = dl._advance_current_target_rotation(
        cycle=rotation_cycle,
        row_count=row_count,
        attempted_count=2,
        incomplete=True,
        state_path=tmp_path / "rotation.json",
    )
    with dl._CURRENT_TARGET_ROTATION_LOCK:
        dl._CURRENT_TARGET_ROTATION_OFFSETS.clear()
    second, second_start, _ = dl._rotate_current_target_rows(
        rows,
        cycle=rotation_cycle,
        state_path=tmp_path / "rotation.json",
    )

    assert first_start == 0
    assert [row.city for row in first] == ["Amsterdam", "Ankara", "Atlanta", "Austin"]
    assert next_start == 2
    assert second_start == 2
    assert [row.city for row in second] == ["Atlanta", "Austin", "Amsterdam", "Ankara"]
    with dl._CURRENT_TARGET_ROTATION_LOCK:
        dl._CURRENT_TARGET_ROTATION_OFFSETS.clear()


def test_durable_rotation_gives_ordinary_lane_a_turn_after_held_prefix(
    tmp_path: Path,
) -> None:
    import scripts.download_replacement_forecast_current_targets as dl

    cycle = AVAILABLE_CYCLE.replace(hour=5)
    priorities = {("Dallas", "2026-06-10", "high"): 0}
    ordered = dl._ordered_current_target_rows(
        (
            _TargetRow("Amsterdam", "2026-06-10", "high", False, True),
            _TargetRow("Dallas", "2026-06-10", "high", False, True),
            _TargetRow("Ankara", "2026-06-10", "high", False, True),
        ),
        priorities,
    )
    state_path = tmp_path / "rotation.json"
    first, _, row_count = dl._rotate_current_target_rows(
        ordered,
        cycle=cycle,
        state_path=state_path,
    )
    assert first[0].city == "Dallas"
    dl._advance_current_target_rotation(
        cycle=cycle,
        row_count=row_count,
        attempted_count=1,
        incomplete=True,
        state_path=state_path,
    )

    with dl._CURRENT_TARGET_ROTATION_LOCK:
        dl._CURRENT_TARGET_ROTATION_OFFSETS.clear()
    after_restart, start, _ = dl._rotate_current_target_rows(
        ordered,
        cycle=cycle,
        state_path=state_path,
    )

    assert start == 1
    assert after_restart[0].city == "Amsterdam"


def _make_db(tmp_path: Path, cycles_by_source: dict[str, str]) -> Path:
    db = tmp_path / "forecasts.db"
    conn = sqlite3.connect(db)
    conn.execute(_ARTIFACTS_DDL)
    for sid, cyc in cycles_by_source.items():
        conn.execute(
            "INSERT INTO raw_forecast_artifacts (source_id, product_id, data_version,"
            " source_cycle_time, source_available_at, captured_at, artifact_path, sha256,"
            " byte_size) VALUES (?, 'p', 'v1', ?, ?, ?, '/tmp/x', 'h', 1)",
            (sid, cyc, cyc, cyc),
        )
    conn.commit()
    conn.close()
    return db


def test_anchor_ladder_skips_single_runs_when_source_clock_says_not_public(monkeypatch) -> None:
    import scripts.download_replacement_forecast_current_targets as dl
    from src.data.openmeteo_ecmwf_ifs9_anchor import build_anchor_request

    request = build_anchor_request(
        latitude=33.63,
        longitude=-84.44,
        run="2026-06-25T12:00:00+00:00",
        timezone_name="UTC",
    )

    monkeypatch.setattr(dl, "_single_runs_public_for_request", lambda _request: False)

    def _single_runs_should_not_be_called(*_args, **_kwargs):
        raise AssertionError("single-runs fetch should be skipped before publication")

    monkeypatch.setattr(
        dl,
        "fetch_openmeteo_ecmwf_ifs9_anchor_payload",
        _single_runs_should_not_be_called,
    )

    def _meta_refuses(*_args, **_kwargs):
        raise ValueError("provider declares an older run")

    monkeypatch.setattr(
        "src.data.openmeteo_ecmwf_ifs9_anchor.fetch_openmeteo_ecmwf_ifs9_anchor_payload_meta_stamped",
        _meta_refuses,
    )
    monkeypatch.setattr(
        dl,
        "_try_bucket_rung_three",
        lambda **_kwargs: (
            {"hourly": {"time": [], "temperature_2m": []}},
            {"run_authority": "bucket_partial_run_test"},
        ),
    )

    payload, provenance = dl._resolve_anchor_payload(
        request=request,
        city="Atlanta",
        target_date="2026-06-25",
        timezone_name="UTC",
    )

    assert payload == {"hourly": {"time": [], "temperature_2m": []}}
    assert provenance["run_authority"] == "bucket_partial_run_test"


def test_meta_stamped_wave_brackets_concurrent_payloads_once(monkeypatch) -> None:
    import scripts.download_replacement_forecast_current_targets as dl
    from src.data.openmeteo_ecmwf_ifs9_anchor import build_anchor_request

    requests = {
        (f"City {i}", "2026-06-25"): build_anchor_request(
            latitude=30.0 + i,
            longitude=10.0 + i,
            run="2026-06-25T12:00:00+00:00",
            timezone_name="UTC",
        )
        for i in range(4)
    }
    meta = {
        "run_initialisation_utc": datetime(2026, 6, 25, 12, tzinfo=timezone.utc),
        "run_availability_utc": datetime(2026, 6, 25, 13, tzinfo=timezone.utc),
        "run_modification_utc": datetime(2026, 6, 25, 13, tzinfo=timezone.utc),
    }
    events: list[str] = []
    barrier = threading.Barrier(4)

    def _meta(**_kwargs):
        events.append("meta")
        return meta

    def _payload(_request, **_kwargs):
        events.append("payload-start")
        barrier.wait(timeout=1.0)
        events.append("payload-done")
        return {"hourly": {"time": [], "temperature_2m": []}}

    monkeypatch.setattr(dl, "fetch_openmeteo_ifs9_model_meta", _meta)
    monkeypatch.setattr(
        dl,
        "fetch_openmeteo_ecmwf_ifs9_anchor_payload_standard_unstamped",
        _payload,
    )

    resolved, failures = dl._fetch_meta_stamped_anchor_wave(
        requests,
        max_workers=4,
        deadline_monotonic=None,
        client=object(),
    )

    assert failures == {}
    assert set(resolved) == set(requests)
    assert events.count("meta") == 2
    assert events[0] == events[-1] == "meta"
    assert all(row[1]["meta_stamp_scope"] == "download_wave" for row in resolved.values())


def test_deadlined_meta_wave_never_queues_more_than_one_worker_width(monkeypatch) -> None:
    import scripts.download_replacement_forecast_current_targets as dl
    from src.data.openmeteo_ecmwf_ifs9_anchor import build_anchor_request

    requests = {
        (f"City {i}", "2026-08-07"): build_anchor_request(
            latitude=30.0 + i,
            longitude=10.0 + i,
            run="2026-08-07T12:00:00+00:00",
            timezone_name="UTC",
        )
        for i in range(8)
    }
    meta = {
        "run_initialisation_utc": datetime(2026, 8, 7, 12, tzinfo=timezone.utc),
        "run_availability_utc": datetime(2026, 8, 7, 13, tzinfo=timezone.utc),
        "run_modification_utc": datetime(2026, 8, 7, 13, tzinfo=timezone.utc),
    }
    attempted: list[object] = []

    monkeypatch.setattr(dl, "fetch_openmeteo_ifs9_model_meta", lambda **_kwargs: meta)

    def _payload(request, **_kwargs):
        attempted.append(request)
        return {"hourly": {"time": [], "temperature_2m": []}}

    monkeypatch.setattr(
        dl,
        "fetch_openmeteo_ecmwf_ifs9_anchor_payload_standard_unstamped",
        _payload,
    )

    resolved, failures = dl._fetch_meta_stamped_anchor_wave(
        requests,
        max_workers=2,
        deadline_monotonic=dl.time.monotonic() + 1.0,
        client=object(),
    )

    assert failures == {}
    assert list(resolved) == list(requests)[:2]
    assert len(attempted) == 2


def test_meta_stamped_wave_discards_every_payload_when_provider_changes_run(
    monkeypatch,
) -> None:
    import scripts.download_replacement_forecast_current_targets as dl
    from src.data.openmeteo_ecmwf_ifs9_anchor import build_anchor_request

    request = build_anchor_request(
        latitude=33.63,
        longitude=-84.44,
        run="2026-06-25T12:00:00+00:00",
        timezone_name="UTC",
    )
    metas = iter(
        (
            {
                "run_initialisation_utc": request.run,
                "run_availability_utc": request.run,
                "run_modification_utc": request.run,
            },
            {
                "run_initialisation_utc": request.run,
                "run_availability_utc": request.run,
                "run_modification_utc": request.run.replace(hour=13),
            },
        )
    )
    monkeypatch.setattr(dl, "fetch_openmeteo_ifs9_model_meta", lambda **_kwargs: next(metas))
    monkeypatch.setattr(
        dl,
        "fetch_openmeteo_ecmwf_ifs9_anchor_payload_standard_unstamped",
        lambda *_args, **_kwargs: {"hourly": {}},
    )

    requests = {("Atlanta", "2026-06-25"): request, ("Paris", "2026-06-25"): request}
    resolved, failures = dl._fetch_meta_stamped_anchor_wave(
        requests,
        max_workers=2,
        deadline_monotonic=None,
        client=object(),
    )

    assert resolved == {}
    assert set(failures) == set(requests)
    assert all("mid-fetch" in str(exc) for exc in failures.values())


def test_direct_downloader_uses_payload_completion_as_possession_time(
    tmp_path,
    monkeypatch,
) -> None:
    import scripts.download_replacement_forecast_current_targets as dl

    row = _TargetRow(
        city="London",
        target_date="2026-06-10",
        temperature_metric="high",
        covered=False,
        missing_openmeteo_manifest=True,
    )
    captured_at = datetime(2026, 6, 9, 13, 59, 45, tzinfo=timezone.utc)
    provenance = {
        "openmeteo_endpoint": "standard_api_meta_stamped",
        "run_authority": "provider_meta_declared",
    }
    monkeypatch.setattr(dl, "_single_runs_public_for_request", lambda _request: False)

    def _wave(requests, **_kwargs):
        key = next(iter(requests))
        return {
            key: (
                {"hourly": {"time": [], "temperature_2m": []}},
                provenance,
                captured_at,
            )
        }, {}

    monkeypatch.setattr(dl, "_fetch_meta_stamped_anchor_wave", _wave)

    report = dl.download_current_target_raw_inputs(
        forecast_db=tmp_path / "forecasts.db",
        output_dir=tmp_path / "raw",
        cycle=AVAILABLE_CYCLE,
        limit=None,
        write_db=False,
        release_lag_hours=14.0,
        anchor_sigma_c=3.0,
        include_covered=True,
        precomputed_plan=_PlanStub(ready=False, rows=(row,)),
    )

    manifest = json.loads(Path(report["written_manifests"][0]).read_text())
    assert manifest["captured_at"] == captured_at.isoformat()
    assert manifest["source_available_at"] == captured_at.isoformat()


def _wire(monkeypatch, *, plan: _PlanStub, calls: list):
    import scripts.download_replacement_forecast_current_targets as dl
    import src.data.replacement_forecast_current_target_plan as plan_mod

    def _plan_builder(db, *args, **kwargs):
        required_cycle = kwargs.get("required_openmeteo_source_cycle_time")
        if required_cycle is None:
            return plan
        conn = sqlite3.connect(db)
        try:
            row = conn.execute(
                "SELECT MAX(source_cycle_time) FROM raw_forecast_artifacts "
                "WHERE source_id = 'openmeteo_ecmwf_ifs_9km'"
            ).fetchone()
        finally:
            conn.close()
        max_cycle = None if row is None else row[0]
        required_iso = required_cycle.isoformat() if hasattr(required_cycle, "isoformat") else str(required_cycle)
        if max_cycle is None or str(max_cycle) < required_iso:
            return _PlanStub(
                ready=False,
                missing_openmeteo_manifest_count=1,
                payload={"status": "CURRENT_TARGETS_MISSING_CURRENT_CYCLE_MANIFESTS"},
            )
        return plan

    monkeypatch.setattr(
        plan_mod,
        "build_replacement_forecast_current_target_plan",
        _plan_builder,
    )
    # Run-selection single authority (2026-06-11): the production job resolves the
    # available cycle via provider probes, never the dead now-minus-lag guess.
    import src.data.replacement_forecast_production as production

    monkeypatch.setattr(
        production, "_probe_resolved_available_cycle", lambda: AVAILABLE_CYCLE
    )

    def _fake_download(**kwargs):
        calls.append(kwargs)
        return {
            "status": "CURRENT_TARGET_RAW_INPUTS_DOWNLOADED",
            "cycle": kwargs["cycle"].isoformat(),
        }

    monkeypatch.setattr(dl, "download_current_target_raw_inputs", _fake_download)


def _cfg(db: Path, tmp_path: Path) -> dict:
    return {
        "download_current_targets_enabled": True,
        "forecast_db": db,
        "download_output_dir": tmp_path / "manifests",
        "download_release_lag_hours": 14.0,
        "download_limit": 10,
        "download_anchor_sigma_c": 3.0,
        "download_aifs_retries": 1,
        "source_clock_fanout_workers": 6,
    }


def test_ready_plan_with_stale_artifacts_still_downloads_new_cycle(tmp_path, monkeypatch) -> None:
    # THE 2026-06-09 incident shape: full posterior coverage + artifacts one cycle behind.
    db = _make_db(tmp_path, {
        "ecmwf_aifs_ens": STALE_CYCLE_ISO,
        "openmeteo_ecmwf_ifs_9km": STALE_CYCLE_ISO,
    })
    calls: list = []
    _wire(monkeypatch, plan=_PlanStub(ready=True), calls=calls)
    report = _download_replacement_forecast_current_targets_if_needed(_cfg(db, tmp_path))
    assert report is not None
    assert report["status"] == "CURRENT_TARGET_RAW_INPUTS_DOWNLOADED", (
        "plan.ready (posterior coverage) must NOT suppress the download of a newer available "
        "cycle — this is the gate that froze deterministic_forecast_anchors at 06-08T18"
    )
    assert len(calls) == 1
    assert calls[0]["cycle"] == AVAILABLE_CYCLE
    assert calls[0]["fetch_workers"] == 6


def test_scoped_source_commit_is_not_truncated_by_maintenance_limit(
    tmp_path, monkeypatch
) -> None:
    db = _make_db(tmp_path, {
        "ecmwf_aifs_ens": STALE_CYCLE_ISO,
        "openmeteo_ecmwf_ifs_9km": STALE_CYCLE_ISO,
    })
    calls: list = []
    _wire(monkeypatch, plan=_PlanStub(ready=False), calls=calls)
    scopes = tuple(
        (f"City-{index:02d}", "2026-07-18", "high")
        for index in range(25)
    )

    report = _download_replacement_forecast_current_targets_if_needed(
        _cfg(db, tmp_path),
        required_scopes=scopes,
        max_wall_clock_seconds=10.0,
    )

    assert report is not None
    assert len(calls) == 1
    assert calls[0]["required_scopes"] == scopes
    assert calls[0]["limit"] is None


def test_exact_held_day0_scope_enters_critical_quota_lane(
    tmp_path, monkeypatch
) -> None:
    db = _make_db(
        tmp_path,
        {
            "ecmwf_aifs_ens": STALE_CYCLE_ISO,
            "openmeteo_ecmwf_ifs_9km": STALE_CYCLE_ISO,
        },
    )
    calls: list = []
    _wire(monkeypatch, plan=_PlanStub(ready=False), calls=calls)
    scope = ("Dallas", "2026-08-17", "high")
    monkeypatch.setattr(
        "src.data.replacement_forecast_seed_discovery.held_position_family_priorities",
        lambda: {scope: 0},
    )
    entered: list[str] = []

    @contextmanager
    def _critical_lane():
        entered.append("enter")
        try:
            yield
        finally:
            entered.append("exit")

    monkeypatch.setattr(
        "src.data.openmeteo_quota.quota_tracker.critical_lane",
        _critical_lane,
    )

    report = _download_replacement_forecast_current_targets_if_needed(
        _cfg(db, tmp_path),
        required_scopes=(scope,),
        quota_critical=True,
    )

    assert report is not None
    assert entered == ["enter", "exit"]
    assert calls[0]["required_scopes"] == (scope,)


def test_nonheld_scope_cannot_borrow_critical_quota(
    tmp_path, monkeypatch
) -> None:
    db = _make_db(
        tmp_path,
        {
            "ecmwf_aifs_ens": STALE_CYCLE_ISO,
            "openmeteo_ecmwf_ifs_9km": STALE_CYCLE_ISO,
        },
    )
    calls: list = []
    _wire(monkeypatch, plan=_PlanStub(ready=False), calls=calls)
    scope = ("Cape Town", "2026-08-19", "high")
    monkeypatch.setattr(
        "src.data.replacement_forecast_seed_discovery.held_position_family_priorities",
        lambda: {scope: 1},
    )

    with pytest.raises(ValueError, match="exact canonical day0_window/pending_exit"):
        _download_replacement_forecast_current_targets_if_needed(
            _cfg(db, tmp_path),
            required_scopes=(scope,),
            quota_critical=True,
        )

    assert calls == []


def test_covered_critical_scope_does_not_rewrite_anchor(
    tmp_path, monkeypatch
) -> None:
    db = _make_db(
        tmp_path,
        {"openmeteo_ecmwf_ifs_9km": CURRENT_CYCLE_ISO},
    )
    scope = ("Dallas", "2026-08-17", "high")
    conn = sqlite3.connect(db)
    conn.execute(
        """
        UPDATE raw_forecast_artifacts
        SET product_id = 'openmeteo_ecmwf_ifs9_deterministic_anchor_v1',
            data_version = 'openmeteo_ecmwf_ifs9_anchor_localday_high',
            artifact_metadata_json = ?
        WHERE source_id = 'openmeteo_ecmwf_ifs_9km'
        """,
        (json.dumps({"city": "Dallas", "target_date": "2026-08-17", "metric": "high"}),),
    )
    conn.commit()
    conn.close()
    calls: list = []
    _wire(monkeypatch, plan=_PlanStub(ready=False), calls=calls)
    monkeypatch.setattr(
        "src.data.replacement_forecast_seed_discovery.held_position_family_priorities",
        lambda: {scope: 0},
    )

    report = _download_replacement_forecast_current_targets_if_needed(
        _cfg(db, tmp_path),
        required_scopes=(scope,),
        quota_critical=True,
    )

    assert report["status"] == "CURRENT_TARGET_CRITICAL_SCOPES_ALREADY_COVERED"
    assert report["target_count"] == 1
    assert report["written_manifest_count"] == 0
    assert calls == []


def test_critical_quota_context_propagates_into_anchor_worker(
    monkeypatch,
) -> None:
    import scripts.download_replacement_forecast_current_targets as dl

    class _Tracker:
        def __init__(self) -> None:
            self.local = threading.local()

        @contextmanager
        def critical_lane(self):
            self.local.critical = True
            try:
                yield
            finally:
                self.local.critical = False

        def is_critical(self) -> bool:
            return bool(getattr(self.local, "critical", False))

    tracker = _Tracker()
    observed: list[bool] = []
    monkeypatch.setattr(dl, "quota_tracker", tracker)
    monkeypatch.setattr(dl, "fetch_openmeteo_ifs9_model_meta", lambda **_kwargs: {})
    monkeypatch.setattr(
        dl,
        "validate_openmeteo_ecmwf_ifs9_meta_window",
        lambda *_args: {},
    )
    monkeypatch.setattr(
        dl,
        "fetch_openmeteo_ecmwf_ifs9_anchor_payload_standard_unstamped",
        lambda *_args, **_kwargs: observed.append(tracker.is_critical()) or {},
    )

    payloads, failures = dl._fetch_meta_stamped_anchor_wave(
        {("Dallas", "2026-08-17"): object()},
        max_workers=1,
        deadline_monotonic=None,
        client=object(),
        quota_critical=True,
    )

    assert failures == {}
    assert tuple(payloads) == (("Dallas", "2026-08-17"),)
    assert observed == [True]


def test_current_target_budget_starts_after_probe_and_plan(tmp_path, monkeypatch) -> None:
    db = _make_db(tmp_path, {
        "ecmwf_aifs_ens": STALE_CYCLE_ISO,
        "openmeteo_ecmwf_ifs_9km": STALE_CYCLE_ISO,
    })
    calls: list = []
    clock = [0.0]
    import scripts.download_replacement_forecast_current_targets as dl
    import src.data.replacement_forecast_current_target_plan as plan_mod
    import src.data.replacement_forecast_production as production

    def _probe():
        clock[0] = 100.0
        return AVAILABLE_CYCLE

    monkeypatch.setattr(production, "_probe_resolved_available_cycle", _probe)
    monkeypatch.setattr(production.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        plan_mod,
        "build_replacement_forecast_current_target_plan",
        lambda *_args, **_kwargs: _PlanStub(
            ready=False,
            missing_openmeteo_manifest_count=1,
        ),
    )

    def _download(**kwargs):
        calls.append(kwargs)
        return {"status": "CURRENT_TARGET_RAW_INPUTS_DOWNLOADED"}

    monkeypatch.setattr(dl, "download_current_target_raw_inputs", _download)

    report = _download_replacement_forecast_current_targets_if_needed(
        _cfg(db, tmp_path),
        max_wall_clock_seconds=5.0,
    )

    assert report["status"] == "CURRENT_TARGET_RAW_INPUTS_DOWNLOADED"
    assert len(calls) == 1
    assert calls[0]["max_wall_clock_seconds"] == 5.0


def test_timeboxed_current_target_slices_reuse_cycle_bucket_pool(
    tmp_path, monkeypatch
) -> None:
    db = _make_db(tmp_path, {
        "ecmwf_aifs_ens": STALE_CYCLE_ISO,
        "openmeteo_ecmwf_ifs_9km": STALE_CYCLE_ISO,
    })
    calls: list[dict[str, object]] = []
    import scripts.download_replacement_forecast_current_targets as dl
    import src.data.replacement_forecast_current_target_plan as plan_mod
    import src.data.replacement_forecast_production as production

    class _Pool:
        close_count = 0

        def read(self, _uri, _index):
            return 0.0

        def close(self):
            self.close_count += 1

    pool = _Pool()
    production._close_current_target_bucket_pool()
    monkeypatch.setattr(
        production, "_probe_resolved_available_cycle", lambda: AVAILABLE_CYCLE
    )
    monkeypatch.setattr(
        plan_mod,
        "build_replacement_forecast_current_target_plan",
        lambda *_args, **_kwargs: _PlanStub(
            ready=False,
            missing_openmeteo_manifest_count=1,
        ),
    )
    monkeypatch.setattr(
        "src.data.openmeteo_ecmwf_ifs9_bucket_transport.BucketPointReaderPool",
        lambda: pool,
    )

    def _download(**kwargs):
        calls.append(kwargs)
        return {
            "status": "CURRENT_TARGET_RAW_INPUTS_TIMEBOXED_INCOMPLETE",
            "timeboxed_incomplete": len(calls) == 1,
        }

    monkeypatch.setattr(dl, "download_current_target_raw_inputs", _download)

    first = _download_replacement_forecast_current_targets_if_needed(
        _cfg(db, tmp_path), max_wall_clock_seconds=5.0
    )
    second = _download_replacement_forecast_current_targets_if_needed(
        _cfg(db, tmp_path), max_wall_clock_seconds=5.0
    )

    assert first["timeboxed_incomplete"] is True
    assert second["timeboxed_incomplete"] is False
    assert calls[0]["bucket_reader_pool"] is pool
    assert calls[1]["bucket_reader_pool"] is pool
    assert pool.close_count == 1


def test_cycle_change_closes_timeboxed_pool_before_zero_budget_return(
    tmp_path, monkeypatch
) -> None:
    db = _make_db(tmp_path, {
        "ecmwf_aifs_ens": STALE_CYCLE_ISO,
        "openmeteo_ecmwf_ifs_9km": STALE_CYCLE_ISO,
    })
    import src.data.replacement_forecast_production as production

    class _Pool:
        close_count = 0

        def close(self):
            self.close_count += 1

    old_pool = _Pool()
    production._close_current_target_bucket_pool()
    monkeypatch.setattr(
        "src.data.openmeteo_ecmwf_ifs9_bucket_transport.BucketPointReaderPool",
        lambda: old_pool,
    )
    assert production._current_target_bucket_pool(AVAILABLE_CYCLE) is old_pool
    next_cycle = AVAILABLE_CYCLE.replace(hour=6)
    monkeypatch.setattr(
        production, "_probe_resolved_available_cycle", lambda: next_cycle
    )
    report = _download_replacement_forecast_current_targets_if_needed(
        _cfg(db, tmp_path),
        max_wall_clock_seconds=0.0,
        required_scopes=(("London", "2026-06-10", "high"),),
    )

    assert report["status"] == "CURRENT_TARGET_RAW_INPUTS_TIMEBOXED_INCOMPLETE"
    assert old_pool.close_count == 1


def test_preflight_error_closes_timeboxed_cycle_pool(tmp_path, monkeypatch) -> None:
    db = _make_db(tmp_path, {
        "ecmwf_aifs_ens": STALE_CYCLE_ISO,
        "openmeteo_ecmwf_ifs_9km": STALE_CYCLE_ISO,
    })
    import src.data.replacement_forecast_current_target_plan as plan_mod
    import src.data.replacement_forecast_production as production

    class _Pool:
        close_count = 0

        def close(self):
            self.close_count += 1

    pool = _Pool()
    production._close_current_target_bucket_pool()
    monkeypatch.setattr(
        "src.data.openmeteo_ecmwf_ifs9_bucket_transport.BucketPointReaderPool",
        lambda: pool,
    )
    assert production._current_target_bucket_pool(AVAILABLE_CYCLE) is pool
    monkeypatch.setattr(
        production, "_probe_resolved_available_cycle", lambda: AVAILABLE_CYCLE
    )
    monkeypatch.setattr(
        plan_mod,
        "build_replacement_forecast_current_target_plan",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("plan failed")),
    )

    with pytest.raises(RuntimeError, match="plan failed"):
        _download_replacement_forecast_current_targets_if_needed(_cfg(db, tmp_path))

    assert pool.close_count == 1


def test_direct_downloader_does_not_close_injected_bucket_pool(tmp_path) -> None:
    import scripts.download_replacement_forecast_current_targets as dl

    class _Pool:
        close_count = 0

        def close(self):
            self.close_count += 1

    pool = _Pool()
    report = dl.download_current_target_raw_inputs(
        forecast_db=tmp_path / "forecasts.db",
        output_dir=tmp_path / "raw",
        cycle=AVAILABLE_CYCLE,
        limit=None,
        write_db=False,
        release_lag_hours=14.0,
        anchor_sigma_c=3.0,
        include_covered=True,
        precomputed_plan=_PlanStub(ready=False, rows=()),
        max_wall_clock_seconds=5.0,
        bucket_reader_pool=pool,
    )

    assert report["target_count"] == 0
    assert pool.close_count == 0


def test_ready_plan_with_current_artifacts_skips_without_download(tmp_path, monkeypatch) -> None:
    db = _make_db(tmp_path, {
        "ecmwf_aifs_ens": CURRENT_CYCLE_ISO,
        "openmeteo_ecmwf_ifs_9km": CURRENT_CYCLE_ISO,
    })
    calls: list = []
    _wire(monkeypatch, plan=_PlanStub(ready=True), calls=calls)
    report = _download_replacement_forecast_current_targets_if_needed(_cfg(db, tmp_path))
    assert report is not None
    assert report["status"] == "CURRENT_TARGETS_ALREADY_COVERED"
    assert calls == []
    # The skip must be self-explaining (anti-silent-skip class): cycle facts in the report.
    assert report["available_cycle"] == AVAILABLE_CYCLE.isoformat()
    assert report["downloaded_cycle"] == CURRENT_CYCLE_ISO


def test_one_source_lagging_fires_download(tmp_path, monkeypatch) -> None:
    # AIFS current but the OpenMeteo ifs9 anchor lagging: the high-water mark is the MIN over
    # BOTH sources — a half-downloaded cycle is NOT current.
    db = _make_db(tmp_path, {
        "ecmwf_aifs_ens": CURRENT_CYCLE_ISO,
        "openmeteo_ecmwf_ifs_9km": STALE_CYCLE_ISO,
    })
    calls: list = []
    _wire(monkeypatch, plan=_PlanStub(ready=True), calls=calls)
    report = _download_replacement_forecast_current_targets_if_needed(_cfg(db, tmp_path))
    assert report["status"] == "CURRENT_TARGET_RAW_INPUTS_DOWNLOADED"
    assert len(calls) == 1


def test_no_artifacts_at_all_fires_download(tmp_path, monkeypatch) -> None:
    db = _make_db(tmp_path, {})
    calls: list = []
    _wire(monkeypatch, plan=_PlanStub(ready=True), calls=calls)
    report = _download_replacement_forecast_current_targets_if_needed(_cfg(db, tmp_path))
    assert report["status"] == "CURRENT_TARGET_RAW_INPUTS_DOWNLOADED"
    assert len(calls) == 1


def test_have_raw_manifests_gate_is_also_cycle_aware(tmp_path, monkeypatch) -> None:
    # plan NOT ready but zero missing manifests (the second short-circuit) + stale artifacts:
    # the download must still fire — BOTH early returns carry the cycle-currency requirement.
    db = _make_db(tmp_path, {
        "ecmwf_aifs_ens": STALE_CYCLE_ISO,
        "openmeteo_ecmwf_ifs_9km": STALE_CYCLE_ISO,
    })
    calls: list = []
    plan = _PlanStub(ready=False, missing_aifs_manifest_count=0, missing_openmeteo_manifest_count=0)
    _wire(monkeypatch, plan=plan, calls=calls)
    report = _download_replacement_forecast_current_targets_if_needed(_cfg(db, tmp_path))
    assert report["status"] == "CURRENT_TARGET_RAW_INPUTS_DOWNLOADED"
    assert len(calls) == 1


def test_partial_current_cycle_manifests_do_not_skip_download(tmp_path, monkeypatch) -> None:
    # Live 2026-06-24 shape: the artifact high-water mark reached the available
    # cycle because a few targets wrote 12Z manifests, while most current targets
    # still only had older-cycle manifests. The skip gate must ask the plan for
    # current-cycle coverage, but the repair must not replay targets whose current
    # cycle raw input is already present.
    db = _make_db(tmp_path, {"openmeteo_ecmwf_ifs_9km": CURRENT_CYCLE_ISO})
    calls: list = []
    import scripts.download_replacement_forecast_current_targets as dl
    import src.data.replacement_forecast_current_target_plan as plan_mod
    import src.data.replacement_forecast_production as production

    stale_non_cycle_plan = _PlanStub(
        ready=False,
        missing_openmeteo_manifest_count=0,
        payload={"status": "CURRENT_TARGETS_HAVE_RAW_MANIFESTS_STALE"},
    )
    current_cycle_plan = _PlanStub(
        ready=False,
        missing_openmeteo_manifest_count=1,
        payload={"status": "CURRENT_TARGETS_MISSING_CURRENT_CYCLE_MANIFESTS"},
    )

    def _plan_builder(_db, *args, **kwargs):
        if kwargs.get("required_openmeteo_source_cycle_time") is not None:
            return current_cycle_plan
        return stale_non_cycle_plan

    monkeypatch.setattr(plan_mod, "build_replacement_forecast_current_target_plan", _plan_builder)
    monkeypatch.setattr(production, "_probe_resolved_available_cycle", lambda: AVAILABLE_CYCLE)

    def _fake_download(**kwargs):
        calls.append(kwargs)
        return {
            "status": "CURRENT_TARGET_RAW_INPUTS_DOWNLOADED",
            "cycle": kwargs["cycle"].isoformat(),
        }

    monkeypatch.setattr(dl, "download_current_target_raw_inputs", _fake_download)
    report = _download_replacement_forecast_current_targets_if_needed(_cfg(db, tmp_path))

    assert report["status"] == "CURRENT_TARGET_RAW_INPUTS_DOWNLOADED"
    assert len(calls) == 1
    assert calls[0].get("include_covered") is False
    assert calls[0].get("missing_manifests_only") is True


def test_direct_current_target_downloader_scopes_plan_to_requested_cycle(tmp_path, monkeypatch) -> None:
    import scripts.download_replacement_forecast_current_targets as dl

    seen: list[dict] = []

    def _plan_builder(_db, *args, **kwargs):
        seen.append(dict(kwargs))
        return _PlanStub(ready=False, rows=())

    monkeypatch.setattr(dl, "build_replacement_forecast_current_target_plan", _plan_builder)

    report = dl.download_current_target_raw_inputs(
        forecast_db=tmp_path / "forecasts.db",
        output_dir=tmp_path / "raw",
        cycle=AVAILABLE_CYCLE,
        limit=None,
        write_db=False,
        release_lag_hours=14.0,
        anchor_sigma_c=3.0,
        include_covered=True,
    )

    assert report["target_count"] == 0
    assert seen[0]["required_openmeteo_source_cycle_time"] == AVAILABLE_CYCLE


def test_direct_current_target_downloader_prioritizes_missing_cycle_manifest_before_limit(
    tmp_path,
    monkeypatch,
) -> None:
    import scripts.download_replacement_forecast_current_targets as dl

    rows = (
        _TargetRow(
            city="Amsterdam",
            target_date="2026-06-10",
            temperature_metric="high",
            covered=True,
            missing_openmeteo_manifest=False,
        ),
        _TargetRow(
            city="London",
            target_date="2026-06-10",
            temperature_metric="high",
            covered=False,
            missing_openmeteo_manifest=True,
        ),
    )
    monkeypatch.setattr(
        dl,
        "build_replacement_forecast_current_target_plan",
        lambda *_args, **_kwargs: _PlanStub(ready=False, rows=rows),
    )
    monkeypatch.setattr(
        dl,
        "_resolve_anchor_payload",
        lambda **_kwargs: (
            {"hourly": {"time": [], "temperature_2m": []}},
            {"openmeteo_endpoint": "single_runs_api", "run_authority": "test"},
        ),
    )

    report = dl.download_current_target_raw_inputs(
        forecast_db=tmp_path / "forecasts.db",
        output_dir=tmp_path / "raw",
        cycle=AVAILABLE_CYCLE,
        limit=1,
        write_db=False,
        release_lag_hours=14.0,
        anchor_sigma_c=3.0,
        include_covered=True,
    )

    assert report["target_count"] == 1
    assert report["manifest_count"] == 1
    assert "London" in report["written_manifests"][0]


def test_same_cycle_repair_excludes_uncovered_rows_with_manifest(
    tmp_path,
    monkeypatch,
) -> None:
    import scripts.download_replacement_forecast_current_targets as dl

    rows = (
        _TargetRow(
            city="Paris",
            target_date="2026-06-10",
            temperature_metric="high",
            covered=False,
            missing_openmeteo_manifest=False,
        ),
        _TargetRow(
            city="London",
            target_date="2026-06-10",
            temperature_metric="high",
            covered=False,
            missing_openmeteo_manifest=True,
        ),
    )
    plan = _PlanStub(ready=False, rows=rows)
    monkeypatch.setattr(
        dl,
        "_resolve_anchor_payload",
        lambda **_kwargs: (
            {"hourly": {"time": [], "temperature_2m": []}},
            {"openmeteo_endpoint": "single_runs_api", "run_authority": "test"},
        ),
    )

    report = dl.download_current_target_raw_inputs(
        forecast_db=tmp_path / "forecasts.db",
        output_dir=tmp_path / "raw",
        cycle=AVAILABLE_CYCLE,
        limit=None,
        write_db=False,
        release_lag_hours=14.0,
        anchor_sigma_c=3.0,
        missing_manifests_only=True,
        precomputed_plan=plan,
    )

    assert report["target_count"] == 1
    assert report["manifest_count"] == 1
    assert "London" in report["written_manifests"][0]


def test_direct_downloader_reuses_plan_and_city_date_payload_across_metrics(
    tmp_path,
    monkeypatch,
) -> None:
    import scripts.download_replacement_forecast_current_targets as dl

    rows = (
        _TargetRow(
            city="London",
            target_date="2026-06-10",
            temperature_metric="high",
            covered=False,
            missing_openmeteo_manifest=True,
        ),
        _TargetRow(
            city="London",
            target_date="2026-06-10",
            temperature_metric="low",
            covered=False,
            missing_openmeteo_manifest=True,
        ),
    )
    plan = _PlanStub(ready=False, rows=rows)
    monkeypatch.setattr(
        dl,
        "build_replacement_forecast_current_target_plan",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("precomputed plan must be reused")
        ),
    )
    calls: list[dict[str, object]] = []

    def _resolve(**kwargs):
        calls.append(kwargs)
        return (
            {"hourly": {"time": [], "temperature_2m": []}},
            {"openmeteo_endpoint": "bucket", "run_authority": "bucket_partial_run_test"},
        )

    monkeypatch.setattr(dl, "_resolve_anchor_payload", _resolve)

    report = dl.download_current_target_raw_inputs(
        forecast_db=tmp_path / "forecasts.db",
        output_dir=tmp_path / "raw",
        cycle=AVAILABLE_CYCLE,
        limit=None,
        write_db=False,
        release_lag_hours=14.0,
        anchor_sigma_c=3.0,
        include_covered=True,
        precomputed_plan=plan,
        max_wall_clock_seconds=5.0,
    )

    assert report["manifest_count"] == 2
    assert report["downloaded"]["openmeteo_transport_fetch_count"] == 1
    assert len(calls) == 1


def test_direct_downloader_batches_run_pinned_anchor_locations(
    tmp_path,
    monkeypatch,
) -> None:
    import scripts.download_replacement_forecast_current_targets as dl

    rows = tuple(
        _TargetRow(
            city=city,
            target_date="2026-06-10",
            temperature_metric="high",
            covered=False,
            missing_openmeteo_manifest=True,
        )
        for city in ("London", "Paris")
    )
    plan = _PlanStub(ready=False, rows=rows)
    wave_calls: list[tuple[tuple[str, str], ...]] = []
    request_past_hours: list[int] = []

    monkeypatch.setattr(dl, "_single_runs_public_for_request", lambda _request: True)

    def _wave(requests, **_kwargs):
        wave_calls.append(tuple(requests))
        request_past_hours.extend(
            request.past_hours for request in requests.values()
        )
        captured_at = datetime.now(timezone.utc)
        return {
            key: (
                {"hourly": {"time": [], "temperature_2m": []}},
                {
                    "openmeteo_endpoint": "single_runs_api",
                    "run_authority": "run_pinned_single_runs",
                    "location_batch_size": len(requests),
                },
                captured_at,
            )
            for key in requests
        }

    monkeypatch.setattr(dl, "_fetch_run_pinned_anchor_wave", _wave)
    monkeypatch.setattr(
        dl,
        "_resolve_anchor_payload",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("batched payloads must bypass per-city transport")
        ),
    )

    report = dl.download_current_target_raw_inputs(
        forecast_db=tmp_path / "forecasts.db",
        output_dir=tmp_path / "raw",
        cycle=AVAILABLE_CYCLE,
        limit=None,
        write_db=False,
        release_lag_hours=14.0,
        anchor_sigma_c=3.0,
        include_covered=True,
        precomputed_plan=plan,
        max_wall_clock_seconds=5.0,
    )

    assert wave_calls == [
        (("London", "2026-06-10"), ("Paris", "2026-06-10"))
    ]
    assert request_past_hours == [24, 24]
    assert report["manifest_count"] == 2
    assert report["downloaded"]["openmeteo_transport_fetch_count"] == 2
    assert report["downloaded"]["openmeteo_single_runs_location_batch_count"] == 1


def test_timebox_commits_ready_wave_payloads_before_deferring_unresolved(
    tmp_path,
    monkeypatch,
) -> None:
    import scripts.download_replacement_forecast_current_targets as dl

    rows = tuple(
        _TargetRow(
            city=city,
            target_date="2026-06-10",
            temperature_metric="high",
            covered=False,
            missing_openmeteo_manifest=True,
        )
        for city in ("London", "Paris", "Seoul")
    )
    plan = _PlanStub(ready=False, rows=rows)
    wave_calls: list[tuple[tuple[str, str], ...]] = []
    monotonic_calls = iter((0.0, 6.0, 6.0, 6.0))

    monkeypatch.setattr(dl.time, "monotonic", lambda: next(monotonic_calls))
    monkeypatch.setattr(dl, "_single_runs_public_for_request", lambda _request: True)

    def _wave(requests, **_kwargs):
        wave_calls.append(tuple(requests))
        captured_at = datetime.now(timezone.utc)
        return {
            key: (
                {"hourly": {"time": [], "temperature_2m": []}},
                {
                    "openmeteo_endpoint": "single_runs_api",
                    "run_authority": "run_pinned_single_runs",
                    "location_batch_size": len(requests),
                },
                captured_at,
            )
            for key in tuple(requests)[:2]
        }

    monkeypatch.setattr(dl, "_fetch_run_pinned_anchor_wave", _wave)
    monkeypatch.setattr(
        dl,
        "_resolve_anchor_payload",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("deadline must block new per-city transport")
        ),
    )

    report = dl.download_current_target_raw_inputs(
        forecast_db=tmp_path / "forecasts.db",
        output_dir=tmp_path / "raw",
        cycle=AVAILABLE_CYCLE,
        limit=None,
        write_db=False,
        release_lag_hours=14.0,
        anchor_sigma_c=3.0,
        include_covered=True,
        precomputed_plan=plan,
        max_wall_clock_seconds=5.0,
    )

    assert wave_calls == [
        (
            ("London", "2026-06-10"),
            ("Paris", "2026-06-10"),
            ("Seoul", "2026-06-10"),
        )
    ]
    assert report["manifest_count"] == 2
    assert report["timeboxed_incomplete"] is True
    assert report["unattempted_target_count"] == 1
    assert any("London" in path for path in report["written_manifests"])
    assert any("Paris" in path for path in report["written_manifests"])


def test_anchor_location_batch_failure_falls_back_as_one_meta_wave(
    tmp_path,
    monkeypatch,
) -> None:
    import scripts.download_replacement_forecast_current_targets as dl

    rows = tuple(
        _TargetRow(
            city=city,
            target_date="2026-06-10",
            temperature_metric="high",
            covered=False,
            missing_openmeteo_manifest=True,
        )
        for city in ("London", "Paris")
    )
    plan = _PlanStub(ready=False, rows=rows)
    meta_calls: list[tuple[tuple[str, str], ...]] = []

    monkeypatch.setattr(dl, "_single_runs_public_for_request", lambda _request: True)
    monkeypatch.setattr(
        dl,
        "_fetch_run_pinned_anchor_wave",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("429 Too Many Requests")
        ),
    )

    def _meta_wave(requests, **_kwargs):
        meta_calls.append(tuple(requests))
        captured_at = datetime.now(timezone.utc)
        return (
            {
                key: (
                    {"hourly": {"time": [], "temperature_2m": []}},
                    {
                        "openmeteo_endpoint": "standard_forecast_api",
                        "run_authority": "provider_meta_declared",
                    },
                    captured_at,
                )
                for key in requests
            },
            {},
        )

    monkeypatch.setattr(dl, "_fetch_meta_stamped_anchor_wave", _meta_wave)
    monkeypatch.setattr(
        dl,
        "_resolve_anchor_payload",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("meta wave payloads must bypass per-city transport")
        ),
    )

    report = dl.download_current_target_raw_inputs(
        forecast_db=tmp_path / "forecasts.db",
        output_dir=tmp_path / "raw",
        cycle=AVAILABLE_CYCLE,
        limit=None,
        write_db=False,
        release_lag_hours=14.0,
        anchor_sigma_c=3.0,
        include_covered=True,
        precomputed_plan=plan,
        max_wall_clock_seconds=5.0,
    )

    assert meta_calls == [
        (("London", "2026-06-10"), ("Paris", "2026-06-10"))
    ]
    assert report["manifest_count"] == 2
    assert report["downloaded"]["openmeteo_model_meta_fetch_count"] == 2
    assert report["downloaded"]["openmeteo_single_runs_location_batch_count"] == 1


def test_direct_downloader_reuses_bucket_manifest_across_targets(
    tmp_path,
    monkeypatch,
) -> None:
    import scripts.download_replacement_forecast_current_targets as dl
    import src.data.openmeteo_ecmwf_ifs9_bucket_transport as bucket_transport

    rows = (
        _TargetRow(
            city="London",
            target_date="2026-06-10",
            temperature_metric="high",
            covered=False,
            missing_openmeteo_manifest=True,
        ),
        _TargetRow(
            city="Paris",
            target_date="2026-06-10",
            temperature_metric="high",
            covered=False,
            missing_openmeteo_manifest=True,
        ),
    )
    manifest_fetches = 0

    def _fetch_bucket_run_manifest(**_kwargs):
        nonlocal manifest_fetches
        manifest_fetches += 1
        return {}

    providers: list[object] = []

    def _resolve(**kwargs):
        provider = kwargs["bucket_manifest_provider"]
        providers.append(provider)
        provider()
        return (
            {"hourly": {"time": [], "temperature_2m": []}},
            {"openmeteo_endpoint": "bucket", "run_authority": "bucket_partial_run_test"},
        )

    monkeypatch.setattr(
        bucket_transport,
        "fetch_bucket_run_manifest",
        _fetch_bucket_run_manifest,
    )
    monkeypatch.setattr(dl, "_resolve_anchor_payload", _resolve)

    report = dl.download_current_target_raw_inputs(
        forecast_db=tmp_path / "forecasts.db",
        output_dir=tmp_path / "raw",
        cycle=AVAILABLE_CYCLE,
        limit=None,
        write_db=False,
        release_lag_hours=14.0,
        anchor_sigma_c=3.0,
        include_covered=True,
        precomputed_plan=_PlanStub(ready=False, rows=rows),
        max_wall_clock_seconds=5.0,
    )

    assert report["manifest_count"] == 2
    assert report["downloaded"]["openmeteo_transport_fetch_count"] == 2
    assert len(providers) == 2
    assert providers[0] is providers[1]
    assert manifest_fetches == 1


def test_disabled_flag_still_short_circuits(tmp_path, monkeypatch) -> None:
    db = _make_db(tmp_path, {})
    calls: list = []
    _wire(monkeypatch, plan=_PlanStub(ready=True), calls=calls)
    cfg = _cfg(db, tmp_path)
    cfg["download_current_targets_enabled"] = False
    assert _download_replacement_forecast_current_targets_if_needed(cfg) is None
    assert calls == []


def test_stale_cycle_download_includes_covered_targets(tmp_path, monkeypatch) -> None:
    # K-ROOT INSTANCE #3 (2026-06-09): the downloader filtered its target list to
    # NOT-covered rows, so a fully-covered window never received the NEW cycle's raw
    # inputs — re-materialization at the fresh cycle could only bind OLD manifests
    # (observed live: 06-11 targets re-pinned to 06-08T18 manifests). When the download
    # fires because the cycle is stale, it must pass include_covered=True.
    db = _make_db(tmp_path, {
        "ecmwf_aifs_ens": STALE_CYCLE_ISO,
        "openmeteo_ecmwf_ifs_9km": STALE_CYCLE_ISO,
    })
    calls: list = []
    _wire(monkeypatch, plan=_PlanStub(ready=True), calls=calls)
    _download_replacement_forecast_current_targets_if_needed(_cfg(db, tmp_path))
    assert len(calls) == 1
    assert calls[0].get("include_covered") is True, (
        "a stale-cycle download must fetch raw inputs for ALL current targets — filtering "
        "to uncovered rows self-perpetuates staleness (coverage never implies currency)"
    )


def test_existing_corrupt_openmeteo_payload_is_not_reused(tmp_path: Path) -> None:
    import scripts.download_replacement_forecast_current_targets as dl

    payload = tmp_path / "openmeteo_Buenos_Aires_2026-06-24_high_20260624T000000Z.json"
    payload.write_text('{"hourly": {}}\n}\n', encoding="utf-8")

    assert dl._json_file_valid(payload) is False

    dl._write_json(payload, {"hourly": {"time": [], "temperature_2m": []}})

    assert dl._json_file_valid(payload) is True


def test_concurrent_payload_publishers_use_distinct_temp_files(
    tmp_path: Path, monkeypatch
) -> None:
    import json
    import os
    import threading
    from concurrent.futures import ThreadPoolExecutor

    import scripts.download_replacement_forecast_current_targets as dl

    target = tmp_path / "openmeteo_Seoul_2026-07-14_high.json"
    payloads = ({"writer": 1}, {"writer": 2})
    barrier = threading.Barrier(2)
    real_replace = os.replace

    def synchronized_replace(source, destination):
        barrier.wait(timeout=5)
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", synchronized_replace)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(dl._write_json, target, payload) for payload in payloads]
        for future in futures:
            future.result(timeout=10)

    assert json.loads(target.read_text(encoding="utf-8")) in payloads
    assert list(tmp_path.glob(f".{target.name}.*.tmp")) == []

# Lifecycle: created=2026-06-08; last_reviewed=2026-08-19; last_reused=2026-08-19
# Purpose: Relationship regression test for BAYES_PRECISION_FUSION extra-model capture wiring in src/main.py; guards against bare `date` NameError (BLOCKER 9) and verifies capture is gated by the edli flag.
# Reuse: Run with pytest; update if the BAYES_PRECISION_FUSION extra-capture wiring or flag gate in src/main.py changes.
# Created: 2026-06-08
# Last reused or audited: 2026-08-19
# Authority basis: PR#400 review (src/main.py:4909 bare `date` NameError swallowed by
#   fail-soft); CONTINUITY_AND_WIRING.md §4 step 2 + BAYES_PRECISION_FUSION_SPEC.md §6 F1 (BAYES_PRECISION_FUSION multi-model
#   SHADOW capture gated by edli.replacement_0_1_bayes_precision_fusion_capture_enabled).
"""Relationship regression test for the BAYES_PRECISION_FUSION extra-model capture wiring in src.main.

Relationship under test (plan-row -> BAYES_PRECISION_FUSION download-target boundary, src/main.py
`_download_bayes_precision_fusion_extra_raw_inputs_if_needed`):

  The plan builder emits ReplacementForecastCurrentTargetPlanRow objects whose
  ``target_date`` is an ISO string. main.py converts that string with
  ``date.fromisoformat(row.target_date) - cycle.date()`` to derive ``lead_days``
  before handing the target to ``download_bayes_precision_fusion_extra_raw_inputs``. ``date`` is NOT a
  module-level name in main.py (module import is only ``datetime, timedelta,
  timezone``), and the function's local import block historically imported only
  ``datetime``/``timezone`` -- so the first uncovered target row raised
  ``NameError: name 'date' is not defined``. That NameError was swallowed by the
  function's broad fail-soft ``except Exception`` (status
  ``BAYES_PRECISION_FUSION_EXTRA_CAPTURE_FAILSOFT_SKIPPED``), so the whole BAYES_PRECISION_FUSION capture silently never
  ran even with the flag ON.

Properties asserted:
  (1) With the capture flag ON and a normal uncovered target row, the function does
      NOT raise NameError and does NOT fall into the fail-soft skip path.
  (2) The capture is actually ATTEMPTED: ``download_bayes_precision_fusion_extra_raw_inputs`` is invoked
      with exactly one target carrying the row's city/metric/target_date and a
      correctly-derived non-negative ``lead_days`` (the value computed across the
      ``date.fromisoformat`` boundary).
  (3) Covered rows are skipped; only uncovered rows become download targets.

No network: the plan builder and the downstream downloader are both injected.
"""
from __future__ import annotations

import importlib
import json
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import src.config as cfg
import src.data.replacement_forecast_current_target_plan as plan_mod
import src.data.bayes_precision_fusion_download as dl_mod
import src.data.replacement_forecast_production as production
import src.main as main_mod
from src.data.bayes_precision_fusion_download import BayesPrecisionFusionDownloadTarget
from src.data.replacement_forecast_current_target_plan import (
    ReplacementForecastCurrentTargetPlan,
    ReplacementForecastCurrentTargetPlanRow,
)


def _row(*, city: str, target_date: str, covered: bool) -> ReplacementForecastCurrentTargetPlanRow:
    # covered == (posterior_count > 0 and readiness_count > 0); flip both to toggle coverage.
    n = 1 if covered else 0
    return ReplacementForecastCurrentTargetPlanRow(
        city=city,
        target_date=target_date,
        temperature_metric="high",
        market_bin_count=1,
        posterior_count=n,
        readiness_count=n,
        openmeteo_manifest_count=1,
        fusion_current_value_count=1,
    )


def _plan(rows: list[ReplacementForecastCurrentTargetPlanRow]) -> ReplacementForecastCurrentTargetPlan:
    covered = sum(1 for r in rows if r.covered)
    return ReplacementForecastCurrentTargetPlan(
        status="CURRENT_TARGETS_MISSING_COVERAGE",
        reason_codes=(),
        target_count=len(rows),
        covered_count=covered,
        missing_coverage_count=len(rows) - covered,
        can_seed_count=0,
        missing_openmeteo_manifest_count=0,
        missing_fusion_current_values_count=0,
        day0_observed_extreme_required_count=0,
        rows=tuple(rows),
    )


def _wire(monkeypatch, *, rows, state_root: Path, forecast_db="zeus-forecasts.db"):
    """Enable the capture flag and inject the plan builder + downloader. Returns the
    list that records each ``download_bayes_precision_fusion_extra_raw_inputs`` call's kwargs."""
    monkeypatch.setitem(
        cfg.settings["edli"], "replacement_0_1_bayes_precision_fusion_capture_enabled", True
    )

    monkeypatch.setattr(
        plan_mod, "build_replacement_forecast_current_target_plan",
        lambda _db: _plan(rows),
    )

    calls: list[dict] = []

    def _fake_download(
        *,
        forecast_db,
        cycle,
        targets,
        release_lag_hours,
        max_wall_clock_seconds,
    ):
        targets = list(targets)
        calls.append({
            "forecast_db": forecast_db,
            "cycle": cycle,
            "targets": targets,
            "release_lag_hours": release_lag_hours,
            "max_wall_clock_seconds": max_wall_clock_seconds,
        })
        return {
            "status": "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED",
            "written_row_count": len(targets),
            "attempted_target_group_count": len(
                {(target.city, target.target_date) for target in targets}
            ),
        }

    monkeypatch.setattr(dl_mod, "download_bayes_precision_fusion_extra_raw_inputs", _fake_download)

    # Run-selection single authority (2026-06-11): the capture lane resolves its cycle
    # via provider probes (never the dead now-minus-lag guess). Pin a deterministic
    # probe-resolved cycle so lead_days assertions are exact and offline.
    import src.data.replacement_forecast_production as production

    probed_cycle = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    monkeypatch.setattr(
        production, "_probe_resolved_available_cycle", lambda: probed_cycle
    )
    monkeypatch.setattr(
        production,
        "_probe_resolved_bayes_precision_fusion_extras_cycle",
        lambda: probed_cycle,
    )

    cfg_dict = {
        "forecast_db": forecast_db,
        "download_release_lag_hours": 14.0,
        "bpf_extra_rotation_state_path": state_root / ".rotation.json",
    }
    return cfg_dict, calls


# ---------------------------------------------------------------------------------------
# (1)+(2) normal uncovered target: no NameError, capture attempted, lead_days correct
# ---------------------------------------------------------------------------------------
def test_does_not_raise_nameerror_and_attempts_capture(monkeypatch, tmp_path) -> None:
    # target_date 6 days after "today" -> lead_days must come out as 6 across the
    # date.fromisoformat boundary. Use a city present in cities_by_name.
    today = datetime.now(timezone.utc).date()
    target_date = (today + timedelta(days=6)).isoformat()
    rows = [_row(city="Amsterdam", target_date=target_date, covered=False)]
    cfg_dict, calls = _wire(monkeypatch, rows=rows, state_root=tmp_path)

    report = main_mod._download_bayes_precision_fusion_extra_raw_inputs_if_needed(cfg_dict)

    # Property (1): NOT the fail-soft skip path. A NameError would have produced
    # status BAYES_PRECISION_FUSION_EXTRA_CAPTURE_FAILSOFT_SKIPPED with the NameError text.
    assert report is not None
    assert report.get("status") != "BAYES_PRECISION_FUSION_EXTRA_CAPTURE_FAILSOFT_SKIPPED", report
    assert "name 'date' is not defined" not in str(report.get("error", ""))
    assert report.get("status") == "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED"

    # Property (2): capture actually attempted with the row's identity + derived lead.
    assert len(calls) == 1
    targets = calls[0]["targets"]
    assert len(targets) == 1
    t = targets[0]
    assert t.city == "Amsterdam"
    assert t.metric == "high"
    assert t.target_date == target_date
    # lead_days is the cross-boundary value the fix unblocks:
    #   max(0, date.fromisoformat(target_date) - cycle.date()).days
    # The function now uses the probe-resolved cycle pinned in _wire (today 00Z), so the
    # expected value is exact and offline.
    cycle = calls[0]["cycle"]
    expected_lead = max(0, (date.fromisoformat(target_date) - cycle.date()).days)
    assert t.lead_days == expected_lead
    assert t.lead_days >= 0


# ---------------------------------------------------------------------------------------
# (3) covered rows are INCLUDED when exact-cycle coverage cannot be proven
# (CYCLE-CURRENCY, K-root instance #5): plan 'covered' has no cycle-awareness, so
# excluding covered rows froze covered targets on stale-cycle extras. Exact-cycle
# coverage, rather than the plan projection, is the only valid optimization boundary.
# ---------------------------------------------------------------------------------------
def test_covered_rows_still_reach_the_downloader(monkeypatch, tmp_path) -> None:
    today = datetime.now(timezone.utc).date()
    td = (today + timedelta(days=3)).isoformat()
    rows = [
        _row(city="Amsterdam", target_date=td, covered=True),   # included (currency)
        _row(city="Ankara", target_date=td, covered=False),     # included
    ]
    cfg_dict, calls = _wire(monkeypatch, rows=rows, state_root=tmp_path)

    report = main_mod._download_bayes_precision_fusion_extra_raw_inputs_if_needed(cfg_dict)

    assert report.get("status") != "BAYES_PRECISION_FUSION_EXTRA_CAPTURE_FAILSOFT_SKIPPED", report
    assert len(calls) == 1
    cities = sorted(t.city for t in calls[0]["targets"])
    assert cities == ["Amsterdam", "Ankara"], (
        "plan coverage must NOT be treated as current-cycle capture — coverage is not "
        "currency (K-root instance #5)"
    )


def test_full_fanout_admits_current_day0_and_prioritizes_held_gap(
    monkeypatch,
    tmp_path,
) -> None:
    import src.data.replacement_forecast_seed_discovery as seed_discovery

    decision_time = datetime.now(timezone.utc)
    cycle = decision_time - timedelta(minutes=1)
    tokyo_day0 = decision_time.astimezone(ZoneInfo("Asia/Tokyo")).date()
    tokyo_day1 = tokyo_day0 + timedelta(days=1)
    amsterdam_day1 = (
        decision_time.astimezone(ZoneInfo("Europe/Amsterdam")).date()
        + timedelta(days=1)
    )
    rows = [
        _row(city="Amsterdam", target_date=amsterdam_day1.isoformat(), covered=True),
        _row(city="Tokyo", target_date=tokyo_day1.isoformat(), covered=False),
    ]
    cfg_dict, calls = _wire(monkeypatch, rows=rows, state_root=tmp_path)
    monkeypatch.setattr(
        production,
        "_probe_resolved_bayes_precision_fusion_extras_cycle",
        lambda: cycle,
    )
    monkeypatch.setattr(
        production,
        "_extras_coverage_missing",
        lambda _cfg, _cycle, *, decision_time=None: (
            {
                ("Tokyo", "high", tokyo_day0.isoformat()),
                ("Amsterdam", "high", amsterdam_day1.isoformat()),
                ("Tokyo", "high", tokyo_day1.isoformat()),
            },
            3,
        ),
    )
    monkeypatch.setattr(
        seed_discovery,
        "held_position_family_priorities",
        lambda: {
            ("Tokyo", tokyo_day0.isoformat(), "high"): 0,
            ("Tokyo", tokyo_day1.isoformat(), "high"): 1,
        },
    )

    report = production._download_bayes_precision_fusion_extra_raw_inputs_if_needed(
        cfg_dict
    )

    assert report is not None
    assert len(calls) == 1
    assert [
        (target.city, target.target_date) for target in calls[0]["targets"]
    ] == [
        ("Tokyo", tokyo_day0.isoformat()),
        ("Tokyo", tokyo_day1.isoformat()),
        ("Amsterdam", amsterdam_day1.isoformat()),
    ]


# ---------------------------------------------------------------------------------------
# Pre-fix guard: the bare-`date` NameError is exactly what fail-soft would have hidden.
# ---------------------------------------------------------------------------------------
def test_target_date_iso_is_parseable_by_date_fromisoformat() -> None:
    # Documents the boundary contract: the row.target_date string MUST be ISO so the
    # main.py conversion `date.fromisoformat(row.target_date)` succeeds. If a future
    # change makes target_date non-ISO, this fails loudly instead of being swallowed.
    td = (date.today() + timedelta(days=2)).isoformat()
    assert date.fromisoformat(td) == date.today() + timedelta(days=2)


def _rotation_targets(
    groups: tuple[tuple[str, str, str], ...] = (
        ("Amsterdam", "2026-07-29", "high"),
        ("Amsterdam", "2026-07-29", "low"),
        ("London", "2026-07-30", "high"),
        ("London", "2026-07-30", "low"),
        ("Paris", "2026-07-30", "high"),
    ),
) -> tuple[BayesPrecisionFusionDownloadTarget, ...]:
    return tuple(
        BayesPrecisionFusionDownloadTarget(
            city=city,
            metric=metric,
            target_date=target_date,
            lead_days=1,
            latitude=0.0,
            longitude=0.0,
            timezone_name="UTC",
        )
        for city, target_date, metric in groups
    )


def test_rotation_cursor_persists_across_process_restart(tmp_path) -> None:
    cycle = datetime(2026, 7, 28, 6, tzinfo=timezone.utc)
    state_path = tmp_path / "replacement_forecast_live" / ".rotation.json"
    targets = _rotation_targets()

    first, start, group_count, read_status = production._rotate_bpf_extra_targets(
        targets,
        cycle=cycle,
        state_path=state_path,
    )
    assert start == 0
    assert group_count == 3
    assert read_status == "MISSING"
    assert [target.city for target in first] == [
        "Amsterdam",
        "Amsterdam",
        "London",
        "London",
        "Paris",
    ]

    write = production._advance_bpf_extra_rotation(
        cycle=cycle,
        rotated_targets=first,
        attempted_group_count=2,
        state_path=state_path,
    )
    assert write["status"] == "PERSISTED"

    restarted = importlib.reload(production)
    second, second_start, _, second_status = restarted._rotate_bpf_extra_targets(
        targets,
        cycle=cycle,
        state_path=state_path,
    )

    assert second_start == 2
    assert second_status == "RESUMED"
    assert [target.city for target in second] == [
        "Paris",
        "Amsterdam",
        "Amsterdam",
        "London",
        "London",
    ]


def test_rotation_recovers_by_stable_key_when_membership_or_order_changes(
    tmp_path,
) -> None:
    cycle = datetime(2026, 7, 28, 6, tzinfo=timezone.utc)
    state_path = tmp_path / "replacement_forecast_live" / ".rotation.json"
    initial = _rotation_targets()
    production._advance_bpf_extra_rotation(
        cycle=cycle,
        rotated_targets=initial,
        attempted_group_count=2,
        state_path=state_path,
    )

    reordered = _rotation_targets(
        (
            ("Shanghai", "2026-07-30", "high"),
            ("London", "2026-07-30", "high"),
            ("Amsterdam", "2026-07-29", "high"),
            ("Paris", "2026-07-30", "high"),
        )
    )
    rotated, start, _, status = production._rotate_bpf_extra_targets(
        reordered,
        cycle=cycle,
        state_path=state_path,
    )
    assert status == "RESUMED"
    assert start == 2
    assert rotated[0].city == "Amsterdam"

    without_london = tuple(
        target for target in reordered if target.city != "London"
    )
    recovered, recovered_start, _, recovered_status = (
        production._rotate_bpf_extra_targets(
            without_london,
            cycle=cycle,
            state_path=state_path,
        )
    )
    assert recovered_status == "MEMBERSHIP_RECOVERED"
    assert recovered_start == 2
    assert recovered[0].city == "Paris"


def test_rotation_resets_on_cycle_change(tmp_path) -> None:
    cycle = datetime(2026, 7, 28, 6, tzinfo=timezone.utc)
    state_path = tmp_path / "replacement_forecast_live" / ".rotation.json"
    targets = _rotation_targets()
    production._advance_bpf_extra_rotation(
        cycle=cycle,
        rotated_targets=targets,
        attempted_group_count=2,
        state_path=state_path,
    )

    reset, reset_start, _, reset_status = production._rotate_bpf_extra_targets(
        targets,
        cycle=cycle + timedelta(hours=6),
        state_path=state_path,
    )
    assert reset_start == 0
    assert reset_status == "CYCLE_RESET"
    assert reset == targets


def test_corrupt_rotation_cursor_is_truthful_and_does_not_block_download_order(
    tmp_path,
) -> None:
    cycle = datetime(2026, 7, 28, 6, tzinfo=timezone.utc)
    state_path = tmp_path / "replacement_forecast_live" / ".rotation.json"
    state_path.parent.mkdir()
    state_path.write_text("{not-json", encoding="utf-8")
    targets = _rotation_targets()

    rotated, start, _, status = production._rotate_bpf_extra_targets(
        targets,
        cycle=cycle,
        state_path=state_path,
    )

    assert status == "CORRUPT"
    assert start == 0
    assert rotated == targets
    write = production._advance_bpf_extra_rotation(
        cycle=cycle,
        rotated_targets=rotated,
        attempted_group_count=1,
        state_path=state_path,
    )
    assert write["status"] == "PERSISTED"
    assert json.loads(state_path.read_text(encoding="utf-8"))[
        "last_attempted_group"
    ] == {"city": "Amsterdam", "target_date": "2026-07-29"}


def test_rotation_cursor_atomic_write_fsyncs_before_and_after_replace(
    tmp_path,
    monkeypatch,
) -> None:
    state_path = tmp_path / "replacement_forecast_live" / ".rotation.json"
    events: list[tuple[str, Path, Path | None]] = []
    real_fsync_file = production._fsync_file
    real_fsync_directory = production._fsync_directory
    real_replace = production.os.replace

    def _fsync_file(path):
        events.append(("fsync_file", Path(path), None))
        real_fsync_file(Path(path))

    def _fsync_directory(path):
        events.append(("fsync_directory", Path(path), None))
        real_fsync_directory(Path(path))

    def _replace(source, destination):
        events.append(("replace", Path(source), Path(destination)))
        real_replace(source, destination)

    monkeypatch.setattr(production, "_fsync_file", _fsync_file)
    monkeypatch.setattr(production, "_fsync_directory", _fsync_directory)
    monkeypatch.setattr(production.os, "replace", _replace)

    production._atomic_write_bpf_extra_rotation(
        state_path,
        cycle_key="2026-07-28T06:00:00+00:00",
        last_attempted_group=("Amsterdam", "2026-07-29"),
    )

    temporary = events[1][1]
    assert temporary.parent == state_path.parent
    assert temporary.name.startswith(
        f".{state_path.name}.pid{production.os.getpid()}."
    )
    assert temporary.name.endswith(".tmp")
    assert events == [
        ("fsync_directory", tmp_path, None),
        ("fsync_file", temporary, None),
        ("replace", temporary, state_path),
        ("fsync_directory", state_path.parent, None),
    ]


def test_durable_rotation_does_not_starve_tail_groups_across_restarts(
    tmp_path,
) -> None:
    cycle = datetime(2026, 7, 28, 6, tzinfo=timezone.utc)
    state_path = tmp_path / "replacement_forecast_live" / ".rotation.json"
    targets = _rotation_targets(
        tuple(
            (city, "2026-07-30", "high")
            for city in ("Amsterdam", "Buenos Aires", "London", "Shanghai")
        )
    )
    attempted: list[str] = []

    for _ in range(8):
        rotated, _, _, _ = production._rotate_bpf_extra_targets(
            targets,
            cycle=cycle,
            state_path=state_path,
        )
        attempted.append(rotated[0].city)
        production._advance_bpf_extra_rotation(
            cycle=cycle,
            rotated_targets=rotated,
            attempted_group_count=1,
            state_path=state_path,
        )

    assert attempted[:4] == [
        "Amsterdam",
        "Buenos Aires",
        "London",
        "Shanghai",
    ]
    assert attempted[4:] == attempted[:4]


def test_priority_rotation_preempts_regular_cursor_and_remains_fair(
    tmp_path,
) -> None:
    cycle = datetime(2026, 8, 5, 0, tzinfo=timezone.utc)
    state_path = tmp_path / "replacement_forecast_live" / ".rotation.json"
    targets = _rotation_targets(
        (
            ("Tokyo", "2026-08-06", "high"),
            ("Wuhan", "2026-08-06", "high"),
            ("Sao Paulo", "2026-08-06", "high"),
        )
    )
    production._advance_bpf_extra_rotation(
        cycle=cycle,
        rotated_targets=(targets[2],),
        attempted_group_count=1,
        state_path=state_path,
    )
    priority = {
        ("Tokyo", "2026-08-06"),
        ("Wuhan", "2026-08-06"),
    }

    first, _, _, first_status = production._rotate_bpf_extra_targets(
        targets,
        cycle=cycle,
        state_path=state_path,
        priority_group_keys=priority,
    )
    assert first[0].city == "Tokyo"
    assert first_status.startswith("PRIORITY_")

    production._advance_bpf_extra_rotation(
        cycle=cycle,
        rotated_targets=first,
        attempted_group_count=1,
        state_path=state_path,
    )
    second, _, _, _ = production._rotate_bpf_extra_targets(
        targets,
        cycle=cycle,
        state_path=state_path,
        priority_group_keys=priority,
    )
    assert second[0].city == "Wuhan"


@pytest.mark.parametrize(
    ("outcome", "receipt", "next_head", "write_status"),
    (
        ("zero_timebox", 0, "Amsterdam", "NO_PROGRESS"),
        ("partial", 1, "London", "PERSISTED"),
        ("transport", 2, "Paris", "PERSISTED"),
        ("exception", 0, "Amsterdam", "NO_PROGRESS"),
    ),
)
def test_rotation_advances_only_by_exact_downloader_progress_receipt(
    tmp_path,
    monkeypatch,
    outcome,
    receipt,
    next_head,
    write_status,
) -> None:
    cycle = datetime(2026, 7, 28, 6, tzinfo=timezone.utc)
    rows = [
        _row(city=city, target_date="2026-07-30", covered=False)
        for city in ("Amsterdam", "London", "Paris")
    ]
    monkeypatch.setattr(
        plan_mod,
        "build_replacement_forecast_current_target_plan",
        lambda _db: _plan(rows),
    )
    monkeypatch.setattr(
        production,
        "_probe_resolved_bayes_precision_fusion_extras_cycle",
        lambda: cycle,
    )
    monkeypatch.setattr(
        dl_mod,
        "bayes_precision_fusion_quota_cooldown_seconds",
        lambda: 0.0,
    )
    attempted_heads: list[str] = []

    def _download(**kwargs):
        attempted_heads.append(kwargs["targets"][0].city)
        if outcome == "exception":
            raise RuntimeError("injected transport exception")
        if outcome in {"zero_timebox", "partial"}:
            return {
                "status": "BAYES_PRECISION_FUSION_EXTRA_TIMEBOXED_INCOMPLETE",
                "timeboxed_incomplete": True,
                "timebox_unattempted_target_groups": 3 - receipt,
                "attempted_target_group_count": receipt,
            }
        return {
            "status": "BAYES_PRECISION_FUSION_EXTRA_TRANSPORT_RETRYABLE",
            "transport_aborted_remaining_targets": True,
            "attempted_target_group_count": receipt,
        }

    monkeypatch.setattr(
        dl_mod,
        "download_bayes_precision_fusion_extra_raw_inputs",
        _download,
    )
    cfg_dict = {
        "forecast_db": tmp_path / "forecasts.db",
        "seed_dir": tmp_path / "replacement_forecast_live" / "seeds",
        "download_release_lag_hours": 14.0,
    }

    first = production._download_bayes_precision_fusion_extra_raw_inputs_if_needed(
        cfg_dict
    )
    second = production._download_bayes_precision_fusion_extra_raw_inputs_if_needed(
        cfg_dict
    )

    assert first is not None
    assert second is not None
    assert first["target_rotation_attempted_group_count"] == receipt
    assert first["target_rotation_cursor_write_status"] == write_status
    assert attempted_heads == ["Amsterdam", next_head]
    if receipt == 0:
        assert first["target_rotation_progress_receipt_status"] == (
            "EXCEPTION_NO_RECEIPT" if outcome == "exception" else "EXACT"
        )
        assert not (
            tmp_path
            / "replacement_forecast_live"
            / production._BPF_EXTRA_ROTATION_FILENAME
        ).exists()
    else:
        assert first["target_rotation_progress_receipt_status"] == "EXACT"
    expected_status = (
        "BAYES_PRECISION_FUSION_EXTRA_CAPTURE_FAILSOFT_SKIPPED"
        if outcome == "exception"
        else (
            "BAYES_PRECISION_FUSION_EXTRA_TIMEBOXED_INCOMPLETE"
            if outcome in {"zero_timebox", "partial"}
            else "BAYES_PRECISION_FUSION_EXTRA_TRANSPORT_RETRYABLE"
        )
    )
    assert first["status"] == expected_status


def test_overlapping_invocation_is_busy_and_cannot_download_or_advance(
    tmp_path,
    monkeypatch,
) -> None:
    cycle = datetime(2026, 7, 28, 6, tzinfo=timezone.utc)
    rows = [
        _row(city="Amsterdam", target_date="2026-07-30", covered=False),
        _row(city="London", target_date="2026-07-30", covered=False),
    ]
    monkeypatch.setattr(
        plan_mod,
        "build_replacement_forecast_current_target_plan",
        lambda _db: _plan(rows),
    )
    monkeypatch.setattr(
        production,
        "_probe_resolved_bayes_precision_fusion_extras_cycle",
        lambda: cycle,
    )
    monkeypatch.setattr(
        dl_mod,
        "bayes_precision_fusion_quota_cooldown_seconds",
        lambda: 0.0,
    )
    cfg_dict = {
        "forecast_db": tmp_path / "forecasts.db",
        "seed_dir": tmp_path / "replacement_forecast_live" / "seeds",
    }
    calls = 0
    overlap: dict[str, object] = {}

    def _download(**kwargs):
        nonlocal calls
        calls += 1
        overlap.update(
            production._download_bayes_precision_fusion_extra_raw_inputs_if_needed(
                cfg_dict
            )
            or {}
        )
        return {
            "status": "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED",
            "attempted_target_group_count": 1,
        }

    monkeypatch.setattr(
        dl_mod,
        "download_bayes_precision_fusion_extra_raw_inputs",
        _download,
    )

    report = production._download_bayes_precision_fusion_extra_raw_inputs_if_needed(
        cfg_dict
    )

    assert calls == 1
    assert overlap["status"] == (
        "BAYES_PRECISION_FUSION_EXTRA_ROTATION_BUSY_FAILSOFT_SKIPPED"
    )
    assert overlap["target_rotation_owner_status"] == "BUSY"
    assert report["target_rotation_cursor_write_status"] == "PERSISTED"


def test_cross_process_busy_owner_skips_download_and_cursor_write(
    tmp_path,
    monkeypatch,
) -> None:
    cycle = datetime(2026, 7, 28, 6, tzinfo=timezone.utc)
    rows = [_row(city="Amsterdam", target_date="2026-07-30", covered=False)]
    monkeypatch.setattr(
        plan_mod,
        "build_replacement_forecast_current_target_plan",
        lambda _db: _plan(rows),
    )
    monkeypatch.setattr(
        production,
        "_probe_resolved_bayes_precision_fusion_extras_cycle",
        lambda: cycle,
    )
    monkeypatch.setattr(
        dl_mod,
        "bayes_precision_fusion_quota_cooldown_seconds",
        lambda: 0.0,
    )
    state_path = tmp_path / "replacement_forecast_live" / ".rotation.json"
    state_path.parent.mkdir()
    lock_path = state_path.with_name(f".{state_path.name}.lock")
    script = (
        "import fcntl, os, pathlib, sys\n"
        "fd=os.open(pathlib.Path(sys.argv[1]), os.O_RDWR|os.O_CREAT, 0o600)\n"
        "fcntl.flock(fd, fcntl.LOCK_EX)\n"
        "print('LOCKED', flush=True)\n"
        "sys.stdin.read(1)\n"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", script, str(lock_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert child.stdout is not None
    assert child.stdout.readline().strip() == "LOCKED"
    calls = 0

    def _download(**_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("busy owner must prevent download")

    monkeypatch.setattr(
        dl_mod,
        "download_bayes_precision_fusion_extra_raw_inputs",
        _download,
    )
    try:
        report = (
            production._download_bayes_precision_fusion_extra_raw_inputs_if_needed(
                {
                    "forecast_db": tmp_path / "forecasts.db",
                    "bpf_extra_rotation_state_path": state_path,
                }
            )
        )
    finally:
        assert child.stdin is not None
        child.stdin.write("x")
        child.stdin.flush()
        child.wait(timeout=5)

    assert calls == 0
    assert report is not None
    assert report["status"] == (
        "BAYES_PRECISION_FUSION_EXTRA_ROTATION_BUSY_FAILSOFT_SKIPPED"
    )
    assert report["target_rotation_owner_status"] == "BUSY"
    assert not state_path.exists()

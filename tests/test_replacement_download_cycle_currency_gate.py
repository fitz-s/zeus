# Created: 2026-06-09
# Last reused or audited: 2026-08-18
# Lifecycle: created=2026-06-09; last_reviewed=2026-08-18; last_reused=2026-08-18
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
import hashlib
import multiprocessing
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


def _anchor_payload(
    target_date: str = "2026-06-10",
    value_c: float | None = 20.0,
) -> dict[str, object]:
    return {
        "hourly": {
            "time": [f"{target_date}T12:00"],
            "temperature_2m": [value_c],
        },
        "hourly_units": {"temperature_2m": "°C"},
    }


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
    rotated, start, rotating_count, generation, _ = dl._rotate_current_target_rows(
        ordered,
        cycle=AVAILABLE_CYCLE.replace(hour=4),
    )

    assert [row.city for row in ordered] == ["Wellington", "Dallas", "Amsterdam"]
    assert start == 0
    assert rotating_count == 3
    assert generation == 0
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
    first, first_start, row_count, generation, state_token = dl._rotate_current_target_rows(
        rows,
        cycle=rotation_cycle,
        state_path=tmp_path / "rotation.json",
    )
    next_start, applied = dl._advance_current_target_rotation(
        cycle=rotation_cycle,
        row_count=row_count,
        attempted_count=2,
        incomplete=True,
        state_path=tmp_path / "rotation.json",
        expected_generation=generation,
        expected_state_token=state_token,
    )
    with dl._CURRENT_TARGET_ROTATION_LOCK:
        dl._CURRENT_TARGET_ROTATION_OFFSETS.clear()
    second, second_start, _, second_generation, _ = dl._rotate_current_target_rows(
        rows,
        cycle=rotation_cycle,
        state_path=tmp_path / "rotation.json",
    )

    assert first_start == 0
    assert [row.city for row in first] == ["Amsterdam", "Ankara", "Atlanta", "Austin"]
    assert next_start == 2
    assert applied is True
    assert second_start == 2
    assert second_generation == 1
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
    first, _, row_count, generation, state_token = dl._rotate_current_target_rows(
        ordered,
        cycle=cycle,
        state_path=state_path,
    )
    assert first[0].city == "Dallas"
    next_start, applied = dl._advance_current_target_rotation(
        cycle=cycle,
        row_count=row_count,
        attempted_count=1,
        incomplete=1 < row_count,
        state_path=state_path,
        expected_generation=generation,
        expected_state_token=state_token,
    )
    assert (next_start, applied) == (1, True)

    with dl._CURRENT_TARGET_ROTATION_LOCK:
        dl._CURRENT_TARGET_ROTATION_OFFSETS.clear()
    after_restart, start, _, _, _ = dl._rotate_current_target_rows(
        ordered,
        cycle=cycle,
        state_path=state_path,
    )

    assert start == 1
    assert after_restart[0].city == "Amsterdam"


def test_rotation_cursor_normalizes_when_same_cycle_universe_shrinks(
    tmp_path: Path,
) -> None:
    import scripts.download_replacement_forecast_current_targets as dl

    cycle = AVAILABLE_CYCLE.replace(hour=5)
    state_path = tmp_path / "rotation.json"
    state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "cycle": cycle.isoformat(),
                "next_start": 5,
                "generation": 42,
            }
        )
    )
    rows = [
        _TargetRow(city, "2026-06-10", "high", False, True)
        for city in ("Amsterdam", "Ankara", "Atlanta")
    ]

    rotated, start, row_count, generation, state_token = dl._rotate_current_target_rows(
        rows,
        cycle=cycle,
        state_path=state_path,
    )
    next_start, applied = dl._advance_current_target_rotation(
        cycle=cycle,
        row_count=row_count,
        attempted_count=1,
        incomplete=True,
        state_path=state_path,
        expected_generation=generation,
        expected_state_token=state_token,
    )

    assert start == 2
    assert generation == 42
    assert [row.city for row in rotated] == ["Atlanta", "Amsterdam", "Ankara"]
    assert (next_start, applied) == (0, True)
    assert json.loads(state_path.read_text()) == {
        "version": 1,
        "cycle": cycle.isoformat(),
        "next_start": 0,
        "generation": 43,
    }


def test_rotation_cursor_cas_prevents_cross_process_regression(
    tmp_path: Path,
) -> None:
    import scripts.download_replacement_forecast_current_targets as dl

    cycle = AVAILABLE_CYCLE.replace(hour=6)
    state_path = tmp_path / "rotation.json"
    rows = [
        _TargetRow(city, "2026-06-10", "high", False, True)
        for city in ("Amsterdam", "Ankara", "Atlanta")
    ]
    _, _, row_count, generation, state_token = dl._rotate_current_target_rows(
        rows,
        cycle=cycle,
        state_path=state_path,
    )
    assert dl._advance_current_target_rotation(
        cycle=cycle,
        row_count=row_count,
        attempted_count=2,
        incomplete=True,
        state_path=state_path,
        expected_generation=generation,
        expected_state_token=state_token,
    ) == (2, True)
    assert dl._advance_current_target_rotation(
        cycle=cycle,
        row_count=row_count,
        attempted_count=1,
        incomplete=True,
        state_path=state_path,
        expected_generation=generation,
        expected_state_token=state_token,
    ) == (2, False)


def test_rotation_cursor_cas_binds_source_cycle_epoch(tmp_path: Path) -> None:
    import scripts.download_replacement_forecast_current_targets as dl

    old_cycle = AVAILABLE_CYCLE.replace(hour=6)
    new_cycle = AVAILABLE_CYCLE.replace(hour=12)
    state_path = tmp_path / "rotation.json"
    rows = [
        _TargetRow(city, "2026-06-10", "high", False, True)
        for city in ("Amsterdam", "Ankara", "Atlanta")
    ]

    _, _, row_count, generation, state_token = dl._rotate_current_target_rows(
        rows,
        cycle=old_cycle,
        state_path=state_path,
    )
    assert dl._advance_current_target_rotation(
        cycle=old_cycle,
        row_count=row_count,
        attempted_count=1,
        incomplete=True,
        state_path=state_path,
        expected_generation=generation,
        expected_state_token=state_token,
    ) == (1, True)

    _, _, _, stale_generation, stale_token = dl._rotate_current_target_rows(
        rows,
        cycle=old_cycle,
        state_path=state_path,
    )
    _, _, new_row_count, new_generation, new_token = dl._rotate_current_target_rows(
        rows,
        cycle=new_cycle,
        state_path=state_path,
    )
    assert new_generation == 0
    assert new_token == (old_cycle.isoformat(), 1)
    assert dl._advance_current_target_rotation(
        cycle=new_cycle,
        row_count=new_row_count,
        attempted_count=1,
        incomplete=True,
        state_path=state_path,
        expected_generation=new_generation,
        expected_state_token=new_token,
    ) == (1, True)
    assert dl._advance_current_target_rotation(
        cycle=old_cycle,
        row_count=row_count,
        attempted_count=1,
        incomplete=True,
        state_path=state_path,
        expected_generation=stale_generation,
        expected_state_token=stale_token,
    ) == (0, False)
    assert json.loads(state_path.read_text()) == {
        "version": 1,
        "cycle": new_cycle.isoformat(),
        "next_start": 1,
        "generation": 1,
    }


@pytest.mark.parametrize("old_advances_first", (False, True))
def test_rotation_cycle_advancement_is_monotonic_from_absent_state(
    tmp_path: Path,
    old_advances_first: bool,
) -> None:
    import scripts.download_replacement_forecast_current_targets as dl

    old_cycle = AVAILABLE_CYCLE.replace(hour=6)
    new_cycle = AVAILABLE_CYCLE.replace(hour=12)
    state_path = tmp_path / "rotation.json"
    rows = [_TargetRow("Amsterdam", "2026-06-10", "high", False, True)]
    _, _, row_count, old_generation, old_token = dl._rotate_current_target_rows(
        rows,
        cycle=old_cycle,
        state_path=state_path,
    )
    _, _, _, new_generation, new_token = dl._rotate_current_target_rows(
        rows,
        cycle=new_cycle,
        state_path=state_path,
    )

    old_result = None
    if old_advances_first:
        old_result = dl._advance_current_target_rotation(
            cycle=old_cycle,
            row_count=row_count,
            attempted_count=1,
            incomplete=True,
            state_path=state_path,
            expected_generation=old_generation,
            expected_state_token=old_token,
        )
        assert old_result == (0, True)
    assert dl._advance_current_target_rotation(
        cycle=new_cycle,
        row_count=row_count,
        attempted_count=1,
        incomplete=True,
        state_path=state_path,
        expected_generation=new_generation,
        expected_state_token=new_token,
    ) == (0, True)
    old_result = dl._advance_current_target_rotation(
        cycle=old_cycle,
        row_count=row_count,
        attempted_count=1,
        incomplete=True,
        state_path=state_path,
        expected_generation=old_generation,
        expected_state_token=old_token,
    )
    assert old_result == (0, False)

    _, _, _, late_old_generation, late_old_token = dl._rotate_current_target_rows(
        rows,
        cycle=old_cycle,
        state_path=state_path,
    )
    assert late_old_generation == 0
    assert late_old_token == (new_cycle.isoformat(), 1)
    assert dl._advance_current_target_rotation(
        cycle=old_cycle,
        row_count=row_count,
        attempted_count=1,
        incomplete=True,
        state_path=state_path,
        expected_generation=late_old_generation,
        expected_state_token=late_old_token,
    ) == (0, False)

    assert json.loads(state_path.read_text())["cycle"] == new_cycle.isoformat()


def test_empty_rotation_universe_validates_without_state_mutation(
    tmp_path: Path,
) -> None:
    import scripts.download_replacement_forecast_current_targets as dl

    cycle = AVAILABLE_CYCLE.replace(hour=6)
    state_path = tmp_path / "rotation.json"
    state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "cycle": cycle.isoformat(),
                "next_start": 5,
                "generation": 42,
            }
        )
    )
    before = state_path.read_bytes()

    rows, start, row_count, generation, state_token = dl._rotate_current_target_rows(
        [],
        cycle=cycle,
        state_path=state_path,
    )
    assert (rows, start, row_count, generation) == ([], 0, 0, 42)
    assert dl._advance_current_target_rotation(
        cycle=cycle,
        row_count=row_count,
        attempted_count=0,
        incomplete=False,
        state_path=state_path,
        expected_generation=generation,
        expected_state_token=state_token,
    ) == (0, False)
    assert state_path.read_bytes() == before

    absent_path = tmp_path / "absent.json"
    _, _, absent_count, absent_generation, absent_token = dl._rotate_current_target_rows(
        [],
        cycle=cycle,
        state_path=absent_path,
    )
    assert dl._advance_current_target_rotation(
        cycle=cycle,
        row_count=absent_count,
        attempted_count=0,
        incomplete=False,
        state_path=absent_path,
        expected_generation=absent_generation,
        expected_state_token=absent_token,
    ) == (0, False)
    assert not absent_path.exists()
    assert not dl._rotation_state_lock_path(absent_path).exists()


def test_rotation_cursor_cas_rejects_stale_dynamic_universe(
    tmp_path: Path,
) -> None:
    import scripts.download_replacement_forecast_current_targets as dl

    cycle = AVAILABLE_CYCLE.replace(hour=6)
    state_path = tmp_path / "rotation.json"
    small_rows = [
        _TargetRow(city, "2026-06-10", "high", False, True)
        for city in ("Amsterdam", "Ankara")
    ]
    large_rows = small_rows + [
        _TargetRow(city, "2026-06-10", "high", False, True)
        for city in ("Atlanta", "Austin", "Dallas")
    ]
    _, _, small_count, small_generation, small_token = dl._rotate_current_target_rows(
        small_rows,
        cycle=cycle,
        state_path=state_path,
    )
    _, _, large_count, large_generation, large_token = dl._rotate_current_target_rows(
        large_rows,
        cycle=cycle,
        state_path=state_path,
    )

    assert dl._advance_current_target_rotation(
        cycle=cycle,
        row_count=large_count,
        attempted_count=4,
        incomplete=True,
        state_path=state_path,
        expected_generation=large_generation,
        expected_state_token=large_token,
    ) == (4, True)
    assert dl._advance_current_target_rotation(
        cycle=cycle,
        row_count=small_count,
        attempted_count=1,
        incomplete=True,
        state_path=state_path,
        expected_generation=small_generation,
        expected_state_token=small_token,
    ) == (4, False)

    reverse_path = tmp_path / "reverse.json"
    _, _, small_count, small_generation, small_token = dl._rotate_current_target_rows(
        small_rows,
        cycle=cycle,
        state_path=reverse_path,
    )
    _, _, large_count, large_generation, large_token = dl._rotate_current_target_rows(
        large_rows,
        cycle=cycle,
        state_path=reverse_path,
    )
    assert dl._advance_current_target_rotation(
        cycle=cycle,
        row_count=small_count,
        attempted_count=1,
        incomplete=True,
        state_path=reverse_path,
        expected_generation=small_generation,
        expected_state_token=small_token,
    ) == (1, True)
    assert dl._advance_current_target_rotation(
        cycle=cycle,
        row_count=large_count,
        attempted_count=4,
        incomplete=True,
        state_path=reverse_path,
        expected_generation=large_generation,
        expected_state_token=large_token,
    ) == (1, False)
    _, normalized_start, _, _, _ = dl._rotate_current_target_rows(
        large_rows,
        cycle=cycle,
        state_path=reverse_path,
    )
    assert normalized_start == 1


def _advance_rotation_in_process(
    state_path: str,
    cycle_iso: str,
    row_count: int,
    attempted_count: int,
    start_event,
    result_queue,
) -> None:
    import scripts.download_replacement_forecast_current_targets as dl

    start_event.wait()
    result_queue.put(
        dl._advance_current_target_rotation(
            cycle=datetime.fromisoformat(cycle_iso),
            row_count=row_count,
            attempted_count=attempted_count,
            incomplete=True,
            state_path=Path(state_path),
            expected_generation=0,
        )
    )


def test_rotation_cursor_os_lock_allows_exactly_one_cross_process_cas(
    tmp_path: Path,
) -> None:
    import scripts.download_replacement_forecast_current_targets as dl

    cycle = AVAILABLE_CYCLE.replace(hour=6)
    state_path = tmp_path / "rotation.json"
    rows = [
        _TargetRow(city, "2026-06-10", "high", False, True)
        for city in ("Amsterdam", "Ankara", "Atlanta")
    ]
    dl._rotate_current_target_rows(rows, cycle=cycle, state_path=state_path)
    context = multiprocessing.get_context("spawn")
    start_event = context.Event()
    result_queue = context.Queue()
    processes = [
        context.Process(
            target=_advance_rotation_in_process,
            args=(str(state_path), cycle.isoformat(), len(rows), count, start_event, result_queue),
        )
        for count in (1, 2)
    ]
    for process in processes:
        process.start()
    start_event.set()
    results = [result_queue.get(timeout=5.0) for _ in processes]
    for process in processes:
        process.join(timeout=5.0)
        assert process.exitcode == 0

    assert sum(1 for _next_start, applied in results if applied) == 1
    persisted = json.loads(state_path.read_text())
    assert persisted["generation"] == 1
    assert persisted["next_start"] in {1, 2}


def test_rotation_cursor_isolated_by_state_path(tmp_path: Path) -> None:
    import scripts.download_replacement_forecast_current_targets as dl

    cycle = AVAILABLE_CYCLE.replace(hour=6)
    rows = [
        _TargetRow(city, "2026-06-10", "high", False, True)
        for city in ("Amsterdam", "Ankara", "Atlanta")
    ]
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"
    for state_path in (first_path, second_path):
        dl._rotate_current_target_rows(rows, cycle=cycle, state_path=state_path)

    assert dl._advance_current_target_rotation(
        cycle=cycle,
        row_count=len(rows),
        attempted_count=1,
        incomplete=True,
        state_path=first_path,
        expected_generation=0,
    ) == (1, True)
    assert dl._advance_current_target_rotation(
        cycle=cycle,
        row_count=len(rows),
        attempted_count=2,
        incomplete=True,
        state_path=second_path,
        expected_generation=0,
    ) == (2, True)


def test_scoped_rotation_cursor_isolated_from_ordinary_universe(
    tmp_path: Path,
) -> None:
    import scripts.download_replacement_forecast_current_targets as dl

    rows = [_TargetRow("Dallas", "2026-06-10", "high", False, True)]
    ordinary = dl._current_target_rotation_state_path(
        tmp_path,
        rows,
        scoped=False,
    )
    scoped = dl._current_target_rotation_state_path(
        tmp_path,
        rows,
        scoped=True,
    )
    different_scope = dl._current_target_rotation_state_path(
        tmp_path,
        [_TargetRow("NYC", "2026-06-10", "low", False, True)],
        scoped=True,
    )

    assert ordinary.name == ".current_target_rotation.json"
    assert scoped != ordinary
    assert different_scope != scoped


def test_legacy_rotation_cursor_is_read_then_upgraded(
    tmp_path: Path,
) -> None:
    import scripts.download_replacement_forecast_current_targets as dl

    cycle = AVAILABLE_CYCLE.replace(hour=7)
    state_path = tmp_path / "rotation.json"
    state_path.write_text(
        json.dumps({"cycle": cycle.isoformat(), "next_start": 1})
    )
    rows = [
        _TargetRow(city, "2026-06-10", "high", False, True)
        for city in ("Amsterdam", "Dallas")
    ]

    rotated, start, row_count, generation, state_token = dl._rotate_current_target_rows(
        rows,
        cycle=cycle,
        state_path=state_path,
    )
    next_start, applied = dl._advance_current_target_rotation(
        cycle=cycle,
        row_count=row_count,
        attempted_count=1,
        incomplete=True,
        state_path=state_path,
        expected_generation=generation,
        expected_state_token=state_token,
    )

    assert [row.city for row in rotated] == ["Dallas", "Amsterdam"]
    assert start == 1
    assert (next_start, applied) == (0, True)
    assert json.loads(state_path.read_text()) == {
        "version": 1,
        "cycle": cycle.isoformat(),
        "next_start": 0,
        "generation": 1,
    }


def test_malformed_rotation_state_fails_closed(tmp_path: Path) -> None:
    import scripts.download_replacement_forecast_current_targets as dl

    state_path = tmp_path / "rotation.json"
    state_path.write_text("[]")
    with pytest.raises(RuntimeError, match="CURRENT_TARGET_ROTATION_STATE_INVALID"):
        dl._rotate_current_target_rows(
            [_TargetRow("Dallas", "2026-06-10", "high", False, True)],
            cycle=AVAILABLE_CYCLE.replace(hour=7),
            state_path=state_path,
        )
    with pytest.raises(RuntimeError, match="CURRENT_TARGET_ROTATION_STATE_INVALID"):
        dl._rotate_current_target_rows(
            [],
            cycle=AVAILABLE_CYCLE.replace(hour=7),
            state_path=state_path,
        )


@pytest.mark.parametrize(
    "payload",
    (
        {
            "version": 1,
            "cycle": AVAILABLE_CYCLE.replace(hour=7).isoformat(),
            "next_start": True,
            "generation": 0,
        },
        {
            "version": 1,
            "cycle": AVAILABLE_CYCLE.replace(hour=7).isoformat(),
            "next_start": -1,
            "generation": 0,
        },
        {
            "version": 1,
            "cycle": AVAILABLE_CYCLE.replace(hour=7).isoformat(),
            "next_start": 0,
            "generation": -1,
        },
        {
            "version": 1,
            "cycle": AVAILABLE_CYCLE.replace(hour=7).isoformat(),
            "next_start": 0,
            "generation": 0,
            "unexpected": "field",
        },
        {
            "version": 1,
            "cycle": "2026-06-09T07:00:00Z",
            "next_start": 0,
            "generation": 0,
        },
    ),
)
def test_rotation_state_schema_is_strict(tmp_path: Path, payload: dict) -> None:
    import scripts.download_replacement_forecast_current_targets as dl

    state_path = tmp_path / "rotation.json"
    state_path.write_text(json.dumps(payload))
    with pytest.raises(RuntimeError, match="CURRENT_TARGET_ROTATION_STATE_INVALID"):
        dl._rotate_current_target_rows(
            [_TargetRow("Dallas", "2026-06-10", "high", False, True)],
            cycle=AVAILABLE_CYCLE.replace(hour=7),
            state_path=state_path,
        )


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


def test_anchor_ladder_falls_through_empty_http_payloads_to_bucket(
    monkeypatch,
) -> None:
    import scripts.download_replacement_forecast_current_targets as dl
    from src.data.openmeteo_ecmwf_ifs9_anchor import build_anchor_request

    request = build_anchor_request(
        latitude=40.77945,
        longitude=-73.88027,
        run="2026-06-25T12:00:00+00:00",
        timezone_name="America/New_York",
    )
    empty = _anchor_payload("2026-06-25", value_c=None)
    valid = _anchor_payload("2026-06-25", value_c=21.0)
    monkeypatch.setattr(dl, "_single_runs_public_for_request", lambda _request: True)
    monkeypatch.setattr(
        dl,
        "fetch_openmeteo_ecmwf_ifs9_anchor_payload",
        lambda *_args, **_kwargs: empty,
    )
    monkeypatch.setattr(
        "src.data.openmeteo_ecmwf_ifs9_anchor.fetch_openmeteo_ecmwf_ifs9_anchor_payload_meta_stamped",
        lambda *_args, **_kwargs: (empty, {"run_authority": "provider_meta_declared"}),
    )
    seen: list[dict[str, object]] = []

    def _bucket(**kwargs):
        seen.append(kwargs)
        return valid, {"run_authority": "bucket_partial_run_test"}

    monkeypatch.setattr(dl, "_try_bucket_rung_three", _bucket)

    payload, provenance = dl._resolve_anchor_payload(
        request=request,
        city="NYC",
        target_date="2026-06-25",
        timezone_name="America/New_York",
    )

    assert payload == valid
    assert provenance["run_authority"] == "bucket_partial_run_test"
    assert len(seen) == 1
    assert "no finite target-day sample" in str(seen[0]["meta_refusal"])


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
        return _anchor_payload("2026-06-25")

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
        return _anchor_payload("2026-08-07")

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
        lambda *_args, **_kwargs: _anchor_payload("2026-06-25"),
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
                _anchor_payload(),
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


def test_direct_downloader_reuses_canonical_bytes_without_moving_capture_time(
    tmp_path,
    monkeypatch,
) -> None:
    import scripts.download_replacement_forecast_current_targets as dl

    row = _TargetRow(
        city="Dallas",
        target_date="2026-06-10",
        temperature_metric="high",
        covered=True,
        missing_openmeteo_manifest=False,
    )
    db = tmp_path / "forecasts.db"
    conn = sqlite3.connect(db)
    conn.execute(_ARTIFACTS_DDL)
    output_dir = tmp_path / "raw"
    raw_dir = output_dir / AVAILABLE_CYCLE.strftime("%Y%m%dT%H%M%SZ")
    raw_dir.mkdir(parents=True)
    payload_path = raw_dir / (
        "openmeteo_Dallas_2026-06-10_high_20260609T000000Z.json"
    )
    payload_path.write_text(json.dumps(_anchor_payload()) + "\n")
    precision_path = raw_dir / "openmeteo_precision_Dallas_2026-06-10_high.json"
    precision_path.write_text(
        json.dumps(
            dl._precision_metadata(
                "Dallas",
                "2026-06-10",
                anchor_sigma_c=3.0,
            )
        )
    )
    payload = payload_path.read_bytes()
    original_capture = "2026-06-09T13:59:45+00:00"
    metadata = {
        "city": "Dallas",
        "target_date": "2026-06-10",
        "metric": "high",
        "openmeteo_payload_json": str(payload_path),
        "precision_metadata_json": str(precision_path),
    }
    conn.execute(
        """
        INSERT INTO raw_forecast_artifacts (
            source_id, product_id, data_version, source_cycle_time,
            source_available_at, captured_at, artifact_path, sha256,
            byte_size, artifact_metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            dl.OPENMETEO_SOURCE_ID,
            dl.OPENMETEO_PRODUCT_ID,
            dl.OPENMETEO_HIGH_DATA_VERSION,
            AVAILABLE_CYCLE.isoformat(),
            original_capture,
            original_capture,
            str(payload_path),
            hashlib.sha256(payload).hexdigest(),
            len(payload),
            json.dumps(metadata),
        ),
    )
    conn.commit()
    conn.close()
    canonical_manifest = dl.RawForecastArtifactManifest.from_file(
        payload_path,
        source_id=dl.OPENMETEO_SOURCE_ID,
        product_id=dl.OPENMETEO_PRODUCT_ID,
        data_version=dl.OPENMETEO_HIGH_DATA_VERSION,
        source_cycle_time=AVAILABLE_CYCLE,
        source_available_at=original_capture,
        captured_at=original_capture,
        request_url="https://example.test/canonical",
        request_params={"run": AVAILABLE_CYCLE.isoformat()},
        product_metadata=metadata,
    )
    dl._write_manifest_file(output_dir, canonical_manifest)

    monkeypatch.setattr(dl, "ensure_replacement_forecast_live_schema", lambda _conn: None)

    def _network_forbidden(*_args, **_kwargs):
        raise AssertionError("canonical same-cycle bytes must not be fetched again")

    monkeypatch.setattr(dl, "_resolve_anchor_payload", _network_forbidden)
    report = dl.download_current_target_raw_inputs(
        forecast_db=db,
        output_dir=output_dir,
        cycle=AVAILABLE_CYCLE,
        limit=None,
        write_db=True,
        release_lag_hours=14.0,
        anchor_sigma_c=3.0,
        include_covered=True,
        precomputed_plan=_PlanStub(ready=True, rows=(row,)),
    )

    conn = sqlite3.connect(db)
    persisted = conn.execute(
        "SELECT source_available_at, captured_at, COUNT(*) "
        "FROM raw_forecast_artifacts"
    ).fetchone()
    conn.close()
    assert report["written_manifest_count"] == 0
    assert report["reused_canonical_artifact_count"] == 1
    assert report["target_rotation_advanced"] is False
    assert persisted == (original_capture, original_capture, 1)


def test_canonical_reuse_refuses_and_repairs_semantically_wrong_precision_sidecar(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import scripts.download_replacement_forecast_current_targets as dl

    target = _TargetRow(
        city="Dallas",
        target_date="2026-06-10",
        temperature_metric="high",
        covered=True,
        missing_openmeteo_manifest=False,
    )
    db = tmp_path / "forecasts.db"
    conn = sqlite3.connect(db)
    conn.execute(_ARTIFACTS_DDL)
    output_dir = tmp_path / "raw"
    raw_dir = output_dir / AVAILABLE_CYCLE.strftime("%Y%m%dT%H%M%SZ")
    raw_dir.mkdir(parents=True)
    payload_path = raw_dir / "openmeteo_Dallas_2026-06-10_high_20260609T000000Z.json"
    payload_path.write_text(json.dumps(_anchor_payload()) + "\n")
    invalid_precision = raw_dir / "invalid-precision.json"
    invalid_payload = dl._precision_metadata(
        "Dallas",
        "2026-06-10",
        anchor_sigma_c=3.0,
    )
    invalid_payload["target_local_date"] = "2026-06-11"
    invalid_precision.write_text(json.dumps(invalid_payload))
    payload = payload_path.read_bytes()
    metadata = {
        "city": "Dallas",
        "target_date": "2026-06-10",
        "metric": "high",
        "openmeteo_payload_json": str(payload_path),
        "precision_metadata_json": str(invalid_precision),
    }
    captured_at = "2026-06-09T13:59:45+00:00"
    conn.execute(
        """
        INSERT INTO raw_forecast_artifacts (
            source_id, product_id, data_version, source_cycle_time,
            source_available_at, captured_at, artifact_path, sha256,
            byte_size, artifact_metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            dl.OPENMETEO_SOURCE_ID,
            dl.OPENMETEO_PRODUCT_ID,
            dl.OPENMETEO_HIGH_DATA_VERSION,
            AVAILABLE_CYCLE.isoformat(),
            captured_at,
            captured_at,
            str(payload_path),
            hashlib.sha256(payload).hexdigest(),
            len(payload),
            json.dumps(metadata),
        ),
    )
    conn.commit()
    conn.close()
    manifest = dl.RawForecastArtifactManifest.from_file(
        payload_path,
        source_id=dl.OPENMETEO_SOURCE_ID,
        product_id=dl.OPENMETEO_PRODUCT_ID,
        data_version=dl.OPENMETEO_HIGH_DATA_VERSION,
        source_cycle_time=AVAILABLE_CYCLE,
        source_available_at=captured_at,
        captured_at=captured_at,
        request_url="https://example.test/canonical",
        request_params={"run": AVAILABLE_CYCLE.isoformat()},
        product_metadata=metadata,
    )
    dl._write_manifest_file(output_dir, manifest)

    assert dl._canonical_current_target_reuse(
        db,
        cycle=AVAILABLE_CYCLE,
        targets=(target,),
        raw_dir=raw_dir,
        anchor_sigma_c=3.0,
    ) == {}
    fetches: list[tuple[str, str]] = []

    def _wave(requests, **_kwargs):
        key = next(iter(requests))
        fetches.append(key)
        return {
            key: (
                {"hourly": {"time": [], "temperature_2m": []}},
                {
                    "openmeteo_endpoint": "standard_api_meta_stamped",
                    "run_authority": "provider_meta_declared",
                },
                datetime.fromisoformat(captured_at),
            )
        }, {}

    monkeypatch.setattr(dl, "_fetch_meta_stamped_anchor_wave", _wave)
    monkeypatch.setattr(dl, "ensure_replacement_forecast_live_schema", lambda _conn: None)
    monkeypatch.setattr(dl, "write_manifest_to_db", lambda *_args, **_kwargs: 2)
    report = dl.download_current_target_raw_inputs(
        forecast_db=db,
        output_dir=output_dir,
        cycle=AVAILABLE_CYCLE,
        limit=None,
        write_db=True,
        release_lag_hours=14.0,
        anchor_sigma_c=3.0,
        include_covered=True,
        precomputed_plan=_PlanStub(ready=True, rows=(target,)),
    )

    assert fetches == []
    assert (raw_dir / "openmeteo_precision_Dallas_2026-06-10_high.json").is_file()
    assert report["reused_canonical_artifact_count"] == 0
    assert report["written_manifest_count"] == 1


def test_limited_batch_advances_past_complete_canonical_reuse(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import scripts.download_replacement_forecast_current_targets as dl

    rows = (
        _TargetRow("Dallas", "2026-06-10", "high", True, False),
        _TargetRow("London", "2026-06-10", "high", False, True),
    )
    monkeypatch.setattr(
        "src.data.replacement_forecast_seed_discovery.held_position_family_priorities",
        lambda: {("Dallas", "2026-06-10", "high"): 0},
    )
    monkeypatch.setattr(
        dl,
        "_canonical_current_target_reuse",
        lambda *_args, **_kwargs: {("Dallas", "2026-06-10", "high"): 7},
    )
    monkeypatch.setattr(dl, "ensure_replacement_forecast_live_schema", lambda _conn: None)

    report = dl.download_current_target_raw_inputs(
        forecast_db=tmp_path / "forecasts.db",
        output_dir=tmp_path / "raw",
        cycle=AVAILABLE_CYCLE,
        limit=1,
        write_db=True,
        release_lag_hours=14.0,
        anchor_sigma_c=3.0,
        include_covered=True,
        precomputed_plan=_PlanStub(ready=False, rows=rows),
    )

    assert report["target_count"] == 1
    assert report["reused_canonical_artifact_count"] == 1
    assert report["unscheduled_target_count"] == 1
    assert report["target_rotation_next_start"] == 1
    assert report["target_rotation_cas_applied"] is True


@pytest.mark.parametrize("limit", (0, -1, True))
def test_current_target_download_rejects_nonpositive_or_boolean_limit(
    tmp_path: Path,
    limit,
) -> None:
    import scripts.download_replacement_forecast_current_targets as dl

    with pytest.raises(ValueError, match="limit must be a positive integer or None"):
        dl.download_current_target_raw_inputs(
            forecast_db=tmp_path / "forecasts.db",
            output_dir=tmp_path / "raw",
            cycle=AVAILABLE_CYCLE,
            limit=limit,
            write_db=False,
            release_lag_hours=14.0,
            anchor_sigma_c=3.0,
            required_scopes=(),
        )


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
    payload_path = tmp_path / "openmeteo_Dallas_2026-08-17_high.json"
    payload_path.write_text(json.dumps(_anchor_payload("2026-08-17")) + "\n")
    payload_bytes = payload_path.read_bytes()
    conn = sqlite3.connect(db)
    conn.execute(
        """
        UPDATE raw_forecast_artifacts
        SET product_id = 'openmeteo_ecmwf_ifs9_deterministic_anchor_v1',
            data_version = 'openmeteo_ecmwf_ifs9_anchor_localday_high',
            artifact_path = ?,
            sha256 = ?,
            byte_size = ?,
            artifact_metadata_json = ?
        WHERE source_id = 'openmeteo_ecmwf_ifs_9km'
        """,
        (
            str(payload_path),
            hashlib.sha256(payload_bytes).hexdigest(),
            len(payload_bytes),
            json.dumps({"city": "Dallas", "target_date": "2026-08-17", "metric": "high"}),
        ),
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


def test_all_null_critical_raw_does_not_mask_missing_anchor(
    tmp_path, monkeypatch
) -> None:
    db = _make_db(
        tmp_path,
        {"openmeteo_ecmwf_ifs_9km": CURRENT_CYCLE_ISO},
    )
    scope = ("Dallas", "2026-08-17", "high")
    payload_path = tmp_path / "openmeteo_Dallas_2026-08-17_high.json"
    payload_path.write_text(
        json.dumps(_anchor_payload("2026-08-17", value_c=None)) + "\n"
    )
    payload_bytes = payload_path.read_bytes()
    conn = sqlite3.connect(db)
    conn.execute(
        """
        UPDATE raw_forecast_artifacts
        SET product_id = 'openmeteo_ecmwf_ifs9_deterministic_anchor_v1',
            data_version = 'openmeteo_ecmwf_ifs9_anchor_localday_high',
            artifact_path = ?,
            sha256 = ?,
            byte_size = ?,
            artifact_metadata_json = ?
        WHERE source_id = 'openmeteo_ecmwf_ifs_9km'
        """,
        (
            str(payload_path),
            hashlib.sha256(payload_bytes).hexdigest(),
            len(payload_bytes),
            json.dumps({"city": "Dallas", "target_date": "2026-08-17", "metric": "high"}),
        ),
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

    assert report["status"] == "CURRENT_TARGET_RAW_INPUTS_DOWNLOADED"
    assert len(calls) == 1
    assert calls[0]["required_scopes"] == (scope,)


def test_direct_downloader_does_not_publish_all_null_anchor_payload(
    tmp_path, monkeypatch
) -> None:
    import scripts.download_replacement_forecast_current_targets as dl

    row = _TargetRow(
        city="Dallas",
        target_date="2026-06-10",
        temperature_metric="high",
        covered=False,
        missing_openmeteo_manifest=True,
    )
    monkeypatch.setattr(dl, "_single_runs_public_for_request", lambda _request: True)
    monkeypatch.setattr(
        dl,
        "_resolve_anchor_payload",
        lambda **_kwargs: (
            _anchor_payload(value_c=None),
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
        include_covered=True,
        precomputed_plan=_PlanStub(ready=False, rows=(row,)),
    )

    assert report["manifest_count"] == 0
    assert report["written_manifest_count"] == 0
    assert report["skipped_cities"] == [
        {
            "city": "Dallas",
            "target_date": "2026-06-10",
            "metric": "high",
            "reason": "anchor payload has no finite target-day sample",
        }
    ]


def test_critical_quota_context_propagates_into_anchor_worker(
    monkeypatch,
) -> None:
    import scripts.download_replacement_forecast_current_targets as dl
    from src.data.openmeteo_ecmwf_ifs9_anchor import build_anchor_request

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
        lambda *_args, **_kwargs: (
            observed.append(tracker.is_critical())
            or _anchor_payload("2026-08-17")
        ),
    )
    request = build_anchor_request(
        latitude=32.8998,
        longitude=-97.0403,
        run="2026-08-17T12:00:00+00:00",
        timezone_name="America/Chicago",
    )

    payloads, failures = dl._fetch_meta_stamped_anchor_wave(
        {("Dallas", "2026-08-17"): request},
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
            _anchor_payload(),
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
            _anchor_payload(),
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
            _anchor_payload(),
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
                _anchor_payload(),
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
                _anchor_payload(),
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
                    _anchor_payload(),
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

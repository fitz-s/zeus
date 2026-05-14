# Created: 2026-05-11
# Last reused/audited: 2026-05-11
# Authority basis: PLAN docs/operations/task_2026-05-11_ecmwf_download_replacement/PLAN.md §5.5
#   Cross-track filename collision antibody: per-step filenames include param
#   (e.g. .step003_mx2t3.grib2 vs .step003_mn2t3.grib2) so concurrent mx2t6_high
#   and mn2t6_low cycles sharing the same output_dir do not clobber each other.
"""Integration regression tests for collect_open_ens_cycle cross-track isolation.

Relationship being tested: when mx2t6_high (param=mx2t3) and mn2t6_low (param=mn2t3)
run concurrently and share the same FIFTY_ONE_ROOT output directory, their per-step
intermediate files must not collide.  The filename pattern is:
  .step{NNN}_{param}.grib2   (e.g. .step003_mx2t3.grib2, .step003_mn2t3.grib2)
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from src.state.db import init_schema
from src.state.schema.v2_schema import apply_v2_schema


def _make_conn(tmp_path: Path) -> sqlite3.Connection:
    db = tmp_path / "world.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    apply_v2_schema(conn)
    return conn


def _ok_fetch_impl(*, cycle_date, cycle_hour, param, step, output_dir, mirrors):
    """Fake _fetch_impl that writes a zero-byte canonical file for each step."""
    canonical = output_dir / f".step{step:03d}_{param}.grib2"
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_bytes(b"\x00" * 16)  # non-empty so resume logic treats it as done
    return ("OK", canonical)


# ---------------------------------------------------------------------------
# Regression: mx2t6_high and mn2t6_low per-step files do NOT collide
# ---------------------------------------------------------------------------

def test_cross_track_per_step_filenames_are_distinct(tmp_path, monkeypatch):
    """mx2t6_high (mx2t3) and mn2t6_low (mn2t3) written to the same output_dir
    must produce distinct .step{NNN}_{param}.grib2 filenames — no collision."""
    from src.data import ecmwf_open_data

    monkeypatch.setattr(ecmwf_open_data, "FIFTY_ONE_ROOT", tmp_path / "51 source data")
    monkeypatch.setattr(ecmwf_open_data, "STEP_HOURS", [3, 6, 9])

    files_written: dict[str, list[str]] = {}

    def capturing_fetch_impl(*, cycle_date, cycle_hour, param, step, output_dir, mirrors):
        canonical = output_dir / f".step{step:03d}_{param}.grib2"
        canonical.parent.mkdir(parents=True, exist_ok=True)
        canonical.write_bytes(b"\x00" * 16)
        files_written.setdefault(param, []).append(canonical.name)
        return ("OK", canonical)

    common_kwargs = dict(
        run_date=date(2026, 5, 11),
        run_hour=0,
        now_utc=datetime(2026, 5, 11, 9, 0, tzinfo=timezone.utc),
        _fetch_impl=capturing_fetch_impl,
        skip_extract=True,
    )

    # Run both tracks (sequentially here; concurrent in production)
    ecmwf_open_data.collect_open_ens_cycle(
        track="mx2t6_high",
        conn=_make_conn(tmp_path),
        **common_kwargs,
    )
    ecmwf_open_data.collect_open_ens_cycle(
        track="mn2t6_low",
        conn=_make_conn(tmp_path),
        **common_kwargs,
    )

    high_files = set(files_written.get("mx2t3", []))
    low_files  = set(files_written.get("mn2t3", []))

    assert high_files, "mx2t6_high produced no per-step files"
    assert low_files,  "mn2t6_low produced no per-step files"

    # The intersection must be empty — filenames are distinct because param differs.
    collision = high_files & low_files
    assert not collision, (
        f"Cross-track filename collision detected: {collision}. "
        "Per-step filenames must include param to prevent clobbering between "
        "concurrent mx2t6_high and mn2t6_low cycles."
    )

    # Sanity: each track produces exactly STEP_HOURS files.
    assert len(high_files) == 3, f"Expected 3 high files, got {len(high_files)}: {high_files}"
    assert len(low_files)  == 3, f"Expected 3 low files, got {len(low_files)}: {low_files}"

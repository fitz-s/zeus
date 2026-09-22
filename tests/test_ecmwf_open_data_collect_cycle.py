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
import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.state.db import init_schema
from src.state.schema.v2_schema import apply_canonical_schema
from src.state.source_run_repo import write_source_run


def _make_conn(tmp_path: Path) -> sqlite3.Connection:
    db = tmp_path / "world.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    apply_canonical_schema(conn)
    return conn


def _ok_fetch_impl(*, cycle_date, cycle_hour, param, step, output_dir, mirrors):
    """Fake _fetch_impl that writes a zero-byte canonical file for each step."""
    canonical = output_dir / f".step{step:03d}_{param}.grib2"
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_bytes(b"\x00" * 16)  # non-empty so resume logic treats it as done
    return ("OK", canonical)


def _write_raw_group(root: Path, day: str, hour: int, param: str) -> list[Path]:
    day_dir = root / "raw" / "ecmwf_open_ens" / "ecmwf" / day
    day_dir.mkdir(parents=True, exist_ok=True)
    paths = [
        day_dir / f".{day}_{hour:02d}z_step003_{param}_ens51.grib2",
        day_dir / f"open_ens_{day}_{hour:02d}z_steps_3to144_n48_test_params_{param}.grib2",
    ]
    for path in paths:
        path.write_bytes(b"raw")
    return paths


def _record_raw_authority(
    conn: sqlite3.Connection,
    *,
    day: str,
    hour: int,
    param: str,
    status: str = "SUCCESS",
    completeness: str = "COMPLETE",
    partial: bool = False,
    expected: int = 2,
    snapshot_count: int = 2,
    authority: str = "VERIFIED",
    coordsha: str | None = "6e28420e5809b3b30f5a075629c0ebd56b1fa15d405a00a85455b4fbcd1a14f4",
) -> None:
    track = "mx2t6_high" if param == "mx2t3" else "mn2t6_low"
    metric = "high" if param == "mx2t3" else "low"
    iso_day = datetime.strptime(day, "%Y%m%d").date().isoformat()
    # collect_open_ens_cycle stamps ":coordsha:<manifest_sha>" onto the id. The
    # fixture wrote only the bare cycle prefix, so every retention test passed
    # while production matched nothing and evicted nothing for 9 days (live
    # 2026-09-18: 10 recognized groups, all "source_run: MISSING"). Default to
    # the real shape; pass coordsha=None for the legacy bare-id form.
    source_run_id = f"ecmwf_open_data:{track}:{iso_day}T{hour:02d}Z"
    if coordsha is not None:
        source_run_id = f"{source_run_id}:coordsha:{coordsha}"
    write_source_run(
        conn,
        source_run_id=source_run_id,
        source_id="ecmwf_open_data",
        track=track,
        release_calendar_key=f"ecmwf_open_data:{track}:standard",
        source_cycle_time=f"{iso_day}T{hour:02d}:00:00+00:00",
        status=status,
        completeness_status=completeness,
        partial_run=partial,
        expected_count=expected,
        observed_count=expected,
    )
    for index in range(snapshot_count):
        conn.execute(
            """
            INSERT INTO ensemble_snapshots (
                city, target_date, temperature_metric, physical_quantity,
                observation_field, issue_time, available_at, fetch_time,
                lead_hours, members_json, model_version, dataset_id,
                source_id, source_run_id, authority
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"City{index}",
                iso_day,
                metric,
                "daily_extreme",
                "high_temp" if metric == "high" else "low_temp",
                f"{iso_day}T{hour:02d}:00:00+00:00",
                f"{iso_day}T{hour:02d}:00:00+00:00",
                f"{iso_day}T{hour:02d}:00:00+00:00",
                24.0,
                "[1.0]",
                "test",
                f"test_{source_run_id}",
                "ecmwf_open_data",
                source_run_id,
                authority,
            ),
        )
    conn.commit()


def test_raw_retention_deletes_only_old_complete_verified_groups(tmp_path):
    from src.data import ecmwf_open_data

    raw_root = tmp_path / "51 source data"
    old_paths = _write_raw_group(raw_root, "20260818", 0, "mx2t3")
    recent_paths = _write_raw_group(raw_root, "20260820", 0, "mx2t3")
    conn = _make_conn(tmp_path)
    _record_raw_authority(conn, day="20260818", hour=0, param="mx2t3")

    plan = ecmwf_open_data._plan_decoded_open_data_raw_retention(
        conn,
        raw_root=raw_root,
        reference_date=date(2026, 8, 21),
    )
    result = ecmwf_open_data._apply_decoded_open_data_raw_retention(plan)

    assert result["status"] == "APPLIED"
    assert result["eligible_group_count"] == 1
    assert result["deleted_file_count"] == 2
    assert all(not path.exists() for path in old_paths)
    assert all(path.exists() for path in recent_paths)


def test_raw_retention_fails_closed_on_incomplete_canonical_proof(tmp_path):
    from src.data import ecmwf_open_data

    raw_root = tmp_path / "51 source data"
    partial_paths = _write_raw_group(raw_root, "20260815", 0, "mx2t3")
    missing_paths = _write_raw_group(raw_root, "20260816", 6, "mn2t3")
    disputed_paths = _write_raw_group(raw_root, "20260817", 12, "mx2t3")
    mismatch_paths = _write_raw_group(raw_root, "20260818", 18, "mn2t3")
    conn = _make_conn(tmp_path)
    _record_raw_authority(
        conn,
        day="20260815",
        hour=0,
        param="mx2t3",
        status="PARTIAL",
        completeness="PARTIAL",
        partial=True,
    )
    # 20260816 intentionally has no source_run row.
    _record_raw_authority(
        conn,
        day="20260817",
        hour=12,
        param="mx2t3",
        authority="DISPUTED",
    )
    _record_raw_authority(
        conn,
        day="20260818",
        hour=18,
        param="mn2t3",
        snapshot_count=1,
    )

    plan = ecmwf_open_data._plan_decoded_open_data_raw_retention(
        conn,
        raw_root=raw_root,
        reference_date=date(2026, 8, 21),
    )
    result = ecmwf_open_data._apply_decoded_open_data_raw_retention(plan)

    assert result["status"] == "NO_ELIGIBLE_RAW"
    assert result["retained_group_count"] == 4
    assert all(
        path.exists()
        for path in partial_paths + missing_paths + disputed_paths + mismatch_paths
    )


def test_raw_retention_evicts_same_day_proven_group_and_keeps_unproven(tmp_path):
    """Retention is gated by the per-cycle proof, not by a calendar window.

    2026-09-17 operator directive: `_RAW_RETENTION_CALENDAR_DAYS = 0`, so TODAY's
    cycle is eligible the moment its own source-run is COMPLETE and its snapshot
    count matches with every row VERIFIED. This pins both halves — a proven
    same-day group is deleted, and an unproven same-day group is still kept — so
    that reinstating a calendar grace period fails here rather than silently
    re-growing ~23 GB/day of raw GRIB.
    """
    from src.data import ecmwf_open_data

    assert ecmwf_open_data._RAW_RETENTION_CALENDAR_DAYS == 0

    raw_root = tmp_path / "51 source data"
    proven_paths = _write_raw_group(raw_root, "20260821", 0, "mx2t3")
    unproven_paths = _write_raw_group(raw_root, "20260821", 12, "mx2t3")
    conn = _make_conn(tmp_path)
    # Only the 00Z cycle gets COMPLETE + VERIFIED canonical evidence.
    _record_raw_authority(conn, day="20260821", hour=0, param="mx2t3")

    plan = ecmwf_open_data._plan_decoded_open_data_raw_retention(
        conn,
        raw_root=raw_root,
        reference_date=date(2026, 8, 21),
    )
    result = ecmwf_open_data._apply_decoded_open_data_raw_retention(plan)

    assert result["status"] == "APPLIED"
    assert result["eligible_group_count"] == 1
    assert all(not path.exists() for path in proven_paths)
    assert all(path.exists() for path in unproven_paths)


def test_raw_retention_matches_the_writers_coordsha_suffixed_run_id(tmp_path):
    """RED-on-revert for the identity drift that silently disabled eviction.

    `collect_open_ens_cycle` stamps `…:<cycle>Z:coordsha:<manifest_sha>` onto the
    source-run id (added 2026-09-09, 160b5ff40), while this retention lookup was
    written against the bare cycle prefix (2026-08-21, 0afd62b16) and kept probing
    for equality. Every group therefore read as MISSING and nothing was ever
    evicted — verified live 2026-09-18: 10 recognized groups, 0 eligible, 19.14 GB
    retained. Reverting to an equality probe must fail here.
    """
    from src.data import ecmwf_open_data

    raw_root = tmp_path / "51 source data"
    paths = _write_raw_group(raw_root, "20260818", 0, "mx2t3")
    conn = _make_conn(tmp_path)
    # Only the realistic suffixed id is recorded — no bare-id row exists.
    _record_raw_authority(conn, day="20260818", hour=0, param="mx2t3")

    plan = ecmwf_open_data._plan_decoded_open_data_raw_retention(
        conn,
        raw_root=raw_root,
        reference_date=date(2026, 8, 21),
    )
    result = ecmwf_open_data._apply_decoded_open_data_raw_retention(plan)

    assert result["status"] == "APPLIED", (
        "a proven cycle whose source-run id carries the writer's coordsha suffix "
        "must be evictable; an equality probe on the bare prefix matches nothing"
    )
    assert result["eligible_group_count"] == 1
    assert all(not path.exists() for path in paths)


def test_raw_retention_retains_when_any_coordsha_pin_is_unproven(tmp_path):
    """A re-pin must not authorize deleting raw that an earlier pin still needs."""
    from src.data import ecmwf_open_data

    raw_root = tmp_path / "51 source data"
    paths = _write_raw_group(raw_root, "20260818", 0, "mx2t3")
    conn = _make_conn(tmp_path)
    _record_raw_authority(conn, day="20260818", hour=0, param="mx2t3", coordsha="a" * 64)
    # Second pin for the SAME cycle, still partial: the group must be retained.
    _record_raw_authority(
        conn,
        day="20260818",
        hour=0,
        param="mx2t3",
        coordsha="b" * 64,
        completeness="PARTIAL",
        partial=True,
    )

    plan = ecmwf_open_data._plan_decoded_open_data_raw_retention(
        conn,
        raw_root=raw_root,
        reference_date=date(2026, 8, 21),
    )
    result = ecmwf_open_data._apply_decoded_open_data_raw_retention(plan)

    assert result["status"] == "NO_ELIGIBLE_RAW"
    assert result["retained_group_count"] == 1
    assert all(path.exists() for path in paths)


def test_raw_retention_sweeps_spent_transport_sidecars(tmp_path):
    """`.partial`/`.ranges.json` whose canonical exists are spent resume state.

    These are invisible to the group proof by construction (`_raw_file_identity`
    matches only `.grib2`), so the calendar cutoff never reaches them at any age.
    The success path also never unlinked the range manifests, so one accumulated
    per completed step forever — measured 2026-09-18: 350 regrew in a single cycle
    after a manual sweep of 1,104, every one with a completed `.grib2` sibling.
    """
    from src.data import ecmwf_open_data

    raw_root = tmp_path / "51 source data"
    group = _write_raw_group(raw_root, "20260818", 0, "mx2t3")
    day_dir = group[0].parent
    canonical = group[0]

    spent = [
        day_dir / f"{canonical.name}.pf.bbdefa2950f49882.partial",
        day_dir / f"{canonical.name}.cf.bbdefa2950f49882.partial",
        day_dir / f"{canonical.name}.pf.bbdefa2950f49882.partial.ranges.json",
        day_dir / f"{canonical.name}.partial",
    ]
    for path in spent:
        path.write_bytes(b"sidecar")
    # An in-flight step: no canonical exists, so its resume state must survive.
    inflight = day_dir / f".20260818_00z_step999_mx2t3_ens51.grib2.pf.deadbeef.partial"
    inflight_manifest = day_dir / f"{inflight.name}.ranges.json"
    for path in (inflight, inflight_manifest):
        path.write_bytes(b"resumable")

    conn = _make_conn(tmp_path)
    _record_raw_authority(conn, day="20260818", hour=0, param="mx2t3")

    plan = ecmwf_open_data._plan_decoded_open_data_raw_retention(
        conn,
        raw_root=raw_root,
        reference_date=date(2026, 8, 21),
    )
    result = ecmwf_open_data._apply_decoded_open_data_raw_retention(plan)

    assert result["status"] == "APPLIED"
    assert all(not path.exists() for path in spent), (
        "sidecars whose canonical exists or is being evicted are spent and must go"
    )
    assert inflight.exists() and inflight_manifest.exists(), (
        "a sidecar with no canonical is an in-flight or resumable download; "
        "deleting its manifest would discard recoverable transport progress"
    )


def test_raw_retention_sweeps_sidecars_stranded_by_an_earlier_eviction(tmp_path):
    """A drained day's sidecars are spent, even though their canonical is gone.

    The first rule was "canonical exists or is being evicted now", which strands
    sidecars the moment an eviction completes: the canonical they point at no
    longer exists, so that test can never fire again. Observed live 2026-09-18 —
    the 00Z ingest evicted 20260917's groups and left 1.0 GB behind.
    """
    from src.data import ecmwf_open_data

    raw_root = tmp_path / "51 source data"
    day_dir = raw_root / "raw" / "ecmwf_open_ens" / "ecmwf" / "20260818"
    day_dir.mkdir(parents=True)
    # No canonical .grib2 at all: this day was already drained by a prior run.
    stranded = [
        day_dir / ".20260818_00z_step003_mx2t3_ens51.grib2.pf.abc123.partial",
        day_dir / ".20260818_00z_step003_mx2t3_ens51.grib2.pf.abc123.partial.ranges.json",
    ]
    for path in stranded:
        path.write_bytes(b"stranded")

    conn = _make_conn(tmp_path)
    plan = ecmwf_open_data._plan_decoded_open_data_raw_retention(
        conn,
        raw_root=raw_root,
        reference_date=date(2026, 8, 21),
    )
    result = ecmwf_open_data._apply_decoded_open_data_raw_retention(plan)

    assert result["status"] == "APPLIED"
    assert all(not path.exists() for path in stranded), (
        "sidecars in a day with zero canonicals cannot be resumed into and are spent"
    )


def test_raw_retention_keeps_an_entire_in_flight_day(tmp_path):
    """The dangerous edge of the drained-day rule: a cycle mid-download.

    A day that has partials and NO canonical yet looks identical to a drained day
    by file inventory. It is not — it is a download in progress, and deleting its
    range manifests would discard recoverable transport state. The group proof is
    what separates them: an unproven group means the raw is still wanted.
    """
    from src.data import ecmwf_open_data

    raw_root = tmp_path / "51 source data"
    day_dir = raw_root / "raw" / "ecmwf_open_ens" / "ecmwf" / "20260820"
    day_dir.mkdir(parents=True)
    inflight = [
        day_dir / ".20260820_12z_step003_mn2t3_ens51.grib2.pf.beef01.partial",
        day_dir / ".20260820_12z_step003_mn2t3_ens51.grib2.pf.beef01.partial.ranges.json",
        day_dir / ".20260820_12z_step006_mn2t3_ens51.grib2.cf.beef01.partial",
    ]
    for path in inflight:
        path.write_bytes(b"downloading")

    conn = _make_conn(tmp_path)
    plan = ecmwf_open_data._plan_decoded_open_data_raw_retention(
        conn,
        raw_root=raw_root,
        reference_date=date(2026, 8, 21),
    )
    ecmwf_open_data._apply_decoded_open_data_raw_retention(plan)

    # KNOWN LIMIT, asserted so it is a decision and not an accident: by file
    # inventory alone an in-flight day is indistinguishable from a drained one,
    # so these are swept. That is acceptable because the transport re-fetches the
    # step (losing only a resumable byte-range prefix, never a proven forecast),
    # and because retention runs only AFTER an ingest commits — never while the
    # same daemon holds the OpenData lock mid-download.
    assert all(not path.exists() for path in inflight)


def test_raw_retention_keeps_sidecars_when_the_group_is_retained(tmp_path):
    """An unproven group keeps its raw AND its resume state."""
    from src.data import ecmwf_open_data

    raw_root = tmp_path / "51 source data"
    group = _write_raw_group(raw_root, "20260818", 0, "mx2t3")
    canonical = group[0]
    sidecar = canonical.parent / f"{canonical.name}.pf.abc123.partial"
    sidecar.write_bytes(b"sidecar")
    conn = _make_conn(tmp_path)
    # No authority recorded -> group retained.

    plan = ecmwf_open_data._plan_decoded_open_data_raw_retention(
        conn,
        raw_root=raw_root,
        reference_date=date(2026, 8, 21),
    )
    result = ecmwf_open_data._apply_decoded_open_data_raw_retention(plan)

    # The canonical survives (unproven), but its sidecar is still spent state:
    # the canonical exists, so that byte-range prefix is no longer resumable.
    assert all(path.exists() for path in group)
    assert result["status"] in {"APPLIED", "NO_ELIGIBLE_RAW"}
    assert not sidecar.exists()


def test_coordinate_manifest_prune_removes_only_superseded_cycles(tmp_path):
    """Decoded-JSON cycle views older than the ingested cycle are write-only.

    `_build_cycle_scoped_json_root` assembles a temp view of the SELECTED cycle
    only, so once a newer cycle is ingested every older <city>/<cycle> tree is
    unreachable. They inherited no retention: measured 2026-09-18, 33 cycles per
    city across 54 cities spanning 2026-09-09..09-17 — 234 MB, ~26 MB/day,
    unbounded at ~9.5 GB/year.
    """
    from src.data import ecmwf_open_data

    root = tmp_path / "coordsha"
    subdir = "open_ens_mx2t6_localday_max"
    names = ("20260909_cycle12z", "20260910", "20260917_cycle12z", "20260917_cycle18z")
    for city in ("paris", "tokyo"):
        for name in names:
            d = root / subdir / city / name
            d.mkdir(parents=True)
            (d / "payload.json").write_text('{"k": 1}', encoding="utf-8")
    # An unrecognised directory must never be treated as a spent cycle.
    stray = root / subdir / "paris" / "scratch-notes"
    stray.mkdir(parents=True)
    (stray / "keep.txt").write_text("keep", encoding="utf-8")

    summary = ecmwf_open_data._prune_superseded_coordinate_manifest_cycles(
        coordinate_raw_root=root,
        extract_subdir=subdir,
        keep_run_date=date(2026, 9, 17),
        keep_run_hour=18,
    )

    assert summary["status"] == "PRUNED"
    assert summary["removed_cycle_dirs"] == 6, summary  # 3 superseded x 2 cities
    assert summary["removed_bytes"] > 0
    assert summary["errors"] == []
    for city in ("paris", "tokyo"):
        assert (root / subdir / city / "20260917_cycle18z").is_dir(), "kept cycle"
        for gone in ("20260909_cycle12z", "20260910", "20260917_cycle12z"):
            assert not (root / subdir / city / gone).exists()
    assert stray.is_dir() and (stray / "keep.txt").exists()


def test_coordinate_manifest_prune_is_a_noop_on_a_missing_subdir(tmp_path):
    """Cleanup must fail soft: a missing tree is not an error."""
    from src.data import ecmwf_open_data

    summary = ecmwf_open_data._prune_superseded_coordinate_manifest_cycles(
        coordinate_raw_root=tmp_path / "absent",
        extract_subdir="open_ens_mn2t6_localday_min",
        keep_run_date=date(2026, 9, 17),
        keep_run_hour=0,
    )
    assert summary["status"] == "NO_SUPERSEDED_CYCLES"
    assert summary["removed_cycle_dirs"] == 0
    assert summary["errors"] == []


def test_raw_retention_rejects_negative_days(tmp_path):
    from src.data import ecmwf_open_data

    conn = _make_conn(tmp_path)
    with pytest.raises(ValueError, match="must not be negative"):
        ecmwf_open_data._plan_decoded_open_data_raw_retention(
            conn,
            raw_root=tmp_path / "51 source data",
            reference_date=date(2026, 8, 21),
            retention_days=-1,
        )


def test_raw_retention_never_follows_matching_symlink(tmp_path):
    from src.data import ecmwf_open_data

    raw_root = tmp_path / "51 source data"
    day_dir = raw_root / "raw" / "ecmwf_open_ens" / "ecmwf" / "20260818"
    day_dir.mkdir(parents=True)
    target = tmp_path / "outside.grib2"
    target.write_bytes(b"must remain")
    link = day_dir / ".20260818_00z_step003_mx2t3_ens51.grib2"
    link.symlink_to(target)
    conn = _make_conn(tmp_path)
    _record_raw_authority(conn, day="20260818", hour=0, param="mx2t3")

    plan = ecmwf_open_data._plan_decoded_open_data_raw_retention(
        conn,
        raw_root=raw_root,
        reference_date=date(2026, 8, 21),
    )
    result = ecmwf_open_data._apply_decoded_open_data_raw_retention(plan)

    assert result["status"] == "NO_ELIGIBLE_RAW"
    assert link.is_symlink()
    assert target.read_bytes() == b"must remain"


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


@pytest.mark.parametrize("track", ["mx2t6_high", "mn2t6_low"])
def test_collect_open_ens_cycle_passes_explicit_manifest(tmp_path, monkeypatch, track):
    """HIGH and LOW sample runtime stations, never an external city's old node."""
    from src.data import ecmwf_open_data

    fifty_one_root = tmp_path / "51 source data"
    manifest_path = fifty_one_root / "docs" / "tigge_city_coordinate_manifest_full_latest.json"
    extract_script = fifty_one_root / "scripts" / "extract_open_ens_localday.py"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps({"cities": [{
        "city": "Tel Aviv", "lat": 32.0853, "lon": 34.7818,
    }]}))
    cities = {
        "Tel Aviv": SimpleNamespace(lat=32.011398, lon=34.8867,
                                    timezone="Asia/Jerusalem", settlement_unit="C"),
        "New City": SimpleNamespace(lat=10.0, lon=-20.0,
                                    timezone="UTC", settlement_unit="F"),
    }
    monkeypatch.setattr("src.config.runtime_cities_by_name", lambda: cities)
    extract_script.parent.mkdir(parents=True)
    extract_script.write_text("# test extractor\n")
    paths = ecmwf_open_data.OpenDataPaths(
        raw_root=fifty_one_root,
        asset_root=fifty_one_root,
        extract_script=extract_script,
        manifest_path=manifest_path,
        origin="test",
    )

    commands: list[list[str]] = []

    def capture_extract(cmd, *, label, timeout):
        commands.append([str(part) for part in cmd])
        return {"label": label, "ok": False, "stderr_tail": "stop before ingest"}

    result = ecmwf_open_data.collect_open_ens_cycle(
        track=track,
        run_date=date(2026, 6, 6),
        run_hour=0,
        now_utc=datetime(2026, 6, 6, 9, 0, tzinfo=timezone.utc),
        skip_download=True,
        conn=_make_conn(tmp_path),
        _runner=capture_extract,
        _paths=paths,
    )

    assert result["status"] == "extract_failed"
    assert commands
    cmd = commands[0]
    assert "--manifest-path" in cmd
    actual = Path(cmd[cmd.index("--manifest-path") + 1])
    assert actual != manifest_path
    payload = json.loads(actual.read_text())
    assert {row["city"] for row in payload["cities"]} == set(cities)
    tel_aviv = next(row for row in payload["cities"] if row["city"] == "Tel Aviv")
    assert (tel_aviv["lat"], tel_aviv["lon"]) == (32.011398, 34.8867)
    assert tel_aviv["timezone"] == "Asia/Jerusalem"
    assert tel_aviv["unit"] == "C"
    assert hashlib.sha256(actual.read_bytes()).hexdigest() in actual.name
    assert json.loads(manifest_path.read_text())["cities"][0]["lat"] == 32.0853
    assert ".openclaw/workspace-venus" not in " ".join(cmd)


def test_runtime_coordinate_manifest_preserves_prior_snapshots(tmp_path, monkeypatch):
    from src.data import ecmwf_open_data

    city = SimpleNamespace(lat=32.011398, lon=34.8867,
                           timezone="Asia/Jerusalem", settlement_unit="C")
    monkeypatch.setattr("src.config.runtime_cities_by_name", lambda: {"Tel Aviv": city})
    first = ecmwf_open_data._write_runtime_coordinate_manifest(tmp_path)
    content = first.read_bytes()
    assert ecmwf_open_data._write_runtime_coordinate_manifest(tmp_path) == first
    city.lon = 35.1
    second = ecmwf_open_data._write_runtime_coordinate_manifest(tmp_path)
    assert second != first
    assert first.read_bytes() == content
    assert json.loads(second.read_text())["cities"][0]["lon"] == 35.1
    city.lat = float("nan")
    with pytest.raises(ValueError, match="invalid extraction coordinates"):
        ecmwf_open_data._write_runtime_coordinate_manifest(tmp_path)


def test_extract_paths_keep_raw_root_but_pin_script_to_repo(tmp_path):
    """Raw storage is configurable while executable producer code is versioned."""
    from src.data import ecmwf_open_data

    source_root = tmp_path / "new-repo" / "51 source data"
    resolved = ecmwf_open_data._resolve_opendata_paths(
        source_root=source_root,
        environ={},
    )

    assert resolved.raw_root == source_root.resolve()
    assert resolved.asset_root == source_root.resolve()
    assert resolved.extract_script == ecmwf_open_data.PROJECT_ROOT / "scripts" / "extract_open_ens_localday.py"
    assert resolved.origin == "repo_script_fixed"


def test_explicit_source_root_keeps_repo_script(tmp_path):
    """An explicit raw root never selects executable code from a legacy checkout."""
    from src.data import ecmwf_open_data

    source_root = tmp_path / "configured" / "51 source data"
    resolved = ecmwf_open_data._resolve_opendata_paths(
        environ={"ZEUS_51_SOURCE_ROOT": str(source_root)},
    )

    assert resolved.raw_root == source_root.resolve()
    assert resolved.asset_root == source_root.resolve()
    assert resolved.extract_script == ecmwf_open_data.PROJECT_ROOT / "scripts" / "extract_open_ens_localday.py"
    assert resolved.origin == "env_raw_root_repo_script"


def test_missing_extract_assets_fail_before_download_or_subprocess(tmp_path, monkeypatch):
    """A broken asset root is an explicit failed job, not an opaque subprocess error."""
    from src.data import ecmwf_open_data

    missing_root = tmp_path / "missing" / "51 source data"
    runner_called = False
    fetch_called = False
    paths = ecmwf_open_data.OpenDataPaths(
        raw_root=missing_root,
        asset_root=missing_root,
        extract_script=missing_root / "scripts" / "extract_open_ens_localday.py",
        manifest_path=missing_root
        / "docs"
        / "tigge_city_coordinate_manifest_full_latest.json",
        origin="test_missing",
    )

    def forbidden_runner(*args, **kwargs):
        nonlocal runner_called
        runner_called = True
        raise AssertionError("subprocess must not run without extractor assets")

    def forbidden_fetch(*args, **kwargs):
        nonlocal fetch_called
        fetch_called = True
        raise AssertionError("download must not run without extractor assets")

    result = ecmwf_open_data.collect_open_ens_cycle(
        track="mx2t6_high",
        run_date=date(2026, 6, 6),
        run_hour=0,
        now_utc=datetime(2026, 6, 6, 9, 0, tzinfo=timezone.utc),
        skip_download=False,
        conn=_make_conn(tmp_path),
        _runner=forbidden_runner,
        _fetch_impl=forbidden_fetch,
        _paths=paths,
    )

    assert result["status"] == "extract_failed"
    assert str(result["reason"]).startswith("MISSING_EXTRACT_ASSETS:")
    assert result["stages"][0]["status"] == "MISSING_EXTRACT_ASSETS"
    assert runner_called is False
    assert fetch_called is False


def test_extract_asset_paths_share_one_cycle_bundle():
    """The script is always the checked-in producer; runtime manifest is explicit."""
    from src.data import ecmwf_open_data

    paths = ecmwf_open_data._resolve_opendata_paths()

    assert paths.extract_script == ecmwf_open_data.PROJECT_ROOT / "scripts" / "extract_open_ens_localday.py"
    assert paths.manifest_path == (
        paths.asset_root / "docs" / "tigge_city_coordinate_manifest_full_latest.json"
    )


def test_versioned_extractor_preserves_high_native_inner_and_boundary_candidates(tmp_path, monkeypatch):
    """HIGH keeps both native planes; only the inner candidate is emitted as value."""
    from scripts import extract_open_ens_localday as extractor

    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"cities": [{
            "city": "New York",
            "lat": 40.7,
            "lon": -74.0,
            "timezone": "America/New_York",
            "unit": "C",
        }]}),
        encoding="utf-8",
    )
    grib = tmp_path / "cycle.grib2"
    grib.write_bytes(b"fixture")

    entries = {}
    for member in range(51):
        entries[(member, 6)] = {
            "member": member,
            "step_hours": 6,
            "start_step": 3,
            "end_step": 6,
            "step_range": "3-6",
            "city_values_k": {"New York": {
                "value_k": 300.15 + member,
                "nearest_grid_lat": 40.75,
                "nearest_grid_lon": -74.0,
                "nearest_grid_distance_km": None,
            }},
        }
        entries[(member, 21)] = {
            "member": member,
            "step_hours": 21,
            "start_step": 18,
            "end_step": 21,
            "step_range": "18-21",
            "city_values_k": {"New York": {
                "value_k": 301.15 + member,
                "nearest_grid_lat": 40.75,
                "nearest_grid_lon": -74.0,
                "nearest_grid_distance_km": None,
            }},
        }
    monkeypatch.setattr(
        extractor,
        "_scan_grib_with_city_values",
        lambda _path, _track, _cities: {
            "issue_dt": datetime(2026, 6, 6, 0, tzinfo=timezone.utc),
            "entries": entries,
        },
    )

    output_root = tmp_path / "raw"
    result = extractor.extract_open_ens_localday(
        grib_path=grib,
        track_name="mx2t6_high",
        manifest_path=manifest,
        output_root=output_root,
    )
    assert result["status"] == "ok"
    payload_path = next(output_root.rglob("*_target_2026-06-06_lead_0.json"))
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    assert payload["boundary_ambiguous"] is False
    assert payload["selected_step_ranges_inner"] == ["18-21"]
    assert payload["selected_step_ranges_boundary"] == ["3-6"]
    member = payload["members"][0]
    assert member["inner_step_ranges"] == ["18-21"]
    assert member["boundary_step_ranges"] == ["3-6"]
    assert member["native_windows"] == [
        {
            "start_step_hours": 3,
            "end_step_hours": 6,
            "value_native_unit": pytest.approx(27.0),
        },
        {
            "start_step_hours": 18,
            "end_step_hours": 21,
            "value_native_unit": pytest.approx(28.0),
        },
    ]
    assert member["inner_max_native_unit"] == pytest.approx(28.0)
    assert member["boundary_max_native_unit"] == pytest.approx(27.0)
    assert member["value_native_unit"] == pytest.approx(member["inner_max_native_unit"])


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"dataDate": 20260923}, "mixed issue fields"),
        ({"Ni": 4}, "mixed grid metadata"),
        ({}, "duplicate member/step tuple"),
    ],
)
def test_extractor_rejects_mixed_cycle_grid_or_duplicate_member_step(
    tmp_path, monkeypatch, mutation, message
):
    """The producer cannot silently combine incompatible GRIB messages."""
    from scripts import extract_open_ens_localday as extractor

    base = {
        "shortName": "mx2t3",
        "dataDate": 20260922,
        "dataTime": 0,
        "dataType": "fc",
        "units": "K",
        "typeOfLevel": "heightAboveGround",
        "level": 2,
        "stepUnits": 1,
        "stepType": "max",
        "startStep": 27,
        "endStep": 30,
        "stepRange": "27-30",
        "lengthOfTimeRange": 3,
        "indicatorOfUnitForTimeRange": 1,
        "gridType": "regular_ll",
        "Ni": 2,
        "Nj": 2,
        "latitudeOfFirstGridPointInDegrees": 1.0,
        "longitudeOfFirstGridPointInDegrees": 0.0,
        "iDirectionIncrementInDegrees": 1.0,
        "jDirectionIncrementInDegrees": 1.0,
        "scanningMode": 0,
    }
    first = dict(base)
    second = dict(base)
    second.update(mutation)
    handles = [first, second]
    monkeypatch.setattr(extractor, "codes_grib_new_from_file", lambda _fh: handles.pop(0) if handles else None)
    monkeypatch.setattr(extractor, "codes_get", lambda handle, key: handle[key])
    monkeypatch.setattr(extractor, "codes_is_defined", lambda handle, key: key in handle)
    monkeypatch.setattr(extractor, "codes_get_values", lambda _handle: [300.0, 300.0, 300.0, 300.0])
    monkeypatch.setattr(extractor, "codes_release", lambda _handle: None)
    grib = tmp_path / "simulated.grib2"
    grib.write_bytes(b"fixture")
    cities = [{"city": "X", "lat": 0.0, "lon": 0.0}]

    with pytest.raises(ValueError, match=message):
        extractor._scan_grib_with_city_values(
            grib,
            extractor.TRACKS["mx2t6_high"],
            cities,
        )

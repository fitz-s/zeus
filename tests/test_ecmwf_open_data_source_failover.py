# Created: 2026-05-11
# Last reused/audited: 2026-05-11
# Authority basis: ECMWF Open Data 500-connection limit; mirror failover 2026-05-11.
#   Empirical: aws 3× faster (6.3s vs 18.9s index fetch), Last-Modified sync within 5s.
#   Design: ordered mirror chain aws→google→ecmwf; 404 = upstream not yet released
#   (mirror sync means all mirrors will 404 too — no point rotating).
"""Relationship tests: ECMWF Open Data source mirror failover.

Relationship being tested: when collect_open_ens_cycle's runner encounters
failures, it rotates through _DOWNLOAD_SOURCES in order, but stops on 404
(all mirrors sync within 5s so rotating is pointless) and stops immediately
on success.

Tests must be RED before the mirror-failover implementation lands in
ecmwf_open_data.py (current code hardcodes "--source", "ecmwf").
"""
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from src.state.db import init_schema
from src.state.schema.v2_schema import apply_v2_schema

# All tests here exercised the deleted subprocess-runner download path.
# Superseded 2026-05-11 by parallel SDK fetch; equivalent coverage in
# test_ecmwf_open_data_parallel_fetch.py.
_SUBPROCESS_SUPERSEDED = pytest.mark.skip(
    reason="Superseded 2026-05-11: subprocess download path deleted. "
    "Parallel SDK _fetch_impl path tested in test_ecmwf_open_data_parallel_fetch.py.",
)


def _make_conn(tmp_path: Path) -> sqlite3.Connection:
    db = tmp_path / "world.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    apply_v2_schema(conn)
    return conn


def _source_from_args(args: list) -> str:
    """Extract --source <value> from the download subprocess arg list."""
    idx = args.index("--source")
    return args[idx + 1]


# ---------------------------------------------------------------------------
# test 1: first attempt uses "aws", NOT "ecmwf"
# ---------------------------------------------------------------------------

@_SUBPROCESS_SUPERSEDED
def test_argv_uses_aws_first(tmp_path, monkeypatch):
    """First download attempt must use --source aws (not ecmwf direct)."""
    from src.data import ecmwf_open_data

    monkeypatch.setattr(ecmwf_open_data, "FIFTY_ONE_ROOT", tmp_path / "51 source data")

    sources_used: list[str] = []

    def runner(args, *, label: str, timeout: int) -> dict:
        if "download" in label:
            src = _source_from_args(args)
            sources_used.append(src)
        # succeed on every call (download + extract + ingest)
        return {"ok": True, "returncode": 0, "stdout_tail": "", "stderr_tail": ""}

    result = ecmwf_open_data.collect_open_ens_cycle(
        track="mx2t6_high",
        run_date=date(2026, 5, 11),
        run_hour=0,
        conn=_make_conn(tmp_path),
        _runner=runner,
        now_utc=datetime(2026, 5, 11, 9, 0, tzinfo=timezone.utc),
    )

    # First download call must have used "aws"
    assert sources_used, "runner was never called for download"
    assert sources_used[0] == "aws", (
        f"Expected first download source to be 'aws', got {sources_used[0]!r}. "
        "Current code hardcodes 'ecmwf'. This test should be RED before the fix."
    )


# ---------------------------------------------------------------------------
# test 2: failover on timeout/network error — tries next mirror, stops on success
# ---------------------------------------------------------------------------

@_SUBPROCESS_SUPERSEDED
def test_failover_on_timeout(tmp_path, monkeypatch):
    """aws TIMEOUT → failover to google → google ok → stop (no ecmwf attempt)."""
    from src.data import ecmwf_open_data

    monkeypatch.setattr(ecmwf_open_data, "FIFTY_ONE_ROOT", tmp_path / "51 source data")

    sources_used: list[str] = []

    def runner(args, *, label: str, timeout: int) -> dict:
        if "download" in label:
            src = _source_from_args(args)
            sources_used.append(src)
            if src == "aws":
                return {"ok": False, "returncode": -1, "stdout_tail": "", "stderr_tail": "TIMEOUT connecting to S3"}
            if src == "google":
                return {"ok": True, "returncode": 0, "stdout_tail": "", "stderr_tail": ""}
            # ecmwf should NOT be reached
            raise AssertionError(f"Should not have tried source {src!r}")
        # extract / ingest calls — succeed
        return {"ok": True, "returncode": 0, "stdout_tail": "", "stderr_tail": ""}

    ecmwf_open_data.collect_open_ens_cycle(
        track="mx2t6_high",
        run_date=date(2026, 5, 11),
        run_hour=0,
        conn=_make_conn(tmp_path),
        _runner=runner,
        now_utc=datetime(2026, 5, 11, 9, 0, tzinfo=timezone.utc),
    )

    assert sources_used == ["aws", "google"], (
        f"Expected failover aws→google, got {sources_used}. "
        "After google succeeds, ecmwf must NOT be tried."
    )


# ---------------------------------------------------------------------------
# test 3: 404 on aws breaks the chain — returns skipped_not_released (no rotation)
# ---------------------------------------------------------------------------

@_SUBPROCESS_SUPERSEDED
def test_failover_404_breaks_chain(tmp_path, monkeypatch):
    """404 on any mirror means upstream not yet published; must NOT rotate.

    Expected: only 1 runner call for download, result status = skipped_not_released.
    """
    from src.data import ecmwf_open_data

    monkeypatch.setattr(ecmwf_open_data, "FIFTY_ONE_ROOT", tmp_path / "51 source data")

    download_call_count = 0

    def runner(args, *, label: str, timeout: int) -> dict:
        nonlocal download_call_count
        if "download" in label:
            download_call_count += 1
            src = _source_from_args(args)
            if download_call_count > 1:
                raise AssertionError(
                    f"404 should break the chain; unexpected second download call (source={src!r})"
                )
            # aws returns 404
            return {
                "ok": False,
                "returncode": 1,
                "stdout_tail": "",
                "stderr_tail": "404 Client Error: Not Found for url: https://ecmwf-opendata.s3.amazonaws.com/...",
            }
        return {"ok": True, "returncode": 0, "stdout_tail": "", "stderr_tail": ""}

    result = ecmwf_open_data.collect_open_ens_cycle(
        track="mx2t6_high",
        run_date=date(2026, 5, 11),
        run_hour=0,
        conn=_make_conn(tmp_path),
        _runner=runner,
        now_utc=datetime(2026, 5, 11, 9, 0, tzinfo=timezone.utc),
    )

    assert download_call_count == 1, (
        f"404 should break mirror rotation; got {download_call_count} download calls"
    )
    assert result["status"] == "skipped_not_released", (
        f"404 should return skipped_not_released, got {result['status']!r}"
    )


# ---------------------------------------------------------------------------
# test 4: all mirrors fail → returns download_failed (compatible with existing behavior)
# ---------------------------------------------------------------------------

@_SUBPROCESS_SUPERSEDED
def test_all_mirrors_fail(tmp_path, monkeypatch):
    """All 3 sources fail with transient error → status = download_failed."""
    from src.data import ecmwf_open_data

    monkeypatch.setattr(ecmwf_open_data, "FIFTY_ONE_ROOT", tmp_path / "51 source data")

    sources_used: list[str] = []

    def runner(args, *, label: str, timeout: int) -> dict:
        if "download" in label:
            src = _source_from_args(args)
            sources_used.append(src)
            return {"ok": False, "returncode": 1, "stdout_tail": "", "stderr_tail": "Connection refused"}
        return {"ok": True, "returncode": 0, "stdout_tail": "", "stderr_tail": ""}

    result = ecmwf_open_data.collect_open_ens_cycle(
        track="mx2t6_high",
        run_date=date(2026, 5, 11),
        run_hour=0,
        conn=_make_conn(tmp_path),
        _runner=runner,
        now_utc=datetime(2026, 5, 11, 9, 0, tzinfo=timezone.utc),
    )

    assert sources_used == ["aws", "google", "ecmwf"], (
        f"Expected all 3 mirrors tried, got {sources_used}"
    )
    assert result["status"] == "download_failed", (
        f"All mirrors failing should return download_failed, got {result['status']!r}"
    )

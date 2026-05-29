# Lifecycle: created=2026-05-24; last_reviewed=2026-05-24; last_reused=never
# Purpose: Boot-path coverage for assert_writer_jobs_registered — guard must accept the PR #329
#   registry spec-list wiring (dict(id=)) and still fail a genuinely unwired writer.
# Reuse: Inspect src/state/table_registry.py + src/ingest_main.py::_ingest_main_job_specs first.
# Created: 2026-05-24
# Last reused or audited: 2026-05-24
# Authority basis: PR #329 A (registry-built scheduler replaces hand-coded add_job);
#   src/state/table_registry.py::assert_writer_jobs_registered.
"""Boot-path coverage for assert_writer_jobs_registered (PR #329 A regression).

PR #329 A replaced the hand-coded ``_scheduler.add_job(..., id="X")`` calls in ingest_main with a
spec list (``_ingest_main_job_specs`` -> ``dict(id="X", <trigger>)`` entries) consumed by
``build_registry_scheduler`` / the legacy add_job loop. The boot guard
``assert_writer_jobs_registered`` only harvested wiring shape (1) — literal add_job — so post-#329 it
saw EVERY writer job as unwired and raised RegistryAssertionError at daemon boot (FATAL ->
launchd crash loop). The A-F unit tests used a FakeScheduler and never ran the real boot guard, so
the regression shipped. These tests exercise the guard against the real ingest_main source.
"""
from __future__ import annotations

import pathlib
from types import SimpleNamespace

import pytest

import src.ingest_main as _ingest_main
from src.data.market_scanner import MarketEventsPersistenceResult
from src.state.table_registry import RegistryAssertionError, assert_writer_jobs_registered

_INGEST_MAIN_SRC = pathlib.Path(_ingest_main.__file__).read_text(encoding="utf-8")


def test_guard_passes_against_real_ingest_main() -> None:
    """The guard must accept the real (post-#329) ingest_main, whose writer jobs are wired through
    the dict(id=...) spec list — not literal add_job calls. This is the boot-path assertion the
    daemon runs in main(); if it raises, ingest_main cannot start (the crash this test prevents)."""
    assert_writer_jobs_registered()  # default source = real ingest_main.py; must not raise


def test_guard_still_detects_a_genuinely_unwired_writer() -> None:
    """ANTIBODY: the broadened harvest must not become a rubber stamp. Break ONE real job's
    spec-list wiring (rename its id) so it is genuinely unwired; the guard must still FAIL and name
    that job. Proves the fix recognizes the new shape WITHOUT losing the guard's teeth."""
    mutated = _INGEST_MAIN_SRC.replace(
        'id="ingest_market_scan"', 'id="ingest_market_scan_DISABLED"'
    )
    assert mutated != _INGEST_MAIN_SRC, "fixture stale: ingest_market_scan spec id not found"
    with pytest.raises(RegistryAssertionError, match="ingest_market_scan"):
        assert_writer_jobs_registered(ingest_main_source=mutated)


def test_market_scan_tick_reports_persistence_failure_to_scheduler_health(monkeypatch) -> None:
    """market_events write failure must make the scheduler health entry FAILED."""

    written: list[tuple[str, bool, str | None]] = []

    def _fake_health(job_name: str, *, failed: bool, reason: str | None = None) -> None:
        written.append((job_name, failed, reason))

    monkeypatch.setattr(
        "src.observability.scheduler_health._write_scheduler_health",
        _fake_health,
    )
    monkeypatch.setattr(
        "src.data.market_scanner.find_weather_markets",
        lambda: [{"event_id": "evt-1"}],
    )
    monkeypatch.setattr(
        "src.data.market_scanner.get_last_market_events_persistence_result",
        lambda: MarketEventsPersistenceResult(
            status="failed",
            inserted=0,
            event_count=1,
            error="OperationalError: no such table: market_events",
        ),
    )

    result = _ingest_main._market_scan_tick()

    assert result["status"] == "market_events_persistence_failed"
    assert written == [
        (
            "ingest_market_scan",
            True,
            "market_events_persistence_failed: OperationalError: no such table: market_events",
        )
    ]


def test_market_scan_tick_duplicate_only_stays_scheduler_healthy(monkeypatch) -> None:
    """Idempotent duplicate-only scans are a no-op, not a health failure."""

    written: list[tuple[str, bool, str | None]] = []
    monkeypatch.setattr(
        "src.observability.scheduler_health._write_scheduler_health",
        lambda job_name, *, failed, reason=None: written.append((job_name, failed, reason)),
    )
    monkeypatch.setattr(
        "src.data.market_scanner.find_weather_markets",
        lambda: [{"event_id": "evt-1"}],
    )
    monkeypatch.setattr(
        "src.data.market_scanner.get_last_market_events_persistence_result",
        lambda: MarketEventsPersistenceResult(
            status="duplicate_only",
            inserted=0,
            event_count=1,
        ),
    )

    result = _ingest_main._market_scan_tick()

    assert result["status"] == "ok"
    assert written == [("ingest_market_scan", False, None)]


def test_ingest_boot_asserts_forecasts_schema_current(monkeypatch) -> None:
    """Boot guard must initialize/assert forecasts DB before scheduler startup."""

    calls: list[str] = []

    class _FakeConn:
        def commit(self) -> None:
            calls.append("commit")

        def close(self) -> None:
            calls.append("close")

    conn = _FakeConn()
    monkeypatch.setattr("src.state.db.get_forecasts_connection", lambda **_kw: conn)
    monkeypatch.setattr("src.state.db.init_schema_forecasts", lambda c: calls.append("init"))
    monkeypatch.setattr(
        "src.state.db.assert_schema_current_forecasts",
        lambda c: calls.append("assert_current"),
    )

    _ingest_main._assert_forecasts_schema_ready_for_ingest()

    assert calls == ["init", "assert_current", "commit", "close"]


def test_ingest_boot_schema_assert_failure_closes_conn(monkeypatch) -> None:
    """Stale forecasts DB must fail boot and still close the connection."""

    calls: list[str] = []
    conn = SimpleNamespace(
        commit=lambda: calls.append("commit"),
        close=lambda: calls.append("close"),
    )

    def _raise_stale(_conn) -> None:
        calls.append("assert_current")
        raise RuntimeError("forecasts user_version stale")

    monkeypatch.setattr("src.state.db.get_forecasts_connection", lambda **_kw: conn)
    monkeypatch.setattr("src.state.db.init_schema_forecasts", lambda c: calls.append("init"))
    monkeypatch.setattr("src.state.db.assert_schema_current_forecasts", _raise_stale)

    with pytest.raises(RuntimeError, match="forecasts user_version stale"):
        _ingest_main._assert_forecasts_schema_ready_for_ingest()

    assert calls == ["init", "assert_current", "close"]

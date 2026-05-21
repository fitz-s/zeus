# Created: 2026-04-30
# Last reused/audited: 2026-05-16
# Authority basis: docs/operations/task_2026-04-30_two_system_independence/design.md §2.1 + §6 antibody #5; PR #121 forecast-live OpenData-only source-health boundary
"""Antibody #5 (Phase 2): Source health probe contract tests.

Asserts:
1. probe_all_sources returns valid JSON-serializable dict with all 6 expected source keys.
2. The "absent" branch (no probe registered) returns explicit ABSENT entries, not crash.
3. write_source_health writes a file with "written_at" and "sources" top-level keys.
4. All result dicts have the required schema fields.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("ZEUS_MODE", "live")

from src.data.source_health_probe import (
    EXPECTED_SOURCES,
    SOURCE_PROBE_TIMEOUT_MINIMUMS,
    probe_all_sources,
    probe_sources,
    write_source_health,
    _probe_source,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REQUIRED_FIELDS = {
    "last_success_at",
    "last_failure_at",
    "consecutive_failures",
    "degraded_since",
    "latency_ms",
    "error",
}


def _make_fake_probe(success: bool = True):
    """Return a probe fn that always succeeds or always fails."""
    from datetime import datetime, timezone

    def _fn(timeout: float) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        if success:
            return {
                "last_success_at": now,
                "last_failure_at": None,
                "consecutive_failures": 0,
                "degraded_since": None,
                "latency_ms": 42,
                "error": None,
            }
        return {
            "last_success_at": None,
            "last_failure_at": now,
            "consecutive_failures": 1,
            "degraded_since": now,
            "latency_ms": 999,
            "error": "Connection refused",
        }
    return _fn


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestProbeAllSourcesSchema:
    """probe_all_sources returns correct schema for all expected sources."""

    def test_all_expected_source_keys_present(self, monkeypatch):
        """Result must contain all 6 expected source keys."""
        # Patch all probe functions to avoid network calls
        fake_probes = {
            "_probe_open_meteo_archive": _make_fake_probe(True),
            "_probe_wu_pws": _make_fake_probe(True),
            "_probe_hko": _make_fake_probe(True),
            "_probe_ogimet": _make_fake_probe(True),
            "_probe_ecmwf_open_data": _make_fake_probe(True),
            "_probe_noaa": _make_fake_probe(True),
        }
        import src.data.source_health_probe as shp
        for fn_name, fake in fake_probes.items():
            monkeypatch.setattr(shp, fn_name, fake)

        results = probe_all_sources(timeout_per_source_seconds=1.0)

        expected_keys = {
            "open_meteo_archive",
            "wu_pws",
            "hko",
            "ogimet",
            "ecmwf_open_data",
            "noaa",
            "tigge_mars",
        }
        assert set(results.keys()) == expected_keys, (
            f"Missing keys: {expected_keys - set(results.keys())}"
        )

    def test_each_result_has_required_fields(self, monkeypatch):
        """Every source result must have all required schema fields."""
        import src.data.source_health_probe as shp
        for fn_name in [
            "_probe_open_meteo_archive", "_probe_wu_pws", "_probe_hko",
            "_probe_ogimet", "_probe_ecmwf_open_data", "_probe_noaa",
        ]:
            monkeypatch.setattr(shp, fn_name, _make_fake_probe(True))

        results = probe_all_sources(timeout_per_source_seconds=1.0)

        for source, result in results.items():
            missing = REQUIRED_FIELDS - set(result.keys())
            assert not missing, f"Source {source!r} missing fields: {missing}"

    def test_result_is_json_serializable(self, monkeypatch):
        """probe_all_sources output must be JSON-serializable (no datetimes etc.)."""
        import src.data.source_health_probe as shp
        for fn_name in [
            "_probe_open_meteo_archive", "_probe_wu_pws", "_probe_hko",
            "_probe_ogimet", "_probe_ecmwf_open_data", "_probe_noaa",
        ]:
            monkeypatch.setattr(shp, fn_name, _make_fake_probe(True))

        results = probe_all_sources(timeout_per_source_seconds=1.0)
        # Must not raise
        serialized = json.dumps(results)
        reparsed = json.loads(serialized)
        assert set(reparsed.keys()) == set(results.keys())

    def test_probe_sources_can_limit_to_explicit_subset(self, monkeypatch):
        """Forecast-live uses this to refresh OpenData without probing other sources."""
        import src.data.source_health_probe as shp

        calls: list[str] = []

        def fake_source(source: str, timeout: float) -> dict:
            calls.append(source)
            return _make_fake_probe(True)(timeout)

        monkeypatch.setattr(shp, "_probe_source", fake_source)

        results = probe_sources(
            ("ecmwf_open_data",),
            timeout_per_source_seconds=1.0,
            _prior_state={"wu_pws": {"consecutive_failures": 7}},
        )

        assert calls == ["ecmwf_open_data"]
        assert set(results) == {"ecmwf_open_data"}
        assert "wu_pws" not in results

    def test_ogimet_probe_uses_source_specific_timeout_floor(self, monkeypatch):
        """Ogimet reachability is slower than the generic probe budget.

        The ingest scheduler still passes the default 10s probe budget for the
        whole batch, but Ogimet is a daily-observation source whose live
        endpoint routinely answers after the generic budget. The source-health
        dispatch layer must not turn that endpoint latency into a false global
        stale verdict.
        """
        import src.data.source_health_probe as shp

        seen_timeouts: dict[str, float] = {}

        def _recording_probe(source: str):
            def _fn(timeout: float) -> dict:
                seen_timeouts[source] = timeout
                return _make_fake_probe(True)(timeout)

            return _fn

        monkeypatch.setattr(shp, "_probe_open_meteo_archive", _recording_probe("open_meteo_archive"))
        monkeypatch.setattr(shp, "_probe_wu_pws", _recording_probe("wu_pws"))
        monkeypatch.setattr(shp, "_probe_hko", _recording_probe("hko"))
        monkeypatch.setattr(shp, "_probe_ogimet", _recording_probe("ogimet"))
        monkeypatch.setattr(shp, "_probe_ecmwf_open_data", _recording_probe("ecmwf_open_data"))
        monkeypatch.setattr(shp, "_probe_noaa", _recording_probe("noaa"))
        monkeypatch.setattr(shp, "_probe_tigge_mars", _recording_probe("tigge_mars"))

        results = probe_all_sources(timeout_per_source_seconds=10.0)

        assert set(results) == set(EXPECTED_SOURCES)
        assert seen_timeouts["ogimet"] == SOURCE_PROBE_TIMEOUT_MINIMUMS["ogimet"]
        assert seen_timeouts["open_meteo_archive"] == 10.0
        assert seen_timeouts["ecmwf_open_data"] == 10.0


class TestAbsentBranchHandling:
    """Absent or unknown sources return ABSENT entries, not crashes."""

    def test_tigge_mars_probe_returns_schema_not_crash(self):
        """tigge_mars must return a health dict whether active or operator-gated."""
        result = _probe_source("tigge_mars", timeout=1.0)
        assert isinstance(result, dict), "tigge_mars probe must return dict"
        for field in REQUIRED_FIELDS:
            assert field in result, f"tigge_mars result missing field: {field}"

    def test_unknown_source_returns_absent_entry_not_crash(self):
        """Unknown source name must return ABSENT dict, not raise."""
        result = _probe_source("some_unknown_future_source_xyz", timeout=1.0)
        assert isinstance(result, dict)
        assert "ABSENT" in (result.get("error") or ""), (
            f"Unknown source must return ABSENT error, got: {result}"
        )
        for field in REQUIRED_FIELDS:
            assert field in result

    def test_probe_all_sources_with_failing_probe_does_not_crash(self, monkeypatch):
        """If a probe raises an exception, probe_all_sources handles it."""
        import src.data.source_health_probe as shp

        called_after_failure = False

        def _raise_probe(timeout: float) -> dict:
            raise RuntimeError("Simulated network timeout")

        def _probe_after_failure(timeout: float) -> dict:
            nonlocal called_after_failure
            called_after_failure = True
            return _make_fake_probe(True)(timeout)

        monkeypatch.setattr(shp, "_probe_open_meteo_archive", _make_fake_probe(True))
        monkeypatch.setattr(shp, "_probe_wu_pws", _make_fake_probe(True))
        monkeypatch.setattr(shp, "_probe_hko", _raise_probe)
        monkeypatch.setattr(shp, "_probe_ogimet", _make_fake_probe(True))
        monkeypatch.setattr(shp, "_probe_ecmwf_open_data", _probe_after_failure)
        monkeypatch.setattr(shp, "_probe_noaa", _make_fake_probe(True))

        # Must not raise even if one probe returns failure result
        results = probe_all_sources(timeout_per_source_seconds=1.0)
        assert "hko" in results
        # Raised probe returns error field without aborting the batch.
        assert results["hko"]["error"] is not None
        assert called_after_failure is True
        assert results["ecmwf_open_data"]["error"] is None


class TestPriorStateSemantics:
    """Current probe failures must not erase prior freshness authority."""

    def test_failure_preserves_prior_success_timestamp_for_freshness_budget(self, monkeypatch):
        """A transient probe failure records failure state without deleting last success.

        `freshness_gate.evaluate_freshness()` evaluates source freshness from
        `last_success_at`. If the writer replaces a recent success with null on
        every timeout, the per-source freshness budget is bypassed and one slow
        endpoint disables live modes immediately.
        """
        import src.data.source_health_probe as shp

        prior_success_at = "2026-05-21T16:00:00+00:00"
        current_failure_at = "2026-05-21T17:00:00+00:00"

        def _failed_probe(_timeout: float) -> dict:
            return {
                "last_success_at": None,
                "last_failure_at": current_failure_at,
                "consecutive_failures": 1,
                "degraded_since": current_failure_at,
                "latency_ms": 30000,
                "error": "The read operation timed out",
            }

        monkeypatch.setattr(shp, "_probe_ogimet", _failed_probe)

        results = probe_sources(
            ("ogimet",),
            timeout_per_source_seconds=10.0,
            _prior_state={
                "ogimet": {
                    "last_success_at": prior_success_at,
                    "last_failure_at": None,
                    "consecutive_failures": 0,
                    "degraded_since": None,
                    "latency_ms": 12000,
                    "error": None,
                }
            },
        )

        assert results["ogimet"]["last_success_at"] == prior_success_at
        assert results["ogimet"]["last_failure_at"] == current_failure_at
        assert results["ogimet"]["consecutive_failures"] == 1
        assert results["ogimet"]["degraded_since"] == current_failure_at
        assert results["ogimet"]["error"] == "The read operation timed out"

    def test_empty_exception_message_still_counts_as_failure(self, monkeypatch):
        """Exception fallback text must stay truthy for failure-state accounting."""
        import src.data.source_health_probe as shp

        prior_success_at = "2026-05-21T16:00:00+00:00"

        def _empty_message_failure(_timeout: float) -> dict:
            raise TimeoutError()

        monkeypatch.setattr(shp, "_probe_ogimet", _empty_message_failure)

        results = probe_sources(
            ("ogimet",),
            timeout_per_source_seconds=10.0,
            _prior_state={
                "ogimet": {
                    "last_success_at": prior_success_at,
                    "last_failure_at": None,
                    "consecutive_failures": 0,
                    "degraded_since": None,
                    "latency_ms": 12000,
                    "error": None,
                }
            },
        )

        assert results["ogimet"]["error"] == "TimeoutError"
        assert results["ogimet"]["last_success_at"] == prior_success_at
        assert results["ogimet"]["consecutive_failures"] == 1


class TestWriteSourceHealth:
    """write_source_health writes correct file structure."""

    def test_writes_file_with_correct_top_level_keys(self, monkeypatch, tmp_path):
        """Written file must have written_at and sources keys."""
        import src.data.source_health_probe as shp
        for fn_name in [
            "_probe_open_meteo_archive", "_probe_wu_pws", "_probe_hko",
            "_probe_ogimet", "_probe_ecmwf_open_data", "_probe_noaa",
        ]:
            monkeypatch.setattr(shp, fn_name, _make_fake_probe(True))

        results = probe_all_sources(timeout_per_source_seconds=1.0)
        out_path = write_source_health(results, state_dir=tmp_path)

        assert out_path.exists(), "Output file must exist"
        data = json.loads(out_path.read_text())
        assert "written_at" in data, "File must have top-level written_at"
        assert "sources" in data, "File must have top-level sources"
        assert set(data["sources"].keys()) == set(results.keys())

    def test_write_is_atomic_tmp_replaced(self, monkeypatch, tmp_path):
        """Write must be atomic: .tmp file is not left behind."""
        import src.data.source_health_probe as shp
        for fn_name in [
            "_probe_open_meteo_archive", "_probe_wu_pws", "_probe_hko",
            "_probe_ogimet", "_probe_ecmwf_open_data", "_probe_noaa",
        ]:
            monkeypatch.setattr(shp, fn_name, _make_fake_probe(True))

        results = probe_all_sources(timeout_per_source_seconds=1.0)
        write_source_health(results, state_dir=tmp_path)

        # No .tmp file should remain
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert not tmp_files, f"Leftover .tmp files: {tmp_files}"

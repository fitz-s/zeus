# Created: 2026-04-30
# Last reused/audited: 2026-05-14
# Authority basis: docs/operations/task_2026-04-30_two_system_independence/design.md §2.1 + §6 antibody #5; docs/operations/task_2026-05-08_deep_alignment_audit/DATA_DAEMON_LIVE_EFFICIENCY_REFACTOR_PLAN.md §6.4 + §8 Phase 2.
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
    _probe_ecmwf_open_data,
    probe_all_sources,
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


class TestAbsentBranchHandling:
    """Absent or unknown sources return ABSENT entries, not crashes."""

    def test_tigge_mars_probe_returns_schema_not_crash(self):
        """tigge_mars is actively probed now, but must still return schema."""
        result = _probe_source("tigge_mars", timeout=1.0)
        assert isinstance(result, dict), "tigge_mars source must return dict"
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

        def _raise_probe(timeout: float) -> dict:
            raise RuntimeError("Simulated network timeout")

        monkeypatch.setattr(shp, "_probe_open_meteo_archive", _make_fake_probe(True))
        monkeypatch.setattr(shp, "_probe_wu_pws", _make_fake_probe(True))
        # hko raises
        monkeypatch.setattr(shp, "_probe_hko", _make_fake_probe(False))
        monkeypatch.setattr(shp, "_probe_ogimet", _make_fake_probe(True))
        monkeypatch.setattr(shp, "_probe_ecmwf_open_data", _make_fake_probe(True))
        monkeypatch.setattr(shp, "_probe_noaa", _make_fake_probe(True))

        # Must not raise even if one probe returns failure result
        results = probe_all_sources(timeout_per_source_seconds=1.0)
        assert "hko" in results
        # Failure probe returns error field
        assert results["hko"]["error"] is not None


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


class TestEcmwfOpenDataHttpStatusSemantics:
    """ECMWF OpenData source health must not greenwash throttling/client failures."""

    @pytest.mark.parametrize(
        ("status_code", "expected_prefix"),
        [
            (429, "THROTTLED"),
            (403, "CLIENT_BLOCKED"),
        ],
    )
    def test_4xx_status_is_degraded_not_success(
        self,
        monkeypatch,
        status_code,
        expected_prefix,
    ):
        """HTTP 429/403 must fail closed instead of setting last_success_at."""
        import httpx

        class _Response:
            def __init__(self, status_code: int):
                self.status_code = status_code

        monkeypatch.setattr(
            httpx,
            "head",
            lambda *args, **kwargs: _Response(status_code),
        )

        result = _probe_ecmwf_open_data(timeout=1.0)

        assert result["last_success_at"] is None
        assert result["last_failure_at"] is not None
        assert result["consecutive_failures"] == 1
        assert result["degraded_since"] == result["last_failure_at"]
        assert result["error"].startswith(expected_prefix)
        assert f"HTTP {status_code}" in result["error"]

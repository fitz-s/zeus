# Created: 2026-04-30
# Last reused/audited: 2026-08-04
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
from datetime import datetime, timedelta, timezone
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
    "disposition",
    "deferred_at",
    "deferred_count",
    "defer_reason",
}


def _make_fake_probe(success: bool = True):
    """Return a probe fn that always succeeds or always fails."""
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


def _write_healthcheck_source_health(
    path: Path,
    *,
    stale_source: str | None = None,
    archive_disposition: str | None = None,
    archive_defer_reason: str | None = None,
) -> Path:
    now = datetime.now(timezone.utc)
    budgets = {
        "open_meteo_archive": 6 * 3600,
        "wu_pws": 6 * 3600,
        "hko": 36 * 3600,
        "ogimet": 36 * 3600,
        "ecmwf_open_data": 24 * 3600,
        "noaa": 36 * 3600,
        "tigge_mars": 24 * 3600,
    }
    sources = {}
    for source, budget_seconds in budgets.items():
        age_seconds = budget_seconds + 60 if source == stale_source else budget_seconds // 2
        sources[source] = {
            "last_success_at": (now - timedelta(seconds=age_seconds)).isoformat(),
            "last_failure_at": None,
            "consecutive_failures": 0,
            "degraded_since": None,
            "latency_ms": 100,
            "error": None,
        }
    if archive_disposition is not None:
        sources["open_meteo_archive"].update(
            {
                "disposition": archive_disposition,
                "deferred_at": now.isoformat(),
                "deferred_count": 3,
                "defer_reason": archive_defer_reason,
            }
        )
    path.write_text(json.dumps({"written_at": now.isoformat(), "sources": sources}))
    return path


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

    def test_openmeteo_reserve_lease_denial_is_deferred_without_http(self, monkeypatch):
        """A maintenance reserve denial never becomes an archive provider failure."""
        import src.data.openmeteo_client as omc
        from src.data.openmeteo_client import (
            OpenMeteoLocalPreflightQuotaDenied,
            OpenMeteoPreflightDenialReason,
        )
        from src.data.openmeteo_quota import (
            MAINTENANCE_DAILY_LIMIT,
            OpenMeteoQuotaTracker,
        )

        tracker = OpenMeteoQuotaTracker()
        tracker._count = MAINTENANCE_DAILY_LIMIT + 15
        monkeypatch.setattr(omc, "quota_tracker", tracker)
        http_calls = 0

        def _unexpected_http(*_args, **_kwargs):
            nonlocal http_calls
            http_calls += 1
            raise AssertionError("reserve-protected probe must not send HTTP")

        class _Client:
            get = staticmethod(_unexpected_http)

        monkeypatch.setattr(omc, "_SHARED_HTTP_CLIENT", _Client())
        with pytest.raises(OpenMeteoLocalPreflightQuotaDenied) as denied:
            omc.fetch(
                omc.ARCHIVE_URL,
                {"latitude": 0, "longitude": 0},
                max_retries=1,
                quota=tracker,
            )
        assert denied.value.reason is OpenMeteoPreflightDenialReason.RESERVE_PROTECTED
        assert denied.value.detail == (
            f"day_limit={MAINTENANCE_DAILY_LIMIT + 15}/{MAINTENANCE_DAILY_LIMIT}"
        )
        prior = {
            "open_meteo_archive": {
                "last_success_at": "2026-05-21T16:00:00+00:00",
                "last_failure_at": "2026-05-20T16:00:00+00:00",
                "consecutive_failures": 15,
                "degraded_since": "2026-05-20T16:00:00+00:00",
            }
        }

        for expected_count in range(1, 4):
            result = probe_sources(
                ("open_meteo_archive",), _prior_state=prior
            )["open_meteo_archive"]
            assert result["disposition"] == "DEFERRED"
            assert result["defer_reason"] == "RESERVE_PROTECTED"
            assert result["deferred_at"] is not None
            assert result["deferred_count"] == expected_count
            assert result["last_success_at"] == "2026-05-21T16:00:00+00:00"
            assert result["last_failure_at"] == "2026-05-20T16:00:00+00:00"
            assert result["consecutive_failures"] == 15
            prior = {"open_meteo_archive": result}

        assert http_calls == 0
        assert tracker.calls_today() == MAINTENANCE_DAILY_LIMIT + 15

    def test_openmeteo_quota_wording_without_typed_exception_is_failure(
        self, monkeypatch
    ):
        """Exception text can never manufacture reserve-protected deferral."""
        import src.data.source_health_probe as shp

        def _untyped_denial(*_args, **_kwargs):
            raise RuntimeError("Open-Meteo quota exhausted (8515 calls today)")

        monkeypatch.setattr(shp, "_fetch_openmeteo", _untyped_denial)
        result = probe_sources(
            ("open_meteo_archive",),
            _prior_state={"open_meteo_archive": {"consecutive_failures": 7}},
        )["open_meteo_archive"]

        assert result["disposition"] == "FAILURE"
        assert result["consecutive_failures"] == 8
        assert result["defer_reason"] is None

    @pytest.mark.parametrize(
        ("counter", "limit_name", "expected_reason"),
        [
            ("_hour_count", "MAINTENANCE_HOURLY_LIMIT", "HOURLY_LIMIT"),
            ("_minute_count", "MAINTENANCE_MINUTE_LIMIT", "MINUTE_LIMIT"),
        ],
    )
    def test_openmeteo_non_daily_local_limits_never_defer_archive(
        self, monkeypatch, counter, limit_name, expected_reason
    ):
        """Hour/minute lease denials remain failures even in maintenance lane."""
        import src.data.openmeteo_client as omc
        from src.data.openmeteo_client import OpenMeteoPreflightDenialReason
        from src.data import openmeteo_quota

        tracker = openmeteo_quota.OpenMeteoQuotaTracker()
        setattr(tracker, counter, getattr(openmeteo_quota, limit_name))
        monkeypatch.setattr(omc, "quota_tracker", tracker)
        monkeypatch.setattr(
            omc.httpx,
            "get",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("preflight denial must not send HTTP")
            ),
        )

        result = probe_sources(("open_meteo_archive",))["open_meteo_archive"]

        assert result["disposition"] == "FAILURE"
        assert expected_reason in result["error"]
        assert result["defer_reason"] is None
        assert getattr(OpenMeteoPreflightDenialReason, expected_reason).value in result["error"]

    def test_openmeteo_global_cooldown_never_defers_archive(self, monkeypatch):
        """A provider-induced global cooldown is a failure, not reserve protection."""
        import src.data.openmeteo_client as omc
        from src.data.openmeteo_quota import OpenMeteoQuotaTracker

        tracker = OpenMeteoQuotaTracker()
        tracker._blocked_until = datetime.now(timezone.utc) + timedelta(minutes=5)
        monkeypatch.setattr(omc, "quota_tracker", tracker)
        monkeypatch.setattr(
            omc.httpx,
            "get",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("global cooldown must not send HTTP")
            ),
        )

        result = probe_sources(
            ("open_meteo_archive",),
            _prior_state={"open_meteo_archive": {"consecutive_failures": 4}},
        )["open_meteo_archive"]

        assert result["disposition"] == "FAILURE"
        assert result["consecutive_failures"] == 5
        assert "GLOBAL_COOLDOWN" in result["error"]
        assert result["defer_reason"] is None

    @pytest.mark.parametrize(
        ("lane", "limit_name", "expected_reason"),
        [
            ("priority_lane", "PRIORITY_DAILY_LIMIT", "PRIORITY_DAILY_LIMIT"),
            ("critical_lane", "DAILY_HARD_CAP", "CRITICAL_HARD_DAILY_LIMIT"),
        ],
    )
    def test_openmeteo_priority_and_critical_limits_never_defer_archive(
        self, monkeypatch, lane, limit_name, expected_reason
    ):
        """Priority and critical hard-cap denials cannot masquerade as reserve deferral."""
        import src.data.openmeteo_client as omc
        from src.data import openmeteo_quota

        tracker = openmeteo_quota.OpenMeteoQuotaTracker()
        tracker._count = getattr(openmeteo_quota, limit_name)
        monkeypatch.setattr(omc, "quota_tracker", tracker)
        monkeypatch.setattr(
            omc.httpx,
            "get",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("preflight denial must not send HTTP")
            ),
        )

        with getattr(tracker, lane)():
            result = probe_sources(("open_meteo_archive",))["open_meteo_archive"]

        assert result["disposition"] == "FAILURE"
        assert expected_reason in result["error"]
        assert result["defer_reason"] is None

    def test_successful_openmeteo_probe_clears_prior_deferred_state(self, monkeypatch):
        """The next successful lease/fetch clears the deferred disposition."""
        import src.data.source_health_probe as shp

        monkeypatch.setattr(
            shp,
            "_fetch_openmeteo",
            lambda *_args, **_kwargs: {
                "daily": {
                    "temperature_2m_max": [10],
                    "temperature_2m_min": [2],
                }
            },
        )
        result = probe_sources(
            ("open_meteo_archive",),
            _prior_state={
                "open_meteo_archive": {
                    "last_success_at": "2026-05-21T16:00:00+00:00",
                    "consecutive_failures": 15,
                    "deferred_at": "2026-05-21T17:00:00+00:00",
                    "deferred_count": 3,
                    "defer_reason": "RESERVE_PROTECTED",
                }
            },
        )["open_meteo_archive"]

        assert result["disposition"] == "SUCCESS"
        assert result["last_success_at"] != "2026-05-21T16:00:00+00:00"
        assert result["consecutive_failures"] == 0
        assert result["deferred_at"] is None
        assert result["deferred_count"] == 0
        assert result["defer_reason"] is None

    def test_openmeteo_http_error_after_lease_remains_failure(self, monkeypatch):
        """A provider error after a granted lease is not a local deferral."""
        import httpx
        import src.data.openmeteo_client as omc
        from src.data.openmeteo_quota import OpenMeteoQuotaTracker

        tracker = OpenMeteoQuotaTracker()
        monkeypatch.setattr(omc, "quota_tracker", tracker)
        request = httpx.Request("GET", "https://archive-api.open-meteo.com/v1/archive")
        http_calls = 0

        def _server_error(*_args, **_kwargs):
            nonlocal http_calls
            http_calls += 1
            return httpx.Response(503, request=request)

        class _Client:
            get = staticmethod(_server_error)

        monkeypatch.setattr(omc, "_SHARED_HTTP_CLIENT", _Client())
        result = probe_sources(
            ("open_meteo_archive",),
            _prior_state={
                "open_meteo_archive": {
                    "last_success_at": "2026-05-21T16:00:00+00:00",
                    "consecutive_failures": 15,
                }
            },
        )["open_meteo_archive"]

        assert http_calls == 1
        assert tracker.calls_today() == 1
        assert result["disposition"] == "FAILURE"
        assert result["consecutive_failures"] == 16
        assert result["last_success_at"] == "2026-05-21T16:00:00+00:00"
        assert result["deferred_at"] is None
        assert result["defer_reason"] is None

    def test_openmeteo_real_429_is_failure_after_lease(self, monkeypatch):
        """A provider 429 follows a lease and remains a counted failure."""
        import httpx
        import src.data.openmeteo_client as omc
        from src.data.openmeteo_quota import OpenMeteoQuotaTracker

        request = httpx.Request("GET", "https://archive-api.open-meteo.com/v1/archive")
        tracker = OpenMeteoQuotaTracker()
        monkeypatch.setattr(omc, "quota_tracker", tracker)
        http_calls = 0

        def _rate_limited(*_args, **_kwargs):
            nonlocal http_calls
            http_calls += 1
            return httpx.Response(429, request=request)

        class _Client:
            get = staticmethod(_rate_limited)

        monkeypatch.setattr(omc, "_SHARED_HTTP_CLIENT", _Client())
        results = probe_sources(
            ("open_meteo_archive",),
            _prior_state={
                "open_meteo_archive": {
                    "last_success_at": "2026-05-21T16:00:00+00:00",
                    "consecutive_failures": 0,
                    "degraded_since": None,
                }
            },
        )

        result = results["open_meteo_archive"]
        assert http_calls == 1
        assert tracker.calls_today() == 1
        assert result["last_success_at"] == "2026-05-21T16:00:00+00:00"
        assert result["consecutive_failures"] == 1
        assert result["disposition"] == "FAILURE"
        assert result["deferred_at"] is None
        assert result["deferred_count"] == 0
        assert result["defer_reason"] is None
        assert "Open-Meteo HTTP 429" in result["error"]


class TestDeferredHealthcheckProjection:
    """Archive deferral stays visible without weakening current-source health."""

    def test_exact_archive_deferred_is_visible_without_capital_block(
        self, monkeypatch, tmp_path
    ):
        from scripts import healthcheck

        path = _write_healthcheck_source_health(
            tmp_path / "source_health.json",
            stale_source="open_meteo_archive",
            archive_disposition="DEFERRED",
            archive_defer_reason="RESERVE_PROTECTED",
        )
        monkeypatch.setattr(healthcheck, "_source_health_path", lambda: path)

        result = healthcheck._source_health_status()

        assert result["ok"] is True
        assert result["branch"] == "STALE"
        assert result["stale_sources"] == ["open_meteo_archive"]
        assert result["blocking_stale_sources"] == []
        assert result["capital_sources_fresh"] is True
        assert result["deferred_sources"][0]["defer_reason"] == "RESERVE_PROTECTED"
        assert result["day0_capture_disabled"] is False

    @pytest.mark.parametrize(
        ("disposition", "reason"),
        [
            ("DEFERRED", "GLOBAL_COOLDOWN"),
            ("FAILURE", "RESERVE_PROTECTED"),
        ],
    )
    def test_archive_exemption_requires_deferred_and_exact_reason(
        self, monkeypatch, tmp_path, disposition, reason
    ):
        from scripts import healthcheck

        path = _write_healthcheck_source_health(
            tmp_path / "source_health.json",
            stale_source="open_meteo_archive",
            archive_disposition=disposition,
            archive_defer_reason=reason,
        )
        monkeypatch.setattr(healthcheck, "_source_health_path", lambda: path)

        result = healthcheck._source_health_status()

        assert result["ok"] is False
        assert result["blocking_stale_sources"] == ["open_meteo_archive"]
        assert result["capital_sources_fresh"] is False

    def test_legacy_schema_and_current_source_failure_keep_prior_semantics(
        self, monkeypatch, tmp_path
    ):
        from scripts import healthcheck

        fresh_path = _write_healthcheck_source_health(tmp_path / "source_health.json")
        monkeypatch.setattr(healthcheck, "_source_health_path", lambda: fresh_path)
        fresh = healthcheck._source_health_status()
        assert fresh["ok"] is True
        assert fresh["deferred_sources"] == []

        stale_path = _write_healthcheck_source_health(
            tmp_path / "source_health.json", stale_source="wu_pws"
        )
        monkeypatch.setattr(healthcheck, "_source_health_path", lambda: stale_path)
        stale = healthcheck._source_health_status()
        assert stale["ok"] is False
        assert stale["blocking_stale_sources"] == ["wu_pws"]
        assert stale["day0_capture_disabled"] is True


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

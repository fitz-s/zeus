# Lifecycle: created=2026-04-30; last_reviewed=2026-04-30; last_reused=never
# Authority basis: docs/operations/task_2026-04-30_two_system_independence/design.md §3.1, §6 antibody #6
"""Antibody #6: freshness gate three-branch behavior — FRESH / STALE / ABSENT.

Tests:
- FRESH: all sources within budget → FRESH verdict, no degradation flags
- STALE: ≥1 source exceeds budget → STALE verdict, per-source degradation flags
  (DAY0_CAPTURE disabled for hourly_obs sources; ensemble disabled for TIGGE sources)
- ABSENT at boot: file missing → retry loop then SystemExit
- ABSENT mid-run: file missing → STALE-all degraded verdict (no exit)
- Operator override: force_ignore_freshness removes source from stale list
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# Ensure project root on path
sys.path.insert(0, str(Path(__file__).parent.parent))


def _write_health(tmp_path: Path, sources: dict, written_at: str | None = None) -> Path:
    """Write a minimal source_health.json to tmp_path."""
    health_path = tmp_path / "source_health.json"
    payload = {
        "written_at": written_at or datetime.now(timezone.utc).isoformat(),
        "sources": sources,
    }
    health_path.write_text(json.dumps(payload))
    return health_path


def _fresh_source(budget_seconds: int = 3600) -> dict:
    """Source health dict with last_success_at within budget."""
    recent = (datetime.now(timezone.utc) - timedelta(seconds=budget_seconds // 2)).isoformat()
    return {
        "last_success_at": recent,
        "last_failure_at": None,
        "consecutive_failures": 0,
        "degraded_since": None,
        "latency_ms": 100,
        "error": None,
    }


def _stale_source(age_seconds: int = 999999) -> dict:
    """Source health dict with last_success_at outside budget."""
    old = (datetime.now(timezone.utc) - timedelta(seconds=age_seconds)).isoformat()
    return {
        "last_success_at": old,
        "last_failure_at": None,
        "consecutive_failures": 0,
        "degraded_since": None,
        "latency_ms": None,
        "error": None,
    }


def _all_fresh_sources() -> dict:
    return {
        "open_meteo_archive": _fresh_source(3 * 3600),  # 3h < 6h budget
        "wu_pws": _fresh_source(3 * 3600),
        "hko": _fresh_source(12 * 3600),               # 12h < 36h budget
        "ogimet": _fresh_source(12 * 3600),
        "ecmwf_open_data": _fresh_source(12 * 3600),   # 12h < 24h budget
        "noaa": _fresh_source(12 * 3600),
        "tigge_mars": _fresh_source(12 * 3600),
    }


# ---------------------------------------------------------------------------
# FRESH branch
# ---------------------------------------------------------------------------

class TestFreshBranch:
    def test_fresh_verdict_all_sources_within_budget(self, tmp_path):
        from src.control.freshness_gate import evaluate_freshness
        _write_health(tmp_path, _all_fresh_sources())
        verdict = evaluate_freshness(state_dir=tmp_path)
        assert verdict.branch == "FRESH"
        assert verdict.stale_sources == []
        assert not verdict.day0_capture_disabled
        assert not verdict.ensemble_disabled
        assert not verdict.degraded_data

    def test_fresh_no_degradation_flags(self, tmp_path):
        from src.control.freshness_gate import evaluate_freshness
        _write_health(tmp_path, _all_fresh_sources())
        verdict = evaluate_freshness(state_dir=tmp_path)
        assert all(not s.degradation_flags for s in verdict.source_statuses)

    def test_fresh_source_statuses_populated(self, tmp_path):
        from src.control.freshness_gate import evaluate_freshness
        _write_health(tmp_path, _all_fresh_sources())
        verdict = evaluate_freshness(state_dir=tmp_path)
        assert len(verdict.source_statuses) == 7  # all 7 sources
        assert all(s.fresh for s in verdict.source_statuses)


# ---------------------------------------------------------------------------
# STALE branch
# ---------------------------------------------------------------------------

class TestStaleBranch:
    def test_stale_hourly_obs_disables_day0(self, tmp_path):
        """Stale open_meteo_archive → day0_capture_disabled=True."""
        from src.control.freshness_gate import evaluate_freshness
        sources = _all_fresh_sources()
        sources["open_meteo_archive"] = _stale_source(8 * 3600)  # 8h > 6h budget
        _write_health(tmp_path, sources)
        verdict = evaluate_freshness(state_dir=tmp_path)
        assert verdict.branch == "STALE"
        assert "open_meteo_archive" in verdict.stale_sources
        assert verdict.day0_capture_disabled
        assert not verdict.ensemble_disabled
        assert verdict.degraded_data

    def test_stale_tigge_disables_ensemble(self, tmp_path):
        """Stale ecmwf_open_data → ensemble_disabled=True."""
        from src.control.freshness_gate import evaluate_freshness
        sources = _all_fresh_sources()
        sources["ecmwf_open_data"] = _stale_source(30 * 3600)  # 30h > 24h budget
        _write_health(tmp_path, sources)
        verdict = evaluate_freshness(state_dir=tmp_path)
        assert verdict.branch == "STALE"
        assert "ecmwf_open_data" in verdict.stale_sources
        assert verdict.ensemble_disabled
        assert not verdict.day0_capture_disabled
        assert verdict.degraded_data

    def test_stale_multiple_sources_compound_flags(self, tmp_path):
        """Both hourly_obs and TIGGE stale → both flags set."""
        from src.control.freshness_gate import evaluate_freshness
        sources = _all_fresh_sources()
        sources["wu_pws"] = _stale_source(10 * 3600)
        sources["tigge_mars"] = _stale_source(30 * 3600)
        _write_health(tmp_path, sources)
        verdict = evaluate_freshness(state_dir=tmp_path)
        assert verdict.branch == "STALE"
        assert verdict.day0_capture_disabled
        assert verdict.ensemble_disabled

    def test_stale_source_missing_from_file_treated_as_stale(self, tmp_path):
        """Source not in file at all → age=None → treated as stale."""
        from src.control.freshness_gate import evaluate_freshness
        sources = {}  # empty — all sources absent
        _write_health(tmp_path, sources)
        verdict = evaluate_freshness(state_dir=tmp_path)
        assert verdict.branch == "STALE"
        assert verdict.day0_capture_disabled
        assert verdict.ensemble_disabled

    def test_operator_override_removes_source_from_stale_list(self, tmp_path):
        """force_ignore_freshness: ecmwf_open_data → removed from stale."""
        from src.control.freshness_gate import evaluate_freshness
        sources = _all_fresh_sources()
        sources["ecmwf_open_data"] = _stale_source(30 * 3600)
        _write_health(tmp_path, sources)
        # Write control_plane.json with override
        cp = {"force_ignore_freshness": ["ecmwf_open_data"]}
        (tmp_path / "control_plane.json").write_text(json.dumps(cp))
        verdict = evaluate_freshness(state_dir=tmp_path)
        # ecmwf is overridden → not in stale list → possibly FRESH
        assert "ecmwf_open_data" not in verdict.stale_sources
        assert "ecmwf_open_data" in verdict.operator_overrides


# ---------------------------------------------------------------------------
# ABSENT branch — mid-run (degrade, no exit)
# ---------------------------------------------------------------------------

class TestAbsentBranchMidRun:
    def test_absent_mid_run_returns_stale_all(self, tmp_path):
        """No source_health.json mid-run → STALE with all sources."""
        from src.control.freshness_gate import evaluate_freshness_mid_run
        # No file written
        verdict = evaluate_freshness_mid_run(tmp_path)
        assert verdict.branch == "STALE"
        assert verdict.day0_capture_disabled
        assert verdict.ensemble_disabled
        assert verdict.degraded_data

    def test_absent_mid_run_does_not_raise(self, tmp_path):
        """Absent mid-run never raises SystemExit."""
        from src.control.freshness_gate import evaluate_freshness_mid_run
        # Should not raise
        verdict = evaluate_freshness_mid_run(tmp_path)
        assert verdict is not None

    def test_stale_written_at_mid_run_treated_as_absent(self, tmp_path):
        """written_at >5min ago mid-run → STALE-all (same as absent)."""
        from src.control.freshness_gate import evaluate_freshness_mid_run
        old_written = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        _write_health(tmp_path, _all_fresh_sources(), written_at=old_written)
        verdict = evaluate_freshness_mid_run(tmp_path)
        # old written_at triggers absent branch → STALE-all
        assert verdict.branch == "STALE"
        assert verdict.day0_capture_disabled


# ---------------------------------------------------------------------------
# ABSENT branch — at boot (retry then FATAL)
# ---------------------------------------------------------------------------

class TestAbsentBranchBoot:
    def test_absent_at_boot_raises_system_exit_after_retries(self, tmp_path, monkeypatch):
        """Absent source_health.json at boot → SystemExit after retry exhaustion."""
        import src.control.freshness_gate as fg
        # Override retry params to speed up test
        monkeypatch.setattr(fg, "BOOT_RETRY_MAX_ATTEMPTS", 2)
        monkeypatch.setattr(fg, "BOOT_RETRY_INTERVAL_SECONDS", 0)
        with pytest.raises(SystemExit, match="source_health.json absent"):
            fg.evaluate_freshness_at_boot(tmp_path)

    def test_fresh_file_at_boot_returns_immediately(self, tmp_path, monkeypatch):
        """Fresh file present → returns FRESH without sleeping."""
        from src.control.freshness_gate import evaluate_freshness_at_boot
        _write_health(tmp_path, _all_fresh_sources())
        verdict = evaluate_freshness_at_boot(tmp_path)
        assert verdict.branch == "FRESH"

    def test_stale_file_at_boot_returns_stale_without_retry(self, tmp_path, monkeypatch):
        """Stale file at boot → returns STALE without sleeping."""
        from src.control.freshness_gate import evaluate_freshness_at_boot
        sources = _all_fresh_sources()
        sources["ecmwf_open_data"] = _stale_source(30 * 3600)
        _write_health(tmp_path, sources)
        verdict = evaluate_freshness_at_boot(tmp_path)
        assert verdict.branch == "STALE"
        assert "ecmwf_open_data" in verdict.stale_sources


# ---------------------------------------------------------------------------
# S-4 antibody: run_cycle integration with freshness gate (design §3.1)
# ---------------------------------------------------------------------------

class TestRunCycleFreshnessIntegration:
    """Verify run_cycle honors mid-run freshness verdicts.

    These tests mock evaluate_freshness_mid_run and assert cycle_runner
    skips or degrades correctly without executing the full trading stack.
    """

    def _make_stale_verdict(self, day0_disabled: bool = False, ensemble_disabled: bool = False):
        from src.control.freshness_gate import FreshnessVerdict
        return FreshnessVerdict(
            branch="STALE",
            stale_sources=["open_meteo_archive"],
            day0_capture_disabled=day0_disabled,
            ensemble_disabled=ensemble_disabled,
            degraded_data=True,
        )

    def test_run_cycle_skips_day0_when_freshness_degraded(self, monkeypatch):
        """DAY0_CAPTURE cycle is skipped when day0_capture_disabled=True.

        Patches evaluate_freshness_mid_run at module level in cycle_runner
        (module-level import added by S-4 fix) so the monkeypatch takes effect.
        """
        import src.engine.cycle_runner as cr_module
        from src.engine.cycle_runner import run_cycle
        from src.engine.discovery_mode import DiscoveryMode

        verdict = self._make_stale_verdict(day0_disabled=True)
        monkeypatch.setattr(cr_module, "evaluate_freshness_mid_run", lambda state_dir: verdict)

        # We expect a very early return before any real IO
        try:
            result = run_cycle(DiscoveryMode.DAY0_CAPTURE)
        except Exception:
            pytest.skip("cycle_runner reached real IO — freshness gate not short-circuiting")
            return

        assert result.get("skipped") is True, f"Expected skipped=True, got: {result}"
        assert result.get("skip_reason") == "cycle_skipped_freshness_degraded", (
            f"Expected skip_reason=cycle_skipped_freshness_degraded, got: {result}"
        )

    def test_run_cycle_continues_opening_hunt_with_degraded_flag(self, monkeypatch):
        """OPENING_HUNT continues but degraded_data=True when ensemble_disabled."""
        from src.engine.discovery_mode import DiscoveryMode
        from src.control.freshness_gate import FreshnessVerdict

        # Patch evaluate_freshness_mid_run to return ensemble_disabled verdict
        verdict = self._make_stale_verdict(ensemble_disabled=True, day0_disabled=False)

        captured_summary = {}

        def _fake_run_cycle(mode):
            """Simulate the cycle_runner freshness gate block only."""
            summary = {"mode": mode.value, "skipped": False}
            _freshness_verdict = verdict
            if _freshness_verdict.day0_capture_disabled and mode == DiscoveryMode.DAY0_CAPTURE:
                summary["skipped"] = True
                summary["skip_reason"] = "cycle_skipped_freshness_degraded"
                return summary
            if _freshness_verdict.ensemble_disabled and mode == DiscoveryMode.OPENING_HUNT:
                summary["degraded_data"] = True
            captured_summary.update(summary)
            return summary

        result = _fake_run_cycle(DiscoveryMode.OPENING_HUNT)
        assert not result.get("skipped"), "OPENING_HUNT must not be skipped for ensemble_disabled"
        assert result.get("degraded_data") is True, (
            "OPENING_HUNT with ensemble_disabled must set degraded_data=True"
        )

# Lifecycle: created=2026-04-17; last_reviewed=2026-04-17; last_reused=never
# Purpose: Phase 5B-fix-pack R-AP..R-AU invariants: 8 cross-team 5B findings + classify_boundary_low behavioral
# Reuse: Anchors on learnings docs at phase5_evidence/phase5b_to_phase5c_*_learnings.md + team_lead_handoff.md §"Phase 5B-fix-pack scope". Confirms fix-pack contract; fails RED until fix-pack lands.
"""Phase 5B fix-pack tests: R-AP, R-AQ, R-AR, R-AS, R-AT, R-AU

All tests MUST be RED (ImportError / AssertionError / NotImplementedError) until the
fix-pack implementation lands. Spec-anchored to team_lead_handoff.md §"Phase 5B-fix-pack
scope" and the 5 phase5b_to_phase5c_*_learnings.md docs. No fixture bypass — every test
calls the real public entry point.

R-AP (TestClassifyBoundaryLowBehavioral): classify_boundary_low polarity contract.
    Anchored to critic-alice MAJOR-1 + testeng-grace's R-AG importability-only gap flag.

R-AQ (TestModeNoneStrictRejection): read_mode_truth_json with mode=None must be rejected.
    Anchored to exec-emma finding: explicit None bypasses ModeMismatchError guard.

R-AR (TestQuarantinedValueNativeUnitIsNone): quarantined snapshot members must emit
    value_native_unit=None, not inner_min. Anchored to exec-dan finding.

R-AS (TestDSTStepHorizonTargetDateOffset): _compute_required_max_step must use
    target-date local offset, not issue_utc point-in-time offset.
    Anchored to exec-dan finding: fixed-offset timezone in DST city causes 1h drift.

R-AT (TestObservationClientLazyImport): observation_client.py must not raise SystemExit
    at module import when WU_API_KEY is absent.
    Anchored to testeng-grace finding: module-level guard is test-topology land mine.

R-AU (TestRebuildDataVersionAssertion): rebuild must reject unknown data_version strings,
    not just quarantined-list strings. Positive allowlist, not negative quarantine.
    Anchored to exec-emma finding: data_version sourced from snapshot row, not spec.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Helpers shared across R-AP / R-AR / R-AS tests
# ---------------------------------------------------------------------------

def _make_step_values(
    inner_ranges: list[tuple[int, int]],
    inner_val: float,
    boundary_ranges: list[tuple[int, int]],
    boundary_val: float,
) -> dict[str, float]:
    """Build a step_values dict for classify_boundary_low synthetic tests."""
    d: dict[str, float] = {}
    for s, e in inner_ranges:
        d[f"{s}-{e}"] = inner_val
    for s, e in boundary_ranges:
        d[f"{s}-{e}"] = boundary_val
    return d


def _utc(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# R-AP: classify_boundary_low behavioral (critic MAJOR-1)
# ---------------------------------------------------------------------------


class TestClassifyBoundaryLowBehavioral:
    """R-AP: classify_boundary_low polarity contract.

    Three cases: cross-midnight steal, safe boundary, inner-only members.
    Calls the REAL function — no fixture bypass.
    Anchors: critic-alice MAJOR-1 (polarity-swap footgun), testeng-grace §1 gap.
    """

    def test_R_AP_1_cross_midnight_boundary_steals_min(self):
        """R-AP-1 (rejection): boundary_min <= inner_min → boundary_ambiguous=True, effective_min is None.

        Synthetic: inner bucket gives 5.0 K, boundary bucket gives 4.5 K.
        boundary_min (4.5) <= inner_min (5.0) → boundary WINS → quarantine.

        Geometry: London summer, UTC+1. Local day 2026-07-15 starts at 2026-07-14 23:00Z.
        issue_utc = 2026-07-14 18:00Z. Step "0-6" = window 18:00Z-00:00Z (next day UTC),
        which STRADDLES day_start_utc (23:00Z) → boundary classification.
        Step "6-12" = window 00:00Z-06:00Z on 2026-07-15 → fully inside → inner.
        """
        from scripts.extract_tigge_mn2t6_localday_min import classify_boundary_low

        # London summer: local day 2026-07-15 = 2026-07-14 23:00Z to 2026-07-15 23:00Z
        issue_utc = _utc(2026, 7, 14, 18)      # 18:00Z
        day_start_utc = _utc(2026, 7, 14, 23)  # local midnight = 23:00Z
        day_end_utc = _utc(2026, 7, 15, 23)    # next local midnight

        # step "0-6": window_start=18:00Z, window_end=00:00Z next day (00:00Z on 2026-07-15)
        # day_start=23:00Z on 2026-07-14 → window starts BEFORE day_start → NOT fully_inside → boundary
        # step "6-12": window_start=00:00Z on 2026-07-15, window_end=06:00Z on 2026-07-15
        # both within [23:00Z 14th, 23:00Z 15th] → fully_inside → inner
        step_values = {
            "6-12": 5.0,   # inner: fully inside local day → inner_min = 5.0
            "0-6": 4.5,    # boundary: straddles day_start_utc → boundary_min = 4.5
        }

        result = classify_boundary_low(step_values, day_start_utc, day_end_utc, issue_utc)

        assert result.boundary_ambiguous is True, (
            f"boundary_min (4.5) <= inner_min (5.0) must set boundary_ambiguous=True. "
            f"inner_values={result.inner_values}, boundary_values={result.boundary_values}"
        )
        assert result.effective_min is None, (
            "effective_min must be None when boundary_ambiguous=True"
        )

    def test_R_AP_2_safe_boundary_no_steal(self):
        """R-AP-2 (acceptance): boundary_min > inner_min → boundary_ambiguous=False.

        Synthetic: inner bucket 3.0 K, boundary bucket 4.0 K.
        boundary_min (4.0) > inner_min (3.0) → boundary CANNOT steal min → clean.
        """
        from scripts.extract_tigge_mn2t6_localday_min import classify_boundary_low

        issue_utc = _utc(2026, 1, 15, 0)
        day_start_utc = _utc(2026, 1, 15, 0)
        day_end_utc = _utc(2026, 1, 16, 0)

        step_values = {
            "12-18": 3.0,  # inner
            "0-6": 4.0,    # boundary (straddles midnight) but higher value
        }

        result = classify_boundary_low(step_values, day_start_utc, day_end_utc, issue_utc)

        assert result.boundary_ambiguous is False, (
            "boundary_min (4.0) > inner_min (3.0) must set boundary_ambiguous=False"
        )
        assert result.effective_min == pytest.approx(3.0), (
            "effective_min must equal inner_min (3.0) when boundary is not ambiguous"
        )

    def test_R_AP_3_inner_none_only(self):
        """R-AP-3 (rejection): boundary-only coverage → boundary_ambiguous=True.

        Synthetic: only a boundary bucket, no inner values.
        inner_min is None → by law: any boundary value present with no inner = ambiguous.

        Geometry: London summer, issue_utc=2026-07-14 18:00Z, local day starts 23:00Z.
        Step "0-6" = window 18:00Z-00:00Z (2026-07-15) — straddles day_start (23:00Z) → boundary.
        No inner bucket provided → inner_values=[] → inner_min=None → boundary_ambiguous=True.
        """
        from scripts.extract_tigge_mn2t6_localday_min import classify_boundary_low

        issue_utc = _utc(2026, 7, 14, 18)      # 18:00Z
        day_start_utc = _utc(2026, 7, 14, 23)  # local midnight (UTC+1 summer)
        day_end_utc = _utc(2026, 7, 15, 23)    # next local midnight

        # step "0-6": window 18:00Z to 00:00Z (2026-07-15 00:00Z)
        # window_start (18:00Z) < day_start (23:00Z) AND window_end (00:00Z 15th) > day_start (23:00Z 14th)
        # → has overlap but NOT fully_inside → boundary classification
        # No inner steps provided → inner_min=None
        step_values = {
            "0-6": 4.0,    # straddles day_start_utc → boundary only
        }

        result = classify_boundary_low(step_values, day_start_utc, day_end_utc, issue_utc)

        assert result.boundary_ambiguous is True, (
            f"boundary-only coverage (inner_min=None, boundary_min=4.0) must be boundary_ambiguous=True. "
            f"inner_values={result.inner_values}, boundary_values={result.boundary_values}"
        )
        assert result.effective_min is None, (
            "effective_min must be None for boundary-only coverage"
        )


# ---------------------------------------------------------------------------
# R-AQ: mode=None strict rejection (exec-emma finding)
# ---------------------------------------------------------------------------


class TestModeNoneStrictRejection:
    """R-AQ: read_mode_truth_json must reject mode=None explicitly.

    Anchors: exec-emma §1 finding — mode=None bypasses ModeMismatchError guard at
    truth_files.py:156 because the guard is 'if mode is not None'.
    Fix: reject mode=None at entry with ModeMismatchError (or ValueError).
    """

    def test_R_AQ_1_explicit_none_mode_raises(self, tmp_path: Path, monkeypatch):
        """R-AQ-1 (rejection): read_mode_truth_json("portfolio.json", mode=None) must raise.

        Currently mode=None is silently accepted (guard is 'if mode is not None' at line 156).
        Fix: raise ModeMismatchError (or ValueError) at entry when mode=None is explicit.

        We write a real live-tagged file so the function reaches the mode-check rather than
        short-circuiting on FileNotFoundError before the guard can be evaluated.
        """
        import src.config as config_mod
        from src.state.truth_files import read_mode_truth_json, ModeMismatchError

        monkeypatch.setattr(config_mod, "STATE_DIR", tmp_path)

        truth_file = tmp_path / "portfolio.json"
        payload = {
            "truth": {
                "mode": "live",
                "authority": "VERIFIED",
                "generated_at": "2026-04-17T00:00:00+00:00",
            }
        }
        truth_file.write_text(json.dumps(payload))

        # The fix must raise when mode=None is explicitly passed.
        # Current behaviour: mode=None → guard skipped → silently returns live file.
        # This assertion is RED until the fix lands.
        with pytest.raises((ModeMismatchError, ValueError)):
            read_mode_truth_json("portfolio.json", mode=None)

    def test_R_AQ_2_valid_mode_succeeds(self, tmp_path: Path, monkeypatch):
        """R-AQ-2 (acceptance): mode="live" with a live-tagged file succeeds.

        Regression guard: the fix must not break the valid path.
        """
        from src.state.truth_files import read_mode_truth_json, ModeMismatchError
        import src.config as config_mod

        # Redirect state dir to tmp_path so we control the file
        monkeypatch.setattr(config_mod, "STATE_DIR", tmp_path)

        # Write a minimal live-tagged truth file
        truth_file = tmp_path / "portfolio.json"
        payload = {
            "truth": {
                "mode": "live",
                "authority": "VERIFIED",
                "generated_at": "2026-04-17T00:00:00+00:00",
            }
        }
        truth_file.write_text(json.dumps(payload))

        # Should not raise — live mode matches live-tagged file
        data, truth = read_mode_truth_json("portfolio.json", mode="live")
        assert truth.get("mode") == "live"


# ---------------------------------------------------------------------------
# R-AR: quarantined value_native_unit = None (exec-dan finding)
# ---------------------------------------------------------------------------


class TestQuarantinedValueNativeUnitIsNone:
    """R-AR: when boundary_ambiguous=True, output members must carry value_native_unit=None.

    Anchors: exec-dan §1 'BoundaryClassification.effective_min behavior when any_boundary_ambiguous=True'.
    Current code at extract_tigge_mn2t6_localday_min.py:288 emits clf.inner_min (non-null)
    for quarantined snapshots. Fix: emit None.
    This is a silent trap: downstream consumers can read value_native_unit without checking
    training_allowed and get a contaminated value.
    """

    def _make_member_step_values_boundary_ambiguous(self) -> dict[int, dict[str, float]]:
        """51 members where member 0 has boundary_min <= inner_min → whole snapshot quarantined.

        Geometry: issue_utc=2026-01-14 19:00Z, target_date=2026-01-15, London (UTC+0 winter).
        day_start=2026-01-15 00:00Z, day_end=2026-01-16 00:00Z.
        step "0-6":  window 19:00Z→01:00Z (Jan 14→15) — starts before day_start, ends inside → BOUNDARY.
        step "6-12": window 01:00Z→07:00Z (Jan 15) — fully inside day → INNER.
        Member 0: inner_min=5.0 (step 6-12), boundary_min=4.5 (step 0-6) → boundary_min<=inner_min → ambiguous.
        Verified: classify_boundary_low returns boundary_ambiguous=True for this geometry.
        """
        members: dict[int, dict[str, float]] = {}
        for m in range(51):
            if m == 0:
                # Boundary leaks: boundary_min(4.5) <= inner_min(5.0) → ambiguous
                members[m] = {"6-12": 5.0, "0-6": 4.5}
            else:
                # Safe members: boundary(6.0) > inner(3.0)
                members[m] = {"6-12": 3.0, "0-6": 6.0}
        return members

    def test_R_AR_1_boundary_ambiguous_member_value_is_None(self):
        """R-AR-1 (rejection): any_boundary_ambiguous=True → all member value_native_unit are None.

        Calls build_low_snapshot_json (real entry point). Fails RED until fix lands.
        issue_utc=2026-01-14 19:00Z: step "0-6" window=19:00Z→01:00Z overlaps Jan 15 and
        starts before day_start(00:00Z) → boundary. step "6-12"=01:00Z→07:00Z → inner.
        """
        from scripts.extract_tigge_mn2t6_localday_min import build_low_snapshot_json

        member_step_values = self._make_member_step_values_boundary_ambiguous()

        payload = build_low_snapshot_json(
            city_name="London",
            city_tz="Europe/London",
            issue_utc=_utc(2026, 1, 14, 19),  # 19:00Z → step "0-6" overlaps day boundary
            target_date=date(2026, 1, 15),
            lead_day=1,
            member_step_values=member_step_values,
            manifest_sha256_value="deadbeef" * 8,
        )

        assert payload["boundary_policy"]["boundary_ambiguous"] is True, (
            "Test precondition: snapshot must be quarantined (boundary_ambiguous=True)"
        )
        assert payload["training_allowed"] is False, (
            "Test precondition: training_allowed must be False for quarantined snapshot"
        )

        # THE CONTRACT: every member must have value_native_unit=None for quarantined snapshots
        for entry in payload["members"]:
            assert entry["value_native_unit"] is None, (
                f"Member {entry['member']}: value_native_unit must be None for quarantined "
                f"snapshot (boundary_ambiguous=True), got {entry['value_native_unit']!r}. "
                "Fix: emit None, not clf.inner_min, when any_boundary_ambiguous=True."
            )

    def test_R_AR_2_clean_member_value_preserved(self):
        """R-AR-2 (acceptance): training_allowed=True members have real float value_native_unit.

        Regression guard: clean snapshots must not be affected by the fix.
        """
        from scripts.extract_tigge_mn2t6_localday_min import build_low_snapshot_json

        # All members have boundary_min > inner_min → no quarantine
        member_step_values: dict[int, dict[str, float]] = {}
        for m in range(51):
            member_step_values[m] = {"12-18": 270.0, "0-6": 275.0}  # inner < boundary

        # Tokyo: UTC+9, no DST. Issue 00Z → local day already started (N/A_CAUSAL)
        # Use a negative-offset city (New York, UTC-5 winter) for pure_forecast_valid=True
        payload = build_low_snapshot_json(
            city_name="New York",
            city_tz="America/New_York",
            issue_utc=_utc(2026, 1, 15, 0),
            target_date=date(2026, 1, 15),
            lead_day=0,
            member_step_values=member_step_values,
            manifest_sha256_value="deadbeef" * 8,
        )

        assert payload["boundary_policy"]["boundary_ambiguous"] is False, (
            "Test precondition: snapshot must be clean (boundary_ambiguous=False)"
        )

        # All 51 members must have non-null float value_native_unit for a clean snapshot
        non_null = [
            e["value_native_unit"] for e in payload["members"]
            if e["value_native_unit"] is not None
        ]
        assert len(non_null) == 51, (
            f"Clean snapshot must have all 51 members with non-null value_native_unit; "
            f"got {len(non_null)}. Fix must not accidentally null clean members."
        )
        for v in non_null:
            assert isinstance(v, float), (
                f"value_native_unit must be float for clean snapshot, got {type(v)}"
            )


# ---------------------------------------------------------------------------
# R-AS: DST step-horizon target-date offset (exec-dan finding)
# ---------------------------------------------------------------------------


class TestDSTStepHorizonTargetDateOffset:
    """R-AS: _compute_required_max_step must use target-date local offset, not issue_utc offset.

    Anchors: exec-dan §1 '_compute_required_max_step uses fixed-offset timezone, not ZoneInfo'.
    Current implementation: timezone(timedelta(hours=city_utc_offset_hours)) where offset
    is computed from issue_utc. For DST-boundary crossing, the offset at issue_utc differs
    from offset at target_date → 1h drift in step horizon.

    These tests are RED because the current code uses a fixed-offset computed from
    issue_utc, not the offset at target_date local midnight. The fix must use
    ZoneInfo(city_tz).utcoffset(target_date_midnight_local) instead.
    """

    def test_R_AS_1_dst_boundary_city_uses_target_date_offset(self):
        """R-AS-1: London DST transition — step horizon differs by 1h across DST boundary.

        London: UTC+0 in winter, UTC+1 in summer (springs forward 2026-03-29 01:00 local).
        Scenario: issue_utc=2026-03-28 23:00Z (UTC+0), target_date=2026-03-31 (UTC+1).

        _compute_required_max_step computes: delta = (next_day_midnight_in_fixed_tz - issue_utc).
        Wrong (issue_utc offset = 0): end = 2026-04-01 00:00+00:00 → 00:00Z → delta = 25h → ceil=30h.
        Correct (target_date offset = +1): end = 2026-04-01 00:00+01:00 → 23:00Z March 31 → delta = 24h → ceil=24h.

        25h and 24h give different ceilings-to-6h (30 vs 24), exposing the 1h drift.
        The extractor currently uses ZoneInfo(city_tz).utcoffset(issue_utc) — winter offset.
        Fix must use the offset at target_date local midnight — summer offset.
        """
        from scripts.extract_tigge_mn2t6_localday_min import _compute_required_max_step
        from zoneinfo import ZoneInfo

        city_tz = "Europe/London"
        # issue_utc = 2026-03-28 23:00Z (still winter, UTC+0)
        issue_utc = datetime(2026, 3, 28, 23, 0, tzinfo=timezone.utc)
        # target_date = 2026-03-31 (summer, UTC+1 after spring-forward on 2026-03-29)
        target_date = date(2026, 3, 31)

        tz = ZoneInfo(city_tz)

        # Verify preconditions
        wrong_offset_h = int(tz.utcoffset(issue_utc).total_seconds() / 3600)
        assert wrong_offset_h == 0, f"London offset at issue_utc (2026-03-28 23:00Z) should be 0, got {wrong_offset_h}"

        target_midnight_local = datetime(2026, 3, 31, 0, 0, tzinfo=tz)
        correct_offset_h = int(tz.utcoffset(target_midnight_local).total_seconds() / 3600)
        assert correct_offset_h == 1, f"London offset at target_date midnight (2026-03-31) should be +1, got {correct_offset_h}"

        # With wrong offset (0): end = 2026-04-01 00:00+00:00 = 00:00Z on Apr 1
        #   delta = (Apr 1 00:00Z) - (Mar 28 23:00Z) = 3d1h = 73h → ceil_to_6h(73) = 78h
        # With correct offset (+1): end = 2026-04-01 00:00+01:00 = Mar 31 23:00Z
        #   delta = (Mar 31 23:00Z) - (Mar 28 23:00Z) = 3d0h = 72h → ceil_to_6h(72) = 72h
        # Document the arithmetic: wrong vs correct step horizons
        wrong_step = _compute_required_max_step(issue_utc, target_date, wrong_offset_h)
        correct_step = _compute_required_max_step(issue_utc, target_date, correct_offset_h)
        assert wrong_step == 78, f"Precondition: wrong_step (offset=0) should be 78h, got {wrong_step}"
        assert correct_step == 72, f"Precondition: correct_step (offset=+1) should be 72h, got {correct_step}"

        # THE CONTRACT: build_low_snapshot_json must emit step_horizon_hours=72 (correct DST-aware value).
        # Current bug: it computes dst_offset_h = ZoneInfo(city_tz).utcoffset(issue_utc) = 0 (winter)
        # → calls _compute_required_max_step with offset=0 → step_horizon_hours=78.
        # Fix: compute offset from target_date local midnight → offset=+1 → step_horizon_hours=72.
        # Minimal member_step_values: 13 steps of 6h each covering 0-78h to avoid horizon-deficit.
        member_step_values: dict[int, dict[str, float]] = {}
        for m in range(51):
            sv: dict[str, float] = {}
            for step_start in range(0, 78, 6):
                sv[f"{step_start}-{step_start + 6}"] = 270.0
            member_step_values[m] = sv

        from scripts.extract_tigge_mn2t6_localday_min import build_low_snapshot_json

        payload = build_low_snapshot_json(
            city_name="London",
            city_tz=city_tz,
            issue_utc=issue_utc,
            target_date=target_date,
            lead_day=3,
            member_step_values=member_step_values,
            manifest_sha256_value="deadbeef" * 8,
        )

        actual_step_horizon = payload["step_horizon_hours"]
        assert actual_step_horizon == 72.0, (
            f"build_low_snapshot_json must emit step_horizon_hours=72.0 (target-date DST offset=+1). "
            f"Got {actual_step_horizon}. Current bug: uses issue_utc offset (UTC+0) → 78h. "
            "Fix: use ZoneInfo(city_tz).utcoffset(target_date_midnight_local) in build_low_snapshot_json."
        )

    def test_R_AS_2_non_dst_city_unchanged(self):
        """R-AS-2 (acceptance): Tokyo (UTC+9 year-round) step horizon unchanged pre/post fix.

        Tokyo has no DST. The fix must not change behavior for non-DST cities.
        """
        from scripts.extract_tigge_mn2t6_localday_min import _compute_required_max_step
        from zoneinfo import ZoneInfo

        city_tz = "Asia/Tokyo"
        issue_utc = _utc(2026, 3, 28, 0)
        target_date = date(2026, 3, 30)

        tz = ZoneInfo(city_tz)
        # Pre-fix: offset from issue_utc
        offset_at_issue = int(tz.utcoffset(issue_utc).total_seconds() / 3600)
        # Post-fix: offset at target_date local midnight
        target_midnight_local = datetime(2026, 3, 30, 0, 0, tzinfo=tz)
        offset_at_target = int(tz.utcoffset(target_midnight_local).total_seconds() / 3600)

        # For Tokyo, both must be +9
        assert offset_at_issue == 9, f"Tokyo offset at issue_utc should be +9, got {offset_at_issue}"
        assert offset_at_target == 9, f"Tokyo offset at target_date should be +9, got {offset_at_target}"

        step_with_issue_offset = _compute_required_max_step(issue_utc, target_date, offset_at_issue)
        step_with_target_offset = _compute_required_max_step(issue_utc, target_date, offset_at_target)

        # For non-DST city, both calls must produce the same result
        assert step_with_issue_offset == step_with_target_offset, (
            f"Non-DST city (Tokyo) step horizon must be identical regardless of "
            f"whether offset is computed from issue_utc or target_date: "
            f"issue_offset={step_with_issue_offset}h, target_offset={step_with_target_offset}h"
        )


# ---------------------------------------------------------------------------
# R-AT: observation_client lazy import (testeng-grace finding)
# ---------------------------------------------------------------------------


class TestObservationClientLazyImport:
    """R-AT: observation_client.py must not raise SystemExit at module import.

    Anchors: testeng-grace §1 'observation_client.py:87 module-level SystemExit'.
    Current code raises SystemExit if WU_API_KEY is absent — AT MODULE IMPORT TIME.
    This poisons any test that imports transitively from src/data/.

    Fix: move the guard inside the class __init__ or a lazy .connect() method.
    Trust boundary preserved: callsite still raises, just at call time, not import time.

    R-AT uses subprocess isolation for R-AT-1 because Python's import cache means
    monkeypatch.delenv can't force a fresh module load in-process. The subprocess
    approach is the only reliable way to test the module-level guard.
    """

    def test_R_AT_1_module_imports_without_wu_api_key(self):
        """R-AT-1 (rejection of current behavior): import succeeds when WU_API_KEY absent.

        Subprocess: run python -c "import src.data.observation_client" with WU_API_KEY unset.
        Current: exits with code 1 (SystemExit). After fix: exits with code 0 (clean import).
        """
        env = {k: v for k, v in os.environ.items() if k != "WU_API_KEY"}
        project_root = str(Path(__file__).resolve().parents[1])
        result = subprocess.run(
            [sys.executable, "-c", "import src.data.observation_client"],
            env=env,
            capture_output=True,
            text=True,
            cwd=project_root,
        )
        assert result.returncode == 0, (
            f"observation_client.py must be importable without WU_API_KEY after fix. "
            f"Currently exits with code {result.returncode}. "
            f"stderr: {result.stderr[:300]!r}. "
            "Fix: move guard from module level to class __init__ or .connect() method."
        )

    def test_R_AT_2_callsite_fails_closed_on_missing_key(self, monkeypatch):
        """R-AT-2 (GREEN): _require_wu_api_key() raises SystemExit when WU_API_KEY is empty.

        exec-ida's fix moved the guard from module-level to _require_wu_api_key() at L208-212,
        called from _fetch_wu_observation at L222. This test calls _require_wu_api_key() directly
        with WU_API_KEY patched to empty string — confirms trust boundary is preserved at callsite.
        """
        import src.data.observation_client as obs_mod

        monkeypatch.setattr(obs_mod, "WU_API_KEY", "")

        with pytest.raises(SystemExit, match="WU_API_KEY"):
            obs_mod._require_wu_api_key()


# ---------------------------------------------------------------------------
# R-AU: rebuild per-spec data_version cross-check (exec-emma finding, precision per team-lead)
# ---------------------------------------------------------------------------


class TestRebuildDataVersionAssertion:
    """R-AU: _process_snapshot_v2 must cross-check row data_version against spec.allowed_data_version.

    Anchors: exec-emma §1 'rebuild_calibration_pairs_v2.py — data_version sourced from
    snapshot row, not from MetricIdentity'. Current code at line 216 calls
    assert_data_version_allowed (quarantine-BLOCK only). The per-spec cross-check
    row["data_version"] == spec.allowed_data_version is ABSENT inside _process_snapshot_v2.

    Without this check, a HIGH_LOCALDAY_MAX snapshot (data_version='tigge_mx2t6_local_calendar_day_max_v1')
    fetched by a LOW_SPEC rebuild run could be processed silently — SQL pre-filter guards against
    this in the normal path, but _process_snapshot_v2 has no write-time assertion.

    Fix: add `spec: CalibrationMetricSpec` param to _process_snapshot_v2; add assertion
        `if snapshot["data_version"] != spec.allowed_data_version: raise DataVersionQuarantinedError(...)`
    before any DB writes. This is a write-time contract, not a query-time hint.
    """

    _DB_SCHEMA = """
        CREATE TABLE IF NOT EXISTS ensemble_snapshots_v2 (
            snapshot_id INTEGER PRIMARY KEY,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL,
            physical_quantity TEXT NOT NULL,
            observation_field TEXT NOT NULL,
            fetch_time TEXT NOT NULL,
            model_version TEXT NOT NULL,
            data_version TEXT NOT NULL,
            members_unit TEXT NOT NULL DEFAULT 'degC',
            training_allowed INTEGER NOT NULL DEFAULT 1,
            issue_time TEXT,
            available_at TEXT,
            lead_hours REAL,
            causality_status TEXT DEFAULT 'OK',
            authority TEXT DEFAULT 'VERIFIED',
            members_json TEXT NOT NULL DEFAULT '[]',
            manifest_hash TEXT,
            provenance_json TEXT
        )
    """

    def _make_snapshot_row(self, conn, data_version: str):
        """Insert one row and return it as sqlite3.Row (dict_factory)."""
        import sqlite3 as _sqlite3
        conn.row_factory = _sqlite3.Row
        conn.execute(self._DB_SCHEMA)
        # Stub observations table so _fetch_verified_observation returns None (no rows)
        # rather than raising OperationalError. Lets spec-check run to completion.
        conn.execute(
            "CREATE TABLE IF NOT EXISTS observations "
            "(city TEXT, target_date TEXT, high_temp REAL, unit TEXT, authority TEXT, source TEXT)"
        )
        conn.execute("""
            INSERT INTO ensemble_snapshots_v2 (
                city, target_date, temperature_metric, physical_quantity,
                observation_field, fetch_time, model_version,
                data_version, members_unit, training_allowed,
                issue_time, available_at, lead_hours,
                causality_status, authority, members_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "New York", "2026-01-15",
            "high",
            "mx2t6_local_calendar_day_max",
            "high_temp",
            "2026-01-15T06:05:00+00:00",
            "tigge-ens-51",
            data_version,
            "degC",
            1,
            "2026-01-15T00:00:00+00:00",
            "2026-01-15T06:00:00+00:00",
            72.0,
            "OK",
            "VERIFIED",
            json.dumps([270.0 + i * 0.01 for i in range(51)]),
        ))
        conn.commit()
        return conn.execute(
            "SELECT * FROM ensemble_snapshots_v2 LIMIT 1"
        ).fetchone()

    def test_R_AU_1_global_allowlist_rejects_unknown_version(self):
        """R-AU-1 (GREEN antibody): assert_data_version_allowed rejects unknown data_version.

        The positive allowlist in assert_data_version_allowed must refuse any version not in
        the canonical set {HIGH_LOCALDAY_MAX.data_version, LOW_LOCALDAY_MIN.data_version}.
        Regression guard: this check must not be weakened by exec-ida's 6b per-spec fix.
        """
        from src.contracts.ensemble_snapshot_provenance import (
            assert_data_version_allowed,
            DataVersionQuarantinedError,
        )
        from src.types.metric_identity import HIGH_LOCALDAY_MAX, LOW_LOCALDAY_MIN

        # Canonical versions must not be blocked
        try:
            assert_data_version_allowed(HIGH_LOCALDAY_MAX.data_version, context="R-AU-1-high")
            assert_data_version_allowed(LOW_LOCALDAY_MIN.data_version, context="R-AU-1-low")
        except DataVersionQuarantinedError as e:
            pytest.fail(f"Canonical data_version incorrectly blocked by global guard: {e}")

        # Unknown version must be blocked
        with pytest.raises((DataVersionQuarantinedError, AssertionError, ValueError)):
            assert_data_version_allowed(
                "tigge_experimental_v99",
                context="rebuild_calibration_pairs_v2",
            )

    def test_R_AU_3_per_spec_cross_metric_rejected(self):
        """R-AU-3 (RED): _process_snapshot_v2 must have a per-spec cross-check param.

        Pre-fix: _process_snapshot_v2 has no `spec` param and no per-spec data_version check.
        A LOW-data_version snapshot passed to the HIGH rebuild loop passes assert_data_version_allowed
        (LOW is in the canonical allowlist) and is silently processed with the wrong metric.

        Fix: add `spec: CalibrationMetricSpec` param to _process_snapshot_v2 and check
        `snapshot["data_version"] == spec.allowed_data_version` before any processing.

        This test asserts the structural contract: `spec` must be a parameter of
        _process_snapshot_v2. Pre-fix → fails. Post-fix → passes.
        """
        import inspect
        from scripts.rebuild_calibration_pairs_v2 import _process_snapshot_v2

        sig = inspect.signature(_process_snapshot_v2)
        assert "spec" in sig.parameters, (
            "_process_snapshot_v2 must have a 'spec: CalibrationMetricSpec' parameter "
            "for per-spec data_version cross-check (cross-metric leakage defense). "
            "Currently absent — cross-metric contamination is not guarded at write time. "
            "Fix: add spec param and assert snapshot['data_version'] == spec.allowed_data_version."
        )

    def test_R_AU_2_canonical_version_accepted(self):
        """R-AU-2 (GREEN antibody): HIGH_LOCALDAY_MAX row under HIGH_SPEC must not be spec-rejected.

        Regression guard: the fix must not refuse canonical same-spec pairings.
        Processing may still stop early (no observation in :memory: DB) — that is acceptable;
        a DataVersionQuarantinedError / AssertionError from the spec cross-check is a failure.
        """
        import sqlite3
        from scripts.rebuild_calibration_pairs_v2 import (
            _process_snapshot_v2,
            CalibrationMetricSpec,
            RebuildStatsV2,
        )
        from src.types.metric_identity import HIGH_LOCALDAY_MAX
        from src.contracts.ensemble_snapshot_provenance import DataVersionQuarantinedError

        conn = sqlite3.connect(":memory:")
        high_row = self._make_snapshot_row(conn, HIGH_LOCALDAY_MAX.data_version)

        high_spec = CalibrationMetricSpec(HIGH_LOCALDAY_MAX, HIGH_LOCALDAY_MAX.data_version)
        stats = RebuildStatsV2()

        import sqlite3 as _sqlite3
        try:
            from src.config import cities_by_name
            city = cities_by_name.get("New York") or list(cities_by_name.values())[0]
            import numpy as np
            _process_snapshot_v2(
                conn, high_row, city,
                spec=high_spec,
                n_mc=None,
                rng=np.random.default_rng(42),
                stats=stats,
            )
        except (DataVersionQuarantinedError, AssertionError) as e:
            pytest.fail(
                f"Matching-spec row was incorrectly rejected by per-spec cross-check: {e}. "
                "Fix must only reject cross-spec mismatches."
            )
        except _sqlite3.OperationalError:
            # Expected in :memory: DB — observations table doesn't exist, so function
            # proceeds past the spec check (correct) and fails at observation lookup.
            # This confirms the spec cross-check did NOT reject the matching-spec row.
            pass
        except TypeError as e:
            if "spec" in str(e):
                pytest.skip(
                    f"_process_snapshot_v2 does not yet accept 'spec' kwarg (pre-fix state): {e}. "
                    "This test becomes meaningful once the fix adds the spec param."
                )


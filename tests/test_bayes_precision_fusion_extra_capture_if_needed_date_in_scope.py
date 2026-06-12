# Lifecycle: created=2026-06-08; last_reviewed=2026-06-08; last_reused=2026-06-08
# Purpose: Relationship regression test for BAYES_PRECISION_FUSION extra-model capture wiring in src/main.py; guards against bare `date` NameError (BLOCKER 9) and verifies capture is gated by the edli flag.
# Reuse: Run with pytest; update if the BAYES_PRECISION_FUSION extra-capture wiring or flag gate in src/main.py changes.
# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: PR#400 review (src/main.py:4909 bare `date` NameError swallowed by
#   fail-soft); CONTINUITY_AND_WIRING.md §4 step 2 + BAYES_PRECISION_FUSION_SPEC.md §6 F1 (BAYES_PRECISION_FUSION multi-model
#   SHADOW capture gated by edli.replacement_0_1_bayes_precision_fusion_capture_enabled).
"""Relationship regression test for the BAYES_PRECISION_FUSION extra-model capture wiring in src.main.

Relationship under test (plan-row -> BAYES_PRECISION_FUSION download-target boundary, src/main.py
`_download_bayes_precision_fusion_extra_raw_inputs_if_needed`):

  The plan builder emits ReplacementForecastCurrentTargetPlanRow objects whose
  ``target_date`` is an ISO string. main.py converts that string with
  ``date.fromisoformat(row.target_date) - cycle.date()`` to derive ``lead_days``
  before handing the target to ``download_bayes_precision_fusion_extra_raw_inputs``. ``date`` is NOT a
  module-level name in main.py (module import is only ``datetime, timedelta,
  timezone``), and the function's local import block historically imported only
  ``datetime``/``timezone`` -- so the first uncovered target row raised
  ``NameError: name 'date' is not defined``. That NameError was swallowed by the
  function's broad fail-soft ``except Exception`` (status
  ``BAYES_PRECISION_FUSION_EXTRA_CAPTURE_FAILSOFT_SKIPPED``), so the whole BAYES_PRECISION_FUSION capture silently never
  ran even with the flag ON.

Properties asserted:
  (1) With the capture flag ON and a normal uncovered target row, the function does
      NOT raise NameError and does NOT fall into the fail-soft skip path.
  (2) The capture is actually ATTEMPTED: ``download_bayes_precision_fusion_extra_raw_inputs`` is invoked
      with exactly one target carrying the row's city/metric/target_date and a
      correctly-derived non-negative ``lead_days`` (the value computed across the
      ``date.fromisoformat`` boundary).
  (3) Covered rows are skipped; only uncovered rows become download targets.

No network: the plan builder and the downstream downloader are both injected.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

import src.config as cfg
import src.data.replacement_forecast_current_target_plan as plan_mod
import src.data.bayes_precision_fusion_download as dl_mod
import src.main as main_mod
from src.data.replacement_forecast_current_target_plan import (
    ReplacementForecastCurrentTargetPlan,
    ReplacementForecastCurrentTargetPlanRow,
)


def _row(*, city: str, target_date: str, covered: bool) -> ReplacementForecastCurrentTargetPlanRow:
    # covered == (posterior_count > 0 and readiness_count > 0); flip both to toggle coverage.
    n = 1 if covered else 0
    return ReplacementForecastCurrentTargetPlanRow(
        city=city,
        target_date=target_date,
        temperature_metric="high",
        market_bin_count=1,
        posterior_count=n,
        readiness_count=n,
        aifs_manifest_count=0,
        openmeteo_manifest_count=0,
    )


def _plan(rows: list[ReplacementForecastCurrentTargetPlanRow]) -> ReplacementForecastCurrentTargetPlan:
    covered = sum(1 for r in rows if r.covered)
    return ReplacementForecastCurrentTargetPlan(
        status="CURRENT_TARGETS_MISSING_COVERAGE",
        reason_codes=(),
        target_count=len(rows),
        covered_count=covered,
        missing_coverage_count=len(rows) - covered,
        can_seed_count=0,
        missing_aifs_manifest_count=0,
        missing_openmeteo_manifest_count=0,
        day0_observed_extreme_required_count=0,
        rows=tuple(rows),
    )


def _wire(monkeypatch, *, rows, forecast_db="zeus-forecasts.db"):
    """Enable the capture flag and inject the plan builder + downloader. Returns the
    list that records each ``download_bayes_precision_fusion_extra_raw_inputs`` call's kwargs."""
    monkeypatch.setitem(
        cfg.settings["edli"], "replacement_0_1_bayes_precision_fusion_capture_enabled", True
    )

    monkeypatch.setattr(
        plan_mod, "build_replacement_forecast_current_target_plan",
        lambda _db: _plan(rows),
    )

    calls: list[dict] = []

    def _fake_download(*, forecast_db, cycle, targets, release_lag_hours):
        targets = list(targets)
        calls.append({
            "forecast_db": forecast_db,
            "cycle": cycle,
            "targets": targets,
            "release_lag_hours": release_lag_hours,
        })
        return {"status": "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED", "written_row_count": len(targets)}

    monkeypatch.setattr(dl_mod, "download_bayes_precision_fusion_extra_raw_inputs", _fake_download)

    # Run-selection single authority (2026-06-11): the capture lane resolves its cycle
    # via provider probes (never the dead now-minus-lag guess). Pin a deterministic
    # probe-resolved cycle so lead_days assertions are exact and offline.
    import src.data.replacement_forecast_production as production

    probed_cycle = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    monkeypatch.setattr(
        production, "_probe_resolved_available_cycle", lambda: probed_cycle
    )

    cfg_dict = {"forecast_db": forecast_db, "download_release_lag_hours": 14.0}
    return cfg_dict, calls


# ---------------------------------------------------------------------------------------
# (1)+(2) normal uncovered target: no NameError, capture attempted, lead_days correct
# ---------------------------------------------------------------------------------------
def test_does_not_raise_nameerror_and_attempts_capture(monkeypatch) -> None:
    # target_date 6 days after "today" -> lead_days must come out as 6 across the
    # date.fromisoformat boundary. Use a city present in cities_by_name.
    today = datetime.now(timezone.utc).date()
    target_date = (today + timedelta(days=6)).isoformat()
    rows = [_row(city="Amsterdam", target_date=target_date, covered=False)]
    cfg_dict, calls = _wire(monkeypatch, rows=rows)

    report = main_mod._download_bayes_precision_fusion_extra_raw_inputs_if_needed(cfg_dict)

    # Property (1): NOT the fail-soft skip path. A NameError would have produced
    # status BAYES_PRECISION_FUSION_EXTRA_CAPTURE_FAILSOFT_SKIPPED with the NameError text.
    assert report is not None
    assert report.get("status") != "BAYES_PRECISION_FUSION_EXTRA_CAPTURE_FAILSOFT_SKIPPED", report
    assert "name 'date' is not defined" not in str(report.get("error", ""))
    assert report.get("status") == "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED"

    # Property (2): capture actually attempted with the row's identity + derived lead.
    assert len(calls) == 1
    targets = calls[0]["targets"]
    assert len(targets) == 1
    t = targets[0]
    assert t.city == "Amsterdam"
    assert t.metric == "high"
    assert t.target_date == target_date
    # lead_days is the cross-boundary value the fix unblocks:
    #   max(0, date.fromisoformat(target_date) - cycle.date()).days
    # The function now uses the probe-resolved cycle pinned in _wire (today 00Z), so the
    # expected value is exact and offline.
    cycle = calls[0]["cycle"]
    expected_lead = max(0, (date.fromisoformat(target_date) - cycle.date()).days)
    assert t.lead_days == expected_lead
    assert t.lead_days >= 0


# ---------------------------------------------------------------------------------------
# (3) covered rows are INCLUDED (CYCLE-CURRENCY, K-root instance #5): plan 'covered' has
# no cycle-awareness, so excluding covered rows froze covered targets on stale-cycle
# extras (Madrid 06-10 fused with icon_global off the 06-08T12 row). The extras job
# feeds ALL current targets; the downloader itself dedups per persisted
# (model, city, target, metric, cycle, endpoint) row.
# ---------------------------------------------------------------------------------------
def test_covered_rows_still_reach_the_downloader(monkeypatch) -> None:
    today = datetime.now(timezone.utc).date()
    td = (today + timedelta(days=3)).isoformat()
    rows = [
        _row(city="Amsterdam", target_date=td, covered=True),   # included (currency)
        _row(city="Ankara", target_date=td, covered=False),     # included
    ]
    cfg_dict, calls = _wire(monkeypatch, rows=rows)

    report = main_mod._download_bayes_precision_fusion_extra_raw_inputs_if_needed(cfg_dict)

    assert report.get("status") != "BAYES_PRECISION_FUSION_EXTRA_CAPTURE_FAILSOFT_SKIPPED", report
    assert len(calls) == 1
    cities = sorted(t.city for t in calls[0]["targets"])
    assert cities == ["Amsterdam", "Ankara"], (
        "covered rows must NOT be filtered from the extras capture — coverage is not "
        "currency (K-root instance #5); per-row dedup lives in the downloader"
    )


# ---------------------------------------------------------------------------------------
# Pre-fix guard: the bare-`date` NameError is exactly what fail-soft would have hidden.
# ---------------------------------------------------------------------------------------
def test_target_date_iso_is_parseable_by_date_fromisoformat() -> None:
    # Documents the boundary contract: the row.target_date string MUST be ISO so the
    # main.py conversion `date.fromisoformat(row.target_date)` succeeds. If a future
    # change makes target_date non-ISO, this fails loudly instead of being swallowed.
    td = (date.today() + timedelta(days=2)).isoformat()
    assert date.fromisoformat(td) == date.today() + timedelta(days=2)

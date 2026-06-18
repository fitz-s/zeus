# Created: 2026-05-18
# Last reused or audited: 2026-06-08 (system_decomposition_plan §8 Step 4: WU daily dedup —
#   updated test_main_wu_daily_job_uses_scheduler_not_fixed_cron for the post-dedup topology,
#   where data-ingest is the SOLE WU-daily owner and src.main registers no wu_daily job)
# Authority basis: G4_CLEANUP_DESIGN.md §2 L (Cluster L), src/data/AGENTS.md;
#   docs/architecture/system_decomposition_plan.md §8 Step 4
# Lifecycle: created=2026-05-18; last_reviewed=2026-06-08; last_reused=2026-06-08
# Purpose: Verify WU scheduler eligibility logic and dispatch routing (K2 cluster L)
# Reuse: standalone pytest; no shared fixtures beyond conftest.py
"""K2 physical-clock WU scheduler tests."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src.config import cities_by_name
from src.data.wu_scheduler import WuDailyScheduler, dispatch_wu_daily_collection


def test_scheduler_basic_construction():
    s = WuDailyScheduler()
    assert s is not None
    assert s._offset_hours == 4.0


def test_next_trigger_uses_local_peak_plus_offset_nyc():
    """NYC peak_hour ~15.8 -> local trigger ~19:48 EDT -> UTC ~23:48 or 00:48 next day."""
    s = WuDailyScheduler()
    nyc = cities_by_name["NYC"]
    # Use a specific reference time: July 15, 2025, 10:00 UTC (pre-trigger)
    ref = datetime(2025, 7, 15, 10, 0, tzinfo=timezone.utc)
    trigger = s.next_trigger_utc(nyc, reference_utc=ref)
    # Trigger must be after reference
    assert trigger > ref
    # Trigger should be in local time at expected hour
    tz = ZoneInfo(nyc.timezone)
    trigger_local = trigger.astimezone(tz)
    expected_hour = int(nyc.historical_peak_hour + 4) % 24
    assert trigger_local.hour == expected_hour


def test_next_trigger_advances_to_tomorrow_when_past():
    """If the reference is after today's trigger, next trigger is tomorrow."""
    s = WuDailyScheduler()
    nyc = cities_by_name["NYC"]
    # Reference AFTER today's trigger: July 16 at 12:00 UTC (well after local trigger)
    ref = datetime(2025, 7, 16, 12, 0, tzinfo=timezone.utc)
    trigger = s.next_trigger_utc(nyc, reference_utc=ref)
    assert trigger > ref


def test_dst_boundary_day_london():
    """London 2025-03-30 is spring-forward; trigger must still produce a valid UTC datetime."""
    s = WuDailyScheduler()
    london = cities_by_name["London"]
    ref = datetime(2025, 3, 30, 5, 0, tzinfo=timezone.utc)  # Pre-trigger
    trigger = s.next_trigger_utc(london, reference_utc=ref)
    # Must not raise and must be valid datetime
    assert isinstance(trigger, datetime)
    assert trigger.tzinfo is not None


def test_zero_coverage_cities_get_valid_trigger():
    """Previously zero-coverage cities (Asia/Oceania) all return valid triggers now."""
    s = WuDailyScheduler()
    now = datetime(2025, 7, 15, 0, 0, tzinfo=timezone.utc)
    for name in ["Auckland", "Beijing", "Busan", "Chengdu", "Chongqing",
                 "Jakarta", "Kuala Lumpur", "Singapore", "Taipei", "Wuhan"]:
        city = cities_by_name.get(name)
        assert city is not None, f"{name} missing from cities_by_name"
        trigger = s.next_trigger_utc(city, reference_utc=now)
        assert isinstance(trigger, datetime)


def test_should_collect_now_within_window():
    """should_collect_now returns True when now is within +-60 min of the trigger."""
    s = WuDailyScheduler()
    nyc = cities_by_name["NYC"]
    target_day = date(2025, 7, 15)
    trigger_utc = s.trigger_for_date(nyc, target_day)
    # Exactly at the trigger: True
    assert s.should_collect_now(nyc, now_utc=trigger_utc) is True
    # 30 min before: True (within window)
    assert s.should_collect_now(nyc, now_utc=trigger_utc - timedelta(minutes=30)) is True
    # 2 hours before: False
    assert s.should_collect_now(nyc, now_utc=trigger_utc - timedelta(hours=2)) is False


def test_dispatch_returns_cities_in_their_window():
    """dispatch_wu_daily_collection returns cities whose trigger is near now_utc.

    Over a full 24-hour walk every city must fire at least once (window coverage).
    """
    s = WuDailyScheduler()
    fired_cities: set[str] = set()
    base = datetime(2025, 7, 15, 0, 0, tzinfo=timezone.utc)
    for hour in range(24):
        now = base + timedelta(hours=hour)
        for name in dispatch_wu_daily_collection(s, now_utc=now):
            fired_cities.add(name)
    expected = set(cities_by_name.keys())
    missing = expected - fired_cities
    assert not missing, f"Cities never fired over 24h: {sorted(missing)}"


def test_run_wu_daily_dispatch_imports_resolve(monkeypatch):
    """Antibody: run_wu_daily_dispatch must not raise ImportError on lazy imports.

    Codex PR #166 thread PRRT_kwDOR0ZtZc6C1RTm: the original implementation
    imported append_daily_obs_for_city which does not exist in
    src.data.daily_obs_append — causing every hourly tick to fail silently.
    This test stubs the DB + append_wu_city so the full import path executes
    without real I/O, verifying the imports resolve correctly.
    """
    from unittest.mock import MagicMock

    import src.data.wu_scheduler as wu_sched

    # Stub get_world_connection so no real DB is needed
    monkeypatch.setattr(
        "src.state.db.get_world_connection",
        lambda: MagicMock(),
        raising=False,
    )
    # Stub append_wu_city so no real HTTP calls are made
    monkeypatch.setattr(
        "src.data.daily_obs_append.append_wu_city",
        lambda *a, **k: {"inserted": 0, "guard_rejected": 0, "fetch_errors": 0, "missing_from_api": 0},
        raising=False,
    )
    # Stub dispatch to return empty — exits before per-city loop; import still executes
    monkeypatch.setattr(wu_sched, "dispatch_wu_daily_collection", lambda *a, **k: [])
    # Must not raise ImportError or AttributeError
    wu_sched.run_wu_daily_dispatch()


def test_main_wu_daily_job_uses_scheduler_not_fixed_cron():
    """WU daily collection must use the per-city WuDailyScheduler gate, NOT a fixed hour=12 cron.

    UPDATED 2026-06-08 (system_decomposition_plan §8 Step 4): the WU daily job was a verified
    duplicate and was REMOVED from src/main (the order daemon). Ownership now lives SOLELY in
    data-ingest (ingest_main.py: ingest_k2_daily_obs -> daily_obs_append.daily_tick), which gates
    per-city via WuDailyScheduler.should_collect_now on an hourly (minute=0) cron — never a fixed
    noon cron. So the invariant moves with the job:
      - src/main must NO LONGER register a wu_daily job (the dedup removed it), and
      - the surviving data-ingest owner must NOT use a fixed hour=12 cron and MUST route through
        the WuDailyScheduler / daily_tick path.
    """
    import ast

    repo_root = Path(__file__).parent.parent
    main_path = repo_root / "src" / "main.py"
    ingest_path = repo_root / "src" / "ingest_main.py"
    if not main_path.exists() or not ingest_path.exists():
        pytest.skip("src/main.py or src/ingest_main.py not present in this worktree")

    ingest_content = ingest_path.read_text()

    # POST-DEDUP: src/main no longer registers the WU daily job at all. Detect REAL add_job id=
    # registrations via AST (not a substring scan — provenance comments mention "wu_daily" in prose).
    def _add_job_id_literals(path: Path) -> set[str]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        ids: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr == "add_job":
                for kw in node.keywords:
                    if kw.arg == "id" and isinstance(kw.value, ast.Constant) \
                            and isinstance(kw.value.value, str):
                        ids.add(kw.value.value)
        return ids

    assert "wu_daily" not in _add_job_id_literals(main_path), (
        "src/main.py still registers a wu_daily add_job — the §8 Step 4 dedup must remove it"
    )

    # The SURVIVING owner (data-ingest) must not use a fixed hour=12, minute=0 WU cron...
    fixed_noon_pattern = re.compile(r'add_job\([^)]*hour=12[^)]*minute=0', re.DOTALL)
    forbidden = fixed_noon_pattern.search(ingest_content)
    assert not forbidden, (
        f"data-ingest has a fixed hour=12, minute=0 WU daily cron: "
        f"{forbidden.group() if forbidden else ''}"
    )
    # ...and MUST route WU collection through the per-city scheduler / canonical daily_tick path.
    assert (
        "daily_tick" in ingest_content
        or "WuDailyScheduler" in ingest_content
        or "should_collect_now" in ingest_content
    ), "data-ingest does not route WU daily collection through the K2 scheduler/daily_tick path"

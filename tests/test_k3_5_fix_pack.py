"""K3.5 fix-pack regression tests.

Pins the 5 critic-identified blockers so they cannot silently regress.

Fix 1: _wu_daily_dispatch passes City objects, not city name strings
Fix 2: backfill computes is_missing_local_hour via _is_missing_local_hour
Fix 3: backfill local_time is target_date at peak hour, not fetch_utc.astimezone
Fix 4: backfill has --dry-run flag
Fix 5: ingestion_guard logs availability_fact on every rejection
"""
from __future__ import annotations

import inspect
from pathlib import Path


# ---------------------------------------------------------------------------
# Fix 1
# ---------------------------------------------------------------------------

def test_main_wu_daily_dispatch_passes_city_objects_not_names():
    """_wu_daily_dispatch must convert city names to City objects before calling collect_daily_highs."""
    from src import main
    source = inspect.getsource(main._wu_daily_dispatch)
    # Old broken call used city_names= kwarg which does not exist on collect_daily_highs
    assert "city_names=" not in source, "_wu_daily_dispatch still uses broken city_names= kwarg"
    # Must now use cities= kwarg (passing City objects)
    assert "cities=" in source, "_wu_daily_dispatch must pass cities= kwarg to collect_daily_highs"


# ---------------------------------------------------------------------------
# Fix 3 (checked before Fix 2 because Fix 2 depends on Fix 3 being correct)
# ---------------------------------------------------------------------------

def test_backfill_local_time_is_not_fetch_utc_astimezone():
    """backfill_wu_daily_all must NOT set local_time = fetch_utc.astimezone(tz) directly.

    That pattern embeds the script's runtime clock into historical atom provenance.
    The correct pattern uses target_date + peak_hour to build a semantically correct
    local_time for the historical observation.
    """
    script = Path(__file__).parent.parent / "scripts" / "backfill_wu_daily_all.py"
    content = script.read_text()
    assert "local_time = fetch_utc.astimezone(tz)" not in content, (
        "backfill_wu_daily_all still sets local_time = fetch_utc.astimezone(tz) — "
        "this embeds script runtime into historical atom provenance"
    )
    # Positive check: must build local_time from target_date + peak_hour
    assert "datetime(" in content and "_peak_h" in content, (
        "backfill_wu_daily_all must construct local_time from target_date + peak_hour"
    )


# ---------------------------------------------------------------------------
# Fix 2
# ---------------------------------------------------------------------------

def test_backfill_uses_is_missing_local_hour():
    """backfill_wu_daily_all must call _is_missing_local_hour instead of hardcoding False."""
    script = Path(__file__).parent.parent / "scripts" / "backfill_wu_daily_all.py"
    content = script.read_text()
    assert "is_missing_local_hour=False" not in content, (
        "backfill_wu_daily_all still hardcodes is_missing_local_hour=False"
    )
    assert "_is_missing" in content, (
        "backfill_wu_daily_all must import and call _is_missing_local_hour"
    )


# ---------------------------------------------------------------------------
# Fix 4
# ---------------------------------------------------------------------------

def test_backfill_has_dry_run_flag():
    """backfill_wu_daily_all accepts --dry-run without error."""
    script = Path(__file__).parent.parent / "scripts" / "backfill_wu_daily_all.py"
    content = script.read_text()
    assert "--dry-run" in content, "backfill_wu_daily_all missing --dry-run argparse flag"
    assert "dry_run" in content, "backfill_wu_daily_all missing dry_run variable"


# ---------------------------------------------------------------------------
# Fix 5
# ---------------------------------------------------------------------------

def test_ingestion_guard_logs_availability_fact_on_rejection():
    """Guard physical-bounds rejection must insert a row into availability_fact."""
    import pytest
    from src.state.db import get_world_connection, init_schema
    from src.data.ingestion_guard import IngestionGuard, PhysicalBoundsViolation

    conn = get_world_connection()
    init_schema(conn)

    guard = IngestionGuard()
    before = conn.execute("SELECT COUNT(*) FROM availability_fact").fetchone()[0]

    # -200.0u00b0F is below every lat-band lower bound, so Layer 2 always fires
    with pytest.raises(PhysicalBoundsViolation):
        guard.check_physical_bounds("NYC", -200.0, 6)

    after = conn.execute("SELECT COUNT(*) FROM availability_fact").fetchone()[0]
    assert after == before + 1, (
        f"availability_fact row not written on guard rejection: before={before} after={after}"
    )

from datetime import datetime, timedelta, timezone
import pytest
from src.contracts.expiring_assumption import ExpiringAssumption

def test_expiring_assumption_valid_window():
    now = datetime.now(timezone.utc)
    assum = ExpiringAssumption(
        value=1.5,
        fallback=1.0,
        last_verified_at=now,
        max_lifespan_days=30,
        kill_switch_action="revert_to_fallback",
        semantic_version="v2",
        owner="test",
        verified_by="unit_test",
        verification_source="manual"
    )
    assert assum.is_valid(now + timedelta(days=29))
    assert assum.active_value == 1.5

def test_expiring_assumption_fallback():
    now = datetime.now(timezone.utc)
    assum = ExpiringAssumption(
        value=1.5,
        fallback=1.0,
        last_verified_at=now - timedelta(days=40),
        max_lifespan_days=30,
        kill_switch_action="revert_to_fallback",
        semantic_version="v2",
        owner="test",
        verified_by="unit_test",
        verification_source="manual"
    )
    assert not assum.is_valid(now)
    assert assum.active_value == 1.0

def test_expiring_assumption_halt():
    now = datetime.now(timezone.utc)
    assum = ExpiringAssumption(
        value=1.5,
        fallback=1.0,
        last_verified_at=now - timedelta(days=40),
        max_lifespan_days=30,
        kill_switch_action="halt_trading",
        semantic_version="v2",
        owner="test",
        verified_by="unit_test",
        verification_source="manual"
    )
    assert not assum.is_valid(now)
    with pytest.raises(RuntimeError, match="ExpiringAssumption died"):
        _ = assum.active_value

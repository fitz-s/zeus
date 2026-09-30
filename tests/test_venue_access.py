# Created: 2026-09-29
# Last reused/audited: 2026-09-29
# Authority basis: 2026-09-29 14:32-17:03Z geoblock window; src/control/venue_access.py
"""Host venue order-access state: one owner of the geoblock fact."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.control import venue_access as va

T0 = datetime(2026, 9, 29, 14, 32, 55, tzinfo=timezone.utc)
DETAIL = (
    "PolyApiException[status_code=403, error_message={'error': 'Trading "
    "restricted in your region, please refer to available regions - "
    "https://docs.polymarket.com/developers/CLOB/geoblock'}]"
)


@pytest.fixture
def path(tmp_path, monkeypatch):
    monkeypatch.setattr(va, "egress_evidence", lambda host="clob.polymarket.com": {"interface": "utun10"})
    return tmp_path / "venue-access.json"


def test_absent_state_is_open(path):
    assert va.entry_block_reason(now=T0, path=path) is None
    assert va.claim_entry_submit(now=T0, path=path) is None


def test_first_geoblock_blocks_entries_until_the_probe_is_due(path):
    va.record_geoblock(DETAIL, now=T0, path=path)

    reason = va.entry_block_reason(now=T0 + timedelta(seconds=59), path=path)
    assert reason is not None and reason.startswith("VENUE_ACCESS_GEOBLOCKED:")
    assert va.claim_entry_submit(now=T0 + timedelta(seconds=59), path=path) == reason
    state = va.summary(path=path)
    assert state["state"] == "GEOBLOCKED"
    assert state["egress"] == {"interface": "utun10"}


def test_one_probe_per_interval_with_backoff_to_five_minutes(path):
    at = T0
    for expected in (60, 120, 240, 300, 300):
        va.record_geoblock(DETAIL, now=at, path=path)
        due = at + timedelta(seconds=expected)
        assert va.claim_entry_submit(now=due - timedelta(seconds=1), path=path) is not None
        assert va.claim_entry_submit(now=due, path=path) is None  # the probe
        assert va.summary(path=path)["state"] == "PROBING"
        # A second caller in the same interval does not get another probe.
        assert va.claim_entry_submit(now=due, path=path) is not None
        at = due


def test_accepted_order_reopens(path):
    va.record_geoblock(DETAIL, now=T0, path=path)
    va.claim_entry_submit(now=T0 + timedelta(seconds=60), path=path)

    va.record_order_accepted(now=T0 + timedelta(seconds=61), path=path)

    assert va.summary(path=path)["state"] == "OPEN"
    assert va.claim_entry_submit(now=T0 + timedelta(seconds=62), path=path) is None
    # Backoff restarts from the first rung after reopening.
    va.record_geoblock(DETAIL, now=T0 + timedelta(seconds=70), path=path)
    assert va.summary(path=path)["consecutive_geoblocks"] == 1


def test_corrupt_state_reads_open(path):
    path.write_text("{not json")
    assert va.entry_block_reason(now=T0, path=path) is None


def test_both_reasons_are_registered_and_transient():
    from src.contracts.rejection_reasons import is_registered_rejection_reason
    from src.events.reactor import (
        TRANSIENT_MONEY_PATH_REASONS,
        _is_explicitly_transient_money_path_reason,
        _is_runtime_authority_retry_reason,
    )

    for reason in ("venue_rejected_geoblock_403", "VENUE_ACCESS_GEOBLOCKED:since=x"):
        assert is_registered_rejection_reason(reason)
        assert _is_explicitly_transient_money_path_reason(reason)
        assert _is_runtime_authority_retry_reason(reason)
    assert "VENUE_ACCESS_GEOBLOCKED" in TRANSIENT_MONEY_PATH_REASONS

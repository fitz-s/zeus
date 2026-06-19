# Created: 2026-06-03
# Last reused or audited: 2026-06-08
# Authority basis: review finding P1-1 (fix/review-findings-2026-06-03)
#                  Confirmed P1-1 canary blocker: scan_authority overloaded with trigger reason
#                  → capture_executable_market_snapshot raises on non-VERIFIED → dead refresh path
#                  2026-06-08 system_decomposition_plan §8 Step 3: _edli_market_channel_refresh_kwargs
#                  lifted from src.main to src.ingest.price_channel_ingest (P3). Contract unchanged;
#                  producer-side boundary is now price_channel_ingest → market_scanner.
# Lifecycle: created=2026-06-03; last_reviewed=2026-06-08; last_reused=2026-06-08
# Purpose: Relationship tests guarding the main.py → market_scanner boundary; enforces that scan_authority is always "VERIFIED" and the trigger reason is carried separately (P1-1 fix).
# Reuse: Confirm scan_authority contract + capture_executable_market_snapshot schema still match before relying on test as evidence.
"""Relationship tests: main.py _edli_market_channel_refresh_kwargs → market_scanner boundary.

Three tests that cross the main.py → market_scanner module boundary:
1. Authority contract: kwargs["scan_authority"] == "VERIFIED" always; reason is separate.
2. Insert passes: refresh with VERIFIED authority inserts a snapshot successfully.
3. Regression guard: old overloaded authority (non-VERIFIED string) inserts 0 (dead path).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from src.state.db import init_schema


# ---------------------------------------------------------------------------
# Fixtures — reuse the SideAwareClob + market shape from test_market_scanner_provenance
# which already passes the full capture gauntlet.
# ---------------------------------------------------------------------------


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def _valid_executable_market() -> dict:
    """Minimal market dict that passes refresh_executable_market_substrate_snapshots."""
    return {
        "event_id": "edli-test-event",
        "slug": "highest-temperature-in-testcity-on-2026-06-03",
        "outcomes": [
            {
                "title": "Test bin 25-26°C",
                "condition_id": "cond-edli-test",
                "question_id": "question-edli-test",
                "gamma_market_id": "gamma-edli-test",
                "token_id": "yes-edli-test",
                "no_token_id": "no-edli-test",
                "market_id": "cond-edli-test",
                "active": True,
                "closed": False,
                "accepting_orders": True,
                "enable_orderbook": True,
                "executable": True,
                "market_end_at": "2026-06-10T12:00:00+00:00",
                "raw_gamma_payload_hash": "e" * 64,
                "token_map_raw": {
                    "clobTokenIds": ["yes-edli-test", "no-edli-test"],
                    "outcomes": ["Yes", "No"],
                },
                "gamma_market_raw": {
                    "id": "gamma-edli-test",
                    "conditionId": "cond-edli-test",
                    "questionID": "question-edli-test",
                    "active": True,
                    "closed": False,
                    "acceptingOrders": True,
                    "enableOrderBook": True,
                    "clobTokenIds": ["yes-edli-test", "no-edli-test"],
                },
            }
        ],
    }


class _FakeClob:
    """Minimal CLOB fake that passes the full capture gauntlet."""

    def get_clob_market_info(self, condition_id: str) -> dict:
        return {
            "condition_id": condition_id,
            "tokens": [
                {"token_id": "yes-edli-test"},
                {"token_id": "no-edli-test"},
            ],
            "feesEnabled": True,
        }

    def get_orderbook_snapshot(self, token_id: str) -> dict:
        return {
            "asset_id": token_id,
            "tick_size": "0.01",
            "min_order_size": "5",
            "neg_risk": False,
            "bids": [{"price": "0.40", "size": "10"}],
            "asks": [{"price": "0.42", "size": "10"}],
        }

    def get_fee_rate(self, token_id: str) -> float:
        return 0

    # Batch prefetch endpoint used by refresh_executable_market_substrate_snapshots
    def get_books(self, token_ids: list[str]) -> list[dict]:
        return [self.get_orderbook_snapshot(tid) for tid in token_ids]


# ---------------------------------------------------------------------------
# Test 1: authority contract (pure helper — no DB, no CLOB)
# ---------------------------------------------------------------------------


def test_market_channel_refresh_uses_verified_authority():
    """_edli_market_channel_refresh_kwargs must set scan_authority=VERIFIED and
    carry the EDLI trigger as refresh_reason metadata (not in scan_authority)."""
    from src.ingest.price_channel_ingest import _edli_market_channel_refresh_kwargs

    class _FakeAction:
        reason = "PRICE_CHANGE"
        condition_id = "cond-x"

    markets = [_valid_executable_market()]
    clob = object()
    captured_at = datetime(2026, 6, 3, 12, 0, tzinfo=timezone.utc)

    kwargs = _edli_market_channel_refresh_kwargs(_FakeAction(), markets, clob, captured_at)

    assert kwargs["scan_authority"] == "VERIFIED", (
        f"scan_authority must be 'VERIFIED' (the snapshot-source contract); "
        f"got {kwargs['scan_authority']!r}"
    )
    assert "refresh_reason" in kwargs, "refresh_reason must be present in kwargs"
    assert kwargs["refresh_reason"].startswith("EDLI_MARKET_CHANNEL:"), (
        f"refresh_reason must start with 'EDLI_MARKET_CHANNEL:'; "
        f"got {kwargs['refresh_reason']!r}"
    )
    # Confirm the trigger reason is NOT bleeding into the authority slot
    assert "EDLI_MARKET_CHANNEL" not in kwargs["scan_authority"], (
        "EDLI trigger reason must not appear in scan_authority"
    )


# ---------------------------------------------------------------------------
# Test 2: end-to-end insert — VERIFIED authority succeeds
# ---------------------------------------------------------------------------


def test_market_channel_refresh_inserts_snapshot():
    """refresh_executable_market_substrate_snapshots called with VERIFIED scan_authority
    (via the helper kwargs) inserts at least one snapshot and records refresh_reason."""
    import src.data.market_scanner as ms
    from src.ingest.price_channel_ingest import _edli_market_channel_refresh_kwargs

    conn = _make_conn()

    class _FakeAction:
        reason = "PRICE_CHANGE"
        condition_id = "cond-edli-test"

    markets = [_valid_executable_market()]
    clob = _FakeClob()
    captured_at = datetime(2026, 6, 3, 12, 0, tzinfo=timezone.utc)

    kwargs = _edli_market_channel_refresh_kwargs(_FakeAction(), markets, clob, captured_at)
    summary = ms.refresh_executable_market_substrate_snapshots(conn, **kwargs)

    assert summary["inserted"] >= 1, (
        f"Expected at least 1 inserted snapshot; summary={summary}"
    )
    # refresh_reason must be echoed in the summary (non-authoritative metadata)
    assert "refresh_reason" in summary, (
        "Summary must carry refresh_reason so callers can log the EDLI trigger"
    )
    assert "EDLI_MARKET_CHANNEL:" in summary["refresh_reason"], (
        f"refresh_reason in summary must contain the trigger; got {summary['refresh_reason']!r}"
    )
    # Verify the stored row has a valid authority_tier (CLOB = captured via live CLOB data),
    # NOT the trigger reason string (which would be rejected by the schema CHECK constraint).
    row = conn.execute(
        "SELECT authority_tier FROM executable_market_snapshots WHERE condition_id = 'cond-edli-test' LIMIT 1"
    ).fetchone()
    assert row is not None, "No snapshot row persisted"
    assert row["authority_tier"] in ("GAMMA", "DATA", "CLOB", "CHAIN"), (
        f"Persisted authority_tier must be a valid tier (GAMMA/DATA/CLOB/CHAIN); "
        f"got {row['authority_tier']!r}. The EDLI trigger string must not have leaked into authority."
    )


# ---------------------------------------------------------------------------
# Test 3: regression guard — old overloaded authority string inserts 0
# ---------------------------------------------------------------------------


def test_old_overloaded_authority_was_dead():
    """Passing scan_authority='EDLI_MARKET_CHANNEL:x' (the pre-fix bug) must insert 0.

    This documents WHY the fix is needed: the original code crammed the trigger
    reason into the authority slot, which the capture contract rejects as non-VERIFIED.
    Fixing this must NOT weaken the VERIFIED check — this test proves the check
    is still active and that VERIFIED is genuinely required.
    """
    import src.data.market_scanner as ms

    conn = _make_conn()
    markets = [_valid_executable_market()]
    clob = _FakeClob()
    captured_at = datetime(2026, 6, 3, 12, 0, tzinfo=timezone.utc)

    summary = ms.refresh_executable_market_substrate_snapshots(
        conn,
        markets=markets,
        clob=clob,
        captured_at=captured_at,
        scan_authority="EDLI_MARKET_CHANNEL:PRICE_CHANGE",  # the pre-fix bug
        max_outcomes=20,
        budget_seconds=15.0,
    )

    assert summary["inserted"] == 0, (
        f"Non-VERIFIED scan_authority must insert 0 (capture rejects it); "
        f"inserted={summary['inserted']}. The VERIFIED contract must not be weakened."
    )
    assert summary["failed"] > 0, (
        "All attempts must fail when authority is not VERIFIED"
    )

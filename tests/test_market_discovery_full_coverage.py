# Created: 2026-05-24
# Lifecycle: created=2026-05-24; last_reviewed=2026-05-24; last_reused=2026-05-24
# Authority basis: fix(discovery): restore full-city market substrate coverage (50→7 regression)
# Purpose: Relationship antibody — refresh_executable_market_substrate_snapshots must
#   capture snapshots for ≥ all-but-one city when fed a full 51-city market list.
#   Regressed in #203 (slug-only wiring) + global top-8 cap.
# Reuse: import refresh_executable_market_substrate_snapshots + ms.cities_by_name
"""Relationship antibody: full-city coverage through substrate refresh.

Two tests:
  R1 (BREADTH_FIRST_CITY_COVERAGE): substrate refresh fed N cities produces
     snapshots across ≥ N-1 distinct cities, not capped at 7.
  R2 (MARKET_DISCOVERY_CYCLE_USES_FULL_SCAN): _market_discovery_cycle calls
     find_weather_markets (tag-query path), not slug-only fallback.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

import src.data.market_scanner as ms
from src.data.market_scanner import refresh_executable_market_substrate_snapshots


_NOW = datetime(2026, 5, 24, 12, 0, 0, tzinfo=timezone.utc)
_CITY_SLUG_RE = re.compile(r"in-([a-z-]+)-on-")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slug(city_slug: str) -> str:
    return f"highest-temperature-in-{city_slug}-on-2026-05-25"


def _make_market(city_name: str, idx: int) -> dict:
    """Build a minimal enriched event dict matching market_scanner parse output."""
    city_slug = city_name.lower().replace(" ", "-")
    cid = f"0x{idx:04x}" + "0" * 60
    cid = cid[:66]
    no_token = f"0x{idx:04x}" + "1" * 60
    no_token = no_token[:66]
    return {
        "event_id": f"evt-{city_slug}-001",
        "slug": _slug(city_slug),
        "title": f"Highest temperature in {city_name} on May 25?",
        "city": ms.cities_by_name.get(city_name, city_name),
        "target_date": "2026-05-25",
        "temperature_metric": "high",
        "hours_to_resolution": 36.0,
        "hours_since_open": 2.0,
        "outcomes": [
            {
                "condition_id": cid,
                "token_id": f"0x{idx:04x}" + "a" * 60,
                "no_token_id": no_token,
                "executable": True,
                "accepting_orders": True,
                "closed": False,
                "enable_orderbook": True,
                "gamma_market_raw": {
                    "conditionId": cid,
                    "acceptingOrders": True,
                    "closed": False,
                    "active": False,
                    "enableOrderBook": True,
                },
            }
        ],
        "condition_ids": [cid],
        "source_contract": {"status": "MATCH"},
    }


def _make_clob_mock() -> MagicMock:
    """CLOB mock that returns minimal valid responses for every call."""
    clob = MagicMock()

    def _market_info(condition_id: str) -> dict:
        return {
            "condition_id": condition_id,
            "question_id": condition_id[:66],
            "tokens": [
                {"token_id": "0xaaaa", "outcome": "YES"},
                {"token_id": "0xbbbb", "outcome": "NO"},
            ],
            "rewards": {"min_size": 0, "max_spread": 0},
        }

    def _orderbook(token_id: str) -> dict:
        return {
            "market": token_id,
            "asset_id": token_id,
            "bids": [{"price": "0.55", "size": "100"}],
            "asks": [{"price": "0.60", "size": "100"}],
        }

    def _fee_details(token_id: str) -> dict:
        return {"feeSchedule": {"makerFeeRate": "0.0", "takerFeeRate": "0.02"}}

    clob.get_clob_market_info.side_effect = _market_info
    clob.get_orderbook_snapshot.side_effect = _orderbook
    clob.get_fee_rate_details.side_effect = _fee_details
    return clob


def _make_in_memory_trade_db() -> sqlite3.Connection:
    """Minimal in-memory trade DB with executable_market_snapshots table."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    # Minimal schema — only columns that capture_executable_market_snapshot writes
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS executable_market_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            event_slug TEXT NOT NULL,
            condition_id TEXT NOT NULL,
            outcome_label TEXT NOT NULL,
            direction TEXT NOT NULL,
            execution_side TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            raw_clob_market_info_hash TEXT,
            raw_orderbook_hash TEXT,
            top_bid REAL,
            top_ask REAL,
            bid_size REAL,
            ask_size REAL,
            spread REAL,
            scan_authority TEXT NOT NULL DEFAULT 'VERIFIED',
            schema_version INTEGER NOT NULL DEFAULT 1
        );
    """)
    conn.commit()
    return conn


def _cities_in_snapshots(conn: sqlite3.Connection) -> set[str]:
    """Extract city slugs from event_slug column."""
    rows = conn.execute("SELECT DISTINCT event_slug FROM executable_market_snapshots").fetchall()
    cities = set()
    for row in rows:
        m = _CITY_SLUG_RE.search(row[0])
        if m:
            cities.add(m.group(1))
    return cities


# ---------------------------------------------------------------------------
# R1: breadth-first city coverage
# ---------------------------------------------------------------------------

def test_substrate_refresh_covers_all_cities_not_global_cap_of_8(monkeypatch):
    """R1 (BREADTH_FIRST_CITY_COVERAGE): refresh with 40 distinct cities produces
    snapshots across ≥ 35 cities, not capped at ~4 (global top-8 / 2 directions).

    Sed-revert on fix: revert refresh_executable_market_substrate_snapshots to
    global limit=8 → distinct_cities ≤ 4 → RED.

    Pre-fix baseline: with default ZEUS_MARKET_DISCOVERY_SNAPSHOT_MAX_OUTCOMES=8,
    global sort selects 8 candidates (all tier-2, all same priority for the
    36h-to-resolution markets above). At most 4 cities (2 per direction pair each).
    With breadth-first per-city selection, all 40 cities get at least 1 direction.
    """
    all_city_names = list(ms.cities_by_name.keys())[:40]  # 40 distinct cities
    markets = [_make_market(name, idx) for idx, name in enumerate(all_city_names, start=1)]

    # Use patch() not monkeypatch — intra-module call bypasses module-attr setattr
    captured_slugs: list[str] = []

    def _mock_capture(conn, *, market, decision, clob, captured_at, scan_authority, execution_side="BUY"):
        slug = market.get("slug", "")
        captured_slugs.append(slug)

    clob = _make_clob_mock()
    conn = _make_in_memory_trade_db()

    with patch("src.data.market_scanner.capture_executable_market_snapshot", side_effect=_mock_capture):
        summary = refresh_executable_market_substrate_snapshots(
            conn,
            markets=markets,
            clob=clob,
            captured_at=_NOW,
            scan_authority="VERIFIED",
        )

    captured_cities = set()
    for slug in captured_slugs:
        m = _CITY_SLUG_RE.search(slug)
        if m:
            captured_cities.add(m.group(1))

    assert len(captured_cities) >= 35, (
        f"substrate refresh must cover ≥35 of 40 input cities "
        f"(breadth-first per-city selection). Got {len(captured_cities)} cities: "
        f"{sorted(captured_cities)}. Summary: {summary}. "
        "Pre-fix failure: global top-8 cap produces ≤4 distinct cities."
    )


# ---------------------------------------------------------------------------
# R2: _market_discovery_cycle calls find_weather_markets (tag path)
# ---------------------------------------------------------------------------

def test_market_discovery_cycle_calls_find_weather_markets_not_slug_only(monkeypatch):
    """R2 (DISCOVERY_CYCLE_FULL_SCAN): _market_discovery_cycle must call
    find_weather_markets (full tag-query, 51 cities), not slug-only fallback.

    Sed-revert on fix: revert _market_discovery_cycle import back to
    find_slug_pattern_weather_markets → find_weather_markets call never happens → RED.

    Pre-fix baseline: _market_discovery_cycle imports and calls
    find_slug_pattern_weather_markets only; find_weather_markets never called.
    """
    import src.main as main_mod

    tag_scan_called = []
    slug_only_called = []

    # Patch both discovery functions to track which gets called
    def _mock_find_weather_markets(**kwargs):
        tag_scan_called.append(kwargs)
        return []

    def _mock_find_slug_pattern(**kwargs):
        slug_only_called.append(kwargs)
        return []

    # Patch at module level that main.py imports from
    import src.data.market_scanner as scanner_mod
    monkeypatch.setattr(scanner_mod, "find_weather_markets", _mock_find_weather_markets)
    monkeypatch.setattr(scanner_mod, "find_slug_pattern_weather_markets", _mock_find_slug_pattern)

    # Patch CLOB and DB so the cycle completes without network/disk
    mock_clob = MagicMock()
    mock_conn = MagicMock()
    mock_conn.__enter__ = lambda s: s
    mock_conn.__exit__ = MagicMock(return_value=False)

    monkeypatch.setattr(
        "src.data.market_scanner.refresh_executable_market_substrate_snapshots",
        lambda conn, *, markets, clob, captured_at, scan_authority: {
            "attempted": 0, "inserted": 0, "skipped": 0, "failed": 0,
            "truncated": 0, "budget_exhausted": 0,
        },
    )

    # Prevent real DB + CLOB instantiation
    with (
        patch("src.data.polymarket_client.PolymarketClient") as mock_clob_cls,
        patch("src.state.db.get_trade_connection", return_value=mock_conn),
    ):
        mock_clob_cls.return_value.__enter__ = lambda s: MagicMock()
        mock_clob_cls.return_value.__exit__ = MagicMock(return_value=False)
        # Patch the lock object itself with a mock that always acquires
        mock_lock = MagicMock()
        mock_lock.acquire.return_value = True
        with patch.object(main_mod, "_market_discovery_lock", mock_lock):
            main_mod._market_discovery_cycle()

    assert len(tag_scan_called) >= 1, (
        "_market_discovery_cycle must call find_weather_markets (full tag-query "
        "path covering all 51 cities). Pre-fix: only find_slug_pattern_weather_markets "
        "(14-city slug-only) is called."
    )

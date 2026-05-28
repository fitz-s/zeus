# Created: 2026-05-24
# Lifecycle: created=2026-05-24; last_reviewed=2026-05-24; last_reused=2026-05-24
# Authority basis: fix(discovery): restore full-city market substrate coverage (50→7 regression)
# Purpose: Relationship antibody — refresh_executable_market_substrate_snapshots must
#   (R1) cap per CITY not per slug — a city with high+low slugs is 1 city, not 2;
#   (R2) _market_discovery_cycle calls find_weather_markets (tag path), not slug-only;
#   (HANG) a full 50-city scan with CLOB latency must complete under wall-clock budget.
# Reuse: import refresh_executable_market_substrate_snapshots + ms.cities_by_name
"""Relationship antibodies: city-scoped per-city cap + hang-proof budget gate.

Three tests:
  R1 (PER_CITY_CAP_NOT_PER_SLUG): cities with multiple slugs (high+low) are capped
     per-city, not per-slug.  40 cities x 2 slugs -> >=35 distinct cities captured,
     each with <= per_city_limit snapshots.
  R2 (MARKET_DISCOVERY_CYCLE_USES_FULL_SCAN): _market_discovery_cycle calls
     find_weather_markets (tag-query path), not slug-only fallback.
  HANG (CLOB_LATENCY_CANNOT_OVERRUN_BUDGET): simulated slow CLOB capture advances
     the clock and refresh_executable_market_substrate_snapshots stops at budget.
"""
from __future__ import annotations

import re
import sqlite3
import time
from collections import defaultdict
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import src.data.market_scanner as ms
from src.data.market_scanner import refresh_executable_market_substrate_snapshots

_NOW = datetime(2026, 5, 24, 12, 0, 0, tzinfo=timezone.utc)
_CITY_SLUG_RE = re.compile(r"in-([a-z-]+)-on-")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slug(city_slug: str, metric: str, target_date: str = "2026-05-25") -> str:
    return f"{metric}-temperature-in-{city_slug}-on-{target_date}"


def _make_market(
    city_name: str,
    idx: int,
    metric: str = "highest",
    target_date: str = "2026-05-25",
) -> dict:
    """Build an enriched event dict matching market_scanner parse output.

    Each (city_name, metric, target_date) triple gets a distinct slug and
    condition_id, so a single city_name can appear across multiple slugs.
    The ``city`` key carries the City object -- production ``_parse_event``
    sets this; tests must mirror it for city_key extraction to work correctly.

    The ``target_date`` axis is critical for the R1 antibody: pre-fix code that
    keys per-event (slug/event_id) would give N×per_city_limit snapshots when a
    city has N (metric, date) combinations.  Post-fix per-city keying caps the
    total at per_city_limit regardless of slug count.
    """
    city_slug = city_name.lower().replace(" ", "-")
    # cid_int encodes (city_ordinal * 1000 + date_ordinal * 10 + metric_ordinal)
    # to guarantee distinct cids across (city, date, metric) triples.
    metric_ord = 0 if metric == "highest" else 1
    # Use a stable date → ordinal mapping so tests are deterministic.
    date_ord = int(target_date.replace("-", "")) % 1000
    cid_int = (idx * 1000 + date_ord * 10 + metric_ord) % (2**16)
    cid = f"0x{cid_int:04x}" + "0" * 60
    cid = cid[:66]
    no_token = f"0x{cid_int:04x}" + "1" * 60
    no_token = no_token[:66]
    return {
        "event_id": f"evt-{city_slug}-{metric}-{target_date}",
        "slug": _slug(city_slug, metric, target_date),
        "title": f"{metric.capitalize()} temperature in {city_name} on {target_date}?",
        # City object -- mirrors _parse_event output; city_key extraction uses .name
        "city": ms.cities_by_name.get(city_name, city_name),
        "target_date": target_date,
        "temperature_metric": metric,
        "hours_to_resolution": 36.0,
        "hours_since_open": 2.0,
        "outcomes": [
            {
                "condition_id": cid,
                "token_id": f"0x{cid_int:04x}" + "a" * 60,
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
    """CLOB mock -- returns minimal valid responses instantly."""
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


def _city_name_from_slug(slug: str) -> str | None:
    m = _CITY_SLUG_RE.search(slug)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# R1: per-CITY cap (not per-slug)
# ---------------------------------------------------------------------------

def test_per_city_cap_applies_across_slugs_not_per_slug(monkeypatch):
    """R1 (PER_CITY_CAP_NOT_PER_SLUG): cities with multiple slugs (high/low × 3
    dates) are capped per-city.  30 cities × 6 slugs each = 180 input markets.

    Uses max_outcomes=2 (tight per-city cap) to make the pre-fix failure
    concrete across BOTH the slug axis AND the event_id axis:
    - Each (city, metric, date) triple produces 2 candidate directions.
    - Pre-fix (city_key=slug or event_id): 6 buckets × 2 = 12 snapshots/city → OVER.
    - Post-fix (city_key=city.name): 1 bucket capped at 2 → PASSES.

    RED on pre-fix code: over_limit dict is non-empty (every multi-slug city exceeds 2).
    GREEN post-fix: no city exceeds 2, and >=25 distinct cities are covered.
    """
    TARGET_DATES = ["2026-05-25", "2026-05-26", "2026-05-27"]
    METRICS = ["highest", "lowest"]
    # 2 metrics × 3 dates = 6 slugs per city; each slug has 2 directions = 12 candidates/city
    CANDIDATES_PER_CITY_PRE_FIX = len(TARGET_DATES) * len(METRICS) * 2  # = 12

    all_city_names = list(ms.cities_by_name.keys())[:30]
    markets = []
    for idx, name in enumerate(all_city_names, start=1):
        for metric in METRICS:
            for tdate in TARGET_DATES:
                markets.append(_make_market(name, idx, metric=metric, target_date=tdate))

    per_city_limit = 2  # tight cap; pre-fix gives 12 per city

    captured_slugs: list[str] = []

    def _mock_capture(conn, *, market, decision, clob, captured_at, scan_authority, execution_side="BUY", **kwargs):
        captured_slugs.append(market.get("slug", ""))

    clob = _make_clob_mock()
    conn = _make_in_memory_trade_db()

    with patch("src.data.market_scanner.capture_executable_market_snapshot", side_effect=_mock_capture):
        summary = refresh_executable_market_substrate_snapshots(
            conn,
            markets=markets,
            clob=clob,
            captured_at=_NOW,
            scan_authority="VERIFIED",
            max_outcomes=per_city_limit,
        )

    # Group captured slugs by city name (extracted from slug pattern)
    city_snapshot_counts: dict[str, int] = defaultdict(int)
    for slug in captured_slugs:
        city = _city_name_from_slug(slug)
        if city:
            city_snapshot_counts[city] += 1

    distinct_cities = set(city_snapshot_counts.keys())

    # >=25 of 30 cities must be covered (breadth-first ensures coverage)
    assert len(distinct_cities) >= 25, (
        f"substrate refresh must cover >=25 of 30 input cities "
        f"(per-city breadth-first). Got {len(distinct_cities)} cities: "
        f"{sorted(distinct_cities)}. Summary: {summary}."
    )

    # No city may exceed per_city_limit snapshots (per-CITY cap, not per-slug or per-event).
    # Pre-fix: city_key=slug/event_id -> {CANDIDATES_PER_CITY_PRE_FIX} candidates/city -> FAILS.
    # Post-fix: city_key=city.name -> 1 bucket capped at 2 -> PASSES.
    over_limit = {c: n for c, n in city_snapshot_counts.items() if n > per_city_limit}
    assert not over_limit, (
        f"Per-city cap must be <={per_city_limit} snapshots per city. "
        f"Cities over limit: {over_limit}. "
        f"Pre-fix failure: city_key=slug/event_id gives up to "
        f"{CANDIDATES_PER_CITY_PRE_FIX} snapshots/city when {len(TARGET_DATES)} "
        f"dates × {len(METRICS)} metrics exist."
    )
    assert summary["selected_executable_city_count"] == 30
    assert summary["fresh_executable_city_count"] == 30
    assert summary["budget_truncated_city_count"] == 0
    assert summary["executable_substrate_coverage_status"] == "FULL"


# ---------------------------------------------------------------------------
# R2: _market_discovery_cycle calls find_weather_markets (tag path)
# ---------------------------------------------------------------------------

def test_market_discovery_cycle_calls_find_weather_markets_not_slug_only(monkeypatch):
    """R2 (DISCOVERY_CYCLE_FULL_SCAN): _market_discovery_cycle must call
    find_weather_markets (full tag-query, 51 cities), not slug-only fallback.

    Sed-revert on fix: revert _market_discovery_cycle import back to
    find_slug_pattern_weather_markets -> find_weather_markets call never happens -> RED.

    Pre-fix baseline: _market_discovery_cycle imports and calls
    find_slug_pattern_weather_markets only; find_weather_markets never called.
    """
    import src.main as main_mod

    tag_scan_called = []
    slug_only_called = []

    def _mock_find_weather_markets(**kwargs):
        tag_scan_called.append(kwargs)
        return []

    def _mock_find_slug_pattern(**kwargs):
        slug_only_called.append(kwargs)
        return []

    import src.data.market_scanner as scanner_mod
    monkeypatch.setattr(scanner_mod, "find_weather_markets", _mock_find_weather_markets)
    monkeypatch.setattr(scanner_mod, "find_slug_pattern_weather_markets", _mock_find_slug_pattern)

    monkeypatch.setattr(
        "src.data.market_scanner.refresh_executable_market_substrate_snapshots",
        lambda conn, *, markets, clob, captured_at, scan_authority: {
            "attempted": 0, "inserted": 0, "skipped": 0, "failed": 0,
            "truncated": 0, "budget_exhausted": 0,
        },
    )

    mock_conn = MagicMock()
    mock_conn.__enter__ = lambda s: s
    mock_conn.__exit__ = MagicMock(return_value=False)

    with (
        patch("src.data.polymarket_client.PolymarketClient") as mock_clob_cls,
        patch("src.state.db.get_trade_connection", return_value=mock_conn),
    ):
        mock_clob_cls.return_value.__enter__ = lambda s: MagicMock()
        mock_clob_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_lock = MagicMock()
        mock_lock.acquire.return_value = True
        with patch.object(main_mod, "_market_discovery_lock", mock_lock):
            main_mod._market_discovery_cycle()

    assert len(tag_scan_called) >= 1, (
        "_market_discovery_cycle must call find_weather_markets (full tag-query "
        "path covering all 51 cities). Pre-fix: only find_slug_pattern_weather_markets "
        "(14-city slug-only) is called."
    )


# ---------------------------------------------------------------------------
# HANG: CLOB latency cannot overrun wall-clock budget
# ---------------------------------------------------------------------------

def test_clob_latency_cannot_overrun_budget(monkeypatch):
    """HANG (CLOB_LATENCY_CANNOT_OVERRUN_BUDGET): simulated slow mock-capture
    advances the clock, and the full refresh must stop at budget.

    Design:
    - 10 cities x 1 slug each = 10 markets, each with 1 outcome.
    - per_city_limit=1, budget=10s (tight, forces early abort after ~3 captures).
    - Each mock_capture advances the monotonic clock by 3s.

    The budget gate (checked between each capture) aborts after ~3 captures
    regardless of CLOB latency. This proves the gate actually fires.

    Assertions:
    - elapsed < budget + one_extra_capture + slack (15s total)
    - budget_exhausted == 1
    - attempted < 10 (did not exhaust all 10 cities)

    This is RED before budget gate is wired: without budget_seconds, the refresh
    would attempt all 10 cities (30s of sleep). With budget_seconds=10, it stops
    after ~4 simulated captures (~12s) without sleeping in CI.
    """
    SLEEP_PER_CAPTURE = 3.0  # simulated seconds per mock capture call
    BUDGET_SECONDS = 10.0    # tight budget to force early abort
    MAX_ELAPSED_SECONDS = BUDGET_SECONDS + SLEEP_PER_CAPTURE + 2.0  # = 15s

    all_city_names = list(ms.cities_by_name.keys())[:10]
    markets = [_make_market(name, idx, metric="highest") for idx, name in enumerate(all_city_names, start=1)]

    capture_calls = []
    fake_now = 0.0

    def _fake_monotonic() -> float:
        return fake_now

    monkeypatch.setattr(ms.time, "monotonic", _fake_monotonic)

    def _slow_capture(conn, *, market, decision, clob, captured_at, scan_authority, execution_side="BUY", **kwargs):
        nonlocal fake_now
        fake_now += SLEEP_PER_CAPTURE
        capture_calls.append(market.get("slug", ""))

    clob = _make_clob_mock()
    conn = _make_in_memory_trade_db()

    t0 = ms.time.monotonic()
    with patch("src.data.market_scanner.capture_executable_market_snapshot", side_effect=_slow_capture):
        summary = refresh_executable_market_substrate_snapshots(
            conn,
            markets=markets,
            clob=clob,
            captured_at=_NOW,
            scan_authority="VERIFIED",
            budget_seconds=BUDGET_SECONDS,
            max_outcomes=1,
        )
    elapsed = ms.time.monotonic() - t0

    assert elapsed < MAX_ELAPSED_SECONDS, (
        f"refresh must complete in < {MAX_ELAPSED_SECONDS:.1f}s even with "
        f"{SLEEP_PER_CAPTURE}s/capture and {BUDGET_SECONDS}s budget. "
        f"Got {elapsed:.1f}s. budget_exhausted={summary.get('budget_exhausted')}. "
        f"captures={len(capture_calls)}."
    )

    assert summary.get("budget_exhausted") == 1, (
        f"budget_exhausted must be 1 when budget={BUDGET_SECONDS}s and each capture "
        f"takes {SLEEP_PER_CAPTURE}s. Got summary={summary}."
    )
    assert summary.get("budget_truncated_city_count", 0) > 0, (
        f"budget_truncated_city_count must count only cities not reached because "
        f"the budget gate tripped. Got summary={summary}."
    )

    # Must have attempted fewer than all 10 cities
    assert summary.get("attempted", 0) < 10, (
        f"Must abort before all 10 cities when budget={BUDGET_SECONDS}s. "
        f"attempted={summary.get('attempted')}, elapsed={elapsed:.1f}s."
    )
# ---------------------------------------------------------------------------
# P1-2: tag-fetch loop is bounded by wall-clock budget
# ---------------------------------------------------------------------------

def test_fetch_events_by_tags_stops_when_budget_exhausted(monkeypatch):
    """P1-2 (TAG_LOOP_BUDGET_BOUND): _fetch_events_by_tags must stop iterating
    TAG_SLUGS once the discovery budget is exhausted.

    Without the budget check at the top of the tag loop, 51 tags × up to 10
    pages × _gamma_get(timeout=15, retries=3) can block for many minutes.
    This test proves the budget gate fires inside the tag loop.

    Design:
    - Monkeypatch TAG_SLUGS to 10 slugs and _discovery_total_budget_seconds_from_env
      to 0.05s (50ms), so the budget expires after the first tag call.
    - Each _gamma_get call sleeps 0.04s so one call eats most of the budget.
    - Assert that fewer than 10 tag-id calls were made (loop exited early).

    RED on pre-fix code (no budget check inside loop): all 10 tags are fetched
    regardless of budget.
    GREEN post-fix: loop exits after budget expires (~1-2 tags fetched).
    """
    import src.data.market_scanner as ms_mod

    FAKE_TAG_SLUGS = [f"fake-tag-{i}" for i in range(10)]
    BUDGET_SECONDS = 0.05  # 50ms — expires after first slow tag call
    SLEEP_PER_TAG = 0.04   # each tag-id call sleeps 40ms

    tag_calls: list[str] = []

    original_gamma_get = ms_mod._gamma_get

    def _slow_gamma_get(path, **kwargs):
        import time as _time
        import httpx
        if path.startswith("/tags/slug/"):
            tag_calls.append(path)
            _time.sleep(SLEEP_PER_TAG)
            # Return a valid-looking tag response
            slug = path.split("/")[-1]
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"id": f"tag-id-{slug}"}
            return mock_resp
        # For /events calls, return empty list so we don't recurse into pagination
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = []
        return mock_resp

    monkeypatch.setattr(ms_mod, "TAG_SLUGS", FAKE_TAG_SLUGS)
    monkeypatch.setattr(ms_mod, "_gamma_get", _slow_gamma_get)
    monkeypatch.setattr(
        ms_mod,
        "_discovery_total_budget_seconds_from_env",
        lambda: BUDGET_SECONDS,
    )

    import time as _time
    t0 = _time.monotonic()
    # include_slug_pattern=False to avoid touching the slug-pattern path
    result = ms_mod._fetch_events_by_tags(include_slug_pattern=False)
    elapsed = _time.monotonic() - t0

    assert len(tag_calls) < len(FAKE_TAG_SLUGS), (
        f"Tag loop must exit early when budget={BUDGET_SECONDS}s expires. "
        f"Got {len(tag_calls)}/{len(FAKE_TAG_SLUGS)} tag calls. "
        f"Pre-fix failure: no budget check inside loop → all 10 tags fetched. "
        f"elapsed={elapsed:.3f}s"
    )
    assert isinstance(result, list), "Must return a list even when budget exhausted early"

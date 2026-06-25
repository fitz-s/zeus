# Created: 2026-05-24
# Lifecycle: created=2026-05-24; last_reviewed=2026-06-06; last_reused=2026-06-06
# Authority basis: fix(discovery): restore full-city market substrate coverage (50→7 regression);
#   2026-06-04 EXECUTABLE_SNAPSHOT_BLOCKED antibody — non-tradeable family-identity bins
#   must reach capture so executable_market_snapshots is family-COMPLETE (FDR full-family proof)
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
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import src.data.market_scanner as ms
from src.data.market_scanner import refresh_executable_market_substrate_snapshots
import src.data.substrate_observer as substrate_observer

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
            question_id TEXT,
            yes_token_id TEXT,
            no_token_id TEXT,
            selected_outcome_token_id TEXT,
            outcome_label TEXT NOT NULL,
            direction TEXT NOT NULL,
            execution_side TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            freshness_deadline TEXT,
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


def test_refresh_order_pairs_yes_no_sides_before_next_city():
    """A tight live budget must complete conditions, not spray one-sided YES rows.

    ``_condition_buy_sides_fresh`` only treats a condition as fresh when both
    selected tokens have fresh snapshots.  Therefore the substrate refresh order
    must keep buy_yes/buy_no adjacent for the same condition before moving to the
    next city's first side.
    """
    city_names = list(ms.cities_by_name.keys())[:5]
    markets = [
        _make_market(name, idx, metric="highest")
        for idx, name in enumerate(city_names, start=1)
    ]
    captured: list[tuple[str, str, str]] = []

    def _spy_capture(conn, *, market, decision, clob, captured_at, scan_authority, execution_side="BUY", **kwargs):
        captured.append(
            (
                str(market.get("slug") or ""),
                str(decision.tokens.get("market_id") or ""),
                str(decision.edge.direction),
            )
        )

    clob = _make_clob_mock()
    conn = _make_in_memory_trade_db()

    with patch("src.data.market_scanner.capture_executable_market_snapshot", side_effect=_spy_capture):
        refresh_executable_market_substrate_snapshots(
            conn,
            markets=markets,
            clob=clob,
            captured_at=_NOW,
            scan_authority="VERIFIED",
            max_outcomes=2,
        )

    assert len(captured) >= 4
    first_slug, first_condition, first_direction = captured[0]
    second_slug, second_condition, second_direction = captured[1]
    assert first_slug == second_slug
    assert first_condition == second_condition
    assert [first_direction, second_direction] == ["buy_yes", "buy_no"]

    third_slug, third_condition, third_direction = captured[2]
    fourth_slug, fourth_condition, fourth_direction = captured[3]
    assert third_slug == fourth_slug
    assert third_condition == fourth_condition
    assert [third_direction, fourth_direction] == ["buy_yes", "buy_no"]
    assert third_condition != first_condition


def test_refresh_prioritizes_money_path_condition_ids_before_family_siblings():
    """Confirmation refresh must price the scoped money-path condition first.

    Continuous redecision may refresh a full weather family, but the next action
    depends on a small scoped set: a held leg, an open rest, or a screened entry.
    When the batch orderbook path only has budget to recover a prefix, that prefix
    must be the scoped condition, not arbitrary sibling bins.
    """
    markets = [
        _make_market("Miami", 1, metric="highest", target_date="2026-05-25"),
        _make_market("Miami", 2, metric="highest", target_date="2026-05-26"),
        _make_market("Miami", 3, metric="highest", target_date="2026-05-27"),
    ]
    priority_condition = markets[2]["condition_ids"][0]
    captured: list[tuple[str, str]] = []

    def _spy_capture(conn, *, market, decision, clob, captured_at, scan_authority, execution_side="BUY", **kwargs):
        captured.append(
            (
                str(decision.tokens.get("market_id") or ""),
                str(decision.edge.direction),
            )
        )

    clob = _make_clob_mock()
    clob.get_orderbook_snapshots = None
    conn = _make_in_memory_trade_db()

    with patch("src.data.market_scanner.capture_executable_market_snapshot", side_effect=_spy_capture):
        refresh_executable_market_substrate_snapshots(
            conn,
            markets=markets,
            clob=clob,
            captured_at=_NOW,
            scan_authority="VERIFIED",
            max_outcomes=0,
            priority_condition_ids={priority_condition},
        )

    assert captured[:2] == [
        (priority_condition, "buy_yes"),
        (priority_condition, "buy_no"),
    ]


def test_refresh_order_prioritizes_one_sided_fresh_condition_completion():
    """A one-sided fresh condition should be completed before new conditions.

    Live regression 2026-06-06: the warm cycle had a 30s freshness window, a
    ~20s cadence, and only enough CLOB budget for a few captures.  Alphabetical
    city ordering repeatedly refreshed one-sided prefixes while hundreds of
    conditions never became complete.  Since the gate requires both YES and NO
    selected tokens, a partial condition is the fastest route to a usable fresh
    condition and must sort before never-captured conditions.
    """
    city_names = list(ms.cities_by_name.keys())[:3]
    markets = [
        _make_market(name, idx, metric="highest")
        for idx, name in enumerate(city_names, start=1)
    ]
    partial_condition = markets[0]["condition_ids"][0]
    partial_outcome = markets[0]["outcomes"][0]
    partial_yes_token = partial_outcome["token_id"]
    conn = _make_in_memory_trade_db()
    conn.execute(
        """
        INSERT INTO executable_market_snapshots (
            snapshot_id, event_slug, condition_id, question_id,
            yes_token_id, no_token_id, selected_outcome_token_id,
            outcome_label, direction, execution_side, captured_at,
            freshness_deadline, scan_authority
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "partial-prefix-yes",
            markets[0]["slug"],
            partial_condition,
            partial_condition,
            partial_outcome["token_id"],
            partial_outcome["no_token_id"],
            partial_yes_token,
            "YES",
            "buy_yes",
            "BUY",
            _NOW.isoformat(),
            (_NOW + timedelta(seconds=30)).isoformat(),
            "VERIFIED",
        ),
    )
    conn.commit()

    captured: list[tuple[str, str, str]] = []

    def _spy_capture(conn, *, market, decision, clob, captured_at, scan_authority, execution_side="BUY", **kwargs):
        captured.append(
            (
                str(market.get("slug") or ""),
                str(decision.tokens.get("market_id") or ""),
                str(decision.edge.direction),
            )
        )

    clob = _make_clob_mock()
    with patch("src.data.market_scanner.capture_executable_market_snapshot", side_effect=_spy_capture):
        refresh_executable_market_substrate_snapshots(
            conn,
            markets=markets,
            clob=clob,
            captured_at=_NOW,
            scan_authority="VERIFIED",
            max_outcomes=2,
        )

    assert len(captured) >= 2
    assert captured[0][1] == partial_condition
    assert captured[0][2] == "buy_no"
    assert captured[1][1] != partial_condition


def test_refresh_skips_already_fresh_side_on_partial_condition():
    """Partial conditions should refresh only the missing selected side.

    The live warm path was repeatedly spending one CLOB slot on a side that was
    already fresh, then running out of budget before enough other families could
    complete.  The correct invariant is side-specific freshness: if YES is fresh
    and NO is stale/missing, only ``buy_no`` should enter capture.
    """
    markets = [_make_market("Miami", 1, metric="highest")]
    condition_id = markets[0]["condition_ids"][0]
    outcome = markets[0]["outcomes"][0]
    yes_token = outcome["token_id"]
    conn = _make_in_memory_trade_db()
    conn.execute(
        """
        INSERT INTO executable_market_snapshots (
            snapshot_id, event_slug, condition_id, question_id,
            yes_token_id, no_token_id, selected_outcome_token_id,
            outcome_label, direction, execution_side, captured_at,
            freshness_deadline, scan_authority
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "fresh-yes-only",
            markets[0]["slug"],
            condition_id,
            condition_id,
            outcome["token_id"],
            outcome["no_token_id"],
            yes_token,
            "YES",
            "buy_yes",
            "BUY",
            _NOW.isoformat(),
            (_NOW + timedelta(seconds=30)).isoformat(),
            "VERIFIED",
        ),
    )
    conn.commit()
    captured: list[str] = []

    def _spy_capture(conn, *, market, decision, clob, captured_at, scan_authority, execution_side="BUY", **kwargs):
        captured.append(str(decision.edge.direction))

    clob = _make_clob_mock()
    with patch("src.data.market_scanner.capture_executable_market_snapshot", side_effect=_spy_capture):
        summary = refresh_executable_market_substrate_snapshots(
            conn,
            markets=markets,
            clob=clob,
            captured_at=_NOW,
            scan_authority="VERIFIED",
            max_outcomes=2,
        )

    assert captured == ["buy_no"]
    assert summary["selected_executable_snapshot_count"] == 1


def test_sparse_fresh_snapshot_does_not_skip_identity_capture():
    """A legacy/sparse fresh row is not enough to skip identity capture."""
    markets = [_make_market("Miami", 1, metric="highest")]
    condition_id = markets[0]["condition_ids"][0]
    yes_token = markets[0]["outcomes"][0]["token_id"]
    conn = _make_in_memory_trade_db()
    conn.execute(
        """
        INSERT INTO executable_market_snapshots (
            snapshot_id, event_slug, condition_id, selected_outcome_token_id,
            outcome_label, direction, execution_side, captured_at,
            freshness_deadline, scan_authority
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "sparse-fresh-yes",
            markets[0]["slug"],
            condition_id,
            yes_token,
            "YES",
            "buy_yes",
            "BUY",
            _NOW.isoformat(),
            (_NOW + timedelta(seconds=30)).isoformat(),
            "VERIFIED",
        ),
    )
    conn.commit()
    captured: list[str] = []

    def _spy_capture(conn, *, market, decision, clob, captured_at, scan_authority, execution_side="BUY", **kwargs):
        captured.append(str(decision.edge.direction))

    clob = _make_clob_mock()
    with patch("src.data.market_scanner.capture_executable_market_snapshot", side_effect=_spy_capture):
        summary = refresh_executable_market_substrate_snapshots(
            conn,
            markets=markets,
            clob=clob,
            captured_at=_NOW,
            scan_authority="VERIFIED",
            max_outcomes=2,
        )

    assert captured == ["buy_yes", "buy_no"]
    assert summary["selected_executable_snapshot_count"] == 2


def test_capture_reuses_clob_market_info_for_adjacent_condition_sides():
    """Adjacent YES/NO captures for one condition should not refetch /markets."""
    market = {
        "event_id": "evt-cache",
        "slug": "highest-temperature-in-cache-on-may-25-2026",
        "outcomes": [
            {
                "condition_id": "cond-cache",
                "market_id": "cond-cache",
                "question_id": "question-cache",
                "gamma_market_id": "gamma-cache",
                "token_id": "yes-cache",
                "no_token_id": "no-cache",
                "active": True,
                "closed": False,
                "accepting_orders": True,
                "enable_orderbook": True,
                "raw_gamma_payload_hash": "f" * 64,
                "gamma_market_raw": {
                    "id": "gamma-cache",
                    "conditionId": "cond-cache",
                    "questionID": "question-cache",
                    "active": True,
                    "closed": False,
                    "acceptingOrders": True,
                    "enableOrderBook": True,
                    "clobTokenIds": ["yes-cache", "no-cache"],
                },
            }
        ],
    }

    class CountingClob:
        def __init__(self) -> None:
            self.market_info_calls = 0

        def get_clob_market_info(self, condition_id: str) -> dict:
            self.market_info_calls += 1
            return {
                "condition_id": condition_id,
                "tokens": [{"token_id": "yes-cache"}, {"token_id": "no-cache"}],
                "archived": False,
                "enable_order_book": True,
                "accepting_orders": True,
                "tick_size": "0.01",
                "min_order_size": "5",
                "neg_risk": True,
            }

        def get_orderbook_snapshot(self, token_id: str) -> dict:
            return {
                "asset_id": token_id,
                "tick_size": "0.01",
                "min_order_size": "5",
                "neg_risk": True,
                "bids": [{"price": "0.40", "size": "10"}],
                "asks": [{"price": "0.42", "size": "10"}],
            }

        def get_fee_rate(self, token_id: str) -> float:
            return 0.0

    conn = _make_in_memory_trade_db()
    clob = CountingClob()
    cache: dict[str, dict] = {}
    ms._prev_orderbook_hash_by_market.pop("cond-cache", None)

    with (
        patch("src.data.market_scanner.insert_snapshot"),
        patch("src.data.market_scanner._write_book_hash_transition"),
    ):
        for direction in ("buy_yes", "buy_no"):
            decision = SimpleNamespace(
                tokens={
                    "token_id": "yes-cache",
                    "no_token_id": "no-cache",
                    "market_id": "cond-cache",
                },
                edge=SimpleNamespace(direction=direction),
            )
            ms.capture_executable_market_snapshot(
                conn,
                market=market,
                decision=decision,
                clob=clob,
                captured_at=_NOW,
                scan_authority="VERIFIED",
                clob_market_info_cache=cache,
            )

    assert clob.market_info_calls == 1
    assert set(cache) == {"cond-cache"}


def test_cached_topology_limits_gamma_lookup_window(monkeypatch):
    """Warm cycles with cached topology must reserve most time for CLOB prices."""
    import src.main as main_mod

    fake_now = 108.0
    monkeypatch.setattr(main_mod.time, "monotonic", lambda: fake_now)
    monkeypatch.delenv("ZEUS_REACTOR_CACHED_TOPOLOGY_GAMMA_SECONDS", raising=False)

    deadline_with_cache = substrate_observer._gamma_lookup_deadline_for_snapshot_refresh(
        refresh_deadline=115.0,
        refresh_budget_s=15.0,
        snapshot_reserve_s=6.0,
        cached_topology_count=50,
    )
    deadline_without_cache = substrate_observer._gamma_lookup_deadline_for_snapshot_refresh(
        refresh_deadline=115.0,
        refresh_budget_s=15.0,
        snapshot_reserve_s=6.0,
        cached_topology_count=0,
    )

    assert deadline_with_cache == pytest.approx(101.0)
    assert deadline_without_cache == pytest.approx(109.0)


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
    monkeypatch.setattr(substrate_observer, "_market_discovery_last_completed_monotonic", None)

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
        with patch.object(substrate_observer, "_market_discovery_lock", mock_lock):
            substrate_observer._market_discovery_cycle()

    assert len(tag_scan_called) >= 1, (
        "_market_discovery_cycle must call find_weather_markets (full tag-query "
        "path covering all 51 cities). Pre-fix: only find_slug_pattern_weather_markets "
        "(14-city slug-only) is called."
    )


def test_market_discovery_defers_while_edli_pending_backlog(monkeypatch):
    """Universe discovery should yield to the pending-family CLOB warm path.

    In EDLI mode, pending-family warm is the latency-critical path that can
    unlock receipts.  A universe-wide discovery scan is data-only and can wait
    when hundreds of pending opportunity events need fresh executable prices.
    """
    import src.main as main_mod
    import src.data.market_scanner as scanner_mod

    monkeypatch.setattr(substrate_observer, "_settings_section", lambda name, default=None: {"enabled": True} if name == "edli_v1" else (default or {}))
    monkeypatch.setenv("ZEUS_MARKET_DISCOVERY_DEFER_WHEN_EDLI_PENDING", "1")
    monkeypatch.setenv("ZEUS_MARKET_DISCOVERY_PENDING_FAIRNESS_SECONDS", "300")
    monkeypatch.setattr(substrate_observer, "_market_discovery_last_completed_monotonic", 100.0)
    monkeypatch.setattr(main_mod.time, "monotonic", lambda: 120.0)
    monkeypatch.setattr(scanner_mod, "find_weather_markets", lambda **kwargs: pytest.fail("must defer"))

    substrate_observer._market_discovery_cycle()


def test_market_discovery_with_pending_and_stale_substrate_still_captures(monkeypatch):
    """ANTIBODY (2026-06-08): executable-substrate capture is NOT gated by the EDLI pending
    backlog — only by substrate STALENESS.

    This test REPLACES the prior test_market_discovery_with_pending_runs_topology_only_not_
    substrate_capture, which enshrined the regression: it asserted that a pending backlog must
    make market_discovery do topology-only and SKIP snapshot capture. That coupling is exactly
    what collapsed coverage when the pending working set grew (channel-event flood with the
    prune off) — capture was skipped forever, families went uncaptured, FSR events dead-lettered,
    and the system silently stopped trading. The correct invariant: with a pending backlog AND a
    STALE substrate (last full capture older than the fairness window), market_discovery still
    runs the FULL executable-substrate capture."""
    import src.main as main_mod
    import src.data.market_scanner as scanner_mod

    calls: list[dict] = []
    captured: list[dict] = []
    monkeypatch.setattr(substrate_observer, "_settings_section", lambda name, default=None: {"enabled": True} if name == "edli_v1" else (default or {}))
    monkeypatch.setenv("ZEUS_MARKET_DISCOVERY_DEFER_WHEN_EDLI_PENDING", "1")
    monkeypatch.setenv("ZEUS_MARKET_DISCOVERY_PENDING_FAIRNESS_SECONDS", "300")
    # STALE substrate: last full capture 400s ago (> 300s fairness window).
    monkeypatch.setattr(substrate_observer, "_market_discovery_last_completed_monotonic", 100.0)
    monkeypatch.setattr(main_mod.time, "monotonic", lambda: 500.0)

    def _mock_find_weather_markets(**kwargs):
        calls.append(kwargs)
        return []

    monkeypatch.setattr(scanner_mod, "find_weather_markets", _mock_find_weather_markets)

    def _capture(conn, *, markets, clob, captured_at, scan_authority):
        captured.append({"scan_authority": scan_authority})
        return {"attempted": 0, "inserted": 0, "skipped": 0, "failed": 0, "truncated": 0, "budget_exhausted": 0}

    monkeypatch.setattr(
        "src.data.market_scanner.refresh_executable_market_substrate_snapshots", _capture
    )
    mock_conn = MagicMock()
    with (
        patch("src.data.polymarket_client.PolymarketClient") as mock_clob_cls,
        patch("src.state.db.get_trade_connection", return_value=mock_conn),
    ):
        mock_clob_cls.return_value.__enter__ = lambda s: MagicMock()
        mock_clob_cls.return_value.__exit__ = MagicMock(return_value=False)
        substrate_observer._market_discovery_cycle()

    # Decoupled from the backlog: full executable-substrate capture ran despite pending=650.
    assert calls
    assert captured, "stale substrate + pending backlog MUST still run full executable-substrate capture"
    assert mock_clob_cls.call_count > 0


def test_market_discovery_defers_when_substrate_fresh_regardless_of_pending(monkeypatch):
    """The ONLY legitimate defer is the fairness floor, keyed on substrate FRESHNESS (not queue
    depth): when a full capture happened within the fairness window, skip redundant re-capture.
    With pending>0 AND a FRESH substrate (last capture within the window), market_discovery
    defers without re-capturing."""
    import src.main as main_mod

    monkeypatch.setattr(substrate_observer, "_settings_section", lambda name, default=None: {"enabled": True} if name == "edli_v1" else (default or {}))
    monkeypatch.setenv("ZEUS_MARKET_DISCOVERY_DEFER_WHEN_EDLI_PENDING", "1")
    monkeypatch.setenv("ZEUS_MARKET_DISCOVERY_PENDING_FAIRNESS_SECONDS", "300")
    # FRESH substrate: last full capture 100s ago (< 300s fairness window).
    monkeypatch.setattr(substrate_observer, "_market_discovery_last_completed_monotonic", 400.0)
    monkeypatch.setattr(main_mod.time, "monotonic", lambda: 500.0)
    monkeypatch.setattr(
        "src.data.market_scanner.refresh_executable_market_substrate_snapshots",
        lambda *args, **kwargs: pytest.fail("a FRESH substrate must not be re-captured"),
    )
    # Defers at the fairness early-return; no capture, no exception.
    substrate_observer._market_discovery_cycle()


def test_market_discovery_continues_when_pending_count_unavailable(monkeypatch):
    """Universe discovery runs regardless of EDLI pending state (P2 superiority).

    Pre-P2 this guarded "a missing EDLI processing schema must not break discovery" because
    the cycle READ a consumer pending_count. The P2 lift DELETES that read entirely — the
    producer no longer touches EDLI processing state at all, so discovery can never be
    broken by it. This test now asserts the stronger invariant: with a STALE substrate the
    producer captures the universe, with zero reference to any pending/reactor state.
    """
    import src.main as main_mod
    import src.data.market_scanner as scanner_mod

    calls: list[dict] = []
    monkeypatch.setattr(substrate_observer, "_settings_section", lambda name, default=None: {"enabled": True} if name == "edli_v1" else (default or {}))
    # P2: force STALE substrate so the staleness gate falls through to capture (order-independent).
    monkeypatch.setattr(substrate_observer, "_market_discovery_last_completed_monotonic", None)
    monkeypatch.setenv("ZEUS_MARKET_DISCOVERY_DEFER_WHEN_EDLI_PENDING", "1")

    def _mock_find_weather_markets(**kwargs):
        calls.append(kwargs)
        return []

    monkeypatch.setattr(scanner_mod, "find_weather_markets", _mock_find_weather_markets)
    monkeypatch.setattr(
        "src.data.market_scanner.refresh_executable_market_substrate_snapshots",
        lambda conn, *, markets, clob, captured_at, scan_authority: {
            "attempted": 0, "inserted": 0, "skipped": 0, "failed": 0,
            "truncated": 0, "budget_exhausted": 0,
        },
    )
    mock_conn = MagicMock()
    with (
        patch("src.data.polymarket_client.PolymarketClient") as mock_clob_cls,
        patch("src.state.db.get_trade_connection", return_value=mock_conn),
    ):
        mock_clob_cls.return_value.__enter__ = lambda s: MagicMock()
        mock_clob_cls.return_value.__exit__ = MagicMock(return_value=False)
        substrate_observer._market_discovery_cycle()

    assert calls


class _FakeLock:
    def __init__(self, acquire_result: bool = True) -> None:
        self.acquire_result = acquire_result
        self.acquire_calls = 0
        self.release_calls = 0

    def acquire(self, blocking: bool = True) -> bool:
        self.acquire_calls += 1
        return self.acquire_result

    def release(self) -> None:
        self.release_calls += 1


def test_market_discovery_busy_substrate_lock_releases_discovery_lock(monkeypatch):
    import src.main as main_mod
    import src.data.market_scanner as scanner_mod

    discovery_lock = _FakeLock(True)
    substrate_lock = _FakeLock(False)
    monkeypatch.setattr(substrate_observer, "_market_discovery_lock", discovery_lock)
    monkeypatch.setattr(substrate_observer, "_market_substrate_refresh_lock", substrate_lock)
    # P2: the pure staleness gate skips early if the substrate is fresh. Force STALE
    # (clock unset) so the cycle reaches the lock logic this test exercises. (A prior test's
    # successful cycle leaves a real time.monotonic() in this module global; monkeypatch it
    # back to None so this lock test is order-independent.)
    monkeypatch.setattr(substrate_observer, "_market_discovery_last_completed_monotonic", None)
    monkeypatch.setenv("ZEUS_MARKET_DISCOVERY_DEFER_WHEN_EDLI_PENDING", "0")
    monkeypatch.setattr(scanner_mod, "find_weather_markets", lambda **kwargs: pytest.fail("must not scan"))

    substrate_observer._market_discovery_cycle()

    assert discovery_lock.acquire_calls == 1
    assert discovery_lock.release_calls == 1
    assert substrate_lock.acquire_calls == 1
    assert substrate_lock.release_calls == 0


def test_market_discovery_releases_locks_when_refresh_raises(monkeypatch):
    import src.main as main_mod
    import src.data.market_scanner as scanner_mod

    discovery_lock = _FakeLock(True)
    substrate_lock = _FakeLock(True)
    monkeypatch.setattr(substrate_observer, "_market_discovery_lock", discovery_lock)
    monkeypatch.setattr(substrate_observer, "_market_substrate_refresh_lock", substrate_lock)
    # P2: force STALE substrate (clock unset) so the staleness gate falls through to the
    # capture/lock path this test exercises (order-independent — see note above).
    monkeypatch.setattr(substrate_observer, "_market_discovery_last_completed_monotonic", None)
    monkeypatch.setenv("ZEUS_MARKET_DISCOVERY_DEFER_WHEN_EDLI_PENDING", "0")
    monkeypatch.setattr(scanner_mod, "find_weather_markets", lambda **kwargs: [_make_market("Miami", 1)])

    def _raise_refresh(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "src.data.market_scanner.refresh_executable_market_substrate_snapshots",
        _raise_refresh,
    )
    mock_conn = MagicMock()
    # P2 lift: the bare lifted _market_discovery_cycle no longer carries the
    # @_scheduler_job decorator that swallowed exceptions (the daemon applies that wrapper
    # at registration). Its OWN contract is lock hygiene: it releases BOTH locks via
    # `finally` even when the refresh raises, then lets the error propagate to the daemon's
    # fail-soft wrapper. Assert exactly that: error propagates, locks released.
    with (
        patch("src.data.polymarket_client.PolymarketClient") as mock_clob_cls,
        patch("src.state.db.get_trade_connection", return_value=mock_conn),
    ):
        mock_clob_cls.return_value.__enter__ = lambda s: MagicMock()
        mock_clob_cls.return_value.__exit__ = MagicMock(return_value=False)
        with pytest.raises(RuntimeError, match="boom"):
            substrate_observer._market_discovery_cycle()

    assert discovery_lock.release_calls == 1
    assert substrate_lock.release_calls == 1


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


def test_slow_batch_orderbook_prefetch_leaves_budget_for_capture(monkeypatch):
    """A large warm cycle must not spend the whole budget in POST /books prefetch.

    Live regression 2026-06-06: after topology cache started submitting all 57
    families, the batch prefetch used the entire snapshot budget before the
    capture loop ran, producing ``attempted=0`` and no fresh receipt flow.  The
    fix gives prefetch its own deadline before the overall deadline so at least
    some selected candidates can move into capture in the same cycle.
    """
    BUDGET_SECONDS = 10.0
    RESERVE_SECONDS = 2.0
    PREFETCH_SECONDS_PER_CHUNK = 8.2

    fake_now = 0.0

    def _fake_monotonic() -> float:
        return fake_now

    monkeypatch.setattr(ms.time, "monotonic", _fake_monotonic)
    monkeypatch.setenv(
        "ZEUS_MARKET_DISCOVERY_SNAPSHOT_CAPTURE_RESERVE_SECONDS",
        str(RESERVE_SECONDS),
    )

    markets = [
        _make_market(name, idx, metric="highest")
        for idx, name in enumerate(list(ms.cities_by_name.keys())[:40], start=1)
    ]
    capture_calls: list[str] = []

    def _batch_books(token_ids: list[str]) -> dict[str, dict]:
        nonlocal fake_now
        fake_now += PREFETCH_SECONDS_PER_CHUNK
        return {
            token_id: {
                "market": token_id,
                "asset_id": token_id,
                "bids": [{"price": "0.55", "size": "100"}],
                "asks": [{"price": "0.60", "size": "100"}],
            }
            for token_id in token_ids
        }

    def _mock_capture(conn, *, market, decision, clob, captured_at, scan_authority, execution_side="BUY", **kwargs):
        capture_calls.append(str(decision.tokens.get("market_id") or ""))

    clob = _make_clob_mock()
    clob.get_orderbook_snapshots.side_effect = _batch_books
    conn = _make_in_memory_trade_db()

    with patch("src.data.market_scanner.capture_executable_market_snapshot", side_effect=_mock_capture):
        summary = refresh_executable_market_substrate_snapshots(
            conn,
            markets=markets,
            clob=clob,
            captured_at=_NOW,
            scan_authority="VERIFIED",
            budget_seconds=BUDGET_SECONDS,
            max_outcomes=2,
        )

    assert summary["attempted"] > 0, (
        "Snapshot refresh must leave budget for capture even when the first "
        "batch /books chunk is slow. Pre-fix behavior consumed a second chunk "
        "under the full deadline and reached capture only after budget expiry: "
        f"summary={summary}"
    )
    assert capture_calls, f"capture loop must run after batch prefetch: summary={summary}"
    assert summary["snapshot_capture_reserve_seconds"] == pytest.approx(RESERVE_SECONDS)
    assert clob.get_orderbook_snapshots.call_count == 1, (
        "Prefetch must stop at its earlier deadline instead of consuming the "
        f"full snapshot budget. summary={summary}"
    )


def test_batch_orderbook_prefetch_uses_live_proven_large_chunks(monkeypatch):
    """Large weather cycles must not regress to tiny POST /books chunks.

    Live CLOB probe 2026-06-06 accepted 500 token IDs per POST /books request
    and rejected 1000.  The warm path normally sees 600+ candidate outcomes; a
    50-token chunk ceiling makes most cycles fall back to serial GET /book.
    """
    monkeypatch.delenv("ZEUS_MARKET_DISCOVERY_SNAPSHOT_CAPTURE_RESERVE_SECONDS", raising=False)
    monkeypatch.delenv("ZEUS_MARKET_DISCOVERY_ORDERBOOK_PREFETCH_MIN_WINDOW_SECONDS", raising=False)

    markets = [
        _make_market(name, idx, metric="highest")
        for idx, name in enumerate(list(ms.cities_by_name.keys())[:40], start=1)
    ]
    batch_sizes: list[int] = []

    def _batch_books(token_ids: list[str]) -> dict[str, dict]:
        batch_sizes.append(len(token_ids))
        return {
            token_id: {
                "market": token_id,
                "asset_id": token_id,
                "bids": [{"price": "0.55", "size": "100"}],
                "asks": [{"price": "0.60", "size": "100"}],
            }
            for token_id in token_ids
        }

    def _mock_capture(conn, *, market, decision, clob, captured_at, scan_authority, execution_side="BUY", **kwargs):
        return None

    clob = _make_clob_mock()
    clob.get_orderbook_snapshots.side_effect = _batch_books
    conn = _make_in_memory_trade_db()

    with patch("src.data.market_scanner.capture_executable_market_snapshot", side_effect=_mock_capture):
        summary = refresh_executable_market_substrate_snapshots(
            conn,
            markets=markets,
            clob=clob,
            captured_at=_NOW,
            scan_authority="VERIFIED",
            budget_seconds=20.0,
            max_outcomes=2,
        )

    assert batch_sizes, f"expected at least one POST /books prefetch: summary={summary}"
    assert max(batch_sizes) > 50, (
        "POST /books chunking must use the live-proven large batch envelope; "
        f"batch_sizes={batch_sizes} summary={summary}"
    )
    assert clob.get_orderbook_snapshots.call_count == 1, (
        "This fixture has fewer than 500 selected tokens, so it should fit in "
        f"one live-proven chunk. batch_sizes={batch_sizes} summary={summary}"
    )
    assert summary["prefetched_orderbook_count"] > 50


def test_unbounded_family_refresh_batches_complete_groups_per_tick(monkeypatch):
    """max_outcomes=0 must not prefetch every stale family in one live tick.

    The family-completion path needs complete YES/NO sibling coverage, but live
    redecision can hand it many families at once. The scheduler should process a
    bounded set of complete family groups and leave the rest for the next tick,
    not prefetch hundreds of books and leave no time for SQLite writes.
    """
    monkeypatch.setenv("ZEUS_SNAPSHOT_CAPTURE_MAX_CANDIDATES_PER_TICK", "4")

    markets = [
        _make_market(name, idx, metric="highest")
        for idx, name in enumerate(list(ms.cities_by_name.keys())[:4], start=1)
    ]
    capture_calls: list[str] = []
    batch_token_counts: list[int] = []

    def _batch_books(token_ids: list[str]) -> dict[str, dict]:
        batch_token_counts.append(len(token_ids))
        return {
            token_id: {
                "market": token_id,
                "asset_id": token_id,
                "bids": [{"price": "0.55", "size": "100"}],
                "asks": [{"price": "0.60", "size": "100"}],
            }
            for token_id in token_ids
        }

    def _mock_capture(conn, *, market, decision, clob, captured_at, scan_authority, execution_side="BUY", **kwargs):
        capture_calls.append(str(decision.tokens.get("market_id") or ""))

    clob = _make_clob_mock()
    clob.get_orderbook_snapshots.side_effect = _batch_books
    conn = _make_in_memory_trade_db()

    with patch("src.data.market_scanner.capture_executable_market_snapshot", side_effect=_mock_capture):
        summary = refresh_executable_market_substrate_snapshots(
            conn,
            markets=markets,
            clob=clob,
            captured_at=_NOW,
            scan_authority="VERIFIED",
            budget_seconds=20.0,
            max_outcomes=0,
        )

    assert summary["executable_snapshot_candidate_count"] == 8
    assert summary["selected_executable_snapshot_count"] == 4
    assert summary["prefetched_orderbook_count"] == 4
    assert batch_token_counts == [4]
    assert len(capture_calls) == 4
    assert summary["truncated"] == 1
    assert summary["budget_truncated_city_count"] == 2


def test_unbounded_family_refresh_default_cap_uses_batch_books_envelope(monkeypatch):
    """Default full-family refresh cap should match the proven one-call /books envelope."""

    monkeypatch.delenv("ZEUS_SNAPSHOT_CAPTURE_MAX_CANDIDATES_PER_TICK", raising=False)

    assert ms._snapshot_capture_max_candidates_per_tick(per_city_limit=0) == 500


def test_tiny_prefetch_window_still_attempts_one_batch_books(monkeypatch):
    """Tiny prefetch window MUST still fire ONE batch POST /books, not fall back
    to a per-token GET /book storm.

    PER-TOKEN STORM FIX (2026-06-16, dead_order_lane_per_token_book_storm): the
    prior behavior SKIPPED the batch entirely when (budget - reserve) fell below
    the 0.75s minimum and returned ``prefetched_orderbook_count == 0``, dumping
    the WHOLE family to a sequential per-token GET /book (~650ms each) inside
    capture — strictly SLOWER than the one ~1s POST /books the skip was avoiding
    (measured live: 104k GET /book vs 2.4k POST /books, 43:1; the budget-tight
    warm lanes were the dominant storm source). The fix ALWAYS attempts the FIRST
    chunk (one POST that replaces the N per-token GETs the fallback runs anyway),
    bounded only by the client's own HTTP timeout; the min-window/deadline gate
    now applies to the SECOND-and-later chunks only. This fixture's ~40 selected
    tokens fit in one ``_BATCH_ORDERBOOK_CHUNK`` chunk, so exactly ONE batch
    attempt is expected and every token is prefetched (no per-token fallback).

    RED-on-revert: restoring the pre-loop ``return {}`` skip makes
    ``get_orderbook_snapshots.call_count`` 0 and ``prefetched_orderbook_count`` 0
    again — the exact storm condition this fix removes.
    """
    BUDGET_SECONDS = 6.0
    fake_now = 0.0

    def _fake_monotonic() -> float:
        return fake_now

    monkeypatch.setattr(ms.time, "monotonic", _fake_monotonic)
    monkeypatch.delenv("ZEUS_MARKET_DISCOVERY_SNAPSHOT_CAPTURE_RESERVE_SECONDS", raising=False)
    monkeypatch.delenv("ZEUS_MARKET_DISCOVERY_ORDERBOOK_PREFETCH_MIN_WINDOW_SECONDS", raising=False)

    markets = [
        _make_market(name, idx, metric="highest")
        for idx, name in enumerate(list(ms.cities_by_name.keys())[:10], start=1)
    ]
    capture_calls: list[str] = []
    batch_token_counts: list[int] = []

    def _batch_books(token_ids: list[str]) -> dict[str, dict]:
        batch_token_counts.append(len(token_ids))
        return {
            token_id: {
                "market": token_id,
                "asset_id": token_id,
                "bids": [{"price": "0.55", "size": "100"}],
                "asks": [{"price": "0.60", "size": "100"}],
            }
            for token_id in token_ids
        }

    def _mock_capture(conn, *, market, decision, clob, captured_at, scan_authority, execution_side="BUY", **kwargs):
        capture_calls.append(str(decision.tokens.get("market_id") or ""))

    clob = _make_clob_mock()
    clob.get_orderbook_snapshots.side_effect = _batch_books
    conn = _make_in_memory_trade_db()

    with patch("src.data.market_scanner.capture_executable_market_snapshot", side_effect=_mock_capture):
        summary = refresh_executable_market_substrate_snapshots(
            conn,
            markets=markets,
            clob=clob,
            captured_at=_NOW,
            scan_authority="VERIFIED",
            budget_seconds=BUDGET_SECONDS,
            max_outcomes=2,
        )

    # The single-chunk family ALWAYS fires exactly one POST /books — never the
    # per-token GET storm the old skip produced.
    assert clob.get_orderbook_snapshots.call_count == 1
    assert batch_token_counts and batch_token_counts[0] > 0
    assert summary["attempted"] > 0
    assert capture_calls
    # Every selected token came from the batch — no per-token fallback (the storm).
    assert summary["prefetched_orderbook_count"] == batch_token_counts[0]
    assert summary["prefetched_orderbook_count"] > 0
    assert summary["snapshot_capture_reserve_seconds"] == pytest.approx(BUDGET_SECONDS - 0.05)


def test_batch_capable_warm_lane_defers_missing_books_without_per_token_fallback(monkeypatch):
    """A partial/failed /books response must not degrade into serial /book reads.

    Live root cause (2026-06-25): the warm lane had a tiny batch-prefetch window,
    but missing batch entries still fell back to per-token ``GET /book`` inside
    capture. A single slow/partial batch could therefore become dozens of blocking
    HTTP calls and starve the next 100+ pending families. Batch-capable substrate
    refresh now skips those missing books for this tick; the next scheduled tick
    retries with fresh CLOB evidence.
    """

    market = _make_market("Shanghai", 1, metric="highest")

    class _Clob:
        def __init__(self):
            self.book_get_calls = 0

        def get_orderbook_snapshots(self, token_ids):
            return {}

        def get_orderbook_snapshot(self, token_id):
            self.book_get_calls += 1
            raise AssertionError("batch-capable warm lane must not fall back to per-token /book")

    clob = _Clob()
    conn = _make_in_memory_trade_db()
    capture_calls: list[str] = []

    def _capture(*_args, **_kwargs):
        capture_calls.append("capture")

    with patch("src.data.market_scanner.capture_executable_market_snapshot", side_effect=_capture):
        summary = refresh_executable_market_substrate_snapshots(
            conn,
            markets=[market],
            clob=clob,
            captured_at=_NOW,
            scan_authority="VERIFIED",
            budget_seconds=6.0,
            max_outcomes=0,
        )

    assert capture_calls == []
    assert clob.book_get_calls == 0
    assert summary["attempted"] == 0
    assert summary["prefetch_missing_skipped"] == summary["selected_executable_snapshot_count"]
    assert summary["prefetch_missing_skipped"] > 0


def test_prefetch_first_chunk_always_fires_chunk2plus_budget_gated(monkeypatch):
    """PER-TOKEN STORM FIX boundary antibody: the FIRST POST /books chunk always
    fires (it replaces the per-token GET storm), while the SECOND-and-later chunks
    of a large multi-chunk cycle remain budget-gated.

    Storm root cause (2026-06-16): the pre-loop min-window skip returned ``{}`` and
    dumped the WHOLE family to sequential per-token GET /book. The fix moves the
    gate INTO the chunk loop and exempts chunk 0, so:
      * no deadline                -> every chunk fires (unchanged)
      * deadline already in the past -> chunk 0 STILL fires (one POST, no storm);
        chunks 1+ are skipped and retried by a later warm tick.

    Driven directly against ``_prefetch_selected_orderbooks`` with a tiny chunk
    size so a small token set spans multiple chunks. RED-on-revert: restoring the
    pre-loop ``return {}`` skip makes the past-deadline case fire ZERO chunks.
    """
    monkeypatch.setattr(ms, "_BATCH_ORDERBOOK_CHUNK", 2)  # 2 tokens/chunk -> 3 chunks for 6 tokens

    def _cand(i: int) -> tuple:
        return (
            0,
            0,
            i,
            {"slug": f"m{i}"},
            {"token_id": f"yes-{i}", "no_token_id": f"no-{i}"},
            f"cond-{i}",
            "buy_yes",
        )

    candidates = [_cand(i) for i in range(6)]
    chunk_calls: list[list[str]] = []

    class _Clob:
        def get_orderbook_snapshots(self, token_ids):
            chunk_calls.append(list(token_ids))
            return {t: {"asset_id": t, "bids": [], "asks": []} for t in token_ids}

    # No deadline -> all 3 chunks fire (back-compat).
    chunk_calls.clear()
    books = ms._prefetch_selected_orderbooks(_Clob(), candidates, deadline=None)
    assert len(chunk_calls) == 3, chunk_calls
    assert len(books) == 6

    # Deadline already in the PAST -> chunk 0 STILL fires (storm fix), chunks 1+ gated.
    chunk_calls.clear()
    past_deadline = time.monotonic() - 100.0
    books = ms._prefetch_selected_orderbooks(_Clob(), candidates, deadline=past_deadline)
    assert len(chunk_calls) == 1, (
        "first chunk MUST always fire (one POST /books replaces the per-token GET "
        f"storm); got {len(chunk_calls)} chunks"
    )
    assert len(books) == 2  # only the first chunk's tokens; the rest retry later


def test_prefetch_failed_large_chunk_splits_to_retry_chunks(monkeypatch):
    """A large POST /books failure must not zero the whole substrate price surface."""

    monkeypatch.setattr(ms, "_BATCH_ORDERBOOK_CHUNK", 6)
    monkeypatch.setattr(ms, "_BATCH_ORDERBOOK_RETRY_CHUNK", 2)
    monkeypatch.setenv("ZEUS_MARKET_DISCOVERY_ORDERBOOK_PREFETCH_MAX_RETRY_CHUNKS", "3")

    def _cand(i: int) -> tuple:
        return (
            0,
            0,
            i,
            {"slug": f"m{i}"},
            {"token_id": f"yes-{i}", "no_token_id": f"no-{i}"},
            f"cond-{i}",
            "buy_yes",
        )

    candidates = [_cand(i) for i in range(6)]
    calls: list[list[str]] = []

    class _Clob:
        def get_orderbook_snapshots(self, token_ids):
            call = list(token_ids)
            calls.append(call)
            if len(call) == 6:
                raise TimeoutError("handshake timeout")
            return {t: {"asset_id": t, "bids": [], "asks": []} for t in call}

    books = ms._prefetch_selected_orderbooks(_Clob(), candidates, deadline=None)

    assert calls == [
        ["yes-0", "yes-1", "yes-2", "yes-3", "yes-4", "yes-5"],
        ["yes-0", "yes-1"],
        ["yes-2", "yes-3"],
        ["yes-4", "yes-5"],
    ]
    assert sorted(books) == [f"yes-{i}" for i in range(6)]


def test_prefetch_failed_large_chunk_retries_bounded_priority_prefix_by_default(monkeypatch):
    """Live default must not let a failed 100-token POST fan out into every retry
    subchunk and overrun the scheduler interval."""

    monkeypatch.setattr(ms, "_BATCH_ORDERBOOK_CHUNK", 6)
    monkeypatch.setattr(ms, "_BATCH_ORDERBOOK_RETRY_CHUNK", 2)

    def _cand(i: int) -> tuple:
        return (
            0,
            0,
            i,
            {"slug": f"m{i}"},
            {"token_id": f"yes-{i}", "no_token_id": f"no-{i}"},
            f"cond-{i}",
            "buy_yes",
        )

    candidates = [_cand(i) for i in range(6)]
    calls: list[list[str]] = []

    class _Clob:
        def get_orderbook_snapshots(self, token_ids):
            call = list(token_ids)
            calls.append(call)
            if len(call) == 6:
                raise TimeoutError("handshake timeout")
            return {t: {"asset_id": t, "bids": [], "asks": []} for t in call}

    books = ms._prefetch_selected_orderbooks(_Clob(), candidates, deadline=None)

    assert calls == [
        ["yes-0", "yes-1", "yes-2", "yes-3", "yes-4", "yes-5"],
        ["yes-0", "yes-1"],
        ["yes-2", "yes-3"],
    ]
    assert sorted(books) == ["yes-0", "yes-1", "yes-2", "yes-3"]


def test_prefetch_retry_chunk_falls_back_to_bounded_singular_get(monkeypatch):
    """If POST /books is unhealthy even for tiny chunks, fetch a bounded priority
    prefix with singular /book instead of returning an empty price surface."""

    monkeypatch.setattr(ms, "_BATCH_ORDERBOOK_CHUNK", 6)
    monkeypatch.setattr(ms, "_BATCH_ORDERBOOK_RETRY_CHUNK", 2)
    monkeypatch.setenv("ZEUS_MARKET_DISCOVERY_ORDERBOOK_SINGULAR_FALLBACK_MAX_TOKENS", "3")

    def _cand(i: int) -> tuple:
        return (
            0,
            0,
            i,
            {"slug": f"m{i}"},
            {"token_id": f"yes-{i}", "no_token_id": f"no-{i}"},
            f"cond-{i}",
            "buy_yes",
        )

    candidates = [_cand(i) for i in range(6)]
    singular_calls: list[str] = []

    class _Clob:
        def get_orderbook_snapshots(self, token_ids):
            raise TimeoutError("books endpoint timeout")

        def get_orderbook_snapshot(self, token_id):
            singular_calls.append(token_id)
            return {"asset_id": token_id, "bids": [], "asks": []}

    books = ms._prefetch_selected_orderbooks(_Clob(), candidates, deadline=None)

    assert singular_calls == ["yes-0", "yes-1", "yes-2"]
    assert sorted(books) == ["yes-0", "yes-1", "yes-2"]


def test_prefetch_singular_fallback_stops_after_default_failure_cap(monkeypatch):
    """If both /books and /book are timing out, stop after a tiny failure sample."""

    monkeypatch.setattr(ms, "_BATCH_ORDERBOOK_CHUNK", 6)
    monkeypatch.setattr(ms, "_BATCH_ORDERBOOK_RETRY_CHUNK", 2)
    monkeypatch.setenv("ZEUS_MARKET_DISCOVERY_ORDERBOOK_PREFETCH_MAX_RETRY_CHUNKS", "3")
    monkeypatch.setenv("ZEUS_MARKET_DISCOVERY_ORDERBOOK_SINGULAR_FALLBACK_MAX_TOKENS", "4")

    def _cand(i: int) -> tuple:
        return (
            0,
            0,
            i,
            {"slug": f"m{i}"},
            {"token_id": f"yes-{i}", "no_token_id": f"no-{i}"},
            f"cond-{i}",
            "buy_yes",
        )

    candidates = [_cand(i) for i in range(6)]
    singular_calls: list[str] = []

    class _Clob:
        def get_orderbook_snapshots(self, token_ids):
            raise TimeoutError("books endpoint timeout")

        def get_orderbook_snapshot(self, token_id):
            singular_calls.append(token_id)
            raise TimeoutError("book endpoint timeout")

    books = ms._prefetch_selected_orderbooks(_Clob(), candidates, deadline=None)

    assert singular_calls == ["yes-0", "yes-1"]
    assert books == {}


def test_prefetch_missing_orderbook_uses_fresh_feasibility_book(monkeypatch):
    """Priority substrate can use the live price-channel witness when /books misses."""

    conn = _make_in_memory_trade_db()
    conn.executescript(
        """
        CREATE TABLE execution_feasibility_evidence (
            evidence_id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL,
            condition_id TEXT NOT NULL,
            token_id TEXT NOT NULL,
            outcome_label TEXT NOT NULL,
            direction TEXT NOT NULL,
            quote_seen_at TEXT NOT NULL,
            book_hash_before TEXT,
            best_bid_before REAL,
            best_ask_before REAL,
            depth_before_json TEXT,
            created_at TEXT NOT NULL,
            schema_version INTEGER NOT NULL
        );
        CREATE INDEX idx_execution_feasibility_evidence_token_time
            ON execution_feasibility_evidence(token_id, quote_seen_at);
        CREATE INDEX idx_execution_feasibility_evidence_token_created
            ON execution_feasibility_evidence(token_id, created_at DESC);
        """
    )
    conn.execute(
        """
        INSERT INTO execution_feasibility_evidence (
            evidence_id, event_id, condition_id, token_id, outcome_label, direction,
            quote_seen_at, book_hash_before, best_bid_before, best_ask_before,
            depth_before_json, created_at, schema_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "e1",
            "evt1",
            "cond-1",
            "yes-1",
            "YES",
            "buy_yes",
            _NOW.isoformat(),
            "hash-1",
            0.24,
            0.27,
            '{"bids":[{"price":"0.24","size":"10"}],"asks":[{"price":"0.27","size":"12"}]}',
            _NOW.isoformat(),
            1,
        ),
    )
    conn.commit()
    outcome = {
        "token_id": "yes-1",
        "no_token_id": "no-1",
        "min_tick_size": "0.001",
        "min_order_size": "1",
        "neg_risk": True,
    }
    candidates = [(0, 0, 0, {"slug": "m1"}, outcome, "cond-1", "buy_yes")]

    books = ms._prefetch_selected_orderbooks_from_feasibility(
        conn,
        candidates,
        captured=_NOW,
        already_prefetched=set(),
    )

    assert sorted(books) == ["yes-1"]
    assert books["yes-1"]["asset_id"] == "yes-1"
    assert books["yes-1"]["asks"][0]["price"] == "0.27"
    assert books["yes-1"]["tick_size"] == "0.001"
    assert books["yes-1"]["neg_risk"] is True


def test_prefetch_missing_orderbook_uses_same_token_feasibility_book_any_direction(monkeypatch):
    """Book evidence is token-level; a sell-side witness can hydrate buy substrate."""

    conn = _make_in_memory_trade_db()
    conn.executescript(
        """
        CREATE TABLE execution_feasibility_evidence (
            evidence_id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL,
            condition_id TEXT NOT NULL,
            token_id TEXT NOT NULL,
            outcome_label TEXT NOT NULL,
            direction TEXT NOT NULL,
            quote_seen_at TEXT NOT NULL,
            book_hash_before TEXT,
            best_bid_before REAL,
            best_ask_before REAL,
            depth_before_json TEXT,
            created_at TEXT NOT NULL,
            schema_version INTEGER NOT NULL
        );
        CREATE INDEX idx_execution_feasibility_evidence_token_time
            ON execution_feasibility_evidence(token_id, quote_seen_at);
        CREATE INDEX idx_execution_feasibility_evidence_token_created
            ON execution_feasibility_evidence(token_id, created_at DESC);
        """
    )
    conn.execute(
        """
        INSERT INTO execution_feasibility_evidence (
            evidence_id, event_id, condition_id, token_id, outcome_label, direction,
            quote_seen_at, book_hash_before, best_bid_before, best_ask_before,
            depth_before_json, created_at, schema_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "e1",
            "evt1",
            "cond-1",
            "yes-1",
            "YES",
            "sell_yes",
            _NOW.isoformat(),
            "hash-1",
            0.24,
            0.27,
            '{"bids":[{"price":"0.24","size":"10"}],"asks":[{"price":"0.27","size":"12"}]}',
            _NOW.isoformat(),
            1,
        ),
    )
    conn.commit()
    outcome = {
        "token_id": "yes-1",
        "no_token_id": "no-1",
        "min_tick_size": "0.001",
        "min_order_size": "1",
        "neg_risk": True,
    }
    candidates = [(0, 0, 0, {"slug": "m1"}, outcome, "cond-1", "buy_yes")]

    books = ms._prefetch_selected_orderbooks_from_feasibility(
        conn,
        candidates,
        captured=_NOW,
        already_prefetched=set(),
    )

    assert sorted(books) == ["yes-1"]
    assert books["yes-1"]["asset_id"] == "yes-1"
    assert books["yes-1"]["asks"][0]["price"] == "0.27"


def test_prefetch_missing_orderbook_hydrates_from_top_of_book_feasibility(monkeypatch):
    """Live price-channel rows may carry top-of-book without full depth JSON."""

    conn = _make_in_memory_trade_db()
    conn.executescript(
        """
        CREATE TABLE execution_feasibility_evidence (
            evidence_id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL,
            condition_id TEXT NOT NULL,
            token_id TEXT NOT NULL,
            outcome_label TEXT NOT NULL,
            direction TEXT NOT NULL,
            quote_seen_at TEXT NOT NULL,
            book_hash_before TEXT,
            best_bid_before REAL,
            best_ask_before REAL,
            depth_before_json TEXT,
            created_at TEXT NOT NULL,
            schema_version INTEGER NOT NULL
        );
        CREATE INDEX idx_execution_feasibility_evidence_token_time
            ON execution_feasibility_evidence(token_id, quote_seen_at);
        CREATE INDEX idx_execution_feasibility_evidence_token_created
            ON execution_feasibility_evidence(token_id, created_at DESC);
        """
    )
    conn.execute(
        """
        INSERT INTO execution_feasibility_evidence (
            evidence_id, event_id, condition_id, token_id, outcome_label, direction,
            quote_seen_at, book_hash_before, best_bid_before, best_ask_before,
            depth_before_json, created_at, schema_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "e1",
            "evt1",
            "cond-1",
            "yes-1",
            "YES",
            "buy_yes",
            _NOW.isoformat(),
            "hash-1",
            0.24,
            0.27,
            "",
            _NOW.isoformat(),
            1,
        ),
    )
    conn.commit()
    outcome = {
        "token_id": "yes-1",
        "no_token_id": "no-1",
        "min_tick_size": "0.001",
        "min_order_size": "5",
        "neg_risk": False,
    }
    candidates = [(0, 0, 0, {"slug": "m1"}, outcome, "cond-1", "buy_yes")]

    books = ms._prefetch_selected_orderbooks_from_feasibility(
        conn,
        candidates,
        captured=_NOW,
        already_prefetched=set(),
    )

    assert sorted(books) == ["yes-1"]
    assert books["yes-1"]["bids"] == [{"price": "0.24", "size": "1"}]
    assert books["yes-1"]["asks"] == [{"price": "0.27", "size": "1"}]
    assert books["yes-1"]["min_order_size"] == "5"


def test_snapshot_capture_retries_short_sqlite_lock(monkeypatch):
    """A transient trade-DB WAL lock must not drop an otherwise fresh bin."""

    monkeypatch.setenv("ZEUS_SNAPSHOT_CAPTURE_SQLITE_LOCK_RETRIES", "2")
    monkeypatch.setattr(ms.time, "sleep", lambda _seconds: None)

    markets = [_make_market("Tokyo", 1, metric="lowest", target_date="2026-06-08")]
    calls: list[str] = []

    def _flaky_capture(conn, *, market, decision, clob, captured_at, scan_authority, execution_side="BUY", **kwargs):
        calls.append(str(decision.tokens.get("market_id") or ""))
        if len(calls) == 1:
            raise sqlite3.OperationalError("database is locked")

    clob = _make_clob_mock()
    conn = _make_in_memory_trade_db()

    with patch("src.data.market_scanner.capture_executable_market_snapshot", side_effect=_flaky_capture):
        summary = refresh_executable_market_substrate_snapshots(
            conn,
            markets=markets,
            clob=clob,
            captured_at=_NOW,
            scan_authority="VERIFIED",
            budget_seconds=5.0,
            max_outcomes=1,
        )

    assert len(calls) == 2
    assert summary["attempted"] == 1
    assert summary["inserted"] == 1
    assert summary["failed"] == 0
    assert "failure_samples" not in summary
    assert summary["executable_substrate_coverage_status"] == "FULL"


def test_default_snapshot_capture_reserve_keeps_prefetch_from_starving_capture(monkeypatch):
    """The default pending-family path must reserve a real capture phase.

    Live-shadow showed the old 6s default let /books prefetch consume most of a
    warm tick and left CLOB capture writing only a handful of snapshots.
    """

    monkeypatch.delenv("ZEUS_MARKET_DISCOVERY_SNAPSHOT_CAPTURE_RESERVE_SECONDS", raising=False)

    summary_budget = 15.0
    assert ms._snapshot_capture_reserve_seconds_from_env(summary_budget) == pytest.approx(12.0)


def test_illiquid_identity_capture_skips_fee_rate_http():
    """No-ask identity rows are non-executable and must not spend fee-rate HTTP."""

    condition_id = "0x" + "1" * 64
    yes_token = "0x" + "2" * 64
    no_token = "0x" + "3" * 64
    market = {
        "event_id": "evt-illiquid",
        "slug": "highest-temperature-in-chicago-on-june-7-2026",
        "city": ms.cities_by_name.get("Chicago", "Chicago"),
        "target_date": "2026-06-07",
        "temperature_metric": "high",
        "outcomes": [
            {
                "condition_id": condition_id,
                "market_id": condition_id,
                "question_id": condition_id,
                "token_id": yes_token,
                "no_token_id": no_token,
                "active": True,
                "closed": False,
                "accepting_orders": True,
                "enable_orderbook": True,
                "gamma_market_raw": {
                    "conditionId": condition_id,
                    "questionID": condition_id,
                    "active": True,
                    "closed": False,
                    "acceptingOrders": True,
                    "enableOrderBook": True,
                    "clobTokenIds": [yes_token, no_token],
                },
            }
        ],
    }
    decision = SimpleNamespace(
        tokens={"token_id": yes_token, "no_token_id": no_token, "market_id": condition_id},
        edge=SimpleNamespace(direction="buy_yes"),
    )

    class IlliquidClob:
        def get_clob_market_info(self, _condition_id: str) -> dict:
            return {
                "condition_id": condition_id,
                "tokens": [{"token_id": yes_token}, {"token_id": no_token}],
                "archived": False,
                "enable_order_book": True,
                "accepting_orders": True,
                "tick_size": "0.01",
                "min_order_size": "5",
                "neg_risk": True,
            }

        def get_orderbook_snapshot(self, _token_id: str) -> dict:
            return {
                "asset_id": yes_token,
                "tick_size": "0.01",
                "min_order_size": "5",
                "neg_risk": True,
                "bids": [{"price": "0.01", "size": "1"}],
                "asks": [],
            }

        def get_fee_rate_details(self, _token_id: str) -> dict:
            raise AssertionError("illiquid identity capture must not fetch fee-rate")

    captured = []
    with (
        patch("src.data.market_scanner.insert_snapshot", side_effect=lambda _conn, snapshot: captured.append(snapshot)),
        patch("src.data.market_scanner._write_book_hash_transition"),
    ):
        ms.capture_executable_market_snapshot(
            _make_in_memory_trade_db(),
            market=market,
            decision=decision,
            clob=IlliquidClob(),
            captured_at=_NOW,
            scan_authority="VERIFIED",
            tolerate_missing_book=True,
        )

    assert len(captured) == 1
    assert captured[0].tradeability_status.executable_allowed is False
    assert captured[0].tradeability_status.reason == "clob_no_ask_illiquid"
    assert captured[0].fee_details["source"] == "not_applicable_illiquid_identity"
    assert captured[0].fee_details["fee_rate_fraction"] == pytest.approx(0.0)


def test_substrate_identity_capture_skips_clob_market_info_http(monkeypatch):
    """Background substrate identity rows should not spend one /markets HTTP per bin."""

    monkeypatch.delenv("ZEUS_PENDING_SUBSTRATE_SYNTHETIC_CLOB_MARKET_INFO", raising=False)
    condition_id = "0x" + "4" * 64
    yes_token = "0x" + "5" * 64
    no_token = "0x" + "6" * 64
    market = {
        "event_id": "evt-liquid",
        "slug": "highest-temperature-in-austin-on-june-7-2026",
        "city": ms.cities_by_name.get("Austin", "Austin"),
        "target_date": "2026-06-07",
        "temperature_metric": "high",
        "outcomes": [
            {
                "condition_id": condition_id,
                "market_id": condition_id,
                "question_id": condition_id,
                "token_id": yes_token,
                "no_token_id": no_token,
                "active": True,
                "closed": False,
                "accepting_orders": True,
                "enable_orderbook": True,
                "gamma_market_raw": {
                    "conditionId": condition_id,
                    "questionID": condition_id,
                    "active": True,
                    "closed": False,
                    "acceptingOrders": True,
                    "enableOrderBook": True,
                    "clobTokenIds": [yes_token, no_token],
                    "tradability_authority": "persisted_snapshot_reconstruction",
                },
            }
        ],
    }
    decision = SimpleNamespace(
        tokens={"token_id": yes_token, "no_token_id": no_token, "market_id": condition_id},
        edge=SimpleNamespace(direction="buy_yes"),
    )

    class LiquidSubstrateClob:
        def get_clob_market_info(self, _condition_id: str) -> dict:
            raise AssertionError("substrate identity capture must not fetch /markets")

        def get_orderbook_snapshot(self, _token_id: str) -> dict:
            return {
                "asset_id": yes_token,
                "tick_size": "0.01",
                "min_order_size": "5",
                "neg_risk": True,
                "bids": [{"price": "0.40", "size": "10"}],
                "asks": [{"price": "0.42", "size": "10"}],
            }

        def get_fee_rate_details(self, _token_id: str) -> dict:
            raise AssertionError("substrate identity capture must not fetch /fee-rate")

    captured = []
    with (
        patch("src.data.market_scanner.insert_snapshot", side_effect=lambda _conn, snapshot: captured.append(snapshot)),
        patch("src.data.market_scanner._write_book_hash_transition"),
    ):
        ms.capture_executable_market_snapshot(
            _make_in_memory_trade_db(),
            market=market,
            decision=decision,
            clob=LiquidSubstrateClob(),
            captured_at=_NOW,
            scan_authority="VERIFIED",
            tolerate_missing_book=True,
        )

    assert len(captured) == 1
    assert captured[0].tradeability_status.executable_allowed is True
    assert captured[0].fee_details["source"] == "weather_fee_contract_substrate_identity"
    assert captured[0].fee_details["authority"] == "local_weather_fee_contract"
    assert captured[0].fee_details["submit_boundary_revalidates_fee"] is True
    assert captured[0].fee_details["fee_rate_fraction"] == pytest.approx(0.05)


def test_substrate_identity_capture_uses_contract_fee_without_http(monkeypatch):
    """Background substrate refresh should not fetch fee rate for every token."""

    monkeypatch.delenv("ZEUS_PENDING_SUBSTRATE_SYNTHETIC_CLOB_MARKET_INFO", raising=False)
    condition_id = "0x" + "7" * 64
    yes_token = "0x" + "8" * 64
    no_token = "0x" + "9" * 64
    market = {
        "event_id": "evt-fee-cache",
        "slug": "highest-temperature-in-austin-on-june-7-2026",
        "city": ms.cities_by_name.get("Austin", "Austin"),
        "target_date": "2026-06-07",
        "temperature_metric": "high",
        "outcomes": [
            {
                "condition_id": condition_id,
                "market_id": condition_id,
                "question_id": condition_id,
                "token_id": yes_token,
                "no_token_id": no_token,
                "active": True,
                "closed": False,
                "accepting_orders": True,
                "enable_orderbook": True,
                "gamma_market_raw": {
                    "conditionId": condition_id,
                    "questionID": condition_id,
                    "active": True,
                    "closed": False,
                    "acceptingOrders": True,
                    "enableOrderBook": True,
                    "clobTokenIds": [yes_token, no_token],
                    "tradability_authority": "persisted_snapshot_reconstruction",
                },
            }
        ],
    }

    class FamilyFeeClob:
        def __init__(self) -> None:
            self.fee_tokens: list[str] = []

        def get_clob_market_info(self, _condition_id: str) -> dict:
            raise AssertionError("substrate identity capture must not fetch /markets")

        def get_orderbook_snapshot(self, token_id: str) -> dict:
            return {
                "asset_id": token_id,
                "tick_size": "0.01",
                "min_order_size": "5",
                "neg_risk": True,
                "bids": [{"price": "0.40", "size": "10"}],
                "asks": [{"price": "0.42", "size": "10"}],
            }

        def get_fee_rate_details(self, token_id: str) -> dict:
            self.fee_tokens.append(token_id)
            raise AssertionError("substrate identity capture must not fetch /fee-rate")

    captured = []
    clob = FamilyFeeClob()
    fee_cache: dict[str, dict[str, object]] = {}
    with (
        patch("src.data.market_scanner.insert_snapshot", side_effect=lambda _conn, snapshot: captured.append(snapshot)),
        patch("src.data.market_scanner._write_book_hash_transition"),
    ):
        for direction in ("buy_yes", "buy_no"):
            ms.capture_executable_market_snapshot(
                _make_in_memory_trade_db(),
                market=market,
                decision=SimpleNamespace(
                    tokens={"token_id": yes_token, "no_token_id": no_token, "market_id": condition_id},
                    edge=SimpleNamespace(direction=direction),
                ),
                clob=clob,
                captured_at=_NOW,
                scan_authority="VERIFIED",
                fee_details_cache=fee_cache,
                tolerate_missing_book=True,
            )

    assert clob.fee_tokens == []
    assert len(captured) == 2
    assert captured[0].fee_details["source"] == "weather_fee_contract_substrate_identity"
    assert captured[1].fee_details["source"] == "weather_fee_contract_substrate_identity"
    assert captured[1].fee_details["token_id"] == no_token
    assert captured[1].fee_details["fee_rate_fraction"] == pytest.approx(0.05)


def test_unlimited_pending_refresh_completes_family_before_next_city():
    """max_outcomes=0 is the pending-family path; it must complete families.

    The FDR gate needs a complete family proof.  If unlimited refresh interleaves
    one condition across every city, a tight live budget keeps all families
    partially fresh and reactor remains EXECUTABLE_SNAPSHOT_BLOCKED.
    """
    first = _make_family_market_with_tail("Chicago")
    second = _make_family_market_with_tail("Austin", target_date="2026-06-05")
    second["condition_ids"] = []
    for idx, outcome in enumerate(second["outcomes"], start=11):
        cid = (f"0x{idx:02x}" + "d" * 62)[:66]
        outcome["condition_id"] = cid
        outcome["market_id"] = cid
        outcome["question_id"] = cid
        outcome["token_id"] = (f"0x{idx:02x}" + "e" * 62)[:66]
        outcome["no_token_id"] = (f"0x{idx:02x}" + "f" * 62)[:66]
        outcome["gamma_market_raw"]["conditionId"] = cid
        second["condition_ids"].append(cid)
    captured_slugs: list[str] = []

    def _spy_capture(conn, *, market, decision, clob, captured_at, scan_authority, execution_side="BUY", **kwargs):
        captured_slugs.append(str(market.get("slug") or ""))

    clob = _make_clob_mock()
    conn = _make_in_memory_trade_db()

    with patch("src.data.market_scanner.capture_executable_market_snapshot", side_effect=_spy_capture):
        summary = refresh_executable_market_substrate_snapshots(
            conn,
            markets=[first, second],
            clob=clob,
            captured_at=_NOW,
            scan_authority="VERIFIED",
            max_outcomes=0,
        )

    assert summary["selected_executable_snapshot_count"] == 12
    assert len(captured_slugs) == 12
    assert len(set(captured_slugs[:6])) == 1
    assert len(set(captured_slugs[6:])) == 1
    assert captured_slugs[0] != captured_slugs[6]
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


# ---------------------------------------------------------------------------
# FDR (NON_EXECUTABLE_BIN_IDENTITY_REACHES_CAPTURE): every active MECE family
# sibling — including non-tradeable (orderbook-disabled) tail bins — must reach
# capture so the executable_market_snapshots family is COMPLETE.
# ---------------------------------------------------------------------------

def _make_family_market_with_tail(
    city_name: str = "Chicago",
    target_date: str = "2026-06-04",
    metric: str = "highest",
) -> dict:
    """One family event whose outcomes include 2 EXECUTABLE bins + 1 NON-EXECUTABLE
    tail bin that still carries a VALID identity (condition_id + yes/no tokens).

    Mirrors live Gamma: the hot/cold tail bins of a weather MECE family have
    enableOrderBook=False (no liquidity), so ``_market_child_is_tradable`` returns
    False → ``executable=False`` — yet their condition_id + token identity is fully
    known (present in market_events). ``find_weather_markets`` surfaces them in
    ``support_outcomes`` (ALL bins), so they DO reach
    ``refresh_executable_market_substrate_snapshots`` with executable=False.
    """
    city_slug = city_name.lower().replace(" ", "-")
    slug = _slug(city_slug, metric, target_date)
    city_obj = ms.cities_by_name.get(city_name, city_name)

    def _bin(seq: int, *, executable: bool) -> dict:
        cid = f"0x{seq:02x}" + "c" * 62
        cid = cid[:66]
        return {
            "title": f"bin-{seq}",
            "condition_id": cid,
            "market_id": cid,
            # Identity is ALWAYS known — even for the non-executable tail bin.
            "token_id": f"0x{seq:02x}" + "a" * 62,
            "no_token_id": f"0x{seq:02x}" + "b" * 62,
            "question_id": cid,
            "executable": executable,
            "accepting_orders": executable,
            "closed": False,
            "enable_orderbook": executable,
            "gamma_market_raw": {
                "conditionId": cid,
                "acceptingOrders": executable,
                "closed": False,
                "active": True,
                "enableOrderBook": executable,
            },
        }

    return {
        "event_id": f"evt-{city_slug}-{metric}-{target_date}",
        "slug": slug,
        "title": f"{metric.capitalize()} temperature in {city_name} on {target_date}?",
        "city": city_obj,
        "target_date": target_date,
        "temperature_metric": metric,
        "hours_to_resolution": 12.0,
        "hours_since_open": 6.0,
        # support_outcomes = ALL bins (find_weather_markets surfaces every MECE
        # sibling regardless of executability). Two liquid bins + one illiquid tail.
        "outcomes": [
            _bin(1, executable=True),
            _bin(2, executable=True),
            _bin(3, executable=False),  # non-tradeable tail bin, valid identity
        ],
        "condition_ids": [
            o["condition_id"]
            for o in (_bin(1, executable=True), _bin(2, executable=True))
        ],
        "source_contract": {"status": "MATCH"},
    }


def test_non_executable_tail_bin_identity_reaches_capture():
    """FDR (NON_EXECUTABLE_BIN_IDENTITY_REACHES_CAPTURE): the substrate refresh
    must hand EVERY active MECE family sibling to ``capture_executable_market_snapshot``,
    including the non-tradeable (orderbook-disabled) tail bin — because the entry
    gate and the FDR full-family proof require an executable_market_snapshots row
    for EVERY family condition_id, not just the liquid subset.

    Root cause (2026-06-04 EXECUTABLE_SNAPSHOT_BLOCKED): the candidate-enumeration
    loop dropped non-executable outcomes (``if not outcome.get("executable"):
    continue``) BEFORE capture, so illiquid tail bins never got an IDENTITY row.
    A weather family then stalled at 8/11 captured siblings forever, the entry gate
    (executable_snapshot_gate_from_trade_conn) required all 11, the event retried 8×
    and dead-lettered as EXECUTABLE_SNAPSHOT_BLOCKED → zero receipts reached the
    trade_score edge gate. This contradicts the function's own design
    (``tolerate_missing_book=True``, "capture IDENTITY for every active MECE bin
    including illiquid no-ask tail bins").

    RED pre-fix: the executable filter drops bin-3 → only 2 of 3 condition_ids reach
    capture → assertion fails.
    GREEN post-fix: all 3 condition_ids reach capture (bin-3 captures as
    non-tradeable identity: executable_allowed=False, top_ask=None).
    """
    market = _make_family_market_with_tail()
    family_condition_ids = {o["condition_id"] for o in market["outcomes"]}
    assert len(family_condition_ids) == 3

    captured_condition_ids: set[str] = set()

    def _spy_capture(conn, *, market, decision, clob, captured_at, scan_authority,
                     execution_side="BUY", prefetched_orderbook=None,
                     tolerate_missing_book=False, **kwargs):
        cid = str(decision.tokens.get("market_id") or "")
        if cid:
            captured_condition_ids.add(cid)

    clob = _make_clob_mock()
    conn = _make_in_memory_trade_db()

    with patch(
        "src.data.market_scanner.capture_executable_market_snapshot",
        side_effect=_spy_capture,
    ):
        refresh_executable_market_substrate_snapshots(
            conn,
            markets=[market],
            clob=clob,
            captured_at=_NOW,
            scan_authority="VERIFIED",
            max_outcomes=0,  # UNLIMITED sentinel — mirrors refresh_pending_family_snapshots
        )

    missing = family_condition_ids - captured_condition_ids
    assert not missing, (
        "Every active MECE family sibling — including the non-tradeable tail bin — "
        "must reach capture so executable_market_snapshots is family-COMPLETE for the "
        "FDR full-family proof. Missing condition_ids (dropped before capture): "
        f"{sorted(missing)}. Captured: {sorted(captured_condition_ids)}. "
        "Pre-fix root cause: the executable-only filter in the candidate loop dropped "
        "non-executable (orderbook-disabled) bins, stalling families at N-of-M and "
        "raising EXECUTABLE_SNAPSHOT_BLOCKED."
    )

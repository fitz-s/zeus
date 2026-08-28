# Created: 2026-08-28
# Last reused or audited: 2026-08-28
# Authority basis: docs/operations/current/plans/reversal_plan_tier0_2026-08-24.md
#   item 26 (external review verdict) — the market-anchored fit provider must
#   survive across batches so its TTL (market_anchored_live_fit.DEFAULT_TTL)
#   is real, not reset to a cold provider every process_current_global_batch
#   call.
"""_market_anchored_correction_resolver reuses one provider across calls.

Before this fix, ``_market_anchored_correction_resolver`` constructed a brand
new ``MarketAnchoredFitProvider`` on every call — so its internal TTL cache
never survived past a single call, and the live path refit on every batch
instead of once per TTL. These tests pin the fix at the resolver's own
boundary: two resolver constructions (simulating two separate batches) within
the TTL must not trigger a second fit, and a construction after the TTL has
elapsed must.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import src.engine.global_batch_runtime as gbr
import src.calibration.market_anchored_live_fit as live_fit

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)


@dataclass(frozen=True)
class _Candidate:
    family_key: str
    bin_id: str
    side: str
    token_id: str


def _memory_world_conn(*, row_count: int, settled_at: datetime) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE settlement_attribution (
            attribution_id TEXT,
            q_in_bin REAL,
            market_in_bin_prob REAL,
            settled_in_bin INTEGER,
            direction TEXT,
            decision_posterior_computed_at TEXT,
            target_date TEXT,
            settled_at TEXT,
            graded_at TEXT
        )
        """
    )
    rows = []
    for i in range(row_count):
        decision_day = date(2026, 8, 1) + timedelta(days=i % 5)
        rows.append(
            {
                "attribution_id": f"row-{i}",
                "q_in_bin": 0.9,
                "market_in_bin_prob": 0.35,
                "settled_in_bin": i % 2,
                "direction": "buy_yes",
                "decision_posterior_computed_at": datetime.combine(
                    decision_day, datetime.min.time(), tzinfo=timezone.utc
                ).isoformat(),
                "target_date": (decision_day + timedelta(days=1)).isoformat(),
                "settled_at": settled_at.isoformat(),
                "graded_at": None,
            }
        )
    conn.executemany(
        """
        INSERT INTO settlement_attribution (
            attribution_id, q_in_bin, market_in_bin_prob, settled_in_bin,
            direction, decision_posterior_computed_at, target_date,
            settled_at, graded_at
        ) VALUES (
            :attribution_id, :q_in_bin, :market_in_bin_prob, :settled_in_bin,
            :direction, :decision_posterior_computed_at, :target_date,
            :settled_at, :graded_at
        )
        """,
        rows,
    )
    conn.commit()
    return conn


def setup_function(_fn) -> None:
    gbr._MARKET_ANCHORED_FIT_PROVIDER = None
    gbr._MARKET_ANCHORED_FIT_PROVIDER_CONN = None


def teardown_function(_fn) -> None:
    gbr._MARKET_ANCHORED_FIT_PROVIDER = None
    gbr._MARKET_ANCHORED_FIT_PROVIDER_CONN = None


def _resolve_once(conn: sqlite3.Connection, *, decision_at_utc: datetime):
    resolver = gbr._market_anchored_correction_resolver(
        conn,
        target_date_by_family={"fam-1": decision_at_utc.date() + timedelta(days=1)},
    )
    assert resolver is not None
    return resolver(
        _Candidate(family_key="fam-1", bin_id="bin-1", side="YES", token_id="tok-1"),
        0.9,
        0.35,
        decision_at_utc,
    )


def test_two_resolver_constructions_within_ttl_share_one_fit(monkeypatch):
    conn = _memory_world_conn(row_count=40, settled_at=NOW - timedelta(days=3))
    fit_calls: list[int] = []
    real_fit = live_fit.fit

    def counting_fit(*args, **kwargs):
        fit_calls.append(1)
        return real_fit(*args, **kwargs)

    monkeypatch.setattr(live_fit, "fit", counting_fit)

    first = _resolve_once(conn, decision_at_utc=NOW)
    assert first is not None
    assert len(fit_calls) == 1

    # A second, independent resolver construction — as happens on the next
    # process_current_global_batch call — must reuse the module-level
    # provider and its still-fresh TTL window, not refit.
    second = _resolve_once(conn, decision_at_utc=NOW + timedelta(hours=1))
    assert second is not None
    assert len(fit_calls) == 1


def test_resolver_construction_after_ttl_expiry_refits(monkeypatch):
    conn = _memory_world_conn(row_count=40, settled_at=NOW - timedelta(days=3))
    fit_calls: list[int] = []
    real_fit = live_fit.fit

    def counting_fit(*args, **kwargs):
        fit_calls.append(1)
        return real_fit(*args, **kwargs)

    monkeypatch.setattr(live_fit, "fit", counting_fit)

    first = _resolve_once(conn, decision_at_utc=NOW)
    assert first is not None
    assert len(fit_calls) == 1

    after_ttl = NOW + live_fit.DEFAULT_TTL + timedelta(minutes=1)
    second = _resolve_once(conn, decision_at_utc=after_ttl)
    assert second is not None
    assert len(fit_calls) == 2


def test_resolver_conn_is_not_captured_stale_across_constructions(monkeypatch):
    """A second batch's own world_conn must be the one actually read on refit.

    Regression guard for the exact bug this fix removes: a provider whose
    connect callable closed over the FIRST call's world_conn would keep
    reading through that connection on every subsequent refit, even once a
    later batch supplied a different (or closed) connection object.
    """
    first_conn = _memory_world_conn(row_count=40, settled_at=NOW - timedelta(days=3))
    _resolve_once(first_conn, decision_at_utc=NOW)
    assert gbr._MARKET_ANCHORED_FIT_PROVIDER_CONN is first_conn

    second_conn = _memory_world_conn(row_count=40, settled_at=NOW - timedelta(days=3))
    after_ttl = NOW + live_fit.DEFAULT_TTL + timedelta(minutes=1)
    result = _resolve_once(second_conn, decision_at_utc=after_ttl)

    assert gbr._MARKET_ANCHORED_FIT_PROVIDER_CONN is second_conn
    assert result is not None

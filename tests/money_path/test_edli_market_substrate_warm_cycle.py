# Created: 2026-06-01
# Last reused/audited: 2026-06-01
# Authority basis: src/main.py:_edli_event_reactor_cycle (inline _refresh_pending_family_snapshots
#   coupling) + _edli_bankroll_warm_cycle precedent (#45 follow-up, the decoupled-warm pattern) +
#   src/main.py:_refresh_pending_family_snapshots (universe Gamma scan + per-token CLOB capture,
#   measured 76s cold for _get_active_events alone).
"""Relationship test for the dedicated EDLI market-substrate warm cycle (throughput).

Cross-module invariant under test (Fitz methodology — test the boundary between the
reactor's DECISION loop and the venue-I/O SUBSTRATE refresh, not a single function):

    The expensive executable-market-snapshot refresh (a universe Gamma scan +
    per-token CLOB /book capture — measured ~76s cold for the active-events scan
    alone, plus per-token book fetches) MUST be DECOUPLED from the per-cycle EDLI
    reactor decision loop. The reactor cycle must read ALREADY-captured snapshots
    (DB-only, microseconds) so a crossable positive-edge candidate reaches submit
    within a fast cycle, instead of the reactor blocking on the refresh until the
    cycle wall-clock blows past the APScheduler interval (overlapping triggers are
    coalesced/skipped → 0 completed cycles → 0 trades).

Background (live evidence 2026-06-01):
    `_edli_event_reactor_cycle` called `_refresh_pending_family_snapshots(...)` INLINE
    at the top of every cycle. That helper runs `find_weather_markets()` →
    `_get_active_events(include_slug_pattern=True)`, a full-universe Gamma scan
    benchmarked at ~76s COLD (TTL 300s, so it re-runs roughly every cycle), followed
    by per-token CLOB `/book` capture across all pending-family bins. The reactor
    interval is 1 min with max_instances=1/coalesce=True, so a 20+ min refresh-bound
    cycle starves `process_pending` → "EDLI reactor cycle result" never logs and no
    crossable candidate ever reaches submit. THIS coupling is the structural defect.

The fix MOVES the refresh to a dedicated decoupled scheduler job (mirroring
`_edli_bankroll_warm_cycle`, #45). It does NOT change any decision, gate, or the
just-in-time submit `/book` (the reactor's no-submit path + full gate chain + JIT
submit are byte-for-byte unchanged — they just read snapshots a background job
captured). Fail-closed is preserved: a family not yet captured still requeues via
the reactor's existing EXECUTABLE_SNAPSHOT_RETRY path.

These tests lock:
  RED-before-fix #1 (coupling proof): the reactor cycle must NOT invoke
    `_refresh_pending_family_snapshots` inline (the expensive venue-I/O is off the
    decision critical path).
  RED-before-fix #2 (decoupled job exists): a dedicated `_edli_market_substrate_warm_cycle`
    job exists and, when EDLI is enabled, DOES invoke the refresh exactly once.
  Gate: when edli_v1 is disabled the warm job does no refresh.
  Fail-soft: a refresh that raises does NOT propagate out of the warm job.
"""

from __future__ import annotations

import inspect
import json
import re
import sqlite3
from types import SimpleNamespace

import src.main as main_module
from src.contracts.executable_market_snapshot import FRESHNESS_WINDOW_DEFAULT


def _enable_edli_cfg(monkeypatch, *, enabled: bool = True) -> None:
    monkeypatch.setattr(
        main_module,
        "_settings_section",
        lambda name, default=None: (
            {"enabled": enabled} if name == "edli_v1" else (default if default is not None else {})
        ),
    )


def test_reactor_cycle_does_not_refresh_inline():
    """RED-before-fix: the reactor decision cycle must not call the expensive
    `_refresh_pending_family_snapshots` inline — that venue-I/O belongs on the
    decoupled warm job, off the decision critical path.

    Static-source assertion (the inline call is a direct lexical call in the cycle
    body) so the test is deterministic and does not depend on DB/venue state.
    """
    src = inspect.getsource(main_module._edli_event_reactor_cycle)
    assert "_refresh_pending_family_snapshots(" not in src, (
        "reactor cycle still calls _refresh_pending_family_snapshots INLINE — the "
        "expensive universe Gamma scan + per-token CLOB capture must be decoupled to "
        "the dedicated _edli_market_substrate_warm_cycle so the reactor reaches submit "
        "in seconds."
    )


def test_pending_family_refresh_does_not_call_global_weather_discovery():
    """Pending-family substrate refresh must stay scoped to exact pending family slugs.

    A global find_weather_markets_or_raise scan is too slow for the warm cadence and
    has a separate discovery budget; putting it in this path makes the substrate
    warmer overrun and starves the reactor of fresh receipt flow.
    """
    src = inspect.getsource(main_module._refresh_pending_family_snapshots)

    assert "find_weather_markets_or_raise" not in src


def test_pending_family_refresh_default_budget_stays_inside_price_ttl():
    src = inspect.getsource(main_module._refresh_pending_family_snapshots)
    match = re.search(
        r'ZEUS_REACTOR_REFRESH_BUDGET_SECONDS", "([0-9.]+)"',
        src,
    )

    assert match is not None
    assert float(match.group(1)) < FRESHNESS_WINDOW_DEFAULT.total_seconds()


def test_market_discovery_defers_while_reactor_active():
    src = inspect.getsource(main_module._market_discovery_cycle)

    assert "_edli_reactor_active()" in src
    assert "market_discovery deferred" in src


def test_market_substrate_warm_cycle_exists_and_refreshes_once(monkeypatch):
    """GREEN-after-fix: a dedicated warm job exists and, when EDLI is enabled, invokes
    the family-snapshot refresh exactly once per tick."""
    assert hasattr(main_module, "_edli_market_substrate_warm_cycle"), (
        "expected a dedicated _edli_market_substrate_warm_cycle scheduler job (mirroring "
        "_edli_bankroll_warm_cycle) that owns the decoupled substrate refresh."
    )

    calls: list[int] = []
    monkeypatch.setattr(
        main_module,
        "_refresh_pending_family_snapshots",
        lambda *a, **k: calls.append(1),
    )
    # The warm job opens world/forecasts connections; stub them so no real DB/venue work
    # runs. The test only asserts the refresh is invoked exactly once.
    import src.state.db as state_db

    monkeypatch.setattr(state_db, "get_world_connection", lambda: _FakeConn())
    monkeypatch.setattr(
        main_module, "get_forecasts_connection_read_only", lambda: _FakeConn(), raising=False
    )
    _enable_edli_cfg(monkeypatch, enabled=True)

    main_module._edli_market_substrate_warm_cycle()
    assert calls == [1], "warm job must invoke the family-snapshot refresh exactly once"


def test_market_substrate_warm_cycle_runs_while_reactor_active(monkeypatch):
    """The warm job owns an independent cadence; reactor-active must not starve price refresh."""
    calls: list[int] = []
    monkeypatch.setattr(
        main_module,
        "_refresh_pending_family_snapshots",
        lambda *a, **k: calls.append(1),
    )
    import src.state.db as state_db

    monkeypatch.setattr(state_db, "get_world_connection", lambda: _FakeConn())
    monkeypatch.setattr(
        main_module, "get_forecasts_connection_read_only", lambda: _FakeConn(), raising=False
    )
    _enable_edli_cfg(monkeypatch, enabled=True)

    assert main_module._edli_reactor_active_lock.acquire(blocking=False)
    try:
        main_module._edli_market_substrate_warm_cycle()
    finally:
        main_module._edli_reactor_active_lock.release()

    assert calls == [1]


def test_market_substrate_warm_cycle_noop_when_edli_disabled(monkeypatch):
    """Config gate: disabled edli_v1 → no refresh side effect."""
    calls: list[int] = []
    monkeypatch.setattr(
        main_module,
        "_refresh_pending_family_snapshots",
        lambda *a, **k: calls.append(1),
    )
    import src.state.db as state_db

    monkeypatch.setattr(state_db, "get_world_connection", lambda: _FakeConn())
    monkeypatch.setattr(
        main_module, "get_forecasts_connection_read_only", lambda: _FakeConn(), raising=False
    )
    _enable_edli_cfg(monkeypatch, enabled=False)

    main_module._edli_market_substrate_warm_cycle()
    assert calls == [], "disabled edli_v1 must skip the refresh"


def test_market_substrate_warm_cycle_failsoft_on_refresh_error(monkeypatch):
    """Fail-soft: a refresh that raises must not propagate out of the warm job (the
    next tick retries; the reactor's EXECUTABLE_SNAPSHOT_RETRY keeps decisions
    fail-closed in the interim)."""
    def _raising(*a, **k):
        raise RuntimeError("gamma scan timeout")

    monkeypatch.setattr(main_module, "_refresh_pending_family_snapshots", _raising)
    monkeypatch.setattr(main_module, "get_world_connection", lambda: _FakeConn(), raising=False)
    monkeypatch.setattr(
        main_module, "get_forecasts_connection_read_only", lambda: _FakeConn(), raising=False
    )
    _enable_edli_cfg(monkeypatch, enabled=True)

    # Must not raise.
    main_module._edli_market_substrate_warm_cycle()


def test_pending_family_refresh_order_prioritizes_new_target_dates():
    """A stale target_date must not consume the bounded substrate-refresh budget
    ahead of fresh families that can still emit a receipt."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE opportunity_events (
            event_id TEXT NOT NULL PRIMARY KEY,
            event_type TEXT NOT NULL,
            entity_key TEXT NOT NULL,
            source TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            available_at TEXT NOT NULL,
            received_at TEXT NOT NULL,
            causal_snapshot_id TEXT,
            payload_hash TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            priority INTEGER NOT NULL DEFAULT 0,
            expires_at TEXT,
            payload_json TEXT NOT NULL,
            schema_version INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE opportunity_event_processing (
            consumer_name TEXT NOT NULL,
            event_id TEXT NOT NULL,
            processing_status TEXT NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            claimed_at TEXT,
            processed_at TEXT,
            last_error TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (consumer_name, event_id)
        );
        CREATE INDEX idx_opportunity_event_processing_status
            ON opportunity_event_processing(consumer_name, processing_status, updated_at);
        """
    )

    def insert_event(event_id: str, city: str, target_date: str, available_at: str) -> None:
        payload = {"city": city, "target_date": target_date, "metric": "high"}
        conn.execute(
            """
            INSERT INTO opportunity_events (
                event_id, event_type, entity_key, source, observed_at, available_at,
                received_at, payload_hash, idempotency_key, priority, payload_json,
                schema_version, created_at
            ) VALUES (?, 'FORECAST_SNAPSHOT_READY', ?, 'test', ?, ?, ?, ?, ?, 50, ?, 1, ?)
            """,
            (
                event_id,
                event_id,
                available_at,
                available_at,
                available_at,
                event_id,
                event_id,
                json.dumps(payload),
                available_at,
            ),
        )
        conn.execute(
            """
            INSERT INTO opportunity_event_processing (
                consumer_name, event_id, processing_status, updated_at
            ) VALUES ('edli_reactor_v1', ?, 'pending', ?)
            """,
            (event_id, available_at),
        )

    insert_event("old-a", "Amsterdam", "2026-06-04", "2026-05-30T00:00:00+00:00")
    insert_event("old-b", "Milan", "2026-06-04", "2026-05-30T00:00:01+00:00")
    insert_event("fresh-a", "Seoul", "2026-06-06", "2026-06-05T00:00:00+00:00")
    insert_event("fresh-b", "Tokyo", "2026-06-06", "2026-06-05T00:00:01+00:00")

    capture = _CaptureConn(conn)
    rows = main_module._pending_family_rows_for_refresh(
        capture, consumer_name="edli_reactor_v1"
    )
    families = [(row[0], row[1], row[2]) for row in rows]

    assert [family[1] for family in families[:2]] == ["2026-06-06", "2026-06-06"]
    assert [family[1] for family in families[-2:]] == ["2026-06-04", "2026-06-04"]

    plan = _explain_plan(conn, capture.sql, capture.params)
    assert "USING INDEX idx_opportunity_event_processing_status" in plan
    assert "SCAN p" not in plan


def test_pending_family_refresh_uses_static_topology_cache_without_gamma(monkeypatch):
    world_conn = _pending_family_conn("event-1", "Hong Kong", "2026-06-07", "high")
    forecasts_conn = _FakeConn()
    write_conn = _FakeConn()
    topology_rows = [
        {
            "market_slug": "highest-temperature-in-hong-kong-on-june-7-2026",
            "city": "Hong Kong",
            "target_date": "2026-06-07",
            "temperature_metric": "high",
            "condition_id": "cond-1",
            "token_id": "yes-1",
            "range_label": "31C",
        }
    ]
    cached_market = {
        "slug": "highest-temperature-in-hong-kong-on-june-7-2026",
        "city": SimpleNamespace(name="Hong Kong"),
        "target_date": "2026-06-07",
        "temperature_metric": "high",
        "outcomes": [
            {
                "condition_id": "cond-1",
                "market_id": "cond-1",
                "token_id": "yes-1",
                "no_token_id": "no-1",
                "question_id": "q-1",
            }
        ],
    }

    import src.data.market_scanner as scanner
    import src.data.polymarket_client as polymarket_client
    import src.engine.event_reactor_adapter as adapter
    import src.state.db as state_db

    monkeypatch.setattr(adapter, "_event_family_market_topology_rows", lambda *a, **k: topology_rows)
    monkeypatch.setattr(state_db, "get_trade_connection", lambda **k: write_conn)
    monkeypatch.setattr(
        scanner,
        "reconstruct_weather_market_from_static_topology",
        lambda *a, **k: cached_market,
    )
    monkeypatch.setattr(scanner, "_gamma_get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("Gamma should not be called")))
    monkeypatch.setattr(scanner, "_parse_and_persist_weather_events", lambda *a, **k: [])

    submitted: list[list[dict]] = []

    def _refresh(_conn, *, markets, **_kwargs):
        submitted.append(markets)
        return {"attempted": 2, "inserted": 2}

    monkeypatch.setattr(scanner, "refresh_executable_market_substrate_snapshots", _refresh)
    monkeypatch.setattr(polymarket_client, "PolymarketClient", _FakePolymarketClient)

    result = main_module._refresh_pending_family_snapshots(world_conn, forecasts_conn)

    assert result["status"] == "refreshed"
    assert result["gamma_refresh_families"] == 0
    assert result["cached_topology_families"] == 1
    assert submitted == [[cached_market]]


def test_pending_family_refresh_falls_back_to_gamma_when_static_topology_incomplete(monkeypatch):
    world_conn = _pending_family_conn("event-1", "Hong Kong", "2026-06-07", "high")
    forecasts_conn = _FakeConn()
    write_conn = _FakeConn()
    topology_rows = [
        {
            "market_slug": "highest-temperature-in-hong-kong-on-june-7-2026",
            "city": "Hong Kong",
            "target_date": "2026-06-07",
            "temperature_metric": "high",
            "condition_id": "cond-1",
            "token_id": "yes-1",
            "range_label": "31C",
        }
    ]
    gamma_event = {
        "slug": "highest-temperature-in-hong-kong-on-june-7-2026",
        "city": SimpleNamespace(name="Hong Kong"),
        "target_date": "2026-06-07",
        "temperature_metric": "high",
        "outcomes": [
            {
                "condition_id": "cond-1",
                "market_id": "cond-1",
                "token_id": "yes-1",
                "no_token_id": "no-1",
                "question_id": "q-1",
            }
        ],
    }

    import src.data.market_scanner as scanner
    import src.data.polymarket_client as polymarket_client
    import src.engine.event_reactor_adapter as adapter
    import src.state.db as state_db

    monkeypatch.setattr(adapter, "_event_family_market_topology_rows", lambda *a, **k: topology_rows)
    monkeypatch.setattr(state_db, "get_trade_connection", lambda **k: write_conn)
    monkeypatch.setattr(scanner, "reconstruct_weather_market_from_static_topology", lambda *a, **k: None)

    gamma_calls: list[dict] = []

    class _GammaResponse:
        status_code = 200

        def json(self):
            return [{"id": "gamma-1"}]

    def _gamma_get(*_args, **kwargs):
        gamma_calls.append(kwargs.get("params") or {})
        return _GammaResponse()

    monkeypatch.setattr(scanner, "_gamma_get", _gamma_get)
    monkeypatch.setattr(scanner, "_parse_and_persist_weather_events", lambda *a, **k: [gamma_event])

    submitted: list[list[dict]] = []

    def _refresh(_conn, *, markets, **_kwargs):
        submitted.append(markets)
        return {"attempted": 2, "inserted": 2}

    monkeypatch.setattr(scanner, "refresh_executable_market_substrate_snapshots", _refresh)
    monkeypatch.setattr(polymarket_client, "PolymarketClient", _FakePolymarketClient)

    result = main_module._refresh_pending_family_snapshots(world_conn, forecasts_conn)

    assert result["status"] == "refreshed"
    assert result["gamma_refresh_families"] == 1
    assert result["cached_topology_incomplete"] == 1
    assert gamma_calls == [{"slug": "highest-temperature-in-hong-kong-on-june-7-2026"}]
    assert submitted == [[gamma_event]]


def test_mainstream_warm_cycle_uses_bounded_fresh_family_window(monkeypatch):
    monkeypatch.setattr(
        main_module,
        "_settings_section",
        lambda name, default=None: (
            {"enabled": True, "mainstream_warm_max_families_per_cycle": 2}
            if name == "edli_v1"
            else (default if default is not None else {})
        ),
    )
    import src.state.db as state_db

    monkeypatch.setattr(state_db, "get_world_connection", lambda: _FakeConn())

    rows = [
        ("Seoul", "2026-06-06", "high"),
        ("Tokyo", "2026-06-06", "high"),
        ("Paris", "2026-06-06", "high"),
    ]
    monkeypatch.setattr(
        main_module,
        "_pending_family_rows_for_refresh",
        lambda *a, **k: rows,
    )

    warmed: list[tuple[str, str, str]] = []

    def _warm(city, target_date, *, metric):
        warmed.append((city, target_date, metric))
        return {"point": 1.0}

    import src.data.mainstream_forecast_source as mainstream

    monkeypatch.setattr(mainstream, "warm_mainstream_point", _warm)

    main_module._edli_mainstream_warm_cycle()

    assert warmed == rows[:2]


class _FakeConn:
    """Minimal connection stub: supports the ATTACH/PRAGMA/close calls the warm job
    may make, and is a no-op for everything else."""

    def execute(self, *a, **k):
        class _Cur:
            def fetchall(self_inner):
                return []

            def fetchone(self_inner):
                return None

        return _Cur()

    def commit(self):
        pass

    def close(self):
        pass


class _FakePolymarketClient:
    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _CaptureConn:
    def __init__(self, conn):
        self._conn = conn
        self.sql = ""
        self.params = ()

    def execute(self, sql, params=()):
        self.sql = sql
        self.params = params
        return self._conn.execute(sql, params)


def _explain_plan(conn, sql: str, params=()) -> str:
    return "\n".join(
        " ".join(str(part) for part in row)
        for row in conn.execute(f"EXPLAIN QUERY PLAN {sql}", params).fetchall()
    )


def _pending_family_conn(event_id: str, city: str, target_date: str, metric: str):
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE opportunity_events (
            event_id TEXT NOT NULL PRIMARY KEY,
            event_type TEXT NOT NULL,
            entity_key TEXT NOT NULL,
            source TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            available_at TEXT NOT NULL,
            received_at TEXT NOT NULL,
            causal_snapshot_id TEXT,
            payload_hash TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            priority INTEGER NOT NULL DEFAULT 0,
            expires_at TEXT,
            payload_json TEXT NOT NULL,
            schema_version INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE opportunity_event_processing (
            consumer_name TEXT NOT NULL,
            event_id TEXT NOT NULL,
            processing_status TEXT NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            claimed_at TEXT,
            processed_at TEXT,
            last_error TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (consumer_name, event_id)
        );
        CREATE INDEX idx_opportunity_event_processing_status
            ON opportunity_event_processing(consumer_name, processing_status, updated_at);
        """
    )
    payload = {"city": city, "target_date": target_date, "metric": metric}
    now = "2026-06-06T00:00:00+00:00"
    conn.execute(
        """
        INSERT INTO opportunity_events (
            event_id, event_type, entity_key, source, observed_at, available_at,
            received_at, payload_hash, idempotency_key, priority, payload_json,
            schema_version, created_at
        ) VALUES (?, 'FORECAST_SNAPSHOT_READY', ?, 'test', ?, ?, ?, ?, ?, 50, ?, 1, ?)
        """,
        (event_id, event_id, now, now, now, event_id, event_id, json.dumps(payload), now),
    )
    conn.execute(
        """
        INSERT INTO opportunity_event_processing (
            consumer_name, event_id, processing_status, updated_at
        ) VALUES ('edli_reactor_v1', ?, 'pending', ?)
        """,
        (event_id, now),
    )
    return conn

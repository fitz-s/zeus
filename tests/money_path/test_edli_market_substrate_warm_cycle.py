# Created: 2026-06-01
# Last reused/audited: 2026-06-13
# Authority basis (2026-06-13 add): docs/operations/live_inventory_warm_skip_2026-06-13.md —
#   venue-close warm-skip relationship tests (live-inventory focus; market_phase.family_venue_closed).
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
  Gate: when edli is disabled the warm job does no refresh.
  Fail-soft: a refresh that raises does NOT propagate out of the warm job.
"""

from __future__ import annotations

import inspect
import json
import re
import sqlite3
from datetime import date, datetime, time, timezone
from types import SimpleNamespace

import pytest

import src.main as main_module
from src.contracts.executable_market_snapshot import FRESHNESS_WINDOW_DEFAULT
import src.data.substrate_observer as substrate_observer


def _venue_open_now(target_date: str) -> datetime:
    """A frozen decision-clock instant at which a family's venue market is still
    OPEN (NOT POST_TRADING) — 06:00 UTC of ``target_date``, six hours before the
    F1 12:00-UTC venue close.

    The warm lane now skips families whose venue market has entered POST_TRADING
    (``_refresh_pending_family_snapshots`` venue-close warm-skip, 2026-06-13). To
    keep the fixed-date fixtures below exercising the live-family path, the tests
    inject this venue-OPEN ``now`` instead of wall-clock (which would make every
    fixed past-date fixture venue-closed → skipped). Clock-relative-by-injection:
    the family's phase is pinned by the date+now pair, not by when the test runs.
    """
    return datetime.combine(
        date.fromisoformat(target_date), time(6, 0, 0), tzinfo=timezone.utc
    )


@pytest.fixture(autouse=True)
def _reset_substrate_refresh_cursor():
    """Reset the round-robin family cursor before each test.

    Funnel-starvation fix (2026-06-09) made ``_SUBSTRATE_REFRESH_CURSOR`` a module
    global that the warmer advances each cycle. Tests that assert a specific
    family-processing ORDER (e.g. the gamma direct-lookup tests) depend on the
    sweep starting at offset 0; without this reset a prior test's cursor advance
    rotates the family list and the order-sensitive assertions become flaky on a
    full-file run. Production correctness does not depend on the start offset (the
    cursor wraps ``% n_families`` and every family is swept within one period); the
    reset is purely test determinism.
    """
    saved = main_module._SUBSTRATE_REFRESH_CURSOR
    main_module._SUBSTRATE_REFRESH_CURSOR = 0
    saved_lifted = substrate_observer._SUBSTRATE_REFRESH_CURSOR
    substrate_observer._SUBSTRATE_REFRESH_CURSOR = 0
    saved_lifted_priority = substrate_observer._SUBSTRATE_PRIORITY_REFRESH_CURSOR
    substrate_observer._SUBSTRATE_PRIORITY_REFRESH_CURSOR = 0
    try:
        yield
    finally:
        main_module._SUBSTRATE_REFRESH_CURSOR = saved
        substrate_observer._SUBSTRATE_REFRESH_CURSOR = saved_lifted
        substrate_observer._SUBSTRATE_PRIORITY_REFRESH_CURSOR = saved_lifted_priority


def _enable_edli_cfg(monkeypatch, *, enabled: bool = True) -> None:
    # P2 lift: the substrate warm cycle + market_discovery read _settings_section from
    # src.data.substrate_observer (its own _settings_section), so the edli_v1 config gate
    # must be patched there. (The mainstream warmer that stays in src.main still reads
    # main_module._settings_section; tests for that patch main_module separately.)
    monkeypatch.setattr(
        substrate_observer,
        "_settings_section",
        lambda name, default=None: (
            {"enabled": enabled} if name in {"edli", "edli_v1"} else (default if default is not None else {})
        ),
    )


def test_substrate_settings_section_accepts_live_edli_alias(monkeypatch):
    """Live settings use `edli`; the lifted warm job must not silently no-op on old `edli_v1`."""
    monkeypatch.setattr(substrate_observer, "settings", {"edli": {"enabled": True}})

    assert substrate_observer._settings_section("edli_v1") == {"enabled": True}


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
    src = inspect.getsource(substrate_observer._refresh_pending_family_snapshots)

    assert "find_weather_markets_or_raise" not in src


def test_pending_family_refresh_default_budget_stays_inside_price_ttl():
    src = inspect.getsource(substrate_observer._refresh_pending_family_snapshots)
    match = re.search(
        r'ZEUS_REACTOR_REFRESH_BUDGET_SECONDS", "([0-9.]+)"',
        src,
    )

    assert match is not None
    assert float(match.group(1)) < FRESHNESS_WINDOW_DEFAULT.total_seconds()


def test_pending_family_refresh_has_no_fixed_family_cap():
    src = inspect.getsource(substrate_observer._refresh_pending_family_snapshots)

    assert "_FAMILY_REFRESH_CAP" not in src
    # No fixed-prefix truncation that DROPS families. The funnel-starvation fix
    # (2026-06-09) introduced a rotating cursor that wraps the family list
    # (``families[start_offset:] + families[:start_offset]``) — this REORDERS, it
    # does not drop, so the only legitimate ``families[:`` occurrence is the
    # rotation wrap-around. Forbid the dropping forms (a numeric/const cap slice)
    # while allowing the wrap-around concatenation.
    assert "families[:_" not in src  # families[:_SOME_CAP]
    import re

    # families[:<int>] would be a hard drop; families[:start_offset] is the rotation.
    dropping_caps = re.findall(r"families\[:\s*\d+\s*\]", src)
    assert not dropping_caps, f"fixed-count family cap present: {dropping_caps}"
    assert "ordinary_families[:start_offset]" in src, (
        "expected the rotating-cursor wrap-around ordinary_families[:start_offset]; the "
        "round-robin sweep is what prevents tail-family starvation"
    )


def test_warm_lane_money_risk_priority_stays_ahead_of_pending_rotation():
    """Open rests and held positions are live money-risk, not ordinary backlog.

    They must remain ahead of the rotating pending-event tail every tick; the
    fair cursor should rotate only the ordinary pending families so a large
    pending queue cannot bury already-submitted orders or chain-confirmed
    holdings.
    """

    src = inspect.getsource(substrate_observer._refresh_pending_family_snapshots)

    assert "get_trade_connection, get_trade_connection_read_only" in src
    assert "held_position_priority_families" in src
    assert "priority_families + new_priority_families + rotated_ordinary_families" in src
    assert "ordinary_families[start_offset:] + ordinary_families[:start_offset]" in src


def test_continuous_redecision_confirms_money_path_before_emit():
    """Continuous redecision must not enqueue from an unconfirmed first-pass screen.

    The first screen only identifies candidate families to refresh. The second
    screen, after the explicit money-path substrate refresh, is the one allowed
    to mutate acted_state and emit EDLI_REDECISION_PENDING.
    """

    screen_src = inspect.getsource(main_module._edli_continuous_redecision_screen_cycle)
    confirm_src = inspect.getsource(main_module._edli_refresh_continuous_money_path_families)

    assert "probe_acted_state = dict(_edli_redecision_acted_state)" in screen_src
    assert "acted_state=probe_acted_state" in screen_src
    assert "_edli_refresh_continuous_money_path_families(" in screen_src
    assert "skipping emit this tick rather than queueing stale redecision" in screen_src
    assert "confirmed_entry_scope = set(family_keys)" in screen_src
    assert "family_keys &= confirmed_entry_scope" in screen_src
    assert "rest_pull_families &= confirmed_rest_scope" in screen_src
    assert "ZEUS_REDECISION_CONFIRM_REFRESH_LOCK_TIMEOUT_SECONDS" in confirm_src
    assert "_edli_redecision_confirm_refresh_lock" in confirm_src
    assert "_market_substrate_refresh_lock" not in confirm_src
    assert "include_pending_families=False" in confirm_src
    assert "extra_priority_families=clean_families" in confirm_src


def test_snapshot_capture_budget_uses_reserve_when_selection_overruns(monkeypatch):
    """Late topology selection must leave both /books prefetch and capture time."""

    monkeypatch.setattr(main_module.time, "monotonic", lambda: 100.0)
    monkeypatch.setenv("ZEUS_MARKET_DISCOVERY_ORDERBOOK_PREFETCH_MIN_WINDOW_SECONDS", "0.75")

    assert substrate_observer._snapshot_capture_budget_for_refresh(
        refresh_deadline=90.0,
        snapshot_reserve_s=12.0,
    ) == pytest.approx(14.0)
    assert substrate_observer._snapshot_capture_budget_for_refresh(
        refresh_deadline=125.0,
        snapshot_reserve_s=12.0,
    ) == pytest.approx(25.0)

    monkeypatch.setenv("ZEUS_MARKET_DISCOVERY_ORDERBOOK_PREFETCH_TARGET_WINDOW_SECONDS", "3.5")
    assert substrate_observer._snapshot_capture_budget_for_refresh(
        refresh_deadline=90.0,
        snapshot_reserve_s=12.0,
    ) == pytest.approx(15.5)


def test_market_discovery_does_not_defer_on_reactor_state_after_p2_lift():
    """SUPERIORITY (system_decomposition_plan §8 Step 1 / §9): INVERTED from the old
    "defers_while_reactor_active" test.

    The P2 lift DELETES the outer pending gates from _market_discovery_cycle. The universe
    sweep is now a separate-process producer triggered by substrate STALENESS alone; it can
    no longer reference the reactor's in-process state. The old assertion (the gate is
    PRESENT) tested the exact regression this refactor kills — it is inverted to assert the
    gate is GONE, making the gate-on-backlog line un-writable across the process boundary.
    """
    # AST over the function body (not raw text) so an explanatory COMMENT describing the
    # deleted gate does not falsely match — only an executable code reference to a
    # reactor-backlog identifier is a coupling.
    import ast

    tree = ast.parse(inspect.getsource(substrate_observer._market_discovery_cycle))
    used: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            used.add(node.attr)
    for sym in ("_edli_reactor_active", "_edli_pending_opportunity_count",
                "_market_discovery_pending_fairness_seconds", "pending_count"):
        assert sym not in used, (
            f"the lifted _market_discovery_cycle must not reference {sym!r} in CODE — the "
            "outer pending gates are DELETED (§0/§8 Step 1/§9); the producer fires on "
            "substrate staleness alone, with no consumer state in scope."
        )


def test_held_position_monitor_does_not_pause_live_decision_line():
    """Held-position monitoring is not a global live-money stop-the-world lock.

    The monitor's own job stays non-reentrant, and broad discretionary scans may
    defer during its bootstrap. Targeted EDLI decision lanes must continue: those
    lanes are what refresh prices, re-decide resting orders, recover commands,
    and submit/reject new events while positions are being monitored.
    """

    was_active = main_module._held_position_monitor_active.is_set()
    was_bootstrap_complete = main_module._held_position_monitor_bootstrap_complete.is_set()
    if was_active:
        main_module._held_position_monitor_active.clear()
    if was_bootstrap_complete:
        main_module._held_position_monitor_bootstrap_complete.clear()

    try:
        main_module._held_position_monitor_active.set()
        live_decision_jobs = {
            "edli_event_reactor",
            "edli_command_recovery",
            "maker_rest_escalation",
            "edli_redecision_screen",
            "EDLI market-substrate warm",
            "EDLI market-channel substrate refresh",
            "new_listing_scout",
        }
        for job_name in live_decision_jobs:
            assert main_module._defer_for_held_position_monitor(job_name) is False

        discretionary_jobs = {
            "market_discovery",
            "afternoon_snapshot_capture",
            "EDLI mainstream warm",
        }
        for job_name in discretionary_jobs:
            assert main_module._defer_for_held_position_monitor(job_name) is True
    finally:
        main_module._held_position_monitor_active.clear()
        main_module._held_position_monitor_bootstrap_complete.clear()
        if was_active:
            main_module._held_position_monitor_active.set()
        if was_bootstrap_complete:
            main_module._held_position_monitor_bootstrap_complete.set()


def test_market_substrate_warm_cycle_exists_and_refreshes_once(monkeypatch):
    """GREEN-after-fix: a dedicated warm job exists and, when EDLI is enabled, invokes
    the family-snapshot refresh exactly once per tick."""
    assert hasattr(substrate_observer, "_edli_market_substrate_warm_cycle"), (
        "expected a dedicated _edli_market_substrate_warm_cycle producer (lifted to the P2 "
        "substrate-observer module) that owns the decoupled substrate refresh."
    )

    calls: list[int] = []
    monkeypatch.setattr(
        substrate_observer,
        "_refresh_pending_family_snapshots",
        lambda *a, **k: calls.append(1),
    )
    # The warm job opens world/forecasts connections; stub them so no real DB/venue work
    # runs. The test only asserts the refresh is invoked exactly once. The cycle imports
    # get_forecasts_connection_read_only from src.state.db at call time, so patch state_db.
    import src.state.db as state_db

    monkeypatch.setattr(state_db, "get_world_connection", lambda: _FakeConn())
    monkeypatch.setattr(
        state_db, "get_forecasts_connection_read_only", lambda: _FakeConn(), raising=False
    )
    monkeypatch.setattr(state_db, "get_trade_connection", lambda **_kwargs: _FakeConn())
    _enable_edli_cfg(monkeypatch, enabled=True)

    substrate_observer._edli_market_substrate_warm_cycle()
    assert calls == [1], "warm job must invoke the family-snapshot refresh exactly once"


def test_market_substrate_warm_cycle_runs_while_reactor_active(monkeypatch):
    """The warm job owns an independent cadence; reactor-active must not starve price refresh."""
    calls: list[int] = []
    monkeypatch.setattr(
        substrate_observer,
        "_refresh_pending_family_snapshots",
        lambda *a, **k: calls.append(1),
    )
    import src.state.db as state_db

    monkeypatch.setattr(state_db, "get_world_connection", lambda: _FakeConn())
    monkeypatch.setattr(
        state_db, "get_forecasts_connection_read_only", lambda: _FakeConn(), raising=False
    )
    monkeypatch.setattr(state_db, "get_trade_connection", lambda **_kwargs: _FakeConn())
    _enable_edli_cfg(monkeypatch, enabled=True)

    assert main_module._edli_reactor_active_lock.acquire(blocking=False)
    try:
        substrate_observer._edli_market_substrate_warm_cycle()
    finally:
        main_module._edli_reactor_active_lock.release()

    assert calls == [1]


def test_market_substrate_warm_cycle_noop_when_edli_disabled(monkeypatch):
    """Config gate: disabled edli → no refresh side effect."""
    calls: list[int] = []
    monkeypatch.setattr(
        substrate_observer,
        "_refresh_pending_family_snapshots",
        lambda *a, **k: calls.append(1),
    )
    import src.state.db as state_db

    monkeypatch.setattr(state_db, "get_world_connection", lambda: _FakeConn())
    monkeypatch.setattr(
        state_db, "get_forecasts_connection_read_only", lambda: _FakeConn(), raising=False
    )
    _enable_edli_cfg(monkeypatch, enabled=False)

    substrate_observer._edli_market_substrate_warm_cycle()
    assert calls == [], "disabled edli_v1 must skip the refresh"


def test_market_substrate_warm_cycle_failsoft_on_refresh_error(monkeypatch):
    """Fail-soft: a refresh that raises must not propagate out of the warm job (the
    next tick retries; the reactor's EXECUTABLE_SNAPSHOT_RETRY keeps decisions
    fail-closed in the interim)."""
    import src.state.db as state_db

    def _raising(*a, **k):
        raise RuntimeError("gamma scan timeout")

    monkeypatch.setattr(substrate_observer, "_refresh_pending_family_snapshots", _raising)
    monkeypatch.setattr(state_db, "get_world_connection", lambda: _FakeConn(), raising=False)
    monkeypatch.setattr(
        state_db, "get_forecasts_connection_read_only", lambda: _FakeConn(), raising=False
    )
    _enable_edli_cfg(monkeypatch, enabled=True)

    # Must not raise.
    substrate_observer._edli_market_substrate_warm_cycle()


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
    rows = substrate_observer._pending_family_rows_for_refresh(
        capture, consumer_name="edli_reactor_v1"
    )
    families = [(row[0], row[1], row[2]) for row in rows]

    assert [family[1] for family in families[:2]] == ["2026-06-06", "2026-06-06"]
    assert [family[1] for family in families[-2:]] == ["2026-06-04", "2026-06-04"]

    plan = _explain_plan(conn, capture.sql, capture.params)
    assert "USING INDEX idx_opportunity_event_processing_status" in plan
    assert "LIMIT ?" in capture.sql
    assert capture.params == ("edli_reactor_v1", 2000)


def test_pending_family_refresh_does_not_truncate_to_fixed_family_cap(monkeypatch):
    """The pending-family warmer must progress by wall-clock budget, not by a hard
    family-count slice. A fixed 8-family cap lets a small prefix monopolise the
    price freshness window while hundreds of live weather families stay pending."""
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
    for idx in range(12):
        city = f"City {idx:02d}"
        event_id = f"event-{idx:02d}"
        available_at = f"2026-06-06T00:00:{idx:02d}+00:00"
        payload = {"city": city, "target_date": "2026-06-07", "metric": "high"}
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

    write_conn = _FakeConn()

    import src.data.market_scanner as scanner
    import src.data.polymarket_client as polymarket_client
    import src.data.market_topology_rows as adapter  # P2: topology reader relocated (lane-neutral)
    import src.state.db as state_db

    def _topology_rows(_forecasts_conn, payload):
        city = payload["city"]
        return [
            {
                "market_slug": f"highest-temperature-in-{city.lower().replace(' ', '-')}-on-june-7-2026",
                "condition_id": f"cond-{city}",
                "token_id": f"yes-{city}",
                "no_token_id": f"no-{city}",
                "range_label": "24C",
            }
        ]

    def _reconstruct(_conn, *, topology_rows, **_kwargs):
        row = topology_rows[0]
        return {
            "slug": row["market_slug"],
            "city": SimpleNamespace(name=row["city"]),
            "target_date": row["target_date"],
            "temperature_metric": row["temperature_metric"],
            "outcomes": [
                {
                    "condition_id": row["condition_id"],
                    "market_id": row["condition_id"],
                    "token_id": row["token_id"],
                    "no_token_id": row["no_token_id"],
                    "question_id": f"q-{row['condition_id']}",
                }
            ],
        }

    monkeypatch.setattr(adapter, "_event_family_market_topology_rows", _topology_rows)
    monkeypatch.setattr(state_db, "get_trade_connection", lambda **_k: write_conn)
    monkeypatch.setattr(state_db, "get_trade_connection_read_only", lambda **_k: _FakeConn())
    monkeypatch.setattr(scanner, "reconstruct_weather_market_from_static_topology", _reconstruct)
    monkeypatch.setattr(scanner, "_gamma_get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("Gamma should not be called")))
    monkeypatch.setattr(scanner, "_parse_and_persist_weather_events", lambda *a, **k: [])
    monkeypatch.setenv("ZEUS_REACTOR_SNAPSHOT_RESERVE_SECONDS", "1.0")

    submitted: list[list[dict]] = []
    refresh_kwargs: list[dict] = []

    def _refresh(_conn, *, markets, **kwargs):
        submitted.append(markets)
        refresh_kwargs.append(kwargs)
        return {"attempted": len(markets), "inserted": len(markets)}

    monkeypatch.setattr(scanner, "refresh_executable_market_substrate_snapshots", _refresh)
    monkeypatch.setattr(polymarket_client, "PolymarketClient", _FakePolymarketClient)

    result = substrate_observer._refresh_pending_family_snapshots(conn, _FakeConn())

    assert result["status"] == "refreshed"
    assert result["families_checked"] == 12
    assert result["cached_topology_families"] == 12
    assert len(submitted) == 1
    assert len(submitted[0]) == 12
    assert refresh_kwargs[0]["max_outcomes"] == 0
    assert refresh_kwargs[0]["budget_seconds"] < FRESHNESS_WINDOW_DEFAULT.total_seconds()


def test_pending_family_refresh_timeboxes_topology_before_capture_reserve(monkeypatch):
    """Topology/cache work must stop expanding scope before it consumes CLOB reserve."""

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
    for idx in range(12):
        city = f"City {idx:02d}"
        event_id = f"event-{idx:02d}"
        available_at = f"2026-06-06T00:00:{idx:02d}+00:00"
        payload = {"city": city, "target_date": "2026-06-07", "metric": "high"}
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

    fake_now = 0.0

    def _monotonic() -> float:
        return fake_now

    import src.data.market_scanner as scanner
    import src.data.polymarket_client as polymarket_client
    import src.data.market_topology_rows as adapter  # P2: topology reader relocated (lane-neutral)
    import src.state.db as state_db

    def _topology_rows(_forecasts_conn, payload):
        nonlocal fake_now
        fake_now += 1.25
        city = payload["city"]
        return [
            {
                "market_slug": f"highest-temperature-in-{city.lower().replace(' ', '-')}-on-june-7-2026",
                "condition_id": f"cond-{city}",
                "token_id": f"yes-{city}",
                "no_token_id": f"no-{city}",
                "range_label": "24C",
            }
        ]

    def _reconstruct(_conn, *, topology_rows, **_kwargs):
        row = topology_rows[0]
        return {
            "slug": row["market_slug"],
            "city": SimpleNamespace(name=row["city"]),
            "target_date": row["target_date"],
            "temperature_metric": row["temperature_metric"],
            "outcomes": [
                {
                    "condition_id": row["condition_id"],
                    "market_id": row["condition_id"],
                    "token_id": row["token_id"],
                    "no_token_id": row["no_token_id"],
                    "question_id": f"q-{row['condition_id']}",
                }
            ],
        }

    monkeypatch.setenv("ZEUS_REACTOR_REFRESH_BUDGET_SECONDS", "15.0")
    monkeypatch.delenv("ZEUS_REACTOR_SNAPSHOT_RESERVE_SECONDS", raising=False)
    monkeypatch.setattr(main_module.time, "monotonic", _monotonic)
    monkeypatch.setattr(adapter, "_event_family_market_topology_rows", _topology_rows)
    monkeypatch.setattr(state_db, "get_trade_connection", lambda **_k: _FakeConn())
    monkeypatch.setattr(scanner, "reconstruct_weather_market_from_static_topology", _reconstruct)
    monkeypatch.setattr(scanner, "_gamma_get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("Gamma should not be called")))
    monkeypatch.setattr(scanner, "_parse_and_persist_weather_events", lambda *a, **k: [])

    submitted: list[list[dict]] = []
    refresh_kwargs: list[dict] = []

    def _refresh(_conn, *, markets, **kwargs):
        submitted.append(markets)
        refresh_kwargs.append(kwargs)
        return {"attempted": len(markets), "inserted": len(markets)}

    monkeypatch.setattr(scanner, "refresh_executable_market_substrate_snapshots", _refresh)
    monkeypatch.setattr(polymarket_client, "PolymarketClient", _FakePolymarketClient)

    result = substrate_observer._refresh_pending_family_snapshots(conn, _FakeConn())

    assert result["status"] == "refreshed"
    assert result["topology_budget_exhausted"] == 1
    assert result["topology_deferred_families"] > 0
    assert 1 <= len(submitted[0]) < 12
    assert refresh_kwargs[0]["budget_seconds"] == pytest.approx(14.0)


def test_pending_family_refresh_reserves_time_for_direct_gamma_lookup(monkeypatch):
    """Topology probing must not consume the whole pre-CLOB slice before Gamma."""

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
    for idx, city in enumerate(("Hong Kong", "Miami", "NYC")):
        payload = {"city": city, "target_date": "2026-06-09", "metric": "high"}
        event_id = f"event-{idx}"
        now = f"2026-06-06T00:00:0{idx}+00:00"
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

    fake_now = 0.0

    def _monotonic() -> float:
        return fake_now

    import src.data.market_scanner as scanner
    import src.data.polymarket_client as polymarket_client
    import src.data.market_topology_rows as adapter  # P2: topology reader relocated (lane-neutral)
    import src.state.db as state_db

    def _topology_rows(_forecasts_conn, _payload):
        nonlocal fake_now
        fake_now += 1.5
        return []

    gamma_calls: list[dict] = []
    gamma_event = {
        "slug": "highest-temperature-in-nyc-on-june-9-2026",
        "city": SimpleNamespace(name="NYC"),
        "target_date": "2026-06-09",
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

    class _GammaResponse:
        status_code = 200

        def json(self):
            return [{"id": "gamma-1"}]

    def _gamma_get(*_args, **kwargs):
        gamma_calls.append(kwargs.get("params") or {})
        return _GammaResponse()

    submitted: list[list[dict]] = []

    def _refresh(_conn, *, markets, **_kwargs):
        submitted.append(markets)
        return {"attempted": len(markets), "inserted": len(markets)}

    monkeypatch.setenv("ZEUS_REACTOR_REFRESH_BUDGET_SECONDS", "15.0")
    monkeypatch.delenv("ZEUS_REACTOR_SNAPSHOT_RESERVE_SECONDS", raising=False)
    monkeypatch.delenv("ZEUS_REACTOR_GAMMA_LOOKUP_MIN_SECONDS", raising=False)
    monkeypatch.setattr(main_module.time, "monotonic", _monotonic)
    monkeypatch.setattr(adapter, "_event_family_market_topology_rows", _topology_rows)
    monkeypatch.setattr(state_db, "get_trade_connection", lambda **_k: _FakeConn())
    monkeypatch.setattr(scanner, "_gamma_get", _gamma_get)
    monkeypatch.setattr(scanner, "_parse_and_persist_weather_events", lambda *a, **k: [gamma_event])
    monkeypatch.setattr(scanner, "refresh_executable_market_substrate_snapshots", _refresh)
    monkeypatch.setattr(polymarket_client, "PolymarketClient", _FakePolymarketClient)

    result = substrate_observer._refresh_pending_family_snapshots(conn, _FakeConn())

    assert result["status"] == "refreshed"
    assert result["topology_budget_exhausted"] == 1
    assert gamma_calls == [{"slug": "highest-temperature-in-nyc-on-june-9-2026"}]
    assert result["skipped_not_found"] == 0
    assert submitted[0][0]["slug"] == gamma_event["slug"]


def test_pending_family_refresh_direct_gamma_lookup_drains_multiple_families(monkeypatch):
    """Direct Gamma lookup must cover the pending family set by budget, not a serial city trickle."""

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
    families = [
        ("Hong Kong", "2026-06-09", "high"),
        ("Miami", "2026-06-09", "high"),
        ("NYC", "2026-06-09", "low"),
        ("Seoul", "2026-06-09", "high"),
    ]
    for idx, (city, target_date, metric) in enumerate(families):
        payload = {"city": city, "target_date": target_date, "metric": metric}
        now = f"2026-06-06T00:00:0{idx}+00:00"
        conn.execute(
            """
            INSERT INTO opportunity_events (
                event_id, event_type, entity_key, source, observed_at, available_at,
                received_at, payload_hash, idempotency_key, priority, payload_json,
                schema_version, created_at
            ) VALUES (?, 'FORECAST_SNAPSHOT_READY', ?, 'test', ?, ?, ?, ?, ?, 50, ?, 1, ?)
            """,
            (f"event-{idx}", f"event-{idx}", now, now, now, f"event-{idx}", f"event-{idx}", json.dumps(payload), now),
        )
        conn.execute(
            """
            INSERT INTO opportunity_event_processing (
                consumer_name, event_id, processing_status, updated_at
            ) VALUES ('edli_reactor_v1', ?, 'pending', ?)
            """,
            (f"event-{idx}", now),
        )

    gamma_events = [
        {
            "slug": f"{'lowest' if metric == 'low' else 'highest'}-temperature-in-{city.lower().replace(' ', '-')}-on-june-9-2026",
            "city": SimpleNamespace(name=city),
            "target_date": target_date,
            "temperature_metric": metric,
            "outcomes": [
                {
                    "condition_id": f"cond-{idx}",
                    "market_id": f"cond-{idx}",
                    "token_id": f"yes-{idx}",
                    "no_token_id": f"no-{idx}",
                    "question_id": f"q-{idx}",
                }
            ],
        }
        for idx, (city, target_date, metric) in enumerate(families)
    ]

    import src.data.market_scanner as scanner
    import src.data.polymarket_client as polymarket_client
    import src.data.market_topology_rows as adapter  # P2: topology reader relocated (lane-neutral)
    import src.state.db as state_db

    monkeypatch.setenv("ZEUS_REACTOR_REFRESH_BUDGET_SECONDS", "29.0")
    monkeypatch.setenv("ZEUS_REACTOR_GAMMA_LOOKUP_CONCURRENCY", "4")
    monkeypatch.setattr(adapter, "_event_family_market_topology_rows", lambda *a, **k: [])
    monkeypatch.setattr(state_db, "get_trade_connection", lambda **_k: _FakeConn())

    gamma_calls: list[str] = []

    class _GammaResponse:
        status_code = 200

        def __init__(self, slug: str):
            self._slug = slug

        def json(self):
            return [{"id": self._slug, "slug": self._slug}]

    def _gamma_get(*_args, **kwargs):
        slug = (kwargs.get("params") or {})["slug"]
        gamma_calls.append(slug)
        return _GammaResponse(slug)

    submitted: list[list[dict]] = []

    def _refresh(_conn, *, markets, **_kwargs):
        submitted.append(markets)
        return {"attempted": len(markets), "inserted": len(markets)}

    monkeypatch.setattr(scanner, "_gamma_get", _gamma_get)
    monkeypatch.setattr(scanner, "_parse_and_persist_weather_events", lambda *a, **k: gamma_events)
    monkeypatch.setattr(scanner, "refresh_executable_market_substrate_snapshots", _refresh)
    monkeypatch.setattr(polymarket_client, "PolymarketClient", _FakePolymarketClient)

    result = substrate_observer._refresh_pending_family_snapshots(conn, _FakeConn())

    assert result["status"] == "refreshed"
    assert result["gamma_refresh_families"] == len(families)
    assert result["gamma_slug_attempted"] == len(families)
    assert result["gamma_slug_timebox_unattempted"] == 0
    assert result["skipped_not_found"] == 0
    assert len(set(gamma_calls)) == len(families)
    assert {market["slug"] for market in submitted[0]} == {event["slug"] for event in gamma_events}


def test_condition_buy_sides_fresh_requires_yes_and_no_selected_tokens():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE executable_market_snapshots (
            snapshot_id TEXT,
            condition_id TEXT,
            yes_token_id TEXT,
            no_token_id TEXT,
            selected_outcome_token_id TEXT,
            captured_at TEXT,
            freshness_deadline TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO executable_market_snapshots (
            snapshot_id, condition_id, yes_token_id, no_token_id,
            selected_outcome_token_id, captured_at, freshness_deadline
        ) VALUES ('snap-yes', 'cond-1', 'yes-1', 'no-1', 'yes-1',
                  '2026-06-06T00:00:00+00:00', '2026-06-06T00:01:00+00:00')
        """
    )

    assert not substrate_observer._condition_buy_sides_fresh(
        conn,
        "cond-1",
        "2026-06-06T00:00:30+00:00",
    )

    conn.execute(
        """
        INSERT INTO executable_market_snapshots (
            snapshot_id, condition_id, yes_token_id, no_token_id,
            selected_outcome_token_id, captured_at, freshness_deadline
        ) VALUES ('snap-no', 'cond-1', 'yes-1', 'no-1', 'no-1',
                  '2026-06-06T00:00:01+00:00', '2026-06-06T00:01:00+00:00')
        """
    )

    assert substrate_observer._condition_buy_sides_fresh(
        conn,
        "cond-1",
        "2026-06-06T00:00:30+00:00",
    )


def test_prune_fresh_market_outcomes_keeps_refresh_moving_past_completed_conditions():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE executable_market_snapshots (
            snapshot_id TEXT,
            condition_id TEXT,
            yes_token_id TEXT,
            no_token_id TEXT,
            selected_outcome_token_id TEXT,
            captured_at TEXT,
            freshness_deadline TEXT
        )
        """
    )
    for snapshot_id, selected in (("snap-yes", "yes-fresh"), ("snap-no", "no-fresh")):
        conn.execute(
            """
            INSERT INTO executable_market_snapshots (
                snapshot_id, condition_id, yes_token_id, no_token_id,
                selected_outcome_token_id, captured_at, freshness_deadline
            ) VALUES (?, 'cond-fresh', 'yes-fresh', 'no-fresh', ?,
                      '2026-06-06T00:00:00+00:00', '2026-06-06T00:01:00+00:00')
            """,
            (snapshot_id, selected),
        )

    market = {
        "slug": "highest-temperature-in-test-on-june-7-2026",
        "condition_ids": ["cond-fresh", "cond-stale"],
        "outcomes": [
            {
                "condition_id": "cond-fresh",
                "market_id": "cond-fresh",
                "token_id": "yes-fresh",
                "no_token_id": "no-fresh",
            },
            {
                "condition_id": "cond-stale",
                "market_id": "cond-stale",
                "token_id": "yes-stale",
                "no_token_id": "no-stale",
            },
        ],
    }

    pruned, fresh_skipped, stale_submitted = (
        substrate_observer._prune_fresh_market_outcomes_for_snapshot_refresh(
            conn,
            [market],
            fresh_at_iso="2026-06-06T00:00:30+00:00",
        )
    )

    assert fresh_skipped == 1
    assert stale_submitted == 1
    assert len(pruned) == 1
    assert [outcome["condition_id"] for outcome in pruned[0]["outcomes"]] == ["cond-stale"]
    assert pruned[0]["condition_ids"] == ["cond-stale"]


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
    import src.data.market_topology_rows as adapter  # P2: topology reader relocated (lane-neutral)
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

    result = substrate_observer._refresh_pending_family_snapshots(world_conn, forecasts_conn)

    assert result["status"] == "refreshed"
    assert result["gamma_refresh_families"] == 0
    assert result["cached_topology_families"] == 1
    assert len(submitted) == 1
    assert submitted[0][0]["slug"] == cached_market["slug"]
    assert submitted[0][0]["outcomes"] == cached_market["outcomes"]
    assert submitted[0][0].get("condition_ids") in (None, ["cond-1"])


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
    import src.data.market_topology_rows as adapter  # P2: topology reader relocated (lane-neutral)
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

    result = substrate_observer._refresh_pending_family_snapshots(world_conn, forecasts_conn)

    assert result["status"] == "refreshed"
    assert result["gamma_refresh_families"] == 1
    assert result["cached_topology_incomplete"] == 1
    assert gamma_calls == [{"slug": "highest-temperature-in-hong-kong-on-june-7-2026"}]
    assert len(submitted) == 1
    assert submitted[0][0]["slug"] == gamma_event["slug"]
    assert submitted[0][0]["outcomes"] == gamma_event["outcomes"]
    assert submitted[0][0].get("condition_ids") in (None, ["cond-1"])


def test_pending_family_refresh_matches_gamma_with_canonical_city_alias(monkeypatch):
    """Pending payload aliases and parsed Gamma canonical city names are one family."""

    world_conn = _pending_family_conn("event-1", "hk", "2026-06-07", "highest")
    forecasts_conn = _FakeConn()
    write_conn = _FakeConn()
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
    import src.data.market_topology_rows as adapter  # P2: topology reader relocated (lane-neutral)
    import src.state.db as state_db

    monkeypatch.setattr(adapter, "_event_family_market_topology_rows", lambda *a, **k: [])
    monkeypatch.setattr(state_db, "get_trade_connection", lambda **k: write_conn)

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

    result = substrate_observer._refresh_pending_family_snapshots(world_conn, forecasts_conn)

    assert result["status"] == "refreshed"
    assert result["gamma_refresh_families"] == 1
    assert result["skipped_not_found"] == 0
    assert gamma_calls == [{"slug": "highest-temperature-in-hong-kong-on-june-7-2026"}]
    assert len(submitted) == 1
    assert submitted[0][0]["slug"] == gamma_event["slug"]


def test_mainstream_warm_cycle_uses_bounded_fresh_family_window(monkeypatch):
    was_bootstrap_complete = main_module._held_position_monitor_bootstrap_complete.is_set()
    main_module._held_position_monitor_bootstrap_complete.set()
    monkeypatch.setattr(
        main_module,
        "_settings_section",
        lambda name, default=None: (
            {"enabled": True, "mainstream_warm_max_families_per_cycle": 2}
            if name == "edli"
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

    try:
        main_module._edli_mainstream_warm_cycle()
    finally:
        if not was_bootstrap_complete:
            main_module._held_position_monitor_bootstrap_complete.clear()

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


def _venue_close_relationship_harness(monkeypatch, *, refresh_module=main_module):
    """Wire a single Hong Kong / 2026-06-07 pending family through the warm
    refresh with all venue-I/O mocked. Returns a callable
    ``run(now_utc) -> (result, submitted)`` so a single fixture can be driven at
    both a venue-OPEN and a venue-CLOSED decision clock."""
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
    import src.data.market_topology_rows as market_topology_rows
    import src.data.polymarket_client as polymarket_client
    import src.engine.event_reactor_adapter as adapter
    import src.state.db as state_db

    monkeypatch.setattr(
        adapter, "_event_family_market_topology_rows", lambda *a, **k: topology_rows
    )
    monkeypatch.setattr(
        market_topology_rows,
        "_event_family_market_topology_rows",
        lambda *a, **k: topology_rows,
    )
    monkeypatch.setattr(state_db, "get_trade_connection", lambda **k: write_conn)
    monkeypatch.setattr(state_db, "get_trade_connection_read_only", lambda **k: _FakeConn())
    monkeypatch.setattr(
        scanner,
        "reconstruct_weather_market_from_static_topology",
        lambda *a, **k: cached_market,
    )
    monkeypatch.setattr(
        scanner,
        "_gamma_get",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("Gamma should not be called")),
    )
    monkeypatch.setattr(scanner, "_parse_and_persist_weather_events", lambda *a, **k: [])
    monkeypatch.setattr(polymarket_client, "PolymarketClient", _FakePolymarketClient)

    def run(now_utc):
        submitted: list[list[dict]] = []

        def _refresh(_conn, *, markets, **_kwargs):
            submitted.append(markets)
            return {"attempted": len(markets), "inserted": len(markets)}

        monkeypatch.setattr(
            scanner, "refresh_executable_market_substrate_snapshots", _refresh
        )
        # Fresh pending family per run so a prior run's cursor / state does not leak.
        world_conn = _pending_family_conn("event-1", "Hong Kong", "2026-06-07", "high")
        result = refresh_module._refresh_pending_family_snapshots(
            world_conn, forecasts_conn, now_utc=now_utc
        )
        return result, submitted

    return run


def test_warm_lane_skips_venue_closed_family_keeps_venue_open_family(monkeypatch):
    """RELATIONSHIP (warm lane ↔ market_phase venue-close authority): the SAME
    pending family must be SKIPPED when its venue market is POST_TRADING and
    REFRESHED when the venue is still open — the only thing that differs is the
    decision clock, which both sites read through the F1 12:00-UTC anchor.

    This is the live-inventory-focus invariant (2026-06-13): a 2026-06-07 family
    re-probed AFTER its 12:00-UTC venue close (the closed-06-13 families measured
    pinning the warm time-box) wastes the bounded budget the live PRE_SETTLEMENT /
    SETTLEMENT families need. Venue-closed ⇒ skip (no topology lookup, no Gamma,
    no CLOB submit); venue-open ⇒ flow through unchanged.

    RED-on-revert: remove the ``family_venue_closed`` warm-skip in
    ``_refresh_pending_family_snapshots`` and the venue-CLOSED branch refreshes the
    family (``venue_closed_skipped == 0``, ``submitted`` non-empty) — this test goes
    red. Fail-soft direction is pinned separately (an unresolvable family is kept).
    """
    run = _venue_close_relationship_harness(monkeypatch)

    # Venue OPEN: 06:00 UTC of target_date, before the 12:00-UTC close → refreshed.
    open_now = datetime(2026, 6, 7, 6, 0, tzinfo=timezone.utc)
    open_result, open_submitted = run(open_now)
    assert open_result["status"] == "refreshed"
    assert open_result["venue_closed_skipped"] == 0
    assert open_result["cached_topology_families"] == 1
    assert len(open_submitted) == 1

    # Venue CLOSED: 18:00 UTC of target_date, after the 12:00-UTC close but BEFORE
    # Hong Kong local midnight (UTC+8 → local-day end is 16:00Z of 06-07's next
    # boundary), so EventStore._strictly_past_in_tz alone would NOT skip it — the
    # venue-close anchor is what makes this family skippable.
    closed_now = datetime(2026, 6, 7, 18, 0, tzinfo=timezone.utc)
    closed_result, closed_submitted = run(closed_now)
    assert closed_result["venue_closed_skipped"] == 1
    # The closed family produced NO refresh work: no topology family, no submit.
    assert closed_result.get("cached_topology_families", 0) == 0
    assert closed_submitted == []
    # all-fresh / no-work status (never "refreshed") because the only family was skipped.
    assert closed_result["status"] != "refreshed"


def test_lifted_substrate_warm_lane_skips_venue_closed_family(monkeypatch):
    """The sidecar-owned lifted warmer must carry the same venue-close eviction as
    ``src.main``; otherwise a closed held family can pin the refresh queue head and
    starve live executable substrate updates."""
    run = _venue_close_relationship_harness(
        monkeypatch, refresh_module=substrate_observer
    )

    closed_now = datetime(2026, 6, 7, 18, 0, tzinfo=timezone.utc)
    closed_result, closed_submitted = run(closed_now)

    assert closed_result["venue_closed_skipped"] == 1
    assert closed_result.get("cached_topology_families", 0) == 0
    assert closed_submitted == []
    assert closed_result["status"] != "refreshed"


def test_lifted_substrate_warm_lane_backs_off_gamma_empty_family(monkeypatch):
    """A family whose direct Gamma slug lookup returned empty must cool down in the
    lifted sidecar path too; otherwise the 20s warm tick hammers the same
    not-listed/no-topology family and starves refreshable live families."""
    forecasts_conn = _FakeConn()
    write_conn = _FakeConn()
    substrate_observer._GAMMA_EMPTY_BACKOFF_UNTIL.clear()
    monkeypatch.setenv("ZEUS_REACTOR_GAMMA_EMPTY_BACKOFF_SECONDS", "300")

    import src.data.market_scanner as scanner
    import src.data.market_topology_rows as market_topology_rows
    import src.state.db as state_db

    class _EmptyGammaResponse:
        status_code = 200

        def json(self):
            return []

    gamma_calls = {"count": 0}

    def _empty_gamma(*_args, **_kwargs):
        gamma_calls["count"] += 1
        return _EmptyGammaResponse()

    monkeypatch.setattr(
        market_topology_rows,
        "_event_family_market_topology_rows",
        lambda *a, **k: [],
    )
    monkeypatch.setattr(state_db, "get_trade_connection", lambda **k: write_conn)
    monkeypatch.setattr(state_db, "get_trade_connection_read_only", lambda **k: _FakeConn())
    monkeypatch.setattr(scanner, "_gamma_get", _empty_gamma)
    monkeypatch.setattr(scanner, "_parse_and_persist_weather_events", lambda *a, **k: [])

    open_now = datetime(2026, 6, 7, 6, 0, tzinfo=timezone.utc)
    first_conn = _pending_family_conn("event-1", "Hong Kong", "2026-06-07", "high")
    first = substrate_observer._refresh_pending_family_snapshots(
        first_conn, forecasts_conn, now_utc=open_now
    )

    second_conn = _pending_family_conn("event-2", "Hong Kong", "2026-06-07", "high")
    second = substrate_observer._refresh_pending_family_snapshots(
        second_conn, forecasts_conn, now_utc=open_now
    )

    assert first["gamma_slug_attempted"] == 1
    assert first["gamma_slug_empty"] == 1
    assert gamma_calls["count"] == 1
    assert second.get("gamma_refresh_families", 0) == 0
    assert second["no_topology_backed_off"] == 1
    assert gamma_calls["count"] == 1


def test_warm_lane_venue_close_skip_is_failsoft_on_unresolvable_family(monkeypatch):
    """Fail-SOFT direction of the venue-close warm-skip: an UNRESOLVABLE family
    (city not in the runtime registry) must be KEPT (not skipped) even past the
    F1 close instant — uncertain ⇒ keep, never drop a possibly-tradeable family.

    Pins the asymmetry: ``family_venue_closed`` returns False on an unresolvable
    city, so the warm lane processes it normally. RED-on-revert of a hypothetical
    fail-CLOSED variant (skip on unresolvable) would drop this family and fail."""
    forecasts_conn = _FakeConn()
    write_conn = _FakeConn()
    cached_market = {
        "slug": "highest-temperature-in-atlantis-on-june-7-2026",
        "city": SimpleNamespace(name="Atlantis"),
        "target_date": "2026-06-07",
        "temperature_metric": "high",
        "outcomes": [
            {
                "condition_id": "cond-x",
                "market_id": "cond-x",
                "token_id": "yes-x",
                "no_token_id": "no-x",
                "question_id": "q-x",
            }
        ],
    }
    topology_rows = [
        {
            "market_slug": "highest-temperature-in-atlantis-on-june-7-2026",
            "city": "Atlantis",
            "target_date": "2026-06-07",
            "temperature_metric": "high",
            "condition_id": "cond-x",
            "token_id": "yes-x",
            "range_label": "31C",
        }
    ]

    import src.data.market_scanner as scanner
    import src.data.polymarket_client as polymarket_client
    import src.engine.event_reactor_adapter as adapter
    import src.state.db as state_db

    submitted: list[list[dict]] = []

    monkeypatch.setattr(
        adapter, "_event_family_market_topology_rows", lambda *a, **k: topology_rows
    )
    monkeypatch.setattr(state_db, "get_trade_connection", lambda **k: write_conn)
    monkeypatch.setattr(
        scanner,
        "reconstruct_weather_market_from_static_topology",
        lambda *a, **k: cached_market,
    )
    monkeypatch.setattr(scanner, "_gamma_get", lambda *a, **k: None)
    monkeypatch.setattr(scanner, "_parse_and_persist_weather_events", lambda *a, **k: [])
    monkeypatch.setattr(
        scanner,
        "refresh_executable_market_substrate_snapshots",
        lambda _c, *, markets, **_k: submitted.append(markets) or {"attempted": 1, "inserted": 1},
    )
    monkeypatch.setattr(polymarket_client, "PolymarketClient", _FakePolymarketClient)

    world_conn = _pending_family_conn("event-1", "Atlantis", "2026-06-07", "high")
    # Decision clock well past the F1 close — a RESOLVABLE family here would skip,
    # but the unresolvable city must be KEPT (fail-soft).
    closed_now = datetime(2026, 6, 7, 18, 0, tzinfo=timezone.utc)
    result = main_module._refresh_pending_family_snapshots(
        world_conn, forecasts_conn, now_utc=closed_now
    )

    assert result["venue_closed_skipped"] == 0
    assert result["status"] == "refreshed"
    assert len(submitted) == 1

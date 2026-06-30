# Created: 2026-06-01
# Last reused/audited: 2026-06-29
# Authority basis (2026-06-13 add): docs/archive/2026-Q2/operations_historical/live_inventory_warm_skip_2026-06-13.md —
#   venue-close warm-skip relationship tests (live-inventory focus; market_phase.family_venue_closed).
# Authority basis: src/main.py:_edli_event_reactor_cycle (historical inline substrate refresh
#   coupling) + _edli_bankroll_warm_cycle precedent (#45 follow-up, the decoupled-warm pattern) +
#   src/data/substrate_observer.py:_refresh_pending_family_snapshots (targeted family topology +
#   per-token CLOB capture).
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

import contextlib
import inspect
import json
import re
import sqlite3
from datetime import date, datetime, time, timezone
from types import SimpleNamespace

import pytest

import src.main as main_module
from src.contracts.executable_market_snapshot import FRESHNESS_WINDOW_DEFAULT
import src.data.market_scanner as market_scanner
import src.data.substrate_observer as substrate_observer


def _create_minimal_trade_exposure_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE position_current (
            position_id TEXT,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            phase TEXT,
            chain_state TEXT,
            chain_shares REAL,
            chain_cost_basis_usd REAL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE venue_commands (
            command_id TEXT,
            position_id TEXT,
            venue_order_id TEXT,
            intent_kind TEXT,
            state TEXT,
            token_id TEXT,
            snapshot_id TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE venue_order_facts (
            venue_order_id TEXT,
            state TEXT,
            remaining_size REAL,
            local_sequence INTEGER
        )
        """
    )


def test_held_position_refresh_scope_excludes_stale_closed_trading_dates(monkeypatch):
    """Old chain-backed rows stay settlement work; they must not consume live refresh budget."""

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(2026, 6, 30, 2, 50, tzinfo=timezone.utc)
            return value if tz is None else value.astimezone(tz)

    class _NoClose:
        def __init__(self, conn: sqlite3.Connection) -> None:
            self._conn = conn

        def execute(self, *args, **kwargs):
            return self._conn.execute(*args, **kwargs)

        def close(self) -> None:
            pass

    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE position_current (
            position_id TEXT,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            phase TEXT,
            chain_state TEXT,
            chain_shares REAL,
            condition_id TEXT
        )
        """
    )
    conn.executemany(
        """
        INSERT INTO position_current (
            position_id, city, target_date, temperature_metric, phase,
            chain_state, chain_shares, condition_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "old-munich-30-no",
                "Munich",
                "2026-06-17",
                "high",
                "quarantined",
                "entry_authority_quarantined",
                29.14,
                "old-cond",
            ),
            (
                "current-miami",
                "Miami",
                "2026-06-29",
                "high",
                "active",
                "synced",
                12.0,
                "current-cond",
            ),
        ],
    )
    monkeypatch.setattr(substrate_observer, "datetime", _FixedDatetime)
    monkeypatch.setattr(
        "src.state.db.get_trade_connection_read_only",
        lambda: _NoClose(conn),
    )

    rows = substrate_observer._edli_current_held_position_scope_rows()

    assert rows == [(("Miami", "2026-06-29", "high"), "current-cond")]


def test_day0_live_family_admission_uses_market_events_without_coldboot_flood():
    forecasts_conn = sqlite3.connect(":memory:")
    trade_conn = sqlite3.connect(":memory:")
    _create_minimal_trade_exposure_tables(trade_conn)
    forecasts_conn.execute(
        "CREATE TABLE market_events (city TEXT, target_date TEXT, temperature_metric TEXT)"
    )
    forecasts_conn.execute(
        "INSERT INTO market_events VALUES (?, ?, ?)",
        ("Paris", "2026-06-29", "low"),
    )

    admission = main_module._edli_day0_live_family_admission(
        forecasts_conn,
        trade_conn,
        decision_time=datetime(2026, 6, 29, 10, 0, tzinfo=timezone.utc),
    )

    assert admission.expiry_safe is True
    assert admission({"city": "Paris", "target_date": "2026-06-29", "metric": "lowest"})
    assert not admission({"city": "Zhengzhou", "target_date": "2026-06-29", "metric": "high"})
    assert not admission({"city": "Paris", "target_date": "2026-06-28", "metric": "low"})


def test_day0_live_family_admission_uses_city_local_day_not_utc_floor(monkeypatch):
    forecasts_conn = sqlite3.connect(":memory:")
    trade_conn = sqlite3.connect(":memory:")
    _create_minimal_trade_exposure_tables(trade_conn)
    forecasts_conn.execute(
        "CREATE TABLE market_events (city TEXT, target_date TEXT, temperature_metric TEXT)"
    )
    monkeypatch.setitem(
        main_module.cities_by_name,
        "Testville",
        SimpleNamespace(
            name="Testville",
            aliases=(),
            slug_names=(),
            timezone="America/Chicago",
        ),
    )
    forecasts_conn.executemany(
        "INSERT INTO market_events VALUES (?, ?, ?)",
        [
            ("Testville", "2026-06-28", "high"),
            ("Testville", "2026-06-29", "high"),
        ],
    )

    admission = main_module._edli_day0_live_family_admission(
        forecasts_conn,
        trade_conn,
        decision_time=datetime(2026, 6, 29, 2, 0, tzinfo=timezone.utc),
    )

    assert admission({"city": "Testville", "target_date": "2026-06-28", "metric": "high"})
    assert not admission({"city": "Testville", "target_date": "2026-06-29", "metric": "high"})


def test_day0_live_family_admission_empty_market_events_does_not_admit_unexposed_family():
    forecasts_conn = sqlite3.connect(":memory:")
    trade_conn = sqlite3.connect(":memory:")
    _create_minimal_trade_exposure_tables(trade_conn)
    forecasts_conn.execute(
        "CREATE TABLE market_events (city TEXT, target_date TEXT, temperature_metric TEXT)"
    )

    admission = main_module._edli_day0_live_family_admission(
        forecasts_conn,
        trade_conn,
        decision_time=datetime(2026, 6, 29, 10, 0, tzinfo=timezone.utc),
    )

    assert admission.expiry_safe is True
    assert admission.admitted_families == frozenset()
    assert not admission({"city": "Paris", "target_date": "2026-06-29", "metric": "low"})


def test_confirmation_refresh_prune_restricts_to_priority_conditions():
    """Scoped redecision confirmation must not refresh every sibling bin."""

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE executable_market_snapshots (
            yes_token_id TEXT,
            no_token_id TEXT,
            selected_outcome_token_id TEXT,
            condition_id TEXT,
            freshness_deadline TEXT,
            captured_at TEXT,
            snapshot_id TEXT
        )
        """
    )
    market = {
        "outcomes": [
            {"condition_id": "priority-cond", "token_id": "yes-priority", "no_token_id": "no-priority"},
            {"condition_id": "sibling-cond", "token_id": "yes-sibling", "no_token_id": "no-sibling"},
        ],
        "condition_ids": ["priority-cond", "sibling-cond"],
    }

    scoped, _fresh, stale = main_module._prune_fresh_market_outcomes_for_snapshot_refresh(
        conn,
        [market],
        fresh_at_iso="2026-06-26T00:00:00+00:00",
        restrict_to_condition_ids={"priority-cond"},
    )
    ordinary, _fresh2, ordinary_stale = main_module._prune_fresh_market_outcomes_for_snapshot_refresh(
        conn,
        [market],
        fresh_at_iso="2026-06-26T00:00:00+00:00",
    )

    assert stale == 1
    assert [o["condition_id"] for o in scoped[0]["outcomes"]] == ["priority-cond"]
    assert scoped[0]["condition_ids"] == ["priority-cond"]
    assert ordinary_stale == 2
    assert [o["condition_id"] for o in ordinary[0]["outcomes"]] == ["priority-cond", "sibling-cond"]
    sidecar_scoped, _sidecar_fresh, sidecar_stale = (
        substrate_observer._prune_fresh_market_outcomes_for_snapshot_refresh(
            conn,
            [market],
            fresh_at_iso="2026-06-26T00:00:00+00:00",
            restrict_to_condition_ids={"priority-cond"},
        )
    )
    assert sidecar_stale == 1
    assert [o["condition_id"] for o in sidecar_scoped[0]["outcomes"]] == ["priority-cond"]
    assert sidecar_scoped[0]["condition_ids"] == ["priority-cond"]


def _venue_open_now(target_date: str) -> datetime:
    """A frozen decision-clock instant during the target local day.

    Warm refresh no longer treats F1/Gamma endDate as venue-close proof, but
    fixed-date fixtures still need an injected clock so they do not depend on
    wall time.
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
    saved_lifted = substrate_observer._SUBSTRATE_REFRESH_CURSOR
    substrate_observer._SUBSTRATE_REFRESH_CURSOR = 0
    saved_lifted_priority = substrate_observer._SUBSTRATE_PRIORITY_REFRESH_CURSOR
    substrate_observer._SUBSTRATE_PRIORITY_REFRESH_CURSOR = 0
    saved_lifted_gamma = substrate_observer._SUBSTRATE_GAMMA_REFRESH_CURSOR
    substrate_observer._SUBSTRATE_GAMMA_REFRESH_CURSOR = 0
    try:
        yield
    finally:
        substrate_observer._SUBSTRATE_REFRESH_CURSOR = saved_lifted
        substrate_observer._SUBSTRATE_PRIORITY_REFRESH_CURSOR = saved_lifted_priority
        substrate_observer._SUBSTRATE_GAMMA_REFRESH_CURSOR = saved_lifted_gamma


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


def test_static_topology_reconstruction_reads_narrow_snapshot_columns():
    """Warm-lane reconstruction must not pull historical orderbook depth payloads."""

    src = inspect.getsource(market_scanner.reconstruct_weather_market_from_static_topology)

    assert "SELECT * FROM executable_market_snapshots" not in src
    assert "snapshot_select_columns" in src
    assert "orderbook_depth_json" not in src


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


def test_pending_family_refresh_orders_money_path_urgency_before_target_date():
    """Day0/redecision freshness must not be buried behind newer future dates."""

    main_src = inspect.getsource(main_module._pending_family_rows_for_refresh)
    observer_src = inspect.getsource(substrate_observer._pending_family_rows_for_refresh)

    for src in (main_src, observer_src):
        day0_pos = src.index("WHEN 'DAY0_EXTREME_UPDATED' THEN 4")
        redecision_pos = src.index("WHEN 'EDLI_REDECISION_PENDING' THEN 3")
        target_pos = src.index("MAX(json_extract(e.payload_json, '$.target_date')) DESC")
        assert day0_pos < target_pos
        assert redecision_pos < target_pos


def test_full_family_capture_cap_is_decoupled_from_direct_clob_threshold():
    """Candidate selection cap and direct /book prefetch threshold serve different purposes."""

    assert market_scanner._snapshot_capture_max_candidates_per_tick(per_city_limit=0) < (
        market_scanner._full_family_direct_clob_prefetch_candidate_threshold()
    )
    src = inspect.getsource(market_scanner.refresh_executable_market_substrate_snapshots)
    assert "len(selected_candidates) <= full_family_direct_clob_candidate_threshold" in src
    assert "priority_direct_clob_service_conditions" in src


def test_priority_direct_clob_uses_selected_priority_subset():
    """A broad priority universe must be serviced by a bounded selected subset."""

    src = inspect.getsource(market_scanner.refresh_executable_market_substrate_snapshots)

    assert "ordered_selected_priority_conditions" in src
    assert "priority_condition_rank" in src
    assert "priority_condition_rank.get" in src
    assert "priority_direct_clob_service_conditions = set(" in src
    assert "ordered_selected_priority_conditions[:priority_direct_clob_condition_limit]" in src
    assert "len(priority_conditions) <= priority_direct_clob_condition_limit" not in src


def test_active_risk_conditions_are_hot_even_with_priority_marker():
    """A marker request must not exclude live open-rest or held-position exact conditions."""

    src = inspect.getsource(substrate_observer._edli_money_path_substrate_priority_cycle)
    open_read = src.index("open_rest_priority_condition_ids = _open_rest_condition_ids_for_refresh")
    held_read = src.index("held_position_priority_condition_ids = _edli_current_held_position_condition_ids()")
    extend_marker = src.index("exact_priority_condition_ids = list(priority_marker_condition_ids)")
    extend_open = src.index("exact_priority_condition_ids.extend(open_rest_priority_condition_ids)")
    extend_held = src.index("exact_priority_condition_ids.extend(held_position_priority_condition_ids)")

    assert open_read < extend_marker < extend_open < extend_held
    assert held_read < extend_open
    assert "if not priority_marker_active:\n                exact_priority_condition_ids.extend" not in src


def test_warm_lane_money_risk_priority_stays_ahead_of_pending_rotation():
    """Open rests and held positions are live money-risk, not ordinary backlog.

    They must remain ahead of the rotating pending-event tail every tick; the
    fair cursor should rotate only the ordinary pending families so a large
    pending queue cannot bury already-submitted orders or chain-confirmed
    holdings.
    """

    src = inspect.getsource(substrate_observer._refresh_pending_family_snapshots)

    assert "get_trade_connection" in src
    assert "get_trade_connection_read_only" in src
    assert "held_position_priority_families" in src
    assert "priority_families + new_priority_families + rotated_ordinary_families" in src
    assert "ordinary_families[start_offset:] + ordinary_families[:start_offset]" in src


def test_substrate_held_scope_includes_chain_backed_quarantine_and_voided(
    monkeypatch,
    tmp_path,
):
    """Chain-positive quarantine/voided exposure must stay in hot substrate scope."""

    db_path = tmp_path / "trades.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE position_current (
                city TEXT,
                target_date TEXT,
                temperature_metric TEXT,
                condition_id TEXT,
                phase TEXT,
                chain_state TEXT,
                chain_shares REAL,
                shares REAL
            )
            """
        )
        conn.executemany(
            "INSERT INTO position_current VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("Munich", "2026-06-30", "high", "cond-munich-30", "quarantined", "chain_absent_confirmed_position_unattributed", 29.14, 29.14),
                ("Madrid", "2026-06-30", "high", "cond-madrid-29", "voided", "synced", 4.5, 0.0),
                ("Paris", "2026-06-30", "low", "cond-paris-19", "day0_window", "synced", 5.0, 5.0),
                ("Seoul", "2026-06-30", "high", "cond-zero", "quarantined", "synced", 0.0, 10.0),
                ("London", "2026-06-30", "high", "cond-closed", "economically_closed", "synced", 7.0, 7.0),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    import src.state.db as state_db

    monkeypatch.setattr(
        state_db,
        "get_trade_connection_read_only",
        lambda: sqlite3.connect(db_path),
    )

    assert set(substrate_observer._edli_current_held_position_condition_ids()) == {
        "cond-munich-30",
        "cond-madrid-29",
        "cond-paris-19",
    }
    assert substrate_observer._edli_current_held_position_family_keys() == {
        ("Munich", "2026-06-30", "high"),
        ("Madrid", "2026-06-30", "high"),
        ("Paris", "2026-06-30", "low"),
    }


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
    assert "open_rest_condition_scope = _edli_open_rest_condition_scope(open_rests, all_beliefs)" in screen_src
    assert "open_rest_condition_scope," in screen_src
    assert "ZEUS_REDECISION_CONFIRM_REFRESH_LOCK_TIMEOUT_SECONDS" in confirm_src
    assert "_edli_redecision_confirm_refresh_lock" in confirm_src
    assert "_market_substrate_refresh_lock" not in confirm_src
    assert "mark_money_path_substrate_priority(" in confirm_src
    assert "_refresh_pending_family_snapshots(" not in confirm_src
    assert "READ_FILTER_REQUIRED" in confirm_src
    assert "_edli_confirmation_refresh_needs_scoped_freshness_filter(confirm_refresh_summary)" in screen_src
    assert "_edli_families_with_fresh_scoped_executable_substrate(" in screen_src
    assert "confirmed_entry_scope &= fresh_entry_scope" in screen_src
    assert "confirmed_rest_scope &= fresh_rest_scope" in screen_src
    assert "_edli_confirmation_refresh_unavailable(confirm_refresh_summary)" in screen_src


def test_live_snapshot_refresh_paths_use_shared_trade_write_coordinator():
    """Live snapshot writers must serialize across daemon processes.

    `_market_substrate_refresh_lock` is process-local. The live daemon and the
    substrate observer are separate processes, so every producer path must take
    the shared substrate refresh lock around refresh orchestration and the
    trade-DB coordinator lease around each snapshot persist+commit.
    """

    confirm_src = inspect.getsource(main_module._edli_refresh_continuous_money_path_families)
    observer_warm_src = inspect.getsource(substrate_observer._edli_market_substrate_warm_cycle)
    observer_discovery_src = inspect.getsource(substrate_observer._market_discovery_cycle)

    for src in (observer_warm_src, observer_discovery_src):
        assert "acquire_lock(\"market_substrate_refresh\")" in src
    assert "acquire_lock(\"market_substrate_refresh\")" not in confirm_src
    assert "snapshot_write_context_factory=" not in confirm_src
    assert not hasattr(main_module, "_snapshot_trade_write_context_factory")

    assert not hasattr(main_module, "_refresh_pending_family_snapshots")

    for src in (
        inspect.getsource(substrate_observer._refresh_pending_family_snapshots),
        observer_discovery_src,
    ):
        assert "snapshot_write_context_factory=" in src
        assert "db_writer_lock(_zeus_trade_db_path(), WriteClass.LIVE)" not in src
        assert "refresh_executable_market_substrate_snapshots(" in src

    for src in (
        inspect.getsource(substrate_observer._refresh_pending_family_snapshots),
        observer_discovery_src,
    ):
        assert "_substrate_snapshot_trade_write_context_factory(" in src

    assert "capture_reserve_seconds=snapshot_reserve_s" in inspect.getsource(
        substrate_observer._refresh_pending_family_snapshots
    )


def test_substrate_snapshot_write_lease_covers_sqlite_busy_floor(monkeypatch):
    """The outer writer lease must not expire before row-level SQLite lock waiting."""

    monkeypatch.delenv("ZEUS_SUBSTRATE_SNAPSHOT_DB_WRITE_LEASE_DEADLINE_MS", raising=False)
    monkeypatch.delenv("ZEUS_SUBSTRATE_SNAPSHOT_DB_WRITE_MAX_HOLD_MS", raising=False)
    monkeypatch.setenv("ZEUS_SNAPSHOT_CAPTURE_BUSY_TIMEOUT_FLOOR_MS", "4000")

    busy_floor = substrate_observer._substrate_snapshot_sqlite_busy_floor_ms()
    assert busy_floor == 4000
    assert substrate_observer._substrate_snapshot_write_lease_deadline_default_ms() == 8000

    monkeypatch.setenv("ZEUS_SUBSTRATE_SNAPSHOT_DB_WRITE_LEASE_DEADLINE_MS", "1000")
    lease_deadline = substrate_observer._substrate_snapshot_write_lease_ms(
        "substrate_snapshot_db_write_lease_deadline_ms",
        substrate_observer._substrate_snapshot_write_lease_deadline_default_ms(),
        minimum=busy_floor,
        maximum=30000,
    )
    assert lease_deadline >= busy_floor

    monkeypatch.setenv("ZEUS_SUBSTRATE_SNAPSHOT_DB_WRITE_MAX_HOLD_MS", "1000")
    max_hold = substrate_observer._substrate_snapshot_write_lease_ms(
        "substrate_snapshot_db_write_max_hold_ms",
        substrate_observer.SUBSTRATE_SNAPSHOT_DB_WRITE_MAX_HOLD_MS,
        minimum=busy_floor,
        maximum=10000,
    )
    assert max_hold >= busy_floor

    monkeypatch.setenv("ZEUS_SNAPSHOT_CAPTURE_BUSY_TIMEOUT_FLOOR_MS", "7000")
    assert substrate_observer._substrate_snapshot_write_lease_deadline_default_ms() == 8000


def test_reactor_uses_targeted_decision_refresher_for_blocked_families():
    """Stale event requeues must trigger the same targeted family recapture path."""

    cycle_src = inspect.getsource(main_module._edli_event_reactor_cycle)
    assert "_reactor_family_snapshot_refresher = _decision_family_snapshot_refresher" in cycle_src
    assert "family_snapshot_refresher=_reactor_family_snapshot_refresher" in cycle_src


def test_decision_refresher_delegates_scoped_priority_to_sidecar(monkeypatch):
    """Selected-row stale handling must not run substrate producer I/O inline."""

    refresh_src = inspect.getsource(main_module._edli_decision_family_snapshot_refresher)
    assert "mark_money_path_substrate_priority(" in refresh_src
    assert "_edli_money_path_substrate_priority_cycle()" not in refresh_src
    assert "refresh_executable_market_substrate_snapshots(" not in refresh_src
    assert "get_trade_connection" not in refresh_src
    assert "PolymarketClient" not in refresh_src
    assert "acquire_lock(\"market_substrate_refresh\")" not in refresh_src

    refresher = main_module._edli_decision_family_snapshot_refresher(None)
    assert refresher(
        city="Paris",
        target_date="2026-06-20",
        metric="low",
        condition_ids=("cond-1",),
    ) is False


def test_background_substrate_warm_leaves_lock_window_for_money_path_refresh():
    """Background warming must not occupy the shared substrate lock for the full cadence."""

    warm_src = inspect.getsource(substrate_observer._edli_market_substrate_warm_cycle)
    helper_src = inspect.getsource(substrate_observer._background_warm_refresh_budget_seconds)
    reserve_src = inspect.getsource(substrate_observer._background_warm_snapshot_reserve_seconds)
    refresh_src = inspect.getsource(substrate_observer._refresh_pending_family_snapshots)

    assert "refresh_budget_seconds=background_budget_s" in warm_src
    assert "snapshot_reserve_seconds=background_snapshot_reserve_s" in warm_src
    assert "ZEUS_SUBSTRATE_BACKGROUND_REFRESH_BUDGET_SECONDS" in helper_src
    assert '"14.0"' in helper_src
    assert "ZEUS_SUBSTRATE_BACKGROUND_SNAPSHOT_RESERVE_SECONDS" in reserve_src
    assert '"5.0"' in reserve_src
    assert "executor.shutdown(wait=False, cancel_futures=True)" in refresh_src
    assert "with ThreadPoolExecutor(" not in refresh_src


def test_substrate_warm_topology_exhaustion_is_scheduler_failure():
    summary = substrate_observer._substrate_warm_business_summary(
        {
            "status": "topology_budget_exhausted",
            "families_checked": 221,
            "topology_deferred_families": 221,
        },
        priority_request=None,
        priority_marker_active=False,
    )

    assert summary["scheduler_failed"] is True
    assert summary["scheduler_failure_reason"] == "topology_budget_exhausted"


def test_substrate_warm_refreshed_zero_coverage_exhaustion_is_scheduler_failure():
    summary = substrate_observer._substrate_warm_business_summary(
        {
            "status": "refreshed",
            "attempted": 8,
            "inserted": 0,
            "failed": 0,
            "budget_exhausted": 1,
            "executable_substrate_coverage_status": "NONE",
        },
        priority_request=None,
        priority_marker_active=False,
    )

    assert summary["scheduler_failed"] is True
    assert summary["scheduler_failure_reason"] == "snapshot_refresh_exhausted_no_coverage"


def test_targeted_decision_refresh_has_no_inline_quota_knobs():
    refresh_src = inspect.getsource(main_module._edli_decision_family_snapshot_refresher)

    assert "reactor_decision_refresh_cycle_budget_seconds" not in refresh_src
    assert "reactor_decision_refresh_max_per_cycle" not in refresh_src
    assert "ZEUS_DECISION_REFRESH_LOCK_TIMEOUT_SECONDS" not in refresh_src


def test_claim_order_priority_default_is_live_tick_window(monkeypatch):
    """Claim-order lookahead is a hot frontier, not a pending-family backlog scan."""

    monkeypatch.delenv("ZEUS_SUBSTRATE_CLAIM_PRIORITY_FAMILY_LIMIT", raising=False)
    assert substrate_observer._claim_order_priority_family_limit() == 4

    monkeypatch.setenv("ZEUS_SUBSTRATE_CLAIM_PRIORITY_FAMILY_LIMIT", "999")
    assert substrate_observer._claim_order_priority_family_limit() == 16

    monkeypatch.setenv("ZEUS_SUBSTRATE_CLAIM_PRIORITY_FAMILY_LIMIT", "not-an-int")
    assert substrate_observer._claim_order_priority_family_limit() == 4


def test_money_path_priority_default_budget_can_finish_hot_snapshot_backlog(monkeypatch):
    """The priority lane needs a real CLOB/topology window, not the old 8s churn loop."""

    monkeypatch.delenv("ZEUS_SUBSTRATE_PRIORITY_REFRESH_INTERVAL_SECONDS", raising=False)
    monkeypatch.delenv("ZEUS_SUBSTRATE_PRIORITY_REFRESH_BUDGET_SECONDS", raising=False)

    assert substrate_observer._priority_refresh_interval_seconds() == pytest.approx(20.0)
    assert substrate_observer._priority_refresh_budget_seconds() == pytest.approx(18.0)


def test_open_rest_condition_scope_maps_unpulled_rests_to_priority_conditions():
    belief = SimpleNamespace(
        family_id="family-1",
        city="Singapore",
        target_date="2026-06-27",
        metric="high",
    )
    rest = SimpleNamespace(family_id="family-1", condition_id="cond-rest")
    unrelated = SimpleNamespace(family_id="family-2", condition_id="cond-other")

    assert main_module._edli_open_rest_condition_scope([rest, unrelated], [belief]) == {
        ("Singapore", "2026-06-27", "high"): {"cond-rest"}
    }


def test_substrate_open_rest_scope_resolves_before_position_projection():
    """ACKED rests must be repriced even before position_current catches up."""

    trade_conn = sqlite3.connect(":memory:")
    forecasts_conn = sqlite3.connect(":memory:")
    trade_conn.executescript(
        """
        CREATE TABLE venue_commands (
            command_id TEXT,
            position_id TEXT,
            venue_order_id TEXT,
            intent_kind TEXT,
            state TEXT,
            token_id TEXT,
            snapshot_id TEXT
        );
        CREATE TABLE venue_order_facts (
            venue_order_id TEXT,
            state TEXT,
            remaining_size REAL,
            local_sequence INTEGER
        );
        CREATE TABLE position_current (
            position_id TEXT,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            phase TEXT
        );
        CREATE TABLE executable_market_snapshots (
            snapshot_id TEXT,
            condition_id TEXT,
            selected_outcome_token_id TEXT,
            yes_token_id TEXT,
            no_token_id TEXT,
            captured_at TEXT
        );
        """
    )
    forecasts_conn.execute(
        """
        CREATE TABLE market_events (
            condition_id TEXT,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT
        )
        """
    )
    trade_conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, position_id, venue_order_id, intent_kind, state, token_id, snapshot_id
        ) VALUES ('cmd-1', 'pos-not-projected', 'order-1', 'ENTRY', 'ACKED', 'yes-31', 'snap-1')
        """
    )
    trade_conn.execute(
        """
        INSERT INTO venue_order_facts (
            venue_order_id, state, remaining_size, local_sequence
        ) VALUES ('order-1', 'RESTING', 5.0, 1)
        """
    )
    trade_conn.execute(
        """
        INSERT INTO executable_market_snapshots (
            snapshot_id, condition_id, selected_outcome_token_id, yes_token_id, no_token_id,
            captured_at
        ) VALUES ('snap-1', 'cond-shanghai-31', 'yes-31', 'yes-31', 'no-31',
                  '2026-06-29T00:00:00+00:00')
        """
    )
    forecasts_conn.execute(
        """
        INSERT INTO market_events (
            condition_id, city, target_date, temperature_metric
        ) VALUES ('cond-shanghai-31', 'Shanghai', '2026-06-29', 'high')
        """
    )

    families = substrate_observer._open_rest_family_rows_for_refresh(
        trade_conn,
        forecasts_conn=forecasts_conn,
    )
    condition_ids = substrate_observer._open_rest_condition_ids_for_refresh(
        trade_conn,
        forecasts_conn=forecasts_conn,
    )

    assert families == [("Shanghai", "2026-06-29", "high")]
    assert condition_ids == ["cond-shanghai-31"]


def test_continuous_redecision_confirm_refresh_delegates_snapshot_production(monkeypatch):
    """The live daemon must not race the substrate sidecar for snapshot writes."""

    import src.data.substrate_priority as substrate_priority

    marked: list[dict] = []

    def _mark(**kwargs):
        marked.append(kwargs)

    monkeypatch.setattr(substrate_priority, "mark_money_path_substrate_priority", _mark)
    monkeypatch.setattr(main_module.time, "sleep", lambda _delay: (_ for _ in ()).throw(AssertionError("no retry sleeps")))

    result = main_module._edli_refresh_continuous_money_path_families(
        {("Paris", "2026-06-20", "low")},
        now_utc=datetime(2026, 6, 19, 12, 0, tzinfo=timezone.utc),
        priority_condition_ids={"cond-1", "cond-2"},
    )

    assert result["status"] == "priority_marked"
    assert result["executable_substrate_coverage_status"] == "READ_FILTER_REQUIRED"
    assert result["families_requested"] == 1
    assert result["priority_condition_count"] == 2
    assert marked == [
        {
            "reason": "continuous_redecision_confirm_refresh",
            "ttl_seconds": 35.0,
            "families": (),
            "condition_ids": {"cond-1", "cond-2"},
        }
    ]


def test_confirm_priority_condition_ids_are_bounded_money_path_frontier(monkeypatch):
    monkeypatch.setenv("ZEUS_REDECISION_PRIORITY_CONDITION_LIMIT", "4")
    family_a = ("Paris", "2026-06-20", "low")
    family_b = ("Shanghai", "2026-06-20", "high")

    condition_ids = main_module._edli_confirm_priority_condition_ids(
        rest_condition_scope={family_b: {"rest-2", "rest-1"}},
        held_condition_scope={family_a: {"held-1"}},
        entry_condition_scope={family_a: {"entry-1"}},
        entry_refresh_condition_scope={family_a: {"refresh-1", "refresh-2"}},
        open_rest_condition_scope={family_a: {"open-rest-1"}},
    )

    assert condition_ids == ["rest-1", "rest-2", "held-1", "entry-1"]


def test_continuous_redecision_confirm_refresh_does_not_wait_on_substrate_process_lock():
    """Sidecar ownership is asynchronous; main only marks priority and filters reads."""

    confirm_src = inspect.getsource(main_module._edli_refresh_continuous_money_path_families)
    assert "src.data.dual_run_lock" not in confirm_src
    assert "acquire_lock(\"market_substrate_refresh\")" not in confirm_src
    assert "get_world_connection" not in confirm_src
    assert "get_forecasts_connection_read_only" not in confirm_src
    assert "ZEUS_REDECISION_CONFIRM_REFRESH_PROCESS_LOCK_RETRY_SECONDS" not in confirm_src
    assert "ZEUS_REDECISION_CONFIRM_REFRESH_LOCK_RETRY_SECONDS" not in confirm_src


def test_continuous_redecision_confirm_refresh_unavailable_on_locked_or_partial_summary():
    """The refresh summary is not live authority for PARTIAL/NONE coverage.

    A PARTIAL capture is not globally sufficient, but it must be resolved by
    scoped condition freshness proof instead of freezing every current family.
    """

    assert main_module._edli_confirmation_refresh_unavailable(
        {
            "status": "skipped_lock_busy",
            "executable_substrate_coverage_status": "FULL",
            "failure_samples": [{"error": "database is locked"}],
        }
    )
    assert main_module._edli_confirmation_refresh_unavailable(
        {"status": "error_refresh_failed", "executable_substrate_coverage_status": "PARTIAL"}
    )
    assert not main_module._edli_confirmation_refresh_unavailable(
        {"status": "refreshed", "executable_substrate_coverage_status": "PARTIAL"}
    )
    assert not main_module._edli_confirmation_refresh_unavailable(
        {
            "status": "refreshed",
            "executable_substrate_coverage_status": "PARTIAL",
            "failure_samples": [{"error": "database is locked"}],
        }
    )
    assert not main_module._edli_confirmation_refresh_unavailable(
        {"status": "refreshed", "executable_substrate_coverage_status": "NONE"}
    )
    assert main_module._edli_confirmation_refresh_needs_scoped_freshness_filter(
        {"status": "refreshed", "executable_substrate_coverage_status": "NONE"}
    )
    assert main_module._edli_confirmation_refresh_needs_scoped_freshness_filter(
        {"status": "refreshed", "executable_substrate_coverage_status": "PARTIAL"}
    )
    assert main_module._edli_confirmation_refresh_needs_scoped_freshness_filter(
        {
            "status": "refreshed",
            "executable_substrate_coverage_status": "PARTIAL",
            "failure_samples": [{"error": "database is locked"}],
        }
    )
    assert not main_module._edli_confirmation_refresh_unavailable(
        {"status": "refreshed", "executable_substrate_coverage_status": "FULL"}
    )


def test_continuous_redecision_partial_refresh_filters_to_fresh_families(monkeypatch):
    """A PARTIAL confirmation refresh must not freeze every family. Only families
    whose full topology has fresh YES and NO executable substrate are admitted."""

    import src.data.market_topology_rows as topology_rows
    import src.state.db as state_db

    class _Conn:
        def __init__(self, name: str):
            self.name = name
            self.closed = False

        def close(self):
            self.closed = True

    forecasts = _Conn("forecasts")
    trade = _Conn("trade")
    topology = {
        ("Paris", "2026-06-20", "low"): [{"condition_id": "fresh-a"}, {"condition_id": "fresh-b"}],
        ("Tokyo", "2026-06-20", "high"): [{"condition_id": "fresh-c"}, {"condition_id": "stale-d"}],
        ("Berlin", "2026-06-20", "high"): [],
    }
    fresh_conditions: list[str] = []

    def _topology(_conn, payload):
        return topology.get((payload["city"], payload["target_date"], payload["metric"]), [])

    def _fresh(_conn, condition_id, fresh_at_iso):
        assert fresh_at_iso == "2026-06-19T12:00:00+00:00"
        fresh_conditions.append(condition_id)
        return condition_id.startswith("fresh")

    monkeypatch.setattr(state_db, "get_forecasts_connection_read_only", lambda: forecasts)
    monkeypatch.setattr(state_db, "get_trade_connection_read_only", lambda: trade)
    monkeypatch.setattr(topology_rows, "_event_family_market_topology_rows", _topology)
    monkeypatch.setattr(main_module, "_condition_buy_sides_fresh", _fresh)

    admitted = main_module._edli_families_with_fresh_executable_substrate(
        {
            ("Paris", "2026-06-20", "low"),
            ("Tokyo", "2026-06-20", "high"),
            ("Berlin", "2026-06-20", "high"),
        },
        now_utc=datetime(2026, 6, 19, 12, 0, tzinfo=timezone.utc),
    )

    assert admitted == {("Paris", "2026-06-20", "low")}
    assert {"fresh-a", "fresh-b", "stale-d"}.issubset(set(fresh_conditions))
    assert forecasts.closed
    assert trade.closed


def test_continuous_redecision_partial_refresh_filters_to_scoped_conditions(monkeypatch):
    """PARTIAL confirmation is money-path scoped: a current rest/candidate must
    not disappear because unrelated sibling bins did not refresh in the same tick."""

    import src.state.db as state_db

    class _Conn:
        closed = False

        def close(self):
            self.closed = True

    trade = _Conn()
    checked: list[str] = []

    def _fresh(_conn, condition_id, fresh_at_iso):
        assert _conn is trade
        assert fresh_at_iso == "2026-06-19T12:00:00+00:00"
        checked.append(condition_id)
        return condition_id != "stale-scoped"

    monkeypatch.setattr(state_db, "get_trade_connection_read_only", lambda: trade)
    monkeypatch.setattr(main_module, "_condition_buy_sides_fresh", _fresh)

    admitted = main_module._edli_families_with_fresh_scoped_executable_substrate(
        {
            ("Paris", "2026-06-20", "low"): {"fresh-rest"},
            ("Tokyo", "2026-06-20", "high"): {"fresh-entry", "stale-scoped"},
        },
        now_utc=datetime(2026, 6, 19, 12, 0, tzinfo=timezone.utc),
    )

    assert admitted == {("Paris", "2026-06-20", "low")}
    assert checked == ["fresh-rest", "fresh-entry", "stale-scoped"]
    assert trade.closed


def test_redecision_condition_scope_uses_screened_bin_condition_id():
    belief = SimpleNamespace(
        family_id="fam-1",
        city="Paris",
        target_date="2026-06-20",
        metric="low",
        bin_labels=["18C", "19C"],
        condition_ids=["cond-18", "cond-19"],
    )
    redecision = SimpleNamespace(family_id="fam-1", bin_label="19C", direction="buy_no")

    scope = main_module._edli_redecision_condition_scope([redecision], [belief])

    assert scope == {("Paris", "2026-06-20", "low"): {"cond-19"}}


def test_day0_emit_scanner_retries_sqlite_lock(monkeypatch):
    class Trigger:
        def __init__(self):
            self.authority_calls = 0
            self.observation_calls = 0
            self.authority_conns = []
            self.observation_conns = []

        def scan_authority_rows(self, **kwargs):
            self.authority_calls += 1
            self.authority_conns.append(kwargs["observation_conn"])
            if self.authority_calls == 1:
                raise sqlite3.OperationalError("database is locked")
            return ["authority"]

        def scan_observation_instants_rows(self, **kwargs):
            self.observation_calls += 1
            self.observation_conns.append(kwargs["observation_conn"])
            return ["observation"]

    trigger = Trigger()
    sleeps = []
    world_conn = object()
    trade_conn = object()
    monkeypatch.setenv("ZEUS_DAY0_EMIT_LOCK_RETRY_SECONDS", "0.01")
    monkeypatch.setattr(main_module.time, "sleep", lambda delay: sleeps.append(delay))

    authority, observation = main_module._edli_scan_day0_with_lock_retry(
        trigger=trigger,
        world_conn=world_conn,
        trade_conn=trade_conn,
        decision_time=datetime(2026, 6, 19, 12, 0, tzinfo=timezone.utc),
        received_at="2026-06-19T12:00:00+00:00",
        limit=10,
    )

    assert authority == ["authority"]
    assert observation == ["observation"]
    assert trigger.authority_calls == 2
    assert trigger.observation_calls == 1
    assert trigger.authority_conns == [trade_conn, trade_conn]
    assert trigger.observation_conns == [world_conn]
    assert sleeps == [0.01]


def test_day0_emit_lock_exhaustion_is_caught_at_reactor_boundary():
    source = inspect.getsource(main_module._edli_event_reactor_cycle)

    assert "_edli_emit_day0_extreme_events" in source
    assert "_edli_is_sqlite_lock_error(_day0_emit_lock_exc)" in source
    assert "skipping Day0 emit this cycle" in source


def test_snapshot_capture_budget_never_extends_scheduler_tick(monkeypatch):
    """Late topology selection must not fabricate CLOB time after the deadline.

    The prior reserve-as-floor behavior returned 14-15.5s even when the refresh
    deadline was already in the past. Live evidence showed that lets one warm
    cycle outlive its 20s scheduler cadence and makes later ticks skip with
    max_instances=1 forever.
    """

    monkeypatch.setattr(main_module.time, "monotonic", lambda: 100.0)
    monkeypatch.setenv("ZEUS_MARKET_DISCOVERY_ORDERBOOK_PREFETCH_MIN_WINDOW_SECONDS", "0.75")

    assert substrate_observer._snapshot_capture_budget_for_refresh(
        refresh_deadline=90.0,
        snapshot_reserve_s=12.0,
    ) == pytest.approx(0.0)
    assert substrate_observer._snapshot_capture_budget_for_refresh(
        refresh_deadline=125.0,
        snapshot_reserve_s=12.0,
    ) == pytest.approx(25.0)

    monkeypatch.setenv("ZEUS_MARKET_DISCOVERY_ORDERBOOK_PREFETCH_TARGET_WINDOW_SECONDS", "3.5")
    assert substrate_observer._snapshot_capture_budget_for_refresh(
        refresh_deadline=104.0,
        snapshot_reserve_s=12.0,
    ) == pytest.approx(4.0)


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
        }
        for job_name in live_decision_jobs:
            assert main_module._defer_for_held_position_monitor(job_name) is False

        discretionary_jobs = {
            "market_discovery",
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


def test_trading_daemon_does_not_host_new_listing_discovery():
    """New listing discovery is a substrate-observer responsibility, not a live
    trading-daemon scheduler job.

    The trading daemon must not run Gamma universe discovery or stage hidden
    producer state while the decision line is trying to consume fresh substrate.
    """

    src = inspect.getsource(main_module)

    assert "_new_listing_scout_cycle" not in src
    assert 'id="new_listing_scout"' not in src
    assert "_afternoon_snapshot_capture_cycle" not in src
    assert 'id="afternoon_snapshot_capture"' not in src
    assert "find_weather_markets_or_raise" not in src
    assert "ZEUS_USER_CHANNEL_BOOT_GAMMA_SCAN" not in src


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
    monkeypatch.setattr(substrate_observer, "money_path_substrate_priority_active", lambda: False)
    monkeypatch.setattr(
        "src.data.dual_run_lock.acquire_lock",
        lambda _name: contextlib.nullcontext(True),
    )
    _enable_edli_cfg(monkeypatch, enabled=True)

    substrate_observer._edli_market_substrate_warm_cycle()
    assert calls == [1], "warm job must invoke the family-snapshot refresh exactly once"


def test_money_path_priority_cycle_prioritizes_claim_order_families(monkeypatch):
    """The priority producer must warm the families the reactor can claim next."""

    calls: list[dict] = []
    claim_families = [("Tokyo", "2026-06-28", "high"), ("Shanghai", "2026-06-28", "low")]
    monkeypatch.setattr(
        substrate_observer,
        "_claim_order_priority_families_for_refresh",
        lambda *a, **k: claim_families,
    )
    monkeypatch.setattr(
        substrate_observer,
        "_refresh_pending_family_snapshots",
        lambda *a, **k: calls.append(k),
    )
    import src.state.db as state_db

    monkeypatch.setattr(state_db, "get_world_connection", lambda: _FakeConn())
    monkeypatch.setattr(
        state_db, "get_forecasts_connection_read_only", lambda: _FakeConn(), raising=False
    )
    monkeypatch.setattr(substrate_observer, "money_path_substrate_priority_active", lambda: False)
    monkeypatch.setattr(
        "src.data.dual_run_lock.acquire_lock",
        lambda _name: contextlib.nullcontext(True),
    )
    _enable_edli_cfg(monkeypatch, enabled=True)

    substrate_observer._edli_money_path_substrate_priority_cycle()

    assert calls and calls[0]["extra_priority_families"] == claim_families
    assert calls[0]["include_pending_families"] is False


def test_money_path_priority_cycle_resolves_condition_marker_without_pending_backlog(
    monkeypatch,
):
    """Condition-only live markers must not fall back to broad pending backlog.

    The marker condition id is already the money-path scope.  The sidecar may
    resolve it to its family for topology/Gamma lookup, but ordinary pending
    families must stay out of the first service window.
    """

    calls: list[dict] = []
    marker_condition_ids = ["cond-shanghai-31"]

    class _ForecastsConn(_FakeConn):
        def execute(self, sql, params=()):
            if "FROM market_events" in sql and params == tuple(marker_condition_ids):
                class _Cur:
                    def fetchall(self_inner):
                        return [
                            (
                                "cond-shanghai-31",
                                "Shanghai",
                                "2026-06-29",
                                "high",
                            )
                        ]

                return _Cur()
            return super().execute(sql, params)

    monkeypatch.setattr(substrate_observer, "money_path_substrate_priority_active", lambda: True)
    monkeypatch.setattr(
        substrate_observer,
        "money_path_substrate_priority_request",
        lambda: {
            "request_id": "req-condition-only",
            "families": [],
            "condition_ids": marker_condition_ids,
        },
    )
    monkeypatch.setattr(substrate_observer, "money_path_substrate_priority_families", lambda: [])
    monkeypatch.setattr(
        substrate_observer,
        "money_path_substrate_priority_condition_ids",
        lambda: marker_condition_ids,
    )
    monkeypatch.setattr(
        substrate_observer,
        "_claim_order_priority_families_for_refresh",
        lambda *a, **k: [],
    )
    monkeypatch.setattr(
        substrate_observer,
        "_refresh_pending_family_snapshots",
        lambda *a, **k: calls.append(k),
    )
    import src.state.db as state_db

    monkeypatch.setattr(state_db, "get_world_connection", lambda: _FakeConn())
    monkeypatch.setattr(
        state_db, "get_forecasts_connection_read_only", lambda: _ForecastsConn(), raising=False
    )
    monkeypatch.setattr(
        "src.data.dual_run_lock.acquire_lock",
        lambda _name: contextlib.nullcontext(True),
    )
    _enable_edli_cfg(monkeypatch, enabled=True)

    substrate_observer._edli_money_path_substrate_priority_cycle()

    assert calls
    assert calls[0]["extra_priority_families"] == [("Shanghai", "2026-06-29", "high")]
    assert calls[0]["priority_condition_ids"] == marker_condition_ids
    assert calls[0]["include_pending_families"] is False
    assert calls[0]["include_money_risk_families"] is False


def test_money_path_priority_cycle_claim_read_failure_does_not_sweep_backlog(
    monkeypatch,
):
    """A failed claim-order lookahead must not degrade into broad pending refresh."""

    calls: list[dict] = []
    monkeypatch.setattr(substrate_observer, "money_path_substrate_priority_active", lambda: False)
    monkeypatch.setattr(
        substrate_observer,
        "_claim_order_priority_families_for_refresh",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        substrate_observer,
        "_refresh_pending_family_snapshots",
        lambda *a, **k: calls.append(k) or {"status": "no_pending_open_rest_or_held_families"},
    )
    import src.state.db as state_db

    monkeypatch.setattr(state_db, "get_world_connection", lambda: _FakeConn())
    monkeypatch.setattr(
        state_db, "get_forecasts_connection_read_only", lambda: _FakeConn(), raising=False
    )
    monkeypatch.setattr(
        "src.data.dual_run_lock.acquire_lock",
        lambda _name: contextlib.nullcontext(True),
    )
    _enable_edli_cfg(monkeypatch, enabled=True)

    result = substrate_observer._edli_money_path_substrate_priority_cycle()

    assert calls
    assert calls[0]["extra_priority_families"] == []
    assert calls[0]["include_pending_families"] is False
    assert result["claim_order_priority_read_failed"] is True


def test_money_path_priority_cycle_exact_conditions_do_not_displace_claim_family(
    monkeypatch,
):
    """Held/resting books are exact-condition hot scope; entries still get family discovery."""

    calls: list[dict] = []
    claim_families = [("Tokyo", "2026-06-30", "low")]
    condition_families = [("Paris", "2026-06-30", "high")]
    monkeypatch.setattr(substrate_observer, "money_path_substrate_priority_active", lambda: False)
    monkeypatch.setattr(
        substrate_observer,
        "_open_rest_condition_ids_for_refresh",
        lambda *a, **k: ["cond-rest"],
    )
    monkeypatch.setattr(
        substrate_observer,
        "_edli_current_held_position_condition_ids",
        lambda: ["cond-held"],
    )
    monkeypatch.setattr(
        substrate_observer,
        "_condition_priority_families_for_refresh",
        lambda _conn, _condition_ids: condition_families,
    )
    monkeypatch.setattr(
        substrate_observer,
        "_claim_order_priority_families_for_refresh",
        lambda *a, **k: claim_families,
    )
    monkeypatch.setattr(
        substrate_observer,
        "_refresh_pending_family_snapshots",
        lambda *a, **k: calls.append(k) or {"status": "refreshed", "attempted": 1, "inserted": 1},
    )
    import src.state.db as state_db

    monkeypatch.setattr(state_db, "get_world_connection", lambda: _FakeConn())
    monkeypatch.setattr(
        state_db, "get_forecasts_connection_read_only", lambda: _FakeConn(), raising=False
    )
    monkeypatch.setattr(
        state_db, "get_trade_connection_read_only", lambda: _FakeConn(), raising=False
    )
    monkeypatch.setattr(
        "src.data.dual_run_lock.acquire_lock",
        lambda _name: contextlib.nullcontext(True),
    )
    _enable_edli_cfg(monkeypatch, enabled=True)

    substrate_observer._edli_money_path_substrate_priority_cycle()

    assert calls
    assert calls[0]["priority_condition_ids"] == ["cond-rest", "cond-held"]
    assert calls[0]["extra_priority_families"] == condition_families + claim_families
    assert calls[0]["include_pending_families"] is False


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
    monkeypatch.setattr(substrate_observer, "money_path_substrate_priority_active", lambda: False)
    monkeypatch.setattr(
        "src.data.dual_run_lock.acquire_lock",
        lambda _name: contextlib.nullcontext(True),
    )
    _enable_edli_cfg(monkeypatch, enabled=True)

    assert main_module._edli_reactor_active_lock.acquire(blocking=False)
    try:
        substrate_observer._edli_market_substrate_warm_cycle()
    finally:
        main_module._edli_reactor_active_lock.release()

    assert calls == [1]


def test_market_substrate_warm_cycle_defers_nonempty_priority_marker(monkeypatch):
    """The background warm tick must not compete with the dedicated priority lane."""

    calls: list[str] = []
    monkeypatch.setattr(
        substrate_observer,
        "_refresh_pending_family_snapshots",
        lambda *a, **k: calls.append("background"),
    )
    monkeypatch.setattr(
        substrate_observer,
        "_edli_money_path_substrate_priority_cycle",
        lambda: calls.append("priority") or {"status": "refreshed", "inserted": 1},
    )
    monkeypatch.setattr(substrate_observer, "money_path_substrate_priority_active", lambda: True)
    monkeypatch.setattr(
        substrate_observer,
        "money_path_substrate_priority_families",
        lambda: [("Shanghai", "2026-06-28", "high")],
    )
    monkeypatch.setattr(substrate_observer, "money_path_substrate_priority_condition_ids", lambda: [])
    _enable_edli_cfg(monkeypatch, enabled=True)

    result = substrate_observer._edli_market_substrate_warm_cycle()

    assert calls == []
    assert result["status"] == "priority_deferred_to_priority_lane"
    assert result["scheduler_failed"] is False
    assert result["serviced_by"] == "money_path_substrate_priority"


def test_market_substrate_warm_cycle_ignores_empty_priority_marker(monkeypatch):
    """An empty marker must not starve background discovery or mark the scheduler failed."""

    calls: list[dict] = []
    receipts: list[dict] = []
    monkeypatch.setattr(
        substrate_observer,
        "_refresh_pending_family_snapshots",
        lambda *a, **k: calls.append(k) or {"status": "no_work", "attempted": 0, "inserted": 0},
    )
    monkeypatch.setattr(substrate_observer, "money_path_substrate_priority_active", lambda: True)
    monkeypatch.setattr(
        substrate_observer,
        "money_path_substrate_priority_request",
        lambda: {"request_id": "empty-req", "families": [], "condition_ids": []},
    )
    monkeypatch.setattr(substrate_observer, "money_path_substrate_priority_families", lambda: [])
    monkeypatch.setattr(substrate_observer, "money_path_substrate_priority_condition_ids", lambda: [])
    monkeypatch.setattr(
        substrate_observer,
        "record_money_path_substrate_priority_receipt",
        lambda **kwargs: receipts.append(kwargs),
    )
    import src.state.db as state_db

    monkeypatch.setattr(state_db, "get_world_connection", lambda: _FakeConn())
    monkeypatch.setattr(
        state_db, "get_forecasts_connection_read_only", lambda: _FakeConn(), raising=False
    )
    monkeypatch.setattr(
        "src.data.dual_run_lock.acquire_lock",
        lambda _name: contextlib.nullcontext(True),
    )
    _enable_edli_cfg(monkeypatch, enabled=True)

    result = substrate_observer._edli_market_substrate_warm_cycle()

    assert calls
    assert calls[0]["include_pending_families"] is True
    assert calls[0]["include_money_risk_families"] is False
    assert result["scheduler_failed"] is False
    assert receipts == []


def test_money_path_priority_cycle_records_empty_scope_as_noop(monkeypatch):
    """An empty priority marker is observable, but it is not a scheduler failure."""

    calls: list[int] = []
    receipts: list[dict] = []
    monkeypatch.setattr(
        substrate_observer,
        "_refresh_pending_family_snapshots",
        lambda *a, **k: calls.append(1),
    )
    monkeypatch.setattr(substrate_observer, "money_path_substrate_priority_active", lambda: True)
    monkeypatch.setattr(
        substrate_observer,
        "money_path_substrate_priority_request",
        lambda: {"request_id": "empty-req", "families": [], "condition_ids": []},
    )
    monkeypatch.setattr(substrate_observer, "money_path_substrate_priority_families", lambda: [])
    monkeypatch.setattr(substrate_observer, "money_path_substrate_priority_condition_ids", lambda: [])
    monkeypatch.setattr(
        substrate_observer,
        "record_money_path_substrate_priority_receipt",
        lambda **kwargs: receipts.append(kwargs),
    )
    _enable_edli_cfg(monkeypatch, enabled=True)

    result = substrate_observer._edli_money_path_substrate_priority_cycle()

    assert calls == []
    assert result["status"] == "priority_request_empty_scope"
    assert result["scheduler_failed"] is False
    assert receipts
    assert receipts[0]["summary"]["status"] == "priority_request_empty_scope"
    assert receipts[0]["summary"]["scheduler_failed"] is False


def test_priority_conditions_deferred_when_refresh_inserted_substrate():
    """Stale exact priority conditions are failed if the sidecar refreshes other books."""

    result = substrate_observer._substrate_warm_business_summary(
        {
            "status": "refreshed",
            "attempted": 12,
            "inserted": 8,
            "failed": 0,
            "direct_clob_prefetch_selected_priority_condition_count": 0,
            "stale_condition_submitted": 2,
        },
        priority_request={
            "request_id": "req-priority-budget",
            "families": [("Munich", "2026-06-30", "high")],
            "condition_ids": ["cond-munich-29"],
        },
        priority_marker_active=True,
    )

    assert result["scheduler_failed"] is True
    assert result["scheduler_failure_reason"] == "priority_conditions_not_serviced"
    assert result["priority_marker_condition_ids"] == 1


def test_money_path_priority_cycle_services_blocked_family_priority_marker(monkeypatch):
    """A concrete reactor-blocked condition marker owns the first service window."""

    calls: list[dict] = []
    marker_families = [("Shanghai", "2026-06-28", "high")]
    marker_condition_ids = ["cond-shanghai-31"]
    claim_families = [("Tokyo", "2026-06-28", "low")]
    monkeypatch.setattr(substrate_observer, "money_path_substrate_priority_active", lambda: True)
    monkeypatch.setattr(
        substrate_observer,
        "money_path_substrate_priority_request",
        lambda: {
            "request_id": "req-1",
            "families": marker_families,
            "condition_ids": marker_condition_ids,
        },
    )
    monkeypatch.setattr(
        substrate_observer,
        "money_path_substrate_priority_families",
        lambda: marker_families,
    )
    monkeypatch.setattr(
        substrate_observer,
        "money_path_substrate_priority_condition_ids",
        lambda: marker_condition_ids,
    )
    monkeypatch.setattr(
        substrate_observer,
        "_claim_order_priority_families_for_refresh",
        lambda *a, **k: claim_families,
    )
    monkeypatch.setattr(
        substrate_observer,
        "_refresh_pending_family_snapshots",
        lambda *a, **k: calls.append(k),
    )
    receipts: list[dict] = []
    monkeypatch.setattr(
        substrate_observer,
        "record_money_path_substrate_priority_receipt",
        lambda **kwargs: receipts.append(kwargs),
    )
    import src.state.db as state_db

    monkeypatch.setattr(state_db, "get_world_connection", lambda: _FakeConn())
    monkeypatch.setattr(
        state_db, "get_forecasts_connection_read_only", lambda: _FakeConn(), raising=False
    )
    monkeypatch.setattr(
        "src.data.dual_run_lock.acquire_lock",
        lambda _name: contextlib.nullcontext(True),
    )
    _enable_edli_cfg(monkeypatch, enabled=True)

    substrate_observer._edli_money_path_substrate_priority_cycle()

    assert calls
    assert calls[0]["extra_priority_families"] == [
        ("Shanghai", "2026-06-28", "high"),
    ]
    assert calls[0]["priority_condition_ids"] == marker_condition_ids
    assert calls[0]["include_pending_families"] is False
    assert calls[0]["include_money_risk_families"] is False
    assert receipts and receipts[0]["request"]["request_id"] == "req-1"


def test_money_path_priority_cycle_marker_family_does_not_starve_claim_order(monkeypatch):
    """A live marker cannot hide claim-order Day0/redecision work."""

    calls: list[dict] = []
    marker_families = [("Shanghai", "2026-06-28", "high")]
    claim_families = [("Tokyo", "2026-06-28", "low")]
    monkeypatch.setattr(substrate_observer, "money_path_substrate_priority_active", lambda: True)
    monkeypatch.setattr(
        substrate_observer,
        "money_path_substrate_priority_request",
        lambda: {
            "request_id": "req-family-only",
            "families": marker_families,
            "condition_ids": [],
        },
    )
    monkeypatch.setattr(
        substrate_observer,
        "money_path_substrate_priority_families",
        lambda: marker_families,
    )
    monkeypatch.setattr(substrate_observer, "money_path_substrate_priority_condition_ids", lambda: [])
    monkeypatch.setattr(
        substrate_observer,
        "_claim_order_priority_families_for_refresh",
        lambda *a, **k: claim_families,
    )
    monkeypatch.setattr(
        substrate_observer,
        "_refresh_pending_family_snapshots",
        lambda *a, **k: calls.append(k),
    )
    import src.state.db as state_db

    monkeypatch.setattr(state_db, "get_world_connection", lambda: _FakeConn())
    monkeypatch.setattr(
        state_db, "get_forecasts_connection_read_only", lambda: _FakeConn(), raising=False
    )
    monkeypatch.setattr(
        "src.data.dual_run_lock.acquire_lock",
        lambda _name: contextlib.nullcontext(True),
    )
    _enable_edli_cfg(monkeypatch, enabled=True)

    substrate_observer._edli_money_path_substrate_priority_cycle()

    assert calls
    assert calls[0]["extra_priority_families"] == claim_families + marker_families
    assert calls[0]["include_pending_families"] is False


def test_priority_family_expands_to_condition_priority_for_captured_books(monkeypatch):
    """A priority family must become concrete condition priority, not just a label sort."""

    family = ("Shanghai", "2026-06-29", "high")
    world_conn = _pending_family_conn("event-1", *family)
    forecasts_conn = _FakeConn()
    write_conn = _FakeConn()
    topology_rows = [
        {
            "market_slug": "highest-temperature-in-shanghai-on-june-29-2026",
            "city": "Shanghai",
            "target_date": "2026-06-29",
            "temperature_metric": "high",
            "condition_id": "cond-30",
            "token_id": "yes-30",
            "range_label": "30C",
        },
        {
            "market_slug": "highest-temperature-in-shanghai-on-june-29-2026",
            "city": "Shanghai",
            "target_date": "2026-06-29",
            "temperature_metric": "high",
            "condition_id": "cond-31",
            "token_id": "yes-31",
            "range_label": "31C",
        },
    ]
    cached_market = {
        "slug": "highest-temperature-in-shanghai-on-june-29-2026",
        "city": SimpleNamespace(name="Shanghai"),
        "target_date": "2026-06-29",
        "temperature_metric": "high",
        "outcomes": [
            {
                "condition_id": "cond-30",
                "market_id": "cond-30",
                "token_id": "yes-30",
                "no_token_id": "no-30",
                "question_id": "q-30",
            },
            {
                "condition_id": "cond-31",
                "market_id": "cond-31",
                "token_id": "yes-31",
                "no_token_id": "no-31",
                "question_id": "q-31",
            },
        ],
    }

    import src.data.market_topology_rows as topology_rows_module
    import src.data.polymarket_client as polymarket_client
    import src.state.db as state_db

    monkeypatch.setattr(topology_rows_module, "_event_family_market_topology_rows", lambda *a, **k: topology_rows)
    monkeypatch.setattr(
        market_scanner,
        "reconstruct_weather_market_from_static_topology",
        lambda *a, **k: cached_market,
    )
    monkeypatch.setattr(substrate_observer, "_edli_current_held_position_family_keys", lambda: set())
    monkeypatch.setattr(state_db, "get_trade_connection", lambda **k: write_conn)
    monkeypatch.setattr(state_db, "get_trade_connection_read_only", lambda **k: _FakeConn())
    monkeypatch.setattr(polymarket_client, "PolymarketClient", _FakePolymarketClient)

    captured_kwargs: list[dict] = []

    def _refresh(_conn, *, markets, **kwargs):
        captured_kwargs.append(kwargs)
        return {
            "attempted": 4,
            "inserted": 4,
            "direct_clob_prefetch_selected_priority_condition_count": len(
                kwargs.get("priority_condition_ids") or []
            ),
        }

    monkeypatch.setattr(market_scanner, "refresh_executable_market_substrate_snapshots", _refresh)

    result = substrate_observer._refresh_pending_family_snapshots(
        world_conn,
        forecasts_conn,
        now_utc=datetime(2026, 6, 29, 0, 0, tzinfo=timezone.utc),
        extra_priority_families=[family],
        include_pending_families=False,
    )

    assert result["status"] == "refreshed"
    assert result["priority_condition_ids_requested"] == 2
    assert set(captured_kwargs[0]["priority_condition_ids"]) == {"cond-30", "cond-31"}


def test_priority_condition_scope_does_not_expand_to_family_siblings(monkeypatch):
    """Concrete money-path condition scope must not become a whole-family refresh.

    The reactor marker already knows which condition blocked submit. Expanding
    that into every sibling in every priority family makes the live sidecar spend
    the orderbook window on unrelated bins before the blocked condition is fresh.
    """

    family = ("Shanghai", "2026-06-29", "high")
    world_conn = _pending_family_conn("event-1", *family)
    forecasts_conn = _FakeConn()
    write_conn = _FakeConn()
    topology_rows = [
        {
            "market_slug": "highest-temperature-in-shanghai-on-june-29-2026",
            "city": "Shanghai",
            "target_date": "2026-06-29",
            "temperature_metric": "high",
            "condition_id": "cond-30",
            "token_id": "yes-30",
            "range_label": "30C",
        },
        {
            "market_slug": "highest-temperature-in-shanghai-on-june-29-2026",
            "city": "Shanghai",
            "target_date": "2026-06-29",
            "temperature_metric": "high",
            "condition_id": "cond-31",
            "token_id": "yes-31",
            "range_label": "31C",
        },
    ]
    cached_market = {
        "slug": "highest-temperature-in-shanghai-on-june-29-2026",
        "city": SimpleNamespace(name="Shanghai"),
        "target_date": "2026-06-29",
        "temperature_metric": "high",
        "condition_ids": ["cond-30", "cond-31"],
        "outcomes": [
            {
                "condition_id": "cond-30",
                "market_id": "cond-30",
                "token_id": "yes-30",
                "no_token_id": "no-30",
                "question_id": "q-30",
            },
            {
                "condition_id": "cond-31",
                "market_id": "cond-31",
                "token_id": "yes-31",
                "no_token_id": "no-31",
                "question_id": "q-31",
            },
        ],
    }

    import src.data.market_topology_rows as topology_rows_module
    import src.data.polymarket_client as polymarket_client
    import src.state.db as state_db

    monkeypatch.setattr(topology_rows_module, "_event_family_market_topology_rows", lambda *a, **k: topology_rows)
    monkeypatch.setattr(
        market_scanner,
        "reconstruct_weather_market_from_static_topology",
        lambda *a, **k: cached_market,
    )
    monkeypatch.setattr(substrate_observer, "_edli_current_held_position_family_keys", lambda: set())
    monkeypatch.setattr(state_db, "get_trade_connection", lambda **k: write_conn)
    monkeypatch.setattr(state_db, "get_trade_connection_read_only", lambda **k: _FakeConn())
    monkeypatch.setattr(polymarket_client, "PolymarketClient", _FakePolymarketClient)

    captured_markets: list[list[dict]] = []
    captured_kwargs: list[dict] = []

    def _refresh(_conn, *, markets, **kwargs):
        captured_markets.append(markets)
        captured_kwargs.append(kwargs)
        return {
            "attempted": 2,
            "inserted": 2,
            "direct_clob_prefetch_selected_priority_condition_count": len(
                kwargs.get("priority_condition_ids") or []
            ),
        }

    monkeypatch.setattr(market_scanner, "refresh_executable_market_substrate_snapshots", _refresh)

    result = substrate_observer._refresh_pending_family_snapshots(
        world_conn,
        forecasts_conn,
        now_utc=datetime(2026, 6, 29, 0, 0, tzinfo=timezone.utc),
        extra_priority_families=[family],
        include_pending_families=False,
        priority_condition_ids=["cond-31"],
    )

    assert result["status"] == "refreshed"
    assert result["priority_condition_ids_requested"] == 1
    assert captured_kwargs[0]["priority_condition_ids"] == {"cond-31"}
    assert [out["condition_id"] for out in captured_markets[0][0]["outcomes"]] == ["cond-31"]
    assert captured_markets[0][0]["condition_ids"] == ["cond-31"]


def test_scoped_priority_condition_prune_preserves_unscoped_claim_family(monkeypatch):
    """Exact monitor/rest scope must not erase unrelated full-family entry discovery."""

    monkeypatch.setattr(substrate_observer, "_condition_buy_sides_fresh", lambda *a, **k: False)
    markets = [
        {
            "slug": "highest-temperature-in-shanghai-on-june-29-2026",
            "condition_ids": ["cond-30", "cond-31"],
            "outcomes": [
                {"condition_id": "cond-30", "token_id": "yes-30"},
                {"condition_id": "cond-31", "token_id": "yes-31"},
            ],
        },
        {
            "slug": "lowest-temperature-in-tokyo-on-june-30-2026",
            "condition_ids": ["cond-21", "cond-22"],
            "outcomes": [
                {"condition_id": "cond-21", "token_id": "yes-21"},
                {"condition_id": "cond-22", "token_id": "yes-22"},
            ],
        },
    ]

    pruned, fresh_skipped, stale_submitted = (
        substrate_observer._prune_fresh_market_outcomes_for_snapshot_refresh(
            _FakeConn(),
            markets,
            fresh_at_iso="2026-06-29T00:00:00+00:00",
            restrict_to_condition_ids={"cond-31"},
        )
    )

    assert fresh_skipped == 0
    assert stale_submitted == 3
    assert [out["condition_id"] for out in pruned[0]["outcomes"]] == ["cond-31"]
    assert [out["condition_id"] for out in pruned[1]["outcomes"]] == ["cond-21", "cond-22"]


def test_substrate_daemon_scheduler_health_uses_business_result(monkeypatch):
    """Scheduler OK must mean the producer made a usable business tick."""

    import src.ingest.substrate_observer_daemon as daemon
    import src.observability.scheduler_health as scheduler_health

    writes: list[dict] = []
    monkeypatch.setattr(
        scheduler_health,
        "_write_scheduler_health",
        lambda job_name, **kwargs: writes.append({"job_name": job_name, **kwargs}),
    )

    wrapped = daemon._scheduler_job("edli_market_substrate_warm")(
        lambda: {
            "status": "priority_unserviced_cross_process_lock_busy",
            "scheduler_failed": True,
            "scheduler_failure_reason": "cross-process executable substrate refresh already running",
        }
    )
    result = wrapped()

    assert result["scheduler_failed"] is True
    assert writes == [
        {
            "job_name": "edli_market_substrate_warm",
            "failed": True,
            "reason": "cross-process executable substrate refresh already running",
            "extra": result,
        }
    ]


def test_market_discovery_cycle_defers_to_nonempty_priority_marker(monkeypatch):
    """A nonempty priority marker owns the hot lane; discovery must not take its lock."""

    calls: list[str] = []
    monkeypatch.setattr(substrate_observer, "money_path_substrate_priority_active", lambda: True)
    monkeypatch.setattr(
        substrate_observer,
        "_market_discovery_staleness_window_seconds",
        lambda: 0.0,
    )
    monkeypatch.setattr(substrate_observer, "_market_discovery_last_completed_monotonic", None)
    import src.data.market_scanner as market_scanner
    import src.data.polymarket_client as polymarket_client
    import src.state.db as state_db

    monkeypatch.setattr(
        market_scanner,
        "find_weather_markets",
        lambda **_k: calls.append("find") or [{"condition_id": "cond-1", "outcomes": []}],
    )
    monkeypatch.setattr(
        market_scanner,
        "refresh_executable_market_substrate_snapshots",
        lambda *_a, **_k: calls.append("refresh") or {"attempted": 1, "inserted": 1},
    )

    class _FakeDiscoveryConn:
        def commit(self):
            calls.append("commit")

        def close(self):
            calls.append("close")

    monkeypatch.setattr(polymarket_client, "PolymarketClient", _FakePolymarketClient)
    monkeypatch.setattr(state_db, "get_trade_connection", lambda **_k: _FakeDiscoveryConn())
    monkeypatch.setattr(
        "src.data.dual_run_lock.acquire_lock",
        lambda _name: contextlib.nullcontext(True),
    )

    substrate_observer._market_discovery_cycle()

    assert calls == []
    assert not substrate_observer._market_discovery_lock.locked()


def test_money_path_targeted_refresh_marks_substrate_priority():
    cycle_src = inspect.getsource(main_module._edli_event_reactor_cycle)
    refresh_src = inspect.getsource(main_module._edli_decision_family_snapshot_refresher)
    confirm_src = inspect.getsource(main_module._edli_refresh_continuous_money_path_families)

    assert 'reason="edli_event_reactor_cycle"' not in cycle_src
    assert "clear_money_path_substrate_priority(" not in cycle_src
    assert "mark_money_path_substrate_priority(" in refresh_src
    assert 'reason="decision_triggered_targeted_refresh"' in refresh_src
    assert "families=[family]" in refresh_src
    assert "condition_ids=condition_ids" in refresh_src
    assert "merge_existing=True" in refresh_src
    assert "mark_money_path_substrate_priority(" in confirm_src
    assert 'reason="continuous_redecision_confirm_refresh"' in confirm_src
    assert "families=marker_families" in confirm_src
    assert "condition_ids=priority_conditions" in confirm_src


def test_substrate_priority_clear_is_pid_scoped(tmp_path, monkeypatch):
    import src.config as config
    from src.data.substrate_priority import (
        clear_money_path_substrate_priority,
        mark_money_path_substrate_priority,
        money_path_substrate_priority_active,
        money_path_substrate_priority_condition_ids,
        money_path_substrate_priority_families,
    )

    monkeypatch.setattr(config, "state_path", lambda rel: tmp_path / rel)

    mark_money_path_substrate_priority(
        reason="test",
        ttl_seconds=60.0,
        families=[("Paris", "2026-06-20", "low")],
        condition_ids=["cond-1", "cond-2", "cond-1"],
    )
    assert money_path_substrate_priority_active()
    assert money_path_substrate_priority_families() == [("Paris", "2026-06-20", "low")]
    assert money_path_substrate_priority_condition_ids() == ["cond-1", "cond-2"]

    clear_money_path_substrate_priority(pid=999999)
    assert money_path_substrate_priority_active()

    clear_money_path_substrate_priority()
    assert not money_path_substrate_priority_active()


def test_substrate_priority_marker_replaces_stale_live_scope(tmp_path, monkeypatch):
    import src.config as config
    from src.data.substrate_priority import (
        mark_money_path_substrate_priority,
        money_path_substrate_priority_condition_ids,
        money_path_substrate_priority_families,
    )

    monkeypatch.setattr(config, "state_path", lambda rel: tmp_path / rel)

    mark_money_path_substrate_priority(
        reason="continuous_redecision_confirm_refresh",
        ttl_seconds=60.0,
        families=[("Paris", "2026-06-20", "low")],
        condition_ids=["cond-old"],
    )
    mark_money_path_substrate_priority(
        reason="decision_triggered_targeted_refresh",
        ttl_seconds=60.0,
        families=[("Shanghai", "2026-07-01", "high")],
        condition_ids=["cond-new"],
    )

    assert money_path_substrate_priority_families() == [("Shanghai", "2026-07-01", "high")]
    assert money_path_substrate_priority_condition_ids() == ["cond-new"]


def test_substrate_priority_marker_can_explicitly_merge_same_request_scope(tmp_path, monkeypatch):
    import src.config as config
    from src.data.substrate_priority import (
        mark_money_path_substrate_priority,
        money_path_substrate_priority_condition_ids,
        money_path_substrate_priority_families,
    )

    monkeypatch.setattr(config, "state_path", lambda rel: tmp_path / rel)

    mark_money_path_substrate_priority(
        reason="test",
        ttl_seconds=60.0,
        families=[("Paris", "2026-06-20", "low")],
        condition_ids=["cond-1"],
    )
    mark_money_path_substrate_priority(
        reason="test",
        ttl_seconds=60.0,
        families=[("Shanghai", "2026-07-01", "high")],
        condition_ids=["cond-2"],
        merge_existing=True,
    )

    assert money_path_substrate_priority_families() == [
        ("Paris", "2026-06-20", "low"),
        ("Shanghai", "2026-07-01", "high"),
    ]
    assert money_path_substrate_priority_condition_ids() == ["cond-1", "cond-2"]


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


def test_pending_family_refresh_filters_globally_stale_target_dates():
    """A stale target_date must not consume the bounded substrate-refresh budget."""
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

    def insert_event(
        event_id: str,
        city: str,
        target_date: str,
        available_at: str,
        *,
        event_type: str = "FORECAST_SNAPSHOT_READY",
        claimed_at: str | None = None,
    ) -> None:
        payload = {"city": city, "target_date": target_date, "metric": "high"}
        conn.execute(
            """
            INSERT INTO opportunity_events (
                event_id, event_type, entity_key, source, observed_at, available_at,
                received_at, payload_hash, idempotency_key, priority, payload_json,
                schema_version, created_at
            ) VALUES (?, ?, ?, 'test', ?, ?, ?, ?, ?, 50, ?, 1, ?)
            """,
            (
                event_id,
                event_type,
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
                consumer_name, event_id, processing_status, claimed_at, updated_at
            ) VALUES ('edli_reactor_v1', ?, 'pending', ?, ?)
            """,
            (event_id, claimed_at, available_at),
        )

    insert_event(
        "old-a",
        "Amsterdam",
        "2026-06-04",
        "2026-05-30T00:00:00+00:00",
        event_type="EDLI_REDECISION_PENDING",
    )
    insert_event("old-b", "Milan", "2026-06-04", "2026-05-30T00:00:01+00:00")
    insert_event("fresh-a", "Seoul", "2026-06-06", "2026-06-05T00:00:00+00:00")
    insert_event("fresh-b", "Tokyo", "2026-06-06", "2026-06-05T00:00:01+00:00")
    insert_event(
        "still-leased",
        "Toronto",
        "2026-06-06",
        "2026-06-05T00:00:02+00:00",
        claimed_at="2026-06-05T12:05:00+00:00",
    )

    decision_time = datetime(2026, 6, 5, 12, 0, 0, tzinfo=timezone.utc)
    for module in (main_module, substrate_observer):
        capture = _CaptureConn(conn)
        rows = module._pending_family_rows_for_refresh(
            capture,
            consumer_name="edli_reactor_v1",
            now_utc=decision_time,
        )
        families = [(row[0], row[1], row[2]) for row in rows]

        assert families == [("Tokyo", "2026-06-06", "high"), ("Seoul", "2026-06-06", "high")]

        plan = _explain_plan(conn, capture.sql, capture.params)
        assert "USING INDEX idx_opportunity_event_processing_status" in plan
        assert "LIMIT ?" in capture.sql
        assert capture.params == (
            "edli_reactor_v1",
            "2026-06-05T12:00:00+00:00",
            "2026-06-05",
            2000,
        )


def test_pending_family_refresh_filters_city_local_past_frontier_band_dates():
    """Frontier-band rows need city-local expiry, not a raw UTC/global date cut."""

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

    def insert_event(event_id: str, city: str, target_date: str) -> None:
        available_at = "2026-06-29T06:00:00+00:00"
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

    insert_event("tokyo-past-frontier-band", "Tokyo", "2026-06-28")
    insert_event("la-still-local-day", "Los Angeles", "2026-06-28")
    insert_event("tokyo-current", "Tokyo", "2026-06-29")

    # At 2026-06-29 06:30Z, Tokyo's 2026-06-28 local day is over. Los Angeles is
    # still 2026-06-28 23:30 PDT, so that row must stay refreshable.
    decision_time = datetime(2026, 6, 29, 6, 30, 0, tzinfo=timezone.utc)
    for module in (main_module, substrate_observer):
        rows = module._pending_family_rows_for_refresh(
            conn,
            consumer_name="edli_reactor_v1",
            now_utc=decision_time,
        )

        assert {(row[0], row[1], row[2]) for row in rows} == {
            ("Los Angeles", "2026-06-28", "high"),
            ("Tokyo", "2026-06-29", "high"),
        }


def test_open_rest_priority_requires_live_command_and_remaining_rest():
    """Cancelled partial fills are historical fills, not live rests to reprice."""

    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE venue_commands (
            command_id TEXT PRIMARY KEY,
            position_id TEXT,
            venue_order_id TEXT,
            token_id TEXT,
            snapshot_id TEXT,
            intent_kind TEXT,
            state TEXT
        );
        CREATE TABLE venue_order_facts (
            venue_order_id TEXT,
            command_id TEXT,
            state TEXT,
            remaining_size TEXT,
            matched_size TEXT,
            local_sequence INTEGER
        );
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            phase TEXT
        );
        """
    )

    rows = [
        (
            "cancelled-partial",
            "old-pos",
            "old-order",
            "CANCELLED",
            "PARTIALLY_MATCHED",
            "6286.757",
            "quarantined",
            "Hong Kong",
            "2026-06-22",
            "high",
        ),
        (
            "acked-partial",
            "live-pos",
            "live-order",
            "ACKED",
            "PARTIALLY_MATCHED",
            "11.25",
            "pending_entry",
            "Singapore",
            "2026-06-26",
            "high",
        ),
        (
            "acked-zero",
            "zero-pos",
            "zero-order",
            "ACKED",
            "PARTIALLY_MATCHED",
            "0",
            "pending_entry",
            "Tokyo",
            "2026-06-26",
            "high",
        ),
    ]
    for (
        command_id,
        position_id,
        order_id,
        command_state,
        fact_state,
        remaining,
        phase,
        city,
        target_date,
        metric,
    ) in rows:
        conn.execute(
            """
            INSERT INTO venue_commands (
                command_id, position_id, venue_order_id, token_id, snapshot_id, intent_kind, state
            ) VALUES (?, ?, ?, ?, ?, 'ENTRY', ?)
            """,
            (
                command_id,
                position_id,
                order_id,
                f"token-{command_id}",
                f"snap-{command_id}",
                command_state,
            ),
        )
        conn.execute(
            """
            INSERT INTO venue_order_facts (
                venue_order_id, command_id, state, remaining_size, matched_size, local_sequence
            ) VALUES (?, ?, ?, ?, '1', 1)
            """,
            (order_id, command_id, fact_state, remaining),
        )
        conn.execute(
            """
            INSERT INTO position_current (
                position_id, city, target_date, temperature_metric, phase
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (position_id, city, target_date, metric, phase),
        )

    expected = [("Singapore", "2026-06-26", "high")]
    assert main_module._open_rest_family_rows_for_refresh(conn) == expected
    assert substrate_observer._open_rest_family_rows_for_refresh(conn) == expected


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

    result = substrate_observer._refresh_pending_family_snapshots(
        conn,
        _FakeConn(),
        now_utc=datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc),
    )

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
    monkeypatch.setattr(state_db, "get_trade_connection_read_only", lambda: _FakeConn())
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

    result = substrate_observer._refresh_pending_family_snapshots(
        conn,
        _FakeConn(),
        now_utc=datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc),
    )

    assert result["status"] == "refreshed"
    assert result["topology_budget_exhausted"] == 1
    assert result["topology_deferred_families"] > 0
    assert 1 <= len(submitted[0]) < 12
    assert refresh_kwargs[0]["budget_seconds"] == pytest.approx(15.0 - fake_now)


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

    result = substrate_observer._refresh_pending_family_snapshots(
        conn,
        _FakeConn(),
        now_utc=datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc),
    )

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

    result = substrate_observer._refresh_pending_family_snapshots(
        conn,
        _FakeConn(),
        now_utc=datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc),
    )

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


def test_condition_buy_sides_fresh_runtime_paths_use_latest_mirror_without_append_scan():
    class _TracingConn:
        def __init__(self, wrapped):
            self._wrapped = wrapped
            self.latest_queries = 0
            self.append_queries = 0

        def execute(self, sql, params=()):
            text = " ".join(str(sql).split())
            if "FROM executable_market_snapshot_latest" in text:
                self.latest_queries += 1
            if "FROM executable_market_snapshots" in text:
                self.append_queries += 1
            return self._wrapped.execute(sql, params)

        def __getattr__(self, name):
            return getattr(self._wrapped, name)

    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE executable_market_snapshot_latest (
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
    for snapshot_id, selected in (("snap-yes", "yes-1"), ("snap-no", "no-1")):
        conn.execute(
            """
            INSERT INTO executable_market_snapshot_latest (
                snapshot_id, condition_id, yes_token_id, no_token_id,
                selected_outcome_token_id, captured_at, freshness_deadline
            ) VALUES (?, 'cond-1', 'yes-1', 'no-1', ?,
                      '2026-06-06T00:00:00+00:00', '2026-06-06T00:01:00+00:00')
            """,
            (snapshot_id, selected),
        )
    conn.executemany(
        """
        INSERT INTO executable_market_snapshots (
            snapshot_id, condition_id, yes_token_id, no_token_id,
            selected_outcome_token_id, captured_at, freshness_deadline
        ) VALUES (?, 'cond-1', 'yes-1', 'no-1', ?,
                  '2026-06-05T00:00:00+00:00', '2026-06-06T00:01:00+00:00')
        """,
        [(f"old-{idx}", "yes-1" if idx % 2 == 0 else "no-1") for idx in range(20)],
    )

    substrate_tracing = _TracingConn(conn)

    assert substrate_observer._condition_buy_sides_fresh(
        substrate_tracing,
        "cond-1",
        "2026-06-06T00:00:30+00:00",
    )
    assert substrate_tracing.latest_queries == 1
    assert substrate_tracing.append_queries == 0

    main_tracing = _TracingConn(conn)
    assert main_module._condition_buy_sides_fresh(
        main_tracing,
        "cond-1",
        "2026-06-06T00:00:30+00:00",
    )
    assert main_tracing.latest_queries == 1
    assert main_tracing.append_queries == 0


def test_condition_buy_sides_fresh_excludes_market_channel_invalidated_latest_rows():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE executable_market_snapshot_latest (
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
        CREATE TABLE executable_market_snapshot_invalidations (
            invalidation_id TEXT,
            condition_id TEXT,
            token_id TEXT,
            reason TEXT,
            invalidated_at TEXT,
            created_at TEXT
        )
        """
    )
    for snapshot_id, selected in (("snap-yes", "yes-1"), ("snap-no", "no-1")):
        conn.execute(
            """
            INSERT INTO executable_market_snapshot_latest (
                snapshot_id, condition_id, yes_token_id, no_token_id,
                selected_outcome_token_id, captured_at, freshness_deadline
            ) VALUES (?, 'cond-1', 'yes-1', 'no-1', ?,
                      '2026-06-06T00:00:00+00:00', '2026-06-06T00:05:00+00:00')
            """,
            (snapshot_id, selected),
        )
    conn.execute(
        """
        INSERT INTO executable_market_snapshot_invalidations
        VALUES ('inv-1', 'cond-1', NULL, 'tick_size_change',
                '2026-06-06T00:01:00+00:00', '2026-06-06T00:01:00+00:00')
        """
    )

    assert not substrate_observer._condition_buy_sides_fresh(
        conn,
        "cond-1",
        "2026-06-06T00:02:00+00:00",
    )
    assert not main_module._condition_buy_sides_fresh(
        conn,
        "cond-1",
        "2026-06-06T00:02:00+00:00",
    )


def test_held_condition_scope_does_not_treat_gamma_active_as_tradeability():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE executable_market_snapshots (
            snapshot_id TEXT,
            condition_id TEXT,
            captured_at TEXT,
            active INTEGER,
            closed INTEGER,
            enable_orderbook INTEGER,
            accepting_orders INTEGER
        )
        """
    )
    conn.execute(
        """
        INSERT INTO executable_market_snapshots
        VALUES ('snap-1', 'cond-1', '2026-06-06T00:00:00+00:00', 0, 0, 1, 1)
        """
    )

    assert main_module._edli_condition_latest_snapshot_executable(conn, "cond-1")


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
    monkeypatch.setattr(state_db, "get_trade_connection_read_only", lambda: _FakeConn())
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

    result = substrate_observer._refresh_pending_family_snapshots(
        world_conn,
        forecasts_conn,
        now_utc=datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc),
    )

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

    result = substrate_observer._refresh_pending_family_snapshots(
        world_conn,
        forecasts_conn,
        now_utc=datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc),
    )

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

    result = substrate_observer._refresh_pending_family_snapshots(
        world_conn,
        forecasts_conn,
        now_utc=datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc),
    )

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


def _venue_close_relationship_harness(monkeypatch, *, refresh_module=substrate_observer):
    """Wire a single Hong Kong / 2026-06-07 pending family through the warm
    refresh with all venue-I/O mocked. Returns a callable
    ``run(now_utc) -> (result, submitted)`` so a single fixture can be driven at
    multiple decision clocks."""
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
    import src.data.market_topology_rows as topology_rows_module
    import src.state.db as state_db

    monkeypatch.setattr(
        topology_rows_module, "_event_family_market_topology_rows", lambda *a, **k: topology_rows
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


def test_warm_lane_keeps_family_active_after_gamma_enddate_while_local_day_open(monkeypatch):
    """RELATIONSHIP: static Gamma endDate/F1 timing is not warm-lane close proof."""
    run = _venue_close_relationship_harness(monkeypatch)

    # Local-day active before 12:00Z → refreshed.
    open_now = datetime(2026, 6, 7, 6, 0, tzinfo=timezone.utc)
    open_result, open_submitted = run(open_now)
    assert open_result["status"] == "refreshed"
    assert open_result["venue_closed_skipped"] == 0
    assert open_result["cached_topology_families"] >= 1
    assert len(open_submitted) == 1

    # After Gamma endDate but before Hong Kong local midnight → still refreshed.
    after_enddate_now = datetime(2026, 6, 7, 14, 0, tzinfo=timezone.utc)
    after_enddate_result, after_enddate_submitted = run(after_enddate_now)
    assert after_enddate_result["status"] == "refreshed"
    assert after_enddate_result["venue_closed_skipped"] == 0
    assert after_enddate_result["cached_topology_families"] == open_result["cached_topology_families"]
    assert len(after_enddate_submitted) == 1


def test_lifted_substrate_warm_lane_keeps_after_gamma_enddate_family(monkeypatch):
    """The sidecar-owned lifted warmer must carry the same static-endDate rule as
    ``src.main``."""
    run = _venue_close_relationship_harness(
        monkeypatch, refresh_module=substrate_observer
    )

    after_enddate_now = datetime(2026, 6, 7, 14, 0, tzinfo=timezone.utc)
    closed_result, closed_submitted = run(after_enddate_now)

    assert closed_result["venue_closed_skipped"] == 0
    assert closed_result.get("cached_topology_families", 0) >= 1
    assert len(closed_submitted) == 1
    assert closed_result["status"] == "refreshed"


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


def test_lifted_substrate_warm_lane_records_market_end_elapsed_evidence(monkeypatch):
    """When every candidate is rejected because the venue end-at elapsed, the
    sidecar must emit durable family evidence so the reactor can terminalize
    stale snapshot events instead of retrying a non-executable family forever."""
    forecasts_conn = _FakeConn()
    write_conn = _FakeConn()
    topology_rows = [
        {
            "market_slug": "lowest-temperature-in-hong-kong-on-june-7-2026",
            "city": "Hong Kong",
            "target_date": "2026-06-07",
            "temperature_metric": "low",
            "condition_id": "cond-1",
            "token_id": "yes-1",
            "range_label": "21C",
        }
    ]
    cached_market = {
        "slug": "lowest-temperature-in-hong-kong-on-june-7-2026",
        "city": SimpleNamespace(name="Hong Kong"),
        "target_date": "2026-06-07",
        "temperature_metric": "low",
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

    import src.data.market_absence_evidence as market_absence_evidence
    import src.data.market_scanner as scanner
    import src.data.market_topology_rows as market_topology_rows
    import src.data.polymarket_client as polymarket_client
    import src.state.db as state_db

    recorded: list[dict] = []

    monkeypatch.setenv("ZEUS_REACTOR_MARKET_UNAVAILABLE_EVIDENCE_SECONDS", "1800")
    monkeypatch.setattr(
        market_topology_rows, "_event_family_market_topology_rows", lambda *a, **k: topology_rows
    )
    monkeypatch.setattr(state_db, "get_trade_connection", lambda **k: write_conn)
    monkeypatch.setattr(state_db, "get_trade_connection_read_only", lambda **k: _FakeConn())
    monkeypatch.setattr(
        scanner,
        "reconstruct_weather_market_from_static_topology",
        lambda *a, **k: cached_market,
    )
    monkeypatch.setattr(scanner, "_gamma_get", lambda *a, **k: None)
    monkeypatch.setattr(scanner, "_parse_and_persist_weather_events", lambda *a, **k: [])
    monkeypatch.setattr(polymarket_client, "PolymarketClient", _FakePolymarketClient)
    monkeypatch.setattr(
        market_absence_evidence,
        "record_market_unavailable_families",
        lambda *args, **kwargs: recorded.append({"args": args, "kwargs": kwargs}),
    )

    def _refresh(_conn, *, markets, **_kwargs):
        assert markets == [cached_market]
        return {
            "executable_substrate_coverage_status": "NO_EXECUTABLE_CANDIDATES",
            "executable_snapshot_candidate_count": 0,
            "executable_snapshot_candidate_rejection_counts": {
                "market_end_at_elapsed": 1,
            },
            "attempted": 0,
            "inserted": 0,
        }

    monkeypatch.setattr(scanner, "refresh_executable_market_substrate_snapshots", _refresh)

    world_conn = _pending_family_conn("event-1", "Hong Kong", "2026-06-07", "low")
    result = substrate_observer._refresh_pending_family_snapshots(
        world_conn,
        forecasts_conn,
        now_utc=datetime(2026, 6, 8, 18, 0, tzinfo=timezone.utc),
        extra_priority_families=[("Hong Kong", "2026-06-07", "low")],
        include_pending_families=False,
    )

    assert result["status"] == "refreshed"
    assert result["market_unavailable_evidence_source"] == "market_end_at_elapsed"
    assert result["market_unavailable_families_recorded"] == 1
    assert recorded == [
        {
            "args": ([("hong kong", "2026-06-07", "low")],),
            "kwargs": {
                "ttl_seconds": 1800.0,
                "observed_at": datetime(2026, 6, 8, 18, 0, tzinfo=timezone.utc),
                "source": "market_end_at_elapsed",
            },
        }
    ]


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
    import src.data.market_topology_rows as market_topology_rows
    import src.state.db as state_db

    submitted: list[list[dict]] = []

    monkeypatch.setattr(
        market_topology_rows, "_event_family_market_topology_rows", lambda *a, **k: topology_rows
    )
    monkeypatch.setattr(state_db, "get_trade_connection", lambda **k: write_conn)
    monkeypatch.setattr(state_db, "get_trade_connection_read_only", lambda: _FakeConn())
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
    result = substrate_observer._refresh_pending_family_snapshots(
        world_conn, forecasts_conn, now_utc=closed_now
    )

    assert result["venue_closed_skipped"] == 0
    assert result["status"] == "refreshed"
    assert len(submitted) == 1

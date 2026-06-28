# Created: 2026-06-16
# Last audited: 2026-06-16
# Authority basis: maker rest cancellation must continue through the same live
#   redecision event type as price and terminal-no-fill management. The caller
#   half harvests confirmed-cancelled rests, recovers each family from venue
#   truth, and emits one EDLI_REDECISION_PENDING per family.
"""Caller-side tests for the escalation re-decision emit.

Covers the two helpers added in src/main.py:
  - _escalation_families_from_cancelled: the venue-truth family recovery.
  - _emit_rest_pull_redecisions: routes the recovered families through the
    standard live redecision lane with redecision_origin='rest_pull'.
"""
from __future__ import annotations

import sqlite3
from types import SimpleNamespace


def _trade_conn_with_snapshot(token_to_cond: dict[str, str]) -> sqlite3.Connection:
    """Minimal executable_market_snapshots with only the columns the recovery reads."""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE executable_market_snapshots (
            snapshot_id TEXT, selected_outcome_token_id TEXT, condition_id TEXT,
            captured_at TEXT)"""
    )
    seq = 0
    for token, cond in token_to_cond.items():
        # Two rows per token (older + newer) so the freshest-by-captured_at pick is exercised.
        conn.execute(
            "INSERT INTO executable_market_snapshots VALUES (?,?,?,?)",
            (f"snap-old-{seq}", token, "STALE_COND", "2026-06-16T00:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO executable_market_snapshots VALUES (?,?,?,?)",
            (f"snap-new-{seq}", token, cond, "2026-06-16T11:00:00+00:00"),
        )
        seq += 1
    return conn


def _forecasts_conn_with_market_events(
    cond_to_family: dict[str, tuple[str, str, str]],
) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE market_events (
            condition_id TEXT, city TEXT, target_date TEXT, temperature_metric TEXT)"""
    )
    for cond, (city, td, metric) in cond_to_family.items():
        conn.execute(
            "INSERT INTO market_events VALUES (?,?,?,?)", (cond, city, td, metric)
        )
    return conn


class _SqlCaptureConn:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self.statements: list[str] = []

    def execute(self, sql, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        self.statements.append(str(sql))
        return self._conn.execute(sql, *args, **kwargs)

    def __getattr__(self, name: str):
        return getattr(self._conn, name)


def test_family_recovery_resolves_city_date_metric_from_venue_truth():
    import src.main as m

    trade = _trade_conn_with_snapshot({"tokA": "condA", "tokB": "condB"})
    forecasts = _forecasts_conn_with_market_events(
        {"condA": ("Moscow", "2026-06-17", "high"),
         "condB": ("Singapore", "2026-06-17", "high")}
    )
    cancelled = [
        {"command_id": "c1", "token_id": "tokA", "market_id": "mA"},
        {"command_id": "c2", "token_id": "tokB", "market_id": "mB"},
    ]
    families = m._escalation_families_from_cancelled(cancelled, trade, forecasts)
    assert families == {
        ("Moscow", "2026-06-17", "high"),
        ("Singapore", "2026-06-17", "high"),
    }


def test_family_recovery_uses_latest_snapshot_mirror_without_append_scan():
    import src.main as m

    trade = _trade_conn_with_snapshot({"tokA": "condA"})
    trade.execute(
        """
        CREATE TABLE executable_market_snapshot_latest (
            selected_outcome_token_id TEXT,
            condition_id TEXT,
            captured_at TEXT
        )
        """
    )
    trade.execute(
        "INSERT INTO executable_market_snapshot_latest VALUES (?,?,?)",
        ("tokA", "condA", "2026-06-16T11:00:00+00:00"),
    )
    forecasts = _forecasts_conn_with_market_events(
        {"condA": ("Moscow", "2026-06-17", "high")}
    )
    captured_trade = _SqlCaptureConn(trade)

    families = m._escalation_families_from_cancelled(
        [{"command_id": "c1", "token_id": "tokA", "market_id": "mA"}],
        captured_trade,
        forecasts,
    )

    assert families == {("Moscow", "2026-06-17", "high")}
    statements = "\n".join(captured_trade.statements)
    assert "FROM executable_market_snapshot_latest" in statements
    assert "FROM executable_market_snapshots" not in statements


def test_family_recovery_skips_unresolvable_entry_without_crashing():
    import src.main as m

    # tokA resolves; tokZ has no snapshot row -> skipped (fail-soft, not a crash).
    trade = _trade_conn_with_snapshot({"tokA": "condA"})
    forecasts = _forecasts_conn_with_market_events(
        {"condA": ("Moscow", "2026-06-17", "high")}
    )
    cancelled = [
        {"command_id": "c1", "token_id": "tokA", "market_id": "mA"},
        {"command_id": "cZ", "token_id": "tokZ", "market_id": "mZ"},
    ]
    families = m._escalation_families_from_cancelled(cancelled, trade, forecasts)
    assert families == {("Moscow", "2026-06-17", "high")}


def test_family_recovery_uses_direct_condition_id_without_snapshot_lookup():
    import src.main as m

    trade = _trade_conn_with_snapshot({})
    forecasts = _forecasts_conn_with_market_events(
        {"condDirect": ("Singapore", "2026-06-27", "high")}
    )
    families = m._escalation_families_from_cancelled(
        [{"command_id": "c1", "condition_id": "condDirect", "token_id": ""}],
        trade,
        forecasts,
    )
    assert families == {("Singapore", "2026-06-27", "high")}


def test_family_recovery_empty_when_no_tokens():
    import src.main as m

    trade = _trade_conn_with_snapshot({})
    forecasts = _forecasts_conn_with_market_events({})
    assert m._escalation_families_from_cancelled([], trade, forecasts) == set()
    assert (
        m._escalation_families_from_cancelled(
            [{"command_id": "c1", "token_id": ""}], trade, forecasts
        )
        == set()
    )


def _trade_conn_with_rest_screen_rows(*, command_state: str) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE venue_commands (
            command_id TEXT, venue_order_id TEXT, token_id TEXT, market_id TEXT,
            side TEXT, price REAL, snapshot_id TEXT, created_at TEXT,
            intent_kind TEXT, state TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE venue_order_facts (
            venue_order_id TEXT, state TEXT, matched_size TEXT, local_sequence INTEGER
        )"""
    )
    conn.execute(
        """CREATE TABLE executable_market_snapshots (
            selected_outcome_token_id TEXT, condition_id TEXT,
            yes_token_id TEXT, no_token_id TEXT, captured_at TEXT
        )"""
    )
    conn.execute(
        """INSERT INTO venue_commands
           VALUES ('cmd-rest', 'ord-rest', 'tok-no', 'market-1', 'BUY', 0.66,
                   'snap-rest', '2026-06-16T11:00:00+00:00', 'ENTRY', ?)""",
        (command_state,),
    )
    conn.execute(
        "INSERT INTO venue_order_facts VALUES ('ord-rest', 'LIVE', '0', 1)"
    )
    conn.execute(
        """INSERT INTO executable_market_snapshots
           VALUES ('tok-no', 'cond-1', 'tok-yes', 'tok-no',
                   '2026-06-16T11:01:00+00:00')"""
    )
    return conn


def _belief_for_rest_screen() -> SimpleNamespace:
    return SimpleNamespace(
        family_id="hyp|live|Paris|2026-06-21|low|disc",
        condition_ids=["cond-1"],
        bin_labels=["Will the lowest temperature in Paris be 20°C on June 21?"],
        p_posterior_vec=[0.2],
    )


def test_open_rest_screen_ignores_terminal_local_commands_even_if_venue_fact_is_stale_live():
    """A cancelled command with a stale LIVE venue fact is not an open rest.

    Live failure mode 2026-06-20: continuous redecision repeatedly tried to
    pull a command already marked CANCELLED locally because the screen read only
    the latest venue fact. The cancel path then skipped the terminal command,
    creating an infinite false rest-pull loop instead of a clean reprice flow.
    """

    import src.main as m

    trade = _trade_conn_with_rest_screen_rows(command_state="CANCELLED")
    world = sqlite3.connect(":memory:")

    assert m._edli_open_maker_rests_for_screen(
        trade,
        world,
        beliefs=[_belief_for_rest_screen()],
    ) == []


def test_open_rest_screen_keeps_active_local_commands_with_open_venue_fact():
    import src.main as m

    trade = _trade_conn_with_rest_screen_rows(command_state="ACKED")
    world = sqlite3.connect(":memory:")

    rests = m._edli_open_maker_rests_for_screen(
        trade,
        world,
        beliefs=[_belief_for_rest_screen()],
    )

    assert len(rests) == 1
    assert rests[0].command_id == "cmd-rest"
    assert rests[0].side == "buy_no"


def test_emit_routes_through_standard_live_redecision(monkeypatch):
    """The emit must build FSR-shaped payloads as standard live redecisions:
      - event_type='EDLI_REDECISION_PENDING'
      - source uses the normal cycle-* redecision source
      - restrict_to_families == the recovered set
      - payload origin is rest_pull, not a source-prefix scheduling authority.
    """
    import src.main as m
    from src.events.opportunity_event import make_opportunity_event

    captured: dict = {}
    written_payloads: list[dict] = []

    class _FakeTrigger:
        def __init__(self, *a, **k):
            pass

        def build_committed_snapshot_events(self, **kwargs):
            captured.update(kwargs)
            return [
                make_opportunity_event(
                    event_type=kwargs["event_type"],
                    entity_key="Moscow|2026-06-17|high|snap-1",
                    source=kwargs["source"],
                    observed_at="2026-06-16T11:00:00+00:00",
                    available_at="2026-06-16T11:00:00+00:00",
                    received_at="2026-06-16T12:00:00+00:00",
                    causal_snapshot_id="snap-1",
                    payload={
                        "city": "Moscow",
                        "target_date": "2026-06-17",
                        "metric": "high",
                        "snapshot_id": "snap-1",
                    },
                    priority=50,
                )
            ]

    class _FakeWriter:
        def __init__(self, _conn):
            pass

        def write_many(self, events):
            import json

            for event in events:
                written_payloads.append(json.loads(event.payload_json))
            return [SimpleNamespace(inserted=True) for _ in events]

    # Stub the heavy collaborators so the test stays in-memory and offline.
    import src.events.triggers.forecast_snapshot_ready as fsr_mod

    monkeypatch.setattr(fsr_mod, "ForecastSnapshotReadyTrigger", _FakeTrigger)
    monkeypatch.setattr(
        fsr_mod, "executable_forecast_live_eligible_reader", lambda conn: (lambda *a, **k: True)
    )
    import src.events.event_writer as writer_mod

    monkeypatch.setattr(writer_mod, "EventWriter", _FakeWriter)

    class _NoopConn:
        def execute(self, *a, **k):
            return self

        def fetchall(self):
            return []

        def commit(self):
            pass

        def close(self):
            pass

    class _NoopMutex:
        def acquire(self):
            pass

        def release(self):
            pass

    import src.state.db as dbmod

    monkeypatch.setattr(dbmod, "get_world_connection", lambda *a, **k: _NoopConn())
    monkeypatch.setattr(
        dbmod, "get_forecasts_connection_read_only", lambda *a, **k: _NoopConn()
    )
    monkeypatch.setattr(dbmod, "world_write_mutex", lambda: _NoopMutex())

    from datetime import datetime, timezone

    m._set_edli_redecision_boot_token("TOK")
    m._reset_edli_redecision_cycle_index()
    families = {("Moscow", "2026-06-17", "high")}
    now = datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc)
    n = m._emit_rest_pull_redecisions(
        families, decision_time=now, received_at=now.isoformat()
    )
    assert n == 1
    assert captured["event_type"] == "EDLI_REDECISION_PENDING"
    assert captured["source"].startswith("cycle-TOK-")
    assert captured["restrict_to_families"] == families
    assert written_payloads == [
        {
            "city": "Moscow",
            "target_date": "2026-06-17",
            "metric": "high",
            "snapshot_id": "snap-1",
            "redecision_origin": "rest_pull",
        }
    ]


def test_emit_noop_on_empty_families(monkeypatch):
    import src.main as m
    from datetime import datetime, timezone

    now = datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc)
    # Must short-circuit BEFORE touching any DB/mutex.
    assert m._emit_rest_pull_redecisions(
        set(), decision_time=now, received_at=now.isoformat()
    ) == 0

# Created: 2026-06-14
# Last reused or audited: 2026-06-19
# Authority basis: freshness-throughput starvation fix (#92,
#   docs/evidence/deadloop_2026-06-14/binding_wall.md). The decision-triggered
#   family snapshot refresher's reconstruct returned None for EVERY family because
#   the topology rows omitted the family-identity columns.
"""RED-on-revert regression test: the decision-triggered family snapshot refresher
MUST re-inject city/target_date/temperature_metric into the topology rows before
calling ``reconstruct_weather_market_from_static_topology``.

Root cause (2026-06-12 - 2026-06-14, ``decision_triggered_targeted_refresh``
marker at ZERO; processed approx 0):
  ``_event_family_market_topology_rows`` binds city/target_date/temperature_metric
  in its WHERE clause but does NOT SELECT them, so the returned rows carry NO
  ``city`` / ``target_date`` / ``temperature_metric`` columns.
  ``reconstruct_weather_market_from_static_topology`` reads ``first.get("city")``
  / ``("target_date")`` / ``("temperature_metric")`` (market_scanner.py ~L3530)
  and returns None at the guard
  ``if not (slug and city_name and target_date and metric): return None``
  (~L3535) whenever they are absent -- which was ALWAYS for the decision-time path.
  That silent None made ``family_snapshot_refresher`` return False for every
  family, so a STALE live family could never get a fresh row and requeued forever.

Fix (main.py, inside ``_edli_decision_family_snapshot_refresher._refresh``):
  Re-inject the three family-identity fields into every topology row before
  reconstruct, mirroring the warm-job lane ``refresh_pending_family_snapshots``
  (main.py ~L3580) that already does this and therefore works.

Tests in this file:
  1. CONTRACT -- topology rows from ``_event_family_market_topology_rows`` lack
     city/target_date/temperature_metric; reconstruct on them returns None;
     reconstruct on the SAME rows WITH the three fields re-injected reconstructs.
     This is the exact gap the call-site fix closes.
  2. CALL-SITE GUARD -- the decision-triggered refresher in main.py
     (``_edli_decision_family_snapshot_refresher``) MUST re-inject
     ``temperature_metric`` (the load-bearing field reconstruct reads). This test
     reads the source file directly so reverting the re-injection is immediately RED.

RED-on-revert proof:
  Remove the re-injection list-comprehension from the refresher -> both tests FAIL
  (CONTRACT: reconstruct returns None on the raw rows; GUARD: the re-injection
  block is absent from the refresher source).
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.data.market_scanner import reconstruct_weather_market_from_static_topology
from src.engine.event_reactor_adapter import _event_family_market_topology_rows
from src.state.snapshot_repo import init_snapshot_schema

UTC = timezone.utc

CITY = "Chicago"
TARGET_DATE = "2026-06-15"
METRIC = "high"
# now is well before the F1 12:00-UTC close of TARGET_DATE so hours_to_resolution > 0
NOW = datetime(2026, 6, 14, 19, 30, 0, tzinfo=UTC)
CAPTURED_AT = NOW - timedelta(seconds=5)
FRESHNESS_DEADLINE = CAPTURED_AT + timedelta(seconds=30)
MARKET_END_AT = datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC).isoformat()

_CONDITION_COUNT = 3


# ---------------------------------------------------------------------------
# Fixtures: market_events topology + fresh executable snapshots for the family
# ---------------------------------------------------------------------------

def _topology_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE market_events (
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            outcome TEXT,
            condition_id TEXT,
            token_id TEXT,
            market_slug TEXT,
            range_label TEXT,
            range_low REAL,
            range_high REAL,
            created_at TEXT
        )
        """
    )
    rows = []
    for index in range(1, _CONDITION_COUNT + 1):
        rows.append(
            (
                CITY,
                TARGET_DATE,
                METRIC,
                f"{20 + index}C",
                f"condition-{index}",
                f"yes-{index}",
                "highest-temperature-in-chicago-on-june-15-2026",
                f"{20 + index}C",
                float(20 + index),
                float(21 + index),
                "2026-06-13T08:00:00+00:00",
            )
        )
    conn.executemany(
        "INSERT INTO market_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    return conn


def _snapshot_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_snapshot_schema(conn)
    cols = [
        str(r[1])
        for r in conn.execute("PRAGMA table_info(executable_market_snapshots)").fetchall()
    ]
    for index in range(1, _CONDITION_COUNT + 1):
        condition_id = f"condition-{index}"
        for side, token in (("YES", f"yes-{index}"), ("NO", f"no-{index}")):
            row = {
                "snapshot_id": f"snap-{index}-{side}",
                "condition_id": condition_id,
                "yes_token_id": f"yes-{index}",
                "no_token_id": f"no-{index}",
                "selected_outcome_token_id": token,
                "outcome_label": side,
                "question_id": f"q-{index}",
                "gamma_market_id": f"gamma-{index}",
                "event_id": f"event-{index}",
                "event_slug": "highest-temperature-in-chicago-on-june-15-2026",
                "enable_orderbook": 1,
                "accepting_orders": 1,
                "active": 1,
                "closed": 0,
                "market_start_at": "2026-06-13T04:30:00+00:00",
                "market_end_at": MARKET_END_AT,
                "market_close_at": MARKET_END_AT,
                "sports_start_at": None,
                "token_map_json": f'{{"YES":"yes-{index}","NO":"no-{index}"}}',
                "min_tick_size": "0.01",
                "min_order_size": "5",
                "fee_details_json": '{"fee_rate_fraction":0.0}',
                "neg_risk": 0,
                "orderbook_top_bid": "0.39",
                "orderbook_top_ask": "0.41",
                "orderbook_depth_json": "{}",
                "raw_gamma_payload_hash": "a" * 64,
                "raw_clob_market_info_hash": "b" * 64,
                "raw_orderbook_hash": "c" * 64,
                "authority_tier": "CLOB",
                "captured_at": CAPTURED_AT.isoformat(),
                "freshness_deadline": FRESHNESS_DEADLINE.isoformat(),
            }
            present = {k: v for k, v in row.items() if k in cols}
            conn.execute(
                f"INSERT INTO executable_market_snapshots ({','.join(present)}) "
                f"VALUES ({','.join('?' for _ in present)})",
                list(present.values()),
            )
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Test 1 -- CONTRACT: raw topology rows lack family identity -> reconstruct None;
#            with the three fields re-injected -> reconstruct succeeds.
# ---------------------------------------------------------------------------

def test_reconstruct_needs_family_identity_reinjection() -> None:
    """ANTIBODY (#92): the topology rows from _event_family_market_topology_rows do
    NOT carry city/target_date/temperature_metric, so reconstruct returns None at
    its identity guard. Re-injecting the three fields (the fix the refresher applies)
    makes reconstruct succeed on the SAME rows + snapshots.

    RED-on-revert: without the call-site re-injection the refresher passes these raw
    rows straight to reconstruct -> None -> refresher returns False for EVERY family
    (decision_triggered_targeted_refresh marker at ZERO, processed approx 0)."""
    topo = _topology_conn()
    snaps = _snapshot_conn()

    payload = {"city": CITY, "target_date": TARGET_DATE, "metric": METRIC}
    rows = [dict(r) for r in _event_family_market_topology_rows(topo, payload)]
    assert len(rows) == _CONDITION_COUNT, rows

    # CONTRACT: the topology rows carry market_slug + condition_id but NOT the three
    # family-identity columns reconstruct reads (they are WHERE-only, not SELECTed).
    assert all(r.get("market_slug") for r in rows)
    for field in ("city", "target_date", "temperature_metric"):
        assert all(not r.get(field) for r in rows), (
            f"topology rows unexpectedly carry {field!r}; the missing-identity "
            "contract this fix compensates for has changed -- re-audit the refresher"
        )

    # THE BUG: raw rows -> reconstruct returns None (identity guard at ~L3535).
    market_bug = reconstruct_weather_market_from_static_topology(
        snaps, topology_rows=rows, now_utc=NOW
    )
    assert market_bug is None, (
        "raw _event_family_market_topology_rows must FAIL reconstruct (missing "
        "city/target_date/temperature_metric) -- if this now reconstructs, the "
        "topology SELECT was widened and the call-site re-injection is the wrong fix"
    )

    # THE FIX: re-inject the three fields (exactly what the refresher does).
    rows_fixed = [
        {**dict(r), "city": CITY, "target_date": TARGET_DATE, "temperature_metric": METRIC}
        for r in rows
    ]
    market_fix = reconstruct_weather_market_from_static_topology(
        snaps, topology_rows=rows_fixed, now_utc=NOW
    )
    assert market_fix is not None, (
        "with city/target_date/temperature_metric re-injected, reconstruct MUST "
        "succeed -- this is the path that lets the decision-time refresher capture a "
        "fresh book for a STALE live family"
    )
    assert len(market_fix["outcomes"]) == _CONDITION_COUNT
    assert market_fix["hours_to_resolution"] is not None
    assert market_fix["hours_to_resolution"] > 0

    topo.close()
    snaps.close()


# ---------------------------------------------------------------------------
# Test 2 -- CALL-SITE GUARD: the decision-triggered refresher re-injects the
#   family-identity fields before reconstruct.  Reverting that is immediately RED.
# ---------------------------------------------------------------------------

_MAIN_SRC = Path(__file__).parent.parent.parent / "src" / "main.py"


def test_decision_refresher_reinjects_family_identity() -> None:
    """ANTIBODY: _edli_decision_family_snapshot_refresher MUST re-inject
    temperature_metric (the load-bearing identity field reconstruct reads) into the
    topology rows before calling reconstruct.

    Reads main.py source directly so removing the re-injection is RED without
    driving the full live CLOB fetch path.

    RED-on-revert: deleting the
    ``"temperature_metric": metric`` re-injection (or the whole list-comprehension)
    from the refresher fails this assertion."""
    src = _MAIN_SRC.read_text(encoding="utf-8")

    anchor = "def _edli_decision_family_snapshot_refresher("
    assert anchor in src, (
        "Expected the decision-triggered refresher builder in main.py -- "
        "check _edli_decision_family_snapshot_refresher is still present."
    )
    # Scope to the refresher body: from its def to the next top-level def.
    body_start = src.index(anchor)
    next_def = re.search(r"\ndef [A-Za-z_]", src[body_start + len(anchor):])
    body = src[body_start : body_start + len(anchor) + (next_def.start() if next_def else len(src))]

    assert '"temperature_metric": metric' in body, (
        "DECISION-REFRESH IDENTITY RE-INJECTION REVERTED: "
        "_edli_decision_family_snapshot_refresher no longer re-injects "
        '"temperature_metric" into the topology rows before reconstruct. Without it, '
        "reconstruct_weather_market_from_static_topology returns None at its identity "
        "guard (market_scanner.py ~L3535) for EVERY family, the refresher returns "
        "False, and STALE live families requeue forever "
        "(decision_triggered_targeted_refresh marker at ZERO; processed approx 0)."
    )
    # The city/target_date partners must accompany it (all three are required by
    # the reconstruct identity guard).
    assert '"city": city' in body and '"target_date": target_date' in body, (
        "the refresher must re-inject city AND target_date alongside "
        "temperature_metric -- reconstruct's identity guard requires all three"
    )
    assert "priority_condition_ids=priority_condition_ids" in body, (
        "decision-triggered family refresh must pass the event's condition ids into "
        "refresh_executable_market_substrate_snapshots so the family-completion lane "
        "keeps the blocked/price-moved condition first while still capturing siblings"
    )


def test_reactor_refresher_delegates_to_sidecar_pending_family_refresh(monkeypatch) -> None:
    """Gate-level snapshot blocks must not run producer I/O in the reactor.

    A blocked event is already requeued into the pending event table; that table is
    the substrate-observer sidecar's work surface. The reactor drain therefore
    records the nudge and returns without calling the Gamma/CLOB refresh helper.
    """

    import src.main as main

    def fail_refresh(*_args, **_kwargs):
        raise AssertionError("reactor must not call substrate producer refresh")

    monkeypatch.setattr(main, "_refresh_pending_family_snapshots", fail_refresh)

    refresher = main._edli_reactor_family_snapshot_refresher()

    assert refresher(city="Auckland", target_date="2026-06-20", metric="low") is False


def test_reactor_market_absence_provider_reads_gamma_empty_backoff(monkeypatch) -> None:
    """The reactor terminalizes no-listed-market blocks only from Gamma-empty proof.

    The provider must read the same normalized family key the warm lane writes, including
    metric aliases, and must stop proving absence when the backoff expires.
    """

    import src.main as main

    monkeypatch.setattr(main.time, "monotonic", lambda: 100.0)
    key = main._substrate_refresh_family_key("Auckland", "2026-06-20", "lowest")
    monkeypatch.setattr(main, "_GAMMA_EMPTY_BACKOFF_UNTIL", {key: 130.0})

    provider = main._edli_reactor_family_market_absence_provider()

    assert provider(city="Auckland", target_date="2026-06-20", metric="low") is True
    assert provider(city="Auckland", target_date="2026-06-20", metric="high") is False

    monkeypatch.setattr(main.time, "monotonic", lambda: 131.0)
    assert provider(city="Auckland", target_date="2026-06-20", metric="low") is False


def test_reactor_market_absence_provider_reads_sidecar_file_evidence(monkeypatch) -> None:
    """Gamma-empty evidence is produced by the substrate-observer process.

    The order daemon's provider must read the shared evidence surface, not only its
    own process-local backoff map.
    """

    import src.main as main
    from src.data import market_absence_evidence

    monkeypatch.setattr(main.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(main, "_GAMMA_EMPTY_BACKOFF_UNTIL", {})

    def _has_recent_gamma_empty_evidence(*, city, target_date, metric, now=None, path=None):
        return (city, target_date, metric) == ("Auckland", "2026-06-20", "low")

    monkeypatch.setattr(
        market_absence_evidence,
        "has_recent_gamma_empty_evidence",
        _has_recent_gamma_empty_evidence,
    )

    provider = main._edli_reactor_family_market_absence_provider()

    assert provider(city="Auckland", target_date="2026-06-20", metric="low") is True
    assert provider(city="Auckland", target_date="2026-06-20", metric="high") is False

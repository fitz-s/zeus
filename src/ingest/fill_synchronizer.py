# Created: 2026-07-13
# Authority basis: docs/rebuild/local_ledger_excision_2026-07-12.md LX-T4
#   ("continuous fill synchronizer + alias graph"); census_local_ledger/
#   census_chain_sources.md §"Fills + order linkage — SUFFICIENT TODAY" (the
#   join key: order_id -> venue_commands.venue_order_id); consult adjudication
#   §排序攻击 Attack A ("a fill lands after replay but before reader cutover" —
#   one-time replay is not enough, the synchronizer must run continuously with
#   a durable coverage watermark).
"""Continuous fill synchronizer: appends venue trade observations, forever.

GROWS existing organs — does not build a parallel pipeline:

  - reads via the EXISTING authenticated ``get_trades(since)``
    (``src.venue.polymarket_v2_adapter.PolymarketV2Adapter.get_trades``);
  - attributes via the EXISTING order_id -> ``venue_commands.venue_order_id``
    join and raw-trade parsing helpers, REUSED (imported, not copied) from
    ``src.execution.exchange_reconcile`` (the module that already does this
    join in its M5 sweep, ``refresh_unresolved_reconcile_findings`` /
    ``run_reconcile_sweep``);
  - writes via the EXISTING ``src.state.venue_command_repo.append_trade_fact``
    (append-only observation log; this module never mutates its semantics or
    the command state machine — command-state side effects (FILL_CONFIRMED
    events, position projections, findings) remain exchange_reconcile's job).

WHY THIS MODULE EXISTS (vs. relying on the M5 sweep alone)
------------------------------------------------------------
The M5 sweep (``run_reconcile_sweep`` / ``refresh_unresolved_reconcile_findings``)
is triggered by WS-gap/heartbeat-loss/cutover events and by already-open
findings — it is not a standing, unconditional poller. LX-T4's consult
adjudication requires venue_trade_facts (the sole chain-fact observation log
under the local-ledger excision target shape) to have a source that "SYNCS
CONTINUOUSLY" so a fill landing in the gap between a one-time historical
replay and a reader cutover is still observed (Attack A) — this module is
that continuous poller, scheduled independently of WS health.

FOREIGN FILLS (shared wallet)
-----------------------------
``get_trades()`` returns trades for the WHOLE wallet (shared with the
operator's manual orders — census_chain_sources.md). A trade whose taker/maker
order_id does not resolve to any local ``venue_commands`` row is a foreign
fill: ``append_trade_fact`` requires a non-empty ``command_id`` (its contract
is NOT relaxed here), so a foreign fill cannot be appended at all — it is
counted (``foreign_fill_count``) and skipped, never fabricated onto a Zeus
command.

DURABLE COVERAGE WATERMARK
---------------------------
``get_trades()`` today returns ALL currently-visible trades on every call (no
server-side pagination cursor — see ``PolymarketV2Adapter.get_trades``, which
accepts ``since`` but does not forward it to the SDK). Absence-safety
therefore comes from two independent mechanisms, not from windowing alone:

  1. every fetched trade is checked against ``venue_trade_facts`` for an
     already-recorded identical revision (idempotent re-append rejected);
  2. ``fill_sync_watermarks`` records the wall-clock time of the last fully
     persisted sync cycle (a coverage-completeness proof, not a hard
     resumption cursor — there is nothing to resume when every cycle already
     scans everything). ``since`` is still passed to ``get_trades`` so a
     future SDK/venue surface that adds real windowing is honored for free.

The watermark row is only written AFTER the whole batch's ``append_trade_fact``
calls have succeeded (advance-after-persist): on any failure mid-cycle the
connection is rolled back, nothing partial persists, and the next cycle
retries the full scan from the unchanged watermark.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any

from src.execution.exchange_reconcile import (
    _hash_payload,
    _local_command_for_trade,
    _local_commands_by_order,
    _missing_trade_fill_economics,
    _raw,
    _trade_fill_price,
    _trade_filled_size,
    _trade_id,
    _trade_order_ids,
    _trade_state,
)
from src.state.schema.fill_sync_watermarks_schema import ensure_table as ensure_watermark_table
from src.state.venue_command_repo import _row_factory_as, append_trade_fact

logger = logging.getLogger(__name__)

DEFAULT_SOURCE = "polymarket_v2_get_trades"


def _coerce_dt(value: datetime | str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(value))


def get_watermark(conn: sqlite3.Connection, *, source: str = DEFAULT_SOURCE) -> dict[str, Any] | None:
    """Return the current coverage watermark row for ``source``, or None."""

    ensure_watermark_table(conn)
    with _row_factory_as(conn, sqlite3.Row):
        row = conn.execute(
            "SELECT source, watermark_ts, cursor, updated_at, coverage_note "
            "FROM fill_sync_watermarks WHERE source = ?",
            (source,),
        ).fetchone()
    return dict(row) if row is not None else None


def _advance_watermark(
    conn: sqlite3.Connection,
    *,
    source: str,
    watermark_ts: str,
    cursor: str | None,
    updated_at: str,
    coverage_note: str,
) -> None:
    conn.execute(
        """
        INSERT INTO fill_sync_watermarks (source, watermark_ts, cursor, updated_at, coverage_note)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(source) DO UPDATE SET
            watermark_ts = excluded.watermark_ts,
            cursor = excluded.cursor,
            updated_at = excluded.updated_at,
            coverage_note = excluded.coverage_note
        """,
        (source, watermark_ts, cursor, updated_at, coverage_note),
    )


def _fact_already_recorded(
    conn: sqlite3.Connection,
    *,
    trade_id: str,
    command_id: str,
    state: str,
    filled_size: str,
    fill_price: str,
) -> bool:
    """True if an identical revision of this trade fact is already durable.

    Scoped to the exact (trade_id, command_id, state, filled_size, fill_price)
    tuple — a genuinely NEW lifecycle revision (e.g. MATCHED -> CONFIRMED, or a
    corrected fill_price) is still appended as its own row; only a byte-for-byte
    repeat observation is rejected. ``append_trade_fact`` itself always inserts
    (it is an append-only log with no upsert), so this check is what makes
    re-running a sync cycle over the same venue response idempotent.
    """

    row = conn.execute(
        """
        SELECT 1 FROM venue_trade_facts
         WHERE trade_id = ? AND command_id = ? AND state = ?
           AND filled_size = ? AND fill_price = ?
         LIMIT 1
        """,
        (trade_id, command_id, state, filled_size, fill_price),
    ).fetchone()
    return row is not None


def sync_fills(
    conn: sqlite3.Connection,
    adapter: Any,
    *,
    source: str = DEFAULT_SOURCE,
    observed_at: datetime | str | None = None,
) -> dict[str, Any]:
    """Run one continuous-sync cycle. Returns a summary dict for logging/health.

    ``adapter`` is read-only here: only ``get_trades`` is called. Attribution,
    idempotent-append, and the watermark advance all happen inside a single
    connection-level transaction — see module docstring "advance-after-persist".
    """

    ensure_watermark_table(conn)
    observed = _coerce_dt(observed_at)
    watermark = get_watermark(conn, source=source)
    since_cursor = watermark.get("watermark_ts") if watermark else None

    raw_trades = list(adapter.get_trades(since=since_cursor) or [])
    local_by_order = _local_commands_by_order(conn)

    appended = 0
    skipped_idempotent = 0
    foreign_fill_count = 0
    unattributable_count = 0

    try:
        # Explicit outer transaction: append_trade_fact's internal atomicity
        # unit is a bare SAVEPOINT/RELEASE (src.state.venue_command_repo.
        # _savepoint_atomic), which composes with an ALREADY-OPEN transaction
        # but otherwise commits immediately on its own RELEASE (project memory
        # L30 / the module's own docstring) — Python's sqlite3 driver does not
        # auto-BEGIN before a bare "SAVEPOINT" statement the way it does for
        # INSERT/UPDATE/DELETE. Without this explicit BEGIN, each
        # append_trade_fact call in the loop below would durably commit on its
        # own, defeating "advance-after-persist": a later failure in the SAME
        # cycle would leave earlier appends committed with no watermark
        # advance, so a retry would see them as already-recorded (harmless,
        # since _fact_already_recorded is idempotent) but the batch would no
        # longer be all-or-nothing. Mirrors src/engine/global_auction_universe.py
        # / src/state/portfolio.py's plain ``conn.execute("BEGIN")`` precedent.
        conn.execute("BEGIN")
        for trade in raw_trades:
            raw = _raw(trade)
            trade_id = _trade_id(raw)
            state = _trade_state(raw)
            order_id, command = _local_command_for_trade(raw, local_by_order)

            if not trade_id or state is None:
                unattributable_count += 1
                continue
            if command is None or not order_id:
                foreign_fill_count += 1
                continue

            command_id = str(command["command_id"])
            filled_size = _trade_filled_size(raw, order_id)
            fill_price = _trade_fill_price(raw, order_id)
            missing = _missing_trade_fill_economics(
                state=state, filled_size=filled_size, fill_price=fill_price
            )
            if missing:
                unattributable_count += 1
                continue

            filled_size_s = str(filled_size)
            fill_price_s = str(fill_price)
            if _fact_already_recorded(
                conn,
                trade_id=trade_id,
                command_id=command_id,
                state=state,
                filled_size=filled_size_s,
                fill_price=fill_price_s,
            ):
                skipped_idempotent += 1
                continue

            append_trade_fact(
                conn,
                trade_id=trade_id,
                venue_order_id=order_id,
                command_id=command_id,
                state=state,
                filled_size=filled_size_s,
                fill_price=fill_price_s,
                source="REST",
                observed_at=observed,
                raw_payload_hash=_hash_payload(raw),
                raw_payload_json=raw,
                tx_hash=raw.get("transaction_hash") or raw.get("tx_hash"),
            )
            appended += 1

        _advance_watermark(
            conn,
            source=source,
            watermark_ts=observed.isoformat(),
            cursor=None,
            updated_at=observed.isoformat(),
            coverage_note=(
                f"full get_trades() scan; {len(raw_trades)} trades observed, "
                f"{appended} appended"
            ),
        )
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()

    return {
        "source": source,
        "trades_seen": len(raw_trades),
        "appended": appended,
        "skipped_idempotent": skipped_idempotent,
        "foreign_fill_count": foreign_fill_count,
        "unattributable_count": unattributable_count,
        "watermark_ts": observed.isoformat(),
    }


def fill_synchronizer_cycle() -> dict[str, Any] | None:
    """Scheduler entry point (registered by ``price_channel_daemon``).

    Disabled unless ``edli_v1.fill_synchronizer_enabled`` is set — mirrors the
    other price-channel-ingest producers' settings-gated enablement. Opens its
    own trade connection and the live venue adapter; never raises (a poller
    fault must not crash the scheduler — the next tick retries).
    """

    from src.ingest.price_channel_ingest import _settings_section

    edli_cfg = _settings_section("edli_v1", {}) or {}
    if not bool(edli_cfg.get("fill_synchronizer_enabled", False)):
        return None

    from src.data.polymarket_client import PolymarketClient
    from src.state.db import get_trade_connection

    conn = get_trade_connection(write_class="live")
    try:
        client = PolymarketClient()
        adapter = client._ensure_v2_adapter()
        return sync_fills(conn, adapter)
    except Exception as exc:  # noqa: BLE001
        logger.error("fill_synchronizer cycle failed (non-fatal; next tick retries): %s", exc, exc_info=True)
        return None
    finally:
        conn.close()

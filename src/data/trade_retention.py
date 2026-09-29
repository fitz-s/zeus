# Created: 2026-09-29
# Last reused or audited: 2026-09-29
# Authority basis: docs/operations/current/plans/edge_program_2026-09-25.md goal 4
#   (disk stops growing without bound; nothing valuable is lost; one universal rule).
#   INV-37: every write is one trade-DB transaction; world/forecasts are read mode=ro.
"""Trade-DB retention by family reachability: ``executable_market_snapshots``.

Why this table: it is the trade DB's one unbounded store (49.6 GB of rows, 0.13 GB/day,
2026-05-15..now). Its inline expiry in ``snapshot_repo.insert_snapshot`` is inert on the
live DB -- it requires ``idx_executable_market_snapshots_captured_at_only``, which was
never built (``_inline_expire_plan`` logs "required cutoff index ... unavailable" on
every firing). ``decision_log`` and ``execution_feasibility_evidence`` already expire
inline and are bounded (7/30-day windows), so they are not touched here.

A snapshot row is kept while ANY reader can still reach it:

* its family (``market_events`` by ``condition_id``) is reachable under the shared law in
  ``src.data.family_reachability`` (current window, non-terminal position, open rest);
* it is younger than ``READER_WINDOW_DAYS`` -- the longest time-window reader
  (``scripts/qkernel_arm_replay.py`` and ``qkernel_settlement_graded_ev.py`` read
  ``captured_at >= now - 16 days``; the inline law already keeps 30);
* its ``snapshot_id`` is stored by a by-id referrer (``REFERRERS`` plus the current
  ``venue_commands`` and ``executable_market_snapshot_latest`` rows); command recovery,
  envelope gates, exit handoffs, the calibration corpus and replay reports dereference
  these ids at any later time;
* it is the newest row of its ``condition_id`` -- every latest-per-condition reader
  (harvester, settlement commands, riskguard settlement proof, market bounds) keeps an
  answer for every condition ever seen.

Unknown evicts nothing: a failed reachability read, a referrer ledger that has not yet
caught up to its table's end, or a condition absent from ``market_events``.

The referrer ledger is incremental: append-only referrer tables are scanned by rowid
from a persisted cursor in short range-bounded reads (no long WAL-pinning read), and
the ids they carry are persisted, so a pass costs only the new rows.

Deletes run in short coordinated trade transactions (BACKGROUND_RECOVERY, bounded
hold), each dropping and re-creating the append-only delete trigger inside the same
transaction (the sanctioned ``scripts/migrations/202608_executable_market_snapshots_
retention.py`` mechanism), and stop above ``wal_limit_bytes``. Freed pages go to the
freelist (auto_vacuum=NONE) and are reused by new inserts, so the file stops growing.
The pass is idempotent: an evicted row is simply absent next time.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, ContextManager, Iterable

from src.data.family_reachability import Reachability, build_reachability, family, read_only

logger = logging.getLogger(__name__)

TABLE = "executable_market_snapshots"
DELETE_TRIGGER = "no_delete_executable_market_snapshots"
READER_WINDOW_DAYS = 30
STATE_FILE = "trade_retention_state.json"

# Per 10-minute pass: <=50k rows classified and <=500 delete chunks of 100 rows, so a
# pass ends well inside its interval; the ~12M-row backlog drains in about two days.
DEFAULT_ROW_BUDGET = 50_000
DEFAULT_REFERRER_BUDGET = 200_000
DEFAULT_READ_BATCH = 5_000
DEFAULT_CHUNK_ROWS = 100
DEFAULT_WAL_LIMIT_BYTES = 512 << 20
CHUNK_PAUSE_SECONDS = 0.5
# Scalar columns + keys per row beyond the four JSON columns (measured 2026-09-29:
# ~3.4 KB/row, of which ~2.7 KB JSON).
ROW_OVERHEAD_BYTES = 700

_SNAPSHOT_ID_IN_JSON = re.compile(r'"[A-Za-z_]*snapshot_id"\s*:\s*"([^"]+)"')


@dataclass(frozen=True)
class Referrer:
    """An append-only table whose rows store snapshot ids read back by id later."""

    name: str
    db: str  # "trade" | "world"
    table: str
    columns: tuple[str, ...]
    json_payload: bool = False  # columns hold JSON; take every "*snapshot_id" value
    where: str = ""  # extra row filter (only rows that can carry an executable id)


REFERRERS: tuple[Referrer, ...] = (
    Referrer("position_events", "trade", "position_events", ("snapshot_id",)),
    Referrer("market_price_history", "trade", "market_price_history", ("snapshot_id",)),
    Referrer("opportunity_fact", "trade", "opportunity_fact", ("snapshot_id",)),
    Referrer(
        "no_trade_regret_events", "world", "no_trade_regret_events",
        ("causal_snapshot_id", "executable_snapshot_id"),
    ),
    Referrer(
        "edli_no_submit_receipts", "world", "edli_no_submit_receipts",
        ("causal_snapshot_id", "executable_snapshot_id"),
    ),
    Referrer(
        "decision_certificates", "world", "decision_certificates", ("payload_json",), True,
        "AND certificate_type IN ('ActionableTradeCertificate', 'FinalIntentCertificate',"
        " 'PreSubmitRevalidationCertificate', 'ExecutableSnapshotCertificate')",
    ),
)
# Mutable referrers: re-read in full every pass (small), never cursor-scanned.
CURRENT_REFERRER_SQL = (
    "SELECT snapshot_id FROM venue_commands WHERE snapshot_id IS NOT NULL",
    "SELECT snapshot_id FROM executable_market_snapshot_latest",
)


class LedgerIncomplete(RuntimeError):
    """A referrer has rows the ledger has not read yet; evict nothing."""


@dataclass
class Report:
    scanned: int = 0
    evicted: int = 0
    bytes: int = 0
    kept_window: int = 0
    kept_reachable: int = 0
    kept_referenced: int = 0
    kept_newest: int = 0
    unclassified: int = 0
    chunks: int = 0
    stopped: str | None = None

    def as_dict(self) -> dict[str, object]:
        return dict(self.__dict__)


@dataclass
class State:
    ems_cursor: int = 0
    referrer_cursors: dict[str, int] = field(default_factory=dict)
    referenced: set[str] = field(default_factory=set)

    @classmethod
    def load(cls, path: Path) -> "State":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return cls(
                int(raw["ems_cursor"]),
                {str(k): int(v) for k, v in raw["referrer_cursors"].items()},
                set(map(str, raw["referenced"])),
            )
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return cls()

    def save(self, path: Path) -> None:
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.write_text(
            json.dumps(
                {
                    "ems_cursor": self.ems_cursor,
                    "referrer_cursors": self.referrer_cursors,
                    "referenced": sorted(self.referenced),
                }
            ),
            encoding="utf-8",
        )
        os.replace(tmp, path)


def _ids_from_row(values: Iterable[object], json_payload: bool) -> Iterable[str]:
    for value in values:
        if not value:
            continue
        if json_payload:
            yield from _SNAPSHOT_ID_IN_JSON.findall(str(value))
        else:
            yield str(value)


def refresh_ledger(
    state: State,
    conns: dict[str, sqlite3.Connection],
    *,
    budget: int,
    batch: int,
) -> dict[str, dict[str, int]]:
    """Advance every referrer cursor by up to ``budget`` rows; raise if any lags.

    Each read is one short rowid-range statement, so no read transaction stays open
    across batches.
    """

    progress: dict[str, dict[str, int]] = {}
    lagging = []
    for ref in REFERRERS:
        conn = conns[ref.db]
        end = conn.execute(f"SELECT COALESCE(MAX(rowid), 0) FROM {ref.table}").fetchone()[0]
        cursor = state.referrer_cursors.get(ref.name, 0)
        read = 0
        cols = ", ".join(ref.columns)
        while cursor < end and read < budget:
            hi = min(end, cursor + batch)
            for row in conn.execute(
                f"SELECT {cols} FROM {ref.table} WHERE rowid > ? AND rowid <= ? {ref.where}",
                (cursor, hi),
            ):
                state.referenced.update(_ids_from_row(row, ref.json_payload))
            read += hi - cursor
            cursor = hi
        state.referrer_cursors[ref.name] = cursor
        progress[ref.name] = {"cursor": cursor, "end": int(end)}
        if cursor < end:
            lagging.append(ref.name)
    if lagging:
        raise LedgerIncomplete(",".join(lagging))
    return progress


def current_referenced(conn: sqlite3.Connection) -> set[str]:
    ids: set[str] = set()
    for sql in CURRENT_REFERRER_SQL:
        ids.update(str(row[0]) for row in conn.execute(sql) if row[0])
    return ids


def _condition_families(forecast: sqlite3.Connection, conditions: Iterable[str]) -> dict:
    wanted = sorted(set(conditions))
    out: dict[str, tuple[str, str, str]] = {}
    for start in range(0, len(wanted), 500):
        part = wanted[start : start + 500]
        marks = ",".join("?" for _ in part)
        for cond, city, target, metric in forecast.execute(
            f"SELECT condition_id, city, target_date, temperature_metric FROM market_events"
            f" WHERE condition_id IN ({marks})",
            part,
        ):
            if cond and city and target and metric:
                out[str(cond)] = family(city, target, metric)
    return out


def _newest_for_condition(trade: sqlite3.Connection, cache: dict, condition: str) -> str | None:
    if condition not in cache:
        row = trade.execute(
            f"SELECT snapshot_id FROM {TABLE} WHERE condition_id = ?"
            " ORDER BY captured_at DESC LIMIT 1",
            (condition,),
        ).fetchone()
        cache[condition] = str(row[0]) if row else None
    return cache[condition]


def plan_evictions(
    trade: sqlite3.Connection,
    forecast: sqlite3.Connection,
    reach: Reachability,
    referenced: set[str],
    *,
    cursor: int,
    window_cutoff: str,
    row_budget: int,
    batch: int,
    report: Report,
) -> tuple[list[tuple[int, str, int]], int]:
    """Classify rows after ``cursor``; return (candidates, next cursor).

    Candidates are ``(rowid, snapshot_id, bytes)``. The next cursor wraps to 0 on
    reaching the reader window (rowid order is insertion order), so rows kept for a
    reason that later lapses are revisited.
    """

    candidates: list[tuple[int, str, int]] = []
    newest: dict[str, str | None] = {}
    while report.scanned < row_budget:
        rows = trade.execute(
            f"""
            SELECT rowid, snapshot_id, condition_id, captured_at,
                   COALESCE(length(orderbook_depth_json), 0)
                   + COALESCE(length(fee_details_json), 0)
                   + COALESCE(length(token_map_json), 0)
                   + COALESCE(length(tradeability_status_json), 0) + ?
              FROM {TABLE} WHERE rowid > ? ORDER BY rowid LIMIT ?
            """,
            (ROW_OVERHEAD_BYTES, cursor, min(batch, row_budget - report.scanned)),
        ).fetchall()
        if not rows:
            report.stopped = "end"
            return candidates, 0
        families = _condition_families(forecast, (r[2] for r in rows if r[2]))
        for rowid, snapshot_id, condition, captured_at, size in rows:
            if str(captured_at) >= window_cutoff:
                report.kept_window += 1
                report.stopped = "reader_window"
                return candidates, 0
            report.scanned += 1
            cursor = int(rowid)
            fam = families.get(str(condition))
            if snapshot_id in referenced:
                report.kept_referenced += 1
            elif fam is None:
                report.unclassified += 1
            elif reach.reachable(fam):
                report.kept_reachable += 1
            elif _newest_for_condition(trade, newest, str(condition)) == snapshot_id:
                report.kept_newest += 1
            else:
                candidates.append((int(rowid), str(snapshot_id), int(size)))
    report.stopped = "row_budget"
    return candidates, cursor


def _wal_bytes(db_path: Path) -> int:
    try:
        return db_path.with_name(db_path.name + "-wal").stat().st_size
    except FileNotFoundError:
        return 0


def delete_chunk(conn: sqlite3.Connection, snapshot_ids: list[str]) -> int:
    """Delete one chunk inside the caller's open write transaction.

    The append-only trigger is dropped and re-created within that transaction, so no
    other writer can observe it missing; a live command that cites a row keeps it.
    """

    trigger_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?", (DELETE_TRIGGER,)
    ).fetchone()
    if trigger_sql is None or not trigger_sql[0]:
        raise RuntimeError(f"{DELETE_TRIGGER} missing; refusing to delete")
    marks = ",".join("?" for _ in snapshot_ids)
    conn.execute(f"DROP TRIGGER {DELETE_TRIGGER}")
    deleted = conn.execute(
        f"""
        DELETE FROM {TABLE}
         WHERE snapshot_id IN ({marks})
           AND NOT EXISTS (SELECT 1 FROM venue_commands vc WHERE vc.snapshot_id = {TABLE}.snapshot_id)
        """,
        snapshot_ids,
    ).rowcount
    conn.execute(trigger_sql[0])
    return int(deleted)


def _coordinated_transaction() -> Callable[[], ContextManager]:
    from src.state.db import connect_existing_trade_db_without_journal_bootstrap  # noqa: PLC0415
    from src.state.write_coordinator import (  # noqa: PLC0415
        DBIdentity,
        WritePriority,
        default_runtime_write_coordinator,
    )

    coordinator = default_runtime_write_coordinator()

    @contextlib.contextmanager
    def transaction():
        with coordinator.transaction(
            (DBIdentity.TRADE,),
            owner="trade_retention",
            write_class="live",
            priority=WritePriority.BACKGROUND_RECOVERY,
            deadline_ms=1_500,
            max_hold_ms=500,
            connection_factory=connect_existing_trade_db_without_journal_bootstrap,
        ) as tx:
            yield tx.connection

    return transaction


def apply_evictions(
    candidates: list[tuple[int, str, int]],
    *,
    trade_db: Path,
    transaction: Callable[[], ContextManager],
    chunk_rows: int,
    wal_limit_bytes: int,
    pause_seconds: float,
    report: Report,
) -> int | None:
    """Delete candidates in chunks; return the rowid before the first unhandled chunk.

    ``None`` means every candidate was handled.
    """

    from src.state.write_coordinator import WriteLeaseTimeout  # noqa: PLC0415

    for start in range(0, len(candidates), chunk_rows):
        chunk = candidates[start : start + chunk_rows]
        if _wal_bytes(trade_db) > wal_limit_bytes:
            report.stopped = "wal_limit"
            return chunk[0][0] - 1
        try:
            with transaction() as conn:
                deleted = delete_chunk(conn, [c[1] for c in chunk])
        except (WriteLeaseTimeout, sqlite3.OperationalError) as exc:
            report.stopped = f"deferred:{type(exc).__name__}"
            return chunk[0][0] - 1
        report.chunks += 1
        report.evicted += deleted
        report.bytes += sum(c[2] for c in chunk)
        if pause_seconds and start + chunk_rows < len(candidates):
            time.sleep(pause_seconds)
    return None


def run_trade_retention(
    *,
    apply: bool,
    now: datetime | None = None,
    state_dir: Path | None = None,
    trade_db: Path | None = None,
    world_db: Path | None = None,
    forecast_db: Path | None = None,
    transaction: Callable[[], ContextManager] | None = None,
    row_budget: int = DEFAULT_ROW_BUDGET,
    referrer_budget: int = DEFAULT_REFERRER_BUDGET,
    read_batch: int = DEFAULT_READ_BATCH,
    chunk_rows: int = DEFAULT_CHUNK_ROWS,
    wal_limit_bytes: int = DEFAULT_WAL_LIMIT_BYTES,
    pause_seconds: float = CHUNK_PAUSE_SECONDS,
) -> dict[str, object]:
    """One bounded pass. Dry run persists nothing. Never raises."""

    started = time.monotonic()
    if None in (state_dir, trade_db, world_db, forecast_db):
        from src.config import STATE_DIR  # noqa: PLC0415
        from src.state.db import (  # noqa: PLC0415
            ZEUS_FORECASTS_DB_PATH,
            ZEUS_WORLD_DB_PATH,
            _zeus_trade_db_path,
        )

        state_dir = state_dir or STATE_DIR
        trade_db = trade_db or _zeus_trade_db_path()
        world_db = world_db or ZEUS_WORLD_DB_PATH
        forecast_db = forecast_db or ZEUS_FORECASTS_DB_PATH
    now = now or datetime.now(timezone.utc)
    state_path = Path(state_dir) / STATE_FILE
    state = State.load(state_path)
    summary: dict[str, object] = {"apply": apply, "table": TABLE}
    report = Report()
    try:
        reach = build_reachability(now=now, trade_db=Path(trade_db))
    except Exception as exc:  # noqa: BLE001 - unknown reachability evicts nothing
        return summary | {"status": "REACHABILITY_UNAVAILABLE", "error": str(exc)}
    conns: dict[str, sqlite3.Connection] = {}
    try:
        conns = {
            "trade": read_only(Path(trade_db)),
            "world": read_only(Path(world_db)),
            "forecast": read_only(Path(forecast_db)),
        }
        try:
            summary["referrers"] = refresh_ledger(
                state, conns, budget=referrer_budget, batch=read_batch
            )
        except LedgerIncomplete as exc:
            summary["status"] = "LEDGER_CATCHING_UP"
            summary["lagging"] = str(exc)
            if apply:
                state.save(state_path)
            return summary
        referenced = state.referenced | current_referenced(conns["trade"])
        cutoff = (now - timedelta(days=READER_WINDOW_DAYS)).astimezone(timezone.utc).isoformat()
        candidates, next_cursor = plan_evictions(
            conns["trade"],
            conns["forecast"],
            reach,
            referenced,
            cursor=state.ems_cursor,
            window_cutoff=cutoff,
            row_budget=row_budget,
            batch=read_batch,
            report=report,
        )
    except Exception as exc:  # noqa: BLE001 - a failed read evicts nothing
        return summary | {"status": "READ_FAILED", "error": str(exc), "report": report.as_dict()}
    finally:
        for conn in conns.values():
            conn.close()
    summary["oldest_reachable_date"] = reach.oldest_reachable_date
    summary["open_families"] = len(reach.open_families)
    summary["referenced_ids"] = len(referenced)
    if not apply:
        report.evicted = len(candidates)
        report.bytes = sum(c[2] for c in candidates)
        summary["status"] = "DRY_RUN"
    else:
        try:
            stopped_at = apply_evictions(
                candidates,
                trade_db=Path(trade_db),
                transaction=transaction or _coordinated_transaction(),
                chunk_rows=chunk_rows,
                wal_limit_bytes=wal_limit_bytes,
                pause_seconds=pause_seconds,
                report=report,
            )
        except Exception as exc:  # noqa: BLE001 - keep the cursor; retry next pass
            logger.exception("trade retention delete failed")
            summary["status"] = "DELETE_FAILED"
            summary["error"] = str(exc)
            stopped_at = candidates[0][0] - 1 if candidates else None
        state.ems_cursor = next_cursor if stopped_at is None else stopped_at
        state.save(state_path)
        summary.setdefault("status", "APPLIED")
    summary["report"] = report.as_dict()
    summary["elapsed_s"] = round(time.monotonic() - started, 3)
    return summary


def _main(argv: Iterable[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="delete (default: dry run)")
    parser.add_argument("--row-budget", type=int, default=DEFAULT_ROW_BUDGET)
    parser.add_argument("--referrer-budget", type=int, default=DEFAULT_REFERRER_BUDGET)
    args = parser.parse_args(list(argv) if argv is not None else None)
    print(
        json.dumps(
            run_trade_retention(
                apply=args.apply,
                row_budget=args.row_budget,
                referrer_budget=args.referrer_budget,
            ),
            indent=1,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())

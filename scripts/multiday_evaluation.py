#!/usr/bin/env python3
# Created: 2026-10-05
# Last audited: 2026-10-05
# Authority basis: operator law 2026-10-05 ("只添加记录 ... 执行真正的多日交易与持续评估"):
#   records and continuous evaluation only; no calibration, proof study, gate or
#   trade-affecting change. Provenance audit 2026-10-05: reuses the selector's own
#   resolution horizon (src.engine.global_auction_universe._payload_resolution_at_utc,
#   CURRENT) and the canonical venue_trade_facts dedup (src.state.fill_dedup:
#   one row per (command_id, trade_id) by proof strength then local_sequence, tx-hash
#   aggregate aliases excluded once an exact child exists).
"""Read-only multi-day evaluation of live entries, outcomes and exits.

A report, never a verdict: no thresholds, no pass/fail, nothing here can change
what trades. Every DB is opened ``mode=ro`` on its own short-lived connection
(INV-37: no ATTACH, no write transaction on any canonical DB).

Decision-time fields are DERIVED here from immutable stored inputs rather than
recorded on the submit path:

  decision clock       venue_commands.created_at of the ENTRY command (the same
                       ``now_str`` that stamps its SUBMIT_REQUESTED event).
  market_listed_at     earliest ``market_events.created_at`` of the command's
                       condition_id that is a Gamma event createdAt (``...Z``
                       verbatim, src/data/market_scanner.py). ``+00:00`` rows are
                       the writer's now() fallback or the recorded_at backfill, not
                       listing times, so they yield null, never a guessed age.
  market_age_hours     decision clock - market_listed_at (null if either is
                       unknown or the difference is negative).
  settlement_lead_hours  resolution_at_utc - decision clock, where resolution_at_utc
                       is the selector's horizon: local midnight ending target_date
                       in the city's settlement timezone.
  condition_id         executable_market_snapshots.condition_id of the command's
                       snapshot (append-only), else position_current.condition_id.

  exit cost            each SELL is charged the running-average cost of the inventory held
                       when it filled (the ledger's allocated_cost = quantity * unit_cost
                       law, on canonical fills ordered by fill_dedup's execution_ts), so
                       a buy that fills after a sell never enters that sell's cost.

Entry history: a window position (target_date >= --since) is costed and bucketed from its
COMPLETE ENTRY history regardless of the ENTRY_FLOOR_DAYS cut, which only bounds which other
ENTRY commands feed the entry-level counts.

Units: ENTRY commands are bucketed by their own market age. Positions (outcome,
exits, exposure) are bucketed by the age of their first filled ENTRY command.
Filtering is by target_date >= --since; open exposure older than the window is
reported as a scalar.
"""
from __future__ import annotations

import argparse
import contextlib
import errno
import faulthandler
import fcntl
import json
import logging
import os
import re
import sqlite3
import sys
import time
from collections import Counter
from dataclasses import dataclass, field, fields
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger("multiday_evaluation")

SCHEMA = "multiday_evaluation.v1"
EXIT_DB_BUSY = 75  # EX_TEMPFAIL: the scheduler tick treats it as "try the next cadence"
# The child's own wall-clock deadline. src/main.py's subprocess.run timeout (300 s) is the
# parent's backstop and must stay above this, so a hung child dies by its own watchdog first
# and still dies when the parent daemon is gone and nothing is left to kill it.
CHILD_DEADLINE_S = 270.0
AGE_BUCKETS = ("<12h", "12-24h", "24-48h", ">=48h", "unknown")
OPEN_PHASES = ("active", "day0_window", "pending_exit")
POSITION_PHASES = OPEN_PHASES + ("economically_closed", "settled")
# Markets list <= ~3 days before target_date (observed max entry lead 2 d).
ENTRY_FLOOR_DAYS = 7
DEFAULT_DAYS = 14
FILL_OVERSHOOT = 1.05
_BATCH = 400


# --------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------
def age_bucket(age_hours: float | None) -> str:
    if age_hours is None:
        return "unknown"
    if age_hours < 12:
        return "<12h"
    if age_hours < 24:
        return "12-24h"
    if age_hours < 48:
        return "24-48h"
    return ">=48h"


def _utc(text: Any) -> datetime | None:
    if not text:
        return None
    try:
        stamp = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def gamma_listed_at(created_at_values: Iterable[Any]) -> datetime | None:
    """Earliest Gamma createdAt among a condition's market_events rows."""
    stamps = [
        _utc(v) for v in created_at_values if isinstance(v, str) and v.endswith("Z")
    ]
    stamps = [s for s in stamps if s is not None]
    return min(stamps) if stamps else None


def hours_between(later: datetime | None, earlier: datetime | None) -> float | None:
    if later is None or earlier is None:
        return None
    return (later - earlier).total_seconds() / 3600.0


def market_age_hours(submitted: datetime | None, listed: datetime | None) -> float | None:
    age = hours_between(submitted, listed)
    return age if age is not None and age >= 0 else None


def fill_totals(rows: Iterable[Sequence[Any]], command_size: float) -> tuple[float, float, bool]:
    """(shares, notional_usd, overshoot_flag) from economic (size, price[, execution_ts]) rows.

    Rows are already exactly-once (see ``load_economic_fills``); several children may
    share one tx_hash and several equal-size children may carry no tx_hash, so no
    further identity folding happens here.
    """
    shares = notional = 0.0
    for size, price, *_ in rows:
        shares += float(size)
        notional += float(size) * float(price)
    return shares, notional, command_size > 0 and shares > FILL_OVERSHOOT * command_size


_EPOCH = datetime.min.replace(tzinfo=timezone.utc)
_INVENTORY_EPS = 1e-9


def sell_costs(
    buys: Iterable[Sequence[Any]], sells: Iterable[Sequence[Any]], *, buys_unverifiable: bool = False
) -> list[dict]:
    """Charge each SELL the average cost of the inventory held when it filled.

    ``buys``/``sells`` are economic (size, price, execution_ts) fills of ONE position.
    Events are replayed in execution order (a buy and a sell at the same instant order
    buy first), so a buy that fills AFTER a sell never enters that sell's cost. This is
    the position ledger's own allocation law (src/execution/exit_lifecycle.py:
    ``allocated_cost = quantity * unit_cost``, unit cost = remaining basis / open
    shares), applied to the canonical fills: selling at the running average leaves the
    average unchanged, so only the buy/sell interleaving matters, never the order among
    sells. A sell larger than the inventory the loaded fills can account for (entry
    outside the loaded window, chain-only holdings) has ``cost_usd`` None, never a guess;
    it consumes the inventory it had.

    The replay needs every fill's place in time. If ANY fill of the position (buy or sell)
    has a missing or unparseable clock, the position's fills cannot be ordered, and a partial
    answer would be wrong: an untimed SELL that is merely skipped still shapes the inventory
    the later sells are charged against (BUY 10@0.20, untimed SELL 10, BUY 10@0.80, SELL 5
    would price the last sell at $2.50 where the truth is $4.00). So EVERY sell of that
    position gets ``cost_usd`` None with ``unknown`` = "no_clock".

    ``buys_unverifiable`` says one of the position's ENTRY facts is a recognized taker whose maker
    legs do not prove its full size (the ledger's TAKER_UNVERIFIABLE): its cost cannot be known,
    so, as with a missing clock, EVERY sell of the position gets ``cost_usd`` None with
    ``unknown`` = "unverifiable_entry_cost". The ledger refuses to use that fact's rounded
    top-level price, and so does this report.
    """
    buys, sells = list(buys), list(sells)
    if buys_unverifiable:
        return [
            {"ts": ts, "shares": float(size), "proceeds_usd": float(size) * float(price),
             "cost_usd": None, "unknown": "unverifiable_entry_cost"}
            for size, price, ts in sells
        ]
    if any(_utc(ts) is None for _, _, ts in [*buys, *sells]):
        return [
            {"ts": ts, "shares": float(size), "proceeds_usd": float(size) * float(price),
             "cost_usd": None, "unknown": "no_clock"}
            for size, price, ts in sells
        ]
    events = [(_utc(ts) or _EPOCH, 0, float(size), float(price), ts) for size, price, ts in buys]
    events += [(_utc(ts) or _EPOCH, 1, float(size), float(price), ts) for size, price, ts in sells]
    events.sort(key=lambda e: (e[0], e[1]))
    held = basis = 0.0
    out: list[dict] = []
    for when, kind, size, price, ts in events:
        if kind == 0:
            held += size
            basis += size * price
            continue
        if held > 0 and held + _INVENTORY_EPS >= size:
            cost = basis * min(size, held) / held
            held -= min(size, held)
            basis -= cost
        else:
            cost = None
            held = basis = 0.0
        out.append(
            {"ts": ts, "shares": size, "proceeds_usd": size * price, "cost_usd": cost,
             "unknown": None if cost is not None else "no_inventory"}
        )
    return out


def held_payoff(direction: str | None, settled_in_bin: int | None) -> float | None:
    """Payout per share of the held token given where the settlement landed."""
    if settled_in_bin is None or direction not in ("buy_yes", "buy_no"):
        return None
    return float(settled_in_bin) if direction == "buy_yes" else 1.0 - float(settled_in_bin)


def reason_head(reason: Any) -> str:
    text = str(reason or "").strip()
    return re.split(r"[ :(\[]", text, maxsplit=1)[0] if text else "UNKNOWN"


def _entry_q_live(payload_json: Any) -> float | None:
    try:
        payload = json.loads(payload_json)
        for comp in payload["execution_capability"]["components"]:
            if comp.get("component") == "entry_economics":
                q = comp["details"]["q_live"]
                return float(q) if q is not None else None
    except (TypeError, ValueError, KeyError, AttributeError):
        return None
    return None


def _default_resolution_at(payload, family, *, city_configs):
    from src.engine.global_auction_universe import _payload_resolution_at_utc

    return _payload_resolution_at_utc(payload, family, city_configs=city_configs)


# --------------------------------------------------------------------------
# Additive accumulator
# --------------------------------------------------------------------------
@dataclass
class Cell:
    entries_submitted: int = 0
    submitted_notional_usd: float = 0.0
    entries_filled: int = 0
    filled_shares: float = 0.0
    filled_notional_usd: float = 0.0
    q_shares: float = 0.0          # shares of fills that carry an acting q
    q_x_shares: float = 0.0
    price_x_shares: float = 0.0    # fill notional over the same fills
    q_missing_fills: int = 0
    lead_n: int = 0
    lead_hours_sum: float = 0.0
    fill_overshoot_flags: int = 0
    positions: int = 0
    settled: int = 0
    wins: int = 0
    losses: int = 0
    flat: int = 0
    pnl_null: int = 0
    realized_pnl_usd: float = 0.0
    # What the graded settled positions would have made had every share been held to
    # settlement (settlement_attribution.world_grade_pnl_usd, src/analysis/
    # settlement_skill_attribution.py). NOT a check of realized_pnl_usd, which also includes
    # the actual SELLs and covers every settled position, graded or not.
    hold_to_settlement_pnl_usd: float = 0.0
    hold_to_settlement_n: int = 0
    # The ONLY cohort on which realized and hold-to-settlement may be differenced: settled,
    # realized_pnl_usd not null AND graded with a hold-to-settlement value.
    matched_n: int = 0
    matched_realized_usd: float = 0.0
    matched_hold_to_settlement_usd: float = 0.0
    # Settled positions with a realized PnL but no hold-to-settlement grade; kept out of the
    # matched difference.
    ungraded_settled_n: int = 0
    ungraded_realized_pnl_usd: float = 0.0
    closed_unsettled: int = 0
    closed_unsettled_pnl_usd: float = 0.0
    exits: int = 0
    exit_proceeds_usd: float = 0.0
    exit_proceeds_costed_usd: float = 0.0   # proceeds of the sells whose cost is known
    exit_cost_usd: float = 0.0
    exit_cost_unknown: int = 0              # sells with no accountable inventory
    exits_resolved: int = 0
    resolved_sell_proceeds_usd: float = 0.0
    resolved_hold_value_usd: float = 0.0
    exits_hold_better: int = 0
    open_positions: int = 0
    open_cost_usd: float = 0.0
    attribution: Counter = field(default_factory=Counter)
    exit_reasons: Counter = field(default_factory=Counter)
    revaluations: Counter = field(default_factory=Counter)

    def merge(self, other: "Cell") -> None:
        for f in fields(self):
            mine, theirs = getattr(self, f.name), getattr(other, f.name)
            if isinstance(mine, Counter):
                mine.update(theirs)
            else:
                setattr(self, f.name, mine + theirs)

    def summary(self) -> dict:
        out: dict[str, Any] = {}
        for f in fields(self):
            v = getattr(self, f.name)
            out[f.name] = dict(sorted(v.items())) if isinstance(v, Counter) else (round(v, 6) if isinstance(v, float) else v)
        mq = self.q_x_shares / self.q_shares if self.q_shares else None
        mp = self.price_x_shares / self.q_shares if self.q_shares else None
        decided = self.wins + self.losses
        out["mean_acting_q"] = None if mq is None else round(mq, 6)
        out["mean_fill_price_on_q_fills"] = None if mp is None else round(mp, 6)
        out["mean_q_minus_fill_price"] = None if mq is None else round(mq - mp, 6)
        out["win_rate"] = round(self.wins / decided, 6) if decided else None
        out["mean_settlement_lead_hours"] = round(self.lead_hours_sum / self.lead_n, 3) if self.lead_n else None
        out["exit_pnl_usd"] = round(self.exit_proceeds_costed_usd - self.exit_cost_usd, 6)
        out["hold_minus_sell_usd"] = round(self.resolved_hold_value_usd - self.resolved_sell_proceeds_usd, 6)
        out["matched_realized_minus_hold_usd"] = (
            round(self.matched_realized_usd - self.matched_hold_to_settlement_usd, 6) if self.matched_n else None
        )
        return out


# --------------------------------------------------------------------------
# Loaders (read-only)
# --------------------------------------------------------------------------
def _open_ro(path: Path | str):
    from src.state.db import get_connection_read_only

    return get_connection_read_only(Path(path))


def _chunks(items: Sequence, n: int = _BATCH):
    for i in range(0, len(items), n):
        yield items[i : i + n]


def _in_rows(conn, sql: str, ids: Iterable[str], tail: str = "") -> list:
    ids = sorted(set(ids))
    rows: list = []
    for chunk in _chunks(ids):
        rows.extend(conn.execute(sql.format(ph=",".join("?" * len(chunk))) + tail, chunk).fetchall())
    return rows


@contextlib.contextmanager
def _read_snapshot(conn):
    """One read transaction: every SELECT inside sees the same committed state of the DB.

    sqlite3 does not open a transaction for a SELECT, so without this each statement is its
    own snapshot and a trade fact or command committed between two of them (the clock map and
    the canonical economics, say) is seen by one and not the other. The connection is
    mode=ro and the transaction is deferred, so it takes no write lock; it is held for the
    duration of the load only. Nests: inside an open transaction it does nothing.
    """
    if conn.in_transaction:
        yield
        return
    conn.execute("BEGIN")
    try:
        yield
    finally:
        if conn.in_transaction:
            conn.execute("COMMIT")


def load_trades_data(conn, since: date) -> dict:
    """The whole trade-DB side of the report, read from one snapshot."""
    with _read_snapshot(conn):
        return _load_trades_data(conn, since)


def _load_trades_data(conn, since: date) -> dict:
    conn.row_factory = sqlite3.Row
    since_s = since.isoformat()
    floor = (since - timedelta(days=ENTRY_FLOOR_DAYS)).isoformat()
    phases = ",".join(f"'{p}'" for p in POSITION_PHASES)
    td: dict[str, Any] = {}
    entry_sql = (
        "SELECT c.command_id, c.position_id, c.state, c.size, c.price, c.created_at, "
        "       s.condition_id AS snap_cond, p.condition_id AS pos_cond, "
        "       p.city AS pos_city, p.target_date AS pos_date, p.temperature_metric AS pos_metric "
        "FROM venue_commands c "
        "LEFT JOIN executable_market_snapshots s ON s.snapshot_id = c.snapshot_id "
        "LEFT JOIN position_current p ON p.position_id = c.position_id "
        "WHERE c.intent_kind = 'ENTRY' AND {where}"
    )
    entries = {r["command_id"]: dict(r) for r in conn.execute(entry_sql.format(where="c.created_at >= ?"), (floor,))}
    td["entries"] = []   # filled below, once the window's positions are known
    td["positions"] = [
        dict(r)
        for r in conn.execute(
            f"""
            SELECT position_id, phase, city, target_date, temperature_metric AS metric,
                   direction, cost_basis_usd, entry_price, realized_pnl_usd, settled_at,
                   settlement_price, exit_reason
            FROM position_current
            WHERE target_date >= ? AND phase IN ({phases})
            """,
            (since_s,),
        )
    ]
    pids = [p["position_id"] for p in td["positions"]]
    # The floor only bounds which OTHER ENTRY commands are read for the entry-level counts. A
    # window position's cost, age bucket and exits use its COMPLETE ENTRY history, however old.
    for r in _in_rows(conn, entry_sql.format(where="c.position_id IN ({ph})"), pids):
        entries.setdefault(r["command_id"], dict(r))
    td["entries"] = sorted(entries.values(), key=lambda e: (e["created_at"], e["command_id"]))
    td["exit_cmds"] = [
        dict(r)
        for r in _in_rows(
            conn,
            "SELECT command_id, position_id, size FROM venue_commands "
            "WHERE intent_kind = 'EXIT' AND position_id IN ({ph})",
            pids,
        )
    ]
    fills, td["entry_unverifiable"] = load_economic_fills(conn, [e["command_id"] for e in td["entries"]])
    td["fills"] = fills
    td["exit_fills"], td["exit_fill_debt"] = load_exit_fills(conn, td["exit_cmds"])
    entry_ids = {e["command_id"] for e in td["entries"]}
    td["entry_q"] = {
        r[0]: _entry_q_live(r[1])
        for r in _in_rows(
            conn,
            "SELECT command_id, payload_json FROM venue_command_events "
            "WHERE event_type = 'SUBMIT_REQUESTED' AND command_id IN ({ph})",
            [c for c in fills if c in entry_ids],
        )
    }
    # Forced onto the composite index. Live EXPLAIN QUERY PLAN without the hint picks
    # sqlite_autoindex_position_events_3 (position_id=?) for this shape (equality or IN
    # on event_type, plus ORDER BY position_id, sequence_no), which walks every event of
    # each position, ~20k MONITOR_REFRESHED payloads apiece: 21-45 s on 150 positions.
    # With the hint it is (position_id=? AND event_type=?) at ~0.01 s. Same precedent as
    # src/execution/exit_lifecycle.py's EXIT_INTENT lookups.
    reasons: dict[str, str] = {}
    exit_pids = [c["position_id"] for c in td["exit_cmds"]]
    for event_type in ("EXIT_INTENT", "EXIT_ORDER_POSTED", "EXIT_ORDER_FILLED"):
        for r in _in_rows(
            conn,
            "SELECT position_id, json_extract(payload_json, '$.exit_reason') "
            "FROM position_events INDEXED BY idx_position_events_position_type_sequence "
            f"WHERE event_type = '{event_type}' AND position_id IN ({{ph}})",
            exit_pids,
            " ORDER BY position_id, sequence_no",
        ):
            if r[1]:
                reasons.setdefault(r[0], r[1])
    td["exit_reasons"] = reasons
    td["revaluations"] = []
    for ts, action, reason, family, cmd in conn.execute(
        "SELECT timestamp, json_extract(artifact_json, '$.action'), "
        "json_extract(artifact_json, '$.reason'), json_extract(artifact_json, '$.family'), "
        "json_extract(artifact_json, '$.command_id') "
        "FROM decision_log WHERE mode = 'standing_entry_revaluation' AND timestamp >= ?",
        (floor,),
    ):
        try:
            fam = json.loads(family)
        except (TypeError, ValueError):
            continue
        td["revaluations"].append(
            {"timestamp": ts, "action": action, "reason": reason, "family": fam, "command_id": cmd}
        )
    td["open_older_than_window"] = tuple(
        conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(cost_basis_usd), 0) FROM position_current "
            "WHERE phase IN ('active', 'day0_window', 'pending_exit') AND target_date < ?",
            (since_s,),
        ).fetchone()
    )
    return td


def load_economic_fills(conn, command_ids: Iterable[str]):
    """-> (fills, unverifiable): command_id -> exactly-once [(size, price, execution_ts)], and the
    commands holding a taker fact the ledger calls TAKER_UNVERIFIABLE.

    ``execution_ts`` is fill_dedup's stable execution order (earliest venue timestamp across
    a trade's revisions, else earliest observed_at), the clock its own economic fold uses.

    Scoped to the given commands before ranking, as src.state.fill_dedup requires (its
    alias exclusion re-evaluates the canonical CTE, so an unscoped window rescans all
    history).

    ENTRY economics follow the ledger's own taker binding, exchange_reconcile.
    _trade_fill_economics_binding, imported rather than copied, and EVERY fact goes through it:
    a TAKER on our order that carries no maker_orders field at all is TAKER_UNVERIFIABLE too, and
    must not fall through to its rounded top-level price. The ledger classifies:
      EXACT_TAKER        size and price come from the exact maker legs (the tick-rounded top-level
                         price is not cost-basis authority when they are present);
      LEGACY_NON_TAKER   the canonical price stands;
      TAKER_UNVERIFIABLE a recognized taker whose legs do not prove the full size. The ledger
                         refuses its rounded top-level price (exchange_reconcile:2477-2481), so
                         the fact is NOT returned as a fill: the command goes in ``unverifiable``
                         and the report treats the position's cost as unknown, never a known cost.
    """
    from src.state.fill_dedup import canonical_trade_fact_cte, economic_trade_fact_cte

    fills: dict[str, list[tuple[float, float, str]]] = {}
    facts: list[tuple] = []      # every fact: the ledger decides its economics
    ids = sorted(set(command_ids))
    for chunk in _chunks(ids):
        ph = ",".join("?" * len(chunk))
        sql = (
            f"WITH {canonical_trade_fact_cte(source_clause_sql=f'WHERE fact.command_id IN ({ph})')}, "
            f"{economic_trade_fact_cte()} "
            "SELECT command_id, filled_size, fill_price, execution_ts, raw_payload_json, venue_order_id "
            "FROM economic_trade_fact "
            "WHERE UPPER(COALESCE(state, '')) IN ('MATCHED', 'MINED', 'CONFIRMED') "
            "AND CAST(COALESCE(filled_size, '0') AS REAL) > 0"
        )
        for command_id, size, price, ts, raw_json, order_id in conn.execute(sql, chunk):
            facts.append((command_id, float(size), float(price), ts, raw_json, order_id))
    commands = _command_rows(conn, {f[0] for f in facts})
    unverifiable: set[str] = set()
    for command_id, size, price, ts, raw_json, order_id in facts:
        state, leg = _bind_entry_fact(conn, commands.get(command_id), raw_json, order_id)
        if state == "TAKER_UNVERIFIABLE":
            unverifiable.add(command_id)
            continue
        if state == "EXACT_TAKER" and leg is not None:
            size, price = leg
        fills.setdefault(command_id, []).append((size, price, ts))
    return fills, unverifiable


def _command_rows(conn, command_ids: Iterable[str]) -> dict[str, dict]:
    """command_id -> the venue_commands row, as the ledger's binding reads it."""
    out: dict[str, dict] = {}
    ids = sorted(set(command_ids))
    cur = conn.cursor()
    try:
        for chunk in _chunks(ids):
            ph = ",".join("?" * len(chunk))
            cur.execute(f"SELECT * FROM venue_commands WHERE command_id IN ({ph})", chunk)
            names = [d[0] for d in cur.description]
            for row in cur.fetchall():
                out[row[names.index("command_id")]] = dict(zip(names, row))
    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc).lower():
            raise
        # no command table: the binding cannot be asked, so _bind_entry_fact falls back to the
        # payload's own role (a named TAKER is unverifiable, anything else keeps its price)
    return out


def _bind_entry_fact(conn, command, raw_json, order_id):
    """(state, (shares, unit_cost) | None) from the ledger's own taker binding.

    ``state`` is EXACT_TAKER, LEGACY_NON_TAKER or TAKER_UNVERIFIABLE exactly as
    exchange_reconcile._trade_fill_economics_binding returns it for this fact. A fact the binding
    cannot even be asked about (no command row, a missing table) is treated as TAKER_UNVERIFIABLE
    only if it names a TAKER on this order, else it keeps its canonical price. A NULL or empty
    payload names no role and is LEGACY_NON_TAKER, as the ledger binds it; a payload that does not
    parse, or a binding that cannot be imported, proves no role and is TAKER_UNVERIFIABLE.
    """
    try:
        from src.execution.exchange_reconcile import (
            _trade_fill_economics_binding,
            _trade_payload_for_maker_economics,
        )

        # a NULL or empty payload names no role, so the ledger binds it as LEGACY_NON_TAKER too
        raw = _trade_payload_for_maker_economics(json.loads(raw_json) if raw_json else {})
    except (TypeError, ValueError, ImportError):
        return "TAKER_UNVERIFIABLE", None
    if command is None:
        return ("TAKER_UNVERIFIABLE" if _names_taker(raw, order_id) else "LEGACY_NON_TAKER"), None
    try:
        binding = _trade_fill_economics_binding(
            conn, command=command, raw=raw, venue_order_id=order_id or command.get("venue_order_id") or ""
        )
    except sqlite3.Error:
        return ("TAKER_UNVERIFIABLE" if _names_taker(raw, order_id) else "LEGACY_NON_TAKER"), None
    if binding.state == "EXACT_TAKER" and binding.filled_size and binding.fill_price:
        return "EXACT_TAKER", (float(binding.filled_size), float(binding.fill_price))
    return binding.state, None


def _names_taker(raw, order_id) -> bool:
    """A payload that says its trader_side is TAKER or names this order as its taker order."""
    if not isinstance(raw, Mapping):
        return False
    side = str(raw.get("trader_side") or raw.get("traderSide") or "").strip().upper()
    taker_order = str(raw.get("taker_order_id") or raw.get("takerOrderId") or "").strip()
    return side == "TAKER" or bool(order_id and taker_order == order_id)


def load_exit_fills(conn, exit_cmds: Sequence[Mapping[str, Any]]):
    """position_id -> canonical SELL fills [(quantity, unit_price, execution_ts)], plus debt ids.

    SELL economics come from src.state.fill_dedup.economic_exit_fills_for_position, the one
    intake the ledger books partial exits and settlement from. It applies the taker-SELL
    maker-leg VWAP: a taker trade can report only its lowest matched leg in the top-level
    price while ``maker_orders`` carries every leg (10 sh at 0.20 on top, legs 5@0.20 + 5@0.40,
    are $3.00 of proceeds, not $2.00). The raw fact row's fill_price must not be used for a SELL.
    The function exposes no fill clock, so each fill is joined on (command_id, trade_id) to the
    canonical ``execution_ts`` of the same economic CTE; a fill left without one makes every sell
    of its position cost-unknown (``sell_costs``), never a partial known cost. The clock map and
    the canonical economics are two reads, so they run inside ONE read snapshot: a trade fact
    committed between them can no longer appear in one and not the other. A position whose
    economics the ledger itself calls debt (PartialExitEconomicDebtError) is returned in the
    debt list and gets no proceeds from this report.
    """
    with _read_snapshot(conn):
        return _load_exit_fills(conn, exit_cmds)


def _load_exit_fills(conn, exit_cmds):
    from src.state.fill_dedup import (
        PartialExitEconomicDebtError,
        canonical_trade_fact_cte,
        economic_exit_fills_for_position,
        economic_trade_fact_cte,
    )

    ts_by_fill: dict[tuple[str, str], str] = {}
    for chunk in _chunks(sorted({c["command_id"] for c in exit_cmds})):
        ph = ",".join("?" * len(chunk))
        sql = (
            f"WITH {canonical_trade_fact_cte(source_clause_sql=f'WHERE fact.command_id IN ({ph})')}, "
            f"{economic_trade_fact_cte()} "
            "SELECT command_id, trade_id, execution_ts FROM economic_trade_fact"
        )
        for command_id, trade_id, ts in conn.execute(sql, chunk):
            ts_by_fill[(command_id, trade_id)] = ts
    fills: dict[str, list[tuple[float, float, str | None]]] = {}
    debt: list[str] = []
    for position_id in sorted({c["position_id"] for c in exit_cmds}):
        try:
            canonical = economic_exit_fills_for_position(conn, position_id)
        except PartialExitEconomicDebtError:
            debt.append(position_id)
            continue
        if canonical:
            fills[position_id] = [
                (float(f.quantity), float(f.unit_price), ts_by_fill.get((f.command_id, f.trade_id)))
                for f in canonical
            ]
    return fills, debt


def load_listings(conn, condition_ids: Iterable[str]) -> dict[str, dict]:
    """condition_id -> {city, target_date, metric, created_at_values} from market_events."""
    listings: dict[str, dict] = {}
    for r in _in_rows(
        conn,
        "SELECT condition_id, city, target_date, temperature_metric, created_at "
        "FROM market_events WHERE condition_id IN ({ph})",
        condition_ids,
    ):
        row = listings.setdefault(
            r[0], {"city": r[1], "target_date": r[2], "metric": r[3], "created_at_values": []}
        )
        row["created_at_values"].append(r[4])
    return listings


def load_attribution(conn, position_ids: Iterable[str]) -> dict[str, dict]:
    return {
        r[0]: {"category": r[1], "settled_in_bin": r[2], "direction": r[3], "world_pnl": r[4]}
        for r in _in_rows(
            conn,
            "SELECT position_id, category, settled_in_bin, direction, world_grade_pnl_usd "
            "FROM settlement_attribution WHERE position_id IN ({ph})",
            position_ids,
        )
    }


def _safe(fn: Callable[[], Any], default: Any, warnings: list[str], what: str) -> Any:
    try:
        return fn()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            warnings.append(f"{what}: {exc}")
            return default
        raise


# --------------------------------------------------------------------------
# Aggregation (pure)
# --------------------------------------------------------------------------
def build_report(
    td: Mapping[str, Any],
    listings: Mapping[str, dict],
    attribution: Mapping[str, dict],
    *,
    since: date,
    now: datetime,
    city_configs: Mapping[str, Any] | None,
    resolution_at: Callable[..., datetime] = _default_resolution_at,
    warnings: Sequence[str] = (),
) -> dict:
    since_s = since.isoformat()
    cov: Counter = Counter()
    cells: dict[tuple[str, str, str], Cell] = {}

    def cell(metric: str, tdate: str, bucket: str) -> Cell:
        return cells.setdefault((metric, tdate, bucket), Cell())

    # ---- ENTRY commands -------------------------------------------------
    entries: list[dict] = []
    by_cmd: dict[str, dict] = {}
    for e in td["entries"]:
        cond = e["snap_cond"] or e["pos_cond"]
        lst = listings.get(cond) if cond else None
        city, tdate, metric = (
            (lst["city"], lst["target_date"], lst["metric"]) if lst else (e["pos_city"], e["pos_date"], e["pos_metric"])
        )
        if not (city and tdate and metric):
            cov["entries_unattributed_no_family"] += 1
            continue
        if tdate < since_s:
            continue
        cov["entries_in_window"] += 1
        submitted = _utc(e["created_at"])
        listed = gamma_listed_at(lst["created_at_values"]) if lst else None
        age = market_age_hours(submitted, listed)
        if age is not None:
            cov["listing_known"] += 1
        elif not cond:
            cov["listing_unknown_no_condition"] += 1
        elif not lst:
            cov["listing_unknown_no_market_events_row"] += 1
        elif listed is None:
            cov["listing_unknown_non_gamma_clock"] += 1
        else:
            cov["listing_unknown_negative_age"] += 1
        try:
            res = resolution_at({}, (city, tdate, metric), city_configs=city_configs)
            lead = hours_between(res, submitted)
        except (ValueError, KeyError, TypeError):
            lead = None
        if lead is None:
            cov["settlement_lead_unknown"] += 1
        shares, notional, flagged = fill_totals(td["fills"].get(e["command_id"], ()), float(e["size"]))
        q = td["entry_q"].get(e["command_id"])
        rec = {
            "command_id": e["command_id"],
            "position_id": e["position_id"],
            "condition_id": cond,
            "city": city,
            "target_date": tdate,
            "metric": metric,
            "decision_time": e["created_at"],
            "state": e["state"],
            "size": float(e["size"]),
            "limit_price": float(e["price"]),
            "market_listed_at": listed.isoformat() if listed else None,
            "market_age_hours": None if age is None else round(age, 3),
            "age_bucket": age_bucket(age),
            "settlement_lead_hours": None if lead is None else round(lead, 3),
            "filled_shares": round(shares, 6),
            "filled_notional_usd": round(notional, 6),
            "acting_q": q,
            "_submitted": submitted,
        }
        entries.append(rec)
        by_cmd[rec["command_id"]] = rec
        c = cell(metric, tdate, rec["age_bucket"])
        c.entries_submitted += 1
        c.submitted_notional_usd += rec["size"] * rec["limit_price"]
        if lead is not None:
            c.lead_n += 1
            c.lead_hours_sum += lead
        c.fill_overshoot_flags += flagged
        if shares > 0:
            c.entries_filled += 1
            c.filled_shares += shares
            c.filled_notional_usd += notional
            if q is None:
                c.q_missing_fills += 1
            else:
                c.q_shares += shares
                c.q_x_shares += q * shares
                c.price_x_shares += notional

    # ---- first filled entry + every ENTRY fill per position -------------
    first_fill: dict[str, dict] = {}
    buys_by_pos: dict[str, list] = {}
    unverifiable_cmds = set(td.get("entry_unverifiable", ()))
    unverifiable_pos: set[str] = {e["position_id"] for e in td["entries"] if e["command_id"] in unverifiable_cmds}
    for rec in entries:
        if rec["command_id"] in unverifiable_cmds:
            cov["entry_commands_taker_unverifiable"] += 1
        if rec["filled_shares"] <= 0:
            continue
        pid = rec["position_id"]
        if pid not in first_fill or rec["_submitted"] < first_fill[pid]["_submitted"]:
            first_fill[pid] = rec
        buys_by_pos.setdefault(pid, []).extend(td["fills"].get(rec["command_id"], ()))
    floor_s = (since - timedelta(days=ENTRY_FLOOR_DAYS)).isoformat()
    window_pids = {p["position_id"] for p in td["positions"]}
    cov["positions_entry_history_read_beyond_floor"] = sum(
        1 for pid, rec in first_fill.items() if pid in window_pids and rec["decision_time"][:10] < floor_s
    )
    if not cov["positions_entry_history_read_beyond_floor"]:
        del cov["positions_entry_history_read_beyond_floor"]

    # ---- positions: outcome, exits, exposure ------------------------------
    exit_debt = set(td["exit_fill_debt"])
    equity: dict[str, Counter] = {}
    exit_rows: list[dict] = []
    for p in td["positions"]:
        pid, metric, tdate = p["position_id"], p["metric"], p["target_date"]
        ff = first_fill.get(pid)
        bucket = ff["age_bucket"] if ff else "unknown"
        if ff is None:
            cov["positions_without_filled_entry_in_window"] += 1
        c = cell(metric, tdate, bucket)
        c.positions += 1
        att = attribution.get(pid)
        phase = p["phase"]
        pnl = p["realized_pnl_usd"]
        if phase == "settled":
            c.settled += 1
            c.attribution[att["category"] if att else "UNGRADED"] += 1
            graded = att is not None and att["world_pnl"] is not None
            if graded:
                c.hold_to_settlement_n += 1
                c.hold_to_settlement_pnl_usd += att["world_pnl"]
            if pnl is None:
                c.pnl_null += 1
            else:
                if graded:
                    c.matched_n += 1
                    c.matched_realized_usd += pnl
                    c.matched_hold_to_settlement_usd += att["world_pnl"]
                else:
                    c.ungraded_settled_n += 1
                    c.ungraded_realized_pnl_usd += pnl
                c.realized_pnl_usd += pnl
                c.wins += pnl > 0
                c.losses += pnl < 0
                c.flat += pnl == 0
                day = (p["settled_at"] or "unknown")[:10]
                equity.setdefault(metric, Counter())[day] += pnl
        elif phase == "economically_closed":
            c.closed_unsettled += 1
            c.closed_unsettled_pnl_usd += pnl or 0.0
        else:
            c.open_positions += 1
            c.open_cost_usd += p["cost_basis_usd"] or 0.0
        # exits (sell legs), judged against settlement where known
        if pid in exit_debt:
            cov["exit_positions_economics_debt"] += 1
            continue
        sell_fills = td["exit_fills"].get(pid, ())
        if not sell_fills:
            continue
        sells = sell_costs(buys_by_pos.get(pid, ()), sell_fills, buys_unverifiable=pid in unverifiable_pos)
        shares = sum(s["shares"] for s in sells)
        proceeds = sum(s["proceeds_usd"] for s in sells)
        costed = [s for s in sells if s["cost_usd"] is not None]
        if any(s["unknown"] == "no_clock" for s in sells):
            cov["exit_positions_untimed_fills"] += 1
        if any(s["unknown"] == "unverifiable_entry_cost" for s in sells):
            cov["exit_positions_unverifiable_entry_cost"] += 1
        cost = sum(s["cost_usd"] for s in costed)
        costed_proceeds = sum(s["proceeds_usd"] for s in costed)
        reason = reason_head(
            td["exit_reasons"].get(pid) or (p["exit_reason"] if p["exit_reason"] != "SETTLEMENT" else None)
        )
        payoff = held_payoff(
            (att or {}).get("direction") or p["direction"], att["settled_in_bin"] if att else None
        )
        if payoff is None and phase == "settled" and p["settlement_price"] in (0, 1, 0.0, 1.0):
            payoff = float(p["settlement_price"])
        c.exits += 1
        c.exit_proceeds_usd += proceeds
        c.exit_reasons[reason] += 1
        c.exit_cost_unknown += len(sells) - len(costed)
        c.exit_cost_usd += cost
        c.exit_proceeds_costed_usd += costed_proceeds
        hold = None if payoff is None else shares * payoff
        if hold is not None:
            c.exits_resolved += 1
            c.resolved_sell_proceeds_usd += proceeds
            c.resolved_hold_value_usd += hold
            c.exits_hold_better += hold > proceeds
        exit_rows.append(
            {
                "position_id": pid,
                "metric": metric,
                "target_date": tdate,
                "age_bucket": bucket,
                "reason": reason,
                "exit_shares": round(shares, 6),
                "proceeds_usd": round(proceeds, 6),
                "cost_usd": round(cost, 6) if costed else None,
                "sells": [
                    {
                        "ts": s["ts"],
                        "shares": round(s["shares"], 6),
                        "proceeds_usd": round(s["proceeds_usd"], 6),
                        "cost_usd": None if s["cost_usd"] is None else round(s["cost_usd"], 6),
                        "unknown": s["unknown"],
                    }
                    for s in sorted(sells, key=lambda s: _utc(s["ts"]) or _EPOCH)
                ],
                "held_token_payoff": payoff,
                "hold_minus_sell_usd": None if hold is None else round(hold - proceeds, 6),
            }
        )

    # ---- keep revaluations ---------------------------------------------
    for r in td["revaluations"]:
        fam = r["family"]
        if len(fam) != 3 or str(fam[1]) < since_s:
            continue
        rec = by_cmd.get(r["command_id"])
        cell(fam[2], str(fam[1]), rec["age_bucket"] if rec else "unknown").revaluations[
            f"{r['action']}|{reason_head(r['reason'])}"
        ] += 1

    # ---- rollups ---------------------------------------------------------
    def rollup(keyfn) -> dict:
        out: dict[tuple, Cell] = {}
        for key, c in cells.items():
            out.setdefault(keyfn(key), Cell()).merge(c)
        return out

    order = {b: i for i, b in enumerate(AGE_BUCKETS)}
    by_bucket = rollup(lambda k: (k[0], k[2]))
    by_date = rollup(lambda k: (k[0], k[1]))
    total = rollup(lambda k: (k[0],))

    def rows(table: dict, names: tuple[str, ...], sort) -> list[dict]:
        return [
            {**dict(zip(names, key)), **table[key].summary()}
            for key in sorted(table, key=sort)
        ]

    eq_all: Counter = Counter()
    for byday in equity.values():
        eq_all.update(byday)
    equity_out = {}
    for name, byday in {**equity, "all": eq_all}.items():
        run = 0.0
        series = []
        for day in sorted(byday):
            run += byday[day]
            series.append({"settled_date": day, "pnl_usd": round(byday[day], 6), "cumulative_usd": round(run, 6)})
        equity_out[name] = series

    n_old, cost_old = td["open_older_than_window"]
    return {
        "schema": SCHEMA,
        "generated_at": now.isoformat(),
        "since_target_date": since_s,
        "entry_floor_date": (since - timedelta(days=ENTRY_FLOOR_DAYS)).isoformat(),
        "entry_floor_days": ENTRY_FLOOR_DAYS,
        "clocks": {
            "decision_time": "venue_commands.created_at of the ENTRY command",
            "market_listed_at": "min market_events.created_at (Gamma 'Z' createdAt) of the condition_id; null otherwise",
            "settlement_lead_hours": "selector horizon (local midnight ending target_date, city tz) - decision_time",
            "equity_date": "position_current.settled_at (UTC date), phase=settled, realized_pnl_usd not null",
            "realized_vs_hold_to_settlement": "realized_pnl_usd includes actual SELLs and covers every settled position; hold_to_settlement_pnl_usd (settlement_attribution.world_grade_pnl_usd) is the all-shares-held counterfactual over graded positions only. matched_realized_minus_hold_usd differences them over the matched cohort only (settled, realized not null, graded: matched_n); ungraded_realized_pnl_usd/ungraded_settled_n are reported separately and never enter it",
            "exit_cost": "per SELL: running-average cost of the inventory held at that fill (ENTRY and EXIT fills replayed by fill_dedup execution_ts); null when the loaded fills cannot account for the shares",
        },
        "coverage": dict(sorted(cov.items())),
        "warnings": list(warnings),
        "totals": rows(total, ("metric",), lambda k: k),
        "by_age_bucket": rows(by_bucket, ("metric", "age_bucket"), lambda k: (k[0], order[k[1]])),
        "by_target_date": rows(by_date, ("metric", "target_date"), lambda k: k),
        "cells": rows(cells, ("metric", "target_date", "age_bucket"), lambda k: (k[0], k[1], order[k[2]])),
        "exits": sorted(exit_rows, key=lambda r: (r["target_date"], r["position_id"])),
        "open_exposure_older_than_window": {"positions": n_old, "cost_usd": round(cost_old, 6)},
        "equity": equity_out,
        "entries": [
            {k: v for k, v in rec.items() if not k.startswith("_")}
            for rec in sorted(entries, key=lambda r: r["decision_time"])
        ],
    }


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------
def _f(v: Any, nd: int = 2) -> str:
    return "-" if v is None else f"{v:.{nd}f}"


def _table(rows: list[dict], first: Sequence[str]) -> list[str]:
    head = [*first, "sub", "fill", "fill$", "q", "px", "settled", "W/L/F", "pnl$", "exits", "exit pnl$", "hold-sell$", "open$"]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for r in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    *(str(r[k]) for k in first),
                    str(r["entries_submitted"]),
                    str(r["entries_filled"]),
                    _f(r["filled_notional_usd"]),
                    _f(r["mean_acting_q"], 3),
                    _f(r["mean_fill_price_on_q_fills"], 3),
                    str(r["settled"]),
                    f"{r['wins']}/{r['losses']}/{r['flat']}",
                    _f(r["realized_pnl_usd"]),
                    str(r["exits"]),
                    _f(r["exit_pnl_usd"]),
                    _f(r["hold_minus_sell_usd"]),
                    _f(r["open_cost_usd"]),
                ]
            )
            + " |"
        )
    return lines


def render_markdown(rep: Mapping[str, Any]) -> str:
    out = [
        f"# Multi-day evaluation (target_date >= {rep['since_target_date']})",
        f"generated {rep['generated_at']} | report only: no thresholds, no verdicts",
        "",
        "coverage: " + ", ".join(f"{k}={v}" for k, v in rep["coverage"].items()),
    ]
    out += [f"warning: {w}" for w in rep["warnings"]]
    out.append(
        "entry history: every window position's COMPLETE ENTRY command history is read, however old, so "
        "its cost and its age bucket (its FIRST filled ENTRY) never depend on a date cut "
        f"(coverage.positions_entry_history_read_beyond_floor counts those older than "
        f"{rep['entry_floor_date']}). The {ENTRY_FLOOR_DAYS}-day floor only bounds which other ENTRY commands "
        "feed the entry-level counts. A position with no filled ENTRY command in the DB at all (a chain-only "
        "holding, say) is bucketed 'unknown' (coverage.positions_without_filled_entry_in_window) and its "
        "sells cannot be costed (exit_cost_unknown)."
    )
    for metric in ("high", "low"):
        out += ["", f"## {metric.upper()} by market age at entry"]
        out += _table([r for r in rep["by_age_bucket"] if r["metric"] == metric], ["age_bucket"])
        out += ["", f"## {metric.upper()} by target_date"]
        out += _table([r for r in rep["by_target_date"] if r["metric"] == metric], ["target_date"])
    out += ["", "## Settlement attribution / exit reasons / keep revaluations"]
    for t in rep["totals"]:
        m = t["metric"].upper()
        out.append(
            f"- {m} realized ${_f(t['realized_pnl_usd'])} "
            f"(position_current, includes the actual SELLs; {t['settled']} settled)"
        )
        out.append(
            f"- {m} hold-to-settlement ${_f(t['hold_to_settlement_pnl_usd'])} "
            f"(settlement_attribution, every share held to settlement; {t['hold_to_settlement_n']} graded)"
        )
        out.append(
            f"- {m} exits' effect on the matched cohort: realized ${_f(t['matched_realized_usd'])} minus "
            f"hold-to-settlement ${_f(t['matched_hold_to_settlement_usd'])} = "
            f"${_f(t['matched_realized_minus_hold_usd'])} over {t['matched_n']} positions settled AND graded "
            f"(see the hold-sell$ column)"
        )
        out.append(
            f"- {m} ungraded settled, kept out of that difference: {t['ungraded_settled_n']} positions, "
            f"realized ${_f(t['ungraded_realized_pnl_usd'])}"
        )
        out.append(f"- {t['metric'].upper()} attribution: {t['attribution'] or '-'}")
        out.append(f"- {t['metric'].upper()} exit reasons: {t['exit_reasons'] or '-'}")
        out.append(f"- {t['metric'].upper()} revaluations: {t['revaluations'] or '-'}")
    oe = rep["open_exposure_older_than_window"]
    out += ["", f"open exposure with target_date before the window: {oe['positions']} positions, ${_f(oe['cost_usd'])}"]
    out += ["", "## Cumulative realized PnL by settlement date (all, last 14)"]
    out += [f"- {r['settled_date']}: {_f(r['pnl_usd'])} (cum {_f(r['cumulative_usd'])})" for r in rep["equity"]["all"][-14:]]
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------
ARTIFACT_NAMES = ("multiday_evaluation.md", "multiday_evaluation.json")
WRITE_LOCK_NAME = "multiday_evaluation.write.lock"


class OutputBusy(RuntimeError):
    """Another run holds the output directory's write lock; this run must not touch the artifacts."""


@contextlib.contextmanager
def _write_lock(out_dir: Path):
    """Exclusive, non-blocking flock on a fixed lock file for the whole cleanup + write phase.

    The artifacts use fixed temp names, so two runs writing at once (a CLI beside the daemon's
    child) can unlink or replace each other's ``.tmp`` and the first writer's os.replace then raises
    FileNotFoundError. One lock serializes the whole phase and is also what makes stale-``.tmp``
    cleanup safe: while it is held no other run is between write_text and replace, so any ``.tmp``
    present belongs to a run that died. flock is advisory, released by the kernel when the holder
    exits or is killed (watchdog, SIGKILL included), and never leaves a stale lock to clear.
    Contention raises OutputBusy at once instead of waiting; the lock file itself is never removed
    (removing it would let two runs lock different inodes).
    """
    fd = os.open(out_dir / WRITE_LOCK_NAME, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK):
                raise OutputBusy(f"{out_dir / WRITE_LOCK_NAME} is held by another run") from exc
            raise
        yield
    finally:
        os.close(fd)      # closing the descriptor releases the flock


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def clear_stale_tmp(out_dir: Path) -> list[str]:
    """Unlink the fixed-name ``.tmp`` of each artifact left by a killed run; returns what it removed.

    Call it only while holding ``_write_lock``: without it a concurrent run's live ``.tmp`` is
    indistinguishable from a dead run's, and unlinking it makes that run's os.replace fail.

    The faulthandler watchdog (and a parent SIGKILL) exit without running ``finally`` or atexit,
    so a kill between ``write_text`` and ``os.replace`` leaves at most one ``<artifact>.tmp`` per
    artifact. The next write would overwrite it anyway, so nothing accumulates; this just removes
    it at the start of the next run instead of leaving it until then. Only the two exact names
    this script writes are touched.
    """
    removed = []
    for name in ARTIFACT_NAMES:
        tmp = out_dir / (name + ".tmp")
        try:
            tmp.unlink()
            removed.append(tmp.name)
        except FileNotFoundError:
            pass
        except OSError:
            logger.warning("could not remove stale %s", tmp, exc_info=True)
    return removed


def run_multiday_evaluation(
    *,
    since: date | None = None,
    now: datetime | None = None,
    trades_db: Path | str | None = None,
    forecasts_db: Path | str | None = None,
    world_db: Path | str | None = None,
    out_dir: Path | str | None = None,
    write: bool = True,
    city_configs: Mapping[str, Any] | None = None,
) -> dict:
    from src.config import STATE_DIR, runtime_cities_by_name
    from src.state.db import ZEUS_FORECASTS_DB_PATH, ZEUS_WORLD_DB_PATH, _zeus_trade_db_path

    now = now or datetime.now(timezone.utc)
    since = since or (now.date() - timedelta(days=DEFAULT_DAYS))
    warnings: list[str] = []
    started = last = time.monotonic()

    def lap(phase: str) -> None:
        nonlocal last
        t = time.monotonic()
        logger.info("phase=%s phase_s=%.2f total_s=%.2f", phase, t - last, t - started)
        last = t

    conn = _open_ro(trades_db or _zeus_trade_db_path())
    try:
        td = load_trades_data(conn, since)
    finally:
        conn.close()
    lap("trades")
    conds = {e["snap_cond"] or e["pos_cond"] for e in td["entries"]} - {None}
    conn = _open_ro(forecasts_db or ZEUS_FORECASTS_DB_PATH)
    try:
        listings = _safe(lambda: load_listings(conn, conds), {}, warnings, "forecasts.market_events")
    finally:
        conn.close()
    lap("forecasts")
    conn = _open_ro(world_db or ZEUS_WORLD_DB_PATH)
    try:
        attribution = _safe(
            lambda: load_attribution(conn, [p["position_id"] for p in td["positions"]]),
            {},
            warnings,
            "world.settlement_attribution",
        )
    finally:
        conn.close()
    lap("world")

    report = build_report(
        td,
        listings,
        attribution,
        since=since,
        now=now,
        city_configs=city_configs if city_configs is not None else runtime_cities_by_name(),
        warnings=warnings,
    )
    report["markdown"] = render_markdown(report)
    lap("build")
    if write:
        out = Path(out_dir) if out_dir else Path(STATE_DIR)
        out.mkdir(parents=True, exist_ok=True)
        with _write_lock(out):
            stale = clear_stale_tmp(out)
            if stale:
                logger.warning("removed stale temp artifacts from a killed run: %s", stale)
            _atomic_write(out / "multiday_evaluation.md", report["markdown"])
            body = {k: v for k, v in report.items() if k != "markdown"}
            _atomic_write(out / "multiday_evaluation.json", json.dumps(body, indent=1))
        lap("write")
    return report


def main(argv: Sequence[str] | None = None, *, watchdog: bool = False) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--since", type=date.fromisoformat, help="target_date lower bound (default: today - 14d)")
    ap.add_argument("--trades-db")
    ap.add_argument("--forecasts-db")
    ap.add_argument("--world-db")
    ap.add_argument("--out-dir", help="default: the runtime state dir")
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--quiet", action="store_true", help="do not print the markdown (the scheduler child)")
    ap.add_argument("--deadline-s", type=float, default=CHILD_DEADLINE_S,
                    help="self-imposed wall-clock limit of a CLI run, in seconds (default %(default)s)")
    args = ap.parse_args(argv)
    if watchdog:
        # faulthandler's watchdog is a C thread that needs no GIL, so it fires even while the
        # main thread is blocked inside a sqlite lock wait (a Python SIGALRM handler would
        # not run until that C call returned). It dumps every thread's stack to stderr and
        # exits the process with status 1, with no parent involved.
        faulthandler.dump_traceback_later(args.deadline_s, exit=True)
        logger.info("watchdog armed deadline_s=%.0f", args.deadline_s)
    try:
        report = run_multiday_evaluation(
            since=args.since,
            trades_db=args.trades_db,
            forecasts_db=args.forecasts_db,
            world_db=args.world_db,
            out_dir=args.out_dir,
            write=not args.no_write,
        )
    except OutputBusy as exc:
        logger.warning("output busy, no report this cadence: %s", exc)
        return EXIT_DB_BUSY
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            logger.warning("database busy, no report this cadence: %s", exc)
            return EXIT_DB_BUSY
        raise
    finally:
        if watchdog:
            faulthandler.cancel_dump_traceback_later()
    logger.info(
        "done since=%s entries=%s coverage=%s warnings=%s",
        report["since_target_date"], len(report["entries"]), report["coverage"], report["warnings"],
    )
    if not args.quiet:
        sys.stdout.write(report["markdown"])
    return 0


if __name__ == "__main__":
    # A bare child has no logging setup, so its lines would reach stderr as untimestamped
    # lastResort text; give it the daemon's own format.
    logging.basicConfig(
        level=logging.INFO, stream=sys.stderr,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    try:
        os.nice(10)  # a background report must never outrank the trading daemon
    except OSError:
        pass
    raise SystemExit(main(watchdog=True))

#!/usr/bin/env python3
# Created: 2026-10-05
# Last audited: 2026-10-05
# Authority basis: operator law 2026-10-05 ("只添加记录 ... 执行真正的多日交易与持续评估"):
#   records and continuous evaluation only; no calibration, proof study, gate or
#   trade-affecting change. Provenance audit 2026-10-05: reuses the selector's own
#   resolution horizon (src.engine.global_auction_universe._payload_resolution_at_utc,
#   CURRENT) and the venue_trade_facts dedup law (tx_hash identity, CONFIRMED over
#   MATCHED, sum(fills) <= 1.05 x command size else flagged).
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

Units: ENTRY commands are bucketed by their own market age. Positions (outcome,
exits, exposure) are bucketed by the age of their first filled ENTRY command.
Filtering is by target_date >= --since; open exposure older than the window is
reported as a scalar.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field, fields
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SCHEMA = "multiday_evaluation.v1"
AGE_BUCKETS = ("<12h", "12-24h", "24-48h", ">=48h", "unknown")
OPEN_PHASES = ("active", "day0_window", "pending_exit")
POSITION_PHASES = OPEN_PHASES + ("economically_closed", "settled")
# Markets list <= ~3 days before target_date (observed max entry lead 2 d).
ENTRY_FLOOR_DAYS = 7
DEFAULT_DAYS = 14
FILL_OVERSHOOT = 1.05
_FILL_RANK = {"MATCHED": 1, "MINED": 2, "CONFIRMED": 3}
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


def dedup_fills(rows: Iterable[Sequence[Any]], command_size: float) -> tuple[float, float, bool]:
    """(shares, notional_usd, overshoot_flag) from venue_trade_facts rows.

    Rows are (state, filled_size, fill_price, tx_hash, observed_at, local_sequence).
    The same physical fill is re-observed under several trade_ids and prices, so
    identity is the 66-char tx_hash (round(size, 6) per command when absent) and
    the best-state, latest observation wins. FAILED/RETRYING never count.
    """
    best: dict[Any, tuple[tuple, float, float]] = {}
    for state, size, price, tx, observed_at, seq in rows:
        rank = _FILL_RANK.get(state)
        if rank is None:
            continue
        shares = float(size)
        key = tx if isinstance(tx, str) and tx.startswith("0x") and len(tx) == 66 else ("size", round(shares, 6))
        cand = ((rank, observed_at or "", seq or 0), shares, float(price))
        if key not in best or cand[0] > best[key][0]:
            best[key] = cand
    shares = sum(b[1] for b in best.values())
    notional = sum(b[1] * b[2] for b in best.values())
    return shares, notional, command_size > 0 and shares > FILL_OVERSHOOT * command_size


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
    world_grade_pnl_usd: float = 0.0   # settlement_attribution cross-check of realized_pnl_usd
    world_grade_n: int = 0
    closed_unsettled: int = 0
    closed_unsettled_pnl_usd: float = 0.0
    exits: int = 0
    exit_proceeds_usd: float = 0.0
    exit_cost_usd: float = 0.0
    exit_cost_unknown: int = 0
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
        out["exit_pnl_usd"] = round(self.exit_proceeds_usd - self.exit_cost_usd, 6)
        out["hold_minus_sell_usd"] = round(self.resolved_hold_value_usd - self.resolved_sell_proceeds_usd, 6)
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


def load_trades_data(conn, since: date) -> dict:
    import sqlite3

    conn.row_factory = sqlite3.Row
    since_s = since.isoformat()
    floor = (since - timedelta(days=ENTRY_FLOOR_DAYS)).isoformat()
    phases = ",".join(f"'{p}'" for p in POSITION_PHASES)
    td: dict[str, Any] = {}
    td["entries"] = [
        dict(r)
        for r in conn.execute(
            """
            SELECT c.command_id, c.position_id, c.state, c.size, c.price, c.created_at,
                   s.condition_id AS snap_cond, p.condition_id AS pos_cond,
                   p.city AS pos_city, p.target_date AS pos_date,
                   p.temperature_metric AS pos_metric
            FROM venue_commands c
            LEFT JOIN executable_market_snapshots s ON s.snapshot_id = c.snapshot_id
            LEFT JOIN position_current p ON p.position_id = c.position_id
            WHERE c.intent_kind = 'ENTRY' AND c.created_at >= ?
            """,
            (floor,),
        )
    ]
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
    td["exit_cmds"] = [
        dict(r)
        for r in _in_rows(
            conn,
            "SELECT command_id, position_id, size FROM venue_commands "
            "WHERE intent_kind = 'EXIT' AND position_id IN ({ph})",
            pids,
        )
    ]
    fill_cmds = [e["command_id"] for e in td["entries"]] + [c["command_id"] for c in td["exit_cmds"]]
    fills: dict[str, list] = {}
    for r in _in_rows(
        conn,
        "SELECT command_id, state, filled_size, fill_price, tx_hash, observed_at, local_sequence "
        "FROM venue_trade_facts WHERE command_id IN ({ph})",
        fill_cmds,
    ):
        fills.setdefault(r[0], []).append(tuple(r)[1:])
    td["fills"] = fills
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
    # One equality probe per event type: ``event_type IN (...)`` makes the planner
    # scan the position_id autoindex and read every MONITOR_REFRESHED payload (~45 s
    # live); equality rides idx_position_events_position_type_sequence (~0.1 s).
    reasons: dict[str, str] = {}
    exit_pids = [c["position_id"] for c in td["exit_cmds"]]
    for event_type in ("EXIT_INTENT", "EXIT_ORDER_POSTED", "EXIT_ORDER_FILLED"):
        for r in _in_rows(
            conn,
            "SELECT position_id, json_extract(payload_json, '$.exit_reason') FROM position_events "
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
    import sqlite3

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
        shares, notional, flagged = dedup_fills(td["fills"].get(e["command_id"], ()), float(e["size"]))
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

    # ---- first filled entry + average entry price per position ----------
    first_fill: dict[str, dict] = {}
    entry_px: dict[str, list[float]] = {}
    for rec in entries:
        if rec["filled_shares"] <= 0:
            continue
        pid = rec["position_id"]
        if pid not in first_fill or rec["_submitted"] < first_fill[pid]["_submitted"]:
            first_fill[pid] = rec
        acc = entry_px.setdefault(pid, [0.0, 0.0])
        acc[0] += rec["filled_notional_usd"]
        acc[1] += rec["filled_shares"]

    # ---- positions: outcome, exits, exposure ------------------------------
    exits_by_pos: dict[str, list[dict]] = {}
    for x in td["exit_cmds"]:
        exits_by_pos.setdefault(x["position_id"], []).append(x)
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
            if att and att["world_pnl"] is not None:
                c.world_grade_n += 1
                c.world_grade_pnl_usd += att["world_pnl"]
            if pnl is None:
                c.pnl_null += 1
            else:
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
        shares = proceeds = 0.0
        for x in exits_by_pos.get(pid, ()):
            s, n, _ = dedup_fills(td["fills"].get(x["command_id"], ()), float(x["size"]))
            shares += s
            proceeds += n
        if shares <= 0:
            continue
        reason = reason_head(
            td["exit_reasons"].get(pid) or (p["exit_reason"] if p["exit_reason"] != "SETTLEMENT" else None)
        )
        avg = entry_px[pid][0] / entry_px[pid][1] if pid in entry_px else p["entry_price"]
        cost = None if avg is None else shares * float(avg)
        payoff = held_payoff(
            (att or {}).get("direction") or p["direction"], att["settled_in_bin"] if att else None
        )
        if payoff is None and phase == "settled" and p["settlement_price"] in (0, 1, 0.0, 1.0):
            payoff = float(p["settlement_price"])
        c.exits += 1
        c.exit_proceeds_usd += proceeds
        c.exit_reasons[reason] += 1
        if cost is None:
            c.exit_cost_unknown += 1
        else:
            c.exit_cost_usd += cost
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
                "cost_usd": None if cost is None else round(cost, 6),
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
        "clocks": {
            "decision_time": "venue_commands.created_at of the ENTRY command",
            "market_listed_at": "min market_events.created_at (Gamma 'Z' createdAt) of the condition_id; null otherwise",
            "settlement_lead_hours": "selector horizon (local midnight ending target_date, city tz) - decision_time",
            "equity_date": "position_current.settled_at (UTC date), phase=settled, realized_pnl_usd not null",
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
    for metric in ("high", "low"):
        out += ["", f"## {metric.upper()} by market age at entry"]
        out += _table([r for r in rep["by_age_bucket"] if r["metric"] == metric], ["age_bucket"])
        out += ["", f"## {metric.upper()} by target_date"]
        out += _table([r for r in rep["by_target_date"] if r["metric"] == metric], ["target_date"])
    out += ["", "## Settlement attribution / exit reasons / keep revaluations"]
    for t in rep["totals"]:
        out.append(
            f"- {t['metric'].upper()} realized pnl ${_f(t['realized_pnl_usd'])} (position_current, "
            f"{t['settled']} settled) vs world-grade ${_f(t['world_grade_pnl_usd'])} "
            f"(settlement_attribution, {t['world_grade_n']} graded)"
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
def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


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

    conn = _open_ro(trades_db or _zeus_trade_db_path())
    try:
        td = load_trades_data(conn, since)
    finally:
        conn.close()
    conds = {e["snap_cond"] or e["pos_cond"] for e in td["entries"]} - {None}
    conn = _open_ro(forecasts_db or ZEUS_FORECASTS_DB_PATH)
    try:
        listings = _safe(lambda: load_listings(conn, conds), {}, warnings, "forecasts.market_events")
    finally:
        conn.close()
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
    if write:
        out = Path(out_dir) if out_dir else Path(STATE_DIR)
        out.mkdir(parents=True, exist_ok=True)
        _atomic_write(out / "multiday_evaluation.md", report["markdown"])
        body = {k: v for k, v in report.items() if k != "markdown"}
        _atomic_write(out / "multiday_evaluation.json", json.dumps(body, indent=1))
    return report


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--since", type=date.fromisoformat, help="target_date lower bound (default: today - 14d)")
    ap.add_argument("--trades-db")
    ap.add_argument("--forecasts-db")
    ap.add_argument("--world-db")
    ap.add_argument("--out-dir", help="default: the runtime state dir")
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args(argv)
    report = run_multiday_evaluation(
        since=args.since,
        trades_db=args.trades_db,
        forecasts_db=args.forecasts_db,
        world_db=args.world_db,
        out_dir=args.out_dir,
        write=not args.no_write,
    )
    sys.stdout.write(report["markdown"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

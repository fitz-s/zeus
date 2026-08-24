# Created: 2026-08-24
# Last reused or audited: 2026-08-24
# Authority basis: docs/operations/current/plans/reversal_plan_tier0_2026-08-24.md
#   item 4 ("Four non-circular panels (forecast/selection/execution/lifecycle)").
#   The old settlement_attribution.category scoreboard is circular (grades our
#   own q against itself via UNATTRIBUTABLE_Q_MISSING/SKILL_WIN/... labels).
#   This script replaces it with four panels computed against price and
#   settlement truth directly, never against our own q as the yardstick
#   (except P1, which explicitly PAIRS q against price to show the deficit).
"""Read-only non-circular scoreboard: forecast / selection / execution / lifecycle.

ANALYTICS ONLY. Opens state/zeus-world.db and state/zeus_trades.db strictly
read-only (``sqlite3`` URI ``mode=ro&immutable=0``) and prints compact
markdown tables to stdout. Never writes to any DB, never authorizes a live
decision, never mutates canonical truth.

Four panels:

  P1 FORECAST   — paired proper scores of q vs market price on settled
                  settlement_attribution rows, per month and per |q-p| bucket.
  P2 SELECTION  — price-only frequency edge (no q needed) vs own side price,
                  per price band and per month, with two-way (city-date and
                  date-only) clustered standard errors; the larger is reported
                  as se_gate per the plan's "use the larger uncertainty" law.
  P3 EXECUTION  — fill quality (latency, taker/maker proxy, slippage) from
                  trades DB venue_commands/venue_trade_facts, after the
                  mandatory two-stage fill dedup law (state-collapse per
                  trade_id, then drop 0x-placeholder rows that double-count an
                  already-confirmed UUID trade_id sibling on the same command).
  P4 LIFECYCLE  — EXIT command state distribution per month, plus cheap/convex
                  (entry avg price < 0.25) held-to-settlement vs early-exited
                  split and pooled exit_proceeds/entry_cost ratio.

Every panel reports raw n, cluster n, and an explicit coverage line for
excluded rows. No panel silently drops rows.
"""

from __future__ import annotations

import argparse
import math
import re
import sqlite3
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

# ---------------------------------------------------------------------------
# DB access — strictly read-only.
# ---------------------------------------------------------------------------


def open_ro(path: Path) -> sqlite3.Connection:
    """Open a sqlite DB strictly read-only via URI (mode=ro&immutable=0).

    immutable=0 (not 1): these are live-updating DBs even though this script
    never writes to them; asserting immutable=1 on a file that may change
    underneath a long-lived connection risks stale-cache reads.
    """
    uri = f"file:{path}?mode=ro&immutable=0"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Shared math helpers.
# ---------------------------------------------------------------------------

_CLIP_LO = 0.005
_CLIP_HI = 0.995


def clip(p: float) -> float:
    return min(max(p, _CLIP_LO), _CLIP_HI)


def logloss(y: int, p: float) -> float:
    p = clip(p)
    return -(y * math.log(p) + (1 - y) * math.log(1.0 - p))


def brier(y: int, p: float) -> float:
    return (clip(p) - y) ** 2


_ISO_MONTH_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def month_of(iso_ts: str | None) -> str:
    """YYYY-MM prefix of an ISO8601 timestamp, or 'UNKNOWN' for anything else.

    Some legacy venue_commands rows (e.g. adopted_exit_* CANCELLED/EXPIRED
    entries) carry a bare unix-epoch-seconds string in created_at instead of
    ISO8601. Naively slicing such a string produces a bogus fake "month" (e.g.
    "1782750") rather than failing loud, so it is explicitly detected and
    bucketed as UNKNOWN instead — visible in output, never silently merged
    into a real month.
    """
    if not iso_ts:
        return "UNKNOWN"
    s = str(iso_ts)
    if _ISO_MONTH_RE.match(s):
        return s[:7]
    return "UNKNOWN"


def cluster_key(city: str | None, target_date: str | None) -> str:
    return f"{city or 'UNKNOWN_CITY'}|{target_date or 'UNKNOWN_DATE'}"


def parse_dt(iso_ts: str) -> datetime:
    return datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))


def clustered_se(diffs_by_cluster: dict[str, list[float]]) -> tuple[float | None, int]:
    """Cluster-mean SE: std(per-cluster mean diff) / sqrt(n_clusters).

    Returns (se, n_clusters). se is None when fewer than 2 clusters (sample
    stdev undefined).
    """
    means = [statistics.mean(v) for v in diffs_by_cluster.values() if v]
    n_clusters = len(means)
    if n_clusters < 2:
        return None, n_clusters
    se = statistics.stdev(means) / math.sqrt(n_clusters)
    return se, n_clusters


def q_p_bucket(diff: float) -> str:
    if diff < 0.15:
        return "<0.15"
    if diff < 0.30:
        return "0.15-0.30"
    if diff < 0.50:
        return "0.30-0.50"
    return ">0.50"


def price_band(p: float) -> str:
    if p < 0.10:
        return "<0.10"
    if p < 0.25:
        return "0.10-0.25"
    if p < 0.45:
        return "0.25-0.45"
    if p < 0.65:
        return "0.45-0.65"
    return ">=0.65"


# ---------------------------------------------------------------------------
# Fill dedup law (mandatory, plan §"Key consult corrections adopted" /
# item 2 canonical economic-fill read model; applied here for P3/P4).
# ---------------------------------------------------------------------------

_STATE_RANK = {"FAILED": 0, "RETRYING": 1, "MATCHED": 2, "MINED": 3, "CONFIRMED": 4}


@dataclass
class DedupResult:
    kept: list[dict[str, Any]]
    stage1_state_collapsed: int  # extra observations of the SAME trade_id folded
    stage2_placeholder_dropped: int  # 0x rows dropped as UUID-trade duplicates


def dedup_fill_facts(rows: list[dict[str, Any]]) -> DedupResult:
    """Two-stage fill dedup law.

    Stage 1: per trade_id, keep the single row with the best lifecycle state
    (CONFIRMED > MINED > MATCHED > RETRYING > FAILED); ties broken by latest
    observed_at. Collapses repeat observations of the same physical fill.

    Stage 2: within each command_id, drop any 0x-prefixed trade_id row that
    has a same-command_id sibling whose trade_id is NOT 0x-prefixed and whose
    CAST(filled_size AS REAL) matches (within float tolerance) — the venue
    emits a transient 0x placeholder trade_id before the real (UUID or
    edli:-prefixed) trade_id is confirmed; without this the same economic
    fill is double counted.
    """
    by_trade: dict[str, dict[str, Any]] = {}
    raw_count = len(rows)
    for row in rows:
        key = row["trade_id"]
        cur = by_trade.get(key)
        if cur is None:
            by_trade[key] = row
            continue
        cur_rank = (_STATE_RANK.get(cur["state"], -1), cur["observed_at"])
        new_rank = (_STATE_RANK.get(row["state"], -1), row["observed_at"])
        if new_rank > cur_rank:
            by_trade[key] = row
    stage1_rows = list(by_trade.values())
    stage1_collapsed = raw_count - len(stage1_rows)

    by_command: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in stage1_rows:
        by_command[row["command_id"]].append(row)

    kept: list[dict[str, Any]] = []
    stage2_dropped = 0
    for group in by_command.values():
        non_placeholder_sizes = {
            round(float(r["filled_size"]), 6)
            for r in group
            if not str(r["trade_id"]).startswith("0x")
        }
        for row in group:
            trade_id = str(row["trade_id"])
            size = round(float(row["filled_size"]), 6)
            if trade_id.startswith("0x") and size in non_placeholder_sizes:
                stage2_dropped += 1
                continue
            kept.append(row)

    return DedupResult(kept=kept, stage1_state_collapsed=stage1_collapsed, stage2_placeholder_dropped=stage2_dropped)


def _fetch_trade_facts_for_commands(conn: sqlite3.Connection, command_ids: list[str]) -> list[dict[str, Any]]:
    """Batched IN-clause fetch (sqlite default variable limit is 999)."""
    out: list[dict[str, Any]] = []
    batch = 400
    for i in range(0, len(command_ids), batch):
        chunk = command_ids[i : i + batch]
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"""
            SELECT trade_id, command_id, state, filled_size, fill_price, observed_at
            FROM venue_trade_facts
            WHERE command_id IN ({placeholders})
            """,
            chunk,
        ).fetchall()
        out.extend(dict(r) for r in rows)
    return out


# ---------------------------------------------------------------------------
# P1 FORECAST — paired proper scores of q vs price on settled events.
# ---------------------------------------------------------------------------


@dataclass
class GroupStats:
    n: int = 0
    clusters: int = 0
    logloss_q: float | None = None
    logloss_p: float | None = None
    brier_q: float | None = None
    brier_p: float | None = None
    q_beats_p_share: float | None = None


def compute_panel1(conn_world: sqlite3.Connection) -> dict[str, Any]:
    rows = conn_world.execute(
        """
        SELECT city, target_date, q_in_bin, market_in_bin_prob, settled_in_bin,
               settled_at, graded_at
        FROM settlement_attribution
        """
    ).fetchall()

    total = len(rows)
    excluded_null_q = 0
    excluded_null_p = 0
    excluded_null_y = 0
    usable: list[dict[str, Any]] = []
    for r in rows:
        q, p, y = r["q_in_bin"], r["market_in_bin_prob"], r["settled_in_bin"]
        if q is None:
            excluded_null_q += 1
            continue
        if p is None:
            excluded_null_p += 1
            continue
        if y is None:
            excluded_null_y += 1
            continue
        month = month_of(r["settled_at"] or r["graded_at"])
        usable.append(
            {
                "city": r["city"],
                "target_date": r["target_date"],
                "q": float(q),
                "p": float(p),
                "y": int(y),
                "month": month,
                "bucket": q_p_bucket(abs(float(q) - float(p))),
            }
        )

    monthly: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    pooled: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for u in usable:
        monthly[(u["month"], u["bucket"])].append(u)
        pooled[u["bucket"]].append(u)

    def summarize(group: list[dict[str, Any]]) -> GroupStats:
        n = len(group)
        clusters = len({cluster_key(u["city"], u["target_date"]) for u in group})
        if n == 0:
            return GroupStats(n=0, clusters=0)
        ll_q = statistics.mean(logloss(u["y"], u["q"]) for u in group)
        ll_p = statistics.mean(logloss(u["y"], u["p"]) for u in group)
        br_q = statistics.mean(brier(u["y"], u["q"]) for u in group)
        br_p = statistics.mean(brier(u["y"], u["p"]) for u in group)
        beats = sum(1 for u in group if logloss(u["y"], u["q"]) < logloss(u["y"], u["p"]))
        return GroupStats(
            n=n,
            clusters=clusters,
            logloss_q=ll_q,
            logloss_p=ll_p,
            brier_q=br_q,
            brier_p=br_p,
            q_beats_p_share=beats / n,
        )

    monthly_out = {key: summarize(group) for key, group in sorted(monthly.items())}
    pooled_out = {bucket: summarize(group) for bucket, group in pooled.items()}
    grand = summarize(usable)

    return {
        "total_rows": total,
        "usable_rows": len(usable),
        "excluded_null_q_in_bin": excluded_null_q,
        "excluded_null_market_in_bin_prob": excluded_null_p,
        "excluded_null_settled_in_bin": excluded_null_y,
        "monthly": monthly_out,
        "pooled_by_bucket": pooled_out,
        "grand_total": grand,
    }


# ---------------------------------------------------------------------------
# P2 SELECTION — price-only frequency edge vs own side price.
# ---------------------------------------------------------------------------


@dataclass
class SelectionStats:
    n: int = 0
    clusters: int = 0
    mean_p: float | None = None
    mean_y: float | None = None
    edge: float | None = None
    se_city_date: float | None = None
    n_clusters_city_date: int = 0
    se_date: float | None = None
    n_clusters_date: int = 0
    se_gate: float | None = None


def compute_panel2(conn_world: sqlite3.Connection) -> dict[str, Any]:
    rows = conn_world.execute(
        """
        SELECT city, target_date, direction, avg_fill_price, settled_in_bin, settled_at, graded_at
        FROM settlement_attribution
        """
    ).fetchall()

    total = len(rows)
    excluded_null_direction = 0
    excluded_null_price = 0
    excluded_null_y = 0
    usable: list[dict[str, Any]] = []
    for r in rows:
        direction, price, y = r["direction"], r["avg_fill_price"], r["settled_in_bin"]
        if direction is None:
            excluded_null_direction += 1
            continue
        if price is None:
            excluded_null_price += 1
            continue
        if y is None:
            excluded_null_y += 1
            continue
        settled_in_bin = int(y)
        side_win = settled_in_bin if direction == "buy_yes" else (1 - settled_in_bin)
        month = month_of(r["settled_at"] or r["graded_at"])
        usable.append(
            {
                "city": r["city"],
                "target_date": r["target_date"],
                "p": float(price),
                "y": side_win,
                "month": month,
                "band": price_band(float(price)),
            }
        )

    monthly: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    pooled: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for u in usable:
        monthly[(u["month"], u["band"])].append(u)
        pooled[u["band"]].append(u)

    def summarize(group: list[dict[str, Any]]) -> SelectionStats:
        n = len(group)
        clusters = len({cluster_key(u["city"], u["target_date"]) for u in group})
        if n == 0:
            return SelectionStats(n=0, clusters=0)
        mean_p = statistics.mean(u["p"] for u in group)
        mean_y = statistics.mean(u["y"] for u in group)
        edge = mean_y - mean_p

        by_city_date: dict[str, list[float]] = defaultdict(list)
        by_date: dict[str, list[float]] = defaultdict(list)
        for u in group:
            diff = u["y"] - u["p"]
            by_city_date[cluster_key(u["city"], u["target_date"])].append(diff)
            by_date[u["target_date"] or "UNKNOWN_DATE"].append(diff)

        se_cd, n_cd = clustered_se(by_city_date)
        se_d, n_d = clustered_se(by_date)
        if se_cd is None:
            se_gate = se_d
        elif se_d is None:
            se_gate = se_cd
        else:
            se_gate = max(se_cd, se_d)

        return SelectionStats(
            n=n,
            clusters=clusters,
            mean_p=mean_p,
            mean_y=mean_y,
            edge=edge,
            se_city_date=se_cd,
            n_clusters_city_date=n_cd,
            se_date=se_d,
            n_clusters_date=n_d,
            se_gate=se_gate,
        )

    monthly_out = {key: summarize(group) for key, group in sorted(monthly.items())}
    pooled_out = {band: summarize(group) for band, group in pooled.items()}
    grand = summarize(usable)

    return {
        "total_rows": total,
        "usable_rows": len(usable),
        "excluded_null_direction": excluded_null_direction,
        "excluded_null_avg_fill_price": excluded_null_price,
        "excluded_null_settled_in_bin": excluded_null_y,
        "monthly": monthly_out,
        "pooled_by_band": pooled_out,
        "grand_total": grand,
    }


# ---------------------------------------------------------------------------
# P3 EXECUTION — fill quality.
# ---------------------------------------------------------------------------

_TAKER_LATENCY_S = 5.0
_MAKER_LATENCY_S = 120.0


@dataclass
class ExecutionMonthStats:
    n_fills: int = 0
    n_commands: int = 0
    dedup_stage1_collapsed: int = 0
    dedup_stage2_dropped: int = 0
    median_latency_s: float | None = None
    p90_latency_s: float | None = None
    share_taker_lt5s: float | None = None
    share_maker_gt120s: float | None = None
    avg_slippage_taker: float | None = None
    avg_slippage_maker: float | None = None
    avg_slippage_mid: float | None = None


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        raise ValueError("empty")
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * pct
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_vals[int(k)]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def compute_panel3(conn_trades: sqlite3.Connection) -> dict[str, Any]:
    entries = [
        dict(r)
        for r in conn_trades.execute(
            """
            SELECT command_id, position_id, created_at, price
            FROM venue_commands
            WHERE intent_kind = 'ENTRY' AND state = 'FILLED'
            """
        ).fetchall()
    ]
    command_ids = [e["command_id"] for e in entries]
    raw_facts = _fetch_trade_facts_for_commands(conn_trades, command_ids)
    dedup = dedup_fill_facts(raw_facts)

    facts_by_command: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for f in dedup.kept:
        facts_by_command[f["command_id"]].append(f)

    entries_no_fills = 0
    entries_unparsable_ts = 0

    monthly_raw: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: {"latency": [], "slip_taker": [], "slip_maker": [], "slip_mid": []}
    )
    monthly_fill_count: dict[str, int] = defaultdict(int)
    monthly_command_count: dict[str, int] = defaultdict(int)

    for e in entries:
        cmd_id = e["command_id"]
        facts = facts_by_command.get(cmd_id)
        month = month_of(e["created_at"])
        if not facts:
            entries_no_fills += 1
            continue
        try:
            created_dt = parse_dt(e["created_at"])
            first_fill_dt = min(parse_dt(f["observed_at"]) for f in facts)
        except ValueError:
            entries_unparsable_ts += 1
            continue

        latency_s = (first_fill_dt - created_dt).total_seconds()
        monthly_raw[month]["latency"].append(latency_s)
        monthly_command_count[month] += 1
        monthly_fill_count[month] += len(facts)

        if latency_s < _TAKER_LATENCY_S:
            klass = "slip_taker"
        elif latency_s > _MAKER_LATENCY_S:
            klass = "slip_maker"
        else:
            klass = "slip_mid"
        for f in facts:
            slip = float(f["fill_price"]) - float(e["price"])
            monthly_raw[month][klass].append(slip)

    monthly_out: dict[str, ExecutionMonthStats] = {}
    for month, agg in monthly_raw.items():
        lat = sorted(agg["latency"])
        n_cmd = monthly_command_count[month]
        monthly_out[month] = ExecutionMonthStats(
            n_fills=monthly_fill_count[month],
            n_commands=n_cmd,
            median_latency_s=_percentile(lat, 0.5) if lat else None,
            p90_latency_s=_percentile(lat, 0.9) if lat else None,
            share_taker_lt5s=(sum(1 for x in lat if x < _TAKER_LATENCY_S) / n_cmd) if n_cmd else None,
            share_maker_gt120s=(sum(1 for x in lat if x > _MAKER_LATENCY_S) / n_cmd) if n_cmd else None,
            avg_slippage_taker=(statistics.mean(agg["slip_taker"]) if agg["slip_taker"] else None),
            avg_slippage_maker=(statistics.mean(agg["slip_maker"]) if agg["slip_maker"] else None),
            avg_slippage_mid=(statistics.mean(agg["slip_mid"]) if agg["slip_mid"] else None),
        )

    return {
        "total_entry_commands": len(entries),
        "raw_fact_rows": len(raw_facts),
        "deduped_fact_rows": len(dedup.kept),
        "dedup_stage1_state_collapsed": dedup.stage1_state_collapsed,
        "dedup_stage2_placeholder_dropped": dedup.stage2_placeholder_dropped,
        "entries_with_no_fill_facts": entries_no_fills,
        "entries_unparsable_timestamp": entries_unparsable_ts,
        "monthly": dict(sorted(monthly_out.items())),
    }


# ---------------------------------------------------------------------------
# P4 LIFECYCLE — exit behavior.
# ---------------------------------------------------------------------------

_EXIT_STATES = ("FILLED", "CANCELLED", "EXPIRED", "REJECTED")


def compute_panel4(conn_trades: sqlite3.Connection) -> dict[str, Any]:
    exit_rows = [
        dict(r)
        for r in conn_trades.execute(
            "SELECT command_id, position_id, state, created_at FROM venue_commands WHERE intent_kind = 'EXIT'"
        ).fetchall()
    ]

    monthly_state: dict[tuple[str, str], int] = defaultdict(int)
    other_state_count = 0
    for r in exit_rows:
        month = month_of(r["created_at"])
        if r["state"] in _EXIT_STATES:
            monthly_state[(month, r["state"])] += 1
        else:
            other_state_count += 1

    # Cheap/convex position split: entry avg price < 0.25 from deduped ENTRY fills.
    entries = [
        dict(r)
        for r in conn_trades.execute(
            "SELECT command_id, position_id, created_at, price FROM venue_commands "
            "WHERE intent_kind = 'ENTRY' AND state = 'FILLED'"
        ).fetchall()
    ]
    entry_command_ids = [e["command_id"] for e in entries]
    entry_facts_raw = _fetch_trade_facts_for_commands(conn_trades, entry_command_ids)
    entry_dedup = dedup_fill_facts(entry_facts_raw)

    cmd_to_position = {e["command_id"]: e["position_id"] for e in entries}
    position_cost: dict[str, float] = defaultdict(float)
    position_qty: dict[str, float] = defaultdict(float)
    for f in entry_dedup.kept:
        pos_id = cmd_to_position.get(f["command_id"])
        if pos_id is None:
            continue
        size = float(f["filled_size"])
        price = float(f["fill_price"])
        position_cost[pos_id] += size * price
        position_qty[pos_id] += size

    cheap_positions = {
        pos_id
        for pos_id, qty in position_qty.items()
        if qty > 0 and (position_cost[pos_id] / qty) < 0.25
    }
    positions_with_zero_qty = sum(1 for qty in position_qty.values() if qty <= 0)

    exit_commands_by_position: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in exit_rows:
        exit_commands_by_position[r["position_id"]].append(r)

    filled_exit_command_ids = [r["command_id"] for r in exit_rows if r["state"] == "FILLED"]
    exit_facts_raw = _fetch_trade_facts_for_commands(conn_trades, filled_exit_command_ids)
    exit_dedup = dedup_fill_facts(exit_facts_raw)
    exit_cmd_to_position = {
        r["command_id"]: r["position_id"] for r in exit_rows if r["state"] == "FILLED"
    }
    exit_proceeds_by_position: dict[str, float] = defaultdict(float)
    for f in exit_dedup.kept:
        pos_id = exit_cmd_to_position.get(f["command_id"])
        if pos_id is None:
            continue
        exit_proceeds_by_position[pos_id] += float(f["filled_size"]) * float(f["fill_price"])

    held_to_settlement = 0
    early_exited = 0
    pooled_exit_proceeds = 0.0
    pooled_entry_cost = 0.0
    for pos_id in cheap_positions:
        has_filled_exit = any(
            cmd["state"] == "FILLED" for cmd in exit_commands_by_position.get(pos_id, [])
        )
        if has_filled_exit:
            early_exited += 1
            pooled_exit_proceeds += exit_proceeds_by_position.get(pos_id, 0.0)
            pooled_entry_cost += position_cost[pos_id]
        else:
            held_to_settlement += 1

    pooled_ratio = (pooled_exit_proceeds / pooled_entry_cost) if pooled_entry_cost > 0 else None

    return {
        "total_exit_commands": len(exit_rows),
        "other_state_exit_commands": other_state_count,
        "monthly_state_counts": dict(sorted(monthly_state.items())),
        "total_entry_commands": len(entries),
        "entry_dedup_stage1_state_collapsed": entry_dedup.stage1_state_collapsed,
        "entry_dedup_stage2_placeholder_dropped": entry_dedup.stage2_placeholder_dropped,
        "exit_dedup_stage1_state_collapsed": exit_dedup.stage1_state_collapsed,
        "exit_dedup_stage2_placeholder_dropped": exit_dedup.stage2_placeholder_dropped,
        "positions_with_zero_entry_qty": positions_with_zero_qty,
        "cheap_position_count": len(cheap_positions),
        "cheap_held_to_settlement": held_to_settlement,
        "cheap_early_exited": early_exited,
        "cheap_pooled_exit_proceeds": pooled_exit_proceeds,
        "cheap_pooled_entry_cost": pooled_entry_cost,
        "cheap_pooled_exit_proceeds_over_entry_cost": pooled_ratio,
    }


# ---------------------------------------------------------------------------
# Markdown rendering.
# ---------------------------------------------------------------------------


def _fmt(v: Any, digits: int = 4) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.{digits}f}"
    return str(v)


def render_panel1_markdown(p1: dict[str, Any]) -> str:
    lines = ["## P1 FORECAST — paired proper scores, q vs price (settled events)", ""]
    lines.append(
        f"coverage: total={p1['total_rows']} usable={p1['usable_rows']} "
        f"excluded_null_q_in_bin={p1['excluded_null_q_in_bin']} "
        f"excluded_null_market_in_bin_prob={p1['excluded_null_market_in_bin_prob']} "
        f"excluded_null_settled_in_bin={p1['excluded_null_settled_in_bin']}"
    )
    lines.append("")
    lines.append("### Pooled by |q-p| bucket")
    lines.append("| bucket | n | clusters | logloss(q) | logloss(p) | brier(q) | brier(p) | q beats p |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for bucket in ("<0.15", "0.15-0.30", "0.30-0.50", ">0.50"):
        s = p1["pooled_by_bucket"].get(bucket, GroupStats())
        lines.append(
            f"| {bucket} | {s.n} | {s.clusters} | {_fmt(s.logloss_q)} | {_fmt(s.logloss_p)} | "
            f"{_fmt(s.brier_q)} | {_fmt(s.brier_p)} | {_fmt(s.q_beats_p_share, 3)} |"
        )
    g = p1["grand_total"]
    lines.append(
        f"| ALL | {g.n} | {g.clusters} | {_fmt(g.logloss_q)} | {_fmt(g.logloss_p)} | "
        f"{_fmt(g.brier_q)} | {_fmt(g.brier_p)} | {_fmt(g.q_beats_p_share, 3)} |"
    )
    lines.append("")
    lines.append("### By month x |q-p| bucket")
    lines.append("| month | bucket | n | clusters | logloss(q) | logloss(p) | brier(q) | brier(p) | q beats p |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for (month, bucket), s in p1["monthly"].items():
        lines.append(
            f"| {month} | {bucket} | {s.n} | {s.clusters} | {_fmt(s.logloss_q)} | {_fmt(s.logloss_p)} | "
            f"{_fmt(s.brier_q)} | {_fmt(s.brier_p)} | {_fmt(s.q_beats_p_share, 3)} |"
        )
    return "\n".join(lines)


def render_panel2_markdown(p2: dict[str, Any]) -> str:
    lines = ["## P2 SELECTION — price-only frequency edge vs own side price", ""]
    lines.append(
        f"coverage: total={p2['total_rows']} usable={p2['usable_rows']} "
        f"excluded_null_direction={p2['excluded_null_direction']} "
        f"excluded_null_avg_fill_price={p2['excluded_null_avg_fill_price']} "
        f"excluded_null_settled_in_bin={p2['excluded_null_settled_in_bin']}"
    )
    lines.append("")
    lines.append("### Pooled by price band")
    lines.append(
        "| band | n | clusters | mean p | mean y | edge | se(city-date) | se(date) | se_gate |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for band in ("<0.10", "0.10-0.25", "0.25-0.45", "0.45-0.65", ">=0.65"):
        s = p2["pooled_by_band"].get(band, SelectionStats())
        lines.append(
            f"| {band} | {s.n} | {s.clusters} | {_fmt(s.mean_p)} | {_fmt(s.mean_y)} | {_fmt(s.edge)} | "
            f"{_fmt(s.se_city_date)} | {_fmt(s.se_date)} | {_fmt(s.se_gate)} |"
        )
    g = p2["grand_total"]
    lines.append(
        f"| ALL | {g.n} | {g.clusters} | {_fmt(g.mean_p)} | {_fmt(g.mean_y)} | {_fmt(g.edge)} | "
        f"{_fmt(g.se_city_date)} | {_fmt(g.se_date)} | {_fmt(g.se_gate)} |"
    )
    lines.append("")
    lines.append("### By month x price band")
    lines.append(
        "| month | band | n | clusters | mean p | mean y | edge | se_gate |"
    )
    lines.append("|---|---|---|---|---|---|---|---|")
    for (month, band), s in p2["monthly"].items():
        lines.append(
            f"| {month} | {band} | {s.n} | {s.clusters} | {_fmt(s.mean_p)} | {_fmt(s.mean_y)} | "
            f"{_fmt(s.edge)} | {_fmt(s.se_gate)} |"
        )
    return "\n".join(lines)


def render_panel3_markdown(p3: dict[str, Any]) -> str:
    lines = ["## P3 EXECUTION — fill quality (ENTRY fills, post-dedup)", ""]
    lines.append(
        f"coverage: total_entry_commands={p3['total_entry_commands']} "
        f"raw_fact_rows={p3['raw_fact_rows']} deduped_fact_rows={p3['deduped_fact_rows']} "
        f"dedup_stage1_state_collapsed={p3['dedup_stage1_state_collapsed']} "
        f"dedup_stage2_placeholder_dropped={p3['dedup_stage2_placeholder_dropped']} "
        f"entries_with_no_fill_facts={p3['entries_with_no_fill_facts']} "
        f"entries_unparsable_timestamp={p3['entries_unparsable_timestamp']}"
    )
    lines.append("")
    lines.append(
        "| month | n fills | n commands | dedup dropped | median lat(s) | p90 lat(s) | "
        "share<5s | share>120s | slip taker | slip maker | slip mid |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    dropped_total = p3["dedup_stage1_state_collapsed"] + p3["dedup_stage2_placeholder_dropped"]
    for month, s in p3["monthly"].items():
        lines.append(
            f"| {month} | {s.n_fills} | {s.n_commands} | {dropped_total} | "
            f"{_fmt(s.median_latency_s, 1)} | {_fmt(s.p90_latency_s, 1)} | "
            f"{_fmt(s.share_taker_lt5s, 3)} | {_fmt(s.share_maker_gt120s, 3)} | "
            f"{_fmt(s.avg_slippage_taker)} | {_fmt(s.avg_slippage_maker)} | {_fmt(s.avg_slippage_mid)} |"
        )
    return "\n".join(lines)


def render_panel4_markdown(p4: dict[str, Any]) -> str:
    lines = ["## P4 LIFECYCLE — exit behavior", ""]
    lines.append(
        f"coverage: total_exit_commands={p4['total_exit_commands']} "
        f"other_state_exit_commands={p4['other_state_exit_commands']} "
        f"total_entry_commands={p4['total_entry_commands']} "
        f"entry_dedup_stage1_state_collapsed={p4['entry_dedup_stage1_state_collapsed']} "
        f"entry_dedup_stage2_placeholder_dropped={p4['entry_dedup_stage2_placeholder_dropped']} "
        f"exit_dedup_stage1_state_collapsed={p4['exit_dedup_stage1_state_collapsed']} "
        f"exit_dedup_stage2_placeholder_dropped={p4['exit_dedup_stage2_placeholder_dropped']} "
        f"positions_with_zero_entry_qty={p4['positions_with_zero_entry_qty']}"
    )
    lines.append("")
    lines.append("### EXIT command state by month")
    lines.append("| month | FILLED | CANCELLED | EXPIRED | REJECTED |")
    lines.append("|---|---|---|---|---|")
    months = sorted({m for (m, _st) in p4["monthly_state_counts"]})
    for month in months:
        counts = [p4["monthly_state_counts"].get((month, st), 0) for st in _EXIT_STATES]
        lines.append(f"| {month} | {counts[0]} | {counts[1]} | {counts[2]} | {counts[3]} |")
    lines.append("")
    lines.append("### Cheap/convex positions (entry avg price < 0.25)")
    lines.append(
        f"n_cheap_positions={p4['cheap_position_count']} "
        f"held_to_settlement={p4['cheap_held_to_settlement']} "
        f"early_exited={p4['cheap_early_exited']} "
        f"pooled_exit_proceeds={_fmt(p4['cheap_pooled_exit_proceeds'], 2)} "
        f"pooled_entry_cost={_fmt(p4['cheap_pooled_entry_cost'], 2)} "
        f"pooled_ratio={_fmt(p4['cheap_pooled_exit_proceeds_over_entry_cost'], 4)}"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="Repo root (DB paths are relative to this).")
    parser.add_argument("--world", default="state/zeus-world.db", help="World DB path (relative to --root).")
    parser.add_argument("--trades", default="state/zeus_trades.db", help="Trades DB path (relative to --root).")
    parser.add_argument(
        "--panel",
        choices=["P1", "P2", "P3", "P4", "ALL"],
        default="ALL",
        help="Restrict output to a single panel (default ALL).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    root = Path(args.root)
    world_path = root / args.world
    trades_path = root / args.trades

    sections: list[str] = []

    if args.panel in ("P1", "P2", "ALL"):
        conn_world = open_ro(world_path)
        try:
            if args.panel in ("P1", "ALL"):
                sections.append(render_panel1_markdown(compute_panel1(conn_world)))
            if args.panel in ("P2", "ALL"):
                sections.append(render_panel2_markdown(compute_panel2(conn_world)))
        finally:
            conn_world.close()

    if args.panel in ("P3", "P4", "ALL"):
        conn_trades = open_ro(trades_path)
        try:
            if args.panel in ("P3", "ALL"):
                sections.append(render_panel3_markdown(compute_panel3(conn_trades)))
            if args.panel in ("P4", "ALL"):
                sections.append(render_panel4_markdown(compute_panel4(conn_trades)))
        finally:
            conn_trades.close()

    print("\n\n".join(sections))
    return 0


if __name__ == "__main__":
    sys.exit(main())

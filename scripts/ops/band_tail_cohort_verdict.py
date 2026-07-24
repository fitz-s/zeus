#!/usr/bin/env python3
# Lifecycle: created=2026-07-24; last_reviewed=2026-07-24; last_reused=never
# Purpose: A3 (COLLISION.md §C2, evidence-first mandate) — read-only, stake-weighted
#   settled-cohort verdict for the mid-price NO-side band (entry price in
#   [0.45,0.65], the band identified by the operator's era-split evidence
#   "jul15+ NO winrate 0.731 vs ~0.64 breakeven"). This is the diagnostic the
#   collision doc requires BEFORE the full_transport_v1/high-tail fail-closed
#   switch (armed in PR-1) may be flipped: flip only if this verdict is NEGATIVE.
# Reuse: run before flipping the C2 fail-closed switch; re-run as the 17 open
#   tokens named in COLLISION.md settle further. Two independent read-only
#   connections (trades, forecasts), app-side join in Python — mirrors
#   scripts/ops/reconcile_settlement_outcomes.py's "never ATTACH" convention.
# Authority basis: docs/operations/current/plans/ultimate_alpha_2026-07-23/
#   COLLISION.md §C2. Economics come from CHAIN-TRUTH facts only
#   (wallet_fill_observations fills + settlement_outcomes winning bin), never
#   position_current.realized_pnl_usd/cost_basis_usd (forbidden local-ledger
#   economics per src/contracts/economics_ownership.py; memory
#   capital-gains-measure-from-chain-truth).
"""Stake-weighted settled-cohort verdict for NO-side entries in [0.45, 0.65].

For each era in {post-2026-07-15, post-2026-07-01} this script:

  1. Selects the cohort: position_current rows with direction='buy_no' and
     entry_price in [0.45, 0.65], whose earliest entry execution_fact fill
     landed at/after the era start.
  2. Splits the cohort into RESOLVED (settlement_outcomes has a VERIFIED row
     for the position's city/target_date/temperature_metric, with a
     winning_bin that parses) vs UNRESOLVED/UNPARSEABLE.
  3. For each resolved position, determines WIN (NO correct) by comparing the
     position's bin_label range to the settlement_outcomes.winning_bin range
     (via src.data.market_scanner._parse_temp_range — the same parser the
     live ingest pipeline uses), NOT by reconstructing the CTF on-chain
     outcome_index (there is no production token_id -> outcome_index join;
     see src/reduce/position_economics.py's own docstring admission of this
     gap). Stake and shares come from wallet_fill_observations BUY fills on
     the position's no_token_id (ZEUS_ATTRIBUTED disposition only — the
     wallet is shared with the operator's manual co-trading).
  4. Reports stake-weighted (by cost, not position count): resolved/unresolved
     counts, win rate, breakeven (= total resolved stake / total resolved net
     shares), net chain PnL, and a VERDICT: POSITIVE / NEGATIVE / INSUFFICIENT
     (unresolved stake fraction > 20%).

Strictly read-only. Never writes. Never ATTACHes one DB to the other.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.market_scanner import _parse_temp_range  # noqa: E402
from src.state.db_paths import primary_forecasts_db_path, primary_trade_db_path  # noqa: E402

BAND_LOW = 0.45
BAND_HIGH = 0.65
UNRESOLVED_INSUFFICIENT_FRACTION = 0.20
FLOAT_TOL = 1e-6

ERAS: dict[str, str] = {
    "post-2026-07-15": "2026-07-15T00:00:00+00:00",
    "post-2026-07-01": "2026-07-01T00:00:00+00:00",
}


def _ro_connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro&cache=private", uri=True, timeout=0.25, isolation_level=None)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=250")
    conn.execute("PRAGMA mmap_size=0")
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


@dataclass
class CohortPosition:
    position_id: str
    city: str
    target_date: str
    temperature_metric: str
    bin_label: str
    entry_price: float
    no_token_id: str
    entry_time: str


def _cohort_positions(trades_conn: sqlite3.Connection, era_start: str) -> list[CohortPosition]:
    # entry_time anchors on posted_at, NOT COALESCE(filled_at, posted_at). A live
    # spot-check (Tokyo/2026-06-26 buy_no position) found an entry execution_fact
    # row posted_at=2026-06-24 but filled_at=2026-07-13 (~19 days later — a
    # delayed fill-reconciliation backfill), which would silently reclassify a
    # late-June trade as "post-2026-07-01" if filled_at were used. posted_at is
    # when Zeus actually submitted the order (decision time) and is immune to
    # this backfill-timestamp artifact.
    rows = trades_conn.execute(
        """
        SELECT pc.position_id, pc.city, pc.target_date, pc.temperature_metric,
               pc.bin_label, pc.entry_price, pc.no_token_id, ef.entry_time
          FROM position_current pc
          JOIN (
                SELECT position_id, MIN(posted_at) AS entry_time
                  FROM execution_fact
                 WHERE order_role = 'entry'
                 GROUP BY position_id
          ) ef ON ef.position_id = pc.position_id
         WHERE pc.direction = 'buy_no'
           AND pc.entry_price BETWEEN ? AND ?
           AND pc.no_token_id IS NOT NULL
           AND ef.entry_time >= ?
        """,
        (BAND_LOW, BAND_HIGH, era_start),
    ).fetchall()
    return [
        CohortPosition(
            position_id=r["position_id"], city=r["city"], target_date=r["target_date"],
            temperature_metric=r["temperature_metric"], bin_label=r["bin_label"],
            entry_price=float(r["entry_price"]), no_token_id=r["no_token_id"],
            entry_time=r["entry_time"],
        )
        for r in rows
    ]


@dataclass
class FillEconomics:
    buy_shares: float
    buy_cost_usd: float
    sell_shares: float
    sell_proceeds_usd: float
    fees_usd: float

    @property
    def net_shares(self) -> float:
        return self.buy_shares - self.sell_shares


def _fill_economics(trades_conn: sqlite3.Connection, no_token_id: str) -> FillEconomics:
    """ZEUS_ATTRIBUTED wallet_fill_observations on this token — chain-truth,
    never position_current.realized_pnl_usd/cost_basis_usd."""
    row = trades_conn.execute(
        """
        SELECT
          SUM(CASE WHEN side='BUY'  THEN CAST(size AS REAL) ELSE 0 END) AS buy_shares,
          SUM(CASE WHEN side='BUY'  THEN CAST(price AS REAL)*CAST(size AS REAL) ELSE 0 END) AS buy_cost,
          SUM(CASE WHEN side='SELL' THEN CAST(size AS REAL) ELSE 0 END) AS sell_shares,
          SUM(CASE WHEN side='SELL' THEN CAST(price AS REAL)*CAST(size AS REAL) ELSE 0 END) AS sell_proceeds,
          SUM(COALESCE(fee_paid_micro, 0)) AS fee_micro
          FROM wallet_fill_observations
         WHERE token_id = ? AND disposition = 'ZEUS_ATTRIBUTED'
        """,
        (no_token_id,),
    ).fetchone()
    return FillEconomics(
        buy_shares=float(row["buy_shares"] or 0.0),
        buy_cost_usd=float(row["buy_cost"] or 0.0),
        sell_shares=float(row["sell_shares"] or 0.0),
        sell_proceeds_usd=float(row["sell_proceeds"] or 0.0),
        fees_usd=float(row["fee_micro"] or 0) / 1e6,
    )


def _settlement_row(forecasts_conn: sqlite3.Connection, city: str, target_date: str, metric: str):
    return forecasts_conn.execute(
        """
        SELECT winning_bin, settlement_value, authority, resolution_state
          FROM settlement_outcomes
         WHERE city = ? AND target_date = ? AND temperature_metric = ?
        """,
        (city, target_date, metric),
    ).fetchone()


def _range_close(a: tuple[float | None, float | None], b: tuple[float | None, float | None]) -> bool:
    def _eq(x: float | None, y: float | None) -> bool:
        if x is None and y is None:
            return True
        if x is None or y is None:
            return False
        return abs(x - y) < FLOAT_TOL
    return _eq(a[0], b[0]) and _eq(a[1], b[1])


def _no_won(bin_label: str, winning_bin: str) -> bool | None:
    """True if the NO holder was correct (position's bin != winning bin).

    None if either label fails to parse into a temperature range (ambiguous —
    caller must treat as unresolved, never guess).
    """
    a = _parse_temp_range(bin_label or "")
    b = _parse_temp_range(winning_bin or "")
    if a == (None, None) or b == (None, None):
        return None
    return not _range_close(a, b)


@dataclass
class EraResult:
    era: str
    cohort_n: int
    resolved_n: int
    unresolved_n: int
    unparseable_n: int
    exited_n: int
    resolved_stake_usd: float
    unresolved_stake_usd: float
    winning_stake_usd: float
    resolved_shares: float
    net_chain_pnl_usd: float
    fees_usd: float
    exited_stake_usd: float
    exited_pnl_usd: float

    @property
    def total_stake_usd(self) -> float:
        return self.resolved_stake_usd + self.unresolved_stake_usd

    @property
    def unresolved_stake_fraction(self) -> float | None:
        if self.total_stake_usd <= 0:
            return None
        return self.unresolved_stake_usd / self.total_stake_usd

    @property
    def stake_weighted_winrate(self) -> float | None:
        if self.resolved_stake_usd <= 0:
            return None
        return self.winning_stake_usd / self.resolved_stake_usd

    @property
    def stake_weighted_breakeven(self) -> float | None:
        if self.resolved_shares <= 0:
            return None
        return self.resolved_stake_usd / self.resolved_shares

    @property
    def fee_rate(self) -> float | None:
        if self.resolved_stake_usd <= 0:
            return None
        return self.fees_usd / self.resolved_stake_usd

    @property
    def verdict(self) -> str:
        frac = self.unresolved_stake_fraction
        if frac is None or frac > UNRESOLVED_INSUFFICIENT_FRACTION:
            return "INSUFFICIENT"
        wr = self.stake_weighted_winrate
        be = self.stake_weighted_breakeven
        fee = self.fee_rate or 0.0
        if wr is None or be is None:
            return "INSUFFICIENT"
        return "POSITIVE" if wr > (be + fee) else "NEGATIVE"


# A position whose net (BUY - SELL) shares on its no_token_id fall below this
# fraction of its gross BUY shares was substantially exited before settlement
# (e.g. a same-day SELL at market) — its outcome is an EXIT fill spread, not a
# hold-to-settlement win/loss, and folding it into the win-rate/breakeven ratio
# corrupts both (a live spot-check found several exactly-net-zero positions that
# pushed a stake-weighted breakeven above 100%, which is impossible for a cohort
# entered at price <= 0.65). Tracked and reported separately instead.
_HELD_MIN_FRACTION = 0.5


def run_era(trades_conn: sqlite3.Connection, forecasts_conn: sqlite3.Connection, era: str, era_start: str) -> EraResult:
    positions = _cohort_positions(trades_conn, era_start)
    resolved_n = unresolved_n = unparseable_n = exited_n = 0
    resolved_stake = unresolved_stake = winning_stake = resolved_shares = 0.0
    net_chain_pnl = fees_total = exited_stake = exited_pnl = 0.0

    for p in positions:
        fe = _fill_economics(trades_conn, p.no_token_id)
        srow = _settlement_row(forecasts_conn, p.city, p.target_date, p.temperature_metric)
        resolved_row = (
            srow is not None
            and srow["authority"] == "VERIFIED"
            and srow["winning_bin"] is not None
            and (srow["resolution_state"] is None or srow["resolution_state"] in ("VENUE_RESOLVED", "PHYSICALLY_CONFIRMED"))
        )

        was_exited = fe.sell_proceeds_usd > 0.0 and (
            fe.buy_shares <= 0.0 or fe.net_shares < _HELD_MIN_FRACTION * fe.buy_shares
        )
        if was_exited:
            exited_n += 1
            exited_stake += fe.buy_cost_usd
            era_pnl = fe.sell_proceeds_usd - fe.buy_cost_usd - fe.fees_usd
            exited_pnl += era_pnl
            net_chain_pnl += era_pnl
            continue

        won = _no_won(p.bin_label, srow["winning_bin"]) if resolved_row else None
        if not resolved_row or won is None:
            if resolved_row and won is None:
                unparseable_n += 1
            else:
                unresolved_n += 1
            unresolved_stake += fe.buy_cost_usd
            continue

        resolved_n += 1
        resolved_stake += fe.buy_cost_usd
        resolved_shares += fe.net_shares
        fees_total += fe.fees_usd
        payout = fe.net_shares * 1.0 if won else 0.0
        chain_pnl = payout - fe.buy_cost_usd - fe.fees_usd + fe.sell_proceeds_usd
        net_chain_pnl += chain_pnl
        if won:
            winning_stake += fe.buy_cost_usd

    return EraResult(
        era=era, cohort_n=len(positions), resolved_n=resolved_n,
        unresolved_n=unresolved_n, unparseable_n=unparseable_n, exited_n=exited_n,
        resolved_stake_usd=resolved_stake, unresolved_stake_usd=unresolved_stake,
        winning_stake_usd=winning_stake, resolved_shares=resolved_shares,
        net_chain_pnl_usd=net_chain_pnl, fees_usd=fees_total,
        exited_stake_usd=exited_stake, exited_pnl_usd=exited_pnl,
    )


def _fmt_pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"


def _fmt_usd(x: float | None) -> str:
    return "n/a" if x is None else f"${x:,.2f}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--state-dir", default=None, help="Override state dir (default: canonical primary_*_db_path()).")
    args = ap.parse_args(argv)

    if args.state_dir:
        trades_path = Path(args.state_dir) / "zeus_trades.db"
        forecasts_path = Path(args.state_dir) / "zeus-forecasts.db"
    else:
        trades_path = primary_trade_db_path()
        forecasts_path = primary_forecasts_db_path()

    if not trades_path.exists() or not forecasts_path.exists():
        print(f"SKIP — DB file absent (trades={trades_path.exists()}, forecasts={forecasts_path.exists()}).")
        return 0

    trades_conn = _ro_connect(trades_path)
    forecasts_conn = _ro_connect(forecasts_path)
    try:
        required = ["position_current", "execution_fact", "wallet_fill_observations"]
        missing = [t for t in required if not _table_exists(trades_conn, t)]
        if missing or not _table_exists(forecasts_conn, "settlement_outcomes"):
            print(f"SKIP — required table(s) absent: trades={missing}, "
                  f"forecasts.settlement_outcomes={_table_exists(forecasts_conn, 'settlement_outcomes')}")
            return 0

        print(f"band_tail_cohort_verdict — NO-side entry_price in [{BAND_LOW}, {BAND_HIGH}]")
        print(f"trades_db={trades_path}  forecasts_db={forecasts_path}")
        print()
        for era, era_start in ERAS.items():
            r = run_era(trades_conn, forecasts_conn, era, era_start)
            print(f"=== {era} (entry_time >= {era_start}) ===")
            print(f"  cohort_n={r.cohort_n}  resolved_n={r.resolved_n}  "
                  f"unresolved_n={r.unresolved_n}  unparseable_bin_n={r.unparseable_n}  "
                  f"exited_before_settlement_n={r.exited_n}")
            print(f"  exited_stake={_fmt_usd(r.exited_stake_usd)}  exited_pnl={_fmt_usd(r.exited_pnl_usd)}  "
                  f"(excluded from win-rate/breakeven — closed by SELL, not held to settlement)")
            print(f"  resolved_stake={_fmt_usd(r.resolved_stake_usd)}  "
                  f"unresolved_stake={_fmt_usd(r.unresolved_stake_usd)}  "
                  f"unresolved_stake_fraction={_fmt_pct(r.unresolved_stake_fraction)}")
            print(f"  stake_weighted_winrate={_fmt_pct(r.stake_weighted_winrate)}  "
                  f"stake_weighted_breakeven(avg_cost)={_fmt_pct(r.stake_weighted_breakeven)}  "
                  f"fee_rate={_fmt_pct(r.fee_rate)}")
            print(f"  net_chain_pnl={_fmt_usd(r.net_chain_pnl_usd)}  fees={_fmt_usd(r.fees_usd)}")
            print(f"  VERDICT: {r.verdict}")
            print()
    finally:
        trades_conn.close()
        forecasts_conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

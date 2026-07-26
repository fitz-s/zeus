#!/usr/bin/env python3
# Lifecycle: created=2026-07-25; last_reviewed=2026-07-25; last_reused=2026-07-25
# Purpose: Read-only HISTORICAL TELEMETRY over settlement_commands rows left in
#   REDEEM_INTENT_CREATED / REDEEM_REVIEW_REQUIRED state, plus an optional
#   --verify-fee cash-effect check on recent MATCHED taker fills.
# Reuse: Re-run any time; opens state/zeus_trades.db mode=ro only, never
#   writes.
# 2026-07-25 IMPORTANT CORRECTION: on-chain redemption is decoupled entirely
#   from Zeus (Polymarket settles win/loss on our behalf; harvester no longer
#   enqueues these rows -- see src/execution/harvester.py). This script
#   PREVIOUSLY labeled REDEEM_INTENT_CREATED rows "ACTIONABLE (claimable now)"
#   and printed an "OPERATOR ACTION" list for a third-party redeemer. That
#   framing was WRONG and operationally hazardous: verified read-only audit
#   confirms the underlying condition_ids are already chain-resolved
#   (payout_observations) and their tokens are already registered in
#   token_suppression (settled_position) -- the money is already home. Rows in
#   this state are PERMANENTLY INERT (nothing consumes REDEEM_INTENT_CREATED
#   any more) historical residue, NOT a to-do list. There is no operator
#   action to take. Kept as read-only historical telemetry only because the
#   dollar/age breakdown still has diagnostic value for auditing the residue;
#   the misleading "actionable"/"operator action" framing is removed below.
"""Read-only HISTORICAL TELEMETRY over stale settlement_commands rows.

NO ACTION EXISTS FOR THESE ROWS. Zeus never submits a redeem transaction
(operator law) and, as of 2026-07-25, on-chain redemption is decoupled
entirely -- Polymarket settles win/loss on Zeus's behalf and the underlying
capital is already accounted for via token_suppression / chain-resolved
payout observations by the time a row lands here. This script only reads
`settlement_commands` + `position_current` from `state/zeus_trades.db`
(mode=ro) and prints a historical table of stuck rows for audit purposes.
It performs no writes, constructs no transaction, and implies no pending
operator task.

`--verify-fee` additionally checks whether Polymarket's per-fill schedule fee
is a real cash effect or just an unpopulated `venue_trade_facts.fee_paid_micro`
column: it compares the collateral-ledger balance delta immediately around N
recent MATCHED taker fills against price*size with and without the documented
price-dependent fee formula (`src.contracts.execution_price.polymarket_fee`).
This half of the script is independent of the redemption-residue reporting
above and is unaffected by the above correction.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.contracts.execution_price import polymarket_fee  # noqa: E402

DEFAULT_DB = REPO_ROOT / "state" / "zeus_trades.db"

# ACTIONABLE_STATE name is legacy (kept to avoid an unnecessary rename of the
# public constant); as of 2026-07-25 these rows are NOT actionable -- redeem
# intent was created, will NEVER be submitted or consumed (on-chain redemption
# is decoupled entirely; harvester no longer enqueues, no poller watches this
# state), and the underlying capital is already accounted for elsewhere. This
# is permanently-inert historical residue.
ACTIONABLE_STATE = "REDEEM_INTENT_CREATED"
# STALE_STATE: per operator redeem directive 2026-06-10, these rows are
# already externally redeemed at zero payout -- bookkeeping-only, not
# actionable capital.
STALE_STATE = "REDEEM_REVIEW_REQUIRED"

MICRO = 1_000_000.0


@dataclass
class RedemptionRow:
    command_id: str
    condition_id: str
    market_id: str
    state: str
    payout_asset: str
    pusd_amount: float
    requested_at: str
    submitted_at: str | None
    error_payload: str | None
    token_ids: list[str] = field(default_factory=list)
    description: str = ""
    wallet: str = "unknown"
    age_days: float = 0.0


def _connect_ro(db_path: Path, retries: int = 1, backoff_seconds: float = 1.0) -> sqlite3.Connection:
    """Open a read-only sqlite connection, retrying once on lock contention."""

    uri = f"file:{db_path}?mode=ro"
    last_exc: sqlite3.OperationalError | None = None
    for attempt in range(retries + 1):
        try:
            conn = sqlite3.connect(uri, uri=True, timeout=5.0)
            conn.row_factory = sqlite3.Row
            conn.execute("SELECT 1")
            return conn
        except sqlite3.OperationalError as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(backoff_seconds)
    assert last_exc is not None
    raise last_exc


def _run_with_retry(
    conn: sqlite3.Connection, sql: str, params: tuple = (), retries: int = 1, backoff_seconds: float = 1.0
) -> list[sqlite3.Row] | None:
    """Run a read query, retrying once on 'database is locked'. Returns None on repeated failure."""

    last_exc: sqlite3.OperationalError | None = None
    for attempt in range(retries + 1):
        try:
            return conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(backoff_seconds)
    return None


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def fetch_wallet_address(conn: sqlite3.Connection) -> str:
    rows = _run_with_retry(conn, "SELECT DISTINCT wallet FROM wallet_balance_head")
    if not rows:
        return "unknown"
    wallets = sorted({str(r["wallet"]) for r in rows if r["wallet"]})
    if len(wallets) == 1:
        return wallets[0]
    if not wallets:
        return "unknown"
    return "MULTIPLE:" + ",".join(wallets)


def _position_descriptions(conn: sqlite3.Connection, condition_ids: list[str]) -> dict[str, sqlite3.Row]:
    """Best (most recently updated) position_current row per condition_id, if any."""

    if not condition_ids:
        return {}
    placeholders = ",".join("?" for _ in condition_ids)
    rows = _run_with_retry(
        conn,
        f"""
        SELECT condition_id, city, target_date, bin_label, temperature_metric, direction, updated_at
        FROM position_current
        WHERE condition_id IN ({placeholders})
        """,
        tuple(condition_ids),
    )
    if not rows:
        return {}
    best: dict[str, sqlite3.Row] = {}
    for row in rows:
        cid = str(row["condition_id"])
        current = best.get(cid)
        if current is None or str(row["updated_at"] or "") > str(current["updated_at"] or ""):
            best[cid] = row
    return best


def fetch_redemption_rows(
    conn: sqlite3.Connection, now: datetime | None = None
) -> tuple[list[RedemptionRow], list[str]]:
    """Return (rows, warnings). Degrades gracefully if a sub-query fails."""

    warnings: list[str] = []
    now = now or datetime.now(timezone.utc)

    sc_rows = _run_with_retry(
        conn,
        """
        SELECT command_id, condition_id, market_id, state, payout_asset,
               pusd_amount_micro, token_amounts_json, requested_at,
               submitted_at, error_payload
        FROM settlement_commands
        WHERE state IN (?, ?)
        """,
        (ACTIONABLE_STATE, STALE_STATE),
    )
    if sc_rows is None:
        warnings.append(
            "settlement_commands query failed (DB locked after retry) -- report is EMPTY, not exhaustive."
        )
        return [], warnings

    condition_ids = sorted({str(r["condition_id"]) for r in sc_rows if r["condition_id"]})
    descriptions = _position_descriptions(conn, condition_ids)
    if condition_ids and not descriptions:
        warnings.append(
            "position_current lookup returned no rows -- city/bin descriptions may be missing "
            "(query failed or no positions matched)."
        )

    wallet = fetch_wallet_address(conn)

    out: list[RedemptionRow] = []
    for row in sc_rows:
        try:
            token_amounts = json.loads(row["token_amounts_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            token_amounts = {}
        token_ids = list(token_amounts.keys()) if isinstance(token_amounts, dict) else []

        cid = str(row["condition_id"])
        pos = descriptions.get(cid)
        if pos is not None and pos["bin_label"]:
            description = f"{pos['city']} {pos['target_date']} ({pos['temperature_metric']}): {pos['bin_label']}"
        else:
            description = f"market_id={row['market_id']} (no position_current match)"

        requested_dt = _parse_ts(row["requested_at"])
        age_days = (now - requested_dt).total_seconds() / 86400.0 if requested_dt else 0.0

        out.append(
            RedemptionRow(
                command_id=str(row["command_id"]),
                condition_id=cid,
                market_id=str(row["market_id"]),
                state=str(row["state"]),
                payout_asset=str(row["payout_asset"]),
                pusd_amount=float(row["pusd_amount_micro"] or 0) / MICRO,
                requested_at=str(row["requested_at"] or ""),
                submitted_at=row["submitted_at"],
                error_payload=row["error_payload"],
                token_ids=token_ids,
                description=description,
                wallet=wallet,
                age_days=round(age_days, 2),
            )
        )
    return out, warnings


@dataclass
class Totals:
    actionable_count: int = 0
    actionable_total_usd: float = 0.0
    actionable_dollar_days: float = 0.0
    stale_count: int = 0
    stale_total_usd: float = 0.0


def aggregate(rows: list[RedemptionRow]) -> tuple[list[RedemptionRow], list[RedemptionRow], Totals]:
    actionable = sorted(
        (r for r in rows if r.state == ACTIONABLE_STATE), key=lambda r: r.pusd_amount, reverse=True
    )
    stale = sorted((r for r in rows if r.state == STALE_STATE), key=lambda r: r.pusd_amount, reverse=True)
    totals = Totals(
        actionable_count=len(actionable),
        actionable_total_usd=sum(r.pusd_amount for r in actionable),
        actionable_dollar_days=sum(r.pusd_amount * r.age_days for r in actionable),
        stale_count=len(stale),
        stale_total_usd=sum(r.pusd_amount for r in stale),
    )
    return actionable, stale, totals


def _row_to_dict(r: RedemptionRow) -> dict[str, Any]:
    return {
        "command_id": r.command_id,
        "condition_id": r.condition_id,
        "market_id": r.market_id,
        "description": r.description,
        "state": r.state,
        "payout_asset": r.payout_asset,
        "pusd_amount": round(r.pusd_amount, 6),
        "age_days": r.age_days,
        "wallet": r.wallet,
        "token_ids": r.token_ids,
        "requested_at": r.requested_at,
        "submitted_at": r.submitted_at,
        "error_payload": r.error_payload,
    }


def render_text(
    actionable: list[RedemptionRow], stale: list[RedemptionRow], totals: Totals, warnings: list[str]
) -> str:
    lines: list[str] = []
    lines.append("=" * 100)
    lines.append("SETTLEMENT_COMMANDS HISTORICAL RESIDUE REPORT (read-only; Zeus never submits redeem tx)")
    lines.append("=" * 100)
    lines.append(
        "NO ACTION EXISTS FOR ANY ROW BELOW. On-chain redemption is decoupled entirely "
        "(2026-07-25) -- Polymarket settles win/loss on Zeus's behalf. This capital is "
        "already accounted for via token_suppression / chain-resolved payout observations. "
        "These rows are permanently-inert historical residue, kept for audit only."
    )
    if warnings:
        lines.append("")
        lines.append("WARNINGS:")
        for w in warnings:
            lines.append(f"  - {w}")

    lines.append("")
    lines.append(f"INERT ({ACTIONABLE_STATE}) -- never submitted, never will be; sorted by amount desc")
    lines.append("-" * 100)
    if not actionable:
        lines.append("  (none)")
    for r in actionable:
        lines.append(
            f"  ${r.pusd_amount:>9.2f}  age={r.age_days:>6.1f}d  {r.condition_id[:18]}...  "
            f"{r.description}"
        )
        lines.append(f"      wallet={r.wallet}  token_ids={r.token_ids}")

    lines.append("")
    lines.append(
        f"BOOKKEEPING-STALE ({STALE_STATE}) -- excluded from the totals above; already "
        "externally redeemed at zero payout per operator directive 2026-06-10"
    )
    lines.append("-" * 100)
    if not stale:
        lines.append("  (none)")
    for r in stale:
        lines.append(f"  ${r.pusd_amount:>9.2f}  age={r.age_days:>6.1f}d  {r.condition_id[:18]}...  {r.description}")

    lines.append("")
    lines.append("TOTALS")
    lines.append("-" * 100)
    lines.append(f"  inert-residue count:     {totals.actionable_count}")
    lines.append(f"  inert-residue $ sum:     ${totals.actionable_total_usd:,.2f}")
    lines.append(f"  inert-residue dollar-days: ${totals.actionable_dollar_days:,.2f}")
    lines.append(f"  bookkeeping-stale count: {totals.stale_count}")
    lines.append(f"  bookkeeping-stale $ sum: ${totals.stale_total_usd:,.2f} (excluded above)")
    return "\n".join(lines)


def render_json(
    actionable: list[RedemptionRow], stale: list[RedemptionRow], totals: Totals, warnings: list[str]
) -> str:
    payload = {
        "notice": (
            "NO ACTION EXISTS for any row below. On-chain redemption is decoupled "
            "entirely (2026-07-25) -- Polymarket settles win/loss on Zeus's behalf; "
            "this capital is already accounted for via token_suppression / "
            "chain-resolved payout observations. Permanently-inert historical "
            "residue, kept for audit only."
        ),
        "warnings": warnings,
        "inert_residue": [_row_to_dict(r) for r in actionable],
        "bookkeeping_stale": [_row_to_dict(r) for r in stale],
        "totals": {
            "inert_residue_count": totals.actionable_count,
            "inert_residue_total_usd": round(totals.actionable_total_usd, 6),
            "inert_residue_dollar_days": round(totals.actionable_dollar_days, 6),
            "stale_count": totals.stale_count,
            "stale_total_usd": round(totals.stale_total_usd, 6),
        },
    }
    return json.dumps(payload, indent=2, sort_keys=True)


# --- D3: is the "venue charges zero fee" claim a real cash effect, or an ---
# --- unpopulated column? ----------------------------------------------------


@dataclass
class FeeCheckRow:
    trade_fact_id: int
    observed_at: str
    filled_size: float
    fill_price: float
    fee_paid_micro: int | None
    trader_side: str
    balance_before_micro: int | None
    balance_after_micro: int | None
    window_clean: bool
    observed_delta_usd: float | None = None
    expected_no_fee_usd: float = 0.0
    expected_with_fee_usd: float = 0.0


def fetch_recent_matched_taker_fills(
    conn: sqlite3.Connection, limit: int = 5, scan_limit: int = 200
) -> tuple[list[sqlite3.Row], int]:
    """Return the most recent MATCHED fills where OUR side was TAKER.

    Polymarket's schedule fee is asymmetric (taker pays, maker does not), and
    the raw CLOB payload's own top-level `fee_rate_bps` reports "0" on BOTH
    maker and taker legs -- so distinguishing role requires reading
    `raw_payload_json.trader_side`, not `fee_paid_micro` (always NULL) or the
    payload's own fee_rate_bps field.
    """

    rows = _run_with_retry(
        conn,
        """
        SELECT trade_fact_id, observed_at, filled_size, fill_price, fee_paid_micro, raw_payload_json
        FROM venue_trade_facts
        WHERE state = 'MATCHED'
        ORDER BY observed_at DESC
        LIMIT ?
        """,
        (scan_limit,),
    )
    if not rows:
        return [], 0

    taker_rows: list[sqlite3.Row] = []
    scanned = 0
    for row in rows:
        scanned += 1
        try:
            payload = json.loads(row["raw_payload_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        if str(payload.get("trader_side") or "").upper() == "TAKER":
            taker_rows.append(row)
        if len(taker_rows) >= limit:
            break
    return taker_rows, scanned


def _snapshot_around(conn: sqlite3.Connection, observed_at: str) -> tuple[sqlite3.Row | None, sqlite3.Row | None]:
    before = _run_with_retry(
        conn,
        """
        SELECT pusd_balance_micro, captured_at FROM collateral_ledger_snapshots
        WHERE captured_at <= ? AND authority_tier = 'CHAIN'
        ORDER BY captured_at DESC LIMIT 1
        """,
        (observed_at,),
    )
    after = _run_with_retry(
        conn,
        """
        SELECT pusd_balance_micro, captured_at FROM collateral_ledger_snapshots
        WHERE captured_at >= ? AND authority_tier = 'CHAIN'
        ORDER BY captured_at ASC LIMIT 1
        """,
        (observed_at,),
    )
    before_row = before[0] if before else None
    after_row = after[0] if after else None
    return before_row, after_row


def _other_fills_in_window(conn: sqlite3.Connection, trade_fact_id: int, start: str, end: str) -> int:
    rows = _run_with_retry(
        conn,
        """
        SELECT COUNT(*) AS n FROM venue_trade_facts
        WHERE trade_fact_id != ? AND state = 'MATCHED'
          AND observed_at > ? AND observed_at < ?
        """,
        (trade_fact_id, start, end),
    )
    if not rows:
        return 0
    return int(rows[0]["n"] or 0)


def verify_fee_cash_effect(conn: sqlite3.Connection, limit: int = 5) -> tuple[list[FeeCheckRow], list[str]]:
    """Compare collateral-ledger balance deltas around recent TAKER fills to price*size.

    Returns (per-fill rows, warnings). A "clean" window has no other MATCHED
    fill between the surrounding before/after snapshots, so the observed
    delta is attributable to this fill alone. Only OUR-side-TAKER fills are
    sampled -- Polymarket's schedule fee is asymmetric (taker pays, maker
    does not), so mixing maker fills in would make a real taker fee look
    "mixed" against genuinely-zero-fee maker fills.
    """

    warnings: list[str] = []
    fills, scanned = fetch_recent_matched_taker_fills(conn, limit=limit)
    if not fills:
        warnings.append(
            f"no MATCHED TAKER-side venue_trade_facts rows found in the last {scanned} MATCHED rows scanned "
            "-- fee check is empty."
        )
        return [], warnings
    if len(fills) < limit:
        warnings.append(
            f"only {len(fills)}/{limit} requested TAKER fills found within the last {scanned} MATCHED rows scanned."
        )

    out: list[FeeCheckRow] = []
    for fill in fills:
        filled_size = float(fill["filled_size"])
        fill_price = float(fill["fill_price"])
        before_row, after_row = _snapshot_around(conn, fill["observed_at"])
        row = FeeCheckRow(
            trade_fact_id=int(fill["trade_fact_id"]),
            observed_at=str(fill["observed_at"]),
            filled_size=filled_size,
            fill_price=fill_price,
            fee_paid_micro=fill["fee_paid_micro"],
            trader_side="TAKER",
            balance_before_micro=before_row["pusd_balance_micro"] if before_row else None,
            balance_after_micro=after_row["pusd_balance_micro"] if after_row else None,
            window_clean=False,
            expected_no_fee_usd=filled_size * fill_price,
            expected_with_fee_usd=filled_size * fill_price + filled_size * polymarket_fee(fill_price),
        )
        if before_row is not None and after_row is not None:
            other = _other_fills_in_window(
                conn, row.trade_fact_id, str(before_row["captured_at"]), str(after_row["captured_at"])
            )
            row.window_clean = other == 0
            row.observed_delta_usd = (int(before_row["pusd_balance_micro"]) - int(after_row["pusd_balance_micro"])) / MICRO
        else:
            warnings.append(f"trade_fact_id {row.trade_fact_id}: missing surrounding collateral snapshot; skipped.")
        out.append(row)
    return out, warnings


def render_fee_check_text(rows: list[FeeCheckRow], warnings: list[str]) -> str:
    lines: list[str] = []
    lines.append("")
    lines.append("=" * 100)
    lines.append("D3: IS THE 'ZERO FEE' CLAIM A REAL CASH EFFECT, OR AN UNPOPULATED COLUMN?")
    lines.append("=" * 100)
    if warnings:
        for w in warnings:
            lines.append(f"  WARNING: {w}")
    lines.append("")
    lines.append(
        f"{'trade_fact_id':>13} {'fee_paid_micro':>14} {'size':>8} {'price':>6} "
        f"{'observed_$':>11} {'no_fee_$':>9} {'with_fee_$':>10} {'clean':>6}"
    )
    lines.append("-" * 100)
    clean_matches_fee = 0
    clean_matches_no_fee = 0
    clean_count = 0
    for r in rows:
        obs = f"{r.observed_delta_usd:.6f}" if r.observed_delta_usd is not None else "n/a"
        lines.append(
            f"{r.trade_fact_id:>13} {str(r.fee_paid_micro):>14} {r.filled_size:>8.2f} {r.fill_price:>6.2f} "
            f"{obs:>11} {r.expected_no_fee_usd:>9.4f} {r.expected_with_fee_usd:>10.4f} "
            f"{'Y' if r.window_clean else 'N':>6}"
        )
        if r.window_clean and r.observed_delta_usd is not None:
            clean_count += 1
            if abs(r.observed_delta_usd - r.expected_with_fee_usd) < 0.001:
                clean_matches_fee += 1
            if abs(r.observed_delta_usd - r.expected_no_fee_usd) < 0.001:
                clean_matches_no_fee += 1

    lines.append("")
    all_fee_micro_null = all(r.fee_paid_micro is None for r in rows)
    lines.append(f"  fee_paid_micro column populated on any sampled fill: {'NO (always NULL)' if all_fee_micro_null else 'yes'}")
    lines.append(f"  clean (single-fill) windows: {clean_count}/{len(rows)}")
    lines.append(f"  clean windows matching price*size + 5% price-dependent fee: {clean_matches_fee}/{clean_count if clean_count else 0}")
    lines.append(f"  clean windows matching price*size with NO fee: {clean_matches_no_fee}/{clean_count if clean_count else 0}")
    lines.append("")
    lines.append("VERDICT:")
    if clean_count == 0:
        lines.append("  Could not isolate a clean single-fill collateral window -- no verdict possible.")
    elif clean_matches_fee == clean_count and clean_matches_no_fee == 0:
        lines.append(
            "  FEE IS REAL. venue_trade_facts.fee_paid_micro is unpopulated (always NULL) on every sampled "
            "fill, but the on-chain/CLOB collateral balance delta around each clean fill matches "
            "price*size PLUS the documented price-dependent 5% fee formula "
            "(src.contracts.execution_price.polymarket_fee) to the cent, and does NOT match a zero-fee "
            "price*size cost. The 'zero fee' claim is FALSE for entries -- it is an unpopulated-column "
            "artifact, not an absent cash effect."
        )
    elif clean_matches_no_fee == clean_count and clean_matches_fee == 0:
        lines.append(
            "  FEE IS ZERO. Every clean single-fill collateral delta matches plain price*size with no fee "
            "component; fee_paid_micro being NULL is consistent with an actually-zero fee, not a hidden one."
        )
    else:
        lines.append(
            "  MIXED/INCONCLUSIVE: some clean windows match the fee-inclusive cost, others do not -- "
            "inspect the per-fill table above before drawing a verdict."
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="Path to zeus_trades.db (read-only)")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of the text table")
    parser.add_argument(
        "--verify-fee",
        action="store_true",
        help="Append the D3 zero-fee cash-effect check on recent MATCHED taker fills",
    )
    parser.add_argument("--fee-sample-size", type=int, default=5, help="Number of recent MATCHED fills to check")
    args = parser.parse_args(argv)

    try:
        conn = _connect_ro(args.db)
    except sqlite3.OperationalError as exc:
        print(f"WARNING: could not open {args.db} read-only after retry: {exc}", file=sys.stderr)
        print(json.dumps({"warnings": [str(exc)], "actionable": [], "bookkeeping_stale": []}))
        return 1

    try:
        rows, warnings = fetch_redemption_rows(conn)
        fee_rows: list[FeeCheckRow] = []
        fee_warnings: list[str] = []
        if args.verify_fee:
            fee_rows, fee_warnings = verify_fee_cash_effect(conn, limit=args.fee_sample_size)
    finally:
        conn.close()

    actionable, stale, totals = aggregate(rows)
    if args.json:
        payload = json.loads(render_json(actionable, stale, totals, warnings))
        if args.verify_fee:
            payload["fee_check"] = {
                "warnings": fee_warnings,
                "fills": [
                    {
                        "trade_fact_id": r.trade_fact_id,
                        "observed_at": r.observed_at,
                        "filled_size": r.filled_size,
                        "fill_price": r.fill_price,
                        "fee_paid_micro": r.fee_paid_micro,
                        "window_clean": r.window_clean,
                        "observed_delta_usd": r.observed_delta_usd,
                        "expected_no_fee_usd": round(r.expected_no_fee_usd, 6),
                        "expected_with_fee_usd": round(r.expected_with_fee_usd, 6),
                    }
                    for r in fee_rows
                ],
            }
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(render_text(actionable, stale, totals, warnings))
        if args.verify_fee:
            print(render_fee_check_text(fee_rows, fee_warnings))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

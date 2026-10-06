#!/usr/bin/env python3
# Created: 2026-10-06
# Last reused or audited: 2026-10-06
# Lifecycle: created=2026-10-06; last_reviewed=2026-10-06; last_reused=never
# Purpose: Book the realized P&L of operator-listed historical positions whose
#   venue-confirmed entry fill was wrongly voided, then held to a finalized
#   chain payout and redeemed. Event-sourced: appends REVIEW_REQUIRED
#   (voided -> active, the T5 restore) + SETTLED (active -> settled) and the
#   matching position_current projection as one atomic pair per position.
# Reuse: DRY-RUN by default; --apply writes state/zeus_trades.db only (INV-37:
#   no other DB is read or written). Re-running after --apply is a no-op keyed
#   by deterministic idempotency_key values.
# Authority basis: AGENTS.md section 2 lifecycle law (voided is terminal; a
#   confirmed fill restores its true phase via REVIEW_REQUIRED);
#   src/state/close_economics.py (R0-a single realized-P&L formula);
#   src/state/ledger.append_many_and_project (canonical event+projection pair);
#   src/state/write_coordinator (unified trade writer lease).
# Provenance audit 2026-10-06 (reused code):
#   CURRENT_REUSABLE: ledger.append_many_and_project, projection guards,
#     close_economics.compute_realized_pnl_usd, fill_dedup economic trade-fact
#     CTEs, write_coordinator.WriteCoordinator.transaction,
#     lifecycle_manager.fold_lifecycle_phase.
#   STALE_REWRITE (not reused): backfill_close_economics.py and
#     backfill_settlement_price_2026_07_25.py (raw position_current UPDATE, no
#     event, legacy per-class lock); backfill_identity_supersession_facts.py
#     (uuid-suffixed event ids, no coordinator lease). Its rule that a
#     historical fact carries its historical occurred_at is kept.
"""Repair historical position economics (operator-approved list, 2026-10-06).

For each listed position, native evidence decides one of three outcomes:

BOOK     All entry fills are CONFIRMED trade facts matched by a PROVEN
         venue_fill_cash_facts row (exact shares and principal); the held
         token's finalized payout is binary (payout_observations,
         chain_rpc_finalized_v1); no Zeus SELL ever filled on the held token;
         no sibling position books the same token; and a verified redemption
         of exactly the filled shares exists. P&L = compute_realized_pnl_usd
         at exit_price 1.0/0.0 (gross of fees, like every other close path;
         fees are reported separately).
NOOP     Already applied, or a sibling row books the same fills at the same
         P&L (booking this row would double-count).
EXCLUDE  Anything else, with every failing reason.

Phase: voided is terminal, so it cannot fold to settled. REVIEW_REQUIRED
restores the position's true phase (active), as
command_recovery.repair_confirmed_phantom_voids does. SETTLED then folds
active -> settled legally. Both events carry the chain redemption time as
occurred_at, so the realized P&L is dated when it actually happened, not
inside today's risk windows.

Cash: the redemption cash is already in the wallet. Only position_events and
position_current are written; no collateral, wallet or settlement-command row
is touched.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

PACKET = "historical_position_economics_2026_10_06"
SOURCE_MODULE = "scripts.repair_historical_position_economics_2026_10_06"
WALLET = "0x6a096d5042cba434521e2cdb95a1fba789a09b7f"
POLYGON_CHAIN_ID = 137
FINALIZED_PAYOUT_SOURCE = "chain_rpc_finalized_v1"
MICRO = Decimal(1_000_000)


@dataclass(frozen=True)
class Redemption:
    """Chain proof that the wallet held the winning token to resolution and
    redeemed it (Polygon CTF ConditionResolution + TransferSingle/Batch and the
    collateral credit, verified 2026-10-06 with eth_getLogs; held-token
    balanceOf is now 0)."""

    resolution_tx: str
    resolution_block: int
    tx_hash: str
    block_number: int
    redeemed_at: str
    payout_micro: int


# The operator-approved scope. None = no verified hold-to-redemption evidence.
TARGETS: dict[str, Redemption | None] = {
    "39db44dc-144": Redemption(
        "0x76a1e5ae3bc8c05cb463d5b8ff71c3ca9b9b1ba8475b9645c7576b57586184d3", 89015362,
        "0x9a5e04db29f298ee07d5762a4944a2b452187d541018069b9b73efeafc00f3f2", 89015369,
        "2026-06-23T15:20:49+00:00", 5_000_000,
    ),
    "e7b9a9cb-7d5": Redemption(
        "0x9cf6aa938100b6464f0f1f571f47f8ae95d59ea6cc4c908ec1f772aeafcb7d41", 89075489,
        "0xca0a52577a19f236ae19d2b38520376018e690fc787acca207db85171e06d99b", 89075497,
        "2026-06-24T16:24:01+00:00", 2_500_000,
    ),
    "d3840f5b-d6a": None,
    "edlia3e4b65ac25aa22e0c854bcdf8041251bc087bc158406e33a9596281924c40a2": Redemption(
        "0xfbfb80da151bdd912065d1eef8893c7aae5e2af07eda93779a0544f6475bc59a", 88237491,
        "0xd4780c8c04e97d095193f870bb500bc7af432eab7706df289b20df703e8b74a5", 88284660,
        "2026-06-10T22:52:55+00:00", 19_000_000,
    ),
    "e4b6c3be-510": None,
}


@dataclass
class Item:
    position_id: str
    status: str = "EXCLUDE"
    reasons: list[str] = field(default_factory=list)
    before: dict | None = None
    after: dict | None = None
    events: list[dict] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)
    expected_max_sequence_no: int = 0

    @property
    def delta(self) -> float:
        if self.status != "BOOK":
            return 0.0
        return float(self.after["realized_pnl_usd"]) - float(self.before["realized_pnl_usd"] or 0.0)


def _key(position_id: str, step: str) -> str:
    return f"{position_id}:{PACKET}:{step}"


def _held(row: sqlite3.Row) -> tuple[str, int]:
    """Held token and its CTF outcome slot (YES=0, NO=1)."""
    if row["direction"] == "buy_no":
        return str(row["no_token_id"] or ""), 1
    if row["direction"] == "buy_yes":
        return str(row["token_id"] or ""), 0
    raise ValueError(f"direction={row['direction']!r}")


def _cash_proof(conn: sqlite3.Connection, *, tx_hash: str, order_id: str, token: str, side: str) -> dict | None:
    """Latest PROVEN chain decode of this order's fill inside tx_hash."""
    rows = conn.execute(
        "SELECT id, proof_json FROM venue_fill_cash_facts "
        "WHERE chain_id = ? AND tx_hash = ? AND wallet = ? AND status = 'PROVEN' ORDER BY id DESC",
        (POLYGON_CHAIN_ID, tx_hash, WALLET),
    ).fetchall()
    for row in rows:
        events = [
            ev
            for ev in (json.loads(row["proof_json"]).get("decoded") or {}).get("events") or []
            if str(ev.get("order_hash") or "").lower() == order_id.lower()
            and str(ev.get("token_id") or "") == token
            and str(ev.get("side") or "") == side
        ]
        if events:
            return {
                "cash_fact_id": int(row["id"]),
                "shares_micro": sum(int(ev["shares_atoms"]) for ev in events),
                "principal_micro": sum(int(ev["principal_atoms"]) for ev in events),
                "fee_micro": sum(int(ev["fee_atoms"]) for ev in events),
            }
    return None


def _fills(conn: sqlite3.Connection, command: sqlite3.Row, token: str, reasons: list[str]) -> list[dict]:
    from src.state.fill_dedup import economic_trade_facts_for_command

    fills = []
    for fact in economic_trade_facts_for_command(conn, str(command["command_id"])):
        qty, price = Decimal(str(fact["filled_size"])), Decimal(str(fact["fill_price"]))
        if qty <= 0:
            continue
        fill = {
            "command_id": command["command_id"],
            "side": command["side"],
            "trade_fact_id": fact["trade_fact_id"],
            "trade_id": fact["trade_id"],
            "state": fact["state"],
            "shares": str(qty),
            "price": str(price),
            "tx_hash": fact["tx_hash"],
        }
        fills.append(fill)
        if fact["state"] != "CONFIRMED":
            reasons.append(f"trade fact {fact['trade_fact_id']} is {fact['state']}, not CONFIRMED")
        proof = _cash_proof(
            conn,
            tx_hash=str(fact["tx_hash"] or ""),
            order_id=str(command["venue_order_id"] or ""),
            token=token,
            side=str(command["side"]),
        )
        if proof is None:
            reasons.append(f"no PROVEN venue_fill_cash_facts for trade {fact['trade_id']}")
            continue
        fill.update(proof)
        # One micro of slack: a VWAP fill_price is not an exact decimal.
        if proof["shares_micro"] != qty * MICRO or abs(proof["principal_micro"] - qty * price * MICRO) > 1:
            reasons.append(f"cash fact {proof['cash_fact_id']} disagrees with trade fact {fact['trade_fact_id']}")
    return fills


def _token_lot(conn: sqlite3.Connection, token: str) -> tuple[list[dict], list[dict], list[str]]:
    """Every Zeus BUY and SELL fill of one token, across all positions."""
    buys, sells, issues = [], [], []
    for command in conn.execute(
        "SELECT command_id, position_id, venue_order_id, side FROM venue_commands WHERE token_id = ? "
        "ORDER BY created_at, command_id",
        (token,),
    ).fetchall():
        fills = [dict(f, position_id=command["position_id"]) for f in _fills(conn, command, token, issues)]
        (sells if command["side"] == "SELL" else buys).extend(fills)
    return buys, sells, issues


def _lot_sale_economics(buys: list[dict], sells: list[dict], issues: list[str]) -> dict:
    """Realized economics of a token lot closed by SELL fills."""
    from src.state.close_economics import compute_realized_pnl_usd

    def notional(f: dict) -> Decimal:
        return (Decimal(f["shares"]) * Decimal(f["price"])).quantize(Decimal("0.000001"))

    bought = sum(Decimal(f["shares"]) for f in buys)
    sold = sum(Decimal(f["shares"]) for f in sells)
    cost = sum(notional(f) for f in buys)
    proceeds = sum(notional(f) for f in sells)
    fees = Decimal(sum(int(f.get("fee_micro", 0)) for f in buys + sells)) / MICRO
    by_position: dict[str, Decimal] = {}
    for f in buys:
        by_position[f["position_id"]] = by_position.get(f["position_id"], Decimal(0)) + Decimal(f["shares"])
    closed = bought == sold and bought > 0
    return {
        "shares_bought": str(bought),
        "shares_sold": str(sold),
        "cost_usd": str(cost),
        "proceeds_usd": str(proceeds),
        "fees_usd": str(fees),
        "gross_realized_pnl_usd": compute_realized_pnl_usd(
            shares=float(sold),
            exit_price=float(proceeds / sold),
            cost_basis_usd=float(cost),
            entry_price=float(cost / bought),
        ) if closed else None,
        "net_of_fees_usd": str(proceeds - cost - fees) if closed else None,
        "lot_closed_by_sells": closed,
        "bought_shares_by_position": {k: str(v) for k, v in by_position.items()},
        "sold_by_positions": sorted({f["position_id"] for f in sells}),
        "unproven_fill_issues": issues,
    }


def _payout(conn: sqlite3.Connection, condition_id: str, reasons: list[str]) -> dict | None:
    """Finalized binary payout, the predicate harvester_pnl_resolver applies."""
    rows = [
        conn.execute(
            "SELECT id, payout_numerator, payout_denominator, state, block_number, block_hash, source "
            "FROM payout_observations WHERE condition_id = ? AND outcome_index = ? ORDER BY id DESC LIMIT 1",
            (condition_id, index),
        ).fetchone()
        for index in (0, 1)
    ]
    if any(
        r is None
        or r["source"] != FINALIZED_PAYOUT_SOURCE
        or r["state"] not in ("RESOLVED_ZERO", "RESOLVED_NONZERO")
        or r["block_number"] is None
        or not str(r["block_hash"] or "").strip()
        for r in rows
    ):
        reasons.append("no finalized binary payout_observations for the condition")
        return None
    numerators = [int(r["payout_numerator"]) for r in rows]
    denominator = int(rows[0]["payout_denominator"])
    if denominator <= 0 or int(rows[1]["payout_denominator"]) != denominator or sorted(numerators) != [0, denominator]:
        reasons.append(f"payout vector {numerators}/{denominator} is not binary")
        return None
    return {
        "observation_ids": [int(r["id"]) for r in rows],
        "numerators": numerators,
        "denominator": denominator,
        "block_number": int(rows[0]["block_number"]),
    }


def _assess(conn: sqlite3.Connection, position_id: str, redemption: Redemption | None, *, now: str) -> Item:
    from src.state.close_economics import compute_realized_pnl_usd
    from src.state.lifecycle_manager import fold_lifecycle_phase
    from src.state.projection import CANONICAL_POSITION_CURRENT_COLUMNS

    item = Item(position_id)
    row = conn.execute("SELECT * FROM position_current WHERE position_id = ?", (position_id,)).fetchone()
    if row is None:
        item.reasons.append("position_current row missing")
        return item
    item.before = {c: row[c] for c in CANONICAL_POSITION_CURRENT_COLUMNS}
    if conn.execute("SELECT 1 FROM position_events WHERE idempotency_key = ?", (_key(position_id, "settled"),)).fetchone():
        item.status, item.reasons = "NOOP", [f"already applied ({_key(position_id, 'settled')})"]
        return item
    reasons = item.reasons
    try:
        token, held_index = _held(row)
    except ValueError as exc:
        reasons.append(f"unsupported {exc}")
        return item
    ev = item.evidence
    ev.update(held_token=token, held_outcome_index=held_index)
    if row["phase"] != "voided":
        reasons.append(f"phase is {row['phase']!r}, this repair restores wrongly voided rows only")

    commands = conn.execute(
        "SELECT command_id, venue_order_id, token_id, side, size, snapshot_id FROM venue_commands "
        "WHERE position_id = ? AND intent_kind = 'ENTRY' ORDER BY created_at, command_id",
        (position_id,),
    ).fetchall()
    entry = []
    for command in commands:
        if command["token_id"] != token or command["side"] != "BUY":
            reasons.append(f"ENTRY {command['command_id']} is not a BUY of the held token")
            continue
        entry += _fills(conn, command, token, reasons)
    ev["entry_fills"] = entry
    if not entry:
        reasons.append("no positive ENTRY fill")
        return item

    snapshot = conn.execute(
        "SELECT condition_id, yes_token_id, no_token_id, event_slug FROM executable_market_snapshots WHERE snapshot_id = ?",
        (commands[0]["snapshot_id"],),
    ).fetchone()
    held_by_slot = None if snapshot is None else (snapshot["yes_token_id"], snapshot["no_token_id"])[held_index]
    if snapshot is None or snapshot["condition_id"] != row["condition_id"] or held_by_slot != token:
        reasons.append("entry snapshot does not bind the held token to this condition's outcome slot")
    ev["market_slug"] = None if snapshot is None else snapshot["event_slug"]

    payout = _payout(conn, str(row["condition_id"] or ""), reasons)
    ev["payout"] = payout

    siblings = []
    for sib in conn.execute(
        "SELECT position_id, phase, direction, token_id, no_token_id, shares, realized_pnl_usd FROM position_current "
        "WHERE (token_id = ? OR no_token_id = ?) AND position_id != ?",
        (token, token, position_id),
    ).fetchall():
        try:
            if _held(sib)[0] != token:
                continue
        except ValueError:
            continue
        booked = sib["realized_pnl_usd"] is not None and (
            sib["phase"] in ("settled", "economically_closed") or float(sib["realized_pnl_usd"]) != 0.0
        )
        siblings.append({k: sib[k] for k in ("position_id", "phase", "shares", "realized_pnl_usd")} | {"booked": booked})
    ev["siblings"] = siblings

    buys, sells, lot_issues = _token_lot(conn, token)
    if sells:
        lot = _lot_sale_economics(buys, sells, lot_issues)
        ev["token_lot"] = lot | {"sell_fills": sells}
        booked_now = {s["position_id"]: s["realized_pnl_usd"] for s in siblings if s["realized_pnl_usd"] is not None}
        reasons.append(
            f"held token was SOLD ({lot['shares_sold']} shares for ${lot['proceeds_usd']} by "
            f"{sorted({f['command_id'] for f in sells})}), not held to settlement; the "
            f"{lot['shares_bought']}-share lot was entered by {len(lot['bought_shares_by_position'])} "
            f"position rows {lot['bought_shares_by_position']}, whose rows now carry realized_pnl_usd "
            f"{booked_now} instead of the lot's {lot['gross_realized_pnl_usd']} gross sale P&L. "
            "Correcting that needs an economic-close + cross-row identity decision, not this settlement repair"
        )
        return item

    shares = sum(Decimal(f["shares"]) for f in entry)
    cost = sum(Decimal(f["shares"]) * Decimal(f["price"]) for f in entry)
    fees = Decimal(sum(int(f.get("fee_micro", 0)) for f in entry)) / MICRO
    won = payout is not None and payout["numerators"][held_index] == payout["denominator"]
    exit_price = 1.0 if won else 0.0
    pnl = compute_realized_pnl_usd(
        shares=float(shares), exit_price=exit_price, cost_basis_usd=float(cost), entry_price=float(cost / shares)
    )
    ev["economics"] = {
        "shares": str(shares),
        "cost_basis_usd": str(cost),
        "position_won": won,
        "exit_price": exit_price,
        "realized_pnl_usd": pnl,
        "fees_usd": str(fees),
        "realized_net_of_fees_usd": str(shares * Decimal(exit_price) - cost - fees),
    }

    booked = [s for s in siblings if s["booked"]]
    if booked:
        same = (
            len(booked) == 1
            and Decimal(str(booked[0]["shares"])) == shares
            and abs(float(booked[0]["realized_pnl_usd"]) - pnl) < 0.005
        )
        listing = ", ".join(f"{s['position_id']} ({s['phase']}, realized={s['realized_pnl_usd']})" for s in booked)
        if same:
            item.status, item.reasons = "NOOP", [
                f"fills already booked on {listing}, equal to the fill-derived {pnl:+.2f}; booking here would double-count"
            ]
            return item
        reasons.append(f"held token already booked on {listing}; booking here would double-count")

    if redemption is None:
        reasons.append("no verified hold-to-redemption chain evidence for this position")
    elif not won:
        reasons.append("redemption evidence given for a position whose held token lost")
    elif Decimal(redemption.payout_micro) != shares * MICRO:
        reasons.append(f"redeemed {redemption.payout_micro} micro != {shares} filled shares")
    if reasons:
        return item

    command_sizes = sum(Decimal(str(c["size"])) for c in commands)
    remainder = command_sizes > shares
    item.after = dict(item.before) | {
        "phase": fold_lifecycle_phase("active", "settled").value,
        "shares": float(shares),
        "cost_basis_usd": float(cost),
        "size_usd": float(cost),
        "entry_price": float(cost / shares),
        "fill_authority": "cancelled_remainder" if remainder else "venue_confirmed_full",
        "order_status": "cancelled_remainder" if remainder else "filled",
        "chain_state": "closed_redeemed",
        "chain_shares": 0.0,
        "realized_pnl_usd": pnl,
        "exit_price": exit_price,
        "settlement_price": exit_price,
        "settled_at": redemption.redeemed_at,
        "exit_reason": "SETTLEMENT",
        "updated_at": now,
    }
    latest = conn.execute(
        "SELECT sequence_no, env FROM position_events WHERE position_id = ? ORDER BY sequence_no DESC LIMIT 1",
        (position_id,),
    ).fetchone()
    item.expected_max_sequence_no = int(latest["sequence_no"]) if latest else 0
    evidence_refs = {
        "packet": PACKET,
        "entry_fills": entry,
        "payout_observation_ids": payout["observation_ids"],
        "payout_numerators": payout["numerators"],
        "condition_resolution": {
            "tx_hash": redemption.resolution_tx,
            "block_number": redemption.resolution_block,
        },
        "redemption": {
            "chain_id": POLYGON_CHAIN_ID,
            "wallet": WALLET,
            "tx_hash": redemption.tx_hash,
            "block_number": redemption.block_number,
            "payout_micro": redemption.payout_micro,
        },
        "fees_usd": str(fees),
        "realized_net_of_fees_usd": ev["economics"]["realized_net_of_fees_usd"],
        "repair_recorded_at": now,
    }
    common = {
        "event_version": 1,
        "occurred_at": redemption.redeemed_at,
        "strategy_key": row["strategy_key"],
        "decision_id": None,
        "snapshot_id": row["decision_snapshot_id"],
        "order_id": row["order_id"],
        "source_module": SOURCE_MODULE,
        "env": (latest["env"] if latest else None) or "live",
        "position_id": position_id,
    }
    item.events = [
        common | {
            "event_id": _key(position_id, "review_required"),
            "idempotency_key": _key(position_id, "review_required"),
            "sequence_no": item.expected_max_sequence_no + 1,
            "event_type": "REVIEW_REQUIRED",
            "phase_before": row["phase"],
            "phase_after": fold_lifecycle_phase("active", "active").value,
            "command_id": commands[0]["command_id"],
            "caused_by": "confirmed_entry_fill_wrongly_voided",
            "venue_status": "review_required",
            "payload_json": json.dumps(
                {
                    "schema_version": 1,
                    "reason": "confirmed_entry_fill_wrongly_voided",
                    "proof_class": "venue_confirmed_fill_with_proven_cash_fact",
                    "phase_before": row["phase"],
                    "phase_after": "active",
                    "held_token_id": token,
                    "evidence_refs": evidence_refs,
                },
                default=str,
                sort_keys=True,
            ),
        },
        common | {
            "event_id": _key(position_id, "settled"),
            "idempotency_key": _key(position_id, "settled"),
            "sequence_no": item.expected_max_sequence_no + 2,
            "event_type": "SETTLED",
            "phase_before": "active",
            "phase_after": item.after["phase"],
            "command_id": None,
            "caused_by": "chain_payout_redeemed",
            "venue_status": None,
            "payload_json": json.dumps(
                {
                    "contract_version": "position_settled.v1",
                    "winning_bin": row["bin_label"] if held_index == 0 else "",
                    "position_bin": row["bin_label"],
                    "won": held_index == 0,
                    "market_bin_won": held_index == 0,
                    "position_won": True,
                    "outcome": 1,
                    "p_posterior": row["p_posterior"],
                    "exit_price": exit_price,
                    "pnl": pnl,
                    "exit_reason": "SETTLEMENT",
                    "settlement_authority": "VENUE_RESOLVED",
                    "settlement_truth_source": "trades.payout_observations",
                    "settlement_market_slug": ev["market_slug"] or "",
                    "settlement_temperature_metric": row["temperature_metric"],
                    "settlement_source": "polymarket_chain_rpc_finalized_v1",
                    "settlement_value": None,
                    "evidence_refs": evidence_refs,
                },
                default=str,
                sort_keys=True,
            ),
        },
    ]
    item.status = "BOOK"
    return item


def plan(conn: sqlite3.Connection, *, now: str, targets: dict[str, Redemption | None] = TARGETS) -> list[Item]:
    return [_assess(conn, pid, redemption, now=now) for pid, redemption in targets.items()]


def totals(conn: sqlite3.Connection) -> dict:
    count, with_pnl, total = conn.execute(
        "SELECT COUNT(*), COUNT(realized_pnl_usd), TOTAL(realized_pnl_usd) FROM position_current"
    ).fetchone()
    return {"positions": count, "positions_with_realized_pnl": with_pnl, "realized_pnl_usd": total}


def apply(trades_db: Path, items: list[Item]) -> None:
    """Append every BOOK item in one coordinated trade-DB transaction.

    Each item's before-state is re-checked under the lease (CAS); any drift
    rolls the whole batch back.
    """
    from src.state.db import connect_existing_trade_db_without_journal_bootstrap
    from src.state.db_writer_lock import WriteClass
    from src.state.ledger import append_many_and_project
    from src.state.write_coordinator import DBIdentity, WriteCoordinator, WritePriority

    def connect(path: Path) -> sqlite3.Connection:
        conn = connect_existing_trade_db_without_journal_bootstrap(path)
        conn.row_factory = sqlite3.Row
        return conn

    book = [i for i in items if i.status == "BOOK"]
    coordinator = WriteCoordinator({DBIdentity.TRADE: trades_db})
    with coordinator.transaction(
        (DBIdentity.TRADE,),
        owner=SOURCE_MODULE,
        write_class=WriteClass.BULK,
        priority=WritePriority.STANDARD,
        deadline_ms=60_000,
        max_hold_ms=5_000,
        connection_factory=connect,
    ) as tx:
        conn = tx.connection
        for item in book:
            row = conn.execute(
                "SELECT phase, realized_pnl_usd, updated_at FROM position_current WHERE position_id = ?",
                (item.position_id,),
            ).fetchone()
            seq = conn.execute(
                "SELECT COALESCE(MAX(sequence_no), 0) FROM position_events WHERE position_id = ?",
                (item.position_id,),
            ).fetchone()[0]
            current = None if row is None else (row["phase"], row["realized_pnl_usd"], row["updated_at"])
            planned = (item.before["phase"], item.before["realized_pnl_usd"], item.before["updated_at"])
            if current != planned or int(seq) != item.expected_max_sequence_no:
                raise RuntimeError(f"plan is stale for {item.position_id}: re-run the dry-run")
            append_many_and_project(conn, item.events, item.after)


def _read_only(path: Path) -> sqlite3.Connection:
    """file:...?mode=ro + PRAGMA query_only through the canonical reader."""
    from src.state.db import _connect_read_only

    return _connect_read_only(path)


def _dump(value) -> str:
    return json.dumps(value, indent=2, sort_keys=True, default=str)


def report(items: list[Item], before: dict) -> None:
    for item in items:
        print(f"=== {item.position_id}: {item.status}")
        for reason in item.reasons:
            print(f"  reason: {reason}")
        print("  before:", _dump(item.before))
        print("  evidence:", _dump(item.evidence))
        if item.status != "BOOK":
            continue
        for event in item.events:
            print("  append event:", _dump(event | {"payload_json": json.loads(event["payload_json"])}))
        print("  after:", _dump(item.after))
        print("  changed:", _dump({k: [item.before[k], v] for k, v in item.after.items() if item.before[k] != v}))
        print(f"  realized_pnl_usd delta: {item.delta:+.2f}")
    delta = sum(i.delta for i in items)
    print("system total before:", _dump(before))
    print(
        "system total after:",
        _dump(before | {
            "positions_with_realized_pnl": before["positions_with_realized_pnl"]
            + sum(1 for i in items if i.status == "BOOK" and i.before["realized_pnl_usd"] is None),
            "realized_pnl_usd": before["realized_pnl_usd"] + delta,
        }),
    )
    print(f"system realized_pnl_usd delta: {delta:+.2f}")


def main(argv: list[str] | None = None) -> int:
    from src.state.db import _zeus_trade_db_path

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--trades-db", type=Path, default=None, help="trade DB path (default: canonical state/zeus_trades.db)")
    ap.add_argument("--apply", action="store_true", help="write; default is a read-only dry-run")
    args = ap.parse_args(argv)
    path = (args.trades_db or _zeus_trade_db_path()).resolve()
    if not path.is_file():
        print(f"trade DB not found: {path}", file=sys.stderr)
        return 2

    now = datetime.now(timezone.utc).isoformat()
    conn = _read_only(path)
    try:
        items = plan(conn, now=now)
        before = totals(conn)
    finally:
        conn.close()
    print(f"{PACKET} trades_db={path} mode={'APPLY' if args.apply else 'DRY-RUN'} now={now}")
    report(items, before)
    if not args.apply:
        print("DRY-RUN: nothing written. Re-run with --apply to write.")
        return 0
    if not any(i.status == "BOOK" for i in items):
        print("APPLY: nothing to book.")
        return 0
    apply(path, items)
    conn = _read_only(path)
    try:
        print("APPLIED. system total now:", _dump(totals(conn)))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

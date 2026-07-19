"""Chain-mirror reconciliation core: classify + (optionally) repair position_current
against venue chain truth.

Authority basis: operator directive 2026-07-04 (root AGENTS.md §2 reconciliation
order Chain > Chronicler > Portfolio); design doc
docs/rebuild/chain_mirror_state_model_2026-07-04.md.

Public surface:
    ChainPositionFact         — one venue data-api position row.
    LocalPositionRow          — one position_current row, read-only view.
    MirrorFinding             — one classification result (may or may not imply a write).
    grade_bin                 — pure win/lose/unknown grading helper.
    classify_local_position   — classify a single local row against chain truth (pure).
    classify_chain_only_asset — classify a chain token with no matching local row (pure).
    load_chain_positions_by_asset(raw_positions) -> dict[str, ChainPositionFact]
    load_local_position_rows(conn) -> list[LocalPositionRow]
    load_settlement_lookup(forecasts_conn) -> dict[tuple, SettlementFact]
    is_zeus_origin_asset(conn, asset_id) -> bool
    has_open_orders_for_position(conn, position_id) -> bool
    apply_size_correction_finding(conn, finding, *, now) — shared CHAIN_SIZE_CORRECTED
        writer; also called directly by src.state.chain_reconciliation's size-mismatch
        branch (P0b) as the ungated fallback when no canonical baseline is available.
    reconcile(conn_trades, conn_forecasts, chain_by_asset, *, apply, now) -> ReconcileReport
    run_cycle() — scheduler entrypoint (fetches chain positions + DB conns,
        calls reconcile(apply=True), commits). R4-b: moved from
        src.main::_chain_mirror_reconcile_cycle (main.py registers it on a
        10-minute APScheduler cadence).

No network I/O and no venue mutation happens in this module. The CLI wrapper
(scripts/reconcile_chain_mirror.py) owns adapter construction; this module only
consumes already-fetched chain facts.

P0b (2026-07-04, docs/rebuild/chain_mirror_state_model_2026-07-04.md §5
follow-up): the REVIEW_OPEN_ABSENT class (open-phase row, held token absent,
market unresolved) escalates to CLOSED_EXITED only for a fill-unproven local
projection after the SAME absence appears on two consecutive mirror runs with
zero open orders. A confirmed fill remains open for review until exit,
redemption, transfer, or settlement evidence explains the disappearance;
Data API omission alone cannot erase real economic exposure.
The "has this been seen before" signal is a lightweight, append-only
REVIEW_REQUIRED marker event (phase_after == phase_before, no lifecycle
mutation) — see _has_prior_review_open_absent_marker.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# Market-rule classification labels (registered in
# architecture/money_path_objects.yaml::chain_mirror_reconciliation_classification).
CLOSED_REDEEMED = "closed_redeemed"
CLOSED_WORTHLESS = "closed_worthless"
SIZE_CORRECTED = "size_corrected"
REDEEMABLE = "redeemable"
REVIEW_OPEN_ABSENT = "review_open_absent"
# P0b (2026-07-04): force-resolve classification for an _OPEN_LIKE_PHASES row
# whose fill-unproven held token has been absent across two consecutive mirror
# runs (market still unresolved, zero open orders in flight). Folds to VOIDED
# with chain_state="closed_exited" recording why. A confirmed entry fill is a
# separate economic fact and never enters this administrative phantom path.
# Registered in architecture/money_path_objects.yaml::chain_mirror_reconciliation_classification.
CLOSED_EXITED = "closed_exited"
MISSING_LOCAL_ROW = "missing_local_row"
FOREIGN = "foreign"
UNGRADEABLE = "ungradeable"
CONSISTENT = "consistent"

# Non-terminal venue_commands states (mirrors executor.py's
# _ENTRY_DUPLICATE_OPEN_COMMAND_STATES / status_summary.py's
# _OPEN_ENTRY_COMMAND_STATES vocabulary) — used only by the force-resolve
# guard below to refuse voiding a position with an order still in flight.
_OPEN_VENUE_COMMAND_STATES = frozenset(
    {
        "INTENT_CREATED",
        "SNAPSHOT_BOUND",
        "SIGNED_PERSISTED",
        "POSTING",
        "POST_ACKED",
        "SUBMITTING",
        "ACKED",
        "PARTIAL",
        "SUBMITTED",
        "UNKNOWN",
    }
)

_OPEN_NO_FILL_ENTRY_ORDER_FACT_STATES = frozenset(
    {"LIVE", "RESTING", "PARTIALLY_MATCHED"}
)

_SIZE_MISMATCH_TOLERANCE = 0.05  # shares; below this the chain/local delta is noise.

# Phases considered "still open" for the purposes of the REVIEW (e) class —
# mirrors the phases that require an on-chain holding per position_current's
# own CHECK vocabulary (src/state/db.py CREATE TABLE position_current).
# T5 (docs/rebuild/quarantine_excision_2026-07-11.md): 'quarantined' retired
# from that CHECK vocabulary post-migration, so it is retired here too.
_OPEN_LIKE_PHASES = frozenset(
    {"pending_entry", "active", "day0_window", "pending_exit"}
)

# Already-closed phases. The reconciler never re-touches these: no grading
# close (they're already resolved one way or another), no size correction
# (a "wrong" chain_shares on already-terminal history is not this
# reconciler's concern, and multiple historical rows can legitimately share
# the same physical token — see the guard in classify_local_position).
_TERMINAL_CLOSED_PHASES = frozenset({"settled", "voided", "admin_closed", "economically_closed"})


@dataclass(frozen=True)
class ChainPositionFact:
    """One row from PolymarketClient.get_positions_from_api()."""

    token_id: str
    condition_id: str
    size: float
    redeemable: bool
    current_value: float
    side: str
    title: str = ""

    @classmethod
    def from_api_dict(cls, item: dict) -> "ChainPositionFact":
        return cls(
            token_id=str(item.get("token_id") or ""),
            condition_id=str(item.get("condition_id") or ""),
            size=float(item.get("size") or 0.0),
            redeemable=bool(item.get("redeemable", False)),
            current_value=float(item.get("current_value") or 0.0),
            side=str(item.get("side") or ""),
            title=str(item.get("title") or ""),
        )


@dataclass(frozen=True)
class LocalPositionRow:
    """Read-only view of a position_current row relevant to chain-mirroring."""

    position_id: str
    phase: str
    chain_state: str
    city: str
    target_date: str
    temperature_metric: str
    bin_label: str
    direction: str
    token_id: str
    no_token_id: str
    condition_id: str
    chain_shares: Optional[float]
    shares: Optional[float]
    strategy_key: str

    def held_token_id(self) -> str:
        if self.direction == "buy_no":
            return self.no_token_id
        return self.token_id

    def local_reported_shares(self) -> float:
        for value in (self.chain_shares, self.shares):
            if value is not None:
                return float(value)
        return 0.0


@dataclass(frozen=True)
class SettlementFact:
    winning_bin: str
    authority: str
    settlement_value: object = None
    settlement_source: str = ""
    market_slug: str = ""


@dataclass(frozen=True)
class MirrorFinding:
    classification: str
    position_id: Optional[str]
    asset: Optional[str]
    writes: bool
    details: dict = field(default_factory=dict)


@dataclass
class ReconcileReport:
    generated_at: str
    dry_run: bool
    findings: list[MirrorFinding] = field(default_factory=list)
    applied: int = 0
    # R2-core hole closure (b) (R0 verifier finding, docs/rebuild/EXECUTION_MASTER_2026-07-07.md
    # §E R2 item 4b): per-row isolation errors from reconcile()'s main loop --
    # a raising position no longer aborts the whole pass (see reconcile()).
    errors: list[dict] = field(default_factory=list)

    def by_classification(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for f in self.findings:
            counts[f.classification] = counts.get(f.classification, 0) + 1
        return counts

    def to_json_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "dry_run": self.dry_run,
            "applied": self.applied,
            "counts": self.by_classification(),
            "errors": self.errors,
            "findings": [
                {
                    "classification": f.classification,
                    "position_id": f.position_id,
                    "asset": f.asset,
                    "writes": f.writes,
                    "details": f.details,
                }
                for f in self.findings
            ],
        }


def grade_bin(bin_label: str, direction: str, winning_bin: str) -> Optional[bool]:
    """Pure win/lose grading. Returns None when ungradeable (mirrors
    src.execution.harvester._parsed_temperature_bins_equivalent semantics: an
    unparseable/mismatched-unit comparison must never silently grade a loss).
    """
    from src.execution.harvester import _parsed_temperature_bins_equivalent

    bin_matches = _parsed_temperature_bins_equivalent(bin_label, winning_bin)
    if bin_matches is None:
        return None
    if direction == "buy_yes":
        return bool(bin_matches)
    if direction == "buy_no":
        return not bool(bin_matches)
    return None


def classify_local_position(
    row: LocalPositionRow,
    chain_by_asset: dict[str, ChainPositionFact],
    settlement_by_key: dict[tuple, SettlementFact],
    *,
    prior_review_open_absent: bool = False,
    has_open_orders: bool = False,
    has_confirmed_entry_fill: bool = False,
) -> MirrorFinding:
    """Classify a single local position_current row against chain truth. Pure.

    ``prior_review_open_absent`` and ``has_open_orders`` are pre-computed by
    the (DB-touching) caller — see reconcile()'s loop and
    _has_prior_review_open_absent_marker / has_open_orders_for_position —
    so this function itself stays pure/DB-free and independently unit-testable.
    """

    held_token = row.held_token_id()
    chain_fact = chain_by_asset.get(held_token) if held_token else None
    settlement_key = (row.city, row.target_date, row.temperature_metric)
    settlement = settlement_by_key.get(settlement_key)
    market_resolved = settlement is not None and settlement.authority == "VERIFIED"

    if chain_fact is None:
        # Held token absent from the chain snapshot.
        if not market_resolved:
            if row.phase in _OPEN_LIKE_PHASES:
                # P0b: escalate a fill-unproven projection ONLY once the SAME
                # absence has been seen on a prior mirror run with nothing open
                # in flight. A confirmed fill needs economic-close evidence.
                # A single absent read stays a REVIEW finding — the operator's
                # explicit instruction for the Manila ce105753-e91 case: one
                # read is ambiguous; two reads only prove projection absence,
                # never what happened to a confirmed economic holding.
                if (
                    prior_review_open_absent
                    and not has_open_orders
                    and not has_confirmed_entry_fill
                ):
                    return MirrorFinding(
                        classification=CLOSED_EXITED,
                        position_id=row.position_id,
                        asset=held_token,
                        writes=True,
                        details={
                            "reason": (
                                "held_token_absent_two_consecutive_mirror_runs_"
                                "market_unresolved_no_open_orders"
                            ),
                            "phase_before": row.phase,
                            "chain_state_before": row.chain_state,
                            "city": row.city,
                            "target_date": row.target_date,
                        },
                    )
                return MirrorFinding(
                    classification=REVIEW_OPEN_ABSENT,
                    position_id=row.position_id,
                    asset=held_token,
                    # writes=False preserved: REVIEW_OPEN_ABSENT itself is
                    # still a non-mutating finding (no phase/chain_state
                    # change). reconcile() appends the append-only
                    # REVIEW_REQUIRED provenance marker for this
                    # classification unconditionally (see
                    # _apply_review_marker_finding) — that marker is
                    # bookkeeping for the two-consecutive-runs threshold, not
                    # a "repair", so it is dispatched independently of `writes`.
                    writes=False,
                    details={
                        "reason": (
                            "confirmed_entry_fill_token_absent_market_not_resolved"
                            if has_confirmed_entry_fill
                            else "held_token_absent_market_not_resolved"
                        ),
                        "phase": row.phase,
                        "chain_state": row.chain_state,
                        "city": row.city,
                        "target_date": row.target_date,
                    },
                )
            return MirrorFinding(
                classification=CONSISTENT,
                position_id=row.position_id,
                asset=held_token,
                writes=False,
                details={"reason": "already_closed_no_chain_evidence_needed"},
            )
        if row.phase in _TERMINAL_CLOSED_PHASES:
            return MirrorFinding(
                classification=CONSISTENT,
                position_id=row.position_id,
                asset=held_token,
                writes=False,
                details={"reason": "already_terminal"},
            )
        won = grade_bin(row.bin_label, row.direction, settlement.winning_bin)
        if won is None:
            return MirrorFinding(
                classification=UNGRADEABLE,
                position_id=row.position_id,
                asset=held_token,
                writes=False,
                details={
                    "reason": "bin_not_comparable_to_winning_bin",
                    "bin_label": row.bin_label,
                    "winning_bin": settlement.winning_bin,
                },
            )
        classification = CLOSED_REDEEMED if won else CLOSED_WORTHLESS
        return MirrorFinding(
            classification=classification,
            position_id=row.position_id,
            asset=held_token,
            writes=True,
            details={
                "won": won,
                "winning_bin": settlement.winning_bin,
                "settlement_value": settlement.settlement_value,
                "settlement_source": settlement.settlement_source,
                "market_slug": settlement.market_slug,
                "phase_before": row.phase,
                "chain_state_before": row.chain_state,
                "chain_absent": True,
            },
        )

    # Chain evidence present for the held token.
    if row.phase in _TERMINAL_CLOSED_PHASES:
        # Already-closed rows are history. A size "correction" against a
        # closed row is out of this reconciler's scope AND risky: multiple
        # historical (e.g. voided) rows can reference the SAME physical
        # token (a pre-existing local duplicate-row condition this
        # reconciler does not attempt to deduplicate — see
        # src/state/position_duplicate_consolidator.py for that concern).
        # Writing the same chain size onto every one of them would be a
        # multi-row over-attribution of a single wallet balance, exactly
        # the counting-error class this reconciler exists to eliminate, not
        # create. Terminal rows are therefore always CONSISTENT here.
        return MirrorFinding(
            classification=CONSISTENT,
            position_id=row.position_id,
            asset=held_token,
            writes=False,
            details={"reason": "already_terminal_no_size_correction", "phase": row.phase},
        )
    local_shares = row.local_reported_shares()
    delta = abs(chain_fact.size - local_shares)
    if delta > _SIZE_MISMATCH_TOLERANCE:
        return MirrorFinding(
            classification=SIZE_CORRECTED,
            position_id=row.position_id,
            asset=held_token,
            writes=True,
            details={
                "chain_size": chain_fact.size,
                "local_shares": local_shares,
                "delta": delta,
            },
        )

    if market_resolved and row.phase not in _TERMINAL_CLOSED_PHASES:
        won = grade_bin(row.bin_label, row.direction, settlement.winning_bin)
        if won is None:
            return MirrorFinding(
                classification=UNGRADEABLE,
                position_id=row.position_id,
                asset=held_token,
                writes=False,
                details={
                    "reason": "bin_not_comparable_to_winning_bin",
                    "bin_label": row.bin_label,
                    "winning_bin": settlement.winning_bin,
                },
            )
        if won:
            return MirrorFinding(
                classification=REDEEMABLE,
                position_id=row.position_id,
                asset=held_token,
                writes=True,
                details={
                    "won": True,
                    "winning_bin": settlement.winning_bin,
                    "settlement_value": settlement.settlement_value,
                    "settlement_source": settlement.settlement_source,
                    "market_slug": settlement.market_slug,
                    "phase_before": row.phase,
                    "chain_state_before": row.chain_state,
                    "chain_absent": False,
                    "chain_size": chain_fact.size,
                },
            )
        return MirrorFinding(
            classification=CLOSED_WORTHLESS,
            position_id=row.position_id,
            asset=held_token,
            writes=True,
            details={
                "won": False,
                "winning_bin": settlement.winning_bin,
                "settlement_value": settlement.settlement_value,
                "settlement_source": settlement.settlement_source,
                "market_slug": settlement.market_slug,
                "phase_before": row.phase,
                "chain_state_before": row.chain_state,
                "chain_absent": False,
                "chain_size": chain_fact.size,
            },
        )

    if prior_review_open_absent:
        return MirrorFinding(
            classification=SIZE_CORRECTED,
            position_id=row.position_id,
            asset=held_token,
            writes=True,
            details={
                "reason": "chain_reappeared_after_review_absence",
                "chain_size": chain_fact.size,
                "local_shares": local_shares,
                "delta": delta,
                "shares_unchanged": True,
            },
        )

    return MirrorFinding(
        classification=CONSISTENT,
        position_id=row.position_id,
        asset=held_token,
        writes=False,
        details={"chain_size": chain_fact.size, "local_shares": local_shares},
    )


def classify_chain_only_asset(
    asset: str,
    chain_fact: ChainPositionFact,
    matched_local_assets: set[str],
    is_zeus_origin: bool,
) -> Optional[MirrorFinding]:
    """Classify a chain token with no matching local position_current row. Pure.

    Returns None when the asset WAS matched to a local row elsewhere (caller
    should only invoke this for the residual chain-only set).
    """
    if asset in matched_local_assets:
        return None
    if is_zeus_origin:
        return MirrorFinding(
            classification=MISSING_LOCAL_ROW,
            position_id=None,
            asset=asset,
            writes=False,
            details={
                "reason": "zeus_origin_token_has_no_position_current_row",
                "size": chain_fact.size,
                "redeemable": chain_fact.redeemable,
                "current_value": chain_fact.current_value,
                "title": chain_fact.title,
                "condition_id": chain_fact.condition_id,
            },
        )
    return MirrorFinding(
        classification=FOREIGN,
        position_id=None,
        asset=asset,
        writes=False,
        details={
            "reason": "no_zeus_origin_never_adopted",
            "size": chain_fact.size,
            "redeemable": chain_fact.redeemable,
            "current_value": chain_fact.current_value,
            "title": chain_fact.title,
            "condition_id": chain_fact.condition_id,
        },
    )


def load_chain_positions_by_asset(raw_positions: list[dict]) -> dict[str, ChainPositionFact]:
    out: dict[str, ChainPositionFact] = {}
    for item in raw_positions:
        fact = ChainPositionFact.from_api_dict(item)
        if fact.token_id:
            out[fact.token_id] = fact
    return out


_LOCAL_ROW_COLUMNS = (
    "position_id", "phase", "chain_state", "city", "target_date",
    "temperature_metric", "bin_label", "direction", "token_id", "no_token_id",
    "condition_id", "chain_shares", "shares", "strategy_key",
)


def load_local_position_rows(conn: sqlite3.Connection) -> list[LocalPositionRow]:
    rows = conn.execute(
        f"SELECT {', '.join(_LOCAL_ROW_COLUMNS)} FROM position_current"
    ).fetchall()
    out = []
    for row in rows:
        out.append(
            LocalPositionRow(
                position_id=str(row["position_id"] or ""),
                phase=str(row["phase"] or ""),
                chain_state=str(row["chain_state"] or ""),
                city=str(row["city"] or ""),
                target_date=str(row["target_date"] or ""),
                temperature_metric=str(row["temperature_metric"] or "high"),
                bin_label=str(row["bin_label"] or ""),
                direction=str(row["direction"] or ""),
                token_id=str(row["token_id"] or ""),
                no_token_id=str(row["no_token_id"] or ""),
                condition_id=str(row["condition_id"] or ""),
                chain_shares=(float(row["chain_shares"]) if row["chain_shares"] is not None else None),
                shares=(float(row["shares"]) if row["shares"] is not None else None),
                strategy_key=str(row["strategy_key"] or ""),
            )
        )
    return out


def load_settlement_lookup(forecasts_conn: sqlite3.Connection) -> dict[tuple, SettlementFact]:
    """Read-only settlement_outcomes lookup, keyed (city, target_date, temperature_metric).

    zeus-forecasts.db is a SEPARATE connection per INV-37 (single-DB writes);
    this function never writes.
    """
    out: dict[tuple, SettlementFact] = {}
    try:
        rows = forecasts_conn.execute(
            """
            SELECT city, target_date, temperature_metric, winning_bin, authority,
                   settlement_value, settlement_source, market_slug
              FROM settlement_outcomes
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return out
    for row in rows:
        key = (
            str(row["city"] or ""),
            str(row["target_date"] or ""),
            str(row["temperature_metric"] or "high"),
        )
        out[key] = SettlementFact(
            winning_bin=str(row["winning_bin"] or ""),
            authority=str(row["authority"] or ""),
            settlement_value=row["settlement_value"],
            settlement_source=str(row["settlement_source"] or ""),
            market_slug=str(row["market_slug"] or ""),
        )
    return out


def is_zeus_origin_asset(conn: sqlite3.Connection, asset_id: str) -> bool:
    """True iff `asset_id` is referenced by any Zeus-owned command/order/position
    row on either side (yes or no token). Read-only.
    """
    if not asset_id:
        return False
    row = conn.execute(
        "SELECT 1 FROM venue_commands WHERE token_id = ? LIMIT 1", (asset_id,)
    ).fetchone()
    if row is not None:
        return True
    row = conn.execute(
        "SELECT 1 FROM position_current WHERE token_id = ? OR no_token_id = ? LIMIT 1",
        (asset_id, asset_id),
    ).fetchone()
    return row is not None


def has_open_orders_for_position(conn: sqlite3.Connection, position_id: str) -> bool:
    """True iff any non-terminal venue_commands row exists for this position.

    Read-only guard for the force-resolve path (P0b): a position with an
    order still in flight must never be force-voided out from under it.
    """
    if not position_id:
        return False
    placeholders = ",".join("?" for _ in _OPEN_VENUE_COMMAND_STATES)
    row = conn.execute(
        f"SELECT 1 FROM venue_commands WHERE position_id = ? AND state IN ({placeholders}) LIMIT 1",
        (position_id, *sorted(_OPEN_VENUE_COMMAND_STATES)),
    ).fetchone()
    return row is not None


def has_confirmed_exit_fill_for_position(conn: sqlite3.Connection, position_id: str) -> bool:
    """True iff durable venue facts prove an EXIT sell filled for this position.

    Chain-mirror can observe the wallet token disappearing before the exit-fill
    projector has folded the position to economically_closed. In that race, the
    absent token is expected exit evidence, not a REVIEW_OPEN_ABSENT marker.
    """

    if not position_id:
        return False
    try:
        row = conn.execute(
            """
            SELECT 1
              FROM venue_commands cmd
             WHERE cmd.position_id = ?
               AND UPPER(COALESCE(cmd.intent_kind, '')) = 'EXIT'
               AND UPPER(COALESCE(cmd.side, '')) = 'SELL'
               AND (
                    EXISTS (
                        SELECT 1
                          FROM venue_trade_facts tf
                         WHERE tf.command_id = cmd.command_id
                           AND tf.state IN ('MATCHED', 'MINED', 'CONFIRMED')
                           AND CAST(COALESCE(tf.filled_size, '0') AS REAL) > 0
                           AND CAST(COALESCE(tf.fill_price, '0') AS REAL) > 0
                         LIMIT 1
                    )
                 OR EXISTS (
                        SELECT 1
                          FROM venue_order_facts ofact
                         WHERE ofact.command_id = cmd.command_id
                           AND ofact.state = 'MATCHED'
                           AND CAST(COALESCE(ofact.matched_size, '0') AS REAL) > 0
                         LIMIT 1
                    )
               )
             LIMIT 1
            """,
            (position_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return False
    return row is not None


def has_confirmed_entry_fill_for_position(conn: sqlite3.Connection, position_id: str) -> bool:
    """True iff canonical or venue facts prove this position was actually bought.

    Data API absence cannot turn a confirmed economic holding into a local
    hallucination. It may mean an external sale, redemption, transfer, or venue
    enumeration lag; those outcomes require their own evidence before lifecycle
    closure.
    """

    if not position_id:
        return False
    try:
        row = conn.execute(
            """
            SELECT 1
              FROM position_events pe
             WHERE pe.position_id = ?
               AND pe.event_type = 'ENTRY_ORDER_FILLED'
             LIMIT 1
            """,
            (position_id,),
        ).fetchone()
        if row is not None:
            return True
        row = conn.execute(
            """
            SELECT 1
              FROM venue_commands cmd
             WHERE cmd.position_id = ?
               AND UPPER(COALESCE(cmd.intent_kind, '')) = 'ENTRY'
               AND UPPER(COALESCE(cmd.side, '')) = 'BUY'
               AND EXISTS (
                    SELECT 1
                      FROM venue_trade_facts tf
                     WHERE tf.command_id = cmd.command_id
                       AND tf.state IN ('MATCHED', 'MINED', 'CONFIRMED')
                       AND CAST(COALESCE(tf.filled_size, '0') AS REAL) > 0
                     LIMIT 1
               )
             LIMIT 1
            """,
            (position_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return False
    return row is not None


def has_open_entry_order_without_fill(conn: sqlite3.Connection, position_id: str) -> bool:
    """True iff venue facts show an ENTRY buy order is open but unfilled.

    A pending-entry maker order can be live on CLOB before any position token is
    held. Chain-mirror must not turn that expected absence into a held-token
    REVIEW_OPEN_ABSENT marker.
    """

    if not position_id:
        return False
    placeholders = ",".join("?" for _ in _OPEN_NO_FILL_ENTRY_ORDER_FACT_STATES)
    try:
        row = conn.execute(
            f"""
            SELECT 1
              FROM venue_commands cmd
             WHERE cmd.position_id = ?
               AND UPPER(COALESCE(cmd.intent_kind, '')) = 'ENTRY'
               AND UPPER(COALESCE(cmd.side, '')) = 'BUY'
               AND cmd.state IN ({",".join("?" for _ in _OPEN_VENUE_COMMAND_STATES)})
               AND EXISTS (
                    SELECT 1
                      FROM venue_order_facts ofact
                     WHERE ofact.command_id = cmd.command_id
                       AND ofact.state IN ({placeholders})
                       AND CAST(COALESCE(ofact.matched_size, '0') AS REAL) <= 0
                     LIMIT 1
               )
               AND NOT EXISTS (
                    SELECT 1
                      FROM venue_trade_facts tf
                     WHERE tf.command_id = cmd.command_id
                       AND tf.state IN ('MATCHED', 'MINED', 'CONFIRMED')
                       AND CAST(COALESCE(tf.filled_size, '0') AS REAL) > 0
                     LIMIT 1
               )
             LIMIT 1
            """,
            (
                position_id,
                *sorted(_OPEN_VENUE_COMMAND_STATES),
                *sorted(_OPEN_NO_FILL_ENTRY_ORDER_FACT_STATES),
            ),
        ).fetchone()
    except sqlite3.OperationalError:
        return False
    return row is not None


def _has_prior_review_open_absent_marker(conn: sqlite3.Connection, position_id: str) -> bool:
    """True iff the latest continuity event is a chain-mirror absence marker.

    Plain cycle-runtime ``MONITOR_REFRESHED`` observations are ignored because
    they contain no Chain/CLOB presence evidence. Semantic monitor subtypes and
    monitor events from other writers remain reset boundaries, as do every
    order, fill, settlement, size-correction, and lifecycle event.

    This is the append-only-evidence half of the two-consecutive-mirror-runs
    threshold (docs/rebuild/chain_mirror_state_model_2026-07-04.md §5
    follow-up / Manila-case caution): a single absent read is ambiguous
    (data-api lag); two independent reads ~10min apart with nothing in
    between are not.

    Exact-size token reappearance is materialized as a no-delta
    ``CHAIN_SIZE_CORRECTED`` observation by ``classify_local_position`` so the
    positive chain fact also resets this streak durably.
    """
    if not position_id:
        return False
    row = conn.execute(
        "SELECT event_type, payload_json, source_module FROM position_events "
        "WHERE position_id = ? AND ("
        "event_type <> 'MONITOR_REFRESHED' "
        "OR source_module <> 'src.engine.cycle_runtime' "
        "OR CASE WHEN json_valid(payload_json) = 0 THEN 1 "
        "ELSE COALESCE(TRIM(json_extract(payload_json, '$.semantic_event')), '') <> '' END"
        ") ORDER BY sequence_no DESC LIMIT 1",
        (position_id,),
    ).fetchone()
    if row is None or str(row["event_type"] or "") != "REVIEW_REQUIRED":
        return False
    try:
        payload = json.loads(row["payload_json"] or "{}")
    except (TypeError, ValueError):
        return False
    return payload.get("chain_mirror_classification") == REVIEW_OPEN_ABSENT


def _next_sequence_no(conn: sqlite3.Connection, position_id: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(sequence_no), 0) FROM position_events WHERE position_id = ?",
        (position_id,),
    ).fetchone()
    return int(row[0] or 0) + 1


def _apply_settlement_finding(
    conn: sqlite3.Connection, finding: MirrorFinding, *, now: datetime
) -> None:
    """Append a SETTLED event + upsert position_current for a graded chain-mirror close.

    Uses the same append-only event + projection primitive the canonical
    settlement path uses (src.state.db.append_many_and_project /
    src.state.projection.upsert_position_current). See design doc §5 for why
    this reuses that primitive directly instead of the pending_exit-only
    transition_phase() / harvester Position-object builder.
    """
    from src.state.db import append_many_and_project
    from src.state.lifecycle_manager import LifecyclePhase, fold_lifecycle_phase
    from src.state.projection import CANONICAL_POSITION_CURRENT_COLUMNS

    position_id = finding.position_id
    assert position_id
    current = conn.execute(
        "SELECT * FROM position_current WHERE position_id = ?", (position_id,)
    ).fetchone()
    if current is None:
        return
    projection = {col: current[col] for col in CANONICAL_POSITION_CURRENT_COLUMNS if col in current.keys()}
    for col in CANONICAL_POSITION_CURRENT_COLUMNS:
        projection.setdefault(col, None)

    occurred_at = now.isoformat()
    phase_before = str(current["phase"] or "")
    direction = str(current["direction"] or "").strip().lower()
    if direction not in {"buy_yes", "buy_no"}:
        raise ValueError(
            "chain-mirror settlement requires direction=buy_yes or buy_no"
        )
    position_won = bool(finding.details.get("won"))
    market_bin_won = (
        position_won if direction == "buy_yes" else not position_won
    )
    if finding.classification == REDEEMABLE:
        # Market resolved + Zeus won + tokens still physically present on
        # chain (not yet swept by the third-party auto-redeemer). Local
        # phase moves to settled (we KNOW the outcome) but chain_state is
        # left untouched — it already correctly says the tokens are there.
        chain_state_after = str(current["chain_state"] or "")
    else:
        chain_state_after = CLOSED_REDEEMED if position_won else CLOSED_WORTHLESS
    # `phase_before` is already the canonical DB phase, so validate it through
    # the canonical fold directly. Runtime-state adapters accept values such
    # as `entered`; using one here would misclassify canonical `active` as
    # unknown and silently skip the settlement under per-row isolation.
    projection["phase"] = fold_lifecycle_phase(
        phase_before, LifecyclePhase.SETTLED
    ).value
    projection["chain_state"] = chain_state_after
    projection["updated_at"] = occurred_at
    projection["settled_at"] = projection.get("settled_at") or occurred_at
    if finding.details.get("settlement_value") is not None:
        projection["settlement_price"] = finding.details.get("settlement_value")
    if finding.details.get("chain_absent"):
        projection["chain_shares"] = 0.0

    # Canonical position_settled.v1 contract payload: riskguard's
    # settlement-quality gate (query_authoritative_settlement_rows →
    # CANONICAL_POSITION_SETTLED_DETAIL_FIELDS) counts a SETTLED event with an
    # incomplete canonical payload as a DEGRADED row, and >0 degraded rows
    # flips settlement_quality_level to YELLOW — blocking ALL new entries on
    # the GREEN-only reactor gate. 2026-07-05 incident: 37 mirror-closed rows
    # did exactly that. The mirror KNOWS every truth field at close time;
    # stamp the full contract.
    # R0-a (close-economics unification, 2026-07-08): the chain reconciler is
    # a settlement-discovery *trigger* (chain truth as backstop for Gamma
    # capture), not a second bookkeeper -- it now feeds the same shared
    # close-economics formula every other close path uses instead of
    # re-deriving its own pnl math. A settlement is graded binary: exit_price
    # is 1.0 (won, redeemed at par) or 0.0 (lost, worthless); no entry_price
    # guard is applied here (matches this path's pre-existing behavior of
    # always booking a chain-verified settlement regardless of entry_price).
    from src.state.close_economics import compute_realized_pnl_usd

    _shares = float(current["chain_shares"] or current["shares"] or 0.0)
    _cost = float(current["cost_basis_usd"] or 0.0)
    # Bug C (realized_pnl_usd clobbering, docs/evidence/capital_efficiency_
    # 2026_07_19/pnl_attribution.md §1): a position that already exited via a
    # REAL fill before this chain-observed settlement fired
    # (phase_before == economically_closed) has already booked its true
    # realized_pnl_usd/exit_price from the actual fill price -- the binary
    # 1.0/0.0 settlement price computed below is not the price it exited at,
    # and is not the redemption value either (a real exit sells the token on
    # the open market, not through CTF redemption at par). Overwriting the
    # booked values here regrades the close using the wrong price -- at best
    # a small drift, at worst clobbering a real gain/loss to 0.0 or flipping
    # its sign when the market's binary outcome disagrees with the fill's own
    # economics (e.g. exited profitably before an adverse late move flips the
    # settlement). This mirrors the was_economically_closed guard in
    # src.state.portfolio.compute_settlement_close, which this sibling writer
    # never had -- a redundant settlement sweep must not re-derive economics
    # for a position the exit path already closed.
    was_economically_closed = phase_before == "economically_closed"
    if was_economically_closed:
        _booked_pnl = current["realized_pnl_usd"]
        _booked_exit_price = current["exit_price"]
        _pnl = float(_booked_pnl) if _booked_pnl is not None else 0.0
        _exit_price = (
            float(_booked_exit_price)
            if _booked_exit_price is not None
            else (1.0 if position_won else 0.0)
        )
    else:
        _exit_price = 1.0 if position_won else 0.0
        _pnl = compute_realized_pnl_usd(
            shares=_shares, exit_price=_exit_price, cost_basis_usd=_cost
        )
    # Bug B (truth-path PnL booking, 2026-07-07): _pnl above was already
    # computed correctly for the audit payload below, but `projection` (built
    # by copying pre-transition columns forward) never carried it into the
    # durable realized_pnl_usd / exit_price columns -- a chain-mirror-graded
    # settlement left those NULL even though the payload's own "pnl"/
    # "exit_price" fields were right. settlement_price (below) is a distinct
    # column holding a raw temperature value; do not touch it.
    projection["realized_pnl_usd"] = round(_pnl, 2)
    projection["exit_price"] = _exit_price
    payload = json.dumps(
        {
            "reconciler": "chain_mirror",
            "chain_mirror_classification": chain_state_after,
            **finding.details,
            "contract_version": "position_settled.v1",
            "position_bin": str(current["bin_label"] or ""),
            # `finding.details.won` predates the A8/A9 split and means the
            # held position won on this writer. Preserve that v1 field, but
            # emit both governed axes so downstream code never has to infer
            # BUY NO economics from it.
            "won": position_won,
            "market_bin_won": market_bin_won,
            "position_won": position_won,
            "outcome": 1 if position_won else 0,
            "p_posterior": current["p_posterior"],
            # Bug C: the payload's own exit_price/pnl must agree with what
            # was durably projected above -- previously this re-derived the
            # raw binary 1.0/0.0 here even when the guarded `_exit_price`
            # above had preserved a booked real fill price, so a downstream
            # reader of the SETTLED event payload (rather than
            # position_current) would still see the wrong economics.
            "exit_price": _exit_price,
            "pnl": _pnl,
            "exit_reason": "chain_mirror_settlement",
            "settlement_authority": "VERIFIED",
            "settlement_truth_source": "forecasts.settlement_outcomes",
            "settlement_market_slug": str(finding.details.get("market_slug") or ""),
            "settlement_temperature_metric": str(
                getattr(finding, "temperature_metric", None)
                or current["temperature_metric"]
                or "high"
            ),
        },
        default=str,
        sort_keys=True,
    )
    sequence_no = _next_sequence_no(conn, position_id)
    event = {
        "event_id": f"{position_id}:chain_mirror_settled:{sequence_no}",
        "position_id": position_id,
        "event_version": 1,
        "sequence_no": sequence_no,
        "event_type": "SETTLED",
        "occurred_at": occurred_at,
        "phase_before": phase_before,
        "phase_after": "settled",
        "strategy_key": str(current["strategy_key"] or ""),
        "decision_id": None,
        "snapshot_id": None,
        "order_id": None,
        "command_id": None,
        "caused_by": "chain_mirror_reconciler",
        "idempotency_key": None,
        "venue_status": None,
        "source_module": "src.state.chain_mirror_reconciler",
        "env": "live",
        "payload_json": payload,
    }
    append_many_and_project(conn, [event], projection)


def apply_size_correction_finding(
    conn: sqlite3.Connection, finding: MirrorFinding, *, now: datetime
) -> bool:
    """Append a CHAIN_SIZE_CORRECTED event + upsert position_current.

    Returns True iff a durable write happened; False (no-op) when no
    position_current row exists yet for this position_id — there is nothing
    to correct durably (the in-memory Position side of a chain-truth
    correction is the caller's concern, not this writer's).

    Public (P0b, 2026-07-04): also called directly by
    src.state.chain_reconciliation's size-mismatch branch as the ungated
    fallback when no canonical baseline is available for
    _append_canonical_size_correction_if_available (that helper's
    _canonical_chain_observation_phase gate raises on a non-open starting
    phase, e.g. quarantined — this writer has no such restriction: chain size
    is truth regardless of the position's current phase, and a size
    correction never mutates phase_before/phase_after).
    """
    from src.state.db import append_many_and_project
    from src.state.projection import CANONICAL_POSITION_CURRENT_COLUMNS

    position_id = finding.position_id
    assert position_id
    current = conn.execute(
        "SELECT * FROM position_current WHERE position_id = ?", (position_id,)
    ).fetchone()
    if current is None:
        return False
    projection = {col: current[col] for col in CANONICAL_POSITION_CURRENT_COLUMNS if col in current.keys()}
    for col in CANONICAL_POSITION_CURRENT_COLUMNS:
        projection.setdefault(col, None)

    occurred_at = now.isoformat()
    phase_before = str(current["phase"] or "")
    chain_size = float(finding.details.get("chain_size") or 0.0)
    projection["chain_shares"] = chain_size
    projection["updated_at"] = occurred_at
    projection["chain_seen_at"] = occurred_at

    payload = json.dumps(
        {
            "reconciler": "chain_mirror",
            "chain_mirror_classification": SIZE_CORRECTED,
            **finding.details,
        },
        default=str,
        sort_keys=True,
    )
    sequence_no = _next_sequence_no(conn, position_id)
    event = {
        "event_id": f"{position_id}:chain_mirror_size:{sequence_no}",
        "position_id": position_id,
        "event_version": 1,
        "sequence_no": sequence_no,
        "event_type": "CHAIN_SIZE_CORRECTED",
        "occurred_at": occurred_at,
        "phase_before": phase_before,
        "phase_after": phase_before,
        "strategy_key": str(current["strategy_key"] or ""),
        "decision_id": None,
        "snapshot_id": None,
        "order_id": None,
        "command_id": None,
        "caused_by": "chain_mirror_reconciler",
        "idempotency_key": None,
        "venue_status": None,
        "source_module": "src.state.chain_mirror_reconciler",
        "env": "live",
        "payload_json": payload,
    }
    append_many_and_project(conn, [event], projection)
    return True


def _apply_review_marker_finding(
    conn: sqlite3.Connection, finding: MirrorFinding, *, now: datetime
) -> None:
    """Append a REVIEW_REQUIRED marker event for a REVIEW_OPEN_ABSENT finding.

    Pure evidence: phase_after == phase_before, no lifecycle transition, no
    position_current mutation beyond updated_at. This is the durable half of
    the two-consecutive-mirror-runs threshold — see
    _has_prior_review_open_absent_marker. Uses the ALREADY-REGISTERED
    REVIEW_REQUIRED event_type literal (no CHECK-constraint migration needed).
    """
    from src.state.db import append_many_and_project
    from src.state.projection import CANONICAL_POSITION_CURRENT_COLUMNS

    position_id = finding.position_id
    assert position_id
    current = conn.execute(
        "SELECT * FROM position_current WHERE position_id = ?", (position_id,)
    ).fetchone()
    if current is None:
        return
    projection = {col: current[col] for col in CANONICAL_POSITION_CURRENT_COLUMNS if col in current.keys()}
    for col in CANONICAL_POSITION_CURRENT_COLUMNS:
        projection.setdefault(col, None)

    occurred_at = now.isoformat()
    phase_before = str(current["phase"] or "")
    projection["updated_at"] = occurred_at

    payload = json.dumps(
        {
            "reconciler": "chain_mirror",
            "chain_mirror_classification": REVIEW_OPEN_ABSENT,
            **finding.details,
        },
        default=str,
        sort_keys=True,
    )
    sequence_no = _next_sequence_no(conn, position_id)
    event = {
        "event_id": f"{position_id}:chain_mirror_review:{sequence_no}",
        "position_id": position_id,
        "event_version": 1,
        "sequence_no": sequence_no,
        "event_type": "REVIEW_REQUIRED",
        "occurred_at": occurred_at,
        "phase_before": phase_before,
        "phase_after": phase_before,
        "strategy_key": str(current["strategy_key"] or ""),
        "decision_id": None,
        "snapshot_id": None,
        "order_id": None,
        "command_id": None,
        "caused_by": "chain_mirror_reconciler",
        "idempotency_key": None,
        "venue_status": None,
        "source_module": "src.state.chain_mirror_reconciler",
        "env": "live",
        "payload_json": payload,
    }
    append_many_and_project(conn, [event], projection)


def _apply_closed_exited_finding(
    conn: sqlite3.Connection, finding: MirrorFinding, *, now: datetime
) -> None:
    """Force-resolve an _OPEN_LIKE_PHASES row whose held token has been
    absent across two consecutive mirror runs (market unresolved, zero open
    orders). Folds to VOIDED via enter_voided_runtime_state — legal from
    every _OPEN_LIKE_PHASES origin (including QUARANTINED, post-P0c) — with
    chain_state="closed_exited" recording why. Mirrors the ADMIN_VOIDED event
    shape src.state.chain_reconciliation._sync_voided_position already uses
    for out-of-band administrative voids.
    """
    from src.state.db import append_many_and_project
    from src.state.lifecycle_manager import enter_voided_runtime_state
    from src.state.projection import CANONICAL_POSITION_CURRENT_COLUMNS

    position_id = finding.position_id
    assert position_id
    current = conn.execute(
        "SELECT * FROM position_current WHERE position_id = ?", (position_id,)
    ).fetchone()
    if current is None:
        return
    projection = {col: current[col] for col in CANONICAL_POSITION_CURRENT_COLUMNS if col in current.keys()}
    for col in CANONICAL_POSITION_CURRENT_COLUMNS:
        projection.setdefault(col, None)

    occurred_at = now.isoformat()
    phase_before = str(current["phase"] or "")
    projection["phase"] = enter_voided_runtime_state(
        phase_before, chain_state=str(current["chain_state"] or "")
    )
    projection["chain_state"] = CLOSED_EXITED
    projection["updated_at"] = occurred_at

    payload = json.dumps(
        {
            "reconciler": "chain_mirror",
            "chain_mirror_classification": CLOSED_EXITED,
            **finding.details,
        },
        default=str,
        sort_keys=True,
    )
    sequence_no = _next_sequence_no(conn, position_id)
    event = {
        "event_id": f"{position_id}:chain_mirror_closed_exited:{sequence_no}",
        "position_id": position_id,
        "event_version": 1,
        "sequence_no": sequence_no,
        "event_type": "ADMIN_VOIDED",
        "occurred_at": occurred_at,
        "phase_before": phase_before,
        "phase_after": "voided",
        "strategy_key": str(current["strategy_key"] or ""),
        "decision_id": None,
        "snapshot_id": None,
        "order_id": None,
        "command_id": None,
        "caused_by": "chain_mirror_reconciler",
        "idempotency_key": None,
        "venue_status": "voided",
        "source_module": "src.state.chain_mirror_reconciler",
        "env": "live",
        "payload_json": payload,
    }
    append_many_and_project(conn, [event], projection)


def reconcile(
    conn_trades: sqlite3.Connection,
    conn_forecasts: Optional[sqlite3.Connection],
    chain_by_asset: dict[str, ChainPositionFact],
    *,
    apply: bool,
    now: Optional[datetime] = None,
) -> ReconcileReport:
    """Classify every local row + every chain-only asset, optionally applying
    the safe repair classes (SETTLED closes, size corrections).

    Never mutates on dry-run (apply=False, the default everywhere this is
    invoked). Idempotent: a second call with unchanged inputs re-derives
    CONSISTENT for every already-repaired row (no duplicate events).
    """
    now = now or datetime.now(timezone.utc)
    report = ReconcileReport(generated_at=now.isoformat(), dry_run=not apply)

    local_rows = load_local_position_rows(conn_trades)
    settlement_by_key = (
        load_settlement_lookup(conn_forecasts) if conn_forecasts is not None else {}
    )

    matched_assets: set[str] = set()
    for row in local_rows:
        held = row.held_token_id()
        if held:
            matched_assets.add(held)
        # R2-core hole closure (b) (R0 verifier finding, docs/rebuild/
        # EXECUTION_MASTER_2026-07-07.md §E R2 item 4b): this loop previously
        # had no per-row isolation -- one raising position aborted the WHOLE
        # pass, silently skipping classification/repair for every row after
        # it. Each row is now independently try/excepted: a raising
        # classify/apply call is logged and skipped, never aborts the pass
        # (the diff engine's reconcile() in src/reconcile/diff_engine.py has
        # this from birth for the same reason).
        try:
            # P0b: only compute the (DB-touching) force-resolve signals when they
            # could actually matter — held token absent from THIS snapshot and
            # the row is in an _OPEN_LIKE_PHASES-eligible phase. Cheap in-memory
            # checks first; avoids a wasted query on the common matched/closed path.
            prior_review_open_absent = False
            has_open_orders = False
            has_confirmed_entry_fill = False
            if row.phase in _OPEN_LIKE_PHASES:
                prior_review_open_absent = _has_prior_review_open_absent_marker(
                    conn_trades, row.position_id
                )
                if not held or held not in chain_by_asset:
                    has_confirmed_entry_fill = has_confirmed_entry_fill_for_position(
                        conn_trades, row.position_id
                    )
                    if row.phase == "pending_entry" and has_open_entry_order_without_fill(
                        conn_trades, row.position_id
                    ):
                        report.findings.append(
                            MirrorFinding(
                                classification=CONSISTENT,
                                position_id=row.position_id,
                                asset=held,
                                writes=False,
                                details={
                                    "reason": "open_entry_order_without_fill_pending_position",
                                    "phase": row.phase,
                                    "chain_state": row.chain_state,
                                },
                            )
                        )
                        continue
                    if has_confirmed_exit_fill_for_position(conn_trades, row.position_id):
                        report.findings.append(
                            MirrorFinding(
                                classification=CONSISTENT,
                                position_id=row.position_id,
                                asset=held,
                                writes=False,
                                details={
                                    "reason": "confirmed_exit_fill_fact_pending_projection",
                                    "phase": row.phase,
                                    "chain_state": row.chain_state,
                                },
                            )
                        )
                        continue
                    if prior_review_open_absent:
                        has_open_orders = has_open_orders_for_position(
                            conn_trades, row.position_id
                        )
            finding = classify_local_position(
                row,
                chain_by_asset,
                settlement_by_key,
                prior_review_open_absent=prior_review_open_absent,
                has_open_orders=has_open_orders,
                has_confirmed_entry_fill=has_confirmed_entry_fill,
            )
            report.findings.append(finding)
            if apply:
                if finding.classification == REVIEW_OPEN_ABSENT:
                    # Bookkeeping marker, dispatched independent of `writes`
                    # (REVIEW_OPEN_ABSENT itself never mutates phase/chain_state —
                    # see classify_local_position's comment on this classification).
                    # One marker suffices for the two-run threshold: when the
                    # latest event already IS the marker (token still absent but
                    # CLOSED_EXITED blocked, e.g. in-flight order), re-appending
                    # would only bloat position_events.
                    if not prior_review_open_absent:
                        _apply_review_marker_finding(conn_trades, finding, now=now)
                        report.applied += 1
                elif finding.writes:
                    if finding.classification in (CLOSED_REDEEMED, CLOSED_WORTHLESS, REDEEMABLE):
                        _apply_settlement_finding(conn_trades, finding, now=now)
                        report.applied += 1
                    elif finding.classification == SIZE_CORRECTED:
                        if apply_size_correction_finding(conn_trades, finding, now=now):
                            report.applied += 1
                    elif finding.classification == CLOSED_EXITED:
                        _apply_closed_exited_finding(conn_trades, finding, now=now)
                        report.applied += 1
        except Exception as exc:  # per-row isolation -- never abort the pass
            logger.error(
                "chain_mirror_reconciler: reconcile failed for position %s: %s",
                row.position_id,
                exc,
            )
            report.errors.append({"position_id": row.position_id, "error": str(exc)})

    for asset, chain_fact in chain_by_asset.items():
        if asset in matched_assets:
            continue
        try:
            zeus_origin = is_zeus_origin_asset(conn_trades, asset)
            finding = classify_chain_only_asset(asset, chain_fact, matched_assets, zeus_origin)
            if finding is not None:
                report.findings.append(finding)
        except Exception as exc:  # per-row isolation -- never abort the pass
            logger.error(
                "chain_mirror_reconciler: chain-only asset classification failed for %s: %s",
                asset,
                exc,
            )
            report.errors.append({"asset": asset, "error": str(exc)})

    return report


def run_cycle() -> None:
    """Scheduler entrypoint (R4-b extraction from src/main.py::_chain_mirror_reconcile_cycle).

    Standing chain-mirror invariant (operator directive 2026-07-04): the local
    position book must mirror on-chain state. Reads the wallet's full position
    set from the venue data-api (read-only GET /positions — no order
    construction, no signing, no redeem submission), diffs every
    position_current row and every chain token per
    docs/rebuild/chain_mirror_state_model_2026-07-04.md, and auto-applies the
    two safe repair classes: (a) settlement closes when a graded position's
    held token is absent from chain and its market has a VERIFIED
    settlement_outcomes row, and (b) chain_shares corrections when a held
    token's chain size differs from the local record. Every other class
    (foreign tokens, missing local rows, open-but-absent ambiguity) is logged
    as a finding only — never written.

    This is the "no row stays quarantined past one reconcile cycle" backstop:
    it reclassifies every local row via chain truth on every tick regardless
    of its current phase, so a quarantined row with a gradable chain outcome
    drains into settled within one cycle without requiring every quarantine
    writer to be rewired (see design doc §5 for the scoped follow-up).

    Called from the main daemon's ``chain_mirror_reconcile`` scheduler job
    (10-minute cadence). Behavior-preserving relocation — was inline in
    src/main.py.
    """
    from src.config import get_mode
    from src.data.polymarket_client import PolymarketClient
    from src.state.ctf_token_registry import record_token_seen
    from src.state.db import get_forecasts_connection_read_only, get_trade_connection

    if get_mode() != "live":
        return
    try:
        raw_positions = PolymarketClient().get_positions_from_api() or []
    except Exception as exc:
        logger.warning("chain_mirror_reconcile: chain read failed, skipping cycle: %s", exc)
        return

    chain_by_asset = load_chain_positions_by_asset(raw_positions)
    conn_trades = get_trade_connection(write_class="live")
    conn_trades.row_factory = sqlite3.Row
    conn_forecasts = None
    try:
        # LX-T2-a discovery hook (docs/rebuild/local_ledger_excision_2026-07-12.md
        # Attack F): every token this data-api /positions read reports is
        # durably registered so a LATER read that omits it (venue lag,
        # redemption, illiquidity) can never be read as the token having
        # vanished -- registry rows are never deleted on absence. Best-effort:
        # a registry write failure must never abort the reconcile pass that
        # already has fresh chain facts in hand.
        for asset, chain_fact in chain_by_asset.items():
            if not chain_fact.condition_id:
                continue
            try:
                record_token_seen(
                    conn_trades,
                    token_id=asset,
                    condition_id=chain_fact.condition_id,
                    source="positions_api_discovery",
                )
            except Exception as exc:
                logger.warning(
                    "chain_mirror_reconcile: ctf_token_registry record failed for token %s: %s",
                    asset,
                    exc,
                )
        try:
            # Genuinely read-only (mode=ro) — grading never writes to
            # zeus-forecasts.db (INV-37: single-DB writes, zeus_trades.db only).
            conn_forecasts = get_forecasts_connection_read_only()
            conn_forecasts.row_factory = sqlite3.Row
        except Exception as exc:
            logger.warning(
                "chain_mirror_reconcile: forecasts connection unavailable, "
                "grading skipped this cycle: %s", exc,
            )
            conn_forecasts = None
        report = reconcile(conn_trades, conn_forecasts, chain_by_asset, apply=True)
        conn_trades.commit()
        if report.applied or report.by_classification():
            logger.info(
                "chain_mirror_reconcile: applied=%d counts=%s",
                report.applied, report.by_classification(),
            )
    finally:
        conn_trades.close()
        if conn_forecasts is not None:
            conn_forecasts.close()

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

No network I/O and no venue mutation happens in this module. The CLI wrapper
(scripts/reconcile_chain_mirror.py) owns adapter construction; this module only
consumes already-fetched chain facts.

P0b (2026-07-04, docs/rebuild/chain_mirror_state_model_2026-07-04.md §5
follow-up): the REVIEW_OPEN_ABSENT class (open-phase/quarantined row, held
token absent, market unresolved) now escalates to a new CLOSED_EXITED
classification (folds to VOIDED, chain_state="closed_exited") once the SAME
absence has been observed on two consecutive mirror runs with zero open
orders in flight — a single absent read stays a REVIEW finding (data-api lag
is real; the Manila ce105753-e91 case must never auto-close on one read).
The "has this been seen before" signal is a lightweight, append-only
REVIEW_REQUIRED marker event (phase_after == phase_before, no lifecycle
mutation) — see _has_prior_review_open_absent_marker.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

# Market-rule classification labels (registered in
# architecture/money_path_objects.yaml::chain_mirror_reconciliation_classification).
CLOSED_REDEEMED = "closed_redeemed"
CLOSED_WORTHLESS = "closed_worthless"
SIZE_CORRECTED = "size_corrected"
REDEEMABLE = "redeemable"
REVIEW_OPEN_ABSENT = "review_open_absent"
# P0b (2026-07-04): force-resolve classification for an _OPEN_LIKE_PHASES row
# whose held token has been absent across two consecutive mirror runs (market
# still unresolved, zero open orders in flight). Folds to VOIDED — the only
# lifecycle target legal from every _OPEN_LIKE_PHASES origin, including
# QUARANTINED post-P0c — with chain_state="closed_exited" recording why.
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

_SIZE_MISMATCH_TOLERANCE = 0.05  # shares; below this the chain/local delta is noise.

# Phases considered "still open" for the purposes of the REVIEW (e) class —
# mirrors the phases that require an on-chain holding per position_current's
# own CHECK vocabulary (src/state/db.py CREATE TABLE position_current).
_OPEN_LIKE_PHASES = frozenset(
    {"pending_entry", "active", "day0_window", "pending_exit", "quarantined"}
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
                # P0b: escalate to a force-close ONLY once the SAME absence has
                # been seen on a prior mirror run with nothing open in flight.
                # A single absent read stays a REVIEW finding — the operator's
                # explicit instruction for the Manila ce105753-e91 case: a
                # single read is ambiguous (data-api lag), two independent
                # reads ~10min apart are not.
                if prior_review_open_absent and not has_open_orders:
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
                        "reason": "held_token_absent_market_not_resolved",
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


def _has_prior_review_open_absent_marker(conn: sqlite3.Connection, position_id: str) -> bool:
    """True iff the MOST RECENT position_events row for this position is a
    chain-mirror REVIEW_OPEN_ABSENT marker (i.e. no intervening write — chain
    reappearance, settlement, or size correction — has happened since it was
    first observed absent).

    This is the append-only-evidence half of the two-consecutive-mirror-runs
    threshold (docs/rebuild/chain_mirror_state_model_2026-07-04.md §5
    follow-up / Manila-case caution): a single absent read is ambiguous
    (data-api lag); two independent reads ~10min apart with nothing in
    between are not.

    Known limitation (accepted scope boundary): if the token reappears and
    the row happens to be an EXACT chain-size match on that intervening run
    (CONSISTENT, which — by design — never writes an event), the streak is
    not visibly reset here. Not exercised by any sanctioned test scenario;
    documented rather than chased, per the smallest-viable-diff mandate.
    """
    if not position_id:
        return False
    row = conn.execute(
        "SELECT event_type, payload_json FROM position_events "
        "WHERE position_id = ? ORDER BY sequence_no DESC LIMIT 1",
        (position_id,),
    ).fetchone()
    if row is None:
        return False
    if str(row["event_type"] or "") != "REVIEW_REQUIRED":
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
    from src.state.lifecycle_manager import enter_settled_runtime_state
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
    won = bool(finding.details.get("won"))
    if finding.classification == REDEEMABLE:
        # Market resolved + Zeus won + tokens still physically present on
        # chain (not yet swept by the third-party auto-redeemer). Local
        # phase moves to settled (we KNOW the outcome) but chain_state is
        # left untouched — it already correctly says the tokens are there.
        chain_state_after = str(current["chain_state"] or "")
    else:
        chain_state_after = CLOSED_REDEEMED if won else CLOSED_WORTHLESS
    # P0c (2026-07-04): the direct `projection["phase"] = "settled"` bypass
    # documented in docs/rebuild/chain_mirror_state_model_2026-07-04.md §5 is
    # retired now that LEGAL_LIFECYCLE_FOLDS[QUARANTINED] legally folds to
    # SETTLED — this now goes through the same guard every other settlement
    # writer uses, which also fails loudly if `phase_before` is somehow not a
    # legal starting phase (active/day0/economically_closed/pending_exit/
    # quarantined) instead of silently minting an illegal transition.
    projection["phase"] = enter_settled_runtime_state(
        phase_before, chain_state=str(current["chain_state"] or "")
    )
    projection["chain_state"] = chain_state_after
    projection["updated_at"] = occurred_at
    projection["settled_at"] = projection.get("settled_at") or occurred_at
    if finding.details.get("settlement_value") is not None:
        projection["settlement_price"] = finding.details.get("settlement_value")
    if finding.details.get("chain_absent"):
        projection["chain_shares"] = 0.0

    payload = json.dumps(
        {
            "reconciler": "chain_mirror",
            "chain_mirror_classification": chain_state_after,
            **finding.details,
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
        # P0b: only compute the (DB-touching) force-resolve signals when they
        # could actually matter — held token absent from THIS snapshot and
        # the row is in an _OPEN_LIKE_PHASES-eligible phase. Cheap in-memory
        # checks first; avoids a wasted query on the common matched/closed path.
        prior_review_open_absent = False
        has_open_orders = False
        if row.phase in _OPEN_LIKE_PHASES and (not held or held not in chain_by_asset):
            prior_review_open_absent = _has_prior_review_open_absent_marker(
                conn_trades, row.position_id
            )
            if prior_review_open_absent:
                has_open_orders = has_open_orders_for_position(conn_trades, row.position_id)
        finding = classify_local_position(
            row,
            chain_by_asset,
            settlement_by_key,
            prior_review_open_absent=prior_review_open_absent,
            has_open_orders=has_open_orders,
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

    for asset, chain_fact in chain_by_asset.items():
        if asset in matched_assets:
            continue
        zeus_origin = is_zeus_origin_asset(conn_trades, asset)
        finding = classify_chain_only_asset(asset, chain_fact, matched_assets, zeus_origin)
        if finding is not None:
            report.findings.append(finding)

    return report

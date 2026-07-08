# Created: 2026-07-08
# Last reused or audited: 2026-07-08
# Authority basis: docs/rebuild/EXECUTION_MASTER_2026-07-07.md §E R2-b
#                  docs/rebuild/whole_system_first_principles_2026-07-07.md §2.4 + §7.1
"""classify(local, chain) -> typed findings -> predicate table -> apply_corrective_event.

Modeled on src/state/chain_mirror_reconciler.py's classify + apply_* +
reconcile() shape (that module IS the target template -- 1033 lines, kept,
not slated for deletion). This module is SHADOW-FREE but INERT: nothing
outside tests/the replay harness calls reconcile() with apply=True yet;
wiring it into a live cycle is R2-c (the 31-pass migration wave), per the
no-shadow-modes axiom (§C6) -- a promotion flag is deliberately NOT added
here because nothing calls this module at all.

Predicate table (§7.1: "31+ 个 reconcile pass 逐个考古:真 venue 行为只有 4-5 个"
-- the 31 legacy passes decompose into 4-5 REAL venue behaviors plus ~26
self-inflicted-scar passes that are NOT reproduced here; see
docs/rebuild/whole_system_first_principles_2026-07-07.md §7.1 for the full
taxonomy). Each predicate below carries a docstring naming the specific
venue behavior it exists to handle (world-fact comment law) plus the
file:line where that behavior is already documented/handled by a legacy
pass, so a verifier can cross-check the claim against real code:

    predicate                          venue behavior                          legacy evidence
    ------------------------------------------------------------------------------------------------------------------------
    cancel_match_race                  cancel confirmation racing a            src/execution/exchange_reconcile.py:4377
                                        concurrent match; a later cancel        "_missing_entry_projection_from_linked_fill"
                                        terminalizes only the UNFILLED
                                        remainder, never an already-matched
                                        fill
    ws_unreliable_rest_point_truth     WS order-fact stream can go quiet       src/state/db.py venue_order_facts.state
                                        (HEARTBEAT_CANCEL_SUSPECTED) or fall    CHECK vocabulary (registers the state
                                        behind a fresher REST/DATA_API          for exactly this reason)
                                        point-in-time read
    partial_fill_disappearance         a PARTIAL command's unfilled            src/execution/command_recovery.py:9482
                                        remainder can vanish from the           "reconcile_partial_remainders" docstring
                                        venue's open-order surface without
                                        an explicit cancel event; the fill
                                        exposure already matched must
                                        survive intact
    fill_dedup_ordering_drift          venue_trade_facts redelivers the        src/state/fill_dedup.py module docstring
                                        SAME real fill across MATCHED->
                                        MINED->CONFIRMED lifecycle
                                        revisions (and out of local_sequence
                                        order); only the dedup CTE's
                                        proof-rank ranking may size a
                                        position
    reservation_orphan_fill_after_     convert_reservation_on_fill derives     src/state/collateral_ledger.py:951-1009
    release                            converted_amount from venue_order_      + docs/rebuild/EXECUTION_MASTER_2026-07-07.md
                                        facts AT TERMINAL-DISPATCH TIME; a      line 53 (R0-b verifier finding: "R2 diff
                                        fill fact landing AFTER that            引擎的 chain-truth 兜底须覆盖此窗口")
                                        dispatch leaves the reservation
                                        released with converted_amount=0
                                        forever (no re-trigger hook exists)

Only ``reservation_orphan_fill_after_release`` has a concrete
apply_corrective_event body in this packet (R2-core): it is the one hole the
R0 verifiers explicitly assigned to this diff engine's domain (see docstring
above). The other four predicates are report-only in R2-core (writes=False,
an append-only evidence class exactly like chain_mirror_reconciler's
REVIEW_OPEN_ABSENT) -- the command-state MUTATION logic for cancel-race/
WS-staleness/partial-fill terminalization belongs to the 31-pass migration
(R2-c), where each gets real replay evidence before it is allowed to write.
Building that mutation logic now, unreviewed and unwired, would be exactly
the kind of shadow machinery the loop-design law (no shadow, minimal
machinery) forbids.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

from src.reconcile.chain_truth import (
    ChainCommandFacts,
    ChainTruthSnapshot,
    load_chain_truth_snapshot,
)
from src.reconcile.local_truth import (
    LocalCommandTruth,
    LocalPositionTruth,
    LocalTruthSnapshot,
    load_local_truth_snapshot,
)
logger = logging.getLogger(__name__)

# Finding classification vocabulary (registered in
# architecture/money_path_objects.yaml::reconcile_diff_finding_classification).
LOCAL_STATE_IGNORES_CONCURRENT_FILL = "local_state_ignores_concurrent_fill"
WS_STATE_STALE_NEEDS_REST_TRUTH = "ws_state_stale_needs_rest_truth"
PARTIAL_REMAINDER_TERMINAL_PROOF_AVAILABLE = "partial_remainder_terminal_proof_available"
POSITION_SHARES_EXCEED_CANONICAL_FILL = "position_shares_exceed_canonical_fill"
RESERVATION_ORPHANED_FILL_AFTER_RELEASE = "reservation_orphaned_fill_after_release"

# Mirrors exchange_reconcile.py's no-fill terminal vocabulary
# (_TERMINAL_NO_FILL_VENUE_STATUSES, :113/:3695) -- venue_commands.state
# values that mean "this command ended with zero fill," the exact claim
# cancel_match_race exists to check against chain fill evidence.
_NO_FILL_TERMINAL_COMMAND_STATES = frozenset(
    {"CANCELLED", "CANCELED", "EXPIRED", "REJECTED", "SUBMIT_REJECTED"}
)
# Mirrors src.state.chain_mirror_reconciler._OPEN_VENUE_COMMAND_STATES /
# executor.py's _ENTRY_DUPLICATE_OPEN_COMMAND_STATES / status_summary.py's
# _OPEN_ENTRY_COMMAND_STATES vocabulary (venue_commands.state values that
# mean "this command has not yet terminalized"). Defined locally rather than
# importing a private module attribute across a package boundary.
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
_ORDER_FACT_NO_LIVE_RECORD_STATES = frozenset({"EXPIRED", "CANCEL_CONFIRMED", "VENUE_WIPED"})
_SHARES_DRIFT_TOLERANCE = 0.05  # shares; mirrors chain_mirror_reconciler._SIZE_MISMATCH_TOLERANCE
_FILL_EPSILON = 1e-9


@dataclass(frozen=True)
class DiffFinding:
    classification: str
    command_id: Optional[str]
    position_id: Optional[str]
    writes: bool
    details: dict = field(default_factory=dict)


def _predicate_cancel_match_race(
    cmd: LocalCommandTruth, facts: ChainCommandFacts
) -> Optional[DiffFinding]:
    """Cancel/match race: a CANCELLED/EXPIRED/REJECTED command state does not
    prove zero exposure when the deduped fill stream shows a positive
    canonical fill for the same command_id (exchange_reconcile.py:4377).
    """
    if cmd.state.upper() not in _NO_FILL_TERMINAL_COMMAND_STATES:
        return None
    if facts.canonical_filled_size <= _FILL_EPSILON:
        return None
    return DiffFinding(
        classification=LOCAL_STATE_IGNORES_CONCURRENT_FILL,
        command_id=cmd.command_id,
        position_id=cmd.position_id,
        writes=False,
        details={
            "local_state": cmd.state,
            "canonical_filled_size": facts.canonical_filled_size,
            "canonical_fill_price": facts.canonical_fill_price,
        },
    )


def _predicate_ws_unreliable_rest_point_truth(
    cmd: LocalCommandTruth, facts: ChainCommandFacts
) -> Optional[DiffFinding]:
    """WS unreliable: a still-open local command state must not be trusted
    against a stale/suspected-dead WS order-fact stream once a fresher
    REST/DATA_API point-in-time read exists (venue_order_facts.state
    HEARTBEAT_CANCEL_SUSPECTED vocabulary).
    """
    if cmd.state.upper() not in _OPEN_VENUE_COMMAND_STATES:
        return None
    if not (facts.heartbeat_cancel_suspected or facts.ws_state_stale_vs_rest):
        return None
    return DiffFinding(
        classification=WS_STATE_STALE_NEEDS_REST_TRUTH,
        command_id=cmd.command_id,
        position_id=cmd.position_id,
        writes=False,
        details={
            "local_state": cmd.state,
            "latest_order_state": facts.latest_order_state,
            "latest_order_source": facts.latest_order_source,
            "latest_rest_order_state": facts.latest_rest_order_state,
            "latest_rest_observed_at": facts.latest_rest_observed_at,
        },
    )


def _predicate_partial_fill_disappearance(
    cmd: LocalCommandTruth, facts: ChainCommandFacts
) -> Optional[DiffFinding]:
    """Partial-fill disappearance: a PARTIAL command's unfilled remainder can
    vanish from the venue's open-order surface (terminal-no-live-record
    order fact) without an explicit cancel event; the already-matched fill
    must remain intact, never zeroed (command_recovery.py:9482
    reconcile_partial_remainders).
    """
    if cmd.state.upper() != "PARTIAL":
        return None
    if facts.latest_order_state not in _ORDER_FACT_NO_LIVE_RECORD_STATES:
        return None
    if facts.canonical_filled_size <= _FILL_EPSILON:
        return None
    return DiffFinding(
        classification=PARTIAL_REMAINDER_TERMINAL_PROOF_AVAILABLE,
        command_id=cmd.command_id,
        position_id=cmd.position_id,
        writes=False,
        details={
            "latest_order_state": facts.latest_order_state,
            "canonical_filled_size": facts.canonical_filled_size,
            "preserve_exposure": True,
        },
    )


def _predicate_reservation_orphan(
    cmd: LocalCommandTruth, facts: ChainCommandFacts
) -> Optional[DiffFinding]:
    """Reservation orphan: convert_reservation_on_fill derives converted_amount
    from matched_size AT TERMINAL-DISPATCH TIME (collateral_ledger.py:951);
    a fill fact landing AFTER that dispatch leaves the reservation released
    with converted_amount=0 forever (WHERE released_at IS NULL guards every
    future re-entry). R0-b verifier finding (EXECUTION_MASTER line 53).
    """
    if not cmd.reservation_released or (cmd.reservation_converted_amount or 0) > 0:
        return None
    if facts.canonical_filled_size <= _FILL_EPSILON:
        return None
    if not facts.latest_fill_observed_at or not cmd.reservation_released_at:
        return None
    if facts.latest_fill_observed_at <= cmd.reservation_released_at:
        return None
    return DiffFinding(
        classification=RESERVATION_ORPHANED_FILL_AFTER_RELEASE,
        command_id=cmd.command_id,
        position_id=cmd.position_id,
        writes=True,
        details={
            "reservation_type": cmd.reservation_type,
            "reservation_amount": cmd.reservation_amount,
            "reservation_released_at": cmd.reservation_released_at,
            "reservation_release_reason": cmd.reservation_release_reason,
            "canonical_filled_size": facts.canonical_filled_size,
            "canonical_fill_price": facts.canonical_fill_price,
            "latest_fill_observed_at": facts.latest_fill_observed_at,
        },
    )


@dataclass(frozen=True)
class Predicate:
    name: str
    venue_behavior: str
    evaluate: Callable[[LocalCommandTruth, ChainCommandFacts], Optional[DiffFinding]]


# The predicate table. Deliberately small (§7.1: 150-300 lines, 4-5 real
# venue behaviors) -- see module docstring for the full evidence table.
PREDICATE_TABLE: tuple[Predicate, ...] = (
    Predicate("cancel_match_race", "cancel confirmation racing a concurrent match", _predicate_cancel_match_race),
    Predicate(
        "ws_unreliable_rest_point_truth",
        "WS order-fact stream unreliable; REST/DATA_API is point-in-time truth",
        _predicate_ws_unreliable_rest_point_truth,
    ),
    Predicate(
        "partial_fill_disappearance",
        "PARTIAL command's unfilled remainder vanishes from the open-order surface",
        _predicate_partial_fill_disappearance,
    ),
    Predicate(
        "reservation_orphan_fill_after_release",
        "fill fact lands after terminal-dispatch reservation release (converted_amount=0 forever)",
        _predicate_reservation_orphan,
    ),
)


def _predicate_position_shares_exceed_canonical_fill(
    position: LocalPositionTruth, local: LocalTruthSnapshot, chain: ChainTruthSnapshot
) -> Optional[DiffFinding]:
    """Fill dedup ordering drift: local position_current.shares must never
    exceed the sum of chain_truth's canonical_filled_size (itself sourced
    exclusively through canonical_trade_fact_cte -- see chain_truth.py)
    across the position's ENTRY commands by more than noise. An un-deduped
    fill total over-counts 1x-4x on the same real fill re-observed across
    lifecycle revisions (src/state/fill_dedup.py module docstring); this
    predicate is the diff-engine-level backstop for that bug class.
    Position-scoped (sums across a position's commands), so it is applied
    separately from the per-command PREDICATE_TABLE above.
    """
    entry_commands = [c for c in local.commands_for_position(position.position_id) if c.intent_kind == "ENTRY"]
    if not entry_commands:
        return None
    canonical_total = sum(chain.command_facts(c.command_id).canonical_filled_size for c in entry_commands)
    local_shares = position.shares if position.shares is not None else 0.0
    if local_shares - canonical_total <= _SHARES_DRIFT_TOLERANCE:
        return None
    return DiffFinding(
        classification=POSITION_SHARES_EXCEED_CANONICAL_FILL,
        command_id=None,
        position_id=position.position_id,
        writes=False,
        details={
            "local_shares": local_shares,
            "canonical_filled_size_total": canonical_total,
            "entry_command_ids": [c.command_id for c in entry_commands],
        },
    )


def classify(local: LocalTruthSnapshot, chain: ChainTruthSnapshot) -> list[DiffFinding]:
    """Pure: run every predicate over every command, plus the position-scoped
    fill-dedup drift check. No DB I/O.
    """
    findings: list[DiffFinding] = []
    for cmd in local.commands.values():
        facts = chain.command_facts(cmd.command_id)
        for predicate in PREDICATE_TABLE:
            finding = predicate.evaluate(cmd, facts)
            if finding is not None:
                findings.append(finding)
    for position in local.positions.values():
        finding = _predicate_position_shares_exceed_canonical_fill(position, local, chain)
        if finding is not None:
            findings.append(finding)
    return findings


def _next_position_sequence_no(conn: sqlite3.Connection, position_id: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(sequence_no), 0) FROM position_events WHERE position_id = ?",
        (position_id,),
    ).fetchone()
    return int(row[0] or 0) + 1


def apply_corrective_event(conn: sqlite3.Connection, finding: DiffFinding, *, now: datetime) -> bool:
    """Append the durable evidence record for a writes=True finding.

    R2-core scope decision (see module docstring): this appends an
    append-only REVIEW_REQUIRED marker event -- it does NOT mutate
    collateral_reservations balances directly. Money-accounting mutation
    logic for the reservation-orphan class is exactly the kind of new,
    unreviewed, unwired write the loop-design law (no shadow, minimal
    machinery) and the money-path PREPARE-level review gate exist to keep
    off the live path until it has replay evidence -- that repair becomes an
    operator-reviewed R2-c decision, informed by this durable evidence
    trail. Idempotent: a duplicate finding re-applied produces a second
    marker event (position_events is append-only by design), which is safe
    -- the marker is evidence, not a balance mutation.
    """
    if finding.classification != RESERVATION_ORPHANED_FILL_AFTER_RELEASE:
        raise NotImplementedError(
            f"apply_corrective_event: no writer for classification={finding.classification!r} "
            "in R2-core -- command-state mutation for this venue behavior belongs to the R2-c "
            "31-pass migration wave, gated on replay evidence."
        )
    position_id = finding.position_id
    if not position_id:
        return False
    current = conn.execute(
        "SELECT strategy_key FROM position_current WHERE position_id = ?", (position_id,)
    ).fetchone()
    if current is None:
        return False
    occurred_at = now.isoformat()
    sequence_no = _next_position_sequence_no(conn, position_id)
    payload = json.dumps(
        {
            "reconciler": "diff_engine",
            "diff_engine_classification": finding.classification,
            "command_id": finding.command_id,
            **finding.details,
        },
        default=str,
        sort_keys=True,
    )
    conn.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, event_version, sequence_no, event_type,
            occurred_at, phase_before, phase_after, strategy_key, decision_id,
            snapshot_id, order_id, command_id, caused_by, idempotency_key,
            venue_status, source_module, env, payload_json
        ) VALUES (?, ?, 1, ?, 'REVIEW_REQUIRED', ?, NULL, NULL, ?, NULL,
                  NULL, NULL, ?, 'diff_engine', ?, NULL, ?, 'live', ?)
        """,
        (
            f"{position_id}:diff_engine_review:{sequence_no}",
            position_id,
            sequence_no,
            occurred_at,
            str(current["strategy_key"] or ""),
            finding.command_id,
            f"{position_id}:diff_engine_review:{sequence_no}",
            "src.reconcile.diff_engine",
            payload,
        ),
    )
    return True


@dataclass
class DiffReport:
    generated_at: str
    dry_run: bool
    findings: list[DiffFinding] = field(default_factory=list)
    applied: int = 0
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
                    "command_id": f.command_id,
                    "position_id": f.position_id,
                    "writes": f.writes,
                    "details": f.details,
                }
                for f in self.findings
            ],
        }


def reconcile(
    conn_trades: sqlite3.Connection,
    conn_forecasts: Optional[sqlite3.Connection],
    chain_by_asset: dict,
    *,
    apply: bool,
    now: Optional[datetime] = None,
) -> DiffReport:
    """Runner entry point. NOT wired into any live cycle (R2-core is inert --
    see module docstring); a test/replay-harness/CLI caller invokes this
    directly.

    Per-row isolation from birth (R0 verifier finding: chain_mirror_
    reconciler's equivalent loop had none, so one raising row aborted the
    whole pass -- see the sibling fix in
    src/state/chain_mirror_reconciler.py's reconcile()): every command/
    position is wrapped in its own try/except; a raising predicate or
    writer is logged and skipped, never aborts the pass.
    """
    now = now or datetime.now(timezone.utc)
    local = load_local_truth_snapshot(conn_trades, now=now)
    chain = load_chain_truth_snapshot(conn_trades, conn_forecasts, chain_by_asset, now=now)
    report = DiffReport(generated_at=now.isoformat(), dry_run=not apply)

    for cmd in local.commands.values():
        try:
            facts = chain.command_facts(cmd.command_id)
            for predicate in PREDICATE_TABLE:
                finding = predicate.evaluate(cmd, facts)
                if finding is None:
                    continue
                report.findings.append(finding)
                if apply and finding.writes:
                    if apply_corrective_event(conn_trades, finding, now=now):
                        report.applied += 1
        except Exception as exc:  # per-row isolation -- never abort the pass
            logger.error("diff_engine: command %s classification failed: %s", cmd.command_id, exc)
            report.errors.append({"command_id": cmd.command_id, "error": str(exc)})

    for position in local.positions.values():
        try:
            finding = _predicate_position_shares_exceed_canonical_fill(position, local, chain)
            if finding is not None:
                report.findings.append(finding)
        except Exception as exc:  # per-row isolation -- never abort the pass
            logger.error("diff_engine: position %s classification failed: %s", position.position_id, exc)
            report.errors.append({"position_id": position.position_id, "error": str(exc)})

    return report

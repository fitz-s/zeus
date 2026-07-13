# Created: 2026-07-13
# Authority basis: docs/rebuild/local_ledger_excision_2026-07-12.md packet
#   LX-2R-a. Both wave-1 reviews (docs/rebuild/consult_answers/
#   local_ledger_excision_wave1_review_2026-07-13.txt +
#   local_ledger_excision_wave1_local_verifier_2026-07-13.md) ruled: reducer
#   *implementation* may proceed in parallel with LX-1R source-spine repair,
#   but it MUST refuse incomplete source/identity coverage, and NO production
#   read-model backfill, NO cent-equivalence claim, NO generation publication
#   against a live DB until the operator runs the F2 identity-supersession
#   backfill (scripts/backfill_identity_supersession_facts.py --apply) and the
#   LX-1R gate otherwise closes. This module is SYNTHETIC-FIXTURE-ONLY: it is
#   a pure function over already-durable trade-DB facts, wired into nothing.
"""Deterministic position-economics reducer (LX-2R-a synthetic implementation).

INPUT CONTRACT -- docs/rebuild/local_ledger_excision_2026-07-12.md
"Read-model 诚实不变量" (the 11-clause honesty invariant this module targets):

  - External facts are written ONLY by the sync/ingest layer. This module
    NEVER writes to venue_trade_facts, position_events, payout_observations,
    or any other source-fact table -- pure derive-on-read.
  - Every economic fill contributes exactly once. Alias-graph dedup (tx-hash
    aggregate vs. exact child trade, lifecycle-revision ranking) is entirely
    delegated to ``src.state.fill_dedup.economic_trade_facts_for_command`` --
    this module never reimplements that rule.
  - Duplicate position identities fold into their keeper via
    ``POSITION_IDENTITY_SUPERSEDED`` facts on ``position_events`` (F2,
    ``src.state.position_duplicate_consolidator`` / ``src.engine.
    lifecycle_events.build_position_identity_superseded_canonical_write``).
    This module walks that fact log itself -- it never re-synthesizes merged
    shares/cost-basis the way the pre-F2 consolidator used to.
  - A payout that is UNKNOWN, UNRESOLVED, or simply not yet observed NEVER
    collapses to zero -- an open position whose condition has not resolved
    carries an explicit ``PENDING`` marker with ``payout_pnl_usd=None``.
  - Missing coverage that the reducer cannot see past -- an absent
    ``fill_sync_watermarks`` row for the claimed source, or a
    ``position_events`` schema that does not yet admit
    ``POSITION_IDENTITY_SUPERSEDED`` (pre-migration DB) -- makes the reducer
    REFUSE with a typed error naming the missing input, rather than silently
    computing a number over a corpus it cannot prove is complete. Fail-closed
    is a feature; see the ``ReducerRefusal`` subclasses below and
    ``tests/reduce/test_position_economics.py::TestRefusalMatrix``.

WHAT THIS MODULE DOES NOT DO
-----------------------------
- It does not touch ``position_current``, ``projection.py``, or any live
  reader (bankroll/riskguard/monitor/exit). Nothing calls this package yet.
- It does not resolve ``token_id -> (condition_id, outcome_index)``. No
  production join for that exists yet (``src/state/schema/
  ctf_token_registry_schema.py`` has no ``outcome_index`` column -- see
  docs/rebuild/consult_answers/local_ledger_excision_wave1_review_2026-07-13.txt
  "[HIGH] condition coverage"). Callers supply ``condition_id``/
  ``outcome_index`` explicitly; a position that still holds shares without
  that attribution is a refusal (``ConditionAttributionMissingError``), never
  a guess.
- It does not claim cent-equivalence against any legacy column. That is an
  LX-2R activation-gate deliverable this packet explicitly does not attempt.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from src.engine.lifecycle_events import position_events_admits_event_type
from src.state.fill_dedup import economic_trade_facts_for_command

# Bumped whenever the fold/refusal rules below change in a way that would
# change a previously-computed number for the same fact corpus. Every
# published src.reduce.generation.Generation carries this value.
REDUCER_VERSION = "lx2r-synthetic-1"

_ENTRY_INTENTS = frozenset({"ENTRY"})
_EXIT_INTENTS = frozenset({"EXIT", "DERISK"})
_ECONOMIC_INTENTS = _ENTRY_INTENTS | _EXIT_INTENTS

_RESOLVED_PAYOUT_STATES = frozenset({"RESOLVED_ZERO", "RESOLVED_NONZERO"})
_PENDING_PAYOUT_STATES = frozenset({"UNKNOWN", "UNRESOLVED"})

_EPSILON = 1e-9

_SUPERSESSION_EVENT_TYPE = "POSITION_IDENTITY_SUPERSEDED"


# --------------------------------------------------------------------------
# Typed refusals -- fail-closed is a feature. Never caught to synthesize a
# fallback number; a caller that wants degraded behavior must catch a named
# subclass and make that decision explicitly.
# --------------------------------------------------------------------------


class ReducerRefusal(Exception):
    """Base class for every fail-closed refusal this reducer can raise."""


class MissingFillSyncWatermarkError(ReducerRefusal):
    """No ``fill_sync_watermarks`` row for the claimed source.

    Without a watermark row, fill coverage for this position cannot be
    proven complete (LX-T4 Attack A: a fill persisted after the last claimed
    coverage boundary but before this read would be silently invisible).
    """


class UnmigratedIdentitySupersessionSchemaError(ReducerRefusal):
    """The live ``position_events.event_type`` CHECK does not yet admit
    ``POSITION_IDENTITY_SUPERSEDED`` (pre scripts/migrations/
    2026_07_position_identity_supersession_check.py). Duplicate-position
    identities cannot be safely deduplicated on this connection.
    """


class ConditionAttributionMissingError(ReducerRefusal):
    """The position still holds open shares but the caller supplied no
    ``condition_id``/``outcome_index`` to look up payout truth. No
    production join resolves this yet -- the reducer refuses to guess.
    """


class UnrecognizedIntentKindError(ReducerRefusal):
    """A command carrying economic trade facts has an ``intent_kind`` this
    reducer does not fold into shares/cost-basis (e.g. a ``CANCEL`` command
    with confirmed fills is self-contradictory input, not a silent no-op).
    """


class OversoldPositionError(ReducerRefusal):
    """An EXIT/DERISK fill sells more shares than the reducer has tracked as
    open for this identity group -- signals missing ENTRY fill coverage
    upstream, never a valid sale.
    """


# --------------------------------------------------------------------------
# Output shape
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FillContribution:
    """One economic fill's contribution to a position's fold, in fold order."""

    command_id: str
    trade_id: str
    intent_kind: str
    filled_size: float
    fill_price: float
    fee_usd: float
    observed_at: str
    execution_ts: str | None


@dataclass(frozen=True)
class PositionEconomics:
    """Deterministic per-position economics for one reducer invocation.

    ``position_id`` is the identity the caller asked about; ``keeper_
    position_id`` is what it resolved to after folding
    POSITION_IDENTITY_SUPERSEDED facts (equal to ``position_id`` when no
    supersession applies).
    """

    position_id: str
    keeper_position_id: str
    absorbed_position_ids: tuple[str, ...]
    reducer_version: str
    net_shares: float
    cost_basis_usd: float
    realized_pnl_usd: float
    fees_usd: float
    fill_count: int
    payout_status: str  # CLOSED_VIA_FILLS | PENDING | RESOLVED_ZERO | RESOLVED_NONZERO
    payout_pnl_usd: float | None
    contributions: tuple[FillContribution, ...]

    @property
    def total_realized_pnl_usd(self) -> float | None:
        """Fill-realized P&L folded with payout P&L once resolved.

        ``None`` while ``payout_status == "PENDING"`` -- the honesty
        invariant this dataclass exists to enforce: an unresolved condition
        never reports a specific total, not even the fill-only partial.
        """
        if self.payout_status == "PENDING":
            return None
        if self.payout_pnl_usd is None:
            return self.realized_pnl_usd
        return self.realized_pnl_usd + self.payout_pnl_usd


# --------------------------------------------------------------------------
# Coverage refusals
# --------------------------------------------------------------------------


def _require_fill_sync_coverage(conn: sqlite3.Connection, source: str) -> None:
    row = conn.execute(
        "SELECT 1 FROM fill_sync_watermarks WHERE source = ?", (source,)
    ).fetchone()
    if row is None:
        raise MissingFillSyncWatermarkError(
            f"no fill_sync_watermarks row for source={source!r} -- fill "
            "coverage for this position cannot be proven complete"
        )


def _require_supersession_schema(conn: sqlite3.Connection) -> None:
    if not position_events_admits_event_type(conn, _SUPERSESSION_EVENT_TYPE):
        raise UnmigratedIdentitySupersessionSchemaError(
            "position_events.event_type CHECK does not admit "
            f"{_SUPERSESSION_EVENT_TYPE!r} on this connection -- run "
            "scripts/migrations/2026_07_position_identity_supersession_check.py "
            "before reducing economics (duplicate identities cannot be "
            "deduplicated without this fact type)"
        )


# --------------------------------------------------------------------------
# Identity resolution -- fold POSITION_IDENTITY_SUPERSEDED facts
# --------------------------------------------------------------------------


def _identity_superseded_payloads(
    conn: sqlite3.Connection, *, position_id: str | None = None
) -> list[dict]:
    if position_id is None:
        rows = conn.execute(
            "SELECT payload_json FROM position_events WHERE event_type = ? "
            "ORDER BY position_id, sequence_no ASC",
            (_SUPERSESSION_EVENT_TYPE,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT payload_json FROM position_events "
            "WHERE event_type = ? AND position_id = ? ORDER BY sequence_no ASC",
            (_SUPERSESSION_EVENT_TYPE, position_id),
        ).fetchall()
    return [json.loads(row[0]) for row in rows]


def _resolve_identity_group(
    conn: sqlite3.Connection, position_id: str
) -> tuple[str, tuple[str, ...]]:
    """Return ``(keeper_position_id, absorbed_position_ids)`` for ``position_id``.

    ``position_id`` may itself be a keeper (its own POSITION_IDENTITY_SUPERSEDED
    events), or may appear as an ``absorbed_position_ids`` entry inside
    another position's event -- redirected to that keeper. Chains are
    followed defensively (cycle-guarded) though the writer never produces
    one: an absorbed row is voided and can never become a keeper itself.
    """
    all_payloads = _identity_superseded_payloads(conn)

    seen: set[str] = set()
    current = position_id
    while current not in seen:
        seen.add(current)
        redirect = next(
            (
                payload["keeper_position_id"]
                for payload in all_payloads
                if current in payload.get("absorbed_position_ids", [])
            ),
            None,
        )
        if redirect is None:
            break
        current = redirect

    keeper = current
    absorbed: set[str] = set()
    for payload in all_payloads:
        if payload.get("keeper_position_id") == keeper:
            absorbed.update(payload.get("absorbed_position_ids", []))
    absorbed.discard(keeper)
    return keeper, tuple(sorted(absorbed))


# --------------------------------------------------------------------------
# Fill gathering + fold
# --------------------------------------------------------------------------


def _commands_for_positions(
    conn: sqlite3.Connection, position_ids: tuple[str, ...]
) -> list[sqlite3.Row]:
    placeholders = ",".join("?" for _ in position_ids)
    return conn.execute(
        f"""
        SELECT command_id, intent_kind, position_id
          FROM venue_commands
         WHERE position_id IN ({placeholders})
         ORDER BY command_id
        """,
        position_ids,
    ).fetchall()


def _fill_contributions(
    conn: sqlite3.Connection, commands: list[sqlite3.Row]
) -> list[FillContribution]:
    contributions: list[FillContribution] = []
    for row in commands:
        command_id = row["command_id"]
        intent_kind = row["intent_kind"]
        facts = economic_trade_facts_for_command(conn, command_id)
        if not facts:
            continue
        if intent_kind not in _ECONOMIC_INTENTS:
            raise UnrecognizedIntentKindError(
                f"command_id={command_id!r} intent_kind={intent_kind!r} carries "
                f"{len(facts)} economic trade fact(s) but is not a recognized "
                "economic intent (ENTRY/EXIT/DERISK)"
            )
        for fact in facts:
            contributions.append(
                FillContribution(
                    command_id=command_id,
                    trade_id=fact["trade_id"],
                    intent_kind=intent_kind,
                    filled_size=float(fact["filled_size"]),
                    fill_price=float(fact["fill_price"]),
                    fee_usd=(fact["fee_paid_micro"] or 0) / 1_000_000.0,
                    observed_at=fact["observed_at"],
                    execution_ts=fact.get("execution_ts"),
                )
            )
    # Fold in trade EXECUTION order (venue match time), NOT ingestion order.
    # observed_at is re-stamped by lifecycle re-observations (e.g. a REST
    # re-confirmation of an entry long after its exits), which would otherwise
    # reorder an entry behind its own exits and fabricate OversoldPositionError.
    # execution_ts (fill_dedup's stable min venue_timestamp per trade) is the
    # authoritative order; fall back to observed_at only when no venue timestamp
    # exists on any revision. Normalize the ISO separator so venue_timestamp
    # ("...T...") and observed_at ("... ...") compare chronologically.
    contributions.sort(
        key=lambda c: (
            (c.execution_ts or c.observed_at or "").replace(" ", "T"),
            (c.observed_at or "").replace(" ", "T"),
            c.trade_id,
        )
    )
    return contributions


def _fold_fills(
    contributions: list[FillContribution],
) -> tuple[float, float, float, float]:
    """Weighted-average-cost fold. Returns (net_shares, cost_basis_usd,
    realized_pnl_usd, fees_usd)."""
    net_shares = 0.0
    cost_basis_usd = 0.0
    realized_pnl_usd = 0.0
    fees_usd = 0.0
    for c in contributions:
        fees_usd += c.fee_usd
        if c.intent_kind in _ENTRY_INTENTS:
            net_shares += c.filled_size
            cost_basis_usd += c.filled_size * c.fill_price + c.fee_usd
            continue
        # EXIT / DERISK
        if c.filled_size > net_shares + _EPSILON:
            raise OversoldPositionError(
                f"command_id={c.command_id!r} trade_id={c.trade_id!r} sells "
                f"{c.filled_size} shares but only {net_shares} are tracked "
                "open -- missing ENTRY fill coverage upstream"
            )
        avg_cost = (cost_basis_usd / net_shares) if net_shares > _EPSILON else 0.0
        sell_qty = c.filled_size
        realized_pnl_usd += sell_qty * (c.fill_price - avg_cost) - c.fee_usd
        cost_basis_usd -= sell_qty * avg_cost
        net_shares -= sell_qty

    if net_shares <= _EPSILON:
        net_shares = 0.0
        cost_basis_usd = 0.0

    return net_shares, cost_basis_usd, realized_pnl_usd, fees_usd


# --------------------------------------------------------------------------
# Payout
# --------------------------------------------------------------------------


def _payout_for_condition(
    conn: sqlite3.Connection, condition_id: str, outcome_index: int
) -> tuple[str, float | None]:
    """Return ``(payout_status, payout_per_share)``.

    ``payout_per_share`` is ``None`` unless the current observation is
    RESOLVED_ZERO/RESOLVED_NONZERO. Absence of any observation row and an
    explicit UNKNOWN/UNRESOLVED row are both folded to PENDING -- both mean
    "the payout is not yet known," which is exactly the state the schema's
    own UNKNOWN-never-zero invariant (src/state/schema/
    payout_observations_schema.py) already encodes.
    """
    row = conn.execute(
        """
        SELECT payout_numerator, payout_denominator, state
          FROM payout_observations
         WHERE condition_id = ? AND outcome_index = ?
         ORDER BY id DESC LIMIT 1
        """,
        (condition_id, outcome_index),
    ).fetchone()
    if row is None:
        return "PENDING", None
    numerator, denominator, state = row
    if state in _PENDING_PAYOUT_STATES:
        return "PENDING", None
    assert state in _RESOLVED_PAYOUT_STATES  # DB CHECK guarantees this
    return state, numerator / denominator


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def reduce_position_economics(
    conn: sqlite3.Connection,
    position_id: str,
    *,
    condition_id: str | None = None,
    outcome_index: int | None = None,
    fill_sync_source: str = "polymarket_v2",
    reducer_version: str = REDUCER_VERSION,
) -> PositionEconomics:
    """Compute deterministic economics for one position identity.

    ``position_id`` may be a keeper or an absorbed identity -- either
    resolves to the same keeper-scoped result. Raises a ``ReducerRefusal``
    subclass (never a silent fallback) when required coverage is missing;
    see the module docstring's honesty invariant.
    """
    if not position_id:
        raise ValueError("position_id is required")

    _require_fill_sync_coverage(conn, fill_sync_source)
    _require_supersession_schema(conn)

    keeper_id, absorbed_ids = _resolve_identity_group(conn, position_id)
    all_ids = (keeper_id, *absorbed_ids)

    commands = _commands_for_positions(conn, all_ids)
    contributions = _fill_contributions(conn, commands)
    net_shares, cost_basis_usd, realized_pnl_usd, fees_usd = _fold_fills(contributions)

    payout_status = "CLOSED_VIA_FILLS"
    payout_pnl_usd: float | None = None
    if net_shares > _EPSILON:
        if not condition_id or outcome_index is None:
            raise ConditionAttributionMissingError(
                f"position_id={keeper_id!r} still holds {net_shares} open "
                "shares -- condition_id/outcome_index required to determine "
                "payout truth"
            )
        payout_status, payout_per_share = _payout_for_condition(
            conn, condition_id, outcome_index
        )
        if payout_status in _RESOLVED_PAYOUT_STATES:
            payout_pnl_usd = net_shares * payout_per_share - cost_basis_usd

    return PositionEconomics(
        position_id=position_id,
        keeper_position_id=keeper_id,
        absorbed_position_ids=absorbed_ids,
        reducer_version=reducer_version,
        net_shares=net_shares,
        cost_basis_usd=cost_basis_usd,
        realized_pnl_usd=realized_pnl_usd,
        fees_usd=fees_usd,
        fill_count=len(contributions),
        payout_status=payout_status,
        payout_pnl_usd=payout_pnl_usd,
        contributions=tuple(contributions),
    )


__all__ = [
    "REDUCER_VERSION",
    "ReducerRefusal",
    "MissingFillSyncWatermarkError",
    "UnmigratedIdentitySupersessionSchemaError",
    "ConditionAttributionMissingError",
    "UnrecognizedIntentKindError",
    "OversoldPositionError",
    "FillContribution",
    "PositionEconomics",
    "reduce_position_economics",
]

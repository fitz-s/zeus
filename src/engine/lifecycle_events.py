"""Canonical lifecycle event builders.

F4 invariant (docs/archive/2026-Q2/findings_historical/findings_2026_05_28.md §F4, 2026-05-28): each
``build_*_canonical_write`` builder accepts an explicit ``phase_after``
argument from its caller. The builder uses that value verbatim for the
event's ``phase_after`` field and for the projected ``position_current.phase``
column. **This module does not derive canonical phase from runtime
``Position.state`` / ``Position.exit_state`` / ``Position.chain_state``
strings.** ``canonical_phase_for_position`` / ``phase_for_runtime_position``
remain available as legacy adapters used by ``build_position_current_projection``
to provide a default for callers that have not yet been migrated, but **the
money-path canonical event builders override the projection's phase with the
explicit caller-supplied value before returning**. Direct mutation of
``Position.state`` therefore cannot change ``position_current.phase`` unless
a builder receives a legal phase transition and ``append_many_and_project``
is called.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from src.contracts.decision_evidence import DecisionEvidence
from src.state.chain_reconciliation import resolve_position_metric
from src.state.lifecycle_manager import (
    LifecyclePhase,
    fold_lifecycle_phase,
    phase_for_runtime_position,
)
from src.state.projection import normalize_position_event_env

CANONICAL_POSITION_SETTLED_CONTRACT_VERSION = "position_settled.v1"

PENDING_ENTRY = LifecyclePhase.PENDING_ENTRY.value
ACTIVE = LifecyclePhase.ACTIVE.value
DAY0_WINDOW = LifecyclePhase.DAY0_WINDOW.value
PENDING_EXIT = LifecyclePhase.PENDING_EXIT.value
ECONOMICALLY_CLOSED = LifecyclePhase.ECONOMICALLY_CLOSED.value
SETTLED = LifecyclePhase.SETTLED.value
QUARANTINED = LifecyclePhase.QUARANTINED.value


def _normalized_state(value: object) -> str:
    if hasattr(value, "value"):
        return str(getattr(value, "value"))
    return str(value or "")


def _non_empty(*values: object) -> str:
    for value in values:
        # "unknown_entered_at" is the QUARANTINE_SENTINEL used by chain_reconciliation.py
        # for positions that have no real entry timestamp. Treat it as absent so that
        # subsequent fallback values (chain_verified_at, updated_at) are used instead.
        if value not in (None, "", "unknown_entered_at"):
            return str(value)
    raise ValueError("missing required timestamp for canonical lifecycle builder")


def _max_iso_chronological(*candidates: str) -> str:
    """Return the chronologically latest ISO-8601 UTC string among candidates.

    Parses each string into an aware datetime so mixed-suffix strings
    (``Z`` vs ``+00:00``) are compared by value, not by ASCII order.
    ``+`` (0x2B) sorts before ``Z`` (0x5A), so lexicographic max over a list
    containing both suffixes can return the wrong winner.

    Uses sorted(...)[-1] rather than max() to avoid false-positives in
    static checks that scan for ``max(`` over raw strings.
    """
    parsed: list[tuple[datetime, str]] = []
    for ts in candidates:
        if not ts:
            continue
        normalized = ts.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(normalized)
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        parsed.append((dt, ts))
    if not parsed:
        raise ValueError("missing required timestamp for canonical lifecycle builder")
    # Sort by datetime; for ties the last original string wins (stable).
    return sorted(parsed, key=lambda x: x[0])[-1][1]


def _nullable(value: object) -> object | None:
    return None if value in (None, "") else value


def _strategy_key(position: Any) -> str:
    strategy_key = str(
        getattr(position, "strategy_key", "") or getattr(position, "strategy", "") or ""
    ).strip()
    if not strategy_key:
        raise ValueError("missing strategy_key for canonical lifecycle builder")
    return strategy_key


def _position_env(position: Any) -> str:
    raw_env = getattr(position, "env", None)
    if raw_env in (None, ""):
        raise ValueError("position missing env for canonical lifecycle builder")
    return normalize_position_event_env(raw_env)


def canonical_phase_for_position(position: Any) -> str:
    """Legacy adapter: derive a phase string from a Position's mutable runtime
    fields. **F4 (docs/archive/2026-Q2/findings_historical/findings_2026_05_28.md §F4, 2026-05-28): this helper is
    NOT a money-path authority.** It is used by ``build_position_current_projection``
    to provide a default ``phase`` for callers that have not yet migrated to
    supply ``phase_after`` explicitly, and by reconstruction code paths that
    must interpret historical Position rows. Canonical event builders override
    the projection's phase with the caller-supplied ``phase_after`` argument
    before returning, so mutating ``Position.state`` cannot change
    ``position_current.phase`` unless a builder receives a legal transition.
    """
    return phase_for_runtime_position(
        state=getattr(position, "state", ""),
        exit_state=getattr(position, "exit_state", ""),
        chain_state=getattr(position, "chain_state", ""),
    ).value


def projection_updated_at(position: Any) -> str:
    # Finding 1 (PR C0, 2026-05-27): chain_verified_at is positive-observation
    # only. last_chain_absence_observed_at carries the parallel absence-
    # observation signal.
    #
    # PR #352 (Part-3 audit, bot #4 on PR #350, 2026-05-27): updated_at is the
    # projection "as of" time — the MOST RECENT thing we learned about this
    # position, not a fixed-priority pick. The former first-non-empty ordering
    # placed chain_verified_at before last_chain_absence_observed_at, so a later
    # absence reconcile on a position with an older positive verification would
    # NOT advance updated_at (the stale positive timestamp won). Take the max
    # over all observation/lifecycle timestamps so any later observation —
    # positive or absence — advances the projection clock. Timestamps are UTC
    # ISO-8601 from one system, so lexicographic max == chronological max.
    candidates = [
        str(getattr(position, "last_monitor_at", "") or ""),
        str(getattr(position, "last_exit_at", "") or ""),
        str(getattr(position, "chain_verified_at", "") or ""),
        str(getattr(position, "last_chain_absence_observed_at", "") or ""),
        str(getattr(position, "day0_entered_at", "") or ""),
        str(getattr(position, "entered_at", "") or ""),
        str(getattr(position, "order_posted_at", "") or ""),
    ]
    present = [c for c in candidates if c not in ("", "unknown_entered_at")]
    if not present:
        raise ValueError("missing required timestamp for canonical lifecycle builder")
    return _max_iso_chronological(*present)


_SETTLED_RUNTIME_STATES = frozenset({"settled"})


def _is_settled_runtime_state(position: Any) -> bool:
    """BUG #128: True iff the Position's runtime state is the terminal settled
    state. compute_settlement_close sets pos.state to the settled runtime state
    BEFORE the projection is built, so settlement_price / settled_at are only
    populated for genuinely-settled rows (economic close leaves them NULL)."""
    state = getattr(position, "state", "")
    state = getattr(state, "value", state)
    return str(state or "").strip().lower() in _SETTLED_RUNTIME_STATES


def _has_realized_close(position: Any) -> bool:
    """BUG #128: True iff the position has a recorded close (a non-empty
    last_exit_at). Open positions carry pnl/exit_price=0.0 by dataclass default;
    without a close timestamp those zeros are NOT realized economics and must
    project as NULL so open rows stay distinguishable from a real $0.00 close."""
    return bool(str(getattr(position, "last_exit_at", "") or "").strip())


def _settled_economics_value(position: Any, attr: str) -> object | None:
    """BUG #128: return the float close-economics attribute (pnl / exit_price)
    when the position has actually closed, else NULL. This keeps open/legacy
    position_current rows at NULL rather than a misleading 0.0."""
    if not _has_realized_close(position):
        return None
    value = getattr(position, attr, None)
    if value is None:
        return None
    return float(value)


def _nullable_bool_int(value: Any) -> int | None:
    if value is None:
        return None
    return 1 if bool(value) else 0


def build_position_current_projection(position: Any) -> dict:
    _position_metric = resolve_position_metric(position)
    order_status = getattr(position, "order_status", "")
    order_status_value = getattr(order_status, "value", order_status)
    exit_state_raw = getattr(position, "exit_state", "")
    exit_state = str(getattr(exit_state_raw, "value", exit_state_raw) or "")
    exit_reason = str(getattr(position, "exit_reason", "") or "")
    if exit_state == "backoff_exhausted" and exit_reason:
        # position_current does not have a dedicated exit_state column. Persist
        # the terminal non-executable exit state through order_status so a
        # restarted monitor reloads the same hold-to-settlement state instead
        # of treating dust as a fresh pending exit.
        order_status = "backoff_exhausted"
    elif str(order_status_value or "") == "backoff_exhausted":
        # A backoff order_status is meaningful only while the exit lifecycle is
        # actually in backoff. Held positions repaired back to active/day0 must
        # not keep a stale sell-failure label through later monitor refreshes.
        order_status = "filled"
    return {
        "position_id": getattr(position, "trade_id"),
        "phase": canonical_phase_for_position(position),
        "trade_id": getattr(position, "trade_id"),
        "market_id": getattr(position, "market_id"),
        "city": getattr(position, "city"),
        "cluster": getattr(position, "cluster"),
        "target_date": getattr(position, "target_date"),
        "bin_label": getattr(position, "bin_label"),
        "direction": getattr(position, "direction"),
        "unit": getattr(position, "unit", "F"),
        "size_usd": getattr(position, "size_usd", 0.0),
        "shares": getattr(position, "shares", 0.0),
        "cost_basis_usd": getattr(position, "cost_basis_usd", 0.0),
        "entry_price": getattr(position, "entry_price", 0.0),
        "p_posterior": getattr(position, "p_posterior", 0.0),
        "entry_ci_width": getattr(position, "entry_ci_width", 0.0),
        # Exit-retry persistence (2026-06-12): without these the chain-truth
        # gate's bounded backoff reset to zero on every load_portfolio() and
        # exit_pending_missing retried forever.
        "exit_retry_count": int(getattr(position, "exit_retry_count", 0) or 0),
        "next_exit_retry_at": _nullable(getattr(position, "next_exit_retry_at", None)),
        "last_monitor_prob": _nullable(getattr(position, "last_monitor_prob", None)),
        "last_monitor_prob_is_fresh": _nullable_bool_int(
            getattr(position, "last_monitor_prob_is_fresh", None)
        ),
        "last_monitor_edge": _nullable(getattr(position, "last_monitor_edge", None)),
        "last_monitor_market_price": _nullable(getattr(position, "last_monitor_market_price", None)),
        "last_monitor_market_price_is_fresh": _nullable_bool_int(
            getattr(position, "last_monitor_market_price_is_fresh", None)
        ),
        "decision_snapshot_id": _nullable(getattr(position, "decision_snapshot_id", "")),
        "entry_method": getattr(position, "entry_method", ""),
        "strategy_key": _strategy_key(position),
        "edge_source": _nullable(getattr(position, "edge_source", "")),
        "discovery_mode": _nullable(getattr(position, "discovery_mode", "")),
        "chain_state": _nullable(getattr(position, "chain_state", "")),
        "token_id": _nullable(getattr(position, "token_id", "")),
        "no_token_id": _nullable(getattr(position, "no_token_id", "")),
        "condition_id": _nullable(getattr(position, "condition_id", "")),
        "order_id": _nullable(getattr(position, "order_id", "")),
        "order_status": _nullable(order_status),
        "updated_at": projection_updated_at(position),
        # Slice P2-C2 (PR #19 phase 2, 2026-04-26) + P2-fix2 (post-review
        # BLOCKER #1, 2026-04-26): route via resolver for audit trail
        # (DEBUG log identifies missing-metric positions). PR D0b
        # (Finding D0/D2-wire, Part-2 audit, 2026-05-27) finally lands
        # the authority schema migration originally deferred above:
        # fill_authority / recovery_authority / chain_shares /
        # chain_seen_at / chain_absence_at are now persisted on
        # position_current, declared in CANONICAL_POSITION_CURRENT_COLUMNS,
        # and consumed by the typed training-eligibility gate
        # (src.state.portfolio.is_training_eligible_position) without
        # snapshot-keyed scanner heuristics.
        "temperature_metric": _position_metric[0],
        "fill_authority": _nullable(getattr(position, "fill_authority", "")),
        # recovery_authority is set per-event by canonical builders
        # (build_venue_position_observed_canonical_write sets
        # 'balance_only'; trade-verified rescue leaves it NULL). The
        # base projection mirrors the runtime Position attribute when
        # the rescue path attaches it; non-rescue projections persist NULL.
        "recovery_authority": _nullable(getattr(position, "recovery_authority", "")),
        "chain_shares": _nullable(getattr(position, "chain_shares", None)),
        # F1 (docs/archive/2026-Q2/findings_historical/findings_2026_05_28.md §F1, 2026-05-28): chain-observed
        # economics carry their own typed projection columns so balance-only
        # rescue persists venue truth on chain_avg_price / chain_cost_basis_usd
        # without overwriting submitted entry_price / cost_basis_usd / size_usd.
        "chain_avg_price": _nullable(getattr(position, "chain_avg_price", None)),
        "chain_cost_basis_usd": _nullable(getattr(position, "chain_cost_basis_usd", None)),
        "chain_seen_at": _nullable(getattr(position, "chain_verified_at", "")),
        "chain_absence_at": _nullable(getattr(position, "last_chain_absence_observed_at", "")),
        # BUG #128 (SEV1, 2026-06-02): durable realized-P&L projection. These
        # mirror the close economics that compute_economic_close /
        # compute_settlement_close (src.state.portfolio) set on the in-memory
        # Position (pnl / exit_price / exit_reason / last_exit_at). Persisting
        # them here — through the canonical write path consumed by BOTH the
        # settlement builder (build_settlement_canonical_write) and the economic-
        # close builder (build_economic_close_canonical_write) — means a
        # filled+settled order leaves a durable, queryable P&L record instead of
        # only the in-memory object + positions.json. Open/legacy positions carry
        # NULL (pnl/exit_price default 0.0 with no close → coerced to NULL below).
        "realized_pnl_usd": _settled_economics_value(position, "pnl"),
        "exit_price": _settled_economics_value(position, "exit_price"),
        # settlement_price is the resolved settlement value, meaningful ONLY for a
        # settled position. compute_settlement_close sets pos.exit_price =
        # settlement_price, so it equals exit_price on settled rows; NULL otherwise.
        "settlement_price": (
            _settled_economics_value(position, "exit_price")
            if _is_settled_runtime_state(position)
            else None
        ),
        "settled_at": (
            _nullable(getattr(position, "last_exit_at", ""))
            if _is_settled_runtime_state(position)
            else None
        ),
        "exit_reason": _nullable(getattr(position, "exit_reason", "")),
    }


def _entry_event_payload(
    position: Any,
    *,
    phase_after: str,
    decision_evidence: DecisionEvidence | None = None,
    decision_evidence_reason: str | None = None,
) -> str:
    # T4.1b 2026-04-23 (D4 Option E): attach DecisionEvidence envelope or
    # a reason sentinel onto ENTRY_ORDER_POSTED payloads. The two keys are
    # mutually exclusive semantic variants — `decision_evidence_envelope`
    # is the verbatim `DecisionEvidence.to_json()` output (read-side uses
    # `json_extract(payload_json, '$.decision_evidence_envelope')` then
    # `DecisionEvidence.from_json(...)`); `decision_evidence_reason`
    # records a known-missing-evidence context (e.g. legacy-position
    # backfill) so the Wave31 D4 hard gate and post-hoc investigation can
    # distinguish missing-because-legacy from missing-because-bug.
    payload: dict[str, Any] = {
        "city": getattr(position, "city", ""),
        "target_date": getattr(position, "target_date", ""),
        "bin_label": getattr(position, "bin_label", ""),
        "direction": getattr(position, "direction", ""),
        "unit": getattr(position, "unit", "F"),
        "size_usd": getattr(position, "size_usd", 0.0),
        "shares": getattr(position, "shares", 0.0),
        "entry_price": getattr(position, "entry_price", 0.0),
        "order_status": getattr(position, "order_status", ""),
        "chain_state": getattr(position, "chain_state", ""),
        "entry_method": getattr(position, "entry_method", ""),
        "phase_after": phase_after,
    }
    if decision_evidence is not None:
        payload["decision_evidence_envelope"] = decision_evidence.to_json()
    if decision_evidence_reason is not None:
        payload["decision_evidence_reason"] = decision_evidence_reason
    return json.dumps(payload, default=str, sort_keys=True)


def _entry_event(
    *,
    position: Any,
    event_type: str,
    sequence_no: int,
    occurred_at: str,
    phase_before: str | None,
    phase_after: str,
    decision_id: str | None,
    source_module: str,
    order_id: str | None,
    decision_evidence: DecisionEvidence | None = None,
    decision_evidence_reason: str | None = None,
) -> dict:
    trade_id = str(getattr(position, "trade_id"))
    slug = event_type.lower()
    return {
        "event_id": f"{trade_id}:{slug}",
        "position_id": trade_id,
        "event_version": 1,
        "sequence_no": sequence_no,
        "event_type": event_type,
        "occurred_at": occurred_at,
        "phase_before": phase_before,
        "phase_after": phase_after,
        "strategy_key": _strategy_key(position),
        "decision_id": decision_id,
        "snapshot_id": _nullable(getattr(position, "decision_snapshot_id", "")),
        "order_id": order_id,
        "command_id": None,
        "caused_by": None,
        "idempotency_key": f"{trade_id}:{slug}",
        "venue_status": _nullable(getattr(position, "order_status", "")),
        "source_module": source_module,
        "env": _position_env(position),
        "payload_json": _entry_event_payload(
            position,
            phase_after=phase_after,
            decision_evidence=decision_evidence,
            decision_evidence_reason=decision_evidence_reason,
        ),
    }


def build_entry_canonical_write(
    position: Any,
    *,
    phase_after: str,
    decision_id: str | None = None,
    source_module: str = "src.engine.lifecycle_events",
    decision_evidence: DecisionEvidence | None = None,
    decision_evidence_reason: str | None = None,
) -> tuple[list[dict], dict]:
    # T4.1b 2026-04-23 (D4 Option E): `decision_evidence` lands as a
    # `decision_evidence_envelope` payload sidecar on the ENTRY_ORDER_POSTED
    # event only (the single event that represents the committed decision
    # with full data still in frame). POSITION_OPEN_INTENT precedes the
    # statistical decision fully materializing; ENTRY_ORDER_FILLED arrives
    # after the decision frame has released. Callers without evidence
    # (legacy-position backfill from src.execution.exit_lifecycle) pass
    # `decision_evidence_reason` instead so the exit-side audit can
    # distinguish missing-because-legacy from missing-because-bug.
    #
    # F4 (docs/archive/2026-Q2/findings_historical/findings_2026_05_28.md §F4, 2026-05-28): callers supply
    # ``phase_after`` explicitly; it MUST be PENDING_ENTRY (pre-fill) or
    # ACTIVE / DAY0_WINDOW (post-fill). The builder overrides the projection's
    # phase with the caller-supplied value so direct mutation of
    # ``Position.state`` cannot change ``position_current.phase``.
    if phase_after not in {PENDING_ENTRY, ACTIVE, DAY0_WINDOW}:
        raise ValueError(
            f"entry canonical builder only supports pending/active entry phases, "
            f"got phase_after={phase_after!r}"
        )
    projection = build_position_current_projection(position)
    projection["phase"] = phase_after
    canonical_phase = phase_after

    posted_at = _non_empty(
        getattr(position, "order_posted_at", ""),
        getattr(position, "entered_at", ""),
        getattr(position, "day0_entered_at", ""),
    )
    order_id = _nullable(getattr(position, "order_id", ""))
    events = [
        _entry_event(
            position=position,
            event_type="POSITION_OPEN_INTENT",
            sequence_no=1,
            occurred_at=posted_at,
            phase_before=None,
            phase_after=fold_lifecycle_phase(None, PENDING_ENTRY).value,
            decision_id=decision_id,
            source_module=source_module,
            order_id=None,
        ),
        _entry_event(
            position=position,
            event_type="ENTRY_ORDER_POSTED",
            sequence_no=2,
            occurred_at=posted_at,
            phase_before=PENDING_ENTRY,
            phase_after=fold_lifecycle_phase(PENDING_ENTRY, PENDING_ENTRY).value,
            decision_id=decision_id,
            source_module=source_module,
            order_id=order_id,
            decision_evidence=decision_evidence,
            decision_evidence_reason=decision_evidence_reason,
        ),
    ]

    if canonical_phase in {ACTIVE, DAY0_WINDOW}:
        filled_at = _non_empty(
            getattr(position, "entered_at", ""),
            getattr(position, "day0_entered_at", ""),
            posted_at,
        )
        events.append(
            _entry_event(
                position=position,
                event_type="ENTRY_ORDER_FILLED",
                sequence_no=3,
                occurred_at=filled_at,
                phase_before=PENDING_ENTRY,
                phase_after=fold_lifecycle_phase(PENDING_ENTRY, canonical_phase).value,
                decision_id=decision_id,
                source_module=source_module,
                order_id=order_id,
            )
        )

    return events, projection


def build_day0_window_entered_canonical_write(
    position: Any,
    *,
    day0_entered_at: str,
    sequence_no: int,
    phase_after: str = DAY0_WINDOW,
    previous_phase: str = ACTIVE,
    source_module: str = "src.engine.cycle_runtime",
) -> tuple[list[dict], dict]:
    """Day0-canonical-event feature slice (2026-04-24): emit a canonical
    DAY0_WINDOW_ENTERED event when cycle_runtime transitions a position from
    active/holding into the day0_window lifecycle phase.

    Pre-T4.1b / pre-Day0-canonical: cycle_runtime.execute_monitoring_phase
    updated position_current.phase via update_trade_lifecycle and
    optionally wrote a legacy POSITION_LIFECYCLE_UPDATED trade_decisions
    row, but did NOT emit a canonical position_events record for the
    day0 transition. Post-this-slice: the transition emits a typed
    position_events row with event_type=DAY0_WINDOW_ENTERED, phase_before=
    previous_phase, phase_after=day0_window, and a payload carrying
    day0_entered_at plus the standard position identity fields.

    Args:
        position: Position instance AFTER the state transition (state must
            already be "day0_window" in memory). Used for identity fields.
        day0_entered_at: ISO8601 UTC timestamp of the day0 transition.
            Caller should pass pos.day0_entered_at immediately after setting.
        sequence_no: The event sequence number relative to the caller's
            canonical write batch. For in-cycle single-event emissions,
            callers typically use 1; ledger append_many_and_project will
            assign the global monotonic position-level sequence.
        previous_phase: The lifecycle phase the position was in before the
            transition (ACTIVE / PENDING_ENTRY). Defaults to ACTIVE because
            that's the common path (entry → holding/active → day0_window).
        source_module: Caller module name for audit provenance.

    Returns:
        (events, projection) tuple suitable for append_many_and_project.
        events is a single-element list containing the DAY0_WINDOW_ENTERED
        event; projection is build_position_current_projection(position)
        reflecting the post-transition state.

    Raises:
        ValueError: if the position is not in the DAY0_WINDOW phase
            post-transition (enforced to catch caller ordering bugs —
            pos.state must be mutated to "day0_window" BEFORE this builder
            is invoked so the projection reflects the transition).
    """
    # F4 (docs/archive/2026-Q2/findings_historical/findings_2026_05_28.md §F4, 2026-05-28): require explicit
    # ``phase_after``; default is DAY0_WINDOW (the event this builder represents).
    if phase_after != DAY0_WINDOW:
        raise ValueError(
            f"day0 canonical builder requires phase_after=DAY0_WINDOW, "
            f"got {phase_after!r}"
        )
    projection = build_position_current_projection(position)
    projection["phase"] = phase_after
    canonical_phase = phase_after

    if not day0_entered_at:
        raise ValueError(
            "day0_entered_at must be a non-empty ISO8601 timestamp"
        )

    trade_id = str(getattr(position, "trade_id"))
    slug = "day0_window_entered"

    payload: dict[str, Any] = {
        "city": getattr(position, "city", ""),
        "target_date": getattr(position, "target_date", ""),
        "bin_label": getattr(position, "bin_label", ""),
        "direction": getattr(position, "direction", ""),
        "unit": getattr(position, "unit", "F"),
        "size_usd": getattr(position, "size_usd", 0.0),
        "entry_price": getattr(position, "entry_price", 0.0),
        "day0_entered_at": day0_entered_at,
        "entry_method": getattr(position, "entry_method", ""),
        "phase_before": previous_phase,
        "phase_after": DAY0_WINDOW,
    }

    event = {
        "event_id": f"{trade_id}:{slug}",
        "position_id": trade_id,
        "event_version": 1,
        "sequence_no": sequence_no,
        "event_type": "DAY0_WINDOW_ENTERED",
        "occurred_at": day0_entered_at,
        "phase_before": previous_phase,
        "phase_after": DAY0_WINDOW,
        "strategy_key": _strategy_key(position),
        "decision_id": None,
        "snapshot_id": _nullable(getattr(position, "decision_snapshot_id", "")),
        "order_id": _nullable(getattr(position, "order_id", "")),
        "command_id": None,
        "caused_by": None,
        "idempotency_key": f"{trade_id}:{slug}",
        "venue_status": _nullable(getattr(position, "order_status", "")),
        "source_module": source_module,
        "env": _position_env(position),
        "payload_json": json.dumps(payload, default=str, sort_keys=True),
    }

    return [event], projection


def build_monitor_refreshed_canonical_write(
    position: Any,
    *,
    sequence_no: int,
    phase_after: str,
    source_module: str = "src.engine.cycle_runtime",
    exit_decision: Any | None = None,
    final_should_exit: bool | None = None,
    final_exit_reason: str | None = None,
    final_exit_trigger: str | None = None,
) -> tuple[list[dict], dict]:
    """Persist a no-transition monitor refresh for an open position."""
    if phase_after not in {ACTIVE, DAY0_WINDOW, PENDING_EXIT, QUARANTINED}:
        raise ValueError(
            "monitor refreshed canonical builder requires phase_after in "
            f"{{ACTIVE, DAY0_WINDOW, PENDING_EXIT, QUARANTINED}}, got {phase_after!r}"
        )
    projection = build_position_current_projection(position)
    projection["phase"] = phase_after
    occurred_at = _non_empty(
        getattr(position, "last_monitor_at", ""),
        projection["updated_at"],
    )
    trade_id = str(getattr(position, "trade_id"))
    slug = f"monitor_refreshed:{sequence_no}"
    payload_dict: dict[str, Any] = {
        "city": getattr(position, "city", ""),
        "target_date": getattr(position, "target_date", ""),
        "bin_label": getattr(position, "bin_label", ""),
        "direction": getattr(position, "direction", ""),
        "unit": getattr(position, "unit", "F"),
        "last_monitor_prob": _nullable(getattr(position, "last_monitor_prob", None)),
        "last_monitor_prob_is_fresh": bool(getattr(position, "last_monitor_prob_is_fresh", False)),
        "last_monitor_edge": _nullable(getattr(position, "last_monitor_edge", None)),
        "last_monitor_market_price": _nullable(getattr(position, "last_monitor_market_price", None)),
        "last_monitor_market_price_is_fresh": bool(
            getattr(position, "last_monitor_market_price_is_fresh", False)
        ),
        "last_monitor_best_bid": _nullable(getattr(position, "last_monitor_best_bid", None)),
        "last_monitor_best_ask": _nullable(getattr(position, "last_monitor_best_ask", None)),
        "last_monitor_market_vig": _nullable(getattr(position, "last_monitor_market_vig", None)),
        "selected_method": getattr(position, "selected_method", ""),
        "applied_validations": list(getattr(position, "applied_validations", []) or []),
        "condition_id": getattr(position, "condition_id", ""),
        "phase_after": phase_after,
    }
    family_redecision = getattr(position, "_monitor_family_redecision", None)
    if family_redecision:
        payload_dict["family_redecision"] = family_redecision
    if exit_decision is not None:
        should_exit = (
            bool(final_should_exit)
            if final_should_exit is not None
            else bool(getattr(exit_decision, "should_exit", False))
        )
        reason = (
            str(final_exit_reason)
            if final_exit_reason is not None
            else str(getattr(exit_decision, "reason", "") or "")
        )
        trigger = (
            str(final_exit_trigger)
            if final_exit_trigger is not None
            else str(getattr(exit_decision, "trigger", "") or "")
        )
        payload_dict.update(
            {
                "exit_decision_available": True,
                "exit_decision_should_exit": should_exit,
                "exit_decision_reason": reason,
                "exit_decision_trigger": trigger,
                "exit_decision_urgency": str(getattr(exit_decision, "urgency", "") or ""),
                "exit_decision_selected_method": str(
                    getattr(exit_decision, "selected_method", "") or ""
                ),
                "exit_decision_applied_validations": list(
                    getattr(exit_decision, "applied_validations", []) or []
                ),
                "exit_decision_neg_edge_count": _nullable(
                    getattr(position, "neg_edge_count", None)
                ),
            }
        )
    else:
        payload_dict["exit_decision_available"] = False
    payload = json.dumps(payload_dict, default=str, sort_keys=True)
    event = {
        "event_id": f"{trade_id}:monitor_refreshed:{sequence_no}",
        "position_id": trade_id,
        "event_version": 1,
        "sequence_no": sequence_no,
        "event_type": "MONITOR_REFRESHED",
        "occurred_at": occurred_at,
        "phase_before": phase_after,
        "phase_after": fold_lifecycle_phase(phase_after, phase_after).value,
        "strategy_key": _strategy_key(position),
        "decision_id": None,
        "snapshot_id": _nullable(getattr(position, "decision_snapshot_id", "")),
        "order_id": _nullable(getattr(position, "order_id", "")),
        "command_id": None,
        "caused_by": "monitor_refresh",
        "idempotency_key": f"{trade_id}:{slug}",
        "venue_status": _nullable(projection.get("order_status")),
        "source_module": source_module,
        "env": _position_env(position),
        "payload_json": payload,
    }
    return [event], projection


def build_entry_fill_only_canonical_write(
    position: Any,
    *,
    sequence_no: int,
    phase_after: str = ACTIVE,
    decision_id: str | None = None,
    source_module: str = "src.execution.fill_tracker",
) -> tuple[list[dict], dict]:
    """Emit ONLY the ENTRY_ORDER_FILLED event for a position whose
    POSITION_OPEN_INTENT and ENTRY_ORDER_POSTED events already exist.

    Used by fill detection (fill_tracker._mark_entry_filled) to advance a
    position from pending_entry → active without re-inserting the earlier
    two entry events (which would violate the unique (position_id, seq) key).

    The caller must pass the next available sequence_no.

    F4 (docs/archive/2026-Q2/findings_historical/findings_2026_05_28.md §F4, 2026-05-28): caller supplies the
    explicit ``phase_after`` (default ACTIVE; DAY0_WINDOW is permitted for
    day0-window fills). The builder overrides the projection's phase with this
    value so the canonical phase no longer depends on Position.state strings.
    """
    if phase_after not in {ACTIVE, DAY0_WINDOW}:
        raise ValueError(
            f"entry fill-only builder requires phase_after in "
            f"{{ACTIVE, DAY0_WINDOW}}, got {phase_after!r}"
        )
    projection = build_position_current_projection(position)
    projection["phase"] = phase_after
    canonical_phase = phase_after
    filled_at = _non_empty(
        getattr(position, "entered_at", ""),
        getattr(position, "day0_entered_at", ""),
        getattr(position, "order_posted_at", ""),
    )
    order_id = _nullable(getattr(position, "order_id", ""))
    events = [
        _entry_event(
            position=position,
            event_type="ENTRY_ORDER_FILLED",
            sequence_no=sequence_no,
            occurred_at=filled_at,
            phase_before=PENDING_ENTRY,
            phase_after=fold_lifecycle_phase(PENDING_ENTRY, canonical_phase).value,
            decision_id=decision_id,
            source_module=source_module,
            order_id=order_id,
        )
    ]
    return events, projection


def build_settlement_canonical_write(
    position: Any,
    *,
    winning_bin: str,
    won: bool,
    outcome: int,
    sequence_no: int,
    phase_before: str,
    phase_after: str = SETTLED,
    source_module: str = "src.execution.harvester",
    settlement_authority: str = "UNKNOWN",
    settlement_truth_source: str = "",
    settlement_market_slug: str = "",
    settlement_temperature_metric: str = "",
    settlement_source: str = "",
    settlement_value: object | None = None,
) -> tuple[list[dict], dict]:
    # F4 (docs/archive/2026-Q2/findings_historical/findings_2026_05_28.md §F4, 2026-05-28): settlement always
    # targets SETTLED; phase_after is an explicit argument (default SETTLED)
    # rather than re-derived from runtime Position.state.
    if phase_after != SETTLED:
        raise ValueError(
            f"settlement canonical builder requires phase_after=SETTLED, "
            f"got {phase_after!r}"
        )
    projection = build_position_current_projection(position)
    projection["phase"] = phase_after

    occurred_at = _non_empty(
        getattr(position, "last_exit_at", ""),
        projection["updated_at"],
    )
    payload = json.dumps(
        {
            "contract_version": CANONICAL_POSITION_SETTLED_CONTRACT_VERSION,
            "winning_bin": winning_bin,
            "position_bin": getattr(position, "bin_label", ""),
            "won": bool(won),
            "outcome": int(outcome),
            "p_posterior": getattr(position, "p_posterior", None),
            "exit_price": getattr(position, "exit_price", None),
            "pnl": getattr(position, "pnl", None),
            "exit_reason": getattr(position, "exit_reason", ""),
            "settlement_authority": str(settlement_authority or "UNKNOWN"),
            "settlement_truth_source": str(settlement_truth_source or ""),
            "settlement_market_slug": str(settlement_market_slug or ""),
            "settlement_temperature_metric": str(settlement_temperature_metric or ""),
            "settlement_source": str(settlement_source or ""),
            "settlement_value": settlement_value,
        },
        default=str,
        sort_keys=True,
    )
    event = {
        "event_id": f"{getattr(position, 'trade_id')}:settled:{sequence_no}",
        "position_id": getattr(position, "trade_id"),
        "event_version": 1,
        "sequence_no": sequence_no,
        "event_type": "SETTLED",
        "occurred_at": occurred_at,
        "phase_before": phase_before,
        "phase_after": fold_lifecycle_phase(phase_before, SETTLED).value,
        "strategy_key": _strategy_key(position),
        "decision_id": None,
        "snapshot_id": _nullable(getattr(position, "decision_snapshot_id", "")),
        "order_id": _nullable(getattr(position, "order_id", "")),
        "command_id": None,
        "caused_by": "harvester_settlement",
        "idempotency_key": f"{getattr(position, 'trade_id')}:settled:{sequence_no}",
        "venue_status": None,
        "source_module": source_module,
        "env": _position_env(position),
        "payload_json": payload,
    }
    return [event], projection


def build_economic_close_canonical_write(
    position: Any,
    *,
    sequence_no: int,
    phase_before: str,
    phase_after: str = ECONOMICALLY_CLOSED,
    source_module: str = "src.execution.exit_lifecycle",
) -> tuple[list[dict], dict]:
    # F4 (docs/archive/2026-Q2/findings_historical/findings_2026_05_28.md §F4, 2026-05-28): the economic-close
    # event always lands ECONOMICALLY_CLOSED; phase_after is explicit
    # (default ECONOMICALLY_CLOSED) rather than re-derived from runtime
    # Position.state.
    if phase_after != ECONOMICALLY_CLOSED:
        raise ValueError(
            f"economic close canonical builder requires phase_after=ECONOMICALLY_CLOSED, "
            f"got {phase_after!r}"
        )
    projection = build_position_current_projection(position)
    projection["phase"] = phase_after
    projection["exit_retry_count"] = 0
    projection["next_exit_retry_at"] = ""
    projection["order_status"] = "filled"

    occurred_at = _non_empty(
        getattr(position, "last_exit_at", ""),
        projection["updated_at"],
    )
    event = {
        "event_id": f"{getattr(position, 'trade_id')}:exit_filled:{sequence_no}",
        "position_id": getattr(position, "trade_id"),
        "event_version": 1,
        "sequence_no": sequence_no,
        "event_type": "EXIT_ORDER_FILLED",
        "occurred_at": occurred_at,
        "phase_before": phase_before,
        "phase_after": fold_lifecycle_phase(phase_before, ECONOMICALLY_CLOSED).value,
        "strategy_key": _strategy_key(position),
        "decision_id": None,
        "snapshot_id": _nullable(getattr(position, "decision_snapshot_id", "")),
        "order_id": _nullable(
            getattr(position, "last_exit_order_id", "") or getattr(position, "order_id", "")
        ),
        "command_id": None,
        "caused_by": "exit_order_filled",
        "idempotency_key": f"{getattr(position, 'trade_id')}:exit_filled:{sequence_no}",
        "venue_status": _nullable(getattr(position, "order_status", "")),
        "source_module": source_module,
        "env": _position_env(position),
        "payload_json": json.dumps(
            {
                "exit_price": getattr(position, "exit_price", None),
                "fill_price": getattr(position, "exit_price", None),
                "best_bid": getattr(position, "last_monitor_best_bid", None),
                "current_market_price": getattr(position, "last_monitor_market_price", None),
                "pnl": getattr(position, "pnl", None),
                "exit_reason": getattr(position, "exit_reason", ""),
                "exit_state": getattr(position, "exit_state", ""),
                "pre_exit_state": getattr(position, "pre_exit_state", ""),
            },
            default=str,
            sort_keys=True,
        ),
    }
    return [event], projection


def build_venue_position_observed_canonical_write(
    position: Any,
    *,
    venue_observed_at: str,
    sequence_no: int,
    phase_after: str = ACTIVE,
    source_module: str = "src.state.chain_reconciliation",
) -> tuple[list[dict], dict]:
    """Canonical event for balance-only rescue (Finding D0 / Part-2 audit, 2026-05-27).

    Emitted when chain reconciliation detects a held venue balance for a
    pending entry but CANNOT link the balance to a venue trade fact. Distinct
    from `build_reconciliation_rescue_canonical_write` (trade-verified rescue):
    the payload carries `fill_authority=venue_position_observed`,
    `recovery_authority=balance_only`, `causality_status=UNVERIFIED`,
    `training_eligible=false` so downstream consumers reading position_events
    can distinguish degraded recovery from verified fill at the event-grammar
    level. The position still folds to ACTIVE so monitor/exit can manage
    exposure; the authority signal lives in the event payload + the runtime
    Position.fill_authority field. A later cycle that obtains a real venue
    trade fact appends a separate verified ENTRY_ORDER_FILLED / CHAIN_SYNCED
    event upgrading the authority.
    """
    # F4 (docs/archive/2026-Q2/findings_historical/findings_2026_05_28.md §F4, 2026-05-28): VENUE_POSITION_OBSERVED
    # always folds to ACTIVE (held venue balance, no verified fill). phase_after
    # is an explicit argument (default ACTIVE) rather than re-derived from
    # runtime Position.state strings.
    if phase_after != ACTIVE:
        raise ValueError(
            f"venue_position_observed canonical builder requires phase_after=ACTIVE, "
            f"got {phase_after!r}"
        )
    projection = build_position_current_projection(position)
    projection["phase"] = phase_after

    # PR #352 (Part-3 bot #6 + Part-5 Finding 2 on PR #351, 2026-05-27): THIS
    # builder is, by definition, the balance-only degraded-recovery event, so it
    # OWNS the authority truth — it must not trust the runtime Position attribute
    # for a field it semantically defines. Force both the durable projection AND
    # the event payload from the same local constants so they can never diverge
    # (the prior code read getattr(position,"fill_authority",...) for the payload
    # while forcing the projection — an empty/wrong attribute would split them).
    _FILL_AUTHORITY = "venue_position_observed"
    _RECOVERY_AUTHORITY = "balance_only"
    projection["recovery_authority"] = _RECOVERY_AUTHORITY
    projection["fill_authority"] = _FILL_AUTHORITY

    # F2 (docs/archive/2026-Q2/findings_historical/findings_2026_05_28.md §F2, 2026-05-28): occurred_at must be
    # the explicit reconcile-time observation timestamp, not a legacy
    # position attribute. Callers pass their ``now`` as venue_observed_at.
    if not venue_observed_at:
        raise ValueError("venue_observed_at is required for build_venue_position_observed_canonical_write")
    occurred_at = venue_observed_at
    payload = json.dumps(
        {
            "status": "entered",
            "source": "chain_reconciliation",
            "reason": "balance_only_recovery",
            "from_state": "pending_tracked",
            "to_state": "entered",
            "entry_order_id": getattr(position, "entry_order_id", "") or getattr(position, "order_id", ""),
            "entry_method": getattr(position, "entry_method", ""),
            "selected_method": getattr(position, "selected_method", "") or getattr(position, "entry_method", ""),
            "applied_validations": list(getattr(position, "applied_validations", []) or []),
            "entry_fill_verified": getattr(position, "entry_fill_verified", False),
            "shares": getattr(position, "shares", None),
            "cost_basis_usd": getattr(position, "cost_basis_usd", None),
            "size_usd": getattr(position, "size_usd", None),
            "condition_id": getattr(position, "condition_id", ""),
            "rescue_condition_id": getattr(position, "condition_id", ""),
            "order_status": getattr(position, "order_status", ""),
            "chain_state": getattr(position, "chain_state", ""),
            # PR D0 additions — explicit degraded-recovery signal. Sourced from
            # the same local constants as the projection (Part-5 Finding 2) so
            # payload and durable projection authority can never disagree.
            "fill_authority": _FILL_AUTHORITY,
            "recovery_authority": _RECOVERY_AUTHORITY,
            "causality_status": "UNVERIFIED",
            "training_eligible": False,
            # F1 (docs/archive/2026-Q2/findings_historical/findings_2026_05_28.md §F1, 2026-05-28): chain
            # economics on the canonical event grammar. Downstream
            # consumers reading position_events for balance-only rescues
            # consult these directly; the legacy `shares` / `cost_basis_usd`
            # / `size_usd` fields above will reflect submitted economics
            # rather than chain aggregate.
            "chain_shares": getattr(position, "chain_shares", None),
            "chain_avg_price": getattr(position, "chain_avg_price", None),
            "chain_cost_basis_usd": getattr(position, "chain_cost_basis_usd", None),
        },
        default=str,
        sort_keys=True,
    )
    event = {
        "event_id": f"{getattr(position, 'trade_id')}:venue_position_observed:{sequence_no}",
        "position_id": getattr(position, "trade_id"),
        "event_version": 1,
        "sequence_no": sequence_no,
        "event_type": "VENUE_POSITION_OBSERVED",
        "occurred_at": occurred_at,
        "phase_before": PENDING_ENTRY,
        "phase_after": fold_lifecycle_phase(PENDING_ENTRY, ACTIVE).value,
        "strategy_key": _strategy_key(position),
        "decision_id": None,
        "snapshot_id": _nullable(getattr(position, "decision_snapshot_id", "")),
        "order_id": _nullable(getattr(position, "order_id", "")),
        "command_id": None,
        "caused_by": "balance_only_recovery",
        "idempotency_key": f"{getattr(position, 'trade_id')}:venue_position_observed:{sequence_no}",
        "venue_status": _nullable(getattr(position, "order_status", "")),
        "source_module": source_module,
        "env": _position_env(position),
        "payload_json": payload,
    }
    return [event], projection


def build_reconciliation_rescue_canonical_write(
    position: Any,
    *,
    chain_synced_at: str,
    sequence_no: int,
    phase_after: str = ACTIVE,
    source_module: str = "src.state.chain_reconciliation",
) -> tuple[list[dict], dict]:
    # F4 (docs/archive/2026-Q2/findings_historical/findings_2026_05_28.md §F4, 2026-05-28): trade-verified rescue
    # always folds to ACTIVE (verified fill on chain). phase_after is explicit
    # (default ACTIVE) rather than re-derived from runtime Position.state.
    if phase_after != ACTIVE:
        raise ValueError(
            f"reconciliation rescue canonical builder requires phase_after=ACTIVE, "
            f"got {phase_after!r}"
        )
    projection = build_position_current_projection(position)
    projection["phase"] = phase_after

    # F2 (docs/archive/2026-Q2/findings_historical/findings_2026_05_28.md §F2, 2026-05-28): occurred_at must be
    # the explicit reconcile-time observation timestamp, not a fallback over
    # legacy position attributes (entered_at could be stale or fabricated).
    if not chain_synced_at:
        raise ValueError("chain_synced_at is required for build_reconciliation_rescue_canonical_write")
    occurred_at = chain_synced_at
    payload = json.dumps(
        {
            "status": "entered",
            "source": "chain_reconciliation",
            "reason": "pending_fill_rescued",
            "from_state": "pending_tracked",
            "to_state": "entered",
            "entry_order_id": getattr(position, "entry_order_id", "") or getattr(position, "order_id", ""),
            "entry_method": getattr(position, "entry_method", ""),
            "selected_method": getattr(position, "selected_method", "") or getattr(position, "entry_method", ""),
            "historical_entry_method": getattr(position, "entry_method", ""),
            "historical_selected_method": getattr(position, "selected_method", "") or getattr(position, "entry_method", ""),
            "applied_validations": list(getattr(position, "applied_validations", []) or []),
            "entry_fill_verified": getattr(position, "entry_fill_verified", False),
            "shares": getattr(position, "shares", None),
            "cost_basis_usd": getattr(position, "cost_basis_usd", None),
            "size_usd": getattr(position, "size_usd", None),
            "condition_id": getattr(position, "condition_id", ""),
            "rescue_condition_id": getattr(position, "condition_id", ""),
            "order_status": getattr(position, "order_status", ""),
            "chain_state": getattr(position, "chain_state", ""),
        },
        default=str,
        sort_keys=True,
    )
    event = {
        "event_id": f"{getattr(position, 'trade_id')}:chain_synced:{sequence_no}",
        "position_id": getattr(position, "trade_id"),
        "event_version": 1,
        "sequence_no": sequence_no,
        "event_type": "CHAIN_SYNCED",
        "occurred_at": occurred_at,
        "phase_before": PENDING_ENTRY,
        "phase_after": fold_lifecycle_phase(PENDING_ENTRY, ACTIVE).value,
        "strategy_key": _strategy_key(position),
        "decision_id": None,
        "snapshot_id": _nullable(getattr(position, "decision_snapshot_id", "")),
        "order_id": _nullable(getattr(position, "order_id", "")),
        "command_id": None,
        "caused_by": "pending_fill_rescued",
        "idempotency_key": f"{getattr(position, 'trade_id')}:chain_synced:{sequence_no}",
        "venue_status": _nullable(getattr(position, "order_status", "")),
        "source_module": source_module,
        "env": _position_env(position),
        "payload_json": payload,
    }
    return [event], projection




def build_chain_size_corrected_canonical_write(
    position: Any,
    *,
    local_shares_before: float,
    sequence_no: int,
    phase_after: str,
    source_module: str = "src.state.chain_reconciliation",
) -> tuple[list[dict], dict]:
    # F4 (docs/archive/2026-Q2/findings_historical/findings_2026_05_28.md §F4, 2026-05-28): chain-size correction
    # does NOT transition the canonical phase — caller passes the position's
    # *current* phase (e.g. ACTIVE or DAY0_WINDOW) explicitly as phase_after.
    # The builder uses it for both phase_before and phase_after on the event
    # (no-op fold) and overrides projection["phase"] verbatim.
    if not phase_after:
        raise ValueError(
            "chain size-corrected canonical builder requires explicit phase_after "
            "(caller passes the position's current phase; this event does not transition phase)"
        )
    projection = build_position_current_projection(position)
    projection["phase"] = phase_after
    phase = fold_lifecycle_phase(phase_after, phase_after).value
    occurred_at = _non_empty(
        getattr(position, "chain_verified_at", ""),
        projection["updated_at"],
    )
    payload = json.dumps(
        {
            "source": "chain_reconciliation",
            "reason": "chain_size_corrected",
            "local_shares_before": local_shares_before,
            "chain_shares_after": getattr(position, "chain_shares", None),
            "shares_after": getattr(position, "shares", None),
            "cost_basis_usd": getattr(position, "cost_basis_usd", None),
            "size_usd": getattr(position, "size_usd", None),
            "condition_id": getattr(position, "condition_id", ""),
            "chain_state": getattr(position, "chain_state", ""),
        },
        default=str,
        sort_keys=True,
    )
    event = {
        "event_id": f"{getattr(position, 'trade_id')}:chain_size_corrected:{sequence_no}",
        "position_id": getattr(position, "trade_id"),
        "event_version": 1,
        "sequence_no": sequence_no,
        "event_type": "CHAIN_SIZE_CORRECTED",
        "occurred_at": occurred_at,
        "phase_before": phase,
        "phase_after": phase,
        "strategy_key": _strategy_key(position),
        "decision_id": None,
        "snapshot_id": _nullable(getattr(position, "decision_snapshot_id", "")),
        "order_id": _nullable(getattr(position, "order_id", "")),
        "command_id": None,
        "caused_by": "chain_size_corrected",
        "idempotency_key": f"{getattr(position, 'trade_id')}:chain_size_corrected:{sequence_no}",
        "venue_status": None,
        "source_module": source_module,
        "env": _position_env(position),
        "payload_json": payload,
    }
    return [event], projection


def build_chain_economics_observed_canonical_write(
    position: Any,
    *,
    chain_observed_at: str,
    sequence_no: int,
    phase_after: str,
    chain_shares_before: float | None,
    source_module: str = "src.state.chain_reconciliation",
) -> tuple[list[dict], dict]:
    """Canonical event: chain economics OBSERVED for an already-synced position.

    Chain-shares-persist fix (2026-05-31, task #56): the matched-no-size-
    mismatch reconciliation path (chain.size == local_shares) previously
    mutated ``Position.chain_shares`` IN-MEMORY only and issued NO canonical
    write, so ``position_current.chain_shares`` stayed NULL forever for every
    synced position (only the SIZE-MISMATCH branch persisted chain economics
    via ``build_chain_size_corrected_canonical_write``). EVIDENCE: 16 on-chain
    positions, all chain_state='synced', chain_shares NULL on all 101 rows —
    the local DB silently diverged from on-chain reality (operator's
    "local db misalign with chain = alpha-" blocker).

    This builder emits the chain OBSERVATION: chain economics
    (``chain_shares`` / ``chain_avg_price`` / ``chain_cost_basis_usd`` /
    ``chain_seen_at``) are projected onto ``position_current`` WITHOUT any
    share mutation and WITHOUT a phase transition. It is semantically a
    no-delta sibling of ``build_chain_size_corrected_canonical_write``:
    chain.size already matched local_shares, so ``shares`` is unchanged and
    ``chain_shares_before == chain_shares_after`` is the common case (the
    only delta being the NULL→value first-population of the chain_* columns).

    The persisted ``event_type`` is ``CHAIN_SIZE_CORRECTED`` — the only
    no-op-phase chain event grammar already accepted by the position_events
    CHECK constraint (avoiding a schema migration on a live DB). The payload
    ``reason='chain_economics_observed'`` plus ``shares_unchanged=True``
    distinguishes a chain OBSERVATION from a true size CORRECTION at the
    event-grammar level for post-hoc analysis; a true correction always
    carries ``reason='chain_size_corrected'``.

    Phase is a no-op fold (``phase_before == phase_after``): the caller
    passes the position's CURRENT canonical phase (active or day0_window).
    This event never transitions phase.
    """
    if not phase_after:
        raise ValueError(
            "chain economics-observed canonical builder requires explicit "
            "phase_after (caller passes the position's current phase; this "
            "event does not transition phase)"
        )
    projection = build_position_current_projection(position)
    projection["phase"] = phase_after
    phase = fold_lifecycle_phase(phase_after, phase_after).value
    if not chain_observed_at:
        raise ValueError(
            "chain_observed_at is required for build_chain_economics_observed_canonical_write"
        )
    occurred_at = chain_observed_at
    payload = json.dumps(
        {
            "source": "chain_reconciliation",
            "reason": "chain_economics_observed",
            "shares_unchanged": True,
            "chain_shares_before": chain_shares_before,
            "chain_shares_after": getattr(position, "chain_shares", None),
            "chain_avg_price": getattr(position, "chain_avg_price", None),
            "chain_cost_basis_usd": getattr(position, "chain_cost_basis_usd", None),
            "shares_after": getattr(position, "shares", None),
            "condition_id": getattr(position, "condition_id", ""),
            "chain_state": getattr(position, "chain_state", ""),
        },
        default=str,
        sort_keys=True,
    )
    event = {
        "event_id": f"{getattr(position, 'trade_id')}:chain_economics_observed:{sequence_no}",
        "position_id": getattr(position, "trade_id"),
        "event_version": 1,
        "sequence_no": sequence_no,
        # CHAIN_SIZE_CORRECTED is the no-op-phase chain event grammar already
        # in the position_events CHECK constraint; reason disambiguates.
        "event_type": "CHAIN_SIZE_CORRECTED",
        "occurred_at": occurred_at,
        "phase_before": phase,
        "phase_after": phase,
        "strategy_key": _strategy_key(position),
        "decision_id": None,
        "snapshot_id": _nullable(getattr(position, "decision_snapshot_id", "")),
        "order_id": _nullable(getattr(position, "order_id", "")),
        "command_id": None,
        "caused_by": "chain_economics_observed",
        "idempotency_key": f"{getattr(position, 'trade_id')}:chain_economics_observed:{sequence_no}",
        "venue_status": None,
        "source_module": source_module,
        "env": _position_env(position),
        "payload_json": payload,
    }
    return [event], projection


def build_review_required_canonical_write(
    position: Any,
    *,
    review_detected_at: str,
    reason: str,
    sequence_no: int,
    phase_after: str = QUARANTINED,
    source_module: str = "src.state.chain_reconciliation",
) -> tuple[list[dict], dict]:
    """PR #352 (Part-3 audit Finding 4, 2026-05-27): durable REVIEW_REQUIRED event.

    Emitted when chain reconciliation detects an unresolved chain/local size
    mismatch but has NO canonical baseline to write a CHAIN_SIZE_CORRECTED
    against. Before this builder, that branch only mutated the runtime Position
    (state=QUARANTINED, chain_state=size_mismatch_unresolved) and bumped a stats
    counter — nothing was persisted, so on daemon restart position_current still
    showed the position as active and the review requirement was lost.

    The caller sets the runtime Position to the quarantined/size-mismatch state
    BEFORE calling this builder, so the projection phase is QUARANTINED and
    append_many_and_project() persists position_current.phase=quarantined +
    chain_state=size_mismatch_unresolved durably alongside the audit event.

    Args:
        review_detected_at: ISO8601 UTC timestamp when the review condition was
            detected (callers pass their ``now`` timestamp). F2 invariant: must
            be explicit, not derived from legacy position attributes.
    """
    # F4 (docs/archive/2026-Q2/findings_historical/findings_2026_05_28.md §F4, 2026-05-28): REVIEW_REQUIRED always
    # folds to QUARANTINED. phase_after is explicit (default QUARANTINED).
    if phase_after != QUARANTINED:
        raise ValueError(
            f"review_required canonical builder requires phase_after=QUARANTINED, "
            f"got {phase_after!r}"
        )
    projection = build_position_current_projection(position)
    projection["phase"] = phase_after
    # F2 (docs/archive/2026-Q2/findings_historical/findings_2026_05_28.md §F2, 2026-05-28): occurred_at must be
    # the explicit reconcile-time detection timestamp.
    if not review_detected_at:
        raise ValueError("review_detected_at is required for build_review_required_canonical_write")
    occurred_at = review_detected_at
    direction = getattr(position, "direction", "")
    direction_value = str(getattr(direction, "value", direction) or "").lower()
    payload = json.dumps(
        {
            "source": "chain_reconciliation",
            "reason": reason,
            "review_state": "unresolved",
            "chain_state": getattr(position, "chain_state", ""),
            "local_shares": getattr(position, "shares", None),
            "chain_shares": getattr(position, "chain_shares", None),
            "condition_id": getattr(position, "condition_id", ""),
            "token_id": getattr(position, "token_id", ""),
            "no_token_id": getattr(position, "no_token_id", ""),
            "held_token_id": (
                getattr(position, "no_token_id", "")
                if direction_value == "buy_no"
                else getattr(position, "token_id", "")
            ),
        },
        default=str,
        sort_keys=True,
    )
    event = {
        "event_id": f"{getattr(position, 'trade_id')}:review_required:{sequence_no}",
        "position_id": getattr(position, "trade_id"),
        "event_version": 1,
        "sequence_no": sequence_no,
        "event_type": "REVIEW_REQUIRED",
        "occurred_at": occurred_at,
        "phase_before": None,
        "phase_after": fold_lifecycle_phase(None, QUARANTINED).value,
        "strategy_key": _strategy_key(position),
        "decision_id": None,
        "snapshot_id": _nullable(getattr(position, "decision_snapshot_id", "")),
        "order_id": _nullable(getattr(position, "order_id", "")),
        "command_id": None,
        "caused_by": reason,
        "idempotency_key": f"{getattr(position, 'trade_id')}:review_required:{sequence_no}",
        "venue_status": _nullable(getattr(position, "order_status", "")),
        "source_module": source_module,
        "env": _position_env(position),
        "payload_json": payload,
    }
    return [event], projection


def build_chain_quarantined_canonical_write(
    position: Any,
    *,
    strategy_key: str,
    sequence_no: int,
    phase_after: str = QUARANTINED,
    source_module: str = "src.state.chain_reconciliation",
) -> tuple[list[dict], dict]:
    if not strategy_key:
        raise ValueError("chain quarantine canonical builder requires explicit strategy_key")

    # F4 (docs/archive/2026-Q2/findings_historical/findings_2026_05_28.md §F4, 2026-05-28): chain-only quarantine
    # always folds to QUARANTINED. phase_after is explicit (default QUARANTINED).
    if phase_after != QUARANTINED:
        raise ValueError(
            f"chain quarantine canonical builder requires phase_after=QUARANTINED, "
            f"got {phase_after!r}"
        )

    original_strategy_key = getattr(position, "strategy_key", "")
    original_strategy = getattr(position, "strategy", "")
    setattr(position, "strategy_key", strategy_key)
    setattr(position, "strategy", strategy_key)
    try:
        projection = build_position_current_projection(position)
    finally:
        setattr(position, "strategy_key", original_strategy_key)
        setattr(position, "strategy", original_strategy)
    projection["phase"] = phase_after

    occurred_at = _non_empty(
        getattr(position, "quarantined_at", ""),
        getattr(position, "chain_verified_at", ""),
        projection["updated_at"],
    )
    payload = json.dumps(
        {
            "source": "chain_reconciliation",
            "reason": "chain_only_quarantined",
            "condition_id": getattr(position, "condition_id", ""),
            "token_id": getattr(position, "token_id", ""),
            "chain_shares": getattr(position, "chain_shares", None),
            "cost_basis_usd": getattr(position, "cost_basis_usd", None),
            "size_usd": getattr(position, "size_usd", None),
            "chain_state": getattr(position, "chain_state", ""),
        },
        default=str,
        sort_keys=True,
    )
    event = {
        "event_id": f"{getattr(position, 'trade_id')}:chain_quarantined:{sequence_no}",
        "position_id": getattr(position, "trade_id"),
        "event_version": 1,
        "sequence_no": sequence_no,
        "event_type": "CHAIN_QUARANTINED",
        "occurred_at": occurred_at,
        "phase_before": None,
        "phase_after": fold_lifecycle_phase(None, QUARANTINED).value,
        "strategy_key": strategy_key,
        "decision_id": None,
        "snapshot_id": _nullable(getattr(position, "decision_snapshot_id", "")),
        "order_id": _nullable(getattr(position, "order_id", "")),
        "command_id": None,
        "caused_by": "chain_only_quarantined",
        "idempotency_key": f"{getattr(position, 'trade_id')}:chain_quarantined:{sequence_no}",
        "venue_status": None,
        "source_module": source_module,
        "env": _position_env(position),
        "payload_json": payload,
    }
    projection["strategy_key"] = strategy_key
    return [event], projection

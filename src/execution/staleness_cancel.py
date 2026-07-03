# Created: 2026-07-03
# Last reused or audited: 2026-07-03
# Authority basis: docs/rebuild/schema_packets/w1_2_order_state_extension_schema_packet_2026-07-02.md
#   (SCH-W1.2-ORDER-STATE) §"C3 mark orders stale" (NO-WRITE; cancel-set goes out through the
#   existing CANCEL intent) + docs/operations/current/plans/order_engine_rebuild_execution_plan_2026-07-02.md
#   W4 row ("C3 staleness path ... same packet: DELETE maker_rest_escalation").
"""C3 staleness classification -> cancel-set -> reconciled re-solve (W4.2).

This module is the TTL/staleness successor to the deleted ``maker_rest_escalation``:
it consumes ``SOURCE_RUN_ARRIVED`` (a q_version-advance signal) and continuous-redecision
ticks, classifies every open ENTRY rest with the W1.2 DERIVED predicates
(``src.state.order_state_predicates``), and cancels the resulting set through the W2.1
batch cancel gateway (``src.execution.batch_order_submission.cancel_commands_batch``),
which already enforces ``cutover_guard.gate_for_intent(CANCEL)`` and the W2.3
cancel-priority rate budget.

NO-WRITE staleness (Option B, schema packet law): nothing here stores a
"stale_pending_cancel" classification. Every call recomputes it fresh from
``venue_commands.q_version`` vs. a live read of the family's current posterior_identity_hash.
A crash or a missed tick loses no state — the next tick reclassifies from the same truth.

TTL ownership handover: ``rest_deadline_exceeded`` (not q-staleness) is what retires a
NULL-q_version or family-blocked ("INDETERMINATE") rest — the same unconditional per-order
age deadline ``maker_rest_escalation`` used to own, now bootstrapped from the same incumbent
constant (``order_state_predicates.bootstrap_rest_deadline_minutes``) so the handover is
behavior-continuous.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable

from src.state.canonical_projections import OPEN_ORDER_FACT_STATES
from src.state.order_state_predicates import (
    bootstrap_rest_deadline_minutes,
    is_stale_pending_cancel,
    rest_deadline_exceeded,
)

logger = logging.getLogger("zeus.staleness_cancel")

UTC = timezone.utc

# Latest-fact states that mean "this order is resting open at the venue" — the single
# canonical open-order-fact set (relocated from maker_rest_escalation.OPEN_REST_FACT_STATES;
# main.py's _edli_open_maker_rests_for_screen imports this constant from here now).
OPEN_REST_FACT_STATES = tuple(sorted(OPEN_ORDER_FACT_STATES))

# The forecast_posteriors product_id this family q_version read targets — matches the
# phase-1 posterior lookup event_reactor_adapter._forecast_authority_payload_from_posterior
# uses (src/engine/event_reactor_adapter.py:11309).
_REPLACEMENT_0_1_PRODUCT_ID = "openmeteo_ecmwf_ifs9_bayes_fusion_v1"

FamilyKey = tuple[str, str, str]


def find_open_entry_rests(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every open ENTRY rest, with its stamped ``q_version``. No deadline filter —
    unlike the retired ``find_expired_resting_entries``, classification (stale vs.
    TTL-expired vs. neither) happens in Python via the derived predicates, not SQL.
    """
    placeholders = ",".join("?" for _ in OPEN_REST_FACT_STATES)
    rows = conn.execute(
        f"""
        WITH latest_facts AS (
            SELECT venue_order_id, state, matched_size,
                   ROW_NUMBER() OVER (
                       PARTITION BY venue_order_id ORDER BY local_sequence DESC
                   ) AS rn
            FROM venue_order_facts
        )
        SELECT vc.command_id, vc.venue_order_id, vc.token_id, vc.market_id,
               vc.created_at, vc.q_version, lf.state AS fact_state, lf.matched_size
        FROM venue_commands vc
        JOIN latest_facts lf
          ON lf.venue_order_id = vc.venue_order_id AND lf.rn = 1
        WHERE vc.intent_kind = 'ENTRY'
          AND vc.venue_order_id IS NOT NULL
          AND vc.venue_order_id != ''
          AND vc.state IN ('ACKED', 'POST_ACKED', 'PARTIAL')
          AND lf.state IN ({placeholders})
        """,
        OPEN_REST_FACT_STATES,
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, sqlite3.Row):
            out.append(dict(row))
        else:
            out.append(
                {
                    "command_id": row[0],
                    "venue_order_id": row[1],
                    "token_id": row[2],
                    "market_id": row[3],
                    "created_at": row[4],
                    "q_version": row[5],
                    "fact_state": row[6],
                    "matched_size": row[7],
                }
            )
    return out


def resolve_order_families(
    entries: list[dict[str, Any]],
    trade_conn: sqlite3.Connection,
    forecasts_conn: sqlite3.Connection,
) -> dict[str, FamilyKey | None]:
    """Per-entry ``command_id -> (city, target_date, metric)`` family resolution.

    Same two-hop join main.py's ``_escalation_families_from_cancelled`` uses (token_id ->
    condition_id via the freshest ``executable_market_snapshots`` row, condition_id ->
    family via ``market_events``), but keyed per-order rather than collapsed to a set —
    C3 classification needs each order's OWN family to read that family's current q_version.
    A row that cannot be resolved maps to ``None`` (INDETERMINATE — governed by
    ``rest_deadline_exceeded`` alone, never q-staleness; fail-closed law).
    """
    token_ids = {str(e.get("token_id") or "") for e in entries if e.get("token_id")}
    cond_by_token: dict[str, str] = {}
    if token_ids:
        placeholders = ",".join("?" for _ in token_ids)
        try:
            for cr in trade_conn.execute(
                f"""
                SELECT selected_outcome_token_id, condition_id
                FROM executable_market_snapshot_latest
                WHERE selected_outcome_token_id IN ({placeholders})
                ORDER BY captured_at DESC
                """,
                tuple(token_ids),
            ).fetchall():
                if cr[0] and cr[1] and str(cr[0]) not in cond_by_token:
                    cond_by_token[str(cr[0])] = str(cr[1])
        except Exception:  # noqa: BLE001 — token->condition resolution is best-effort
            cond_by_token = {}
        if not cond_by_token:
            try:
                for cr in trade_conn.execute(
                    f"""
                    SELECT selected_outcome_token_id, condition_id,
                           ROW_NUMBER() OVER (PARTITION BY selected_outcome_token_id
                                              ORDER BY captured_at DESC) AS rn
                    FROM executable_market_snapshots
                    WHERE selected_outcome_token_id IN ({placeholders})
                    """,
                    tuple(token_ids),
                ).fetchall():
                    if cr[2] == 1 and cr[0] and cr[1]:
                        cond_by_token[str(cr[0])] = str(cr[1])
            except Exception:  # noqa: BLE001 — best-effort
                cond_by_token = {}

    cond_ids = {c for c in cond_by_token.values() if c}
    family_by_condition: dict[str, FamilyKey] = {}
    if cond_ids:
        cph = ",".join("?" for _ in cond_ids)
        try:
            for fr in forecasts_conn.execute(
                f"""
                SELECT DISTINCT condition_id, city, target_date, temperature_metric
                FROM market_events
                WHERE condition_id IN ({cph})
                """,
                tuple(cond_ids),
            ).fetchall():
                condition_id, city, target_date, metric = (
                    str(fr[0] or ""), str(fr[1] or ""), str(fr[2] or ""), str(fr[3] or "")
                )
                if condition_id and city and target_date and metric:
                    family_by_condition[condition_id] = (city, target_date, metric)
        except Exception:  # noqa: BLE001 — condition->family map is best-effort
            family_by_condition = {}

    families_by_command: dict[str, FamilyKey | None] = {}
    for entry in entries:
        command_id = str(entry.get("command_id") or "")
        token_id = str(entry.get("token_id") or "")
        condition_id = cond_by_token.get(token_id)
        families_by_command[command_id] = (
            family_by_condition.get(condition_id) if condition_id else None
        )
    return families_by_command


def read_current_family_q_versions(
    forecasts_conn: sqlite3.Connection,
    families: Iterable[FamilyKey],
) -> dict[FamilyKey, str | None]:
    """Freshest live ``posterior_identity_hash`` per distinct family.

    Lightweight CURRENT-VALUE read for staleness classification — the same
    ``forecast_posteriors`` lookup shape ``event_reactor_adapter._forecast_authority_payload_from_posterior``
    uses (ORDER BY source_cycle_time DESC, computed_at DESC LIMIT 1), without the heavier
    freshness/HWM/member-count gates that lookup applies for decision authority: this read
    only needs "what q is this family being decided against right now," not a licensed
    decision payload. A family with no posterior row yet is BLOCKED/no-servable-q ->
    ``None`` (INDETERMINATE for every one of its orders' q-staleness comparison).
    """
    out: dict[FamilyKey, str | None] = {}
    for family in {f for f in families if f}:
        city, target_date, metric = family
        try:
            row = forecasts_conn.execute(
                """
                SELECT posterior_identity_hash
                  FROM forecast_posteriors
                 WHERE product_id = ?
                   AND city = ? AND target_date = ? AND temperature_metric = ?
                 ORDER BY source_cycle_time DESC, computed_at DESC
                 LIMIT 1
                """,
                (_REPLACEMENT_0_1_PRODUCT_ID, city, target_date, metric),
            ).fetchone()
        except Exception:  # noqa: BLE001 — fail-closed to INDETERMINATE, never raise
            row = None
        out[family] = str(row[0]) if row and row[0] else None
    return out


def classify_cancel_set(
    entries: list[dict[str, Any]],
    families_by_command: dict[str, FamilyKey | None],
    q_by_family: dict[FamilyKey, str | None],
    *,
    now: datetime,
    deadline_minutes: float,
) -> list[dict[str, Any]]:
    """Pure classification: which open entries belong in the cancel-set, and why.

    cancel iff ``is_stale_pending_cancel(...) is True`` OR ``rest_deadline_exceeded(...)``.
    This single OR naturally implements both fail-closed laws without special-casing:
    ``is_stale_pending_cancel`` can only return True when BOTH q's are known and differ,
    so a NULL-stamp order or a family with no servable q (readiness BLOCKED) never
    contributes a q-staleness cancel — it falls through to ``rest_deadline_exceeded``,
    which is the unconditional per-order age backstop (retired maker_rest_escalation's
    entire job) and applies regardless of q_version or family blockage.
    """
    cancel_set: list[dict[str, Any]] = []
    for entry in entries:
        command_id = str(entry.get("command_id") or "")
        family = families_by_command.get(command_id)
        current_q = q_by_family.get(family) if family else None
        stale = is_stale_pending_cancel(
            entry.get("q_version"), current_q, True
        )
        ttl = rest_deadline_exceeded(
            order_open=True,
            resting_since=entry.get("created_at"),
            now=now,
            deadline_minutes=deadline_minutes,
        )
        if not (stale is True or ttl):
            continue
        reasons = []
        if stale is True:
            reasons.append("Q_VERSION_STALE")
        if ttl:
            reasons.append("REST_DEADLINE_EXCEEDED")
        cancel_set.append(
            {
                **entry,
                "family": family,
                "cancel_reason": "+".join(reasons),
                "cancel_action": "CANCEL_REPLACE",
                "cancel_detail": {
                    "trigger": "c3_staleness_cancel",
                    "stale_pending_cancel": stale,
                    "rest_deadline_exceeded": ttl,
                    "deadline_minutes": deadline_minutes,
                    "command_q_version": entry.get("q_version"),
                    "current_family_q_version": current_q,
                },
            }
        )
    return cancel_set


def run_c3_staleness_cancel_cycle(
    trade_conn_ro: sqlite3.Connection,
    trade_conn_rw: sqlite3.Connection,
    forecasts_conn_ro: sqlite3.Connection,
    client: Any,
    *,
    affected_cities: frozenset[str] | None = None,
    now: datetime | None = None,
    rate_budget: Any = None,
    deadline_minutes: float | None = None,
) -> dict[str, Any]:
    """Scan -> resolve families -> read current q -> classify -> cancel -> confirm.

    Cancels go out through ``cancel_commands_batch`` (W2.1) — never a direct
    single-order call — so cutover_guard.gate_for_intent(CANCEL) and the W2.3
    cancel-priority rate budget are exercised on every cancel this path issues.
    A rate-budget denial DEFERS (the outcome is "not_attempted"; the command stays
    open and un-journaled) rather than dropping the intent: the next tick's fresh
    scan reclassifies the same still-open, still-stale order and retries it — there
    is no stored "pending cancel" state to leak or lose.

    Returns ``confirmed_families``: families whose cancel-set command(s) are
    RE-READ (not assumed from the in-memory batch outcome) as CANCELLED via
    ``get_command`` before being included — the "poll the facts you journaled"
    proof the reconciled re-solve gates on.
    """
    from src.execution.batch_order_submission import cancel_commands_batch
    from src.state.venue_command_repo import get_command

    now = now or datetime.now(UTC)
    deadline_minutes = (
        float(deadline_minutes)
        if deadline_minutes is not None
        else bootstrap_rest_deadline_minutes()
    )

    entries = find_open_entry_rests(trade_conn_ro)
    families_by_command = resolve_order_families(entries, trade_conn_ro, forecasts_conn_ro)

    if affected_cities is not None:
        entries = [
            e
            for e in entries
            if (families_by_command.get(str(e.get("command_id") or "")) or (None,))[0]
            in affected_cities
        ]

    q_by_family = read_current_family_q_versions(
        forecasts_conn_ro,
        (f for f in families_by_command.values() if f),
    )

    cancel_set = classify_cancel_set(
        entries, families_by_command, q_by_family, now=now, deadline_minutes=deadline_minutes
    )

    result: dict[str, Any] = {
        "scanned": len(entries),
        "cancel_set_size": len(cancel_set),
        "outcomes": [],
        "confirmed_families": set(),
    }
    if not cancel_set:
        return result

    outcomes = cancel_commands_batch(
        trade_conn_rw,
        client,
        [str(e["command_id"]) for e in cancel_set],
        rate_budget=rate_budget,
    )
    result["outcomes"] = outcomes

    confirmed_families: set[FamilyKey] = set()
    for outcome in outcomes:
        if outcome.status != "acked":
            continue
        # Poll the journaled fact rather than trust the in-memory outcome: re-read
        # the command fresh so a redecision is gated on DURABLE cancel-confirmed
        # truth, not on what this call thinks it just wrote.
        command = get_command(trade_conn_rw, outcome.command_id)
        if command is None or str(command.get("state") or "").upper() != "CANCELLED":
            continue
        family = families_by_command.get(outcome.command_id)
        if family:
            confirmed_families.add(family)
    result["confirmed_families"] = confirmed_families
    logger.info(
        "c3_staleness_cancel: scanned=%d cancel_set=%d acked=%d confirmed_families=%d",
        result["scanned"],
        result["cancel_set_size"],
        sum(1 for o in outcomes if o.status == "acked"),
        len(confirmed_families),
    )
    return result

# Created: 2026-07-03
# Last reused or audited: 2026-07-04
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

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable

from src.data import replacement_input_hwm as _replacement_input_hwm
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
_Q_AUTHORITY_BLOCKED_PREFIX = "__Q_AUTHORITY_BLOCKED__:"

FamilyKey = tuple[str, str, str]


def _venue_commands_q_version_select_expr(conn: sqlite3.Connection) -> str:
    try:
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(venue_commands)")}
    except sqlite3.DatabaseError:
        columns = set()
    return "vc.q_version" if "q_version" in columns else "NULL"


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    try:
        return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table_name})")}
    except sqlite3.DatabaseError:
        return set()


def _has_table(conn: sqlite3.Connection, table_name: str) -> bool:
    try:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone() is not None
    except sqlite3.DatabaseError:
        return False


def _decision_source_details_from_submit_payload(payload_json: object) -> dict[str, object] | None:
    try:
        payload = json.loads(str(payload_json or "{}"))
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    capability = payload.get("execution_capability")
    if not isinstance(capability, dict):
        return None
    components = capability.get("components")
    if not isinstance(components, list):
        return None
    details: dict[str, object] | None = None
    for component in components:
        if not isinstance(component, dict):
            continue
        if component.get("component") != "decision_source_integrity":
            continue
        raw_details = component.get("details")
        if isinstance(raw_details, dict):
            details = raw_details
        break
    if not isinstance(details, dict):
        return None
    return details


def _decision_q_authority_from_details(details: dict[str, object] | None) -> str | None:
    if not isinstance(details, dict):
        return None
    authority_tier = str(details.get("authority_tier") or "").strip().upper()
    source_role = str(details.get("forecast_source_role") or "").strip()
    if authority_tier == "FORECAST" and source_role == "entry_primary":
        return "forecast_entry_primary"
    if authority_tier in {"OBSERVATION", "DAY0_OBSERVATION"} or source_role in {
        "day0_live_observation",
        "day0_observed_probability",
    }:
        return "day0_observation"
    return None


def _decision_q_version_from_details(details: dict[str, object] | None) -> str | None:
    if not isinstance(details, dict):
        return None
    if str(details.get("authority_tier") or "") != "FORECAST":
        return None
    if str(details.get("forecast_source_role") or "") != "entry_primary":
        return None
    if str(details.get("source_id") or "") != "openmeteo_ecmwf_ifs9_bayes_fusion":
        return None
    for key in ("posterior_identity_hash", "raw_payload_hash"):
        value = str(details.get(key) or "").strip()
        if len(value) == 64 and all(ch in "0123456789abcdefABCDEF" for ch in value):
            return value
    return None


def find_open_entry_rests(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every open ENTRY rest, with its stamped ``q_version``. No deadline filter —
    unlike the retired ``find_expired_resting_entries``, classification (stale vs.
    TTL-expired vs. neither) happens in Python via the derived predicates, not SQL.
    """
    placeholders = ",".join("?" for _ in OPEN_REST_FACT_STATES)
    q_version_expr = _venue_commands_q_version_select_expr(conn)
    venue_command_columns = _table_columns(conn, "venue_commands")
    snapshot_id_select = (
        "vc.snapshot_id AS snapshot_id" if "snapshot_id" in venue_command_columns else "NULL AS snapshot_id"
    )
    snapshot_join = ""
    snapshot_min_order_select = "NULL AS min_order_size"
    if "snapshot_id" in venue_command_columns and _has_table(conn, "executable_market_snapshots"):
        snapshot_join = """
        LEFT JOIN executable_market_snapshots snap
          ON snap.snapshot_id = vc.snapshot_id
        """
        snapshot_min_order_select = "snap.min_order_size AS min_order_size"
    submit_payload_join = ""
    submit_payload_select = "NULL AS submit_payload_json"
    if _has_table(conn, "venue_command_events"):
        submit_payload_join = """
        LEFT JOIN (
            SELECT command_id, payload_json
            FROM (
                SELECT command_id, payload_json,
                       ROW_NUMBER() OVER (
                           PARTITION BY command_id ORDER BY sequence_no DESC
                       ) AS rn
                FROM venue_command_events
                WHERE event_type = 'SUBMIT_REQUESTED'
            )
            WHERE rn = 1
        ) submit_payload
          ON submit_payload.command_id = vc.command_id
        """
        submit_payload_select = "submit_payload.payload_json AS submit_payload_json"
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
               vc.created_at, {q_version_expr} AS q_version, lf.state AS fact_state, lf.matched_size,
               {snapshot_id_select}, {snapshot_min_order_select}, {submit_payload_select}
        FROM venue_commands vc
        JOIN latest_facts lf
          ON lf.venue_order_id = vc.venue_order_id AND lf.rn = 1
        {snapshot_join}
        {submit_payload_join}
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
            item = dict(row)
        else:
            item = {
                "command_id": row[0],
                "venue_order_id": row[1],
                "token_id": row[2],
                "market_id": row[3],
                "created_at": row[4],
                "q_version": row[5],
                "fact_state": row[6],
                "matched_size": row[7],
                "snapshot_id": row[8],
                "min_order_size": row[9],
                "submit_payload_json": row[10],
            }
        source_details = _decision_source_details_from_submit_payload(item.get("submit_payload_json"))
        q_authority = _decision_q_authority_from_details(source_details)
        if q_authority:
            item["q_version_authority"] = q_authority
        if not item.get("q_version"):
            recovered = _decision_q_version_from_details(source_details)
            if recovered:
                item["q_version"] = recovered
                item["q_version_source"] = "submit_requested_decision_source"
                item["q_version_authority"] = "forecast_entry_primary"
        item.pop("submit_payload_json", None)
        out.append(item)
    return out


def _has_sub_min_partial_fill(entry: dict[str, Any]) -> bool:
    try:
        matched_size = float(entry.get("matched_size") or 0.0)
        min_order_size = float(entry.get("min_order_size") or 0.0)
    except (TypeError, ValueError):
        return False
    return (
        matched_size > 0.0
        and min_order_size > 0.0
        and matched_size < min_order_size
    )


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
    *,
    now: datetime | None = None,
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
    decision_time = (now or datetime.now(UTC)).astimezone(UTC)
    for family in {f for f in families if f}:
        city, target_date, metric = family
        try:
            row = forecasts_conn.execute(
                """
                SELECT posterior_identity_hash, source_cycle_time, computed_at
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
        if not row or not row[0]:
            out[family] = None
            continue
        q_version = str(row[0])
        try:
            lag_reason = _replacement_input_hwm.replacement_live_input_lag_reason(
                forecasts_conn,
                city=city,
                target_date=target_date,
                metric=metric,
                decision_time=decision_time,
                posterior_source_cycle_time=row[1],
                posterior_computed_at=row[2],
            )
        except Exception:  # noqa: BLE001 — classification must stay conservative
            lag_reason = None
        if lag_reason:
            out[family] = f"{_Q_AUTHORITY_BLOCKED_PREFIX}{q_version}:{lag_reason}"
        else:
            out[family] = q_version
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
        stale = None
        if entry.get("q_version_authority") != "day0_observation":
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
        if _has_sub_min_partial_fill(entry):
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
    """Two independent classification passes over one scan, merged before cancel.

    TTL pass (UNCONDITIONAL, every call): scans every open ENTRY rest with NO
    city filter and classifies with ``q_by_family={}`` — forcing
    ``is_stale_pending_cancel`` to INDETERMINATE for every entry, so only
    ``rest_deadline_exceeded`` can select a command here. This is the exact
    unconditional-per-order-age backstop the retired ``maker_rest_escalation``
    job owned; it must never be gated on ``affected_cities`` or on whether the
    caller has any claimed events at all, or expired rests strand during quiet
    periods (the orphaned-GTC composition bug this split fixes).

    q-version staleness pass (scoped, only when ``affected_cities``): restricts
    to entries whose resolved family's city is in ``affected_cities`` and reads
    live q only for those families — a source-run event is what makes staleness
    classification meaningful; families outside it never had their q move.

    The two cancel-sets are merged de-duplicated by command_id (TTL wins on
    conflict, since it ran unconditionally) before the single
    ``cancel_commands_batch`` call — so a command that is both past-deadline and
    q-stale in the same tick is cancelled once, not twice.

    Cancels go out through ``cancel_commands_batch`` (W2.1) — never a direct
    single-order call — so cutover_guard.gate_for_intent(CANCEL) and the W2.3
    cancel-priority rate budget are exercised on every cancel this path issues.
    A rate-budget denial DEFERS (the outcome is "not_attempted"; the command stays
    open and un-journaled) rather than dropping the intent: the next tick's fresh
    scan reclassifies the same still-open, still-stale order and retries it — there
    is no stored "pending cancel" state to leak or lose. A command already
    CANCELLED or CANCEL_PENDING from a prior/duplicate tick is likewise safe to
    re-submit here: cancel_commands_batch skips non-requestable terminal states
    and treats CANCEL_PENDING as eligible without re-appending CANCEL_REQUESTED,
    so a replayed SOURCE_RUN_ARRIVED can drive this cycle again without a
    duplicate venue side effect.

    Returns ``confirmed_families``: families whose cancel-set command(s) are
    RE-READ (not assumed from the in-memory batch outcome) as CANCELLED via
    ``get_command`` before being included — the "poll the facts you journaled"
    proof the reconciled re-solve gates on. FAMILY-LEVEL, conservative: a
    family is confirmed only if EVERY one of its commands in this cycle's
    outcomes durably cancelled — a family with even one non-acked/non-CANCELLED
    outcome (REVIEW_REQUIRED, a rate-budget defer, an unmapped response, ...)
    is entirely excluded, never partially confirmed, because that family still
    carries a recovery-owned ambiguous venue exposure a redecision must not
    submit against.
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

    ttl_cancel_set = classify_cancel_set(
        entries, families_by_command, {}, now=now, deadline_minutes=deadline_minutes
    )

    q_cancel_set: list[dict[str, Any]] = []
    if affected_cities:
        scoped_entries = [
            e
            for e in entries
            if (families_by_command.get(str(e.get("command_id") or "")) or (None,))[0]
            in affected_cities
        ]
        q_by_family = read_current_family_q_versions(
            forecasts_conn_ro,
            (
                f
                for f in families_by_command.values()
                if f and f[0] in affected_cities
            ),
            now=now,
        )
        q_cancel_set = classify_cancel_set(
            scoped_entries, families_by_command, q_by_family, now=now, deadline_minutes=deadline_minutes
        )

    cancel_by_command: dict[str, dict[str, Any]] = {
        str(e["command_id"]): e for e in ttl_cancel_set
    }
    for e in q_cancel_set:
        cancel_by_command.setdefault(str(e["command_id"]), e)
    cancel_set = list(cancel_by_command.values())

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

    # Family-level gating (conservative suppression): a family is confirmed
    # ONLY if EVERY one of its commands in this cycle's outcomes durably
    # cancelled. A family with even one non-acked/non-CANCELLED outcome
    # (REVIEW_REQUIRED from an SDK exception, a rate-budget defer, an
    # unmapped/unknown response, ...) is a family with a recovery-owned,
    # ambiguous venue exposure still open -- emitting a redecision for it
    # anyway risks a duplicate/overlapping submit against that ambiguity.
    # blocked_families always wins over confirmed_families, regardless of
    # dict/set iteration order over outcomes.
    confirmed_families: set[FamilyKey] = set()
    blocked_families: set[FamilyKey] = set()
    for outcome in outcomes:
        family = families_by_command.get(outcome.command_id)
        if outcome.status != "acked":
            if family:
                blocked_families.add(family)
            continue
        # Poll the journaled fact rather than trust the in-memory outcome: re-read
        # the command fresh so a redecision is gated on DURABLE cancel-confirmed
        # truth, not on what this call thinks it just wrote.
        command = get_command(trade_conn_rw, outcome.command_id)
        if command is None or str(command.get("state") or "").upper() != "CANCELLED":
            if family:
                blocked_families.add(family)
            continue
        if family:
            confirmed_families.add(family)
    confirmed_families -= blocked_families
    result["confirmed_families"] = confirmed_families
    logger.info(
        "c3_staleness_cancel: scanned=%d cancel_set=%d acked=%d confirmed_families=%d",
        result["scanned"],
        result["cancel_set_size"],
        sum(1 for o in outcomes if o.status == "acked"),
        len(confirmed_families),
    )
    return result

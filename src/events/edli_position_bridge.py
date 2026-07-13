# Created: 2026-06-01
# Last reused or audited: 2026-07-09
# Authority basis: DEFECT-1 capital-recoverability bridge — EDLI fill → canonical
#   position_current. Audit: an EDLI FILL_CONFIRMED writes only
#   edli_live_order_events + edli_live_profit_audit and NEVER a position_current
#   row, so chain-reconciliation / exit-lifecycle / harvester / redeem (all of
#   which read position_current exclusively) cannot see the position → stuck
#   capital. This module is the missing seam.
"""Bridge an EDLI confirmed fill into the canonical position lifecycle.

The EDLI event-sourced execution lane (``edli_live_order_events`` /
``edli_live_profit_audit``, world.db) and the legacy ``position_current`` /
``position_events`` lifecycle (trade.db) are two disconnected worlds. Every
downstream lifecycle subsystem — ``src.state.chain_reconciliation`` (chain
truth), ``src.execution.exit_lifecycle`` (exit), the harvester (PnL), and
redeem — reads ``position_current`` only. Without this bridge an EDLI fill is
invisible to all of them.

When an EDLI order fill is CONFIRMED (a ``UserTradeObserved`` carrying
``fill_authority_state == "FILL_CONFIRMED"``), this module materialises — or
idempotently updates — a single canonical ``position_current`` row for the
filled token, using the SAME canonical write path the legacy fill path uses
(``src.state.ledger.append_many_and_project`` via
``build_entry_canonical_write``). The row carries the exact field semantics
``record_entry`` produces so chain-reconciliation matches it by token and
populates ``chain_shares`` (proven for the legacy Shanghai position).

INV-37: the caller MUST pass a connection on which ``position_current`` /
``position_events`` are writable AND ``edli_live_order_events`` is readable —
i.e. a trade connection with world ATTACHed (``get_trade_connection_with_world_*``)
in production. The bridge performs NO independent connection; every read and
write happens on the single connection passed in, and the canonical write path
nests its own SAVEPOINT (ATTACH + SAVEPOINT, never an independent connection).

Idempotency: the deterministic ``position_id`` is derived from the EDLI
``aggregate_id`` (``"edli" + sha256_hex``, 68 chars).  A re-projected fill
UPDATEs the same ``position_current`` row (``ON CONFLICT(position_id) DO
UPDATE``) and skips re-inserting the entry events (``position_events`` is
append-only, keyed ``UNIQUE(position_id, sequence_no)`` and ``event_id
PRIMARY KEY``), so a replay never duplicates.

FOK semantics produce a single full fill today, but the economics aggregation
sums across every ``UserTradeObserved`` (size-weighted avg price, summed fees)
so multiple partial fills for one aggregate are handled correctly
(forward-proofs DEFECT-4).
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any, Mapping

from src.contracts.semantic_types import EntryMethod

logger = logging.getLogger(__name__)


# Fill authority — imported from the canonical single source of truth
# (src.state.portfolio) rather than re-literal'd, so the bridged row is treated
# as a fill-grade exposure by has_tradable_exposure / has_verified_trade_fill /
# chain reconciliation. Re-literalling here would let the value drift out of
# FILL_GRADE_FILL_AUTHORITIES and silently make the bridged position
# unmanageable by the exit lane (capital stuck — the exact failure we cure).
from src.state.portfolio import FILL_AUTHORITY_VENUE_CONFIRMED_FULL

# The EDLI lifecycle marker that means "this trade is irrevocably filled".
EDLI_FILL_CONFIRMED_STATE = "FILL_CONFIRMED"

class EdliPositionBridgeError(RuntimeError):
    """Raised when a confirmed EDLI fill cannot be projected to a position."""


def edli_bridge_position_id(aggregate_id: str) -> str:
    """Deterministic canonical position_id for an EDLI aggregate.

    Keyed off the aggregate_id so replay/dedup maps to the SAME
    ``position_current`` row (idempotency floor).

    Width: full SHA-256 hex digest (64 chars) prefixed with "edli" = 68 chars
    total, giving 256 bits of collision resistance.  The former 11-char
    truncation yielded only 28 effective bits (4-char literal "edli" + 7 hex
    chars), making silent position_current merge via ON CONFLICT(position_id)
    DO UPDATE probable at ~10 k fills (birthday bound ≈ 19 % at 10 k).
    FIX #96.

    **Idempotency / legacy-row note**: callers that test for the existence of
    a ``position_current`` row MUST also probe with ``edli_bridge_position_id_legacy``
    to handle the 101 rows written before this widening.  See
    ``_edli_durable_fill_bridge_scan`` in src/main.py for the dual-probe
    pattern.  New fills written after this commit use the wide 68-char ID.
    """
    digest = hashlib.sha256(str(aggregate_id).encode("utf-8")).hexdigest()
    return "edli" + digest


def edli_bridge_position_id_legacy(aggregate_id: str) -> str:
    """Return the OLD 11-char position_id for ``aggregate_id`` (pre-FIX-#96).

    Used ONLY to detect whether a ``position_current`` row was written before
    the widening, so that ``_edli_durable_fill_bridge_scan`` does not
    re-bridge an already-bridged aggregate that has a legacy short ID.

    Do NOT use this to write new rows.  Call ``edli_bridge_position_id``
    (the 68-char form) for all new writes.
    """
    digest = hashlib.sha256(str(aggregate_id).encode("utf-8")).hexdigest()
    return ("edli" + digest)[:11]


def _edli_events_table(conn: sqlite3.Connection) -> str:
    """Resolve the schema-qualified name of the EDLI events table.

    Production historically intended the world copy to be authoritative, but
    live cutovers have also written current aggregates to trade-main while
    leaving a stale world ghost. Pick the freshest available event table so
    confirmed fills are bridged back into the same active aggregate stream.
    """
    candidates: list[str] = []
    if _table_exists(conn, "edli_live_order_events"):
        candidates.append("edli_live_order_events")
    try:
        attached = {row[1] for row in conn.execute("PRAGMA database_list").fetchall()}
    except sqlite3.Error:
        attached = set()
    if "world" in attached and _table_exists(conn, "edli_live_order_events", schema="world"):
        candidates.append("world.edli_live_order_events")
    if not candidates:
        return "edli_live_order_events"
    if len(candidates) == 1:
        return candidates[0]

    def _latest_occurred_at(table: str) -> str:
        try:
            row = conn.execute(
                f"SELECT MAX(occurred_at) AS max_occurred_at FROM {table}"
            ).fetchone()
        except sqlite3.Error:
            return ""
        if row is None:
            return ""
        try:
            value = row["max_occurred_at"] if isinstance(row, sqlite3.Row) else row[0]
        except (IndexError, KeyError):
            return ""
        return str(value or "")

    return max(candidates, key=lambda table: (_latest_occurred_at(table), table))


def _table_exists(conn: sqlite3.Connection, table: str, *, schema: str = "main") -> bool:
    try:
        if schema == "main":
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
        else:
            row = conn.execute(
                f"SELECT 1 FROM {schema}.sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
    except sqlite3.Error:
        return False
    return row is not None


def _resolved_table(conn: sqlite3.Connection, table_name: str) -> str:
    """Prefer the ATTACHed world table when present, else the local table."""
    try:
        attached = {row[1] for row in conn.execute("PRAGMA database_list").fetchall()}
    except sqlite3.Error:
        attached = set()
    if "world" in attached:
        row = conn.execute(
            "SELECT 1 FROM world.sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
        if row is not None:
            return f"world.{table_name}"
    return table_name


def _aggregate_event_rows(conn: sqlite3.Connection, aggregate_id: str) -> list[tuple[str, dict[str, Any]]]:
    table = _edli_events_table(conn)
    rows = conn.execute(
        f"""
        SELECT event_type, payload_json
        FROM {table}
        WHERE aggregate_id = ?
        ORDER BY event_sequence ASC
        """,
        (aggregate_id,),
    ).fetchall()
    out: list[tuple[str, dict[str, Any]]] = []
    for row in rows:
        event_type = str(row[0])
        try:
            payload = json.loads(str(row[1]))
        except (TypeError, json.JSONDecodeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        out.append((event_type, payload))
    return out


def _certificate_by_hash(conn: sqlite3.Connection, certificate_hash: str) -> dict[str, Any] | None:
    if not certificate_hash:
        return None
    table = _resolved_table(conn, "decision_certificates")
    try:
        row = conn.execute(
            f"""
            SELECT certificate_id, certificate_type, payload_json, certificate_hash
            FROM {table}
            WHERE certificate_hash = ?
            LIMIT 1
            """,
            (certificate_hash,),
        ).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    try:
        payload = json.loads(str(row["payload_json"] if isinstance(row, sqlite3.Row) else row[2]))
    except (TypeError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return {
        "certificate_id": str(row["certificate_id"] if isinstance(row, sqlite3.Row) else row[0]),
        "certificate_type": str(row["certificate_type"] if isinstance(row, sqlite3.Row) else row[1]),
        "payload": payload,
        "certificate_hash": str(row["certificate_hash"] if isinstance(row, sqlite3.Row) else row[3]),
    }


def _parent_certificate_hashes(conn: sqlite3.Connection, child_certificate_id: str) -> dict[str, str]:
    if not child_certificate_id:
        return {}
    table = _resolved_table(conn, "decision_certificate_edges")
    try:
        rows = conn.execute(
            f"""
            SELECT parent_role, parent_certificate_hash
            FROM {table}
            WHERE child_certificate_id = ?
            """,
            (child_certificate_id,),
        ).fetchall()
    except sqlite3.Error:
        return {}
    return {
        str(row["parent_role"] if isinstance(row, sqlite3.Row) else row[0]): str(
            row["parent_certificate_hash"] if isinstance(row, sqlite3.Row) else row[1]
        )
        for row in rows
    }


def _entry_authority_from_certificates(
    conn: sqlite3.Connection,
    *,
    actionable_certificate_hash: str,
) -> tuple[Any | None, float | None, float, str | None]:
    """Recover entry DecisionEvidence and belief CI from persisted certificates.

    This never fabricates authority: if the Actionable/Belief/FDR certificate
    chain is incomplete or malformed, the bridge returns no evidence and leaves
    the D4 exit gate fail-closed.
    """
    if not actionable_certificate_hash:
        return None, None, 0.0, None
    actionable = _certificate_by_hash(conn, actionable_certificate_hash)
    if actionable is None:
        return None, None, 0.0, None
    actionable_payload = actionable["payload"]
    entry_method: str | None = None
    q_live = _float_or_none(actionable_payload.get("q_live"))
    q_lcb = _float_or_none(actionable_payload.get("q_lcb_5pct"))
    qkernel_payload = actionable_payload.get("qkernel_execution_economics")
    if isinstance(qkernel_payload, dict):
        if _qkernel_payload_has_live_entry_authority(
            qkernel_payload,
            event_type=actionable_payload.get("event_type"),
            probability_payload=actionable_payload,
            source="certificate",
        ):
            qkernel_point = _float_or_none(qkernel_payload.get("payoff_q_point"))
            qkernel_lcb = _float_or_none(qkernel_payload.get("payoff_q_lcb"))
            if (
                qkernel_point is not None
                and qkernel_lcb is not None
                and 0.0 <= qkernel_lcb <= qkernel_point <= 1.0
            ):
                q_live = qkernel_point
                q_lcb = qkernel_lcb
                entry_method = EntryMethod.QKERNEL_SPINE.value
        elif _is_day0_or_retired_day0_qkernel(actionable_payload, qkernel_payload):
            q_live = None
            q_lcb = None
    ci_width = 0.0
    if q_live is not None and q_lcb is not None and q_live > q_lcb:
        ci_width = min(1.0, max(0.0, 2.0 * (q_live - q_lcb)))

    parents = _parent_certificate_hashes(conn, actionable["certificate_id"])
    belief = _certificate_by_hash(conn, parents.get("belief", ""))
    fdr = _certificate_by_hash(conn, parents.get("fdr", ""))
    if belief is None or fdr is None:
        return None, q_live, ci_width, entry_method
    belief_payload = belief["payload"]
    fdr_payload = fdr["payload"]
    bootstrap_n = _float_or_none(
        belief_payload.get("bootstrap_n") or fdr_payload.get("edge_bootstrap_n")
    )
    if bootstrap_n is None or bootstrap_n < 1:
        return None, q_live, ci_width, entry_method
    if fdr_payload.get("passed") is not True:
        return None, q_live, ci_width, entry_method

    from src.contracts.decision_evidence import DecisionEvidence
    from src.strategy.selection_family import DEFAULT_FDR_ALPHA

    return (
        DecisionEvidence(
            evidence_type="entry",
            statistical_method="bootstrap_ci_bh_fdr",
            sample_size=int(bootstrap_n),
            confidence_level=DEFAULT_FDR_ALPHA,
            fdr_corrected=True,
            consecutive_confirmations=1,
        ),
        q_live,
        ci_width,
        entry_method,
    )


def _entry_authority_from_decision_audit(
    events: list[tuple[str, dict[str, Any]]],
) -> tuple[float | None, float, str | None]:
    """Recover qkernel entry belief from the accepted EDLI decision audit.

    The durable fill bridge normally reads the immutable ActionableTradeCertificate
    by hash. During boot recovery, however, the caller may have the EDLI aggregate
    event stream without a readable certificate table on the same connection. The
    accepted decision audit is still part of that aggregate and carries the same
    qkernel economics stamped at submit time, so use it as a local projection
    fallback instead of writing a fake zero posterior.
    """

    payload = _latest_payload(events, "DecisionProofAccepted") or {}
    audit = payload.get("decision_audit")
    if not isinstance(audit, dict):
        audit = payload
    q_live = _float_or_none(audit.get("q_live"))
    q_lcb = _float_or_none(audit.get("q_lcb_5pct"))
    entry_method: str | None = None
    qkernel_payload = _selected_qkernel_execution_economics(audit)
    if qkernel_payload:
        if _qkernel_payload_has_live_entry_authority(
            qkernel_payload,
            event_type=audit.get("event_type"),
            probability_payload=audit,
            source="decision_audit",
        ):
            qkernel_point = _float_or_none(qkernel_payload.get("payoff_q_point"))
            qkernel_lcb = _float_or_none(qkernel_payload.get("payoff_q_lcb"))
            if (
                qkernel_point is not None
                and qkernel_lcb is not None
                and 0.0 <= qkernel_lcb <= qkernel_point <= 1.0
            ):
                q_live = qkernel_point
                q_lcb = qkernel_lcb
                entry_method = EntryMethod.QKERNEL_SPINE.value
        elif _is_day0_or_retired_day0_qkernel(audit, qkernel_payload):
            q_live = None
            q_lcb = None
    ci_width = 0.0
    if q_live is not None and q_lcb is not None and q_live > q_lcb:
        ci_width = min(1.0, max(0.0, 2.0 * (q_live - q_lcb)))
    return q_live, ci_width, entry_method


def _selected_qkernel_execution_economics(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract selected qkernel economics from current live receipt shapes."""

    direct = payload.get("qkernel_execution_economics")
    if isinstance(direct, dict) and direct:
        return direct
    selected = payload.get("selected_qkernel_execution_economics")
    if isinstance(selected, dict) and selected:
        return selected
    opportunity_book = payload.get("opportunity_book")
    if isinstance(opportunity_book, dict):
        cache_summary = opportunity_book.get("cache_summary")
        if isinstance(cache_summary, dict):
            selected = cache_summary.get("selected_qkernel_execution_economics")
            if isinstance(selected, dict) and selected:
                return selected
    return {}


def _is_day0_or_retired_day0_qkernel(payload: dict[str, Any], economics: dict[str, Any]) -> bool:
    if str(payload.get("event_type") or "").strip() == "DAY0_EXTREME_UPDATED":
        return True
    return any(
        str(economics.get(field_name) or "").strip() == "DAY0_OBSERVED_BOUNDARY"
        for field_name in ("q_lcb_guard_basis", "selection_guard_basis")
    )


def _qkernel_payload_has_live_entry_authority(
    economics: dict[str, Any],
    *,
    event_type: Any,
    probability_payload: Mapping[str, Any],
    source: str,
) -> bool:
    if any(
        str(economics.get(field_name) or "").strip() == "DAY0_OBSERVED_BOUNDARY"
        for field_name in ("q_lcb_guard_basis", "selection_guard_basis")
    ):
        logger.warning(
            "edli position bridge: %s qkernel payload uses retired Day0 observed-boundary guard",
            source,
        )
        return False
    if str(event_type or "").strip() != "DAY0_EXTREME_UPDATED":
        return True
    try:
        from src.events.day0_authority import (
            Day0AuthorityError,
            assert_live_day0_probability_authority,
            assert_live_day0_qkernel_guard_authority,
        )

        assert_live_day0_probability_authority(
            probability_payload,
            direction=probability_payload.get("direction")
            or probability_payload.get("actual_direction"),
            condition_id=probability_payload.get("condition_id")
            or probability_payload.get("actual_condition_id"),
            q_live=economics.get("payoff_q_point"),
            q_lcb=economics.get("payoff_q_lcb"),
        )
        assert_live_day0_qkernel_guard_authority(
            economics,
            probability_payload=probability_payload,
        )
    except Day0AuthorityError as exc:
        logger.warning(
            "edli position bridge: %s Day0 qkernel payload is not live entry authority: %s",
            source,
            exc,
        )
        return False
    return True


def _pre_submit_posterior(pre_submit: dict[str, Any]) -> float:
    if str(pre_submit.get("event_type") or "").strip() != "DAY0_EXTREME_UPDATED":
        return _float_or_none(pre_submit.get("q_live")) or 0.0
    economics = _selected_qkernel_execution_economics(pre_submit)
    if not economics:
        return 0.0
    if not _qkernel_payload_has_live_entry_authority(
        economics,
        event_type=pre_submit.get("event_type"),
        probability_payload=pre_submit,
        source="pre_submit",
    ):
        return 0.0
    return (
        _float_or_none(economics.get("payoff_q_point"))
        or _float_or_none(pre_submit.get("q_live"))
        or 0.0
    )


def _latest_payload(events: list[tuple[str, dict[str, Any]]], event_type: str) -> dict[str, Any] | None:
    for current_type, payload in reversed(events):
        if current_type == event_type:
            return payload
    return None


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed:  # NaN
        return None
    return parsed


def _confirmed_fill_payloads(events: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    """One UserTradeObserved payload per DISTINCT venue fill (deduped by trade_id).

    MF-2 phantom over-materialization fix. A SINGLE venue fill is re-reported by
    the user channel as several ``UserTradeObserved`` legs sharing one
    ``trade_id`` as it advances MATCHED -> MINED -> CONFIRMED (the production
    shape proven at tests/test_user_channel_ingest.py:920-926 — three rows, each
    ``filled_size=100``, one fill). Summing economics across every leg would
    triple-count that one fill (300 shares for a real 100-share / $40 fill).

    So we collapse re-reports of one fill to exactly ONE payload per DISTINCT
    ``trade_id``, preferring the CONFIRMED leg's economics (fall back to the
    latest-status leg if no CONFIRMED leg exists yet). Legs WITHOUT a
    ``trade_id`` cannot be deduped — they are kept individually so genuine
    multi-partial fills that lack a per-fill id still sum (DEFECT-4 forward-proof).
    The downstream ``_aggregate_fill_economics`` then sums only across these
    DISTINCT fills: two distinct ``trade_id``s still sum correctly.

    Order is preserved (first-seen position per ``trade_id``) so the
    size-weighted VWAP is deterministic.
    """
    fills = [payload for event_type, payload in events if event_type == "UserTradeObserved"]

    deduped: list[dict[str, Any]] = []
    # trade_id -> index into `deduped` of the leg currently chosen for that id.
    chosen_index: dict[str, int] = {}
    for payload in fills:
        trade_id = str(payload.get("trade_id") or "").strip()
        if not trade_id:
            # No disambiguating id: cannot be a re-report we can collapse. Keep
            # it as its own fill so id-less genuine partials continue to sum.
            deduped.append(payload)
            continue
        is_confirmed = str(payload.get("fill_authority_state") or "") == EDLI_FILL_CONFIRMED_STATE
        if trade_id not in chosen_index:
            chosen_index[trade_id] = len(deduped)
            deduped.append(payload)
            continue
        # Already saw this fill. Upgrade to the CONFIRMED leg's economics, or to
        # the latest-status leg when none is confirmed yet. Replacing the prior
        # entry (rather than appending) is the latest-wins fallback.
        prior = deduped[chosen_index[trade_id]]
        prior_confirmed = str(prior.get("fill_authority_state") or "") == EDLI_FILL_CONFIRMED_STATE
        if is_confirmed or not prior_confirmed:
            deduped[chosen_index[trade_id]] = payload
    return deduped


def _has_confirmed_fill(events: list[tuple[str, dict[str, Any]]]) -> bool:
    for event_type, payload in events:
        if event_type == "UserTradeObserved" and str(payload.get("fill_authority_state") or "") == EDLI_FILL_CONFIRMED_STATE:
            return True
    return False


def _aggregate_fill_economics(fill_payloads: list[dict[str, Any]]) -> tuple[float, float, float]:
    """Sum filled_size, size-weight avg_fill_price, sum fees across DISTINCT fills.

    The caller (``_confirmed_fill_payloads``) has already collapsed the
    MATCHED/MINED/CONFIRMED re-reports of one venue fill to a single payload per
    distinct ``trade_id`` (MF-2 phantom-fix), so this function sums one entry per
    real fill. Forward-proofs DEFECT-4: FOK gives one fill today, but two genuine
    partial fills (two distinct ``trade_id``s, or two id-less legs) still sum.
    Each leg's price defaults to its own ``avg_fill_price`` (or ``fill_price``);
    the position-level price is the size-weighted mean so cost_basis =
    sum(size_i * price_i) exactly.
    """
    total_size = 0.0
    total_notional = 0.0
    total_fees = 0.0
    for payload in fill_payloads:
        size = _float_or_none(payload.get("filled_size") or payload.get("size"))
        if size is None or size <= 0:
            continue
        price = _float_or_none(payload.get("avg_fill_price") or payload.get("fill_price"))
        if price is None:
            continue
        total_size += size
        total_notional += size * price
        fees = _float_or_none(payload.get("fees"))
        if fees is not None:
            total_fees += fees
    avg_price = (total_notional / total_size) if total_size > 0 else 0.0
    return total_size, avg_price, total_fees


def _resolve_strategy_key_from_pre_submit(
    pre_submit: dict[str, Any],
    *,
    direction: str,
    metric: str,
) -> str:
    """Resolve strategy identity from the EDLI money path, never by default."""

    strategy_key = str(pre_submit.get("strategy_key") or "").strip()
    if not strategy_key:
        event_type = str(pre_submit.get("event_type") or "").strip()
        if event_type == "DAY0_EXTREME_UPDATED":
            if direction == "buy_no":
                strategy_key = "settlement_capture"
            elif direction == "buy_yes":
                strategy_key = "day0_nowcast_entry"
            else:
                raise EdliPositionBridgeError(
                    f"EDLI_BRIDGE_STRATEGY_DIRECTION_UNKNOWN:{event_type}:direction={direction}"
                )
        elif event_type in {"FORECAST_SNAPSHOT_READY", "EDLI_REDECISION_PENDING"}:
            if direction in {"buy_yes", "buy_no"}:
                strategy_key = "forecast_qkernel_entry"
            else:
                raise EdliPositionBridgeError(
                    f"EDLI_BRIDGE_STRATEGY_DIRECTION_UNKNOWN:{event_type}:direction={direction}"
                )
        else:
            raise EdliPositionBridgeError(
                "EDLI_BRIDGE_STRATEGY_MISSING: strategy_key required when event_type is absent"
            )

    from src.strategy.strategy_profile import try_get

    profile = try_get(strategy_key)
    if profile is None:
        raise EdliPositionBridgeError(f"EDLI_BRIDGE_STRATEGY_UNKNOWN:{strategy_key}")
    if not profile.is_runtime_live():
        raise EdliPositionBridgeError(f"EDLI_BRIDGE_STRATEGY_NOT_RUNTIME_LIVE:{strategy_key}")
    if not profile.is_direction_allowed(direction):
        raise EdliPositionBridgeError(
            f"EDLI_BRIDGE_STRATEGY_DIRECTION_BLOCKED:{strategy_key}:direction={direction}"
        )
    if metric and not profile.metric_is_live(metric):
        raise EdliPositionBridgeError(
            f"EDLI_BRIDGE_STRATEGY_METRIC_BLOCKED:{strategy_key}:metric={metric}"
        )
    return strategy_key


def _resolve_identity(events: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    """Pull condition_id / token_id / direction / identity from the aggregate.

    Primary source is PreSubmitRevalidated (the revalidated, committed trade
    identity). ExecutionCommandCreated supplies execution_command_id; the
    UserTradeObserved venue_order_id provides the order linkage.
    """
    pre_submit = _latest_payload(events, "PreSubmitRevalidated") or {}
    command = _latest_payload(events, "ExecutionCommandCreated") or {}
    last_fill = None
    for event_type, payload in reversed(events):
        if event_type == "UserTradeObserved":
            last_fill = payload
            break

    condition_id = str(pre_submit.get("condition_id") or "").strip()
    token_id = str(pre_submit.get("token_id") or "").strip()
    if not condition_id or not token_id:
        raise EdliPositionBridgeError(
            "EDLI_BRIDGE_IDENTITY_MISSING: PreSubmitRevalidated must carry condition_id and token_id"
        )
    direction = str(pre_submit.get("direction") or "").strip().lower()
    if direction not in {"buy_yes", "buy_no"}:
        # native_token_side ("NO"/"YES") or outcome_label is the fallback
        # discriminator when an explicit direction is absent.
        side_hint = str(
            pre_submit.get("native_token_side")
            or pre_submit.get("outcome_label")
            or ""
        ).strip().upper()
        if side_hint == "NO":
            direction = "buy_no"
        elif side_hint == "YES":
            direction = "buy_yes"
        else:
            raise EdliPositionBridgeError(
                "EDLI_BRIDGE_DIRECTION_UNRESOLVED: cannot determine buy_yes/buy_no for fill"
            )
    city = str(pre_submit.get("city") or "").strip()
    target_date = str(pre_submit.get("target_date") or "").strip()
    bin_label = str(pre_submit.get("bin_label") or "").strip()
    metric = str(pre_submit.get("metric") or pre_submit.get("temperature_metric") or "").strip().lower()
    unit = str(pre_submit.get("unit") or pre_submit.get("temperature_unit") or "").strip().upper()
    strategy_key = _resolve_strategy_key_from_pre_submit(
        pre_submit,
        direction=direction,
        metric=metric,
    )
    missing_identity = [
        name
        for name, value in (
            ("city", city),
            ("target_date", target_date),
            ("bin_label", bin_label),
            ("metric", metric),
            ("unit", unit),
        )
        if not value
    ]
    if missing_identity:
        raise EdliPositionBridgeError(
            "EDLI_BRIDGE_MARKET_IDENTITY_MISSING: "
            + ",".join(missing_identity)
            + " required before materializing position_current"
        )
    if metric not in {"high", "low"}:
        raise EdliPositionBridgeError(f"EDLI_BRIDGE_METRIC_INVALID: {metric!r}")
    if unit not in {"C", "F"}:
        raise EdliPositionBridgeError(f"EDLI_BRIDGE_UNIT_INVALID: {unit!r}")

    return {
        "condition_id": condition_id,
        "token_id": token_id,
        "direction": direction,
        "outcome_label": str(pre_submit.get("outcome_label") or ("NO" if direction == "buy_no" else "YES")),
        "city": city,
        "target_date": target_date,
        "bin_label": bin_label,
        "metric": metric,
        "unit": unit,
        "strategy_key": strategy_key,
        "market_id": str(pre_submit.get("market_id") or condition_id),
        "cluster": str(pre_submit.get("cluster") or city),
        "p_posterior": _pre_submit_posterior(pre_submit),
        "entry_ci_width": 0.0,
        "actionable_certificate_hash": str(
            pre_submit.get("expected_edge_source_certificate_hash")
            or pre_submit.get("actionable_certificate_hash")
            or ""
        ),
        "final_intent_certificate_hash": str(pre_submit.get("final_intent_certificate_hash") or ""),
        "decision_snapshot_id": str(pre_submit.get("executable_snapshot_id") or pre_submit.get("snapshot_id") or ""),
        "final_intent_id": str(pre_submit.get("final_intent_id") or ""),
        "execution_command_id": str(command.get("execution_command_id") or (last_fill or {}).get("execution_command_id") or ""),
        "venue_order_id": str((last_fill or {}).get("venue_order_id") or command.get("venue_order_id") or ""),
        "event_id": str(pre_submit.get("event_id") or ""),
    }


def _build_bridge_position(
    *,
    aggregate_id: str,
    identity: dict[str, Any],
    filled_size: float,
    avg_fill_price: float,
    fees: float,
    filled_at: str,
    posted_at: str | None = None,
    env: str,
):
    """Construct a Position carrying the legacy ``record_entry`` field semantics.

    Critical token placement (chain_reconciliation.py:1057):
    ``tid = pos.token_id if pos.direction == "buy_yes" else pos.no_token_id``.
    The EDLI ``token_id`` is the ELECTED/traded native token. For buy_no it must
    land on ``no_token_id``; for buy_yes on ``token_id`` — otherwise the chain
    aggregate keyed by the on-chain asset token will not match.
    """
    from src.state.portfolio import Position

    position_id = edli_bridge_position_id(aggregate_id)
    direction = identity["direction"]
    elected_token = identity["token_id"]
    cost_basis = filled_size * avg_fill_price
    temperature_metric = str(identity["metric"])

    pos = Position(
        trade_id=position_id,
        market_id=identity["market_id"],
        city=identity["city"],
        cluster=identity["cluster"],
        target_date=identity["target_date"],
        bin_label=identity["bin_label"],
        direction=direction,
        unit=str(identity["unit"]),
        temperature_metric=temperature_metric,
        env=env,
        size_usd=cost_basis,
        entry_price=avg_fill_price,
        p_posterior=float(identity.get("p_posterior") or 0.0),
        entry_ci_width=float(identity.get("entry_ci_width") or 0.0),
        shares=filled_size,
        cost_basis_usd=cost_basis,
        condition_id=identity["condition_id"],
        decision_snapshot_id=identity["decision_snapshot_id"],
        entry_method=str(identity.get("entry_method") or "ens_member_counting"),
        strategy_key=str(identity["strategy_key"]),
        order_id=identity.get("venue_order_id") or identity.get("execution_command_id") or "",
        order_status="filled",
        # C4 telemetry-truth: order_posted_at = real submit time (venue_commands.created_at,
        # threaded as posted_at); NULL if the command row is absent — never the synthetic fill
        # wall-clock (which would collapse submit→fill latency to 0). Consumers use getattr(...,None)
        # + COALESCE, and the column is nullable, so NULL is safe.
        order_posted_at=posted_at,
        entered_at=filled_at,
        entered_at_authority="verified_entry_fill",
        entry_fill_verified=True,
        fill_authority=FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
        chain_state="local_only",
    )
    # Token placement by direction so chain reconciliation matches by token.
    if direction == "buy_yes":
        pos.token_id = elected_token
    else:
        pos.no_token_id = elected_token
    # state must reflect an active, filled position so canonical phase folds to ACTIVE.
    from src.state.portfolio import LifecycleState

    pos.state = LifecycleState.HOLDING.value
    return pos


def _open_intent_event_exists(conn: sqlite3.Connection, position_id: str) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM position_events
        WHERE position_id = ? AND event_type = 'POSITION_OPEN_INTENT'
        LIMIT 1
        """,
        (position_id,),
    ).fetchone()
    return row is not None


_BRIDGE_OPEN_PHASES = ("pending_entry", "active", "day0_window", "pending_exit", "unknown")
_BRIDGE_EQUIVALENCE_COLUMNS = (
    "market_id",
    "city",
    "target_date",
    "bin_label",
    "direction",
    "unit",
    "strategy_key",
    "condition_id",
    "temperature_metric",
    "order_id",
)


def _bridge_projection_token(projection: dict) -> str:
    return str(projection.get("token_id") or projection.get("no_token_id") or "").strip()


def _bridge_norm(value) -> str:
    return str(getattr(value, "value", value) or "").strip()


def _same_bridge_identity(existing: sqlite3.Row, projection: dict) -> bool:
    for col in _BRIDGE_EQUIVALENCE_COLUMNS:
        if _bridge_norm(existing[col]) != _bridge_norm(projection.get(col)):
            return False
    return True


def _bridge_numeric_equal(left: object, right: object, *, tol: float = 1e-9) -> bool:
    try:
        return abs(float(left or 0.0) - float(right or 0.0)) <= tol
    except (TypeError, ValueError):
        return False


def _same_order_chain_size_authority_must_be_preserved(existing: sqlite3.Row) -> bool:
    """Whether a same-order bridge replay must not overwrite current sizing.

    Once chain reconciliation has observed wallet inventory, chain shares/cost
    become the stronger live-money authority for current exposure. The EDLI
    bridge projection is still useful for initial materialisation and fill
    provenance, but replaying it after chain reconciliation must not inflate the
    position back to stale fill-projection size.
    """
    try:
        chain_state = _bridge_norm(existing["chain_state"])
    except (KeyError, IndexError):
        chain_state = ""
    try:
        chain_shares = float(existing["chain_shares"] or 0.0)
    except (KeyError, IndexError, TypeError, ValueError):
        chain_shares = 0.0
    return chain_state == "synced" and chain_shares > 0.0


def _same_order_absorb_already_recorded(
    conn: sqlite3.Connection,
    *,
    position_id: str,
    attempted_position_id: str,
) -> bool:
    if not attempted_position_id:
        return False
    rows = conn.execute(
        """
        SELECT payload_json
          FROM position_events
         WHERE position_id = ?
           AND event_type = 'MANUAL_OVERRIDE_APPLIED'
           AND source_module = 'src.events.edli_position_bridge'
         ORDER BY sequence_no DESC
        """,
        (position_id,),
    ).fetchall()
    for row in rows:
        try:
            payload = json.loads(str(row["payload_json"] or "{}"))
        except (TypeError, ValueError):
            continue
        if (
            isinstance(payload, dict)
            and payload.get("reason") == "edli_bridge_same_order_already_materialized"
            and str(payload.get("attempted_position_id") or "") == attempted_position_id
        ):
            return True
    return False


def _absorb_same_order_duplicate_bridge_fill(
    conn: sqlite3.Connection,
    projection: dict,
) -> str | None:
    """Resolve an EDLI bridge F109 collision when the order is already materialised.

    This is deliberately narrower than same-token averaging. It only absorbs when
    the existing open row has the same token AND the same order_id/market identity
    as the bridge projection. Different orders on the same token remain F109
    failures so duplicate-entry defects stay loud.
    """
    token_id = _bridge_projection_token(projection)
    order_id = str(projection.get("order_id") or "").strip()
    if not token_id or not order_id:
        return None
    rows = conn.execute(
        """
        SELECT *
          FROM position_current
         WHERE (token_id = ? OR no_token_id = ?)
           AND order_id = ?
           AND phase IN (?, ?, ?, ?, ?)
         ORDER BY updated_at DESC, position_id DESC
        """,
        (token_id, token_id, order_id, *_BRIDGE_OPEN_PHASES),
    ).fetchall()
    matches = [row for row in rows if _same_bridge_identity(row, projection)]
    if len(matches) != 1:
        return None

    existing = matches[0]
    position_id = str(existing["position_id"])
    attempted_position_id = str(projection.get("position_id") or "")
    if _same_order_chain_size_authority_must_be_preserved(existing):
        iso_now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            UPDATE position_current
               SET p_posterior = ?,
                   entry_ci_width = ?,
                   entry_method = ?,
                   decision_snapshot_id = ?,
                   fill_authority = COALESCE(?, fill_authority),
                   updated_at = ?
             WHERE position_id = ?
            """,
            (
                float(projection.get("p_posterior") or 0.0),
                float(projection.get("entry_ci_width") or 0.0),
                str(projection.get("entry_method") or ""),
                str(projection.get("decision_snapshot_id") or ""),
                projection.get("fill_authority"),
                iso_now,
                position_id,
            ),
        )
        return position_id
    if (
        _same_order_absorb_already_recorded(
            conn,
            position_id=position_id,
            attempted_position_id=attempted_position_id,
        )
        and _bridge_numeric_equal(existing["shares"], projection.get("shares"))
        and _bridge_numeric_equal(existing["cost_basis_usd"], projection.get("cost_basis_usd"))
    ):
        return position_id
    iso_now = datetime.now(timezone.utc).isoformat()
    seq_row = conn.execute(
        "SELECT COALESCE(MAX(sequence_no), 0) FROM position_events WHERE position_id = ?",
        (position_id,),
    ).fetchone()
    next_seq = int(seq_row[0]) + 1 if seq_row else 1
    payload = json.dumps(
        {
            "reason": "edli_bridge_same_order_already_materialized",
            "attempted_position_id": attempted_position_id,
            "shares": float(projection.get("shares") or 0.0),
            "cost_basis_usd": float(projection.get("cost_basis_usd") or 0.0),
        },
        sort_keys=True,
    )
    conn.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, event_version, sequence_no, event_type,
            occurred_at, phase_before, phase_after, strategy_key,
            source_module, payload_json, env
        ) VALUES (?, ?, 1, ?, 'MANUAL_OVERRIDE_APPLIED', ?, ?, ?, ?,
                  'src.events.edli_position_bridge', ?, 'live')
        """,
        (
            f"bridge_absorb_{position_id}_{next_seq}",
            position_id,
            next_seq,
            iso_now,
            str(existing["phase"] or ""),
            str(existing["phase"] or ""),
            str(existing["strategy_key"] or ""),
            payload,
        ),
    )
    conn.execute(
        """
        UPDATE position_current
           SET shares = ?,
               cost_basis_usd = ?,
               size_usd = ?,
               entry_price = ?,
               p_posterior = ?,
               entry_ci_width = ?,
               entry_method = ?,
               decision_snapshot_id = ?,
               fill_authority = COALESCE(?, fill_authority),
               updated_at = ?
         WHERE position_id = ?
        """,
        (
            float(projection.get("shares") or 0.0),
            float(projection.get("cost_basis_usd") or 0.0),
            float(projection.get("size_usd") or projection.get("cost_basis_usd") or 0.0),
            float(projection.get("entry_price") or 0.0),
            float(projection.get("p_posterior") or 0.0),
            float(projection.get("entry_ci_width") or 0.0),
            str(projection.get("entry_method") or ""),
            str(projection.get("decision_snapshot_id") or ""),
            projection.get("fill_authority"),
            iso_now,
            position_id,
        ),
    )
    return position_id


def _posterior_id_for_final_intent(
    conn: sqlite3.Connection, final_intent_id: str | None
) -> int | None:
    """Fail-soft lookup of the driving posterior_id for an entry fill.

    H2_E2E (REAUDIT_0_1.md §2/§4): the only durable, typed link between a
    replacement_0_1 order and the posterior that drove it is
    ``edli_no_submit_receipts.posterior_id`` (populated by the reactor on the
    NO_SUBMIT receipt; ``no_submit_receipts.py:165``). The reconcile here joins
    that row by ``final_intent_id`` so ``execution_fact.posterior_id`` is
    populated from the actually-written source rather than left dead.

    Observability ONLY and STRICTLY FAIL-SOFT: any miss (no final_intent_id, no
    receipts table, no matching row, NULL posterior_id, or ANY sqlite error)
    returns ``None`` so the fill is never blocked or altered. ``log_execution_fact``
    COALESCEs the value, so passing None never NULLs an existing link, and a
    canonical (non-replacement) order whose receipt has NULL posterior_id simply
    leaves the column NULL.
    """
    if not final_intent_id:
        return None
    try:
        table = _resolved_table(conn, "edli_no_submit_receipts")
        row = conn.execute(
            f"""
            SELECT posterior_id
            FROM {table}
            WHERE final_intent_id = ?
              AND posterior_id IS NOT NULL
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (final_intent_id,),
        ).fetchone()
    except sqlite3.Error:
        # Receipts table absent (single-table unit conn) or any read error:
        # fail-soft. The posterior link is observability only — never a gate.
        return None
    if row is None:
        return None
    value = row["posterior_id"] if isinstance(row, sqlite3.Row) else row[0]
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _row_value(row: sqlite3.Row | tuple | None, key: str, index: int) -> Any:
    if row is None:
        return None
    if isinstance(row, sqlite3.Row):
        return row[key]
    return row[index]


def _venue_command_row_for_execution_command_id(
    conn: sqlite3.Connection,
    execution_command_id: str | None,
) -> sqlite3.Row | tuple | None:
    """Find the venue command row that corresponds to an EDLI execution command.

    Live EDLI commands have historically stored the EDLI execution command id in
    ``venue_commands.decision_id`` while ``venue_commands.command_id`` remains
    the shorter command-journal id. Probe both fields so bridge projection,
    execution_fact, and command-journal links all converge on the same command.
    """

    execution_command_id = str(execution_command_id or "").strip()
    if not execution_command_id:
        return None
    try:
        return conn.execute(
            """
            SELECT command_id, position_id, created_at
              FROM venue_commands
             WHERE command_id = ?
                OR decision_id = ?
             ORDER BY created_at DESC
             LIMIT 1
            """,
            (execution_command_id, execution_command_id),
        ).fetchone()
    except sqlite3.Error:
        return None


def _execution_command_id_from_bridge_events(events: list[tuple[str, dict[str, Any]]]) -> str:
    """Return the EDLI execution command id without resolving trade strategy.

    Command-link repair is a journal convergence operation for an already
    materialized position. It must not re-run the full position identity parser:
    historical bridge events can lack ``strategy_key`` / ``event_type`` while
    still carrying enough command identity to prove that no link repair is
    possible or required.
    """

    command = _latest_payload(events, "ExecutionCommandCreated") or {}
    command_id = str(command.get("execution_command_id") or "").strip()
    if command_id:
        return command_id
    for event_type, payload in reversed(events):
        if event_type == "UserTradeObserved":
            return str(payload.get("execution_command_id") or "").strip()
    return ""


def sync_venue_command_position_link_for_edli_fill(
    conn: sqlite3.Connection,
    aggregate_id: str,
    *,
    position_id: str | None = None,
    now: datetime | None = None,
) -> bool:
    """Relink an EDLI filled command to its canonical position_current row.

    This does not create positions and does not overwrite a command that already
    points at another existing position. It only cures the EDLI bridge split
    where the command journal kept its pre-bridge short ``position_id`` after
    the confirmed fill was projected under the deterministic EDLI position id.
    """

    if not aggregate_id:
        return False
    events = _aggregate_event_rows(conn, aggregate_id)
    if not events or not _has_confirmed_fill(events):
        return False
    execution_command_id = _execution_command_id_from_bridge_events(events)
    if not execution_command_id:
        return False
    command_row = _venue_command_row_for_execution_command_id(
        conn,
        execution_command_id,
    )
    command_id = str(_row_value(command_row, "command_id", 0) or "")
    if not command_id:
        return False
    canonical_position_id = str(position_id or edli_bridge_position_id(aggregate_id)).strip()
    if not canonical_position_id:
        return False
    current_position_id = str(_row_value(command_row, "position_id", 1) or "")
    if current_position_id == canonical_position_id:
        return False
    if current_position_id:
        current_exists = conn.execute(
            "SELECT 1 FROM position_current WHERE position_id = ? LIMIT 1",
            (current_position_id,),
        ).fetchone()
        if current_exists is not None:
            return False

    from src.state.venue_command_repo import repair_command_position_link_if_orphaned

    observed_at = (now or datetime.now(timezone.utc)).isoformat()
    return repair_command_position_link_if_orphaned(
        conn,
        command_id=command_id,
        canonical_position_id=canonical_position_id,
        occurred_at=observed_at,
        reason="edli_confirmed_fill_bridge_canonical_position_id",
    )


# ---------------------------------------------------------------------------
# Settled-market routing + bridge-failure retry helpers
# ---------------------------------------------------------------------------

# Disposition constants — must match the CHECK in edli_fill_bridge_dispositions_schema.py
DISPOSITION_SETTLED_MARKET = "SETTLED_MARKET_FILL_BOOKED"

# A second, DELIBERATELY NON-AUTOMATIC terminal for aggregates a human has
# diagnosed as structurally unrecoverable (e.g. a payload shape that will
# never parse under current code). This constant is written by exactly one
# path: ``mark_unrecoverable_manual_review`` — an explicit operator/script
# call, never by the scan/retry loop itself. The automatic path always stays
# retry-forever with decaying cadence (see ``is_retry_eligible``); marking a
# row here only stops WASTED automatic retry attempts once a human has
# confirmed retry can never succeed — it does not stop the row from being
# seen. Every scan pass that encounters this disposition logs a WARNING with
# the row's age (see the scan in src.ingest.price_channel_ingest), so an
# unresolved manual-review row keeps surfacing in the logs instead of
# silently vanishing — the honest alternative to claiming an unparseable
# aggregate is still "making progress" via infinite retry.
DISPOSITION_UNRECOVERABLE_MANUAL_REVIEW = "UNRECOVERABLE_MANUAL_REVIEW"

# Cap on the decaying retry cadence, in scan cycles (~60s/cycle live cadence).
# A confirmed on-chain fill is truth that MUST eventually materialise; failure
# to do so is a code/venue problem, never a reason to stop trying. This bounds
# the WORST-CASE wait between attempts (~4.3h) without ever excluding the
# aggregate — eligibility never terminates.
#
# Scheduler-threshold note (deep-review adjudication, 2026-07-11): eligibility
# is computed by scanning every row's (attempt_count, updated_at) each cycle
# (O(rows), no index) — adjudicated OPTIONAL at the current live row count
# (~21 rows across settled + accumulating + manual-review). An indexed
# ``next_attempt_at`` column (materialise the backoff deadline at write time,
# index it, query only due rows) becomes MANDATORY if this table's row count
# grows large enough that a full per-cycle scan is no longer cheap — watch
# for it if the aggregate volume driving this table changes by an order of
# magnitude.
_RETRY_BACKOFF_CAP_CYCLES = 256
_RETRY_CYCLE_SECONDS = 60


def _dispositions_table(conn: sqlite3.Connection) -> str:
    """Resolve the schema-qualified name of the disposition table (ATTACHed world or local)."""
    try:
        attached = {row[1] for row in conn.execute("PRAGMA database_list").fetchall()}
    except sqlite3.Error:
        attached = set()
    if "world" in attached:
        row = conn.execute(
            "SELECT 1 FROM world.sqlite_master WHERE type='table' AND name='edli_fill_bridge_dispositions'"
        ).fetchone()
        if row is not None:
            return "world.edli_fill_bridge_dispositions"
    try:
        conn.execute("SELECT 1 FROM edli_fill_bridge_dispositions LIMIT 1")
        return "edli_fill_bridge_dispositions"
    except sqlite3.Error:
        return "edli_fill_bridge_dispositions"


def get_fill_bridge_disposition(conn: sqlite3.Connection, aggregate_id: str) -> str | None:
    """Return the terminal disposition for an aggregate, or None if not yet disposed.

    Returns None when:
    - no row exists for the aggregate,
    - the row exists but disposition is NULL (accumulating failure count under
      decaying retry — not terminal; the aggregate is retried on a backoff
      cadence, never permanently excluded).
    Returns DISPOSITION_SETTLED_MARKET for terminal (accounting-truth) rows,
    or DISPOSITION_UNRECOVERABLE_MANUAL_REVIEW for rows an operator has
    explicitly flagged as diagnosed-unrecoverable (never set automatically —
    see ``mark_unrecoverable_manual_review``).
    """
    table = _dispositions_table(conn)
    try:
        row = conn.execute(
            f"SELECT disposition FROM {table} WHERE aggregate_id = ? LIMIT 1",
            (aggregate_id,),
        ).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    val = row[0] if not isinstance(row, sqlite3.Row) else row["disposition"]
    # SQL NULL → Python None (accumulating row, not yet terminal)
    if val is None:
        return None
    return str(val)


def _record_settled_disposition(
    conn: sqlite3.Connection,
    aggregate_id: str,
    reason: str,
    now_str: str,
) -> None:
    """Persist SETTLED_MARKET_FILL_BOOKED, idempotent (INSERT OR IGNORE)."""
    table = _dispositions_table(conn)
    try:
        conn.execute(
            f"""
            INSERT OR IGNORE INTO {table}
                (aggregate_id, disposition, reason, attempt_count, created_at, updated_at)
            VALUES (?, ?, ?, 0, ?, ?)
            """,
            (aggregate_id, DISPOSITION_SETTLED_MARKET, reason, now_str, now_str),
        )
    except sqlite3.Error as exc:
        logger.warning("fill-bridge: could not persist settled disposition for %s: %s", aggregate_id, exc)


def mark_unrecoverable_manual_review(
    conn: sqlite3.Connection,
    aggregate_id: str,
    reason: str,
    now_str: str,
) -> None:
    """Explicit operator/script transition to DISPOSITION_UNRECOVERABLE_MANUAL_REVIEW.

    THE SOLE SANCTIONED WRITER of this disposition. The automatic scan/retry
    loop (``_edli_durable_fill_bridge_scan`` in
    ``src.ingest.price_channel_ingest``) never calls this function and never
    writes this value — a human (or an explicit operator script acting on a
    human's diagnosis) must call it directly, after confirming an aggregate's
    failure is structurally unrecoverable (e.g. a payload shape current code
    can never parse), not merely persistent. Idempotent no-op against a row
    already carrying DISPOSITION_SETTLED_MARKET (accounting truth is never
    overwritten by a manual-review flag).
    """
    table = _dispositions_table(conn)
    try:
        conn.execute(
            f"""
            INSERT INTO {table}
                (aggregate_id, disposition, reason, attempt_count, created_at, updated_at)
            VALUES (?, ?, ?, 0, ?, ?)
            ON CONFLICT(aggregate_id) DO UPDATE SET
                disposition = excluded.disposition,
                reason = excluded.reason,
                updated_at = excluded.updated_at
            WHERE disposition IS NULL OR disposition = ?
            """,
            (
                aggregate_id,
                DISPOSITION_UNRECOVERABLE_MANUAL_REVIEW,
                reason,
                now_str,
                now_str,
                DISPOSITION_UNRECOVERABLE_MANUAL_REVIEW,
            ),
        )
    except sqlite3.Error as exc:
        logger.warning(
            "fill-bridge: could not mark %s UNRECOVERABLE_MANUAL_REVIEW: %s", aggregate_id, exc
        )


def disposition_reason_and_age(
    conn: sqlite3.Connection, aggregate_id: str, now: datetime
) -> tuple[str, str] | None:
    """Return (reason, age_str) for an aggregate's disposition row, or None.

    Used by the scan to WARNING-log an UNRECOVERABLE_MANUAL_REVIEW row on
    every pass with its age — the row stays operator-visible instead of
    silently vanishing once flagged.
    """
    table = _dispositions_table(conn)
    try:
        row = conn.execute(
            f"SELECT reason, updated_at FROM {table} WHERE aggregate_id = ? LIMIT 1",
            (aggregate_id,),
        ).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    reason = str((row[0] if not isinstance(row, sqlite3.Row) else row["reason"]) or "")
    updated_at = row[1] if not isinstance(row, sqlite3.Row) else row["updated_at"]
    age_str = "unknown"
    try:
        marked_at = datetime.fromisoformat(str(updated_at))
        if marked_at.tzinfo is None:
            marked_at = marked_at.replace(tzinfo=timezone.utc)
        now_aware = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
        age_str = f"{(now_aware - marked_at).days}d"
    except (TypeError, ValueError):
        pass
    return reason, age_str


def _increment_failure_count(
    conn: sqlite3.Connection,
    aggregate_id: str,
    error_str: str,
    now_str: str,
) -> int:
    """Increment attempt_count for an aggregate, inserting the row if absent.

    Returns the NEW attempt_count after the increment. attempt_count is pure
    evidence + retry-cadence input (see ``_retry_backoff_seconds`` /
    ``is_retry_eligible``) — it never drives a terminal exclusion. The caller
    logs loudly on every failure and keeps the aggregate in the scan set
    forever, retried on a decaying cadence so a poison aggregate cannot
    dominate every cycle.
    """
    table = _dispositions_table(conn)
    try:
        # Upsert: insert with NULL disposition (accumulating, not terminal) on first
        # failure; increment attempt_count on subsequent failures. The WHERE guard prevents
        # incrementing a row that already has a terminal disposition (SETTLED written by
        # _record_settled_disposition — the only terminal disposition left).
        conn.execute(
            f"""
            INSERT INTO {table}
                (aggregate_id, disposition, reason, attempt_count, last_error, created_at, updated_at)
            VALUES (?, NULL, 'bridge_failure_accumulating', 1, ?, ?, ?)
            ON CONFLICT(aggregate_id) DO UPDATE SET
                attempt_count = attempt_count + 1,
                last_error = excluded.last_error,
                updated_at = excluded.updated_at
            WHERE disposition IS NULL
            """,
            (
                aggregate_id,
                error_str[:2000],
                now_str,
                now_str,
            ),
        )
        row = conn.execute(
            f"SELECT attempt_count FROM {table} WHERE aggregate_id = ? LIMIT 1",
            (aggregate_id,),
        ).fetchone()
        if row is None:
            return 1
        return int(row[0] if not isinstance(row, sqlite3.Row) else row["attempt_count"])
    except sqlite3.Error as exc:
        logger.warning("fill-bridge: could not update failure count for %s: %s", aggregate_id, exc)
        return 1


def _retry_backoff_seconds(attempt_count: int) -> int:
    """Decaying retry cadence for an accumulating bridge-failure aggregate.

    Doubles the minimum wait between attempts per failure, in ~cycle units
    (``_RETRY_CYCLE_SECONDS`` each), capped at ``_RETRY_BACKOFF_CAP_CYCLES``.
    Bounds per-cycle cost (a poison aggregate is attempted less and less
    often) without ever making retry ineligible — the cap is a ceiling on
    wait time, not a terminal exclusion.
    """
    cycles = min(2 ** min(max(int(attempt_count), 0), 8), _RETRY_BACKOFF_CAP_CYCLES)
    return cycles * _RETRY_CYCLE_SECONDS


def is_retry_eligible(conn: sqlite3.Connection, aggregate_id: str, now: datetime) -> bool:
    """Whether an accumulating bridge-failure aggregate may be retried this cycle.

    True when: no disposition row exists yet (fresh aggregate — always try),
    or the row's terminal disposition is not SETTLED (i.e. it is an
    accumulating failure row) AND enough wall-clock time has elapsed since
    ``updated_at`` per ``_retry_backoff_seconds(attempt_count)``. Malformed
    timestamps fail open (eligible) rather than silently starving retry.
    """
    table = _dispositions_table(conn)
    try:
        row = conn.execute(
            f"SELECT attempt_count, updated_at FROM {table} "
            "WHERE aggregate_id = ? AND disposition IS NULL LIMIT 1",
            (aggregate_id,),
        ).fetchone()
    except sqlite3.Error:
        return True
    if row is None:
        return True
    attempt_count = int(row[0] if not isinstance(row, sqlite3.Row) else row["attempt_count"])
    updated_at = row[1] if not isinstance(row, sqlite3.Row) else row["updated_at"]
    try:
        last = datetime.fromisoformat(str(updated_at))
    except (TypeError, ValueError):
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    now_aware = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    elapsed = (now_aware - last).total_seconds()
    return elapsed >= _retry_backoff_seconds(attempt_count)


def _market_is_settled(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    temperature_metric: str,
    today_utc: str,
) -> tuple[bool, str]:
    """Check whether a weather market is already settled.

    Returns (is_settled, evidence_string).

    Authority order:
    1. settlements table with authority='VERIFIED' — definitive settlement record.
    2. Conservative fallback: target_date strictly older than today_utc (daily weather
       market; if target_date < today it has settled by definition even without a DB row).

    The fallback is conservative: target_date = today is NOT declared settled (could still
    be trading during the day). Target_date < today (strictly) on a daily market means
    settlement is structurally over.
    """
    if city and target_date and temperature_metric:
        # Primary: settlements table — check ATTACHed world or local.
        settlements_table = _resolved_table(conn, "settlements")
        try:
            row = conn.execute(
                f"""
                SELECT 1 FROM {settlements_table}
                WHERE city = ?
                  AND target_date = ?
                  AND temperature_metric = ?
                  AND authority = 'VERIFIED'
                LIMIT 1
                """,
                (city, target_date, temperature_metric),
            ).fetchone()
            if row is not None:
                return True, f"settlements.authority=VERIFIED city={city} target_date={target_date} metric={temperature_metric}"
        except sqlite3.Error:
            pass  # Table absent (test conn) — fall through to date fallback

    # Fallback: date comparison (UTC).
    if target_date and today_utc:
        try:
            if target_date < today_utc[:10]:  # ISO date prefix comparison: "2026-06-06" < "2026-06-12"
                return True, f"target_date={target_date} < today_utc={today_utc[:10]} (conservative date fallback)"
        except (TypeError, ValueError):
            pass

    return False, ""


# Trade-fact state this bridge ever writes. Only a truly FILL_CONFIRMED EDLI
# leg becomes a permanent venue_trade_facts row -- see
# _append_edli_confirmed_trade_facts.
_EDLI_TRADE_FACT_STATE = "CONFIRMED"


def _edli_source_event_hash(aggregate_id: str, payload: Mapping[str, Any], index: int) -> str:
    """Deterministic sha256 identity for one EDLI confirmed-fill event.

    Stable across replay: derived only from the EDLI aggregate id, the fill's
    own event/final-intent identity, and its position within the deduped fill
    list (``_confirmed_fill_payloads`` order is deterministic, sorted by
    ``event_sequence``) -- never from bridge wall-clock or DB row ids. This is
    BOTH the required ``append_trade_fact`` ``raw_payload_hash`` and (when the
    EDLI payload carries no native venue ``trade_id``) the basis for a stable
    synthetic ``trade_id``, and it is echoed inside ``raw_payload_json`` as
    ``source_edli_event_hash`` so the source EDLI event stays traceable
    without decoding column semantics.
    """
    basis = {
        "aggregate_id": aggregate_id,
        "trade_id": str(payload.get("trade_id") or "").strip(),
        "event_id": payload.get("event_id"),
        "final_intent_id": payload.get("final_intent_id"),
        "index": index,
    }
    return hashlib.sha256(
        json.dumps(basis, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _edli_canonical_trade_fact_already_recorded(
    conn: sqlite3.Connection,
    *,
    trade_id: str,
    command_id: str,
    state: str,
    filled_size: str,
    fill_price: str,
) -> bool:
    """True if an identical revision of this canonical trade fact is durable.

    Mirrors ``src.ingest.fill_synchronizer._fact_already_recorded`` (the
    established idempotency check for ``append_trade_fact``, which is a plain
    append-only insert with no upsert): scoped to the exact (trade_id,
    command_id, state, filled_size, fill_price) tuple, so a genuinely new
    revision would still append, but a byte-for-byte replay of the same
    source EDLI event is a no-op.
    """
    row = conn.execute(
        """
        SELECT 1 FROM venue_trade_facts
         WHERE trade_id = ? AND command_id = ? AND state = ?
           AND filled_size = ? AND fill_price = ?
         LIMIT 1
        """,
        (trade_id, command_id, state, filled_size, fill_price),
    ).fetchone()
    return row is not None


def _append_edli_confirmed_trade_facts(
    conn: sqlite3.Connection,
    *,
    aggregate_id: str,
    fill_payloads: list[dict[str, Any]],
    command_id: str,
    default_venue_order_id: str,
    observed_at: str,
) -> list[int]:
    """Append a permanent venue_trade_facts row per distinct confirmed EDLI fill.

    LX-T4/round-2-delta BLOCKER (docs/rebuild/local_ledger_excision_2026-07-12.md
    Sec.C1; docs/rebuild/consult_answers/local_ledger_excision_delta_round2_2026-07-13.txt
    "[BLOCKER] EDLI fill visibility"): before any EDLI fill may be excised from
    ``position_current`` writes (LX-3R), it must already be a durable,
    canonical trade-DB fact via the SAME append-only observation log
    (``venue_trade_facts``, ``src.state.venue_command_repo.append_trade_fact``)
    every other fill lane uses -- otherwise a future derive-on-read reducer
    would never see an EDLI-originated fill. This runs on EVERY call to
    ``materialize_position_current_from_edli_fill`` (first materialisation
    AND replay), appending exactly once per real confirmed fill via the
    deterministic-identity idempotency check below; it never touches
    ``position_current`` economics.

    Fail-soft only for the ONE expected non-error condition: no venue_commands
    row yet exists for this EDLI command (``command_id`` empty). That is not a
    bug -- the command row is normally written at submit time, before the
    fill this function is bridging -- so we log and defer; the next bridge
    call (replay) picks it up once the command row lands. Any other failure
    (malformed leg economics, missing venue_order_id) is skipped per-leg with
    a warning so one bad leg cannot starve the rest of the aggregate's facts.
    """
    if not command_id:
        logger.warning(
            "edli position bridge: no venue_commands row for aggregate=%s; "
            "canonical trade-fact append deferred until the command row exists",
            aggregate_id,
        )
        return []

    from src.state.venue_command_repo import append_trade_fact

    fact_ids: list[int] = []
    for index, payload in enumerate(fill_payloads):
        if str(payload.get("fill_authority_state") or "") != EDLI_FILL_CONFIRMED_STATE:
            # Only a truly confirmed leg is a permanent fact; MATCHED/MINED
            # legs of a still-in-flight partial are not yet canonical truth.
            continue
        size = _float_or_none(payload.get("filled_size") or payload.get("size"))
        price = _float_or_none(payload.get("avg_fill_price") or payload.get("fill_price"))
        if size is None or size <= 0 or price is None or price <= 0:
            logger.warning(
                "edli position bridge: confirmed fill leg has invalid economics, "
                "skipping canonical fact aggregate=%s index=%s",
                aggregate_id,
                index,
            )
            continue
        venue_order_id = str(
            payload.get("venue_order_id") or default_venue_order_id or ""
        ).strip()
        if not venue_order_id:
            logger.warning(
                "edli position bridge: confirmed fill leg missing venue_order_id, "
                "skipping canonical fact aggregate=%s index=%s",
                aggregate_id,
                index,
            )
            continue
        fees = _float_or_none(payload.get("fees")) or 0.0
        source_event_hash = _edli_source_event_hash(aggregate_id, payload, index)
        native_trade_id = str(payload.get("trade_id") or "").strip()
        trade_id = f"edli:{native_trade_id}" if native_trade_id else f"edli:{source_event_hash}"
        filled_size_s = str(size)
        fill_price_s = str(price)
        if _edli_canonical_trade_fact_already_recorded(
            conn,
            trade_id=trade_id,
            command_id=command_id,
            state=_EDLI_TRADE_FACT_STATE,
            filled_size=filled_size_s,
            fill_price=fill_price_s,
        ):
            continue
        fact_id = append_trade_fact(
            conn,
            trade_id=trade_id,
            venue_order_id=venue_order_id,
            command_id=command_id,
            state=_EDLI_TRADE_FACT_STATE,
            filled_size=filled_size_s,
            fill_price=fill_price_s,
            fee_paid_micro=int(round(fees * 1_000_000)),
            # WS_USER: EDLI fills are confirmed via the same authenticated
            # user channel legacy WS_USER trade facts represent (see
            # src.events.live_order_reconcile.assert_user_channel_fill_authority);
            # there is no dedicated EDLI value in the venue_trade_facts.source
            # CHECK constraint, and WS_USER is the existing closest analog
            # (src.events.edli_trade_fact_bridge already treats WS_USER+CONFIRMED
            # as the recognized user-channel-confirmed provenance).
            source="WS_USER",
            observed_at=observed_at,
            raw_payload_hash=source_event_hash,
            raw_payload_json={
                "source_module": "src.events.edli_position_bridge",
                "edli_aggregate_id": aggregate_id,
                "source_edli_event_hash": source_event_hash,
                "fill_authority_state": payload.get("fill_authority_state"),
                "raw_fill_payload": payload,
            },
        )
        fact_ids.append(fact_id)
    return fact_ids


def materialize_position_current_from_edli_fill(
    conn: sqlite3.Connection,
    aggregate_id: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Materialise / idempotently update a canonical position_current row.

    Returns a small summary dict on success (``position_id``, ``shares``,
    ``avg_fill_price``, ``cost_basis_usd``, ``created`` bool), or ``None`` when
    the aggregate has no CONFIRMED fill yet (nothing to bridge).

    INV-37: writes ``position_events`` + ``position_current`` and reads
    ``edli_live_order_events`` ON THE SAME CONNECTION ``conn``. The canonical
    write path nests its own SAVEPOINT. No independent connection is opened, and
    this function does NOT commit — the caller owns the transaction boundary.
    """
    if not aggregate_id:
        raise EdliPositionBridgeError("aggregate_id is required")

    events = _aggregate_event_rows(conn, aggregate_id)
    if not events:
        return None
    if not _has_confirmed_fill(events):
        return None

    identity = _resolve_identity(events)
    (
        decision_evidence,
        certificate_q_live,
        certificate_entry_ci_width,
        certificate_entry_method,
    ) = _entry_authority_from_certificates(
        conn,
        actionable_certificate_hash=str(identity.get("actionable_certificate_hash") or ""),
    )
    if certificate_q_live is None or not certificate_entry_method:
        audit_q_live, audit_entry_ci_width, audit_entry_method = _entry_authority_from_decision_audit(events)
        if certificate_q_live is None and audit_q_live is not None:
            certificate_q_live = audit_q_live
        if certificate_entry_ci_width <= 0.0 and audit_entry_ci_width > 0.0:
            certificate_entry_ci_width = audit_entry_ci_width
        if not certificate_entry_method and audit_entry_method:
            certificate_entry_method = audit_entry_method
    if certificate_q_live is not None:
        identity["p_posterior"] = certificate_q_live
    if certificate_entry_ci_width > 0.0:
        identity["entry_ci_width"] = certificate_entry_ci_width
    if certificate_entry_method:
        identity["entry_method"] = certificate_entry_method
    fill_payloads = _confirmed_fill_payloads(events)
    filled_size, avg_fill_price, fees = _aggregate_fill_economics(fill_payloads)
    if filled_size <= 0 or avg_fill_price <= 0:
        raise EdliPositionBridgeError(
            f"EDLI_BRIDGE_FILL_ECONOMICS_INVALID: filled_size={filled_size} avg_fill_price={avg_fill_price}"
        )

    now = now or datetime.now(timezone.utc)
    # 'now' is the bridge reconcile wall-clock — NOT the real fill time.
    # Only use it for position lifecycle fields (_build_bridge_position) where a
    # reference time is required; do NOT persist it as a telemetry event time.
    filled_at = now.isoformat()
    env = _bridge_env()

    # C4 telemetry-truth: resolve posted_at from the real submit-intent time
    # (venue_commands.created_at). If the command row is absent, posted_at stays
    # NULL so latency_seconds computes NULL (honest absence, not synthetic 0.0).
    _cmd_id = identity.get("execution_command_id") or ""
    _cmd_row = _venue_command_row_for_execution_command_id(conn, _cmd_id)
    _actual_command_id = str(_row_value(_cmd_row, "command_id", 0) or _cmd_id or "")
    _cmd_created_at: str | None = None
    if _cmd_row is not None:
        _created_at = _row_value(_cmd_row, "created_at", 2)
        _cmd_created_at = str(_created_at) if _created_at else None

    # LX-T4/round-2-delta BLOCKER: land the permanent canonical fact BEFORE
    # any position_current write below, on every call (first materialisation
    # AND replay share this code path) -- see _append_edli_confirmed_trade_facts.
    # command_id is only passed when a real venue_commands row was resolved
    # above; an unresolved EDLI execution_command_id must never be used as a
    # trade fact's command_id (FK target in production).
    _append_edli_confirmed_trade_facts(
        conn,
        aggregate_id=aggregate_id,
        fill_payloads=fill_payloads,
        command_id=_actual_command_id if _cmd_row is not None else "",
        default_venue_order_id=str(identity.get("venue_order_id") or ""),
        observed_at=filled_at,
    )

    pos = _build_bridge_position(
        aggregate_id=aggregate_id,
        identity=identity,
        filled_size=filled_size,
        avg_fill_price=avg_fill_price,
        fees=fees,
        filled_at=filled_at,
        posted_at=_cmd_created_at,
        env=env,
    )

    from src.engine.lifecycle_events import (
        ACTIVE,
        build_entry_canonical_write,
        build_position_current_projection,
    )
    from src.state.db import log_execution_fact
    from src.state.ledger import append_many_and_project, upsert_position_current
    from src.state.projection import DuplicatePositionOpenError

    position_id = pos.trade_id
    already_opened = _open_intent_event_exists(conn, position_id)

    if not already_opened:
        # First materialisation: POSITION_OPEN_INTENT + ENTRY_ORDER_POSTED +
        # ENTRY_ORDER_FILLED with phase ACTIVE (exact legacy entry semantics).
        events_batch, projection = build_entry_canonical_write(
            pos,
            phase_after=ACTIVE,
            source_module="src.events.edli_position_bridge",
            decision_evidence=decision_evidence,
        )
        try:
            append_many_and_project(conn, events_batch, projection)
            created = True
        except DuplicatePositionOpenError:
            absorbed_position_id = _absorb_same_order_duplicate_bridge_fill(conn, projection)
            if absorbed_position_id is None:
                raise
            position_id = absorbed_position_id
            created = False
    else:
        # Replay: the entry events already exist (append-only, unique key).
        # Re-derive the projection from the freshly-summed economics and UPDATE
        # position_current only (ON CONFLICT(position_id) DO UPDATE). This keeps
        # shares/cost_basis correct if a later partial fill arrived, without
        # duplicating events.
        projection = build_position_current_projection(pos)
        projection["phase"] = ACTIVE
        upsert_position_current(conn, projection)
        created = False

    sync_venue_command_position_link_for_edli_fill(
        conn,
        aggregate_id,
        position_id=position_id,
        now=now,
    )

    # H2_E2E: populate execution_fact.posterior_id at the PRIMARY entry-fill
    # reconcile from the actually-written receipt source (edli_no_submit_receipts,
    # joined on the real final_intent_id). Fail-soft: None on any miss, COALESCEd
    # in log_execution_fact so it never NULLs an existing link and never gates the
    # fill. Only the primary entry-fill site is wired; the exit/recovery callers
    # (exit_lifecycle / exchange_reconcile / command_recovery) legitimately leave
    # posterior_id NULL — exits/recoveries are not driven by a replacement_0_1
    # posterior and have no NO_SUBMIT receipt to join, so there is nothing to
    # carry (and COALESCE preserves the entry-set link if one later re-upserts).
    _entry_posterior_id = _posterior_id_for_final_intent(
        conn, identity["final_intent_id"]
    )

    log_execution_fact(
        conn,
        intent_id=identity["final_intent_id"] or identity["execution_command_id"] or aggregate_id,
        position_id=position_id,
        order_role="entry",
        decision_id=identity["final_intent_id"] or None,
        command_id=_actual_command_id or None,
        strategy_key=str(identity["strategy_key"]),
        # C4 telemetry-truth: posted_at = venue_commands.created_at (real
        # submit-intent time); NULL if command row absent (honest absence, so
        # latency_seconds computes NULL rather than synthetic 0.0).
        # filled_at = NULL (bridge reconcile wall-clock is not the real fill time;
        # no fill timestamp is available in the EDLI event payload).
        posted_at=_cmd_created_at,
        filled_at=None,
        submitted_price=avg_fill_price,
        fill_price=avg_fill_price,
        shares=filled_size,
        fill_quality=1.0,
        venue_status="CONFIRMED",
        terminal_exec_status="filled",
        posterior_id=_entry_posterior_id,
    )

    return {
        "position_id": position_id,
        "aggregate_id": aggregate_id,
        "shares": filled_size,
        "avg_fill_price": avg_fill_price,
        "cost_basis_usd": filled_size * avg_fill_price,
        "fees": fees,
        "direction": identity["direction"],
        "token_id": identity["token_id"],
        "condition_id": identity["condition_id"],
        "created": created,
    }


def _bridge_env() -> str:
    """Resolve the position env tag from the runtime mode (live by default)."""
    try:
        from src.config import get_mode

        mode = str(get_mode() or "").lower()
    except Exception:
        mode = ""
    if mode in {"test", "replay", "backtest", "live"}:
        return mode
    return "live"

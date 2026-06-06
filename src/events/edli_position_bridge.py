# Created: 2026-06-01
# Last reused or audited: 2026-06-03
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
from typing import Any

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

    Production: the bridge runs on a trade connection with world ATTACHed, so
    the table is ``world.edli_live_order_events``. Unit tests on a single
    ``init_schema`` connection see it unqualified. Prefer the ATTACHed world
    copy when present (that is the authoritative world_class table).
    """
    try:
        attached = {row[1] for row in conn.execute("PRAGMA database_list").fetchall()}
    except sqlite3.Error:
        attached = set()
    if "world" in attached:
        row = conn.execute(
            "SELECT 1 FROM world.sqlite_master WHERE type='table' AND name='edli_live_order_events'"
        ).fetchone()
        if row is not None:
            return "world.edli_live_order_events"
    return "edli_live_order_events"


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
        "market_id": str(pre_submit.get("market_id") or condition_id),
        "cluster": str(pre_submit.get("cluster") or city),
        "p_posterior": _float_or_none(pre_submit.get("q_live")) or 0.0,
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
        shares=filled_size,
        cost_basis_usd=cost_basis,
        condition_id=identity["condition_id"],
        decision_snapshot_id=identity["decision_snapshot_id"],
        entry_method="edli_event_driven",
        # The EDLI forecast-driven lane is the event-sourced re-implementation of
        # the settlement-capture strategy (forecast settlement edge → buy the
        # mispriced outcome). position_events.strategy_key CHECK admits only the
        # four canonical strategy keys; settlement_capture is the correct
        # semantic home. final_intent_id linkage lives on order_id / the audit.
        strategy_key="settlement_capture",
        order_id=identity.get("venue_order_id") or identity.get("execution_command_id") or "",
        order_status="filled",
        order_posted_at=filled_at,
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
    fill_payloads = _confirmed_fill_payloads(events)
    filled_size, avg_fill_price, fees = _aggregate_fill_economics(fill_payloads)
    if filled_size <= 0 or avg_fill_price <= 0:
        raise EdliPositionBridgeError(
            f"EDLI_BRIDGE_FILL_ECONOMICS_INVALID: filled_size={filled_size} avg_fill_price={avg_fill_price}"
        )

    now = now or datetime.now(timezone.utc)
    filled_at = now.isoformat()
    env = _bridge_env()

    pos = _build_bridge_position(
        aggregate_id=aggregate_id,
        identity=identity,
        filled_size=filled_size,
        avg_fill_price=avg_fill_price,
        fees=fees,
        filled_at=filled_at,
        env=env,
    )

    from src.engine.lifecycle_events import (
        ACTIVE,
        build_entry_canonical_write,
        build_position_current_projection,
    )
    from src.state.db import log_execution_fact
    from src.state.ledger import append_many_and_project, upsert_position_current

    position_id = pos.trade_id
    already_opened = _open_intent_event_exists(conn, position_id)

    if not already_opened:
        # First materialisation: POSITION_OPEN_INTENT + ENTRY_ORDER_POSTED +
        # ENTRY_ORDER_FILLED with phase ACTIVE (exact legacy entry semantics).
        events_batch, projection = build_entry_canonical_write(
            pos,
            phase_after=ACTIVE,
            source_module="src.events.edli_position_bridge",
        )
        append_many_and_project(conn, events_batch, projection)
        created = True
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

    log_execution_fact(
        conn,
        intent_id=identity["final_intent_id"] or identity["execution_command_id"] or aggregate_id,
        position_id=position_id,
        order_role="entry",
        decision_id=identity["final_intent_id"] or None,
        command_id=identity["execution_command_id"] or None,
        strategy_key="settlement_capture",
        posted_at=filled_at,
        filled_at=filled_at,
        submitted_price=avg_fill_price,
        fill_price=avg_fill_price,
        shares=filled_size,
        fill_quality=1.0,
        venue_status="CONFIRMED",
        terminal_exec_status="filled",
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
    if mode in {"test", "replay", "backtest", "shadow", "live"}:
        return mode
    return "live"

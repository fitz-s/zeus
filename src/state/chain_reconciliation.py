"""Chain reconciliation: 3 rules. Chain is truth. Portfolio is cache.

Blueprint v2 §5: Three sources of truth WILL disagree.
Chain > Chronicler > Portfolio. Always.

Rules:
1. Local + chain match → SYNCED
2. Local but NOT on chain → VOID immediately (don't ask why)
3. Chain but NOT local → QUARANTINE (low confidence, 48h forced exit eval)

Live mode: MANDATORY every cycle before any trading.
"""

import logging
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from src.state.chain_state import ChainState, classify_chain_state
from src.state.lifecycle_manager import (
    enter_chain_quarantined_runtime_state,
    phase_for_runtime_position,
    rescue_pending_runtime_state,
)
from src.state.portfolio import INACTIVE_RUNTIME_STATES, QUARANTINE_SENTINEL, Position, PortfolioState, void_position
from src.observability.counters import increment as _cnt_inc

logger = logging.getLogger(__name__)
PENDING_EXIT_STATES = frozenset({"exit_intent", "sell_placed", "sell_pending", "retry_pending"})
LIVE_TRADE_FACT_SOURCES = frozenset({"REST", "WS_USER", "WS_MARKET", "DATA_API", "CHAIN"})
FILL_TRADE_FACT_STATES = frozenset({"MATCHED", "MINED", "CONFIRMED"})

# Slice A4 (PR #19 finding 8, 2026-04-26): structural anchor for the
# learning-authority contract previously held only in resolve_rescue_authority's
# docstring. Any downstream learning consumer that ingests rescue_events_v2
# rows MUST filter on authority = LEARNING_AUTHORITY_REQUIRED to avoid silently
# misclassifying legacy / placeholder / quarantine-default rows (which are
# tagged UNVERIFIED + 'position_missing_metric:...') as VERIFIED training
# evidence. The relationship test in
# tests/test_authority_strict_learning.py scans the repo for SELECT-side
# reads of rescue_events_v2 and asserts each carries this filter.
#
# Antibody scope (post-review honesty, code-reviewer fix#4): the scanner is
# LOOSE — it accepts any `WHERE authority = ?` clause, including hardcoded
# "VERIFIED" string literals that bypass this constant. A future consumer
# can satisfy the antibody without importing LEARNING_AUTHORITY_REQUIRED.
# This is intentional: requiring the import would be a stronger antibody
# but would also reject legitimate diagnostic / one-off audit reads. Code
# reviewers reading new SELECT-from-rescue_events_v2 sites should verify
# the literal matches this constant value.
LEARNING_AUTHORITY_REQUIRED = "VERIFIED"


def resolve_position_metric(position) -> tuple[str, str, str]:
    """Peer of resolve_rescue_authority for non-rescue position-metric reads.

    Slice P2-C1 (PR #19 phase 2, 2026-04-26): consolidates the 4 silent-
    HIGH defaults at lifecycle_events.py:100, monitor_refresh.py:140/298/334
    into one helper that preserves UNVERIFIED authority tagging.

    Pre-P2-C1, those sites used `getattr(position, "temperature_metric",
    "high")` which silently substituted HIGH for missing metric WITHOUT
    informing downstream consumers. monitor_refresh.py:140 was the most
    severe case — passing the silent HIGH directly into get_calibrator,
    undermining Phase 9C L3's metric-aware calibrator gate at the entry
    seam (a LOW position with missing metric received the HIGH Platt
    model silently).

    Return shape mirrors resolve_rescue_authority exactly so analytics
    consumers can apply the same authority='VERIFIED' filter pattern.
    Emits a DEBUG log when the UNVERIFIED default fires so operators can
    audit which positions are being defaulted.

    Returns:
        (metric, authority, source)
        - metric: str — "high" or "low" (always in domain; defaults to "high"
          for backward compat with legacy positions whose metric was never set).
        - authority: str — "VERIFIED" iff metric was materialized from a valid
          (high|low) value; "UNVERIFIED" otherwise.
        - source: str — provenance string for forensic filtering.
    """
    # Slice P2-fix4 (post-review MAJOR #4 from code-reviewer, 2026-04-26):
    # type guard for the most common caller-bug case (passing None instead
    # of a Position). Without this guard, None inputs would silently produce
    # the same audit-log line as a legitimate quarantine-default Position,
    # making programmer errors invisible to ops review. dict / attribute-
    # less objects fall through to the UNVERIFIED default below — they
    # surface in DEBUG audit logs as `position_missing_metric:None`.
    if position is None:
        raise TypeError(
            "resolve_position_metric expected a Position-like object; got None"
        )
    _raw_metric = getattr(position, "temperature_metric", None)
    if _raw_metric in ("high", "low"):
        return (_raw_metric, "VERIFIED", "position_materialized")
    logger.debug(
        "resolve_position_metric: defaulting to HIGH+UNVERIFIED for "
        "position trade_id=%s raw_metric=%r",
        getattr(position, "trade_id", "?"),
        _raw_metric,
    )
    return ("high", "UNVERIFIED", f"position_missing_metric:{_raw_metric!r}")


def resolve_rescue_authority(position) -> tuple[str, str, str]:
    """Resolve (temperature_metric, authority, authority_source) for a
    rescue_events_v2 row from a Position object.

    Slice P2-fix4 (post-review MAJOR #3 from code-reviewer, 2026-04-26):
    delegates to resolve_position_metric. Pre-fix4 had byte-identical
    semantics maintained as two separate function bodies + a symmetry
    test pinning their equivalence — textbook DRY violation. Now one
    canonical implementation; the two names remain for caller-context
    documentation (rescue vs non-rescue read sites).

    SD-1 (binary temperature_metric) + SD-H (provenance on authority):
    - Position materialized through materialize_position() carries a valid
      temperature_metric in {"high", "low"} → VERIFIED + "position_materialized".
    - Position with missing, None, empty, or out-of-domain temperature_metric
      (quarantine placeholder, stale JSON reconstruction, legacy row) falls
      back to the SD-1 default "high" but MUST be tagged UNVERIFIED with a
      concrete authority_source so downstream analytics can filter on
      authority='VERIFIED' for strict forensic work.

    This helper is the single source of truth for the authority rule. Both
    `_emit_rescue_event` (live path) and B063 tests import this function to
    avoid logic drift between prod and test (B063 P1 fix per critic review).
    """
    return resolve_position_metric(position)


@dataclass
class ChainPosition:
    """On-chain position data from CLOB API."""
    token_id: str
    size: float
    avg_price: float
    cost: float = 0.0
    condition_id: str = ""


@dataclass(frozen=True)
class ChainPositionView:
    """Immutable per-cycle snapshot of chain state.

    Built once per cycle from chain API. All downstream code reads from this
    snapshot, never from live API calls mid-cycle. Prevents inconsistent reads
    when chain state changes during a cycle.

    Fix D (Option 4b): The `state: ChainState` field has been removed.
    Classification is a per-reconcile-call fact computed by classify_chain_state()
    inside reconcile(), not something cached on the view. No external caller
    outside reconcile() was found to read a `.state` field on this view.
    """
    positions: tuple  # tuple of ChainPosition (frozen requires immutable)
    fetched_at: str = ""
    is_stale: bool = False

    @staticmethod
    def from_chain_positions(
        chain_positions: list[ChainPosition],
        fetched_at: str = "",
    ) -> "ChainPositionView":
        return ChainPositionView(
            positions=tuple(chain_positions),
            fetched_at=fetched_at or datetime.now(timezone.utc).isoformat(),
        )

    @staticmethod
    def empty() -> "ChainPositionView":
        return ChainPositionView(positions=(), is_stale=True)

    def _by_token(self) -> dict:
        return {cp.token_id: cp for cp in self.positions}

    def has_token(self, token_id: str) -> bool:
        return any(cp.token_id == token_id for cp in self.positions)

    def get_position(self, token_id: str):
        for cp in self.positions:
            if cp.token_id == token_id:
                return cp
        return None


_ALLOCATE_DUST = 0.01  # minimum size difference treated as dust, not a gap


def allocate_chain_truth(
    positions: list,
    chain_balance: float,
    dust: float = _ALLOCATE_DUST,
    policy: str = "LIFO",
) -> tuple[list, list]:
    """Allocate chain backing to local positions using LIFO by entered_at.

    Returns (allocated, phantom) where:
    - allocated: positions backed by the chain balance (chain is truth for them)
    - phantom: positions the chain balance cannot cover (aggregate phantoms)

    Policy: LIFO — most-recently-entered positions have priority for backing.
    Rationale: exits are typically of newer positions; LIFO matches observed
    Polymarket exit fill ordering.

    Whole-position semantics only: a position is either fully backed or phantom.
    Fractional backing is not supported (deferred, DQ-1/DQ-2).
    """
    if policy != "LIFO":
        raise ValueError(f"allocate_chain_truth: unsupported policy {policy!r}")

    # Sort descending by entered_at (LIFO: newest first).
    # Positions with empty entered_at sort last (oldest).
    def _sort_key(p):
        ea = getattr(p, "entered_at", "") or ""
        return ea  # ISO strings sort lexicographically = chronologically

    sorted_positions = sorted(positions, key=_sort_key, reverse=True)

    allocated: list = []
    phantom: list = []
    remaining = chain_balance

    for pos in sorted_positions:
        size = float(getattr(pos, "effective_shares", None) or getattr(pos, "shares", 0) or 0)
        if size <= 0:
            # Zero-size position: treat as backed (no chain needed)
            allocated.append(pos)
            continue
        if remaining >= size - dust:
            allocated.append(pos)
            # Consume exactly what remains (not full size) when within dust
            # tolerance — prevents remaining going negative and incorrectly
            # backing later positions in the LIFO walk.
            remaining = max(0.0, remaining - size)
        else:
            phantom.append(pos)

    return allocated, phantom


def reconcile(portfolio: PortfolioState, chain_positions: list[ChainPosition], conn=None) -> dict:
    """Three rules. No reasoning about WHY. Chain is truth.

    Returns: {"synced": int, "voided": int, "quarantined": int}

    Safety: if chain returns 0 positions but local has N, the API likely
    returned incomplete data. Skip voiding to prevent false PHANTOM kills.
    """
    update_trade_lifecycle = None
    if conn is not None:
        from src.state.db import update_trade_lifecycle

    def _next_canonical_sequence_no(position_id: str) -> int:
        if conn is None:
            return 1
        try:
            row = conn.execute(
                "SELECT COALESCE(MAX(sequence_no), 0) FROM position_events WHERE position_id = ?",
                (position_id,),
            ).fetchone()
        except Exception:
            return 1
        return int(row[0] or 0) + 1

    def _has_canonical_position_history(position_id: str) -> bool:
        if conn is None:
            return False
        try:
            row = conn.execute(
                "SELECT 1 FROM position_events WHERE position_id = ? LIMIT 1",
                (position_id,),
            ).fetchone()
        except Exception:
            return False
        return row is not None

    def _canonical_rescue_baseline_available(position_id: str) -> bool:
        if conn is None:
            return False
        try:
            row = conn.execute(
                "SELECT phase FROM position_current WHERE position_id = ?",
                (position_id,),
            ).fetchone()
        except Exception:
            return False
        if row is None:
            if _has_canonical_position_history(position_id):
                raise RuntimeError("canonical rescue baseline missing current projection")
            return False
        phase = str(row[0] or "")
        if phase != "pending_entry":
            raise RuntimeError(f"canonical rescue baseline phase mismatch: expected pending_entry, got {phase!r}")
        return True

    def _positive_decimal(value) -> bool:
        if value is None:
            return False
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, ValueError):
            return False
        return parsed.is_finite() and parsed > 0

    def _canonical_current_shares(position_id: str) -> float | None:
        if conn is None:
            return None
        try:
            row = conn.execute(
                "SELECT shares FROM position_current WHERE position_id = ?",
                (position_id,),
            ).fetchone()
        except Exception:
            return None
        if row is None:
            return None
        value = row["shares"] if hasattr(row, "keys") else row[0]
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None
        if not parsed.is_finite():
            return None
        return float(parsed)

    def _pending_entry_has_durable_command(position: Position) -> bool:
        if conn is None:
            return False
        order_id = str(getattr(position, "entry_order_id", "") or getattr(position, "order_id", "") or "").strip()
        if not order_id:
            return False
        try:
            row = conn.execute(
                """
                SELECT 1
                  FROM venue_commands
                 WHERE venue_order_id = ?
                   AND intent_kind = 'ENTRY'
                 LIMIT 1
                """,
                (order_id,),
            ).fetchone()
        except Exception as exc:
            raise RuntimeError(
                f"pending-entry command lookup failed for order_id={order_id}"
            ) from exc
        return row is not None

    def _pending_entry_has_linked_fill_fact(position: Position) -> bool:
        if conn is None:
            return False
        order_id = str(getattr(position, "entry_order_id", "") or getattr(position, "order_id", "") or "").strip()
        if not order_id:
            return False
        try:
            rows = conn.execute(
                """
                SELECT state, source, filled_size, fill_price
                  FROM venue_trade_facts
                 WHERE venue_order_id = ?
                 ORDER BY observed_at DESC, local_sequence DESC
                """,
                (order_id,),
            ).fetchall()
        except Exception:
            return False
        for row in rows:
            state = str(row["state"] if hasattr(row, "keys") else row[0])
            source = str(row["source"] if hasattr(row, "keys") else row[1])
            filled_size = row["filled_size"] if hasattr(row, "keys") else row[2]
            fill_price = row["fill_price"] if hasattr(row, "keys") else row[3]
            if (
                state in FILL_TRADE_FACT_STATES
                and source in LIVE_TRADE_FACT_SOURCES
                and _positive_decimal(filled_size)
                and _positive_decimal(fill_price)
            ):
                return True
        return False

    def _canonical_size_correction_baseline_available(position_id: str, *, expected_phase: str) -> bool:
        if conn is None:
            return False
        try:
            row = conn.execute(
                "SELECT phase FROM position_current WHERE position_id = ?",
                (position_id,),
            ).fetchone()
        except Exception:
            return False
        if row is None:
            if _has_canonical_position_history(position_id):
                raise RuntimeError("canonical size-correction baseline missing current projection")
            return False
        phase = str(row[0] or "")
        if phase != expected_phase:
            raise RuntimeError(
                f"canonical size-correction baseline phase mismatch: expected {expected_phase!r}, got {phase!r}"
            )
        return True

    def _append_canonical_rescue_if_available(position: Position) -> bool:
        if conn is None:
            return False
        if not _canonical_rescue_baseline_available(getattr(position, "trade_id", "")):
            return False

        from src.engine.lifecycle_events import build_reconciliation_rescue_canonical_write
        from src.state.db import append_many_and_project

        try:
            events, projection = build_reconciliation_rescue_canonical_write(
                position,
                sequence_no=_next_canonical_sequence_no(getattr(position, "trade_id", "")),
                source_module="src.state.chain_reconciliation",
            )
            append_many_and_project(conn, events, projection)
        except Exception as exc:
            raise RuntimeError(
                f"canonical reconciliation rescue dual-write failed for {position.trade_id}: {exc}"
            ) from exc

        return True

    def _append_canonical_size_correction_if_available(
        position: Position,
        *,
        local_shares_before: float,
    ) -> bool:
        if conn is None:
            return False
        # Race: if the fill just landed, the position is still in pending_entry
        # phase when chain reconciliation runs. The fill event will set the
        # correct size in its own path — skip canonical size correction here
        # to avoid colliding with fill detection. On the next cycle the phase
        # will be 'active' and real size corrections can proceed normally.
        try:
            _phase_row = conn.execute(
                "SELECT phase FROM position_current WHERE position_id = ?",
                (getattr(position, "trade_id", ""),),
            ).fetchone()
        except Exception:
            _phase_row = None
        if _phase_row is not None and str(_phase_row[0] or "") == "pending_entry":
            return False
        expected_phase = "day0_window" if getattr(position, "day0_entered_at", "") else "active"
        if not _canonical_size_correction_baseline_available(
            getattr(position, "trade_id", ""),
            expected_phase=expected_phase,
        ):
            return False

        from src.engine.lifecycle_events import build_chain_size_corrected_canonical_write
        from src.state.db import append_many_and_project

        try:
            events, projection = build_chain_size_corrected_canonical_write(
                position,
                local_shares_before=local_shares_before,
                sequence_no=_next_canonical_sequence_no(getattr(position, "trade_id", "")),
                source_module="src.state.chain_reconciliation",
            )
            append_many_and_project(conn, events, projection)
        except Exception as exc:
            raise RuntimeError(
                f"canonical reconciliation size-correction dual-write failed for {position.trade_id}: {exc}"
            ) from exc

        return True

    def _already_logged_rescue_event(position) -> bool:
        """Check canonical position_events for a prior rescue event."""
        if conn is None:
            return False
        try:
            row = conn.execute(
                """
                SELECT 1 FROM position_events
                WHERE position_id = ?
                  AND source_module LIKE '%chain_reconciliation%'
                LIMIT 1
                """,
                (getattr(position, 'trade_id', ''),),
            ).fetchone()
            return row is not None
        except Exception:
            return False

    def _emit_rescue_event(position, *, rescued_at: str) -> None:
        # Bug #54: log rescue for observability (canonical write is in
        # _append_canonical_rescue_if_available).
        logger.info(
            "RESCUE: %s rescued at %s (chain_state=%s, shares=%.4f, entry=%.4f)",
            getattr(position, "trade_id", "?"),
            rescued_at,
            getattr(position, "chain_state", "?"),
            getattr(position, "shares", 0.0),
            getattr(position, "entry_price", 0.0),
        )
        if conn is not None:
            import json
            from src.state.db import log_rescue_event
            # B063 P1 fix: unify occurred_at across the legacy
            # CHAIN_RESCUE_AUDIT row and the new rescue_events_v2 row so
            # post-mortem JOINs on occurred_at correlate correctly and
            # the v2 UNIQUE(trade_id, occurred_at) key matches whatever
            # the legacy row recorded. Capture once, use twice.
            _rescue_ts = datetime.now(timezone.utc).isoformat()
            try:
                columns = {
                    str(row[1])
                    for row in conn.execute("PRAGMA table_info(position_events)").fetchall()
                }
                if "payload" in columns:
                    insert_columns = [
                        "position_id",
                        "sequence_no",
                        "event_type",
                        "occurred_at",
                        "payload",
                        "source_module",
                    ]
                    values = [
                        getattr(position, "trade_id", ""),
                        _next_canonical_sequence_no(getattr(position, "trade_id", "")),
                        "CHAIN_RESCUE_AUDIT",
                        _rescue_ts,
                        json.dumps({
                            "chain_state": getattr(position, "chain_state", "?"),
                            "shares": getattr(position, "shares", 0.0),
                            "entry_price": getattr(position, "entry_price", 0.0)
                        }),
                        "src.state.chain_reconciliation_audit",
                    ]
                    if "env" in columns:
                        insert_columns.append("env")
                        values.append(str(getattr(position, "env", "unknown_env") or "unknown_env"))
                    conn.execute(
                        f"""
                        INSERT INTO position_events ({", ".join(insert_columns)})
                        VALUES ({", ".join(["?"] * len(insert_columns))})
                        """,
                        tuple(values),
                    )
            except Exception as e:
                logger.warning(f"Failed to durability-log legacy rescue event: {e}")
            try:
                # B063: append the Phase 2 v2 audit row independently of the
                # legacy CHAIN_RESCUE_AUDIT row. Canonical position_events no
                # longer accepts the legacy event shape, and that compatibility
                # miss must not suppress the structured authority-bearing row.
                _metric, _authority, _authority_source = resolve_rescue_authority(position)
                log_rescue_event(
                    conn,
                    trade_id=getattr(position, "trade_id", ""),
                    position_id=getattr(position, "trade_id", None),
                    chain_state=str(getattr(position, "chain_state", "?")),
                    reason="chain_reconciliation_rescue",
                    occurred_at=_rescue_ts,
                    temperature_metric=_metric,
                    causality_status="UNKNOWN",
                    authority=_authority,
                    authority_source=_authority_source,
                )
                # INFO(DT#1): rescue_events_v2 is an authoritative audit
                # record, not a derived export, so durability is allowed here.
                conn.commit()
            except Exception as e:
                logger.error(f"Failed to durability-log rescue event: {e}")

    def _sync_reconciled_trade_lifecycle(position) -> None:
        if update_trade_lifecycle is None:
            return
        try:
            update_trade_lifecycle(conn, position)
        except Exception as exc:
            raise RuntimeError(
                f"reconciliation lifecycle sync failed for {position.trade_id}: {exc}"
            ) from exc

    def _sync_voided_position(
        position,
        *,
        phase_before: str | None,
        reason: str,
        token_id: str,
    ) -> None:
        if conn is None:
            return
        try:
            import json

            from src.engine.lifecycle_events import build_position_current_projection
            from src.state.db import append_many_and_project

            trade_id = str(getattr(position, "trade_id", "") or "")
            if not trade_id:
                return
            occurred_at = getattr(position, "last_exit_at", "") or datetime.now(timezone.utc).isoformat()
            sequence_no = _next_canonical_sequence_no(trade_id)
            projection = build_position_current_projection(position)
            projection["updated_at"] = occurred_at
            if projection.get("phase") != "voided":
                raise RuntimeError(
                    f"void projection for {trade_id} resolved to {projection.get('phase')!r}"
                )
            env = str(getattr(position, "env", "") or "live")
            if env not in {"live", "test", "replay", "backtest", "shadow"}:
                env = "live"
            event = {
                "event_id": f"{trade_id}:chain_void:{sequence_no}",
                "position_id": trade_id,
                "event_version": 1,
                "sequence_no": sequence_no,
                "event_type": "ADMIN_VOIDED",
                "occurred_at": occurred_at,
                "phase_before": phase_before,
                "phase_after": "voided",
                "strategy_key": str(
                    getattr(position, "strategy_key", "")
                    or getattr(position, "strategy", "")
                    or ""
                ),
                "decision_id": None,
                "snapshot_id": getattr(position, "decision_snapshot_id", "") or None,
                "order_id": getattr(position, "order_id", "") or None,
                "command_id": None,
                "caused_by": "chain_reconciliation",
                "idempotency_key": f"{trade_id}:chain_void:{sequence_no}",
                "venue_status": "voided",
                "source_module": "src.state.chain_reconciliation",
                "env": env,
                "payload_json": json.dumps(
                    {
                        "reason": reason,
                        "token_id": token_id,
                        "chain_state": getattr(position, "chain_state", ""),
                    },
                    default=str,
                    sort_keys=True,
                ),
            }
            append_many_and_project(conn, [event], projection)
        except Exception as exc:
            raise RuntimeError(
                f"canonical phantom void sync failed for {getattr(position, 'trade_id', '?')}: {exc}"
            ) from exc

    chain_by_token = {cp.token_id: cp for cp in chain_positions}
    local_tokens = set()
    stats = {
        "synced": 0,
        "voided": 0,
        "quarantined": 0,
        "updated": 0,
        "skipped_pending": 0,
        "rescued_pending": 0,
    }
    now = datetime.now(timezone.utc).isoformat()

    def _pending_exit_owned_by_exit_lifecycle(position: Position) -> bool:
        return (
            getattr(position, "state", "") == "pending_exit"
            or getattr(position, "exit_state", "") in PENDING_EXIT_STATES
        )

    def _persist_chain_only_quarantine_fact(token_id: str, chain: ChainPosition) -> None:
        if conn is None:
            return
        from src.state.db import record_token_suppression

        try:
            result = record_token_suppression(
                conn,
                token_id=token_id,
                condition_id=chain.condition_id,
                suppression_reason="chain_only_quarantined",
                source_module="src.state.chain_reconciliation",
                evidence={
                    "size": chain.size,
                    "avg_price": chain.avg_price,
                    "cost": chain.cost or (chain.size * chain.avg_price),
                    "condition_id": chain.condition_id,
                    "first_seen_at": now,
                },
            )
        except Exception as exc:
            raise RuntimeError(
                f"chain-only quarantine fact write failed for {token_id}: {exc}"
            ) from exc
        if result.get("status") != "written":
            raise RuntimeError(
                f"chain-only quarantine fact write failed for {token_id}: {result}"
            )

    # DT#4 / INV-18: derive three-state from inputs at the TOP of reconcile().
    # reconcile() is only called when the chain API responded (cycle_runtime.py
    # raises if api_positions is None). Treat the call timestamp as fetched_at.
    # Fix E: fetched_at=now is correct here — reconcile() is only called after
    # the chain API returns a non-None response, so the fetch itself is fresh.
    # CHAIN_UNKNOWN reachability inside reconcile is exclusively via the
    # empty-chain-with-recent-local-verified branch of classify_chain_state.
    chain_state: ChainState = classify_chain_state(
        fetched_at=now,  # API responded (non-None) — use current timestamp
        chain_positions=chain_positions,
        portfolio=portfolio,
    )
    if chain_state == ChainState.CHAIN_UNKNOWN:
        logger.warning(
            "INCOMPLETE CHAIN RESPONSE: classify_chain_state=CHAIN_UNKNOWN. "
            "Skipping Rule 2 (void) to prevent false PHANTOM kills.",
        )
        stats["skipped_void_incomplete_api"] = sum(
            1 for p in portfolio.positions
            if p.state != "pending_tracked"
            and p.state not in INACTIVE_RUNTIME_STATES
            and (p.token_id if p.direction == "buy_yes" else p.no_token_id)
        )

    # ── Pass-1: aggregate reconciliation per token (Bug #3, PR-S1) ──────────
    # Group active positions by token_id, allocate chain backing LIFO.
    # Skipped when chain state is UNKNOWN (suspect API response).
    # Positions that the chain cannot cover are marked phantom and voided in
    # pass-2 (the per-position loop below) using PHANTOM_NOT_ON_CHAIN.
    phantom_set: set[str] = set()
    # aggregate_backed_set: trade_ids confirmed backed by allocate_chain_truth.
    # These must bypass per-position size-mismatch correction: their individual
    # shares are correct; chain.size is the AGGREGATE across all lots for the
    # token, not the per-lot size. Comparing chain.size vs pos.effective_shares
    # for an aggregate-backed lot would trigger false quarantine (bot PR #141).
    aggregate_backed_set: set[str] = set()
    if chain_state != ChainState.CHAIN_UNKNOWN:
        _token_to_positions: dict[str, list] = {}
        for _p in portfolio.positions:
            _tid = _p.token_id if _p.direction == "buy_yes" else _p.no_token_id
            if not _tid:
                continue
            _state_val = getattr(_p.state, "value", _p.state)
            if _state_val in INACTIVE_RUNTIME_STATES or _state_val == "pending_tracked":
                continue
            # Exclude positions with an active exit in flight — exit_lifecycle owns them.
            if (
                _state_val == "pending_exit"
                or getattr(_p, "exit_state", "") in PENDING_EXIT_STATES
            ):
                continue
            # Exclude positions awaiting chain propagation (entry verified but chain
            # record not yet visible). The existing reconcile awaiting_chain_entry
            # branch handles them; premature phantom-marking would void prematurely.
            if (
                getattr(_p, "entry_fill_verified", False)
                and getattr(_p, "chain_state", "") in {"local_only", "unknown"}
            ):
                continue
            _token_to_positions.setdefault(_tid, []).append(_p)

        for _tid, _positions in _token_to_positions.items():
            _chain_cp = chain_by_token.get(_tid)
            _chain_bal = _chain_cp.size if _chain_cp is not None else 0.0
            _allocated, _phantoms = allocate_chain_truth(_positions, _chain_bal)
            for _ph in _phantoms:
                phantom_set.add(_ph.trade_id)
            # Only mark aggregate-backed when there are multiple lots for this
            # token; single-lot positions are correctly compared against chain.size.
            if len(_positions) > 1:
                for _al in _allocated:
                    aggregate_backed_set.add(_al.trade_id)
    # ────────────────────────────────────────────────────────────────────────

    for pos in list(portfolio.positions):
        tid = pos.token_id if pos.direction == "buy_yes" else pos.no_token_id
        if not tid:
            if pos.state == "pending_tracked":
                stats["skipped_pending"] += 1
            continue

        # Pass-2: skip aggregate-phantom positions — chain cannot back them.
        # Void using the existing PHANTOM_NOT_ON_CHAIN state (no new enum).
        if pos.trade_id in phantom_set:
            logger.warning(
                "AGGREGATE_PHANTOM: trade_id=%s token=%s voided by chain aggregate reconciliation",
                pos.trade_id,
                tid,
            )
            phase_before = phase_for_runtime_position(
                state=getattr(pos, "state", ""),
                exit_state=getattr(pos, "exit_state", ""),
                chain_state=getattr(pos, "chain_state", ""),
            ).value
            if phase_before == "unknown":
                phase_before = None
            voided = void_position(portfolio, pos.trade_id, "PHANTOM_NOT_ON_CHAIN")
            if voided is not None:
                _sync_voided_position(
                    voided,
                    phase_before=phase_before,
                    reason="PHANTOM_NOT_ON_CHAIN",
                    token_id=tid,
                )
            stats["voided"] += 1
            stats["aggregate_phantom_voided"] = stats.get("aggregate_phantom_voided", 0) + 1
            continue

        state_name = getattr(pos.state, "value", pos.state)
        if state_name in {"quarantined"}:
            local_tokens.add(tid)
        elif state_name not in INACTIVE_RUNTIME_STATES:
            local_tokens.add(tid)

        if pos.state in INACTIVE_RUNTIME_STATES:
            state_name = getattr(pos.state, "value", pos.state)
            key = f"skipped_{state_name}"
            stats[key] = stats.get(key, 0) + 1
            continue

        chain = chain_by_token.get(tid)
        if pos.state == "pending_tracked":
            if chain is None:
                stats["skipped_pending"] += 1
                continue
            canonical_rescue_baseline_available = _canonical_rescue_baseline_available(getattr(pos, "trade_id", ""))
            if not canonical_rescue_baseline_available:
                stats["skipped_pending"] += 1
                stats["skipped_pending_missing_canonical_baseline"] = stats.get("skipped_pending_missing_canonical_baseline", 0) + 1
                continue
            if (
                _pending_entry_has_durable_command(pos)
                and not _pending_entry_has_linked_fill_fact(pos)
            ):
                stats["skipped_pending"] += 1
                stats["skipped_pending_missing_fill_fact"] = stats.get("skipped_pending_missing_fill_fact", 0) + 1
                continue
            rescued = replace(pos)
            rescued.entry_order_id = rescued.entry_order_id or rescued.order_id or ""
            rescued.order_id = rescued.order_id or rescued.entry_order_id or ""
            rescued.chain_state = "synced"
            rescued.chain_shares = chain.size
            rescued.chain_verified_at = now
            rescued.condition_id = rescued.condition_id or chain.condition_id
            _rescue_eligible = getattr(pos, "corrected_executable_economics_eligible", False)
            if chain.avg_price > 0:
                if not _rescue_eligible:
                    rescued.entry_price = chain.avg_price
                else:
                    _cnt_inc("cost_basis_chain_mutation_blocked_total", labels={"field": "entry_price"})
                    logger.warning("telemetry_counter event=cost_basis_chain_mutation_blocked_total field=entry_price")
            if chain.cost > 0:
                if not _rescue_eligible:
                    rescued.cost_basis_usd = chain.cost
                    rescued.size_usd = chain.cost
                else:
                    _cnt_inc("cost_basis_chain_mutation_blocked_total", labels={"field": "cost_basis_usd"})
                    logger.warning("telemetry_counter event=cost_basis_chain_mutation_blocked_total field=cost_basis_usd")
                    _cnt_inc("cost_basis_chain_mutation_blocked_total", labels={"field": "size_usd"})
                    logger.warning("telemetry_counter event=cost_basis_chain_mutation_blocked_total field=size_usd")
            if chain.size > 0:
                if not _rescue_eligible:
                    rescued.shares = chain.size
                else:
                    _cnt_inc("cost_basis_chain_mutation_blocked_total", labels={"field": "shares"})
                    logger.warning("telemetry_counter event=cost_basis_chain_mutation_blocked_total field=shares")
            rescued.entry_fill_verified = True
            rescued.order_status = "filled"
            rescued.state = rescue_pending_runtime_state(
                rescued.state,
                exit_state=getattr(rescued, "exit_state", ""),
                chain_state=getattr(rescued, "chain_state", ""),
            )
            if not rescued.entered_at:
                # B064: entered_at is fabricated because the pending position
                # arrived at rescue with no real entry timestamp. Emit a
                # structured warning so operators can notice + backfill, and
                # avoid feeding the sentinel into temporal consumers below.
                logger.warning(
                    "ENTERED_AT_FABRICATED: trade_id=%s token=%s chain_state=%s rescued_at=%s",
                    getattr(rescued, "trade_id", "?"),
                    tid,
                    getattr(rescued, "chain_state", "?"),
                    now,
                )
                rescued.entered_at = now  # `now` already in scope; line 668 uses it as _rescue_display_ts
                _entered_at_was_fabricated = True
            else:
                _entered_at_was_fabricated = False
            if canonical_rescue_baseline_available:
                _append_canonical_rescue_if_available(rescued)
            _sync_reconciled_trade_lifecycle(rescued)
            # B064: when entered_at is the fabrication sentinel, the rescue
            # event's display timestamp must be the reconcile `now`, not the
            # sentinel string.
            _rescue_display_ts = now if _entered_at_was_fabricated else (rescued.entered_at or now)
            _emit_rescue_event(rescued, rescued_at=_rescue_display_ts)
            pos.entry_order_id = rescued.entry_order_id
            pos.order_id = rescued.order_id
            pos.chain_state = rescued.chain_state
            pos.chain_shares = rescued.chain_shares
            pos.chain_verified_at = rescued.chain_verified_at
            pos.condition_id = rescued.condition_id
            pos.entry_price = rescued.entry_price
            pos.cost_basis_usd = rescued.cost_basis_usd
            pos.size_usd = rescued.size_usd
            pos.shares = rescued.shares
            pos.entry_fill_verified = rescued.entry_fill_verified
            pos.order_status = rescued.order_status
            pos.state = rescued.state
            pos.entered_at = rescued.entered_at
            stats["rescued_pending"] += 1
            stats["synced"] += 1
            continue

        if chain is None:
            if chain_state == ChainState.CHAIN_UNKNOWN:
                continue  # Don't void — API response is suspect
            if (
                getattr(pos, "entry_fill_verified", False)
                and pos.chain_state in {"local_only", "unknown"}
                and pos.state in {"entered", "holding", "day0_window"}
            ):
                pos.chain_state = "local_only"
                pos.chain_verified_at = now
                stats["awaiting_chain_entry"] = stats.get("awaiting_chain_entry", 0) + 1
                continue
            if _pending_exit_owned_by_exit_lifecycle(pos):
                logger.info(
                    "EXIT IN FLIGHT: %s missing on chain while exit_state=%s; "
                    "deferring phantom decision to exit_lifecycle",
                    pos.trade_id,
                    pos.exit_state,
                )
                pos.chain_state = "exit_pending_missing"
                pos.chain_verified_at = now
                stats["skipped_pending_exit"] = stats.get("skipped_pending_exit", 0) + 1
                continue
            # Rule 2: Local but NOT on chain → VOID immediately
            logger.warning("PHANTOM: %s not on chain → voiding", pos.trade_id)
            phase_before = phase_for_runtime_position(
                state=getattr(pos, "state", ""),
                exit_state=getattr(pos, "exit_state", ""),
                chain_state=getattr(pos, "chain_state", ""),
            ).value
            if phase_before == "unknown":
                phase_before = None
            voided = void_position(portfolio, pos.trade_id, "PHANTOM_NOT_ON_CHAIN")
            if voided is not None:
                _sync_voided_position(
                    voided,
                    phase_before=phase_before,
                    reason="PHANTOM_NOT_ON_CHAIN",
                    token_id=tid,
                )
            stats["voided"] += 1
        else:
            runtime_local_shares = pos.effective_shares
            canonical_local_shares = _canonical_current_shares(pos.trade_id)
            local_shares = (
                canonical_local_shares
                if canonical_local_shares is not None
                else runtime_local_shares
            )
            corrected = replace(pos)
            if canonical_local_shares is not None:
                corrected.shares = canonical_local_shares
            corrected.chain_state = "synced"
            corrected.chain_shares = chain.size
            corrected.chain_verified_at = now
            corrected.condition_id = corrected.condition_id or chain.condition_id
            _size_mismatch_eligible = getattr(pos, "corrected_executable_economics_eligible", False)
            if chain.avg_price > 0:
                if not _size_mismatch_eligible:
                    corrected.entry_price = chain.avg_price
                else:
                    _cnt_inc("cost_basis_chain_mutation_blocked_total", labels={"field": "entry_price"})
                    logger.warning("telemetry_counter event=cost_basis_chain_mutation_blocked_total field=entry_price")
            if chain.cost > 0:
                if not _size_mismatch_eligible:
                    corrected.cost_basis_usd = chain.cost
                    corrected.size_usd = chain.cost
                else:
                    _cnt_inc("cost_basis_chain_mutation_blocked_total", labels={"field": "cost_basis_usd"})
                    logger.warning("telemetry_counter event=cost_basis_chain_mutation_blocked_total field=cost_basis_usd")
                    _cnt_inc("cost_basis_chain_mutation_blocked_total", labels={"field": "size_usd"})
                    logger.warning("telemetry_counter event=cost_basis_chain_mutation_blocked_total field=size_usd")
            if pos.trade_id in aggregate_backed_set:
                # Aggregate-backed: chain.size is the token aggregate across multiple
                # lots; comparing it against this lot's shares would produce a false
                # SIZE MISMATCH and quarantine. Allocation confirmed chain coverage;
                # preserve local share count (bot finding PR #141).
                logger.debug(
                    "AGGREGATE_BACKED: %s skipping size-mismatch check (chain agg=%.4f, lot=%.4f)",
                    pos.trade_id,
                    chain.size,
                    local_shares,
                )
            elif abs(chain.size - local_shares) > 0.01:
                logger.warning("SIZE MISMATCH: %s local %.4f vs chain %.4f", pos.trade_id, local_shares, chain.size)
                if not _size_mismatch_eligible:
                    corrected.shares = chain.size
                else:
                    _cnt_inc("cost_basis_chain_mutation_blocked_total", labels={"field": "shares"})
                    logger.warning("telemetry_counter event=cost_basis_chain_mutation_blocked_total field=shares")
                if not _append_canonical_size_correction_if_available(
                    corrected,
                    local_shares_before=local_shares,
                ):
                    logger.warning(
                        "SIZE MISMATCH UNRESOLVED: %s — no canonical baseline for correction "
                        "(local=%.4f, chain=%.4f); quarantining position",
                        pos.trade_id, local_shares, chain.size,
                    )
                    corrected.state = "quarantine_size_mismatch"
                    corrected.chain_state = "size_mismatch_unresolved"
                    if not _size_mismatch_eligible:
                        corrected.shares = local_shares
                    else:
                        _cnt_inc("cost_basis_chain_mutation_blocked_total", labels={"field": "shares"})
                        logger.warning("telemetry_counter event=cost_basis_chain_mutation_blocked_total field=shares")
                    stats["skipped_size_correction_missing_canonical_baseline"] = (
                        stats.get("skipped_size_correction_missing_canonical_baseline", 0) + 1
                    )
                else:
                    stats["updated"] += 1
            pos.chain_state = corrected.chain_state
            pos.chain_shares = corrected.chain_shares
            pos.chain_verified_at = corrected.chain_verified_at
            pos.condition_id = corrected.condition_id
            pos.entry_price = corrected.entry_price
            pos.cost_basis_usd = corrected.cost_basis_usd
            pos.size_usd = corrected.size_usd
            pos.shares = corrected.shares
            pos.state = corrected.state
            stats["synced"] += 1

    # Rule 3: Chain but NOT local → QUARANTINE (skip ignored tokens)
    ignored = set(getattr(portfolio, "ignored_tokens", []) or [])
    for tid, chain in chain_by_token.items():
        if tid in ignored:
            continue  # Token was explicitly acknowledged/resolved or redeemed/expired — don't resurrect
        if tid not in local_tokens:
            logger.warning(
                "QUARANTINE EXCLUDED FROM CANONICAL MIGRATION: chain token %s...%s not in portfolio; pending future governance design",
                tid[:8],
                tid[-4:],
            )
            quarantine_pos = Position(
                # B066: synthesize IDs with an explicit QUARANTINE_SENTINEL
                # value rather than empty strings. Empty-string trade_id /
                # market_id can collide with degraded-but-live positions
                # elsewhere (e.g. pre-fill pending state where the venue
                # order_id has not yet been returned). Using the same
                # sentinel already adopted by portfolio.py void_position()
                # for city/target_date/bin_label keeps the quarantine-vs-
                # real classification deterministic: downstream consumers
                # can match on ``is_quarantine_placeholder`` OR on any of
                # these sentinel-valued identifier fields.
                trade_id=QUARANTINE_SENTINEL,
                market_id=QUARANTINE_SENTINEL,
                city=QUARANTINE_SENTINEL, cluster=QUARANTINE_SENTINEL,
                target_date=QUARANTINE_SENTINEL, bin_label=QUARANTINE_SENTINEL,
                direction="unknown",
                size_usd=0.0,
                entry_price=0.0,
                p_posterior=0.0,
                edge=0.0,
                entered_at="unknown_entered_at",  # QUARANTINE_SENTINEL pattern: all fields are sentinel; intentional (distinct from line-658 bug fixed in F8). Do NOT replace with `now`.
                token_id=tid,
                state=enter_chain_quarantined_runtime_state(),
                strategy="",
                edge_source="",
                cost_basis_usd=chain.cost or (chain.size * chain.avg_price),
                shares=chain.size,
                chain_state="quarantined",
                chain_shares=chain.size,
                chain_verified_at=now,
                condition_id=chain.condition_id,
                quarantined_at=now,
            )
            _persist_chain_only_quarantine_fact(tid, chain)
            portfolio.positions.append(quarantine_pos)
            stats["quarantined"] += 1

    return stats


QUARANTINE_TIMEOUT_HOURS = 48
QUARANTINE_REVIEW_REQUIRED = "QUARANTINE_REVIEW_REQUIRED"
QUARANTINE_EXPIRED_REVIEW_REQUIRED = "QUARANTINE_EXPIRED_REVIEW_REQUIRED"


def quarantine_resolution_reason(chain_state: str) -> str:
    if chain_state == "quarantine_expired":
        return QUARANTINE_EXPIRED_REVIEW_REQUIRED
    return QUARANTINE_REVIEW_REQUIRED


def check_quarantine_timeouts(portfolio: PortfolioState) -> int:
    """Expire quarantined positions after 48 hours.

    Expired positions become eligible for exit evaluation.
    Returns: number of positions expired.
    """
    now = datetime.now(timezone.utc)
    expired = 0

    for pos in portfolio.positions:
        if pos.chain_state != "quarantined":
            continue
        if not pos.quarantined_at:
            # No timestamp at all — treat as maximally stale, force expiry
            logger.warning(
                "QUARANTINE MISSING TIMESTAMP: %s — forcing exit evaluation",
                pos.trade_id,
            )
            pos.chain_state = "quarantine_expired"
            expired += 1
            continue

        try:
            quarantined_dt = datetime.fromisoformat(
                pos.quarantined_at.replace("Z", "+00:00")
            )
        except ValueError:
            logger.warning(
                "QUARANTINE BAD TIMESTAMP: %s quarantined_at=%r — forcing exit evaluation",
                pos.trade_id, pos.quarantined_at,
            )
            pos.chain_state = "quarantine_expired"
            expired += 1
            continue

        hours_quarantined = (now - quarantined_dt).total_seconds() / 3600
        if hours_quarantined > QUARANTINE_TIMEOUT_HOURS:
            logger.warning(
                "QUARANTINE EXPIRED: %s held for %.0fh — forcing exit evaluation",
                pos.trade_id, hours_quarantined,
            )
            pos.chain_state = "quarantine_expired"
            expired += 1

    return expired

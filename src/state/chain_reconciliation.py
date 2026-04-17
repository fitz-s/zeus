"""Chain reconciliation: 3 rules. Chain is truth. Portfolio is cache.

Blueprint v2 §5: Three sources of truth WILL disagree.
Chain > Chronicler > Portfolio. Always.

Rules:
1. Local + chain match → SYNCED
2. Local but NOT on chain → VOID immediately (don't ask why)
3. Chain but NOT local → QUARANTINE (low confidence, 48h forced exit eval)

Paper mode: skip (no chain to reconcile).
Live mode: MANDATORY every cycle before any trading.
"""

import logging
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

from src.state.chain_state import ChainState, classify_chain_state
from src.state.lifecycle_manager import (
    enter_chain_quarantined_runtime_state,
    rescue_pending_runtime_state,
)
from src.state.portfolio import INACTIVE_RUNTIME_STATES, QUARANTINE_SENTINEL, Position, PortfolioState, void_position

logger = logging.getLogger(__name__)
PENDING_EXIT_STATES = frozenset({"exit_intent", "sell_placed", "sell_pending", "retry_pending"})


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
            try:
                conn.execute(
                    "INSERT INTO position_events (position_id, sequence_no, event_type, occurred_at, payload, source_module) "
                    "VALUES (?, (SELECT COALESCE(MAX(sequence_no),0)+1 FROM position_events WHERE position_id=?), ?, ?, ?, ?)",
                    (
                        getattr(position, "trade_id", ""),
                        getattr(position, "trade_id", ""),
                        "CHAIN_RESCUE_AUDIT",
                        datetime.now(timezone.utc).isoformat(),
                        json.dumps({
                            "chain_state": getattr(position, "chain_state", "?"),
                            "shares": getattr(position, "shares", 0.0),
                            "entry_price": getattr(position, "entry_price", 0.0)
                        }),
                        "src.state.chain_reconciliation_audit"
                    )
                )
                # INFO(DT#1): This commit is exempt from the commit_then_export
                # choke point. The CHAIN_RESCUE_AUDIT row is itself the
                # authoritative observability record (not a derived export),
                # and durability must survive a subsequent cycle crash.
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

    for pos in list(portfolio.positions):
        tid = pos.token_id if pos.direction == "buy_yes" else pos.no_token_id
        if not tid:
            if pos.state == "pending_tracked":
                stats["skipped_pending"] += 1
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
            rescued = replace(pos)
            rescued.entry_order_id = rescued.entry_order_id or rescued.order_id or ""
            rescued.order_id = rescued.order_id or rescued.entry_order_id or ""
            rescued.chain_state = "synced"
            rescued.chain_shares = chain.size
            rescued.chain_verified_at = now
            rescued.condition_id = rescued.condition_id or chain.condition_id
            if chain.avg_price > 0:
                rescued.entry_price = chain.avg_price
            if chain.cost > 0:
                rescued.cost_basis_usd = chain.cost
                rescued.size_usd = chain.cost
            if chain.size > 0:
                rescued.shares = chain.size
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
                rescued.entered_at = "unknown_entered_at"
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
            void_position(portfolio, pos.trade_id, "PHANTOM_NOT_ON_CHAIN")
            stats["voided"] += 1
        else:
            local_shares = pos.effective_shares
            corrected = replace(pos)
            corrected.chain_state = "synced"
            corrected.chain_shares = chain.size
            corrected.chain_verified_at = now
            corrected.condition_id = corrected.condition_id or chain.condition_id
            if chain.avg_price > 0:
                corrected.entry_price = chain.avg_price
            if chain.cost > 0:
                corrected.cost_basis_usd = chain.cost
                corrected.size_usd = chain.cost
            if abs(chain.size - local_shares) > 0.01:
                logger.warning("SIZE MISMATCH: %s local %.4f vs chain %.4f", pos.trade_id, local_shares, chain.size)
                corrected.shares = chain.size
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
                    corrected.shares = local_shares
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
                entered_at="unknown_entered_at",
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

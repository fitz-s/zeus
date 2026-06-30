"""Chain reconciliation: 3 rules. Chain is truth. Portfolio is cache.

Blueprint v2 §5: Three sources of truth WILL disagree.
Chain > Chronicler > Portfolio. Always.

Rules:
1. Local + chain match → SYNCED.
2. Local but NOT on chain → VOID *only if* the chain snapshot is
   CHAIN_EMPTY (fresh, complete, and authoritatively empty).
   CHAIN_UNKNOWN (missing/stale/incomplete API response) MUST NEVER void
   — degraded snapshots are not evidence of absence. Finding 1 / PR C0
   (2026-05-27) split this further: positive observation timestamps
   (`Position.chain_verified_at`) and absence observation timestamps
   (`Position.last_chain_absence_observed_at`) are now separate fields so
   `classify_chain_state()` can reason about chain freshness without
   conflating the two signals.
3. Chain but NOT local → emit a typed `ChainOnlyFact` review-queue entry
   (PR C2 / PR E2, 2026-05-27). Earlier versions of this module
   synthesized a fake `Position(direction="unknown", ...)` for these
   tokens; that is no longer permitted. Downstream consumers consult
   `PortfolioState.chain_only_facts`.

Live mode: MANDATORY every cycle before any trading.
"""

import json
import logging
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from src.contracts.position_truth import CHAIN_ONLY_REVIEW_WINDOW_HOURS, ChainOnlyFact
from src.contracts.semantic_types import LifecycleState
from src.state.chain_state import ChainSnapshotCompleteness, classify_chain_state
from src.state.lifecycle_manager import (
    LifecyclePhase,
    phase_for_runtime_position,
    rescue_pending_runtime_state,
)
from src.state.portfolio import (
    FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
    FILL_AUTHORITY_VENUE_POSITION_OBSERVED,
    INACTIVE_RUNTIME_STATES,
    Position,
    PortfolioState,
    void_position,
)
from src.observability.counters import increment as _cnt_inc

logger = logging.getLogger(__name__)
PENDING_EXIT_STATES = frozenset({"exit_intent", "sell_placed", "sell_pending", "retry_pending"})
LIVE_TRADE_FACT_SOURCES = frozenset({"REST", "WS_USER", "WS_MARKET", "DATA_API", "CHAIN"})
FILL_TRADE_FACT_STATES = frozenset({"MATCHED", "MINED", "CONFIRMED"})
CONFIRMED_CHAIN_ABSENCE_REVIEW_REASON = "chain_absent_confirmed_position_unattributed"
CONFIRMED_CHAIN_ABSENCE_CHAIN_STATE = "chain_absent_confirmed_position_unattributed"
ENTRY_AUTHORITY_CHAIN_ABSENCE_REVIEW_REASON = "entry_authority_chain_absence_conflict"
ENTRY_AUTHORITY_CHAIN_ABSENCE_CHAIN_STATE = "entry_authority_quarantined"

# Slice A4 (PR #19 finding 8, 2026-04-26): structural anchor for the
# learning-authority contract previously held only in resolve_rescue_authority's
# docstring. Any downstream learning consumer that ingests rescue_events
# rows MUST filter on authority = LEARNING_AUTHORITY_REQUIRED to avoid silently
# misclassifying legacy / placeholder / quarantine-default rows (which are
# tagged UNVERIFIED + 'position_missing_metric:...') as VERIFIED training
# evidence. The relationship test in
# tests/test_authority_strict_learning.py scans the repo for SELECT-side
# reads of rescue_events and asserts each carries this filter.
#
# Antibody scope (post-review honesty, code-reviewer fix#4): the scanner is
# LOOSE — it accepts any `WHERE authority = ?` clause, including hardcoded
# "VERIFIED" string literals that bypass this constant. A future consumer
# can satisfy the antibody without importing LEARNING_AUTHORITY_REQUIRED.
# This is intentional: requiring the import would be a stronger antibody
# but would also reject legitimate diagnostic / one-off audit reads. Code
# reviewers reading new SELECT-from-rescue_events sites should verify
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
    rescue_events row from a Position object.

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

    Fix D (Option 4b): The `state: ChainSnapshotCompleteness` field has been removed.
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
# Copilot review fix (2026-05-31, issue #1): after the chain_shares first-
# population the old helper returned early when shares were unchanged, leaving
# chain_seen_at permanently frozen. Re-emit the observation event when the
# persisted timestamp is older than this threshold to keep classify_chain_state()
# correctly classifying long-lived synced positions on daemon restart.
_CHAIN_SEEN_AT_MAX_AGE_SECONDS: int = 1800  # 30 minutes
_CONFIRMED_CHAIN_ABSENCE_RECENT_POSITIVE_SECONDS: int = 6 * 3600


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

    def _position_has_linked_fill_fact(position: Position) -> bool:
        if conn is None:
            return False
        position_id = str(getattr(position, "trade_id", "") or "").strip()
        order_ids = {
            str(value).strip()
            for value in (
                getattr(position, "entry_order_id", ""),
                getattr(position, "order_id", ""),
            )
            if str(value or "").strip()
        }
        command_ids = {
            str(value).strip()
            for value in (
                getattr(position, "entry_command_id", ""),
                getattr(position, "command_id", ""),
            )
            if str(value or "").strip()
        }
        predicates: list[str] = []
        params: list[str] = []
        if position_id:
            predicates.append("vc.position_id = ?")
            params.append(position_id)
        if order_ids:
            placeholders = ", ".join(["?"] * len(order_ids))
            predicates.append(f"(vtf.venue_order_id IN ({placeholders}) OR vc.venue_order_id IN ({placeholders}))")
            params.extend(order_ids)
            params.extend(order_ids)
        if command_ids:
            placeholders = ", ".join(["?"] * len(command_ids))
            predicates.append(f"(vtf.command_id IN ({placeholders}) OR vc.command_id IN ({placeholders}))")
            params.extend(command_ids)
            params.extend(command_ids)
        if not predicates:
            return False
        try:
            rows = conn.execute(
                f"""
                SELECT vtf.state, vtf.source, vtf.filled_size, vtf.fill_price
                  FROM venue_trade_facts vtf
                  LEFT JOIN venue_commands vc
                    ON vc.command_id = vtf.command_id
                 WHERE {" OR ".join(f"({predicate})" for predicate in predicates)}
                 ORDER BY vtf.observed_at DESC, vtf.local_sequence DESC
                """,
                tuple(params),
            ).fetchall()
        except Exception as exc:
            raise RuntimeError(
                f"venue fill fact lookup failed for position_id={position_id or 'missing'}"
            ) from exc
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

    def _pending_entry_has_linked_fill_fact(position: Position) -> bool:
        return _position_has_linked_fill_fact(position)

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

    def _canonical_chain_observation_phase(position_id: str) -> str | None:
        """Return the current canonical phase for a chain-economics no-op write.

        Chain observations do not own lifecycle transitions; they only refresh
        chain_shares / chain_seen_at / chain economics.  Therefore the event
        must fold the current canonical phase to itself.  Pending entries remain
        excluded because entry-fill detection owns that race; pending exits are
        included because exit_lifecycle still needs fresh chain facts while a
        sell attempt is backoff/exhausted/in flight.
        """
        if conn is None:
            return None
        try:
            row = conn.execute(
                "SELECT phase FROM position_current WHERE position_id = ?",
                (position_id,),
            ).fetchone()
        except Exception:
            return None
        if row is None:
            if _has_canonical_position_history(position_id):
                raise RuntimeError("canonical chain-observation baseline missing current projection")
            return None
        phase = str(row[0] or "")
        if phase == LifecyclePhase.PENDING_ENTRY.value:
            return None
        if phase not in {
            LifecyclePhase.ACTIVE.value,
            LifecyclePhase.DAY0_WINDOW.value,
            LifecyclePhase.PENDING_EXIT.value,
        }:
            raise RuntimeError(
                f"canonical chain-observation baseline phase is not open: got {phase!r}"
            )
        return phase

    def _preserve_existing_chain_noop_projection_fields(
        projection: dict,
        position_id: str,
    ) -> None:
        """Keep non-chain lifecycle/monitor fields stable on chain no-op writes.

        Chain observation and size-correction events are chain-truth writes. They
        must not erase the latest exit reason, retry state, or monitor snapshot
        just because the runtime Position cache lacks those fields.
        """
        if conn is None:
            return
        fields = (
            "exit_reason",
            "exit_retry_count",
            "next_exit_retry_at",
            "last_monitor_prob",
            "last_monitor_prob_is_fresh",
            "last_monitor_edge",
            "last_monitor_market_price",
            "last_monitor_market_price_is_fresh",
        )
        try:
            row = conn.execute(
                f"SELECT {', '.join(fields)} FROM position_current WHERE position_id = ?",
                (position_id,),
            ).fetchone()
        except Exception:
            return
        if row is None:
            return
        for idx, field in enumerate(fields):
            current_value = row[field] if hasattr(row, "keys") else row[idx]
            if current_value is None:
                continue
            projection[field] = current_value

    def _append_canonical_rescue_if_available(position: Position) -> bool:
        if conn is None:
            return False
        if not _canonical_rescue_baseline_available(getattr(position, "trade_id", "")):
            return False

        # PR D0 (Finding D0 / Part-2 audit, 2026-05-27): emit distinct
        # canonical event grammar for balance-only vs trade-verified rescue.
        # When the position has no linked venue trade fact (legacy /
        # pre-command-journal pending rows that still reach rescue), emit
        # VENUE_POSITION_OBSERVED with recovery_authority=balance_only +
        # training_eligible=false in the payload. Trade-verified rescues
        # continue to emit CHAIN_SYNCED via the original builder.
        from src.engine.lifecycle_events import (
            build_reconciliation_rescue_canonical_write,
            build_venue_position_observed_canonical_write,
        )
        from src.state.db import append_many_and_project

        has_trade_fact = _pending_entry_has_linked_fill_fact(position)

        try:
            if has_trade_fact:
                # F4 (docs/archive/2026-Q2/findings_historical/findings_2026_05_28.md §F4, 2026-05-28): trade-verified
                # rescue is the verified entry-fill recovery — pending_entry →
                # ACTIVE. Pass phase_after explicitly; the canonical projection's
                # phase no longer reads pos.state/exit_state/chain_state strings.
                events, projection = build_reconciliation_rescue_canonical_write(
                    position,
                    chain_synced_at=now,
                    sequence_no=_next_canonical_sequence_no(getattr(position, "trade_id", "")),
                    phase_after=LifecyclePhase.ACTIVE.value,
                    source_module="src.state.chain_reconciliation",
                )
            else:
                # F4: balance-only rescue — exposure exists on chain but no
                # verified fill fact. Folds to ACTIVE so monitor/exit can manage
                # the exposure; authority degradation lives in the event payload
                # (fill_authority=venue_position_observed, training_eligible=false).
                events, projection = build_venue_position_observed_canonical_write(
                    position,
                    venue_observed_at=now,
                    sequence_no=_next_canonical_sequence_no(getattr(position, "trade_id", "")),
                    phase_after=LifecyclePhase.ACTIVE.value,
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
        current_phase = _canonical_chain_observation_phase(getattr(position, "trade_id", ""))
        if not current_phase:
            return False

        from src.engine.lifecycle_events import build_chain_size_corrected_canonical_write
        from src.state.db import append_many_and_project

        try:
            # F4 (docs/archive/2026-Q2/findings_historical/findings_2026_05_28.md §F4, 2026-05-28): size-correction
            # does NOT transition the canonical phase — pass the position's
            # *current* canonical phase explicitly.
            events, projection = build_chain_size_corrected_canonical_write(
                position,
                local_shares_before=local_shares_before,
                sequence_no=_next_canonical_sequence_no(getattr(position, "trade_id", "")),
                phase_after=current_phase,
                source_module="src.state.chain_reconciliation",
            )
            _preserve_existing_chain_noop_projection_fields(
                projection,
                getattr(position, "trade_id", ""),
            )
            append_many_and_project(conn, events, projection)
        except Exception as exc:
            raise RuntimeError(
                f"canonical reconciliation size-correction dual-write failed for {position.trade_id}: {exc}"
            ) from exc

        return True

    def _canonical_current_chain_shares(
        position_id: str,
    ) -> tuple[bool, float | None, str | None, str | None]:
        """Return (row_exists, chain_shares, chain_seen_at, chain_state) from position_current.

        chain_shares is the persisted on-chain share count (NULL when the
        chain observation has never been projected). chain_seen_at is the
        ISO-8601 string of the last persisted positive-observation timestamp
        (empty-string or NULL when never written). (False, None, None, None) means
        no canonical row exists yet — the position has no projection to update.

        Copilot review fix (2026-05-31, issue #1): include chain_seen_at so the
        observation helper can decide whether the TIMESTAMP needs refresh
        independently of whether chain_shares changed. Without this the helper
        returned early on shares-unchanged positions and chain_seen_at went
        permanently stale after first-population, causing classify_chain_state()
        to mis-classify long-lived synced positions on restart.
        """
        if conn is None:
            return (False, None, None, None)
        try:
            row = conn.execute(
                "SELECT chain_shares, chain_seen_at, chain_state FROM position_current "
                "WHERE position_id = ?",
                (position_id,),
            ).fetchone()
        except Exception:
            return (False, None, None, None)
        if row is None:
            return (False, None, None, None)
        if hasattr(row, "keys"):
            raw_shares = row["chain_shares"]
            raw_seen_at = row["chain_seen_at"]
            raw_chain_state = row["chain_state"]
        else:
            raw_shares = row[0]
            raw_seen_at = row[1]
            raw_chain_state = row[2]
        seen_at = str(raw_seen_at or "") or None
        persisted_chain_state = str(raw_chain_state or "") or None
        if raw_shares is None:
            return (True, None, seen_at, persisted_chain_state)
        try:
            parsed = Decimal(str(raw_shares))
        except (InvalidOperation, ValueError):
            return (True, None, seen_at, persisted_chain_state)
        if not parsed.is_finite():
            return (True, None, seen_at, persisted_chain_state)
        return (True, float(parsed), seen_at, persisted_chain_state)

    def _append_canonical_chain_observation_if_available(
        position: Position,
        *,
        prior_chain_state: str = "",
    ) -> bool:
        """Persist chain economics for a SYNCED (matched, no size-mismatch)
        position whose persisted chain_shares is NULL (first-population), has
        drifted from the freshly-observed chain.size, OR whose persisted
        chain_seen_at is stale (> _CHAIN_SEEN_AT_MAX_AGE_SECONDS old).

        Chain-shares-persist fix (2026-05-31, task #56): the matched-no-size-
        mismatch path mutated Position.chain_shares in-memory but issued NO
        canonical write, leaving position_current.chain_shares NULL forever for
        every synced position (only the SIZE-MISMATCH branch persisted via
        _append_canonical_size_correction_if_available). This sibling emits a
        chain-OBSERVATION canonical event (build_chain_economics_observed_
        canonical_write) that projects chain_shares / chain_avg_price /
        chain_cost_basis_usd / chain_seen_at onto position_current WITHOUT any
        share mutation or phase transition.

        Timestamp-refresh fix (2026-05-31, Copilot review issue #1): after
        first-population, shares are unchanged so the old code returned early
        every cycle — chain_seen_at was frozen at the first-population timestamp
        forever. On daemon restart classify_chain_state() reads chain_seen_at
        from position_current back into Position.chain_verified_at; a stale
        positive-observation timestamp triggers CHAIN_UNKNOWN mis-classification
        for long-lived synced positions. Fix: skip the write ONLY when shares
        are unchanged AND the persisted chain_seen_at is fresh (within
        _CHAIN_SEEN_AT_MAX_AGE_SECONDS). When the timestamp is stale the
        observation event is re-emitted (cheap: one SAVEPOINT write per cycle
        per stale position, bounded by the 30-minute window).

        Idempotency guard (fix #121, 2026-06-03): the stale-timestamp re-emit
        (case d) is restricted to positions whose chain_state was ALREADY
        "synced" before this reconcile cycle (prior_chain_state == "synced").
        When a position is transitioning into "synced" for the first time (e.g.
        prior chain_state was "unknown"), reconcile's matched branch sets
        corrected.chain_verified_at = now, which propagates to position_current
        via this write — so a subsequent cycle will see a fresh chain_seen_at
        and skip. Without this guard, a position corrected with an old
        chain_verified_at (externally seeded or restored from DB with a stale
        timestamp) would re-emit on every cycle until the timestamp aged out,
        causing a repeated CHAIN_SIZE_CORRECTED storm (#121).

        Cases:
          (a) shares NULL → always write (first-population).
          (b) shares drifted → always write.
          (c) shares unchanged + timestamp fresh → skip.
          (d) shares unchanged + timestamp stale + prior_chain_state == "synced"
              → write (long-lived synced: genuine refresh needed).
          (e) shares unchanged + timestamp stale + prior_chain_state != "synced"
              → skip (transitioning into synced: chain_seen_at will be fresh
              after this cycle's canonical write from the size-correction path).
          (f) shares unchanged + projected chain_state != "synced" → write
              (chain economics match, but the canonical visibility projection
              still misleads monitor/redecision).

        Gating mirrors _append_canonical_size_correction_if_available:
          - conn present; skip pending_entry phase (don't fight fill detection);
          - canonical row must exist in an open chain-observable phase
            (active/day0_window/pending_exit) — a missing projection with prior
            history or terminal phase is a contract violation surfaced by the
            shared baseline gate.

        Fail-closed: any unexpected error is swallowed (logged) so reconcile
        never crashes. The next cycle re-detects and retries the write.
        """
        if conn is None:
            return False
        try:
            trade_id = getattr(position, "trade_id", "")
            current_phase = _canonical_chain_observation_phase(trade_id)
            if not current_phase:
                return False

            row_exists, persisted_chain_shares, persisted_seen_at, persisted_chain_state = (
                _canonical_current_chain_shares(trade_id)
            )
            if not row_exists:
                return False
            target_chain_shares = getattr(position, "chain_shares", None)
            if target_chain_shares is None:
                return False

            # Decide whether a write is needed:
            #   (a) shares need first-population (NULL) → always write.
            #   (b) shares have genuinely drifted → always write.
            #   (c) shares are unchanged AND timestamp is fresh → skip.
            #   (d) shares are unchanged AND timestamp is stale AND position was
            #       already synced before this cycle → write to refresh
            #       chain_seen_at so classify_chain_state() keeps the correct
            #       CHAIN_KNOWN classification on restart.
            #   (e) shares are unchanged AND timestamp is stale AND position was
            #       NOT already synced → skip (fix #121: transitioning into
            #       synced; chain_seen_at will be fresh after this write).
            #   (f) shares are unchanged but projected chain_state is not synced
            #       → write (restore canonical chain visibility).
            shares_unchanged = (
                persisted_chain_shares is not None
                and abs(float(persisted_chain_shares) - float(target_chain_shares)) <= 1e-9
            )
            if shares_unchanged and persisted_chain_state == "synced":
                # Check timestamp freshness (case c vs d/e).
                timestamp_fresh = False
                if persisted_seen_at:
                    try:
                        from datetime import datetime, timezone
                        ts = datetime.fromisoformat(persisted_seen_at)
                        if ts.tzinfo is None:
                            ts = ts.replace(tzinfo=timezone.utc)
                        now_dt = datetime.fromisoformat(now)
                        if now_dt.tzinfo is None:
                            now_dt = now_dt.replace(tzinfo=timezone.utc)
                        age_s = (now_dt - ts).total_seconds()
                        timestamp_fresh = age_s < _CHAIN_SEEN_AT_MAX_AGE_SECONDS
                    except Exception:
                        timestamp_fresh = False  # parse failure → treat as stale
                if timestamp_fresh:
                    return False  # case c: nothing to write
                # Case d vs e: stale timestamp. Only re-emit for long-lived
                # synced positions (fix #121 idempotency guard).
                if prior_chain_state != "synced":
                    return False  # case e: transitioning to synced this cycle

            from src.engine.lifecycle_events import (
                build_chain_economics_observed_canonical_write,
            )
            from src.state.db import append_many_and_project

            events, projection = build_chain_economics_observed_canonical_write(
                position,
                chain_observed_at=now,
                sequence_no=_next_canonical_sequence_no(trade_id),
                phase_after=current_phase,
                chain_shares_before=persisted_chain_shares,
                source_module="src.state.chain_reconciliation",
            )
            _preserve_existing_chain_noop_projection_fields(projection, trade_id)
            append_many_and_project(conn, events, projection)
        except Exception as exc:
            logger.warning(
                "CHAIN_OBSERVATION canonical write failed for %s: %s "
                "(in-memory chain_shares stands; next reconcile cycle retries)",
                getattr(position, "trade_id", "?"), exc,
            )
            return False
        return True

    def _append_canonical_review_required(position: Position, *, reason: str) -> bool:
        """PR #352 (Part-3 audit Finding 4): persist a durable REVIEW_REQUIRED
        event + quarantined projection for an unresolved size mismatch.

        The caller has already set position.state=QUARANTINED and
        chain_state=size_mismatch_unresolved, so the projection phase is
        QUARANTINED. append_many_and_project writes position_current.phase=
        quarantined durably — the review requirement now survives daemon restart
        instead of living only in the in-memory Position. Best-effort: if no
        connection or the write fails, the runtime quarantine still stands (the
        next reconcile cycle re-detects and re-attempts the durable write).
        """
        if conn is None:
            return False
        from src.engine.lifecycle_events import build_review_required_canonical_write
        from src.state.db import append_many_and_project

        try:
            # F4 (docs/archive/2026-Q2/findings_historical/findings_2026_05_28.md §F4, 2026-05-28): unresolved
            # size mismatch always quarantines the position. Pass phase_after
            # explicitly so canonical position_current.phase is QUARANTINED
            # regardless of any prior runtime pos.state string mutation.
            events, projection = build_review_required_canonical_write(
                position,
                review_detected_at=now,
                reason=reason,
                sequence_no=_next_canonical_sequence_no(getattr(position, "trade_id", "")),
                phase_after=LifecyclePhase.QUARANTINED.value,
                source_module="src.state.chain_reconciliation",
            )
            append_many_and_project(conn, events, projection)
        except Exception as exc:
            logger.warning(
                "REVIEW_REQUIRED canonical write failed for %s: %s (runtime quarantine stands; "
                "next reconcile cycle retries)",
                getattr(position, "trade_id", "?"), exc,
            )
            return False
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
            # CHAIN_RESCUE_AUDIT row and the new rescue_events row so
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
                # INFO(DT#1): rescue_events is an authoritative audit
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
            if env not in {"live", "test", "replay", "backtest"}:
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

    def _has_confirmed_entry_authority(position: Position) -> bool:
        return (
            bool(getattr(position, "entry_fill_verified", False))
            or str(getattr(position, "fill_authority", "") or "")
            == FILL_AUTHORITY_VENUE_CONFIRMED_FULL
            or _position_has_linked_fill_fact(position)
        )

    def _parse_reconcile_dt(value: object) -> datetime | None:
        if value in (None, ""):
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _positive_chain_observation_is_recent(value: object) -> bool:
        observed_at = _parse_reconcile_dt(value)
        now_dt = _parse_reconcile_dt(now)
        if observed_at is None or now_dt is None:
            return False
        age_seconds = (now_dt - observed_at).total_seconds()
        return 0.0 <= age_seconds <= _CONFIRMED_CHAIN_ABSENCE_RECENT_POSITIVE_SECONDS

    def _payload_has_positive_chain_observation(payload_json: str) -> bool:
        try:
            payload = json.loads(payload_json or "{}")
        except Exception:
            return False
        if str(payload.get("chain_state") or "") not in {"", "synced"}:
            return False
        for key in ("chain_shares_after", "chain_shares", "shares_after", "shares"):
            if _positive_decimal(payload.get(key)):
                return True
        return False

    def _recent_positive_chain_observation(position: Position) -> tuple[bool, str, str]:
        """Return whether recent positive chain evidence should veto absence quarantine.

        A non-empty chain snapshot that omits one token is not proof that a
        recently observed, venue-confirmed position is gone. Treating that case
        as terminal quarantine removed live exposure from monitor/redecision.
        """
        runtime_seen_at = getattr(position, "chain_verified_at", "") or ""
        if (
            _positive_decimal(getattr(position, "chain_shares", None))
            and runtime_seen_at
            and _positive_chain_observation_is_recent(runtime_seen_at)
        ):
            return (True, str(runtime_seen_at), "runtime_chain_verified_at")
        if conn is not None:
            position_id = str(getattr(position, "trade_id", "") or "")
            if position_id:
                try:
                    row = conn.execute(
                        """
                        SELECT chain_shares, chain_seen_at
                          FROM position_current
                         WHERE position_id = ?
                        """,
                        (position_id,),
                    ).fetchone()
                except Exception:
                    row = None
                if row is not None:
                    chain_shares = row["chain_shares"] if hasattr(row, "keys") else row[0]
                    chain_seen_at = row["chain_seen_at"] if hasattr(row, "keys") else row[1]
                    if (
                        _positive_decimal(chain_shares)
                        and chain_seen_at
                        and _positive_chain_observation_is_recent(chain_seen_at)
                    ):
                        return (True, str(chain_seen_at), "position_current.chain_seen_at")
                try:
                    rows = conn.execute(
                        """
                        SELECT occurred_at, payload_json
                          FROM position_events
                         WHERE position_id = ?
                           AND event_type IN ('CHAIN_SYNCED', 'CHAIN_SIZE_CORRECTED')
                         ORDER BY occurred_at DESC, sequence_no DESC
                         LIMIT 8
                        """,
                        (position_id,),
                    ).fetchall()
                except Exception:
                    rows = []
                for event_row in rows:
                    occurred_at = event_row["occurred_at"] if hasattr(event_row, "keys") else event_row[0]
                    payload_json = event_row["payload_json"] if hasattr(event_row, "keys") else event_row[1]
                    if (
                        _positive_chain_observation_is_recent(occurred_at)
                        and _payload_has_positive_chain_observation(str(payload_json or ""))
                    ):
                        return (True, str(occurred_at), "position_events.positive_chain_observation")
        return (False, "", "")

    def _defer_confirmed_chain_absence_when_recently_observed(
        position: Position,
        *,
        token_id: str,
        source: str,
    ) -> bool:
        recent, observed_at, basis = _recent_positive_chain_observation(position)
        if not recent:
            return False
        position.last_chain_absence_observed_at = now
        stats["confirmed_chain_absence_recent_positive_deferred"] = (
            stats.get("confirmed_chain_absence_recent_positive_deferred", 0) + 1
        )
        logger.warning(
            "CONFIRMED_POSITION_CHAIN_ABSENCE_DEFERRED: trade_id=%s token=%s "
            "source=%s basis=%s observed_at=%s; preserving monitorable exposure",
            getattr(position, "trade_id", "?"),
            token_id,
            source,
            basis,
            observed_at,
        )
        return True

    def _preserve_confirmed_fill_chain_absence_conflict(
        position: Position,
        *,
        token_id: str,
        source: str,
    ) -> None:
        corrected = replace(position)
        corrected.state = LifecycleState.QUARANTINED.value
        corrected.chain_state = ENTRY_AUTHORITY_CHAIN_ABSENCE_CHAIN_STATE
        if not _positive_decimal(getattr(corrected, "chain_shares", None)):
            corrected.chain_shares = float(getattr(corrected, "shares", 0.0) or 0.0)
        if not _positive_decimal(getattr(corrected, "chain_avg_price", None)):
            corrected.chain_avg_price = float(getattr(corrected, "entry_price", 0.0) or 0.0)
        if not _positive_decimal(getattr(corrected, "chain_cost_basis_usd", None)):
            corrected.chain_cost_basis_usd = float(getattr(corrected, "cost_basis_usd", 0.0) or 0.0)
        corrected.fill_authority = FILL_AUTHORITY_VENUE_CONFIRMED_FULL
        corrected.exit_reason = ENTRY_AUTHORITY_CHAIN_ABSENCE_REVIEW_REASON
        corrected.last_chain_absence_observed_at = now
        corrected.quarantined_at = corrected.quarantined_at or now
        logger.error(
            "CONFIRMED_FILL_CHAIN_ABSENCE_CONFLICT: trade_id=%s token=%s source=%s; "
            "preserving live monitorable exposure for attribution instead of marking no-risk absent",
            getattr(position, "trade_id", "?"),
            token_id,
            source,
        )
        if _append_canonical_review_required(
            corrected,
            reason=ENTRY_AUTHORITY_CHAIN_ABSENCE_REVIEW_REASON,
        ):
            stats["review_required_persisted"] = (
                stats.get("review_required_persisted", 0) + 1
            )
        position.state = corrected.state
        position.chain_state = corrected.chain_state
        position.chain_shares = corrected.chain_shares
        position.chain_avg_price = corrected.chain_avg_price
        position.chain_cost_basis_usd = corrected.chain_cost_basis_usd
        position.fill_authority = corrected.fill_authority
        position.exit_reason = corrected.exit_reason
        position.last_chain_absence_observed_at = corrected.last_chain_absence_observed_at
        position.quarantined_at = corrected.quarantined_at
        stats["quarantined"] += 1
        stats["confirmed_fill_chain_absence_conflict_preserved"] = (
            stats.get("confirmed_fill_chain_absence_conflict_preserved", 0) + 1
        )

    def _persist_chain_only_quarantine_fact(token_id: str, chain: ChainPosition) -> str:
        if conn is None:
            return "global"
        from src.state.db import chain_only_entry_block_scope, record_token_suppression

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
        return chain_only_entry_block_scope(
            conn,
            condition_id=str(getattr(chain, "condition_id", "") or ""),
        )

    def _held_token_id(position: Position) -> str:
        direction = getattr(position, "direction", "")
        direction = getattr(direction, "value", direction)
        if str(direction) == "buy_no":
            return str(getattr(position, "no_token_id", "") or "")
        return str(getattr(position, "token_id", "") or "")

    def _chain_observed_cost(chain: ChainPosition) -> float:
        try:
            cost = float(getattr(chain, "cost", 0.0) or 0.0)
        except (TypeError, ValueError):
            cost = 0.0
        if cost > 0.0:
            return cost
        try:
            return float(chain.size) * float(chain.avg_price or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _restore_terminal_chain_exposure_if_available(
        token_id: str,
        chain: ChainPosition,
    ) -> bool:
        candidates: list[Position] = []
        chain_condition_id = str(getattr(chain, "condition_id", "") or "")
        for position in portfolio.positions:
            if _held_token_id(position) != token_id:
                continue
            state_value = getattr(position.state, "value", position.state)
            if str(state_value) == "economically_closed":
                continue
            if str(state_value) not in INACTIVE_RUNTIME_STATES:
                continue
            if (
                chain_condition_id
                and str(getattr(position, "condition_id", "") or "")
                and str(getattr(position, "condition_id", "") or "") != chain_condition_id
            ):
                continue
            candidates.append(position)
        if not candidates:
            return False
        restored = sorted(
            candidates,
            key=lambda p: (
                str(getattr(p, "entered_at", "") or ""),
                str(getattr(p, "order_posted_at", "") or ""),
                str(getattr(p, "trade_id", "") or ""),
            ),
            reverse=True,
        )[0]
        cost = _chain_observed_cost(chain)
        restored.state = LifecycleState.QUARANTINED.value
        restored.chain_state = "entry_authority_quarantined"
        restored.chain_shares = float(chain.size)
        restored.chain_avg_price = float(getattr(chain, "avg_price", 0.0) or 0.0)
        restored.chain_cost_basis_usd = cost
        restored.chain_verified_at = now
        restored.quarantined_at = restored.quarantined_at or now
        restored.fill_authority = FILL_AUTHORITY_VENUE_POSITION_OBSERVED
        restored.recovery_authority = "balance_only"
        restored.shares = float(chain.size)
        restored.cost_basis_usd = cost
        restored.size_usd = cost
        if restored.entry_price <= 0.0:
            restored.entry_price = float(getattr(chain, "avg_price", 0.0) or 0.0)
        if chain_condition_id and not str(getattr(restored, "condition_id", "") or ""):
            restored.condition_id = chain_condition_id
        restored.order_status = "filled"
        restored.exit_state = ""
        restored.exit_reason = ""
        if _append_canonical_review_required(
            restored,
            reason="chain_held_after_terminal_projection",
        ):
            stats["review_required_persisted"] = (
                stats.get("review_required_persisted", 0) + 1
            )
        stats["terminal_chain_exposure_restored"] = (
            stats.get("terminal_chain_exposure_restored", 0) + 1
        )
        stats["quarantined"] += 1
        return True

    def _attached_schemas() -> set[str]:
        if conn is None:
            return set()
        try:
            return {str(row[1]) for row in conn.execute("PRAGMA database_list").fetchall()}
        except Exception:
            return {"main"}

    def _table_exists(schema: str, table: str) -> bool:
        if conn is None:
            return False
        try:
            row = conn.execute(
                f"SELECT 1 FROM {schema}.sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
        except Exception:
            return False
        return row is not None

    def _ensure_forecasts_attached() -> None:
        if conn is None:
            return
        if "forecasts" in _attached_schemas():
            return
        try:
            from src.state.db import ZEUS_FORECASTS_DB_PATH

            conn.execute("ATTACH DATABASE ? AS forecasts", (str(ZEUS_FORECASTS_DB_PATH),))
        except Exception:
            return

    def _chain_market_metadata(token_id: str, chain: ChainPosition) -> dict[str, object] | None:
        if conn is None:
            return None
        _ensure_forecasts_attached()
        schemas = _attached_schemas()
        for schema in ("forecasts", "world", "main"):
            if schema not in schemas or not _table_exists(schema, "market_events"):
                continue
            cols = {
                str(row[1])
                for row in conn.execute(f"PRAGMA {schema}.table_info(market_events)").fetchall()
            }
            metric_expr = (
                "temperature_metric"
                if "temperature_metric" in cols
                else (
                    "CASE WHEN lower(market_slug) LIKE '%lowest-temperature%' "
                    "THEN 'low' ELSE 'high' END"
                )
            )
            query = f"""
                SELECT city, target_date, {metric_expr} AS temperature_metric,
                       market_slug, range_label, token_id, condition_id
                  FROM {schema}.market_events
                 WHERE (
                        NULLIF(condition_id, '') = NULLIF(?, '')
                     OR NULLIF(token_id, '') = NULLIF(?, '')
                 )
                 ORDER BY CASE WHEN token_id = ? THEN 0 ELSE 1 END
                 LIMIT 1
            """
            try:
                row = conn.execute(
                    query,
                    (
                        str(getattr(chain, "condition_id", "") or ""),
                        token_id,
                        token_id,
                    ),
                ).fetchone()
            except Exception:
                continue
            if row is None:
                continue
            try:
                row_token = str(row["token_id"] or "")
            except Exception:
                row_token = str(row[5] or "")
            direction = "buy_yes" if row_token == token_id else "buy_no"
            return {
                "city": row["city"],
                "target_date": row["target_date"],
                "temperature_metric": row["temperature_metric"],
                "market_slug": row["market_slug"],
                "bin_label": row["range_label"],
                "yes_token_id": row_token,
                "condition_id": row["condition_id"] or getattr(chain, "condition_id", ""),
                "direction": direction,
            }
        return None

    def _materialize_chain_only_position_if_resolvable(
        token_id: str,
        chain: ChainPosition,
    ) -> bool:
        metadata = _chain_market_metadata(token_id, chain)
        if metadata is None:
            return False
        cost = _chain_observed_cost(chain)
        direction = str(metadata["direction"])
        yes_token_id = str(metadata.get("yes_token_id") or "")
        position = Position(
            trade_id=f"chain-only-{token_id[-16:]}",
            market_id=str(metadata.get("condition_id") or getattr(chain, "condition_id", "") or token_id),
            city=str(metadata.get("city") or "CHAIN_ONLY_UNRESOLVED"),
            cluster=str(metadata.get("city") or "CHAIN_ONLY_UNRESOLVED"),
            target_date=str(metadata.get("target_date") or ""),
            bin_label=str(metadata.get("bin_label") or ""),
            direction=direction,
            unit="C" if "°C" in str(metadata.get("bin_label") or "") else "F",
            temperature_metric=str(metadata.get("temperature_metric") or "high"),
            env="live",
            size_usd=cost,
            entry_price=float(getattr(chain, "avg_price", 0.0) or 0.0),
            p_posterior=0.0,
            shares=float(chain.size),
            cost_basis_usd=cost,
            entered_at=now,
            entered_at_authority="reconstructed_from_chain",
            entry_method="chain_only_reconciliation",
            strategy_key="chain_only_reconciliation",
            strategy="chain_only_reconciliation",
            edge_source="chain_only_quarantine",
            discovery_mode="chain_reconciliation",
            state=LifecycleState.QUARANTINED.value,
            order_status="filled",
            chain_state="entry_authority_quarantined",
            chain_shares=float(chain.size),
            chain_avg_price=float(getattr(chain, "avg_price", 0.0) or 0.0),
            chain_cost_basis_usd=cost,
            chain_verified_at=now,
            token_id=yes_token_id if direction == "buy_no" else token_id,
            no_token_id=token_id if direction == "buy_no" else "",
            condition_id=str(metadata.get("condition_id") or getattr(chain, "condition_id", "") or ""),
            quarantined_at=now,
            fill_authority=FILL_AUTHORITY_VENUE_POSITION_OBSERVED,
            market_slug=str(metadata.get("market_slug") or ""),
        )
        position.recovery_authority = "balance_only"
        if _append_canonical_review_required(
            position,
            reason="chain_only_canonical_quarantine",
        ):
            stats["review_required_persisted"] = (
                stats.get("review_required_persisted", 0) + 1
            )
        portfolio.positions.append(position)
        stats["chain_only_canonical_quarantine_materialized"] = (
            stats.get("chain_only_canonical_quarantine_materialized", 0) + 1
        )
        stats["quarantined"] += 1
        return True

    # DT#4 / INV-18: derive three-state from inputs at the TOP of reconcile().
    # reconcile() is only called when the chain API responded (cycle_runtime.py
    # raises if api_positions is None). Treat the call timestamp as fetched_at.
    # Fix E: fetched_at=now is correct here — reconcile() is only called after
    # the chain API returns a non-None response, so the fetch itself is fresh.
    # CHAIN_UNKNOWN reachability inside reconcile is exclusively via the
    # empty-chain-with-recent-local-verified branch of classify_chain_state.
    chain_state: ChainSnapshotCompleteness = classify_chain_state(
        fetched_at=now,  # API responded (non-None) — use current timestamp
        chain_positions=chain_positions,
        portfolio=portfolio,
    )
    if chain_state == ChainSnapshotCompleteness.CHAIN_UNKNOWN:
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
    if chain_state != ChainSnapshotCompleteness.CHAIN_UNKNOWN:
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
            if _chain_bal > 0.0 and len(_positions) > 1 and any(
                (float(getattr(_p, "chain_shares", 0.0) or 0.0) >= _chain_bal - 0.01)
                and bool(getattr(_p, "chain_verified_at", "") or "")
                for _p in _positions
            ):
                # The chain balance is token-aggregate truth. If an individual
                # lot already carries that aggregate observation, LIFO allocation
                # cannot distinguish lots and would false-void real exposure.
                for _p in _positions:
                    aggregate_backed_set.add(_p.trade_id)
                stats["skipped_aggregate_allocation_existing_chain_observation"] = (
                    stats.get("skipped_aggregate_allocation_existing_chain_observation", 0) + 1
                )
                logger.warning(
                    "AGGREGATE_ALLOCATION_SKIPPED: token=%s chain=%.4f rows=%d "
                    "reason=existing_lot_carries_aggregate_chain_observation",
                    _tid,
                    _chain_bal,
                    len(_positions),
                )
                continue
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
            if _has_confirmed_entry_authority(pos):
                if _defer_confirmed_chain_absence_when_recently_observed(
                    pos,
                    token_id=tid,
                    source="aggregate_allocation",
                ):
                    continue
                _preserve_confirmed_fill_chain_absence_conflict(
                    pos,
                    token_id=tid,
                    source="aggregate_allocation",
                )
                continue
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

        if (
            state_name == LifecyclePhase.VOIDED.value
            and str(getattr(pos, "exit_reason", "") or "") == "PHANTOM_NOT_ON_CHAIN"
            and (
                _positive_decimal(getattr(pos, "chain_shares", None))
                or _positive_decimal(getattr(pos, "shares", None))
            )
            and _has_confirmed_entry_authority(pos)
        ):
            corrected = replace(pos)
            corrected.state = LifecycleState.QUARANTINED.value
            corrected.chain_state = ENTRY_AUTHORITY_CHAIN_ABSENCE_CHAIN_STATE
            if not _positive_decimal(getattr(corrected, "chain_shares", None)):
                corrected.chain_shares = float(getattr(corrected, "shares", 0.0) or 0.0)
            if not _positive_decimal(getattr(corrected, "chain_avg_price", None)):
                corrected.chain_avg_price = float(getattr(corrected, "entry_price", 0.0) or 0.0)
            if not _positive_decimal(getattr(corrected, "chain_cost_basis_usd", None)):
                corrected.chain_cost_basis_usd = float(getattr(corrected, "cost_basis_usd", 0.0) or 0.0)
            corrected.fill_authority = FILL_AUTHORITY_VENUE_CONFIRMED_FULL
            corrected.exit_reason = ENTRY_AUTHORITY_CHAIN_ABSENCE_REVIEW_REASON
            corrected.last_chain_absence_observed_at = (
                getattr(corrected, "last_chain_absence_observed_at", "") or now
            )
            corrected.quarantined_at = corrected.quarantined_at or now
            if _append_canonical_review_required(
                corrected,
                reason=ENTRY_AUTHORITY_CHAIN_ABSENCE_REVIEW_REASON,
            ):
                stats["false_phantom_void_positive_exposure_restored"] = (
                    stats.get("false_phantom_void_positive_exposure_restored", 0) + 1
                )
                stats["review_required_persisted"] = (
                    stats.get("review_required_persisted", 0) + 1
                )
            else:
                stats["false_phantom_void_positive_exposure_runtime_restored"] = (
                    stats.get("false_phantom_void_positive_exposure_runtime_restored", 0) + 1
                )
            pos.state = corrected.state
            pos.chain_state = corrected.chain_state
            pos.chain_shares = corrected.chain_shares
            pos.chain_avg_price = corrected.chain_avg_price
            pos.chain_cost_basis_usd = corrected.chain_cost_basis_usd
            pos.fill_authority = corrected.fill_authority
            pos.exit_reason = corrected.exit_reason
            pos.last_chain_absence_observed_at = corrected.last_chain_absence_observed_at
            pos.quarantined_at = corrected.quarantined_at
            stats["quarantined"] += 1
            continue

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
            # F1 (docs/archive/2026-Q2/findings_historical/findings_2026_05_28.md §F1, 2026-05-28): chain
            # aggregate always lands on chain_* fields, regardless of rescue
            # authority. This is the canonical home for venue-observed
            # economics; the legacy `entry_price` / `cost_basis_usd` /
            # `size_usd` / `shares` fields are now only mutated by the
            # trade-verified rescue branch below.
            if chain.avg_price > 0:
                rescued.chain_avg_price = chain.avg_price
            if chain.cost > 0:
                rescued.chain_cost_basis_usd = chain.cost
            # PR D0 (Finding D0, Part-2 audit, 2026-05-27): discriminate rescue
            # authority by whether the position has a linked venue trade fact.
            # F1 (2026-05-28): the entry/fill economics mutations below now
            # FIRE ONLY on the trade-verified branch — balance-only rescue is
            # not authoritative for fill economics.
            #
            # Only set entry_fill_verified=True and order_status="filled" for
            # trade-verified rescue (linked fill fact present). Balance-only
            # recovery (fill_authority=FILL_AUTHORITY_VENUE_POSITION_OBSERVED)
            # must NOT flip entry_fill_verified=True — downstream gates must use
            # has_tradable_exposure() for EXPOSURE checks and
            # has_verified_trade_fill() for fill-economics checks instead.
            #
            # PR C3 note: gate at top of this branch already skipped commanded
            # pending entries that lack a fill fact, so a missing fill fact here
            # means the position was pre-command-journal legacy.
            _has_linked_fill_fact = _pending_entry_has_linked_fill_fact(pos)
            if _has_linked_fill_fact:
                # Trade-verified rescue branch: chain economics ARE fill
                # economics (verified by venue trade fact). Continue to
                # mutate the legacy fields so downstream P&L and reporting
                # see the verified fill values.
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
                rescued.fill_authority = FILL_AUTHORITY_VENUE_CONFIRMED_FULL
                rescued.entry_fill_verified = True
                rescued.order_status = "filled"
            else:
                # F1 (2026-05-28): balance-only rescue. The chain aggregate is
                # observed exposure, NOT verified fill economics — leave
                # submitted entry/fill economics (entry_price, cost_basis_usd,
                # size_usd, shares) untouched. chain_avg_price /
                # chain_cost_basis_usd / chain_shares (set above) carry the
                # venue-observed truth. Downstream consumers consult
                # effective_exposure() which routes to chain_* when
                # fill_authority == venue_position_observed.
                rescued.fill_authority = FILL_AUTHORITY_VENUE_POSITION_OBSERVED
                # entry_fill_verified stays False for balance-only rescue.
                # order_status stays at its current value (not forced to "filled").
                logger.warning(
                    "RESCUE_DEGRADED_AUTHORITY: trade_id=%s token=%s — chain balance present "
                    "but no linked venue trade fact; setting fill_authority=%s. Position is "
                    "tradable (has_tradable_exposure) but NOT fill-verified or training-eligible. "
                    "Submitted entry economics preserved; chain economics on chain_* fields.",
                    getattr(rescued, "trade_id", "?"),
                    tid,
                    FILL_AUTHORITY_VENUE_POSITION_OBSERVED,
                )
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
                rescued.entered_at_authority = "reconstructed_from_chain"
                _entered_at_was_fabricated = True
            else:
                _entered_at_was_fabricated = False
                # F2: entered_at was already present from real venue fill data.
                if not rescued.entered_at_authority:
                    rescued.entered_at_authority = "verified_entry_fill"
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
            # F1 (2026-05-28): chain-observed economics always copy back —
            # they are the canonical home for venue-observed truth and are
            # set by both rescue branches above.
            pos.chain_avg_price = rescued.chain_avg_price
            pos.chain_cost_basis_usd = rescued.chain_cost_basis_usd
            pos.chain_verified_at = rescued.chain_verified_at
            pos.condition_id = rescued.condition_id
            # F1 (2026-05-28): entry/fill economics copy back ONLY on the
            # trade-verified branch. Balance-only rescue leaves submitted
            # entry economics untouched; downstream readers must consult
            # `effective_exposure()` for authority-routed values.
            pos.fill_authority = rescued.fill_authority
            if rescued.fill_authority == FILL_AUTHORITY_VENUE_CONFIRMED_FULL:
                pos.entry_price = rescued.entry_price
                pos.cost_basis_usd = rescued.cost_basis_usd
                pos.size_usd = rescued.size_usd
                pos.shares = rescued.shares
            pos.entry_fill_verified = rescued.entry_fill_verified
            pos.order_status = rescued.order_status
            pos.state = rescued.state
            pos.entered_at = rescued.entered_at
            pos.entered_at_authority = rescued.entered_at_authority
            stats["rescued_pending"] += 1
            stats["synced"] += 1
            continue

        if chain is None:
            if chain_state == ChainSnapshotCompleteness.CHAIN_UNKNOWN:
                continue  # Don't void — API response is suspect
            if (
                getattr(pos, "entry_fill_verified", False)
                and pos.chain_state in {"local_only", "unknown"}
                and pos.state in {"entered", "holding", "day0_window"}
            ):
                pos.chain_state = "local_only"
                # Finding 1 (PR C0): this branch fires when the local position is
                # ABSENT from the chain snapshot. Record absence, NOT positive
                # verification — chain_verified_at must remain a positive-only marker
                # so classify_chain_state() can correctly distinguish CHAIN_EMPTY
                # (fresh complete snapshot saw nothing) from CHAIN_UNKNOWN
                # (incomplete/stale snapshot).
                pos.last_chain_absence_observed_at = now
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
                # Finding 1 (PR C0): exit-in-flight branch fires when the chain
                # snapshot does NOT contain this position. Absence ≠ positive
                # verification — see Position.chain_verified_at docstring.
                pos.last_chain_absence_observed_at = now
                stats["skipped_pending_exit"] = stats.get("skipped_pending_exit", 0) + 1
                continue
            if _has_confirmed_entry_authority(pos):
                if _defer_confirmed_chain_absence_when_recently_observed(
                    pos,
                    token_id=tid,
                    source="per_position_missing_token",
                ):
                    continue
                _preserve_confirmed_fill_chain_absence_conflict(
                    pos,
                    token_id=tid,
                    source="per_position_missing_token",
                )
                continue
            # Rule 2: Local but NOT on chain → VOID — but ONLY when the
            # chain snapshot reaching this line is CHAIN_EMPTY (fresh,
            # complete, authoritative). CHAIN_UNKNOWN is short-circuited
            # earlier via `if chain_state == ChainSnapshotCompleteness.CHAIN_UNKNOWN:
            # continue` — see the gate above. Reaching this point with a
            # missing/stale snapshot is a contract violation.
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
            # F1 (PR1 critic in-spirit): chain economics always populate chain_*
            # fields so VenuePositionObservedEcon is current regardless of whether
            # the entry/fill fields are also updated.  Entry/fill mutation is
            # controlled by the existing _size_mismatch_eligible opt-in gate.
            if chain.avg_price > 0:
                corrected.chain_avg_price = chain.avg_price
            if chain.cost > 0:
                corrected.chain_cost_basis_usd = chain.cost
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
                    # Finding 2 (PR C1, 2026-05-27): the previous string
                    # "quarantine_size_mismatch" was NOT a member of LifecycleState
                    # and downstream phase_for_runtime_position() mapped it to
                    # LifecyclePhase.UNKNOWN — exposure/exit/harvester then saw
                    # inconsistent phases. The size-mismatch discriminator already
                    # lives in chain_state (SIZE_MISMATCH_UNRESOLVED); state must
                    # carry the canonical LifecycleState.QUARANTINED.value.
                    corrected.state = LifecycleState.QUARANTINED.value
                    corrected.chain_state = "size_mismatch_unresolved"
                    corrected.quarantined_at = corrected.quarantined_at or now
                    if not _size_mismatch_eligible:
                        corrected.shares = local_shares
                    else:
                        _cnt_inc("cost_basis_chain_mutation_blocked_total", labels={"field": "shares"})
                        logger.warning("telemetry_counter event=cost_basis_chain_mutation_blocked_total field=shares")
                    stats["skipped_size_correction_missing_canonical_baseline"] = (
                        stats.get("skipped_size_correction_missing_canonical_baseline", 0) + 1
                    )
                    # PR #352 (Part-3 audit Finding 4): persist the review
                    # requirement durably. Without this, position_current stays
                    # 'active' on disk and the quarantine/review is lost on the
                    # next daemon restart — unresolved size mismatch is live
                    # exposure risk and must survive process lifetime.
                    if _append_canonical_review_required(
                        corrected, reason="size_mismatch_unresolved_no_canonical_baseline"
                    ):
                        stats["review_required_persisted"] = (
                            stats.get("review_required_persisted", 0) + 1
                        )
                else:
                    stats["updated"] += 1
            else:
                # Chain-shares-persist fix (2026-05-31, task #56): matched —
                # chain.size == local_shares, single-lot (NOT aggregate-backed),
                # no size mismatch. The pre-fix code mutated corrected.chain_*
                # in-memory here but issued NO canonical write, so
                # position_current.chain_shares stayed NULL forever for every
                # synced position (only the SIZE-MISMATCH branch persisted chain
                # economics). Persist the chain OBSERVATION when chain_shares
                # needs first-population (NULL) or has drifted. Fail-closed:
                # the helper never raises; in-memory chain_* below still stands.
                _prior_cs = getattr(pos, "chain_state", "") or ""
                if _append_canonical_chain_observation_if_available(
                    corrected,
                    prior_chain_state=getattr(_prior_cs, "value", _prior_cs),
                ):
                    stats["chain_observation_persisted"] = (
                        stats.get("chain_observation_persisted", 0) + 1
                    )
            pos.chain_state = corrected.chain_state
            pos.chain_shares = corrected.chain_shares
            pos.chain_avg_price = corrected.chain_avg_price
            pos.chain_cost_basis_usd = corrected.chain_cost_basis_usd
            pos.chain_verified_at = corrected.chain_verified_at
            pos.condition_id = corrected.condition_id
            pos.entry_price = corrected.entry_price
            pos.cost_basis_usd = corrected.cost_basis_usd
            pos.size_usd = corrected.size_usd
            pos.shares = corrected.shares
            pos.state = corrected.state
            pos.quarantined_at = getattr(corrected, "quarantined_at", getattr(pos, "quarantined_at", ""))
            stats["synced"] += 1

    # Rule 3: Chain but NOT local → QUARANTINE (skip ignored tokens)
    ignored = set(getattr(portfolio, "ignored_tokens", []) or [])
    for tid, chain in chain_by_token.items():
        if tid in ignored:
            continue  # Token was explicitly acknowledged/resolved or redeemed/expired — don't resurrect
        if tid not in local_tokens:
            if _restore_terminal_chain_exposure_if_available(tid, chain):
                local_tokens.add(tid)
                continue
            if _materialize_chain_only_position_if_resolvable(tid, chain):
                local_tokens.add(tid)
                continue
            logger.warning(
                "QUARANTINE EXCLUDED FROM CANONICAL MIGRATION: chain token %s...%s not in portfolio; pending future governance design",
                tid[:8],
                tid[-4:],
            )
            # PR C2 (Finding 3, 2026-05-27): emit typed ChainOnlyFact instead
            # of synthesizing a fake Position with direction="unknown" and
            # sentinel identity. The suppression row was already written via
            # _persist_chain_only_quarantine_fact, so durable storage is
            # unchanged. portfolio.chain_only_facts carries the in-memory
            # review-queue signal that cycle gates consult alongside
            # portfolio.positions during the migration window.
            entry_block_scope = _persist_chain_only_quarantine_fact(tid, chain)
            portfolio.chain_only_facts.append(
                ChainOnlyFact(
                    token_id=tid,
                    condition_id=getattr(chain, "condition_id", "") or "",
                    size=float(chain.size),
                    avg_price=float(getattr(chain, "avg_price", 0.0) or 0.0),
                    cost_basis=float(chain.cost or (chain.size * (chain.avg_price or 0.0))),
                    first_seen_at=now,
                    last_seen_at=now,
                    entry_block_scope=entry_block_scope,
                )
            )
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

    Expired positions remain non-entry-block-cleared, but become eligible for
    explicit monitor/admin resolution with QUARANTINE_EXPIRED_REVIEW_REQUIRED.
    Returns: number of positions expired.
    """
    now = datetime.now(timezone.utc)
    expired = 0

    for pos in portfolio.positions:
        if pos.chain_state != "quarantined":
            continue
        if not pos.quarantined_at:
            # No timestamp at all — treat as maximally stale, force admin review.
            logger.warning(
                "QUARANTINE MISSING TIMESTAMP: %s — forcing admin resolution",
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
                "QUARANTINE BAD TIMESTAMP: %s quarantined_at=%r — forcing admin resolution",
                pos.trade_id, pos.quarantined_at,
            )
            pos.chain_state = "quarantine_expired"
            expired += 1
            continue

        hours_quarantined = (now - quarantined_dt).total_seconds() / 3600
        if hours_quarantined > QUARANTINE_TIMEOUT_HOURS:
            logger.warning(
                "QUARANTINE EXPIRED: %s held for %.0fh — forcing admin resolution",
                pos.trade_id, hours_quarantined,
            )
            pos.chain_state = "quarantine_expired"
            expired += 1

    # PR #352 (Part-3 audit, Copilot #350 finding): ChainOnlyFact 48h review
    # escalation consumer. Chain-only inventory is NOT a local Position, so the
    # position "exit evaluation" above does not apply — there is nothing to
    # exit. Instead, its review_state escalates UNRESOLVED -> EXPIRED at the 48h
    # window and is surfaced here for operator attention. Expiry is not current
    # chain truth and must not freeze unrelated entries forever. Prior to this,
    # the 48h ChainOnlyFact lifecycle the README references had no consumer
    # beyond the entry gate. Read-only escalation (the fact's review_state is
    # derived); resolution is operator-driven via the suppression row.
    for fact in getattr(portfolio, "chain_only_facts", None) or []:
        review_state = getattr(getattr(fact, "review_state", None), "value", None)
        if review_state == "resolved":
            continue  # RESOLVED — nothing to escalate
        first_seen = str(getattr(fact, "first_seen_at", "") or "")
        try:
            seen_dt = datetime.fromisoformat(first_seen.replace("Z", "+00:00"))
        except ValueError:
            logger.warning(
                "CHAIN_ONLY_REVIEW MISSING/BAD TIMESTAMP: token=%s first_seen=%r — operator review required (fresh entry blocked)",
                getattr(fact, "token_id", "?"), first_seen,
            )
            continue
        hours_seen = (now - seen_dt).total_seconds() / 3600
        if hours_seen > CHAIN_ONLY_REVIEW_WINDOW_HOURS:
            logger.warning(
                "CHAIN_ONLY_REVIEW EXPIRED: token=%s held %.0fh review_state=%s — operator review required (entry no longer globally blocked by this stale fact)",
                getattr(fact, "token_id", "?"),
                hours_seen,
                getattr(getattr(fact, "review_state", None), "value", "?"),
            )

    return expired

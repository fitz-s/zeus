"""DT#4 / INV-18: Chain-truth three-state machine.

Public symbols:
  ChainState (enum)
  classify_chain_state(*, fetched_at, chain_positions, portfolio, ...) -> ChainState

Timestamp contract (Finding 1, PR C0, 2026-05-27):
  This classifier consults `Position.chain_verified_at` to decide whether a
  fresh chain-empty snapshot reflects reality (CHAIN_EMPTY → safe to void)
  or a degraded API (CHAIN_UNKNOWN → never void).

  The contract REQUIRES `chain_verified_at` to be a POSITIVE-observation
  timestamp — i.e. it is updated only when the venue/chain confirmed the
  position is held (rescue, size correction, sync). Negative observations
  (position absent from snapshot) live in `Position.last_chain_absence_observed_at`
  and MUST NOT advance `chain_verified_at`. Violating the contract inverts
  CHAIN_EMPTY vs CHAIN_UNKNOWN: a recent absence-write would falsely prove
  recent positive verification and force CHAIN_UNKNOWN, blocking legitimate
  voids on a genuinely complete empty snapshot.

  Producers of `chain_verified_at` (positive-only): the rescue branch and
  size-correction branch in `src/state/chain_reconciliation.py:reconcile()`.
  Concrete line numbers intentionally omitted because they drift with every
  edit to the file; grep for `chain_verified_at = ` to enumerate writers,
  and confirm each lives inside a branch where `chain_state` is set to
  `"synced"` (positive observation) rather than `"local_only"` /
  `"exit_pending_missing"` (absence). The
  `test_chain_reconciliation_absence_branches_do_not_advance_positive_timestamp`
  test enforces that invariant statically.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Optional, Sequence

if TYPE_CHECKING:
    pass


class ChainState(str, Enum):
    """Per-cycle chain snapshot completeness (NOT per-position visibility).

    Finding 7 (PR B, 2026-05-27): The name `ChainState` is shared with
    `src/contracts/semantic_types.py.ChainState` (per-position visibility).
    These are different real-world objects. New code SHOULD import the
    domain-specific alias below; legacy imports of `ChainState` remain wire-
    compatible.
    """

    CHAIN_SYNCED = "chain_synced"
    CHAIN_EMPTY = "chain_empty"
    CHAIN_UNKNOWN = "chain_unknown"


# Domain-specific alias (Finding 7 / PR B). Prefer this name in new code.
ChainSnapshotCompleteness = ChainState


_STALE_GUARD_SECONDS = 6 * 3600


def classify_chain_state(
    *,
    fetched_at: Optional[str],
    chain_positions: Sequence,
    portfolio,
    stale_guard_seconds: int = _STALE_GUARD_SECONDS,
    now: Optional[datetime] = None,
) -> ChainState:
    """Pure-function classifier for chain reconciliation state.

    Transition table (phase2_plan.md §5, R-C):
      fetched_at=None                         → CHAIN_UNKNOWN
      fetched_at present, non-empty positions → CHAIN_SYNCED
      fetched_at present, empty positions,
        all active local chain_verified_at > stale_guard_seconds → CHAIN_EMPTY
      fetched_at present, empty positions,
        any active local chain_verified_at <= stale_guard_seconds → CHAIN_UNKNOWN

    Parameters
    ----------
    fetched_at:
        ISO timestamp string from the chain API response, or None if the API
        call failed / was not made.
    chain_positions:
        Sequence of on-chain positions returned by the CLOB API.
    portfolio:
        PortfolioState (or any object with a .positions attribute).
    stale_guard_seconds:
        Window in seconds after which a local chain_verified_at is considered
        stale enough to trust a chain-empty response.  Default: 6 hours.
    now:
        Inject the current UTC datetime for testing.  Defaults to
        datetime.now(timezone.utc).
    """
    if fetched_at is None:
        return ChainState.CHAIN_UNKNOWN

    if len(chain_positions) > 0:
        return ChainState.CHAIN_SYNCED

    # chain returned empty — decide whether to trust it
    _now = now if now is not None else datetime.now(timezone.utc)

    # Inspect active local positions for recent chain_verified_at timestamps
    for pos in getattr(portfolio, "positions", []):
        # Skip inactive / pending states (mirrors chain_reconciliation.py logic)
        pos_state = str(getattr(pos, "state", "") or "")
        if pos_state in {"pending_tracked", "settled", "voided", "admin_closed",
                         "expired", "quarantined_void"}:
            continue

        verified = getattr(pos, "chain_verified_at", "") or ""
        if not verified:
            # Position has never been verified on chain — treat as recently active
            return ChainState.CHAIN_UNKNOWN

        try:
            vt = datetime.fromisoformat(verified.replace("Z", "+00:00")) if isinstance(verified, str) else verified
            age_seconds = (_now - vt).total_seconds()
            if age_seconds <= stale_guard_seconds:
                # This position was verified within the stale guard window —
                # an empty chain response is suspect
                return ChainState.CHAIN_UNKNOWN
        except (ValueError, TypeError):
            # Unparseable timestamp — be conservative
            return ChainState.CHAIN_UNKNOWN

    # All active local positions have stale chain_verified_at (or there are none)
    return ChainState.CHAIN_EMPTY

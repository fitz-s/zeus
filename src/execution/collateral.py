"""Pre-sell collateral verification. Live safety mechanism.

Polymarket binary markets: selling YES shares requires (1 - price) * shares
as collateral locked. Without sufficient collateral, the sell order fails
on-chain, but the position is already marked as "exiting" locally.

This check is FAIL-CLOSED: if we can't verify collateral, we don't sell.
"""

import logging
import sqlite3
import time as _time
from datetime import datetime, timezone
from typing import Optional

from src.state.collateral_ledger import CollateralInsufficient, assert_sell_preflight

logger = logging.getLogger(__name__)
SUBMIT_COLLATERAL_REFRESH_TIMEOUT_SECONDS = 20.0


def _capability_component(
    component: str,
    *,
    allowed: bool = True,
    reason: str = "allowed",
    **details,
) -> dict:
    payload = {
        "component": component,
        "allowed": bool(allowed),
        "reason": str(reason),
    }
    if details:
        payload["details"] = dict(details)
    return payload


class _DeadlineCollateralAdapter:
    def __init__(self, adapter, *, timeout_seconds: float):
        self._adapter = adapter
        self._timeout_seconds = timeout_seconds
        self.last_error: str | None = None

    def get_collateral_payload(self) -> dict:
        from src.runtime.timeout_guard import run_with_timeout

        fn = getattr(self._adapter, "get_collateral_payload", None)
        if not callable(fn):
            raise AttributeError("adapter exposes no get_collateral_payload")
        try:
            return run_with_timeout(
                fn,
                seconds=self._timeout_seconds,
                label="submit_collateral_refresh",
            )
        except Exception as exc:
            self.last_error = str(exc)
            raise


def refresh_collateral_snapshot_for_submit(
    conn: sqlite3.Connection,
    *,
    action: str,
    reuse_fresh_snapshot: bool = False,
) -> dict:
    """Ensure collateral truth is fresh synchronously on a submit path.

    This is a fail-closed pre-side-effect gate. If the latest snapshot is
    already fresh, it is reused. If it is stale, degraded, or absent, refresh
    from the venue before preflight. A transient SQLite lock is retried briefly;
    venue/adapter failures become a degraded snapshot and therefore block later
    preflight.
    """

    from src.data.polymarket_client import PolymarketClient
    from src.state.collateral_ledger import (
        COLLATERAL_SNAPSHOT_MAX_AGE_SECONDS,
        CollateralLedger,
    )

    ledger = CollateralLedger(conn)
    current_snapshot = ledger.snapshot()
    captured_at = current_snapshot.captured_at
    if captured_at.tzinfo is None:
        captured_at = captured_at.replace(tzinfo=timezone.utc)
    age_seconds = (
        datetime.now(timezone.utc) - captured_at.astimezone(timezone.utc)
    ).total_seconds()
    reusable_fresh_snapshot = (
        current_snapshot.authority_tier != "DEGRADED"
        and 0 <= age_seconds < COLLATERAL_SNAPSHOT_MAX_AGE_SECONDS
    )
    if (
        reusable_fresh_snapshot
        and action == "entry_submit"
        and current_snapshot.available_pusd_allowance_micro <= 0
    ):
        reusable_fresh_snapshot = False

    if reuse_fresh_snapshot and reusable_fresh_snapshot:
        return _capability_component(
            "collateral_snapshot_refresh",
            authority_tier=current_snapshot.authority_tier,
            captured_at=current_snapshot.captured_at.isoformat(),
            action=action,
            reused_fresh_snapshot=True,
        )

    _LOCK_RETRIES = 5
    _LOCK_BACKOFF_SECONDS = 0.4
    client = PolymarketClient()
    ensure_adapter = getattr(client, "_ensure_v2_adapter", None)
    raw_adapter = ensure_adapter() if callable(ensure_adapter) else client
    adapter = _DeadlineCollateralAdapter(
        raw_adapter,
        timeout_seconds=SUBMIT_COLLATERAL_REFRESH_TIMEOUT_SECONDS,
    )
    snapshot = None
    for attempt in range(_LOCK_RETRIES):
        try:
            snapshot = ledger.refresh(adapter)
            break
        except CollateralInsufficient:
            raise
        except sqlite3.OperationalError as exc:
            if "lock" not in str(exc).lower() or attempt == _LOCK_RETRIES - 1:
                raise CollateralInsufficient(f"collateral_refresh_failed: {exc}") from exc
            _time.sleep(_LOCK_BACKOFF_SECONDS)
        except Exception as exc:
            raise CollateralInsufficient(f"collateral_refresh_failed: {exc}") from exc
    if snapshot is None:
        raise CollateralInsufficient("collateral_refresh_failed: lock_retries_exhausted")
    if snapshot.authority_tier == "DEGRADED":
        error = f": {adapter.last_error}" if adapter.last_error else ""
        raise CollateralInsufficient(
            f"collateral_snapshot_degraded: refreshed_before_{action}{error}"
        )
    return _capability_component(
        "collateral_snapshot_refresh",
        authority_tier=snapshot.authority_tier,
        captured_at=snapshot.captured_at.isoformat(),
        action=action,
    )


def check_sell_collateral(
    entry_price: float,
    shares: float,
    clob,
    *,
    token_id: str = "",
    conn: sqlite3.Connection | None = None,
) -> tuple[bool, Optional[str]]:
    """Verify CTF outcome-token inventory for a sell.

    Returns: (can_sell, reason) — reason only set on failure.
    """
    if token_id:
        try:
            if conn is not None:
                from src.state.collateral_ledger import CollateralLedger

                CollateralLedger(conn).sell_preflight(token_id=token_id, size=shares)
            else:
                assert_sell_preflight(token_id, shares)
            return True, None
        except CollateralInsufficient as exc:
            return False, str(exc)

    # Legacy compatibility for tests/callers that do not have token identity.
    # Runtime exit paths pass token_id and therefore use CollateralLedger. This
    # fallback must not be treated as proof that pUSD can satisfy a CTF sell.
    try:
        balance = float(clob.get_balance())
    except Exception as exc:
        # Can't check → don't sell (fail-closed)
        return False, f"balance_fetch_failed: {exc}"

    required = (1.0 - entry_price) * shares
    if required < 0:
        required = 0.0  # Edge case: entry_price > 1.0 shouldn't happen but be safe

    if balance < required:
        logger.warning(
            "COLLATERAL INSUFFICIENT: need $%.2f, have $%.2f (entry=%.3f, shares=%.2f)",
            required, balance, entry_price, shares,
        )
        return False, f"need ${required:.2f}, have ${balance:.2f}"

    return True, None

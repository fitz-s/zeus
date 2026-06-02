# Created: 2026-05-01
# Last reused/audited: 2026-05-01
# Authority basis: docs/operations/task_2026-05-01_bankroll_truth_chain/architect_memo.md §7
"""On-chain wallet bankroll provider — single source of truth for live-mode bankroll.

Wraps `PolymarketClient.get_balance()` with a small in-process cache + staleness
window so that the riskguard tick (60s cadence) and other consumers can ask
"what is the current bankroll of record?" without each call hitting the venue.

Authority semantics (architect memo §2):
- The on-chain wallet is the canonical bankroll for trailing-loss math, equity,
  and drawdown computation. Retired config-literal capital is not a bankroll
  truth source.

Behaviour contract (architect memo §7):
- Fresh cache (age < `max_age_seconds`, default 30s): return cached value with
  `cached=True, staleness_seconds=age`.
- Stale cache, fetch succeeds: refresh + return new value with `cached=False,
  staleness_seconds=0.0`.
- Stale cache, fetch fails, last fetch within `fail_closed_after_seconds`
  (default 300s = 5 min): return cached value with `cached=True,
  staleness_seconds=age`. Caller decides whether to act on staleness.
- Stale cache, fetch fails, last fetch > `fail_closed_after_seconds` ago OR
  no prior fetch: return `None`. Caller MUST fail-closed.

Provenance: every returned `BankrollOfRecord` carries `source="polymarket_wallet"`
and `authority="canonical"` so callers can assert before consuming.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_MAX_AGE_SECONDS = 30.0
_DEFAULT_FAIL_CLOSED_AFTER_SECONDS = 300.0

# Resilient staleness bound for cached() — the proof-only / no-submit read path.
#
# RESILIENCE FIX (2026-05-31, follow-up to #45/#64): the on-chain wallet RPC fails
# intermittently (~38/hr observed) and those failures CLUSTER. A single warm tick's
# failure does NOT blank the module global (current() retains _last_value_usd on a
# failed fetch — it only returns None without overwriting), but cached() independently
# re-checked age against the tight 300s _DEFAULT_FAIL_CLOSED_AFTER_SECONDS window. A
# burst of consecutive RPC blips spanning >300s therefore aged a perfectly-good last
# value out of cached() → None → KELLY_PROOF_MISSING:bankroll_provider_unavailable on
# every positive-edge candidate (161/308 lost in 24h).
#
# Wallet balance changes SLOWLY (only on our own fills / settlements, never venue-side
# between cycles), so serving the last good value for a generous window across a
# transient RPC outage is SAFE and strictly better than fail-closing the whole canary.
# The genuine fail-closed semantics are preserved: never-fetched → None, and stale
# beyond this generous bound → None. This decouples cached()'s resilient bound from
# current()'s tighter refresh bound. Override via env for ops tuning.
_DEFAULT_CACHED_RESILIENT_BOUND_SECONDS = 1800.0  # 30 min — survives RPC blip clusters


def _resilient_cached_bound_seconds() -> float:
    raw = os.environ.get("ZEUS_BANKROLL_CACHED_BOUND_SECONDS")
    if raw is None:
        return _DEFAULT_CACHED_RESILIENT_BOUND_SECONDS
    try:
        parsed = float(raw)
    except (TypeError, ValueError):
        logger.warning(
            "ZEUS_BANKROLL_CACHED_BOUND_SECONDS=%r is not a float; using default %.0fs",
            raw, _DEFAULT_CACHED_RESILIENT_BOUND_SECONDS,
        )
        return _DEFAULT_CACHED_RESILIENT_BOUND_SECONDS
    if parsed <= 0:
        logger.warning(
            "ZEUS_BANKROLL_CACHED_BOUND_SECONDS=%r is non-positive; using default %.0fs",
            raw, _DEFAULT_CACHED_RESILIENT_BOUND_SECONDS,
        )
        return _DEFAULT_CACHED_RESILIENT_BOUND_SECONDS
    return parsed


@dataclass(frozen=True)
class BankrollOfRecord:
    """Typed contract for an on-chain wallet bankroll observation."""

    value_usd: float
    fetched_at: str  # ISO-8601 UTC of the underlying wallet fetch
    source: str = "polymarket_wallet"
    authority: str = "canonical"
    staleness_seconds: float = 0.0  # 0.0 = fresh fetch this call
    cached: bool = False  # True iff returned from cache without re-fetching


_lock = threading.Lock()
_last_value_usd: Optional[float] = None
_last_fetched_at: Optional[datetime] = None


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _fetch_balance() -> float:
    """Single underlying call site for the on-chain wallet query.

    Imported lazily to avoid pulling polymarket SDK into modules that only
    care about the typed contract.
    """
    from src.data.polymarket_client import PolymarketClient

    client = PolymarketClient()
    return float(client.get_balance())


def current(
    *,
    max_age_seconds: float = _DEFAULT_MAX_AGE_SECONDS,
    fail_closed_after_seconds: float = _DEFAULT_FAIL_CLOSED_AFTER_SECONDS,
) -> Optional[BankrollOfRecord]:
    """Return the current bankroll of record, or None if unavailable.

    Args:
        max_age_seconds: cache TTL. Within this age, no live fetch is issued.
        fail_closed_after_seconds: maximum staleness tolerated when the live
            fetch fails. Older than this (or never fetched) → return None.

    Returns:
        BankrollOfRecord on success; None when the wallet is unreachable AND
        no usable cache exists. Callers MUST treat None as fail-closed.
    """
    global _last_value_usd, _last_fetched_at

    with _lock:
        now = _now_utc()
        cached_value = _last_value_usd
        cached_fetched_at = _last_fetched_at
        cached_age = (now - cached_fetched_at).total_seconds() if cached_fetched_at else None

        # 1. Fresh cache hit — return without contacting the venue.
        if cached_value is not None and cached_age is not None and cached_age < max_age_seconds:
            return BankrollOfRecord(
                value_usd=cached_value,
                fetched_at=cached_fetched_at.isoformat(),
                staleness_seconds=cached_age,
                cached=True,
            )

        # 2. Cache miss or stale — try a live fetch.
        try:
            fresh_value = _fetch_balance()
            _last_value_usd = fresh_value
            _last_fetched_at = now
            return BankrollOfRecord(
                value_usd=fresh_value,
                fetched_at=now.isoformat(),
                staleness_seconds=0.0,
                cached=False,
            )
        except Exception as exc:
            logger.warning("bankroll_provider live fetch failed: %s", exc)

            # 3. Live fetch failed. Decide fail-closed vs cached-stale.
            if cached_value is None or cached_age is None:
                # Never fetched — fail closed.
                return None
            if cached_age > fail_closed_after_seconds:
                # Cache is too old to trust — fail closed.
                return None
            # Cache is stale-but-tolerable — return it with the staleness flag
            # so the caller can annotate the risk decision.
            return BankrollOfRecord(
                value_usd=cached_value,
                fetched_at=cached_fetched_at.isoformat(),
                staleness_seconds=cached_age,
                cached=True,
            )


def cached(*, max_age_seconds: Optional[float] = None) -> Optional[BankrollOfRecord]:
    """Return a cached bankroll observation without contacting the venue.

    This is intentionally weaker than ``current()``: it never refreshes from
    Polymarket.  Proof-only/no-submit paths can use it to fail closed without
    introducing a wallet/API side effect.

    Resilient staleness bound: when ``max_age_seconds`` is None (the default),
    the bound resolves to ``_resilient_cached_bound_seconds()`` (30 min default,
    env-overridable). This is DELIBERATELY larger than ``current()``'s 300s
    refresh window — a last-good on-chain wallet value survives a transient
    cluster of wallet-RPC blips instead of blanking to None and killing every
    positive-edge candidate with ``KELLY_PROOF_MISSING:bankroll_provider_unavailable``.
    Wallet balance only moves on our own fills/settlements, so a 30-min-old value
    is a faithful bankroll-of-record. Genuine fail-closed is preserved: never
    fetched → None, and stale beyond the resilient bound → None.
    """
    bound = _resilient_cached_bound_seconds() if max_age_seconds is None else max_age_seconds
    with _lock:
        if _last_value_usd is None or _last_fetched_at is None:
            logger.error(
                "bankroll cached() -> None: NEVER-FETCHED in this process "
                "(_last_value_usd=%r _last_fetched_at=%r). The per-cycle warm "
                "current() is not populating this module global.",
                _last_value_usd, _last_fetched_at,
            )
            return None
        now = _now_utc()
        age = (now - _last_fetched_at).total_seconds()
        if age > bound:
            logger.error(
                "bankroll cached() -> None: STALE age=%.1fs > resilient_bound=%.1fs "
                "(last_fetch=%s). On-chain wallet RPC has been failing longer than the "
                "resilient bound; bankroll genuinely unavailable → fail-closed.",
                age, bound, _last_fetched_at.isoformat(),
            )
            return None
        return BankrollOfRecord(
            value_usd=_last_value_usd,
            fetched_at=_last_fetched_at.isoformat(),
            staleness_seconds=age,
            cached=True,
        )


def reset_cache_for_tests() -> None:
    """Clear the module-level cache. Tests only — not part of the public contract."""
    global _last_value_usd, _last_fetched_at
    with _lock:
        _last_value_usd = None
        _last_fetched_at = None

# Created: 2026-05-01
# Last reused/audited: 2026-05-01
# Authority basis: docs/operations/task_2026-05-01_bankroll_truth_chain/architect_memo.md §7
"""Polymarket bankroll provider — single source of truth for live-mode bankroll.

Wraps Polymarket wallet + position-equity reads with a small in-process cache +
staleness window so that the riskguard tick (60s cadence) and other consumers
can ask "what is the current bankroll of record?" without each call hitting the
venue.

Authority semantics (architect memo §2):
- Polymarket wallet equity is the canonical bankroll for trailing-loss math,
  equity, and drawdown computation. Retired config-literal capital is not a
  bankroll truth source.
- Free pUSD is BUY collateral. Live entry sizing must use this spendable-cash
  field instead of equity so open positions cannot inflate the next order's
  single-position cap.

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
    spendable_cash_usd: float | None = None
    # Provenance of the position-value leg of equity (2026-06-09 blip guard).
    # "verified" = venue affirmatively reported holdings (or genuinely none);
    # "blip_held" = the /positions read returned EMPTY while contradicting a
    # recent verified nonzero position value — last-known-good value held.
    positions_read_verdict: str = "verified"
    # DUAL BANKROLL (2026-06-09 P1 follow-up). `value_usd` above is the
    # LOSS-THRESHOLD equity: under blip_held it HOLDS the last-known-good
    # position value so a transient empty /positions read can't collapse the
    # daily-loss threshold base into a false catastrophic RED. But that held
    # value is a PHANTOM for NEW-ENTRY sizing: during the hold the positions may
    # have genuinely vanished, so Kelly must NOT size off it. This second value
    # is the conservative NEW-ENTRY sizing base = free cash + ONLY
    # chain/cash-corroborated position value; under blip_held it EXCLUDES the
    # held phantom component. Defaults to None -> sizing consumers fall back to
    # spendable_cash_usd / value_usd, preserving old behavior for cold records.
    equity_for_new_entry_sizing_usd: float | None = None


_lock = threading.Lock()
_last_value_usd: Optional[float] = None
_last_spendable_cash_usd: Optional[float] = None
# Dual-bankroll (2026-06-09 P1): conservative new-entry sizing equity = free
# cash + only corroborated position value (excludes the blip_held phantom).
_last_sizing_equity_usd: Optional[float] = None
_last_fetched_at: Optional[datetime] = None
# Positions-blip guard state (2026-06-09): anchor of the last VERIFIED nonzero
# position value, used to detect an empty /positions read that contradicts
# recent reality. Updated only under _lock (all writers run inside current()).
_last_position_value_usd: Optional[float] = None
_last_nonzero_positions_at: Optional[datetime] = None
_last_positions_read_verdict: str = "verified"

# An empty /positions response that contradicts a verified nonzero position
# value younger than this bound is treated as a transient venue blip: the
# last-known-good position value is HELD (with a WARN) instead of silently
# collapsing account equity by the full open-position notional. Past the bound,
# a persistently-empty read is accepted as truth (genuine closure persists;
# blips do not). The 2026-06-09 live incident blipped for 13+ consecutive
# minutes, so the default mirrors the 30-min resilient cached() bound.
_DEFAULT_POSITIONS_EMPTY_HOLD_SECONDS = 1800.0
# Equity below this is "no position value worth defending" — no hold applies.
_POSITION_VALUE_EPSILON_USD = 0.01
# Cash corroboration: positions legitimately vanish via settlement/redemption,
# which pays winners INTO free cash. If free cash jumped by at least this
# fraction of the vanished position value in the same read, the empty list is
# corroborated as a genuine redemption and accepted immediately (no hold).
_REDEMPTION_CASH_CORROBORATION_FRACTION = 0.25


def _positions_empty_hold_seconds() -> float:
    raw = os.environ.get("ZEUS_BANKROLL_POSITIONS_EMPTY_HOLD_S")
    if raw is None:
        return _DEFAULT_POSITIONS_EMPTY_HOLD_SECONDS
    try:
        parsed = float(raw)
    except (TypeError, ValueError):
        logger.warning(
            "ZEUS_BANKROLL_POSITIONS_EMPTY_HOLD_S=%r is not a float; using default %.0fs",
            raw, _DEFAULT_POSITIONS_EMPTY_HOLD_SECONDS,
        )
        return _DEFAULT_POSITIONS_EMPTY_HOLD_SECONDS
    if parsed < 0:
        logger.warning(
            "ZEUS_BANKROLL_POSITIONS_EMPTY_HOLD_S=%r is negative; using default %.0fs",
            raw, _DEFAULT_POSITIONS_EMPTY_HOLD_SECONDS,
        )
        return _DEFAULT_POSITIONS_EMPTY_HOLD_SECONDS
    return parsed


def _classify_positions_read(
    *,
    free_pusd: float,
    raw_position_value: float,
    positions_count: int,
    prev_spendable_cash: float | None,
    prev_position_value: float | None,
    prev_nonzero_positions_at: datetime | None,
    now: datetime,
    hold_bound_seconds: float,
) -> tuple[str, float]:
    """Classify a /positions read and return (verdict, effective_position_value).

    PROVENANCE ANTIBODY (Fitz #4, live incident 2026-06-09 22:15-22:28Z): the
    Polymarket positions endpoint intermittently returned an EMPTY list while
    ~$857 of open positions existed. ``free + 0`` collapsed account equity
    10x ($951 -> $94), the riskguard daily-loss threshold base collapsed with
    it ($76 -> $7.53), and an otherwise-fine $10.44 realized loss tripped a
    false RED that blocked ALL new entries. The defect: an empty response was
    treated as the TRUE STATE "no positions" rather than as a possibly-failed
    READ. This classifier distinguishes the two by internal consistency:

    - positions list NON-EMPTY: the venue affirmatively reported holdings —
      trust the value VERBATIM, including a collapsed one. A genuine
      mark-to-market drawdown (positions present, values down) MUST still
      tighten gates; no hold ever applies here. -> "verified"
    - EMPTY with no recent verified nonzero position value: nothing is
      contradicted (cold start / genuinely flat account). -> "verified"
    - EMPTY contradicting a recent nonzero value, with free cash jumped by
      >= 25% of the vanished value in the same read: settlement/redemption
      pays winners into cash, so the closure is corroborated — accept
      immediately. -> "redemption_corroborated"
    - EMPTY contradicting a recent nonzero value, no cash corroboration,
      within the hold bound: transient venue blip — HOLD the last verified
      value (caller WARNs). -> "blip_held"
    - EMPTY persisting beyond the hold bound: genuine closure persists, blips
      do not — accept zero (caller WARNs once accepting). ->
      "persistent_empty_accepted"

    NOT gate weakening: the hold only refuses to let a single contradicted
    empty READ silently delete equity; every affirmative venue report passes
    through verbatim, and a genuine catastrophic loss also surfaces through
    the realized-settled-PnL loss numerator independent of this base.
    """
    if positions_count > 0:
        return "verified", raw_position_value
    if prev_position_value is None or prev_position_value <= _POSITION_VALUE_EPSILON_USD:
        return "verified", 0.0
    if prev_nonzero_positions_at is None:
        return "verified", 0.0
    age_seconds = (now - prev_nonzero_positions_at).total_seconds()
    if age_seconds > hold_bound_seconds:
        return "persistent_empty_accepted", 0.0
    if (
        prev_spendable_cash is not None
        and (free_pusd - prev_spendable_cash)
        >= _REDEMPTION_CASH_CORROBORATION_FRACTION * prev_position_value
    ):
        return "redemption_corroborated", 0.0
    return "blip_held", prev_position_value


def _resolve_position_value(
    free_pusd: float,
    raw_position_value: float,
    positions_count: int,
    *,
    now: datetime | None = None,
) -> tuple[float, float]:
    """Apply the blip classifier against module state and update the anchors.

    Returns (loss_threshold_position_value, sizing_position_value):
    - loss_threshold_position_value: the value the daily-loss threshold base
      uses. Under "blip_held" this HOLDS the last-known-good position value so a
      transient empty /positions read cannot collapse the threshold into a false
      catastrophic RED (the 2026-06-09 incident).
    - sizing_position_value: the value NEW-ENTRY Kelly sizing uses. It equals
      the loss-threshold value in every verdict EXCEPT "blip_held", where it is
      0.0 — during the hold the held value is a PHANTOM (the positions may have
      genuinely vanished), and sizing must never inflate Kelly off equity that
      might not exist. This is the dual-bankroll structural decision (P1
      follow-up): defend the loss threshold WITHOUT arming new entries on
      phantom equity.

    Runs inside current()'s _lock (the only `_fetch_balance` call site), so the
    module-global reads/writes here are lock-safe. On "blip_held" the anchors
    are deliberately NOT advanced: the hold window is measured from the last
    VERIFIED nonzero read, so a sustained empty streak ages out at the bound
    instead of self-renewing forever.
    """
    global _last_position_value_usd, _last_nonzero_positions_at, _last_positions_read_verdict

    moment = now or _now_utc()
    verdict, effective_value = _classify_positions_read(
        free_pusd=free_pusd,
        raw_position_value=raw_position_value,
        positions_count=positions_count,
        prev_spendable_cash=_last_spendable_cash_usd,
        prev_position_value=_last_position_value_usd,
        prev_nonzero_positions_at=_last_nonzero_positions_at,
        now=moment,
        hold_bound_seconds=_positions_empty_hold_seconds(),
    )
    _last_positions_read_verdict = verdict
    if verdict == "blip_held":
        held_age = (
            (moment - _last_nonzero_positions_at).total_seconds()
            if _last_nonzero_positions_at
            else 0.0
        )
        logger.warning(
            "bankroll positions-read BLIP: /positions returned empty but a verified "
            "nonzero position value %.2f USD is only %.0fs old and free cash did not "
            "corroborate a redemption — HOLDING last-known-good position value for the "
            "LOSS-THRESHOLD base (hold bound %.0fs). NEW-ENTRY sizing EXCLUDES this "
            "phantom (sizing position value = 0.0) so Kelly cannot size off "
            "possibly-vanished equity.",
            effective_value, held_age, _positions_empty_hold_seconds(),
        )
        # Loss threshold holds the phantom; sizing excludes it.
        return effective_value, 0.0

    if verdict == "persistent_empty_accepted":
        logger.warning(
            "bankroll positions-read: /positions empty has persisted beyond the "
            "%.0fs hold bound — accepting position value 0.0 as truth.",
            _positions_empty_hold_seconds(),
        )
    _last_position_value_usd = effective_value
    if effective_value > _POSITION_VALUE_EPSILON_USD:
        _last_nonzero_positions_at = moment
    # Non-blip verdicts: the value is corroborated truth (venue-reported,
    # cash-corroborated, or genuinely flat) so sizing and loss-threshold agree.
    return effective_value, effective_value


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _fetch_balance() -> tuple[float, float, float]:
    """Single underlying call site for Polymarket account equity.

    Returns (equity_for_loss_threshold, free_pusd, equity_for_new_entry_sizing):
    - equity_for_loss_threshold = free cash + held (possibly-phantom under
      blip_held) position value — the daily-loss threshold base.
    - free_pusd = spendable BUY collateral.
    - equity_for_new_entry_sizing = free cash + ONLY corroborated position value
      (under blip_held the phantom is excluded), the conservative Kelly base.

    Imported lazily to avoid pulling polymarket SDK into modules that only
    care about the typed contract.
    """
    from src.data.polymarket_client import PolymarketClient

    client = PolymarketClient()
    free_pusd = float(client.get_wallet_balance())
    # NOTE: `or []` also coerces a None (failed/declined read) to empty — both
    # routes flow through the blip classifier below, which is the point: an
    # empty/failed positions read must not silently zero the equity base.
    positions = client.get_positions_from_api() or []
    raw_position_value = 0.0
    for position in positions:
        try:
            raw_position_value += max(0.0, float(position.get("current_value", 0.0) or 0.0))
        except (AttributeError, TypeError, ValueError):
            raise ValueError(f"bankroll_position_value_malformed:{position!r}")
    loss_threshold_position_value, sizing_position_value = _resolve_position_value(
        free_pusd, raw_position_value, len(positions)
    )
    return (
        free_pusd + loss_threshold_position_value,
        free_pusd,
        free_pusd + sizing_position_value,
    )


def _coerce_fetch_balance_result(
    result: float | tuple[float, ...],
) -> tuple[float, float | None, float | None]:
    """Normalize a _fetch_balance result to (equity, spendable, sizing_equity).

    Tuple shapes accepted for compatibility with test doubles that may patch
    _fetch_balance with the older 1-value or 2-value contracts:
    - (equity, spendable, sizing_equity): the current dual-bankroll contract.
    - (equity, spendable): pre-dual contract -> sizing_equity unknown (None).
    - bare float: equity only -> spendable and sizing_equity unknown (None).
    """
    if isinstance(result, tuple):
        if len(result) == 3:
            return float(result[0]), float(result[1]), float(result[2])
        if len(result) == 2:
            return float(result[0]), float(result[1]), None
        raise ValueError(f"bankroll_fetch_result_malformed:{result!r}")
    return float(result), None, None


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
    global _last_value_usd, _last_spendable_cash_usd, _last_sizing_equity_usd, _last_fetched_at

    with _lock:
        now = _now_utc()
        cached_value = _last_value_usd
        cached_fetched_at = _last_fetched_at
        cached_age = (now - cached_fetched_at).total_seconds() if cached_fetched_at else None

        # 1. Fresh cache hit — return without contacting the venue.
        if cached_value is not None and cached_age is not None and cached_age < max_age_seconds:
            return BankrollOfRecord(
                value_usd=cached_value,
                spendable_cash_usd=_last_spendable_cash_usd,
                fetched_at=cached_fetched_at.isoformat(),
                staleness_seconds=cached_age,
                cached=True,
                positions_read_verdict=_last_positions_read_verdict,
                equity_for_new_entry_sizing_usd=_last_sizing_equity_usd,
            )

        # 2. Cache miss or stale — try a live fetch.
        try:
            fresh_value, fresh_spendable_cash, fresh_sizing_equity = (
                _coerce_fetch_balance_result(_fetch_balance())
            )
            _last_value_usd = fresh_value
            _last_spendable_cash_usd = fresh_spendable_cash
            _last_sizing_equity_usd = fresh_sizing_equity
            _last_fetched_at = now
            return BankrollOfRecord(
                value_usd=fresh_value,
                spendable_cash_usd=fresh_spendable_cash,
                fetched_at=now.isoformat(),
                staleness_seconds=0.0,
                cached=False,
                positions_read_verdict=_last_positions_read_verdict,
                equity_for_new_entry_sizing_usd=fresh_sizing_equity,
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
                spendable_cash_usd=_last_spendable_cash_usd,
                fetched_at=cached_fetched_at.isoformat(),
                staleness_seconds=cached_age,
                cached=True,
                positions_read_verdict=_last_positions_read_verdict,
                equity_for_new_entry_sizing_usd=_last_sizing_equity_usd,
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
            spendable_cash_usd=_last_spendable_cash_usd,
            fetched_at=_last_fetched_at.isoformat(),
            staleness_seconds=age,
            cached=True,
            positions_read_verdict=_last_positions_read_verdict,
            equity_for_new_entry_sizing_usd=_last_sizing_equity_usd,
        )


def reset_cache_for_tests() -> None:
    """Clear the module-level cache. Tests only — not part of the public contract."""
    global _last_value_usd, _last_spendable_cash_usd, _last_sizing_equity_usd, _last_fetched_at
    global _last_position_value_usd, _last_nonzero_positions_at, _last_positions_read_verdict
    with _lock:
        _last_value_usd = None
        _last_spendable_cash_usd = None
        _last_sizing_equity_usd = None
        _last_fetched_at = None
        _last_position_value_usd = None
        _last_nonzero_positions_at = None
        _last_positions_read_verdict = "verified"

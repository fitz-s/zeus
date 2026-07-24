# Created: 2026-07-02
# Last reused/audited: 2026-07-02
# Authority basis: architecture/invariants.yaml
#   §1 "rate-limit budget + cancel-priority" (BUILD — only reactive 429 handling
#   exists) — W2.3 packet. Lands INERT: no production call site. W2.1's batch
#   wrapper and W3/W4 consumers wire this module in a later packet.
"""Venue rate-limit budget with cancel-priority (W2.3, inert).

Design law (packet brief): when the budget is scarce, killing stale exposure
(CANCEL) must beat adding new exposure (SUBMIT). This module gives the daemon
a single shared, injectable-clock, thread-safe token-bucket call budget for
venue order-endpoint calls, split into two priority classes so CANCEL is never
starved by SUBMIT traffic under pressure.

WHY A SHARED BUCKET, NOT TWO INDEPENDENT ONES
----------------------------------------------
Polymarket's own Cloudflare limits are already split per HTTP endpoint (POST
/order vs DELETE /order have separate ceilings — see SOURCE OF NUMBERS below),
so two fully independent buckets would never let SUBMIT and CANCEL compete and
"cancel outranks submit" would be a no-op. This budget instead models Zeus's
OWN internal pacing problem: a single shared HTTP connection pool
(``PUBLIC_CLOB_HTTP_LIMITS`` in src/data/polymarket_client.py, max 16
connections / 8 keepalive) fed by a 20+2 worker thread pool that can burst
near-simultaneous submits and cancels in the same decision cycle. One shared
bucket with a reserve floor that only CANCEL may draw past is the mechanism:
SUBMIT is refused once the bucket drops to the reserve; CANCEL keeps draining
underneath it.

SOURCE OF NUMBERS (venue-published, https://docs.polymarket.com/api-reference/rate-limits,
fetched 2026-07-02):

    | Endpoint                    | Burst Limit     | Sustained Limit       |
    |------------------------------|-----------------|------------------------|
    | POST /order                  | 5,000 req / 10s | 120,000 req / 10 min   |
    | DELETE /order                | 5,000 req / 10s | 120,000 req / 10 min   |
    | POST /orders (batch)         | 2,000 req / 10s | 21,000 req / 10 min    |
    | DELETE /orders (batch)       | 2,000 req / 10s | 15,000 req / 10 min    |
    | DELETE /cancel-all           |   250 req / 10s |  6,000 req / 10 min    |
    | DELETE /cancel-market-orders | 1,500 req / 10s | 21,000 req / 10 min    |

Limits are enforced by Cloudflare on a sliding window and THROTTLE (delay)
rather than reject — a 429 from Polymarket's CLOB is still possible under
burst but is not the only signal of pressure.

The sustained POST/DELETE /order ceiling is 120,000 req/10min = 200 req/s.
Zeus's real order volume (single API key, one decision cycle at a time) is
orders of magnitude below that. This budget does NOT try to approach the
venue ceiling — CONSERVATIVE GUESS, flagged for measurement: default
``rate_per_sec=20.0`` / ``capacity_tokens=20.0`` is 10% of the venue's
sustained per-second ceiling, chosen as a self-imposed daemon-health gate
rather than a venue-derived number (Zeus has no live order-volume corpus yet
to size against). Tighten once W3/W4 wire a consumer and real submit/cancel
counts exist.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Mapping, Optional

logger = logging.getLogger("zeus.venue_rate_budget")

Clock = Callable[[], float]

DEFAULT_429_BACKOFF_SECONDS = 15.0  # matches src/data/openmeteo_client.py DEFAULT_429_FALLBACK_WAIT


class RequestClass(Enum):
    """Priority class for a venue API call competing for the shared budget."""

    SUBMIT = "submit"
    CANCEL = "cancel"


class BudgetDecision(Enum):
    GRANTED = "granted"
    DEFERRED = "deferred"  # budget momentarily short; retry after wait_seconds
    DENIED = "denied"  # 429 cooldown active for this class; retry after wait_seconds


@dataclass(frozen=True)
class BudgetResult:
    decision: BudgetDecision
    request_class: RequestClass
    wait_seconds: float = 0.0

    @property
    def granted(self) -> bool:
        return self.decision is BudgetDecision.GRANTED


@dataclass(frozen=True)
class RateBudgetConfig:
    """Token-bucket parameters. See module docstring for the source of numbers."""

    capacity_tokens: float = 20.0
    rate_per_sec: float = 20.0
    # Floor SUBMIT may not drain past; CANCEL may drain into it. This is the
    # cancel-priority mechanism.
    cancel_reserve_tokens: float = 5.0
    default_429_backoff_seconds: float = DEFAULT_429_BACKOFF_SECONDS

    def __post_init__(self) -> None:
        if self.capacity_tokens <= 0:
            raise ValueError("capacity_tokens must be > 0")
        if self.rate_per_sec <= 0:
            raise ValueError("rate_per_sec must be > 0")
        if self.cancel_reserve_tokens < 0:
            raise ValueError("cancel_reserve_tokens must be >= 0")
        if self.cancel_reserve_tokens > self.capacity_tokens:
            raise ValueError("cancel_reserve_tokens must be <= capacity_tokens")
        if self.default_429_backoff_seconds <= 0:
            raise ValueError("default_429_backoff_seconds must be > 0")

    @classmethod
    def from_settings(cls, settings: object) -> "RateBudgetConfig":
        """Read an optional ``execution.venue_rate_budget`` override block.

        Strict Settings (src/config.py) requires ``execution`` to exist but
        does not require this sub-key — a settings.json without it (every
        settings.json today) falls back to the documented defaults above.
        Duck-typed on ``__getitem__`` so tests can pass a plain dict.
        """
        defaults = cls()
        try:
            execution_cfg = settings["execution"]  # type: ignore[index]
        except (KeyError, TypeError):
            return defaults
        raw = execution_cfg.get("venue_rate_budget") if isinstance(execution_cfg, Mapping) else None
        if not raw:
            return defaults
        return cls(
            capacity_tokens=float(raw.get("capacity_tokens", defaults.capacity_tokens)),
            rate_per_sec=float(raw.get("rate_per_sec", defaults.rate_per_sec)),
            cancel_reserve_tokens=float(
                raw.get("cancel_reserve_tokens", defaults.cancel_reserve_tokens)
            ),
            default_429_backoff_seconds=float(
                raw.get("default_429_backoff_seconds", defaults.default_429_backoff_seconds)
            ),
        )


@dataclass(frozen=True)
class RetryInstruction:
    """Backoff instruction derived from a venue 429 response."""

    request_class: RequestClass
    retry_after_seconds: float
    source: str  # "header" | "default"


def parse_retry_after_seconds(raw: Optional[str]) -> Optional[float]:
    """Parse a ``Retry-After`` header value as delay-seconds.

    Matches src/data/openmeteo_client.py's existing convention: numeric
    delay-seconds only. The HTTP-date form (RFC 7231) is out of scope — no
    Zeus venue client parses it today; callers fall back to the default
    backoff via :func:`retry_instruction_from_response`.
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return value if value >= 0 else None


def retry_instruction_from_response(
    request_class: RequestClass,
    *,
    status_code: Optional[int],
    headers: Optional[Mapping[str, str]] = None,
    default_backoff_seconds: float = DEFAULT_429_BACKOFF_SECONDS,
) -> Optional[RetryInstruction]:
    """Build a :class:`RetryInstruction` from a response's status/headers.

    Returns ``None`` when ``status_code`` is not 429 (no instruction to give).
    Honors ``Retry-After`` when present and parseable; otherwise falls back to
    ``default_backoff_seconds``.
    """
    if status_code != 429:
        return None
    retry_after = None
    if headers:
        raw = headers.get("Retry-After")
        if raw is None:
            raw = headers.get("retry-after")
        retry_after = parse_retry_after_seconds(raw)
    if retry_after is not None:
        return RetryInstruction(request_class, retry_after, "header")
    return RetryInstruction(request_class, float(default_backoff_seconds), "default")


def retry_instruction_from_exception(
    request_class: RequestClass,
    exc: BaseException,
    *,
    default_backoff_seconds: float = DEFAULT_429_BACKOFF_SECONDS,
) -> Optional[RetryInstruction]:
    """Build a :class:`RetryInstruction` from an httpx-shaped exception.

    Duck-typed on ``exc.response.status_code`` / ``exc.response.headers``
    (matches ``httpx.HTTPStatusError``, per
    tests/test_promote_pending_trades.py's existing 429 fixture shape) so
    this module never has to import httpx directly.
    """
    response = getattr(exc, "response", None)
    if response is None:
        return None
    status_code = getattr(response, "status_code", None)
    headers = getattr(response, "headers", None)
    return retry_instruction_from_response(
        request_class,
        status_code=status_code,
        headers=headers,
        default_backoff_seconds=default_backoff_seconds,
    )


class VenueRateBudget:
    """Shared token-bucket call budget with CANCEL-priority under pressure.

    Thread-safe (single lock guards refill + spend); clock is injectable so
    tests never sleep. See module docstring for the design law and the
    reserve-floor mechanism.
    """

    def __init__(
        self,
        config: Optional[RateBudgetConfig] = None,
        *,
        clock: Clock = time.monotonic,
    ) -> None:
        self._config = config or RateBudgetConfig()
        self._clock = clock
        self._lock = threading.Lock()
        self._tokens = self._config.capacity_tokens
        self._last_refill = self._clock()
        self._cooldown_until: dict[RequestClass, float] = {}
        self._counters: dict[RequestClass, dict[str, int]] = {
            cls: {"granted": 0, "deferred": 0, "denied": 0} for cls in RequestClass
        }

    def _refill_locked(self) -> None:
        now = self._clock()
        elapsed = now - self._last_refill
        if elapsed > 0:
            self._tokens = min(
                self._config.capacity_tokens,
                self._tokens + elapsed * self._config.rate_per_sec,
            )
        self._last_refill = now

    def try_acquire(self, request_class: RequestClass) -> BudgetResult:
        """Non-blocking: attempt to spend one token for ``request_class``.

        CANCEL may drain the bucket down to zero. SUBMIT may only drain it
        down to ``cancel_reserve_tokens`` — the cancel-priority floor.
        """
        with self._lock:
            now = self._clock()
            deadline = self._cooldown_until.get(request_class)
            if deadline is not None:
                if now < deadline:
                    self._counters[request_class]["denied"] += 1
                    return BudgetResult(BudgetDecision.DENIED, request_class, deadline - now)
                del self._cooldown_until[request_class]

            self._refill_locked()
            floor = 0.0 if request_class is RequestClass.CANCEL else self._config.cancel_reserve_tokens
            if self._tokens - 1.0 >= floor - 1e-9:
                self._tokens -= 1.0
                self._counters[request_class]["granted"] += 1
                return BudgetResult(BudgetDecision.GRANTED, request_class, 0.0)

            deficit = (floor + 1.0) - self._tokens
            wait_seconds = max(0.0, deficit / self._config.rate_per_sec)
            self._counters[request_class]["deferred"] += 1
            return BudgetResult(BudgetDecision.DEFERRED, request_class, wait_seconds)

    def note_rate_limited(self, request_class: RequestClass, retry_after_seconds: float) -> None:
        """Engage a per-class 429 cooldown. Extends, never shortens, an active one."""
        with self._lock:
            now = self._clock()
            deadline = now + max(0.0, retry_after_seconds)
            existing = self._cooldown_until.get(request_class, now)
            self._cooldown_until[request_class] = max(existing, deadline)
            effective = self._cooldown_until[request_class]
        logger.warning(
            "venue rate budget: %s 429 cooldown engaged for %.1fs (deadline clock=%.3f)",
            request_class.value,
            retry_after_seconds,
            effective,
        )

    def note_429_response(
        self,
        request_class: RequestClass,
        *,
        status_code: Optional[int],
        headers: Optional[Mapping[str, str]] = None,
    ) -> Optional[RetryInstruction]:
        """Parse + apply a 429 response's backoff in one call. No-op if not 429."""
        instruction = retry_instruction_from_response(
            request_class,
            status_code=status_code,
            headers=headers,
            default_backoff_seconds=self._config.default_429_backoff_seconds,
        )
        if instruction is not None:
            self.note_rate_limited(request_class, instruction.retry_after_seconds)
        return instruction

    def note_429_exception(
        self, request_class: RequestClass, exc: BaseException
    ) -> Optional[RetryInstruction]:
        """Parse + apply a 429 exception's backoff in one call. No-op if not a 429."""
        instruction = retry_instruction_from_exception(
            request_class, exc, default_backoff_seconds=self._config.default_429_backoff_seconds
        )
        if instruction is not None:
            self.note_rate_limited(request_class, instruction.retry_after_seconds)
        return instruction

    def snapshot(self) -> dict[str, dict[str, int]]:
        """Granted/deferred/denied counters per class."""
        with self._lock:
            return {cls.value: dict(counts) for cls, counts in self._counters.items()}

    def emit_snapshot(self) -> dict[str, dict[str, int]]:
        """Emit one structured INFO log line with current counters (W0.2-style lane).

        Side-effect-free beyond logging — safe to call from a scheduler or
        health-check loop at whatever cadence the metrics lane uses.
        """
        counts = self.snapshot()
        submit = counts[RequestClass.SUBMIT.value]
        cancel = counts[RequestClass.CANCEL.value]
        logger.info(
            "venue rate budget snapshot: submit granted=%d deferred=%d denied=%d | "
            "cancel granted=%d deferred=%d denied=%d",
            submit["granted"],
            submit["deferred"],
            submit["denied"],
            cancel["granted"],
            cancel["deferred"],
            cancel["denied"],
        )
        return counts

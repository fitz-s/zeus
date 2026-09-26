"""Shared Open-Meteo HTTP client with retry, 429 handling, and quota tracking.

Phase C extraction: replaces duplicated httpx.get + retry logic in
hourly_instants_append, solar_append, and forecasts_append.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import logging
import math
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from enum import Enum
from typing import Mapping
from urllib.parse import urlsplit

import httpx

from src.data.openmeteo_quota import (
    DAILY_HARD_CAP,
    MAINTENANCE_DAILY_LIMIT,
    PRIORITY_DAILY_LIMIT,
    OpenMeteoQuotaTracker,
    quota_tracker,
)
from src.data.openmeteo_response_store import (
    ExactRequest,
    OpenMeteoResponseStore,
    exact_request,
    meta_slug,
    meta_url,
    runtime_response_store,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Canonical base URLs
# ---------------------------------------------------------------------------

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
PREVIOUS_RUNS_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"

# ---------------------------------------------------------------------------
# Defaults (can be overridden per-call)
# ---------------------------------------------------------------------------

DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_SEC = 2.0
DEFAULT_429_FALLBACK_WAIT = 15.0
SINGLE_RUNS_OUTCOME_CLASSIFIER_REVISION = "provider_reason_v2"

# Top-level ``httpx.get`` creates and tears down a connection pool for every
# request. Open-Meteo is a recurring multi-lane source, so that shape pays a
# fresh TCP/TLS handshake on every city/source-clock pass. One bounded client
# per daemon keeps transport reuse below the quota/request-lease authority: the
# caller still acquires and settles one canonical lease for every HTTP attempt.
_SHARED_HTTP_CLIENT = httpx.Client(
    http2=False,
    limits=httpx.Limits(
        max_connections=16,
        max_keepalive_connections=8,
        keepalive_expiry=60.0,
    ),
)
atexit.register(_SHARED_HTTP_CLIENT.close)

# One durable answer store per process (None under test). ``fetch()`` serves an
# exact-run answer from it whenever that answer provably equals the provider's.
response_store: OpenMeteoResponseStore | None = runtime_response_store()
# How long a caller waits for another process's in-flight twin before paying itself.
IN_FLIGHT_WAIT_SECONDS = 20.0
IN_FLIGHT_POLL_SECONDS = 0.25


class OpenMeteoRetryClass(str, Enum):
    """Provider-specific disposition for an HTTP response."""

    TERMINAL = "terminal"
    CONDITIONAL = "conditional"
    RETRYABLE = "retryable"
    RATE_LIMITED = "rate_limited"


@dataclass(frozen=True)
class OpenMeteoHTTPOutcome:
    """Redacted, durable classification of one Open-Meteo HTTP response."""

    status_code: int
    retry_class: OpenMeteoRetryClass
    retry_after_seconds: float | None
    reason: str
    body_sha256: str

    def persisted(self) -> dict[str, object]:
        payload = asdict(self)
        payload["retry_class"] = self.retry_class.value
        return payload

    @classmethod
    def from_persisted(cls, payload: Mapping[str, object]) -> "OpenMeteoHTTPOutcome":
        return cls(
            status_code=int(payload.get("status_code") or 0),
            retry_class=OpenMeteoRetryClass(str(payload.get("retry_class") or "terminal")),
            retry_after_seconds=(
                float(payload["retry_after_seconds"])
                if payload.get("retry_after_seconds") is not None
                else None
            ),
            reason=str(payload.get("reason") or "persisted_terminal"),
            body_sha256=str(payload.get("body_sha256") or ""),
        )


class OpenMeteoHTTPStatusError(httpx.HTTPStatusError):
    """``HTTPStatusError`` carrying a redacted provider outcome."""

    def __init__(self, response: httpx.Response, outcome: OpenMeteoHTTPOutcome) -> None:
        request = getattr(response, "request", None) or httpx.Request(
            "GET", "https://open-meteo.invalid"
        )
        super().__init__(
            f"Open-Meteo HTTP {outcome.status_code} ({outcome.retry_class.value}:{outcome.reason})",
            request=request,
            response=response,
        )
        self.outcome = outcome


class OpenMeteoRequestSuppressed(RuntimeError):
    """A prior terminal response already negatively cached this exact request."""

    def __init__(self, outcome: OpenMeteoHTTPOutcome) -> None:
        super().__init__(
            f"Open-Meteo request terminally cached "
            f"(status={outcome.status_code} class={outcome.retry_class.value})"
        )
        self.outcome = outcome


class OpenMeteoPreflightDenialReason(str, Enum):
    """Typed reason for a request rejected before any HTTP attempt."""

    RESERVE_PROTECTED = "RESERVE_PROTECTED"
    GLOBAL_COOLDOWN = "GLOBAL_COOLDOWN"
    PRIORITY_DAILY_LIMIT = "PRIORITY_DAILY_LIMIT"
    CRITICAL_HARD_DAILY_LIMIT = "CRITICAL_HARD_DAILY_LIMIT"
    DAILY_LIMIT = "DAILY_LIMIT"
    HOURLY_LIMIT = "HOURLY_LIMIT"
    MINUTE_LIMIT = "MINUTE_LIMIT"
    REQUEST_EMBARGO = "REQUEST_EMBARGO"
    REQUEST_IN_FLIGHT = "REQUEST_IN_FLIGHT"
    REQUEST_STATE_CAPACITY = "REQUEST_STATE_CAPACITY"
    REQUEST_TERMINAL = "REQUEST_TERMINAL"
    SHARED_QUOTA_UNAVAILABLE = "SHARED_QUOTA_UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


class OpenMeteoLocalPreflightQuotaDenied(RuntimeError):
    """A local quota/request lease was denied before provider I/O."""

    def __init__(
        self,
        reason: OpenMeteoPreflightDenialReason,
        *,
        detail: str | None,
    ) -> None:
        self.reason = reason
        self.detail = detail
        if reason in {
            OpenMeteoPreflightDenialReason.REQUEST_EMBARGO,
            OpenMeteoPreflightDenialReason.REQUEST_IN_FLIGHT,
        }:
            message = f"Open-Meteo request embargoed ({detail or reason.value})"
        else:
            message = (
                "Open-Meteo quota exhausted "
                f"(preflight_reason={reason.value}; detail={detail or 'missing'})"
            )
        super().__init__(message)


def _quota_limit_detail(detail: str) -> tuple[str, int] | None:
    label, separator, counts = detail.partition("=")
    if not separator or label not in {"day_limit", "hour_limit", "minute_limit"}:
        return None
    _, slash, limit = counts.partition("/")
    if not slash:
        return None
    try:
        return label, int(limit)
    except ValueError:
        return None


def _preflight_denial_reason(detail: str | None) -> OpenMeteoPreflightDenialReason:
    """Type the quota authority's structured denial detail exactly once."""

    if not detail:
        return OpenMeteoPreflightDenialReason.UNKNOWN
    if detail.startswith("request_retry_until="):
        return OpenMeteoPreflightDenialReason.REQUEST_EMBARGO
    if detail.startswith("request_in_flight_until="):
        return OpenMeteoPreflightDenialReason.REQUEST_IN_FLIGHT
    if detail.startswith("request_state_capacity="):
        return OpenMeteoPreflightDenialReason.REQUEST_STATE_CAPACITY
    if detail.startswith("request_terminal="):
        return OpenMeteoPreflightDenialReason.REQUEST_TERMINAL
    if detail == "shared_quota_unavailable":
        return OpenMeteoPreflightDenialReason.SHARED_QUOTA_UNAVAILABLE
    if detail.startswith("cooldown_until="):
        return OpenMeteoPreflightDenialReason.GLOBAL_COOLDOWN
    limit_detail = _quota_limit_detail(detail)
    if limit_detail is None:
        return OpenMeteoPreflightDenialReason.UNKNOWN
    label, limit = limit_detail
    if label == "hour_limit":
        return OpenMeteoPreflightDenialReason.HOURLY_LIMIT
    if label == "minute_limit":
        return OpenMeteoPreflightDenialReason.MINUTE_LIMIT
    if limit == MAINTENANCE_DAILY_LIMIT:
        return OpenMeteoPreflightDenialReason.RESERVE_PROTECTED
    if limit == PRIORITY_DAILY_LIMIT:
        return OpenMeteoPreflightDenialReason.PRIORITY_DAILY_LIMIT
    if limit == DAILY_HARD_CAP:
        return OpenMeteoPreflightDenialReason.CRITICAL_HARD_DAILY_LIMIT
    return OpenMeteoPreflightDenialReason.DAILY_LIMIT


def request_identity(
    url: str,
    params: dict,
    *,
    conditional_status_codes: frozenset[int] = frozenset(),
) -> str:
    """Return a stable identity for the executable request and its terminality law."""

    identity: dict[str, object] = {"url": url, "params": params}
    if urlsplit(url).netloc == "single-runs-api.open-meteo.com":
        # SCOPE: exact Single Runs request identities classified under the old law.
        # DRAIN: the next scheduled poll retries once under this revision-keyed identity.
        # RESET: success or bounded conditional retry replaces that identity's attempt state.
        identity["outcome_classifier_revision"] = SINGLE_RUNS_OUTCOME_CLASSIFIER_REVISION
    if conditional_status_codes:
        identity["conditional_status_codes"] = sorted(conditional_status_codes)
    payload = json.dumps(
        identity,
        default=str,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _parameter_count(value: object) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        return len([item for item in value.split(",") if item.strip()])
    if isinstance(value, (list, tuple)):
        return len(value)
    return 1


def _provider_quota_cost(params: Mapping[str, object]) -> int:
    """Conservatively meter Open-Meteo's documented weighted API-call units."""

    locations = max(
        1,
        _parameter_count(params.get("latitude")),
        _parameter_count(params.get("longitude")),
    )
    variables = max(
        1,
        sum(
            _parameter_count(params.get(key))
            for key in ("hourly", "daily", "current", "minutely_15")
        ),
    )
    hours = max(
        float(params.get("forecast_hours") or 0),
        24.0 * float(params.get("forecast_days") or 0),
    ) + max(
        float(params.get("past_hours") or 0),
        24.0 * float(params.get("past_days") or 0),
    )
    explicit_days = 0
    try:
        start = date.fromisoformat(str(params.get("start_date")))
        end = date.fromisoformat(str(params.get("end_date")))
        explicit_days = max(0, (end - start).days + 1)
    except ValueError:
        pass
    days = max(1.0, hours / 24.0, float(explicit_days))
    weighted = locations * max(1.0, variables / 10.0) * max(1.0, days / 14.0)
    return max(1, math.ceil(weighted))


def _endpoint_for_url(url: str) -> str:
    parsed = urlsplit(url)
    return f"{parsed.netloc}{parsed.path}"[:160]


def _retry_after_seconds(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return 0.0
        if retry_at.tzinfo is None:
            return 0.0
        return max(0.0, (retry_at - datetime.now(retry_at.tzinfo)).total_seconds())


def _provider_reason(response: httpx.Response) -> str | None:
    """Return only the provider's explicit retry reason; never retain arbitrary body text."""

    try:
        payload = response.json()
    except (AttributeError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    reason = " ".join(str(payload.get("reason") or "").strip().lower().split())
    if reason in {"run_not_published", "availability"}:
        return reason
    if reason.startswith("the requested model run is not available"):
        return "run_not_published"
    for window in ("daily", "hourly", "minutely"):
        if reason.startswith(f"{window} api request limit exceeded"):
            return f"{window}_api_request_limit_exceeded"
    return None


def _rate_limit_wait(outcome: OpenMeteoHTTPOutcome, attempt: int) -> float:
    if outcome.retry_after_seconds:
        return outcome.retry_after_seconds
    now = datetime.now(timezone.utc)
    if outcome.reason == "daily_api_request_limit_exceeded":
        boundary = datetime.combine(
            now.date() + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc
        )
    elif outcome.reason == "hourly_api_request_limit_exceeded":
        boundary = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    elif outcome.reason == "minutely_api_request_limit_exceeded":
        boundary = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
    else:
        return DEFAULT_429_FALLBACK_WAIT * (attempt + 1)
    return max(1.0, (boundary - now).total_seconds() + 1.0)


def _http_outcome(
    response: httpx.Response,
    *,
    conditional_status_codes: frozenset[int] = frozenset(),
) -> OpenMeteoHTTPOutcome:
    status_code = int(response.status_code)
    retry_after = _retry_after_seconds(response.headers.get("Retry-After"))
    provider_reason = _provider_reason(response)
    if status_code == 429:
        retry_class = OpenMeteoRetryClass.RATE_LIMITED
    elif status_code in conditional_status_codes or (
        status_code == 400 and provider_reason is not None
    ):
        retry_class = OpenMeteoRetryClass.CONDITIONAL
    elif status_code in {408, 425} or status_code >= 500:
        retry_class = OpenMeteoRetryClass.RETRYABLE
    else:
        retry_class = OpenMeteoRetryClass.TERMINAL
    body = getattr(response, "content", b"") or b""
    if isinstance(body, str):
        body = body.encode("utf-8", errors="replace")
    return OpenMeteoHTTPOutcome(
        status_code=status_code,
        retry_class=retry_class,
        retry_after_seconds=retry_after or None,
        reason=provider_reason or f"http_{status_code}",
        body_sha256=hashlib.sha256(body).hexdigest()[:16],
    )


def http_outcome_payload(error: object) -> dict[str, object] | None:
    """Serialize a typed client outcome for BPF/production reports without body leakage."""

    outcome = getattr(error, "outcome", None)
    return outcome.persisted() if isinstance(outcome, OpenMeteoHTTPOutcome) else None


def _refresh_run_state(
    store: OpenMeteoResponseStore,
    req: ExactRequest,
    *,
    tracker: OpenMeteoQuotaTracker,
    client: httpx.Client | None,
    timeout: float,
) -> None:
    """Re-read, unmetered and within the caller's timeout, the run state ``req`` needs."""

    stop = time.monotonic() + float(timeout)
    for slug in store.stale_slugs(req):
        remaining = stop - time.monotonic()
        if remaining <= 0.05:
            return
        try:
            fetch(
                meta_url(slug),
                {},
                timeout=min(remaining, 10.0),
                max_retries=1,
                endpoint_label=f"response_store_meta_{slug}",
                fast_fail_429=True,
                quota=tracker,
                client=client,
                count_toward_quota=False,
                store=store,
            )
        except Exception as exc:  # noqa: BLE001 -- unproven state takes the network path.
            logger.debug("Open-Meteo run state for %s unavailable: %s", slug, exc)


def _await_twin(
    store: OpenMeteoResponseStore,
    tracker: OpenMeteoQuotaTracker,
    request_id: str,
    req: ExactRequest,
    deadline: float,
) -> object | None:
    """Wait for a provable answer another caller is fetching for this exact request."""

    if store.proofs(req) is None:
        return None
    while time.monotonic() < deadline:
        time.sleep(IN_FLIGHT_POLL_SECONDS)
        held = store.lookup(request_id, req)
        if held is not None or not tracker.request_in_flight(request_id):
            return held
    return None


def fetch(
    url: str,
    params: dict,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    max_retries: int = DEFAULT_MAX_RETRIES,
    backoff_sec: float = DEFAULT_BACKOFF_SEC,
    endpoint_label: str = "",
    fast_fail_429: bool = False,
    quota: OpenMeteoQuotaTracker | None = None,
    client: httpx.Client | None = None,
    count_toward_quota: bool = True,
    conditional_status_codes: frozenset[int] = frozenset(),
    store: OpenMeteoResponseStore | None = None,
) -> dict:
    """GET an Open-Meteo endpoint with retries, 429 handling, and quota tracking.

    Returns the parsed JSON response dict.

    Raises:
        httpx.HTTPError: after all retries exhausted on transport errors.
        OpenMeteoLocalPreflightQuotaDenied: if no request lease is granted.

    ``fast_fail_429`` is for callers with an independent transport fallback. They still mark
    the quota cooldown, but they receive the 429 immediately instead of sleeping inside this
    shared client and blocking the fallback ladder.

    LAW: a metered request is sent only when its answer can differ from one already held.
    An exact-run request (see ``openmeteo_response_store``) is answered from the durable
    store at zero quota cost until its provider run state changes, and a caller whose
    identical twin is in flight in another process waits for that answer instead of
    paying again.
    """
    tracker = quota or quota_tracker
    answers = store or response_store
    request_id = request_identity(
        url,
        params,
        conditional_status_codes=conditional_status_codes,
    )
    quota_cost = _provider_quota_cost(params) if count_toward_quota else 1
    endpoint = _endpoint_for_url(url)
    job = endpoint_label or endpoint
    req = exact_request(url, params) if answers is not None else None
    if req is not None:
        _refresh_run_state(answers, req, tracker=tracker, client=client, timeout=timeout)
        held = answers.lookup(request_id, req)
        if held is not None:
            answers.note_served(job, quota_cost)
            return held
    twin_deadline = time.monotonic() + min(IN_FLIGHT_WAIT_SECONDS, float(timeout))
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        while True:
            allowed, reason, lease_id = tracker.acquire_request(
                request_id,
                endpoint=endpoint,
                job=job,
                lease_seconds=max(float(timeout) + 5.0, DEFAULT_TIMEOUT),
                count_toward_quota=count_toward_quota,
                quota_cost=quota_cost,
            )
            if (
                allowed
                or req is None
                or _preflight_denial_reason(reason)
                is not OpenMeteoPreflightDenialReason.REQUEST_IN_FLIGHT
            ):
                break
            # Another caller is paying for this exact answer; wait for it instead.
            held = _await_twin(answers, tracker, request_id, req, twin_deadline)
            if held is not None:
                answers.note_served(job, quota_cost)
                return held
            if time.monotonic() >= twin_deadline or tracker.request_in_flight(request_id):
                break
        if not allowed:
            if reason and reason.startswith("request_terminal="):
                persisted = tracker.request_terminal_outcome(request_id)
                if persisted is not None:
                    raise OpenMeteoRequestSuppressed(
                        OpenMeteoHTTPOutcome.from_persisted(persisted)
                    )
            raise OpenMeteoLocalPreflightQuotaDenied(
                _preflight_denial_reason(reason),
                detail=reason,
            )
        if answers is not None and count_toward_quota:
            answers.note_metered(request_id, job, quota_cost)
        # Proven before the send: an answer carries the state it was fetched under.
        proofs = answers.proofs(req) if req is not None else None
        try:
            get = client.get if client is not None else _SHARED_HTTP_CLIENT.get
            resp = get(url, params=params, timeout=timeout)

            if resp.status_code >= 400:
                outcome = _http_outcome(
                    resp,
                    conditional_status_codes=conditional_status_codes,
                )
                error = OpenMeteoHTTPStatusError(resp, outcome)
                if outcome.retry_class is OpenMeteoRetryClass.RATE_LIMITED:
                    wait = _rate_limit_wait(outcome, attempt)
                    tracker.note_rate_limited(int(wait), endpoint=endpoint)
                    tracker.record_request_retry(
                        request_id,
                        endpoint=endpoint,
                        job=job,
                        retry_after_seconds=wait,
                        lease_id=lease_id,
                        http_outcome=outcome.persisted(),
                    )
                    if fast_fail_429:
                        logger.warning(
                            "Open-Meteo 429 on attempt %d%s — fast-fail to fallback ladder; no client sleep",
                            attempt + 1,
                            f" [{endpoint_label}]" if endpoint_label else "",
                        )
                    else:
                        logger.warning(
                            "Open-Meteo 429 on attempt %d%s — persisted cooldown; deferring retry",
                            attempt + 1,
                            f" [{endpoint_label}]" if endpoint_label else "",
                        )
                    raise error
                if outcome.retry_class is OpenMeteoRetryClass.TERMINAL:
                    tracker.record_request_terminal(
                        request_id,
                        endpoint=endpoint,
                        job=job,
                        lease_id=lease_id,
                        http_outcome=outcome.persisted(),
                    )
                raise error

            payload = resp.json()
            if answers is not None:
                slug = meta_slug(url)
                if slug is not None:
                    answers.record_meta(slug, payload)
                if proofs is not None:
                    answers.put(request_id, req, proofs, payload)
                if count_toward_quota:
                    answers.note_success(request_id)
            recorded = tracker.record_request_success(
                request_id,
                endpoint=endpoint,
                job=job,
                lease_id=lease_id,
            )
            if not recorded:
                raise RuntimeError(
                    "Open-Meteo request lease lost; discarded unowned response"
                )
            return payload

        except httpx.HTTPError as e:
            last_exc = e
            outcome = getattr(e, "outcome", None)
            if isinstance(outcome, OpenMeteoHTTPOutcome) and outcome.retry_class in {
                OpenMeteoRetryClass.RATE_LIMITED,
                OpenMeteoRetryClass.TERMINAL,
            }:
                raise
            retry_delay = tracker.record_request_retry(
                request_id,
                endpoint=endpoint,
                job=job,
                lease_id=lease_id,
                http_outcome=(
                    outcome.persisted()
                    if isinstance(outcome, OpenMeteoHTTPOutcome)
                    else None
                ),
            )
            if attempt < max_retries - 1:
                wait = max(backoff_sec * (attempt + 1), float(retry_delay))
                logger.debug(
                    "Open-Meteo retry %d/%d%s: %s — waiting %.1fs",
                    attempt + 1,
                    max_retries,
                    f" [{endpoint_label}]" if endpoint_label else "",
                    e,
                    wait,
                )
                time.sleep(wait)
                continue

    raise last_exc or RuntimeError("Open-Meteo fetch exhausted retries")

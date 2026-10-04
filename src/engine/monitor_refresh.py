# Created: prior
# Last audited: 2026-08-27
# Authority basis: current replacement probability and held-position redecision law.
"""Monitor refresh: recompute fresh probability for held positions.

Blueprint v2 §7 Layer 1: recompute the held-side probability.

PRIMARY AUTHORITY (corrected 2026-07-14): Day0 absorbing hard facts dominate
model belief when qualified; otherwise held positions use the exact current
global probability builder used by entry and global SELL/HOLD/CASH comparison.
Non-Day0 positions read the multi-model fused posterior ``forecast_posteriors``.
The legacy ENS/Day0 member-counting path is retained only for positions that
lack a canonical Polymarket condition identity; it cannot substitute for a
failed current authority on a live identified position.
Uses full p_raw_vector with MC instrument noise (not simplified _estimate_bin_p_raw).
"""

import logging
import math
import sqlite3
import copy
import hashlib
import json
import threading
import time
from collections.abc import Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np

from src.config import (
    cities_by_name,
    edge_n_bootstrap,
)
from src.contracts import EntryMethod
from src.contracts.exceptions import ObservationUnavailableError
from src.contracts.probability_arithmetic import one_minus
from src.data.market_scanner import _parse_temp_range, get_last_scan_authority, get_sibling_outcomes
from src.data.observation_client import (
    _DAY0_COVERAGE_WINDOW_GRACE_HOURS,
    Day0ObservationContext,
)
from src.data.polymarket_client import PolymarketClient
from src.engine.evaluator import (
    DAY0_EXECUTABLE_OBSERVATION_SOURCES_BY_SETTLEMENT_TYPE,
    _parse_day0_observation_time_utc,
)
from src.observability.counters import increment as _cnt_inc
from src.state.chain_reconciliation import resolve_position_metric
from src.state.portfolio import (
    Position,
    flash_crash_catastrophe_velocity,
    flash_crash_confirmations,
)
from src.strategy.market_fusion import (
    MODEL_ONLY_POSTERIOR_MODE,
    compute_posterior,
    vwmp,
)

logger = logging.getLogger(__name__)
_MONITOR_PROBABILITY_FRESH_ATTR = "_monitor_probability_is_fresh"
_DAY0_ZERO_PROBABILITY_EXIT_AUTHORITY_ATTR = "_day0_zero_probability_exit_authority"
_GLOBAL_MONITOR_SAMPLES_ATTR = "_current_global_held_probability_samples"
_GLOBAL_MONITOR_ALPHA_ATTR = "_current_global_probability_band_alpha"
_MONITOR_PROBABILITY_RECEIPT_ATTR = "_monitor_probability_receipt"
_MONITOR_PRIMARY_BELIEF_READ_ELAPSED_SECONDS_ATTR = (
    "_monitor_primary_belief_read_elapsed_seconds"
)
_MONITOR_PREFETCHED_ORDERBOOKS_ATTR = "_zeus_monitor_prefetched_orderbooks"
_MONITOR_PREFETCH_ATTEMPTED_TOKENS_ATTR = (
    "_zeus_monitor_prefetch_attempted_tokens"
)
_CURRENT_MONITOR_ORDERBOOK_BATCH_LOCK = threading.Lock()
_CURRENT_MONITOR_ORDERBOOK_BATCH: tuple[dict[str, dict], datetime | None] = (
    {},
    None,
)
_HELD_MONITOR_DEADLINE_ATTR = "_zeus_held_monitor_deadline_monotonic"
_HELD_MONITOR_MIN_ORDER_SIZE_ATTR = "_zeus_held_monitor_min_order_size"
# This is intentionally separate from monitor-price freshness.  A market-channel
# BBA twin can truthfully carry a current price while lacking the full depth
# required to authorize a SELL.
_HELD_MONITOR_FULL_DEPTH_ACTION_AUTHORITY_ATTR = (
    "_zeus_held_monitor_full_depth_action_authority"
)
HELD_MONITOR_PRIMARY_BELIEF_READ_MAX_SECONDS = 5.0
HELD_MONITOR_QUOTE_READ_MAX_SECONDS = 1.0
#: One held book read through the spawned hard-deadline child: measured
#: 2026-10-04 at ~0.73 s spawn+import (``-m src.main`` parent) plus ~1.1-1.5 s
#: /books round trip, 2.09-2.64 s end to end. 1.0 s failed 3/3 and 2.5 s 6/6
#: (the child poll reserves min(0.5, budget/4) for cleanup); 3.0 s passed 5/6.
#: The read stays inside the position's own deadline and the child's governor
#: lease expires with it (20b98dd97), so this cannot extend the monitor cycle.
HELD_MONITOR_NETWORK_QUOTE_READ_MAX_SECONDS = 3.0
#: How far back a confirming sample may be sought. This is a FRESHNESS bound and
#: must stay tight: widening it to 600 s let a 10-minute-stale quote confirm a
#: collapse, which is exactly the staleness
#: tests/test_flash_crash_evidence_gate.py::test_causal_catastrophe_confirmation_refuses_quote_gap
#: exists to refuse. Persistence is enforced separately, by
#: _FLASH_CRASH_CONFIRMATION_MIN_SPAN_SECONDS.
_FLASH_CRASH_CONFIRMATION_MAX_GAP_SECONDS = 120.0

#: Minimum separation between the current quote and a confirming sample before
#: an adverse move counts as persistent. The freshness bound above only limits
#: how far back confirmations may be sought; it never required them to be
#: SEPARATED, so two quotes seconds apart satisfied "persistent catastrophe".
#: Measured on the two 2026-09-17/18 false panics the confirming pair was 15 s
#: apart (Wellington 00:59:44 / 00:59:59) and 7 s apart (Seattle 22:53:51 /
#: 22:53:58); in Wellington's case ZERO of the 82 quotes in the preceding 20
#: minutes were at or below the 0.31 bid it sold on, and the final 5 minutes
#: were flat at 0.54 while our own posterior was 0.787. Both bins then settled
#: in our favour (Wellington observed 14.0 C against a 15 C bin, Seattle 71.96 F
#: against a 74-75 F bin), so the trigger liquidated two winners on sub-minute
#: quote excursions. 30 s sits above both observed pairs and well inside the
#: 120 s freshness bound, so a move that genuinely persists across quote cycles
#: still confirms while a single-cycle excursion cannot.
_FLASH_CRASH_CONFIRMATION_MIN_SPAN_SECONDS = 30.0

#: Distinct causal instants to consider as confirmation candidates. Must cover
#: the discarded recent span plus enough older instants to confirm with; a dense
#: stream publishes every few seconds, so a small cap saw only the excursion
#: itself.
_FLASH_CRASH_CONFIRMATION_CANDIDATE_LIMIT = 400
# A ratio is scale-free only on a continuous variable. On the venue's discrete
# grid the DENOMINATOR carries the tick count, so a percentage bound silently
# becomes a price-level filter: one 0.01 tick is 12.5% of a 0.08 bid and 1.3% of
# a 0.75 bid. Measured over 112,857 quotes (2026-09-12 onward), the -0.40/hr
# catastrophe bound was satisfied by 25.4% of one-hour windows at bid <= 0.10
# against 0.3% above 0.60 -- an 85x spread from granularity alone. Hong Kong
# 09-20 low 27C on 2026-09-19 tripped it on a 0.10 -> 0.06 slide: four ticks,
# while the ask never left 0.10-0.12 (a real collapse moves both sides).
#
# Requiring a real tick DISTANCE alongside the ratio removes exactly that
# artifact and nothing else. Sweeping the floor over the same corpus: 4 ticks
# drops the <= 0.10 rate 9.3% -> 1.3% and leaves every band above 0.10 identical
# (3.2 / 2.8 / 1.0 / 0.4%); 8+ ticks begins suppressing legitimate mid-price
# collapses. This bounds the CATASTROPHE witness only -- the velocity
# measurement itself stays the honest observed ratio.
_FLASH_CRASH_MIN_TICK_DISTANCE = 4.0
_FLASH_CRASH_COARSE_TICK = 0.01
_FLASH_CRASH_FINE_TICK = 0.001
HELD_MONITOR_PROBABILITY_PREPARE_MAX_SECONDS = 2.5
HELD_MONITOR_RAW_HWM_READ_MAX_SECONDS = 2.5
_MONITOR_DAY0_FAMILY_CACHE_ATTR = "_zeus_monitor_day0_family_cache"
_MONITOR_REPLACEMENT_HWM_SNAPSHOT_ATTR = "_zeus_monitor_replacement_hwm_snapshot"
_DAY0_MATERIALIZATION_VISIBILITY_RETRY_SECONDS = 0.1
_DAY0_MATERIALIZATION_VISIBILITY_RETRY_BUDGET_SECONDS = 0.35
_DAY0_CANONICAL_OBSERVATION_EVENT_NOT_VISIBLE = (
    "GLOBAL_DAY0_CANONICAL_OBSERVATION_EVENT_NOT_VISIBLE"
)
_DAY0_MATERIALIZATION_VISIBILITY_REASONS = frozenset(
    {
        "REPLACEMENT_RAW_INPUT_HWM",
        _DAY0_CANONICAL_OBSERVATION_EVENT_NOT_VISIBLE,
        "GLOBAL_DAY0_PROVISIONAL_POSTERIOR_IDENTITY_MISMATCH",
        "GLOBAL_DAY0_PROVISIONAL_POSTERIOR_IDENTITY_MISSING",
        "GLOBAL_DAY0_REPLACEMENT_CONDITIONING_MISSING",
        "GLOBAL_DAY0_CONDITIONING_OBSERVATION_TIME_MISMATCH",
    }
)
_PINNED_CARRIER_CURRENT_EVENT_DEFERABLE_BLOCK_REASONS = frozenset(
    {
        # These establish that the older held carrier cannot name the current
        # authorized Day0 event.  They do not disprove a separately rebuilt
        # current-event posterior.
        "REPLACEMENT_PINNED_DAY0_METRIC_MISMATCH",
        "REPLACEMENT_PINNED_DAY0_UNIT_MISMATCH",
        "REPLACEMENT_PINNED_DAY0_SOURCE_STATION_MISMATCH",
        "REPLACEMENT_PINNED_DAY0_LIKELIHOOD_STATION_MISMATCH",
        "REPLACEMENT_PINNED_DAY0_LIKELIHOOD_SOURCE_PAIR_MISMATCH",
        "REPLACEMENT_PINNED_ELIGIBLE_ENS_HWM_UNAVAILABLE",
    }
)
_WHALE_TOXICITY_PRICE_MARGIN = 0.05
_WHALE_TOXICITY_SEVERE_PRICE_MARGIN = 0.15
_WHALE_TOXICITY_LOOKBACK_HOURS = 1.0
_WHALE_TOXICITY_MIN_NOTIONAL_USD = 25.0
SELECTED_METHOD_DAY0_ABSORBING_HARD_FACT = "day0_absorbing_hard_fact"
SELECTED_METHOD_DAY0_OBSERVATION_REMAINING_WINDOW = "day0_observation_remaining_window"
SELECTED_METHOD_FINAL_DAILY_OBSERVATION_EXACT = "final_daily_observation_exact"
_BELIEF_RESEED_LOCK = threading.Lock()
_BELIEF_RESEED_GENERATIONS: dict[tuple[str, str, str], int] = {}


@dataclass(frozen=True)
class _CurrentGlobalDay0FamilySnapshot:
    witness: object
    token_pairs: tuple[tuple[str, str, str], ...]
    deterministic_condition_ids: frozenset[str]
    day0_payload: dict[str, object]
    metric: str
    probability_authority: str = ""


@dataclass
class _CurrentGlobalDay0FamilyCache:
    decision_time: datetime | None = None
    snapshots: dict[
        tuple[str, str, str], list[_CurrentGlobalDay0FamilySnapshot]
    ] = field(default_factory=dict)
    failures: dict[
        tuple[str, str, str], tuple[type[Exception], str]
    ] = field(default_factory=dict)
    failure_receipts: dict[
        tuple[str, str, str], Mapping[str, object]
    ] = field(default_factory=dict)


class _CachedCurrentGlobalDay0FamilyError(RuntimeError):
    pass


class _Day0UnobservedPrefixUnavailable(ObservationUnavailableError):
    """The target local day has no canonical observation yet."""


class _Day0SnapshotReadDeadlineExceeded(TimeoutError):
    """The held-monitor deadline elapsed during a current Day0 SQLite read."""


def _is_day0_materialization_visibility_gap(exc: Exception) -> bool:
    reason = str(exc)
    return any(code in reason for code in _DAY0_MATERIALIZATION_VISIBILITY_REASONS)


def _pinned_complete_bundle_matches_current_day0_event(
    bundle: object,
    event: object,
    *,
    metric: str,
    settlement_unit: str,
) -> bool:
    """Whether a prior carrier can be causally overlaid by this Day0 event.

    A prior carrier may bridge an incomplete forecast wave for a held family, but
    only for the same source/metric/unit and a later monotone extreme. A same-clock
    value change is a correction, not an advance, and returns ``False`` so the
    current bundle path must establish a new source identity.
    """

    provenance = getattr(bundle, "provenance_json", None) or {}
    provisional = (
        provenance.get("day0_provisional_observation")
        if isinstance(provenance, Mapping)
        else None
    )
    if not isinstance(provisional, Mapping) or provisional.get("active") is not True:
        return False
    try:
        payload = json.loads(str(getattr(event, "payload_json", "")))
    except (TypeError, ValueError):
        return False
    if not isinstance(payload, Mapping):
        return False

    expected_metric = str(payload.get("metric") or "").strip().lower()
    expected_source = str(
        payload.get("settlement_source")
        or payload.get("observation_source")
        or payload.get("source")
        or ""
    ).strip().lower()
    expected_time = str(payload.get("observation_time") or "").strip()
    expected_unit = str(
        payload.get("settlement_unit")
        or payload.get("unit")
        or settlement_unit
        or ""
    ).strip().upper()
    raw_value = payload.get("high_so_far" if expected_metric == "high" else "low_so_far")
    if raw_value in (None, ""):
        raw_value = payload.get("raw_value")
    if raw_value in (None, ""):
        raw_value = payload.get("observed_extreme_native")
    try:
        expected_value_c = float(raw_value)
        observed_value_c = float(provisional["observed_extreme_c"])
    except (KeyError, TypeError, ValueError):
        return False
    if expected_unit == "F":
        expected_value_c = (expected_value_c - 32.0) * 5.0 / 9.0

    prior_time_text = str(provisional.get("observation_time") or "").strip()
    try:
        current_time = datetime.fromisoformat(expected_time.replace("Z", "+00:00"))
        prior_time = datetime.fromisoformat(prior_time_text.replace("Z", "+00:00"))
    except ValueError:
        return False
    if current_time.tzinfo is None or prior_time.tzinfo is None:
        return False
    current_time = current_time.astimezone(timezone.utc)
    prior_time = prior_time.astimezone(timezone.utc)
    same_value = math.isclose(
        observed_value_c,
        expected_value_c,
        rel_tol=0.0,
        abs_tol=1e-9,
    )
    monotone_advance = bool(
        current_time > prior_time
        and (
            (expected_metric == "high" and expected_value_c >= observed_value_c)
            or (expected_metric == "low" and expected_value_c <= observed_value_c)
        )
    )

    return bool(
        expected_metric == str(metric).strip().lower()
        and str(provisional.get("metric") or "").strip().lower() == expected_metric
        and expected_source
        and str(provisional.get("source") or "").strip().lower() == expected_source
        and expected_time
        and current_time >= prior_time
        and expected_unit
        and str(provisional.get("unit") or "").strip().upper() == expected_unit
        and (same_value or monotone_advance)
    )


def _pinned_complete_bundle_has_valid_causal_evidence(bundle: object) -> bool:
    """Require the immutable carrier and its causal vector certificate together."""

    provenance = getattr(bundle, "provenance_json", None)
    if not isinstance(provenance, Mapping):
        return False
    causal_bundle = provenance.get("day0_causal_evidence_bundle")
    remaining_witness = provenance.get("day0_remaining_vector_witness")
    if not isinstance(causal_bundle, Mapping) or not isinstance(
        remaining_witness, Mapping
    ):
        return False
    if remaining_witness != causal_bundle.get("carrier_vector_witness"):
        return False
    try:
        from src.data.day0_hourly_vectors import (
            validate_day0_causal_evidence_bundle,
        )

        validation = validate_day0_causal_evidence_bundle(
            expected=causal_bundle,
            actual=causal_bundle,
        )
    except (TypeError, ValueError):
        return False
    return bool(validation.ok)


def _pinned_carrier_block_defers_to_current_day0_event(reason_code: object) -> bool:
    """Whether a rejected old carrier may defer to current-event authority.

    This is intentionally a closed allow-list.  Carrier shape, clock, missing
    witness, and likelihood-integrity failures remain fail-closed rather than
    becoming a broad way to ignore reader ``BLOCKED`` results.
    """

    return str(reason_code or "").strip() in (
        _PINNED_CARRIER_CURRENT_EVENT_DEFERABLE_BLOCK_REASONS
    )


def _day0_materialization_visibility_retry_deadline(
    deadline_monotonic: float | None,
) -> float:
    """Bound one family's publish-visibility read-through below the cycle budget."""
    retry_deadline = (
        time.monotonic() + _DAY0_MATERIALIZATION_VISIBILITY_RETRY_BUDGET_SECONDS
    )
    if deadline_monotonic is not None:
        retry_deadline = min(retry_deadline, deadline_monotonic)
    return retry_deadline


def _day0_primary_snapshot_read_deadline(
    deadline_monotonic: float | None,
) -> float:
    """Bound the primary read while reserving the visibility-retry budget."""
    primary_deadline = (
        time.monotonic() + HELD_MONITOR_PRIMARY_BELIEF_READ_MAX_SECONDS
    )
    if deadline_monotonic is not None:
        primary_deadline = min(
            primary_deadline,
            float(deadline_monotonic)
            - _DAY0_MATERIALIZATION_VISIBILITY_RETRY_BUDGET_SECONDS,
        )
    return primary_deadline


def _held_monitor_stage_deadline(
    outer_deadline_monotonic: float | None,
    max_seconds: float,
) -> float:
    stage_deadline = time.monotonic() + float(max_seconds)
    if outer_deadline_monotonic is not None:
        stage_deadline = min(stage_deadline, float(outer_deadline_monotonic))
    _raise_if_day0_snapshot_read_deadline_elapsed(stage_deadline)
    return stage_deadline


def _raise_if_day0_snapshot_read_deadline_elapsed(
    deadline_monotonic: float | None,
) -> None:
    if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
        raise _Day0SnapshotReadDeadlineExceeded(
            "monitor current global Day0 SQLite read deadline elapsed"
        )


@contextmanager
def _day0_snapshot_sqlite_read_deadline(conn, deadline_monotonic: float | None):
    """Bound one current-Day0 snapshot connection to the held-monitor deadline."""
    if deadline_monotonic is None:
        yield conn
        return
    _raise_if_day0_snapshot_read_deadline_elapsed(deadline_monotonic)
    if not hasattr(conn, "set_progress_handler"):
        yield conn
        _raise_if_day0_snapshot_read_deadline_elapsed(deadline_monotonic)
        return
    remaining_ms = max(
        0,
        int((deadline_monotonic - time.monotonic()) * 1000.0),
    )
    previous_busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    conn.execute(f"PRAGMA busy_timeout = {remaining_ms}")

    def _deadline_expired() -> int:
        return int(time.monotonic() >= deadline_monotonic)

    conn.set_progress_handler(_deadline_expired, 1_000)
    try:
        yield conn
        _raise_if_day0_snapshot_read_deadline_elapsed(deadline_monotonic)
    except sqlite3.OperationalError as exc:
        if time.monotonic() >= deadline_monotonic:
            raise _Day0SnapshotReadDeadlineExceeded(
                "monitor current global Day0 SQLite read deadline elapsed"
            ) from exc
        raise
    finally:
        try:
            conn.set_progress_handler(None, 0)
            conn.execute(f"PRAGMA busy_timeout = {int(previous_busy_timeout)}")
        except Exception:  # noqa: BLE001 - close is the read-only connection backstop.
            pass


def _read_current_global_day0_snapshot_tokens(
    *,
    trade_conn,
    condition_ids: tuple[str, ...],
    deadline_monotonic: float | None,
):
    """Read current token bindings without changing the shared monitor connection."""
    placeholders = ",".join("?" for _ in condition_ids)
    query = f"""
        SELECT condition_id, yes_token_id, no_token_id
          FROM executable_market_snapshot_latest
         WHERE condition_id IN ({placeholders})
           AND yes_token_id IS NOT NULL
           AND no_token_id IS NOT NULL
         ORDER BY captured_at DESC, snapshot_id DESC
    """
    if not isinstance(trade_conn, sqlite3.Connection):
        # Test doubles are not shared SQLite handles and cannot receive a progress
        # handler. Production always takes the independent canonical reader below.
        return trade_conn.execute(query, condition_ids).fetchall()

    from src.state.db import get_trade_connection_read_only

    try:
        token_conn = get_trade_connection_read_only(
            deadline_monotonic=deadline_monotonic,
        )
    except sqlite3.OperationalError:
        _raise_if_day0_snapshot_read_deadline_elapsed(deadline_monotonic)
        raise
    try:
        with _day0_snapshot_sqlite_read_deadline(token_conn, deadline_monotonic):
            return token_conn.execute(query, condition_ids).fetchall()
    finally:
        token_conn.close()


def install_monitor_orderbook_prefetch(
    clob,
    books: dict[str, dict],
    *,
    attempted_token_ids=(),
    merge: bool = False,
) -> bool:
    """Attach one-cycle batch books to the cycle-scoped CLOB client."""

    clean = {
        str(token_id): book
        for token_id, book in books.items()
        if str(token_id).strip() and isinstance(book, dict) and book
    }
    attempted = frozenset(
        token_id
        for value in attempted_token_ids
        if (token_id := str(value).strip())
    )
    if merge:
        current_books = getattr(clob, "__dict__", {}).get(
            _MONITOR_PREFETCHED_ORDERBOOKS_ATTR
        )
        if isinstance(current_books, dict):
            clean = {**current_books, **clean}
        current_attempted = getattr(clob, "__dict__", {}).get(
            _MONITOR_PREFETCH_ATTEMPTED_TOKENS_ATTR
        )
        if isinstance(current_attempted, frozenset):
            attempted = current_attempted | attempted
    try:
        setattr(clob, _MONITOR_PREFETCHED_ORDERBOOKS_ATTR, clean)
        setattr(clob, _MONITOR_PREFETCH_ATTEMPTED_TOKENS_ATTR, attempted)
    except (AttributeError, TypeError):
        return False
    return True


def publish_current_monitor_orderbook_batch(
    books: dict[str, dict],
    *,
    captured_at_utc: datetime | None,
    merge: bool = False,
) -> int:
    """Publish one exact monitor batch for immediate global SELL redecision.

    ``merge`` joins a later network tranche to the same cycle's already
    published local tranche while retaining the oldest source capture clock.
    It never makes either tranche newer than its actual observation.
    """

    global _CURRENT_MONITOR_ORDERBOOK_BATCH
    captured_at = captured_at_utc
    if captured_at is not None:
        if captured_at.tzinfo is None:
            raise ValueError("MONITOR_ORDERBOOK_BATCH_CLOCK_NAIVE")
        captured_at = captured_at.astimezone(timezone.utc)
    clean: dict[str, dict] = {}
    for raw_token_id, raw_book in books.items():
        token_id = str(raw_token_id).strip()
        if not token_id or not isinstance(raw_book, dict) or not raw_book:
            continue
        asset_id = str(
            raw_book.get("asset_id")
            or raw_book.get("assetId")
            or raw_book.get("token_id")
            or ""
        ).strip()
        if asset_id != token_id:
            continue
        clean[token_id] = dict(raw_book)
    with _CURRENT_MONITOR_ORDERBOOK_BATCH_LOCK:
        if merge:
            existing_books, existing_at = _CURRENT_MONITOR_ORDERBOOK_BATCH
            clean = {**existing_books, **clean}
            captured_at = (
                min(existing_at, captured_at)
                if existing_at is not None and captured_at is not None
                else existing_at or captured_at
            )
        if not clean:
            captured_at = None
        _CURRENT_MONITOR_ORDERBOOK_BATCH = (clean, captured_at)
    return len(clean)


def current_monitor_orderbook_batch(
    token_ids,
    *,
    checked_at_utc: datetime,
    max_age: timedelta,
) -> tuple[dict[str, dict], datetime] | None:
    """Read the latest exact monitor network cut without extending its clock."""

    if checked_at_utc.tzinfo is None or max_age <= timedelta(0):
        raise ValueError("MONITOR_ORDERBOOK_BATCH_READ_CLOCK_INVALID")
    checked_at = checked_at_utc.astimezone(timezone.utc)
    requested = {
        token_id
        for value in token_ids
        if (token_id := str(value).strip())
    }
    with _CURRENT_MONITOR_ORDERBOOK_BATCH_LOCK:
        books, captured_at = _CURRENT_MONITOR_ORDERBOOK_BATCH
        selected = {
            token_id: dict(books[token_id])
            for token_id in requested
            if token_id in books
        }
    if captured_at is None:
        return None
    age = checked_at - captured_at
    if age < timedelta(0) or age > max_age or not selected:
        return None
    return selected, captured_at


def install_monitor_day0_family_cache(
    clob,
    *,
    decision_time: datetime | None = None,
) -> bool:
    """Install a fresh family cache for one held-monitor cycle."""

    try:
        setattr(
            clob,
            _MONITOR_DAY0_FAMILY_CACHE_ATTR,
            _CurrentGlobalDay0FamilyCache(decision_time=decision_time),
        )
    except (AttributeError, TypeError):
        return False
    return True


def install_monitor_replacement_hwm_snapshot(clob, snapshot: object) -> bool:
    """Install one immutable held-family HWM cut on the cycle-local client."""

    try:
        setattr(clob, _MONITOR_REPLACEMENT_HWM_SNAPSHOT_ATTR, snapshot)
    except (AttributeError, TypeError):
        return False
    return True


def prefetched_monitor_orderbook(clob, token_id: str) -> dict | None:
    books = getattr(clob, "__dict__", {}).get(_MONITOR_PREFETCHED_ORDERBOOKS_ATTR)
    if not isinstance(books, dict):
        return None
    book = books.get(str(token_id))
    return book if isinstance(book, dict) and book else None


def _remember_monitor_orderbook(clob, token_id: str, book: object) -> bool:
    """Keep a successful singular refresh available for this monitor cycle."""

    books = getattr(clob, "__dict__", {}).get(_MONITOR_PREFETCHED_ORDERBOOKS_ATTR)
    token = str(token_id).strip()
    if (
        not isinstance(books, dict)
        or not token
        or not isinstance(book, dict)
        or not book
    ):
        return False
    books[token] = book
    return True


def monitor_orderbook_prefetch_attempted(clob, token_id: str) -> bool:
    attempted = getattr(clob, "__dict__", {}).get(
        _MONITOR_PREFETCH_ATTEMPTED_TOKENS_ATTR
    )
    return isinstance(attempted, frozenset) and str(token_id) in attempted


def _monitor_receipt_float(value) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(out):
        return None
    return out


def _compact_monitor_probability_receipt(
    payload: dict[str, object],
) -> dict[str, object]:
    """Keep probability identity and clocks without copying the full evidence."""

    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    compact = {
        key: payload[key]
        for key in (
            "schema_version",
            "selected_method",
            "probability_authority",
            "probability_functional",
            "probability_semantics_revision",
            "posterior_id",
            "computed_at",
            "source_cycle_time",
            "source_id",
            "posterior_method",
            "latest_raw_cycle_time",
            "probability_witness_identity",
            "probability_content_identity",
            "q_version",
            "source_truth_identity",
            "held_side_probability",
            "held_side_lcb",
            "held_side_ucb",
            "city",
            "target_date",
            "temperature_metric",
            "bin_label",
            "bin_key",
            "held_direction",
            "captured_at_utc",
            "day0_zero_observation_proven",
            "hard_fact_evidence",
        )
        if payload.get(key) is not None
    }
    compact["evidence_content_hash"] = hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()
    return compact


def _monitor_receipt_quantiles(values) -> dict[str, float | None]:
    try:
        arr = np.asarray(values, dtype=float)
    except (TypeError, ValueError):
        return {"count": 0, "min": None, "q50": None, "q90": None, "max": None}
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return {"count": 0, "min": None, "q50": None, "q90": None, "max": None}
    return {
        "count": int(arr.size),
        "min": _monitor_receipt_float(np.min(arr)),
        "q50": _monitor_receipt_float(np.quantile(arr, 0.5)),
        "q90": _monitor_receipt_float(np.quantile(arr, 0.9)),
        "max": _monitor_receipt_float(np.max(arr)),
    }


@dataclass(frozen=True)
class HeldTokenMonitorQuote:
    """Held-token monitor-price truth with explicit SELL-book authority."""

    token_id: str
    best_bid: float
    best_ask: float | None
    bid_size: float
    ask_size: float
    mark_price: float
    source_timestamp: str
    min_order_size: float | None = None
    min_tick: float | None = field(default=None, kw_only=True)
    # Held-side depth ladder (top rungs, price-descending) for the depth-honest
    # exit stopping law. Empty when the book was unavailable (one-sided/degraded).
    bid_ladder: tuple[tuple[float, float], ...] = ()
    # ``False`` preserves an exact, fresh BBA price only for monitoring.  It
    # cannot become a global SELL proposal or a direct exit command.
    full_depth_action_authority: bool = False


def _monitor_snapshot_is_executable(
    *,
    active: object,
    closed: object,
    accepting_orders: object,
    tradeability_status_json: object,
) -> bool:
    """Use normalized CLOB tradeability before legacy Gamma routing flags."""

    if tradeability_status_json is None:
        return bool(active) and not bool(closed) and accepting_orders == 1
    try:
        status = (
            tradeability_status_json
            if isinstance(tradeability_status_json, dict)
            else json.loads(str(tradeability_status_json))
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    if isinstance(status, dict) and isinstance(
        status.get("executable_allowed"), bool
    ):
        return status["executable_allowed"]
    return False


def _monitor_snapshot_has_held_exit_evidence(
    *,
    active: object,
    closed: object,
    accepting_orders: object,
    tradeability_status_json: object | None = None,
) -> bool:
    """Accept normalized executable or legacy open held-monitor quote evidence.

    ``executable_allowed`` is the entry/submit predicate.  A held Day0
    monitor may still consume a durable one-sided book: a positive bid is a
    valid SELL quote even when no ask makes the snapshot entry-executable;
    ask-only books become the typed zero-liquidation-value quote downstream.
    """

    if _monitor_snapshot_is_executable(
        active=active,
        closed=closed,
        accepting_orders=accepting_orders,
        tradeability_status_json=tradeability_status_json,
    ):
        return True
    if bool(closed) or accepting_orders != 1:
        return False
    try:
        status = (
            tradeability_status_json
            if isinstance(tradeability_status_json, dict)
            else json.loads(str(tradeability_status_json))
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        status = None
    if isinstance(status, dict):
        # ``active`` is an entry/executability projection and becomes false for
        # a one-sided book. Held SELL monitoring needs only proof that this CLOB
        # still accepts orders and exposes its book; the held bid/depth is
        # validated separately below and submit authority remains JIT-gated.
        if (
            status.get("accepting_orders") is True
            and status.get("clob_enable_order_book") is True
            and status.get("clob_archived") is not True
            and status.get("child_closed") is not True
        ):
            return True
    return bool(active)


def _book_min_order_size(book: dict | None) -> float | None:
    if not isinstance(book, dict):
        return None
    try:
        value = float(book.get("min_order_size"))
    except (TypeError, ValueError):
        return None
    return value if np.isfinite(value) and value > 0.0 else None


def _book_min_tick(book: dict | None) -> float | None:
    if not isinstance(book, dict):
        return None
    for key in ("tick_size", "min_tick_size", "minimum_tick_size", "minTickSize"):
        if key not in book:
            continue
        try:
            value = float(book[key])
        except (TypeError, ValueError):
            continue
        if np.isfinite(value) and value > 0.0:
            return value
    return None


def _fresh_canonical_monitor_orderbook(
    conn,
    pos: Position,
    token_id: str,
    *,
    now_utc: datetime | None = None,
) -> tuple[dict, str] | None:
    """Read exact fresh held-token selection evidence after venue-read failure.

    This is not submit authority: every resulting SELL still crosses the existing
    JIT executable-truth boundary.  The independent reader matters in production
    because the cycle connection may already hold an older SQLite read snapshot
    while the priority snapshot writer has committed a newer held-token book.
    """

    if not isinstance(conn, sqlite3.Connection):
        return None
    condition_id = str(getattr(pos, "condition_id", "") or "").strip()
    token_id = str(token_id or "").strip()
    if not condition_id or not token_id:
        return None
    checked_at = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    outer_deadline = getattr(pos, _HELD_MONITOR_DEADLINE_ATTR, None)
    read_deadline = time.monotonic() + 0.25
    if outer_deadline is not None:
        read_deadline = min(read_deadline, float(outer_deadline))

    readers = [conn]
    owned_reader = None
    caller_has_uncommitted_state = False
    try:
        db_path_row = conn.execute("PRAGMA database_list").fetchone()
        db_path = str(db_path_row[2] or "").strip() if db_path_row else ""
        caller_has_uncommitted_state = bool(db_path and conn.in_transaction)
        if db_path:
            from src.state.db import _connect_read_only

            owned_reader = _connect_read_only(
                Path(db_path),
                deadline_monotonic=read_deadline,
            )
            readers.append(owned_reader)
    except sqlite3.Error:
        owned_reader = None

    candidates: list[tuple[datetime, dict, str]] = []
    invalidated_at_values: list[datetime] = []
    invalidation_parse_failed = False
    try:
        for reader in readers:
            try:
                with _day0_snapshot_sqlite_read_deadline(reader, read_deadline):
                    invalidation_table = reader.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' "
                        "AND name='executable_market_snapshot_invalidations' LIMIT 1"
                    ).fetchone()
                    if invalidation_table is None:
                        continue
                    row = reader.execute(
                        """
                        SELECT latest.captured_at,
                               latest.freshness_deadline,
                               latest.yes_token_id,
                               latest.no_token_id,
                               latest.active,
                               latest.closed,
                               latest.accepting_orders,
                               snapshot.min_order_size,
                               snapshot.orderbook_depth_json,
                               latest.tradeability_status_json
                          FROM executable_market_snapshot_latest AS latest
                          JOIN executable_market_snapshots AS snapshot
                            ON snapshot.snapshot_id = latest.snapshot_id
                           AND snapshot.condition_id = latest.condition_id
                           AND snapshot.selected_outcome_token_id =
                               latest.selected_outcome_token_id
                           AND snapshot.yes_token_id = latest.yes_token_id
                           AND snapshot.no_token_id = latest.no_token_id
                           AND snapshot.active = latest.active
                           AND snapshot.closed = latest.closed
                           AND snapshot.accepting_orders IS latest.accepting_orders
                           AND snapshot.captured_at = latest.captured_at
                           AND snapshot.freshness_deadline = latest.freshness_deadline
                           AND snapshot.tradeability_status_json IS
                               latest.tradeability_status_json
                         WHERE latest.condition_id = ?
                           AND latest.selected_outcome_token_id = ?
                         LIMIT 1
                        """,
                        (condition_id, token_id),
                    ).fetchone()
                    if row is None or not _monitor_snapshot_has_held_exit_evidence(
                        active=row[4],
                        closed=row[5],
                        accepting_orders=row[6],
                        tradeability_status_json=row[9],
                    ):
                        continue
                    captured_at = datetime.fromisoformat(
                        str(row[0]).replace("Z", "+00:00")
                    )
                    freshness_deadline = datetime.fromisoformat(
                        str(row[1]).replace("Z", "+00:00")
                    )
                    if captured_at.tzinfo is None or freshness_deadline.tzinfo is None:
                        continue
                    captured_at = captured_at.astimezone(timezone.utc)
                    freshness_deadline = freshness_deadline.astimezone(timezone.utc)
                    if captured_at > checked_at or freshness_deadline < checked_at:
                        continue
                    invalidation_rows = reader.execute(
                        """
                        SELECT invalidated_at
                          FROM executable_market_snapshot_invalidations
                         WHERE (
                                condition_id = ?
                                OR token_id IN (?, ?, ?)
                           )
                        """,
                        (
                            condition_id,
                            token_id,
                            str(row[2] or ""),
                            str(row[3] or ""),
                        ),
                    ).fetchall()
                for invalidation_row in invalidation_rows:
                    try:
                        invalidated_at = datetime.fromisoformat(
                            str(invalidation_row[0]).replace("Z", "+00:00")
                        )
                        if invalidated_at.tzinfo is None:
                            continue
                        invalidated_at_values.append(
                            invalidated_at.astimezone(timezone.utc)
                        )
                    except (TypeError, ValueError):
                        invalidation_parse_failed = True
                        continue
                book = json.loads(str(row[8]))
                if not isinstance(book, dict):
                    continue
                asset_id = str(
                    book.get("asset_id")
                    or book.get("assetId")
                    or book.get("token_id")
                    or ""
                ).strip()
                if asset_id != token_id:
                    continue
                book = dict(book)
                book.setdefault("min_order_size", row[7])
                if reader is not conn or not caller_has_uncommitted_state:
                    candidates.append((captured_at, book, captured_at.isoformat()))
            except (
                sqlite3.Error,
                TypeError,
                ValueError,
                json.JSONDecodeError,
                _Day0SnapshotReadDeadlineExceeded,
            ):
                continue
    finally:
        if owned_reader is not None:
            owned_reader.close()
    if invalidation_parse_failed:
        return None
    candidates = [
        candidate
        for candidate in candidates
        if not any(
            candidate[0] <= invalidated_at <= checked_at
            for invalidated_at in invalidated_at_values
        )
    ]
    if not candidates:
        return None
    _captured_at, book, source_timestamp = max(candidates, key=lambda item: item[0])
    return book, source_timestamp


def _fresh_canonical_monitor_no_bid_witness(
    conn,
    pos: Position,
    token_id: str,
    *,
    now_utc: datetime | None = None,
) -> HeldTokenMonitorQuote | None:
    """Return fresh exact market-channel BBA truth without SELL authority.

    The producer projects a SELL latest row but selectively appends its BUY
    twin.  This join may preserve a zero or out-of-band positive held bid as
    monitor truth.  It deliberately cannot provide depth or sizing authority
    to a SELL/JIT path.

    SCOPE: one exact ``condition_id/token_id/sell_direction`` witness.
    DRAIN: the next market-channel append is re-read on the next monitor turn.
    RESET: only another fresh no-bid witness recreates this zero-value quote.
    """

    if not isinstance(conn, sqlite3.Connection):
        return None
    condition_id = str(getattr(pos, "condition_id", "") or "").strip()
    token_id = str(token_id or "").strip()
    position_direction = getattr(getattr(pos, "direction", ""), "value", None)
    if position_direction is None:
        position_direction = getattr(pos, "direction", "")
    direction = {
        "buy_yes": "sell_yes",
        "buy_no": "sell_no",
    }.get(str(position_direction or "").lower())
    outcome_label = {"sell_yes": "YES", "sell_no": "NO"}.get(direction)
    append_direction = {"sell_yes": "buy_yes", "sell_no": "buy_no"}.get(
        direction
    )
    if (
        not condition_id
        or not token_id
        or not direction
        or not outcome_label
        or not append_direction
    ):
        return None

    from src.contracts.executable_market_snapshot import FRESHNESS_WINDOW_DEFAULT

    checked_at = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    outer_deadline = getattr(pos, _HELD_MONITOR_DEADLINE_ATTR, None)
    read_deadline = time.monotonic() + 0.25
    if outer_deadline is not None:
        read_deadline = min(read_deadline, float(outer_deadline))

    readers = [conn]
    owned_reader = None
    caller_has_uncommitted_state = False
    try:
        db_path_row = conn.execute("PRAGMA database_list").fetchone()
        db_path = str(db_path_row[2] or "").strip() if db_path_row else ""
        caller_has_uncommitted_state = bool(db_path and conn.in_transaction)
        if db_path:
            from src.state.db import _connect_read_only

            owned_reader = _connect_read_only(
                Path(db_path),
                deadline_monotonic=read_deadline,
            )
            readers.append(owned_reader)
    except sqlite3.Error:
        owned_reader = None

    candidates: list[HeldTokenMonitorQuote] = []
    try:
        for reader in readers:
            try:
                with _day0_snapshot_sqlite_read_deadline(reader, read_deadline):
                    row = reader.execute(
                        """
                        SELECT latest.evidence_id,
                               latest.event_id,
                               latest.condition_id,
                               latest.token_id,
                               latest.outcome_label,
                               latest.direction,
                               latest.quote_seen_at,
                               latest.created_at,
                               latest.best_bid_before,
                               latest.best_ask_before,
                               latest.depth_before_json,
                               latest.schema_version,
                               evidence.evidence_id,
                               evidence.event_id,
                               evidence.condition_id,
                               evidence.token_id,
                               evidence.outcome_label,
                               evidence.direction,
                               evidence.quote_seen_at,
                               evidence.created_at,
                               evidence.best_bid_before,
                               evidence.best_ask_before,
                               evidence.depth_before_json,
                               evidence.schema_version
                          FROM execution_feasibility_latest AS latest
                          JOIN execution_feasibility_evidence AS evidence
                            ON evidence.event_id = latest.event_id
                           AND evidence.condition_id = latest.condition_id
                           AND evidence.token_id = latest.token_id
                           AND evidence.outcome_label = latest.outcome_label
                           AND evidence.direction = ?
                           AND evidence.quote_seen_at = latest.quote_seen_at
                           AND evidence.created_at = latest.created_at
                           AND evidence.book_hash_before IS latest.book_hash_before
                           AND evidence.best_bid_before IS latest.best_bid_before
                           AND evidence.best_ask_before IS latest.best_ask_before
                           AND evidence.schema_version = latest.schema_version
                         WHERE latest.condition_id = ?
                           AND latest.token_id = ?
                           AND latest.outcome_label = ?
                           AND latest.direction = ?
                           AND latest.depth_before_json IS NULL
                         LIMIT 1
                        """,
                        (
                            append_direction,
                            condition_id,
                            token_id,
                            outcome_label,
                            direction,
                        ),
                    ).fetchone()
                if row is None or (reader is conn and caller_has_uncommitted_state):
                    continue
                quote_at = datetime.fromisoformat(str(row[6]).replace("Z", "+00:00"))
                created_at = datetime.fromisoformat(str(row[7]).replace("Z", "+00:00"))
                if quote_at.tzinfo is None or created_at.tzinfo is None:
                    continue
                quote_at = quote_at.astimezone(timezone.utc)
                created_at = created_at.astimezone(timezone.utc)
                if (
                    quote_at > checked_at
                    or created_at > checked_at
                    or checked_at - quote_at > FRESHNESS_WINDOW_DEFAULT
                    or not str(row[0] or "").strip()
                    or not str(row[1] or "").strip()
                    or not str(row[12] or "").strip()
                ):
                    continue
                bid_raw, ask_raw, raw_depth = row[8], row[9], row[22]
                try:
                    bid_f = 0.0 if bid_raw is None else float(bid_raw)
                except (TypeError, ValueError):
                    continue
                if not np.isfinite(bid_f) or bid_f < 0.0:
                    continue
                ask_f = None
                if ask_raw is not None:
                    ask_f = float(ask_raw)
                    if not np.isfinite(ask_f) or ask_f <= 0.0:
                        continue
                ask_size = 0.0
                depth = None
                if raw_depth is not None and str(raw_depth).strip():
                    depth = json.loads(str(raw_depth))
                    if not isinstance(depth, dict):
                        continue
                    bids, asks = depth.get("bids"), depth.get("asks")
                    if not isinstance(bids, list) or not isinstance(asks, list) or bids:
                        continue
                    if asks:
                        from src.data.market_scanner import _top_book_level_decimal

                        depth_ask, depth_ask_size = _top_book_level_decimal(depth, "asks")
                        if ask_f is None or not np.isclose(float(depth_ask), ask_f):
                            continue
                        ask_size = float(depth_ask_size)
                    elif ask_f is not None:
                        continue
                elif bid_f == 0.0 and ask_f is None:
                    # Scalar-NULL without explicit full depth proves nothing.
                    continue
                candidates.append(
                    HeldTokenMonitorQuote(
                        token_id=token_id,
                        best_bid=bid_f,
                        best_ask=ask_f,
                        bid_size=0.0,
                        ask_size=ask_size,
                        mark_price=bid_f,
                        source_timestamp=quote_at.isoformat(),
                        min_tick=_book_min_tick(depth),
                        bid_ladder=(),
                        full_depth_action_authority=False,
                    )
                )
            except (
                sqlite3.Error,
                TypeError,
                ValueError,
                json.JSONDecodeError,
                _Day0SnapshotReadDeadlineExceeded,
            ):
                continue
    finally:
        if owned_reader is not None:
            owned_reader.close()
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda quote: datetime.fromisoformat(
            quote.source_timestamp.replace("Z", "+00:00")
        ),
    )


def _compute_divergence_score(p_posterior: float, p_market: float, *, available: bool) -> float:
    """Adverse-only divergence: positive edge is entry signal, not exit signal.

    Non-finite inputs propagate as NaN so stale or missing quotes surface loudly
    rather than recording a spurious 0.0 (max() would silently swallow NaN).
    """
    if not available:
        return float("nan")
    if not (np.isfinite(p_posterior) and np.isfinite(p_market)):
        return float("nan")
    return max(0.0, p_market - p_posterior)


def _book_side_absent_for_token(
    conn: sqlite3.Connection | None,
    *,
    token_id: str,
    observed_at: str | None,
) -> bool:
    """True when the newest causal quote for this token has NO ask either.

    Distinguishes a withdrawn book (both sides absent) from a real zero bid,
    which still carries an ask. Any read failure returns False so the caller
    keeps its existing behaviour rather than silently suppressing a real
    collapse.
    """
    if conn is None or not token_id or not observed_at:
        return False
    try:
        row = conn.execute(
            """
            SELECT bid, ask
              FROM token_price_log
             WHERE token_id = ?
               AND COALESCE(
                       julianday(NULLIF(source_timestamp, '')),
                       julianday(timestamp)
                   ) <= julianday(?)
             ORDER BY COALESCE(
                          julianday(NULLIF(source_timestamp, '')),
                          julianday(timestamp)
                      ) DESC,
                      id DESC
             LIMIT 1
            """,
            (str(token_id), str(observed_at)),
        ).fetchone()
    except sqlite3.Error:
        return False
    if row is None:
        return False
    bid = row["bid"] if hasattr(row, "keys") else row[0]
    ask = row["ask"] if hasattr(row, "keys") else row[1]
    if ask is not None:
        return False
    try:
        return bid is None or float(bid) <= 0.0
    except (TypeError, ValueError):
        return False


def _causal_market_velocity_1h(
    conn: sqlite3.Connection | None,
    *,
    token_id: str,
    current_bid: float,
    observed_at: str | None,
) -> float | None:
    """Return held-side executable-bid drawdown from a causal reference.

    Established tokens compare both the one-to-two-hour baseline and a recent
    high supported by distinct source instants. An old low must not hide a
    rapid collapse from that sustained high. New tokens retain the causal
    trailing-hour high fallback. The result is scale-free: ``0.10 -> 0.06``
    and ``0.50 -> 0.30`` are the same ``-0.40`` market-path observation.
    """
    if conn is None or not observed_at:
        return None
    # A WITHDRAWN book is not a price move. `_held_token_quote_from_book` carries
    # an empty bid side forward as 0.0 by design (the exit boundary still refuses
    # it as non-executable SELL authority), but feeding that 0.0 into a
    # price-CHANGE measure reads a liquidity withdrawal as a -100%/h collapse.
    # Shanghai 2026-09-18: the held NO bid sat at 0.998-0.999 for 25 minutes,
    # then BOTH sides went absent at 16:30 and flash-crash fired velocity
    # -1.00/hr on a position whose settlement was already a win (local-day low
    # 25.0C against a shorted 24C bin) — only NO_EXECUTABLE_BID stopped the sale.
    # A genuinely zero-valued market still quotes an ask (someone sells the
    # worthless side), so bid absent AND ask absent is the discriminator.
    # Returning None withdraws market-path evidence entirely, which is the
    # already-built fail-closed path: `fresh_market_path_evidence` goes false and
    # the confirmation count resets (src/state/portfolio.py).
    if float(current_bid) <= 0.0 and _book_side_absent_for_token(
        conn, token_id=token_id, observed_at=observed_at
    ):
        return None
    try:
        as_of = datetime.fromisoformat(str(observed_at).replace("Z", "+00:00"))
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=timezone.utc)
        as_of = as_of.astimezone(timezone.utc)
        cutoff = (as_of - timedelta(hours=1)).isoformat()
        oldest_baseline = (as_of - timedelta(hours=2)).isoformat()
        row = conn.execute(
            """
            SELECT bid
              FROM token_price_log
             WHERE token_id = ?
               AND COALESCE(
                       julianday(NULLIF(source_timestamp, '')),
                       julianday(timestamp)
                   ) >= julianday(?)
               AND COALESCE(
                       julianday(NULLIF(source_timestamp, '')),
                       julianday(timestamp)
                   ) <= julianday(?)
             ORDER BY COALESCE(
                          julianday(NULLIF(source_timestamp, '')),
                          julianday(timestamp)
                      ) DESC,
                      id DESC
             LIMIT 1
            """,
            (str(token_id), oldest_baseline, cutoff),
        ).fetchone()
        has_old_reference = row is not None
        if row is None:
            row = conn.execute(
                """
                SELECT MAX(bid) AS bid
                  FROM token_price_log
                 WHERE token_id = ?
                   AND COALESCE(
                           julianday(NULLIF(source_timestamp, '')),
                           julianday(timestamp)
                       ) > julianday(?)
                   AND COALESCE(
                           julianday(NULLIF(source_timestamp, '')),
                           julianday(timestamp)
                       ) < julianday(?)
                """,
                (str(token_id), cutoff, as_of.isoformat()),
            ).fetchone()
            if row is None or row["bid"] is None:
                return None
        if row["bid"] is None:
            return None
        old_price = float(row["bid"])
        now_price = float(current_bid)
        if (
            not (np.isfinite(old_price) and np.isfinite(now_price))
            or old_price <= 0.0
            or now_price < 0.0
        ):
            return None
        if has_old_reference:
            # SCOPE: this token's protective market-path reference only.
            # DRAIN: each causal quote/monitor recomputes the trailing window.
            # RESET: recovery or expiry/loss of distinct high quotes removes
            # this reference; the existing confirmation/gap law still applies.
            # Deduplicate source instants before ranking bids, so repeated
            # writes or one isolated high tick cannot establish a new high.
            recent = conn.execute(
                """
                SELECT bid FROM (
                    SELECT bid, ROW_NUMBER() OVER (
                        PARTITION BY COALESCE(
                            julianday(NULLIF(source_timestamp, '')),
                            julianday(timestamp)
                        ) ORDER BY id DESC
                    ) AS source_rank
                    FROM token_price_log
                    WHERE token_id = ?
                      AND COALESCE(
                          julianday(NULLIF(source_timestamp, '')),
                          julianday(timestamp)
                      ) > julianday(?)
                      AND COALESCE(
                          julianday(NULLIF(source_timestamp, '')),
                          julianday(timestamp)
                      ) < julianday(?)
                )
                WHERE source_rank = 1 AND bid > 0.0 AND bid <= 1.0
                ORDER BY bid DESC
                LIMIT 1 OFFSET ?
                """,
                (
                    str(token_id), cutoff, as_of.isoformat(),
                    max(1, int(flash_crash_confirmations())) - 1,
                ),
            ).fetchone()
            if recent is not None:
                old_price = max(old_price, float(recent["bid"]))
        return (now_price / old_price) - 1.0
    except (TypeError, ValueError, sqlite3.Error):
        return None


def _flash_crash_token_tick(conn: sqlite3.Connection | None, *, token_id: str) -> float:
    """Return this token's venue tick, defaulting to the coarsest live grid.

    Two regimes are live (0.01 and 0.001). The tightest spread the token has ever
    quoted is an upper bound on its grid -- a book cannot quote finer than one
    tick -- so snap that to a regime rather than trusting a noisy minimum. The
    coarse default is conservative here: it demands the LARGEST absolute move
    before a deep ratio is believed, so an unknown grid cannot re-admit the
    artifact.
    """

    if conn is None:
        return _FLASH_CRASH_COARSE_TICK
    try:
        row = conn.execute(
            """
            SELECT MIN(ABS(bid - ask)) AS spread
              FROM token_price_log
             WHERE token_id = ?
               AND bid IS NOT NULL
               AND ask IS NOT NULL
               AND ABS(bid - ask) > 0
            """,
            (str(token_id),),
        ).fetchone()
    except sqlite3.Error:
        return _FLASH_CRASH_COARSE_TICK
    if row is None or row["spread"] is None:
        return _FLASH_CRASH_COARSE_TICK
    try:
        observed = float(row["spread"])
    except (TypeError, ValueError):
        return _FLASH_CRASH_COARSE_TICK
    if not np.isfinite(observed) or observed <= 0.0:
        return _FLASH_CRASH_COARSE_TICK
    return (
        _FLASH_CRASH_FINE_TICK
        if observed < _FLASH_CRASH_COARSE_TICK
        else _FLASH_CRASH_COARSE_TICK
    )


def _deep_drawdown_clears_tick_floor(
    conn: sqlite3.Connection | None,
    *,
    token_id: str,
    current_bid: float,
    velocity: float,
) -> bool:
    """Return whether a deep RATIO also covers a real tick DISTANCE."""

    try:
        now_price = float(current_bid)
        vel = float(velocity)
    except (TypeError, ValueError):
        return False
    if not (np.isfinite(now_price) and np.isfinite(vel)) or vel >= 0.0:
        return False
    # Recover the reference the ratio was taken against, so the floor is measured
    # on the same pair the threshold used.
    denominator = 1.0 + vel
    if denominator <= 0.0:
        # A total collapse to zero is unambiguous at any grid.
        return True
    old_price = now_price / denominator
    tick = _flash_crash_token_tick(conn, token_id=token_id)
    required = _FLASH_CRASH_MIN_TICK_DISTANCE * tick
    # The reference is recovered through a division, so a drawdown of exactly the
    # floor lands a half-ULP short (0.09 -> 0.05 recovers 0.039999999999999994
    # against 0.04). Compare with a tolerance far below one tick so the boundary
    # case counts as clearing it, without admitting a genuinely smaller move.
    return (old_price - now_price) >= required - (tick / 1024.0)


def _causal_deep_market_catastrophe_evidence(
    conn: sqlite3.Connection | None,
    *,
    token_id: str,
    current_bid: float,
    observed_at: str | None,
) -> tuple[float | None, int]:
    """Return one coherent velocity/confirmation catastrophe witness.

    Held positions are reconstructed from canonical DB truth on every monitor
    claim, so an in-memory counter cannot prove persistence across claims.  This
    derives both values from the same persisted token-price timeline.  Computing
    velocity and confirmations in separate reads can split on a tight monitor
    deadline: confirmations may prove catastrophe while a failed velocity read
    is converted to zero and suppresses the exit.  The current quote is counted
    once; earlier samples must have a distinct evidence timestamp, be strictly
    causal, and independently cross the same deep one-hour velocity bound.
    """
    if conn is None or not observed_at or not token_id:
        return None, 0
    try:
        as_of = datetime.fromisoformat(str(observed_at).replace("Z", "+00:00"))
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=timezone.utc)
        as_of = as_of.astimezone(timezone.utc)
        required = max(1, int(flash_crash_confirmations()))
        current_velocity = _causal_market_velocity_1h(
            conn,
            token_id=token_id,
            current_bid=current_bid,
            observed_at=as_of.isoformat(),
        )
        threshold = float(flash_crash_catastrophe_velocity())
        if current_velocity is None or current_velocity > threshold:
            return current_velocity, 0
        if not _deep_drawdown_clears_tick_floor(
            conn,
            token_id=token_id,
            current_bid=current_bid,
            velocity=current_velocity,
        ):
            # The ratio is deep only because its denominator is small. Report the
            # measured velocity with ZERO confirmations: the witness is what this
            # function decides, and no confirmation count means no catastrophe.
            return current_velocity, 0
        if required == 1:
            return current_velocity, 1

        confirmation_start = (
            as_of - timedelta(seconds=_FLASH_CRASH_CONFIRMATION_MAX_GAP_SECONDS)
        ).isoformat()
        rows = conn.execute(
            """
            SELECT bid,
                   COALESCE(NULLIF(source_timestamp, ''), timestamp) AS evidence_at
              FROM token_price_log
             WHERE token_id = ?
               AND COALESCE(
                       julianday(NULLIF(source_timestamp, '')),
                       julianday(timestamp)
                   ) >= julianday(?)
               AND COALESCE(
                       julianday(NULLIF(source_timestamp, '')),
                       julianday(timestamp)
                   ) < julianday(?)
             ORDER BY COALESCE(
                          julianday(NULLIF(source_timestamp, '')),
                          julianday(timestamp)
                      ) DESC,
                      id DESC
             LIMIT ?
            """,
            (
                str(token_id),
                confirmation_start,
                as_of.isoformat(),
                # The separation rule below discards everything inside the most
                # recent _FLASH_CRASH_CONFIRMATION_MIN_SPAN_SECONDS, so the
                # candidate set must be deep enough to still contain older
                # instants on a dense quote stream. required*8 covered only the
                # last few minutes and left nothing to confirm with.
                _FLASH_CRASH_CONFIRMATION_CANDIDATE_LIMIT,
            ),
        ).fetchall()
        seen_times = {as_of.isoformat()}
        samples: list[tuple[float, str]] = []
        for row in rows:
            evidence_at = str(row["evidence_at"] or "")
            if not evidence_at or evidence_at in seen_times:
                continue
            seen_times.add(evidence_at)
            if row["bid"] is None:
                continue
            # A confirmation must be SEPARATED from the current quote:
            # "persisted for at least N consecutive causal quote samples" is a
            # claim about time, and two prints seconds apart are one market
            # event observed twice. Filtering here rather than after the list
            # is capped keeps deeper instants available to confirm with.
            sample_instant = _parse_utc_datetime(evidence_at)
            if sample_instant is None:
                continue
            if (
                as_of - sample_instant
            ).total_seconds() < _FLASH_CRASH_CONFIRMATION_MIN_SPAN_SECONDS:
                continue
            samples.append((float(row["bid"]), evidence_at))
            if len(samples) >= required - 1:
                break

        # SCOPE: this held token's market-path exit authority only. DRAIN: the
        # live quote channel appends another causal sample and the recurring
        # monitor re-evaluates it. RESET: no latch exists; every decision
        # recomputes this bounded window and a gap/recovery returns zero/one.
        #
        # A confirmation must also be SEPARATED from the current quote by
        # _FLASH_CRASH_CONFIRMATION_MIN_SPAN_SECONDS: "persisted for at least N
        # consecutive causal quote samples" is a statement about time, and two
        # prints seconds apart are one market event observed twice.
        count = 1
        for price, sample_at in samples:
            velocity = _causal_market_velocity_1h(
                conn,
                token_id=token_id,
                current_bid=price,
                observed_at=sample_at,
            )
            if velocity is None or velocity > threshold:
                break
            count += 1
            if count >= required:
                break
        return current_velocity, count
    except (TypeError, ValueError, sqlite3.Error):
        return None, 0


def _causal_deep_market_catastrophe_confirmations(
    conn: sqlite3.Connection | None,
    *,
    token_id: str,
    current_bid: float,
    observed_at: str | None,
) -> int:
    """Compatibility projection of the coherent catastrophe witness."""

    return _causal_deep_market_catastrophe_evidence(
        conn,
        token_id=token_id,
        current_bid=current_bid,
        observed_at=observed_at,
    )[1]


def _model_only_native_posterior(p_native: float) -> float:
    """Return held-side payoff belief without using executable quote as prior."""
    p = float(p_native)
    if not np.isfinite(p) or not 0.0 <= p <= 1.0:
        raise ValueError(f"native monitor probability must be in [0, 1], got {p!r}")
    return p


def _normalize_monitor_direction(direction: object) -> str:
    """Normalize entry direction before converting YES-bin belief to held side."""
    direction_value = getattr(direction, "value", direction)
    text = str(direction_value or "").strip().lower()
    if text in {"buy_yes", "yes", "direction.yes"}:
        return "buy_yes"
    if text in {"buy_no", "no", "direction.no"}:
        return "buy_no"
    raise ValueError(f"unsupported monitor direction {direction!r}")


def _held_side_probability_from_yes_bin_probability(p_yes_bin: float, direction: object) -> float:
    """Convert a YES-bin point probability into the held-side outcome space."""
    p_yes = _model_only_native_posterior(p_yes_bin)
    if _normalize_monitor_direction(direction) == "buy_no":
        return _model_only_native_posterior(one_minus(p_yes))
    return p_yes


def _day0_monitor_belief_validations(
    selected_method: str,
    *,
    kind: str,
    metric: str | None = None,
) -> list[str]:
    metric_part = f";metric={metric}" if metric else ""
    return [
        selected_method,
        (
            f"belief_source={selected_method}"
            f";kind={kind}{metric_part}"
            ";posterior_mode=model_only_v1"
        ),
        f"market_quote_prior_excluded:{selected_method}",
        f"alpha_blend_inapplicable:{selected_method}",
    ]


def _stamp_day0_remaining_window_belief(
    position: Position,
    *,
    metric: str | None = None,
) -> None:
    _stamp_day0_monitor_belief(
        position,
        selected_method=SELECTED_METHOD_DAY0_OBSERVATION_REMAINING_WINDOW,
        kind="probabilistic_remaining_window",
        metric=metric,
    )


def _stamp_day0_monitor_belief(
    position: Position,
    *,
    selected_method: str,
    kind: str,
    metric: str | None = None,
) -> None:
    setattr(position, "selected_method", selected_method)
    for validation in _day0_monitor_belief_validations(
        selected_method,
        kind=kind,
        metric=metric,
    ):
        _append_monitor_validation(position, validation)


def _set_monitor_probability_fresh(position: Position, is_fresh: bool) -> None:
    setattr(position, _MONITOR_PROBABILITY_FRESH_ATTR, is_fresh)


def _set_day0_zero_probability_exit_authority(position: Position, has_authority: bool) -> None:
    setattr(position, _DAY0_ZERO_PROBABILITY_EXIT_AUTHORITY_ATTR, has_authority)


# K6 stage-1 belief-dead watchdog (2026-06-12). A fail-closed hold on missing
# probability authority is correct for one cycle and a silent catastrophe for
# 719 (the Karachi position was monitored its whole life with stale belief and
# nothing escalated). Track consecutive stale-belief cycles per position WHILE
# the market price stays fresh; at the threshold, brand the monitor event and
# log at ERROR so the condition is loud in both the event payload and the log.
_BELIEF_STALE_FAULT_THRESHOLD = 3
_belief_stale_cycles: dict[str, int] = {}

# LAYER 2 belief-debt ledger (2026-06-21 held-belief freeze fix). When the
# synchronous same-authority read-through CANNOT honestly recompute a held
# family's belief (no current single_runs / no on-disk anchor artifact), the
# fail-closed HOLD is correct but MUST be durably recorded so it is never a silent
# permanent freeze. We track (family -> first_failed_at, attempts) in process and
# stamp the structured marker onto the position's applied_validations, which
# cycle_runtime persists to position_events (TRADES state, INV-37 — the monitor
# writes only order-lifecycle state). The existing same-family reseed enqueue is
# the repair lane; the read-through retries every cycle, so the debt is RETRYABLE.
_belief_debt_first_failed_at: dict[str, str] = {}
_belief_debt_attempts: dict[str, int] = {}


def _record_belief_debt(pos: "Position", *, city: str, target_date: str, metric: str, reason: str) -> str:
    """Stamp a durable, retryable belief-debt marker on the position.

    Returns the marker string (also appended to applied_validations). The marker
    carries family + reason + first_failed_at + attempt count so a held position
    can never be silently frozen — the operator/audit can query position_events
    for ``belief_debt`` and the read-through retries it next cycle.
    """
    from datetime import datetime, timezone

    key = f"{city}|{target_date}|{metric}|{getattr(pos, 'trade_id', '') or id(pos)}"
    now_iso = datetime.now(timezone.utc).isoformat()
    first = _belief_debt_first_failed_at.setdefault(key, now_iso)
    attempts = _belief_debt_attempts.get(key, 0) + 1
    _belief_debt_attempts[key] = attempts
    marker = (
        f"belief_debt;city={city};target_date={target_date};metric={metric};"
        f"reason={reason};first_failed_at={first};attempts={attempts}"
    )
    _append_monitor_validation(pos, marker)
    return marker


def _clear_belief_debt(*, city: str, target_date: str, metric: str, pos: "Position") -> None:
    """A successful read-through clears the family's belief-debt counters."""
    key = f"{city}|{target_date}|{metric}|{getattr(pos, 'trade_id', '') or id(pos)}"
    _belief_debt_first_failed_at.pop(key, None)
    _belief_debt_attempts.pop(key, None)


# Day0 same-identity reseed idempotency (self-defeating-freshness fix, 2026-09-13).
# `_perform_single_family_belief_reseed_failsoft`'s Day0 branch requires a posterior
# committed AFTER `minimum_posterior_computed_at` before it will treat the gap as
# drained. Recomputing that bound to wall-clock `now()` on every call means no
# already-existing posterior -- including one this same repair lane committed a
# second earlier -- can ever satisfy it, so an UNCHANGED Day0 observation identity
# gets re-enqueued every monitor cycle (Beijing 2026-09-13: 42 redundant enqueues
# in 17 minutes for one unchanged extreme value, see T_beijing_belief trace). Pin
# the bound to when THIS gap (this exact conditioning identity, per family) was
# FIRST detected instead: a rollover to a new identity starts a fresh window
# (different key), and a resolved gap clears its entry so a later genuine gap on
# the same identity is detected fresh rather than reusing a stale first-seen time.
_day0_reseed_gap_first_seen_at: dict[str, tuple[str, str]] = {}


def _day0_reseed_minimum_posterior_computed_at(
    *,
    city: str,
    target_date: str,
    metric: str,
    day0_payload: dict[str, object],
    repair_started_at: datetime,
) -> datetime:
    """First-detection time of the current Day0 conditioning identity's gap.

    Returns ``repair_started_at`` itself on first detection (same as the old
    behavior for that one call) and the stored first-seen time on every
    subsequent call for the SAME identity, so an already-committed posterior can
    satisfy the freshness check instead of being permanently too old to count.
    """
    from src.data.replacement_cycle_advance_trigger import (  # noqa: PLC0415
        _day0_conditioning_identity,
    )

    identity = _day0_conditioning_identity(
        source=day0_payload.get("day0_observed_extreme_source"),
        observation_time=day0_payload.get("day0_observed_extreme_observation_time"),
        observed_extreme_c=day0_payload.get("day0_observed_extreme_c"),
        unit=day0_payload.get("day0_observed_extreme_unit"),
    ) or str(day0_payload.get("day0_observation_state") or "")
    family_key = f"{city}|{target_date}|{metric}"
    prior = _day0_reseed_gap_first_seen_at.get(family_key)
    if prior is not None and prior[0] == identity:
        return datetime.fromisoformat(prior[1])
    first_seen_iso = repair_started_at.isoformat()
    _day0_reseed_gap_first_seen_at[family_key] = (identity, first_seen_iso)
    return repair_started_at


def _clear_day0_reseed_gap(*, city: str, target_date: str, metric: str) -> None:
    """A drained Day0 gap clears its first-seen record (mirrors `_clear_belief_debt`)."""
    _day0_reseed_gap_first_seen_at.pop(f"{city}|{target_date}|{metric}", None)


def _track_belief_staleness(pos: Position) -> None:
    key = str(getattr(pos, "trade_id", "") or id(pos))
    if getattr(pos, "last_monitor_prob_is_fresh", False):
        _belief_stale_cycles.pop(key, None)
        return
    if not getattr(pos, "last_monitor_market_price_is_fresh", False):
        return
    count = _belief_stale_cycles.get(key, 0) + 1
    _belief_stale_cycles[key] = count
    # The cycle's own validation tags say WHY belief is not fresh (a designed
    # decline such as POST_LOCAL_DAY_FINAL_OBSERVATION_UNAVAILABLE, or a failed
    # refresh); the fault line carries them so the alarm is precise.
    reasons = ",".join(
        tag for tag in (getattr(pos, "applied_validations", []) or [])
        if tag != "BELIEF_AUTHORITY_FAULT" and not tag.startswith("belief_stale_cycles=")
    )
    _append_monitor_validation(pos, f"belief_stale_cycles={count}")
    if count >= _BELIEF_STALE_FAULT_THRESHOLD:
        _append_monitor_validation(pos, "BELIEF_AUTHORITY_FAULT")
        logger.error(
            "BELIEF_AUTHORITY_FAULT: position %s (%s %s %s) has had stale belief "
            "for %d consecutive monitor cycles while the market price is fresh — "
            "the exit organ is blind on a live position; reasons=%s",
            getattr(pos, "trade_id", "?"),
            getattr(pos, "city", "?"),
            getattr(pos, "target_date", "?"),
            getattr(pos, "direction", "?"),
            count,
            reasons or "none",
        )


def _is_position_target_local_day(pos: Position, city, target_d) -> bool:
    if target_d is None:
        return False
    try:
        target_date_value = target_d if isinstance(target_d, date) else date.fromisoformat(str(target_d))
    except Exception:
        return False
    timezone_name = str(getattr(city, "timezone", "") or "").strip()
    try:
        local_today = datetime.now(ZoneInfo(timezone_name)).date()
    except Exception:
        local_today = datetime.now(timezone.utc).date()
    return target_date_value == local_today


def _is_position_after_target_local_day(
    pos: Position,
    city,
    target_d,
    *,
    now: datetime | None = None,
) -> bool:
    """Whether no forecast hours can remain in the contract-local target day."""

    if target_d is None:
        return False
    try:
        target_date_value = (
            target_d if isinstance(target_d, date) else date.fromisoformat(str(target_d))
        )
        local_today = (now or datetime.now(timezone.utc)).astimezone(
            ZoneInfo(str(getattr(city, "timezone", "") or ""))
        ).date()
    except Exception:
        return False
    return target_date_value < local_today


def _perform_single_family_belief_reseed_failsoft(
    *, city: str, target_date: str, metric: str
) -> dict[str, object] | None:
    """Fail-soft single-family replacement-posterior re-materialization trigger.

    Called when a non-day0 held position finds its replacement belief
    stale/missing (BELIEF_AUTHORITY_FAULT): re-materialize THAT family's
    posterior onto the freshest materializable cycle so the exit organ regains a
    fresh same-authority belief next cycle, instead of papering over the fault
    with a cross-era legacy substitution (regime law U1/U2, 2026-06-12).

    Reuses the SAME live materialization lane the reactor/poll uses
    (forecast_db/seed_dir/raw_manifest_dir from the live queue config + the
    shared idempotency marker),
    so a family already enqueued elsewhere never double-enqueues. NEVER raises into
    the monitor: any error (config missing, DB lock, import failure) is logged and
    a status dict (or None) is returned.
    """
    try:
        repair_started_at = datetime.now(timezone.utc)
        # A held family's target local day can end (local midnight rolls past
        # target_date) while this lane keeps firing every stale-belief detection
        # cycle. Past that point `_is_position_target_local_day`'s exact-date
        # match already empties the Day0 payload (`_day0_observed_extreme_reseed_payload`
        # returns {}), so the seed builder fails the request closed with
        # OM9_LOCALDAY_HOURLY_COVERAGE_INCOMPLETE before any materialization work
        # runs (London 2026-09-13 high, receipt
        # seed_failed/London.2026-09-13.high.20260914T030722Z...json,
        # request_written:false). Gate on the exact semantic complement of the
        # "day started" check two call sites over, using the SAME clock read
        # this function already needs for `minimum_posterior_computed_at` below,
        # so nothing reaches the DB/file work for an ended local day.
        city_obj = cities_by_name.get(str(city))
        try:
            target_d = date.fromisoformat(str(target_date))
        except Exception:
            target_d = None
        if city_obj is not None and _is_position_after_target_local_day(
            None, city_obj, target_d, now=repair_started_at
        ):
            _clear_day0_reseed_gap(city=city, target_date=target_date, metric=metric)
            logger.info(
                "monitor belief reseed skipped (target local day ended): "
                "city=%s target_date=%s metric=%s",
                city, target_date, metric,
            )
            return {
                "status": "RESEED_SKIPPED_TARGET_LOCAL_DAY_ENDED",
                "city": city,
                "target_date": target_date,
                "metric": metric,
                "enqueued": False,
            }

        from pathlib import Path

        from src.data.replacement_forecast_production import (
            _replacement_forecast_live_materialization_queue_config,
        )
        from src.data.replacement_cycle_advance_trigger import (
            enqueue_single_family_cycle_advance_reseed,
        )
        from src.data.replacement_fusion_upgrade_trigger import (
            enqueue_fusion_upgrade_reseeds,
        )

        cfg = _replacement_forecast_live_materialization_queue_config()
        forecast_db = cfg.get("forecast_db")
        seed_dir = cfg.get("seed_dir")
        raw_manifest_dir = cfg.get("raw_manifest_dir")
        if forecast_db is None or seed_dir is None or raw_manifest_dir is None:
            logger.info(
                "monitor belief reseed skipped (lane not configured): %s/%s/%s",
                city, target_date, metric,
            )
            return None
        day0_payload = _day0_observed_extreme_reseed_payload(
            city=city,
            target_date=target_date,
            metric=metric,
        )
        # A stale held belief has two independent causal repairs. A strictly
        # newer carrier cycle belongs to cycle-advance, while newer provider
        # rows at the SAME carrier cycle belong to fusion/input-revision
        # upgrade. Sending both cases only to cycle-advance makes the latter an
        # impossible reset: it correctly returns CYCLE_ADVANCE_NOT_NEEDED while
        # the exit organ remains blind despite already-persisted newer inputs.
        fusion_report = enqueue_fusion_upgrade_reseeds(
            forecast_db=Path(str(forecast_db)),
            seed_dir=Path(str(seed_dir)),
            raw_manifest_dir=Path(str(raw_manifest_dir)),
            limit=1,
            scopes=((city, target_date, metric),),
        )
        if int(fusion_report.get("seeds_enqueued", 0) or 0) > 0:
            report = dict(fusion_report)
            report.update(
                status="BELIEF_INPUT_REVISION_RESEED_ENQUEUED",
                enqueued=True,
                repair_lane="input_revision",
            )
            logger.info(
                "monitor belief reseed enqueued city=%s target_date=%s metric=%s "
                "status=%s enqueued=%s repair_lane=%s",
                city,
                target_date,
                metric,
                report["status"],
                report["enqueued"],
                report["repair_lane"],
            )
            return report
        input_revision_status = fusion_report.get("status")
        if int(fusion_report.get("already_enqueued", 0) or 0) > 0:
            # The input-revision marker is durable after its seed is consumed.
            # It proves only that lane's request identity; it cannot veto the
            # independent newer-carrier-cycle repair.  Falling through gives a
            # later materializable cycle a real RESET instead of retaining the
            # held family in BELIEF_AUTHORITY_FAULT forever.
            input_revision_status = "BELIEF_INPUT_REVISION_RESEED_PENDING"
        from src.engine.position_belief import monitor_belief_max_age_hours

        # Day0 hourly vectors can advance without changing the observation identity or
        # carrier cycle. Once current-q construction rejects the old vector witness,
        # only a posterior built after the gap was FIRST detected (not "now" recomputed
        # on every call -- see _day0_reseed_gap_first_seen_at) can prove the gap drained.
        minimum_posterior_computed_at = (
            _day0_reseed_minimum_posterior_computed_at(
                city=city,
                target_date=target_date,
                metric=metric,
                day0_payload=day0_payload,
                repair_started_at=repair_started_at,
            )
            if day0_payload
            else repair_started_at
            - timedelta(hours=monitor_belief_max_age_hours())
        )
        report = enqueue_single_family_cycle_advance_reseed(
            forecast_db=Path(str(forecast_db)),
            seed_dir=Path(str(seed_dir)),
            raw_manifest_dir=Path(str(raw_manifest_dir)),
            city=city,
            target_date=target_date,
            metric=metric,
            held_position=True,
            minimum_posterior_computed_at=minimum_posterior_computed_at,
            **day0_payload,
        )
        if isinstance(report, dict):
            report = dict(report)
            report["repair_lane"] = "cycle_advance"
            report["input_revision_status"] = input_revision_status
        if (
            day0_payload
            and isinstance(report, dict)
            and report.get("status") == "CYCLE_ADVANCE_NOT_NEEDED"
            and report.get("day0_posterior_matched") is True
        ):
            # CYCLE_ADVANCE_NOT_NEEDED also fires for "still queued" and "owner
            # actively working it" -- neither means the gap is drained. Only a
            # posterior that actually matched this identity (day0_posterior_matched)
            # proves that, so only that case clears the first-seen record. Clearing
            # on the umbrella status alone would let a queue backlog recreate a
            # throttled version of the original defect (R-AG review, 2026-09-13).
            _clear_day0_reseed_gap(city=city, target_date=target_date, metric=metric)
        logger.info(
            "monitor belief reseed enqueued city=%s target_date=%s metric=%s status=%s "
            "enqueued=%s repair_lane=%s day0_observed_extreme=%s",
            city, target_date, metric,
            report.get("status") if isinstance(report, dict) else None,
            report.get("enqueued") if isinstance(report, dict) else None,
            report.get("repair_lane") if isinstance(report, dict) else None,
            day0_payload.get("day0_observed_extreme_c") if day0_payload else None,
        )
        return report
    except Exception as exc:  # noqa: BLE001 — reseed MUST NOT crash the monitor
        logger.warning(
            "monitor belief reseed FAILED (fail-soft) city=%s target_date=%s metric=%s exc=%s",
            city, target_date, metric, exc,
        )
        return None


def _run_single_family_belief_reseed_worker(
    *,
    key: tuple[str, str, str],
    city: str,
    target_date: str,
    metric: str,
) -> None:
    """Run one family repair lane and coalesce arrivals while it is active."""

    while True:
        with _BELIEF_RESEED_LOCK:
            generation = _BELIEF_RESEED_GENERATIONS.get(key)
        if generation is None:
            return
        try:
            _perform_single_family_belief_reseed_failsoft(
                city=city,
                target_date=target_date,
                metric=metric,
            )
        except Exception:  # noqa: BLE001 - worker isolation must survive test/adapter faults
            logger.exception(
                "monitor belief reseed worker failed city=%s target_date=%s metric=%s",
                city,
                target_date,
                metric,
            )
        with _BELIEF_RESEED_LOCK:
            if _BELIEF_RESEED_GENERATIONS.get(key) == generation:
                _BELIEF_RESEED_GENERATIONS.pop(key, None)
                return


def _enqueue_single_family_belief_reseed_failsoft(
    *, city: str, target_date: str, metric: str
) -> dict[str, object] | None:
    """Dispatch belief repair without retaining the held-position SELL lane.

    A reseed cannot change the current monitor decision: its result is consumed
    only by a later re-decision. Keep one worker per family, coalesce arrivals
    during that run, and let unrelated families progress independently.
    """

    city = str(city).strip()
    target_date = str(target_date).strip()[:10]
    metric = str(metric).strip().lower()
    key = (city.casefold(), target_date, metric)
    with _BELIEF_RESEED_LOCK:
        generation = _BELIEF_RESEED_GENERATIONS.get(key, 0) + 1
        dispatch = key not in _BELIEF_RESEED_GENERATIONS
        _BELIEF_RESEED_GENERATIONS[key] = generation
    if not dispatch:
        return {
            "status": "CYCLE_ADVANCE_RESEED_COALESCED",
            "city": city,
            "target_date": target_date,
            "metric": metric,
            "dispatched": False,
        }
    try:
        threading.Thread(
            target=_run_single_family_belief_reseed_worker,
            kwargs={
                "key": key,
                "city": city,
                "target_date": target_date,
                "metric": metric,
            },
            name=f"belief-reseed-{city}-{target_date}-{metric}",
            daemon=True,
        ).start()
    except Exception as exc:  # noqa: BLE001 - monitor remains fail-closed, never blocked
        with _BELIEF_RESEED_LOCK:
            # No worker exists to serve arrivals coalesced while ``start`` was
            # blocked. Clear the whole dispatch window so the next arrival can
            # create a real worker.
            _BELIEF_RESEED_GENERATIONS.pop(key, None)
        logger.warning(
            "monitor belief reseed dispatch FAILED city=%s target_date=%s metric=%s exc=%s",
            city,
            target_date,
            metric,
            exc,
        )
        return None
    return {
        "status": "CYCLE_ADVANCE_RESEED_DISPATCHED",
        "city": city,
        "target_date": target_date,
        "metric": metric,
        "dispatched": True,
    }


_HELD_BELIEF_PENDING_SEED_SCAN_LIMIT = 256


def _freshest_family_seed_on_disk(*, city: str, target_date: str, metric: str):
    """Return the current durable seed or freshest bounded pending seed.

    This runs synchronously on the held-position monitor worker. Processed
    seed/request archive enumeration has no monitor-budget bound and can starve
    every exit. Read the queue-published O(1) per-family hard-link cache first,
    then inspect only the live pending queue with a bounded fallback. A miss
    fails closed; the caller's asynchronous family reseed is the repair lane.

    The seed name is ``{city}.{target_date}.{metric}.{stamp}.json``; we pick the
    lexicographically-latest stamp (ISO-ordered) from the bounded pending slice.
    """
    import json as _json
    import os as _os
    from itertools import islice
    from pathlib import Path

    try:
        from src.data.replacement_forecast_production import (
            _replacement_forecast_live_materialization_queue_config,
        )

        cfg = _replacement_forecast_live_materialization_queue_config()
    except Exception:  # noqa: BLE001 — lane not configured / import failure -> not eligible
        return None
    # Normalize the family file-name segments the seed builder uses (spaces -> '_').
    city_seg = str(city).replace(" ", "_")
    prefix = f"{city_seg}.{target_date}.{metric}."
    seed_dir = cfg.get("seed_dir")
    if not seed_dir:
        return None
    base = Path(str(seed_dir))
    if not base.exists():
        return None
    latest_path = (
        base.parent
        / "seeds_latest"
        / f"{city_seg}.{target_date}.{metric}.json"
    )
    if latest_path.is_file():
        try:
            payload = _json.loads(latest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            payload = None
        if (
            isinstance(payload, dict)
            and _seed_payload_covers_target_local_day(
                seed_path=latest_path,
                payload=payload,
            )
        ):
            return latest_path, payload
    candidates: list[tuple[str, Path]] = []
    try:
        with _os.scandir(base) as entries:
            for entry in islice(
                entries,
                _HELD_BELIEF_PENDING_SEED_SCAN_LIMIT,
            ):
                name = entry.name
                if (
                    not name.startswith(prefix)
                    or not name.endswith(".json")
                    or name.endswith(".receipt.json")
                    or not entry.is_file(follow_symlinks=False)
                ):
                    continue
                # Compare by the trailing stamp portion (ISO timestamps sort lexically).
                stamp = name[len(prefix):]
                candidates.append((stamp, base / name))
    except OSError:
        return None
    for _stamp, path in sorted(candidates, reverse=True):
        try:
            payload = _json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        if not _seed_payload_covers_target_local_day(seed_path=path, payload=payload):
            continue
        return path, payload
    return None


def _seed_payload_covers_target_local_day(*, seed_path, payload: dict) -> bool:
    """True iff a held-belief seed can extract its requested local day."""
    from pathlib import Path

    try:
        target = date.fromisoformat(str(payload.get("target_date") or "").strip())
        city_timezone = str(payload.get("city_timezone") or "").strip()
        payload_text = str(payload.get("openmeteo_payload_json") or "").strip()
        if not city_timezone or not payload_text:
            return False
        openmeteo_payload_path = Path(payload_text)
        if not openmeteo_payload_path.is_absolute():
            openmeteo_payload_path = Path(seed_path).parent / openmeteo_payload_path
        openmeteo_payload = json.loads(openmeteo_payload_path.read_text(encoding="utf-8"))
        from src.data.openmeteo_ecmwf_ifs9_anchor import (
            extract_openmeteo_ecmwf_ifs9_localday_anchor,
        )

        extract_openmeteo_ecmwf_ifs9_localday_anchor(
            openmeteo_payload,
            city_timezone=city_timezone,
            target_local_date=target,
            min_hourly_samples=1,
            require_full_localday=False,
        )
    except Exception:
        return False
    return True


def _attempt_held_belief_readthrough(
    pos: "Position", *, city, target_d, metric: str,
    decision_now: datetime | None = None,
    deadline_monotonic: float | None = None,
) -> tuple[float, float, float] | None:
    """LAYER 2 — synchronous single-family read-through recompute (held-belief freeze fix).

    Recompute THIS held family's replacement posterior via the SAME canonical
    Bayes-precision fusion the live write path uses, against whatever single_runs
    are CURRENTLY persisted, WITHOUT writing forecast_posteriors. Returns the
    fresh HELD-SIDE probability for the position's bin, or None when the family
    cannot be honestly recomputed (no on-disk anchor seed / no current single_runs
    / not live-eligible). Returns held-side ``(q, lcb, ucb)`` atomically; the
    point is not authority without its current-evidence bounds. Fewer providers
    ⇒ honestly wider fusion CI (correct).

    ``decision_now`` is the CURRENT monitor cycle instant (the decision time for
    this recompute).  The arrival guard inside the Bayes-precision fusion admits
    only single_runs whose ``source_available_at <= decision_now``, so it MUST be
    the live clock — not the seed's original ``computed_at`` (which could be hours
    earlier, causing every recently-arrived single_run to be excluded and the
    recompute to collapse to STALE_HISTORY_ONLY / live_eligible=False).  The seed's
    ``source_cycle_time`` (the forecast cycle hour, e.g. "06:00 UTC") is kept
    verbatim: it identifies WHICH cycle's single_runs to fuse, not a wall-clock.

    Testability: ``decision_now`` defaults to ``None`` (→ ``datetime.now(UTC)``).
    Pass an explicit value in tests to make the arrival-guard behaviour deterministic.

    INV-37: reads forecasts via a dedicated READ-ONLY forecasts-MAIN connection
    (``get_forecasts_connection_read_only``) — the SAME pattern this module already
    uses for bare ``raw_model_forecasts`` reads — NEVER the trades lifecycle conn
    (whose MAIN is zeus_trades, where the fusion's bare forecast-table names would
    not resolve) and NEVER an independent WRITE connection. Writes nothing.

    Fail-soft: ANY error / missing input returns None so the caller fail-closes to
    HOLD + belief_debt (never a fabricated belief, never a monitor crash).
    """
    try:
        target_date = str(getattr(pos, "target_date", "") or "")
        if not target_date:
            return None
        seed = _freshest_family_seed_on_disk(
            city=str(pos.city), target_date=target_date, metric=metric
        )
        if seed is None:
            return None
        seed_path, seed_payload = seed

        # The on-disk seed is a source/anchor envelope, not the monitor decision
        # instant. Re-use its source-cycle identity, but stamp the read-only
        # request with the current monitor clock so arrival/freshness guards do
        # not compare a new decision time to an expired seed TTL.
        _now = decision_now if decision_now is not None else datetime.now(timezone.utc)
        from src.engine.position_belief import monitor_belief_max_age_hours

        readthrough_ttl_h = max(0.01, float(monitor_belief_max_age_hours()))
        readthrough_payload = dict(seed_payload)
        readthrough_payload["computed_at"] = _now.isoformat()
        readthrough_payload["expires_at"] = (_now + timedelta(hours=readthrough_ttl_h)).isoformat()

        from src.data.replacement_forecast_materialization_request_builder import (
            build_materialize_request_dataclass,
            build_replacement_forecast_materialization_request,
        )

        build = build_replacement_forecast_materialization_request(
            readthrough_payload, base_dir=seed_path.parent
        )
        if not build.ok or build.request is None:
            return None
        request = build_materialize_request_dataclass(
            build.request, base_dir=seed_path.parent
        )

        # ARRIVAL-GUARD DECISION INSTANT FIX (real-chain verified 2026-06-21):
        # The seed's ``computed_at`` is the seed's BUILD time (e.g. 12:09:08 for
        # Panama City's 12Z seed).  The Bayes-precision fusion's arrival guard
        # excludes single_runs whose ``source_available_at > computed_at``; for
        # frozen families the relevant single_runs arrived AFTER the seed's build
        # time (e.g. 06:00-cycle at 14:10) — so using the seed's stale computed_at
        # fuses ZERO multi-model extras → STALE_HISTORY_ONLY → live_eligible=False
        # → read-through returns None → the freeze is reproduced, not cured.
        # Fix: the DECISION INSTANT for this read-through recompute is NOW (the
        # live monitor cycle), so all single_runs available at that instant are
        # admitted.  source_cycle_time (the forecast cycle, "06:00 UTC") is kept
        # verbatim — it is NOT a wall-clock and must NOT be advanced.
        request = replace(
            request,
            computed_at=_now,
            expires_at=_now + timedelta(hours=readthrough_ttl_h),
        )

        from src.data.replacement_forecast_materializer import (
            compute_replacement_posterior_readonly,
        )
        from src.state.db import get_forecasts_connection_read_only

        fc_conn = get_forecasts_connection_read_only()
        try:
            fc_conn.row_factory = sqlite3.Row
            # Enforce the read-only contract at the SQLite level, not just by the
            # factory's name (critic 2026-06-21, MEDIUM-1): query_only turns the
            # no-write guarantee from convention into enforcement. Any inadvertent
            # write through this connection — e.g. a future edit to a reader deep in
            # the fusion call tree — raises instead of silently corrupting forecast
            # truth during the live monitor loop. The compute path is provably
            # write-free today; this is defense-in-depth on a live 51GB forecasts DB.
            fc_conn.execute("PRAGMA query_only=ON")
            if deadline_monotonic is not None:
                deadline = float(deadline_monotonic)

                def _monitor_deadline_expired() -> int:
                    return int(time.monotonic() >= deadline)

                # SCOPE: only this position's synchronous fallback recompute.
                # DRAIN: SQLite interrupts at the monitor cycle deadline and the
                # remaining positions retain their fair next-cycle reservation.
                # RESET: the progress handler dies with this read-only connection;
                # every monitor cycle injects a fresh deadline.
                fc_conn.set_progress_handler(_monitor_deadline_expired, 1_000)
            result = compute_replacement_posterior_readonly(fc_conn, request)
        finally:
            if deadline_monotonic is not None:
                try:
                    fc_conn.set_progress_handler(None, 0)
                except Exception:  # noqa: BLE001 - connection close is the backstop.
                    pass
            fc_conn.close()
        if result is None or not result.live_eligible:
            return None
        # Index the held bin by its venue range-label, exactly like load_replacement_belief.
        from src.engine.position_belief import _match_bin, held_side_bounds  # noqa: PLC0415

        if result.q_lcb_map is None or result.q_ucb_map is None:
            return None
        matched = _match_bin(result.q, str(pos.bin_label))
        matched_lcb = _match_bin(result.q_lcb_map, str(pos.bin_label))
        matched_ucb = _match_bin(result.q_ucb_map, str(pos.bin_label))
        if matched is None or matched_lcb is None or matched_ucb is None:
            return None
        _bin_key, q_yes = matched
        _lcb_key, q_yes_lcb = matched_lcb
        _ucb_key, q_yes_ucb = matched_ucb
        if not (0.0 <= q_yes_lcb <= q_yes <= q_yes_ucb <= 1.0):
            return None
        direction = str(getattr(pos.direction, "value", pos.direction))
        held = _held_side_probability_from_yes_bin_probability(q_yes, direction)
        held_lcb, held_ucb = held_side_bounds(q_yes_lcb, q_yes_ucb, direction)
        logger.info(
            "monitor held-belief READ-THROUGH recompute OK city=%s target_date=%s metric=%s "
            "providers=%d/%d q_held=%.4f band=[%.4f,%.4f] "
            "(exit organ regains fresh same-authority belief)",
            pos.city, target_date, metric,
            result.decorrelated_providers_served, result.decorrelated_providers_expected,
            held, held_lcb, held_ucb,
        )
        return float(held), float(held_lcb), float(held_ucb)
    except Exception as exc:  # noqa: BLE001 — read-through MUST NOT crash the monitor
        logger.warning(
            "monitor held-belief read-through FAILED (fail-soft -> fail-close) "
            "city=%s target_date=%s metric=%s exc=%s",
            getattr(pos, "city", "?"), getattr(pos, "target_date", "?"), metric, exc,
        )
        return None


def _attempt_held_belief_readthrough_outside_bounded_monitor(
    pos: "Position", *, city, target_d, metric: str,
    decision_now: datetime | None = None,
    deadline_monotonic: float | None = None,
) -> tuple[float, float, float] | None:
    """Keep synchronous fusion outside the portfolio monitor critical path.

    The live held-position monitor always supplies a cycle deadline.  Python work
    inside the fusion stack cannot be interrupted by SQLite's progress handler,
    so attempting it inline can retain every later position past that deadline.
    A bounded monitor therefore fails closed and lets the independent reseed /
    materialization producer publish authority for the next re-decision.  Direct
    unbounded callers retain the diagnostic read-through behavior.
    """

    if deadline_monotonic is not None:
        return None
    return _attempt_held_belief_readthrough(
        pos,
        city=city,
        target_d=target_d,
        metric=metric,
        decision_now=decision_now,
        deadline_monotonic=None,
    )


def _parse_utc_datetime(raw: object) -> datetime | None:
    if isinstance(raw, bool):
        return None
    return _parse_day0_observation_time_utc(raw)


def _position_state_value(pos: Position) -> str:
    return str(getattr(getattr(pos, "state", ""), "value", getattr(pos, "state", "")) or "")


def _city_supports_executable_day0_observation(city) -> bool:
    source_type = str(getattr(city, "settlement_source_type", "") or "").strip()
    return source_type in DAY0_EXECUTABLE_OBSERVATION_SOURCES_BY_SETTLEMENT_TYPE


def _fetch_canonical_day0_observation_from_instants(
    city: object,
    target_d: date,
    *,
    reference_time: datetime,
) -> Day0ObservationContext | None:
    """Build an executable Day0 observation from canonical observation_instants."""

    try:
        from src.data.day0_observation_reader import (
            read_day0_observation_context_from_instants,
        )
        from src.state.db import get_world_connection_read_only
    except Exception:
        return None

    city_name = str(getattr(city, "name", "") or "")
    timezone_name = str(getattr(city, "timezone", "") or "")
    if not city_name or not timezone_name:
        return None
    conn = None
    try:
        conn = get_world_connection_read_only()
        return read_day0_observation_context_from_instants(
            conn,
            city=city,
            target_date=target_d.isoformat(),
            decision_time_utc=reference_time,
        )
    except Exception:
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _temperature_native_value_to_c(value: float, *, unit: str) -> float:
    normalized = str(unit or "").strip().upper()
    number = float(value)
    if normalized == "C":
        return number
    if normalized == "F":
        return (number - 32.0) * 5.0 / 9.0
    raise ValueError(f"unsupported Day0 observed-extreme unit: {unit!r}")


def _day0_observed_extreme_from_canonical_surface(
    *,
    city_name: str,
    target_date: str,
    metric_is_low: bool,
    now: datetime | None = None,
    world_conn: sqlite3.Connection | None = None,
) -> tuple[float, str, str, int] | None:
    """Observed running extreme + its observation version from the canonical settlement-grade
    ``world.observation_instants`` surface — the SAME source the day0 hard-fact lane
    (``day0_hard_fact_exit._durable_observation_instants_extremes``) and the
    ``day0_extreme_updated`` trigger already treat as authoritative.

    Same-day exit-blindness fix 2026-06-23: the monitor belief reseed previously sourced the
    observed extreme ONLY from a live-provider fetch (``get_current_observation``) that routinely
    fails on the settlement day ("All observation providers failed for <city>/<date>"), starving
    the day0 conditioning while this canonical WU-hourly surface already held the verified running
    extreme (Toronto NO@24 -98.94% incident). Returns ``(observed_native, observation_time_iso,
    chosen_source, sample_count)``, or None when no admissible row is available up to ``now``.
    Preserving ``chosen_source`` is required because WU/Ogimet running extrema are monotone bounds
    while the HKO intraday accumulator is a revisable current snapshot. ``world_conn`` is injectable
    for tests; otherwise a private short-lived read-only world connection is opened and closed (the
    position_belief read posture). See
    docs/evidence/same_day_exit_blindness/2026-06-23_toronto_total_loss.md.
    """
    decision_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    owns_conn = world_conn is None
    if owns_conn:
        try:
            from src.state.db import get_world_connection_read_only

            world_conn = get_world_connection_read_only()
        except Exception:  # noqa: BLE001 — read posture is best-effort; reseed continues without it
            return None
    try:
        from src.data.day0_observation_reader import (
            COVERAGE_NONE,
            read_day0_observed_extrema,
            source_priority_for_city,
        )

        city = cities_by_name.get(city_name)
        if city is None:
            return None
        priority = source_priority_for_city(city, target_date)
        for table_ref in ("world.observation_instants", "observation_instants"):
            try:
                result = read_day0_observed_extrema(
                    world_conn,
                    city=city_name,
                    target_date=target_date,
                    timezone_name=str(getattr(city, "timezone", "") or "UTC"),
                    decision_time_utc=decision_time,
                    source_priority=priority,
                    table_ref=table_ref,
                )
            except sqlite3.DatabaseError:
                continue
            if result.coverage_status == COVERAGE_NONE:
                continue
            extreme = result.low_so_far if metric_is_low else result.high_so_far
            if extreme is None or not result.last_observation_time_utc:
                continue
            return (
                float(extreme),
                str(result.last_observation_time_utc),
                str(result.chosen_source),
                int(result.row_count),
            )
        return None
    finally:
        if owns_conn:
            try:
                world_conn.close()
            except Exception:  # noqa: BLE001
                pass


def _compose_day0_observed_extreme(
    *,
    live: tuple[float, str, str, int] | None,
    canonical: tuple[float, str, str, int] | None,
    metric_is_low: bool,
) -> tuple[float, str, str, int] | None:
    """Compose live + canonical observed extremes by the ABSORBING LAW (consult
    REQ-20260623-184115 BLOCKER): the canonical settlement-grade surface is a HARD bound; a live
    reading may only IMPROVE the absorbing extreme (raise the high / lower the low), never undercut
    it. Returns ``(observed_native, observation_time_iso, source, sample_count)`` for the dominant
    source, with the LATER observation version on a tie so a fresh plateau still advances the
    idempotency version. None when neither source is available. A stale/lower live value therefore
    can NEVER suppress a higher canonical running extreme and materialise a fresh-but-wrong belief
    (the 9h staleness guard cannot catch a semantically false but timestamp-fresh posterior).
    ``live`` and ``canonical`` both carry (native, observation_time, source, sample_count).
    """
    from src.data.replacement_cycle_advance_trigger import normalize_observation_version

    candidates: list[tuple[float, str, str, int]] = []
    if live is not None:
        candidates.append((float(live[0]), str(live[1]), str(live[2]), int(live[3])))
    if canonical is not None:
        candidates.append(
            (float(canonical[0]), str(canonical[1]), str(canonical[2]), int(canonical[3]))
        )
    if not candidates:
        return None
    extreme = min(c[0] for c in candidates) if metric_is_low else max(c[0] for c in candidates)
    dominant = [c for c in candidates if c[0] == extreme]
    best = max(dominant, key=lambda c: normalize_observation_version(c[1]) or "")
    return (extreme, best[1], best[2], best[3])


def _day0_observed_extreme_reseed_payload(
    *, city: str, target_date: str, metric: str
) -> dict[str, object]:
    """Read the exact Day0 conditioning identity used by seed discovery/current-global.

    A monitor visibility repair must not compose a narrower local observation
    view: it would enqueue a valid-but-stale identity after current-global has
    advanced on a later causal ``observation_print``.  The shared producer
    includes the canonical settlement fact, fast-station conditioning, and the
    latest causal observation frontier under one identity contract.
    """
    city_obj = cities_by_name.get(str(city))
    if city_obj is None or not _city_supports_executable_day0_observation(city_obj):
        return {}
    try:
        target_d = date.fromisoformat(str(target_date))
    except Exception:
        return {}
    if not _is_position_target_local_day(None, city_obj, target_d):
        return {}
    try:
        from src.data.replacement_forecast_seed_discovery import (  # noqa: PLC0415
            _day0_observed_extreme_seed_payload,
        )

        payload = _day0_observed_extreme_seed_payload(
            city=str(city),
            target_date=str(target_date),
            metric=str(metric),
            computed_at=datetime.now(timezone.utc),
        )
    except Exception as exc:  # noqa: BLE001 - monitor repair remains fail-soft
        logger.info(
            "monitor belief reseed Day0 canonical conditioning unavailable "
            "city=%s target_date=%s metric=%s exc=%s",
            city,
            target_date,
            metric,
            exc,
        )
        return {}
    return dict(payload or {})


def _one_sided_monitor_quote(
    conn,
    clob: PolymarketClient,
    pos: Position,
    token_id: str,
    *,
    book: dict | None = None,
    source_timestamp: str | None = None,
) -> HeldTokenMonitorQuote | None:
    if book is None and not hasattr(clob, "get_orderbook"):
        return None
    try:
        from src.data.market_scanner import _top_book_level_decimal

        if book is None:
            book = clob.get_orderbook(token_id)
        # An explicit empty depth side is a current market fact, distinct from
        # an absent or malformed book.  The monitor can carry its zero
        # liquidation value forward, while the exit/submit boundaries still
        # reject it as non-executable SELL authority.
        if not isinstance(book, dict):
            return None
        bids = book.get("bids")
        asks = book.get("asks")
        if not isinstance(bids, list) or not isinstance(asks, list):
            return None

        if bids:
            best_bid, bid_size = _top_book_level_decimal(book, "bids")
        else:
            best_bid = bid_size = None
        if asks:
            best_ask, ask_size = _top_book_level_decimal(book, "asks")
        else:
            best_ask = ask_size = None

        bid_f = float(best_bid) if best_bid is not None else 0.0
        bid_sz_f = float(bid_size) if bid_size is not None else 0.0
        ask_f = float(best_ask) if best_ask is not None else None
        ask_sz_f = float(ask_size) if ask_size is not None else 0.0
        if ask_f is not None and (not np.isfinite(ask_f) or ask_f <= 0.0):
            ask_f = None
            ask_sz_f = 0.0
        if not np.isfinite(bid_f) or bid_f < 0.0 or not np.isfinite(bid_sz_f) or bid_sz_f < 0.0:
            return None
        source_timestamp = source_timestamp or datetime.now(timezone.utc).isoformat()
        from src.data.market_scanner import _bid_ladder_from_book

        return HeldTokenMonitorQuote(
            token_id=token_id,
            best_bid=bid_f,
            best_ask=ask_f,
            bid_size=bid_sz_f,
            ask_size=ask_sz_f,
            mark_price=bid_f,
            source_timestamp=source_timestamp,
            min_order_size=_book_min_order_size(book),
            min_tick=_book_min_tick(book),
            bid_ladder=(
                _bid_ladder_from_book(book) if isinstance(book, dict) else ()
            ),
            full_depth_action_authority=True,
        )
    except Exception as exc:
        logger.debug(
            "Held one-sided quote refresh failed for %s: %s",
            pos.trade_id,
            exc,
        )
        return None


def monitor_quote_refresh(
    conn,
    clob: PolymarketClient,
    pos: Position,
    *,
    retry_after_prefetch: bool = False,
) -> HeldTokenMonitorQuote | None:
    """Refresh held-token executable quote without opening a DB write."""

    tid = pos.token_id if pos.direction == "buy_yes" else pos.no_token_id
    if not tid:
        return None

    book = prefetched_monitor_orderbook(clob, tid)
    source_timestamp: str | None = None
    if book is None:
        # The market-channel snapshot is selection evidence, not submit
        # authority. Consume it before any bounded venue read so a fresh
        # durable held book cannot be starved by the monitor stage deadline.
        fallback = _fresh_canonical_monitor_orderbook(conn, pos, tid)
        if fallback is not None:
            book, source_timestamp = fallback
        else:
            no_bid_quote = _fresh_canonical_monitor_no_bid_witness(
                conn,
                pos,
                tid,
            )
            if no_bid_quote is not None:
                return no_bid_quote
            if (
                monitor_orderbook_prefetch_attempted(clob, tid)
                and not retry_after_prefetch
            ):
                return None
    get_orderbook = getattr(clob, "get_orderbook", None)
    try:
        if book is None:
            deadline = getattr(pos, _HELD_MONITOR_DEADLINE_ATTR, None)
            if deadline is not None:
                remaining = float(deadline) - time.monotonic()
                if remaining <= 0.0:
                    return None
                hard_deadline_books = getattr(
                    clob,
                    "get_held_orderbook_snapshots_hard_deadline",
                    None,
                )
                if not callable(hard_deadline_books):
                    return None
                quote_deadline = _held_monitor_stage_deadline(
                    float(deadline),
                    HELD_MONITOR_NETWORK_QUOTE_READ_MAX_SECONDS,
                )
                book = hard_deadline_books(
                    [tid],
                    timeout_seconds=max(0.0, quote_deadline - time.monotonic()),
                ).get(tid)
                if book is None:
                    fallback = _fresh_canonical_monitor_orderbook(conn, pos, tid)
                    if fallback is None:
                        return _fresh_canonical_monitor_no_bid_witness(
                            conn,
                            pos,
                            tid,
                        )
                    book, source_timestamp = fallback
            else:
                book = get_orderbook(tid) if callable(get_orderbook) else None
                if book is None:
                    fallback = _fresh_canonical_monitor_orderbook(conn, pos, tid)
                    if fallback is not None:
                        book, source_timestamp = fallback
                    else:
                        return _fresh_canonical_monitor_no_bid_witness(
                            conn,
                            pos,
                            tid,
                        )
            if source_timestamp is None:
                _remember_monitor_orderbook(clob, tid, book)
        if book is not None:
            from src.data.market_scanner import _top_book_level_decimal

            try:
                bid, bid_sz = _top_book_level_decimal(book, "bids")
                ask, ask_sz = _top_book_level_decimal(book, "asks")
            except Exception:
                one_sided_quote = _one_sided_monitor_quote(
                    conn,
                    clob,
                    pos,
                    tid,
                    book=book,
                    source_timestamp=source_timestamp,
                )
                if one_sided_quote is not None:
                    return one_sided_quote
                return _fresh_canonical_monitor_no_bid_witness(conn, pos, tid)
            if bid >= ask:
                return None
        else:
            bid, ask, bid_sz, ask_sz = clob.get_best_bid_ask(tid)
        bid_f = float(bid)
        ask_f = float(ask)
        bid_sz_f = float(bid_sz)
        ask_sz_f = float(ask_sz)
        mark_price = (
            bid_f
            if pos.state == "day0_window"
            else float(vwmp(bid_f, ask_f, bid_sz_f, ask_sz_f))
        )
        source_timestamp = source_timestamp or datetime.now(timezone.utc).isoformat()
        from src.data.market_scanner import _bid_ladder_from_book

        return HeldTokenMonitorQuote(
            token_id=tid,
            best_bid=bid_f,
            best_ask=ask_f,
            bid_size=bid_sz_f,
            ask_size=ask_sz_f,
            mark_price=mark_price,
            source_timestamp=source_timestamp,
            min_order_size=_book_min_order_size(book),
            min_tick=_book_min_tick(book),
            bid_ladder=(
                _bid_ladder_from_book(book) if isinstance(book, dict) else ()
            ),
            full_depth_action_authority=True,
        )
    except Exception as e:
        if book is not None:
            one_sided_quote = _one_sided_monitor_quote(
                conn,
                clob,
                pos,
                tid,
                book=book,
                source_timestamp=source_timestamp,
            )
            if one_sided_quote is not None:
                return one_sided_quote
            return _fresh_canonical_monitor_no_bid_witness(conn, pos, tid)
        logger.debug("VWMP refresh failed for %s: %s", pos.trade_id, e)
        return None


def _persist_monitor_quote(conn, pos: Position, quote: HeldTokenMonitorQuote | None) -> None:
    """Write quote evidence only after probability-side WORLD writes finish."""

    if conn is None or quote is None:
        return
    try:
        from src.state.db import log_microstructure

        ask = quote.best_ask
        spread = (
            round(float(ask - quote.best_bid), 4)
            if ask is not None and ask >= quote.best_bid
            else None
        )
        log_microstructure(
            conn,
            token_id=quote.token_id,
            city=pos.city,
            target_date=pos.target_date,
            range_label=pos.bin_label,
            price=float(quote.mark_price),
            volume=float(quote.bid_size + quote.ask_size),
            bid=float(quote.best_bid),
            ask=(float(ask) if ask is not None else None),
            spread=spread,
            source_timestamp=quote.source_timestamp,
        )
    except Exception as exc:
        logger.debug("Monitor microstructure log failed for %s: %s", pos.trade_id, exc)


from src.contracts.edge_context import EdgeContext


def _append_monitor_validation(position: Position, validation: str) -> None:
    validations = list(getattr(position, "applied_validations", []) or [])
    if validation not in validations:
        validations.append(validation)
    position.applied_validations = validations


def _clone_for_probability_refresh(position: Position) -> Position:
    """Start one probability cut without inheriting prior-cut evidence."""

    refreshed = copy.copy(position)
    try:
        delattr(refreshed, "_replacement_current_evidence_held_bounds")
    except AttributeError:
        pass
    refreshed.applied_validations = []
    return refreshed


def _bin_sort_key(outcome: dict) -> tuple[int, float]:
    low = outcome.get("range_low")
    high = outcome.get("range_high")
    if low is None and high is None:
        return (1, float("inf"))
    if low is None:
        return (0, float(high))
    return (0, float(low))


def _adjacent_sibling_outcomes(position: Position, siblings: list[dict]) -> list[dict]:
    """Return tradable weather bins adjacent to the held bin within one event."""

    if not position.market_id:
        return []
    ordered = [
        outcome for outcome in sorted(siblings, key=_bin_sort_key)
        if outcome.get("range_low") is not None or outcome.get("range_high") is not None
    ]
    held_index = next(
        (idx for idx, outcome in enumerate(ordered) if outcome.get("market_id") == position.market_id),
        None,
    )
    if held_index is None:
        return []
    adjacent: list[dict] = []
    if held_index > 0:
        adjacent.append(ordered[held_index - 1])
    if held_index + 1 < len(ordered):
        adjacent.append(ordered[held_index + 1])
    return adjacent


def _recent_price_delta(
    conn,
    *,
    token_id: str,
    current_price: float,
    now: datetime,
    lookback_hours: float = _WHALE_TOXICITY_LOOKBACK_HOURS,
) -> float | None:
    if conn is None or not token_id:
        return None
    try:
        lookback = (now - timedelta(hours=lookback_hours)).isoformat()
        row = conn.execute(
            """
            SELECT price
            FROM token_price_log
            WHERE token_id = ? AND timestamp <= ?
            ORDER BY timestamp DESC
            LIMIT 1
            """,
            (token_id, lookback),
        ).fetchone()
        if row is None:
            return None
        return float(current_price) - float(row["price"])
    except Exception as exc:
        logger.debug("Whale-toxicity price delta unavailable for token=%s: %s", token_id, exc)
        return None


def _detect_whale_toxicity_from_orderbook(
    conn,
    clob,
    position: Position,
    *,
    held_best_bid: float | None,
    held_best_ask: float | None,
    now: datetime | None = None,
) -> bool | None:
    """Detect adjacent-bin orderbook pressure for held YES positions.

    This is deliberately narrower than a true market-wide trade-sweep detector:
    Zeus currently has no market-level trade stream producer.  A signal is
    raised only when VERIFIED sibling bins plus fresh CLOB top-book facts show a
    large adjacent YES bid with enough visible depth.  Missing facts stay
    unknown (`None`) so the exit evidence does not pretend the detector ran.
    """

    if position.direction != "buy_yes":
        _append_monitor_validation(position, "whale_toxicity_not_applicable:buy_no")
        return False
    if conn is None:
        return None
    if clob is None or not position.market_id or held_best_bid is None:
        _append_monitor_validation(position, "whale_toxicity_unavailable:missing_market_facts")
        return None

    try:
        siblings = get_sibling_outcomes(position.market_id)
        if str(get_last_scan_authority()).upper() != "VERIFIED":
            _append_monitor_validation(position, "whale_toxicity_unavailable:market_scan_not_verified")
            return None
    except Exception as exc:
        logger.debug("Whale-toxicity sibling scan failed for %s: %s", position.trade_id, exc)
        _append_monitor_validation(position, "whale_toxicity_unavailable:sibling_scan_failed")
        return None

    adjacent = _adjacent_sibling_outcomes(position, siblings)
    if not adjacent:
        _append_monitor_validation(position, "whale_toxicity_unavailable:no_adjacent_bins")
        return None

    observed = False
    basis_price = float(held_best_ask if held_best_ask is not None else held_best_bid)
    effective_cost_basis = float(getattr(position, "effective_cost_basis_usd", 0.0) or 0.0)
    if getattr(position, "has_fill_economics_authority", False):
        position_notional = max(_WHALE_TOXICITY_MIN_NOTIONAL_USD, effective_cost_basis)
    else:
        position_notional = max(
            _WHALE_TOXICITY_MIN_NOTIONAL_USD,
            effective_cost_basis,
            float(getattr(position, "size_usd", 0.0) or 0.0),
        )
    now_utc = now or datetime.now(timezone.utc)

    for outcome in adjacent:
        adjacent_token = str(outcome.get("token_id") or "").strip()
        if not adjacent_token:
            continue
        try:
            adj_bid, adj_ask, adj_bid_size, _adj_ask_size = clob.get_best_bid_ask(adjacent_token)
        except Exception as exc:
            logger.debug(
                "Whale-toxicity adjacent book unavailable for trade=%s token=%s: %s",
                position.trade_id,
                adjacent_token,
                exc,
            )
            continue

        observed = True
        adjacent_notional = float(adj_bid) * float(adj_bid_size)
        current_mid = (float(adj_bid) + float(adj_ask)) / 2.0
        prior_delta = _recent_price_delta(
            conn,
            token_id=adjacent_token,
            current_price=current_mid,
            now=now_utc,
        )
        has_sufficient_depth = adjacent_notional >= position_notional
        has_recent_surge = (
            prior_delta is not None
            and prior_delta >= _WHALE_TOXICITY_PRICE_MARGIN
            and float(adj_bid) >= basis_price + _WHALE_TOXICITY_PRICE_MARGIN
        )
        has_severe_static_pressure = (
            prior_delta is None
            and float(adj_bid) >= basis_price + _WHALE_TOXICITY_SEVERE_PRICE_MARGIN
            and adjacent_notional >= position_notional * 2.0
        )
        if has_sufficient_depth and (has_recent_surge or has_severe_static_pressure):
            _append_monitor_validation(
                position,
                "whale_toxicity_available:adjacent_orderbook_pressure",
            )
            return True

    if observed:
        _append_monitor_validation(position, "whale_toxicity_available:clear")
        return False

    _append_monitor_validation(position, "whale_toxicity_unavailable:adjacent_orderbook_missing")
    return None


def _post_local_day_final_daily_verdict(
    *,
    pos: Position,
    conn,
    city,
    target_d,
    metric: str,
) -> tuple[float, Position, bool] | None:
    """Definitive post-local-day verdict from the COMPLETE-day settlement-grade
    final extreme.

    This is deliberately distinct from evaluate_hard_fact_exit's intraday
    monotonic EXCLUSION-only machinery below: hard_fact_bin_verdict can only
    ever KILL a bounded bin (a running extreme so far never proves the final
    will not move further), and its HardFactEvidence.is_complete_for() checks
    payload provenance only — it has no concept of day-coverage at all. Using
    that machinery to CONFIRM a post-day position from a merely partial
    running extreme (e.g. a METAR feed that stopped reporting mid-day) would
    silently authorize a wrong exit. _final_daily_observation_extreme already
    requires a VERIFIED settlement-product daily row with product-specific
    provenance and final-day timing. Raw hourly mirrors cannot establish the
    resolver product's final extreme. None means the final record is not
    available at this decision, not that it was never
    checked.
    """
    if conn is None:
        return None
    try:
        from src.execution.day0_hard_fact_exit import (
            _final_daily_observation_extreme,
            final_observed_bin_verdict,
            hard_fact_monitor_belief,
        )

        observation = _final_daily_observation_extreme(
            city=city,
            target_date=str(target_d),
            metric=metric,
            now=datetime.now(timezone.utc),
            conn=conn,
        )
        if observation is None:
            return None
        bin_low, bin_high = _parse_temp_range(pos.bin_label)
        verdict = final_observed_bin_verdict(
            metric=metric,
            direction=getattr(pos, "direction", ""),
            bin_low=bin_low,
            bin_high=bin_high,
            final_extreme=observation.settled_extreme,
        )
        if verdict is None:
            return None
        belief = hard_fact_monitor_belief(
            verdict=verdict, direction=getattr(pos, "direction", ""),
        )
        if belief is None:
            return None
    except Exception as exc:  # noqa: BLE001 - hard-fact overlay must fail soft
        logger.warning(
            "monitor_probability_refresh: post-local-day final-daily overlay "
            "failed for %s: %s",
            getattr(pos, "trade_id", "?"),
            exc,
        )
        return None

    hard_pos = _clone_for_probability_refresh(pos)
    setattr(hard_pos, "selected_method", SELECTED_METHOD_DAY0_ABSORBING_HARD_FACT)
    _append_monitor_validation(hard_pos, SELECTED_METHOD_DAY0_ABSORBING_HARD_FACT)
    _append_monitor_validation(
        hard_pos,
        (
            "belief_source=day0_final_daily_observation;"
            "kind=deterministic_final_daily;"
            f"metric={verdict.metric};"
            f"yes_verdict={belief.yes_verdict};"
            f"held_verdict={belief.held_verdict};"
            f"yes_prob={belief.yes_prob:.6f};"
            f"held_prob={belief.held_side_prob:.6f};"
            f"final_extreme={verdict.rounded_extreme:g};"
            f"source={observation.source};"
            f"station_id={observation.station_id};"
            f"fetched_at={observation.fetched_at.isoformat()}"
        ),
    )
    if belief.held_verdict == "STRUCTURAL_WIN":
        _append_monitor_validation(hard_pos, "day0_hard_fact_structural_win_hold")
    else:
        _append_monitor_validation(hard_pos, "day0_hard_fact_structural_loss")
    _append_monitor_validation(
        hard_pos,
        "model_divergence_panic_inapplicable:day0_absorbing_hard_fact",
    )
    _append_monitor_validation(
        hard_pos,
        "forecast_posteriors_dominated_by_day0_hard_fact",
    )
    setattr(
        hard_pos,
        _MONITOR_PROBABILITY_RECEIPT_ATTR,
        _compact_monitor_probability_receipt(
            {
                "schema_version": 1,
                "selected_method": SELECTED_METHOD_DAY0_ABSORBING_HARD_FACT,
                "probability_authority": "day0_final_daily_observation",
                "probability_functional": "DETERMINISTIC_ABSORBING_FACT",
                "held_side_probability": float(belief.held_side_prob),
                "final_daily_observation": {
                    "source": observation.source,
                    "station_id": observation.station_id,
                    "unit": observation.unit,
                    "raw_extreme": observation.raw_extreme,
                    "settled_extreme": observation.settled_extreme,
                    "fetched_at": observation.fetched_at.isoformat(),
                },
            }
        ),
    )
    _set_monitor_probability_fresh(hard_pos, True)
    _set_day0_zero_probability_exit_authority(hard_pos, True)
    return float(belief.held_side_prob), hard_pos, True


# Only NOAA WRH resolver-page evidence and HKO final daily evidence enter this
# exact lane. Other settlement families retain their statistical redecision.
_POST_LOCAL_DAY_HARD_FACT_ELIGIBLE_SOURCE_TYPES = frozenset({"noaa", "hko"})


def _post_local_day_hard_fact_lane_applies(city) -> bool:
    source_type = str(getattr(city, "settlement_source_type", "") or "").strip().lower()
    return source_type in _POST_LOCAL_DAY_HARD_FACT_ELIGIBLE_SOURCE_TYPES


def _day0_absorbing_hard_fact_overlay(
    *,
    pos: Position,
    conn,
    city,
    target_d,
) -> tuple[float, Position, bool] | None:
    """Return exact monitor belief when a qualified Day0 hard fact is absorbing."""

    # Once the position's contract-local target day has ended, the
    # day0_observation_remaining_window bootstrap this overlay would otherwise
    # defer to has zero remaining hours to sample: it degenerates to a
    # maximally-wide but still-finite belief band (e.g. [0,1]) that passes the
    # held-side evidence_ok check while carrying no real information, and the
    # exit organ never learns that its belief is meaningless. This overlay is
    # the durable-evidence lane that can name that gap; extend its window to
    # cover "day already ended" so a past-local-day position first tries the
    # COMPLETE-day settlement-grade final extreme
    # (_post_local_day_final_daily_verdict — can CONFIRM either outcome, not
    # only exclude), then the same intraday monotonic-exclusion evidence this
    # overlay already used for same-day (evaluate_hard_fact_exit — can still
    # validly KILL a bin from partial evidence). Only when NEITHER produces
    # anything AND the position's settlement family is actually on this lane
    # (_post_local_day_hard_fact_lane_applies) does it decline by the named
    # POST_LOCAL_DAY_FINAL_OBSERVATION_UNAVAILABLE reason instead of silently
    # falling through into the degenerate remaining-window recompute — a
    # family never on this lane (WU-settled) must fall through unchanged,
    # never decline here.
    is_after_target_day = _is_position_after_target_local_day(pos, city, target_d)
    if not (_is_position_target_local_day(pos, city, target_d) or is_after_target_day):
        return None

    def _post_local_day_final_observation_unavailable() -> (
        tuple[float, Position, bool] | None
    ):
        if not is_after_target_day or not _post_local_day_hard_fact_lane_applies(city):
            return None
        stale = _clone_for_probability_refresh(pos)
        setattr(stale, "selected_method", SELECTED_METHOD_DAY0_ABSORBING_HARD_FACT)
        _append_monitor_validation(stale, "POST_LOCAL_DAY_FINAL_OBSERVATION_UNAVAILABLE")
        _set_monitor_probability_fresh(stale, False)
        _set_day0_zero_probability_exit_authority(stale, False)
        return float(getattr(pos, "p_posterior", 0.0) or 0.0), stale, False

    metric = str(getattr(pos, "temperature_metric", "") or "").strip().lower()
    if metric not in {"high", "low"}:
        return None

    if is_after_target_day:
        final_daily = _post_local_day_final_daily_verdict(
            pos=pos, conn=conn, city=city, target_d=target_d, metric=metric,
        )
        if final_daily is not None:
            return final_daily

    try:
        from src.execution.day0_hard_fact_exit import (
            evaluate_hard_fact_exit,
            hard_fact_monitor_belief,
        )

        verdict = evaluate_hard_fact_exit(
            position=pos,
            city=city,
            now=datetime.now(timezone.utc),
            world_conn=conn,
            # The held-monitor claim is deadline-bound. Direct WU fetching is
            # producer work and can retain the entire position book past that
            # claim; consume only timestamped canonical evidence here.
            durable_only=True,
        )
        if verdict is None:
            # A missing exact verdict can defer only to the qualified global
            # held-monitor reader. Positions without that canonical route must
            # retain the existing post-local-day stale result.
            if (
                is_after_target_day
                and _post_local_day_hard_fact_lane_applies(city)
                and _would_use_day0_monitor_lane(pos, city, target_d)
                and _canonical_condition_id(pos) is not None
            ):
                return None
            return _post_local_day_final_observation_unavailable()
        evidence = getattr(verdict, "evidence", None)
        if evidence is None or not evidence.is_complete_for(city):
            stale = _clone_for_probability_refresh(pos)
            setattr(stale, "selected_method", SELECTED_METHOD_DAY0_ABSORBING_HARD_FACT)
            _append_monitor_validation(
                stale,
                "day0_absorbing_hard_fact_evidence_incomplete:read_only",
            )
            _set_monitor_probability_fresh(stale, False)
            _set_day0_zero_probability_exit_authority(stale, False)
            return float(getattr(pos, "p_posterior", 0.0) or 0.0), stale, False
        belief = hard_fact_monitor_belief(
            verdict=verdict,
            direction=getattr(pos, "direction", ""),
        )
        if belief is None:
            return _post_local_day_final_observation_unavailable()
    except Exception as exc:  # noqa: BLE001 - hard-fact overlay must fail soft
        logger.warning(
            "monitor_probability_refresh: day0 hard-fact overlay failed for %s: %s",
            getattr(pos, "trade_id", "?"),
            exc,
        )
        return _post_local_day_final_observation_unavailable()

    hard_pos = _clone_for_probability_refresh(pos)
    setattr(hard_pos, "selected_method", SELECTED_METHOD_DAY0_ABSORBING_HARD_FACT)
    _append_monitor_validation(hard_pos, SELECTED_METHOD_DAY0_ABSORBING_HARD_FACT)
    _append_monitor_validation(
        hard_pos,
        (
            "belief_source=day0_absorbing_hard_fact;"
            "kind=deterministic_absorbing;"
            f"metric={verdict.metric};"
            f"yes_verdict={belief.yes_verdict};"
            f"held_verdict={belief.held_verdict};"
            f"yes_prob={belief.yes_prob:.6f};"
            f"held_prob={belief.held_side_prob:.6f};"
            f"effective_extreme={verdict.rounded_extreme:g};"
            f"source={verdict.source or 'unknown'};"
            f"station_id={evidence.station_id};"
            f"observed_at={evidence.observed_at};"
            f"payload_identity={evidence.payload_identity}"
        ),
    )
    if belief.held_verdict == "STRUCTURAL_WIN":
        _append_monitor_validation(hard_pos, "day0_hard_fact_structural_win_hold")
    else:
        _append_monitor_validation(hard_pos, "day0_hard_fact_structural_loss")
    _append_monitor_validation(
        hard_pos,
        "model_divergence_panic_inapplicable:day0_absorbing_hard_fact",
    )
    _append_monitor_validation(
        hard_pos,
        "forecast_posteriors_dominated_by_day0_hard_fact",
    )
    setattr(
        hard_pos,
        _MONITOR_PROBABILITY_RECEIPT_ATTR,
        _compact_monitor_probability_receipt(
            {
                "schema_version": 1,
                "selected_method": SELECTED_METHOD_DAY0_ABSORBING_HARD_FACT,
                "probability_authority": "day0_absorbing_hard_fact",
                "probability_functional": "DETERMINISTIC_ABSORBING_FACT",
                "held_side_probability": float(belief.held_side_prob),
                "hard_fact_evidence": evidence.as_dict(),
            }
        ),
    )
    _set_monitor_probability_fresh(hard_pos, True)
    _set_day0_zero_probability_exit_authority(hard_pos, True)
    return float(belief.held_side_prob), hard_pos, True


def _would_use_day0_monitor_lane(pos: Position, city, target_d) -> bool:
    """Whether held probability must use current target-day observation truth."""

    return (
        pos.entry_method == EntryMethod.DAY0_OBSERVATION.value
        or (
            _position_state_value(pos) == "day0_window"
            and _city_supports_executable_day0_observation(city)
        )
        or (
            _is_position_target_local_day(pos, city, target_d)
            and _city_supports_executable_day0_observation(city)
        )
        or (
            _is_position_after_target_local_day(pos, city, target_d)
            and _city_supports_executable_day0_observation(city)
        )
    )


def _within_day0_observation_start_grace(
    city,
    target_d,
    *,
    now: datetime | None = None,
) -> bool:
    """Whether the target local day began inside the observation coverage grace."""

    if target_d is None:
        return False
    try:
        target_date = (
            target_d if isinstance(target_d, date) else date.fromisoformat(str(target_d))
        )
        tz = ZoneInfo(str(getattr(city, "timezone", "") or ""))
        local_now = (now or datetime.now(timezone.utc)).astimezone(tz)
    except (TypeError, ValueError, ZoneInfoNotFoundError):
        return False
    if local_now.date() != target_date:
        return False
    local_midnight = local_now.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
        fold=0,
    )
    elapsed = local_now - local_midnight
    return timedelta(0) <= elapsed <= timedelta(
        hours=_DAY0_COVERAGE_WINDOW_GRACE_HOURS
    )


def _canonical_condition_id(position: Position) -> str | None:
    """Return a real 32-byte Polymarket condition id, never a local surrogate."""

    condition_id = str(getattr(position, "condition_id", "") or "").strip()
    if len(condition_id) != 66 or not condition_id.startswith("0x"):
        return None
    try:
        int(condition_id[2:], 16)
    except ValueError:
        return None
    return condition_id


def _current_global_held_samples(
    position: Position,
    witness: object,
    *,
    current_token_pair: tuple[str, str],
) -> np.ndarray:
    """Select the held token from a joint witness with exact side identity."""

    condition_id = _canonical_condition_id(position)
    if condition_id is None:
        raise ValueError("monitor canonical condition identity is missing")
    bindings = tuple(getattr(witness, "bindings", ()) or ())
    indexes = [
        index
        for index, binding in enumerate(bindings)
        if str(getattr(binding, "condition_id", "") or "") == condition_id
    ]
    if len(indexes) != 1:
        raise ValueError("monitor condition is not unique in current global witness")
    binding = bindings[indexes[0]]
    current_yes, current_no = (str(token or "").strip() for token in current_token_pair)
    position_yes = str(getattr(position, "token_id", "") or "").strip()
    position_no = str(getattr(position, "no_token_id", "") or "").strip()
    binding_yes = str(getattr(binding, "yes_token_id", "") or "").strip()
    binding_no = str(getattr(binding, "no_token_id", "") or "").strip()
    if (
        not current_yes
        or not current_no
        or binding_yes != current_yes
        or binding_no != current_no
    ):
        raise ValueError("monitor position token pair does not match current global witness")
    direction = _normalize_monitor_direction(position.direction)
    expected_token = current_no if direction == "buy_no" else current_yes
    held_token = position_no if direction == "buy_no" else position_yes
    if held_token != expected_token:
        raise ValueError("monitor held token does not match current global witness side")
    complementary_token = position_yes if direction == "buy_no" else position_no
    expected_complement = current_yes if direction == "buy_no" else current_no
    if complementary_token and complementary_token != expected_complement:
        raise ValueError("monitor complementary token conflicts with current global witness")

    from src.solve.solver import (
        DeterministicBinPayoffWitness,
        family_payoff_q_samples,
    )

    if isinstance(witness, DeterministicBinPayoffWitness):
        samples = family_payoff_q_samples(
            witness,
            bin_id=str(binding.bin_id),
            side="NO" if direction == "buy_no" else "YES",
        )
        if samples is None:
            raise ValueError("monitor held bin is unknown in deterministic witness")
    else:
        samples = np.asarray(witness.yes_q_samples[:, indexes[0]], dtype=float)
        if direction == "buy_no":
            samples = 1.0 - samples
    if (
        samples.ndim != 1
        or samples.size < 2
        or not np.isfinite(samples).all()
        or (samples < 0.0).any()
        or (samples > 1.0).any()
    ):
        raise ValueError("monitor current-global held samples are invalid")
    return np.ascontiguousarray(samples)


def _current_global_held_point_probability(
    position: Position,
    witness: object,
) -> float:
    """Select the held side's predictive point q from a joint witness."""

    condition_id = _canonical_condition_id(position)
    if condition_id is None:
        raise ValueError("monitor canonical condition identity is missing")
    bindings = tuple(getattr(witness, "bindings", ()) or ())
    indexes = [
        index
        for index, binding in enumerate(bindings)
        if str(getattr(binding, "condition_id", "") or "") == condition_id
    ]
    if len(indexes) != 1:
        raise ValueError("monitor condition is not unique in current global witness")

    from src.solve.solver import (
        DeterministicBinPayoffWitness,
        family_payoff_q_samples,
    )

    direction = _normalize_monitor_direction(position.direction)
    if isinstance(witness, DeterministicBinPayoffWitness):
        samples = family_payoff_q_samples(
            witness,
            bin_id=str(bindings[indexes[0]].bin_id),
            side="NO" if direction == "buy_no" else "YES",
        )
        if samples is None:
            raise ValueError("monitor held bin is unknown in deterministic witness")
        probability = float(samples.mean())
    else:
        point_q = np.asarray(getattr(witness, "yes_point_q", ()), dtype=float)
        if point_q.shape != (len(bindings),):
            raise ValueError("monitor current-global point q is invalid")
        probability = float(point_q[indexes[0]])
        if direction == "buy_no":
            probability = 1.0 - probability
    if not np.isfinite(probability) or not 0.0 <= probability <= 1.0:
        raise ValueError("monitor current-global held point q is invalid")
    return probability


def _day0_family_snapshot_token_map(
    snapshot: _CurrentGlobalDay0FamilySnapshot,
) -> dict[str, tuple[str, str]]:
    return {
        condition_id: (yes_token_id, no_token_id)
        for condition_id, yes_token_id, no_token_id in snapshot.token_pairs
    }


def _day0_family_snapshot_covers_condition(
    snapshot: _CurrentGlobalDay0FamilySnapshot,
    condition_id: str,
) -> bool:
    bindings = tuple(getattr(snapshot.witness, "bindings", ()) or ())
    matched = tuple(
        binding
        for binding in bindings
        if str(getattr(binding, "condition_id", "") or "") == condition_id
    )
    if len(matched) != 1:
        return False

    from src.solve.solver import DeterministicBinPayoffWitness

    if not isinstance(snapshot.witness, DeterministicBinPayoffWitness):
        return condition_id not in snapshot.deterministic_condition_ids
    exact_bin_ids = {
        str(bin_id)
        for bin_id, _payoff in snapshot.witness.exact_yes_payoffs
    }
    return str(matched[0].bin_id) in exact_bin_ids


def _target_day_has_canonical_observation(
    conn,
    position: Position,
    *,
    decision_time: datetime | None = None,
) -> bool:
    """Read the same causal observation authority used to build current q.

    ``DAY0_EXTREME_UPDATED`` is a committed observation carrier even before
    the optional ``observation_instants`` projection contains a row.  Counting
    only that projection mislabeled live event evidence as an unobserved prefix,
    so the pinned full-day path skipped current-observation conditioning and
    the held monitor rejected its own q as provenance-incomplete.
    """

    from src.data.replacement_forecast_current_target_plan import (
        _latest_authorized_day0_fact,
    )

    now = decision_time or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("monitor observation decision_time must be timezone-aware")
    metric = resolve_position_metric(position)[0]
    return bool(
        _latest_authorized_day0_fact(
            conn,
            city=str(position.city),
            target_date=str(position.target_date),
            temperature_metric=metric,
            decision_time=now.astimezone(timezone.utc),
            require_settlement_channel=False,
        )
        is not None
    )


def _target_day_has_canonical_observation_now(position: Position) -> bool:
    """Revalidate a cached zero-observation proof against canonical truth."""

    from src.state.db import get_world_connection_read_only

    world = get_world_connection_read_only()
    try:
        return _target_day_has_canonical_observation(world, position)
    finally:
        world.close()


def _materialize_current_global_day0_probability(
    position: Position,
    snapshot: _CurrentGlobalDay0FamilySnapshot,
) -> tuple[float, Position, bool]:
    condition_id = _canonical_condition_id(position)
    if condition_id is None:
        raise ValueError("monitor canonical condition identity is missing")
    token_map = _day0_family_snapshot_token_map(snapshot)
    if condition_id not in token_map:
        raise ValueError("monitor current family token identity is incomplete")
    witness = snapshot.witness
    held_samples = _current_global_held_samples(
        position,
        witness,
        current_token_pair=token_map[condition_id],
    )
    direction = _normalize_monitor_direction(position.direction)
    held_probability = _current_global_held_point_probability(position, witness)

    from src.engine.event_reactor_adapter import (
        _GLOBAL_FINAL_DAILY_EXACT_SETTLEMENT_SIMPLEX_BAND_BASIS,
    )

    refreshed = _clone_for_probability_refresh(position)
    refreshed.token_id = token_map[condition_id][0]
    refreshed.no_token_id = token_map[condition_id][1]
    is_final_daily = (
        witness.band_basis
        == _GLOBAL_FINAL_DAILY_EXACT_SETTLEMENT_SIMPLEX_BAND_BASIS
    )
    is_unobserved_prefix_replacement = (
        snapshot.probability_authority
        == "replacement_unobserved_day0_prefix_global_probability_v1"
    )
    is_provisional_observation_replacement = (
        snapshot.probability_authority
        == "replacement_provisional_day0_global_probability_v1"
    )
    is_conditioned_replacement = (
        snapshot.probability_authority
        == "day0_conditioned_replacement_global_probability_v1"
    )
    is_remaining_day = (
        snapshot.probability_authority
        == "day0_remaining_day_global_probability_v1"
    )
    is_held_pinned_recompute = (
        snapshot.probability_authority
        == "day0_held_same_cycle_day0_recompute_v1"
    )
    is_deterministic_bin_payoff = (
        snapshot.probability_authority == "day0_deterministic_bin_payoff_v1"
    )
    observation = snapshot.day0_payload.get("_edli_global_day0_binding")
    causal_bundle_validation = snapshot.day0_payload.get(
        "_edli_day0_causal_evidence_bundle_validation"
    )
    if is_remaining_day or is_held_pinned_recompute:
        bundle = (
            observation.get("day0_causal_evidence_bundle")
            if isinstance(observation, Mapping)
            else None
        )
        if not isinstance(bundle, Mapping):
            bundle = snapshot.day0_payload.get(
                "_edli_day0_causal_evidence_bundle"
            )
        remaining_witness = (
            observation.get("day0_remaining_vector_witness")
            if isinstance(observation, Mapping)
            else None
        )
        if not isinstance(remaining_witness, Mapping):
            remaining_witness = snapshot.day0_payload.get(
                "_edli_day0_remaining_vector_witness"
            )
        identity_pairs = (
            ("bundle_identity", "actual_bundle_identity"),
            ("carrier_vector_identity", "actual_carrier_vector_identity"),
            ("carrier_vector_hash", "actual_carrier_vector_hash"),
        )
        provenance_complete = bool(
            isinstance(observation, Mapping)
            and str(
                observation.get("posterior_id")
                or observation.get("probability_base_identity")
                or ""
            ).strip()
            and isinstance(bundle, Mapping)
            and isinstance(remaining_witness, Mapping)
            and remaining_witness == bundle.get("carrier_vector_witness")
            and isinstance(causal_bundle_validation, Mapping)
            and causal_bundle_validation.get("reason") is None
            and all(
                str(bundle.get(bundle_key) or "").strip()
                and bundle.get(bundle_key)
                == causal_bundle_validation.get(validation_key)
                == causal_bundle_validation.get(
                    validation_key.replace("actual_", "expected_")
                )
                for bundle_key, validation_key in identity_pairs
            )
        )
        if not provenance_complete:
            # SCOPE: this held city/date/metric snapshot only. DRAIN: the next
            # normal monitor build reads a complete current posterior + causal
            # vector bundle. RESET: that exact bundle validates and materializes
            # normally; other families and deterministic hard facts continue.
            raise ValueError("GLOBAL_DAY0_STATISTICAL_PROVENANCE_INCOMPLETE")
    if is_final_daily:
        selected_method = SELECTED_METHOD_FINAL_DAILY_OBSERVATION_EXACT
        probability_authority = (
            "final_daily_observation_exact_global_probability_v1"
        )
    elif (
        is_unobserved_prefix_replacement
        or is_provisional_observation_replacement
        or is_conditioned_replacement
    ):
        selected_method = "replacement_posterior"
        probability_authority = snapshot.probability_authority
    elif is_remaining_day or is_held_pinned_recompute:
        selected_method = SELECTED_METHOD_DAY0_OBSERVATION_REMAINING_WINDOW
        probability_authority = snapshot.probability_authority
    elif is_deterministic_bin_payoff:
        selected_method = SELECTED_METHOD_DAY0_ABSORBING_HARD_FACT
        probability_authority = "day0_deterministic_bin_payoff_v1"
    else:
        raise ValueError(
            "monitor current global Day0 probability authority is unsupported: "
            f"{snapshot.probability_authority or 'missing'}"
        )
    refreshed.selected_method = selected_method
    _append_monitor_validation(
        refreshed,
        f"probability_authority={probability_authority}",
    )
    _append_monitor_validation(
        refreshed,
        f"probability_witness_identity:{witness.witness_identity}",
    )
    if is_final_daily:
        _stamp_day0_monitor_belief(
            refreshed,
            selected_method=selected_method,
            kind="exact_final_daily_observation",
            metric=snapshot.metric,
        )
    elif is_unobserved_prefix_replacement:
        _append_monitor_validation(
            refreshed,
            "day0_unobserved_prefix_zero_observation_proven:"
            "replacement_global_probability_authority",
        )
    elif is_provisional_observation_replacement:
        _append_monitor_validation(
            refreshed,
            "day0_provisional_observation_probability_only:"
            "replacement_global_probability_authority",
        )
    elif is_conditioned_replacement:
        _stamp_day0_monitor_belief(
            refreshed,
            selected_method=selected_method,
            kind="probabilistic_day0_conditioned_replacement",
            metric=snapshot.metric,
        )
    elif is_deterministic_bin_payoff:
        _stamp_day0_monitor_belief(
            refreshed,
            selected_method=selected_method,
            kind="deterministic_bin_payoff",
            metric=snapshot.metric,
        )
    elif is_held_pinned_recompute:
        _stamp_day0_remaining_window_belief(refreshed, metric=snapshot.metric)
        _append_monitor_validation(
            refreshed,
            "held_same_cycle_day0_recompute:immutable_complete_carrier",
        )
    else:
        _stamp_day0_remaining_window_belief(refreshed, metric=snapshot.metric)
    if (
        not is_final_daily
        and not is_unobserved_prefix_replacement
        and not is_deterministic_bin_payoff
    ):
        maturity_status = str(
            snapshot.day0_payload.get("_edli_day0_exit_authority_status")
            or "unavailable"
        ).strip().lower()
        maturity_reason = str(
            snapshot.day0_payload.get("_edli_day0_exit_authority_reason") or ""
        ).strip()
        setattr(refreshed, "_day0_exit_authority_status", maturity_status)
        setattr(
            refreshed,
            "_day0_exit_authority_reason",
            maturity_reason or "day0_extreme_maturity_unavailable:missing",
        )
        if maturity_reason:
            _append_monitor_validation(refreshed, maturity_reason)
    _set_monitor_probability_fresh(refreshed, True)
    _set_day0_zero_probability_exit_authority(
        refreshed,
        is_deterministic_bin_payoff,
    )
    setattr(refreshed, _GLOBAL_MONITOR_SAMPLES_ATTR, held_samples)
    setattr(refreshed, _GLOBAL_MONITOR_ALPHA_ATTR, float(witness.band_alpha))

    setattr(
        refreshed,
        "_day0_monitor_probability_receipt",
        {
            "schema_version": 1,
            "selected_method": selected_method,
            "probability_authority": probability_authority,
            "metric": snapshot.metric,
            "held_direction": direction,
            "held_side_probability": held_probability,
            "probability_witness_identity": witness.witness_identity,
            "probability_content_identity": str(
                getattr(witness, "probability_content_identity", "") or ""
            ),
            "q_version": witness.q_version,
            "source_truth_identity": witness.source_truth_identity,
            "held_pinned_recompute": bool(
                snapshot.day0_payload.get("_edli_day0_held_pinned_recompute")
            ),
            "pinned_complete_posterior_id": snapshot.day0_payload.get(
                "_edli_day0_held_pinned_posterior_id"
            ),
            "pinned_complete_posterior_identity": snapshot.day0_payload.get(
                "_edli_day0_held_pinned_posterior_identity"
            ),
            "pinned_observation_overlay": snapshot.day0_payload.get(
                "_edli_day0_held_pinned_overlay"
            ),
            "band": {
                "basis": witness.band_basis,
                "alpha": float(witness.band_alpha),
                "sample_count": int(held_samples.size),
                "held_side_summary": _monitor_receipt_quantiles(held_samples),
            },
            "observation": dict(observation) if isinstance(observation, dict) else {},
            "causal_evidence_bundle_validation": (
                dict(causal_bundle_validation)
                if isinstance(causal_bundle_validation, Mapping)
                else None
            ),
            "remaining_window": {
                "source": "current_global_probability_builder",
                "finite_evidence_member_count": snapshot.day0_payload.get(
                    "_edli_day0_finite_evidence_member_count"
                ),
                "finite_evidence_hits_by_condition": snapshot.day0_payload.get(
                    "_edli_day0_finite_evidence_hits_by_condition"
                ),
            }
            if is_remaining_day or is_held_pinned_recompute
            else None,
        },
    )
    return held_probability, refreshed, True


def _build_current_global_day0_family_snapshot(
    position: Position,
    *,
    trade_conn,
    decision_time: datetime | None,
    cached_snapshots: tuple[_CurrentGlobalDay0FamilySnapshot, ...]
    | list[_CurrentGlobalDay0FamilySnapshot],
    deadline_monotonic: float | None = None,
    hwm_deadline_monotonic: float | None = None,
) -> _CurrentGlobalDay0FamilySnapshot:
    condition_id = _canonical_condition_id(position)
    if condition_id is None:
        raise ValueError("monitor canonical condition identity is missing")
    metric = resolve_position_metric(position)[0]
    now = decision_time or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("monitor current-global decision_time must be timezone-aware")
    now = now.astimezone(timezone.utc)

    from src.contracts.executable_market_snapshot import FRESHNESS_WINDOW_DEFAULT
    from src.events.opportunity_event import OpportunityEvent
    from src.state.db import (
        get_forecasts_connection_read_only,
        get_forecasts_connection_with_world_read_only,
    )
    from src.engine.global_auction_universe import WorkContext
    from src.data.replacement_current_value_serving import physical_read_pass

    hwm_forecasts = None
    try:
        prepare_deadline = _held_monitor_stage_deadline(
            deadline_monotonic,
            HELD_MONITOR_PROBABILITY_PREPARE_MAX_SECONDS,
        )
        prepare_context = WorkContext(
            deadline_monotonic=prepare_deadline,
            monotonic=time.monotonic,
        )
        hwm_deadline: list[float | None] = [None]
        hwm_handoff_started = [False]
        with ExitStack() as prepare_sqlite:
            prepare_sqlite.enter_context(physical_read_pass(include_model_surface=False))
            try:
                forecasts_seed = prepare_sqlite.enter_context(
                    get_forecasts_connection_with_world_read_only(
                        deadline_monotonic=prepare_deadline,
                    )
                )
            except sqlite3.OperationalError:
                _raise_if_day0_snapshot_read_deadline_elapsed(prepare_deadline)
                raise
            forecasts = prepare_sqlite.enter_context(
                _day0_snapshot_sqlite_read_deadline(
                    forecasts_seed,
                    prepare_deadline,
                )
            )
            # The attached world schema is part of this read's source identity.
            # Reopening forecasts alone would discard it before the NOAA
            # likelihood carrier reads the raw observation ledger.
            world = forecasts
            prepare_context.checkpoint("held_monitor_probability_prepare:connections")

            def _begin_raw_hwm_read() -> float:
                prepare_context.checkpoint(
                    "held_monitor_probability_prepare:hwm_handoff"
                )
                _raise_if_day0_snapshot_read_deadline_elapsed(hwm_deadline[0])
                if not hwm_handoff_started[0]:
                    # Keep the live prepare connections usable by the reader and
                    # adapter after the HWM handoff.  HWM authority has its own
                    # deadline-bound connection; closing the ExitStack here
                    # invalidates ``forecasts``/``world`` before same-cut replay.
                    hwm_busy_ms = max(
                        0,
                        int((hwm_deadline[0] - time.monotonic()) * 1000.0),
                    )
                    hwm_forecasts.execute(
                        f"PRAGMA busy_timeout = {min(1_000, hwm_busy_ms)}"
                    )
                    hwm_handoff_started[0] = True
                return float(hwm_deadline[0])

            attached = {
                str(database[1])
                for database in world.execute("PRAGMA database_list").fetchall()
            }
            opportunity_events_table = (
                "world.opportunity_events"
                if "world" in attached
                else "opportunity_events"
            )
            row = world.execute(
                f"""
            SELECT event_id, event_type, entity_key, source, observed_at,
                   available_at, received_at, causal_snapshot_id, payload_hash,
                   idempotency_key, priority, expires_at, payload_json,
                   schema_version, created_at
              FROM {opportunity_events_table}
                   INDEXED BY idx_opportunity_events_day0_family_extreme
             WHERE event_type = 'DAY0_EXTREME_UPDATED'
               AND json_extract(payload_json, '$.city') = ?
               AND json_extract(payload_json, '$.target_date') = ?
               AND json_extract(payload_json, '$.metric') = ?
               AND available_at <= ?
               AND received_at <= ?
               AND created_at <= ?
             ORDER BY available_at DESC, received_at DESC, event_id DESC
             LIMIT 1
            """,
                (
                    str(position.city),
                    str(position.target_date),
                    metric,
                    now.isoformat(),
                    now.isoformat(),
                    now.isoformat(),
                ),
            ).fetchone()
            _raise_if_day0_snapshot_read_deadline_elapsed(deadline_monotonic)
            if row is None:
                if not _target_day_has_canonical_observation(
                    world,
                    position,
                    decision_time=now,
                ):
                    raise _Day0UnobservedPrefixUnavailable(
                        "current global Day0 family event unavailable: "
                        "zero target-date canonical observations"
                    )
                raise ObservationUnavailableError(
                    _DAY0_CANONICAL_OBSERVATION_EVENT_NOT_VISIBLE
                )
            event = OpportunityEvent(**dict(row))
            from src.engine.event_reactor_adapter import (
                _CurrentProbabilityUse,
                _prepare_current_global_probability_family,
            )
            from src.data.replacement_forecast_bundle_reader import (
                read_prior_complete_replacement_forecast_bundle,
            )

            day0_payload: dict[str, object] = {}
            cache_metadata: dict[str, str] = {}
            city = cities_by_name.get(str(position.city))
            unobserved_prefix = bool(
                city is not None
                and not _target_day_has_canonical_observation(
                    world,
                    position,
                    decision_time=now,
                )
            )
            pinned_result = read_prior_complete_replacement_forecast_bundle(
                forecasts,
                city=str(position.city),
                target_date=str(position.target_date),
                temperature_metric=metric,
                decision_time=now,
            )
            if (
                pinned_result.status == "BLOCKED"
                and pinned_result.reason_code
                != "REPLACEMENT_RAW_INPUT_HWM_REQUIRED_RETRYABLE"
                and not _pinned_carrier_block_defers_to_current_day0_event(
                    pinned_result.reason_code
                )
            ):
                raise ValueError(
                    "GLOBAL_HELD_PINNED_COMPLETE_POSTERIOR_BLOCKED:"
                    f"{pinned_result.reason_code}"
                )
            if not pinned_result.ok:
                hwm_deadline[0] = _held_monitor_stage_deadline(
                    hwm_deadline_monotonic,
                    HELD_MONITOR_RAW_HWM_READ_MAX_SECONDS,
                )
                hwm_forecasts = get_forecasts_connection_read_only(
                    deadline_monotonic=float(hwm_deadline[0]),
                )
                _begin_raw_hwm_read()
                pinned_result = read_prior_complete_replacement_forecast_bundle(
                    forecasts,
                    city=str(position.city),
                    target_date=str(position.target_date),
                    temperature_metric=metric,
                    decision_time=now,
                    raw_input_hwm_conn=hwm_forecasts,
                    raw_input_hwm_deadline_monotonic=float(hwm_deadline[0]),
                    raw_input_hwm_read_max_seconds=HELD_MONITOR_RAW_HWM_READ_MAX_SECONDS,
                )
                if (
                    pinned_result.status == "BLOCKED"
                    and not _pinned_carrier_block_defers_to_current_day0_event(
                        pinned_result.reason_code
                    )
                ):
                    raise ValueError(
                        "GLOBAL_HELD_PINNED_COMPLETE_POSTERIOR_BLOCKED:"
                        f"{pinned_result.reason_code}"
                    )
            pinned_complete_bundle = (
                pinned_result.bundle if pinned_result.ok else None
            )
            if pinned_complete_bundle is not None and not (
                _pinned_complete_bundle_matches_current_day0_event(
                    pinned_complete_bundle,
                    event,
                    metric=metric,
                    settlement_unit=str(
                        getattr(city, "settlement_unit", "") or ""
                    ),
                )
            ):
                # The latest authorized Day0 event is the observation authority.
                # SCOPE: this held city/date/metric family. DRAIN: a successor
                # posterior materialized from this exact fact. RESET: the next
                # read can pin that matching carrier.  Never pass an older q as
                # a pinned source identity while the successor is absent.
                pinned_complete_bundle = None
            if (
                pinned_complete_bundle is not None
                and not _pinned_complete_bundle_has_valid_causal_evidence(
                    pinned_complete_bundle
                )
            ):
                # SCOPE: this held city/date/metric pin only. DRAIN: the
                # current-bundle path below rebuilds q from a complete causal
                # vector certificate. RESET: a later immutable posterior that
                # carries the certificate is eligible for pinning again.
                pinned_complete_bundle = None
            if pinned_complete_bundle is None:
                if hwm_forecasts is None:
                    hwm_deadline[0] = _held_monitor_stage_deadline(
                        hwm_deadline_monotonic,
                        HELD_MONITOR_RAW_HWM_READ_MAX_SECONDS,
                    )
                    hwm_forecasts = get_forecasts_connection_read_only(
                        deadline_monotonic=float(hwm_deadline[0]),
                    )
                    _begin_raw_hwm_read()
            try:
                prepared = _prepare_current_global_probability_family(
                    event,
                    forecast_conn=forecasts,
                    topology_conn=forecasts,
                    observation_conn=world,
                    decision_time=now,
                    max_age=FRESHNESS_WINDOW_DEFAULT,
                    day0_payload_out=day0_payload,
                    cache_metadata_out=cache_metadata,
                    required_condition_id=condition_id,
                    allow_unobserved_day0_replacement=unobserved_prefix,
                    allow_provisional_day0_replacement=True,
                    probability_use=_CurrentProbabilityUse.HELD_MONITOR,
                    raw_input_hwm_conn=hwm_forecasts,
                    before_raw_input_hwm_read=(
                        _begin_raw_hwm_read
                        if pinned_complete_bundle is None
                        else None
                    ),
                    raw_input_hwm_read_max_seconds=(
                        HELD_MONITOR_RAW_HWM_READ_MAX_SECONDS
                        if pinned_complete_bundle is None
                        else None
                    ),
                    pinned_complete_bundle=pinned_complete_bundle,
                )
            except ValueError as exc:
                if (
                    str(exc) == "GLOBAL_DAY0_CURRENT_OBSERVATION_MISSING"
                    and not _target_day_has_canonical_observation(
                        world,
                        position,
                        decision_time=now,
                    )
                ):
                    raise _Day0UnobservedPrefixUnavailable(
                        "current global Day0 probability unavailable: "
                        "zero target-date canonical observations"
                    ) from exc
                raise
            _raise_if_day0_snapshot_read_deadline_elapsed(deadline_monotonic)
    finally:
        if hwm_forecasts is not None:
            hwm_forecasts.close()

    witness = prepared.probability_witness
    condition_ids = tuple(binding.condition_id for binding in witness.bindings)
    if cached_snapshots:
        token_map = _day0_family_snapshot_token_map(cached_snapshots[0])
        if set(token_map) != set(condition_ids):
            raise ValueError("monitor current family topology changed within cycle")
    else:
        token_rows = _read_current_global_day0_snapshot_tokens(
            trade_conn=trade_conn,
            condition_ids=condition_ids,
            deadline_monotonic=deadline_monotonic,
        )
        token_map = {}
        for token_row in token_rows:
            try:
                row_condition = token_row["condition_id"]
                pair = (token_row["yes_token_id"], token_row["no_token_id"])
            except (TypeError, KeyError, IndexError):
                row_condition = token_row[0]
                pair = (token_row[1], token_row[2])
            key = str(row_condition or "").strip()
            normalized = tuple(str(token or "").strip() for token in pair)
            if not key or not all(normalized):
                continue
            existing = token_map.get(key)
            if existing is not None and existing != normalized:
                raise ValueError("monitor current family token identity is ambiguous")
            token_map[key] = normalized
    if set(token_map) != set(condition_ids):
        raise ValueError("monitor current family token identity is incomplete")
    from src.engine.global_auction_universe import (
        _rebind_probability_witness_tokens,
    )

    witness = _rebind_probability_witness_tokens(
        witness,
        token_map_by_condition=token_map,
    )
    try:
        deterministic_condition_ids = frozenset(
            str(value)
            for value in json.loads(
                cache_metadata.get("deterministic_condition_ids_json", "[]")
            )
        )
    except (TypeError, ValueError):
        raise ValueError("monitor deterministic condition metadata is invalid")
    if not deterministic_condition_ids.issubset(condition_ids):
        raise ValueError("monitor deterministic condition metadata is inconsistent")
    probability_authority = str(
        day0_payload.get("probability_authority")
        or (
            "replacement_unobserved_day0_prefix_global_probability_v1"
            if unobserved_prefix
            else ""
        )
    ).strip()
    if not probability_authority:
        raise ValueError("monitor current global Day0 probability authority is missing")
    return _CurrentGlobalDay0FamilySnapshot(
        witness=witness,
        token_pairs=tuple(
            (bound_condition, *token_map[bound_condition])
            for bound_condition in condition_ids
        ),
        deterministic_condition_ids=deterministic_condition_ids,
        day0_payload=day0_payload,
        metric=metric,
        probability_authority=probability_authority,
    )


def _refresh_current_global_day0_probability(
    position: Position,
    *,
    trade_conn,
    decision_time: datetime | None = None,
    family_cache: _CurrentGlobalDay0FamilyCache | None = None,
    deadline_monotonic: float | None = None,
) -> tuple[float, Position, bool] | None:
    """Read one held side from a cycle-scoped current family witness."""

    condition_id = _canonical_condition_id(position)
    if condition_id is None:
        return None
    if trade_conn is None:
        raise ValueError("monitor current-global trade authority is missing")
    family_key = (
        str(position.city),
        str(position.target_date),
        resolve_position_metric(position)[0],
    )
    cached_snapshots = (
        family_cache.snapshots.get(family_key, ())
        if family_cache is not None
        else ()
    )
    for snapshot in tuple(cached_snapshots):
        if _day0_family_snapshot_covers_condition(snapshot, condition_id):
            if (
                snapshot.probability_authority
                == "replacement_unobserved_day0_prefix_global_probability_v1"
                and _target_day_has_canonical_observation_now(position)
            ):
                cached_snapshots.remove(snapshot)
                _cnt_inc("monitor_day0_unobserved_snapshot_cache_invalidated_total")
                continue
            _cnt_inc("monitor_day0_family_snapshot_cache_hit_total")
            return _materialize_current_global_day0_probability(position, snapshot)
    if family_cache is not None:
        cached_failure = family_cache.failures.get(family_key)
        if cached_failure is not None:
            failure_type, reason = cached_failure
            if failure_type is _Day0UnobservedPrefixUnavailable:
                if _target_day_has_canonical_observation_now(position):
                    del family_cache.failures[family_key]
                    _cnt_inc("monitor_day0_unobserved_failure_cache_invalidated_total")
                else:
                    _cnt_inc("monitor_day0_family_failure_cache_hit_total")
                    raise _Day0UnobservedPrefixUnavailable(reason)
            else:
                _cnt_inc("monitor_day0_family_failure_cache_hit_total")
                cached_error = _CachedCurrentGlobalDay0FamilyError(reason)
                cached_receipt = family_cache.failure_receipts.get(family_key)
                if isinstance(cached_receipt, Mapping):
                    setattr(
                        cached_error,
                        "day0_causal_bundle_validation_receipt",
                        dict(cached_receipt),
                    )
                raise cached_error

    primary_deadline = _day0_primary_snapshot_read_deadline(
        deadline_monotonic
    )
    try:
        snapshot = _build_current_global_day0_family_snapshot(
            position,
            trade_conn=trade_conn,
            decision_time=decision_time,
            cached_snapshots=cached_snapshots,
            deadline_monotonic=primary_deadline,
            hwm_deadline_monotonic=deadline_monotonic,
        )
    except Exception as exc:
        if _is_day0_materialization_visibility_gap(exc):
            # SCOPE: this held city/date/metric only. DRAIN: the source-clock
            # materializer commits the matching posterior/readiness certificate.
            # RESET: a bounded read-only connection pair sees that commit; otherwise
            # the existing fail-closed cache/reseed path remains authoritative.
            _cnt_inc("monitor_day0_materialization_visibility_retry_total")
            effective_deadline = _day0_materialization_visibility_retry_deadline(
                deadline_monotonic
            )
            remaining = effective_deadline - time.monotonic()
            if remaining > 0:
                time.sleep(
                    min(_DAY0_MATERIALIZATION_VISIBILITY_RETRY_SECONDS, remaining)
                )
            if time.monotonic() < effective_deadline:
                # The builder owns its forecasts+world read-only pair.  Calling it
                # again deliberately abandons the failed SQLite snapshot.
                try:
                    snapshot = _build_current_global_day0_family_snapshot(
                        position,
                        trade_conn=trade_conn,
                        decision_time=decision_time,
                        cached_snapshots=cached_snapshots,
                        deadline_monotonic=effective_deadline,
                        hwm_deadline_monotonic=effective_deadline,
                    )
                except Exception as retry_exc:
                    exc = retry_exc
                else:
                    _cnt_inc(
                        "monitor_day0_materialization_visibility_retry_recovered_total"
                    )
                    if family_cache is not None:
                        family_cache.snapshots.setdefault(family_key, []).append(snapshot)
                    _cnt_inc("monitor_day0_family_snapshot_build_total")
                    return _materialize_current_global_day0_probability(
                        position,
                        snapshot,
                    )
        if (
            family_cache is not None
            and str(exc) != "GLOBAL_REQUIRED_CONDITION_BINDING_INVALID"
        ):
            family_cache.failures[family_key] = (type(exc), str(exc))
            receipt = getattr(
                exc, "day0_causal_bundle_validation_receipt", None
            )
            if isinstance(receipt, Mapping):
                family_cache.failure_receipts[family_key] = dict(receipt)
            _cnt_inc("monitor_day0_family_builder_failure_total")
        raise
    if family_cache is not None:
        family_cache.snapshots.setdefault(family_key, []).append(snapshot)
    _cnt_inc("monitor_day0_family_snapshot_build_total")
    return _materialize_current_global_day0_probability(position, snapshot)


def _refresh_day0_unobserved_prefix_probability(
    position: Position,
    *,
    city,
    target_d,
    zero_observation_proven: bool = False,
    deadline_monotonic: float | None = None,
) -> tuple[float, Position, bool] | None:
    """Keep current belief continuous before the first target-day observation.

    A typed zero-observation result is stronger than the source-coverage grace:
    it proves no Day0 evidence exists yet, so local midnight cannot invalidate a
    fresh full-day posterior.  Generic observation failures retain the bounded
    grace because they do not prove that the Day0 evidence plane is empty.
    """

    if (
        not zero_observation_proven
        and not _within_day0_observation_start_grace(city, target_d)
    ):
        return None

    metric = resolve_position_metric(position)[0]
    from src.engine.position_belief import (
        POSTERIOR_PREDICTIVE_MEAN,
        ReplacementBelief,
        SELECTED_METHOD_REPLACEMENT_POSTERIOR,
        load_replacement_belief,
        monitor_belief_max_age_hours,
    )

    try:
        belief_deadline = (
            None
            if deadline_monotonic is None
            else min(
                float(deadline_monotonic),
                time.monotonic()
                + HELD_MONITOR_PRIMARY_BELIEF_READ_MAX_SECONDS,
            )
        )
        belief = load_replacement_belief(
            city=position.city,
            target_date=position.target_date,
            temperature_metric=metric,
            bin_label=position.bin_label,
            direction=str(getattr(position.direction, "value", position.direction)),
            max_age_hours=monitor_belief_max_age_hours(),
            deadline_monotonic=belief_deadline,
        )
    except Exception as exc:  # noqa: BLE001 - absence remains fail-closed
        logger.debug(
            "Day0 unobserved-prefix replacement read failed for %s: %s",
            getattr(position, "trade_id", "?"),
            exc,
        )
        return None
    if belief is None or not belief.fresh:
        return None

    from src.data.replacement_forecast_cycle_policy import CURRENT_EVIDENCE_SEMANTICS_REVISION
    from src.data.replacement_forecast_readiness import SOURCE_ID

    direction = str(getattr(position.direction, "value", position.direction))
    if not (
        isinstance(belief, ReplacementBelief)
        and belief.probability_functional == POSTERIOR_PREDICTIVE_MEAN
        and belief.probability_semantics_revision == CURRENT_EVIDENCE_SEMANTICS_REVISION
        and belief.source_table == "forecast_posteriors"
        and belief.source_id == SOURCE_ID
        and belief.posterior_method == SOURCE_ID
        and belief.direction == direction
        and all(isinstance(value, str) and value.strip() for value in (
            belief.posterior_id, belief.computed_at, belief.source_cycle_time, belief.bin_key,
        ))
        and " ".join(belief.bin_key.split()).casefold() == " ".join(position.bin_label.split()).casefold()
        and all(_monitor_receipt_float(value) is not None for value in (
            belief.held_side_lcb, belief.held_side_prob, belief.held_side_ucb,
        ))
        and 0.0 <= float(belief.held_side_lcb) <= float(belief.held_side_prob) <= float(belief.held_side_ucb) <= 1.0
    ):
        # SCOPE: this held family/side's current no-observation certificate.
        # DRAIN: the normal monitor reads its complete current replacement
        # carrier. RESET: current revision, source identity and coherent CI;
        # a generic observation failure never establishes the empty prefix.
        return None

    refreshed = _clone_for_probability_refresh(position)
    refreshed.selected_method = SELECTED_METHOD_REPLACEMENT_POSTERIOR
    _append_monitor_validation(
        refreshed,
        (
            "day0_unobserved_prefix_zero_observation_proven:"
            "replacement_posterior_authority"
            if zero_observation_proven
            else "day0_unobserved_prefix_within_start_grace:"
            "replacement_posterior_authority"
        ),
    )
    _append_monitor_validation(
        refreshed,
        (
            "day0_observation_unavailable_zero_observation_proven:"
            "replacement_posterior_authority"
            if zero_observation_proven
            else "day0_observation_unavailable_within_start_grace:"
            "replacement_posterior_authority"
        ),
    )
    _append_monitor_validation(refreshed, belief.freshness_validation())
    setattr(refreshed, "_replacement_current_evidence_held_bounds", (
        belief.held_side_lcb, belief.held_side_ucb,
    ))
    setattr(
        refreshed,
        _MONITOR_PROBABILITY_RECEIPT_ATTR,
        _compact_monitor_probability_receipt({
            "schema_version": 1,
            "selected_method": SELECTED_METHOD_REPLACEMENT_POSTERIOR,
            "probability_authority": belief.source_table,
            "probability_functional": belief.probability_functional,
            "probability_semantics_revision": belief.probability_semantics_revision,
            "posterior_id": belief.posterior_id,
            "computed_at": belief.computed_at,
            "source_cycle_time": belief.source_cycle_time,
            "source_id": belief.source_id,
            "posterior_method": belief.posterior_method,
            "captured_at_utc": datetime.now(timezone.utc).isoformat(),
            "city": str(position.city),
            "target_date": str(position.target_date),
            "temperature_metric": metric,
            "bin_label": str(position.bin_label),
            "bin_key": belief.bin_key,
            "held_direction": direction,
            "held_side_probability": float(belief.held_side_prob),
            "held_side_lcb": float(belief.held_side_lcb),
            "held_side_ucb": float(belief.held_side_ucb),
            "day0_zero_observation_proven": zero_observation_proven is True,
            **({"latest_raw_cycle_time": belief.latest_raw_cycle_time}
               if belief.latest_raw_cycle_time is not None else {}),
        }),
    )
    _set_day0_zero_probability_exit_authority(refreshed, False)
    _set_monitor_probability_fresh(refreshed, True)
    return float(belief.held_side_prob), refreshed, True


def _current_global_monitor_edge_band(
    held_samples,
    *,
    alpha: float,
    current_p_market: float,
    held_probability_point: float,
) -> tuple[float, float]:
    """Map one coherent point-plus-CVaR probability carrier into edge space."""

    from src.solve.solver import _lower_cvar

    samples = np.asarray(held_samples, dtype=float)
    if (
        samples.ndim != 1
        or samples.size < 2
        or not np.isfinite(samples).all()
        or (samples < 0.0).any()
        or (samples > 1.0).any()
        or not 0.0 < float(alpha) < 0.5
        or not np.isfinite(float(current_p_market))
        or not np.isfinite(float(held_probability_point))
        or not 0.0 <= float(held_probability_point) <= 1.0
    ):
        raise ValueError("current global monitor probability band is invalid")
    weights = np.ones(samples.size, dtype=float)
    q_lcb = _lower_cvar(samples, weights, float(alpha))
    q_ucb = 1.0 - _lower_cvar(1.0 - samples, weights, float(alpha))
    # The witness point can carry finite-evidence smoothing that is absent from
    # the empirical samples (all-zero/all-one tails are the sharp case).  A
    # confidence carrier that excludes its own authoritative point is
    # self-contradictory and makes Position.evaluate_exit report
    # EVIDENCE_UNAVAILABLE despite fresh q and book evidence.  Preserve the
    # solver tails while closing the carrier over the point used for payoff.
    q_lcb = min(float(q_lcb), float(held_probability_point))
    q_ucb = max(float(q_ucb), float(held_probability_point))
    edge_lower = float(q_lcb - current_p_market)
    edge_upper = float(q_ucb - current_p_market)
    # ExitContext translates back to probability space. Preserve the original
    # interval when subtraction/addition would otherwise round an endpoint
    # inward and falsely exclude its own current probability witness.
    if edge_lower + current_p_market > q_lcb:
        edge_lower = math.nextafter(edge_lower, -math.inf)
    if edge_upper + current_p_market < q_ucb:
        edge_upper = math.nextafter(edge_upper, math.inf)
    return edge_lower, edge_upper


def monitor_probability_refresh(
    pos: Position,
    *,
    conn,
    city,
    target_d,
    day0_family_cache: _CurrentGlobalDay0FamilyCache | None = None,
    deadline_monotonic: float | None = None,
) -> tuple[float, Position, bool | None]:
    """Refresh held-side posterior without consuming the held-token quote.

    PRIMARY AUTHORITY: a same-day absorbing hard fact is exact and dominates
    model belief. A post-day final observation enters through the complete
    global simplex so SELL remains owned by the BUY/SELL/HOLD/CASH auction.
    When no absorbing hard fact exists, the K1 single
    belief authority is the replacement-chain posterior
    (``forecast_posteriors``), the SAME authority the entry decision used.
    This removes the entry-belief vs exit-belief twin-authority failure mode
    without encoding any current live-position coverage claim in source comments.
    """
    hard_fact_overlay = _day0_absorbing_hard_fact_overlay(
        pos=pos,
        conn=conn,
        city=city,
        target_d=target_d,
    )
    if hard_fact_overlay is not None:
        return hard_fact_overlay

    if _would_use_day0_monitor_lane(pos, city, target_d):
        if _canonical_condition_id(pos) is not None:
            try:
                current = _refresh_current_global_day0_probability(
                    pos,
                    trade_conn=conn,
                    decision_time=(
                        day0_family_cache.decision_time
                        if day0_family_cache is not None
                        else None
                    ),
                    family_cache=day0_family_cache,
                    deadline_monotonic=deadline_monotonic,
                )
            except Exception as exc:  # noqa: BLE001 - current authority fails closed
                if isinstance(exc, _Day0UnobservedPrefixUnavailable):
                    unobserved_prefix = _refresh_day0_unobserved_prefix_probability(
                        pos,
                        city=city,
                        target_d=target_d,
                        zero_observation_proven=True,
                        deadline_monotonic=deadline_monotonic,
                    )
                    if unobserved_prefix is not None:
                        return unobserved_prefix
                stale = _clone_for_probability_refresh(pos)
                _set_monitor_probability_fresh(stale, False)
                bundle_receipt = getattr(
                    exc, "day0_causal_bundle_validation_receipt", None
                )
                if isinstance(bundle_receipt, Mapping):
                    bundle_monitor_receipt = {
                        "schema_version": 1,
                        "probability_authority": "day0_causal_bundle_successor_gate",
                        "causal_evidence_bundle_validation": dict(
                            bundle_receipt
                        ),
                    }
                    setattr(
                        stale,
                        _MONITOR_PROBABILITY_RECEIPT_ATTR,
                        bundle_monitor_receipt,
                    )
                    setattr(
                        stale,
                        "_day0_monitor_probability_receipt",
                        bundle_monitor_receipt,
                    )
                    _append_monitor_validation(
                        stale,
                        "day0_causal_evidence_bundle_validation:"
                        + json.dumps(
                            dict(bundle_receipt),
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    )
                post_day_final_missing = (
                    "POST_LOCAL_DAY_FINAL_OBSERVATION_UNAVAILABLE" in str(exc)
                )
                _append_monitor_validation(
                    stale,
                    "POST_LOCAL_DAY_FINAL_OBSERVATION_UNAVAILABLE"
                    if post_day_final_missing
                    else "day0_current_global_probability_unavailable:"
                    f"{type(exc).__name__}:{exc}",
                )
                if not post_day_final_missing and not isinstance(
                    exc,
                    _CachedCurrentGlobalDay0FamilyError,
                ):
                    _enqueue_single_family_belief_reseed_failsoft(
                        city=str(pos.city),
                        target_date=str(pos.target_date),
                        metric=resolve_position_metric(pos)[0],
                    )
                logger.warning(
                    "monitor_probability_refresh: current global Day0 probability "
                    "unavailable for %s: %s",
                    pos.trade_id,
                    exc,
                )
                return pos.p_posterior, stale, False
            if current is None:
                raise AssertionError("canonical condition must resolve current global q")
            return current
        # The Day0 q law is the complete current global simplex, which binds the
        # held token by its canonical condition identity. Without that identity
        # no Day0 q exists for this position: belief is unavailable, not a
        # second q built off the served law.
        stale = _clone_for_probability_refresh(pos)
        _set_monitor_probability_fresh(stale, False)
        _append_monitor_validation(stale, "day0_canonical_condition_identity_missing")
        return pos.p_posterior, stale, False

    from src.engine.position_belief import (
        POSTERIOR_PREDICTIVE_MEAN,
        SELECTED_METHOD_REPLACEMENT_POSTERIOR,
        load_replacement_belief,
        monitor_belief_max_age_hours,
    )

    try:
        primary_belief_deadline = (
            None
            if deadline_monotonic is None
            else min(
                float(deadline_monotonic),
                time.monotonic()
                + HELD_MONITOR_PRIMARY_BELIEF_READ_MAX_SECONDS,
            )
        )
        belief_kwargs = dict(
            city=pos.city,
            target_date=pos.target_date,
            temperature_metric=str(getattr(pos, "temperature_metric", "high")),
            bin_label=pos.bin_label,
            direction=str(getattr(pos.direction, "value", pos.direction)),
            max_age_hours=monitor_belief_max_age_hours(),
            deadline_monotonic=primary_belief_deadline,
        )
        if day0_family_cache is not None:
            belief_kwargs["now"] = day0_family_cache.decision_time
        belief = load_replacement_belief(**belief_kwargs)
    except Exception as exc:  # noqa: BLE001 — belief read must not kill the monitor
        belief = None
        logger.warning(
            "monitor_probability_refresh: replacement belief read failed for %s: %s",
            pos.trade_id,
            exc,
        )
    if belief is not None and belief.fresh:
        if not (
            np.isfinite(belief.held_side_lcb)
            and np.isfinite(belief.held_side_prob)
            and np.isfinite(belief.held_side_ucb)
            and belief.probability_functional == POSTERIOR_PREDICTIVE_MEAN
            and 0.0
            <= belief.held_side_lcb
            <= belief.held_side_prob
            <= belief.held_side_ucb
            <= 1.0
        ):
            logger.warning(
                "monitor_probability_refresh: incoherent replacement bounds for %s",
                pos.trade_id,
            )
            _append_monitor_validation(
                pos,
                "replacement_posterior_incoherent_current_evidence_bounds",
            )
            belief = None
    if belief is not None and belief.fresh:
        fresh_pos = _clone_for_probability_refresh(pos)
        setattr(fresh_pos, "selected_method", SELECTED_METHOD_REPLACEMENT_POSTERIOR)
        setattr(
            fresh_pos,
            "_replacement_current_evidence_held_bounds",
            (belief.held_side_lcb, belief.held_side_ucb),
        )
        _append_monitor_validation(fresh_pos, SELECTED_METHOD_REPLACEMENT_POSTERIOR)
        _append_monitor_validation(
            fresh_pos,
            "replacement_current_evidence_probability_bounds",
        )
        _append_monitor_validation(
            fresh_pos,
            f"probability_functional={POSTERIOR_PREDICTIVE_MEAN}",
        )
        _append_monitor_validation(fresh_pos, belief.freshness_validation())
        setattr(
            fresh_pos,
            _MONITOR_PROBABILITY_RECEIPT_ATTR,
            _compact_monitor_probability_receipt(
                {
                    "schema_version": 1,
                    "selected_method": SELECTED_METHOD_REPLACEMENT_POSTERIOR,
                    "probability_authority": belief.source_table,
                    "probability_functional": belief.probability_functional,
                    "probability_semantics_revision": getattr(belief, "probability_semantics_revision", None),
                    "posterior_id": belief.posterior_id,
                    "computed_at": belief.computed_at,
                    "source_cycle_time": belief.source_cycle_time,
                    "source_id": belief.source_id,
                    "posterior_method": belief.posterior_method,
                    "latest_raw_cycle_time": belief.latest_raw_cycle_time,
                    "held_side_probability": float(belief.held_side_prob),
                }
            ),
        )
        _set_monitor_probability_fresh(fresh_pos, True)
        return float(belief.held_side_prob), fresh_pos, True
    if belief is not None:
        _append_monitor_validation(
            pos,
            f"replacement_posterior_stale;age_h={belief.age_hours:.2f};"
            f"basis={belief.freshness_basis}"
            + (
                f";raw_input_lag_reason={belief.raw_input_lag_reason}"
                if belief.raw_input_lag_reason
                else ""
            ),
        )
    else:
        _append_monitor_validation(pos, "replacement_posterior_missing")

    # BELIEF-AUTHORITY FAULT (regime law U1/U2, 2026-06-12): a position whose
    # replacement belief is stale/missing must NOT have the gap papered over by
    # the legacy ENS forecast belief (the Denver 2026-06-12 incident: stale 0.79
    # masked as fresh while the market said 0.22). For these positions we (a) mark
    # belief NOT-fresh, (b) emit BELIEF_AUTHORITY_FAULT, (c) fire a fail-soft
    # single-family reseed so the SAME authority refreshes next cycle — and return
    # WITHOUT the cross-era substitution. The day0 nowcast lane is exempt (it is
    # settlement-day observation, not a forecast-belief substitution).
    #
    # SOURCE-PARITY WIDENING (2026-06-16, spine source-divergence fix, plan Option
    # A): the guard formerly fired only for replacement-authority (edli) positions,
    # leaving LEGACY non-edli positions to substitute the cold single-model
    # ``ensemble_snapshots`` EMOS center — the same cold-center divergence the entry
    # spine fix removed, re-introduced on the held side. The guard is now widened to
    # ALL non-day0 positions: a legacy position with a fresh ``forecast_posteriors``
    # row already returned fresh ABOVE (``load_replacement_belief`` is position-
    # agnostic); one with a stale/missing posterior is marked belief-unavailable
    # (fail-closed hold) rather than exiting off a cold ensemble center. VERIFIED
    # PREREQUISITE: all live legacy held positions (Houston aef7968f active;
    # Chengdu ad59da00 / Hong Kong day0_window) have ``forecast_posteriors`` coverage
    # for their family, so widening never strands a held position with NO belief
    # source. The same-family reseed is the only repair lane; the ensemble
    # registry below is retained ONLY as applied-list telemetry.
    _metric_for_family = resolve_position_metric(pos)[0]
    # LAYER 2 (2026-06-21 held-belief freeze fix): BEFORE fail-closing, attempt a
    # SYNCHRONOUS single-family read-through recompute of THIS family's replacement
    # posterior via the SAME canonical fusion authority, using whatever single_runs
    # are CURRENTLY persisted. This is NOT a loosening of the BELIEF_AUTHORITY_FAULT
    # guard: it makes the belief FRESH legitimately (canonical fusion, honestly wider
    # CI when fewer providers) instead of substituting the cold legacy ENS center.
    # If it yields a fresh posterior, the exit organ regains a fresh same-authority
    # belief THIS cycle (so CI_SEPARATED_REVERSAL can arm); if not, we fail-close as
    # before AND record a durable, retryable belief_debt marker (never a silent freeze).
    readthrough_deferred_to_producer = deadline_monotonic is not None
    readthrough_belief = _attempt_held_belief_readthrough_outside_bounded_monitor(
        pos,
        city=city,
        target_d=target_d,
        metric=_metric_for_family,
        deadline_monotonic=deadline_monotonic,
    )
    if readthrough_belief is not None:
        readthrough_prob, readthrough_lcb, readthrough_ucb = readthrough_belief
        fresh_pos = _clone_for_probability_refresh(pos)
        setattr(fresh_pos, "selected_method", SELECTED_METHOD_REPLACEMENT_POSTERIOR)
        setattr(
            fresh_pos,
            "_replacement_current_evidence_held_bounds",
            (readthrough_lcb, readthrough_ucb),
        )
        _append_monitor_validation(fresh_pos, SELECTED_METHOD_REPLACEMENT_POSTERIOR)
        _append_monitor_validation(
            fresh_pos,
            "replacement_current_evidence_probability_bounds",
        )
        _append_monitor_validation(
            fresh_pos,
            "belief_source=forecast_posteriors_readthrough_recompute;basis=canonical_bayes_precision_fusion",
        )
        _set_monitor_probability_fresh(fresh_pos, True)
        _clear_belief_debt(
            city=str(pos.city), target_date=str(pos.target_date),
            metric=_metric_for_family, pos=fresh_pos,
        )
        return float(readthrough_prob), fresh_pos, True
    if readthrough_deferred_to_producer:
        _append_monitor_validation(
            pos,
            "replacement_belief_readthrough_deferred_to_independent_producer",
        )
    _set_monitor_probability_fresh(pos, False)
    _append_monitor_validation(pos, "BELIEF_AUTHORITY_FAULT")
    _append_monitor_validation(pos, "legacy_belief_substitution_suppressed")
    # Durable, retryable belief-debt: the read-through could not honestly recompute
    # (no current single_runs / no on-disk anchor) — record it so a held position is
    # never silently frozen. The reseed below is the repair lane.
    _record_belief_debt(
        pos, city=str(pos.city), target_date=str(pos.target_date),
        metric=_metric_for_family,
        reason=(
            "bounded_monitor_reseed_required"
            if readthrough_deferred_to_producer
            else "readthrough_inputs_insufficient"
        ),
    )
    _enqueue_single_family_belief_reseed_failsoft(
        city=str(pos.city),
        target_date=str(pos.target_date),
        metric=_metric_for_family,
    )
    # Return the stored entry-time posterior as the value carrier but with
    # is_fresh=False so refresh_position records NaN current_p_posterior and
    # the exit organ treats belief as unavailable (never a stale-as-fresh).
    _posterior_provenance = pos.selected_method or pos.entry_method
    if not _posterior_provenance:
        _append_monitor_validation(pos, "stored_entry_probability_provenance_missing")
        return float("nan"), pos, False
    return pos.p_posterior, pos, False


def refresh_exact_one_position(pos: Position) -> EdgeContext:
    """Build a no-I/O context after settlement truth fixes held value at one."""

    if pos.direction not in {"buy_yes", "buy_no"}:
        raise ValueError(f"Unknown direction {pos.direction} for trade {pos.trade_id}")

    pos.last_monitor_at = datetime.now(timezone.utc).isoformat()
    pos.last_monitor_best_bid = None
    pos.last_monitor_best_ask = None
    pos.last_monitor_min_tick = None
    pos.last_monitor_market_vig = None
    pos.last_monitor_whale_toxicity = False
    pos.last_monitor_market_price_is_fresh = False
    setattr(pos, _HELD_MONITOR_FULL_DEPTH_ACTION_AUTHORITY_ATTR, False)
    setattr(pos, _HELD_MONITOR_MIN_ORDER_SIZE_ATTR, None)
    for attr in (
        _GLOBAL_MONITOR_SAMPLES_ATTR,
        _GLOBAL_MONITOR_ALPHA_ATTR,
        "_replacement_current_evidence_held_bounds",
    ):
        try:
            delattr(pos, attr)
        except AttributeError:
            pass

    pos.selected_method = SELECTED_METHOD_DAY0_ABSORBING_HARD_FACT
    pos.last_monitor_prob = 1.0
    pos.last_monitor_prob_is_fresh = True
    pos.last_monitor_edge = float("nan")
    _set_day0_zero_probability_exit_authority(pos, False)
    _append_monitor_validation(pos, SELECTED_METHOD_DAY0_ABSORBING_HARD_FACT)
    _append_monitor_validation(pos, "day0_hard_fact_probability_recompute_bypassed")
    _append_monitor_validation(pos, "day0_hard_fact_structural_win_quote_bypassed")

    return EdgeContext(
        p_raw=np.array([]),
        p_cal=np.array([]),
        p_market=np.array([]),
        p_posterior=1.0,
        forward_edge=float("nan"),
        alpha=0.0,
        confidence_band_upper=float("nan"),
        confidence_band_lower=float("nan"),
        entry_provenance=EntryMethod(pos.entry_method),
        decision_snapshot_id=pos.decision_snapshot_id,
        n_edges_found=0,
        n_edges_after_fdr=0,
        market_velocity_1h=0.0,
        divergence_score=0.0,
    )


def refresh_exact_zero_position(
    conn,
    clob: PolymarketClient,
    pos: Position,
    *,
    refresh_quote: bool = True,
) -> EdgeContext:
    """Build a held-position context after settlement truth fixes q at zero."""

    if pos.direction not in {"buy_yes", "buy_no"}:
        raise ValueError(f"Unknown direction {pos.direction} for trade {pos.trade_id}")

    pos.last_monitor_at = datetime.now(timezone.utc).isoformat()
    pos.last_monitor_best_bid = None
    pos.last_monitor_best_ask = None
    pos.last_monitor_min_tick = None
    pos.last_monitor_bid_size = None
    pos.last_monitor_bid_ladder = ()
    pos.last_monitor_market_vig = None
    pos.last_monitor_whale_toxicity = False
    pos.last_monitor_market_price_is_fresh = False
    setattr(pos, _HELD_MONITOR_FULL_DEPTH_ACTION_AUTHORITY_ATTR, False)
    for attr in (
        _GLOBAL_MONITOR_SAMPLES_ATTR,
        _GLOBAL_MONITOR_ALPHA_ATTR,
        _MONITOR_PROBABILITY_RECEIPT_ATTR,
    ):
        try:
            delattr(pos, attr)
        except AttributeError:
            pass

    current_p_market = (
        pos.last_monitor_market_price
        if pos.last_monitor_market_price is not None
        else pos.entry_price
    )
    quote = (
        monitor_quote_refresh(
            conn,
            clob,
            pos,
            retry_after_prefetch=True,
        )
        if refresh_quote
        else None
    )
    if quote is not None:
        pos.last_monitor_best_bid = quote.best_bid
        pos.last_monitor_best_ask = quote.best_ask
        pos.last_monitor_min_tick = getattr(quote, "min_tick", None)
        pos.last_monitor_bid_size = quote.bid_size
        pos.last_monitor_bid_ladder = quote.bid_ladder
        current_p_market = quote.mark_price
        pos.last_monitor_market_price = current_p_market
        pos.last_monitor_market_price_is_fresh = True
        setattr(
            pos,
            _HELD_MONITOR_FULL_DEPTH_ACTION_AUTHORITY_ATTR,
            bool(quote.full_depth_action_authority),
        )
        setattr(pos, _HELD_MONITOR_MIN_ORDER_SIZE_ATTR, quote.min_order_size)
    else:
        setattr(pos, _HELD_MONITOR_MIN_ORDER_SIZE_ATTR, None)

    pos.selected_method = SELECTED_METHOD_DAY0_ABSORBING_HARD_FACT
    pos.last_monitor_prob = 0.0
    pos.last_monitor_prob_is_fresh = True
    pos.last_monitor_edge = (
        -float(current_p_market)
        if pos.last_monitor_market_price_is_fresh
        else float("nan")
    )
    _set_day0_zero_probability_exit_authority(pos, True)
    _append_monitor_validation(pos, SELECTED_METHOD_DAY0_ABSORBING_HARD_FACT)
    _append_monitor_validation(pos, "day0_hard_fact_probability_recompute_bypassed")
    _persist_monitor_quote(conn, pos, quote)

    forward_edge = float(pos.last_monitor_edge)
    return EdgeContext(
        p_raw=np.array([]),
        p_cal=np.array([]),
        p_market=np.array([float(current_p_market)]),
        p_posterior=0.0,
        forward_edge=forward_edge,
        alpha=0.0,
        confidence_band_upper=forward_edge,
        confidence_band_lower=forward_edge,
        entry_provenance=EntryMethod(pos.entry_method),
        decision_snapshot_id=pos.decision_snapshot_id,
        n_edges_found=1,
        n_edges_after_fdr=1,
        market_velocity_1h=0.0,
        divergence_score=(
            float(current_p_market)
            if pos.last_monitor_market_price_is_fresh
            else float("nan")
        ),
    )


def refresh_position(
    conn,
    clob: PolymarketClient,
    pos: Position,
    *,
    refresh_quote: bool = True,
    quote_conn=None,
) -> EdgeContext:
    """Recompute held q and optionally fetch its executable market price.

    Blueprint v2 §7 Layer 1: uses same method as entry (p_raw_vector with MC noise).
    Returns: EdgeContext wrapping both fresh market and semantic provenance.
    Missing probability authority materializes as non-finite probability fields.
    ``conn`` serves every read. The quote evidence row is written on
    ``quote_conn`` when the caller reads off a read-only handle (the held
    monitor since c4f59a23d); a caller with one write-capable connection
    passes it as ``conn`` alone.
    """
    monitor_evaluated_at = datetime.now(timezone.utc).isoformat()
    pos.last_monitor_at = monitor_evaluated_at
    current_p_market = (
        pos.last_monitor_market_price
        if pos.last_monitor_market_price is not None
        else pos.entry_price
    )
    current_p_posterior = float("nan")
    try:
        delattr(pos, "_replacement_current_evidence_held_bounds")
    except AttributeError:
        pass
    if pos.direction not in {"buy_yes", "buy_no"}:
        logger.warning("Skipping refresh for %s: unknown direction %r", pos.trade_id, pos.direction)
        raise ValueError(f"Unknown direction {pos.direction} for trade {pos.trade_id}")

    pos.last_monitor_best_bid = None
    pos.last_monitor_best_ask = None
    pos.last_monitor_min_tick = None
    pos.last_monitor_bid_size = None
    pos.last_monitor_bid_ladder = ()
    pos.last_monitor_market_vig = None
    pos.last_monitor_whale_toxicity = None
    pos.last_monitor_market_price_is_fresh = False
    pos.last_monitor_prob_is_fresh = False
    setattr(pos, _HELD_MONITOR_FULL_DEPTH_ACTION_AUTHORITY_ATTR, False)
    setattr(pos, _HELD_MONITOR_MIN_ORDER_SIZE_ATTR, None)
    _set_day0_zero_probability_exit_authority(pos, False)
    try:
        delattr(pos, "_day0_monitor_probability_receipt")
    except AttributeError:
        pass
    for attr in (
        _GLOBAL_MONITOR_SAMPLES_ATTR,
        _GLOBAL_MONITOR_ALPHA_ATTR,
        _MONITOR_PROBABILITY_RECEIPT_ATTR,
        _MONITOR_PRIMARY_BELIEF_READ_ELAPSED_SECONDS_ATTR,
    ):
        try:
            delattr(pos, attr)
        except AttributeError:
            pass

    # 1. Refresh held-token quote
    market_refreshed = False
    quote = monitor_quote_refresh(conn, clob, pos) if refresh_quote else None
    if quote is not None:
        pos.last_monitor_best_bid = quote.best_bid
        pos.last_monitor_best_ask = quote.best_ask
        pos.last_monitor_min_tick = getattr(quote, "min_tick", None)
        pos.last_monitor_bid_size = quote.bid_size
        pos.last_monitor_bid_ladder = quote.bid_ladder
        current_p_market = quote.mark_price
        market_refreshed = True
        pos.last_monitor_market_price = current_p_market
        pos.last_monitor_market_price_is_fresh = True
        setattr(
            pos,
            _HELD_MONITOR_FULL_DEPTH_ACTION_AUTHORITY_ATTR,
            bool(quote.full_depth_action_authority),
        )
        setattr(pos, _HELD_MONITOR_MIN_ORDER_SIZE_ATTR, quote.min_order_size)

    # 2. Recompute P_posterior from fresh ENS/Day0 evidence
    city = cities_by_name.get(pos.city)
    if city is None:
        raise ValueError(f"Unknown city {pos.city} for trade {pos.trade_id}")

    from src.data.replacement_input_hwm import (
        install_frozen_replacement_artifact_hwm,
    )

    monitor_deadline = getattr(pos, _HELD_MONITOR_DEADLINE_ATTR, None)
    release_hwm_snapshot = install_frozen_replacement_artifact_hwm(
        (
            getattr(clob, _MONITOR_REPLACEMENT_HWM_SNAPSHOT_ATTR, None)
            if monitor_deadline is not None
            else None
        )
    )
    try:
        target_d = date.fromisoformat(pos.target_date)
        day0_family_cache = getattr(
            clob,
            _MONITOR_DAY0_FAMILY_CACHE_ATTR,
            None,
        )
        if not isinstance(day0_family_cache, _CurrentGlobalDay0FamilyCache):
            day0_family_cache = None
        if monitor_deadline is None:
            day0_family_cache = None
        _primary_belief_read_started_at = time.perf_counter()
        refreshed_p_posterior, refresh_pos, prob_refresh_is_fresh = monitor_probability_refresh(
            pos,
            conn=conn,
            city=city,
            target_d=target_d,
            day0_family_cache=day0_family_cache,
            deadline_monotonic=monitor_deadline,
        )
        setattr(
            pos,
            _MONITOR_PRIMARY_BELIEF_READ_ELAPSED_SECONDS_ATTR,
            time.perf_counter() - _primary_belief_read_started_at,
        )
        pos.selected_method = refresh_pos.selected_method
        pos.applied_validations = list(getattr(refresh_pos, "applied_validations", []) or [])
        # A1: Propagate bootstrap context from refresh_pos (may differ from pos for day0_window)
        _bootstrap_ctx = getattr(refresh_pos, "_bootstrap_context", None)
        if _bootstrap_ctx is not None:
            setattr(pos, "_bootstrap_context", _bootstrap_ctx)
        _day0_receipt = getattr(refresh_pos, "_day0_monitor_probability_receipt", None)
        if _day0_receipt is not None:
            setattr(pos, "_day0_monitor_probability_receipt", _day0_receipt)
        _probability_receipt = getattr(
            refresh_pos,
            _MONITOR_PROBABILITY_RECEIPT_ATTR,
            None,
        )
        if _probability_receipt is None and isinstance(_day0_receipt, dict):
            _probability_receipt = _compact_monitor_probability_receipt(
                _day0_receipt
            )
        if _probability_receipt is not None:
            setattr(
                pos,
                _MONITOR_PROBABILITY_RECEIPT_ATTR,
                _probability_receipt,
            )
        _global_samples = getattr(refresh_pos, _GLOBAL_MONITOR_SAMPLES_ATTR, None)
        if _global_samples is not None:
            setattr(pos, _GLOBAL_MONITOR_SAMPLES_ATTR, _global_samples)
            setattr(
                pos,
                _GLOBAL_MONITOR_ALPHA_ATTR,
                getattr(refresh_pos, _GLOBAL_MONITOR_ALPHA_ATTR),
            )
        _replacement_bounds = getattr(
            refresh_pos,
            "_replacement_current_evidence_held_bounds",
            None,
        )
        if _replacement_bounds is not None:
            setattr(
                pos,
                "_replacement_current_evidence_held_bounds",
                _replacement_bounds,
            )
        for attr in (
            "_day0_exit_authority_status",
            "_day0_exit_authority_reason",
        ):
            value = getattr(refresh_pos, attr, None)
            if value is not None:
                setattr(pos, attr, value)
        _set_day0_zero_probability_exit_authority(
            pos,
            bool(
                getattr(
                    refresh_pos,
                    _DAY0_ZERO_PROBABILITY_EXIT_AUTHORITY_ATTR,
                    False,
                )
            ),
        )

        # Persist monitor state on Position only when the producer explicitly
        # attests freshness. Stored entry-time posterior is not a current
        # monitor probability and must not be relabeled as such.
        pos.last_monitor_prob_is_fresh = prob_refresh_is_fresh is True
        if pos.last_monitor_prob_is_fresh:
            current_p_posterior = float(refreshed_p_posterior)
            pos.last_monitor_prob = current_p_posterior
            pos.last_monitor_edge = current_p_posterior - current_p_market
        else:
            current_p_posterior = float("nan")
            pos.last_monitor_edge = float("nan")
            _append_monitor_validation(pos, "monitor_probability_stale")
            if prob_refresh_is_fresh is None:
                _append_monitor_validation(pos, "monitor_probability_authority_unknown")
        if not market_refreshed:
            pos.last_monitor_market_price = current_p_market

    except Exception as e:
        logger.debug("ENS refresh failed for %s: %s", pos.trade_id, e)
        pos.last_monitor_prob_is_fresh = False
        current_p_posterior = float("nan")
        pos.last_monitor_edge = float("nan")
        # This cycle produced no refresh, so it owns no carried tags: the list
        # hydrated from last cycle's monitor event must not read as this
        # cycle's reasons (the success path replaces it wholesale above).
        pos.applied_validations = ["monitor_probability_refresh_failed"]
    finally:
        release_hwm_snapshot()

    _track_belief_staleness(pos)

    probability_authority_available = (
        pos.last_monitor_prob_is_fresh
        and np.isfinite(current_p_posterior)
    )

    if pos.direction != "buy_yes":
        pos.last_monitor_whale_toxicity = False
        _append_monitor_validation(pos, "whale_toxicity_not_applicable:buy_no")
    elif probability_authority_available:
        pos.last_monitor_whale_toxicity = None
        _append_monitor_validation(pos, "whale_toxicity_deferred:fresh_probability_authority")
    else:
        pos.last_monitor_whale_toxicity = _detect_whale_toxicity_from_orderbook(
            conn,
            clob,
            pos,
            held_best_bid=pos.last_monitor_best_bid,
            held_best_ask=pos.last_monitor_best_ask,
        )

    divergence_score = _compute_divergence_score(
        current_p_posterior, current_p_market, available=probability_authority_available
    )
    market_velocity_1h = 0.0

    # Try fetching 1h velocity if we know the token
    tid = pos.token_id if pos.direction == "buy_yes" else pos.no_token_id
    if tid:
        (
            market_velocity_1h,
            pos.flash_crash_count,
        ) = _causal_deep_market_catastrophe_evidence(
            conn,
            token_id=tid,
            current_bid=pos.last_monitor_best_bid,
            observed_at=getattr(quote, "source_timestamp", None),
        )
    else:
        pos.flash_crash_count = 0

    # Wrap into verified EdgeContext
    current_forward_edge = (
        current_p_posterior - current_p_market
        if probability_authority_available
        else float("nan")
    )

    # A1: Recompute bootstrap CI from fresh data (symmetric with entry path).
    # Slice P3.2 + P3-fix3 (post-review critic Major #2, 2026-04-26): when
    # fresh bootstrap CI is unavailable (no cached _bootstrap_context — e.g.
    # position re-loaded from JSON fallback after process restart, or test
    # fixture without the cached context), fall back to entry's CI width
    # rather than the pre-P3.2 degenerate `ci_lower = ci_upper =
    # current_forward_edge` (zero width). With degenerate fallback,
    # conservative_forward_edge collapsed to point-estimate logic —
    # exit decisions reverted to raw-point edge, breaking the entry/exit
    # epistemic-symmetry contract that known_gaps.md says was fixed for
    # the bootstrap-present path.
    #
    # CAVEAT (critic Major #2): entry_ci_width is FROZEN at entry-time
    # (cycle_runtime.py:273; never updated post-entry). For positions held
    # past significant bin-distribution evolution, this fallback gives
    # STALE-but-defensive CI width — wider than current truth in late-
    # cycle scenarios. Operationally bounded to post-restart first-cycle
    # window since the recompute branch dominates steady-state. DEBUG
    # log emitted on fallback so operators can audit incidence.
    _entry_ci_half = max(0.0, getattr(pos, "entry_ci_width", 0.0)) / 2.0
    ci_lower = current_forward_edge - _entry_ci_half
    ci_upper = current_forward_edge + _entry_ci_half
    bootstrap_ctx = getattr(pos, "_bootstrap_context", None)
    global_samples = getattr(pos, _GLOBAL_MONITOR_SAMPLES_ATTR, None)
    global_alpha = getattr(pos, _GLOBAL_MONITOR_ALPHA_ATTR, None)
    replacement_bounds = getattr(
        pos,
        "_replacement_current_evidence_held_bounds",
        None,
    )
    if not probability_authority_available:
        ci_lower = float("nan")
        ci_upper = float("nan")
    elif bootstrap_ctx is None or len(bootstrap_ctx.get("bins", []) if bootstrap_ctx else []) <= 1:
        logger.debug(
            "P3.2 fallback: no _bootstrap_context; using stale entry_ci_width "
            "for trade=%s entry_ci_width=%.6f",
            getattr(pos, "trade_id", "?"),
            getattr(pos, "entry_ci_width", 0.0),
        )
    if not probability_authority_available:
        pass
    elif replacement_bounds is not None:
        held_lcb, held_ucb = replacement_bounds
        ci_lower = float(held_lcb) - current_p_market
        ci_upper = float(held_ucb) - current_p_market
    elif global_samples is not None:
        ci_lower, ci_upper = _current_global_monitor_edge_band(
            global_samples,
            alpha=float(global_alpha),
            current_p_market=current_p_market,
            held_probability_point=current_p_posterior,
        )
    elif bootstrap_ctx is not None and len(bootstrap_ctx["bins"]) > 1:
        try:
            from src.strategy.market_analysis import MarketAnalysis
            from src.contracts.forecast_sharpness import ForecastSharpnessEvidence
            held_idx = bootstrap_ctx["held_idx"]
            bins = bootstrap_ctx["bins"]
            if len(bootstrap_ctx["member_extrema"]) == 0:
                raise ValueError("Bootstrap context has no member_extrema")
            p_market_arr = None
            if pos.direction == "buy_yes" or len(bins) <= 2:
                p_market_arr = np.zeros(len(bins))
                # Binary buy_no may still use complement price semantics. In
                # multi-bin buy_no, native NO quote below is the only executable
                # cost and model-only posterior never consumes this vector.
                p_market_yes = current_p_market if pos.direction == "buy_yes" else 0.0
                p_market_arr[held_idx] = p_market_yes
            p_market_no_arr = None
            buy_no_quote_available = None
            if pos.direction == "buy_no" and len(bins) > 2:
                p_market_no_arr = np.zeros(len(bins))
                p_market_no_arr[held_idx] = current_p_market
                buy_no_quote_available = np.zeros(len(bins), dtype=bool)
                buy_no_quote_available[held_idx] = True

            analysis = MarketAnalysis(
                p_raw=bootstrap_ctx["p_raw"],
                p_cal=bootstrap_ctx["p_cal"],
                p_market=p_market_arr,
                p_market_no=p_market_no_arr,
                buy_no_quote_available=buy_no_quote_available,
                alpha=bootstrap_ctx["alpha"],
                bins=bins,
                member_maxes=bootstrap_ctx["member_extrema"],
                calibrator=bootstrap_ctx["calibrator"],
                lead_days=bootstrap_ctx["lead_days"],
                unit=bootstrap_ctx["unit"],
                posterior_mode=MODEL_ONLY_POSTERIOR_MODE,
                bootstrap_probability_sampler=bootstrap_ctx.get("bootstrap_probability_sampler"),
                bootstrap_signal_type=bootstrap_ctx.get("bootstrap_signal_type", "monitor_forecast"),
                # K1: this path recomputes CI for a HELD position via _bootstrap_bin
                # (never find_edges), so the sharpness gate is moot — exempt evidence
                # keeps the required ctor contract satisfied without affecting CI.
                forecast_sharpness=ForecastSharpnessEvidence.exempt(unit=bootstrap_ctx["unit"]),
            )
            # Call _bootstrap_bin directly (not find_edges) so CI is computed
            # regardless of edge sign — monitor needs CI even when edge is negative.
            if pos.direction == "buy_yes":
                ci_lower, ci_upper, _ = analysis._bootstrap_bin(held_idx, edge_n_bootstrap())
            else:
                ci_lower, ci_upper, _ = analysis._bootstrap_bin_no(held_idx, edge_n_bootstrap())
            # Guard against NaN from degenerate bootstrap (e.g., empty member_maxes)
            if np.isnan(ci_lower) or np.isnan(ci_upper):
                raise ValueError("Bootstrap produced NaN CI bounds")
        except Exception as e:
            logger.debug("A1: Bootstrap CI recomputation failed for %s: %s", pos.trade_id, e)
            ci_half_width = max(0.0, pos.entry_ci_width) / 2.0
            ci_lower = current_forward_edge - ci_half_width
            ci_upper = current_forward_edge + ci_half_width
    else:
        # Single-bin fallback or no bootstrap context — use stale CI width
        ci_half_width = max(0.0, pos.entry_ci_width) / 2.0
        ci_lower = current_forward_edge - ci_half_width
        ci_upper = current_forward_edge + ci_half_width

    # Probability refresh may persist a world-owned Day0 observation fact, and
    # stale-q toxicity may fetch an adjacent CLOB book. The remaining edge/CI
    # work is read-only but can be expensive, so persist quote evidence only
    # after all of it; the caller commits immediately on return before venue I/O.
    _persist_monitor_quote(conn if quote_conn is None else quote_conn, pos, quote)

    return EdgeContext(
        p_raw=np.array([]),
        p_cal=np.array([]),
        p_market=np.array([current_p_market]),
        p_posterior=current_p_posterior,
        forward_edge=current_forward_edge,
        alpha=(
            float(global_alpha)
            if global_samples is not None and global_alpha is not None
            else (bootstrap_ctx["alpha"] if bootstrap_ctx else 0.0)
        ),
        confidence_band_upper=ci_upper,
        confidence_band_lower=ci_lower,
        entry_provenance=EntryMethod(pos.entry_method),
        decision_snapshot_id=pos.decision_snapshot_id,
        n_edges_found=1,
        n_edges_after_fdr=1,
        market_velocity_1h=market_velocity_1h,
        divergence_score=divergence_score,
    )

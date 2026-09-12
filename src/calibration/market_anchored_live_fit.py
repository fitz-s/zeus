# Created: 2026-08-27
# Last reused or audited: 2026-09-08
# Authority basis: docs/operations/current/plans/reversal_plan_tier0_2026-08-24.md
#   item 9 ("Market-anchored walk-forward calibrator") — live wiring. Row
#   extraction mirrors scripts/calibrator_walkforward_report.py (load_rows /
#   build_walk_forward_rows); the calibrator math is imported, never restated.
"""In-process market-anchored residual fit serving.

``MarketAnchoredFitProvider`` remains the legacy monitor provider over its
selected attribution corpus. ``CanonicalMarketAnchoredFitProvider`` serves
ENTRY only from the canonical command corpus, with a causal cutoff and exact
metric/execution-contract/raw-revision scope. Both cache immutable artifacts,
never DB handles.

Canonical probability serving omits receipt-decoded cash proof and terminal
markout, while retaining its complete command denominator and marking those
endpoint facts UNKNOWN. The default corpus reader still validates cash proof
for OOS/markout consumers. ``None`` only means no usable fit: it preserves raw
q and is neither calibrated probability nor positive-edge evidence.
"""

from __future__ import annotations

import sqlite3
import base64
import hashlib
import json
import zlib
from collections import Counter, OrderedDict, defaultdict
from dataclasses import dataclass, fields, replace
from types import MappingProxyType
import threading
import time
import math
from decimal import Decimal
from contextlib import ExitStack, contextmanager
from datetime import date, datetime, timedelta, timezone
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Callable, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.calibration.market_anchored_residual import (
    BETA_MAX,
    BETA_MIN,
    CLIP_D,
    LAMBDA_GRID,
    MIN_TRAIN_ROWS,
    P_CLIP_HI,
    P_CLIP_LO,
    FitRow,
    ResidualCalibratorArtifact,
    LEGACY_LEAD_BUCKETS,
    LEGACY_LEAD_CALENDAR_REVISION,
    LEAD_BUCKETS,
    LEAD_CALENDAR_REVISION,
    UNBOUND_LEAD_CALENDAR_REVISION,
    apply_artifact,
    fit,
    legacy_lead_bucket_of,
    lead_bucket_of,
)
from src.contracts.payoff_q_correction import (
    CalibrationFitScope,
    CalibrationPolicySpec,
    CanonicalTrainingManifest,
    PayoffQCorrection,
    PayoffQCorrectionUnavailable,
)

# One fit serves this long before a refit is attempted. Six hours matches the
# forecast cycle interval (00/06/12/18Z): settled rows arrive in bursts tied to
# market resolution, so refitting faster re-reads the same table to recompute
# the same parameters, and refitting slower lets a full cycle of settled
# evidence sit unused.
DEFAULT_TTL = timedelta(hours=6)

# Lambda for the live fit. The walk-forward report selects lambda on an early
# tuning fold; live has no such fold (it fits once over all settled history),
# so it takes the grid's most-regularized value. Under-regularizing a live
# acting probability manufactures edge; over-regularizing shrinks toward the
# market price, which is the plan's explicit safe direction.
LIVE_LAMBDA = max(LAMBDA_GRID)
CALIBRATION_ALGORITHM_REVISION = "market_anchored_ridge_offset_irls_v1"
CALIBRATION_INPUT_REVISION = (
    "settlement_attribution.q_in_bin_current_graded_before_cutoff_claim_weight_v1"
)
CALIBRATION_METRIC_POOLING = "unfiltered_attribution_claims"
CANONICAL_CALIBRATION_INPUT_REVISION = (
    "canonical_fit_corpus.sealed_entry_fill_finalized_payout_v1"
)
CANONICAL_CALIBRATION_METRIC_POOLING = (
    "metric_separated_entry_execution_contract_raw_probability_revision"
)

_FIT_TABLE_BY_ALIAS = {
    "main": "settlement_attribution",
    "world": "world.settlement_attribution",
}
ArtifactCacheKey = tuple[object, ...]


def _canonical_db_identity(
    conn: sqlite3.Connection,
    *,
    schema_alias: str,
) -> tuple[str, int, int] | None:
    """Return the physical identity of one attached canonical database."""

    if schema_alias not in _FIT_TABLE_BY_ALIAS:
        return None
    try:
        rows = conn.execute("PRAGMA database_list").fetchall()
        raw_path = next(
            str(row[2] or "")
            for row in rows
            if len(row) > 2 and str(row[1]) == schema_alias
        )
        if not raw_path:
            return None
        path = Path(raw_path).resolve(strict=False)
        stat = path.stat()
        return str(path), int(stat.st_dev), int(stat.st_ino)
    except (OSError, StopIteration, TypeError, ValueError, sqlite3.Error):
        return None


def _borrowed_db_identity(
    conn: sqlite3.Connection,
    *,
    schema_alias: str,
) -> tuple[str, int, int] | None:
    """Physical identity for a borrowed canonical handle; never retains it."""

    try:
        rows = conn.execute("PRAGMA database_list").fetchall()
        raw_path = next(
            str(row[2] or "")
            for row in rows
            if len(row) > 2 and str(row[1]) == schema_alias
        )
        if not raw_path:
            return None
        path = Path(raw_path).resolve(strict=False)
        stat = path.stat()
        return str(path), int(stat.st_dev), int(stat.st_ino)
    except (OSError, StopIteration, TypeError, ValueError, sqlite3.Error):
        return None


class MarketAnchoredArtifactCache:
    """Thread-safe cache of immutable fit artifacts, never database handles."""

    def __init__(self, *, max_entries: int | None = None) -> None:
        if max_entries is not None and (type(max_entries) is not int or max_entries <= 0):
            raise ValueError("artifact cache max_entries must be positive")
        self._lock = threading.Lock()
        self._max_entries = max_entries
        self._entries: dict[
            ArtifactCacheKey,
            tuple[ResidualCalibratorArtifact, datetime],
        ] = {}

    def get_or_fit(
        self,
        key: ArtifactCacheKey,
        *,
        now: datetime,
        ttl: timedelta,
        fit_current: Callable[[], ResidualCalibratorArtifact | None],
        deadline_monotonic: float | None = None,
    ) -> tuple[ResidualCalibratorArtifact | None, datetime | None]:
        """Serve a live artifact or fit once using only the current connection."""

        if deadline_monotonic is None:
            acquired = self._lock.acquire()
        else:
            remaining = float(deadline_monotonic) - time.monotonic()
            if not math.isfinite(remaining) or remaining <= 0:
                return None, now
            acquired = self._lock.acquire(timeout=remaining)
        if not acquired:
            return None, now
        try:
            if (
                deadline_monotonic is not None
                and time.monotonic() >= float(deadline_monotonic)
            ):
                return None, now
            cached = self._entries.get(key)
            if cached is not None:
                artifact, fitted_at = cached
                age = now - fitted_at
                if timedelta(0) <= age < ttl:
                    if (
                        deadline_monotonic is not None
                        and time.monotonic() >= float(deadline_monotonic)
                    ):
                        return None, now
                    return artifact, fitted_at
            artifact = fit_current()
            if (
                artifact is not None
                and (
                    deadline_monotonic is None
                    or time.monotonic() < float(deadline_monotonic)
                )
            ):
                if cached is None or now >= cached[1]:
                    self._entries[key] = (artifact, now)
                    if self._max_entries is not None:
                        while len(self._entries) > self._max_entries:
                            self._entries.pop(next(iter(self._entries)))
                    return artifact, now
                # This was a causal backfill earlier than a newer shared
                # artifact.  Serve the backfill to this caller without
                # letting its provider-local cache hide the newer artifact.
                return artifact, None
            # A failed current connection must not poison a different provider's
            # cache entry. The caller may still locally cache None for its own TTL.
            return None, now
        finally:
            self._lock.release()


_SHARED_ARTIFACT_CACHE = MarketAnchoredArtifactCache()
_CANONICAL_CACHE_MAX_ENTRIES = 128
_SHARED_CANONICAL_ARTIFACT_CACHE = MarketAnchoredArtifactCache(
    max_entries=_CANONICAL_CACHE_MAX_ENTRIES,
)


def _freeze_corpus_value(value):
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_corpus_value(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_corpus_value(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze_corpus_value(item) for item in value)
    return value


class CanonicalCorpusCache:
    """Bounded cache of immutable canonical corpora, never SQLite handles."""

    def __init__(self, *, max_entries: int = _CANONICAL_CACHE_MAX_ENTRIES) -> None:
        if type(max_entries) is not int or max_entries <= 0:
            raise ValueError("canonical corpus cache max_entries must be positive")
        self._lock = threading.Lock()
        self._max_entries = max_entries
        self._entries: OrderedDict[ArtifactCacheKey, tuple[CanonicalFitCorpus, datetime]] = OrderedDict()

    def get_or_load(
        self,
        key: ArtifactCacheKey,
        *,
        requested_cutoff: datetime,
        ttl: timedelta,
        load_current: Callable[[], CanonicalFitCorpus | None],
        deadline_monotonic: float | None = None,
    ) -> tuple[CanonicalFitCorpus | None, datetime | None]:
        """Reuse only a corpus causally no newer than the requested cutoff."""

        if deadline_monotonic is None:
            acquired = self._lock.acquire()
        else:
            remaining = float(deadline_monotonic) - time.monotonic()
            if not math.isfinite(remaining) or remaining <= 0:
                return None, None
            acquired = self._lock.acquire(timeout=remaining)
        if not acquired:
            return None, None
        try:
            if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                return None, None
            cached = self._entries.get(key)
            if cached is not None:
                corpus, corpus_cutoff = cached
                age = requested_cutoff - corpus_cutoff
                if timedelta(0) <= age < ttl:
                    self._entries.move_to_end(key)
                    return corpus, corpus_cutoff
            corpus = load_current()
            if (
                corpus is None
                or _parse_ts(corpus.training_cutoff) != requested_cutoff
                or (deadline_monotonic is not None and time.monotonic() >= deadline_monotonic)
            ):
                return None, None
            corpus = replace(
                corpus,
                records=_freeze_corpus_value(corpus.records),
                unknown=_freeze_corpus_value(corpus.unknown),
                command_accounting=_freeze_corpus_value(corpus.command_accounting),
            )
            if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                return None, None
            # A backward request must not replace the newer corpus used by the
            # ordinary forward cadence, but its caller still receives its own
            # causally valid result.
            if cached is None or requested_cutoff >= cached[1]:
                self._entries[key] = (corpus, requested_cutoff)
                self._entries.move_to_end(key)
                while len(self._entries) > self._max_entries:
                    self._entries.popitem(last=False)
            return corpus, requested_cutoff
        finally:
            self._lock.release()


_SHARED_CANONICAL_CORPUS_CACHE = CanonicalCorpusCache()


def get_shared_artifact_cache() -> MarketAnchoredArtifactCache:
    """Return the process-local artifact cache; no connection is retained."""

    return _SHARED_ARTIFACT_CACHE


@contextmanager
def _sqlite_fit_deadline(
    conn: sqlite3.Connection,
    deadline_monotonic: float | None,
):
    """Bound one borrowed SQLite read without replacing outer progress hooks."""

    if deadline_monotonic is None:
        yield conn
        return
    remaining = float(deadline_monotonic) - time.monotonic()
    if not math.isfinite(remaining) or remaining <= 0:
        raise TimeoutError("market anchored SQLite fit deadline expired")

    previous_busy_timeout: int | None = None
    timer: threading.Timer | None = None
    try:
        try:
            row = conn.execute("PRAGMA busy_timeout").fetchone()
            previous_busy_timeout = int(row[0]) if row else None
            if previous_busy_timeout is not None:
                remaining_ms = max(
                    1,
                    int(max(0.0, deadline_monotonic - time.monotonic()) * 1000.0),
                )
                conn.execute(
                    f"PRAGMA busy_timeout = {min(previous_busy_timeout, remaining_ms)}"
                )
        except Exception:  # noqa: BLE001 - the timer still bounds supported handles
            previous_busy_timeout = None

        remaining = float(deadline_monotonic) - time.monotonic()
        if not math.isfinite(remaining) or remaining <= 0:
            raise TimeoutError("market anchored SQLite fit deadline expired")
        timer = threading.Timer(
            remaining,
            lambda: _interrupt_connection(conn),
        )
        timer.daemon = True
        timer.start()
        yield conn
    finally:
        if timer is not None:
            timer.cancel()
            timer.join()
        if previous_busy_timeout is not None:
            try:
                conn.execute(f"PRAGMA busy_timeout = {previous_busy_timeout}")
            except Exception:  # noqa: BLE001 - borrowed connection may be closing
                pass


def _interrupt_connection(conn: sqlite3.Connection) -> None:
    try:
        conn.interrupt()
    except Exception:  # noqa: BLE001 - interruption is best effort
        pass


def _validated_city_timezone_snapshot(
    city_timezones: Mapping[str, str] | None,
) -> tuple[tuple[str, str], ...] | None:
    """Copy exact city names and validated ZoneInfo keys immutably."""

    if city_timezones is None:
        return ()
    if not isinstance(city_timezones, Mapping):
        return None
    if not city_timezones:
        return None
    snapshot: list[tuple[str, str]] = []
    for city, zone_name in city_timezones.items():
        if not isinstance(city, str) or not city or not isinstance(zone_name, str):
            continue
        try:
            zone = ZoneInfo(zone_name)
        except (TypeError, ValueError, ZoneInfoNotFoundError):
            continue
        snapshot.append((city, zone.key))
    return tuple(sorted(snapshot)) if snapshot else None


def _city_local_target_date(
    instant: datetime,
    city: str,
    city_timezone_snapshot: tuple[tuple[str, str], ...],
) -> date | None:
    """Derive a target-date lead anchor from one aware instant and city."""

    if (
        not isinstance(instant, datetime)
        or instant.tzinfo is None
        or instant.utcoffset() is None
        or not isinstance(city, str)
        or not city
    ):
        return None
    zones = dict(city_timezone_snapshot)
    zone_name = zones.get(city)
    if zone_name is None:
        return None
    try:
        return instant.astimezone(ZoneInfo(zone_name)).date()
    except (TypeError, ValueError, ZoneInfoNotFoundError, OverflowError):
        return None


def _snapshot_is_valid(snapshot: object) -> bool:
    if not isinstance(snapshot, tuple) or not snapshot:
        return False
    cities: set[str] = set()
    for item in snapshot:
        if not isinstance(item, tuple) or len(item) != 2:
            return False
        city, zone_name = item
        if not isinstance(city, str) or not city or city in cities:
            return False
        if not isinstance(zone_name, str) or not zone_name:
            return False
        try:
            ZoneInfo(zone_name)
        except (TypeError, ValueError, ZoneInfoNotFoundError):
            return False
        cities.add(city)
    return tuple(sorted(snapshot)) == snapshot


def _parse_ts(value: object) -> datetime | None:
    """Parse an ISO8601 timestamp to tz-aware UTC, or None (never raises)."""

    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _parse_date(value: object) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


CANONICAL_CORPUS_REVISION = (
    "sealed_raw_finalized_payout_event_weight_v3_execution_contract_bound"
)

_EXECUTION_CONTRACT_BY_MODE = {
    "TAKER_LIMIT": frozenset({"FOK_FULL_OR_ZERO", "FAK_PARTIAL"}),
    "MAKER_REST": frozenset({"MAKER_REST"}),
}
_EXECUTION_CONTRACTS = frozenset(
    contract
    for contracts in _EXECUTION_CONTRACT_BY_MODE.values()
    for contract in contracts
)


def _execution_contract_for(
    mode: object, order_type: object, post_only: object,
) -> tuple[str | None, str | None]:
    """Resolve the sealed venue envelope into a fit-policy contract."""

    mode = str(mode or "").strip().upper()
    order_type = str(order_type or "").strip().upper()
    if mode not in _EXECUTION_CONTRACT_BY_MODE:
        return None, "EXECUTION_CONTRACT_MODE_UNSUPPORTED"
    if order_type == "":
        return None, "EXECUTION_CONTRACT_ENVELOPE_MISSING"
    if post_only is None:
        return None, "EXECUTION_CONTRACT_ENVELOPE_MISSING"
    if isinstance(post_only, bool):
        post_only = int(post_only)
    elif isinstance(post_only, int):
        if post_only not in (0, 1):
            return None, "EXECUTION_CONTRACT_ENVELOPE_INVALID"
    elif isinstance(post_only, float):
        if not math.isfinite(post_only) or post_only not in (0.0, 1.0):
            return None, "EXECUTION_CONTRACT_ENVELOPE_INVALID"
        post_only = int(post_only)
    elif isinstance(post_only, str) and post_only in {"0", "1"}:
        post_only = int(post_only)
    else:
        return None, "EXECUTION_CONTRACT_ENVELOPE_INVALID"
    if mode == "TAKER_LIMIT" and post_only == 0:
        contract = {
            "FOK": "FOK_FULL_OR_ZERO",
            "FAK": "FAK_PARTIAL",
        }.get(order_type)
        if contract is not None:
            return contract, None
    if mode == "MAKER_REST" and post_only == 1 and order_type in {"GTC", "GTD"}:
        return "MAKER_REST", None
    return None, "EXECUTION_CONTRACT_ENVELOPE_MISMATCH"


@dataclass(frozen=True)
class CanonicalFitCorpus:
    """Filled-policy evidence; neither account returns nor admission authority."""

    records: tuple[Mapping[str, object], ...]
    unknown: Mapping[str, int]
    command_count: int
    training_cutoff: str
    revision: str = CANONICAL_CORPUS_REVISION
    command_accounting: tuple[Mapping[str, object], ...] = ()

    def _fit_selection(
        self, *, metric: str, execution_mode: str, probability_revision: str,
        execution_contract: str,
    ) -> tuple[list[Mapping[str, object]], list[FitRow]]:
        """Keep HIGH/LOW and execution policies separate; normalize each event."""
        if metric not in {"high", "low"}:
            raise ValueError("unsupported metric")
        if execution_mode not in _EXECUTION_CONTRACT_BY_MODE:
            raise ValueError("unsupported execution_mode")
        if not isinstance(probability_revision, str) or not probability_revision.strip():
            raise ValueError("probability_revision is required")
        if execution_contract not in _EXECUTION_CONTRACTS:
            raise ValueError("unsupported execution_contract")
        if execution_contract not in _EXECUTION_CONTRACT_BY_MODE[execution_mode]:
            raise ValueError("execution_contract does not match execution_mode")
        records = [r for r in self.records if r["metric"] == metric
                   and r["execution_mode"] == execution_mode
                   and r.get("execution_contract") == execution_contract
                   and r.get("raw_probability_revision") == probability_revision
                   and r["lead_bucket"] is not None and r["payout"] in (0, 1)]
        totals: dict[tuple, float] = defaultdict(float)
        for record in records:
            totals[record["event_key"]] += record["confirmed_shares"]
        # The serving transform applies the artifact in YES-event space.
        # Complement NO inputs AND label together, never just the prediction.
        rows = [FitRow(
            p0=r["p0"] if r["side"] == "YES" else 1 - r["p0"],
            q_raw=r["q_raw"] if r["side"] == "YES" else 1 - r["q_raw"],
            y=r["payout"] if r["side"] == "YES" else 1 - r["payout"],
            lead_bucket=r["lead_bucket"],
            w=r["confirmed_shares"] / totals[r["event_key"]],
        ) for r in records]
        return records, rows

    def fit_rows(
        self, *, metric: str, execution_mode: str, probability_revision: str,
        execution_contract: str,
    ) -> list[FitRow]:
        return self._fit_selection(
            metric=metric, execution_mode=execution_mode,
            probability_revision=probability_revision,
            execution_contract=execution_contract,
        )[1]

    def training_manifest(
        self, *, scope: CalibrationFitScope,
    ) -> CanonicalTrainingManifest:
        """Describe precisely the normalized rows returned by ``fit_rows``."""
        if not isinstance(scope, CalibrationFitScope):
            raise TypeError("canonical training manifest scope is invalid")
        records, rows = self._fit_selection(
            metric=scope.metric, execution_mode=scope.execution_mode,
            probability_revision=scope.raw_probability_revision,
            execution_contract=scope.execution_contract,
        )
        if len(records) != len(rows) or not records:
            raise ValueError("canonical training manifest rows are not aligned")
        manifest_rows: list[dict[str, object]] = []
        for record, row in zip(records, rows, strict=True):
            identity_fields = (
                record.get("command_id"), record.get("certificate_hash"),
                record.get("condition_id"), record.get("token_id"),
                record.get("side"), row.lead_bucket,
            )
            event_key = record.get("event_key")
            if (
                not all(isinstance(value, str) and value.strip() for value in identity_fields)
                or record.get("side") not in {"YES", "NO"}
                or not isinstance(event_key, tuple) or len(event_key) != 3
                or not all(isinstance(value, str) and value.strip() for value in event_key)
                or not all(
                    type(value) in (int, float) and math.isfinite(float(value))
                    for value in (row.p0, row.q_raw, row.w, row.y)
                )
                or not 0.0 <= float(row.p0) <= 1.0
                or not 0.0 <= float(row.q_raw) <= 1.0
                or float(row.y) not in {0.0, 1.0}
                or float(row.w) <= 0.0
            ):
                raise ValueError("canonical training manifest row is not aligned")
            fill_at = _parse_ts(record.get("fill_available_at"))
            label_at = _parse_ts(record.get("payout_available_at"))
            if fill_at is None or label_at is None:
                raise ValueError("canonical training manifest availability is unavailable")
            available_at = max(fill_at, label_at)
            manifest_rows.append({
                "command_id": str(record.get("command_id") or ""),
                "certificate_hash": str(record.get("certificate_hash") or ""),
                "condition_id": str(record.get("condition_id") or ""),
                "token_id": str(record.get("token_id") or ""),
                "side": str(record.get("side") or ""),
                "event_key": [str(item) for item in event_key],
                "lead_bucket": str(row.lead_bucket or ""),
                "p0": float(row.p0),
                "q_raw": float(row.q_raw),
                "label": int(row.y),
                "weight": float(row.w),
                "fill_available_at": fill_at.isoformat().replace("+00:00", "Z"),
                "label_available_at": label_at.isoformat().replace("+00:00", "Z"),
                "availability_upper_bound": available_at.isoformat().replace("+00:00", "Z"),
            })
        # IRLS consumes this sequence; floating-point reductions depend on it.
        ordered = tuple(manifest_rows)
        from src.decision_kernel.canonicalization import stable_hash
        input_hash = stable_hash({"rows": [dict(row) for row in ordered]})
        fill_available = max(
            _parse_ts(row["fill_available_at"]) for row in ordered
        )
        label_available = max(
            _parse_ts(row["label_available_at"]) for row in ordered
        )
        if fill_available is None or label_available is None:
            raise ValueError("canonical training manifest availability is unavailable")
        return CanonicalTrainingManifest.build(
            scope_hash=scope.as_payload()["scope_hash"],
            corpus_revision=self.revision,
            training_cutoff=self.training_cutoff,
            row_count=len(ordered),
            event_count=len({tuple(row["event_key"]) for row in ordered}),
            weight_sum=math.fsum(float(row["weight"]) for row in ordered),
            max_fill_available_at=fill_available.isoformat().replace("+00:00", "Z"),
            max_label_available_at=label_available.isoformat().replace("+00:00", "Z"),
            input_hash=input_hash,
        )



def _command_outcome_index(command: dict) -> int | None:
    """Bind a token to its snapshot's CTF slot independently of model evidence."""
    try:
        token_map = command["token_map_json"]
        token_map = json.loads(token_map) if isinstance(token_map, str) else token_map
        if not isinstance(token_map, dict):
            return None
        token = command["token_id"]
        yes, no = command["yes_token_id"], command["no_token_id"]
        if not token or not yes or not no or yes == no:
            return None
        token_ids = token_map.get("clobTokenIds")
        if (token_map.get("token_map_valid") is True and isinstance(token_ids, list)
                and len(token_ids) == 2 and len(set(map(str, token_ids))) == 2
                and {str(t) for t in token_ids} == {yes, no} and str(token) in token_ids):
            return token_ids.index(str(token))
        if token_map.get("YES") == yes and token_map.get("NO") == no and token in (yes, no):
            from src.venue.polymarket_v2_adapter import _zeus_index_set_to_ctf_bitmask
            return _zeus_index_set_to_ctf_bitmask(2 if token == yes else 1).bit_length() - 1
    except (KeyError, TypeError, ValueError):
        pass
    return None


def _command_accounting(command, fills, pair, *, conn, cutoff, schema,
                        available_cash_transactions=None, include_cash_proofs=True):
    """Preserve physical ENTRY outcomes before any calibration sample selection.

    The endpoint is token settlement markout, not exited-position cash PnL.
    Numerator/denominator fields retain fractional payouts without rounding.
    """
    from src.ingest.payout_observer import _coherent_finalized_pair
    index = _command_outcome_index(command)
    # Absence of a proof is enough to report UNKNOWN. Do not repeatedly scan
    # signed envelopes for the entire unproved population. Presence still
    # requires the full independent decoder and identity checks below.
    if not include_cash_proofs:
        cash = {"status": "UNKNOWN", "reason": "CHAIN_CASH_NOT_REQUESTED"}
    elif not fills:
        cash = {"status": "UNKNOWN", "reason": "LOCAL_FILL_CHILDREN_MISSING"}
    elif (available_cash_transactions is not None
            and any(str(fill.get("tx_hash") or "").lower() not in available_cash_transactions
                    for fill in fills)):
        cash = {"status": "UNKNOWN", "reason": "CHAIN_CASH_PROOF_UNAVAILABLE"}
    else:
        from src.state.fill_cash_reader import read_command_fill_cash

        cash = read_command_fill_cash(conn, command=command, fills=fills, cutoff=cutoff, schema=schema)
    row = {key: command.get(key) for key in (
        "command_id", "created_at", "order_side", "snapshot_id", "condition_id", "token_id",
    )}
    row.update(outcome_index=index, confirmed_fill_count=len(fills), confirmed_shares_atoms=None,
               chain_cash=cash, payout_pair=tuple(pair),
               terminal_net_payoff_numerator_atoms=None, terminal_net_payoff_denominator=None,
               net_markout_per_share_numerator=None, net_markout_per_share_denominator=None,
               endpoint_available_at=None, physical_endpoint_status="UNKNOWN",
               physical_endpoint_reason=None, calibration_evidence_reason=None,
               calibration_policy=None, calibration_policy_reason=None,
               calibration_training_manifest=None,
               calibration_training_manifest_reason="CALIBRATION_EVIDENCE_NOT_EVALUATED")
    reasons = []
    if command.get("order_side") != "BUY":
        reasons.append("ENTRY_SIDE_UNSUPPORTED_FOR_BUY_MARKOUT")
    if not fills:
        reasons.append("NO_CONFIRMED_FILL_AT_CUTOFF")
    created = _parse_ts(command.get("created_at"))
    shares_atoms = 0
    available = []
    for fill in fills:
        observed = _parse_ts(fill.get("observed_at"))
        ingested_text = str(fill.get("ingested_at"))
        if "+" not in ingested_text and not ingested_text.endswith("Z"):
            ingested_text = ingested_text.replace(" ", "T") + "Z"
        ingested, executed = _parse_ts(ingested_text), _parse_ts(fill.get("execution_ts"))
        try:
            atoms = Decimal(str(fill.get("filled_size"))) * 1_000_000
            valid_atoms = atoms.is_finite() and atoms > 0 and atoms == atoms.to_integral_value()
        except ArithmeticError:
            valid_atoms = False
        if (not valid_atoms or not created or not observed or not ingested or not executed
                or not created <= executed <= observed < cutoff or not executed <= ingested < cutoff
                or fill.get("command_id") != command["command_id"]
                or not command.get("venue_order_id")
                or fill.get("venue_order_id") != command["venue_order_id"]):
            reasons.append("CONFIRMED_FILL_CLOCK_OR_IDENTITY_UNBOUND")
            break
        shares_atoms += int(atoms)
        available.extend((observed, ingested))
    else:
        if fills:
            row["confirmed_shares_atoms"] = shares_atoms
    if cash["status"] != "PROVEN":
        reasons.append("CHAIN_CASH_UNKNOWN")
    elif cash["shares_atoms"] != row["confirmed_shares_atoms"]:
        reasons.append("CHAIN_CASH_SHARE_MISMATCH")
    if index is None or not command.get("condition_id"):
        reasons.append("PAYOUT_IDENTITY_UNKNOWN")
    captured = _parse_ts(command.get("captured_at"))
    if not captured or not created or captured > created:
        reasons.append("SNAPSHOT_CLOCK_UNBOUND")
    if not _coherent_finalized_pair(pair):
        reasons.append("PAYOUT_PENDING_OR_UNKNOWN")
    else:
        payout_clocks = [_parse_ts(value["observed_at"]) for value in pair]
        if any(stamp is None or stamp >= cutoff for stamp in payout_clocks):
            reasons.append("PAYOUT_CLOCK_UNBOUND")
        else:
            available.extend(payout_clocks)
    if cash["status"] == "PROVEN":
        cash_clock = _parse_ts(cash.get("available_at"))
        if cash_clock is None or cash_clock >= cutoff:
            reasons.append("CHAIN_CASH_CLOCK_UNBOUND")
        else:
            available.append(cash_clock)
    row["physical_endpoint_reason"] = reasons[0] if reasons else None
    if not reasons:
        held = next(value for value in pair if value["outcome_index"] == index)
        denominator = held["payout_denominator"]
        net_numerator = (shares_atoms * held["payout_numerator"]
                         - (cash["principal_atoms"] + cash["fee_atoms"]) * denominator)
        row.update(physical_endpoint_status="PROVEN",
                   terminal_net_payoff_numerator_atoms=net_numerator,
                   terminal_net_payoff_denominator=denominator,
                   net_markout_per_share_numerator=net_numerator,
                   net_markout_per_share_denominator=denominator * shares_atoms,
                   endpoint_available_at=max(available).isoformat())
    return row


def _certificate_header_bound(record: dict, edge_rows: list[dict]) -> bool:
    from src.decision_kernel.certificate import CertificateHeader, ParentEdge, certificate_hash_for

    try:
        values = {field.name: record[field.name] for field in fields(CertificateHeader)
                  if field.name != "parent_edges"}
        for name in ("decision_time", "source_available_at", "agent_received_at", "persisted_at",
                     "max_parent_source_available_at", "max_parent_agent_received_at", "max_parent_persisted_at"):
            values[name] = _parse_ts(values[name])
        values["parent_edges"] = tuple(ParentEdge(
            row["parent_role"], row["parent_certificate_hash"], row["parent_certificate_type"],
            bool(row["required"]),
        ) for row in edge_rows)
        return certificate_hash_for(CertificateHeader(**values)) == record["certificate_hash"]
    except (TypeError, ValueError, KeyError):
        return False


def _maker_anchor_bound(payload, economics, witness, side, decision_at) -> bool:
    from src.solve.solver import CurrentMakerFillWitness, MakerFillOutcome, maker_fill_candidate_binding_identity

    try:
        values = {field.name: witness[field.name] for field in fields(CurrentMakerFillWitness)}
        values["limit_price"] = Decimal(values["limit_price"])
        for name in ("training_cutoff_at_utc", "issued_at_utc", "valid_until_at_utc"):
            values[name] = _parse_ts(values[name])
        values["outcomes"] = tuple(MakerFillOutcome(**{
            name: Decimal(row[name]) for name in ("probability", "fill_fraction", "proceeds_per_share_usd")
        }) for row in values["outcomes"])
        rebound = CurrentMakerFillWitness(**values)
        rebound.assert_current_at(decision_at)
        binding = maker_fill_candidate_binding_identity(
            action="BUY", family_key=economics["global_family_key"],
            bin_id=economics["global_bin_id"], condition_id=payload["condition_id"],
            side=side, token_id=payload["token_id"], ledger_snapshot_id=witness["ledger_snapshot_id"],
            position_id=None, held_shares=None, asset_epoch_identity=witness["asset_epoch_identity"],
            proposal_identity=witness["proposal_identity"],
        )
        return (binding == rebound.candidate_binding_identity and witness["action"] == "BUY"
                and economics["global_jit_book_snapshot_id"] == rebound.book_snapshot_id
                and economics["global_candidate_id"] == payload["candidate_id"]
                and economics["global_token_id"] == payload["token_id"])
    except (AttributeError, ArithmeticError, KeyError, TypeError, ValueError):
        return False


def _finite_policy_number(value: object) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, TypeError, ValueError):
        return False


def _reproduced_policy_probability(
    policy: CalibrationPolicySpec,
    *,
    raw_q: object,
    p0: object,
    alpha_lead: object,
    beta: object,
    lead_bucket: object,
    side: object,
) -> float | None:
    """Reproduce the sealed correction using only its policy and certificate fields."""

    allowed_lead_buckets = (
        LEGACY_LEAD_BUCKETS
        if policy.lead_calendar_revision == LEGACY_LEAD_CALENDAR_REVISION
        else LEAD_BUCKETS
        if policy.lead_calendar_revision == LEAD_CALENDAR_REVISION
        else ()
    )
    if (
        not isinstance(lead_bucket, str)
        or lead_bucket not in allowed_lead_buckets
        or not isinstance(side, str)
        or side not in {"YES", "NO"}
        or not all(
            _finite_policy_number(value)
            for value in (raw_q, p0, alpha_lead, beta)
        )
        or not 0 <= float(raw_q) <= 1
        or not 0 <= float(p0) <= 1
    ):
        return None
    clip_lo, clip_hi = policy.probability_clip

    def apply(in_p0: float, in_raw: float, alpha: float) -> float:
        p0_clipped = min(max(in_p0, clip_lo), clip_hi)
        raw_clipped = min(max(in_raw, clip_lo), clip_hi)
        logit_delta = math.log(raw_clipped / (1.0 - raw_clipped)) - math.log(
            p0_clipped / (1.0 - p0_clipped)
        )
        logit_delta = min(max(logit_delta, -policy.logit_clip), policy.logit_clip)
        value = 1.0 / (
            1.0
            + math.exp(
                -(
                    math.log(p0_clipped / (1.0 - p0_clipped))
                    + alpha
                    + float(beta) * logit_delta
                )
            )
        )
        return min(max(value, clip_lo), clip_hi)

    if side == "NO":
        return 1.0 - apply(1.0 - float(p0), 1.0 - float(raw_q), -float(alpha_lead))
    return apply(float(p0), float(raw_q), float(alpha_lead))


def _sealed_calibration_policy(
    correction: Mapping[str, object],
    *,
    raw_q: object,
    p0: object,
    payload: Mapping[str, object],
    side: object,
    expected_lead_bucket: str,
    legacy_expected_lead_bucket: str | None = None,
    execution_mode: str,
    execution_contract: str,
    raw_probability_revision: str | None,
) -> tuple[dict[str, object] | None, str | None]:
    """Validate policy identity and correction reproduction without refitting."""

    if correction.get("applied") is not True or "calibration_policy" not in correction:
        return None, "CALIBRATION_POLICY_MISSING" if correction.get("applied") is True else None
    try:
        policy = CalibrationPolicySpec.from_payload(correction["calibration_policy"])
    except (TypeError, ValueError, KeyError):
        return None, "CALIBRATION_POLICY_INVALID"
    correction_lead_bucket = correction.get("lead_bucket")
    legacy_policy = (
        policy.input_revision == CALIBRATION_INPUT_REVISION
        and policy.metric_pooling == CALIBRATION_METRIC_POOLING
    )
    canonical_policy = (
        policy.input_revision == CANONICAL_CALIBRATION_INPUT_REVISION
        and policy.metric_pooling == CANONICAL_CALIBRATION_METRIC_POOLING
    )
    supported_lead_revisions = {
        LEGACY_LEAD_CALENDAR_REVISION,
        LEAD_CALENDAR_REVISION,
    }
    expected_policy_lead_bucket = (
        legacy_expected_lead_bucket
        if policy.lead_calendar_revision == LEGACY_LEAD_CALENDAR_REVISION
        else expected_lead_bucket
    )
    if (
        policy.algorithm_revision != CALIBRATION_ALGORITHM_REVISION
        or not (legacy_policy or canonical_policy)
        or policy.lead_calendar_revision not in supported_lead_revisions
        or policy.beta_bounds != (float(BETA_MIN), float(BETA_MAX))
        or policy.logit_clip != float(CLIP_D)
        or policy.probability_clip != (float(P_CLIP_LO), float(P_CLIP_HI))
        or not isinstance(expected_lead_bucket, str)
        or correction_lead_bucket != expected_policy_lead_bucket
        or not _finite_policy_number(correction.get("lambda"))
        or abs(policy.lambda_ - float(correction["lambda"])) > 1e-12
        or not _finite_policy_number(correction.get("beta"))
        or not policy.beta_bounds[0] <= float(correction["beta"]) <= policy.beta_bounds[1]
        or payload.get("temperature_metric", payload.get("metric")) not in ("high", "low")
        or not isinstance(correction_lead_bucket, str)
        or correction_lead_bucket not in (
            LEGACY_LEAD_BUCKETS
            if policy.lead_calendar_revision == LEGACY_LEAD_CALENDAR_REVISION
            else LEAD_BUCKETS
        )
    ):
        return None, "CALIBRATION_POLICY_INVALID"
    if canonical_policy:
        try:
            scope = CalibrationFitScope.from_payload(correction.get("fit_scope"))
            expected_scope = CalibrationFitScope(
                metric=str(payload.get("temperature_metric", payload.get("metric"))),
                execution_mode=execution_mode,
                execution_contract=execution_contract,
                raw_probability_revision=str(raw_probability_revision or ""),
            )
        except (TypeError, ValueError):
            return None, "CALIBRATION_FIT_SCOPE_INVALID"
        if scope != expected_scope:
            return None, "CALIBRATION_FIT_SCOPE_INVALID"
    try:
        reproduced = _reproduced_policy_probability(
            policy,
            raw_q=raw_q,
            p0=p0,
            alpha_lead=correction.get("alpha_lead"),
            beta=correction.get("beta"),
            lead_bucket=correction.get("lead_bucket"),
            side=side,
        )
    except (OverflowError, TypeError, ValueError):
        return None, "CALIBRATION_POLICY_INVALID"
    corrected_q = correction.get("q_corrected")
    if (
        reproduced is None
        or not _finite_policy_number(corrected_q)
        or abs(reproduced - float(corrected_q)) > 1e-12
    ):
        return None, "CALIBRATION_POLICY_INVALID"
    return policy.as_payload(), None


def _sealed_training_manifest(
    correction: Mapping[str, object], *,
    calibration_policy: dict[str, object] | None,
    decision_at: datetime | None,
    scope: CalibrationFitScope,
) -> tuple[dict[str, object] | None, str | None]:
    """Validate a decision's training commitment, not its OOS profitability.

    The canonical certificate reader authenticates the containing decision.
    A consumer must still reproduce the committed inputs and prove the frozen
    evaluation cohort, endpoints and controls before granting entry authority.
    """
    if correction.get("applied") is not True:
        return None, "CALIBRATION_NOT_APPLIED"
    if calibration_policy is None:
        return None, "CALIBRATION_POLICY_UNAVAILABLE"
    if correction.get("training_manifest") is None:
        return None, "CALIBRATION_TRAINING_MANIFEST_MISSING"
    try:
        policy = CalibrationPolicySpec.from_payload(calibration_policy)
        manifest = CanonicalTrainingManifest.from_payload(correction["training_manifest"])
        sealed_scope = CalibrationFitScope.from_payload(correction.get("fit_scope"))
    except (TypeError, ValueError, KeyError, OverflowError):
        return None, "CALIBRATION_TRAINING_MANIFEST_INVALID"
    if (
        policy.input_revision != CANONICAL_CALIBRATION_INPUT_REVISION
        or policy.metric_pooling != CANONICAL_CALIBRATION_METRIC_POOLING
        or manifest.corpus_revision != CANONICAL_CORPUS_REVISION
    ):
        return None, "CALIBRATION_TRAINING_MANIFEST_REVISION_MISMATCH"
    if sealed_scope != scope or manifest.scope_hash != scope.as_payload()["scope_hash"]:
        return None, "CALIBRATION_TRAINING_MANIFEST_SCOPE_MISMATCH"
    cutoff = _parse_ts(correction.get("training_cutoff"))
    if cutoff is None or cutoff != _parse_ts(manifest.training_cutoff):
        return None, "CALIBRATION_TRAINING_MANIFEST_CUTOFF_MISMATCH"
    if (
        decision_at is None or decision_at.tzinfo is None
        or decision_at.utcoffset() is None or cutoff > decision_at
    ):
        return None, "CALIBRATION_TRAINING_MANIFEST_DECISION_CLOCK_UNBOUND"
    if type(correction.get("n_train")) is not int or correction["n_train"] != manifest.row_count:
        return None, "CALIBRATION_TRAINING_MANIFEST_COUNT_MISMATCH"
    return manifest.as_payload(), None


def load_canonical_fit_corpus(
    world_conn: sqlite3.Connection,
    trade_conn: sqlite3.Connection,
    *,
    training_cutoff: datetime,
    city_timezone_snapshot: tuple[tuple[str, str], ...],
    forecast_conn: sqlite3.Connection | None = None,
    world_schema: str = "main",
    trade_schema: str = "main",
    forecast_schema: str = "main",
    deadline_monotonic: float | None = None,
    include_cash_proofs: bool = True,
) -> CanonicalFitCorpus:
    """Read raw inputs, actual fills and labels from their canonical owners.

    All ENTRY commands available before the cutoff remain in the denominator.
    Audit attribution, current position VWAP and requested size are never read.
    Both connections are borrowed; this function opens, writes and closes none.
    Row availability is filtered BEFORE latest/proof ranking, including the
    source rows used for economic-alias exclusion.

    ``include_cash_proofs=False`` is probability-serving only: it preserves
    command, fill, payout, raw-q and anchor validation while reporting cash and
    terminal markout as UNKNOWN.  It is never a terminal-net-payoff proof.
    """
    from src.decision_kernel.canonicalization import stable_hash
    from src.ingest.payout_observer import _coherent_finalized_pair
    from src.state.fill_dedup import canonical_trade_fact_cte, economic_trade_fact_cte

    if (world_schema not in ("main", "world")
            or trade_schema not in ("main", "trades")
            or forecast_schema not in ("main", "forecasts")):
        raise ValueError("unsupported canonical corpus schema")
    if training_cutoff.tzinfo is None or not _snapshot_is_valid(city_timezone_snapshot):
        raise ValueError("canonical corpus requires an aware cutoff and city clocks")
    if type(include_cash_proofs) is not bool:
        raise ValueError("canonical corpus include_cash_proofs must be boolean")
    cutoff = training_cutoff.astimezone(timezone.utc)
    cutoff_text = cutoff.isoformat()

    def check_deadline() -> None:
        if deadline_monotonic is not None and (
            not math.isfinite(float(deadline_monotonic))
            or time.monotonic() >= float(deadline_monotonic)
        ):
            raise TimeoutError("canonical fit corpus deadline expired")

    def records(conn, sql, params=()):
        check_deadline()
        cursor = conn.execute(sql, params)
        names = [column[0] for column in cursor.description]
        rows = cursor.fetchall()
        check_deadline()
        return [dict(zip(names, row)) for row in rows]

    def obj(value):
        try:
            value = json.loads(value) if isinstance(value, str) else value
            return value if isinstance(value, dict) else {}
        except (TypeError, ValueError):
            return {}

    def probability(value):
        if isinstance(value, bool):
            return None
        try:
            value = float(value)
            return value if math.isfinite(value) and 0 <= value <= 1 else None
        except (OverflowError, TypeError, ValueError):
            return None

    def equal(a, b):
        a, b = probability(a), probability(b)
        return a is not None and b is not None and abs(a - b) <= 1e-12

    def canonical_hash(value):
        return (
            isinstance(value, str)
            and len(value) == 64
            and all(character in "0123456789abcdef" for character in value)
        )

    forecast_table_available = False
    if forecast_conn is not None:
        try:
            forecast_table_available = bool(forecast_conn.execute(
                f"SELECT 1 FROM {forecast_schema}.sqlite_master "
                "WHERE type='table' AND name='forecast_posteriors'"
            ).fetchone())
        except sqlite3.Error:
            forecast_table_available = False

    # Cache only the immutable source row.  Eligibility clocks depend on the
    # child decision, so caching a clock-validated result would make PIT order
    # observable when two commands share one posterior.
    forecast_cache: dict[tuple[object, ...], dict | None] = {}

    def parent_for(certificate, child_edge_rows, role, certificate_type, decision_at):
        matches = [
            edge for edge in child_edge_rows
            if edge.get("parent_role") == role
        ]
        if len(matches) != 1:
            return None
        edge = matches[0]
        if edge.get("parent_certificate_type") != certificate_type:
            return None
        parent = parent_certificates.get(edge.get("parent_certificate_hash"))
        if parent is None or parent.get("certificate_type") != certificate_type:
            return None
        if parent.get("mode") != "LIVE" or parent.get("verifier_status") != "VERIFIED":
            return None
        if stable_hash(obj(parent.get("payload_json"))) != parent.get("payload_hash"):
            return None
        if not _certificate_header_bound(parent, parent_edges[parent["certificate_id"]]):
            return None
        created_at = _parse_ts(parent.get("created_at"))
        if created_at is None or created_at >= cutoff:
            return None
        if decision_at is None:
            return None
        for field in ("source_available_at", "agent_received_at", "persisted_at"):
            stamp = _parse_ts(parent.get(field))
            if stamp is None or stamp > decision_at:
                return None
        return parent

    def forecast_row_for(parent_payload, city, target, metric, decision_at):
        if not forecast_table_available:
            return None, "FORECAST_LINEAGE_CONNECTION_UNAVAILABLE"
        posterior_id = parent_payload.get("replacement_posterior_id", parent_payload.get("posterior_id"))
        identity = parent_payload.get("posterior_identity_hash")
        if posterior_id in (None, "") or not identity:
            return None, "FORECAST_LINEAGE_POSTERIOR_IDENTITY_UNBOUND"
        cache_key = (str(posterior_id), str(identity), city, target.isoformat(), metric)
        if cache_key not in forecast_cache:
            try:
                cursor = forecast_conn.execute(f"""
                SELECT posterior_id, city, target_date, temperature_metric,
                       source_available_at, computed_at, recorded_at,
                       q_json, provenance_json, posterior_identity_hash,
                       runtime_layer, training_allowed
                FROM {forecast_schema}.forecast_posteriors
                WHERE posterior_id = ? AND posterior_identity_hash = ?
                  AND city = ? AND target_date = ? AND temperature_metric = ?
                LIMIT 1
                """, (posterior_id, str(identity), city, target.isoformat(), metric)).fetchone()
            except sqlite3.Error:
                cursor = None
            if cursor is None:
                forecast_cache[cache_key] = None
            else:
                names = [column[0] for column in forecast_conn.execute(
                    f"SELECT posterior_id, city, target_date, temperature_metric, "
                    "source_available_at, computed_at, recorded_at, q_json, "
                    "provenance_json, posterior_identity_hash, runtime_layer, training_allowed "
                    f"FROM {forecast_schema}.forecast_posteriors LIMIT 0"
                ).description]
                forecast_cache[cache_key] = dict(zip(names, cursor))
        result = forecast_cache[cache_key]
        if result is None:
            return None, "FORECAST_LINEAGE_POSTERIOR_UNBOUND"
        source_at = _parse_ts(result.get("source_available_at"))
        computed_at = _parse_ts(result.get("computed_at"))
        recorded_at = _parse_ts(result.get("recorded_at"))
        if recorded_at is None and result.get("recorded_at"):
            recorded_at = _parse_ts(str(result["recorded_at"]).replace(" ", "T") + "Z")
        training_allowed = result.get("training_allowed")
        metadata_valid = (
            result.get("runtime_layer") == "live"
            and type(training_allowed) is int
            and training_allowed == 0
        )
        if not metadata_valid:
            return None, "FORECAST_LINEAGE_METADATA_UNBOUND"
        if (source_at is None or computed_at is None or source_at > decision_at
                or computed_at > decision_at or recorded_at is None
                or recorded_at > decision_at or recorded_at >= cutoff):
            return None, "FORECAST_LINEAGE_CLOCK_UNBOUND"
        result = dict(result)
        result["source_available_at"] = source_at.isoformat()
        result["computed_at"] = computed_at.isoformat()
        result["recorded_at"] = recorded_at.isoformat()
        return result, None

    def forecast_probability_revision(row):
        provenance = obj(row.get("provenance_json"))
        revision = provenance.get("probability_semantics_revision")
        if revision is None:
            fusion = provenance.get("bayes_precision_fusion")
            shape = fusion.get("current_evidence_shape") if isinstance(fusion, dict) else None
            revision = shape.get("semantics_revision") if isinstance(shape, dict) else None
        return revision if isinstance(revision, str) and revision.strip() else None

    def forecast_lineage_for(
        certificate, child_edge_rows, payload, economics, raw,
        raw_probability_revision, side, city, target, metric, decision_at,
    ):
        """Return sealed raw forecast provenance without changing acceptance."""

        if forecast_conn is None:
            return None, "FORECAST_LINEAGE_CONNECTION_UNAVAILABLE"
        forecast_parent = parent_for(
            certificate, child_edge_rows, "forecast_authority",
            "ForecastAuthorityCertificate", decision_at,
        )
        if forecast_parent is None:
            return None, "FORECAST_LINEAGE_PARENT_UNBOUND"
        forecast_payload = obj(forecast_parent.get("payload_json"))
        parent_metric = forecast_payload.get("metric", forecast_payload.get("temperature_metric"))
        if (forecast_payload.get("city") != city
                or forecast_payload.get("target_date") != target.isoformat()
                or parent_metric != metric):
            return None, "FORECAST_LINEAGE_IDENTITY_UNBOUND"
        posterior_id = forecast_payload.get(
            "replacement_posterior_id", forecast_payload.get("posterior_id")
        )
        posterior_identity = forecast_payload.get("posterior_identity_hash")
        if posterior_id in (None, "") or not isinstance(posterior_identity, str) or not posterior_identity:
            return None, "FORECAST_LINEAGE_POSTERIOR_IDENTITY_UNBOUND"
        child_identity = payload.get("posterior_identity_hash")
        if (any(value not in (None, "") and str(value) != str(posterior_id)
                for value in (payload.get("posterior_id"), economics.get("global_posterior_id")))
                or (child_identity not in (None, "")
                    and str(child_identity) != str(posterior_identity))):
            return None, "FORECAST_LINEAGE_IDENTITY_UNBOUND"
        row, row_reason = forecast_row_for(forecast_payload, city, target, metric, decision_at)
        if row is None:
            return None, row_reason
        bin_label = payload.get("bin_label")
        parent_q_map = forecast_payload.get("replacement_q")
        if not isinstance(bin_label, str) or not bin_label or not isinstance(parent_q_map, dict):
            return None, "FORECAST_LINEAGE_BIN_UNBOUND"
        source_q = obj(row.get("q_json")).get(bin_label)
        parent_q = parent_q_map.get(bin_label)
        source_q_value = probability(source_q)
        parent_q_value = probability(parent_q)
        expected_raw = source_q_value if side == "YES" else (
            1.0 - source_q_value if source_q_value is not None else None
        )
        if (source_q_value is None or parent_q_value is None
                or not equal(source_q_value, parent_q_value)
                or raw is None or not equal(expected_raw, raw)):
            return None, "FORECAST_LINEAGE_Q_UNBOUND"
        source_revision = forecast_probability_revision(row)
        if (source_revision is None or not isinstance(raw_probability_revision, str)
                or not raw_probability_revision.strip()
                or source_revision != raw_probability_revision):
            return None, "FORECAST_LINEAGE_REVISION_UNBOUND"
        provenance = obj(row.get("provenance_json"))
        fusion = provenance.get("bayes_precision_fusion")
        shape = fusion.get("current_evidence_shape") if isinstance(fusion, dict) else None
        if not isinstance(shape, dict):
            return None, "FORECAST_LINEAGE_SHAPE_UNBOUND"
        shape_revision = shape.get("semantics_revision")
        if (not isinstance(shape_revision, str) or not shape_revision.strip()
                or shape_revision != source_revision
                or shape_revision != raw_probability_revision):
            return None, "FORECAST_LINEAGE_REVISION_UNBOUND"
        snapshot_id = shape.get("snapshot_id")
        shape_hash = shape.get("shape_hash")
        if (isinstance(snapshot_id, bool) or type(snapshot_id) is not int or snapshot_id <= 0
                or not isinstance(shape_hash, str) or not shape_hash):
            return None, "FORECAST_LINEAGE_SHAPE_UNBOUND"
        source_at = _parse_ts(row.get("source_available_at"))
        computed_at = _parse_ts(row.get("computed_at"))
        recorded_at = _parse_ts(row.get("recorded_at"))
        if source_at is None or computed_at is None or recorded_at is None:
            return None, "FORECAST_LINEAGE_CLOCK_UNBOUND"
        return {
            "forecast_certificate_hash": forecast_parent.get("certificate_hash"),
            "posterior_id": posterior_id,
            "posterior_identity_hash": posterior_identity,
            "source_available_at": source_at.isoformat(),
            "computed_at": computed_at.isoformat(),
            "recorded_at": recorded_at.isoformat(),
            "ensemble_snapshot_id": snapshot_id,
            "current_evidence_shape_hash": shape_hash,
            "probability_revision": source_revision,
        }, None

    def legacy_replacement_input(certificate, child_edge_rows, payload, economics,
                                 correction, side, city, target, metric, decision_at):
        """Bind the old replacement path to its exact forecast parent and row."""
        q_source = payload.get("_edli_q_source") or payload.get("q_source")
        if correction.get("applied") in (True, False) or q_source != "replacement_0_1":
            return None, None, None
        forecast_parent = parent_for(
            certificate, child_edge_rows, "forecast_authority",
            "ForecastAuthorityCertificate", decision_at,
        )
        if forecast_parent is None:
            return None, None, "LEGACY_FORECAST_AUTHORITY_UNBOUND"
        forecast_payload = obj(forecast_parent.get("payload_json"))
        if (forecast_payload.get("city") != city
                or forecast_payload.get("target_date") != target.isoformat()
                or forecast_payload.get("metric", forecast_payload.get("temperature_metric")) != metric):
            return None, None, "LEGACY_FORECAST_IDENTITY_UNBOUND"
        parent_posterior_id = forecast_payload.get(
            "replacement_posterior_id", forecast_payload.get("posterior_id")
        )
        child_posterior_id = payload.get("posterior_id", economics.get("global_posterior_id"))
        if (child_posterior_id not in (None, "")
                and str(child_posterior_id) != str(parent_posterior_id)):
            return None, None, "LEGACY_FORECAST_IDENTITY_UNBOUND"
        child_identity = payload.get("posterior_identity_hash")
        if (child_identity not in (None, "")
                and str(child_identity) != str(forecast_payload.get("posterior_identity_hash"))):
            return None, None, "LEGACY_FORECAST_IDENTITY_UNBOUND"
        row, _ = forecast_row_for(forecast_payload, city, target, metric, decision_at)
        if row is None:
            return None, None, "LEGACY_FORECAST_POSTERIOR_UNBOUND"
        try:
            q_map = obj(row.get("q_json"))
            parent_q_map = forecast_payload.get("replacement_q")
            parent_q = parent_q_map.get(payload.get("bin_label")) if isinstance(parent_q_map, dict) else None
            source_q = q_map.get(payload.get("bin_label"))
        except (AttributeError, TypeError):
            return None, None, "LEGACY_FORECAST_BIN_UNBOUND"
        if not payload.get("bin_label") or not equal(source_q, parent_q):
            return None, None, "LEGACY_FORECAST_BIN_UNBOUND"
        source_q_value = probability(source_q)
        raw = source_q_value if side == "YES" else (1.0 - source_q_value if source_q_value is not None else None)
        if raw is None:
            return None, None, "LEGACY_FORECAST_BIN_UNBOUND"
        provenance = obj(row.get("provenance_json"))
        child_revision = payload.get("probability_semantics_revision")
        child_revision = child_revision if isinstance(child_revision, str) and child_revision.strip() else None
        source_revision = provenance.get("probability_semantics_revision")
        if source_revision is None:
            shape = provenance.get("bayes_precision_fusion")
            shape = shape.get("current_evidence_shape") if isinstance(shape, dict) else None
            source_revision = shape.get("semantics_revision") if isinstance(shape, dict) else None
        source_revision = source_revision if isinstance(source_revision, str) and source_revision.strip() else None
        if (child_revision is not None and source_revision is not None
                and child_revision != source_revision):
            return None, None, "LEGACY_PROBABILITY_REVISION_UNBOUND"
        raw_revision = child_revision if child_revision == source_revision else None
        return raw, raw_revision, None

    def legacy_anchor(certificate, child_edge_rows, payload, economics, command,
                      side, decision_at):
        required = {
            role: parent_for(certificate, child_edge_rows, role, kind, decision_at)
            for role, kind in (
                ("quote_feasibility", "QuoteFeasibilityCertificate"),
                ("executable_snapshot", "ExecutableSnapshotCertificate"),
                ("candidate", "CandidateEvidenceCertificate"),
                ("cost_model", "CostModelCertificate"),
            )
        }
        if any(value is None for value in required.values()):
            return None, None, "LEGACY_ANCHOR_PARENT_UNBOUND"
        parent_payloads = {role: obj(cert.get("payload_json")) for role, cert in required.items()}
        quote = parent_payloads["quote_feasibility"]
        snapshot = parent_payloads["executable_snapshot"]
        candidate = parent_payloads["candidate"]
        cost = parent_payloads["cost_model"]
        token = command["token_id"]
        condition = command["condition_id"]
        identity_pairs = (
            (quote.get("condition_id"), condition), (quote.get("token_id"), token),
            (quote.get("selected_token_id"), token), (quote.get("quote_book_condition_id"), condition),
            (quote.get("quote_book_token_id"), token), (snapshot.get("condition_id"), condition),
            (snapshot.get("token_id"), token), (candidate.get("condition_id"), condition),
            (candidate.get("selected_token_id"), token), (cost.get("condition_id"), condition),
            (cost.get("token_id"), token),
        )
        if any(left in (None, "") or str(left) != str(right) for left, right in identity_pairs):
            return None, None, "LEGACY_ANCHOR_IDENTITY_UNBOUND"
        if (payload.get("executable_snapshot_id") != snapshot.get("selected_snapshot_id")
                or quote.get("quote_depth_hash") != snapshot.get("orderbook_hash")
                or cost.get("cost_source") != "native_orderbook_ask"
                or cost.get("quote_source_kind") != "executable_market_snapshot_native_book"):
            return None, None, "LEGACY_ANCHOR_BOOK_UNBOUND"
        captured_at = _parse_ts(snapshot.get("captured_at"))
        if captured_at is None or decision_at is None or captured_at > decision_at:
            return None, None, "LEGACY_ANCHOR_BOOK_UNBOUND"
        proof = str(payload.get("proof_execution_mode_intent") or "").strip().upper()
        global_mode = str(economics.get("global_execution_mode") or "").strip().upper()
        command_type = str(command.get("command_order_type") or "").strip().upper()
        try:
            command_post_only = int(command.get("command_post_only"))
        except (TypeError, ValueError):
            command_post_only = None
        if proof == "TAKER":
            mode = "TAKER_LIMIT"
            if global_mode and global_mode != mode:
                return None, None, "LEGACY_ANCHOR_MODE_UNBOUND"
            if command_type not in {"FOK", "FAK"} or command_post_only != 0:
                return None, None, "LEGACY_ANCHOR_MODE_UNBOUND"
            if (quote.get("cost_source") != "native_orderbook_ask"
                    or quote.get("quote_source_kind") != "executable_market_snapshot_native_book"):
                return None, None, "LEGACY_ANCHOR_QUOTE_UNBOUND"
            p0 = probability(quote.get("best_ask"))
        elif proof == "MAKER":
            mode = "MAKER_REST"
            if global_mode and global_mode != mode:
                return None, None, "LEGACY_ANCHOR_MODE_UNBOUND"
            if command_type not in {"GTC", "GTD"} or command_post_only != 1:
                return None, None, "LEGACY_ANCHOR_MODE_UNBOUND"
            p0 = probability(payload.get("proof_maker_limit_price"))
        else:
            return None, None, "LEGACY_ANCHOR_MODE_UNBOUND"
        if p0 is None:
            return None, None, "LEGACY_ANCHOR_PRICE_UNBOUND"
        return mode, p0, None

    trade_tables = {
        str(row[0]).strip()
        for row in trade_conn.execute(
            f"SELECT name FROM {trade_schema}.sqlite_master WHERE type='table'"
        ).fetchall()
    }
    envelope_join = ""
    envelope_columns = "NULL AS command_order_type, NULL AS command_post_only, NULL AS envelope_id"
    if "venue_submission_envelopes" in trade_tables:
        envelope_join = (
            f"LEFT JOIN {trade_schema}.venue_submission_envelopes e "
            "ON e.envelope_id=c.envelope_id"
        )
        envelope_columns = "e.order_type AS command_order_type, e.post_only AS command_post_only, e.envelope_id"
    commands = records(trade_conn, f"""
        SELECT c.command_id, c.token_id, c.created_at, c.venue_order_id, c.snapshot_id, c.side AS order_side,
               {envelope_columns},
               s.condition_id, s.yes_token_id, s.no_token_id,
               s.selected_outcome_token_id, s.orderbook_top_ask,
               s.raw_orderbook_hash, s.captured_at, s.token_map_json
        FROM {trade_schema}.venue_commands c
        {envelope_join}
        LEFT JOIN {trade_schema}.executable_market_snapshots s ON s.snapshot_id=c.snapshot_id
        WHERE c.intent_kind='ENTRY' AND julianday(c.created_at)<julianday(?)
        ORDER BY c.command_id
    """, (cutoff_text,))
    links: dict[str, set[str]] = defaultdict(set)
    for row in records(trade_conn, f"""
        SELECT command_id, decision_certificate_hash
        FROM {trade_schema}.position_decision_attribution
        WHERE intent_kind='ENTRY' AND julianday(created_at)<julianday(?)
    """, (cutoff_text,)):
        links[row["command_id"]].add(row["decision_certificate_hash"])
    certificates = {}
    edges = defaultdict(list)
    hashes = sorted({h for command in commands for h in links[command["command_id"]] if h})
    for start in range(0, len(hashes), 400):
        check_deadline()
        keys = hashes[start:start + 400]
        marks = ",".join("?" for _ in keys)
        for row in records(world_conn, f"""
            SELECT * FROM {world_schema}.decision_certificates
            WHERE certificate_hash IN ({marks})
        """, keys):
            certificates[row["certificate_hash"]] = row
        for row in records(world_conn, f"""
            SELECT e.* FROM {world_schema}.decision_certificate_edges e
            JOIN {world_schema}.decision_certificates c ON c.certificate_id=e.child_certificate_id
            WHERE c.certificate_hash IN ({marks}) ORDER BY e.rowid
        """, keys):
            edges[row["child_certificate_id"]].append(row)

    # Parse each child payload once for parent selection and the later command
    # validation loop. Parent/PIT validation remains per child and uncached.
    child_payloads = {
        certificate["certificate_id"]: obj(certificate.get("payload_json"))
        for certificate in certificates.values()
    }
    legacy_parent_roles = frozenset({
        "quote_feasibility", "executable_snapshot", "candidate", "cost_model",
    })
    legacy_children = {
        child_id
        for child_id, payload in child_payloads.items()
        if (
            obj(obj(payload.get("qkernel_execution_economics")).get(
                "market_anchored_correction"
            )).get("applied") not in (True, False)
            and (payload.get("_edli_q_source") or payload.get("q_source"))
            == "replacement_0_1"
        )
    }
    # Header reconstruction below consumes every child edge. Parent payloads
    # are narrower: forecast authority is always used by raw lineage; legacy
    # anchors consume four additional roles only for an actual legacy child.
    parent_certificates = {}
    parent_edges = defaultdict(list)
    parent_hashes = sorted({
        row["parent_certificate_hash"]
        for child_id, rows in edges.items()
        for row in rows
        if (
            row.get("parent_certificate_hash")
            and (
                row.get("parent_role") == "forecast_authority"
                or (
                    child_id in legacy_children
                    and row.get("parent_role") in legacy_parent_roles
                )
            )
        )
    })
    for start in range(0, len(parent_hashes), 400):
        check_deadline()
        keys = parent_hashes[start:start + 400]
        marks = ",".join("?" for _ in keys)
        for row in records(world_conn, f"""
            SELECT * FROM {world_schema}.decision_certificates
            WHERE certificate_hash IN ({marks})
        """, keys):
            parent_certificates[row["certificate_hash"]] = row
    parent_ids = [row["certificate_id"] for row in parent_certificates.values()]
    for start in range(0, len(parent_ids), 400):
        check_deadline()
        keys = parent_ids[start:start + 400]
        marks = ",".join("?" for _ in keys)
        for row in records(world_conn, f"""
            SELECT e.* FROM {world_schema}.decision_certificate_edges e
            WHERE e.child_certificate_id IN ({marks}) ORDER BY e.rowid
        """, keys):
            parent_edges[row["child_certificate_id"]].append(row)

    fact_scope = "WHERE julianday(fact.observed_at)<julianday(?) AND julianday(fact.ingested_at)<julianday(?)"
    source_scope = "AND julianday(source_fact.observed_at)<julianday(?) AND julianday(source_fact.ingested_at)<julianday(?)"
    canonical = canonical_trade_fact_cte(source_schema=trade_schema, source_clause_sql=fact_scope)
    economic = economic_trade_fact_cte(source_schema=trade_schema, source_clause_sql=source_scope)
    fills: dict[str, list[dict]] = defaultdict(list)
    for row in records(trade_conn, f"""WITH {canonical}, {economic}
        SELECT * FROM economic_trade_fact WHERE UPPER(state)='CONFIRMED'
    """, (cutoff_text,) * 4):
        fills[row["command_id"]].append(row)

    payouts: dict[str, list[dict]] = defaultdict(list)
    condition_ids = sorted({
        str(command["condition_id"])
        for command in commands
        if command.get("condition_id") not in (None, "")
    })
    for start in range(0, len(condition_ids), 400):
        check_deadline()
        keys = condition_ids[start:start + 400]
        marks = ",".join("?" for _ in keys)
        for row in records(trade_conn, f"""
            WITH ranked AS (
              SELECT *, ROW_NUMBER() OVER(PARTITION BY condition_id,outcome_index ORDER BY id DESC) AS rn
              FROM {trade_schema}.payout_observations
              WHERE condition_id IN ({marks})
                AND julianday(observed_at)<julianday(?) AND outcome_index IN (0,1)
            ) SELECT * FROM ranked WHERE rn=1
        """, (*keys, cutoff_text)):
            payouts[row["condition_id"]].append(row)

    available_cash_transactions = None
    if include_cash_proofs and "venue_fill_cash_facts" in trade_tables:
        available_cash_transactions = {
            str(row[0]).lower() for row in trade_conn.execute(f"""
                SELECT DISTINCT tx_hash FROM {trade_schema}.venue_fill_cash_facts
                WHERE chain_id=137 AND status='PROVEN' AND julianday(observed_at)<julianday(?)
            """, (cutoff_text,))
        }
    accounting = {}
    for command in commands:
        check_deadline()
        accounting[command["command_id"]] = _command_accounting(
            command, fills[command["command_id"]], payouts[command["condition_id"]],
            conn=trade_conn, cutoff=cutoff, schema=trade_schema,
            available_cash_transactions=available_cash_transactions,
            include_cash_proofs=include_cash_proofs,
        )
        check_deadline()
    unknown: Counter[str] = Counter()
    accepted = []
    for command in commands:
        check_deadline()
        command_id = command["command_id"]
        accounting_row = accounting[command_id]
        reasons = []
        if command["order_side"] != "BUY":
            unknown["ENTRY_NOT_BUY"] += 1
            accounting_row["calibration_evidence_reason"] = "ENTRY_NOT_BUY"
            continue
        hs = links[command_id]
        certificate = certificates.get(next(iter(hs))) if len(hs) == 1 else None
        if certificate is None:
            unknown["CERTIFICATE_LINK_MISSING_OR_AMBIGUOUS"] += 1
            accounting_row["calibration_evidence_reason"] = "CERTIFICATE_LINK_MISSING_OR_AMBIGUOUS"
            continue
        payload = child_payloads.get(certificate["certificate_id"], {})
        economics = obj(payload.get("qkernel_execution_economics"))
        correction = obj(economics.get("market_anchored_correction"))
        token = command["token_id"]
        side = ("YES" if token == command["yes_token_id"] else "NO"
                if token == command["no_token_id"] else None)
        if (not side or command["yes_token_id"] == command["no_token_id"]
                or payload.get("token_id") != token
                or payload.get("direction") != ("buy_yes" if side == "YES" else "buy_no")
                or command["selected_outcome_token_id"] != token
                or payload.get("condition_id") != command["condition_id"]
                or stable_hash(payload) != certificate["payload_hash"]
                or certificate["certificate_type"] != "ActionableTradeCertificate"
                or certificate["mode"] != "LIVE" or certificate["verifier_status"] != "VERIFIED"):
            reasons.append("CERTIFICATE_IDENTITY_UNBOUND")
        if not _certificate_header_bound(certificate, edges[certificate["certificate_id"]]):
            reasons.append("CERTIFICATE_HEADER_HASH_UNBOUND")
        outcome_index = accounting_row["outcome_index"]
        if outcome_index is None:
            reasons.append("TOKEN_OUTCOME_INDEX_UNBOUND")
        decision_at = _parse_ts(certificate["decision_time"])
        persisted_at = _parse_ts(certificate["persisted_at"])
        if not decision_at or not persisted_at or not decision_at <= persisted_at < cutoff:
            reasons.append("CERTIFICATE_CLOCK_UNBOUND")
        for field in ("source_available_at", "max_parent_source_available_at", "max_parent_persisted_at"):
            stamp = _parse_ts(certificate[field])
            if not stamp or not decision_at or stamp > decision_at:
                reasons.append("CERTIFICATE_PARENT_CLOCK_UNBOUND")
                break
        if not equal(economics.get("payoff_q_point"), payload.get("q_live")):
            reasons.append("ACTING_Q_UNBOUND")
        raw = None
        raw_source = None
        raw_probability_revision = payload.get("probability_semantics_revision")
        if not isinstance(raw_probability_revision, str) or not raw_probability_revision.strip():
            raw_probability_revision = None
        if correction.get("applied") is True and equal(correction.get("q_corrected"), payload.get("q_live")):
            raw = probability(correction.get("q_raw"))
            raw_source = "SEALED_CORRECTION_INPUT"
        elif correction.get("applied") is False:
            raw = probability(economics.get("payoff_q_point"))
            raw_source = "EXPLICIT_UNCORRECTED_INPUT"
        elif (payload.get("_edli_q_source") or payload.get("q_source")) == "replacement_0_1":
            city = payload.get("city")
            target = _parse_date(payload.get("target_date"))
            metric = payload.get("temperature_metric", payload.get("metric"))
            if target is not None:
                raw, raw_probability_revision, legacy_reason = legacy_replacement_input(
                    certificate, edges[certificate["certificate_id"]], payload, economics,
                    correction, side, city, target, metric, decision_at,
                )
                if raw is not None:
                    raw_source = "LEGACY_REPLACEMENT_POSTERIOR_INPUT"
                elif legacy_reason:
                    reasons.append(legacy_reason)
        if raw is None:
            reasons.append("RAW_INPUT_UNBOUND")
        mode = economics.get("global_execution_mode")
        p0 = None
        if mode == "MAKER_REST":
            witness = obj(economics.get("global_maker_fill_witness"))
            fields = ("witness_identity", "candidate_binding_identity", "asset_epoch_identity",
                      "proposal_identity", "book_snapshot_id", "book_hash", "outcomes")
            if (all(witness.get(field) for field in fields)
                    and equal(witness.get("limit_price"), economics.get("global_limit_price"))
                    and economics.get("global_jit_book_hash") == witness.get("book_hash")
                    and _maker_anchor_bound(payload, economics, witness, side, decision_at)):
                p0 = probability(witness.get("limit_price"))
        elif mode == "TAKER_LIMIT":
            if (economics.get("decision_p0_source")
                    and economics.get("global_book_hash") == command["raw_orderbook_hash"]
                    and equal(economics.get("decision_p0"), command["orderbook_top_ask"])
                    and _parse_ts(command["captured_at"]) is not None
                    and decision_at is not None and _parse_ts(command["captured_at"]) <= decision_at
                    and economics.get("global_token_id") == token
                    and economics.get("global_candidate_id") == payload.get("candidate_id")
                    and economics.get("global_jit_execution_curve_identity")) :
                p0 = probability(economics.get("decision_p0"))
        if (raw_source == "LEGACY_REPLACEMENT_POSTERIOR_INPUT"
                and decision_at is not None):
            legacy_mode, legacy_p0, legacy_reason = legacy_anchor(
                certificate, edges[certificate["certificate_id"]], payload, economics,
                command, side, decision_at,
            )
            if legacy_reason:
                reasons.append(legacy_reason)
            else:
                mode, p0 = legacy_mode, legacy_p0
        execution_contract, contract_reason = _execution_contract_for(
            mode, command.get("command_order_type"), command.get("command_post_only")
        )
        if contract_reason:
            reasons.append(contract_reason)
        capture = obj(economics.get("raw_calibration_input"))
        typed_exact_reason = None
        if capture:
            captured_identity = (
                capture.get("schema_version") == 1
                and capture.get("capture_basis") == "GLOBAL_CERTIFICATE_INPUT"
                and capture.get("p0_basis") == "GROSS_NATIVE_TOKEN_PRICE"
                and capture.get("condition_id") == command["condition_id"]
                and capture.get("token_id") == token and capture.get("side") == side
                and capture.get("candidate_id") == payload.get("candidate_id")
                and capture.get("candidate_id") == economics.get("global_candidate_id")
                and capture.get("family_key") == economics.get("global_family_key")
                and capture.get("bin_id") == economics.get("global_bin_id")
                and capture.get("probability_witness_identity") == economics.get("global_probability_witness_identity")
                and bool(capture.get("probability_witness_identity"))
                and capture.get("sample_hash") == economics.get("sample_hash")
                and bool(capture.get("sample_hash"))
                and bool(capture.get("economic_curve_identity"))
                and capture.get("execution_mode") == mode
                and capture.get("correction_applied") is correction.get("applied")
                and equal(capture.get("raw_q_held"), raw)
            )
            if mode == "MAKER_REST":
                captured_identity = (captured_identity and p0 is not None
                    and capture.get("maker_fill_witness_identity") == witness.get("witness_identity")
                    and capture.get("maker_proposal_identity") == witness.get("proposal_identity")
                    and capture.get("economic_curve_identity") == witness.get("proposal_identity")
                    and capture.get("book_snapshot_id") == witness.get("book_snapshot_id")
                    and capture.get("book_hash") == witness.get("book_hash")
                    and equal(capture.get("p0_held"), p0))
            elif mode == "TAKER_LIMIT":
                captured_identity = (captured_identity
                    and capture.get("book_snapshot_id") == economics.get("decision_p0_source")
                    and bool(capture.get("book_snapshot_id"))
                    and capture.get("book_hash") == economics.get("global_book_hash")
                    and bool(capture.get("book_hash"))
                    and equal(capture.get("p0_held"), economics.get("decision_p0")))
            else:
                captured_identity = False
            if captured_identity:
                p0 = probability(capture.get("p0_held"))
            else:
                p0 = None
            exact_fields = {
                key for key in capture
                if isinstance(key, str) and key.startswith("exact_")
            }
            typed_exact_marker = (
                "probability_input_kind" in capture or bool(exact_fields)
            )
            if typed_exact_marker:
                complete = (
                    capture.get("probability_input_kind") == "TYPED_EXACT_PAYOFF"
                    and exact_fields == {
                        "exact_payoff_content_identity",
                        "exact_payoff_witness_identity",
                        "exact_payoff",
                    }
                    and canonical_hash(capture.get("exact_payoff_content_identity"))
                    and canonical_hash(capture.get("exact_payoff_witness_identity"))
                    and type(capture.get("exact_payoff")) is int
                    and capture.get("exact_payoff") in (0, 1)
                    and correction.get("applied") is False
                    and captured_identity
                    and equal(capture.get("exact_payoff"), capture.get("raw_q_held"))
                    and equal(capture.get("exact_payoff"), raw)
                )
                typed_exact_reason = (
                    "TYPED_EXACT_PAYOFF_NOT_STATISTICAL_INPUT"
                    if complete
                    else "INVALID_TYPED_EXACT_PAYOFF_CAPTURE"
                )
        if typed_exact_reason:
            # Exact payoff witnesses are evidence for the known token only;
            # they must never become Bernoulli fit rows.  Put this first so
            # command accounting records the typed classification even if a
            # separate endpoint proof is also unavailable.
            reasons.insert(0, typed_exact_reason)
        if p0 is None or (correction.get("applied") is True and not equal(correction.get("p0"), p0)):
            reasons.append("DECISION_ANCHOR_UNBOUND")
        pair = payouts[command["condition_id"]]
        if not _coherent_finalized_pair(pair):
            reasons.append("FINALIZED_PAYOUT_UNBOUND")
        elif any(row["payout_numerator"] not in (0, row["payout_denominator"]) for row in pair):
            reasons.append("FRACTIONAL_PAYOUT_UNSUPPORTED")
        shares = 0.0
        fill_available_at = None
        for fill in fills[command_id]:
            check_deadline()
            observed = _parse_ts(fill["observed_at"])
            # ingested_at is SQLite datetime('now'), explicitly UTC in its owner schema.
            ingested = _parse_ts(str(fill["ingested_at"]).replace(" ", "T") + "Z") if "+" not in str(fill["ingested_at"]) and not str(fill["ingested_at"]).endswith("Z") else _parse_ts(fill["ingested_at"])
            executed = _parse_ts(fill["execution_ts"])
            try:
                size = float(fill["filled_size"])
            except (TypeError, ValueError):
                size = float("nan")
            if (not math.isfinite(size) or size <= 0 or not observed or not ingested or not executed
                    or not persisted_at or not persisted_at <= executed <= observed < cutoff
                    or not executed <= ingested < cutoff
                    or not fill["venue_order_id"] or fill["venue_order_id"] != command["venue_order_id"]):
                reasons.append("CONFIRMED_FILL_CLOCK_OR_IDENTITY_UNBOUND")
                break
            shares += size
            fill_available_at = max(filter(None, (fill_available_at, observed, ingested)))
        if shares <= 0:
            reasons.append("CONFIRMED_FILL_MISSING")
        city, target = payload.get("city"), _parse_date(payload.get("target_date"))
        metric = payload.get("temperature_metric", payload.get("metric"))
        local_date = _city_local_target_date(decision_at, city, city_timezone_snapshot) if decision_at else None
        if not city or target is None or local_date is None or metric not in ("high", "low"):
            reasons.append("FAMILY_OR_LOCAL_CLOCK_UNBOUND")
        elif lead_bucket_of(local_date, target) is None:
            reasons.append("FIT_LEAD_UNSUPPORTED")
        if reasons:
            # One primary reason per command keeps the unknown denominator additive.
            unknown[reasons[0]] += 1
            accounting_row["calibration_evidence_reason"] = reasons[0]
            continue
        calibration_policy_payload, calibration_policy_reason = _sealed_calibration_policy(
            correction,
            raw_q=raw,
            p0=p0,
            payload=payload,
            side=side,
            expected_lead_bucket=lead_bucket_of(local_date, target),
            legacy_expected_lead_bucket=legacy_lead_bucket_of(local_date, target),
            execution_mode=mode,
            execution_contract=execution_contract,
            raw_probability_revision=raw_probability_revision,
        )
        raw_forecast_lineage, raw_forecast_lineage_reason = forecast_lineage_for(
            certificate, edges[certificate["certificate_id"]], payload, economics, raw,
            raw_probability_revision, side, city, target, metric, decision_at,
        )
        held = next(row for row in pair if row["outcome_index"] == outcome_index)
        accounting_row["calibration_policy"] = calibration_policy_payload
        accounting_row["calibration_policy_reason"] = calibration_policy_reason
        training_manifest, training_manifest_reason = (None, "CALIBRATION_FIT_SCOPE_UNAVAILABLE")
        if raw_probability_revision:
            training_manifest, training_manifest_reason = _sealed_training_manifest(
                correction,
                calibration_policy=calibration_policy_payload,
                decision_at=decision_at,
                scope=CalibrationFitScope(
                    metric=metric, execution_mode=mode,
                    execution_contract=execution_contract,
                    raw_probability_revision=raw_probability_revision,
                ),
            )
        accounting_row["calibration_training_manifest"] = training_manifest
        accounting_row["calibration_training_manifest_reason"] = training_manifest_reason
        cash = accounting_row["chain_cash"]
        if include_cash_proofs and cash["status"] != "PROVEN":
            from src.state.fill_cash_reader import read_command_fill_cash

            # Preserve the existing fit reader's more specific UNKNOWN reason.
            cash = read_command_fill_cash(trade_conn, command=command, fills=fills[command_id],
                                          cutoff=cutoff, schema=trade_schema)
        terminal_net_atoms = None
        if cash["status"] == "PROVEN":
            terminal_net_atoms = (cash["shares_atoms"] * int(held["payout_numerator"] == held["payout_denominator"])
                                  + cash["collateral_delta_atoms"])
        accepted.append(dict(
            command_id=command_id, certificate_hash=certificate["certificate_hash"],
            condition_id=command["condition_id"], token_id=token, side=side,
            city=city, target_date=target.isoformat(), metric=metric,
            event_key=(city, target.isoformat(), metric), execution_mode=mode,
            execution_contract=execution_contract,
            decision_time=decision_at.isoformat(), lead_days=(target-local_date).days,
            lead_bucket=lead_bucket_of(local_date, target), q_raw=raw, raw_source=raw_source,
            raw_probability_revision=raw_probability_revision,
            raw_forecast_lineage=raw_forecast_lineage,
            raw_forecast_lineage_reason=raw_forecast_lineage_reason,
            calibration_policy=calibration_policy_payload,
            calibration_policy_reason=calibration_policy_reason,
            calibration_training_manifest=training_manifest,
            calibration_training_manifest_reason=training_manifest_reason,
            acting_q=probability(payload.get("q_live")), p0=p0, confirmed_shares=shares,
            fill_available_at=fill_available_at.isoformat(),
            payout=held["payout_numerator"] / held["payout_denominator"],
            payout_available_at=max(_parse_ts(row["observed_at"]) for row in pair).isoformat(),
            fill_proof_tier="CLOB_CONFIRMED", payout_proof_tier="FINALIZED_CHAIN_PAIR",
            chain_cash=cash, terminal_net_payoff_atoms=terminal_net_atoms,
        ))
    return CanonicalFitCorpus(
        tuple(accepted), dict(unknown), len(commands), cutoff_text,
        command_accounting=tuple(accounting.values()),
    )


def load_fit_rows(
    conn: sqlite3.Connection, *, training_cutoff: datetime,
        city_timezone_snapshot: tuple[tuple[str, str], ...] | None = None,
        schema_alias: str = "main",
) -> list[FitRow]:
    """Extract settled training rows whose outcome preceded ``training_cutoff``.

    Predicates mirror ``load_rows`` in scripts/calibrator_walkforward_report.py
    (q_in_bin / market_in_bin_prob / settled_in_bin / direction all NOT NULL).
    Both the strict ``settled_at < training_cutoff`` condition and the
    ``graded_at <= training_cutoff`` condition are applied after parsing. A
    missing ``settled_at`` may use a valid grade time as its conservative
    fallback; an invalid or missing grade can never make a row eligible.

    A claim is (city, target_date, temperature_metric, traded_bin_label,
    direction). The live table carries roughly one row per claim, but the
    certificate surface underneath it is re-certified many times over (a
    claim re-certifying does not produce a new independent outcome), so an
    unweighted fit over-counts whichever claims happen to re-certify most.
    Each returned row is weighted 1/(claim count within this same
    cutoff-filtered set) so a claim contributes exactly one row's worth of
    evidence to the fit regardless of how many certified rows it produced.
    """

    table = _FIT_TABLE_BY_ALIAS.get(schema_alias)
    if table is None:
        raise ValueError(f"unsupported calibration schema alias: {schema_alias!r}")
    rows = conn.execute(
        f"""
        SELECT q_in_bin, market_in_bin_prob, settled_in_bin,
               decision_posterior_computed_at, target_date, settled_at, graded_at,
               city, temperature_metric, traded_bin_label, direction
        FROM {table}
        WHERE q_in_bin IS NOT NULL
          AND market_in_bin_prob IS NOT NULL
          AND settled_in_bin IS NOT NULL
          AND direction IS NOT NULL
        """
    ).fetchall()

    valid: list[tuple[dict, str, tuple, int]] = []
    for row in rows:
        record = dict(row) if not isinstance(row, dict) else row
        graded_at = _parse_ts(record.get("graded_at"))
        if graded_at is None or graded_at > training_cutoff:
            continue
        if record.get("settled_at") is None:
            settled_at = graded_at
        else:
            settled_at = _parse_ts(record.get("settled_at"))
        if settled_at is None or settled_at >= training_cutoff:
            continue
        decision_at = _parse_ts(record.get("decision_posterior_computed_at"))
        target_date = _parse_date(record.get("target_date"))
        if decision_at is None or target_date is None:
            continue
        decision_date = (
            _city_local_target_date(
                decision_at,
                record.get("city"),
                city_timezone_snapshot,
            )
            if city_timezone_snapshot is not None
            else None
        )
        if decision_date is None:
            continue
        lead_bucket = lead_bucket_of(decision_date, target_date)
        if lead_bucket is None:
            continue
        try:
            outcome = int(record["settled_in_bin"])
        except (KeyError, TypeError, ValueError):
            continue
        claim_key = (
            record.get("city"),
            target_date.isoformat(),
            record.get("temperature_metric"),
            record.get("traded_bin_label"),
            record.get("direction"),
        )
        valid.append((record, lead_bucket, claim_key, outcome))

    claim_counts: dict[tuple, int] = {}
    for _record, _lead_bucket, claim_key, _outcome in valid:
        claim_counts[claim_key] = claim_counts.get(claim_key, 0) + 1

    fit_rows: list[FitRow] = []
    for record, lead_bucket, claim_key, outcome in valid:
        fit_rows.append(
            FitRow(
                p0=record.get("market_in_bin_prob"),
                q_raw=record.get("q_in_bin"),
                lead_bucket=lead_bucket,
                y=outcome,
                w=1.0 / claim_counts[claim_key],
            )
        )
    return fit_rows


class MarketAnchoredFitProvider:
    """TTL-cached artifact source for one borrowed DB connection factory.

    The provider never stores or closes a connection.  Only successful,
    immutable artifacts enter the shared cache; a failed current connection
    cannot poison a later provider that has a live connection.
    """

    def __init__(
        self,
        connect,
        *,
        ttl: timedelta = DEFAULT_TTL,
        min_train_rows: int = MIN_TRAIN_ROWS,
        lambda_: float = LIVE_LAMBDA,
        city_timezones: Mapping[str, str] | None,
        schema_alias: str = "main",
        cache: MarketAnchoredArtifactCache | None = None,
        db_identity: tuple[object, ...] | None = None,
        cache_only: bool = False,
    ) -> None:
        if schema_alias not in _FIT_TABLE_BY_ALIAS:
            raise ValueError(f"unsupported calibration schema alias: {schema_alias!r}")
        self._connect = connect
        self._schema_alias = schema_alias
        self._ttl = ttl
        self._min_train_rows = min_train_rows
        self._lambda = lambda_
        self._city_timezone_snapshot = _validated_city_timezone_snapshot(city_timezones)
        self._lead_calendar_revision = (
            LEAD_CALENDAR_REVISION if city_timezones is not None else UNBOUND_LEAD_CALENDAR_REVISION
        )
        self._cache = cache if cache is not None else get_shared_artifact_cache()
        self._db_identity = db_identity
        self._cache_only = bool(cache_only)
        self._lock = threading.Lock()
        self._artifact: ResidualCalibratorArtifact | None = None
        self._fitted_at: datetime | None = None
        self._calibration_policy = CalibrationPolicySpec(
            algorithm_revision=CALIBRATION_ALGORITHM_REVISION,
            input_revision=CALIBRATION_INPUT_REVISION,
            metric_pooling=CALIBRATION_METRIC_POOLING,
            lead_calendar_revision=self._lead_calendar_revision,
            lambda_=self._lambda,
            min_train_weight=self._min_train_rows,
            beta_bounds=(BETA_MIN, BETA_MAX),
            logit_clip=CLIP_D,
            probability_clip=(P_CLIP_LO, P_CLIP_HI),
            refit_seconds=self._ttl.total_seconds(),
        )

    @property
    def calibration_policy(self) -> CalibrationPolicySpec:
        """Immutable identity of this provider's calibration component."""

        return self._calibration_policy

    def _cache_key(self, db_identity: tuple[str, int, int]) -> ArtifactCacheKey:
        return (
            db_identity,
            self._city_timezone_snapshot,
            self._lead_calendar_revision,
            float(self._lambda),
            int(self._min_train_rows),
            self._ttl.total_seconds(),
        )

    def artifact(
        self,
        *,
        now: datetime,
        deadline_monotonic: float | None = None,
    ) -> ResidualCalibratorArtifact | None:
        """The current artifact, refitting when the cached one has aged out."""

        if (
            not isinstance(now, datetime)
            or now.tzinfo is None
            or now.utcoffset() is None
        ):
            return None
        now_utc = now.astimezone(timezone.utc)
        if deadline_monotonic is not None:
            if not math.isfinite(float(deadline_monotonic)) or time.monotonic() >= float(deadline_monotonic):
                return None
        if deadline_monotonic is None:
            acquired = self._lock.acquire()
        else:
            remaining = float(deadline_monotonic) - time.monotonic()
            if not math.isfinite(remaining) or remaining <= 0:
                return None
            acquired = self._lock.acquire(timeout=remaining)
        if not acquired:
            return None
        try:
            if (
                deadline_monotonic is not None
                and time.monotonic() >= float(deadline_monotonic)
            ):
                return None
            if self._fitted_at is not None:
                age = now_utc - self._fitted_at
                if age < timedelta(0):
                    # A backward request is independently fit at its causal
                    # cutoff, without downgrading the newest cached result.
                    return (
                        None
                        if self._cache_only
                        else self._fit(
                            now_utc,
                            deadline_monotonic=deadline_monotonic,
                        )
                    )
                if age < self._ttl:
                    if (
                        deadline_monotonic is not None
                        and time.monotonic() >= float(deadline_monotonic)
                    ):
                        return None
                    return self._artifact
            if self._cache_only:
                return None
            artifact, fitted_at = self._fit_cached(
                now_utc,
                deadline_monotonic=deadline_monotonic,
            )
            if fitted_at is None:
                return artifact
            # A deadline miss is transient work-budget exhaustion.  Preserve
            # the previous local state so a later caller may retry; in
            # particular, a monitor warm-up must not turn a late fit into a
            # six-hour cached ``None``.
            if (
                artifact is None
                and deadline_monotonic is not None
                and time.monotonic() >= float(deadline_monotonic)
            ):
                return None
            self._artifact = artifact
            self._fitted_at = fitted_at
            return self._artifact
        finally:
            self._lock.release()

    def warm(
        self,
        *,
        now: datetime,
        deadline_monotonic: float | None,
    ) -> ResidualCalibratorArtifact | None:
        """Fit/refresh once before entering a monitor's position loop."""

        previous_cache_only = self._cache_only
        self._cache_only = False
        try:
            return self.artifact(
                now=now,
                deadline_monotonic=deadline_monotonic,
            )
        finally:
            self._cache_only = previous_cache_only

    def _fit_connection(
        self,
        conn: sqlite3.Connection,
        training_cutoff: datetime,
        *,
        deadline_monotonic: float | None = None,
    ) -> ResidualCalibratorArtifact | None:
        try:
            if self._city_timezone_snapshot is None:
                return None
            if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                return None
            with _sqlite_fit_deadline(conn, deadline_monotonic):
                rows = load_fit_rows(
                    conn,
                    training_cutoff=training_cutoff,
                    city_timezone_snapshot=(
                        self._city_timezone_snapshot
                        if self._lead_calendar_revision == LEAD_CALENDAR_REVISION
                        else None
                    ),
                    schema_alias=self._schema_alias,
                )
        except Exception:  # noqa: BLE001 - an unavailable fit must never block serving
            return None
        # Each row is weighted 1/(claim count), so the training-row floor is
        # measured in claim-equivalent weight (sum(w)), not raw row count —
        # a claim re-certified many times must not look like many claims.
        if sum(row.w for row in rows) < self._min_train_rows:
            return None
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            return None
        cutoff_iso = training_cutoff.isoformat().replace("+00:00", "Z")
        try:
            artifact = fit(
                rows,
                lambda_=self._lambda,
                training_cutoff=cutoff_iso,
                lead_calendar_revision=self._lead_calendar_revision,
                city_timezone_snapshot=self._city_timezone_snapshot,
            )
            if (
                deadline_monotonic is not None
                and time.monotonic() >= deadline_monotonic
            ):
                return None
            return artifact
        except Exception:  # noqa: BLE001 - a failed fit degrades to raw q, never raises
            return None

    def _fit(
        self,
        training_cutoff: datetime,
        *,
        deadline_monotonic: float | None = None,
    ) -> ResidualCalibratorArtifact | None:
        try:
            conn = self._connect()
            if conn is None:
                return None
        except Exception:  # noqa: BLE001 - an unavailable fit must never block serving
            return None
        return self._fit_connection(
            conn,
            training_cutoff,
            deadline_monotonic=deadline_monotonic,
        )

    def _fit_cached(
        self,
        training_cutoff: datetime,
        *,
        deadline_monotonic: float | None = None,
    ) -> tuple[ResidualCalibratorArtifact | None, datetime | None]:
        try:
            conn = self._connect()
            if conn is None:
                return None, training_cutoff
            identity = self._db_identity or _canonical_db_identity(
                conn,
                schema_alias=self._schema_alias,
            )
        except Exception:  # noqa: BLE001 - an unavailable fit must never block serving
            return None, training_cutoff
        if identity is None:
            return (
                self._fit_connection(
                    conn,
                    training_cutoff,
                    deadline_monotonic=deadline_monotonic,
                ),
                training_cutoff,
            )
        return self._cache.get_or_fit(
            self._cache_key(identity),
            now=training_cutoff,
            ttl=self._ttl,
            fit_current=lambda: self._fit_connection(
                conn,
                training_cutoff,
                deadline_monotonic=deadline_monotonic,
            ),
            deadline_monotonic=deadline_monotonic,
        )


class CanonicalMarketAnchoredFitProvider:
    """Scope-separated ENTRY fits; deliberately excludes cash-proof markout."""

    def __init__(
        self,
        connects: Callable[[], tuple[sqlite3.Connection, sqlite3.Connection, sqlite3.Connection]],
        *,
        city_timezones: Mapping[str, str] | None,
        lambda_: float = LIVE_LAMBDA,
        min_train_rows: int = MIN_TRAIN_ROWS,
        ttl: timedelta = DEFAULT_TTL,
        cache: MarketAnchoredArtifactCache | None = None,
        corpus_cache: CanonicalCorpusCache | None = None,
    ) -> None:
        if not callable(connects):
            raise TypeError("canonical fit provider requires a connects callable")
        snapshot = _validated_city_timezone_snapshot(city_timezones)
        if snapshot is None:
            raise ValueError("canonical fit provider requires valid city timezones")
        if not _finite_policy_number(lambda_) or float(lambda_) < 0:
            raise ValueError("canonical fit provider lambda is invalid")
        if type(min_train_rows) is not int or min_train_rows <= 0:
            raise ValueError("canonical fit provider min_train_rows is invalid")
        if not isinstance(ttl, timedelta) or ttl <= timedelta(0):
            raise ValueError("canonical fit provider ttl is invalid")
        self._connects = connects
        self._city_timezone_snapshot = snapshot
        self._lambda = float(lambda_)
        self._min_train_rows = min_train_rows
        self._ttl = ttl
        self._cache = cache or _SHARED_CANONICAL_ARTIFACT_CACHE
        self._corpus_cache = corpus_cache or _SHARED_CANONICAL_CORPUS_CACHE
        self._calibration_policy = CalibrationPolicySpec(
            algorithm_revision=CALIBRATION_ALGORITHM_REVISION,
            input_revision=CANONICAL_CALIBRATION_INPUT_REVISION,
            metric_pooling=CANONICAL_CALIBRATION_METRIC_POOLING,
            lead_calendar_revision=LEAD_CALENDAR_REVISION,
            lambda_=self._lambda,
            min_train_weight=self._min_train_rows,
            beta_bounds=(BETA_MIN, BETA_MAX), logit_clip=CLIP_D,
            probability_clip=(P_CLIP_LO, P_CLIP_HI),
            refit_seconds=self._ttl.total_seconds(),
        )

    @property
    def calibration_policy(self) -> CalibrationPolicySpec:
        return self._calibration_policy

    @staticmethod
    def _expired(deadline_monotonic: float | None) -> bool:
        return deadline_monotonic is not None and (
            not math.isfinite(float(deadline_monotonic))
            or time.monotonic() >= float(deadline_monotonic)
        )

    def _cache_key(
        self, identities: tuple[tuple[str, int, int], ...], scope: CalibrationFitScope,
    ) -> ArtifactCacheKey:
        return (
            "canonical_market_anchored_fit_v1", identities,
            ("main", "main", "main"), CANONICAL_CORPUS_REVISION,
            "probability_only_no_cash_proofs",
            scope.as_payload()["scope_hash"], self._city_timezone_snapshot,
            self._calibration_policy.as_payload()["policy_hash"],
        )

    def _borrowed_corpus_handles(
        self, *, deadline_monotonic: float | None,
    ) -> tuple[
        tuple[sqlite3.Connection, sqlite3.Connection, sqlite3.Connection],
        tuple[tuple[str, int, int], ...],
        ArtifactCacheKey | None,
    ] | None:
        """Validate current borrowed handles and derive the corpus cache key."""

        try:
            handles = self._connects()
            if (not isinstance(handles, tuple) or len(handles) != 3
                    or any(not isinstance(conn, sqlite3.Connection) for conn in handles)):
                return None
            with ExitStack() as stack:
                for conn in dict.fromkeys(handles):
                    stack.enter_context(_sqlite_fit_deadline(conn, deadline_monotonic))
                identities = tuple(
                    _borrowed_db_identity(conn, schema_alias="main") for conn in handles
                )
        except Exception:  # noqa: BLE001 - closed borrowed handles cannot authorize a fit
            return None
        physical_identities = tuple(identity for identity in identities if identity is not None)
        corpus_key = None
        if len(physical_identities) == len(handles):
            corpus_key = (
                physical_identities, ("main", "main", "main"),
                CANONICAL_CORPUS_REVISION, "probability_only_no_cash_proofs",
                self._city_timezone_snapshot, self._ttl.total_seconds(),
            )
        return handles, physical_identities, corpus_key

    def _prepared_corpus(
        self, *, now: datetime, deadline_monotonic: float | None,
    ) -> tuple[
        tuple[sqlite3.Connection, sqlite3.Connection, sqlite3.Connection],
        tuple[tuple[str, int, int], ...],
        ArtifactCacheKey | None,
        CanonicalFitCorpus,
    ] | None:
        if (
            not isinstance(now, datetime) or now.tzinfo is None
            or now.utcoffset() is None or self._expired(deadline_monotonic)
        ):
            return None
        cutoff = now.astimezone(timezone.utc)
        prepared = self._borrowed_corpus_handles(
            deadline_monotonic=deadline_monotonic,
        )
        if prepared is None:
            return None
        handles, physical_identities, corpus_key = prepared
        corpus = self._corpus(
            handles, cutoff=cutoff, corpus_key=corpus_key,
            deadline_monotonic=deadline_monotonic,
        )
        if corpus is None or self._expired(deadline_monotonic):
            return None
        corpus_cutoff = _parse_ts(corpus.training_cutoff)
        if corpus_cutoff is None or not timedelta(0) <= cutoff - corpus_cutoff < self._ttl:
            return None
        return handles, physical_identities, corpus_key, corpus

    def warm_corpus(
        self, *, now: datetime, deadline_monotonic: float | None = None,
    ) -> bool:
        """Prepare and cache the canonical corpus without fitting an artifact."""

        return self._prepared_corpus(
            now=now, deadline_monotonic=deadline_monotonic,
        ) is not None

    def artifact(
        self, *, scope: CalibrationFitScope, now: datetime,
        deadline_monotonic: float | None = None,
    ) -> ResidualCalibratorArtifact | None:
        """Return a canonical scoped fit or None; never select legacy rows."""
        if (
            not isinstance(scope, CalibrationFitScope)
            or not isinstance(now, datetime) or now.tzinfo is None
            or now.utcoffset() is None or self._expired(deadline_monotonic)
        ):
            return None
        prepared = self._prepared_corpus(
            now=now, deadline_monotonic=deadline_monotonic,
        )
        if prepared is None:
            return None
        _handles, physical_identities, corpus_key, corpus = prepared
        corpus_cutoff = _parse_ts(corpus.training_cutoff)
        def fit_current() -> ResidualCalibratorArtifact | None:
            return self._fit_scope(
                corpus, scope=scope,
                deadline_monotonic=deadline_monotonic,
            )
        if corpus_key is None:
            return fit_current()
        # Cache age starts at the actual evidence cutoff. Fitting another scope
        # later must not renew the same corpus for an additional refit interval.
        return self._cache.get_or_fit(
            (*self._cache_key(physical_identities, scope), corpus_cutoff),
            now=corpus_cutoff, ttl=self._ttl, fit_current=fit_current,
            deadline_monotonic=deadline_monotonic,
        )[0]

    def _corpus(
        self, handles: tuple[sqlite3.Connection, sqlite3.Connection, sqlite3.Connection],
        *, cutoff: datetime, corpus_key: tuple[object, ...] | None,
        deadline_monotonic: float | None,
    ) -> CanonicalFitCorpus | None:
        def load_current() -> CanonicalFitCorpus | None:
            if self._expired(deadline_monotonic):
                return None
            try:
                with ExitStack() as stack:
                    for conn in dict.fromkeys(handles):
                        stack.enter_context(_sqlite_fit_deadline(conn, deadline_monotonic))
                    corpus = load_canonical_fit_corpus(
                        handles[0], handles[1], training_cutoff=cutoff,
                        city_timezone_snapshot=self._city_timezone_snapshot,
                        forecast_conn=handles[2], deadline_monotonic=deadline_monotonic,
                        include_cash_proofs=False,
                    )
                if self._expired(deadline_monotonic):
                    return None
                return corpus
            except Exception:  # noqa: BLE001 - failed reads are never cached
                return None

        if corpus_key is None:
            return load_current()
        return self._corpus_cache.get_or_load(
            corpus_key, requested_cutoff=cutoff, ttl=self._ttl,
            load_current=load_current, deadline_monotonic=deadline_monotonic,
        )[0]

    def _fit_scope(
        self, corpus: CanonicalFitCorpus, *, scope: CalibrationFitScope,
        deadline_monotonic: float | None,
    ) -> ResidualCalibratorArtifact | None:
        if self._expired(deadline_monotonic):
            return None
        try:
            rows = corpus.fit_rows(metric=scope.metric, execution_mode=scope.execution_mode,
                                   execution_contract=scope.execution_contract,
                                   probability_revision=scope.raw_probability_revision)
            if sum(row.w for row in rows) < self._min_train_rows:
                return None
            manifest = corpus.training_manifest(scope=scope)
            artifact = fit(rows, lambda_=self._lambda,
                           training_cutoff=corpus.training_cutoff,
                           lead_calendar_revision=LEAD_CALENDAR_REVISION,
                           city_timezone_snapshot=self._city_timezone_snapshot,
                           training_manifest=manifest)
        except Exception:  # noqa: BLE001 - required-fit callers handle absence
            return None
        return None if self._expired(deadline_monotonic) else artifact


# The active provider is monitor-scope state only.  Entry selection uses its
# batch-local provider and never registers it here.
_active_provider: ContextVar[object | None] = ContextVar(
    "market_anchored_active_provider",
    default=None,
)


def register_active_provider(
    provider: object | None,
) -> Token:
    """Set the current monitor-scope provider and return its reset token."""

    return _active_provider.set(provider)


def reset_active_provider(token: Token) -> None:
    """Restore the provider scope represented by ``token``."""

    _active_provider.reset(token)


@contextmanager
def active_provider_scope(provider: object | None):
    token = register_active_provider(provider)
    try:
        yield provider
    finally:
        reset_active_provider(token)


def get_active_provider() -> object | None:
    """The registered active provider, or None when unset."""

    return _active_provider.get()


# side vocabulary accepted by corrected_probability. Both the candidate.side
# ("YES"/"NO") and direction ("buy_yes"/"buy_no") spellings are in live use
# across the codebase, so both are recognized; anything else raises rather
# than silently defaulting to buy_yes.
_YES_SIDE_VALUES = frozenset({"YES", "buy_yes"})
_NO_SIDE_VALUES = frozenset({"NO", "buy_no"})


def _is_no_side(side: str) -> bool:
    if side in _NO_SIDE_VALUES:
        return True
    if side in _YES_SIDE_VALUES:
        return False
    raise ValueError(f"corrected_probability: unrecognized side {side!r}")


def corrected_probability(
    artifact: ResidualCalibratorArtifact | None,
    *,
    p0: float,
    q_raw: float,
    target_date: date,
    side: str,
    city: str | None = None,
    decision_at: datetime | None = None,
) -> tuple[float, str, float] | None:
    """Apply ``artifact`` to one candidate, or None when it cannot be applied.

    The artifact is fit in in-bin (YES-event) space: p0 and q_raw there are
    ``market_in_bin_prob``/``q_in_bin``, i.e. probabilities of the YES event.
    ``p0``/``q_raw`` passed in here are in HELD-TOKEN space instead (the price
    and payoff probability of whichever token the candidate holds). For a
    buy_no candidate, held-token space is the in-bin space's complement
    (q_NO = 1 - q_in, p_NO = 1 - p_in), so this complements both inputs into
    in-bin space, applies the unchanged artifact, and complements the result
    back: ``1 - apply_artifact(artifact, 1 - p0, 1 - q_raw, lead_bucket)``.
    buy_yes needs no transform since held-token space already is in-bin space.

    Returns ``(corrected_q, lead_bucket, alpha_lead)`` where ``alpha_lead`` is
    the EFFECTIVE signed intercept applied in held-token space (the fitted
    alpha for buy_yes, its negation for buy_no), so certificates record what
    was actually applied. None means every fail-open case at once — no
    artifact, an unmodeled lead, a non-finite input — because each has the
    identical consequence for the caller: keep the raw q.
    """

    if artifact is None:
        return None
    artifact_revision = getattr(artifact, "lead_calendar_revision", UNBOUND_LEAD_CALENDAR_REVISION)
    if artifact_revision in {
        LEGACY_LEAD_CALENDAR_REVISION,
        LEAD_CALENDAR_REVISION,
    }:
        if (
            not isinstance(decision_at, datetime)
            or decision_at.tzinfo is None
            or decision_at.utcoffset() is None
            or not city
        ):
            return None
        snapshot = getattr(artifact, "city_timezone_snapshot", None)
        if not _snapshot_is_valid(snapshot):
            return None
        decision_date = _city_local_target_date(
            decision_at, city, snapshot
        )
        if decision_date is None:
            return None
        training_cutoff = _parse_ts(getattr(artifact, "training_cutoff", None))
        decision_at_utc = decision_at.astimezone(timezone.utc)
        if training_cutoff is None or training_cutoff > decision_at_utc:
            return None
    else:
        return None
    lead_bucket = (
        legacy_lead_bucket_of(decision_date, target_date)
        if artifact_revision == LEGACY_LEAD_CALENDAR_REVISION
        else lead_bucket_of(decision_date, target_date)
    )
    if lead_bucket is None:
        return None
    is_no = _is_no_side(side)
    if is_no:
        corrected = apply_artifact(artifact, 1.0 - p0, 1.0 - q_raw, lead_bucket)
        if corrected is None:
            return None
        corrected = 1.0 - corrected
    else:
        corrected = apply_artifact(artifact, p0, q_raw, lead_bucket)
        if corrected is None:
            return None
    alpha_lead = float(artifact.alpha[lead_bucket])
    if is_no:
        alpha_lead = -alpha_lead
    return corrected, lead_bucket, alpha_lead


_ENTRY_AUDIT_ENCODING = "zlib+base64+canonical-json-object-v1"
_ENTRY_AUDIT_DELTA_ENCODING = "zlib+base64+canonical-json-object-delta-v1"
_ENTRY_AUDIT_MAX_COMPRESSED_BYTES = 2_000_000
_ENTRY_AUDIT_MAX_RAW_BYTES = 10_000_000
_ENTRY_AUDIT_MAX_DELTA_DEPTH = 8


def _held_correction_unavailable(reason: str) -> PayoffQCorrectionUnavailable:
    return PayoffQCorrectionUnavailable(f"HELD_ENTRY_CALIBRATION_{reason}")


def _decode_held_audit_payload(value: object, *, expected_hash: str) -> Mapping[str, object]:
    """Decode one bounded canonical JSON receipt component."""

    try:
        compressed = base64.b64decode(str(value), validate=True)
        if len(compressed) > _ENTRY_AUDIT_MAX_COMPRESSED_BYTES:
            raise ValueError
        decoder = zlib.decompressobj()
        raw = decoder.decompress(compressed, _ENTRY_AUDIT_MAX_RAW_BYTES + 1)
        if (
            len(raw) > _ENTRY_AUDIT_MAX_RAW_BYTES
            or not decoder.eof
            or decoder.unconsumed_tail
            or decoder.unused_data
        ):
            raise ValueError
        tail = decoder.flush(_ENTRY_AUDIT_MAX_RAW_BYTES + 1 - len(raw))
        if tail or not decoder.eof or decoder.unconsumed_tail or decoder.unused_data:
            raise ValueError
        if hashlib.sha256(raw).hexdigest() != expected_hash:
            raise ValueError
        decoded = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError, zlib.error) as exc:
        raise _held_correction_unavailable("AUDIT_PAYLOAD_INVALID") from exc
    if not isinstance(decoded, Mapping):
        raise _held_correction_unavailable("AUDIT_PAYLOAD_INVALID")
    return decoded


def _receipt_summary(
    conn: sqlite3.Connection,
    *,
    decision_log_id: int,
    expected_mode: str,
    expected_receipt_hash: str,
) -> Mapping[str, object]:
    """Read and authenticate one exact, already-persisted auction receipt."""

    try:
        row = conn.execute(
            "SELECT mode, artifact_json FROM main.decision_log WHERE id = ? LIMIT 1",
            (decision_log_id,),
        ).fetchone()
        if row is None or str(row[0]) != expected_mode:
            raise ValueError
        artifact = json.loads(str(row[1] or ""))
        summary = artifact["summary"]
        if not isinstance(summary, Mapping):
            raise ValueError
        from src.contracts.global_auction_receipt import (
            assert_global_auction_summary_integrity,
        )

        assert_global_auction_summary_integrity(summary)
        if str(summary.get("receipt_hash") or "") != expected_receipt_hash:
            raise ValueError
        return summary
    except (KeyError, TypeError, ValueError, sqlite3.Error, json.JSONDecodeError) as exc:
        raise _held_correction_unavailable("RECEIPT_INVALID") from exc


def _load_held_audit_context(
    conn: sqlite3.Connection,
    *,
    decision_log_id: int,
    expected_mode: str,
    expected_receipt_hash: str,
    expected_audit_hash: str | None = None,
    expected_delta_chain_depth: int | None = None,
    depth: int = 0,
    seen_ids: frozenset[int] = frozenset(),
) -> Mapping[str, object]:
    """Resolve an audit context through its authenticated compact-receipt chain."""

    if depth > _ENTRY_AUDIT_MAX_DELTA_DEPTH or decision_log_id in seen_ids:
        raise _held_correction_unavailable("AUDIT_DELTA_DEPTH")
    if expected_delta_chain_depth is not None and not (
        0 <= expected_delta_chain_depth <= _ENTRY_AUDIT_MAX_DELTA_DEPTH
    ):
        raise _held_correction_unavailable("AUDIT_DELTA_DEPTH")
    seen_ids = seen_ids | frozenset({decision_log_id})
    summary = _receipt_summary(
        conn,
        decision_log_id=decision_log_id,
        expected_mode=expected_mode,
        expected_receipt_hash=expected_receipt_hash,
    )
    audit_hash = str(summary.get("audit_context_sha256") or "")
    if len(audit_hash) != 64 or (
        expected_audit_hash is not None and audit_hash != expected_audit_hash
    ):
        raise _held_correction_unavailable("AUDIT_HASH_INVALID")
    encoding = str(summary.get("audit_context_encoding") or "")
    if "audit_context_zlib_b64" in summary:
        if encoding != _ENTRY_AUDIT_ENCODING or expected_delta_chain_depth not in (None, 0):
            raise _held_correction_unavailable("AUDIT_ENCODING_INVALID")
        return _decode_held_audit_payload(
            summary["audit_context_zlib_b64"], expected_hash=audit_hash
        )

    if "audit_context_reference_decision_log_id" in summary:
        try:
            return _load_held_audit_context(
                conn,
                decision_log_id=int(summary["audit_context_reference_decision_log_id"]),
                expected_mode=str(summary["audit_context_reference_mode"]),
                expected_receipt_hash=str(summary["audit_context_reference_receipt_hash"]),
                expected_audit_hash=str(summary["audit_context_reference_sha256"]),
                expected_delta_chain_depth=expected_delta_chain_depth,
                depth=depth + 1,
                seen_ids=seen_ids,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise _held_correction_unavailable("AUDIT_REFERENCE_INVALID") from exc

    if "audit_context_delta_zlib_b64" not in summary:
        raise _held_correction_unavailable("AUDIT_MISSING")
    if (
        encoding != _ENTRY_AUDIT_ENCODING
        or str(summary.get("audit_context_delta_encoding") or "")
        != _ENTRY_AUDIT_DELTA_ENCODING
    ):
        raise _held_correction_unavailable("AUDIT_ENCODING_INVALID")
    try:
        delta_hash = str(summary["audit_context_delta_sha256"])
        base_id = int(summary["audit_context_base_decision_log_id"])
        base_mode = str(summary["audit_context_base_mode"])
        base_receipt_hash = str(summary["audit_context_base_receipt_hash"])
        base_audit_hash = str(summary["audit_context_base_sha256"])
        declared_depth = int(summary["audit_context_delta_chain_depth"])
        if (
            not 1 <= declared_depth <= _ENTRY_AUDIT_MAX_DELTA_DEPTH
            or (
                expected_delta_chain_depth is not None
                and declared_depth != expected_delta_chain_depth
            )
        ):
            raise ValueError
        delta = _decode_held_audit_payload(
            summary["audit_context_delta_zlib_b64"], expected_hash=delta_hash
        )
        base = _load_held_audit_context(
            conn,
            decision_log_id=base_id,
            expected_mode=base_mode,
            expected_receipt_hash=base_receipt_hash,
            expected_audit_hash=base_audit_hash,
            expected_delta_chain_depth=declared_depth - 1,
            depth=depth + 1,
            seen_ids=seen_ids,
        )
        # This is the writer's stable object-delta semantics.  Reusing it
        # avoids accepting a locally interpreted receipt shape.
        from src.engine.global_batch_runtime import (
            _apply_json_object_delta,
            _canonical_json_bytes,
        )

        resolved = _apply_json_object_delta(base, delta)
        if hashlib.sha256(_canonical_json_bytes(resolved)).hexdigest() != audit_hash:
            raise ValueError
        return resolved
    except (KeyError, TypeError, ValueError, PayoffQCorrectionUnavailable) as exc:
        if isinstance(exc, PayoffQCorrectionUnavailable):
            raise
        raise _held_correction_unavailable("AUDIT_DELTA_INVALID") from exc


def _artifact_from_held_audit(payload: object) -> ResidualCalibratorArtifact:
    """Rehydrate the immutable artifact recorded by the ENTRY receipt."""

    try:
        if not isinstance(payload, Mapping):
            raise ValueError
        manifest_raw = payload.get("training_manifest")
        manifest = None
        if manifest_raw is not None:
            try:
                manifest = CanonicalTrainingManifest.from_payload(manifest_raw)
            except (TypeError, ValueError, KeyError):
                # The audit writer stores ``asdict(artifact)``.  Its nested
                # manifest is the immutable dataclass fields without the wire
                # type/version wrapper, so reconstruct and recheck its hash.
                if not isinstance(manifest_raw, Mapping):
                    raise
                manifest = CanonicalTrainingManifest.build(
                    scope_hash=str(manifest_raw["scope_hash"]),
                    corpus_revision=str(manifest_raw["corpus_revision"]),
                    training_cutoff=str(manifest_raw["training_cutoff"]),
                    row_count=int(manifest_raw["row_count"]),
                    event_count=int(manifest_raw["event_count"]),
                    weight_sum=float(manifest_raw["weight_sum"]),
                    max_fill_available_at=str(manifest_raw["max_fill_available_at"]),
                    max_label_available_at=str(manifest_raw["max_label_available_at"]),
                    input_hash=str(manifest_raw["input_hash"]),
                )
                if str(manifest_raw.get("manifest_hash") or "") != manifest.manifest_hash:
                    raise ValueError
        artifact = ResidualCalibratorArtifact(
            alpha=dict(payload["alpha"]),
            beta=float(payload["beta"]),
            lambda_=float(payload["lambda_"]),
            clip_d=float(payload["clip_d"]),
            p_clip=tuple(float(value) for value in payload["p_clip"]),
            lead_buckets=tuple(str(value) for value in payload["lead_buckets"]),
            training_cutoff=str(payload["training_cutoff"]),
            n_train=int(payload["n_train"]),
            n_excluded=int(payload["n_excluded"]),
            excluded_reasons={str(key): int(value) for key, value in dict(payload["excluded_reasons"]).items()},
            param_hash=str(payload["param_hash"]),
            lead_calendar_revision=str(payload["lead_calendar_revision"]),
            city_timezone_snapshot=tuple(
                (str(city), str(zone)) for city, zone in payload["city_timezone_snapshot"]
            ),
            training_manifest=manifest,
        )
        from src.calibration.market_anchored_residual import _param_hash

        if artifact.param_hash != _param_hash(
            alpha=artifact.alpha,
            beta=artifact.beta,
            lambda_=artifact.lambda_,
            clip_d=artifact.clip_d,
            p_clip=artifact.p_clip,
            lead_buckets=artifact.lead_buckets,
            training_cutoff=artifact.training_cutoff,
            lead_calendar_revision=artifact.lead_calendar_revision,
            city_timezone_snapshot=artifact.city_timezone_snapshot,
        ):
            raise ValueError
        return artifact
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise _held_correction_unavailable("ARTIFACT_INVALID") from exc


@dataclass(frozen=True)
class HeldEntryCalibrationBinding:
    """Entry-sealed calibration that may correct a held token's fresh q only."""

    artifact: ResidualCalibratorArtifact
    fit_scope: CalibrationFitScope
    calibration_policy: CalibrationPolicySpec
    position_id: str
    decision_log_id: int
    decision_certificate_hash: str
    family_key: str
    bin_id: str
    token_id: str
    side: str

    def corrected_probability(
        self,
        *,
        family_key: str,
        bin_id: str,
        token_id: str,
        side: str,
        raw_q: float,
        p0: float,
        city: str,
        target_date: date,
        decision_at: datetime,
    ) -> PayoffQCorrection:
        """Apply the frozen ENTRY artifact to current source-clock q and p0."""

        if (
            family_key != self.family_key
            or bin_id != self.bin_id
            or token_id != self.token_id
            or side != self.side
        ):
            raise _held_correction_unavailable("CANDIDATE_IDENTITY_MISMATCH")
        applied = corrected_probability(
            self.artifact,
            p0=p0,
            q_raw=raw_q,
            city=city,
            target_date=target_date,
            decision_at=decision_at,
            side=side,
        )
        if applied is None:
            raise _held_correction_unavailable("CURRENT_Q_UNAVAILABLE")
        corrected_q, lead_bucket, alpha_lead = applied
        return PayoffQCorrection(
            family_key=family_key,
            bin_id=bin_id,
            token_id=token_id,
            side=side,
            raw_q=float(raw_q),
            corrected_q=float(corrected_q),
            p0=float(p0),
            lead_bucket=lead_bucket,
            alpha_lead=alpha_lead,
            beta=float(self.artifact.beta),
            lambda_=float(self.artifact.lambda_),
            training_cutoff=self.artifact.training_cutoff,
            n_train=int(self.artifact.n_train),
            param_hash=self.artifact.param_hash,
            calibration_policy=self.calibration_policy,
            fit_scope=self.fit_scope,
            training_manifest=self.artifact.training_manifest,
        )


def load_held_entry_calibration(
    trade_conn: sqlite3.Connection,
    *,
    position_id: str,
    token_id: str,
    side: str,
    world_conn: sqlite3.Connection | None = None,
    world_schema_alias: str = "world",
) -> HeldEntryCalibrationBinding:
    """Load one position's immutable ENTRY calibration without fitting or opening DBs."""

    if (
        not isinstance(trade_conn, sqlite3.Connection)
        or not isinstance(position_id, str)
        or not position_id
        or not isinstance(token_id, str)
        or not token_id
        or side not in {"YES", "NO"}
        or world_schema_alias not in {"main", "world"}
    ):
        raise _held_correction_unavailable("REQUEST_INVALID")
    certificate_conn = world_conn or trade_conn
    certificate_schema = "main" if world_conn is not None and world_schema_alias == "world" else world_schema_alias
    try:
        event_status = trade_conn.execute(
            """
            SELECT
                COUNT(*),
                SUM(CASE
                    WHEN typeof(decision_id) != 'text' OR trim(decision_id) = '' THEN 1
                    ELSE 0
                END),
                SUM(CASE
                    WHEN payload_json IS NULL
                     OR json_valid(payload_json) = 0
                     OR json_type(payload_json) != 'object' THEN 1
                    ELSE 0
                END),
                SUM(CASE
                    WHEN json_valid(payload_json) = 1
                     AND json_type(payload_json, '$.decision_log_id') IS NOT NULL
                     AND json_type(payload_json, '$.decision_log_id') != 'integer' THEN 1
                    ELSE 0
                END)
              FROM position_events
             WHERE position_id = ?
               AND event_type IN ('POSITION_OPEN_INTENT', 'ENTRY_ORDER_POSTED', 'ENTRY_ORDER_FILLED')
            """,
            (position_id,),
        ).fetchone()
        event_identity_rows = trade_conn.execute(
            """
            SELECT DISTINCT decision_id FROM position_events
             WHERE position_id = ?
               AND event_type IN ('POSITION_OPEN_INTENT', 'ENTRY_ORDER_POSTED', 'ENTRY_ORDER_FILLED')
             LIMIT 3
            """,
            (position_id,),
        ).fetchall()
        payload_receipt_rows = trade_conn.execute(
            """
            SELECT DISTINCT json_extract(payload_json, '$.decision_log_id')
              FROM position_events
             WHERE position_id = ?
               AND event_type IN ('POSITION_OPEN_INTENT', 'ENTRY_ORDER_POSTED', 'ENTRY_ORDER_FILLED')
               AND json_valid(payload_json) = 1
               AND json_type(payload_json, '$.decision_log_id') = 'integer'
             LIMIT 3
            """,
            (position_id,),
        ).fetchall()
        attribution_rows = trade_conn.execute(
            """
            SELECT DISTINCT decision_certificate_hash
              FROM position_decision_attribution
             WHERE position_id = ?
               AND intent_kind = 'ENTRY'
               AND resolution = 'ATTRIBUTED'
               AND decision_certificate_hash IS NOT NULL
             LIMIT 2
            """,
            (position_id,),
        ).fetchall()
        certificate_hashes = {str(row[0]).strip() for row in attribution_rows if str(row[0]).strip()}
    except (sqlite3.Error, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise _held_correction_unavailable("ENTRY_PROVENANCE_UNAVAILABLE") from exc
    if (
        event_status is None
        or int(event_status[0] or 0) < 1
        or int(event_status[1] or 0) > 0
        or int(event_status[2] or 0) > 0
        or int(event_status[3] or 0) > 0
        or not 1 <= len(event_identity_rows) <= 2
        or len(payload_receipt_rows) > 1
        or len(certificate_hashes) != 1
    ):
        raise _held_correction_unavailable("ENTRY_PROVENANCE_AMBIGUOUS")
    certificate_hash = next(iter(certificate_hashes))
    try:
        certificate_row = certificate_conn.execute(
            f"""
            SELECT payload_json, payload_hash FROM {certificate_schema}.decision_certificates
             WHERE certificate_hash = ?
               AND certificate_type = 'ActionableTradeCertificate'
               AND mode = 'LIVE'
               AND verifier_status = 'VERIFIED'
             LIMIT 1
            """,
            (certificate_hash,),
        ).fetchone()
        if certificate_row is None:
            raise ValueError
        certificate = json.loads(str(certificate_row[0] or ""))
        if not isinstance(certificate, Mapping):
            raise ValueError
        from src.decision_kernel.canonicalization import stable_hash

        if stable_hash(certificate) != str(certificate_row[1] or ""):
            raise ValueError
        receipt_ref_raw = certificate["global_auction_receipt"]
        from src.contracts.global_auction_receipt import GlobalAuctionReceiptRef

        receipt_ref = GlobalAuctionReceiptRef.from_payload(receipt_ref_raw)
    except (KeyError, TypeError, ValueError, sqlite3.Error, json.JSONDecodeError) as exc:
        raise _held_correction_unavailable("CERTIFICATE_INVALID") from exc
    # Both normal/recovery spellings may refer to the same ENTRY. Authenticate
    # each against its certificate; a third distinct spelling is invalid.
    for (event_certificate_hash,) in event_identity_rows:
        if event_certificate_hash != certificate_hash:
            parts = event_certificate_hash.split(":")
            if len(parts) < 5 or parts[0] != "edli_exec_cmd":
                raise _held_correction_unavailable("ENTRY_PROVENANCE_AMBIGUOUS")
            event_id = parts[1]
            command_token = parts[-2]
            command_direction = parts[-1]
            final_intent_id = ":".join(parts[2:-2])
            if (
                str(certificate.get("event_id") or "").strip() != event_id
                or str(certificate.get("final_intent_id") or "").strip() != final_intent_id
                or str(certificate.get("token_id") or "").strip() != command_token
                or str(certificate.get("direction") or "").strip() != command_direction
            ):
                raise _held_correction_unavailable("ENTRY_PROVENANCE_AMBIGUOUS")
    decision_log_id = receipt_ref.decision_log_id
    if payload_receipt_rows and payload_receipt_rows[0][0] != decision_log_id:
        raise _held_correction_unavailable("RECEIPT_BINDING_MISMATCH")
    economics = certificate.get("qkernel_execution_economics")
    if economics is not None and not isinstance(economics, Mapping):
        raise _held_correction_unavailable("CERTIFICATE_INVALID")
    economics = economics or {}

    def certificate_value(field: str) -> object:
        outer = certificate.get(field)
        nested = economics.get(field)
        if outer not in (None, "") and nested not in (None, "") and outer != nested:
            raise _held_correction_unavailable("CERTIFICATE_BINDING_MISMATCH")
        return outer if outer not in (None, "") else nested

    direction = str(certificate_value("direction") or "").lower()
    cert_side = "YES" if direction == "buy_yes" else "NO" if direction == "buy_no" else ""
    if (
        cert_side != side
        or str(certificate_value("token_id") or "") != token_id
        or str(certificate_value("global_token_id") or "") != token_id
        or not str(certificate_value("global_family_key") or "").strip()
        or not str(certificate_value("global_bin_id") or "").strip()
    ):
        raise _held_correction_unavailable("TOKEN_OR_SIDE_MISMATCH")
    correction = certificate_value("market_anchored_correction")
    try:
        if not isinstance(correction, Mapping) or correction.get("applied") is not True:
            raise ValueError
        policy = CalibrationPolicySpec.from_payload(correction["calibration_policy"])
        scope = CalibrationFitScope.from_payload(correction["fit_scope"])
        param_hash = str(correction["param_hash"])
        if (
            policy.input_revision != CANONICAL_CALIBRATION_INPUT_REVISION
            or policy.metric_pooling != CANONICAL_CALIBRATION_METRIC_POOLING
            or scope.metric not in {"high", "low"}
            or len(param_hash) != 64
        ):
            raise ValueError
        audit_context = _load_held_audit_context(
            trade_conn,
            decision_log_id=receipt_ref.decision_log_id,
            expected_mode=receipt_ref.decision_log_mode,
            expected_receipt_hash=receipt_ref.receipt_hash,
        )
        audit = audit_context.get("market_anchored_fit_artifact_audit")
        if not isinstance(audit, Mapping):
            raise ValueError
        if audit.get("revision") != "canonical_entry_fit_artifact_audit_v1":
            raise ValueError
        consulted = audit.get("consulted_scopes")
        if not isinstance(consulted, Mapping):
            raise ValueError
        matches = [
            entry for entry in consulted.values()
            if isinstance(entry, Mapping)
            and entry.get("status") == "AVAILABLE"
            and str(entry.get("param_hash") or "") == param_hash
            and entry.get("scope") == scope.as_payload()
            and entry.get("policy") == policy.as_payload()
        ]
        if len(matches) != 1:
            raise ValueError
        artifact = _artifact_from_held_audit(matches[0].get("artifact"))
        reproduced = _reproduced_policy_probability(
            policy,
            raw_q=correction["q_raw"],
            p0=correction["p0"],
            alpha_lead=correction["alpha_lead"],
            beta=correction["beta"],
            lead_bucket=correction["lead_bucket"],
            side=side,
        )
        if (
            artifact.param_hash != param_hash
            or not math.isclose(float(correction["beta"]), artifact.beta, rel_tol=0.0, abs_tol=1e-12)
            or not math.isclose(float(correction["lambda"]), artifact.lambda_, rel_tol=0.0, abs_tol=1e-12)
            or str(correction["training_cutoff"]) != artifact.training_cutoff
            or int(correction["n_train"]) != artifact.n_train
            or not isinstance(correction.get("lead_bucket"), str)
            or correction["lead_bucket"] not in artifact.alpha
            or not math.isclose(
                float(correction["alpha_lead"]),
                -float(artifact.alpha[correction["lead_bucket"]]) if side == "NO" else float(artifact.alpha[correction["lead_bucket"]]),
                rel_tol=0.0,
                abs_tol=1e-12,
            )
            or reproduced is None
            or not math.isclose(float(correction["q_corrected"]), reproduced, rel_tol=0.0, abs_tol=1e-12)
        ):
            raise ValueError
        correction_manifest = correction.get("training_manifest")
        if (
            artifact.training_manifest is None
            or correction_manifest != artifact.training_manifest.as_payload()
            or artifact.training_manifest.scope_hash != scope.as_payload()["scope_hash"]
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise _held_correction_unavailable("CORRECTION_BINDING_INVALID") from exc
    return HeldEntryCalibrationBinding(
        artifact=artifact,
        fit_scope=scope,
        calibration_policy=policy,
        position_id=position_id,
        decision_log_id=decision_log_id,
        decision_certificate_hash=certificate_hash,
        family_key=str(certificate_value("global_family_key")),
        bin_id=str(certificate_value("global_bin_id")),
        token_id=token_id,
        side=side,
    )


class HeldEntryCalibrationProvider:
    """Monitor-scoped reader over already-open trade/world handles."""

    def __init__(
        self,
        trade_conn: sqlite3.Connection,
        *,
        world_conn: sqlite3.Connection | None = None,
        world_schema_alias: str = "world",
    ) -> None:
        self._trade_conn = trade_conn
        self._world_conn = world_conn
        self._world_schema_alias = world_schema_alias

    def load(
        self, *, position_id: str, token_id: str, side: str,
    ) -> HeldEntryCalibrationBinding:
        return load_held_entry_calibration(
            self._trade_conn,
            position_id=position_id,
            token_id=token_id,
            side=side,
            world_conn=self._world_conn,
            world_schema_alias=self._world_schema_alias,
        )


class UnavailableHeldEntryCalibrationProvider:
    """Explicit live-monitor sentinel; required ENTRY proof cannot become raw q."""

    def load(self, **_kwargs) -> HeldEntryCalibrationBinding:
        raise _held_correction_unavailable("READER_UNAVAILABLE")

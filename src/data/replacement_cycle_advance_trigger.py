# Created: 2026-06-12
# Last reused or audited: 2026-06-12 (external review FINDING 2: per-family materializable-cycle
#   gate + typed leg-artifact-missing reason + loud held-family read failure)
# Authority basis: U5 step 2a (operator regime-unification + freshness investigation 2026-06-12,
#   docs/authority/regime_unification_2026-06-12.md §U2 + docs/evidence/freshness/
#   2026-06-12_forecast_freshness_truth.md §Q4(b)). The U2 root fix's first half: re-materialize a
#   HELD/active family's posterior the moment a NEWER provider cycle has been ingested than the
#   cycle the posterior consumed — NOT on a wall clock. Belief decay is a STEP function on missed
#   model cycles (measured: new-cycle ingest moves posterior TV 0.319 / center 0.7°C mean, 1.9°C
#   p90; same-cycle recompute Δμ≈0), so re-materialization is worthwhile EXACTLY when a fresher
#   cycle exists and worthless otherwise. Born-stale (14.1% measured) + backward thrashing (78
#   transitions / 267 live families) are the diseases this kills together with the materializer's
#   monotone-advance refusal (_cycle_monotone_block_reasons).
"""SINGLE-AUTHORITY newer-cycle comparison + idempotent re-materialization enqueue.

Sibling of replacement_fusion_upgrade_trigger (Task #32): SAME availability-poll lane, SAME seed
builder, SAME seed_dir the materialize cycle drains, SAME plan + day0 guard + nearest-target-first
ordering — the ONLY difference is the verdict. The fusion-upgrade trigger fires on instrument-set
expansion at the SAME cycle; this trigger fires on a NEWER cycle becoming materializable.

THE single comparison (`scope_needs_cycle_advance`): a scope needs re-materialization iff its latest
posterior consumed a model cycle STRICTLY OLDER than the freshest in-universe cycle that is now
materializable under the current live dependency identity. After the AIFS removal, that live
materialization leg is the OM9 anchor; the previous two-leg AIFS+OM9 high-water mark is not allowed
to gate live redecision.

Prioritization (operator directive 2026-06-12): (i) families with HELD positions (zeus_trades
position_current, read-only) first, then (ii) families with markets in their active trading window
(the current-target plan already restricts to token-bearing markets with target_date >= today).
Bounded per tick by the fair-cursor budget (Wave1B precedent — count only WRITTEN seeds, never a
numeric drop-cap on the candidate set).

Idempotency: cycle_advance_enqueues UNIQUE(city, target_date, metric, target_cycle_time). A scope is
re-enqueued AT MOST ONCE per target-cycle advance once a real seed exists. A typed gap row
(manifest absent) is healable: when the same target cycle's artifact later appears, the row is
updated with the seed file instead of blocking the repair. Fail-soft throughout: any per-scope error
is logged and skipped; the function never raises into the poll.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import sqlite3
import stat
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Mapping, Sequence

from src.contracts.position_truth import CURRENT_MONEY_RISK_CHAIN_STATES

from src.data.raw_forecast_artifact_manifest import RawForecastArtifactManifest
from src.data.replacement_forecast_readiness import SOURCE_ID

_LOG = logging.getLogger("zeus.replacement_cycle_advance_trigger")

UTC = timezone.utc

_ANCHOR_LEG_SOURCE_ID = "openmeteo_ecmwf_ifs_9km"
_HELD_REHEAL_COOLDOWN = timedelta(minutes=30)
_DAY0_CONDITIONING_IDENTITY_COLUMN = "day0_conditioning_identity_json"
_CYCLE_ADVANCE_STAGING_DIR = ".cycle-advance-staging"
_DAY0_BRIDGE_STOP = object()
_DAY0_BRIDGE_CONDITION = threading.Condition()
_DAY0_BRIDGE_QUEUES: dict[bool, queue.Queue[object]] = {
    True: queue.Queue(),
    False: queue.Queue(),
}
_DAY0_BRIDGE_CLASSIFY_QUEUE: queue.Queue[object] = queue.Queue()
_DAY0_BRIDGE_THREADS: tuple[threading.Thread, ...] = ()
_DAY0_BRIDGE_CLOSED = False


@dataclass
class _Day0BridgePending:
    city: str
    target_date: str
    metric: str
    computed_at: datetime | None
    held_position: bool | None
    generation: int = 1
    running: bool = False
    lane: bool | None = None
    enqueued_monotonic: float = 0.0
    failures: int = 0


_DAY0_BRIDGE_PENDING: dict[tuple[str, str, str], _Day0BridgePending] = {}
_DAY0_BRIDGE_RETRY_BASE_SECONDS = 0.5
_DAY0_BRIDGE_RETRY_MAX_SECONDS = 30.0


class _Day0EnqueueOwnerRequestState(Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    INDETERMINATE = "INDETERMINATE"


@dataclass(frozen=True)
class _Day0EnqueueOwnerRequestCheck:
    state: _Day0EnqueueOwnerRequestState
    reason: str


def _family_manifests_from_db(
    conn: sqlite3.Connection,
    *,
    city: str,
    identity,
    computed_at: datetime,
    limit: int = 96,
) -> tuple[RawForecastArtifactManifest, ...]:
    """Read only one family's recent canonical manifests, newest first."""

    rows = conn.execute(
        """
        SELECT artifact_id, source_id, product_id, data_version, artifact_path, sha256,
               byte_size, source_cycle_time, source_available_at, captured_at,
               request_url, request_params_json, artifact_metadata_json,
               training_allowed
        FROM raw_forecast_artifacts
        WHERE source_id = ?
          AND product_id = ?
          AND data_version = ?
          AND json_extract(artifact_metadata_json, '$.city') = ?
          AND julianday(source_available_at) <= julianday(?)
        ORDER BY source_cycle_time DESC, captured_at DESC, artifact_id DESC
        LIMIT ?
        """,
        (
            identity.source_id,
            identity.product_id,
            identity.data_version,
            str(city),
            computed_at.astimezone(UTC).isoformat(),
            int(limit),
        ),
    ).fetchall()
    manifests: list[RawForecastArtifactManifest] = []
    decision_cut = computed_at.astimezone(UTC)
    for row in rows:
        try:
            source_available_at = str(row["source_available_at"])
            available_at = _parse_cycle(source_available_at)
            if available_at is None or available_at > decision_cut:
                continue
            product_metadata = json.loads(row["artifact_metadata_json"] or "{}")
            product_metadata["artifact_id"] = int(row["artifact_id"])
            manifests.append(
                RawForecastArtifactManifest(
                    source_id=str(row["source_id"]),
                    product_id=str(row["product_id"]),
                    data_version=str(row["data_version"]),
                    artifact_path=str(row["artifact_path"]),
                    sha256=str(row["sha256"]),
                    byte_size=int(row["byte_size"]),
                    source_cycle_time=str(row["source_cycle_time"]),
                    source_available_at=source_available_at,
                    captured_at=str(row["captured_at"]),
                    request_url=str(row["request_url"] or ""),
                    request_params=json.loads(row["request_params_json"] or "{}"),
                    product_metadata=product_metadata,
                    training_allowed=bool(row["training_allowed"]),
                )
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            _LOG.warning(
                "invalid canonical raw manifest skipped city=%s source=%s cycle=%s: %s",
                city,
                row["source_id"],
                row["source_cycle_time"],
                exc,
            )
    return tuple(manifests)


def _parse_cycle(value: object) -> datetime | None:
    if value is None or not str(value).strip():
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)
    except Exception:
        return None


def normalize_observation_version(value: object) -> str | None:
    """Canonicalize an observation timestamp without discarding microsecond identity."""
    parsed = _parse_cycle(value)
    if parsed is None:
        return None
    return parsed.isoformat()


def _day0_conditioning_identity(
    *,
    source: object | None,
    observation_time: object | None,
    observed_extreme_c: object | None,
    unit: object | None,
) -> str | None:
    """Return the Day0 posterior-conditioning identity, or None when incomplete."""
    normalized_time = normalize_observation_version(observation_time)
    normalized_source = str(source or "").strip()
    normalized_unit = str(unit or "").strip().upper()
    if not normalized_time or not normalized_source or not normalized_unit:
        return None
    try:
        normalized_extreme = round(float(observed_extreme_c), 9)
    except (TypeError, ValueError):
        return None
    return json.dumps(
        {
            "observation_time": normalized_time,
            "observed_extreme_c": normalized_extreme,
            "source": normalized_source,
            "unit": normalized_unit,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _active_day0_provisional_or_conditioning(
    provenance: object,
) -> Mapping[str, object] | None:
    """Choose the Day0 posterior evidence authoritative for completion checks."""
    if not isinstance(provenance, Mapping):
        return None
    provisional = provenance.get("day0_provisional_observation")
    if isinstance(provisional, Mapping) and provisional.get("active") is True:
        return provisional
    conditioning = provenance.get("day0_conditioning")
    return conditioning if isinstance(conditioning, Mapping) else None


def _ensure_day0_conditioning_identity_column(conn: sqlite3.Connection) -> None:
    """Add the additive liveness column to pre-hotfix forecast DBs exactly once."""
    columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(cycle_advance_enqueues)")
    }
    if _DAY0_CONDITIONING_IDENTITY_COLUMN not in columns:
        conn.execute(
            "ALTER TABLE cycle_advance_enqueues "
            f"ADD COLUMN {_DAY0_CONDITIONING_IDENTITY_COLUMN} TEXT"
        )


def _staged_cycle_advance_seed_paths(
    *,
    seed_path: Path,
    city: str,
    target_date: str,
    metric: str,
    computed_at: datetime,
    seed_name,
) -> tuple[Path, Path]:
    """Allocate an owner-unique hidden stage and its queue-visible final path."""
    base_name = seed_name(
        {"city": city, "target_date": target_date, "temperature_metric": metric},
        computed_at=computed_at,
    )
    base = Path(base_name)
    owned_name = f"{base.stem}.enqueue-{uuid.uuid4().hex}{base.suffix}"
    visible = seed_path / owned_name
    return seed_path / _CYCLE_ADVANCE_STAGING_DIR / owned_name, visible


def _publish_staged_cycle_advance_seed_if_owned(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    metric: str,
    target_cycle_iso: str,
    staged_seed_file: Path,
    visible_seed_file: Path,
    identity: str | None,
) -> bool:
    """Atomically expose only a seed still owned by its durable enqueue marker."""
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT seed_file, day0_conditioning_identity_json
            FROM cycle_advance_enqueues
            WHERE city = ? AND target_date = ? AND metric = ? AND target_cycle_time = ?
            LIMIT 1
            """,
            (city, target_date, metric, target_cycle_iso),
        ).fetchone()
        recorded_seed = (
            None
            if row is None
            else (row["seed_file"] if hasattr(row, "keys") else row[0])
        )
        recorded_identity = (
            None
            if row is None
            else (
                row["day0_conditioning_identity_json"] if hasattr(row, "keys") else row[1]
            )
        )
        if str(recorded_seed or "") != str(visible_seed_file) or (
            identity is not None and recorded_identity != identity
        ):
            conn.rollback()
            return False
        if staged_seed_file.exists() and not visible_seed_file.exists():
            visible_seed_file.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged_seed_file, visible_seed_file)
        published = visible_seed_file.exists()
        conn.commit()
        return published
    except Exception:
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        return False


def _discard_unpublished_cycle_advance_stage(staged_seed_file: Path) -> None:
    """Remove only this attempt's UUID-private, never-queue-visible staging file."""
    if staged_seed_file.parent.name == _CYCLE_ADVANCE_STAGING_DIR:
        staged_seed_file.unlink(missing_ok=True)


def _marker_owned_cycle_advance_stage_path(seed_file: Path) -> Path | None:
    """Return the private stage path only for UUID-owned staged enqueue outputs."""
    if ".enqueue-" not in seed_file.stem:
        return None
    return seed_file.parent / _CYCLE_ADVANCE_STAGING_DIR / seed_file.name


def _latest_posterior_matches_day0_conditioning(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    metric: str,
    identity: str,
    target_cycle_iso: str,
    as_of: datetime | None,
) -> bool:
    """Whether the current live posterior consumed this Day0 evidence at this cycle."""
    if as_of is None:
        return False
    target_cycle = _parse_cycle(target_cycle_iso)
    if target_cycle is None:
        return False
    as_of_iso = as_of.astimezone(UTC).isoformat()
    try:
        row = conn.execute(
            """
            SELECT provenance_json, source_cycle_time
            FROM forecast_posteriors
            WHERE source_id = ? AND city = ? AND target_date = ? AND temperature_metric = ?
              AND runtime_layer = 'live'
              AND computed_at <= ?
            ORDER BY computed_at DESC, posterior_id DESC
            LIMIT 1
            """,
            (SOURCE_ID, city, target_date, metric, as_of_iso),
        ).fetchone()
        if row is None:
            return False
        raw = row["provenance_json"] if hasattr(row, "keys") else row[0]
        provenance = json.loads(str(raw or "{}"))
        conditioning = _active_day0_provisional_or_conditioning(provenance)
        if conditioning is None:
            return False
        anchor_artifact_id = int(
            provenance.get("openmeteo_anchor_artifact_id") or 0
        )
        consumed_cycle = _parse_cycle(
            row["source_cycle_time"] if hasattr(row, "keys") else row[1]
        )
        return (
            anchor_artifact_id > 0
            and consumed_cycle is not None
            and consumed_cycle >= target_cycle
            and _day0_conditioning_identity(
                source=conditioning.get("source"),
                observation_time=conditioning.get("observation_time"),
                observed_extreme_c=conditioning.get("observed_extreme_c"),
                unit=conditioning.get("unit"),
            )
            == identity
        )
    except (sqlite3.Error, TypeError, ValueError, json.JSONDecodeError):
        return False


def consumed_cycle_dt(value: str) -> datetime:
    """Parse a consumed/target cycle ISO string back to a UTC datetime for comparison. The verdict
    serializes cycles to ISO; the family-scope gate compares against them, so we round-trip here.
    Raises on an unparseable value (the verdict produced it, so it must parse — fail-loud)."""
    parsed = _parse_cycle(value)
    if parsed is None:
        raise ValueError(f"unparseable consumed cycle: {value!r}")
    return parsed


def _fresh_enough_to_retry_held_reheal(enqueued_at: object, *, now: datetime | None = None) -> bool:
    """Bound same-scope held re-heal retries so one failed materialization cannot flood the queue."""
    parsed = _parse_cycle(enqueued_at)
    if parsed is None:
        return True
    return (now or datetime.now(tz=UTC)).astimezone(UTC) - parsed >= _HELD_REHEAL_COOLDOWN


def _per_leg_max_cycle(conn: sqlite3.Connection, source_id: str) -> datetime | None:
    """MAX(source_cycle_time) ingested for one raw-artifact leg (None when absent). Fail-soft."""
    try:
        row = conn.execute(
            "SELECT MAX(source_cycle_time) FROM raw_forecast_artifacts WHERE source_id = ?",
            (source_id,),
        ).fetchone()
    except Exception:
        return None
    if row is None:
        return None
    return _parse_cycle(row[0])


def freshest_materializable_cycle(conn: sqlite3.Connection) -> datetime | None:
    """The freshest in-universe cycle for which the current live raw artifact leg is ingested."""

    return _per_leg_max_cycle(conn, _ANCHOR_LEG_SOURCE_ID)


def family_materializable_cycle(
    manifests,
    *,
    city: str,
    target_date: str,
    metric: str,
    city_timezone: str | None = None,
    expected_identity,
    latest_manifest,
) -> tuple[datetime | None, tuple[tuple[str, str], ...]]:
    """FINDING 2 (external review 2026-06-12) — the materializable cycle AT FAMILY SCOPE.

    This is the SAME authority, narrowed to a scope: a cycle is materializable for THIS family iff
    the current live dependency identity's raw artifact leg has a manifest for THIS
    (city, target_date). After the AIFS removal, that is the OM9 anchor leg. Returns
    (cycle, missing_legs). cycle is None when the live leg's manifest is absent for the family;
    missing_legs is the tuple of (role, source_id) legs that were absent.
    """
    expected = expected_identity(metric)
    legs = (("openmeteo_ifs9_anchor", expected["openmeteo_ifs9_anchor"]),)
    leg_cycles: list[datetime] = []
    missing: list[tuple[str, str]] = []
    for role, identity in legs:
        man = latest_manifest(
            manifests,
            source_id=identity.source_id,
            data_version=identity.data_version,
            city=city,
            target_date=target_date,
            city_timezone=city_timezone,
        )
        if man is None:
            missing.append((role, str(identity.source_id)))
            continue
        cyc = man.source_cycle_time
        if not isinstance(cyc, datetime):
            cyc = _parse_cycle(cyc)
        if cyc is None:
            missing.append((role, str(identity.source_id)))
            continue
        leg_cycles.append(cyc.astimezone(UTC) if cyc.tzinfo else cyc.replace(tzinfo=UTC))
    if missing or not leg_cycles:
        return None, tuple(missing)
    return min(leg_cycles), ()


def _latest_posterior_consumed_cycle(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    metric: str,
    as_of: datetime | None = None,
) -> datetime | None:
    """The model cycle the LATEST posterior of this scope consumed (its source_cycle_time), or
    None when there is no posterior. Fail-soft: any read/parse error -> None."""
    try:
        query = """
            SELECT source_cycle_time
            FROM forecast_posteriors
            WHERE source_id = ? AND city = ? AND target_date = ? AND temperature_metric = ?
        """
        params: list[object] = [SOURCE_ID, city, target_date, metric]
        if as_of is not None:
            query += " AND computed_at <= ?"
            params.append(as_of.astimezone(UTC).isoformat())
        query += " ORDER BY computed_at DESC LIMIT 1"
        row = conn.execute(query, params).fetchone()
    except Exception:
        return None
    if row is None:
        return None
    return _parse_cycle(row[0] if not hasattr(row, "keys") else row["source_cycle_time"])


def _latest_posterior_covers_target_cycle(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    metric: str,
    target_cycle_iso: str,
    as_of: datetime,
    minimum_computed_at: datetime | None = None,
) -> bool:
    """True when the latest posterior covers the cycle and required computation clock."""
    target_cycle = _parse_cycle(target_cycle_iso)
    if target_cycle is None:
        return False
    try:
        row = conn.execute(
            """
            SELECT source_cycle_time, computed_at
            FROM forecast_posteriors
            WHERE source_id = ?
              AND city = ?
              AND target_date = ?
              AND temperature_metric = ?
              AND computed_at <= ?
            ORDER BY computed_at DESC
            LIMIT 1
            """,
            (
                SOURCE_ID,
                city,
                target_date,
                metric,
                as_of.astimezone(UTC).isoformat(),
            ),
        ).fetchone()
    except Exception:
        return False
    if row is None:
        return False
    consumed_cycle = _parse_cycle(
        row["source_cycle_time"] if hasattr(row, "keys") else row[0]
    )
    if consumed_cycle is None or consumed_cycle < target_cycle:
        return False
    if minimum_computed_at is None:
        return True
    computed_at = _parse_cycle(row["computed_at"] if hasattr(row, "keys") else row[1])
    return computed_at is not None and computed_at >= minimum_computed_at.astimezone(UTC)


def _day0_enqueue_owner_request_check(
    *,
    city: str,
    target_date: str,
    metric: str,
    target_cycle_iso: str,
    seed_file: str,
    identity: str | None,
) -> _Day0EnqueueOwnerRequestCheck:
    """Classify whether this exact Day0 enqueue owner still has a live queue request.

    SCOPE: one (city, target_date, metric, target_cycle, seed_file, conditioning identity)
    witness. DRAIN: the materialization queue moves a completed/failed request out of requests or
    inflight, or canonically recovers an abandoned batch back into requests. RESET: one complete,
    error-free scan under the queue's claim lock proving the exact witness absent permits marker
    withdrawal. Any lock/config/filesystem/JSON uncertainty is INDETERMINATE and retains the owner.
    Batch claimed_at is not a per-request execution clock: a request later than the worker width
    may not have started, so every exact witness in requests or inflight remains ACTIVE until the
    queue moves it.
    """
    try:
        from src.data.replacement_forecast_production import (  # noqa: PLC0415
            _replacement_forecast_live_materialization_queue_config,
        )

        cfg = _replacement_forecast_live_materialization_queue_config()
        request_dir = cfg.get("request_dir")
        inflight_dir = cfg.get("inflight_dir")
        if request_dir is None or inflight_dir is None:
            return _Day0EnqueueOwnerRequestCheck(
                _Day0EnqueueOwnerRequestState.INDETERMINATE,
                "DAY0_ENQUEUE_OWNER_REQUEST_CONFIG_INCOMPLETE",
            )
        request_path = Path(str(request_dir))
        inflight_path = Path(str(inflight_dir))
    except Exception as exc:
        return _Day0EnqueueOwnerRequestCheck(
            _Day0EnqueueOwnerRequestState.INDETERMINATE,
            f"DAY0_ENQUEUE_OWNER_REQUEST_CONFIG_READ_FAILED:{type(exc).__name__}",
        )

    expected_witness = {
        "city": city,
        "target_date": target_date,
        "metric": metric,
        "target_cycle_time": target_cycle_iso,
        "seed_file": seed_file,
        "conditioning_identity": identity,
    }

    def _request_check(request_file: Path, *, location: str) -> _Day0EnqueueOwnerRequestCheck:
        try:
            raw = request_file.read_text(encoding="utf-8")
        except FileNotFoundError:
            return _Day0EnqueueOwnerRequestCheck(
                _Day0EnqueueOwnerRequestState.INACTIVE,
                f"DAY0_ENQUEUE_OWNER_REQUEST_{location}_ABSENT",
            )
        except OSError as exc:
            return _Day0EnqueueOwnerRequestCheck(
                _Day0EnqueueOwnerRequestState.INDETERMINATE,
                f"DAY0_ENQUEUE_OWNER_REQUEST_{location}_READ_FAILED:{type(exc).__name__}",
            )
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            return _Day0EnqueueOwnerRequestCheck(
                _Day0EnqueueOwnerRequestState.INDETERMINATE,
                f"DAY0_ENQUEUE_OWNER_REQUEST_{location}_JSON_INVALID:{type(exc).__name__}",
            )
        if not isinstance(payload, Mapping):
            return _Day0EnqueueOwnerRequestCheck(
                _Day0EnqueueOwnerRequestState.INDETERMINATE,
                f"DAY0_ENQUEUE_OWNER_REQUEST_{location}_PAYLOAD_INVALID",
            )
        witness = payload.get("day0_enqueue_owner_witness")
        if not isinstance(witness, Mapping):
            return _Day0EnqueueOwnerRequestCheck(
                _Day0EnqueueOwnerRequestState.INDETERMINATE,
                f"DAY0_ENQUEUE_OWNER_REQUEST_{location}_WITNESS_INVALID",
            )
        if {key: witness.get(key) for key in expected_witness} == expected_witness:
            return _Day0EnqueueOwnerRequestCheck(
                _Day0EnqueueOwnerRequestState.ACTIVE,
                f"DAY0_ENQUEUE_OWNER_REQUEST_{location}_ACTIVE",
            )
        return _Day0EnqueueOwnerRequestCheck(
            _Day0EnqueueOwnerRequestState.INACTIVE,
            f"DAY0_ENQUEUE_OWNER_REQUEST_{location}_OTHER_OWNER",
        )

    def _scan() -> _Day0EnqueueOwnerRequestCheck:
        try:
            batches = tuple(inflight_path.iterdir())
        except FileNotFoundError:
            batches = ()
        except OSError as exc:
            return _Day0EnqueueOwnerRequestCheck(
                _Day0EnqueueOwnerRequestState.INDETERMINATE,
                f"DAY0_ENQUEUE_OWNER_REQUEST_INFLIGHT_SCAN_FAILED:{type(exc).__name__}",
            )
        pending = _request_check(
            request_path / Path(seed_file).name,
            location="PENDING",
        )
        if pending.state is not _Day0EnqueueOwnerRequestState.INACTIVE:
            return pending
        for batch_path in batches:
            try:
                entry_mode = batch_path.stat().st_mode
            except FileNotFoundError:
                # The consumer may finish and remove one claimed batch after
                # the parent snapshot. Its exact request is absent from this
                # scan; the mandatory second full scan below closes the race.
                continue
            except OSError as exc:
                return _Day0EnqueueOwnerRequestCheck(
                    _Day0EnqueueOwnerRequestState.INDETERMINATE,
                    f"DAY0_ENQUEUE_OWNER_REQUEST_INFLIGHT_ENTRY_FAILED:{type(exc).__name__}",
                )
            if not stat.S_ISDIR(entry_mode):
                continue
            claimed = _request_check(
                batch_path / Path(seed_file).name,
                location="INFLIGHT",
            )
            if claimed.state is not _Day0EnqueueOwnerRequestState.INACTIVE:
                return claimed
        return _Day0EnqueueOwnerRequestCheck(
            _Day0EnqueueOwnerRequestState.INACTIVE,
            "DAY0_ENQUEUE_OWNER_REQUEST_ABSENT",
        )

    try:
        from src.data.replacement_forecast_live_materialization_queue import (  # noqa: PLC0415
            _queue_lock,
        )

        with _queue_lock(request_path.parent / ".materialization_queue.lock") as acquired:
            if not acquired:
                return _Day0EnqueueOwnerRequestCheck(
                    _Day0EnqueueOwnerRequestState.INDETERMINATE,
                    "DAY0_ENQUEUE_OWNER_REQUEST_QUEUE_LOCK_BUSY",
                )
            return _scan()
    except OSError as exc:
        return _Day0EnqueueOwnerRequestCheck(
            _Day0EnqueueOwnerRequestState.INDETERMINATE,
            f"DAY0_ENQUEUE_OWNER_REQUEST_QUEUE_LOCK_FAILED:{type(exc).__name__}",
        )


def _delete_missing_owned_cycle_advance_marker(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    metric: str,
    target_cycle_iso: str,
    seed_file: str,
) -> bool:
    """Atomically delete only the vanished UUID owner's marker before a new build starts."""
    try:
        if conn.in_transaction:
            conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            """
            DELETE FROM cycle_advance_enqueues
             WHERE city = ?
               AND target_date = ?
               AND metric = ?
               AND target_cycle_time = ?
               AND seed_file = ?
            """,
            (city, target_date, metric, target_cycle_iso, seed_file),
        )
        conn.commit()
        return cursor.rowcount == 1
    except sqlite3.Error:
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        return False


def scope_needs_cycle_advance(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    metric: str,
    freshest_cycle: datetime,
) -> dict[str, object]:
    """THE single comparison: does this scope's latest posterior need re-materialization because a
    STRICTLY NEWER materializable cycle now exists?

    Returns {needs_advance, consumed_cycle, target_cycle}. needs_advance is True iff the scope has a
    posterior AND its consumed cycle is strictly older than ``freshest_cycle``. A scope with no
    posterior is NOT advanced here (it is a fresh-seed case the seed discovery owns). Fail-soft.
    """
    consumed = _latest_posterior_consumed_cycle(
        conn, city=city, target_date=target_date, metric=metric
    )
    if consumed is None:
        return {"needs_advance": False, "consumed_cycle": None, "target_cycle": None}
    needs = consumed < freshest_cycle
    return {
        "needs_advance": needs,
        "consumed_cycle": consumed.isoformat(),
        "target_cycle": freshest_cycle.isoformat(),
    }


def _held_position_families(conn_trades: sqlite3.Connection) -> set[tuple[str, str, str]]:
    """The (city, target_date, temperature_metric) families with a HELD position right now.

    Read-only from zeus_trades.position_current. A family is HELD only when it has chain-confirmed
    economic exposure. Pending entries, local-only rows, and open-row ghosts are deliberately
    excluded: new-money redecision admission comes from the positive-edge screen, while held-family
    admission is reserved for money already at risk. Fail-soft: any read/schema error -> empty set
    (no prioritization, never a crash).
    """
    try:
        cols = {
            str(row[1])
            for row in conn_trades.execute("PRAGMA table_info(position_current)").fetchall()
        }
        if "position_current" not in {
            str(row[0])
            for row in conn_trades.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='position_current'"
            ).fetchall()
        }:
            return set()
        required_chain_cols = {"chain_state", "chain_shares", "chain_cost_basis_usd"}
        if not required_chain_cols.issubset(cols):
            return set()
        # T5 (docs/rebuild/quarantine_excision_2026-07-11.md): this used to
        # also OR in a phase='quarantined' branch — retired, DB CHECK no
        # longer admits the literal post-migration.
        chain_state_values = tuple(sorted(CURRENT_MONEY_RISK_CHAIN_STATES))
        chain_placeholders = ",".join("?" for _ in chain_state_values)
        rows = conn_trades.execute(
            f"""
            SELECT DISTINCT city, target_date, temperature_metric
            FROM position_current
            WHERE COALESCE(phase, '') IN ('active', 'day0_window', 'pending_exit')
              AND COALESCE(chain_state, '') IN ({chain_placeholders})
              AND COALESCE(chain_shares, 0) > 0
              AND COALESCE(chain_cost_basis_usd, 0) > 0
              AND city IS NOT NULL AND target_date IS NOT NULL
              AND temperature_metric IS NOT NULL
            """,
            chain_state_values,
        ).fetchall()
    except Exception as exc:  # noqa: BLE001
        # FINDING 2 / MEDIUM (external review 2026-06-12): a held-family read FAILURE silently
        # dropped held-position priority — the families whose stale belief most directly risks
        # money would be processed as if NO position were held (nearest-target-first only),
        # losing their re-materialization priority WITHOUT any signal. The poll must NOT crash on
        # this (prioritization is best-effort), but the consequence must be LOUD so the dropped
        # priority is diagnosable, not invisible.
        _LOG.error(
            "cycle-advance HELD-position read FAILED — held families lose re-materialization "
            "PRIORITY this tick (processed as non-held, nearest-target-first only); stale held "
            "belief may not be refreshed first: %s",
            exc,
        )
        return set()
    held: set[tuple[str, str, str]] = set()
    for r in rows:
        try:
            held.add((str(r[0]), str(r[1]), str(r[2])))
        except Exception:
            continue
    return held


def _already_enqueued(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    metric: str,
    target_cycle_iso: str,
    allow_missing_seed_file_reenqueue: bool = False,
    day0_observed_extreme_observation_time: str | None = None,
    day0_observed_extreme_source: str | None = None,
    day0_observed_extreme_c: float | None = None,
    day0_observed_extreme_unit: str | None = None,
    as_of: datetime | None = None,
    minimum_posterior_computed_at: datetime | None = None,
) -> bool:
    """True iff a real re-materialization seed already exists for this exact target cycle.

    A ``CYCLE_LEG_ARTIFACT_MISSING`` row is a visible, typed gap marker, not a terminal enqueue.
    Returning False for that row lets the next tick heal it when the same target cycle's artifact
    finally lands; ``_record_enqueue`` updates the marker in place under the UNIQUE bound.

    Day0 suppression requires the durable marker identity to match current
    canonical evidence. Once its seed drains, the current posterior must name
    that identity too; a completed marker alone is not completion evidence.
    """
    incoming_identity = _day0_conditioning_identity(
        source=day0_observed_extreme_source,
        observation_time=day0_observed_extreme_observation_time,
        observed_extreme_c=day0_observed_extreme_c,
        unit=day0_observed_extreme_unit,
    )
    decision_as_of = (as_of or datetime.now(tz=UTC)).astimezone(UTC)
    try:
        _ensure_day0_conditioning_identity_column(conn)
        row = conn.execute(
            """
            SELECT seed_file, reason, held_position, day0_observed_extreme_observation_time,
                   enqueued_at, day0_conditioning_identity_json
            FROM cycle_advance_enqueues
            WHERE city = ? AND target_date = ? AND metric = ? AND target_cycle_time = ?
            LIMIT 1
            """,
            (city, target_date, metric, target_cycle_iso),
        ).fetchone()
    except Exception:
        return False
    if row is None:
        return False
    seed_file = str((row["seed_file"] if hasattr(row, "keys") else row[0]) or "")
    reason = str((row["reason"] if hasattr(row, "keys") else row[1]) or "")
    if not seed_file and reason.startswith("CYCLE_LEG_ARTIFACT_MISSING:"):
        return False
    recorded_identity_raw = (
        row["day0_conditioning_identity_json"] if hasattr(row, "keys") else row[5]
    )
    recorded_identity = (
        str(recorded_identity_raw) if recorded_identity_raw not in (None, "") else None
    )
    identity_changed = (
        incoming_identity is not None and recorded_identity != incoming_identity
    )
    visible_seed_file = Path(seed_file) if seed_file else None
    owned_stage_file = (
        None
        if visible_seed_file is None
        else _marker_owned_cycle_advance_stage_path(visible_seed_file)
    )
    if visible_seed_file is not None and not visible_seed_file.exists() and owned_stage_file is not None:
        _publish_staged_cycle_advance_seed_if_owned(
            conn,
            city=city,
            target_date=target_date,
            metric=metric,
            target_cycle_iso=target_cycle_iso,
            staged_seed_file=owned_stage_file,
            visible_seed_file=visible_seed_file,
            identity=recorded_identity,
        )
    if identity_changed:
        # SCOPE: one family/cycle's exact current enqueue owner. DRAIN: its staged,
        # pending, or inflight request reaches a terminal queue move. RESET: the next
        # current-evidence tick proves that exact owner absent, removes only its marker,
        # and then admits the newer observation identity. Serializing revisions here
        # preserves full seed/request input identity while preventing owner-swap livelock.
        if (
            visible_seed_file is not None
            and (
                visible_seed_file.exists()
                or (owned_stage_file is not None and owned_stage_file.exists())
            )
        ):
            return True
        if visible_seed_file is not None:
            request_check = _day0_enqueue_owner_request_check(
                city=city,
                target_date=target_date,
                metric=metric,
                target_cycle_iso=target_cycle_iso,
                seed_file=seed_file,
                identity=recorded_identity,
            )
            if request_check.state is _Day0EnqueueOwnerRequestState.ACTIVE:
                return True
            if request_check.state is _Day0EnqueueOwnerRequestState.INDETERMINATE:
                _LOG.warning(
                    "superseded day0 enqueue owner request INDETERMINATE; retaining marker "
                    "city=%s target_date=%s metric=%s target_cycle=%s reason=%s",
                    city,
                    target_date,
                    metric,
                    target_cycle_iso,
                    request_check.reason,
                )
                return True
        if visible_seed_file is not None and owned_stage_file is not None:
            _delete_missing_owned_cycle_advance_marker(
                conn,
                city=city,
                target_date=target_date,
                metric=metric,
                target_cycle_iso=target_cycle_iso,
                seed_file=seed_file,
            )
        return False
    if incoming_identity is not None:
        if visible_seed_file is not None and visible_seed_file.exists():
            return True
        if _latest_posterior_matches_day0_conditioning(
            conn,
            city=city,
            target_date=target_date,
            metric=metric,
            identity=incoming_identity,
            target_cycle_iso=target_cycle_iso,
            as_of=decision_as_of,
        ):
            return True
        if visible_seed_file is not None:
            request_check = _day0_enqueue_owner_request_check(
                city=city,
                target_date=target_date,
                metric=metric,
                target_cycle_iso=target_cycle_iso,
                seed_file=seed_file,
                identity=incoming_identity,
            )
            if request_check.state is _Day0EnqueueOwnerRequestState.ACTIVE:
                return True
            if request_check.state is _Day0EnqueueOwnerRequestState.INDETERMINATE:
                _LOG.warning(
                    "day0 enqueue owner request INDETERMINATE; retaining marker "
                    "city=%s target_date=%s metric=%s target_cycle=%s reason=%s",
                    city,
                    target_date,
                    metric,
                    target_cycle_iso,
                    request_check.reason,
                )
                return True
        if visible_seed_file is not None and owned_stage_file is not None:
            _delete_missing_owned_cycle_advance_marker(
                conn,
                city=city,
                target_date=target_date,
                metric=metric,
                target_cycle_iso=target_cycle_iso,
                seed_file=seed_file,
            )
        return False
    if day0_observed_extreme_observation_time is not None:
        incoming_version = normalize_observation_version(day0_observed_extreme_observation_time)
        recorded_version = normalize_observation_version(
            row["day0_observed_extreme_observation_time"] if hasattr(row, "keys") else row[3]
        )
        # Both normalized to fixed-width UTC ISO => lexicographic compare == instant compare.
        if incoming_version is not None and (
            recorded_version is None or incoming_version > recorded_version
        ):
            return False
    if visible_seed_file is not None and visible_seed_file.exists():
        return True
    if visible_seed_file is not None and owned_stage_file is not None:
        if minimum_posterior_computed_at is not None:
            request_check = _day0_enqueue_owner_request_check(
                city=city,
                target_date=target_date,
                metric=metric,
                target_cycle_iso=target_cycle_iso,
                seed_file=seed_file,
                identity=None,
            )
            if request_check.state is _Day0EnqueueOwnerRequestState.ACTIVE:
                return True
            if request_check.state is _Day0EnqueueOwnerRequestState.INDETERMINATE:
                _LOG.warning(
                    "same-cycle held recompute request INDETERMINATE; retaining marker "
                    "city=%s target_date=%s metric=%s target_cycle=%s reason=%s",
                    city,
                    target_date,
                    metric,
                    target_cycle_iso,
                    request_check.reason,
                )
                return True
        if _latest_posterior_covers_target_cycle(
            conn,
            city=city,
            target_date=target_date,
            metric=metric,
            target_cycle_iso=target_cycle_iso,
            as_of=decision_as_of,
            minimum_computed_at=minimum_posterior_computed_at,
        ):
            return True
        _delete_missing_owned_cycle_advance_marker(
            conn,
            city=city,
            target_date=target_date,
            metric=metric,
            target_cycle_iso=target_cycle_iso,
            seed_file=seed_file,
        )
        return False
    # HELD-POSITION RE-HEAL (live freeze fix 2026-06-21): a held (money-at-risk) marker whose seed
    # was built then processed/moved out of the live queue but produced NO posterior — the
    # single_runs serving race materializes BLOCKED on REQUIREMENTS_NOT_MET — must NOT suppress
    # re-enqueue forever, else the held belief freezes (Panama City 2026-06-22 stuck 13h+) ->
    # BELIEF_AUTHORITY_FAULT fail-closed HOLD -> reversal exit starved ("observe but not act").
    # Auto-enable the missing-seed re-enqueue for held rows, mirroring the day0 escape hatch.
    # Bounded by the upstream needs_advance/coverage gate, so a successfully materialized cycle
    # (posterior present) never reaches here to churn; a still-PRESENT pending seed also suppresses.
    held = bool((row["held_position"] if hasattr(row, "keys") else row[2]) or 0)
    if (allow_missing_seed_file_reenqueue or held) and seed_file and not Path(seed_file).exists():
        # A moved seed file is normal after the queue processed it. Re-enqueueing immediately every
        # poll tick creates a live backlog of identical failed work. Only Day0 observation-version
        # advancement bypasses this cooldown above; otherwise retry the same scope/cycle after the
        # cooling period or when a newer model cycle changes the idempotency key.
        if held and not allow_missing_seed_file_reenqueue:
            enqueued_at = row["enqueued_at"] if hasattr(row, "keys") else row[4]
            if not _fresh_enough_to_retry_held_reheal(enqueued_at):
                return True
        return False
    return True


def _promote_existing_enqueue_to_held(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    metric: str,
    target_cycle_iso: str,
) -> bool:
    """Upgrade an existing enqueue row into the held-position priority tier.

    Monitor-triggered single-family reseeds are money-at-risk work even when a
    broad cycle scanner already wrote the idempotency row first. The unique
    enqueue key must prevent duplicate seeds, not permanently freeze the row in
    the non-held tier.
    """
    before = conn.total_changes
    conn.execute(
        """
        UPDATE cycle_advance_enqueues
           SET held_position = 1,
               enqueued_at = ?
         WHERE city = ?
           AND target_date = ?
           AND metric = ?
           AND target_cycle_time = ?
           AND COALESCE(held_position, 0) != 1
        """,
        (
            datetime.now(tz=UTC).isoformat(),
            city,
            target_date,
            metric,
            target_cycle_iso,
        ),
    )
    return conn.total_changes > before


def _record_enqueue(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    metric: str,
    consumed_cycle_iso: str,
    target_cycle_iso: str,
    held_position: bool,
    seed_file: str | None,
    reason: str | None = None,
    replace_existing_seed_file: bool = False,
    day0_observed_extreme_observation_time: str | None = None,
    day0_observed_extreme_source: str | None = None,
    day0_observed_extreme_c: float | None = None,
    day0_observed_extreme_unit: str | None = None,
) -> bool:
    """Write the idempotency marker. Returns True iff this call inserted the row (False = a
    concurrent/prior enqueue already recorded it, via the UNIQUE index INSERT OR IGNORE).

    ``day0_observed_extreme_observation_time`` records the OBSERVATION VERSION this enqueue was
    built at (same-day exit-blindness fix 2026-06-23). It advances the marker so a later held/day0
    reseed at the SAME model cycle but a NEWER observed running-max version is re-enqueued by
    ``_already_enqueued`` instead of frozen.

    ``reason`` carries a typed status for the row. None for a normal successful enqueue (the
    presence of seed_file is the success signal); a typed string (FINDING 2) when the row instead
    records a per-family leg-artifact gap (CYCLE_LEG_ARTIFACT_MISSING:<source>:<cycle>) so the
    blocked family is VISIBLE in the queue rather than an invisible manifest_missing skip. Both
    share the SAME UNIQUE(scope, target_cycle) bound, so a gap row and a later success row for the
    same (scope, target-cycle) cannot both exist — the gap heals into the success on the next tick."""
    # Persist the observation version in canonical fixed-width UTC ISO so the marker comparison in
    # _already_enqueued (and the monotone replacement guard below) is instant-accurate, not
    # spelling-sensitive (consult REQ-20260623-184115 HIGH).
    day0_observed_extreme_observation_time = normalize_observation_version(
        day0_observed_extreme_observation_time
    )
    day0_conditioning_identity = _day0_conditioning_identity(
        source=day0_observed_extreme_source,
        observation_time=day0_observed_extreme_observation_time,
        observed_extreme_c=day0_observed_extreme_c,
        unit=day0_observed_extreme_unit,
    )
    _ensure_day0_conditioning_identity_column(conn)
    before = conn.total_changes
    conn.execute(
        """
        INSERT OR IGNORE INTO cycle_advance_enqueues
            (enqueued_at, city, target_date, metric, consumed_cycle_time, target_cycle_time,
             held_position, seed_file, reason, day0_observed_extreme_observation_time,
             day0_conditioning_identity_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            datetime.now(tz=UTC).isoformat(),
            city,
            target_date,
            metric,
            consumed_cycle_iso,
            target_cycle_iso,
            1 if held_position else 0,
            seed_file,
            reason,
            day0_observed_extreme_observation_time,
            day0_conditioning_identity,
        ),
    )
    if conn.total_changes > before:
        return True
    if seed_file:
        update_before = conn.total_changes
        # HELD-POSITION RE-HEAL (live freeze fix 2026-06-21): a held re-enqueue must REPLACE an
        # existing seed-built marker (the moved/BLOCKED row), pairing with the _already_enqueued
        # held re-heal above. Without this, INSERT OR IGNORE no-ops and the default NULL-seed gap
        # UPDATE below cannot rewrite a seed-bearing held row, so the re-heal never completes and
        # the held belief stays frozen.
        if replace_existing_seed_file or held_position:
            conn.execute(
                """
                UPDATE cycle_advance_enqueues
                   SET enqueued_at = ?,
                       consumed_cycle_time = ?,
                       held_position = ?,
                       seed_file = ?,
                       reason = ?,
                       day0_observed_extreme_observation_time = COALESCE(?, day0_observed_extreme_observation_time),
                       day0_conditioning_identity_json = COALESCE(?, day0_conditioning_identity_json)
                 WHERE city = ?
                   AND target_date = ?
                   AND metric = ?
                   AND target_cycle_time = ?
                   AND (
                       (
                           ? IS NULL
                           AND (
                               ? IS NULL
                               OR day0_observed_extreme_observation_time IS NULL
                               OR ? > day0_observed_extreme_observation_time
                           )
                       )
                       OR (
                           ? IS NOT NULL
                           AND (
                               day0_observed_extreme_observation_time IS NULL
                               OR ? >= day0_observed_extreme_observation_time
                           )
                           AND (
                               day0_conditioning_identity_json IS NULL
                               OR ? <> day0_conditioning_identity_json
                           )
                       )
                   )
                """,
                (
                    datetime.now(tz=UTC).isoformat(),
                    consumed_cycle_iso,
                    1 if held_position else 0,
                    seed_file,
                    reason,
                    day0_observed_extreme_observation_time,
                    day0_conditioning_identity,
                    city,
                    target_date,
                    metric,
                    target_cycle_iso,
                    day0_conditioning_identity,
                    day0_observed_extreme_observation_time,
                    day0_observed_extreme_observation_time,
                    day0_conditioning_identity,
                    day0_observed_extreme_observation_time,
                    day0_conditioning_identity,
                ),
            )
            return conn.total_changes > update_before
        conn.execute(
            """
            UPDATE cycle_advance_enqueues
               SET enqueued_at = ?,
                   consumed_cycle_time = ?,
                   held_position = ?,
                   seed_file = ?,
                   reason = ?,
                   day0_observed_extreme_observation_time = COALESCE(?, day0_observed_extreme_observation_time),
                   day0_conditioning_identity_json = COALESCE(?, day0_conditioning_identity_json)
             WHERE city = ?
               AND target_date = ?
               AND metric = ?
               AND target_cycle_time = ?
               AND seed_file IS NULL
               AND COALESCE(reason, '') LIKE 'CYCLE_LEG_ARTIFACT_MISSING:%'
            """,
            (
                datetime.now(tz=UTC).isoformat(),
                consumed_cycle_iso,
                1 if held_position else 0,
                seed_file,
                reason,
                day0_observed_extreme_observation_time,
                day0_conditioning_identity,
                city,
                target_date,
                metric,
                target_cycle_iso,
            ),
        )
        return conn.total_changes > update_before
    return False


def enqueue_cycle_advance_reseeds(
    *,
    forecast_db: Path | str,
    seed_dir: Path | str,
    raw_manifest_dir: Path | str,
    trades_db: Path | str | None = None,
    computed_at: datetime | None = None,
    limit: int = 50,
    scopes: Sequence[tuple[str, str, str]] | None = None,
    manifests: Sequence[RawForecastArtifactManifest] | None = None,
    include_missing_posterior: bool = False,
) -> dict[str, object]:
    """For every active-window target whose latest posterior consumed a STRICTLY OLDER cycle than
    the freshest materializable in-universe cycle, enqueue exactly one re-materialization seed
    (reusing the existing seed builder + seed_dir the materialize cycle drains). HELD-position
    families are processed FIRST. Idempotent per (scope, target-cycle) via cycle_advance_enqueues.

    Exact source-commit scopes may also opt into first materialization when no live posterior
    exists. The default remains newer-cycle-only so global maintenance does not duplicate seed
    discovery. Belongs in the EXISTING availability-poll lane (no new daemon). Fail-soft: any
    per-scope error is logged and skipped; the function never raises into the poll. Returns a
    compact report.
    """
    from src.data.replacement_forecast_current_target_plan import (  # noqa: PLC0415
        build_replacement_forecast_current_target_plan,
    )
    from src.data.replacement_forecast_materialization_seed_builder import (  # noqa: PLC0415
        build_replacement_forecast_materialization_seed,
        latest_baseline_coverage_for_replacement_seed,
        market_bins_for_replacement_seed,
        write_seed,
    )
    from src.data.replacement_forecast_seed_discovery import (  # noqa: PLC0415
        _day0_observed_extreme_seed_payload,
        _latest_manifest,
        _load_manifests,
        _manifest_base_dir,
        _manifest_path_value,
        _resolve_path,
        _seed_name,
    )
    from src.data.replacement_forecast_source_run_identity import (  # noqa: PLC0415
        expected_replacement_dependency_identity_by_role,
    )
    from src.state.db import _connect  # noqa: PLC0415
    from src.state.schema.v2_schema import (  # noqa: PLC0415
        ensure_replacement_forecast_live_schema,
    )

    now = (computed_at or datetime.now(tz=UTC)).astimezone(UTC)
    forecast_db = Path(forecast_db)
    seed_path = Path(seed_dir)
    raw_dir = Path(raw_manifest_dir)
    report: dict[str, object] = {
        "status": "CYCLE_ADVANCE_TRIGGER",
        "freshest_materializable_cycle": None,
        "scopes_checked": 0,
        "advances_detected": 0,
        "day0_observation_advances_detected": 0,
        "first_materializations_detected": 0,
        "held_advances_detected": 0,
        "seeds_enqueued": 0,
        "first_materialization_seeds_enqueued": 0,
        "held_seeds_enqueued": 0,
        "already_enqueued": 0,
        "manifest_missing": 0,
        "leg_artifact_missing": 0,
        "family_cycle_missing": 0,
        "family_cycle_not_newer": 0,
        "day0_skipped": 0,
        "comparison_failed": 0,
        "family_scope_check_failed": 0,
        "seed_build_failed": 0,
        "enqueued": [],
    }
    if not forecast_db.exists():
        report["status"] = "CYCLE_ADVANCE_FORECAST_DB_MISSING"
        return report

    if scopes is None:
        plan = build_replacement_forecast_current_target_plan(
            forecast_db,
            min_target_date=now.date().isoformat(),
            require_raw_artifacts=False,
            now_utc=now,
        )
        if plan.status == "BLOCKED":
            report["status"] = "CYCLE_ADVANCE_PLAN_BLOCKED"
            report["reason_codes"] = list(plan.reason_codes)
            return report
        candidates = tuple(
            (
                str(row.city),
                str(row.target_date),
                str(row.temperature_metric),
                bool(getattr(row, "day0_observed_extreme_required", False)),
            )
            for row in plan.rows
        )
    else:
        from src.data.replacement_forecast_current_target_plan import (  # noqa: PLC0415
            _city_timezone_by_name,
            _day0_observed_extreme_required,
        )

        timezone_by_city = _city_timezone_by_name()
        candidates = tuple(
            (
                city,
                target_date,
                metric,
                _day0_observed_extreme_required(
                    city=city,
                    target_date=target_date,
                    timezone_by_city=timezone_by_city,
                    now_utc=now,
                ),
            )
            for city, target_date, metric in dict.fromkeys(
                (
                    str(city).strip(),
                    str(target_date).strip(),
                    str(metric).strip(),
                )
                for city, target_date, metric in scopes
                if str(city).strip()
                and str(target_date).strip()
                and str(metric).strip() in {"high", "low"}
            )
        )

    manifests = (
        _load_manifests(raw_dir, computed_at=now)
        if manifests is None
        else tuple(manifests)
    )

    # HELD-position families (priority tier i). Read-only on the trades DB (mode=ro — the trigger
    # NEVER writes zeus_trades; K1 DB split). Fail-soft to empty: prioritization is best-effort.
    held: set[tuple[str, str, str]] = set()
    if trades_db is not None and Path(trades_db).exists():
        try:
            conn_t = sqlite3.connect(f"file:{Path(trades_db)}?mode=ro", uri=True, timeout=5.0)
            try:
                held = _held_position_families(conn_t)
            finally:
                conn_t.close()
        except Exception as exc:  # noqa: BLE001 — prioritization is best-effort, never fatal
            _LOG.debug("cycle-advance held-position read failed (no prioritization): %s", exc)

    conn = _connect(forecast_db, write_class="live")
    conn.row_factory = sqlite3.Row
    try:
        ensure_replacement_forecast_live_schema(conn)
        freshest = freshest_materializable_cycle(conn)
        if freshest is None:
            report["status"] = "CYCLE_ADVANCE_NO_MATERIALIZABLE_CYCLE"
            return report
        report["freshest_materializable_cycle"] = freshest.isoformat()

        # PRIORITY ORDER: HELD families first (tier i), then nearest-target-first (mirrors the
        # seed-budget K-decision — far-date non-tradeable scopes must not starve the tradeable day0/day1
        # money scopes of the per-tick enqueue budget). A single sort key encodes both tiers.
        def _priority_key(candidate) -> tuple:
            scope = candidate[:3]
            is_held = scope in held
            return (0 if is_held else 1, scope[1], scope[0], scope[2])

        enqueued = 0
        for city, target_date, metric, day0_required in sorted(
            candidates,
            key=_priority_key,
        ):
            if enqueued >= max(1, int(limit)):
                break
            scope = (city, target_date, metric)
            is_held = scope in held
            day0_payload: dict[str, object] = {}
            day0_observation_time: str | None = None
            # DAY0 GUARD: a started local day's scope must re-materialize with the canonical
            # observed-extreme hard fact. Skipping here permanently strands same-day stale
            # posteriors even when observation_instants already has the required truth.
            if day0_required:
                payload = _day0_observed_extreme_seed_payload(
                    city=city,
                    target_date=target_date,
                    metric=metric,
                    computed_at=now,
                )
                if payload is None:
                    report["day0_skipped"] = int(report["day0_skipped"]) + 1
                    continue
                day0_payload = payload
                day0_observation_time = str(
                    payload.get("day0_observed_extreme_observation_time") or ""
                ) or None
            day0_identity = _day0_conditioning_identity(
                source=day0_payload.get("day0_observed_extreme_source"),
                observation_time=day0_observation_time,
                observed_extreme_c=day0_payload.get("day0_observed_extreme_c"),
                unit=day0_payload.get("day0_observed_extreme_unit"),
            )
            report["scopes_checked"] = int(report["scopes_checked"]) + 1
            try:
                verdict = scope_needs_cycle_advance(
                    conn, city=city, target_date=target_date, metric=metric, freshest_cycle=freshest
                )
            except Exception as exc:  # noqa: BLE001 — per-scope fail-soft
                report["comparison_failed"] = int(report.get("comparison_failed", 0)) + 1
                _LOG.debug("cycle-advance comparison failed for %s/%s/%s: %s", city, target_date, metric, exc)
                continue
            missing_posterior = verdict.get("consumed_cycle") is None
            # A Day0 canonical observation is a separate causal clock from the
            # model source cycle.  A fresh plateau print can advance its exact
            # conditioning identity while the family remains on the same model
            # cycle; let _already_enqueued bind that identity to the marker and
            # completed posterior instead of suppressing this repair here.
            day0_observation_advance_candidate = day0_identity is not None
            if not verdict["needs_advance"] and not (
                include_missing_posterior and scopes is not None and missing_posterior
            ) and not day0_observation_advance_candidate:
                continue
            if missing_posterior:
                report["first_materializations_detected"] = (
                    int(report["first_materializations_detected"]) + 1
                )
            else:
                if verdict["needs_advance"]:
                    report["advances_detected"] = int(report["advances_detected"]) + 1
                    if is_held:
                        report["held_advances_detected"] = (
                            int(report["held_advances_detected"]) + 1
                        )
                elif day0_observation_advance_candidate:
                    report["day0_observation_advances_detected"] = (
                        int(report["day0_observation_advances_detected"]) + 1
                    )
            consumed_cycle_iso = (
                "NO_LIVE_POSTERIOR"
                if missing_posterior
                else str(verdict["consumed_cycle"])
            )
            target_cycle_iso = str(
                verdict.get("target_cycle") or freshest.isoformat()
            )
            # FINDING 2 (external review 2026-06-12): the verdict above used the UNIVERSE-wide
            # freshest cycle, which can be a FALSE advance signal when a leg's raw artifact is
            # missing for THIS family at that cycle. Re-check materializability AT FAMILY SCOPE.
            try:
                from src.config import cities_by_name  # noqa: PLC0415

                city_cfg = cities_by_name.get(city)
                city_timezone = str(getattr(city_cfg, "timezone", "") or "") or None
                family_cycle, missing_legs = family_materializable_cycle(
                    manifests,
                    city=city,
                    target_date=target_date,
                    metric=metric,
                    city_timezone=city_timezone,
                    expected_identity=expected_replacement_dependency_identity_by_role,
                    latest_manifest=_latest_manifest,
                )
            except Exception as exc:  # noqa: BLE001 — per-scope fail-soft
                report["family_scope_check_failed"] = int(
                    report.get("family_scope_check_failed", 0)
                ) + 1
                _LOG.debug("cycle-advance family-scope check failed for %s/%s/%s: %s", city, target_date, metric, exc)
                continue
            if missing_legs:
                # A held/active family lacks one leg's raw artifact at the freshest cycle. Do NOT
                # silently increment manifest_missing — record a typed, idempotent reason row so the
                # ALWAYS-DECIDABLE gap is VISIBLE in the queue and a fetch-repair lane can act on it.
                reason = "CYCLE_LEG_ARTIFACT_MISSING:" + ",".join(
                    f"{src}@{target_cycle_iso}" for _role, src in missing_legs
                )
                report["leg_artifact_missing"] = int(report.get("leg_artifact_missing", 0)) + 1
                _LOG.error(
                    "cycle-advance LEG ARTIFACT MISSING for %s/%s/%s at cycle %s — held=%s family "
                    "cannot advance (missing legs: %s); recording typed gap (no silent skip)",
                    city, target_date, metric, target_cycle_iso, is_held,
                    [src for _role, src in missing_legs],
                )
                if not _already_enqueued(
                    conn, city=city, target_date=target_date, metric=metric,
                    target_cycle_iso=target_cycle_iso,
                    as_of=now,
                ):
                    _record_enqueue(
                        conn, city=city, target_date=target_date, metric=metric,
                        consumed_cycle_iso=consumed_cycle_iso, target_cycle_iso=target_cycle_iso,
                        held_position=is_held, seed_file=None, reason=reason,
                    )
                    conn.commit()
                continue
            # Both legs present for the family: the family-scoped cycle is the authoritative target.
            # If it is NOT strictly newer than the consumed cycle, the global verdict was a false
            # positive for this family (the fresher universe cycle was carried by OTHER cities) —
            # honest no-op, not an advance.
            if family_cycle is None:
                report["family_cycle_missing"] = int(report.get("family_cycle_missing", 0)) + 1
                continue
            if (
                not missing_posterior
                and family_cycle < consumed_cycle_dt(consumed_cycle_iso)
            ):
                report["family_cycle_not_newer"] = int(
                    report.get("family_cycle_not_newer", 0)
                ) + 1
                continue
            if (
                not missing_posterior
                and family_cycle == consumed_cycle_dt(consumed_cycle_iso)
                and not day0_observation_advance_candidate
            ):
                report["family_cycle_not_newer"] = int(
                    report.get("family_cycle_not_newer", 0)
                ) + 1
                continue
            target_cycle_iso = family_cycle.isoformat()
            if _already_enqueued(
                conn,
                city=city,
                target_date=target_date,
                metric=metric,
                target_cycle_iso=target_cycle_iso,
                allow_missing_seed_file_reenqueue=bool(day0_payload) or missing_posterior,
                day0_observed_extreme_observation_time=day0_observation_time,
                day0_observed_extreme_source=day0_payload.get("day0_observed_extreme_source"),
                day0_observed_extreme_c=day0_payload.get("day0_observed_extreme_c"),
                day0_observed_extreme_unit=day0_payload.get("day0_observed_extreme_unit"),
                as_of=now,
            ):
                report["already_enqueued"] = int(report["already_enqueued"]) + 1
                continue
            try:
                staged_seed_file, visible_seed_file = _staged_cycle_advance_seed_paths(
                    seed_path=seed_path,
                    city=city,
                    target_date=target_date,
                    metric=metric,
                    computed_at=now,
                    seed_name=_seed_name,
                )
                seed_file = _build_and_write_advance_seed(
                    conn,
                    city=city,
                    target_date=target_date,
                    metric=metric,
                    manifests=manifests,
                    raw_dir=raw_dir,
                    seed_path=seed_path,
                    computed_at=now,
                    build_seed=build_replacement_forecast_materialization_seed,
                    latest_baseline_coverage=latest_baseline_coverage_for_replacement_seed,
                    market_bins=market_bins_for_replacement_seed,
                    write_seed=write_seed,
                    latest_manifest=_latest_manifest,
                    manifest_path_value=_manifest_path_value,
                    manifest_base_dir=_manifest_base_dir,
                    resolve_path=_resolve_path,
                    seed_name=_seed_name,
                    expected_identity=expected_replacement_dependency_identity_by_role,
                    upgrade_trigger=(
                        "missing_live_posterior_reseed"
                        if missing_posterior
                        else "day0_observation_advanced"
                        if day0_observation_advance_candidate
                        and not verdict["needs_advance"]
                        else "newer_cycle_ingested"
                    ),
                    day0_observed_extreme_c=day0_payload.get("day0_observed_extreme_c"),
                    day0_observed_extreme_source=day0_payload.get("day0_observed_extreme_source"),
                    day0_observed_extreme_observation_time=day0_observation_time,
                    day0_observed_extreme_sample_count=day0_payload.get(
                        "day0_observed_extreme_sample_count"
                    ),
                    day0_observed_extreme_unit=day0_payload.get("day0_observed_extreme_unit"),
                    day0_observation_state=day0_payload.get("day0_observation_state"),
                    output_path=staged_seed_file,
                    cycle_advance_enqueue_owner=True,
                )
            except Exception as exc:  # noqa: BLE001 — per-scope fail-soft
                report["seed_build_failed"] = int(report.get("seed_build_failed", 0)) + 1
                _LOG.debug("cycle-advance seed build failed for %s/%s/%s: %s", city, target_date, metric, exc)
                continue
            if seed_file is None:
                report["manifest_missing"] = int(report["manifest_missing"]) + 1
                continue
            inserted = _record_enqueue(
                conn,
                city=city,
                target_date=target_date,
                metric=metric,
                consumed_cycle_iso=consumed_cycle_iso,
                target_cycle_iso=target_cycle_iso,
                held_position=is_held,
                seed_file=str(visible_seed_file),
                reason=(
                    "MISSING_LIVE_POSTERIOR"
                    if missing_posterior
                    else "DAY0_OBSERVATION_ADVANCED"
                    if day0_observation_advance_candidate and not verdict["needs_advance"]
                    else None
                ),
                replace_existing_seed_file=bool(day0_payload) or missing_posterior,
                day0_observed_extreme_observation_time=day0_observation_time,
                day0_observed_extreme_source=day0_payload.get("day0_observed_extreme_source"),
                day0_observed_extreme_c=day0_payload.get("day0_observed_extreme_c"),
                day0_observed_extreme_unit=day0_payload.get("day0_observed_extreme_unit"),
            )
            conn.commit()
            if inserted:
                _publish_staged_cycle_advance_seed_if_owned(
                    conn,
                    city=city,
                    target_date=target_date,
                    metric=metric,
                    target_cycle_iso=target_cycle_iso,
                    staged_seed_file=staged_seed_file,
                    visible_seed_file=visible_seed_file,
                    identity=_day0_conditioning_identity(
                        source=day0_payload.get("day0_observed_extreme_source"),
                        observation_time=day0_observation_time,
                        observed_extreme_c=day0_payload.get("day0_observed_extreme_c"),
                        unit=day0_payload.get("day0_observed_extreme_unit"),
                    ),
                )
            else:
                _discard_unpublished_cycle_advance_stage(staged_seed_file)
            if inserted:
                enqueued += 1
                report["seeds_enqueued"] = int(report["seeds_enqueued"]) + 1
                if missing_posterior:
                    report["first_materialization_seeds_enqueued"] = (
                        int(report["first_materialization_seeds_enqueued"]) + 1
                    )
                if is_held:
                    report["held_seeds_enqueued"] = int(report["held_seeds_enqueued"]) + 1
                report["enqueued"].append(
                    {
                        "city": city,
                        "target_date": target_date,
                        "metric": metric,
                        "held_position": is_held,
                        "consumed_cycle": (
                            None if missing_posterior else consumed_cycle_iso
                        ),
                        "target_cycle": target_cycle_iso,
                        "seed_file": str(visible_seed_file),
                    }
                )
            else:
                report["already_enqueued"] = int(report["already_enqueued"]) + 1
    finally:
        conn.close()
    return report


def enqueue_single_family_cycle_advance_reseed(
    *,
    forecast_db: Path | str,
    seed_dir: Path | str,
    raw_manifest_dir: Path | str,
    city: str,
    target_date: str,
    metric: str,
    computed_at: datetime | None = None,
    day0_observed_extreme_c: float | None = None,
    day0_observed_extreme_source: str | None = None,
    day0_observed_extreme_observation_time: str | None = None,
    day0_observed_extreme_sample_count: int | None = None,
    day0_observed_extreme_unit: str | None = None,
    day0_observation_state: str | None = None,
    held_position: bool = False,
    minimum_posterior_computed_at: datetime | None = None,
) -> dict[str, object]:
    """ALWAYS-DECIDABLE invariant — Build 2 (operator law 2026-06-12). Single-family variant of
    ``enqueue_cycle_advance_reseeds``: when the reactor/monitor finds ONE family blocked on a
    STALE or ABSENT replacement posterior, materialize THAT family's posterior onto the freshest
    materializable cycle — no plan scan, no fan-out. Same seed builder, same idempotency marker
    (``cycle_advance_enqueues`` UNIQUE(scope, target_cycle)) as the poll-lane batch variant, so a
    family already enqueued by the poll never double-enqueues here and vice-versa.

    Fail-soft throughout: any error returns a status dict, never raises into the reactor cycle.
    Returns a compact report ({status, enqueued, seed_file, ...}).
    """
    from src.data.replacement_forecast_materialization_seed_builder import (  # noqa: PLC0415
        build_replacement_forecast_materialization_seed,
        latest_baseline_coverage_for_replacement_seed,
        market_bins_for_replacement_seed,
        write_seed,
    )
    from src.data.replacement_forecast_seed_discovery import (  # noqa: PLC0415
        _latest_manifest,
        _manifest_base_dir,
        _manifest_path_value,
        _resolve_path,
        _seed_name,
    )
    from src.data.replacement_forecast_source_run_identity import (  # noqa: PLC0415
        expected_replacement_dependency_identity_by_role,
    )
    from src.state.db import _connect  # noqa: PLC0415
    from src.state.schema.v2_schema import (  # noqa: PLC0415
        ensure_replacement_forecast_live_schema,
    )

    now = (computed_at or datetime.now(tz=UTC)).astimezone(UTC)
    forecast_db = Path(forecast_db)
    seed_path = Path(seed_dir)
    raw_dir = Path(raw_manifest_dir)
    city = str(city)
    target_date = str(target_date)
    metric = str(metric)
    has_day0_evidence = (
        day0_observed_extreme_c is not None or day0_observation_state is not None
    )
    report: dict[str, object] = {
        "status": "SINGLE_FAMILY_CYCLE_ADVANCE",
        "city": city,
        "target_date": target_date,
        "metric": metric,
        "held_position": bool(held_position),
        "enqueued": False,
    }
    if minimum_posterior_computed_at is not None:
        if (
            minimum_posterior_computed_at.tzinfo is None
            or minimum_posterior_computed_at.utcoffset() is None
        ):
            report["status"] = "SAME_CYCLE_RECOMPUTE_CUTOFF_INVALID"
            return report
        minimum_posterior_computed_at = minimum_posterior_computed_at.astimezone(UTC)
        if not held_position:
            report["status"] = "SAME_CYCLE_RECOMPUTE_REQUIRES_HELD_POSITION"
            return report
    if not forecast_db.exists():
        report["status"] = "CYCLE_ADVANCE_FORECAST_DB_MISSING"
        return report
    if metric not in {"high", "low"}:
        report["status"] = "CYCLE_ADVANCE_METRIC_INVALID"
        return report

    conn = _connect(forecast_db, write_class="live")
    conn.row_factory = sqlite3.Row
    try:
        ensure_replacement_forecast_live_schema(conn)
        expected = expected_replacement_dependency_identity_by_role(metric)
        manifests = _family_manifests_from_db(
            conn,
            city=city,
            identity=expected["openmeteo_ifs9_anchor"],
            computed_at=now,
        )
        freshest = freshest_materializable_cycle(conn)
        if freshest is None:
            report["status"] = "CYCLE_ADVANCE_NO_MATERIALIZABLE_CYCLE"
            return report
        report["freshest_materializable_cycle"] = freshest.isoformat()
        verdict = scope_needs_cycle_advance(
            conn, city=city, target_date=target_date, metric=metric, freshest_cycle=freshest
        )
        consumed_cycle_iso = (
            str(verdict["consumed_cycle"])
            if verdict.get("consumed_cycle") is not None
            else "NO_LIVE_POSTERIOR"
        )
        target_cycle_iso = str(verdict["target_cycle"] or freshest.isoformat())
        family_cycle, missing_legs = family_materializable_cycle(
            manifests,
            city=city,
            target_date=target_date,
            metric=metric,
            expected_identity=lambda _metric: expected,
            latest_manifest=_latest_manifest,
        )
        if missing_legs:
            # Record a typed, idempotent gap row instead of a silent manifest_missing skip.
            reason = "CYCLE_LEG_ARTIFACT_MISSING:" + ",".join(
                f"{src}@{target_cycle_iso}" for _role, src in missing_legs
            )
            _LOG.error(
                "single-family cycle-advance LEG ARTIFACT MISSING for %s/%s/%s at cycle %s "
                "(missing legs: %s) — recording typed gap (no silent skip)",
                city, target_date, metric, target_cycle_iso, [src for _role, src in missing_legs],
            )
            if not _already_enqueued(
                conn,
                city=city,
                target_date=target_date,
                metric=metric,
                target_cycle_iso=target_cycle_iso,
                as_of=now,
            ):
                _record_enqueue(
                    conn, city=city, target_date=target_date, metric=metric,
                    consumed_cycle_iso=consumed_cycle_iso, target_cycle_iso=target_cycle_iso,
                    held_position=held_position, seed_file=None, reason=reason,
                )
                conn.commit()
            elif held_position:
                report["held_priority_promoted"] = _promote_existing_enqueue_to_held(
                    conn,
                    city=city,
                    target_date=target_date,
                    metric=metric,
                    target_cycle_iso=target_cycle_iso,
                )
                conn.commit()
            report["status"] = "CYCLE_ADVANCE_LEG_ARTIFACT_MISSING"
            report["reason"] = reason
            report["consumed_cycle"] = consumed_cycle_iso
            report["target_cycle"] = target_cycle_iso
            return report
        # Day0 observation time is an independent source clock. A newer global
        # forecast cycle carried by another family must not divert this family
        # around the monotone observation-time re-materialization path below.
        if not verdict["needs_advance"] or has_day0_evidence:
            if verdict.get("consumed_cycle") is not None:
                if has_day0_evidence:
                    if (
                        family_cycle is None
                        or family_cycle < consumed_cycle_dt(consumed_cycle_iso)
                    ):
                        report["status"] = "CYCLE_ADVANCE_MANIFEST_MISSING"
                        report["consumed_cycle"] = consumed_cycle_iso
                        return report
                    target_cycle_iso = family_cycle.isoformat()
                    if _already_enqueued(
                        conn,
                        city=city,
                        target_date=target_date,
                        metric=metric,
                        target_cycle_iso=target_cycle_iso,
                        allow_missing_seed_file_reenqueue=True,
                        day0_observed_extreme_observation_time=(
                            day0_observed_extreme_observation_time
                        ),
                        day0_observed_extreme_source=day0_observed_extreme_source,
                        day0_observed_extreme_c=day0_observed_extreme_c,
                        day0_observed_extreme_unit=day0_observed_extreme_unit,
                        as_of=now,
                    ):
                        report["status"] = "CYCLE_ADVANCE_NOT_NEEDED"
                        report["consumed_cycle"] = consumed_cycle_iso
                        report["target_cycle"] = target_cycle_iso
                        return report
                    staged_seed_file, visible_seed_file = _staged_cycle_advance_seed_paths(
                        seed_path=seed_path,
                        city=city,
                        target_date=target_date,
                        metric=metric,
                        computed_at=now,
                        seed_name=_seed_name,
                    )
                    seed_file = _build_and_write_advance_seed(
                        conn,
                        city=city,
                        target_date=target_date,
                        metric=metric,
                        manifests=manifests,
                        raw_dir=raw_dir,
                        seed_path=seed_path,
                        computed_at=now,
                        build_seed=build_replacement_forecast_materialization_seed,
                        latest_baseline_coverage=(
                            latest_baseline_coverage_for_replacement_seed
                        ),
                        market_bins=market_bins_for_replacement_seed,
                        write_seed=write_seed,
                        latest_manifest=_latest_manifest,
                        manifest_path_value=_manifest_path_value,
                        manifest_base_dir=_manifest_base_dir,
                        resolve_path=_resolve_path,
                        seed_name=_seed_name,
                        expected_identity=(
                            expected_replacement_dependency_identity_by_role
                        ),
                        upgrade_trigger="day0_observation_advanced",
                        day0_observed_extreme_c=day0_observed_extreme_c,
                        day0_observed_extreme_source=day0_observed_extreme_source,
                        day0_observed_extreme_observation_time=(
                            day0_observed_extreme_observation_time
                        ),
                        day0_observed_extreme_sample_count=(
                            day0_observed_extreme_sample_count
                        ),
                        day0_observed_extreme_unit=day0_observed_extreme_unit,
                        day0_observation_state=day0_observation_state,
                        output_path=staged_seed_file,
                        cycle_advance_enqueue_owner=True,
                    )
                    if seed_file is None:
                        report["status"] = "CYCLE_ADVANCE_MANIFEST_MISSING"
                        return report
                    inserted = _record_enqueue(
                        conn,
                        city=city,
                        target_date=target_date,
                        metric=metric,
                        consumed_cycle_iso=consumed_cycle_iso,
                        target_cycle_iso=target_cycle_iso,
                        held_position=held_position,
                        seed_file=str(visible_seed_file),
                        reason="DAY0_OBSERVATION_ADVANCED",
                        replace_existing_seed_file=True,
                        day0_observed_extreme_observation_time=(
                            day0_observed_extreme_observation_time
                        ),
                        day0_observed_extreme_source=day0_observed_extreme_source,
                        day0_observed_extreme_c=day0_observed_extreme_c,
                        day0_observed_extreme_unit=day0_observed_extreme_unit,
                    )
                    conn.commit()
                    if inserted:
                        _publish_staged_cycle_advance_seed_if_owned(
                            conn,
                            city=city,
                            target_date=target_date,
                            metric=metric,
                            target_cycle_iso=target_cycle_iso,
                            staged_seed_file=staged_seed_file,
                            visible_seed_file=visible_seed_file,
                            identity=_day0_conditioning_identity(
                                source=day0_observed_extreme_source,
                                observation_time=day0_observed_extreme_observation_time,
                                observed_extreme_c=day0_observed_extreme_c,
                                unit=day0_observed_extreme_unit,
                            ),
                        )
                    else:
                        _discard_unpublished_cycle_advance_stage(staged_seed_file)
                    report["enqueued"] = bool(inserted)
                    report["status"] = (
                        "DAY0_OBSERVATION_ADVANCE_ENQUEUED"
                        if inserted
                        else "CYCLE_ADVANCE_ALREADY_ENQUEUED"
                    )
                    report["seed_file"] = str(visible_seed_file)
                    report["consumed_cycle"] = consumed_cycle_iso
                    report["target_cycle"] = target_cycle_iso
                    return report
                # No newer cycle than the one the posterior already consumed: the staleness is not a
                # missed-cycle gap. A held posterior whose computation clock has expired still has
                # an independent, bounded RESET: rematerialize the exact latest causal family cycle
                # while that source cycle remains inside the shared maximum-age law. This does not
                # relabel stale evidence fresh; only the queue's new canonical posterior can clear
                # the monitor gate.
                if minimum_posterior_computed_at is not None:
                    consumed_cycle = consumed_cycle_dt(consumed_cycle_iso)
                    if family_cycle is None or family_cycle < consumed_cycle:
                        report["status"] = "SAME_CYCLE_RECOMPUTE_MANIFEST_MISSING"
                        report["consumed_cycle"] = consumed_cycle_iso
                        return report
                    target_cycle_iso = family_cycle.isoformat()
                    from src.data.replacement_forecast_cycle_policy import (  # noqa: PLC0415
                        cycle_age_outside_bound,
                    )

                    if cycle_age_outside_bound(now, family_cycle):
                        report["status"] = "SAME_CYCLE_RECOMPUTE_SOURCE_EXPIRED"
                        report["consumed_cycle"] = consumed_cycle_iso
                        report["target_cycle"] = target_cycle_iso
                        return report
                    if _latest_posterior_covers_target_cycle(
                        conn,
                        city=city,
                        target_date=target_date,
                        metric=metric,
                        target_cycle_iso=target_cycle_iso,
                        as_of=now,
                        minimum_computed_at=minimum_posterior_computed_at,
                    ):
                        report["status"] = "CYCLE_ADVANCE_NOT_NEEDED"
                        report["consumed_cycle"] = consumed_cycle_iso
                        report["target_cycle"] = target_cycle_iso
                        return report
                    if _already_enqueued(
                        conn,
                        city=city,
                        target_date=target_date,
                        metric=metric,
                        target_cycle_iso=target_cycle_iso,
                        as_of=now,
                        minimum_posterior_computed_at=minimum_posterior_computed_at,
                    ):
                        report["status"] = "SAME_CYCLE_RECOMPUTE_PENDING"
                        report["consumed_cycle"] = consumed_cycle_iso
                        report["target_cycle"] = target_cycle_iso
                        return report
                    staged_seed_file, visible_seed_file = _staged_cycle_advance_seed_paths(
                        seed_path=seed_path,
                        city=city,
                        target_date=target_date,
                        metric=metric,
                        computed_at=now,
                        seed_name=_seed_name,
                    )
                    seed_file = _build_and_write_advance_seed(
                        conn,
                        city=city,
                        target_date=target_date,
                        metric=metric,
                        manifests=manifests,
                        raw_dir=raw_dir,
                        seed_path=seed_path,
                        computed_at=now,
                        build_seed=build_replacement_forecast_materialization_seed,
                        latest_baseline_coverage=(
                            latest_baseline_coverage_for_replacement_seed
                        ),
                        market_bins=market_bins_for_replacement_seed,
                        write_seed=write_seed,
                        latest_manifest=_latest_manifest,
                        manifest_path_value=_manifest_path_value,
                        manifest_base_dir=_manifest_base_dir,
                        resolve_path=_resolve_path,
                        seed_name=_seed_name,
                        expected_identity=(
                            expected_replacement_dependency_identity_by_role
                        ),
                        upgrade_trigger="held_belief_computed_age_expired",
                        output_path=staged_seed_file,
                        cycle_advance_enqueue_owner=True,
                    )
                    if seed_file is None:
                        report["status"] = "SAME_CYCLE_RECOMPUTE_MANIFEST_MISSING"
                        return report
                    inserted = _record_enqueue(
                        conn,
                        city=city,
                        target_date=target_date,
                        metric=metric,
                        consumed_cycle_iso=consumed_cycle_iso,
                        target_cycle_iso=target_cycle_iso,
                        held_position=True,
                        seed_file=str(visible_seed_file),
                        reason="HELD_BELIEF_COMPUTED_AGE_EXPIRED",
                        replace_existing_seed_file=True,
                    )
                    conn.commit()
                    if inserted:
                        _publish_staged_cycle_advance_seed_if_owned(
                            conn,
                            city=city,
                            target_date=target_date,
                            metric=metric,
                            target_cycle_iso=target_cycle_iso,
                            staged_seed_file=staged_seed_file,
                            visible_seed_file=visible_seed_file,
                            identity=None,
                        )
                    else:
                        _discard_unpublished_cycle_advance_stage(staged_seed_file)
                    report["enqueued"] = bool(inserted)
                    report["status"] = (
                        "SAME_CYCLE_RECOMPUTE_ENQUEUED"
                        if inserted
                        else "SAME_CYCLE_RECOMPUTE_PENDING"
                    )
                    report["seed_file"] = str(visible_seed_file)
                    report["consumed_cycle"] = consumed_cycle_iso
                    report["target_cycle"] = target_cycle_iso
                    return report
                else:
                    # No freshness-expiry repair was requested. Honest no-op.
                    report["status"] = "CYCLE_ADVANCE_NOT_NEEDED"
                    report["consumed_cycle"] = verdict["consumed_cycle"]
                    return report
            if family_cycle is None:
                report["status"] = "CYCLE_ADVANCE_MANIFEST_MISSING"
                report["consumed_cycle"] = None
                return report
            target_cycle_iso = family_cycle.isoformat()
            if _already_enqueued(
                conn,
                city=city,
                target_date=target_date,
                metric=metric,
                target_cycle_iso=target_cycle_iso,
                allow_missing_seed_file_reenqueue=has_day0_evidence,
                day0_observed_extreme_observation_time=day0_observed_extreme_observation_time,
                day0_observed_extreme_source=day0_observed_extreme_source,
                day0_observed_extreme_c=day0_observed_extreme_c,
                day0_observed_extreme_unit=day0_observed_extreme_unit,
                as_of=now,
            ):
                if held_position:
                    report["held_priority_promoted"] = _promote_existing_enqueue_to_held(
                        conn,
                        city=city,
                        target_date=target_date,
                        metric=metric,
                        target_cycle_iso=target_cycle_iso,
                    )
                    conn.commit()
                report["status"] = "CYCLE_ADVANCE_ALREADY_ENQUEUED"
                return report
            staged_seed_file, visible_seed_file = _staged_cycle_advance_seed_paths(
                seed_path=seed_path,
                city=city,
                target_date=target_date,
                metric=metric,
                computed_at=now,
                seed_name=_seed_name,
            )
            seed_file = _build_and_write_advance_seed(
                conn,
                city=city,
                target_date=target_date,
                metric=metric,
                manifests=manifests,
                raw_dir=raw_dir,
                seed_path=seed_path,
                computed_at=now,
                build_seed=build_replacement_forecast_materialization_seed,
                latest_baseline_coverage=latest_baseline_coverage_for_replacement_seed,
                market_bins=market_bins_for_replacement_seed,
                write_seed=write_seed,
                latest_manifest=_latest_manifest,
                manifest_path_value=_manifest_path_value,
                manifest_base_dir=_manifest_base_dir,
                resolve_path=_resolve_path,
                seed_name=_seed_name,
                expected_identity=expected_replacement_dependency_identity_by_role,
                upgrade_trigger="missing_live_posterior_reseed",
                day0_observed_extreme_c=day0_observed_extreme_c,
                day0_observed_extreme_source=day0_observed_extreme_source,
                day0_observed_extreme_observation_time=day0_observed_extreme_observation_time,
                day0_observed_extreme_sample_count=day0_observed_extreme_sample_count,
                day0_observed_extreme_unit=day0_observed_extreme_unit,
                day0_observation_state=day0_observation_state,
                output_path=staged_seed_file,
                cycle_advance_enqueue_owner=True,
            )
            if seed_file is None:
                report["status"] = "CYCLE_ADVANCE_MANIFEST_MISSING"
                return report
            inserted = _record_enqueue(
                conn,
                city=city,
                target_date=target_date,
                metric=metric,
                consumed_cycle_iso="NO_LIVE_POSTERIOR",
                target_cycle_iso=target_cycle_iso,
                held_position=held_position,
                seed_file=str(visible_seed_file),
                reason="MISSING_LIVE_POSTERIOR",
                replace_existing_seed_file=has_day0_evidence,
                day0_observed_extreme_observation_time=day0_observed_extreme_observation_time,
                day0_observed_extreme_source=day0_observed_extreme_source,
                day0_observed_extreme_c=day0_observed_extreme_c,
                day0_observed_extreme_unit=day0_observed_extreme_unit,
            )
            conn.commit()
            if inserted:
                _publish_staged_cycle_advance_seed_if_owned(
                    conn,
                    city=city,
                    target_date=target_date,
                    metric=metric,
                    target_cycle_iso=target_cycle_iso,
                    staged_seed_file=staged_seed_file,
                    visible_seed_file=visible_seed_file,
                    identity=_day0_conditioning_identity(
                        source=day0_observed_extreme_source,
                        observation_time=day0_observed_extreme_observation_time,
                        observed_extreme_c=day0_observed_extreme_c,
                        unit=day0_observed_extreme_unit,
                    ),
                )
            else:
                _discard_unpublished_cycle_advance_stage(staged_seed_file)
            report["enqueued"] = bool(inserted)
            report["status"] = (
                "CYCLE_ADVANCE_FIRST_MATERIALIZATION_ENQUEUED"
                if inserted
                else "CYCLE_ADVANCE_ALREADY_ENQUEUED"
            )
            report["seed_file"] = str(visible_seed_file)
            report["consumed_cycle"] = None
            report["target_cycle"] = target_cycle_iso
            return report
        if family_cycle is None or family_cycle <= consumed_cycle_dt(consumed_cycle_iso):
            # Global verdict was a false positive for this family (fresher cycle carried by other
            # cities). Honest no-op.
            report["status"] = "CYCLE_ADVANCE_NOT_NEEDED"
            report["consumed_cycle"] = consumed_cycle_iso
            return report
        target_cycle_iso = family_cycle.isoformat()
        if _already_enqueued(
            conn,
            city=city,
            target_date=target_date,
            metric=metric,
            target_cycle_iso=target_cycle_iso,
            allow_missing_seed_file_reenqueue=has_day0_evidence,
            day0_observed_extreme_observation_time=day0_observed_extreme_observation_time,
            day0_observed_extreme_source=day0_observed_extreme_source,
            day0_observed_extreme_c=day0_observed_extreme_c,
            day0_observed_extreme_unit=day0_observed_extreme_unit,
            as_of=now,
        ):
            if held_position:
                report["held_priority_promoted"] = _promote_existing_enqueue_to_held(
                    conn,
                    city=city,
                    target_date=target_date,
                    metric=metric,
                    target_cycle_iso=target_cycle_iso,
                )
                conn.commit()
            report["status"] = "CYCLE_ADVANCE_ALREADY_ENQUEUED"
            return report
        staged_seed_file, visible_seed_file = _staged_cycle_advance_seed_paths(
            seed_path=seed_path,
            city=city,
            target_date=target_date,
            metric=metric,
            computed_at=now,
            seed_name=_seed_name,
        )
        seed_file = _build_and_write_advance_seed(
            conn,
            city=city,
            target_date=target_date,
            metric=metric,
            manifests=manifests,
            raw_dir=raw_dir,
            seed_path=seed_path,
            computed_at=now,
            build_seed=build_replacement_forecast_materialization_seed,
            latest_baseline_coverage=latest_baseline_coverage_for_replacement_seed,
            market_bins=market_bins_for_replacement_seed,
            write_seed=write_seed,
            latest_manifest=_latest_manifest,
            manifest_path_value=_manifest_path_value,
            manifest_base_dir=_manifest_base_dir,
            resolve_path=_resolve_path,
            seed_name=_seed_name,
            expected_identity=expected_replacement_dependency_identity_by_role,
            upgrade_trigger="newer_cycle_ingested",
            day0_observed_extreme_c=day0_observed_extreme_c,
            day0_observed_extreme_source=day0_observed_extreme_source,
            day0_observed_extreme_observation_time=day0_observed_extreme_observation_time,
            day0_observed_extreme_sample_count=day0_observed_extreme_sample_count,
            day0_observed_extreme_unit=day0_observed_extreme_unit,
            day0_observation_state=day0_observation_state,
            output_path=staged_seed_file,
            cycle_advance_enqueue_owner=True,
        )
        if seed_file is None:
            report["status"] = "CYCLE_ADVANCE_MANIFEST_MISSING"
            return report
        inserted = _record_enqueue(
            conn,
            city=city,
            target_date=target_date,
            metric=metric,
            consumed_cycle_iso=consumed_cycle_iso,
            target_cycle_iso=target_cycle_iso,
            held_position=held_position,
            seed_file=str(visible_seed_file),
            replace_existing_seed_file=has_day0_evidence,
            day0_observed_extreme_observation_time=day0_observed_extreme_observation_time,
            day0_observed_extreme_source=day0_observed_extreme_source,
            day0_observed_extreme_c=day0_observed_extreme_c,
            day0_observed_extreme_unit=day0_observed_extreme_unit,
        )
        conn.commit()
        if inserted:
            _publish_staged_cycle_advance_seed_if_owned(
                conn,
                city=city,
                target_date=target_date,
                metric=metric,
                target_cycle_iso=target_cycle_iso,
                staged_seed_file=staged_seed_file,
                visible_seed_file=visible_seed_file,
                identity=_day0_conditioning_identity(
                    source=day0_observed_extreme_source,
                    observation_time=day0_observed_extreme_observation_time,
                    observed_extreme_c=day0_observed_extreme_c,
                    unit=day0_observed_extreme_unit,
                ),
            )
        else:
            _discard_unpublished_cycle_advance_stage(staged_seed_file)
        report["enqueued"] = bool(inserted)
        report["status"] = "CYCLE_ADVANCE_ENQUEUED" if inserted else "CYCLE_ADVANCE_ALREADY_ENQUEUED"
        report["seed_file"] = str(visible_seed_file)
        report["consumed_cycle"] = consumed_cycle_iso
        report["target_cycle"] = target_cycle_iso
    except Exception as exc:  # noqa: BLE001 — fail-soft: never raise into the reactor cycle
        _LOG.debug(
            "single-family cycle-advance failed for %s/%s/%s: %s", city, target_date, metric, exc
        )
        report["status"] = "CYCLE_ADVANCE_FAILSOFT_SKIPPED"
        report["error"] = str(exc)
    finally:
        conn.close()
    return report


def _materialize_day0_extreme_updated_seed(
    *,
    city: str,
    target_date: str,
    metric: str,
    computed_at: datetime | None = None,
    held_position: bool | None = None,
) -> dict[str, object]:
    """Bridge a committed DAY0_EXTREME_UPDATED event to an immediate re-materialization seed.

    Operator directive 2026-07-19 (Day0 is a zero-sum race against the market book): the measured
    bottleneck is the ~40-minute SCHEDULED posterior recompute cadence (HOP 2b,
    docs/evidence/upstream_physical_2026_07_17/day0_latency_chain_measurement.md), not fetch or
    event delivery — those are already fast (<1 min / ~1 min p50). A fresh observed extreme must
    reprice q immediately instead of waiting on the next scheduled tick.

    Reuses the EXISTING single-family cycle-advance seed transport verbatim — same seed builder,
    same seed_dir, same ``cycle_advance_enqueues`` idempotency marker with its
    ``day0_observed_extreme_observation_time`` monotone guard (``_already_enqueued`` /
    ``_record_enqueue`` above) — no new subsystem, no second transport. The observed extreme is
    re-read FRESH from the canonical settlement-grade surface via
    ``replacement_forecast_seed_discovery._day0_observed_extreme_seed_payload`` (the SAME reader
    the poll-lane batch trigger uses), never trusted from the caller, so a stale/racing caller
    cannot inject a wrong extreme.

    ``held_position`` defaults to an auto-detected held-family check reusing
    ``_edli_current_held_position_family_keys`` (2b5ae40a3) so held/traded families are tagged for
    priority drain ordering, but held_position only affects queue PRIORITY — every admitted Day0
    family, held or not, still gets a seed (operator: "entry opportunities repriced too, not only
    held positions").

    Fail-soft throughout: any error (config missing, no observed extreme, DB fault, held-lookup
    failure) is logged and a status dict returned; this must NEVER raise into the event-emission
    path that calls it.
    """
    city = str(city)
    target_date = str(target_date)
    metric = str(metric)
    report: dict[str, object] = {
        "status": "DAY0_EXTREME_BRIDGE_SKIPPED",
        "city": city,
        "target_date": target_date,
        "metric": metric,
    }
    try:
        from src.data.replacement_forecast_production import (  # noqa: PLC0415
            _replacement_forecast_live_materialization_queue_config,
        )
        from src.data.replacement_forecast_seed_discovery import (  # noqa: PLC0415
            _day0_observed_extreme_seed_payload,
        )

        cfg = _replacement_forecast_live_materialization_queue_config()
        forecast_db = cfg.get("forecast_db")
        seed_dir = cfg.get("seed_dir")
        raw_manifest_dir = cfg.get("raw_manifest_dir")
        if forecast_db is None or seed_dir is None or raw_manifest_dir is None:
            report["status"] = "DAY0_EXTREME_BRIDGE_NOT_CONFIGURED"
            return report
        now = (computed_at or datetime.now(tz=UTC)).astimezone(UTC)
        day0_payload = _day0_observed_extreme_seed_payload(
            city=city, target_date=target_date, metric=metric, computed_at=now,
        )
        if day0_payload is None:
            report["status"] = "DAY0_EXTREME_BRIDGE_NO_OBSERVED_EXTREME"
            return report
        if held_position is None:
            try:
                from src.events.reactor import (  # noqa: PLC0415
                    _edli_current_held_position_family_keys,
                )

                held_position = (
                    (city, target_date, metric) in _edli_current_held_position_family_keys()
                )
            except Exception:  # noqa: BLE001 — priority tagging is best-effort, never fatal
                held_position = False
        inner = enqueue_single_family_cycle_advance_reseed(
            forecast_db=Path(str(forecast_db)),
            seed_dir=Path(str(seed_dir)),
            raw_manifest_dir=Path(str(raw_manifest_dir)),
            city=city,
            target_date=target_date,
            metric=metric,
            computed_at=now,
            held_position=bool(held_position),
            **day0_payload,
        )
        report.update(inner)
        return report
    except Exception as exc:  # noqa: BLE001 — fail-soft: never raise into event emission
        _LOG.warning(
            "day0-extreme-updated materialization bridge FAILED (fail-soft) "
            "city=%s target_date=%s metric=%s exc=%s",
            city, target_date, metric, exc,
        )
        report["status"] = "DAY0_EXTREME_BRIDGE_FAILSOFT_SKIPPED"
        report["error"] = str(exc)
        return report


def _day0_bridge_status_retryable(status: object) -> bool:
    value = str(status or "")
    return value.startswith("WORKER_FAILED:") or value in {
        "CYCLE_ADVANCE_FAILSOFT_SKIPPED",
        "CYCLE_ADVANCE_FORECAST_DB_MISSING",
        "CYCLE_ADVANCE_LEG_ARTIFACT_MISSING",
        "CYCLE_ADVANCE_MANIFEST_MISSING",
        "CYCLE_ADVANCE_NO_MATERIALIZABLE_CYCLE",
        "DAY0_EXTREME_BRIDGE_FAILSOFT_SKIPPED",
        "DAY0_EXTREME_BRIDGE_NO_OBSERVED_EXTREME",
        "DAY0_EXTREME_BRIDGE_NOT_CONFIGURED",
    }


def _requeue_day0_bridge_pending(
    key: tuple[str, str, str],
) -> None:
    with _DAY0_BRIDGE_CONDITION:
        pending = _DAY0_BRIDGE_PENDING.get(key)
        if pending is None or pending.running or _DAY0_BRIDGE_CLOSED:
            return
        pending.enqueued_monotonic = time.monotonic()
        _DAY0_BRIDGE_QUEUES[pending.lane].put_nowait(key)
        _DAY0_BRIDGE_CONDITION.notify_all()


def _day0_bridge_worker(held_lane: bool) -> None:
    bridge_queue = _DAY0_BRIDGE_QUEUES[held_lane]
    while True:
        item = bridge_queue.get()
        try:
            if item is _DAY0_BRIDGE_STOP:
                return
            key = item
            with _DAY0_BRIDGE_CONDITION:
                pending = _DAY0_BRIDGE_PENDING.get(key)
                if pending is None or pending.running or pending.lane != held_lane:
                    continue
                pending.running = True
                generation = pending.generation
                computed_at = pending.computed_at
                held_position = pending.held_position
                queued_at = pending.enqueued_monotonic
            started_at = time.monotonic()
            try:
                report = _materialize_day0_extreme_updated_seed(
                    city=pending.city,
                    target_date=pending.target_date,
                    metric=pending.metric,
                    computed_at=computed_at,
                    held_position=held_position,
                )
                status = report.get("status")
            except Exception as exc:  # noqa: BLE001 - durable event remains retry authority
                status = f"WORKER_FAILED:{type(exc).__name__}"
                _LOG.exception(
                    "day0 materialization worker failed city=%s target_date=%s metric=%s",
                    pending.city,
                    pending.target_date,
                    pending.metric,
                )
            runtime_ms = (time.monotonic() - started_at) * 1000.0
            _LOG.info(
                "day0 materialization worker city=%s target_date=%s metric=%s "
                "held_lane=%s status=%s queue_wait_ms=%.1f runtime_ms=%.1f",
                pending.city,
                pending.target_date,
                pending.metric,
                held_lane,
                status,
                (started_at - queued_at) * 1000.0,
                runtime_ms,
            )
            with _DAY0_BRIDGE_CONDITION:
                current = _DAY0_BRIDGE_PENDING.get(key)
                if current is pending and current.generation == generation:
                    if _day0_bridge_status_retryable(status):
                        current.running = False
                        current.failures += 1
                        delay = min(
                            _DAY0_BRIDGE_RETRY_MAX_SECONDS,
                            _DAY0_BRIDGE_RETRY_BASE_SECONDS
                            * (2 ** min(current.failures - 1, 8)),
                        )
                        retry = threading.Timer(
                            delay,
                            _requeue_day0_bridge_pending,
                            args=(key,),
                        )
                        retry.daemon = True
                        retry.start()
                    else:
                        del _DAY0_BRIDGE_PENDING[key]
                elif current is pending:
                    current.running = False
                    current.failures = 0
                    current.lane = current.held_position is not False
                    current.enqueued_monotonic = time.monotonic()
                    _DAY0_BRIDGE_QUEUES[current.lane].put_nowait(key)
                _DAY0_BRIDGE_CONDITION.notify_all()
        finally:
            bridge_queue.task_done()


def _day0_bridge_held_position_keys(
    keys: tuple[tuple[str, str, str], ...],
) -> set[tuple[str, str, str]]:
    """Classify a coalesced batch off the fact-publication thread."""

    try:
        from src.events.reactor import _edli_current_held_position_family_keys

        held = _edli_current_held_position_family_keys()
    except Exception:  # noqa: BLE001 - priority failure degrades to entry lane
        return set()
    return set(keys) & set(held)


def _day0_bridge_classifier() -> None:
    while True:
        first = _DAY0_BRIDGE_CLASSIFY_QUEUE.get()
        items = [first]
        stop = first is _DAY0_BRIDGE_STOP
        while not stop:
            try:
                item = _DAY0_BRIDGE_CLASSIFY_QUEUE.get_nowait()
            except queue.Empty:
                break
            items.append(item)
            stop = item is _DAY0_BRIDGE_STOP
        keys = tuple(item for item in items if item is not _DAY0_BRIDGE_STOP)
        try:
            held_keys = _day0_bridge_held_position_keys(keys)
            with _DAY0_BRIDGE_CONDITION:
                for key in keys:
                    pending = _DAY0_BRIDGE_PENDING.get(key)
                    if pending is None or pending.running or pending.lane is not None:
                        continue
                    is_held = key in held_keys
                    pending.held_position = is_held
                    pending.lane = is_held
                    pending.enqueued_monotonic = time.monotonic()
                    _DAY0_BRIDGE_QUEUES[is_held].put_nowait(key)
                _DAY0_BRIDGE_CONDITION.notify_all()
        finally:
            for _item in items:
                _DAY0_BRIDGE_CLASSIFY_QUEUE.task_done()
        if stop:
            return


def _start_day0_bridge_workers_locked() -> None:
    global _DAY0_BRIDGE_THREADS

    if _DAY0_BRIDGE_THREADS:
        return
    workers = tuple(
        threading.Thread(
            target=_day0_bridge_worker,
            args=(held_lane,),
            name=f"day0-materialization-{'held' if held_lane else 'entry'}",
            daemon=True,
        )
        for held_lane in (True, False)
    )
    _DAY0_BRIDGE_THREADS = (
        threading.Thread(
            target=_day0_bridge_classifier,
            name="day0-materialization-classifier",
            daemon=True,
        ),
        *workers,
    )
    for thread in _DAY0_BRIDGE_THREADS:
        thread.start()


def enqueue_day0_extreme_updated_materialization_seed(
    *,
    city: str,
    target_date: str,
    metric: str,
    computed_at: datetime | None = None,
    held_position: bool | None = None,
) -> dict[str, object]:
    """Queue Day0 posterior work without running materialization inline."""

    key = (str(city), str(target_date), str(metric))
    with _DAY0_BRIDGE_CONDITION:
        if _DAY0_BRIDGE_CLOSED:
            return {
                "status": "DAY0_EXTREME_BRIDGE_CLOSED",
                "city": key[0],
                "target_date": key[1],
                "metric": key[2],
            }
        pending = _DAY0_BRIDGE_PENDING.get(key)
        if pending is not None:
            pending.generation += 1
            pending.computed_at = computed_at
            if held_position is True:
                pending.held_position = True
                if not pending.running and pending.lane is not True:
                    pending.lane = True
                    _DAY0_BRIDGE_QUEUES[True].put_nowait(key)
            elif held_position is False and not pending.running and pending.lane is None:
                pending.held_position = False
                pending.lane = False
                _DAY0_BRIDGE_QUEUES[False].put_nowait(key)
            return {
                "status": "DAY0_EXTREME_BRIDGE_COALESCED",
                "city": key[0],
                "target_date": key[1],
                "metric": key[2],
                "held_lane": pending.lane,
                "priority_classification_pending": pending.lane is None,
            }
        lane = bool(held_position) if held_position is not None else None
        pending = _Day0BridgePending(
            city=key[0],
            target_date=key[1],
            metric=key[2],
            computed_at=computed_at,
            held_position=held_position,
            lane=lane,
            enqueued_monotonic=time.monotonic(),
        )
        _DAY0_BRIDGE_PENDING[key] = pending
        _start_day0_bridge_workers_locked()
        if lane is None:
            _DAY0_BRIDGE_CLASSIFY_QUEUE.put_nowait(key)
        else:
            _DAY0_BRIDGE_QUEUES[lane].put_nowait(key)
    return {
        "status": "DAY0_EXTREME_BRIDGE_QUEUED",
        "city": key[0],
        "target_date": key[1],
        "metric": key[2],
        "held_lane": lane,
        "priority_classification_pending": lane is None,
    }


def _wait_for_day0_materialization_bridge_idle(timeout_s: float) -> bool:
    deadline = time.monotonic() + max(0.0, float(timeout_s))
    with _DAY0_BRIDGE_CONDITION:
        while _DAY0_BRIDGE_PENDING:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            _DAY0_BRIDGE_CONDITION.wait(remaining)
        return True


def close_day0_materialization_bridge() -> None:
    global _DAY0_BRIDGE_CLOSED

    with _DAY0_BRIDGE_CONDITION:
        if _DAY0_BRIDGE_CLOSED:
            return
        _DAY0_BRIDGE_CLOSED = True
        _DAY0_BRIDGE_PENDING.clear()
        _DAY0_BRIDGE_CONDITION.notify_all()
        if not _DAY0_BRIDGE_THREADS:
            return
        _DAY0_BRIDGE_CLASSIFY_QUEUE.put_nowait(_DAY0_BRIDGE_STOP)
        for bridge_queue in _DAY0_BRIDGE_QUEUES.values():
            bridge_queue.put_nowait(_DAY0_BRIDGE_STOP)


def _build_and_write_advance_seed(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    metric: str,
    manifests,
    raw_dir: Path,
    seed_path: Path,
    computed_at: datetime,
    build_seed,
    latest_baseline_coverage,
    market_bins,
    write_seed,
    latest_manifest,
    manifest_path_value,
    manifest_base_dir,
    resolve_path,
    seed_name,
    expected_identity,
    upgrade_trigger: str = "newer_cycle_ingested",
    day0_observed_extreme_c: float | None = None,
    day0_observed_extreme_source: str | None = None,
    day0_observed_extreme_observation_time: str | None = None,
    day0_observed_extreme_sample_count: int | None = None,
    day0_observed_extreme_unit: str | None = None,
    day0_observation_state: str | None = None,
    output_path: Path | None = None,
    cycle_advance_enqueue_owner: bool = False,
) -> Path | None:
    """Build one re-materialization seed for a scope using the existing seed-builder pieces and
    write it into seed_dir. Returns the seed Path, or None when the required manifests/context are
    absent (the scope's raw inputs for the fresh cycle are not yet on disk — recorded as
    manifest_missing, retried next tick once they land). The seed builder pins source_cycle_time to
    the LATEST manifest cycle, so the re-materialized posterior advances onto the fresh cycle and the
    materializer's monotone guard admits it (request cycle >= current posterior cycle). Mirrors the
    fusion-upgrade trigger's _build_and_write_upgrade_seed (single seed-build shape)."""
    expected = expected_identity(metric)
    from src.config import cities_by_name  # noqa: PLC0415

    city_cfg = cities_by_name.get(city)
    city_timezone = str(getattr(city_cfg, "timezone", "") or "") or None
    openmeteo = latest_manifest(
        manifests,
        source_id=expected["openmeteo_ifs9_anchor"].source_id,
        data_version=expected["openmeteo_ifs9_anchor"].data_version,
        city=city,
        target_date=target_date,
        city_timezone=city_timezone,
    )
    if openmeteo is None:
        return None
    openmeteo_payload = manifest_path_value(openmeteo, "openmeteo_payload_json") or openmeteo.artifact_path
    precision_metadata = manifest_path_value(openmeteo, "precision_metadata_json")
    if not openmeteo_payload or not precision_metadata:
        return None
    coverage = latest_baseline_coverage(
        conn,
        city=city,
        target_date=target_date,
        temperature_metric=metric,
        not_after_source_cycle_time=openmeteo.source_cycle_time,
    )
    bins = market_bins(conn, city=city, target_date=target_date, temperature_metric=metric)
    if coverage is None or not bins:
        return None
    openmeteo_base_dir = manifest_base_dir(openmeteo, fallback=raw_dir)
    seed_result = build_seed(
        city=city,
        target_date=target_date,
        temperature_metric=metric,
        market_bins=bins,
        baseline_coverage=coverage,
        openmeteo_manifest=openmeteo,
        openmeteo_payload_json=resolve_path(openmeteo_payload, base_dir=openmeteo_base_dir),
        precision_metadata_json=resolve_path(precision_metadata, base_dir=openmeteo_base_dir),
        computed_at=computed_at,
        base_dir=seed_path,
        day0_observed_extreme_c=day0_observed_extreme_c,
        day0_observed_extreme_source=day0_observed_extreme_source,
        day0_observed_extreme_observation_time=day0_observed_extreme_observation_time,
        day0_observed_extreme_sample_count=day0_observed_extreme_sample_count,
        day0_observed_extreme_unit=day0_observed_extreme_unit,
        day0_observation_state=day0_observation_state,
    )
    if not seed_result.ok or seed_result.seed is None:
        return None
    # Honest re-materialization provenance: this seed exists because a NEWER cycle landed, not a
    # fresh first materialization. Threaded into provenance_json so the posterior records WHY.
    seed_payload: dict[str, object] = dict(seed_result.seed)
    seed_payload["upgrade_trigger"] = upgrade_trigger
    if cycle_advance_enqueue_owner:
        seed_payload["cycle_advance_enqueue_owner"] = True
    seed_file = output_path or seed_path / seed_name(
        {"city": city, "target_date": target_date, "temperature_metric": metric},
        computed_at=computed_at,
    )
    write_seed(seed_file, seed_payload)
    return seed_file

# Created: 2026-06-11
# Last reused or audited: 2026-07-28
# Authority basis: Task #32 (operator 2026-06-11) — PARTIAL fusions never upgrade when late
#   instruments publish. The materializer reads CURRENT values from the persisted single_runs
#   capture (gem via previous_runs exception) at the OM9 anchor cycle; a provider whose
#   single_runs row was not yet persisted at materialize time is dropped, and the resulting
#   served<5 posterior is then marked "covered" (q_lcb NOT NULL) by all three coverage gates —
#   which key coverage on the baseline_b0 (ecmwf_open_data) run, BLIND to the bayes_precision_fusion decorrelated
#   instrument set. So the scope never re-materializes even after its 5th provider lands.
#   K-decision: compare both the served provider-family set and the exact persisted CURRENT input
#   revisions consumed by the latest posterior. A new family, changed provider row, or changed
#   Day0 hourly-vector bundle requires re-materialization.
"""SINGLE-AUTHORITY comparison + idempotent enqueue for the PARTIAL-fusion upgrade trigger.

The decorrelated-provider FAMILY mapping (`decorrelated_provider_families_of`) is the SOLE
authority for "which model belongs to which of the 5 decorrelated provider families". The
materializer's served/missing-provider determination imports it (single-builder), so the
trigger and the fusion can never disagree on what "served 5/5" means.

The comparison (`scope_capture_offers_larger_provider_set`) is the SOLE authority for whether
the latest posterior's provider set or exact CURRENT input revisions have been superseded. The
seed-discovery / queue / plan coverage gates remain keyed on baseline_b0 + q_lcb (their job is
freshness/tradeable-grade, not input revision detection).

The enqueue (`enqueue_fusion_upgrade_reseeds`) durably reserves the UNIQUE transition marker,
writes via the EXISTING atomic write_seed into owner-private hidden staging, then durably
finalizes ownership before an atomic hardlink publish into the SAME seed_dir the materialize
cycle already drains — no new daemon or parallel materialization path. The marker is UNIQUE on
(city, target_date, metric, source_cycle_time, capturable_family_set): family growth uses the
canonical family set as its transition key; exact input revisions suffix that key with the
changed source raw-row ids. A transition has at most one active publication. If that publication
is consumed without converging the posterior and has no terminal no-retry receipt, the same marker
is atomically reclaimed so a failed consumer cannot turn idempotency into permanent starvation.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping, Sequence
from uuid import uuid4

from src.data.raw_forecast_artifact_manifest import RawForecastArtifactManifest
from src.data.replacement_forecast_readiness import SOURCE_ID

_LOG = logging.getLogger("zeus.replacement_fusion_upgrade_trigger")

UTC = timezone.utc
_RESERVATION_PREFIX = "__fusion_upgrade_reservation__:"
_PUBLISH_PENDING_PREFIX = "__fusion_upgrade_publish_pending__:"
_RESERVATION_TTL = timedelta(minutes=5)
_DAY0_HOURLY_VECTOR_SOURCE = "day0_hourly_vectors"
_DAY0_CAUSAL_BUNDLE_SOURCE = "day0_causal_evidence_bundle"


@dataclass(frozen=True)
class _SeedPublication:
    owner: str
    staging_file: Path
    publish_temp: Path
    seed_file: Path

# THE single authority mapping model -> decorrelated provider family. Mirrors exactly the
# materializer's per-provider check (replacement_forecast_materializer lines ~1012-1024): the
# physical providers each contribute ONE representative to the fusion, and a family is "served"
# when ANY of its members is in the fused set. The ECMWF anchor is intentionally NOT here: it is
# the PRIOR (not a decorrelated likelihood provider). icon_seamless was also NOT here and has since
# been removed from the candidate set entirely (2026-06-17 — it was the alias-dedup probe, not a
# provider). The materializer imports DECORRELATED_PROVIDER_FAMILIES so the two sites can never
# drift on what counts as a provider.
# 2026-06-17 COARSE-GLOBAL REMOVAL: the 0.25°/25km gfs_global and ~15km gem_global are dropped
# from the fusion (model_selection.DECORR_GLOBALS), so they are no longer family members here.
# NCEP is now repped ONLY by its CONUS nests (gfs_hrrr 3km / ncep_nbm ~13km) and CMC ONLY by the
# HRDPS 2.5km North-America nest — both DOMAIN-GATED. OUTSIDE those nest domains NCEP/CMC have no
# servable member and are STRUCTURALLY ABSENT for that city; the flat 5-family count would then
# false-flag them as "missing". `expected_provider_families_for_city` below is the per-city
# domain-aware expected set that replaces the flat count at the materializer's completeness gate.
# 2026-06-17 JMA DROP (operator, settlement-graded): jma_seamless (the only JMA member) is the
# coldest/least-precise global (lead-1 raw bias -1.46, MAE 2.124) and was dropped from the fusion
# (model_selection.DECORR_GLOBALS). The JMA family is therefore REMOVED entirely here — no member
# can ever serve, so it must never be expected anywhere. The contract is now {NCEP, DWD, CMC, UKMO}.
DECORRELATED_PROVIDER_FAMILIES: dict[str, tuple[str, ...]] = {
    "NCEP": ("gfs_hrrr", "ncep_nbm_conus"),
    "DWD": ("icon_d2", "icon_eu", "icon_global"),
    "CMC": ("gem_hrdps_continental",),
    "UKMO": ("ukmo_global_deterministic_10km", "ukmo_uk_deterministic_2km"),
}

# The GLOBAL maximum family count (every family servable). Retained as the fail-open fallback for
# expected_provider_families_for_city when a city's coords cannot be resolved; the LIVE
# completeness gate uses the per-city expected set, never this flat count.
EXPECTED_DECORRELATED_PROVIDER_COUNT = len(DECORRELATED_PROVIDER_FAMILIES)


def expected_provider_families_for_city(lat: float, lon: float, lead_days: int) -> frozenset[str]:
    """THE per-city, per-LEAD domain-aware expected provider-family set (2026-06-17 removal).

    A decorrelated provider family is EXPECTED for a city AT THIS LEAD only if it has a member
    that is SERVABLE there-and-then: a pure-global member (available worldwide at any lead) OR a
    domain-gated member whose polygon covers (lat, lon) AND whose max_lead_days cap is not
    exceeded at ``lead_days``. After the coarse-global drop, NCEP (gfs_hrrr / ncep_nbm, both
    CONUS) and CMC (gem_hrdps, N-America) are nest-only; outside those domains — OR at a lead
    PAST the nest's max_lead_days cap (gfs_hrrr=2, ncep_nbm=3, gem_hrdps=2) — they are NOT
    expected, so a non-CONUS/non-NA city, AND a CONUS/NA city at far lead, is COMPLETE on the
    pure globals {DWD, UKMO} (+ anchor) with no phantom PARTIAL flag and no upgrade re-enqueue.

    LEAD-AWARENESS IS LOAD-BEARING (2026-06-17 critic fix): lead 0 is NOT "the most permissive
    lead" — it is the OPPOSITE. A nest eligible at lead 0 becomes INELIGIBLE past its cap, so a
    lead-0 expected set over-expects NCEP/CMC at far lead (CONUS lead>=4 / NA lead>=3) and
    re-fires the exact phantom-PARTIAL + upgrade loop this contract exists to kill. The expected
    set MUST be evaluated at the lead the fusion actually serves (the city-local lead).

    The "is this member domain-gated" test is `_REGIONAL_DOMAIN_KEY` membership — the SAME gate
    `regional_eligible` itself keys on. A member NOT in `_REGIONAL_DOMAIN_KEY` is a pure global
    (icon_global / ukmo_global) servable at any lead; this correctly treats the domain-gated
    global `ncep_nbm_conus` (CONUS-only, NOT in REGIONAL_MODELS) as gated, which a
    `REGIONAL_MODELS`-only test would miss. Fail-soft: any error -> all families expected (the
    conservative pre-removal behavior; never silently under-reports completeness).
    """
    try:
        from src.forecast.model_selection import (  # noqa: PLC0415
            _REGIONAL_DOMAIN_KEY,
            regional_eligible,
        )

        def _member_servable(member: str) -> bool:
            if member in _REGIONAL_DOMAIN_KEY:
                return regional_eligible(member, lat=lat, lon=lon, lead_days=int(lead_days))
            return True  # pure global member: servable worldwide at any lead

        expected: set[str] = set()
        for family, members in DECORRELATED_PROVIDER_FAMILIES.items():
            if any(_member_servable(m) for m in members):
                expected.add(family)
        return frozenset(expected)
    except Exception:
        return frozenset(DECORRELATED_PROVIDER_FAMILIES)


def decorrelated_provider_families_of(models: "set[str] | frozenset[str] | tuple[str, ...]") -> frozenset[str]:
    """Return the set of decorrelated provider families REPRESENTED by ``models``.

    A family is present iff ANY of its member models is in ``models``. The ECMWF anchor
    contributes no family (prior), and icon_seamless was removed from the candidate set
    (2026-06-17 — alias-dedup probe), so stray icon_seamless values never inflate the count.
    """
    present: set[str] = set()
    for family, members in DECORRELATED_PROVIDER_FAMILIES.items():
        if any(m in models for m in members):
            present.add(family)
    return frozenset(present)


def _family_set_key(families: "frozenset[str] | set[str]") -> str:
    """Canonical, order-independent string key for a family set (marker uniqueness)."""
    return ",".join(sorted(families))


def _input_revision_marker_key(
    capturable_family_key: str,
    revisions: Mapping[str, object],
) -> str:
    """Durable transition key for one exact set of changed CURRENT inputs."""

    def revision_value(value: object) -> str:
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
        canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    revision_key = ",".join(
        f"{source}:{revision_value(revision)}"
        for source, revision in sorted(revisions.items())
    )
    return f"{capturable_family_key}|input_revision={revision_key}"


def _canonical_day0_vector_revision(
    witness: object,
) -> tuple[str | None, tuple[str, ...]]:
    """Return the exact complete hourly-vector bundle identity in canonical form."""

    if not isinstance(witness, Mapping):
        return None, ()
    raw_models = witness.get("expected_models")
    raw_ids = witness.get("vector_ids_by_model")
    if not isinstance(raw_models, (list, tuple)) or not isinstance(raw_ids, Mapping):
        return None, ()
    models = tuple(dict.fromkeys(str(model).strip() for model in raw_models))
    if not models or any(not model for model in models) or set(raw_ids) != set(models):
        return None, ()
    vector_ids = {
        model: str(raw_ids.get(model) or "").strip()
        for model in models
    }
    if any(not vector_id for vector_id in vector_ids.values()):
        return None, ()
    return (
        json.dumps(
            {
                "expected_models": list(models),
                "vector_ids_by_model": vector_ids,
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        models,
    )


def _capturable_day0_vector_revision(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    expected_models: tuple[str, ...],
    decision_time: datetime | None,
) -> str | None:
    """Read the newest persisted complete bundle for the posterior's exact model set."""

    if (
        decision_time is None
        or decision_time.tzinfo is None
        or decision_time.utcoffset() is None
        or not expected_models
    ):
        return None
    cutoff = decision_time.astimezone(UTC).isoformat()
    placeholders = ",".join("?" for _ in expected_models)
    try:
        rows = conn.execute(
            f"""SELECT model, vector_id, captured_at
                  FROM day0_hourly_vectors
                 WHERE city = ? AND target_date = ? AND captured_at <= ?
                   AND model IN ({placeholders})
                 ORDER BY captured_at DESC, model""",
            (city, target_date, cutoff, *expected_models),
        ).fetchall()
    except sqlite3.Error:
        return None
    by_capture: dict[str, dict[str, str]] = {}
    for row in rows:
        model = str(row[0] or "").strip()
        vector_id = str(row[1] or "").strip()
        captured_at = str(row[2] or "").strip()
        if model and vector_id and captured_at:
            by_capture.setdefault(captured_at, {})[model] = vector_id
    for captured_at in sorted(by_capture, reverse=True):
        vector_ids = by_capture[captured_at]
        if set(vector_ids) != set(expected_models):
            continue
        revision, _models = _canonical_day0_vector_revision(
            {
                "expected_models": expected_models,
                "vector_ids_by_model": vector_ids,
            }
        )
        return revision
    return None


def _capturable_inputs_for_scope(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    metric: str,
    source_cycle_iso: str,
    decision_time: datetime | None = None,
) -> dict[str, int]:
    """CURRENT model -> raw row id available to the materializer for this scope and cycle.

    Delegates ENTIRELY to the single serving authority
    (replacement_current_value_serving.read_current_instrument_values) — the SAME function the
    materializer's q path consumes — so "capturable" and "what the fusion will actually serve"
    can never drift (registry member #10). This includes the generalized previous-runs
    substitution (没有新的就用老的): a provider structurally unpublished on this cycle's
    single_runs leg (JMA at 06Z-cadence cycles) counts as capturable via its previous-runs row.
    Fail-soft: any read error -> empty mapping (nothing newly capturable).
    """
    from src.data.replacement_current_value_serving import (  # noqa: PLC0415
        read_current_instrument_values,
    )

    try:
        served = read_current_instrument_values(
            conn,
            city=city,
            metric=metric,
            target_date=target_date,
            source_cycle_time_iso=source_cycle_iso,
            include_station_sources=True,
            decision_time_iso=(
                decision_time.astimezone(UTC).isoformat()
                if decision_time is not None
                else None
            ),
        )
        return {model: int(value.raw_model_forecast_id) for model, value in served.items()}
    except Exception:
        return {}


def _capturable_models_for_scope(
    conn: sqlite3.Connection, *, city: str, target_date: str, metric: str, source_cycle_iso: str
) -> set[str]:
    """Compatibility view used by existing callers that only need model identities."""
    return set(
        _capturable_inputs_for_scope(
            conn,
            city=city,
            target_date=target_date,
            metric=metric,
            source_cycle_iso=source_cycle_iso,
        )
    )


def _latest_posterior_inputs(
    conn: sqlite3.Connection, *, city: str, target_date: str, metric: str
) -> tuple[
    str | None,
    frozenset[str],
    dict[str, int],
    frozenset[str],
    str | None,
    tuple[str, ...],
    bool,
]:
    """Return cycle, provider inputs, and committed Day0 successor state."""
    try:
        row = conn.execute(
            """
            SELECT source_cycle_time, provenance_json
            FROM forecast_posteriors
            WHERE source_id = ? AND city = ? AND target_date = ? AND temperature_metric = ?
            ORDER BY computed_at DESC
            LIMIT 1
            """,
            (SOURCE_ID, city, target_date, metric),
        ).fetchone()
    except Exception:
        return None, frozenset(), {}, frozenset(), None, (), False
    if row is None:
        return None, frozenset(), {}, frozenset(), None, (), False
    source_cycle_iso = str(row[0]) if row[0] is not None else None
    try:
        prov = json.loads(row[1]) if row[1] else {}
    except Exception:
        return source_cycle_iso, frozenset(), {}, frozenset(), None, (), False
    fusion = prov.get("bayes_precision_fusion", {}) or {}
    used = fusion.get("used_models") or []
    if not isinstance(used, (list, tuple)):
        used = []
    serving = fusion.get("current_value_serving") or {}
    consumed: dict[str, int] = {}
    if isinstance(serving, Mapping):
        for model, value in serving.items():
            if not isinstance(value, Mapping):
                continue
            try:
                consumed[str(model)] = int(value["raw_model_forecast_id"])
            except (KeyError, TypeError, ValueError):
                continue
    source_clock = fusion.get("source_clock_one_scheme") or {}
    configured = (
        source_clock.get("configured_sources") or []
        if isinstance(source_clock, Mapping)
        else []
    )
    if not isinstance(configured, (list, tuple)):
        configured = []
    day0_revision, day0_expected_models = _canonical_day0_vector_revision(
        prov.get("day0_remaining_vector_witness")
    )
    day0_bundle_valid = False
    bundle = prov.get("day0_causal_evidence_bundle")
    if isinstance(bundle, Mapping):
        try:
            from src.data.day0_hourly_vectors import (
                validate_day0_causal_evidence_bundle,
            )

            day0_bundle_valid = bool(
                validate_day0_causal_evidence_bundle(
                    expected=bundle,
                    actual=bundle,
                ).ok
            )
        except (KeyError, TypeError, ValueError):
            day0_bundle_valid = False
    return (
        source_cycle_iso,
        decorrelated_provider_families_of(set(str(m) for m in used)),
        consumed,
        frozenset(str(source) for source in configured if str(source).strip()),
        day0_revision,
        day0_expected_models,
        day0_bundle_valid,
    )


def _city_latlon(city: str) -> tuple[float, float] | None:
    """Resolve a city's (lat, lon) from the live runtime city map. None when the city is unknown
    or the map cannot be read (caller fails OPEN to all-families-expected). Single source of
    truth for coords — the SAME runtime_cities_by_name the materializer's q path reads, so the
    upgrade trigger and the fusion can never disagree on where a city is."""
    try:
        from src.config import runtime_cities_by_name  # noqa: PLC0415

        city_obj = runtime_cities_by_name().get(city)
        if city_obj is None:
            return None
        return float(getattr(city_obj, "lat")), float(getattr(city_obj, "lon"))
    except Exception:
        return None


def _scope_lead_days(city: str, target_date: str, cycle_iso: str) -> int:
    """City-LOCAL lead (days) from the posterior's cycle to the target date — the lead at which
    the fusion serves this scope. Used to evaluate the per-city expected set at the REAL lead
    (the nests are lead-capped: gfs_hrrr=2, ncep_nbm=3, gem_hrdps=2), so a far-lead CONUS/NA scope
    does NOT over-expect NCEP/CMC. Fail-soft to lead 0 (the MOST-expecting / loudest direction:
    over-expect -> PARTIAL/upgrade, never a silent false-COMPLETE)."""
    try:
        from datetime import date as _date, datetime as _dt  # noqa: PLC0415
        from zoneinfo import ZoneInfo  # noqa: PLC0415

        from src.config import runtime_cities_by_name  # noqa: PLC0415

        cycle_dt = _dt.fromisoformat(str(cycle_iso).replace("Z", "+00:00"))
        city_obj = runtime_cities_by_name().get(city)
        tz = getattr(city_obj, "timezone", None) if city_obj is not None else None
        cycle_local = cycle_dt.astimezone(ZoneInfo(tz)).date() if tz else cycle_dt.date()
        return max(0, (_date.fromisoformat(str(target_date)) - cycle_local).days)
    except Exception:
        return 0


def scope_capture_offers_larger_provider_set(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    metric: str,
    changed_sources: Sequence[str] | None = None,
    decision_time: datetime | None = None,
) -> dict[str, object]:
    """Return whether a larger family set or changed consumed input requires materialization.

    Returns a dict:
      {is_upgrade, family_upgrade, input_revision_changed, source_cycle_time,
       served_families, capturable_families, new_families, changed_input_sources,
       changed_input_revisions}.

    ``changed_sources`` narrows source-clock commit callbacks to the provider that just landed.
    Periodic catch-up omits it and compares every configured/previously-consumed source. Exact raw
    row ids make repeated polls no-ops once the resulting posterior commits.
    """
    (
        source_cycle_iso,
        served,
        consumed_inputs,
        configured_sources,
        consumed_day0_vector_revision,
        day0_expected_models,
        day0_causal_bundle_valid,
    ) = _latest_posterior_inputs(conn, city=city, target_date=target_date, metric=metric)
    if source_cycle_iso is None:
        return {
            "is_upgrade": False,
            "source_cycle_time": None,
            "served_families": [],
            "capturable_families": [],
            "new_families": [],
            "family_upgrade": False,
            "input_revision_changed": False,
            "changed_input_sources": [],
            "changed_input_revisions": {},
        }
    capturable_inputs = _capturable_inputs_for_scope(
        conn,
        city=city,
        target_date=target_date,
        metric=metric,
        source_cycle_iso=source_cycle_iso,
        decision_time=decision_time,
    )
    capturable = decorrelated_provider_families_of(set(capturable_inputs))
    # DOMAIN-AWARE gate (2026-06-17 coarse-global removal): a family that is STRUCTURALLY ABSENT
    # for this city (NCEP/CMC outside their nest domains, now that the global fallbacks are gone)
    # must NEVER trigger an upgrade re-enqueue — there is no provider that can ever land, so the
    # chase would loop forever. Intersect capturable with the per-city expected set so only a
    # family that is BOTH capturable AND expected-here can count as a growth target. Fail-open
    # (expected = all families) when coords are missing, which preserves the exact pre-removal
    # comparison (capturable already excludes structurally-absent families via missing rows).
    _latlon = _city_latlon(city)
    _lead = _scope_lead_days(city, target_date, source_cycle_iso)
    expected = (
        expected_provider_families_for_city(_latlon[0], _latlon[1], _lead)
        if _latlon is not None
        else frozenset(DECORRELATED_PROVIDER_FAMILIES)
    )
    capturable_expected = capturable & expected
    new_families = capturable_expected - served
    # STRICT superset: the capturable-and-expected set must add a family the served set lacks. A
    # served set with no fusion (empty) is NOT upgraded here — there is no smaller-set posterior
    # to grow (the single-anchor fallback is a separate concern handled by the missing-capture gate).
    family_upgrade = bool(served) and bool(new_families) and served.issubset(capturable_expected)
    station_sources = frozenset(
        source
        for source in capturable_inputs
        if source.startswith(("cwa_", "hko_"))
    )
    relevant_sources = (configured_sources or frozenset(consumed_inputs)) | station_sources
    requested_sources = None
    if changed_sources is not None:
        requested_sources = frozenset(
            str(source).strip() for source in changed_sources if str(source).strip()
        )
        relevant_sources &= requested_sources
    changed_inputs = sorted(
        source
        for source in relevant_sources
        if source in capturable_inputs
        and capturable_inputs[source] != consumed_inputs.get(source)
    )
    changed_revisions: dict[str, object] = {
        source: capturable_inputs[source] for source in changed_inputs
    }
    day0_revision_requested = (
        requested_sources is None
        or _DAY0_HOURLY_VECTOR_SOURCE in requested_sources
    )
    if consumed_day0_vector_revision is not None and day0_revision_requested:
        current_day0_vector_revision = _capturable_day0_vector_revision(
            conn,
            city=city,
            target_date=target_date,
            expected_models=day0_expected_models,
            decision_time=decision_time,
        )
        if (
            current_day0_vector_revision is not None
            and current_day0_vector_revision != consumed_day0_vector_revision
        ):
            changed_inputs.append(_DAY0_HOURLY_VECTOR_SOURCE)
            changed_inputs.sort()
            changed_revisions[_DAY0_HOURLY_VECTOR_SOURCE] = (
                current_day0_vector_revision
            )
        if current_day0_vector_revision is not None and not day0_causal_bundle_valid:
            # SCOPE: this exact city/date/metric posterior. DRAIN: the existing
            # single-flight fusion seed materializes the unchanged vector rows
            # with a causal bundle. RESET: the newest self-validating posterior
            # makes this predicate false. Missing bundle is therefore a real
            # input revision even when the vector IDs themselves did not move.
            changed_inputs.append(_DAY0_CAUSAL_BUNDLE_SOURCE)
            changed_inputs.sort()
            changed_revisions[_DAY0_CAUSAL_BUNDLE_SOURCE] = (
                current_day0_vector_revision
            )
    input_revision_changed = bool(changed_inputs)
    return {
        "is_upgrade": family_upgrade or input_revision_changed,
        "family_upgrade": family_upgrade,
        "input_revision_changed": input_revision_changed,
        "source_cycle_time": source_cycle_iso,
        "served_families": sorted(served),
        "capturable_families": sorted(capturable_expected),
        "new_families": sorted(new_families),
        "changed_input_sources": changed_inputs,
        "changed_input_revisions": changed_revisions,
    }


def _new_seed_publication(
    seed_file: Path,
    transition_keys: Sequence[str] = (),
) -> _SeedPublication:
    owner = uuid4().hex
    transition_digest = hashlib.sha256(
        "\n".join(sorted(set(transition_keys))).encode("utf-8")
    ).hexdigest()
    queue_file = seed_file.with_name(
        f"{seed_file.stem}.transition-{transition_digest}{seed_file.suffix}"
    ).absolute()
    staging_file = (
        queue_file.parent
        / ".fusion_upgrade_staging"
        / f"{queue_file.name}.{owner}.json"
    )
    publish_temp = queue_file.with_name(f".{queue_file.name}.{owner}.publish")
    return _SeedPublication(
        owner=owner,
        staging_file=staging_file,
        publish_temp=publish_temp,
        seed_file=queue_file,
    )


def _publication_value(prefix: str, publication: _SeedPublication) -> str:
    return prefix + json.dumps(
        {
            "owner": publication.owner,
            "publish_temp": str(publication.publish_temp),
            "seed_file": str(publication.seed_file),
            "staging_file": str(publication.staging_file),
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _parse_publication(
    value: object,
    *,
    prefix: str,
) -> _SeedPublication | None:
    raw = str(value or "")
    if not raw.startswith(prefix):
        return None
    try:
        payload = json.loads(raw[len(prefix) :])
        if not isinstance(payload, Mapping):
            return None
        owner = str(payload["owner"]).strip()
        staging_file = Path(str(payload["staging_file"]))
        publish_temp = Path(str(payload["publish_temp"]))
        seed_file = Path(str(payload["seed_file"]))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if not owner:
        return None
    return _SeedPublication(
        owner=owner,
        staging_file=staging_file,
        publish_temp=publish_temp,
        seed_file=seed_file,
    )


def _cleanup_private_publication(publication: _SeedPublication) -> None:
    publication.publish_temp.unlink(missing_ok=True)
    publication.staging_file.unlink(missing_ok=True)


def _fsync_file(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | int(getattr(os, "O_DIRECTORY", 0))
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _ensure_directory_entry_durable(
    directory: Path,
    *,
    durable_ancestor: Path,
) -> None:
    directory = directory.absolute()
    durable_ancestor = durable_ancestor.absolute()
    try:
        relative = directory.relative_to(durable_ancestor)
    except ValueError as exc:
        raise ValueError(
            f"{directory} is outside durable ancestor {durable_ancestor}"
        ) from exc
    if not durable_ancestor.is_dir():
        raise FileNotFoundError(
            f"durable directory ancestor missing: {durable_ancestor}"
        )
    parent = durable_ancestor
    for part in relative.parts:
        child = parent / part
        child.mkdir(exist_ok=True)
        if not child.is_dir():
            raise NotADirectoryError(child)
        # Retry this even for an existing child: mkdir may have succeeded
        # before a prior parent-directory fsync failed.
        _fsync_directory(parent)
        parent = child


def _fsync_staged_seed(
    staging_file: Path,
    *,
    durable_ancestor: Path,
) -> None:
    """Make the private seed and its directory durable before SQLite references it."""
    staging_directory = staging_file.parent
    _ensure_directory_entry_durable(
        staging_directory,
        durable_ancestor=durable_ancestor,
    )
    _fsync_file(staging_file)
    _fsync_directory(staging_directory)


def _fsync_publication_directories(publication: _SeedPublication) -> None:
    """Make the atomic publish durable before SQLite advertises the public path."""
    directories = {
        publication.publish_temp.parent,
        publication.seed_file.parent,
    }
    for directory in sorted(directories, key=str):
        _fsync_directory(directory)


def _publish_finalized_seed(publication: _SeedPublication) -> None:
    """Publish a finalized private seed exactly once, even across a crash."""
    publication.seed_file.parent.mkdir(parents=True, exist_ok=True)
    if publication.seed_file.exists():
        if publication.staging_file.exists() and os.path.samefile(
            publication.staging_file,
            publication.seed_file,
        ):
            _fsync_publication_directories(publication)
            return
        raise RuntimeError(
            "fusion seed queue path is occupied by a different publication: "
            f"{publication.seed_file}"
        )
    if publication.publish_temp.exists():
        os.replace(publication.publish_temp, publication.seed_file)
        _fsync_publication_directories(publication)
        return
    if not publication.staging_file.exists():
        raise FileNotFoundError(
            f"finalized fusion seed staging missing: {publication.staging_file}"
        )
    # A queue consumer moves, rather than unlinks, claimed seeds. The private
    # staging hardlink therefore retains st_nlink > 1 after a successful publish
    # even when the queue path has already disappeared. Do not republish it.
    if publication.staging_file.stat().st_nlink > 1:
        _fsync_publication_directories(publication)
        return
    try:
        os.link(publication.staging_file, publication.publish_temp)
    except FileExistsError:
        pass
    if publication.publish_temp.exists():
        os.replace(publication.publish_temp, publication.seed_file)
        _fsync_publication_directories(publication)
        return
    if publication.seed_file.exists() or publication.staging_file.stat().st_nlink > 1:
        _fsync_publication_directories(publication)
        return
    raise RuntimeError(
        f"fusion seed atomic publish lost both temp and queue path: {publication.seed_file}"
    )


def _complete_published_enqueues(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    metric: str,
    source_cycle_iso: str,
    transition_keys: Sequence[str],
    publish_pending: str,
    seed_file: Path,
) -> int:
    try:
        for transition_key in transition_keys:
            conn.execute(
                """
                UPDATE fusion_upgrade_enqueues
                SET seed_file = ?
                WHERE city = ? AND target_date = ? AND metric = ?
                  AND source_cycle_time = ? AND capturable_family_set = ?
                  AND seed_file = ?
                """,
                (
                    str(seed_file),
                    city,
                    target_date,
                    metric,
                    source_cycle_iso,
                    transition_key,
                    publish_pending,
                ),
            )
        rows = tuple(
            conn.execute(
                """
                SELECT seed_file
                FROM fusion_upgrade_enqueues
                WHERE city = ? AND target_date = ? AND metric = ?
                  AND source_cycle_time = ? AND capturable_family_set = ?
                """,
                (
                    city,
                    target_date,
                    metric,
                    source_cycle_iso,
                    transition_key,
                ),
            ).fetchone()
            for transition_key in transition_keys
        )
        if any(row is None or str(row["seed_file"]) != str(seed_file) for row in rows):
            raise RuntimeError(
                "fusion-upgrade published marker did not converge to the queue path"
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return len(transition_keys)


def _recover_pending_publications(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    metric: str,
    source_cycle_iso: str,
) -> None:
    rows = conn.execute(
        """
        SELECT DISTINCT seed_file
        FROM fusion_upgrade_enqueues
        WHERE city = ? AND target_date = ? AND metric = ?
          AND source_cycle_time = ?
          AND seed_file LIKE ?
        """,
        (
            city,
            target_date,
            metric,
            source_cycle_iso,
            f"{_PUBLISH_PENDING_PREFIX}%",
        ),
    ).fetchall()
    for row in rows:
        publish_pending = str(row["seed_file"])
        publication = _parse_publication(
            publish_pending,
            prefix=_PUBLISH_PENDING_PREFIX,
        )
        if publication is None:
            continue
        _publish_finalized_seed(publication)
        transition_rows = conn.execute(
            """
            SELECT capturable_family_set
            FROM fusion_upgrade_enqueues
            WHERE city = ? AND target_date = ? AND metric = ?
              AND source_cycle_time = ? AND seed_file = ?
            """,
            (
                city,
                target_date,
                metric,
                source_cycle_iso,
                publish_pending,
            ),
        ).fetchall()
        transition_keys = tuple(
            str(transition_row["capturable_family_set"])
            for transition_row in transition_rows
        )
        if transition_keys:
            _complete_published_enqueues(
                conn,
                city=city,
                target_date=target_date,
                metric=metric,
                source_cycle_iso=source_cycle_iso,
                transition_keys=transition_keys,
                publish_pending=publish_pending,
                seed_file=publication.seed_file,
            )
        _cleanup_private_publication(publication)


def _reserve_enqueues(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    metric: str,
    source_cycle_iso: str,
    served_family_key: str,
    transition_keys: Sequence[str],
    publication: _SeedPublication,
) -> tuple[str, tuple[str, ...]]:
    """Durably claim all unresolved transition rows as one fenced ownership set."""
    _recover_pending_publications(
        conn,
        city=city,
        target_date=target_date,
        metric=metric,
        source_cycle_iso=source_cycle_iso,
    )
    reserved_at = datetime.now(tz=UTC)
    stale_before = reserved_at - _RESERVATION_TTL
    reservation = _publication_value(_RESERVATION_PREFIX, publication)
    reserved: list[str] = []
    observed_before_lock: dict[str, sqlite3.Row | None] = {
        transition_key: conn.execute(
            """
            SELECT enqueued_at, seed_file
            FROM fusion_upgrade_enqueues
            WHERE city = ? AND target_date = ? AND metric = ?
              AND source_cycle_time = ? AND capturable_family_set = ?
            LIMIT 1
            """,
            (city, target_date, metric, source_cycle_iso, transition_key),
        ).fetchone()
        for transition_key in transition_keys
    }
    reclaimable_markers: dict[str, str] = {}
    for transition_key, row in observed_before_lock.items():
        if row is None:
            continue
        marker_value = str(row["seed_file"] or "")
        if marker_value.startswith(
            (_PUBLISH_PENDING_PREFIX, _RESERVATION_PREFIX)
        ):
            continue
        queue_state = _finalized_seed_has_active_queue_work(marker_value)
        if queue_state is False:
            reclaimable_markers[transition_key] = marker_value
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing: dict[str, sqlite3.Row | None] = {
            transition_key: conn.execute(
                """
                SELECT enqueued_at, seed_file
                FROM fusion_upgrade_enqueues
                WHERE city = ? AND target_date = ? AND metric = ?
                  AND source_cycle_time = ? AND capturable_family_set = ?
                LIMIT 1
                """,
                (city, target_date, metric, source_cycle_iso, transition_key),
            ).fetchone()
            for transition_key in transition_keys
        }
        for transition_key, row in existing.items():
            if row is None:
                continue
            marker_value = str(row["seed_file"] or "")
            if _parse_publication(
                marker_value,
                prefix=_PUBLISH_PENDING_PREFIX,
            ) is not None:
                conn.rollback()
                return reservation, ()
            if marker_value.startswith(_PUBLISH_PENDING_PREFIX):
                conn.rollback()
                return reservation, ()
            foreign = _parse_publication(
                marker_value,
                prefix=_RESERVATION_PREFIX,
            )
            if marker_value.startswith(_RESERVATION_PREFIX) and foreign is None:
                conn.rollback()
                return reservation, ()
            if foreign is None:
                # SCOPE: this exact scope/cycle/transition publication.
                # DRAIN: the queue consumes public/request/inflight work, or
                # records a terminal receipt for the exact seed.
                # RESET: an absent exact queue/terminal witness while the caller's
                # comparison still proves the posterior stale permits reclaim.
                # Queue inspection happens before the DB writer lock; the exact
                # marker match below is the compare-and-swap fence.
                continue
            try:
                marker_time = datetime.fromisoformat(
                    str(row["enqueued_at"])
                ).astimezone(UTC)
            except (TypeError, ValueError):
                marker_time = datetime.min.replace(tzinfo=UTC)
            # SCOPE: the complete unresolved transition set for this scope/cycle.
            # DRAIN: the owner finalizes all keys before publishing one seed.
            # RESET: only an expired reservation can be atomically fenced out.
            if marker_time > stale_before:
                conn.rollback()
                return reservation, ()
        for transition_key in transition_keys:
            row = existing[transition_key]
            if row is None:
                inserted = conn.execute(
                    """
                    INSERT OR IGNORE INTO fusion_upgrade_enqueues
                        (enqueued_at, city, target_date, metric, source_cycle_time,
                         served_family_set, capturable_family_set, seed_file)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        reserved_at.isoformat(),
                        city,
                        target_date,
                        metric,
                        source_cycle_iso,
                        served_family_key,
                        transition_key,
                        reservation,
                    ),
                ).rowcount
                if inserted:
                    reserved.append(transition_key)
                continue
            marker_value = str(row["seed_file"] or "")
            foreign = _parse_publication(
                marker_value,
                prefix=_RESERVATION_PREFIX,
            )
            if foreign is None:
                if reclaimable_markers.get(transition_key) == marker_value:
                    updated = conn.execute(
                        """
                        UPDATE fusion_upgrade_enqueues
                        SET enqueued_at = ?, served_family_set = ?, seed_file = ?
                        WHERE city = ? AND target_date = ? AND metric = ?
                          AND source_cycle_time = ? AND capturable_family_set = ?
                          AND seed_file = ?
                        """,
                        (
                            reserved_at.isoformat(),
                            served_family_key,
                            reservation,
                            city,
                            target_date,
                            metric,
                            source_cycle_iso,
                            transition_key,
                            marker_value,
                        ),
                    ).rowcount
                    if updated:
                        reserved.append(transition_key)
                continue
            updated = conn.execute(
                """
                UPDATE fusion_upgrade_enqueues
                SET enqueued_at = ?, served_family_set = ?, seed_file = ?
                WHERE city = ? AND target_date = ? AND metric = ?
                  AND source_cycle_time = ? AND capturable_family_set = ?
                  AND seed_file = ?
                """,
                (
                    reserved_at.isoformat(),
                    served_family_key,
                    reservation,
                    city,
                    target_date,
                    metric,
                    source_cycle_iso,
                    transition_key,
                    marker_value,
                ),
            ).rowcount
            if updated:
                reserved.append(transition_key)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return reservation, tuple(reserved)


def _finalized_seed_has_active_queue_work(seed_file: str) -> bool | None:
    """Return True/False for exact queue work; None when fencing is uncertain."""
    marker = str(seed_file or "").strip()
    if not marker:
        return None
    path = Path(marker)
    if path.parent.name != "seeds":
        return None
    queue_root = path.parent.parent
    try:
        from src.data.replacement_forecast_live_materialization_queue import (  # noqa: PLC0415
            _queue_lock,
            _seed_terminal_receipt_index_path,
        )

        with _queue_lock(queue_root / ".materialization_queue.lock") as acquired:
            if not acquired:
                return None
            if path.is_file() or (queue_root / "requests" / path.name).is_file():
                return True
            inflight = queue_root / "inflight"
            try:
                batches = tuple(inflight.iterdir())
            except FileNotFoundError:
                batches = ()
            for batch in batches:
                if batch.is_dir() and (batch / path.name).is_file():
                    return True
            indexed_receipt = _seed_terminal_receipt_index_path(path)
            if indexed_receipt is not None and indexed_receipt.is_file():
                try:
                    indexed = json.loads(
                        indexed_receipt.read_text(encoding="utf-8")
                    )
                except (OSError, TypeError, ValueError, json.JSONDecodeError):
                    return None
                if str(indexed.get("seed_file") or "") != marker:
                    return None
                if str(indexed.get("status") or "") in {
                    "SKIPPED_UNCHANGED_BLOCKED_INPUT",
                    "SKIPPED_ALREADY_COVERED",
                    "SKIPPED_SOURCE_CYCLE_REGRESSION",
                }:
                    return True
            processed = queue_root / "seed_processed" / path.name
            receipt = processed.with_suffix(processed.suffix + ".receipt.json")
            if processed.exists():
                if not receipt.is_file():
                    return None
                try:
                    status = str(
                        json.loads(receipt.read_text(encoding="utf-8")).get(
                            "status", ""
                        )
                    )
                except (OSError, TypeError, ValueError, json.JSONDecodeError):
                    return None
                if status in {
                    "SKIPPED_UNCHANGED_BLOCKED_INPUT",
                    "SKIPPED_ALREADY_COVERED",
                    "SKIPPED_SOURCE_CYCLE_REGRESSION",
                }:
                    return True
            return False
    except OSError:
        return None


def _release_enqueue_reservations(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    metric: str,
    source_cycle_iso: str,
    transition_keys: Sequence[str],
    reservation: str,
) -> None:
    try:
        for transition_key in transition_keys:
            conn.execute(
                """
                DELETE FROM fusion_upgrade_enqueues
                WHERE city = ? AND target_date = ? AND metric = ?
                  AND source_cycle_time = ? AND capturable_family_set = ?
                  AND seed_file = ?
                """,
                (
                    city,
                    target_date,
                    metric,
                    source_cycle_iso,
                    transition_key,
                    reservation,
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _finalize_enqueue_reservations(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    metric: str,
    source_cycle_iso: str,
    transition_keys: Sequence[str],
    reservation: str,
    publication: _SeedPublication,
) -> str:
    publish_pending = _publication_value(
        _PUBLISH_PENDING_PREFIX,
        publication,
    )
    finalized = 0
    try:
        for transition_key in transition_keys:
            finalized += conn.execute(
                """
                UPDATE fusion_upgrade_enqueues
                SET seed_file = ?
                WHERE city = ? AND target_date = ? AND metric = ?
                  AND source_cycle_time = ? AND capturable_family_set = ?
                  AND seed_file = ?
                """,
                (
                    publish_pending,
                    city,
                    target_date,
                    metric,
                    source_cycle_iso,
                    transition_key,
                    reservation,
                ),
            ).rowcount
        if finalized != len(transition_keys):
            raise RuntimeError(
                "fusion-upgrade reservation ownership changed before finalize"
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return publish_pending


def _publication_has_durable_marker_reference(
    conn: sqlite3.Connection,
    publication: _SeedPublication,
) -> bool:
    """Fail closed when SQLite may still rely on this publication's staging file."""
    reservation = _publication_value(_RESERVATION_PREFIX, publication)
    publish_pending = _publication_value(_PUBLISH_PENDING_PREFIX, publication)
    try:
        return (
            conn.execute(
                """
                SELECT 1
                FROM fusion_upgrade_enqueues
                WHERE seed_file IN (?, ?)
                LIMIT 1
                """,
                (reservation, publish_pending),
            ).fetchone()
            is not None
        )
    except Exception:
        return True


def enqueue_fusion_upgrade_reseeds(
    *,
    forecast_db: Path | str,
    seed_dir: Path | str,
    raw_manifest_dir: Path | str,
    computed_at: datetime | None = None,
    limit: int = 50,
    scopes: Sequence[tuple[str, str, str]] | None = None,
    changed_sources: Sequence[str] | None = None,
    manifests: Sequence[RawForecastArtifactManifest] | None = None,
) -> dict[str, object]:
    """Enqueue scopes with a larger provider set or changed persisted input revision.

    Family growth retains the durable marker. Exact input revisions close their own loop through
    posterior provenance, while pending duplicate seeds are coalesced by semantic request key.
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
        "status": "FUSION_UPGRADE_TRIGGER",
        "scopes_checked": 0,
        "upgrades_detected": 0,
        "input_revisions_detected": 0,
        "day0_conditioned_upgrades": 0,
        "day0_skipped": 0,
        "seeds_enqueued": 0,
        "already_enqueued": 0,
        "manifest_missing": 0,
        "enqueued": [],
    }
    if not forecast_db.exists():
        report["status"] = "FUSION_UPGRADE_FORECAST_DB_MISSING"
        return report
    # The configured layout is STATE_DIR / queue_root / seeds.  STATE_DIR must
    # pre-exist so this poll can durably create every descendant before SQLite.
    staging_durable_ancestor = seed_path.parent.parent.absolute()
    if not staging_durable_ancestor.is_dir():
        report["status"] = "FUSION_UPGRADE_STAGING_ANCESTOR_MISSING"
        report["staging_durable_ancestor"] = str(staging_durable_ancestor)
        return report

    if scopes is None:
        # Periodic catch-up retains the full current-target authority. Source-clock
        # commits pass exact durable scopes below and avoid this global DB plan.
        plan = build_replacement_forecast_current_target_plan(
            forecast_db,
            min_target_date=now.date().isoformat(),
            require_raw_artifacts=False,
            now_utc=now,
        )
        if plan.status == "BLOCKED":
            report["status"] = "FUSION_UPGRADE_PLAN_BLOCKED"
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

    # Periodic discovery owns a global catch-up scan.  An exact source-clock or
    # held-family repair does not: walking the full raw-manifest tree before a
    # one-family comparison can retain every belief-reseed worker behind
    # unrelated historical artifacts.  Keep caller-supplied manifests exact;
    # otherwise defer scoped lookup until the forecast DB is open below.
    default_manifests = (
        _load_manifests(raw_dir, computed_at=now)
        if scopes is None and manifests is None
        else tuple(manifests) if manifests is not None else None
    )

    conn = _connect(forecast_db, write_class="live")
    conn.row_factory = sqlite3.Row
    try:
        ensure_replacement_forecast_live_schema(conn)
        enqueued = 0
        # NEAREST-TARGET-FIRST (mirrors the seed-budget K-decision, registry member #6): the
        # plan's native order is target_date DESC, which would spend the per-tick enqueue budget
        # on far-date non-tradeable scopes while the tradeable day0/day1 money scopes starve.
        for city, target_date, metric, day0_required in sorted(
            candidates,
            key=lambda scope: scope[:3],
        ):
            if enqueued >= max(1, int(limit)):
                break
            scope_manifests = default_manifests
            if scope_manifests is None:
                from src.data.replacement_cycle_advance_trigger import (  # noqa: PLC0415
                    _family_manifests_from_db,
                )

                scope_manifests = _family_manifests_from_db(
                    conn,
                    city=city,
                    identity=expected_replacement_dependency_identity_by_role(metric)[
                        "openmeteo_ifs9_anchor"
                    ],
                    computed_at=now,
                )
            day0_payload: dict[str, object] = {}
            # A Day0 input revision still changes q, but its re-materialization must
            # preserve the canonical observed-extreme conditioning.  Skipping Day0
            # here leaves REPLACEMENT_RAW_INPUT_HWM with no RESET: cycle-advance
            # correctly sees the same carrier cycle while fusion owns the changed
            # raw-row identity.  Build the fusion seed with the same canonical Day0
            # payload used by cycle-advance instead of emitting a plain seed.
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
                report["day0_conditioned_upgrades"] = (
                    int(report["day0_conditioned_upgrades"]) + 1
                )
            report["scopes_checked"] = int(report["scopes_checked"]) + 1
            try:
                verdict = scope_capture_offers_larger_provider_set(
                    conn,
                    city=city,
                    target_date=target_date,
                    metric=metric,
                    changed_sources=changed_sources,
                    decision_time=now,
                )
            except Exception as exc:  # noqa: BLE001 — per-scope fail-soft
                _LOG.debug("fusion-upgrade comparison failed for %s/%s/%s: %s", city, target_date, metric, exc)
                continue
            if not verdict["is_upgrade"]:
                continue
            report["upgrades_detected"] = int(report["upgrades_detected"]) + 1
            if verdict["input_revision_changed"]:
                report["input_revisions_detected"] = (
                    int(report["input_revisions_detected"]) + 1
                )
            source_cycle_iso = str(verdict["source_cycle_time"])
            # CYCLE-AGE GUARD (live-run finding 2026-06-11): the materializer refuses a request
            # whose cycle exceeds the staleness bound (cycle_age_exceeds_bound -> CYCLE_TOO_OLD),
            # so enqueueing an upgrade for a posterior stuck on an over-age cycle only spawns a
            # guaranteed-failure subprocess. The SAME policy function decides here (single
            # authority: replacement_forecast_cycle_policy) — such a scope heals on the next
            # fresh-cycle materialization instead.
            try:
                from src.data.replacement_forecast_cycle_policy import (  # noqa: PLC0415
    cycle_age_outside_bound,
                )

                _cycle_dt = datetime.fromisoformat(source_cycle_iso.replace("Z", "+00:00"))
                if cycle_age_outside_bound(now, _cycle_dt):
                    report["cycle_too_old_skipped"] = int(report.get("cycle_too_old_skipped", 0)) + 1  # type: ignore[arg-type]
                    continue
            except Exception:  # noqa: BLE001 — unparseable cycle: let the materializer decide
                pass
            capturable_key = _family_set_key(set(verdict["capturable_families"]))  # type: ignore[arg-type]
            served_key = _family_set_key(set(verdict["served_families"]))  # type: ignore[arg-type]
            revision_update = bool(verdict["input_revision_changed"])
            transition_keys: list[str] = []
            if verdict["family_upgrade"]:
                transition_keys.append(capturable_key)
            if revision_update:
                transition_keys.append(
                    _input_revision_marker_key(
                        capturable_key,
                        verdict["changed_input_revisions"],  # type: ignore[arg-type]
                    )
                )
            seed_file = seed_path / _seed_name(
                {
                    "city": city,
                    "target_date": target_date,
                    "temperature_metric": metric,
                },
                computed_at=now,
            )
            publication = _new_seed_publication(
                seed_file,
                transition_keys,
            )
            try:
                reservation, reserved_keys = _reserve_enqueues(
                    conn,
                    city=city,
                    target_date=target_date,
                    metric=metric,
                    source_cycle_iso=source_cycle_iso,
                    served_family_key=served_key,
                    transition_keys=transition_keys,
                    publication=publication,
                )
            except Exception as exc:  # noqa: BLE001 — per-scope fail-soft
                report["reservation_failed"] = int(
                    report.get("reservation_failed", 0)
                ) + 1
                _LOG.debug(
                    "fusion-upgrade reservation failed for %s/%s/%s: %s",
                    city,
                    target_date,
                    metric,
                    exc,
                )
                continue
            if not reserved_keys:
                report["already_enqueued"] = int(report["already_enqueued"]) + 1
                continue
            # Build the seed from the SAME manifests/coverage/bins the seed discovery uses, then
            # atomically write it into this owner's hidden staging path. Only a durable,
            # all-transition ownership finalize may expose it to the existing queue.
            try:
                staging_file = _build_and_write_upgrade_seed(
                    conn,
                    city=city,
                    target_date=target_date,
                    metric=metric,
                    manifests=scope_manifests,
                    raw_dir=raw_dir,
                    seed_path=seed_path,
                    seed_file=publication.staging_file,
                    computed_at=now,
                    source_cycle_time=source_cycle_iso,
                    build_seed=build_replacement_forecast_materialization_seed,
                    latest_baseline_coverage=latest_baseline_coverage_for_replacement_seed,
                    market_bins=market_bins_for_replacement_seed,
                    write_seed=write_seed,
                    latest_manifest=_latest_manifest,
                    manifest_path_value=_manifest_path_value,
                    manifest_base_dir=_manifest_base_dir,
                    resolve_path=_resolve_path,
                    expected_identity=expected_replacement_dependency_identity_by_role,
                    day0_payload=day0_payload,
                )
            except Exception as exc:  # noqa: BLE001 — per-scope fail-soft
                report["seed_build_failed"] = int(
                    report.get("seed_build_failed", 0)
                ) + 1
                try:
                    _release_enqueue_reservations(
                        conn,
                        city=city,
                        target_date=target_date,
                        metric=metric,
                        source_cycle_iso=source_cycle_iso,
                        transition_keys=reserved_keys,
                        reservation=reservation,
                    )
                except Exception as release_exc:  # noqa: BLE001
                    report["reservation_release_failed"] = int(
                        report.get("reservation_release_failed", 0)
                    ) + 1
                    _LOG.warning(
                        "fusion-upgrade reservation release failed for %s/%s/%s: %s",
                        city,
                        target_date,
                        metric,
                        release_exc,
                    )
                _cleanup_private_publication(publication)
                _LOG.debug("fusion-upgrade seed build failed for %s/%s/%s: %s", city, target_date, metric, exc)
                continue
            if staging_file is None:
                report["manifest_missing"] = int(report["manifest_missing"]) + 1
                try:
                    _release_enqueue_reservations(
                        conn,
                        city=city,
                        target_date=target_date,
                        metric=metric,
                        source_cycle_iso=source_cycle_iso,
                        transition_keys=reserved_keys,
                        reservation=reservation,
                    )
                except Exception as release_exc:  # noqa: BLE001
                    report["reservation_release_failed"] = int(
                        report.get("reservation_release_failed", 0)
                    ) + 1
                    _LOG.warning(
                        "fusion-upgrade reservation release failed for %s/%s/%s: %s",
                        city,
                        target_date,
                        metric,
                        release_exc,
                    )
                _cleanup_private_publication(publication)
                continue
            try:
                _fsync_staged_seed(
                    staging_file,
                    durable_ancestor=staging_durable_ancestor,
                )
            except Exception as exc:  # noqa: BLE001 — reservation retains retry ownership
                report["seed_staging_fsync_failed"] = int(
                    report.get("seed_staging_fsync_failed", 0)
                ) + 1
                _LOG.warning(
                    "fusion-upgrade staging durability failed for %s/%s/%s: %s",
                    city,
                    target_date,
                    metric,
                    exc,
                )
                continue
            try:
                publish_pending = _finalize_enqueue_reservations(
                    conn,
                    city=city,
                    target_date=target_date,
                    metric=metric,
                    source_cycle_iso=source_cycle_iso,
                    transition_keys=reserved_keys,
                    reservation=reservation,
                    publication=publication,
                )
            except Exception as exc:  # noqa: BLE001 — a lost owner must never publish
                report["reservation_finalize_failed"] = int(
                    report.get("reservation_finalize_failed", 0)
                ) + 1
                if not _publication_has_durable_marker_reference(
                    conn,
                    publication,
                ):
                    _cleanup_private_publication(publication)
                _LOG.warning(
                    "fusion-upgrade reservation finalize failed for %s/%s/%s: %s",
                    city,
                    target_date,
                    metric,
                    exc,
                )
                continue
            try:
                _publish_finalized_seed(publication)
            except Exception as exc:  # noqa: BLE001 — durable pending marker owns recovery
                report["seed_publish_failed"] = int(
                    report.get("seed_publish_failed", 0)
                ) + 1
                _LOG.warning(
                    "fusion-upgrade seed publish failed for %s/%s/%s: %s",
                    city,
                    target_date,
                    metric,
                    exc,
                )
                continue
            try:
                completed = _complete_published_enqueues(
                    conn,
                    city=city,
                    target_date=target_date,
                    metric=metric,
                    source_cycle_iso=source_cycle_iso,
                    transition_keys=reserved_keys,
                    publish_pending=publish_pending,
                    seed_file=publication.seed_file,
                )
            except Exception as exc:  # noqa: BLE001 — staging proves publish for retry
                report["publish_marker_complete_failed"] = int(
                    report.get("publish_marker_complete_failed", 0)
                ) + 1
                _LOG.warning(
                    "fusion-upgrade published marker completion failed for %s/%s/%s: %s",
                    city,
                    target_date,
                    metric,
                    exc,
                )
                continue
            _cleanup_private_publication(publication)
            if completed:
                enqueued += 1
                report["seeds_enqueued"] = int(report["seeds_enqueued"]) + 1
                report["enqueued"].append(  # type: ignore[union-attr]
                    {
                        "city": city,
                        "target_date": target_date,
                        "metric": metric,
                        "source_cycle_time": source_cycle_iso,
                        "served_families": verdict["served_families"],
                        "capturable_families": verdict["capturable_families"],
                        "new_families": verdict["new_families"],
                        "changed_input_sources": verdict["changed_input_sources"],
                        "seed_file": str(publication.seed_file),
                    }
                )
            else:
                report["already_enqueued"] = int(report["already_enqueued"]) + 1
    finally:
        conn.close()
    return report


def _build_and_write_upgrade_seed(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    metric: str,
    manifests,
    raw_dir: Path,
    seed_path: Path,
    seed_file: Path,
    computed_at: datetime,
    source_cycle_time: datetime | str,
    build_seed,
    latest_baseline_coverage,
    market_bins,
    write_seed,
    latest_manifest,
    manifest_path_value,
    manifest_base_dir,
    resolve_path,
    expected_identity,
    day0_payload: Mapping[str, object] | None = None,
) -> Path | None:
    """Build one re-materialization seed for a scope using the existing seed-builder pieces and
    atomically write it into private staging. Returns the staging Path, or None when the required
    manifests/context are absent (the scope's raw inputs are not on disk — recorded as
    manifest_missing, retried next tick once they land)."""
    expected = expected_identity(metric)
    from src.config import cities_by_name  # noqa: PLC0415

    city_cfg = cities_by_name.get(city)
    city_timezone = str(getattr(city_cfg, "timezone", "") or "") or None

    def cycle_utc(value: datetime | str) -> datetime:
        parsed = (
            value
            if isinstance(value, datetime)
            else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        )
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    expected_cycle = cycle_utc(source_cycle_time)
    openmeteo = latest_manifest(
        manifests,
        source_id=expected["openmeteo_ifs9_anchor"].source_id,
        data_version=expected["openmeteo_ifs9_anchor"].data_version,
        city=city,
        target_date=target_date,
        city_timezone=city_timezone,
        cycle_admissible=lambda manifest: cycle_utc(
            manifest.source_cycle_time
        )
        == expected_cycle,
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
        as_of_time=computed_at,
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
        **dict(day0_payload or {}),
    )
    if not seed_result.ok or seed_result.seed is None:
        return None
    # Thread the honest upgrade-trigger provenance note into the seed so the re-materialized
    # posterior records WHY it was produced (instrument-set expansion, not a fresh cycle).
    seed_payload: dict[str, object] = dict(seed_result.seed)
    seed_payload["upgrade_trigger"] = "instrument_set_expansion"
    write_seed(seed_file, seed_payload)
    return seed_file

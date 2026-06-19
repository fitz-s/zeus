# Created: 2026-06-10
# Last reused or audited: 2026-06-10
# Authority basis: pipeline-contract project, operator directive 2026-06-10
"""Typed boundary contracts for the replacement-forecast pipeline file kinds.

The replacement-forecast pipeline moves three distinct JSON file kinds between
producers and consumers, and until now each kind existed only as an *implicit*
dict convention scattered across a seed builder, a request builder, a queue
runner, a materializer subprocess, and a new-listing scout. That implicit
schema let a producer (the scout) write one kind's shape into another kind's
directory: the scout wrote condition-id INTENT stubs
``{source, condition_id, enqueued_at, reason}`` into the materializer's
``requests/`` directory, whose consumer expects fully-resolved REQUEST
payloads. The materializer subprocess crashed with ``KeyError`` on every
cycle and, because the bad file was never removed, permanently consumed a
queue slot — 772 such stubs starved all legitimate posterior production for
~8h on 2026-06-10.

That is a *category* failure, not an instance failure: any time one stage's
output passes to the next stage and the schema contract lives only in two
independent dict-assembly sites, a divergence between them is silent until a
downstream ``KeyError``. This module makes the category impossible by giving
each file kind ONE explicit, versioned, validated schema that BOTH the
producer (validate-before-write) and the consumer (validate-on-read) call.
A malformed file is rejected at the boundary with a precise field error
naming exactly which keys are missing or wrong, instead of poisoning a
downstream stage.

Field sources of truth (derived from the ACTUAL consumers, not invented):

* ``MATERIALIZATION_REQUEST`` — the request the materializer subprocess parses.
  Authority: the request dict assembled by
  ``src.data.replacement_forecast_materialization_request_builder``
  ``.build_replacement_forecast_materialization_request`` (its output is the
  exact JSON ``scripts/materialize_replacement_forecast_live.py`` consumes),
	  combined with the queue's pre-spawn poison-pill gate
	  (``temperature_metric``, ``target_date``, ``source_cycle_time`` are
	  accessed immediately by the subprocess).

* ``MATERIALIZATION_SEED`` — the seed the request builder consumes.
  Authority: the seed dict assembled by
  ``src.data.replacement_forecast_materialization_seed_builder``
  ``.build_replacement_forecast_materialization_seed`` and the queue's
  ``_looks_like_seed`` consumer check.

* ``SCOUT_INTENT`` — the condition-id stub the new-listing scout stages.
  Authority: the intent dict written by ``_new_listing_scout_cycle`` in
  ``src.main`` (~L4317).

Each contract exposes:

* a frozen dataclass capturing the validated, typed payload;
* a module-level ``SCHEMA_VERSION`` (the dataclass carries it so a written
  file records the schema it was produced under);
* ``validate_<kind>(payload: Mapping) -> <Type>`` which raises
  ``ContractViolation`` with a precise, field-naming message on any violation.

The validators are deliberately *structural* — they check presence, type, and
	structural request shape, NOT the deep domain invariants (timezone-awareness
of timestamps, precision-guard passability, bin-family completeness) that the
producer builders already enforce and that require DB / artifact context. The
contract is the producer⇄consumer compatibility boundary, not a re-run of the
builder. A payload that passes the producer builder MUST pass the matching
consumer validator (round-trip), and the exact scout-stub shape MUST be
rejected by the REQUEST validator (violation) — those two properties are the
relationship the contract tests pin.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

__all__ = [
    "ContractViolation",
    "SCOUT_INTENT_SCHEMA_VERSION",
    "MATERIALIZATION_SEED_SCHEMA_VERSION",
    "MATERIALIZATION_REQUEST_SCHEMA_VERSION",
    "ScoutIntent",
    "MaterializationSeed",
    "MaterializationRequest",
    "validate_scout_intent",
    "validate_materialization_seed",
    "validate_materialization_request",
]


class ContractViolation(ValueError):
    """A replacement-pipeline file violated its boundary contract.

    Raised by ``validate_<kind>`` with a message that names the file kind, the
    schema version, and the precise offending field(s). The message is meant
    to be written verbatim into a queue failed/ receipt so an operator can see
    *which* keys were missing or mistyped without reading the file.
    """

    def __init__(self, kind: str, schema_version: str, detail: str) -> None:
        self.kind = kind
        self.schema_version = schema_version
        self.detail = detail
        super().__init__(f"{kind} (schema v{schema_version}) contract violation: {detail}")


# ---------------------------------------------------------------------------
# Shared structural validators (presence + type only; deep domain invariants
# stay in the producer builders, which have the DB/artifact context).
# ---------------------------------------------------------------------------
def _require_non_empty_str(
    payload: Mapping[str, object],
    key: str,
    *,
    kind: str,
    schema_version: str,
    missing: list[str],
) -> str:
    value = payload.get(key)
    if value is None or not isinstance(value, str) or not value.strip():
        missing.append(key)
        return ""
    return value.strip()


def _require_number(
    payload: Mapping[str, object],
    key: str,
    *,
    missing: list[str],
    bad_type: list[str],
) -> float | None:
    value = payload.get(key)
    if value is None:
        missing.append(key)
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        bad_type.append(f"{key}(must be number)")
        return None
    return float(value)


def _optional_typed_str(
    payload: Mapping[str, object],
    key: str,
    *,
    bad_type: list[str],
) -> str:
    """Return a present-and-valid string, '' if absent; flag a wrong-typed present value."""
    value = payload.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        bad_type.append(f"{key}(must be string)")
        return ""
    return value.strip()


def _optional_typed_number(
    payload: Mapping[str, object],
    key: str,
    *,
    bad_type: list[str],
) -> float | None:
    """Return a present-and-valid float, None if absent; flag a wrong-typed present value."""
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        bad_type.append(f"{key}(must be number)")
        return None
    return float(value)


# ===========================================================================
# SCOUT_INTENT
# ===========================================================================
SCOUT_INTENT_SCHEMA_VERSION = "1"

# Authority: src.main._new_listing_scout_cycle intent writer (~L4317).
_SCOUT_INTENT_REQUIRED_KEYS: tuple[str, ...] = (
    "source",
    "condition_id",
    "enqueued_at",
    "reason",
)


@dataclass(frozen=True)
class ScoutIntent:
    """A condition-id-only new-listing scout stub (NOT a materialization request)."""

    source: str
    condition_id: str
    enqueued_at: str
    reason: str
    schema_version: str = SCOUT_INTENT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "condition_id": self.condition_id,
            "enqueued_at": self.enqueued_at,
            "reason": self.reason,
        }


def validate_scout_intent(payload: Mapping[str, object]) -> ScoutIntent:
    """Validate a scout-intent payload; raise ContractViolation on any violation."""
    kind = "SCOUT_INTENT"
    if not isinstance(payload, Mapping):
        raise ContractViolation(kind, SCOUT_INTENT_SCHEMA_VERSION, f"payload must be an object, got {type(payload).__name__}")
    missing: list[str] = []
    source = _require_non_empty_str(payload, "source", kind=kind, schema_version=SCOUT_INTENT_SCHEMA_VERSION, missing=missing)
    condition_id = _require_non_empty_str(payload, "condition_id", kind=kind, schema_version=SCOUT_INTENT_SCHEMA_VERSION, missing=missing)
    enqueued_at = _require_non_empty_str(payload, "enqueued_at", kind=kind, schema_version=SCOUT_INTENT_SCHEMA_VERSION, missing=missing)
    reason = _require_non_empty_str(payload, "reason", kind=kind, schema_version=SCOUT_INTENT_SCHEMA_VERSION, missing=missing)
    if missing:
        raise ContractViolation(
            kind,
            SCOUT_INTENT_SCHEMA_VERSION,
            "missing_or_empty_required_keys=" + ",".join(sorted(missing)),
        )
    return ScoutIntent(source=source, condition_id=condition_id, enqueued_at=enqueued_at, reason=reason)


# ===========================================================================
# MATERIALIZATION_SEED
# ===========================================================================
MATERIALIZATION_SEED_SCHEMA_VERSION = "1"

# WHO IS THE AUTHORITY? The CONSUMER, as with REQUEST. A seed file is the
# request builder's INPUT, and the request builder is MORE PERMISSIVE than the
# seed builder's full output: it derives city_timezone from config, defaults
# anchor_weight/anchor_sigma_c/settlement_step_c, and re-derives source_cycle_time
# / expires_at. So the seed CONSUMER contract's HARD-REQUIRED set is exactly the
# queue's own seed discriminator (_looks_like_seed) — the keys without which the
# request builder cannot proceed — NOT the seed builder's full emission. The
# richer seed-builder-only keys are typed-if-present, optional otherwise.
# Net effect: a full seed-builder output passes (superset); a leaner
# request-builder-input seed passes; a scout stub or a seed missing a discriminator
# key fails.
# Authority basis: pipeline-contract project, operator directive 2026-06-10.
_SEED_REQUIRED_TEXT_KEYS: tuple[str, ...] = (
    "city",
    "target_date",
    "temperature_metric",
    "computed_at",
    "baseline_source_run_id",
    "openmeteo_source_run_id",
    "openmeteo_payload_json",
    "precision_metadata_json",
)
# Full seed-builder shape; validated for type IF present, not required.
_SEED_OPTIONAL_TEXT_KEYS: tuple[str, ...] = (
    "city_timezone",
    "source_cycle_time",
    "expires_at",
    "baseline_data_version",
    "baseline_source_available_at",
    "openmeteo_source_available_at",
)
_SEED_OPTIONAL_NUMBER_KEYS: tuple[str, ...] = (
    "anchor_weight",
    "anchor_sigma_c",
    "settlement_step_c",
)


@dataclass(frozen=True)
class MaterializationSeed:
    """A validated replacement materialization seed (consumed by the request builder).

    Carries the hard-required discriminator fields plus the full seed-builder shape
    (empty string / None when a leaner request-builder-input seed omits them — the
    request builder derives or defaults those).
    """

    city: str
    target_date: str
    temperature_metric: str
    computed_at: str
    baseline_source_run_id: str
    openmeteo_source_run_id: str
    openmeteo_payload_json: str
    precision_metadata_json: str
    bins: tuple[Mapping[str, object], ...]
    city_timezone: str = ""
    source_cycle_time: str = ""
    expires_at: str = ""
    baseline_data_version: str = ""
    baseline_source_available_at: str = ""
    openmeteo_source_available_at: str = ""
    anchor_weight: float | None = None
    anchor_sigma_c: float | None = None
    settlement_step_c: float | None = None
    schema_version: str = MATERIALIZATION_SEED_SCHEMA_VERSION


def _validate_bins(
    payload: Mapping[str, object],
    *,
    kind: str,
    schema_version: str,
) -> tuple[Mapping[str, object], ...]:
    rows = payload.get("bins")
    if not isinstance(rows, (list, tuple)) or not rows:
        raise ContractViolation(kind, schema_version, "bins must be a non-empty array")
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ContractViolation(kind, schema_version, f"bins[{index}] must be an object")
        if not str(row.get("bin_id") or "").strip():
            raise ContractViolation(kind, schema_version, f"bins[{index}] missing bin_id")
    return tuple(dict(row) for row in rows)


def validate_materialization_seed(payload: Mapping[str, object]) -> MaterializationSeed:
    """Validate a materialization-seed payload; raise ContractViolation on any violation."""
    kind = "MATERIALIZATION_SEED"
    sv = MATERIALIZATION_SEED_SCHEMA_VERSION
    if not isinstance(payload, Mapping):
        raise ContractViolation(kind, sv, f"payload must be an object, got {type(payload).__name__}")
    missing: list[str] = []
    bad_type: list[str] = []
    text_values = {
        key: _require_non_empty_str(payload, key, kind=kind, schema_version=sv, missing=missing)
        for key in _SEED_REQUIRED_TEXT_KEYS
    }
    metric = text_values.get("temperature_metric", "")
    if metric and metric not in {"high", "low"}:
        bad_type.append("temperature_metric(must be high|low)")
    optional_text = {
        key: _optional_typed_str(payload, key, bad_type=bad_type)
        for key in _SEED_OPTIONAL_TEXT_KEYS
    }
    number_values = {
        key: _optional_typed_number(payload, key, bad_type=bad_type)
        for key in _SEED_OPTIONAL_NUMBER_KEYS
    }
    if missing or bad_type:
        detail_parts = []
        if missing:
            detail_parts.append("missing_or_empty_required_keys=" + ",".join(sorted(missing)))
        if bad_type:
            detail_parts.append("bad_type=" + ",".join(sorted(bad_type)))
        raise ContractViolation(kind, sv, "; ".join(detail_parts))
    bins = _validate_bins(payload, kind=kind, schema_version=sv)
    return MaterializationSeed(
        city=text_values["city"],
        target_date=text_values["target_date"],
        temperature_metric=metric,
        computed_at=text_values["computed_at"],
        baseline_source_run_id=text_values["baseline_source_run_id"],
        openmeteo_source_run_id=text_values["openmeteo_source_run_id"],
        openmeteo_payload_json=text_values["openmeteo_payload_json"],
        precision_metadata_json=text_values["precision_metadata_json"],
        bins=bins,
        city_timezone=optional_text["city_timezone"],
        source_cycle_time=optional_text["source_cycle_time"],
        expires_at=optional_text["expires_at"],
        baseline_data_version=optional_text["baseline_data_version"],
        baseline_source_available_at=optional_text["baseline_source_available_at"],
        openmeteo_source_available_at=optional_text["openmeteo_source_available_at"],
        anchor_weight=number_values["anchor_weight"],
        anchor_sigma_c=number_values["anchor_sigma_c"],
        settlement_step_c=number_values["settlement_step_c"],
    )


# ===========================================================================
# MATERIALIZATION_REQUEST
# ===========================================================================
MATERIALIZATION_REQUEST_SCHEMA_VERSION = "1"

# WHO IS THE AUTHORITY, AND WHAT IS THIS CONTRACT FOR?
#
# This validator plays TWO boundary roles, and they have DIFFERENT required
# sets. Conflating them is the trap (it over-rejects runnable requests):
#
#   * CONSUMER gate (the queue, pre-spawn): the materializer subprocess
#     (scripts/materialize_replacement_forecast_live.py:163-165, then
#     173/178/197) accesses ONLY these keys as a hard, immediate top-level
    #     read before any work: temperature_metric, target_date, source_cycle_time. A file
#     carrying those is RUNNABLE; a file missing any of them KeyError-crashes
#     the subprocess. So the consumer contract's HARD-REQUIRED set is exactly
#     that minimal-runnable set — no more. The scout stub
#     {source, condition_id, enqueued_at, reason} is missing ALL of them and is
#     rejected with a message naming them.
#
#   * PRODUCER guarantee (request builder output): the request builder always
#     emits the full set (city, city_id, baseline_*, openmeteo_*, anchor_*,
#     bins, ...). Those richer keys are validated for TYPE here when present
#     (a producer that emits a wrong-typed anchor_weight is a bug we want
#     caught), but they are OPTIONAL to the consumer gate so a deliberately
#     partial-but-runnable request still passes.
#
# Net effect (all three populations behave correctly): scout stub -> REJECTED;
# full builder output -> ACCEPTED (superset of required); minimal runnable
# request -> ACCEPTED.
# Authority basis: pipeline-contract project, operator directive 2026-06-10.
_REQUEST_REQUIRED_TEXT_KEYS: tuple[str, ...] = (
    "temperature_metric",
    "target_date",
    "source_cycle_time",
)
# Full-shape keys the producer emits; validated for type IF present, not required.
_REQUEST_OPTIONAL_TEXT_KEYS: tuple[str, ...] = (
    "city",
    "city_id",
    "city_timezone",
    "computed_at",
    "expires_at",
    "baseline_source_run_id",
    "baseline_data_version",
    "baseline_source_available_at",
    "openmeteo_source_run_id",
    "openmeteo_source_available_at",
    "openmeteo_payload_json",
    "precision_metadata_json",
)
_REQUEST_OPTIONAL_NUMBER_KEYS: tuple[str, ...] = (
    "anchor_weight",
    "anchor_sigma_c",
    "settlement_step_c",
)


@dataclass(frozen=True)
class MaterializationRequest:
    """A validated, RUNNABLE replacement materialization request.

    Carries the hard-required minimal-runnable fields plus the full-shape fields
    the producer emits (empty string / None when a partial-but-runnable request
    omits them — the materializer reads those conditionally or via the readiness
    builder, never as an immediate top-level KeyError).
    """

    temperature_metric: str
    target_date: str
    source_cycle_time: str
    city: str = ""
    city_id: str = ""
    city_timezone: str = ""
    computed_at: str = ""
    expires_at: str = ""
    baseline_source_run_id: str = ""
    baseline_data_version: str = ""
    baseline_source_available_at: str = ""
    openmeteo_source_run_id: str = ""
    openmeteo_source_available_at: str = ""
    openmeteo_payload_json: str = ""
    precision_metadata_json: str = ""
    anchor_weight: float | None = None
    anchor_sigma_c: float | None = None
    settlement_step_c: float | None = None
    bins: tuple[Mapping[str, object], ...] = ()
    schema_version: str = MATERIALIZATION_REQUEST_SCHEMA_VERSION


def validate_materialization_request(payload: Mapping[str, object]) -> MaterializationRequest:
    """Validate a materialization-request payload; raise ContractViolation on any violation.

    This is the consumer-side gate the queue runs BEFORE spawning the
    materializer subprocess. The exact new-listing-scout stub
    ``{source, condition_id, enqueued_at, reason}`` is rejected here with a
    message naming every missing hard-required key, so it leaves the queue at
    most once instead of crashing the subprocess on every cycle. See the
    two-roles note above for why the hard-required set is the minimal-runnable
    set, not the full producer shape.
    """
    kind = "MATERIALIZATION_REQUEST"
    sv = MATERIALIZATION_REQUEST_SCHEMA_VERSION
    if not isinstance(payload, Mapping):
        raise ContractViolation(kind, sv, f"payload must be an object, got {type(payload).__name__}")
    missing: list[str] = []
    bad_type: list[str] = []
    text_values = {
        key: _require_non_empty_str(payload, key, kind=kind, schema_version=sv, missing=missing)
        for key in _REQUEST_REQUIRED_TEXT_KEYS
    }
    metric = text_values.get("temperature_metric", "")
    if metric and metric not in {"high", "low"}:
        bad_type.append("temperature_metric(must be high|low)")
    optional_text = {
        key: _optional_typed_str(payload, key, bad_type=bad_type)
        for key in _REQUEST_OPTIONAL_TEXT_KEYS
    }
    number_values = {
        key: _optional_typed_number(payload, key, bad_type=bad_type)
        for key in _REQUEST_OPTIONAL_NUMBER_KEYS
    }
    if missing or bad_type:
        detail_parts = []
        if missing:
            detail_parts.append("missing_or_empty_required_keys=" + ",".join(sorted(missing)))
        if bad_type:
            detail_parts.append("bad_type=" + ",".join(sorted(bad_type)))
        raise ContractViolation(kind, sv, "; ".join(detail_parts))
    # bins is part of the full producer shape; validate it only when present so a
    # partial-but-runnable request (no bins) still passes the consumer gate.
    bins: tuple[Mapping[str, object], ...] = ()
    if payload.get("bins") is not None:
        bins = _validate_bins(payload, kind=kind, schema_version=sv)
    return MaterializationRequest(
        temperature_metric=metric,
        target_date=text_values["target_date"],
        source_cycle_time=text_values["source_cycle_time"],
        city=optional_text["city"],
        city_id=optional_text["city_id"],
        city_timezone=optional_text["city_timezone"],
        computed_at=optional_text["computed_at"],
        expires_at=optional_text["expires_at"],
        baseline_source_run_id=optional_text["baseline_source_run_id"],
        baseline_data_version=optional_text["baseline_data_version"],
        baseline_source_available_at=optional_text["baseline_source_available_at"],
        openmeteo_source_run_id=optional_text["openmeteo_source_run_id"],
        openmeteo_source_available_at=optional_text["openmeteo_source_available_at"],
        openmeteo_payload_json=optional_text["openmeteo_payload_json"],
        precision_metadata_json=optional_text["precision_metadata_json"],
        anchor_weight=number_values["anchor_weight"],
        anchor_sigma_c=number_values["anchor_sigma_c"],
        settlement_step_c=number_values["settlement_step_c"],
        bins=bins,
    )

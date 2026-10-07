# Created: 2026-09-27
# Last reused or audited: 2026-09-27
# Authority basis: correction design review REQ-20260925-223704 §2 (persist every
#   cut before its winner/no-winner branch; raw q before any rejection), §3
#   (complete ordered raw YES simplex + synchronized market snapshot; missing or
#   invalid quotes stored with a reason, never patched), §10 (idempotent on
#   immutable identities; the corpus never touches receipt atomicity).
"""Build, queue and persist the complete auction learning corpus.

``build_cut_corpus`` turns one evaluated cut's frozen inputs into ready rows.
It performs no I/O. ``build_unreceipted_cut`` does the same for a cut that
ended before its receipt. Either result is queued in-process per trade DB.
``flush_pending_cuts`` then writes the queue in its own short trade-DB
transaction, after the auction receipt has committed. The corpus therefore
never shares, extends or endangers the receipt transaction. A failed flush,
for example on SQLITE_FULL, loses nothing: the rows stay queued and the next
receipt retries them.

Raw q comes from the family witness with the same projection as the solver's
``family_payoff_point_q``. It does not come from a sealed correction, which
exists only for scored legs, so a candidate rejected before scoring still
carries the raw q it was rejected against. A deterministic Day0 witness gives
q only for its proved bins; an unproved bin stays NULL.

A family state is content-addressed over its topology, witness content
identities, per-bin book top and leg outcomes. A family unchanged across
consecutive cuts is stored once, and each cut links to it through
``tier0_cut_family``. The YES midpoint is reported only when YES bid and ask
both exist, are positive and are uncrossed. Otherwise a reason is stored and
no number is patched in.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import sqlite3
import sys
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable, Mapping, Sequence

import zstandard

from src.solve.solver import (
    DeterministicBinPayoffWitness,
    JointOutcomeProbabilityWitness,
)
from src.state.schema.tier0_auction_corpus_schema import (
    CUT_ENCODING,
    SNAPSHOT_ENCODING,
    SNAPSHOT_POINT_TRACE_ENCODING,
    TOPOLOGY_ENCODING,
)

_ZSTD_LEVEL = 3
# ~100-200 KB per built cut: the queue is bounded to ~100 MB however long
# flushes fail (e.g. a full disk).
_QUEUE_LIMIT = 512
_FLUSH_LIMIT = 16
_POINT_TRACE_CANONICAL_LIMIT = 16 * 1024
_POINT_TRACE_COMPRESSED_LIMIT = 8 * 1024
_POINT_TRACE_QUEUE_LIMIT = 8 * 1024 * 1024
_POINT_TRACE_TRANSACTION_LIMIT = 256 * 1024
_LOG = logging.getLogger(__name__)


class Tier0CorpusIdentityConflict(RuntimeError):
    """An immutable cut identity already holds a different payload."""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def encode_payload(raw: bytes) -> bytes:
    """zstd-3 BLOB for one canonical-JSON corpus payload."""

    return zstandard.ZstdCompressor(level=_ZSTD_LEVEL).compress(raw)


def _project_held_point_roles(roles: object, *, unit: str, has_y: bool) -> dict:
    """Finite math inputs only; this diagnostic grants no native source authority."""
    from src.events.day0_authority import DAY0_PROBABILITY_SEMANTICS_REVISION

    if (not isinstance(roles, Mapping)
            or roles.get("schema") != "day0_measurement_domain_shapes_v1"
            or roles.get("unit") != unit or unit not in ("C", "F")
            or roles.get("semantics_revision") != DAY0_PROBABILITY_SEMANTICS_REVISION):
        raise ValueError("HELD_POINT_ROLE_IDENTITY_INVALID")
    out = {key: roles[key] for key in ("schema", "unit", "semantics_revision")}
    for name, role in (("X", "remaining_X"), ("Y", "full_Y")):
        if name == "Y" and not has_y:
            continue
        shape = roles.get(name)
        if not isinstance(shape, Mapping) or shape.get("role") != role or shape.get("unit") != unit:
            raise ValueError("HELD_POINT_ROLE_MISSING")
        keys = {"role", "unit", "provider_families", "provider_centers_native",
                "member_points_native", "member_interval_bounds_native"}
        if name == "Y":
            keys |= {"prefix_information_kind", "conditioning_likelihood_scope"}
        projected = {key: shape[key] for key in keys if key in shape}
        if set(projected) != keys:
            raise ValueError("HELD_POINT_ROLE_FIELDS_INVALID")
        families, centers = projected["provider_families"], projected["provider_centers_native"]
        points, bounds = projected["member_points_native"], projected["member_interval_bounds_native"]
        if (not isinstance(families, (list, tuple)) or not 1 <= len(families) <= 256
                or any(not isinstance(f, str) or not 0 < len(f) <= 256 for f in families)
                or len(set(families)) != len(families)
                or not isinstance(centers, (list, tuple)) or len(centers) != len(families)
                or not isinstance(points, (list, tuple)) or len(points) != 51
                or not isinstance(bounds, (list, tuple)) or len(bounds) != 51
                or any(not isinstance(pair, (list, tuple)) or len(pair) != 2 for pair in bounds)):
            raise ValueError("HELD_POINT_ROLE_SHAPE_INVALID")
        scalars = [*centers, *points, *(value for pair in bounds for value in pair)]
        if (any(type(v) not in (int, float) or not math.isfinite(v) for v in scalars)
                or any(not lo <= point <= hi for point, (lo, hi) in zip(points, bounds))):
            raise ValueError("HELD_POINT_ROLE_SCALAR_INVALID")
        if name == "Y" and (projected["prefix_information_kind"] not in {
                "REPORTED_PRODUCT_PROXY", "INCOMPLETE_SAME_QUANTITY_BOUND",
                "COMPLETE_SAME_QUANTITY_PREFIX", "NO_PREFIX_CONDITIONING", "UNKNOWN"}
                or projected["conditioning_likelihood_scope"] not in {
                    "COARSENED_BOUND_ONLY", "UNCONDITIONED_FULL_Y_PRIOR",
                    "Y_PREFIX_LIKELIHOOD_UNIDENTIFIED"}):
            raise ValueError("HELD_POINT_ROLE_PREFIX_INVALID")
        out[name] = projected
    # A JSON round-trip freezes arrays without retaining mutable caller metadata.
    return json.loads(_canonical(out))


def decode_payload(blob: bytes) -> object:
    """Inverse of ``encode_payload`` for every ``*_ENCODING`` in the schema."""

    return json.loads(zstandard.ZstdDecompressor().decompress(blob))


def _point_trace_unavailable(reason: str, trace: Mapping[str, object] | None = None) -> bytes:
    # Preserve only the consumed witness's diagnostic binding, never a partial
    # kernel that could be mistaken for replayable point authority.
    fields = {"lane", "decision_at_utc", "family", "final_yes_q", "bindings",
              "producer_witness_identity", "probability_content_identity", "q_version",
              "source_truth_identity", "posterior_identity_hash", "consumer_witness_identity",
              "consumer_bindings", "consumer_yes_q", "consumer_captured_at_utc",
              "selected_lane", "role", "producer_identity_recipe"}
    value = {key: value for key, value in (trace or {}).items() if key in fields}
    value.update(schema="held_sell_point_kernel_trace_v1",status="UNAVAILABLE",reason=reason)
    try:
        raw = _canonical(value)
        if (len(raw) <= _POINT_TRACE_CANONICAL_LIMIT
                and len(encode_payload(raw)) <= _POINT_TRACE_COMPRESSED_LIMIT):
            return raw
    except Exception:  # noqa: BLE001 -- unavailable evidence is also optional
        pass
    # Literal fallback also survives a broken optional JSON encoder.
    return (b'{"reason":"POINT_TRACE_UNAVAILABLE_ENCODING_FAILED",'
            b'"schema":"held_sell_point_kernel_trace_v1","status":"UNAVAILABLE"}')


def _point_trace_warning(message: str, *args: object) -> None:
    try:
        _LOG.warning(message, *args)
    except Exception:  # noqa: BLE001 -- a logging handler cannot cost the base cut
        pass


def freeze_held_sell_point_trace(trace: Mapping[str, object]) -> bytes:
    """Freeze optional point diagnostics, never an action/probability authority.

    Only these low-dimensional fields may enter the corpus. The queue retains
    the canonical bytes alone, not the caller's dictionaries or arrays. A bad
    diagnostic is locally unavailable; it cannot fail the probability cut.
    This ordinary diagnostic follows existing label/30-day retention. Queue
    overflow or restart can lose it; it is not protected TotalLoss evidence.
    """
    fields = {
        "schema", "status", "lane", "decision_at_utc", "probability_clock_utc",
        "loaded_revision", "loaded_revision_status", "family", "kernel", "diurnal",
        "diurnal_status", "final_yes_q", "producer_witness_identity", "q_version",
        "probability_content_identity", "source_truth_identity", "posterior_identity_hash",
        "bindings", "input_identities", "carrier_content_identity",
        "consumer_witness_identity", "consumer_bindings", "consumer_yes_q",
        "consumer_captured_at_utc", "selected_lane",
        "role", "reason", "producer_identity_recipe",
    }
    try:
        if set(trace).difference(fields) or trace.get("schema") != "held_sell_point_kernel_trace_v1":
            return _point_trace_unavailable("TRACE_FIELDS_INVALID")
        recipe = trace.get("producer_identity_recipe")
        recipe_fields = {
            "kind", "resolution_identity", "topology_identity", "authority_certificate_hash",
            "band_alpha", "band_basis", "captured_at_utc", "sample_shape", "sample_matrix_identity",
        }
        if recipe is not None and (
            not isinstance(recipe, Mapping) or set(recipe) != recipe_fields
            or any(not isinstance(recipe[key], str) for key in recipe_fields-{"band_alpha", "sample_shape"})
            or type(recipe["band_alpha"]) not in {int, float} or not math.isfinite(recipe["band_alpha"])
            or not isinstance(recipe["sample_shape"], (list, tuple)) or len(recipe["sample_shape"]) != 2
            or any(type(value) is not int or value < 0 for value in recipe["sample_shape"])
        ):
            return _point_trace_unavailable("PRODUCER_IDENTITY_RECIPE_FIELDS_INVALID",
                {key: value for key, value in trace.items() if key != "producer_identity_recipe"})
        if trace.get("status") == "UNAVAILABLE":
            return _point_trace_unavailable(str(trace.get("reason") or "POINT_TRACE_UNAVAILABLE"),trace)
        kernel = trace.get("kernel")
        if not isinstance(kernel, Mapping):
            return _point_trace_unavailable("POINT_KERNEL_MISSING",trace)
        if set(kernel).difference({
            "future_extremes_c", "final_extreme_centers_c", "boundary_scenarios", "metric",
            "path_error_sigma_c", "instrument_sigma_c", "bin_bounds_c", "remaining_center_bias_native",
            "operator", "settlement", "resolver_terminal", "carrier_to_witness", "n_point",
            "n_samples", "base_yes_q", "support_mask",
            "domain_role_shapes", "domain_role_shapes_sha256",
        }):
            return _point_trace_unavailable("POINT_KERNEL_FIELDS_INVALID",trace)
        from src.data.day0_hourly_vectors import (
            DAY0_REMAINING_CARRIER_OPERATOR_V1, DAY0_REMAINING_CARRIER_OPERATOR_V2,
            DAY0_REMAINING_CARRIER_OPERATOR_V3, DAY0_REMAINING_CARRIER_OPERATOR_RESOLVER,
        )
        operator = kernel.get("operator")
        if operator == DAY0_REMAINING_CARRIER_OPERATOR_V1:
            return _point_trace_unavailable("UNSUPPORTED_POINT_KERNEL_V1",trace)
        if operator not in {DAY0_REMAINING_CARRIER_OPERATOR_V2,
                            DAY0_REMAINING_CARRIER_OPERATOR_V3,
                            DAY0_REMAINING_CARRIER_OPERATOR_RESOLVER}:
            return _point_trace_unavailable("UNSUPPORTED_POINT_KERNEL",trace)
        roles = kernel.get("domain_role_shapes")
        from src.events.day0_authority import DAY0_PROBABILITY_SEMANTICS_REVISION
        if roles is not None:
            projection = _project_held_point_roles(roles,
                unit=kernel["settlement"]["measurement_unit"],
                has_y=bool(kernel["final_extreme_centers_c"]))
            if (projection != roles or kernel.get("domain_role_shapes_sha256") !=
                    hashlib.sha256(_canonical(projection)).hexdigest()):
                return _point_trace_unavailable("POINT_ROLE_PROJECTION_MISMATCH",trace)
        elif DAY0_PROBABILITY_SEMANTICS_REVISION in str(trace.get("q_version") or ""):
            return _point_trace_unavailable("POINT_ROLE_PROJECTION_MISSING",trace)
        raw = _canonical(dict(trace))
        if len(raw) > _POINT_TRACE_CANONICAL_LIMIT:
            return _point_trace_unavailable("TRACE_CANONICAL_SIZE_LIMIT",trace)
        # The temporary compressed form is discarded; only one representation
        # is retained by the pending queue.
        if len(encode_payload(raw)) > _POINT_TRACE_COMPRESSED_LIMIT:
            return _point_trace_unavailable("TRACE_COMPRESSED_SIZE_LIMIT",trace)
        return raw
    except Exception:  # noqa: BLE001 -- diagnostic failure cannot alter q
        return _point_trace_unavailable("TRACE_FREEZE_FAILED")


def replay_held_sell_point_trace(raw: bytes) -> tuple[float, ...]:
    """Replay the frozen scalar point kernel, not native paths or sample draws.

    V2/V3 point integration is independent of the confidence RNG. One throwaway
    confidence row is required by the existing builder API but is never used.
    V1 and any uncaptured composition remain explicitly unsupported.
    """
    import numpy as np
    from src.calibration.day0_diurnal_residual import Day0DiurnalMixture
    from src.calibration.day0_resolver_terminal_residual import Day0ResolverTerminalInput
    from src.contracts.settlement_semantics import SettlementSemantics
    from src.data.day0_hourly_vectors import build_day0_remaining_probability_carrier

    if not isinstance(raw, bytes) or len(raw) > _POINT_TRACE_CANONICAL_LIMIT:
        raise ValueError("HELD_POINT_TRACE_SIZE_INVALID")
    trace = json.loads(raw)
    if trace.get("status") != "READY":
        raise ValueError("HELD_POINT_TRACE_UNAVAILABLE:"+str(trace.get("reason") or "unknown"))
    kernel = trace["kernel"]
    parameters = {
        key: kernel[key] for key in (
            "future_extremes_c", "final_extreme_centers_c", "boundary_scenarios",
            "metric", "path_error_sigma_c", "instrument_sigma_c", "bin_bounds_c",
            "operator", "remaining_center_bias_native",
        )
    }
    if str(parameters["operator"]).endswith("noisy_future_v1"):
        raise ValueError("HELD_POINT_TRACE_UNSUPPORTED_V1")
    semantics = SettlementSemantics.from_frozen_payload(kernel["settlement"])
    identity_inputs = {"unit": semantics.measurement_unit}
    if kernel.get("domain_role_shapes") is not None:
        roles = _project_held_point_roles(kernel["domain_role_shapes"],
            unit=semantics.measurement_unit, has_y=bool(kernel["final_extreme_centers_c"]))
        if (roles != kernel["domain_role_shapes"] or kernel.get("domain_role_shapes_sha256") !=
                hashlib.sha256(_canonical(roles)).hexdigest()):
            raise ValueError("HELD_POINT_TRACE_ROLE_PROJECTION_MISMATCH")
        identity_inputs["domain_role_shapes"] = roles
    else:
        from src.events.day0_authority import DAY0_PROBABILITY_SEMANTICS_REVISION
        if DAY0_PROBABILITY_SEMANTICS_REVISION in str(trace.get("q_version") or ""):
            raise ValueError("HELD_POINT_TRACE_ROLE_PROJECTION_MISSING")
    parameters.update(
        n_point=1, n_samples=1, identity_inputs=identity_inputs,
        settlement_semantics=semantics,
        resolver_terminal=(Day0ResolverTerminalInput.from_payload(kernel["resolver_terminal"])
                           if kernel.get("resolver_terminal") is not None else None),
    )
    carrier = build_day0_remaining_probability_carrier(**parameters)
    projection = tuple(kernel.get("carrier_to_witness") or range(len(carrier["q"])))
    if sorted(projection) != list(range(len(carrier["q"]))):
        raise ValueError("HELD_POINT_TRACE_PROJECTION_INVALID")
    base = [float(carrier["q"][i]) for i in projection]
    mask = kernel.get("support_mask")
    if mask is not None:
        if len(mask) != len(base) or any(type(value) is not bool for value in mask):
            raise ValueError("HELD_POINT_TRACE_MASK_INVALID")
        masked = np.asarray(base, dtype=float)*np.asarray(mask, dtype=bool)
        total = float(masked.sum())
        if total <= 0.: raise ValueError("HELD_POINT_TRACE_MASK_EMPTY")
        base = (masked/total).tolist()
    if not np.array_equal(base, kernel["base_yes_q"]):
        raise ValueError("HELD_POINT_TRACE_UNCAPTURED_COMPOSITION")
    mixture = trace.get("diurnal")
    final = Day0DiurnalMixture.from_payload(mixture).apply(base) if mixture is not None else base
    if not np.array_equal(final, trace["final_yes_q"]):
        raise ValueError("HELD_POINT_TRACE_FINAL_Q_MISMATCH")
    return tuple(float(value) for value in final)


def _id128(*parts: bytes) -> bytes:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part)
        digest.update(b"\x1f")
    return digest.digest()[:16]


def witness_yes_q_by_bin(witness: object) -> dict[str, float]:
    """Raw YES point probability per bin, exactly as ``family_payoff_point_q``.

    A joint witness gives every column. A deterministic Day0 witness gives only
    its proved bins; an unproved bin is absent, never zero.
    """

    if isinstance(witness, JointOutcomeProbabilityWitness):
        return dict(zip(witness.bin_ids, witness.yes_point_q.tolist()))
    if isinstance(witness, DeterministicBinPayoffWitness):
        return {bin_id: float(value) for bin_id, value in witness.exact_yes_payoffs}
    return {}


def held_payoff_q(yes_q: float, side: str) -> float:
    """``family_payoff_point_q``'s native-side projection of a YES column."""

    return yes_q if side == "YES" else 1.0 - yes_q


def candidate_raw_q(
    witnesses: Mapping[str, object],
    evaluation: object,
    yes_by_family: dict[str, dict[str, float]] | None = None,
) -> float | None:
    """Held-side raw q for one evaluated leg, from the witness it was scored on."""

    family_key = evaluation.family_key
    witness = witnesses.get(family_key)
    if (
        witness is None
        or witness.witness_identity != evaluation.probability_witness_identity
    ):
        return None
    cache = {} if yes_by_family is None else yes_by_family
    yes_by_bin = cache.get(family_key)
    if yes_by_bin is None:
        yes_by_bin = cache[family_key] = witness_yes_q_by_bin(witness)
    yes = yes_by_bin.get(evaluation.bin_id)
    if yes is None:
        return None
    return held_payoff_q(yes, evaluation.side)


def _quote(
    asset: object | None,
    sell_asset: object | None,
    epoch_captured_at: datetime | None,
) -> tuple[str | None, str | None, str | None]:
    """(best bid, best ask, captured_at when it differs from the epoch clock).

    Prices keep the venue's own decimal text (``str`` of the book Decimal).
    ``min``/``max`` rather than ``levels[0]``: the best level must not depend
    on each curve type's sort convention.
    """

    ask = bid = captured = None
    if asset is not None:
        if asset.curve.levels:
            ask = str(min(level.price for level in asset.curve.levels))
        if asset.bid_levels:
            bid = str(max(level.price for level in asset.bid_levels))
        captured = asset.captured_at_utc
    if bid is None and sell_asset is not None:
        if sell_asset.curve.levels:
            bid = str(max(level.price for level in sell_asset.curve.levels))
        captured = captured or sell_asset.captured_at_utc
    if captured is None or captured == epoch_captured_at:
        return bid, ask, None
    return bid, ask, captured.isoformat()


def _yes_mid(
    status: str | None,
    bid: str | None,
    ask: str | None,
) -> tuple[str | None, str | None]:
    """(mid, reason). Exactly one of the two is None."""

    if status is None:
        return None, "BOOK_SIDE_NOT_CAPTURED"
    if status not in {"EXECUTABLE", "NO_ASK"}:
        return None, status
    if bid is None:
        return None, "YES_BID_MISSING"
    if ask is None:
        return None, "YES_ASK_MISSING"
    b, a = Decimal(bid), Decimal(ask)
    if b <= 0 or a <= 0:
        return None, "YES_QUOTE_NON_POSITIVE"
    if b > a:
        return None, "YES_QUOTE_CROSSED"
    return format(((b + a) / 2).normalize(), "f"), None


@dataclass(frozen=True)
class FamilyRows:
    """One family's rows in one cut. Topology and state are content-addressed."""

    topology_id: bytes
    topology: tuple[object, ...]
    family_state_id: bytes
    snapshot: tuple[object, ...]
    witness_identity: bytes


@dataclass(frozen=True)
class CutCorpus:
    cut_row: tuple[object, ...]
    families: tuple[FamilyRows, ...]
    q_raw_by_candidate: Mapping[str, float]


def _topology(
    witness: object,
    context: Mapping[str, str],
    unit: str | None,
    first_seen: str,
) -> tuple[bytes, tuple[object, ...]]:
    bindings = [
        [b.bin_id, b.condition_id, b.yes_token_id, b.no_token_id]
        for b in witness.bindings
    ]
    raw = _canonical(
        {
            "family_key": str(witness.family_key),
            "family_binding_identity": str(witness.family_binding_identity),
            "topology_identity": str(witness.topology_identity),
            "resolution_identity": str(witness.resolution_identity),
            "column_order": "witness_binding_order",
            "bindings": bindings,
            "native_unit": unit,
        }
    )
    topology_id = _id128(b"tier0_topology_v1", raw)
    return topology_id, (
        topology_id,
        str(witness.family_key),
        str(context.get("city") or ""),
        str(context.get("target_date") or ""),
        str(context.get("metric") or ""),
        unit,
        len(bindings),
        TOPOLOGY_ENCODING,
        encode_payload(raw),
        first_seen,
    )


def cut_status(*, winner_candidate_id: str | None, candidate_count: int) -> str:
    if winner_candidate_id:
        return "SELECTED"
    return "NO_TRADE" if candidate_count else "NO_CANDIDATES"


def _cut_row(
    *,
    cut_id: str,
    selection_epoch_identity: str | None,
    status: str,
    reason: str | None,
    decision_iso: str,
    selection_policy_identity: str,
    full_scope_family_count: int,
    eligible_family_count: int,
    candidate_count: int,
    winner_candidate_id: str | None,
    payload: Mapping[str, object],
    cancel_source: str | None = None,
    cancel_stage: str | None = None,
) -> tuple[object, ...]:
    raw = _canonical(dict(payload))
    return (
        cut_id,
        selection_epoch_identity,
        status,
        reason,
        decision_iso,
        selection_policy_identity,
        full_scope_family_count,
        eligible_family_count,
        candidate_count,
        winner_candidate_id,
        None,
        CUT_ENCODING,
        hashlib.sha256(raw).hexdigest(),
        encode_payload(raw),
        datetime.now(timezone.utc).isoformat(),
        cancel_source,
        cancel_stage,
        None,
        None,
    )


def selected_cut_id(selection_epoch_identity: str, decision_at_utc: datetime) -> str:
    """The immutable id of the cut an evaluated selection writes."""

    decision_iso = decision_at_utc.astimezone(timezone.utc).isoformat()
    return hashlib.sha256(
        _canonical(["tier0_cut_v1", selection_epoch_identity, decision_iso])
    ).hexdigest()


def build_cut_corpus(
    *,
    selection_epoch_identity: str,
    reason: str | None,
    decision_at_utc: datetime,
    selection_policy_identity: str,
    full_scope_family_count: int,
    probability_witnesses: Mapping[str, object],
    ineligible_by_family: Mapping[str, str],
    excluded_by_family: Mapping[str, str],
    evaluations: Sequence[object],
    winner_candidate_id: str | None,
    book_epoch: object | None,
    family_context_by_key: Mapping[str, Mapping[str, str]],
    native_unit_by_city: Mapping[str, str],
    policy: Mapping[str, object],
) -> CutCorpus:
    """Freeze one evaluated cut's corpus rows. Pure: no I/O."""

    states = {
        (str(row[0]), str(row[1]), str(row[3])): str(row[5])
        for row in tuple(getattr(book_epoch, "asset_states", ()) or ())
    }
    assets = getattr(book_epoch, "asset_by_key", None) or {}
    sell_assets = getattr(book_epoch, "sell_asset_by_key", None) or {}
    book_at = getattr(book_epoch, "captured_at_utc", None)
    book_max_age = getattr(book_epoch, "max_age", None)
    legs_by_family: dict[str, list[tuple[object, ...]]] = {}
    q_raw_by_candidate: dict[str, float] = {}
    yes_by_family: dict[str, dict[str, float]] = {}
    for evaluation in evaluations:
        q = candidate_raw_q(probability_witnesses, evaluation, yes_by_family)
        if q is not None:
            q_raw_by_candidate[str(evaluation.candidate_id)] = q
        legs_by_family.setdefault(str(evaluation.family_key), []).append(
            (
                str(evaluation.bin_id),
                str(evaluation.side),
                str(evaluation.action),
                str(evaluation.execution_mode),
                str(evaluation.status),
                evaluation.rejection_reason,
                evaluation.q_served,
            )
        )
    decision_iso = decision_at_utc.astimezone(timezone.utc).isoformat()
    families: list[FamilyRows] = []
    for family_key in sorted(probability_witnesses):
        witness = probability_witnesses[family_key]
        context = family_context_by_key.get(family_key) or {}
        topology_id, topology = _topology(
            witness,
            context,
            native_unit_by_city.get(str(context.get("city") or "")),
            decision_iso,
        )
        bindings = tuple(witness.bindings)
        column = {binding.bin_id: index for index, binding in enumerate(bindings)}
        yes_by_bin = yes_by_family.get(family_key)
        if yes_by_bin is None:
            yes_by_bin = yes_by_family[family_key] = witness_yes_q_by_bin(witness)
        yes_q = [yes_by_bin.get(b.bin_id) for b in bindings]
        simplex_complete = bool(bindings) and all(
            q is not None for q in yes_q
        ) and math.isclose(sum(yes_q), 1.0, abs_tol=1e-9)
        book: list[list[object]] = []
        mids_complete = bool(bindings)
        for binding in bindings:
            row: list[object] = []
            for side, token in (("YES", binding.yes_token_id), ("NO", binding.no_token_id)):
                key = (family_key, binding.bin_id, side, str(token or ""))
                row.append(states.get((family_key, binding.bin_id, side)))
                row.extend(_quote(assets.get(key), sell_assets.get(key), book_at))
            mid, mid_reason = _yes_mid(row[0], row[1], row[2])
            mids_complete = mids_complete and mid_reason is None
            row.extend([mid, mid_reason])
            book.append(row)
        legs = sorted(
            (
                [column.get(leg[0], leg[0]), *leg[1:]]
                for leg in legs_by_family.get(family_key, ())
            ),
            key=lambda leg: (str(leg[0]), *leg[1:4]),
        )
        raw = _canonical(
            {
                "q_version": str(getattr(witness, "q_version", "") or ""),
                "posterior_identity_hash": str(
                    getattr(witness, "posterior_identity_hash", "") or ""
                ),
                "source_truth_identity": str(
                    getattr(witness, "source_truth_identity", "") or ""
                ),
                "raw_yes_q": yes_q,
                "book": book,
                "legs": legs,
                "family_excluded_reason": excluded_by_family.get(family_key),
            }
        )
        family_state_id = _id128(b"tier0_family_state_v1", topology_id, raw)
        families.append(
            FamilyRows(
                topology_id=topology_id,
                topology=topology,
                family_state_id=family_state_id,
                snapshot=(
                    family_state_id,
                    type(witness).__name__,
                    int(simplex_complete),
                    int(mids_complete),
                    SNAPSHOT_ENCODING,
                    encode_payload(raw),
                    decision_iso,
                ),
                witness_identity=bytes.fromhex(str(witness.witness_identity)),
            )
        )
    book_age = (
        (decision_at_utc - book_at).total_seconds() if book_at is not None else None
    )
    return CutCorpus(
        cut_row=_cut_row(
            cut_id=selected_cut_id(selection_epoch_identity, decision_at_utc),
            selection_epoch_identity=selection_epoch_identity,
            status=cut_status(
                winner_candidate_id=winner_candidate_id,
                candidate_count=len(evaluations),
            ),
            reason=reason,
            decision_iso=decision_iso,
            selection_policy_identity=selection_policy_identity,
            full_scope_family_count=full_scope_family_count,
            eligible_family_count=len(probability_witnesses),
            candidate_count=len(evaluations),
            winner_candidate_id=winner_candidate_id,
            payload={
                # Per-family state and witness identity live in tier0_cut_family.
                "ineligible_by_family": dict(sorted(ineligible_by_family.items())),
                "excluded_by_family": dict(sorted(excluded_by_family.items())),
                "book_epoch_identity": getattr(book_epoch, "witness_identity", None),
                "book_captured_at_utc": (
                    book_at.isoformat() if book_at is not None else None
                ),
                "book_max_age_seconds": (
                    book_max_age.total_seconds() if book_max_age is not None else None
                ),
                "book_age_seconds": book_age,
                "book_stale": (
                    book_age is not None
                    and book_max_age is not None
                    and book_age > book_max_age.total_seconds()
                ),
                "policy": dict(policy),
            },
        ),
        families=tuple(families),
        q_raw_by_candidate=q_raw_by_candidate,
    )


_NO_CANDIDATE_REASONS = (
    "GLOBAL_AUCTION_NO_CURRENT_PROBABILITY_FAMILY",
    "GLOBAL_AUCTION_NO_REDUCE_ONLY_FAMILY",
    "GLOBAL_FAMILY_INELIGIBLE:",
)


def build_unreceipted_cut(
    *,
    reason: str,
    decision_at_utc: datetime,
    selection_policy_identity: str,
    economic_cut_completed: bool,
    detail: Mapping[str, object],
    cancel_source: str | None = None,
    cancel_stage: str | None = None,
) -> CutCorpus:
    """Corpus for a cut that ended before its auction receipt was written.

    Such a cut never froze a full witness/book vector, so it records the
    status, reason and decision time, and nothing is reconstructed. A
    cancelled INCOMPLETE cut also records its cancel source and stage.
    """

    if reason.startswith(_NO_CANDIDATE_REASONS):
        status = "NO_CANDIDATES"
    elif economic_cut_completed:
        status = "NO_TRADE"
    else:
        status = "INCOMPLETE"
    return CutCorpus(
        cut_row=_cut_row(
            cut_id=uuid.uuid4().hex,
            selection_epoch_identity=None,
            status=status,
            reason=reason,
            decision_iso=decision_at_utc.astimezone(timezone.utc).isoformat(),
            selection_policy_identity=selection_policy_identity,
            full_scope_family_count=0,
            eligible_family_count=0,
            candidate_count=0,
            winner_candidate_id=None,
            payload=detail,
            cancel_source=cancel_source if status == "INCOMPLETE" else None,
            cancel_stage=cancel_stage if status == "INCOMPLETE" else None,
        ),
        families=(),
        q_raw_by_candidate={},
    )


CandidateRows = tuple[tuple[object, ...], ...]


def _attach_held_point_traces(
    corpus: CutCorpus, traces: tuple[bytes, ...], reservation: int,
) -> tuple[CutCorpus, int]:
    """Attach optional evidence to corpus identities only; base cut is immutable."""
    by_witness: dict[str, list[Mapping[str, object]]] = {}
    for raw in traces:
        try:
            trace = json.loads(raw)
            if trace.get("status") not in {"READY", "UNAVAILABLE"}:
                _point_trace_warning("held point trace unavailable: %s", trace.get("reason"))
                continue
            consumer = str(trace.get("consumer_witness_identity") or "")
            if not consumer:
                raise ValueError("CONSUMER_WITNESS_MISSING")
            by_witness.setdefault(consumer, []).append(trace)
        except Exception as exc:  # noqa: BLE001 -- one diagnostic cannot hide peers
            _point_trace_warning("held point trace dropped: malformed:%s", type(exc).__name__)
    families = []
    charge = 0
    for family in corpus.families:
        selected = by_witness.get(family.witness_identity.hex())
        if not selected:
            families.append(family)
            continue
        try:
            topology = decode_payload(family.topology[8])
            base = decode_payload(family.snapshot[5])
            valid = [trace for trace in selected
                     if trace.get("consumer_bindings") == topology["bindings"]
                     and trace.get("consumer_yes_q") == base["raw_yes_q"]
                     and trace.get("family") == topology["family_key"]]
            if not valid:
                raise ValueError("CONSUMER_BINDING_MISMATCH")
            raw = _canonical({**base, "held_sell_point_traces": valid})
            encoded = encode_payload(raw)
            extra = max(0, sys.getsizeof(encoded)-sys.getsizeof(family.snapshot[5]))
            if charge+extra > reservation or len(encoded) > _POINT_TRACE_TRANSACTION_LIMIT:
                raise ValueError("ENCODED_DIAGNOSTIC_BUDGET")
            state_id = _id128(b"tier0_family_state_v2_point_trace", family.topology_id, raw)
            snapshot = (state_id, *family.snapshot[1:4], SNAPSHOT_POINT_TRACE_ENCODING,
                        encoded, family.snapshot[6])
            families.append(FamilyRows(family.topology_id, family.topology, state_id,
                                       snapshot, family.witness_identity))
            charge += extra
        except Exception as exc:  # noqa: BLE001 -- preserve q/book/legs/full base cut
            _point_trace_warning("held point trace dropped: %s:%s", type(exc).__name__, exc)
            families.append(family)
    return CutCorpus(corpus.cut_row, tuple(families), corpus.q_raw_by_candidate), charge


class PendingCut:
    """One queued cut. Its rows are built lazily, once, off the receipt path.

    ``build`` closes over the cut's frozen inputs and returns the corpus plus
    any winner candidate rows. ``rows()`` calls it at most once and then drops
    it, so a queued cut holds compact bytes, not witness matrices.
    """

    __slots__ = ("_build", "_rows", "decision_log_id", "actuation",
                 "_point_traces", "_diagnostic_charge")

    def __init__(
        self,
        build: Callable[[], tuple[CutCorpus, CandidateRows]],
        decision_log_id: int | None,
        *, point_traces: Sequence[bytes] = (),
    ) -> None:
        self._build: Callable[[], tuple[CutCorpus, CandidateRows]] | None = build
        self._rows: tuple[CutCorpus, CandidateRows] | None = None
        self.decision_log_id = decision_log_id
        # (outcome, reason) of a SELECTED cut's winner, set before the flush.
        self.actuation: tuple[str, str | None] | None = None
        retained: list[bytes] = []
        total = 0
        for raw in point_traces:
            if not isinstance(raw, bytes) or len(raw) > _POINT_TRACE_CANONICAL_LIMIT:
                _point_trace_warning("held point trace dropped: invalid frozen body")
                continue
            total += sys.getsizeof(raw)
            if total > _POINT_TRACE_QUEUE_LIMIT:
                _point_trace_warning("held point traces dropped: incoming batch budget")
                retained.clear()
                break
            retained.append(raw)
        self._point_traces = tuple(retained)
        self._diagnostic_charge = (
            sys.getsizeof(self._point_traces)+sum(sys.getsizeof(raw) for raw in self._point_traces)
            if self._point_traces else 0
        )

    def drop_point_traces(self, reason: str) -> None:
        count = len(self._point_traces)
        self._point_traces = ()
        self._diagnostic_charge = 0
        if count:
            _point_trace_warning("held point traces dropped: reason=%s count=%d", reason, count)

    def rows(self) -> tuple[CutCorpus, CandidateRows]:
        if self._rows is None:
            build, self._build = self._build, None
            assert build is not None
            try:
                corpus, candidates = build()
                if self._point_traces:
                    try:
                        corpus, charge = _attach_held_point_traces(
                            corpus, self._point_traces, self._diagnostic_charge,
                        )
                        self._diagnostic_charge = charge
                    except Exception as exc:  # noqa: BLE001 -- retain the entire base cut
                        self._diagnostic_charge = 0
                        _point_trace_warning("held point trace attach failed: %s", type(exc).__name__)
                self._rows = corpus, candidates
            except BaseException:
                self._diagnostic_charge = 0
                raise
            finally:
                # Pending canonical bytes are replaced by the actual encoded
                # snapshot increment, never retained alongside a second copy.
                self._point_traces = ()
        return self._rows


_PENDING_LOCK = threading.Lock()
_PENDING: dict[str, list[PendingCut]] = {}
_OVERFLOW: dict[str, int] = {}


def point_trace_pending_charge() -> int:
    """Actual optional bytes/container increment retained by all DB queues."""
    with _PENDING_LOCK:
        return sum(cut._diagnostic_charge for queue in _PENDING.values() for cut in queue)


def queue_cut(db_key: str, cut: PendingCut) -> None:
    """Hold a cut until a flush on ``db_key`` commits it.

    SCOPE: this process's cuts. DRAIN: every global batch ends with one
    bounded flush. RESET: rows leave the queue only after their flush
    transaction committed. When the queue is full the oldest cut is dropped
    and counted; the next flush writes the count as its own INCOMPLETE row, so
    the loss is recorded rather than silent. A process exit loses the queue.
    """

    with _PENDING_LOCK:
        queue = _PENDING.setdefault(db_key, [])
        if len(queue) >= _QUEUE_LIMIT:
            dropped = queue.pop(0)
            dropped.drop_point_traces("CORPUS_QUEUE_OVERFLOW")
            _OVERFLOW[db_key] = _OVERFLOW.get(db_key, 0) + 1
        # Charge the objects actually retained, not their compressed estimate.
        # No day quota: a successful flush immediately restores capacity, and
        # a budget miss drops only this optional diagnostic, never its cut.
        if (sum(item._diagnostic_charge for pending in _PENDING.values() for item in pending)
                +cut._diagnostic_charge > _POINT_TRACE_QUEUE_LIMIT):
            cut.drop_point_traces("DIAGNOSTIC_QUEUE_BUDGET")
        queue.append(cut)


def record_actuation(
    db_key: str,
    decision_log_id: int,
    outcome: str,
    reason: str | None,
) -> bool:
    """Attach a SELECTED cut's actuation outcome to its queued row.

    Returns False when no queued cut carries ``decision_log_id`` (dropped by
    overflow, or already written); the caller logs that loss.
    """

    with _PENDING_LOCK:
        for cut in _PENDING.get(db_key, ()):
            if cut.decision_log_id == decision_log_id:
                cut.actuation = (outcome, reason)
                return True
    return False


def pending_cuts(db_key: str) -> tuple[tuple[PendingCut, ...], int]:
    """The oldest queued cuts, at most ``_FLUSH_LIMIT``, and the overflow count.

    The bound keeps each flush transaction short; a batch queues one to a few
    cuts, so live traffic drains within one flush.
    """

    with _PENDING_LOCK:
        return tuple(_PENDING.get(db_key, ())[:_FLUSH_LIMIT]), _OVERFLOW.get(db_key, 0)


def release_cuts(db_key: str, written: Sequence[PendingCut], overflow: int) -> None:
    """Drop cuts, and the overflow count, whose flush transaction committed."""

    ids = {id(cut) for cut in written}
    with _PENDING_LOCK:
        queue = _PENDING.get(db_key)
        if queue is not None:
            queue[:] = [cut for cut in queue if id(cut) not in ids]
        for cut in written:
            cut._diagnostic_charge = 0
            cut._point_traces = ()
        remaining = _OVERFLOW.get(db_key, 0) - overflow
        if remaining > 0:
            _OVERFLOW[db_key] = remaining
        else:
            _OVERFLOW.pop(db_key, None)


_CUT_COLUMNS = (
    "cut_id", "selection_epoch_identity", "status", "reason", "decision_at_utc",
    "selection_policy_identity", "full_scope_family_count", "eligible_family_count",
    "candidate_count", "winner_candidate_id", "decision_log_id",
    "payload_encoding", "payload_sha256", "payload", "created_at",
    "cancel_source", "cancel_stage", "actuation_outcome", "actuation_reason",
)
_TOPOLOGY_COLUMNS = (
    "topology_id", "family_key", "city", "target_date", "metric", "native_unit",
    "bin_count", "payload_encoding", "payload", "first_seen_at_utc",
)
_SNAPSHOT_COLUMNS = (
    "family_state_id", "witness_kind", "simplex_complete",
    "market_reference_complete", "payload_encoding", "payload", "first_seen_at_utc",
)


def _sequence_ids(
    conn: sqlite3.Connection,
    *,
    table: str,
    seq: str,
    key: str,
    columns: Sequence[str],
    rows: Mapping[bytes, tuple[object, ...]],
    extra: Mapping[bytes, Sequence[object]] | None = None,
    extra_columns: Sequence[str] = (),
) -> dict[bytes, int]:
    """Insert absent content-addressed rows; return {content id: integer seq}."""

    found: dict[bytes, int] = {}
    keys = list(rows)
    for offset in range(0, len(keys), 400):
        chunk = keys[offset : offset + 400]
        marks = ",".join("?" for _ in chunk)
        found.update(
            (bytes(row[0]), int(row[1]))
            for row in conn.execute(
                f"SELECT {key}, {seq} FROM {table} WHERE {key} IN ({marks})", chunk
            )
        )
    missing = [k for k in keys if k not in found]
    if missing:
        names = ",".join((*columns, *extra_columns))
        marks = ",".join("?" for _ in (*columns, *extra_columns))
        for k in missing:
            cursor = conn.execute(
                f"INSERT INTO {table} ({names}) VALUES ({marks})",
                (*rows[k], *(extra[k] if extra else ())),
            )
            found[k] = int(cursor.lastrowid)
    return found


def cut_write_units(
    conn: sqlite3.Connection,
    corpus: CutCorpus,
    *,
    decision_log_id: int | None,
    max_new_rows: int,
    actuation: tuple[str, str | None] | None = None,
) -> list[Callable[[], None]]:
    """Split one cut into writes of at most ``max_new_rows`` new content rows.

    Each unit runs in its own transaction, so the bytes committed, which is
    what drives commit latency on this host, stay bounded. The
    content-addressed topology and snapshot rows go first, in chunks. The final
    unit writes the cut row and its links, and it is the only unit that makes
    the cut visible. Every unit is idempotent: a retry after a partial flush
    finds the earlier chunks already present and writes only what is missing.
    """

    families = corpus.families
    units: list[Callable[[], None]] = []
    chunk: list[FamilyRows] = []
    diagnostic_bytes = 0
    for family in families:
        # Counting the entire V2 BLOB is conservative: it bounds the added
        # diagnostic bytes without expanding the existing row/time limits.
        extra = len(family.snapshot[5]) if family.snapshot[4] == SNAPSHOT_POINT_TRACE_ENCODING else 0
        if chunk and (len(chunk) >= max_new_rows
                      or diagnostic_bytes+extra > _POINT_TRACE_TRANSACTION_LIMIT):
            frozen_chunk = tuple(chunk)
            units.append(lambda chunk=frozen_chunk: _write_family_content(conn, chunk))
            chunk, diagnostic_bytes = [], 0
        chunk.append(family)
        diagnostic_bytes += extra
    if chunk:
        frozen_chunk = tuple(chunk)
        units.append(lambda chunk=frozen_chunk: _write_family_content(conn, chunk))
    units.append(
        lambda: write_cut(
            conn, corpus, decision_log_id=decision_log_id, actuation=actuation
        )
    )
    return units


def _write_family_content(
    conn: sqlite3.Connection,
    families: Sequence[FamilyRows],
) -> tuple[dict[bytes, int], dict[bytes, int]]:
    topology_seq = _sequence_ids(
        conn,
        table="tier0_family_topology",
        seq="topology_seq",
        key="topology_id",
        columns=_TOPOLOGY_COLUMNS,
        rows={f.topology_id: f.topology for f in families},
    )
    state_seq = _sequence_ids(
        conn,
        table="tier0_family_snapshot",
        seq="state_seq",
        key="family_state_id",
        columns=_SNAPSHOT_COLUMNS,
        rows={f.family_state_id: f.snapshot for f in families},
        extra={f.family_state_id: (topology_seq[f.topology_id],) for f in families},
        extra_columns=("topology_seq",),
    )
    return topology_seq, state_seq


def write_cut(
    conn: sqlite3.Connection,
    corpus: CutCorpus,
    *,
    decision_log_id: int | None,
    actuation: tuple[str, str | None] | None = None,
) -> None:
    """Write one cut inside the caller's open trade-DB transaction.

    ``actuation`` is a SELECTED cut's (outcome, reason): SUBMITTED, or why its
    winner reached no venue order. Idempotent: an identical retry of a cut
    already written is a no-op, and a conflicting payload for the same cut id
    raises.
    """

    row = dict(zip(_CUT_COLUMNS, corpus.cut_row))
    row["decision_log_id"] = decision_log_id
    if actuation is not None:
        row["actuation_outcome"], row["actuation_reason"] = actuation
    stored = conn.execute(
        "SELECT status, payload_sha256 FROM tier0_auction_cut WHERE cut_id = ?",
        (row["cut_id"],),
    ).fetchone()
    if stored is not None:
        if tuple(stored) != (row["status"], row["payload_sha256"]):
            raise Tier0CorpusIdentityConflict(
                f"TIER0_CORPUS_IDENTITY_CONFLICT:tier0_auction_cut:{row['cut_id']}"
            )
        return
    families = corpus.families
    topology_seq = _sequence_ids(
        conn,
        table="tier0_family_topology",
        seq="topology_seq",
        key="topology_id",
        columns=_TOPOLOGY_COLUMNS,
        rows={f.topology_id: f.topology for f in families},
    )
    state_seq = _sequence_ids(
        conn,
        table="tier0_family_snapshot",
        seq="state_seq",
        key="family_state_id",
        columns=_SNAPSHOT_COLUMNS,
        rows={f.family_state_id: f.snapshot for f in families},
        extra={f.family_state_id: (topology_seq[f.topology_id],) for f in families},
        extra_columns=("topology_seq",),
    )
    cursor = conn.execute(
        f"INSERT INTO tier0_auction_cut ({','.join(_CUT_COLUMNS)}) "
        f"VALUES ({','.join('?' for _ in _CUT_COLUMNS)})",
        tuple(row[column] for column in _CUT_COLUMNS),
    )
    cut_seq = int(cursor.lastrowid)
    conn.executemany(
        "INSERT INTO tier0_cut_family "
        "(cut_seq, topology_seq, state_seq, probability_witness_identity) "
        "VALUES (?,?,?,?)",
        [
            (
                cut_seq,
                topology_seq[f.topology_id],
                state_seq[f.family_state_id],
                f.witness_identity,
            )
            for f in families
        ],
    )


def overflow_cut(*, overflow: int, selection_policy_identity: str) -> CutCorpus:
    """INCOMPLETE row recording ``overflow`` cuts dropped from a full queue."""

    return build_unreceipted_cut(
        reason=f"CORPUS_QUEUE_OVERFLOW:{overflow}",
        decision_at_utc=datetime.now(timezone.utc),
        selection_policy_identity=selection_policy_identity,
        economic_cut_completed=False,
        detail={"dropped_cut_count": overflow},
    )

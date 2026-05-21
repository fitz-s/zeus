# Created: 2026-05-20
# Last reused or audited: 2026-05-21
# Authority basis: operator P0-1 live-money spec 2026-05-20/21 (mutually-exclusive weather
#                  family sizing), STAGE A; Fitz §1 (structural decision > patch).

"""P0-1 STAGE A — emergency mutually-exclusive family entry gate.

A weather market for one ``(city, target_date, temperature_metric)`` is a
PARTITION: exactly one temperature bin resolves YES. The bins are NOT
independent assets — payoff covariance is singular/negative (only one YES
pays). The legacy pipeline ran family-wise FDR, marked EVERY bin passing the
BH cutoff as ``should_trade=True``, and the cycle runtime submitted each as an
INDEPENDENT scalar-Kelly live order → ~Nx over-allocation on one underlying
event.

This module is the STAGE A emergency gate (Stage B replaces it with the full
``ExclusiveOutcomePortfolio`` / ``WeatherFamilyDecision`` object). When the env
flag ``ZEUS_LIVE_MAX_ONE_ENTRY_PER_WEATHER_FAMILY`` is ON (default "1"), for
each family with >=2 ``should_trade=True`` bins, exactly ONE bin survives —
the single best by **executable net EV after fees + spread + depth + family
cap** — and the rest are flipped to ``should_trade=False`` carrying the
auditable ``MUTUALLY_EXCLUSIVE_FAMILY_DEDUP`` reason string.

STAGE A is PURE RUNTIME GATING — no schema change (per the operator spec). The
dropped-bin audit trail is the reason STRING in ``rejection_reasons`` +
``rejection_stage`` + ``rejection_reason_detail`` + a structured log line; it
does NOT set ``rejection_reason_enum``. Rationale: the ``no_trade_events`` DB
CHECK clause is built dynamically from the ``NoTradeReason`` enum at table
creation, so adding an enum member changes the schema hash (SCHEMA_VERSION
bump + re-pin) and would be rejected by the baked-in CHECK on already-created
SV15 DBs. Persisting the enum is therefore deferred to Stage B (the
architectural-object PR that already carries a DB migration). The spec wording
("record NoTradeReason ... so it's auditable, e.g. MUTUALLY_EXCLUSIVE_FAMILY_DEDUP")
is satisfied by the string-level audit. SEE the SCAFFOLD report — this is the
flagged brief-premise conflict (brief said "no schema change" AND "add to enum";
both cannot hold, runtime-gating wins for Stage A).

Selection-metric provenance: Stage A now has two hooks. The primary
``preselect_single_family_edge_before_kelly`` hook runs in the evaluator before
scalar Kelly/risk sizing and ranks by ``BinEdge.forward_edge`` so dropped
siblings cannot mutate projected exposure. The cycle-runtime
``dedup_mutually_exclusive_families`` hook remains a second-line safety net for
legacy/mixed callers and still ranks already-sized ``EdgeDecision`` objects by
``size_usd``. Stage B replaces both with a first-class family payoff optimizer.

Fail-safe: this gate can only REMOVE entries (set should_trade False). It never
adds, resizes, or re-enables a decision, so it can never increase exposure.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from typing import TYPE_CHECKING, Any, Iterable

if TYPE_CHECKING:  # pragma: no cover - typing only
    from src.engine.evaluator import EdgeDecision

logger = logging.getLogger(__name__)

ENV_FLAG = "ZEUS_LIVE_MAX_ONE_ENTRY_PER_WEATHER_FAMILY"
_DEFAULT = "1"  # ON by default (live-money fail-safe).

# Audit reason string for dropped bins. STAGE A uses the STRING (not the
# NoTradeReason enum) so no schema-derived CHECK clause is touched. Stage B
# promotes this to a NoTradeReason enum member + DB migration. Kept lower-case
# to match the StrEnum auto() value convention the eventual member will take.
MUTUALLY_EXCLUSIVE_FAMILY_DEDUP = "mutually_exclusive_family_dedup"
FAMILY_REJECTION_STAGE = "ANTI_CHURN"


@dataclass(frozen=True)
class WeatherFamilyKey:
    """Identity for one mutually-exclusive weather outcome family."""

    city: str
    target_date: str
    temperature_metric: str


@dataclass(frozen=True)
class WeatherFamilyExposure:
    """Minimal read model for existing open/pending/active family exposure."""

    key: WeatherFamilyKey
    bin_label: str = ""
    phase: str = "active"
    position_id: str = ""


@dataclass(frozen=True)
class FamilyPreselectionDrop:
    """One FDR-selected edge removed before scalar Kelly sizing."""

    edge: Any
    dropped_bin: str
    kept_bin: str
    family_selection_score: float
    kept_family_selection_score: float


_BLOCKING_EXPOSURE_PHASES = frozenset(
    {
        "",
        "open",
        "pending",
        "active",
        "pending_entry",
        "pending_tracked",
        "entered",
        "holding",
        "day0_window",
        "pending_exit",
    }
)


def _family_key(city: str, target_date: str, temperature_metric: str) -> WeatherFamilyKey:
    return WeatherFamilyKey(str(city), str(target_date), str(temperature_metric))


def _field(obj: Any, name: str, default: Any = "") -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _exposure_key(exposure: Any) -> WeatherFamilyKey:
    key = _field(exposure, "key", None)
    if isinstance(key, WeatherFamilyKey):
        return key
    if isinstance(key, dict):
        return WeatherFamilyKey(
            str(key.get("city", "")),
            str(key.get("target_date", "")),
            str(key.get("temperature_metric", "")),
        )
    return WeatherFamilyKey(
        str(_field(exposure, "city", "")),
        str(_field(exposure, "target_date", "")),
        str(_field(exposure, "temperature_metric", "")),
    )


def _exposure_bin_label(exposure: Any) -> str:
    return str(
        _field(
            exposure,
            "bin_label",
            _field(exposure, "range_label", _field(exposure, "outcome_label", "")),
        )
        or ""
    )


def _decision_bin_label(decision: "EdgeDecision") -> str:
    edge = getattr(decision, "edge", None)
    if edge is None or getattr(edge, "bin", None) is None:
        return ""
    return str(getattr(edge.bin, "label", "") or "")


def _blocking_exposures_for_key(
    exposures: Iterable[Any] | None,
    key: WeatherFamilyKey,
) -> list[Any]:
    if exposures is None:
        return []
    blocking: list[Any] = []
    for exposure in exposures:
        if _exposure_key(exposure) != key:
            continue
        phase = str(_field(exposure, "phase", _field(exposure, "state", "")) or "").lower()
        if phase in _BLOCKING_EXPOSURE_PHASES:
            blocking.append(exposure)
    return blocking


def weather_family_exposures_from_portfolio(portfolio: Any) -> list[WeatherFamilyExposure]:
    """Project portfolio positions into the family-gate exposure read model."""
    exposures: list[WeatherFamilyExposure] = []
    for pos in getattr(portfolio, "positions", None) or ():
        city = str(_field(pos, "city", "") or "")
        target_date = str(_field(pos, "target_date", "") or "")
        temperature_metric = str(_field(pos, "temperature_metric", "") or "")
        if not (city and target_date and temperature_metric):
            continue
        phase = str(_field(pos, "phase", _field(pos, "state", "")) or "")
        if phase.lower() not in _BLOCKING_EXPOSURE_PHASES:
            continue
        exposures.append(
            WeatherFamilyExposure(
                key=WeatherFamilyKey(city, target_date, temperature_metric),
                bin_label=_exposure_bin_label(pos),
                phase=phase,
                position_id=str(_field(pos, "position_id", _field(pos, "trade_id", "")) or ""),
            )
        )
    return exposures


def _has_conflicting_existing_exposure(
    decision: "EdgeDecision",
    exposures: list[Any],
) -> tuple[bool, Any | None]:
    new_label = _decision_bin_label(decision)
    for exposure in exposures:
        existing_label = _exposure_bin_label(exposure)
        if not existing_label or not new_label or existing_label != new_label:
            return True, exposure
    return False, None


def family_gate_enabled() -> bool:
    """True when the STAGE A one-entry-per-family gate is ON.

    Default ON ("1"). Disabled only by an explicit ``"0"`` / ``"false"`` /
    ``"no"`` / ``"off"`` (case-insensitive). Any other value (including the
    unset default) keeps the live-money fail-safe ON.
    """
    raw = os.environ.get(ENV_FLAG, _DEFAULT).strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _executable_rank_key(decision: "EdgeDecision") -> tuple[float, float, str]:
    """Sort key for "single best" within a mutually-exclusive family.

    Primary: ``size_usd`` (the executor's post-everything executable dollar —
    see module docstring for the reuse rationale). Secondary tie-break:
    ``edge.forward_edge`` (fee/spread-adjusted per-dollar edge). Final
    deterministic tie-break: ``decision_id`` (lexicographic) so identical
    economic ranks resolve stably across runs.

    Returned tuple is compared with ``max(...)``; larger is better. The
    ``decision_id`` term is negated-by-min handling at the call site, so here
    we return it as-is and the caller uses a composite that prefers the
    lexicographically smallest id on a full tie.
    """
    size = float(getattr(decision, "size_usd", 0.0) or 0.0)
    edge = getattr(decision, "edge", None)
    forward_edge = float(getattr(edge, "forward_edge", 0.0) or 0.0) if edge is not None else 0.0
    return (size, forward_edge, getattr(decision, "decision_id", "") or "")


def _edge_family_selection_score(edge: Any) -> float:
    """Stage-A pre-Kelly utility proxy for one mutually-exclusive family leg.

    This intentionally does not use ``EdgeDecision.size_usd`` because that is a
    downstream scalar-Kelly output. Until Stage B lands a payoff-vector
    optimizer, the least-wrong pre-sizing score is the edge's executable
    forward edge, with legacy ``edge`` as fallback.
    """

    try:
        score = float(getattr(edge, "forward_edge", 0.0) or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    if score == 0.0:
        try:
            score = float(getattr(edge, "edge", 0.0) or 0.0)
        except (TypeError, ValueError):
            score = 0.0
    return score


def _edge_preselection_key(edge: Any) -> tuple[float, float, float, tuple[int, ...]]:
    score = _edge_family_selection_score(edge)
    try:
        posterior = float(getattr(edge, "p_posterior", 0.0) or 0.0)
    except (TypeError, ValueError):
        posterior = 0.0
    try:
        entry_price = float(getattr(edge, "entry_price", 0.0) or 0.0)
    except (TypeError, ValueError):
        entry_price = 0.0
    label = ""
    bin_obj = getattr(edge, "bin", None)
    if bin_obj is not None:
        label = str(getattr(bin_obj, "label", "") or "")
    return (score, posterior, entry_price, tuple(-ord(c) for c in label))


def preselect_single_family_edge_before_kelly(
    edges: list[Any],
    *,
    city: str,
    target_date: str,
    temperature_metric: str,
    enabled: bool | None = None,
) -> tuple[list[Any], list[FamilyPreselectionDrop]]:
    """Collapse one mutually-exclusive weather family before scalar Kelly.

    ``evaluate_candidate`` calls this after full-family FDR and before the
    Kelly/risk loop. That prevents dropped sibling bins from mutating projected
    exposure, heat, min-order, or risk throttles.
    """

    if enabled is None:
        enabled = family_gate_enabled()
    if not enabled or len(edges) < 2:
        return edges, []

    best = max(edges, key=_edge_preselection_key)
    best_bin = ""
    best_edge_bin = getattr(best, "bin", None)
    if best_edge_bin is not None:
        best_bin = str(getattr(best_edge_bin, "label", "") or "")
    best_score = _edge_family_selection_score(best)
    kept: list[Any] = [best]
    drops: list[FamilyPreselectionDrop] = []
    for edge in edges:
        if edge is best:
            continue
        dropped_bin = ""
        edge_bin = getattr(edge, "bin", None)
        if edge_bin is not None:
            dropped_bin = str(getattr(edge_bin, "label", "") or "")
        score = _edge_family_selection_score(edge)
        drops.append(
            FamilyPreselectionDrop(
                edge=edge,
                dropped_bin=dropped_bin,
                kept_bin=best_bin,
                family_selection_score=score,
                kept_family_selection_score=best_score,
            )
        )
        logger.info(
            "[MUTUALLY_EXCLUSIVE_FAMILY_PRE_KELLY] family=%s|%s|%s dropped_bin=%r "
            "kept_bin=%r dropped_score=%.6f kept_score=%.6f",
            city,
            target_date,
            temperature_metric,
            dropped_bin,
            best_bin,
            score,
            best_score,
        )
    return kept, drops


def _pick_best_index(decisions: list["EdgeDecision"], idxs: list[int]) -> int:
    """Return the index (into ``decisions``) of the single best family member.

    Best = highest ``(size_usd, forward_edge)``; on a full economic tie the
    lexicographically smallest ``decision_id`` wins (stable, deterministic).
    """
    def _composite(i: int) -> tuple[float, float, tuple[int, ...]]:
        size, fwd, did = _executable_rank_key(decisions[i])
        # Negate the id codepoints so that `max` selects the SMALLEST id on a
        # (size, forward_edge) tie — deterministic and run-stable.
        neg_id = tuple(-ord(c) for c in did)
        return (size, fwd, neg_id)

    return max(idxs, key=_composite)


def dedup_mutually_exclusive_families(
    decisions: list["EdgeDecision"],
    *,
    city: str,
    target_date: str,
    temperature_metric: str,
    enabled: bool | None = None,
    existing_exposures: Iterable[Any] | None = None,
    family_portfolio_intent: bool = False,
) -> list["EdgeDecision"]:
    """STAGE A gate: keep only the single best entry per exclusive family.

    Mutates the passed ``EdgeDecision`` objects in place (sets
    ``should_trade=False`` + ``rejection_stage`` + ``rejection_reasons`` string
    + ``rejection_reason_detail`` on dropped bins; the ``rejection_reason_enum``
    is left untouched — STAGE A is pure runtime gating, no schema-derived CHECK)
    and returns the same list for caller convenience.

    Args:
        decisions: the per-candidate decision list from ``evaluate_candidate``.
            All entries belong to ONE ``(city, target_date, metric)`` market
            family (one candidate == one family; see
            ``src/strategy/selection_family.py`` family-scope docstring). They
            are nonetheless grouped defensively by
            ``(city, target_date, temperature_metric)`` so the contract holds
            even if a future caller passes a mixed list.
        city / target_date / temperature_metric: the family key. EdgeDecision
            does not itself carry the family identity (city/date/metric live on
            the candidate, not the per-bin decision), so the caller supplies it.
        enabled: override for the env gate; ``None`` reads
            ``family_gate_enabled()``.
        existing_exposures: optional current-cycle read model of already
            open/pending/active exposure keyed by ``WeatherFamilyKey``. When a
            different bin already has exposure, new independent entries for the
            same family are blocked unless ``family_portfolio_intent`` is true.
        family_portfolio_intent: true only when a first-class family portfolio
            optimizer emitted the executable portfolio intent. FDR-selected
            hypotheses alone are not a portfolio intent.

    Returns:
        The same ``decisions`` list (mutated in place when the gate fires).
    """
    if enabled is None:
        enabled = family_gate_enabled()
    if not enabled:
        return decisions

    key = _family_key(city, target_date, temperature_metric)
    blocking_exposures = (
        []
        if family_portfolio_intent
        else _blocking_exposures_for_key(existing_exposures, key)
    )
    if blocking_exposures:
        for d in decisions:
            if not getattr(d, "should_trade", False):
                continue
            conflicts, exposure = _has_conflicting_existing_exposure(d, blocking_exposures)
            if not conflicts:
                continue
            existing_label = _exposure_bin_label(exposure) if exposure is not None else ""
            existing_position = str(_field(exposure, "position_id", "") or "")
            d.should_trade = False
            d.rejection_stage = FAMILY_REJECTION_STAGE
            d.rejection_reasons = [MUTUALLY_EXCLUSIVE_FAMILY_DEDUP]
            d.rejection_reason_detail = (
                f"family={city}|{target_date}|{temperature_metric} "
                f"dropped_bin={_decision_bin_label(d)!r} "
                f"existing_exposure_bin={existing_label!r} "
                f"existing_position_id={existing_position!r} "
                f"({ENV_FLAG}=1; existing family exposure; no family portfolio intent)"
            )
            logger.info(
                "[MUTUALLY_EXCLUSIVE_FAMILY_DEDUP] family=%s|%s|%s dropped_bin=%r "
                "existing_exposure_bin=%r existing_position_id=%r decision_id=%s",
                city,
                target_date,
                temperature_metric,
                _decision_bin_label(d),
                existing_label,
                existing_position,
                getattr(d, "decision_id", "") or "",
            )

    # Group the should_trade=True decisions by the family key. With one
    # candidate per call this is a single group, but the dict keeps the
    # contract robust under a mixed-list caller.
    groups: dict[tuple[str, str, str], list[int]] = {}
    for i, d in enumerate(decisions):
        if not getattr(d, "should_trade", False):
            continue
        key = (str(city), str(target_date), str(temperature_metric))
        groups.setdefault(key, []).append(i)

    for key, idxs in groups.items():
        if len(idxs) < 2:
            # Single-bin (or single-entry) family: untouched — byte-identical
            # to the legacy per-edge path. No regression.
            continue
        best_i = _pick_best_index(decisions, idxs)
        best = decisions[best_i]
        best_label = ""
        best_edge = getattr(best, "edge", None)
        if best_edge is not None and getattr(best_edge, "bin", None) is not None:
            best_label = str(getattr(best_edge.bin, "label", "") or "")
        kept_size = float(getattr(best, "size_usd", 0.0) or 0.0)
        for i in idxs:
            if i == best_i:
                continue
            d = decisions[i]
            d.should_trade = False
            d.rejection_stage = FAMILY_REJECTION_STAGE
            # STAGE A audit: reason STRING only (no enum → no schema CHECK).
            d.rejection_reasons = [MUTUALLY_EXCLUSIVE_FAMILY_DEDUP]
            dropped_label = ""
            d_edge = getattr(d, "edge", None)
            if d_edge is not None and getattr(d_edge, "bin", None) is not None:
                dropped_label = str(getattr(d_edge.bin, "label", "") or "")
            d.rejection_reason_detail = (
                f"family={city}|{target_date}|{temperature_metric} "
                f"dropped_bin={dropped_label!r} kept_bin={best_label!r} "
                f"kept_size_usd={kept_size:.2f} "
                f"({ENV_FLAG}=1; STAGE A single_best)"
            )
            logger.info(
                "[MUTUALLY_EXCLUSIVE_FAMILY_DEDUP] family=%s|%s|%s dropped_bin=%r "
                "kept_bin=%r kept_size_usd=%.2f decision_id=%s",
                city,
                target_date,
                temperature_metric,
                dropped_label,
                best_label,
                kept_size,
                getattr(d, "decision_id", "") or "",
            )

    return decisions

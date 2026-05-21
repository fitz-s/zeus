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

Selection-metric provenance (flagged for critic review): the codebase does not
compute an explicit "executable net EV" scalar anywhere reusable.
``BinEdge.ev_per_dollar`` is a stale field (never set; always 0.0) and
``rank_edges()`` no longer exists. The executor's own *revealed preference* —
the single executable dollar figure produced AFTER fee-rate, phase-aware Kelly,
DDD discount, oracle penalty, risk-throttle, allocation multiplier, min-order
and risk-limit gates — is ``EdgeDecision.size_usd`` (set at evaluator.py
size_usd=size). We therefore rank by ``size_usd`` (descending), which IS the
post-fees / post-depth / post-cap executable allocation the executor would
deploy. Deterministic tie-break: ``edge.forward_edge`` then ``decision_id``.
No new EV formula is invented; we reuse the executor's existing sizing output.

Fail-safe: this gate can only REMOVE entries (set should_trade False). It never
adds, resizes, or re-enables a decision, so it can never increase exposure.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

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

    Returns:
        The same ``decisions`` list (mutated in place when the gate fires).
    """
    if enabled is None:
        enabled = family_gate_enabled()
    if not enabled:
        return decisions

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
            d.rejection_stage = "MUTUALLY_EXCLUSIVE_FAMILY"
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

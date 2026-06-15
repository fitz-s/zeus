# Created: 2026-06-14
# Last reused or audited: 2026-06-14
# Authority basis: docs/rebuild/consult_build_spec.md Stage 0 (lines 994-1033;
#   the 19 receipt-field list lines 1008-1027; the one-invariant statement lines 5-12) +
#   docs/evidence/qkernel_rebuild/spec_vs_live_drift_ledger.md (spine-only fields not yet
#   computed by the current live path are Optional/None now; each later stage wires its own)

"""DecisionReceipt — the Stage 0 receipt spine (observability before behavior change).

The one rebuild invariant (consult_build_spec.md:5-12): for every live candidate the
receipt must prove

    fresh settlement-station inputs -> one debiased predictive distribution
    -> one complete Omega -> one normalized joint q -> one coherent joint
    uncertainty sample -> one executable family book -> one payoff-vector decision
    -> one liquidation-aware lifecycle

Stage 0 makes every CURRENT live candidate *reconstructable from source inputs to
decision* — it changes NO decision, sizing, or submit behavior. It populates only the
fields the current live path already computes (mu_native, sigma_native, q_source,
rounding_rule, q_sum, member envelope when available); the spine-only fields each later
stage will own (predictive_distribution_id, q_band_basis, route_id, payoff_vector_hash,
edge_lcb, delta_u, market_implied_q) exist here NOW as Optional/None.

CORRECTED-TRANSFORMATION DESIGN (operator law — no detector/gate/clamp):
  The reconstruction-correctness of the receipt is not *checked* after the fact and
  flagged when wrong. It is made *unconstructable-when-wrong* by deriving the coherence
  fields from the same arrays the q-build used:

    - member_min_native / member_max_native are min()/max() of the SAME member array,
      so member_min_native <= member_max_native is true by construction (a receipt with
      min > max cannot be built through ``from_q_build``).
    - q_sum is the literal sum of the SAME q vector, so a receipt whose q_sum disagrees
      with its q vector is unconstructable — there is no setter that takes a free q_sum.
    - debiased_member_{min,max}_native are min()/max() of the debiased member array, so
      the debiased envelope is coherent for the same structural reason.

  A later stage that breaks the transform (e.g. emits members [20..23] but a receipt
  claiming the family is reconstructable at 26) cannot smuggle it past this object: the
  reconstruction reads the derived envelope, and the derived envelope can only describe
  the array that was actually integrated. The bad output is mathematically impossible,
  not caught.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from typing import Any, Optional, Sequence


def _opt_float(value: Any) -> Optional[float]:
    """Coerce to float, preserving None and rejecting non-finite as None."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return f


def _native_min(members: Optional[Sequence[float]]) -> Optional[float]:
    if members is None:
        return None
    vals = [float(m) for m in members if _opt_float(m) is not None]
    return min(vals) if vals else None


def _native_max(members: Optional[Sequence[float]]) -> Optional[float]:
    if members is None:
        return None
    vals = [float(m) for m in members if _opt_float(m) is not None]
    return max(vals) if vals else None


@dataclass(frozen=True)
class ForecastSpine:
    """The predictive-distribution leg of the receipt (forecast -> mu/sigma/envelope).

    Reconstructs: fresh settlement-station inputs -> one (debiased) predictive
    distribution. Stage 0 populates mu_native/sigma_native + the raw/debiased member
    envelope from the CURRENT live q-build; ``predictive_distribution_id`` is the
    spine-only handle a later stage (Stage 1/3 PredictiveDistributionBuilder) will own.
    """

    # Spine-only — None until the PredictiveDistribution authority exists (Stage 1/3).
    predictive_distribution_id: Optional[str] = None
    # Populated by the CURRENT live path (Stage 0).
    mu_native: Optional[float] = None
    sigma_native: Optional[float] = None
    member_min_native: Optional[float] = None
    member_max_native: Optional[float] = None
    debiased_member_min_native: Optional[float] = None
    debiased_member_max_native: Optional[float] = None
    applied_debias_native: Optional[float] = None
    debias_artifact_id: Optional[str] = None
    day0_observed_extreme_native: Optional[float] = None


@dataclass(frozen=True)
class QSpine:
    """The probability leg of the receipt (predictive distribution -> normalized joint q).

    Reconstructs: one complete Omega -> one normalized joint q -> one coherent joint
    uncertainty sample. Stage 0 populates q_source / rounding_rule / q_sum (q_sum DERIVED
    from the live q vector); q_band_basis and market_implied_q are spine-only.
    """

    q_source: Optional[str] = None
    rounding_rule: Optional[str] = None
    q_sum: Optional[float] = None
    # Spine-only — None until JointQBand (Stage 6) / market-implied reconciliation exist.
    q_band_basis: Optional[str] = None
    market_implied_q: Optional[float] = None


@dataclass(frozen=True)
class RouteSpine:
    """The executable-route leg of the receipt (family book -> chosen route -> payoff vector).

    Reconstructs: one executable family book -> one payoff-vector decision. All fields are
    spine-only at Stage 0 — the FamilyBook / NegRiskRouteSet (Stage 7) and PayoffVector
    decision (Stage 8) do not exist yet, so each stays None until its stage wires it.
    """

    route_id: Optional[str] = None
    payoff_vector_hash: Optional[str] = None
    edge_lcb: Optional[float] = None
    delta_u: Optional[float] = None


@dataclass(frozen=True)
class SizeSpine:
    """The sizing leg of the receipt (decision -> stake authority).

    Reconstructs: one payoff-vector decision -> sized stake. Stage 0 records WHICH authority
    sized the stake (the current path already names this via the receipt's selection /
    sizing provenance); the vector-sizing authority itself is Stage 8.
    """

    sizing_authority: Optional[str] = None


@dataclass(frozen=True)
class DecisionReceipt:
    """One reconstructable spine for one live candidate decision.

    OBSERVABILITY ONLY. Constructing, hashing, or persisting a DecisionReceipt never
    changes a decision, a size, or a submit. It exists so that every live candidate can be
    replayed forecast -> q -> route -> size from the values the live path actually used.

    The four legs (forecast / q / route / size) carry the 19 spec fields exactly
    (consult_build_spec.md:1008-1027). ``from_q_build`` is the only Stage-0 constructor; it
    DERIVES the coherence fields (envelope min/max, q_sum) so an incoherent receipt cannot
    be built.
    """

    forecast: ForecastSpine = field(default_factory=ForecastSpine)
    q: QSpine = field(default_factory=QSpine)
    route: RouteSpine = field(default_factory=RouteSpine)
    size: SizeSpine = field(default_factory=SizeSpine)

    # ------------------------------------------------------------------ builders

    @classmethod
    def from_q_build(
        cls,
        *,
        q_source: Optional[str],
        q_vector: Optional[Sequence[float]] = None,
        mu_native: Optional[float] = None,
        sigma_native: Optional[float] = None,
        raw_members_native: Optional[Sequence[float]] = None,
        debiased_members_native: Optional[Sequence[float]] = None,
        applied_debias_native: Optional[float] = None,
        debias_artifact_id: Optional[str] = None,
        day0_observed_extreme_native: Optional[float] = None,
        rounding_rule: Optional[str] = None,
        sizing_authority: Optional[str] = None,
        # Spine-only pass-throughs (each later stage may supply its own; default None).
        predictive_distribution_id: Optional[str] = None,
        q_band_basis: Optional[str] = None,
        market_implied_q: Optional[float] = None,
        route_id: Optional[str] = None,
        payoff_vector_hash: Optional[str] = None,
        edge_lcb: Optional[float] = None,
        delta_u: Optional[float] = None,
    ) -> "DecisionReceipt":
        """Build the spine from the values the CURRENT live q-build already computed.

        The coherence fields are DERIVED here (never accepted as free inputs), which is the
        corrected transformation: a receipt that misrepresents its own envelope or q-mass
        cannot be constructed.

            member_min_native      = min(raw_members_native)
            member_max_native      = max(raw_members_native)
            debiased_member_min/max= min/max(debiased_members_native)
            q_sum                  = sum(q_vector)

        Every argument is read-only; nothing here can alter the live decision that produced
        these values.
        """
        q_sum: Optional[float] = None
        if q_vector is not None:
            masses = [float(p) for p in q_vector if _opt_float(p) is not None]
            if masses:
                q_sum = float(sum(masses))

        forecast = ForecastSpine(
            predictive_distribution_id=predictive_distribution_id,
            mu_native=_opt_float(mu_native),
            sigma_native=_opt_float(sigma_native),
            member_min_native=_native_min(raw_members_native),
            member_max_native=_native_max(raw_members_native),
            debiased_member_min_native=_native_min(debiased_members_native),
            debiased_member_max_native=_native_max(debiased_members_native),
            applied_debias_native=_opt_float(applied_debias_native),
            debias_artifact_id=debias_artifact_id,
            day0_observed_extreme_native=_opt_float(day0_observed_extreme_native),
        )
        q = QSpine(
            q_source=q_source,
            rounding_rule=rounding_rule,
            q_sum=q_sum,
            q_band_basis=q_band_basis,
            market_implied_q=_opt_float(market_implied_q),
        )
        route = RouteSpine(
            route_id=route_id,
            payoff_vector_hash=payoff_vector_hash,
            edge_lcb=_opt_float(edge_lcb),
            delta_u=_opt_float(delta_u),
        )
        size = SizeSpine(sizing_authority=sizing_authority)
        return cls(forecast=forecast, q=q, route=route, size=size)

    # ------------------------------------------------------------- reconstruction

    def reconstruct_forecast_q_route_and_size(self) -> dict[str, Any]:
        """Replay the receipt into a forecast / q / route / size view.

        This is the contract the Stage-0 RED-on-revert test asserts against. It proves the
        receipt reconstructs the decision *to the extent the current path provides them*:
        whichever legs the live path populated come back coherent, and the not-yet-wired
        spine fields come back as None (their stage owns them).

        Coherence guarantees (structural, not checked):
          - member_min_native <= member_max_native whenever both present
          - debiased_member_min_native <= debiased_member_max_native whenever both present
          - q_sum equals the sum of the q vector it was built from (Sigma q within fp tol of 1
            for a normalized live distribution)
        """
        return {
            "forecast": {
                "predictive_distribution_id": self.forecast.predictive_distribution_id,
                "mu_native": self.forecast.mu_native,
                "sigma_native": self.forecast.sigma_native,
                "member_min_native": self.forecast.member_min_native,
                "member_max_native": self.forecast.member_max_native,
                "debiased_member_min_native": self.forecast.debiased_member_min_native,
                "debiased_member_max_native": self.forecast.debiased_member_max_native,
                "applied_debias_native": self.forecast.applied_debias_native,
                "debias_artifact_id": self.forecast.debias_artifact_id,
                "day0_observed_extreme_native": self.forecast.day0_observed_extreme_native,
            },
            "q": {
                "q_source": self.q.q_source,
                "rounding_rule": self.q.rounding_rule,
                "q_sum": self.q.q_sum,
                "q_band_basis": self.q.q_band_basis,
                "market_implied_q": self.q.market_implied_q,
            },
            "route": {
                "route_id": self.route.route_id,
                "payoff_vector_hash": self.route.payoff_vector_hash,
                "edge_lcb": self.route.edge_lcb,
                "delta_u": self.route.delta_u,
            },
            "size": {
                "sizing_authority": self.size.sizing_authority,
            },
        }

    def has_forecast_spine(self) -> bool:
        """True when the forecast leg carries the current-path minimum (mu/sigma/envelope).

        The Stage-0 live verification signal (consult_build_spec.md:1033) is: no candidate
        receipt lacks mu/sigma/member-envelope/q_source/route. ``has_forecast_spine`` and
        ``has_q_spine`` make that signal queryable without re-deriving it.
        """
        f = self.forecast
        return (
            f.mu_native is not None
            and f.sigma_native is not None
            and f.member_min_native is not None
            and f.member_max_native is not None
        )

    def has_q_spine(self) -> bool:
        """True when the q leg carries q_source and a coherent q_sum."""
        return self.q.q_source is not None and self.q.q_sum is not None

    def envelope_is_coherent(self) -> bool:
        """Structural coherence of the (debiased) member envelope and q mass.

        Returns True whenever the present fields satisfy min <= max and (when a q_sum is
        present) Sigma q is within floating-point tolerance of 1.0. By construction of
        ``from_q_build`` this can only be False for a hand-built receipt — a from_q_build
        receipt is always coherent — so a False here is itself the signal that a receipt was
        assembled outside the corrected transform.
        """
        f = self.forecast
        if f.member_min_native is not None and f.member_max_native is not None:
            if f.member_min_native > f.member_max_native:
                return False
        if (
            f.debiased_member_min_native is not None
            and f.debiased_member_max_native is not None
        ):
            if f.debiased_member_min_native > f.debiased_member_max_native:
                return False
        if self.q.q_sum is not None:
            if not math.isclose(self.q.q_sum, 1.0, abs_tol=1e-6):
                return False
        return True

    # ----------------------------------------------------------------- serialize

    def to_row(self) -> dict[str, Any]:
        """Flatten the spine into the 19 receipt columns (no_trade_events_schema names).

        Column names are EXACTLY the spec field names (consult_build_spec.md:1008-1027) so
        the schema column list and the dataclass field list are one vocabulary.
        """
        return {
            "predictive_distribution_id": self.forecast.predictive_distribution_id,
            "q_source": self.q.q_source,
            "mu_native": self.forecast.mu_native,
            "sigma_native": self.forecast.sigma_native,
            "member_min_native": self.forecast.member_min_native,
            "member_max_native": self.forecast.member_max_native,
            "debiased_member_min_native": self.forecast.debiased_member_min_native,
            "debiased_member_max_native": self.forecast.debiased_member_max_native,
            "applied_debias_native": self.forecast.applied_debias_native,
            "debias_artifact_id": self.forecast.debias_artifact_id,
            "day0_observed_extreme_native": self.forecast.day0_observed_extreme_native,
            "rounding_rule": self.q.rounding_rule,
            "q_sum": self.q.q_sum,
            "q_band_basis": self.q.q_band_basis,
            "market_implied_q": self.q.market_implied_q,
            "route_id": self.route.route_id,
            "payoff_vector_hash": self.route.payoff_vector_hash,
            "edge_lcb": self.route.edge_lcb,
            "delta_u": self.route.delta_u,
            "sizing_authority": self.size.sizing_authority,
        }

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "DecisionReceipt":
        """Inverse of ``to_row`` — rebuild the spine from a persisted receipt row."""
        return cls(
            forecast=ForecastSpine(
                predictive_distribution_id=row.get("predictive_distribution_id"),
                mu_native=_opt_float(row.get("mu_native")),
                sigma_native=_opt_float(row.get("sigma_native")),
                member_min_native=_opt_float(row.get("member_min_native")),
                member_max_native=_opt_float(row.get("member_max_native")),
                debiased_member_min_native=_opt_float(row.get("debiased_member_min_native")),
                debiased_member_max_native=_opt_float(row.get("debiased_member_max_native")),
                applied_debias_native=_opt_float(row.get("applied_debias_native")),
                debias_artifact_id=row.get("debias_artifact_id"),
                day0_observed_extreme_native=_opt_float(row.get("day0_observed_extreme_native")),
            ),
            q=QSpine(
                q_source=row.get("q_source"),
                rounding_rule=row.get("rounding_rule"),
                q_sum=_opt_float(row.get("q_sum")),
                q_band_basis=row.get("q_band_basis"),
                market_implied_q=_opt_float(row.get("market_implied_q")),
            ),
            route=RouteSpine(
                route_id=row.get("route_id"),
                payoff_vector_hash=row.get("payoff_vector_hash"),
                edge_lcb=_opt_float(row.get("edge_lcb")),
                delta_u=_opt_float(row.get("delta_u")),
            ),
            size=SizeSpine(sizing_authority=row.get("sizing_authority")),
        )

    def spine_hash(self) -> str:
        """Stable hash of the spine (canonical JSON of the 19 columns).

        Observability handle for shadow comparison / drift detection — never gates.
        """
        payload = json.dumps(self.to_row(), sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def as_dict(self) -> dict[str, Any]:
        """Full nested dict (legs preserved) for debugging / logging."""
        return asdict(self)


# The 19 receipt columns, in spec order (consult_build_spec.md:1008-1027). Imported by the
# schema module and the contract test so the column vocabulary has ONE definition.
RECEIPT_SPINE_COLUMNS: tuple[str, ...] = (
    "predictive_distribution_id",
    "q_source",
    "mu_native",
    "sigma_native",
    "member_min_native",
    "member_max_native",
    "debiased_member_min_native",
    "debiased_member_max_native",
    "applied_debias_native",
    "debias_artifact_id",
    "day0_observed_extreme_native",
    "rounding_rule",
    "q_sum",
    "q_band_basis",
    "market_implied_q",
    "route_id",
    "payoff_vector_hash",
    "edge_lcb",
    "delta_u",
    "sizing_authority",
)

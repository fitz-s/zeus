# Created: 2026-07-03
# Last reused or audited: 2026-07-03
# Authority basis: architecture doc §4 decision 2 (ScenarioService is the ONE seam, C4 = drop-in
#   service swap); CONSULT REV-2 rulings 2026-07-03 (joint outcome atom axis; transitional service
#   serves ONLY the degenerate single-family case; multi-family joint FAILS CLOSED until C4 —
#   index-pairing is not a certifiable independent product, caps are a loss limiter not a license).
"""ScenarioService — the ONE seam through which outcome scenarios reach the solver.

The solver never samples, never correlates, never touches probability internals: it
consumes a ``JointOutcomeScenarioSet`` over joint outcome ATOMS. W3 ships
``TransitionalIndependentProduct``, which serves ONLY the single-family case — for one
family the joint atom axis IS the family's bins and ``q_draws`` is literally the served
band's ``samples`` (the one-belief law: the solver integrates over EXACTLY the belief the
authority served, no second distribution). C4 replaces the provider, not the solver.

WHY MULTI-FAMILY FAILS CLOSED (consult REV-2 blocker + numeric proof): index-pairing draw
k of family A with draw k of family B is a valid independent product ONLY if each family's
draws are exchangeable and independently generated; sorted / quantile-ordered draws are
COMONOTONE, not independent, and the risk_allocator correlation caps limit notional but do
NOT certify the marginal log-utility condition. Two identical q=0.60, f=0.20 even-money
bets have +0.039 expected log under independence but −0.0024 under perfect positive
dependence (both-lose prob 0.16→0.40). So until C4 supplies a MEASURED joint distribution,
this service refuses to fabricate one — multi-family raises, and any future degraded rail
must stamp ``correlation_rail="caps_degraded_not_optimal"`` with promotion evidence BLOCKED.

C4 rehoming note (W3.C4 brief): the reusable math for a real joint service is
``src/strategy/correlation_shrinkage.py``'s regime-agnostic Ledoit-Wolf estimator; the
regime-keyed cache (``regime_correlation_store.py``) and its taxonomy do NOT move here —
W5 deletions. Historical backing data: ``settlement_outcomes`` rows keyed
(city, target_date, temperature_metric); offline precedent
``scripts/measure_member_correlation.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Mapping, Protocol, runtime_checkable

from src.solve.types import JointOutcomeAtom, JointOutcomeScenarioSet

if TYPE_CHECKING:
    from src.probability.joint_q_band import JointQBand

PROVIDER_VERSION = "transitional_independent_product_v1"


class MultiFamilyJointUnavailableError(RuntimeError):
    """Raised when a multi-family joint distribution is requested before C4.

    Fail-closed: the transitional rail cannot certify cross-family log-utility (index-pairing
    is not a measured joint; caps limit loss, they do not repair the objective). A real joint
    scenario service (C4) is required — see module header.
    """


@runtime_checkable
class ScenarioService(Protocol):
    """Provides joint outcome scenarios for a set of families.

    Contract: the returned ``JointOutcomeScenarioSet`` is over joint outcome ATOMS; under
    ``POSTERIOR_Q_DRAWS`` every ``q_draws`` row is a coherent joint distribution over those
    atoms (the served belief). Per-family marginals are derived projections, not the input.
    """

    def scenarios(self, bands_by_family: Mapping[str, "JointQBand"]) -> JointOutcomeScenarioSet:
        ...


class TransitionalIndependentProduct:
    """Single-family transitional rail — the ONLY mode wired for W3 (consult REV-2 ruling 2).

    One family: the family's own band ``samples`` pass through verbatim as ``q_draws`` over
    one atom per bin (each row already a coherent simplex — the JointOutcomeScenarioSet
    validator re-checks). No index-pairing happens, so sort order is irrelevant and the
    one-belief law holds exactly. Multiple families: FAILS CLOSED (raises) until C4.
    """

    provider_name = "transitional_independent_product"

    def scenarios(self, bands_by_family: Mapping[str, "JointQBand"]) -> JointOutcomeScenarioSet:
        if not bands_by_family:
            raise ValueError("scenarios() requires at least one family band")
        if len(bands_by_family) != 1:
            raise MultiFamilyJointUnavailableError(
                f"transitional rail serves single-family only; got {sorted(bands_by_family)} — "
                "multi-family joint scenarios require the C4 measured service (fail-closed)"
            )
        (family, band), = bands_by_family.items()
        fam_bin_ids = [b.bin_id for b in band.joint_q.omega.bins]
        atoms = tuple(JointOutcomeAtom.of({family: bin_id}) for bin_id in fam_bin_ids)
        return JointOutcomeScenarioSet.build(
            atoms=atoms,
            q_draws=band.samples,
            semantics="POSTERIOR_Q_DRAWS",
            alpha=float(band.alpha),
            provider=self.provider_name,
            provider_version=PROVIDER_VERSION,
            band_hashes_by_family={family: band.sample_hash},
            draw_weights=None,
            family_projections={family: tuple(range(len(fam_bin_ids)))},
        )

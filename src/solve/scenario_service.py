# Created: 2026-07-03
# Last reused or audited: 2026-07-03
# Authority basis: architecture doc §4 decision 2 (transitional rail ACCEPTED, ScenarioService
#   interface from day one, C4 = drop-in service swap, not a solver rewrite).
"""ScenarioService — the ONE seam through which outcome scenarios reach the solver.

The solver never samples, never correlates, never touches probability internals: it
consumes a ``ScenarioSet``. W3 ships ``TransitionalIndependentProduct`` (per-family
independent product measure — for a single family this is literally the family's own
``JointQBand.samples``). C4 replaces the provider, not the solver.

C4 rehoming note (W3.C4 brief): the reusable math for a real joint service is
``src/strategy/correlation_shrinkage.py``'s regime-agnostic Ledoit-Wolf estimator;
the regime-keyed cache (``regime_correlation_store.py``) and its taxonomy do NOT move
here — they are W5 deletions. Historical backing data: ``settlement_outcomes`` rows
keyed (city, target_date, temperature_metric); offline precedent
``scripts/measure_member_correlation.py``.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Mapping, Protocol, runtime_checkable

from src.solve.types import ScenarioSet

if TYPE_CHECKING:
    from src.probability.joint_q_band import JointQBand


@runtime_checkable
class ScenarioService(Protocol):
    """Provides joint outcome scenarios for a set of families.

    Contract: ``samples`` rows are coherent joint outcomes — within each family's
    slice every row is a simplex point over that family's bins (JointQBand invariant);
    across families, coherence is the provider's promise (independent product for the
    transitional impl; measured joint structure for C4).
    """

    def scenarios(self, bands_by_family: Mapping[str, "JointQBand"]) -> ScenarioSet:
        ...


class TransitionalIndependentProduct:
    """Per-family independent product measure — the W3 transitional rail.

    Single family (the W3 case): the family's own band samples pass through verbatim,
    so the solver integrates over EXACTLY the belief the authority served (no second
    distribution — the same one-belief law the spine bridge enforces).

    Multiple families: rows are joined index-wise WITHOUT reordering (draw k of family
    A pairs with draw k of family B). Because each family's draws are exchangeable and
    independently generated, index-pairing IS the independent product up to Monte-Carlo
    error, with no combinatorial blow-up. Cross-family risk control stays with the
    risk_allocator caps (correlation_rail="caps") until C4.
    """

    provider_name = "transitional_independent_product"

    def scenarios(self, bands_by_family: Mapping[str, "JointQBand"]) -> ScenarioSet:
        if not bands_by_family:
            raise ValueError("scenarios() requires at least one family band")
        families = sorted(bands_by_family)  # deterministic order → stable hash
        n_draws_set = {int(bands_by_family[f].samples.shape[0]) for f in families}
        if len(n_draws_set) != 1:
            raise ValueError(
                f"independent-product join requires equal n_draws across families, got {n_draws_set}"
            )
        import numpy as np  # local: keep module import light

        bin_ids: list[str] = []
        slices: dict[str, tuple[int, int]] = {}
        blocks = []
        cursor = 0
        for fam in families:
            band = bands_by_family[fam]
            fam_bins = [b.bin_id for b in band.joint_q.omega.bins]
            slices[fam] = (cursor, cursor + len(fam_bins))
            cursor += len(fam_bins)
            bin_ids.extend(fam_bins)
            blocks.append(band.samples)
        samples = blocks[0] if len(blocks) == 1 else np.concatenate(blocks, axis=1)
        digest = hashlib.sha256()
        for fam in families:
            digest.update(fam.encode())
            digest.update(bands_by_family[fam].sample_hash.encode())
        return ScenarioSet(
            bin_ids=tuple(bin_ids),
            samples=samples,
            family_slices=slices,
            provider=self.provider_name,
            sample_hash=digest.hexdigest(),
        )

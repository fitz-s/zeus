# Created: 2026-07-03
# Last reused/audited: 2026-07-03
"""Deterministic fixture builders for the W3 solve math-core tests (joint atom axis).

No RNG at solve time; where a test needs a family of inputs it draws them from an
explicitly-seeded generator (payoff_vector band-draw discipline) so the suite is
reproducible byte-for-byte.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Mapping, Sequence

import numpy as np

from src.solve.types import (
    AtomPayoffProjector,
    JointOutcomeAtom,
    JointOutcomeScenarioSet,
    MenuItem,
    SolveMenu,
    WealthStateByAtom,
)

FAMILY = "fam"


def atom_id(bin_id: str, family: str = FAMILY) -> str:
    return JointOutcomeAtom.canonical_id({family: bin_id})


class StubScenarioService:
    """A ScenarioService returning a prebuilt JointOutcomeScenarioSet (decouples solver tests
    from real JointQBand construction — the passthrough is exercised in test_scenario_service)."""

    def __init__(self, scenario_set: JointOutcomeScenarioSet) -> None:
        self._set = scenario_set

    def scenarios(self, bands_by_family) -> JointOutcomeScenarioSet:  # noqa: ANN001 - protocol shape
        return self._set


def scenarios_single_family(
    bins: Sequence[str],
    q_draws: np.ndarray,
    *,
    family: str = FAMILY,
    alpha: float = 0.05,
    provider: str = "transitional_independent_product",
    provider_version: str = "test_v1",
    band_hash: str = "test_band_hash",
) -> JointOutcomeScenarioSet:
    atoms = [JointOutcomeAtom.of({family: b}) for b in bins]
    return JointOutcomeScenarioSet.build(
        atoms=atoms,
        q_draws=np.asarray(q_draws, dtype=np.float64),
        semantics="POSTERIOR_Q_DRAWS",
        alpha=alpha,
        provider=provider,
        provider_version=provider_version,
        band_hashes_by_family={family: band_hash},
        family_projections={family: tuple(range(len(bins)))},
    )


def bands(alpha: float = 0.05) -> Mapping[str, object]:
    """Minimal bands_by_family — the StubScenarioService ignores it (alpha lives on the set)."""
    return {FAMILY: object()}


def buy_item(
    item_id: str,
    win_bin: str,
    cost: float,
    bins: Sequence[str],
    *,
    family: str = FAMILY,
    kind: str = "buy_yes",
    max_units: float = 100000.0,
    executable: bool = True,
    min_tick_size: float = 0.01,
    min_order_size: float = 0.01,
) -> MenuItem:
    """A one-unit Arrow-Debreu buy: net +(1-cost) in win_bin's atom, -cost elsewhere."""
    payoff = {atom_id(b, family): (1.0 - cost if b == win_bin else -cost) for b in bins}
    return MenuItem(
        item_id=item_id,
        kind=kind,  # type: ignore[arg-type]
        family_key=family,
        bin_id=win_bin,
        route=None,
        executable=executable,
        non_executable_reason=None if executable else "NO_DEPTH",
        unit_payoff=AtomPayoffProjector(payoff_by_atom_id=payoff, unit_cost_usd=cost),
        max_units=Decimal(str(max_units)),
        min_tick_size=Decimal(str(min_tick_size)),
        min_order_size=Decimal(str(min_order_size)),
    )


def menu(items, *, family: str = FAMILY, menu_hash: str = "test_menu_hash") -> SolveMenu:
    return SolveMenu(family_key=family, items=tuple(items), menu_hash=menu_hash)


def wealth_state(
    wealth_by_atom: Mapping[str, float],
    cash_usd: float,
    *,
    ledger_snapshot_id: str = "ledger_test",
) -> WealthStateByAtom:
    return WealthStateByAtom(
        atom_ids=tuple(wealth_by_atom),
        wealth_by_atom={k: float(v) for k, v in wealth_by_atom.items()},
        cash_usd=float(cash_usd),
        ledger_snapshot_id=ledger_snapshot_id,
    )


def flat_wealth_state(bins: Sequence[str], cash: float, *, family: str = FAMILY) -> WealthStateByAtom:
    """Cash-only endowment: cash pays in every atom (design's first-class cash bound)."""
    return wealth_state({atom_id(b, family): cash for b in bins}, cash)


def two_bin_q_draws(win_probs: Sequence[float]) -> np.ndarray:
    """Rows [p, 1-p] over a 2-bin (win, lose) atom axis."""
    qs = np.asarray(win_probs, dtype=np.float64)
    return np.column_stack([qs, 1.0 - qs])

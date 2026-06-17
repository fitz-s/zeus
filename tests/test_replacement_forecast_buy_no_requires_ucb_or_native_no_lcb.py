# Created: 2026-06-07
# Last reused or audited: 2026-06-17
# Authority basis: replacement_0_1 q_lcb law after bin-selection S2 / Hidden #3:
#   buy_no q_lcb is the lower tail of (1 - q_yes), represented by 1 - q_ucb_yes
#   and clamped under the NO point. This file was re-audited after live Shanghai
#   2026-06-19 low exposed stale hook-factory wiring that carried a baseline
#   buy_no LCB into a final buy_yes replacement direction.
from dataclasses import dataclass
from types import SimpleNamespace

from src.engine.replacement_forecast_hook_factory import _replacement_q_lcb_for_candidate
from src.types.market import Bin


@dataclass
class _Proof:
    direction: str = "buy_no:bin-a"
    q_lcb_5pct: float = 0.80

    @property
    def candidate(self):
        return SimpleNamespace(bin=Bin(low=10.0, high=10.0, unit="C", label="not-the-key"))


@dataclass
class _Bundle:
    q: dict
    q_lcb: dict | None = None
    q_ucb: dict | None = None
    provenance_json: dict | None = None


def _bundle(**overrides) -> _Bundle:
    payload = {
        "q": {"bin-a": 0.02, "bin-b": 0.98},
        "q_lcb": None,
        "q_ucb": None,
        "provenance_json": {
            "bin_topology": [
                {"bin_id": "bin-a", "lower_c": 10.0, "upper_c": 10.0, "center_c": 10.0},
                {"bin_id": "bin-b", "lower_c": 11.0, "upper_c": 11.0, "center_c": 11.0},
            ],
            "bin_topology_hash": "test",
        },
    }
    payload.update(overrides)
    return _Bundle(**payload)


def test_buy_no_uses_native_no_lcb_when_present() -> None:
    value = _replacement_q_lcb_for_candidate(
        _Proof(),
        replacement_bundle=_bundle(q_lcb={"no:bin-a": 0.61}),
    )

    assert value == 0.61


def test_buy_no_uses_yes_ucb_complement_when_native_no_lcb_absent() -> None:
    """Replacement posterior stores per-bin YES UCB; the native-NO conservative
    bound is 1 - q_ucb_yes, clamped under the NO point."""

    value = _replacement_q_lcb_for_candidate(
        _Proof(),
        replacement_bundle=_bundle(q_ucb={"bin-a": 0.12}),
    )

    assert value == 0.88


def test_buy_no_ignores_yes_ucb_even_with_native_no_lcb_present() -> None:
    """A present q_ucb must not perturb the native-NO bound. The buy_no q_lcb is
    fully determined by the native NO source; the YES side is irrelevant.
    """

    value = _replacement_q_lcb_for_candidate(
        _Proof(),
        replacement_bundle=_bundle(q_lcb={"no:bin-a": 0.61}, q_ucb={"bin-a": 0.12}),
    )

    assert value == 0.61

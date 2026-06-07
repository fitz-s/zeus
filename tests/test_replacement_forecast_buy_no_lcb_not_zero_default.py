from dataclasses import dataclass
from types import SimpleNamespace

from src.engine.replacement_forecast_hook_factory import _replacement_q_lcb_for_candidate
from src.types.market import Bin


@dataclass
class _Proof:
    direction: str = "buy_no:bin-a"
    q_lcb_5pct: float = 0.73

    @property
    def candidate(self):
        return SimpleNamespace(bin=Bin(low=10.0, high=10.0, unit="C", label="human label"))


@dataclass
class _Bundle:
    q: dict
    q_lcb: dict | None = None
    provenance_json: dict | None = None


def _bundle(**overrides) -> _Bundle:
    payload = {
        "q": {"bin-a": 0.02},
        "q_lcb": None,
        "provenance_json": {
            "bin_topology": [
                {"bin_id": "bin-a", "lower_c": 10.0, "upper_c": 10.0, "center_c": 10.0},
            ],
            "bin_topology_hash": "test",
        },
    }
    payload.update(overrides)
    return _Bundle(**payload)


def test_buy_no_missing_replacement_lcb_preserves_baseline_not_zero() -> None:
    value = _replacement_q_lcb_for_candidate(_Proof(), replacement_bundle=_bundle())

    assert value == 0.73


def test_buy_no_without_topology_preserves_baseline_instead_of_label_lookup() -> None:
    value = _replacement_q_lcb_for_candidate(
        _Proof(),
        replacement_bundle=_bundle(provenance_json={"bin_topology_hash": "missing-topology"}),
    )

    assert value == 0.73

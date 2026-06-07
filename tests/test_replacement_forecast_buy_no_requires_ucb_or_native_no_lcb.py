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
        "q": {"bin-a": 0.02},
        "q_lcb": None,
        "q_ucb": None,
        "provenance_json": {
            "bin_topology": [
                {"bin_id": "bin-a", "lower_c": 10.0, "upper_c": 10.0, "center_c": 10.0},
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


def test_buy_no_derives_lcb_only_from_yes_ucb_when_present() -> None:
    value = _replacement_q_lcb_for_candidate(
        _Proof(),
        replacement_bundle=_bundle(q_ucb={"bin-a": 0.12}),
        cap_to_baseline=False,
    )

    assert value == 0.88

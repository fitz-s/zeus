from dataclasses import dataclass

from src.engine.replacement_forecast_hook_factory import _replacement_q_lcb_for_candidate


@dataclass
class _Proof:
    direction: str = "buy_no:bin-a"
    q_lcb_5pct: float = 0.73


@dataclass
class _Bundle:
    q: dict
    q_lcb: dict | None = None


def test_buy_no_missing_replacement_lcb_preserves_baseline_not_zero() -> None:
    value = _replacement_q_lcb_for_candidate(_Proof(), replacement_bundle=_Bundle(q={"bin-a": 0.02}, q_lcb=None))

    assert value == 0.73

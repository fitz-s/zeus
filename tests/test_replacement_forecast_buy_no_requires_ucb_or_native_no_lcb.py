from dataclasses import dataclass

from src.engine.replacement_forecast_hook_factory import _replacement_q_lcb_for_candidate


@dataclass
class _Proof:
    direction: str = "buy_no:bin-a"
    q_lcb_5pct: float = 0.80


@dataclass
class _Bundle:
    q: dict
    q_lcb: dict | None = None
    q_ucb: dict | None = None


def test_buy_no_uses_native_no_lcb_when_present() -> None:
    value = _replacement_q_lcb_for_candidate(
        _Proof(),
        replacement_bundle=_Bundle(q={"bin-a": 0.02}, q_lcb={"no:bin-a": 0.61}),
    )

    assert value == 0.61


def test_buy_no_derives_lcb_only_from_yes_ucb_when_present() -> None:
    value = _replacement_q_lcb_for_candidate(
        _Proof(),
        replacement_bundle=_Bundle(q={"bin-a": 0.02}, q_lcb=None, q_ucb={"bin-a": 0.12}),
        cap_to_baseline=False,
    )

    assert value == 0.88

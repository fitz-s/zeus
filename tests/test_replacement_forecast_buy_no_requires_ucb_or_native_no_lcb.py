# Created: 2026-06-07
# Last reused or audited: 2026-06-08
# Authority basis: commit 16c35e7445 ("Make YES and NO probability authority
#   independent") + FIX-4 (§2). The buy_no q_lcb MUST come from a native NO
#   calibration source; deriving it from a YES complement (1 - q_yes_ucb) is
#   BANNED. Current contract: src/engine/replacement_forecast_hook_factory.py
#   :315-329 (buy_no branch reads ONLY native-NO keys from q_lcb; the
#   1.0 - q_ucb fallback was DELETED), src/strategy/live_inference/live_admission.py
#   :22-24 + :123-125 (YES_UCB_DERIVED removed; never infer YES by complement
#   arithmetic), src/calibration/qlcb_provenance.py:43-46 (closed vocabulary has
#   no YES_UCB_DERIVED). Original (pre-audit) test asserted the banned complement.
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


def test_buy_no_never_derives_lcb_from_yes_ucb_complement() -> None:
    """BAN (16c35e7445 + FIX-4): buy_no must NOT derive its conservative bound
    from a YES complement (1 - q_yes_ucb). With only q_ucb present and NO native
    NO-side q_lcb, the function must fall back to the baseline q_lcb_5pct and must
    NEVER produce the banned complement 1 - q_ucb = 0.88.

    YES and NO probability authority are independent: a NO conservative bound may
    only come from a native NO calibration source (closed vocabulary in
    qlcb_provenance.CALIBRATION_SOURCES). YES_UCB_DERIVED is not expressible by the
    QlcbByDirection carrier and was removed from the allow-list.
    """

    value = _replacement_q_lcb_for_candidate(
        _Proof(),
        replacement_bundle=_bundle(q_ucb={"bin-a": 0.12}),
        cap_to_baseline=False,
    )

    # Must NOT be the banned YES complement.
    assert value != 0.88
    # Must fall back to the baseline conservative bound (no native NO source given).
    assert value == 0.80


def test_buy_no_ignores_yes_ucb_even_with_native_no_lcb_present() -> None:
    """A present q_ucb must not perturb the native-NO bound. The buy_no q_lcb is
    fully determined by the native NO source; the YES side is irrelevant.
    """

    value = _replacement_q_lcb_for_candidate(
        _Proof(),
        replacement_bundle=_bundle(q_lcb={"no:bin-a": 0.61}, q_ucb={"bin-a": 0.12}),
        cap_to_baseline=False,
    )

    assert value == 0.61

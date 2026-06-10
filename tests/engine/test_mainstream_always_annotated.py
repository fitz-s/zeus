# Created: 2026-06-04
# Last reused/audited: 2026-06-04
# Authority basis: Operator directive 2026-06-04 #2 — ALWAYS compute + annotate the
#   mainstream/bias agreement value on every candidate, DECOUPLED from the reference flag
#   (mainstream_agreement_reference_enabled). The number must be SHOWN ("数值显示") without
#   flipping the foreign config. Warm-cache-only read (read_mainstream_point_cached, the
#   STEP-7 off-mutex path); cache cold -> pass=None (unknown), fail-open (candidate still
#   forms — mainstream is display-only and can't block).
"""Relationship test: gate evaluation is UNGATED by the reference flag.

The producer (_canonical_probability_and_fdr_proof) must invoke the mainstream
evaluation regardless of mainstream_agreement_reference_enabled, so the value is
annotated on every receipt. Cache cold -> verdict carries mainstream_available=False
(pass annotated as unknown), never blocks.
"""
from __future__ import annotations

import inspect

import pytest


def test_gate_eval_not_gated_by_reference_flag_in_source():
    """STRUCTURAL: the producer must NOT wrap _evaluate_and_store_mainstream_agreement in
    an `if ...mainstream_agreement_reference_enabled` guard — annotation is unconditional."""
    from src.engine import event_reactor_adapter as era

    src = inspect.getsource(era._canonical_probability_and_fdr_proof)
    assert "_evaluate_and_store_mainstream_agreement" in src, (
        "the canonical proof builder must invoke the mainstream evaluation"
    )
    # The reference flag must not gate the evaluation call. Look at the ~400 chars
    # of context around the eval call site for a reference-flag conditional.
    idx = src.index("_evaluate_and_store_mainstream_agreement")
    window = src[max(0, idx - 400):idx]
    assert "mainstream_agreement_reference_enabled" not in window, (
        "the mainstream evaluation is still gated by mainstream_agreement_reference_enabled; "
        "annotation must be UNCONDITIONAL (decoupled from the foreign reference flag)."
    )


def test_value_annotated_with_reference_flag_unset(monkeypatch):
    """Even with mainstream_agreement_reference_enabled absent/False, the warm cache point
    yields a populated verdict (pass + delta + point + bin_label) in the payload."""
    import numpy as np
    from types import SimpleNamespace
    from unittest.mock import patch
    from src.config import settings
    from src.strategy.market_analysis import MarketAnalysis
    from src.contracts.forecast_sharpness import ForecastSharpnessEvidence
    from src.types.market import Bin
    from src.engine.event_reactor_adapter import _evaluate_and_store_mainstream_agreement

    # Reference flag explicitly OFF — annotation must NOT depend on it.
    monkeypatch.setitem(settings["edli"], "mainstream_agreement_reference_enabled", False)

    bins = [
        Bin(low=None, high=14, unit="C", label="14°C or below"),
        Bin(low=15, high=15, unit="C", label="15°C"),
        Bin(low=16, high=None, unit="C", label="16°C or higher"),
    ]
    members = np.array([15.0, 15.1, 14.9, 15.2, 15.0])
    p = np.array([0.1, 0.7, 0.2])
    analysis = MarketAnalysis(
        forecast_sharpness=ForecastSharpnessEvidence.exempt(unit="C"),
        p_raw=p, p_cal=p, p_market=None, alpha=1.0, bins=bins,
        member_maxes=members, unit="C", precision=1.0,
    )
    event = SimpleNamespace(event_id="evt-annotate-001")
    candidate_stub = SimpleNamespace(condition_id="cond-annotate-1", bin=bins[1])
    family = SimpleNamespace(
        city="Wellington", target_date="2026-06-04", metric="high",
        candidates=[candidate_stub],
    )
    payload: dict = {}
    mainstream_result = {
        "point": 15.8, "unit": "C", "source": "open_meteo_standard_forecast",
        "authority_tier": "mainstream", "fetched_at_utc": "2026-06-04T10:00:00+00:00",
        "latitude": -41.325, "longitude": 174.792, "target_date": "2026-06-04",
    }
    with patch(
        "src.data.mainstream_forecast_source.read_mainstream_point_cached",
        return_value=mainstream_result,
    ):
        _evaluate_and_store_mainstream_agreement(
            event=event, family=family, analysis=analysis, payload=payload,
        )
    assert "_mainstream_agreement_verdicts" in payload, (
        "value not annotated with reference flag OFF — annotation must be decoupled"
    )
    v = payload["_mainstream_agreement_verdicts"][("cond-annotate-1", "buy_yes")]
    assert v["mainstream_agreement_pass"] is True
    assert v["mainstream_point"] == 15.8
    assert v["forecast_delta"] is not None
    assert v["mainstream_bin_label"] is not None


def test_cache_cold_annotates_unknown_fail_open(monkeypatch):
    """Cache cold (read_mainstream_point_cached -> None): the verdict carries
    mainstream_available=False (pass = unknown/fail-closed) but the candidate still
    forms — mainstream is display-only and never blocks."""
    import numpy as np
    from types import SimpleNamespace
    from unittest.mock import patch
    from src.config import settings
    from src.strategy.market_analysis import MarketAnalysis
    from src.contracts.forecast_sharpness import ForecastSharpnessEvidence
    from src.types.market import Bin
    from src.engine.event_reactor_adapter import _evaluate_and_store_mainstream_agreement

    monkeypatch.setitem(settings["edli"], "mainstream_agreement_reference_enabled", False)
    bins = [
        Bin(low=None, high=14, unit="C", label="14°C or below"),
        Bin(low=15, high=15, unit="C", label="15°C"),
        Bin(low=16, high=None, unit="C", label="16°C or higher"),
    ]
    members = np.array([15.0, 15.1, 14.9, 15.2, 15.0])
    p = np.array([0.1, 0.7, 0.2])
    analysis = MarketAnalysis(
        forecast_sharpness=ForecastSharpnessEvidence.exempt(unit="C"),
        p_raw=p, p_cal=p, p_market=None, alpha=1.0, bins=bins,
        member_maxes=members, unit="C", precision=1.0,
    )
    event = SimpleNamespace(event_id="evt-cold-001")
    candidate_stub = SimpleNamespace(condition_id="cond-cold-1", bin=bins[1])
    family = SimpleNamespace(
        city="Wellington", target_date="2026-06-04", metric="high",
        candidates=[candidate_stub],
    )
    payload: dict = {}
    with patch(
        "src.data.mainstream_forecast_source.read_mainstream_point_cached",
        return_value=None,  # cold cache
    ):
        # Must not raise (fail-open) — candidate forms regardless.
        _evaluate_and_store_mainstream_agreement(
            event=event, family=family, analysis=analysis, payload=payload,
        )
    # Verdict still recorded (so the receipt shows "unknown"), but NOT a pass.
    v = payload["_mainstream_agreement_verdicts"][("cond-cold-1", "buy_yes")]
    assert v["mainstream_agreement_pass"] is not True, (
        "cold cache must NOT annotate a PASS (unknown != pass)"
    )

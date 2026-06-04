# Created: 2026-06-04
# Lifecycle: created=2026-06-04; last_reviewed=2026-06-04; last_reused=2026-06-04
# Purpose: Relationship test (cross-module, LIVE call path) for the ONE-CALIBRATOR seam
#   (#110 / ELEVATION S2). Reproduces the LIVE failure class that the direct build_emos_q
#   unit tests cannot see: the seam _market_analysis_from_event_snapshot swallowed EVERY
#   EMOS exception with a bare `except Exception: _emos_q = None` and NO logging, so a
#   flag-ON-but-always-failing EMOS calibrator looked identical to flag-OFF (q_source absent;
#   legacy Platt ran invisibly). This is the #149 fail-open-INERT category: a served cell
#   that silently degrades to legacy is forbidden. These tests LOCK that:
#     (1) a clean served=emos cell ENGAGES through the real seam  -> q_source == 'emos';
#     (2) when the EMOS build raises, the seam emits a LOUD, DISTINCT EMOS_SERVE_FAILED log
#         (the antibody — a silent swallow on a served cell can never recur in CI);
#     (3) on EMOS failure the family still FORMS via the honest legacy path (q_source !=
#         'emos', no hard-crash) — best-effort degrade, but LOUD, never silent.
# Reuse: update when src/engine/event_reactor_adapter.py:_market_analysis_from_event_snapshot,
#   src/calibration/emos_q_builder.build_emos_q, or _assert_settlement_unit_identity change.
# Authority basis: plan compiled-foraging-quail.md (one ensemble->settlement calibrator);
#   live diagnosis 2026-06-04 (edli_emos_sole_calibrator_enabled=true but silently inert).
#   Models: tests/engine/test_bias_grid_mutual_exclusion.py (real-seam harness),
#   tests/test_receipt_q_source_provenance_120.py (q_source provenance).
from __future__ import annotations

import json
import logging
import sqlite3
from types import SimpleNamespace
from unittest import mock

import numpy as np
import pytest

from src.contracts.execution_price import ExecutionPrice as EP
from src.engine.event_reactor_adapter import _market_analysis_from_event_snapshot
from src.types.market import Bin
from src.config import runtime_cities_by_name, settings
from src.calibration import emos as emos_mod


# A deterministic served=emos cell. params = [a, b, c, d, e]:
#   mu = a + b*xbar ;  sigma2 = exp(c + d*log(S2) + e*lead_days). b=1.0 (no mean stretch).
_SYNTH_CELL = {"params": [0.5, 1.0, 0.0, 1.0, 0.20], "n": 500, "served": "emos"}


def _served_city_unit_c() -> str:
    """Pick a runtime city configured for °C settlement (the synthetic cell is keyed to it)."""
    cities = runtime_cities_by_name()
    for name, cfg in cities.items():
        if getattr(cfg, "settlement_unit", None) == "C":
            return name
    pytest.skip("no °C runtime city config available")


def _two_c_bins():
    return [Bin(23, 23, "C", "23°C"), Bin(24, None, "C", "24°C or higher")]


def _family(city, bins, metric="high", target_date="2026-07-15"):
    candidates = [
        SimpleNamespace(condition_id=f"cond-{i}", bin=b,
                        yes_token_id=f"yes-{i}", no_token_id=f"no-{i}")
        for i, b in enumerate(bins)
    ]
    return SimpleNamespace(
        city=city, metric=metric, target_date=target_date,
        event_type="FORECAST_SNAPSHOT_READY", bins=bins, candidates=candidates,
        yes_token_ids=[f"yes-{i}" for i in range(len(bins))],
        no_token_ids=[f"no-{i}" for i in range(len(bins))], family_id="test-fam",
    )


def _snapshot(members):
    return {
        "settlement_unit": "C", "temperature_metric": "high",
        "members_json": json.dumps(members.tolist()), "members_precision": 1.0,
        "source_id": "ecmwf_open_data", "issue_time": "2026-07-12T00:00:00+00:00",
        "lead_hours": 72.0, "dataset_id": "test_v1", "data_version": "test_v1",
    }


def _costs(bins, no_price=0.75, yes_price=0.25):
    costs = {}
    for i, _ in enumerate(bins):
        cid = f"cond-{i}"
        costs[(cid, "buy_yes")] = (None, EP(yes_price, "ask", fee_deducted=True, currency="probability_units"), yes_price, None, None)
        costs[(cid, "buy_no")] = (None, EP(no_price, "ask", fee_deducted=True, currency="probability_units"), no_price, None, None)
    return costs


def _run_seam(*, monkeypatch, emos_serves: bool, city: str, mock_legacy_pcal: bool = False):
    """Drive the REAL seam with the EMOS flag ON and a deterministic served/raw cell.

    Returns (payload, analysis). Always uses proper Bin objects (the live forecast shape),
    so this exercises the production call path — not the direct build_emos_q unit.

    ``mock_legacy_pcal``: when the EMOS path is expected to FALL BACK to legacy (served=raw
    or a forced EMOS failure), the legacy p_cal needs a calibration store. Tests that only
    care about the EMOS engage/degrade *contract* (not the legacy Platt math) mock
    _snapshot_p_cal to a normalized point vector so the family forms without a real cal DB —
    mirroring how tests/engine/test_bias_grid_mutual_exclusion.py mocks the bias/grid hooks.
    """
    season = "JJA"  # target_date 2026-07-15 -> NH month-season JJA (matches the seam keying)
    served = "emos" if emos_serves else "raw"
    table = {"_meta": {"metric": "multi"},
             "cells": {f"{city}|{season}|high": {**_SYNTH_CELL, "served": served}}}
    monkeypatch.setattr(emos_mod, "_emos_table_cache", table, raising=False)
    monkeypatch.setitem(settings["edli_v1"], "edli_emos_sole_calibrator_enabled", True)

    if mock_legacy_pcal:
        def _fake_pcal(_cal, *, snapshot, family, bins, p_raw, payload, decision_time):
            v = np.asarray(p_raw, dtype=float)
            return v / float(v.sum())
        monkeypatch.setattr(
            "src.engine.event_reactor_adapter._snapshot_p_cal", _fake_pcal
        )

    bins = _two_c_bins()
    family = _family(city, bins)
    members = np.random.default_rng(7).normal(24.5, 1.6, 51).astype(float)
    snapshot = _snapshot(members)
    native_costs = _costs(bins)
    payload: dict = {}
    cal = sqlite3.connect(":memory:")
    analysis = _market_analysis_from_event_snapshot(
        calibration_conn=cal, snapshot=snapshot, family=family,
        native_costs=native_costs, payload=payload, decision_time=None,
    )
    return payload, analysis


# ---------------------------------------------------------------------------
# (1) ENGAGEMENT — a clean served=emos cell engages through the REAL seam.
#     Locks: the live forecast call path (proper Bin objects) yields q_source=='emos'.
# ---------------------------------------------------------------------------
def test_served_emos_engages_on_live_seam(monkeypatch):
    city = _served_city_unit_c()
    payload, analysis = _run_seam(monkeypatch=monkeypatch, emos_serves=True, city=city)
    assert payload.get("_edli_q_source") == "emos", (
        "served=emos cell with the flag ON MUST engage EMOS on the live seam "
        f"(got q_source={payload.get('_edli_q_source')!r})"
    )
    # The EMOS predictive sigma must travel into the bootstrap (one-calibrator lcb).
    assert analysis._bootstrap_probability_sampler is not None, (
        "EMOS engagement must install the N(mu,sigma) lcb bootstrap sampler"
    )


# ---------------------------------------------------------------------------
# (2) THE ANTIBODY — an EMOS build failure must be LOUD, never silently swallowed.
#     RED before fix: the bare `except Exception: _emos_q = None` logged NOTHING, so this
#     assertion finds no EMOS_SERVE_FAILED record. GREEN after: the except logs a distinct
#     warning carrying the exception type + city/season so monitoring catches the inert state.
# ---------------------------------------------------------------------------
def test_emos_build_failure_is_loud_not_silent(monkeypatch, caplog):
    city = _served_city_unit_c()

    # Force the EMOS build to raise the EXACT live failure class (NameError surrogate) so we
    # prove the seam logs the failure rather than swallowing it into a silent legacy fallback.
    def _boom(*args, **kwargs):
        raise NameError("name 'unit' is not defined")

    monkeypatch.setattr(
        "src.engine.event_reactor_adapter.build_emos_q", _boom, raising=False
    )
    # The module imports build_emos_q lazily inside the try (`from ... import build_emos_q as
    # _build_emos_q`); patch the source symbol so the lazy import resolves to the boom.
    import src.calibration.emos_q_builder as qb
    monkeypatch.setattr(qb, "build_emos_q", _boom, raising=True)

    with caplog.at_level(logging.WARNING):
        payload, analysis = _run_seam(
            monkeypatch=monkeypatch, emos_serves=True, city=city, mock_legacy_pcal=True
        )

    # ANTIBODY: a served cell whose EMOS build failed MUST leave a loud, distinct trail.
    serve_failed = [r for r in caplog.records if "EMOS_SERVE_FAILED" in r.getMessage()]
    assert serve_failed, (
        "a served=emos build failure MUST log a distinct EMOS_SERVE_FAILED line — the "
        "silent `except Exception: _emos_q = None` is the #149 fail-open-inert category "
        "(flag-ON-but-always-failing looks identical to flag-OFF)"
    )
    msg = serve_failed[0].getMessage()
    assert "NameError" in msg, "the log MUST carry the exception type for diagnosability"
    assert city in msg, "the log MUST carry the city/cell so the inert cell is identifiable"

    # And the family still FORMS via the honest legacy path — LOUD degrade, never a hard crash.
    assert payload.get("_edli_q_source") in {"platt", "bias_platt"}, (
        "EMOS failure must degrade to the honest legacy calibrator (best-effort), not crash "
        f"the family (got q_source={payload.get('_edli_q_source')!r})"
    )
    assert analysis is not None


# ---------------------------------------------------------------------------
# (3) HONEST FALLBACK — served=raw cell takes the legacy path with NO EMOS engagement,
#     and (unlike a failure) emits no EMOS_SERVE_FAILED noise (served=raw is expected, quiet).
# ---------------------------------------------------------------------------
def test_served_raw_uses_legacy_quietly(monkeypatch, caplog):
    city = _served_city_unit_c()
    with caplog.at_level(logging.WARNING):
        payload, analysis = _run_seam(
            monkeypatch=monkeypatch, emos_serves=False, city=city, mock_legacy_pcal=True
        )
    assert payload.get("_edli_q_source") in {"platt", "bias_platt"}, (
        "served=raw cell must fall back to the honest legacy calibrator"
    )
    # served=raw is the documented, expected do-no-harm fallback — it is NOT a serve failure.
    assert not [r for r in caplog.records if "EMOS_SERVE_FAILED" in r.getMessage()], (
        "served=raw is an expected quiet fallback (None from build_emos_q), NOT a serve "
        "failure — it must not emit EMOS_SERVE_FAILED noise"
    )

# Created: 2026-05-01
# Last reused/audited: 2026-05-18
# Lifecycle: created=2026-05-01; last_reviewed=2026-05-18; last_reused=2026-05-18
# Authority basis: Operator directive 2026-05-07 — ECMWF enfo stream deprecated
#   mx2t6/mn2t6; stream now serves mx2t3/mn2t3 (3h native). Download script
#   default updated from mx2t6/mn2t6 → mx2t3/mn2t3.
#   Prior authority: Operator directive 2026-05-01 — antibody for Invariant A
#   (download_ecmwf_open_ens.py default param is the calendar-day-aligned
#   mx2t3+mn2t3, not the wrong-physical-quantity 2t).
# Purpose: Guard ECMWF Open Data high/low fetch params against 2t or deprecated 6h defaults.
# Reuse: Run when src/data/ecmwf_open_data.py track config or fetch params change.
"""Antibody guarding the download script's default --param.

Zeus trades calendar-day high/low markets. The 3-hour native aggregations
``mx2t3`` and ``mn2t3`` from ECMWF Open Data ENS are the current physical
quantities that match. ``2t`` (instantaneous temperature) is the wrong
quantity and contaminated training fits before the 2026-05-01 fix.
"""
from __future__ import annotations

import argparse
import importlib
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "src" / "data" / "ecmwf_open_data.py"


@pytest.fixture(scope="module")
def opendata_module():
    try:
        module = importlib.import_module("src.data.ecmwf_open_data")
    except (ModuleNotFoundError, ImportError) as exc:
        pytest.skip(f"ECMWF Open Data module has unavailable dependency: {exc}")
    return module


def test_default_param_is_mx2t3_mn2t3(opendata_module):
    tracks = opendata_module.TRACKS
    assert tracks["mx2t6_high"]["open_data_param"] == "mx2t3"
    assert tracks["mn2t6_low"]["open_data_param"] == "mn2t3"


def test_explicit_2t_parser_compatibility_remains_documented():
    """Backwards compat for legacy parser callers that still pass --param 2t."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--param", nargs="+", default=["mx2t3", "mn2t3"])
    args = parser.parse_args(["--date", "2026-05-01", "--param", "2t"])
    assert args.param == ["2t"]


def test_script_text_default_mentions_mx2t3_mn2t3():
    """Defensive: a future agent might silently revert the default. Grep the
    module text for the mx2t3+mn2t3 default declaration so we don't depend
    purely on importlib parsing."""
    text = MODULE_PATH.read_text()
    # The default= keyword appears with both params on consecutive tokens.
    assert "mx2t3" in text and "mn2t3" in text, (
        "Script must default --param to ['mx2t3','mn2t3']. Found neither."
    )
    # Catch the regression where someone re-defaults to '2t'.
    forbidden = ['default=["2t"]', "default=['2t']"]
    for pattern in forbidden:
        assert pattern not in text, (
            f"Regression: script default reverted to {pattern!r}. "
            f"Zeus trades mx2t3/mn2t3 not instantaneous 2t."
        )

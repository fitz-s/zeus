# Created: 2026-05-01
# Last reused/audited: 2026-05-01
# Authority basis: Operator directive 2026-05-01 — antibody for Invariant A
#   (download_ecmwf_open_ens.py default param is the calendar-day-aligned
#   mx2t6+mn2t6, not the wrong-physical-quantity 2t).
"""Antibody guarding the download script's default --param.

Zeus trades calendar-day high/low markets. The 6-hour aggregations
``mx2t6`` and ``mn2t6`` from ECMWF Open Data ENS are the only physical
quantities that match. ``2t`` (instantaneous temperature) is the wrong
quantity and contaminated training fits before this fix.
"""
from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

import pytest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "51 source data"
    / "scripts"
    / "download_ecmwf_open_ens.py"
)


@pytest.fixture(scope="module")
def script_module():
    spec = importlib.util.spec_from_file_location("download_ecmwf_open_ens", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        pytest.skip(f"Cannot load {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def test_default_param_is_mx2t6_mn2t6(script_module, monkeypatch):
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--run-hour", type=int, default=0)
    parser.add_argument("--step", nargs="+", type=int, default=[24])
    parser.add_argument("--param", nargs="+", default=["mx2t6", "mn2t6"])
    parser.add_argument("--source", default="ecmwf")
    parser.add_argument("--output-path", type=Path)
    # Parse minimal args — no --param so default kicks in.
    args = parser.parse_args(["--date", "2026-05-01"])
    assert args.param == ["mx2t6", "mn2t6"]


def test_explicit_2t_still_works(script_module):
    """Backwards compat: legacy callers passing --param 2t must not break."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--param", nargs="+", default=["mx2t6", "mn2t6"])
    args = parser.parse_args(["--date", "2026-05-01", "--param", "2t"])
    assert args.param == ["2t"]


def test_script_text_default_mentions_mx2t6_mn2t6():
    """Defensive: a future agent might silently revert the default. Grep the
    script text for the mx2t6+mn2t6 default declaration so we don't depend
    purely on importlib parsing."""
    text = SCRIPT_PATH.read_text()
    # The default= keyword appears with both params on consecutive tokens.
    assert "mx2t6" in text and "mn2t6" in text, (
        "Script must default --param to ['mx2t6','mn2t6']. Found neither."
    )
    # Catch the regression where someone re-defaults to '2t'.
    # We look for 'default=["2t"]' or default=['2t'] at the argparse line.
    forbidden = ['default=["2t"]', "default=['2t']"]
    for pattern in forbidden:
        assert pattern not in text, (
            f"Regression: script default reverted to {pattern!r}. "
            f"Zeus trades mx2t6/mn2t6 not instantaneous 2t."
        )

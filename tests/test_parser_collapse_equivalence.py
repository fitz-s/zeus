# Created: 2026-06-13
# Last reused or audited: 2026-06-13
# Authority basis: tangle-simplify audit (.omc/research/tangle_simplify_deepmap_2026-06-13.md) dimension #7 (duplicated
#   clone) + #13 (timezone provenance) + #6 (naming collision); relationship-test guard for the parser/clamp families.
#
# RELATIONSHIP TEST (cross-module invariant), NOT a feature test. It encodes what the empirical 2026-06-13 probe proved:
# the same-named helpers are NOT interchangeable, so they must NOT be blindly collapsed into one canonical function.
#
#   FINDING 1 (ISO -> datetime): on an UNAMBIGUOUS input (Z or explicit offset) all copies agree (positive invariant,
#     asserted below). On a NAIVE input (no tz) they SPLIT: some return a naive datetime, some assume UTC. That split is
#     the timezone-provenance hazard (#13). A future src/utils collapse MUST first decide naive-tz semantics and fix each
#     call site; this test pins the current split so an accidental unification fails loudly here.
#   FINDING 2 (_clamp_probability): replay floors at 1e-12 (log-safe), benchmark_suite clamps to 0.0 (plain [0,1] for an
#     fmean of fill-probabilities — NOT a log/Kelly input, NOT a live bug). Two legitimately-different contracts sharing
#     ONE name = a naming collision to RENAME, never to merge. This test pins both contracts.
#
# Adds NO gate/flag/switch/artifact — it only observes existing functions and locks their current, documented contracts.

import importlib
import math

import pytest


def _load(modpath: str, fnname: str):
    try:
        mod = importlib.import_module(modpath)
    except Exception:  # noqa: BLE001 — an un-importable copy is a skipped data point, not a failure
        return None
    return getattr(mod, fnname, None)


# Copies that, on a NAIVE iso string, return a NAIVE datetime (tzinfo is None).
ISO_NAIVE_RETURNING = [
    ("src.execution.exit_lifecycle", "_parse_iso"),
    ("src.execution.fill_tracker", "_parse_iso"),
    ("src.control.freshness_gate", "_parse_iso"),
]
# Copies that, on a NAIVE iso string, ASSUME UTC and return a tz-aware datetime.
ISO_UTC_ASSUMING = [
    ("src.data.collection_frontier", "_parse_iso"),
    ("src.state.calibration_observation", "_parse_iso_to_dt"),
    ("src.state.db", "_parse_iso_timestamp"),
    ("src.state.learning_loop_observation", "_parse_iso_to_dt"),
    ("src.state.portfolio", "_parse_iso_datetime"),
]

UNAMBIGUOUS_UTC = "2026-06-13T12:34:56Z"
NAIVE = "2026-06-13T12:34:56"


def _instant(dt):
    return dt.astimezone().timestamp()


def test_iso_copies_agree_on_unambiguous_utc_input():
    """Positive invariant: every importable copy maps an explicit-UTC string to the SAME instant."""
    instants = {}
    for mp, fn in ISO_NAIVE_RETURNING + ISO_UTC_ASSUMING:
        f = _load(mp, fn)
        if f is None:
            continue
        out = f(UNAMBIGUOUS_UTC)
        assert out is not None and out.tzinfo is not None, f"{mp}.{fn} lost tz on explicit-UTC input"
        instants[f"{mp}.{fn}"] = _instant(out)
    assert instants, "no copies importable"
    distinct = set(round(v, 6) for v in instants.values())
    assert len(distinct) == 1, f"copies disagree on explicit-UTC instant: {instants}"


def test_iso_naive_handling_split_is_pinned():
    """Characterization guard: the naive-tz SPLIT is real and intentional-until-resolved. If a collapse unifies these,
    this fails — forcing the provenance decision instead of a silent behavior change."""
    for mp, fn in ISO_NAIVE_RETURNING:
        f = _load(mp, fn)
        if f is None:
            continue
        out = f(NAIVE)
        assert out is not None and out.tzinfo is None, (
            f"{mp}.{fn} CHANGED: was naive-returning, now {out!r}. Resolve naive-tz provenance before collapsing.")
    for mp, fn in ISO_UTC_ASSUMING:
        f = _load(mp, fn)
        if f is None:
            continue
        out = f(NAIVE)
        assert out is not None and out.tzinfo is not None, (
            f"{mp}.{fn} CHANGED: was UTC-assuming, now {out!r}. Resolve naive-tz provenance before collapsing.")


def test_clamp_probability_contracts_are_distinct_by_design():
    """Pin the two different contracts so the naming collision is not 'fixed' by merging them."""
    replay = _load("src.engine.replay", "_clamp_probability")
    bench = _load("src.strategy.benchmark_suite", "_clamp_probability")
    if replay is None or bench is None:
        pytest.skip("clamp copies not both importable")
    # replay = log-safe floor; must keep clamp(0) strictly positive.
    assert replay(0.0) > 0.0 and math.isclose(replay(0.0), 1e-12, abs_tol=1e-15), (
        f"replay._clamp_probability log-safe floor CHANGED: clamp(0.0)={replay(0.0)!r}")
    # benchmark = plain [0,1] clamp for fill-prob averaging; clamp(0)=0.0 is correct HERE.
    assert bench(0.0) == 0.0, f"benchmark_suite._clamp_probability contract CHANGED: clamp(0.0)={bench(0.0)!r}"
    # They are intentionally different — assert the difference is real (rename, do not merge).
    assert replay(0.0) != bench(0.0), "the two _clamp_probability copies are no longer distinct — was the collision merged?"


# ---------------------------------------------------------------------------
# Family 3: _finite_float_or_none  (collapse CANDIDATE — characterize before merging)
# ---------------------------------------------------------------------------
FINITE_FLOAT_COPIES = [
    ("src.state.db", "_finite_float_or_none"),
    ("src.riskguard.riskguard", "_finite_float_or_none"),
    ("src.engine.cycle_runtime", "_finite_float_or_none"),
]

FINITE_FIXTURE = [None, "", "1.5", 1.5, 0, "0", "nan", float("nan"), float("inf"),
                  float("-inf"), "inf", "abc", "1e3", "  2.0  ", True]


def _finite_norm(fn, value):
    try:
        out = fn(value)
    except Exception as exc:  # noqa: BLE001 — characterizing divergent error behavior on purpose
        return ("raised", type(exc).__name__)
    if out is None:
        return ("none",)
    try:
        if isinstance(out, float) and math.isnan(out):
            return ("nan",)
        return ("val", round(float(out), 9))
    except Exception:  # noqa: BLE001
        return ("other", repr(out))


def test_finite_float_or_none_copies_agree():
    """If all copies agree on the full fixture, this family IS a safe mechanical collapse candidate. If not, it joins
    the do-not-blindly-collapse list with the exact divergence map."""
    available = [(mp, fn, _load(mp, fn)) for mp, fn in FINITE_FLOAT_COPIES]
    available = [(mp, fn, f) for mp, fn, f in available if f is not None]
    if len(available) < 2:
        pytest.skip("fewer than 2 _finite_float_or_none copies importable")
    divergences = []
    for value in FINITE_FIXTURE:
        results = {f"{mp}.{fn}": _finite_norm(f, value) for mp, fn, f in available}
        if len(set(results.values())) > 1:
            divergences.append((repr(value), results))
    if divergences:
        lines = ["_finite_float_or_none copies DIVERGE — NOT a safe mechanical collapse:"]
        for value, results in divergences:
            lines.append(f"  input {value}:")
            for name, norm in sorted(results.items()):
                lines.append(f"      {name} -> {norm}")
        pytest.fail("\n".join(lines))

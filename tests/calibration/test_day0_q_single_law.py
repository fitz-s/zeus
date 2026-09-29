# Created: 2026-09-29
# Last reused or audited: 2026-09-29
# Authority basis: docs/authority/replacement_final_form_2026_06_09.md §1e "Day0
#   diurnal-residual mixture" — ONE Day0 q law for every city, every producer.
"""Structural antibody: every served Day0 q goes through the one mixture operator.

Day0 q is produced from the remaining-path carrier by three finalizers — the
materializer's posterior payload, the reactor's market analysis (point and every
bootstrap row) and the replay corpus — and each must pass its simplex through
``Day0DiurnalMixture.apply``. A function that builds a Day0 simplex from a carrier
primitive may exist only if it is one of those finalizers or feeds one of them.
A second Day0 q engine (the retired Day0Router signal classes) may not be reachable
from any runtime module.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"

MIXTURE_MODULE = "src/calibration/day0_diurnal_residual.py"

# Day0 carrier primitives: calling one builds a Day0 bin simplex (or its draws).
CARRIER_PRIMITIVES = frozenset({
    "_day0_remaining_p_raw_vector",
    "_rebuild_decision_time_day0_carrier",
    "_rebuild_held_day0_shared_carrier",
    "_day0_shared_carrier_q_shape",
    "_Day0CarrierRowSampler",
    "_make_day0_bootstrap_sampler",
})

# Finalizer -> the mixture seam it must call. Each is where a Day0 q becomes served.
FINALIZERS = {
    ("src/data/replacement_forecast_materializer.py", "_compute_posterior_payload"):
        "_apply_day0_diurnal_mixture",
    ("src/engine/event_reactor_adapter.py", "_market_analysis_from_event_snapshot"):
        "_Day0DiurnalMixedSampler",
    ("src/calibration/probability_replay_corpus.py", "replay_day0_state"):
        "apply",
}

# Non-finalizer functions allowed to call a carrier primitive, each proven to feed a
# finalizer (named on the right) rather than to serve q itself.
FEEDERS = {
    ("src/engine/event_reactor_adapter.py", "_snapshot_p_raw"):
        "_market_analysis_from_event_snapshot",
    ("src/engine/event_reactor_adapter.py", "_day0_remaining_day_members"):
        "_market_analysis_from_event_snapshot",
}

RETIRED_DAY0_ENGINES = (
    "src.signal.day0_router",
    "src.signal.day0_signal",
    "src.signal.day0_high_signal",
    "src.signal.day0_low_nowcast_signal",
    "src.signal.day0_high_nowcast_signal",
)


def _modules():
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        yield rel, ast.parse(path.read_text(encoding="utf-8"))


def _called_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            func = sub.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
                if isinstance(func.value, ast.Name):
                    names.add(func.value.id)
    return names


def _top_level_functions(tree: ast.Module):
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node.name, node
        elif isinstance(node, ast.ClassDef):
            for inner in node.body:
                if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    yield f"{node.name}.{inner.name}", inner


def _carrier_consumers() -> set[tuple[str, str]]:
    """Every function outside a primitive's own class that calls a carrier primitive."""
    found = set()
    for rel, tree in _modules():
        for name, fn in _top_level_functions(tree):
            if name.split(".")[0] in CARRIER_PRIMITIVES:
                continue
            if _called_names(fn) & CARRIER_PRIMITIVES:
                found.add((rel, name))
    return found


def test_every_carrier_consumer_is_a_mixed_finalizer_or_feeds_one() -> None:
    consumers = _carrier_consumers()
    # Non-vacuity: the known finalizers and feeders are found by this scan.
    assert set(FEEDERS) | set(FINALIZERS) <= consumers
    offenders = sorted(consumers - set(FINALIZERS) - set(FEEDERS))
    assert offenders == [], (
        "Day0 simplex built outside the mixed finalizers; route it through "
        f"Day0DiurnalMixture.apply: {offenders}"
    )


def test_every_finalizer_calls_the_mixture_and_every_feeder_reaches_its_finalizer() -> None:
    trees = dict(_modules())
    for (rel, name), seam in FINALIZERS.items():
        fn = dict(_top_level_functions(trees[rel]))[name]
        assert seam in _called_names(fn), f"{rel}::{name} does not call {seam}"
    for (rel, name), consumer in FEEDERS.items():
        functions = dict(_top_level_functions(trees[rel]))
        assert name in functions, f"{rel}::{name} vanished; drop it from FEEDERS"
        assert name in _called_names(functions[consumer]), (
            f"{rel}::{name} no longer feeds {consumer}"
        )


def test_the_mixture_seams_resolve_to_the_one_operator() -> None:
    """Each seam applies ``Day0DiurnalMixture.apply`` from the one lookup."""
    trees = dict(_modules())
    materializer = dict(_top_level_functions(trees["src/data/replacement_forecast_materializer.py"]))
    reactor = dict(_top_level_functions(trees["src/engine/event_reactor_adapter.py"]))
    assert "apply" in _called_names(materializer["_apply_day0_diurnal_mixture"])
    assert "day0_diurnal_mixture" in _called_names(materializer["_day0_diurnal_mixture_for_request"])
    assert "apply" in _called_names(reactor["_Day0DiurnalMixedSampler._mix"])
    assert "day0_diurnal_mixture" in _called_names(reactor["_day0_diurnal_mixture_for_family"])
    assert "_day0_diurnal_mixture_for_family" in _called_names(
        reactor["_market_analysis_from_event_snapshot"]
    )
    lookups = [
        f"{rel}::{name}"
        for rel, tree in _modules()
        if rel != MIXTURE_MODULE
        for name, fn in _top_level_functions(tree)
        if "day0_diurnal_mixture" in _called_names(fn)
    ]
    assert sorted(lookups) == [
        "src/data/replacement_forecast_materializer.py::_day0_diurnal_mixture_for_request",
        "src/engine/event_reactor_adapter.py::_day0_diurnal_mixture_for_family",
    ]


def test_no_runtime_module_reaches_a_retired_day0_q_engine() -> None:
    offenders = []
    for rel, tree in _modules():
        if rel.startswith("src/signal/day0_") and rel.replace("/", ".")[:-3] in RETIRED_DAY0_ENGINES:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in RETIRED_DAY0_ENGINES:
                offenders.append(f"{rel}:{node.lineno}")
            elif isinstance(node, ast.Import):
                offenders += [f"{rel}:{node.lineno}" for a in node.names if a.name in RETIRED_DAY0_ENGINES]
    assert offenders == [], f"a second Day0 q engine is reachable: {offenders}"

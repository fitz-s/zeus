# Created: 2026-06-16
# Last reused or audited: 2026-06-16
# Authority basis: docs/evidence/qkernel_rebuild/fix_spine_source_multimodel_2026-06-16.md
#   (the money-path source-rewire this test is the RED-on-revert smoke for) +
#   src/engine/event_reactor_adapter.py::_spine_multimodel_members_for_event (the new
#   live spine member-envelope accessor) + AGENTS.md probability authority (μ* = T2
#   Bayesian precision fusion over DECORRELATED providers, NOT the ECMWF ensemble) +
#   scripts/qkernel_arm_replay.fresh_members_at_cycle (the validated replay member set
#   the live producer must now reproduce).
"""RED-on-revert smoke for the spine SOURCE rewire (2026-06-16).

THE BUG (fixed by this change): the live ``_generate_candidate_proofs`` Q-KERNEL SPINE
INPUTS block built the forecast member envelope from ``ensemble_snapshots.members_json``
(51 ``ecmwf_ens`` ensemble members) via ``_bound_forecast_snapshot_row_for_spine`` +
``_snapshot_members``. But the strategy-of-record, the de-bias provider, AND the
ARM-replay validation ALL use the MULTI-MODEL DETERMINISTIC fusion source
``raw_model_forecasts`` (~7-13 decorrelated NWP providers). A 213-family settlement
audit found 0/213 ensemble-vs-multimodel member sets equal (mean |Δμ*|=1.14°C), with
the ECMWF-ensemble center systematically colder — the cold-center / failed-de-bias-
transfer / 100%-buy_no-losing-book root cause. The probability authority (AGENTS.md)
mandates ``μ* = T2 Bayesian precision fusion over decorrelated providers``, which is the
``raw_model_forecasts`` source, NEVER the ECMWF ensemble.

THE FIX: the producer now sources its member envelope from ``raw_model_forecasts`` via the
new ``_spine_multimodel_members_for_event`` accessor (causal-cycle-bound from the event's
bound ensemble snapshot, ``source_available_at ≤ decision_time``, latest cycle per model,
°C→native), reproducing the validated replay member set bit-for-bit.

This test builds an in-memory forecasts DB carrying BOTH:
  * a bound ``ensemble_snapshots`` row (the causal-cycle pin) with a DISTINCT (cold,
    51-member) ensemble envelope, and
  * a ``raw_model_forecasts`` multi-model envelope (warmer, ~8 decorrelated models)
    on the same causal cycle date,
so the two sources are unambiguously different. It proves the accessor returns the
MULTI-MODEL set (count + values == raw_model_forecasts, NOT the 51-member ensemble), and
that the values equal the validated ``fresh_members_at_cycle`` reduction.

RED-on-revert: revert the producer to source the ensemble envelope
(``_bound_forecast_snapshot_row_for_spine`` + ``_snapshot_members``) and the returned set
becomes the 51-member ensemble — the count + value assertions here FAIL.
"""
from __future__ import annotations

import datetime as _dt
import json
import sqlite3

import numpy as np
import pytest

from src.engine import event_reactor_adapter as era

CITY = "Wuhan"
TARGET_DATE = "2026-06-17"
METRIC = "high"
CAUSAL_SNAPSHOT_ID = "9900001"
# The event's bound causal cycle (the ensemble snapshot's source_cycle_time).
CAUSAL_CYCLE_DATE = "2026-06-16"
SOURCE_CYCLE_TIME_UTC = "2026-06-16T12:00:00+00:00"
# In production decision_time = now(UTC); for a target-tomorrow family that is well after
# every raw cycle on the causal date is ingested. We pin it to end-of-causal-date.
DECISION_TIME = _dt.datetime(2026, 6, 17, 0, 0, tzinfo=_dt.timezone.utc)

# A DISTINCT, COLD 51-member ECMWF ensemble envelope (the WRONG source — must NOT be
# returned). Mean ~30.0.
_RNG = np.random.default_rng(20260616)
ENSEMBLE_MEMBERS = [float(round(x, 3)) for x in (30.0 + 0.5 * _RNG.standard_normal(51))]

# The CORRECT multi-model deterministic envelope on the causal cycle date: 8 decorrelated
# providers, WARMER (mean ~33.0), each a single latest-cycle value. These are the values
# fresh_members_at_cycle would keep (latest cycle per model, °C). Wuhan is °C-settled, so
# native == °C (no F conversion).
MULTIMODEL_C = {
    "ecmwf_ifs": 32.8,
    "gfs_global": 33.4,
    "icon_global": 33.1,
    "icon_eu": 32.6,
    "icon_seamless": 33.0,
    "jma_seamless": 33.5,
    "ukmo_global_deterministic_10km": 32.9,
    "gem_global": 33.2,
}


class _Event:
    """Duck-typed OpportunityEvent carrying only the fields the accessor reads."""

    event_id = "evt-spine-multimodel-source"
    event_type = "FORECAST_SNAPSHOT_READY"
    causal_snapshot_id = CAUSAL_SNAPSHOT_ID


class _Family:
    city = CITY
    target_date = TARGET_DATE
    metric = METRIC
    family_id = f"{CITY}|{TARGET_DATE}|{METRIC}"


def _forecasts_conn() -> sqlite3.Connection:
    """In-memory forecasts DB with a bound ensemble snapshot (causal pin, cold 51-member
    envelope) AND a raw_model_forecasts multi-model envelope (warm, 8 models) on the same
    causal cycle date. The two sources are deliberately different so the source the
    accessor reads is unambiguous."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE ensemble_snapshots (
            snapshot_id TEXT, city TEXT, target_date TEXT, temperature_metric TEXT,
            members_json TEXT, available_at TEXT, authority TEXT, causality_status TEXT,
            boundary_ambiguous INTEGER, source_cycle_time TEXT, issue_time TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO ensemble_snapshots (
            snapshot_id, city, target_date, temperature_metric, members_json,
            available_at, authority, causality_status, boundary_ambiguous,
            source_cycle_time, issue_time
        ) VALUES (?, ?, ?, ?, ?, ?, 'VERIFIED', 'OK', 0, ?, ?)
        """,
        (
            CAUSAL_SNAPSHOT_ID, CITY, TARGET_DATE, METRIC, json.dumps(ENSEMBLE_MEMBERS),
            SOURCE_CYCLE_TIME_UTC, SOURCE_CYCLE_TIME_UTC, SOURCE_CYCLE_TIME_UTC,
        ),
    )
    conn.execute(
        """
        CREATE TABLE raw_model_forecasts (
            model TEXT, city TEXT, target_date TEXT, metric TEXT,
            source_cycle_time TEXT, source_available_at TEXT, lead_days INTEGER,
            forecast_value_c REAL
        )
        """
    )
    # Each model: an EARLIER same-date cycle (00:00) AND the latest (18:00) — the accessor
    # must keep the LATEST per model (matching fresh_members_at_cycle). Both are available
    # before DECISION_TIME (end of causal date + the 18:00 cycle ingests ~03:00 next day,
    # still < end of TARGET_DATE).
    rows = []
    for model, v_latest in MULTIMODEL_C.items():
        # earlier cycle carries a deliberately wrong (much colder) value that must be DROPPED
        rows.append((model, CITY, TARGET_DATE, METRIC,
                     f"{CAUSAL_CYCLE_DATE}T00:00:00+00:00",
                     f"{CAUSAL_CYCLE_DATE}T11:47:00+00:00", 1, v_latest - 5.0))
        rows.append((model, CITY, TARGET_DATE, METRIC,
                     f"{CAUSAL_CYCLE_DATE}T18:00:00+00:00",
                     f"{CAUSAL_CYCLE_DATE}T23:30:00+00:00", 1, v_latest))
    conn.executemany(
        "INSERT INTO raw_model_forecasts (model, city, target_date, metric, "
        "source_cycle_time, source_available_at, lead_days, forecast_value_c) "
        "VALUES (?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    return conn


def test_spine_producer_sources_raw_model_forecasts_not_ensemble():
    """The live spine member accessor returns the MULTI-MODEL deterministic envelope
    (raw_model_forecasts, latest cycle per model), NOT the ECMWF ensemble envelope.

    RED-on-revert: if the producer is reverted to source ensemble_snapshots.members_json,
    the count is 51 (the ensemble) and the values match ENSEMBLE_MEMBERS — both assertions
    below fail.
    """
    conn = _forecasts_conn()
    out = era._spine_multimodel_members_for_event(
        conn, event=_Event(), family=_Family(), decision_time=DECISION_TIME
    )
    assert out is not None, "multi-model envelope must be sourced for a causal-bound family"
    # FINAL no-shadow §1-§2: the producer now returns a THIRD element — the per-model RAW
    # second moment + n (precision_by_index), in the SAME index order as the member list.
    members, source_cycle, precision_by_index = out
    assert len(precision_by_index) == len(members), (
        "the per-model precision list must align 1:1 with the member list (RAW diagonal weights)"
    )

    # COUNT: 8 decorrelated models, NOT the 51-member ensemble.
    assert len(members) == len(MULTIMODEL_C), (
        f"expected {len(MULTIMODEL_C)} multi-model members (raw_model_forecasts), got "
        f"{len(members)} — a count of {len(ENSEMBLE_MEMBERS)} means the source reverted to "
        f"the ECMWF ensemble (the root-cause bug)."
    )
    assert len(members) != len(ENSEMBLE_MEMBERS), "must NOT be the 51-member ensemble set"

    # VALUES: the LATEST cycle per model (Wuhan is °C, so native == °C, no F conversion).
    expected = sorted(MULTIMODEL_C.values())
    assert sorted(round(v, 6) for v in members) == [round(v, 6) for v in expected], (
        "members must equal the latest-cycle-per-model raw_model_forecasts values "
        "(the validated fresh_members_at_cycle reduction); the earlier (colder) cycle "
        "must be dropped and the ensemble envelope must NOT appear."
    )

    # The source cycle is the causal ensemble cycle (provenance preserved).
    assert str(source_cycle)[:10] == CAUSAL_CYCLE_DATE


def test_spine_producer_matches_arm_replay_fresh_members_at_cycle():
    """Equivalence to the VALIDATED replay: the accessor's member set equals
    scripts/qkernel_arm_replay.fresh_members_at_cycle keyed on the SAME causal cycle date
    (both source raw_model_forecasts, latest cycle per model, °C→native)."""
    import importlib.util
    import os
    import sys

    arm_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "scripts", "qkernel_arm_replay.py",
    )
    # Load under the canonical module name so the script's dataclasses can resolve their
    # __module__ (Python 3.14 dataclass field introspection requires the module be in
    # sys.modules under its own name).
    spec = importlib.util.spec_from_file_location("qkernel_arm_replay", arm_path)
    arm = importlib.util.module_from_spec(spec)
    sys.modules["qkernel_arm_replay"] = arm
    spec.loader.exec_module(arm)

    conn = _forecasts_conn()
    out = era._spine_multimodel_members_for_event(
        conn, event=_Event(), family=_Family(), decision_time=DECISION_TIME
    )
    assert out is not None
    live_members, source_cycle, _precision_by_index = out
    cycle_date = str(source_cycle)[:10]

    replay_raw = arm.fresh_members_at_cycle(conn, CITY, METRIC, TARGET_DATE, cycle_date)
    # Wuhan is °C-settled: native == °C, so the replay raw values are directly comparable.
    replay_vals = [float(v) for _m, _s, _a, _l, v in replay_raw]
    assert sorted(round(v, 6) for v in live_members) == sorted(
        round(v, 6) for v in replay_vals
    ), "live producer member set must equal the validated replay fresh_members_at_cycle set"


def test_spine_producer_fail_closed_when_fewer_than_three_models():
    """Fail-closed: <3 multi-model members ⇒ None (honest SPINE_INPUTS_UNAVAILABLE),
    NEVER a fallback to the ensemble envelope."""
    conn = _forecasts_conn()
    # delete all but 2 models from the latest cycle
    keep = list(MULTIMODEL_C)[:2]
    conn.execute(
        "DELETE FROM raw_model_forecasts WHERE model NOT IN (?,?)", (keep[0], keep[1])
    )
    conn.commit()
    out = era._spine_multimodel_members_for_event(
        conn, event=_Event(), family=_Family(), decision_time=DECISION_TIME
    )
    assert out is None, "fewer than 3 models must fail closed (no ensemble fallback)"


def test_live_producer_is_authoritative_and_not_preempted_by_canonical_stash():
    """RED-on-revert (Task 2, canonical-stash pre-emption fix 2026-06-16): the live
    spine producer in ``_generate_candidate_proofs`` MUST run its multi-model
    ``raw_model_forecasts`` accessor UNCONDITIONALLY for forecast-decision events and
    OVERWRITE the spine-decision keys — it must NOT skip when ``_edli_spine_mu_native``
    is already present in the payload.

    THE DEFECT this guards: the canonical Stage-0 stash
    (``_market_analysis_from_event_snapshot``, observability-only) populates
    ``_edli_spine_mu_native`` from ``ensemble_snapshots`` on a replacement-flag-OFF
    fall-through. The producer's former guard
    ``and "_edli_spine_mu_native" not in payload`` then SKIPPED the raw_model_forecasts
    fix, so the spine silently decided on the cold ECMWF-ensemble center again.

    Structural assertion (robust to line numbers): the producer's guard for the
    Q-KERNEL SPINE INPUTS block contains the ``_FORECAST_DECISION_EVENT_TYPES`` test
    but NOT the ``"_edli_spine_mu_native" not in payload`` skip. Reverting to the
    skip-guard re-introduces the pre-emption → this test fails.
    """
    import ast
    import inspect

    src = inspect.getsource(era._generate_candidate_proofs)
    tree = ast.parse(src)

    # Find the `if` whose test references _FORECAST_DECISION_EVENT_TYPES and that guards
    # the spine-input population (its body assigns _spine_multimodel ...).
    spine_guards = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test_src = ast.get_source_segment(src, node.test) or ast.dump(node.test)
        body_src = ast.get_source_segment(src, node) or ""
        if (
            "_FORECAST_DECISION_EVENT_TYPES" in test_src
            and "_spine_multimodel_members_for_event" in body_src
        ):
            spine_guards.append(test_src)

    assert spine_guards, (
        "could not locate the Q-KERNEL SPINE INPUTS guard that calls "
        "_spine_multimodel_members_for_event in _generate_candidate_proofs"
    )
    for guard in spine_guards:
        assert "_edli_spine_mu_native" not in guard, (
            "the spine-input producer guard must NOT skip when _edli_spine_mu_native is "
            "already in the payload — that lets the canonical ensemble_snapshots stash "
            "pre-empt the raw_model_forecasts multi-model source (the cold-center "
            "regression). The producer must run unconditionally and overwrite."
        )

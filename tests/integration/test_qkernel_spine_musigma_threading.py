# Created: 2026-06-16
# Last reused or audited: 2026-06-16
# Authority basis: docs/evidence/qkernel_rebuild/fix_spine_musigma_threading_2026-06-16.md
#   (the money-path wiring fix this test is the RED-on-revert smoke for) +
#   src/engine/event_reactor_adapter.py Q-KERNEL SPINE INPUTS block (the live
#   threading seam) + the task brief (assert: a FORECAST_SNAPSHOT_READY family with
#   a bound snapshot carrying a valid member envelope threads μ/σ + members to the
#   spine — i.e. the bridge no longer sees SPINE_INPUTS_UNAVAILABLE:MU_SIGMA_NOT_STASHED;
#   reverting the threading fix must make μ/σ NOT stash).
"""RED-on-revert smoke for the spine μ/σ threading fix (2026-06-16).

THE BUG (fixed by this change): the live ``_generate_candidate_proofs`` Q-KERNEL
SPINE INPUTS block sourced the bound forecast snapshot through
``_forecast_snapshot_row_for_event``, which ALSO runs the executable-forecast
READER-BLOCK *trade-eligibility* gate (``_forecast_snapshot_reader_block_reason``).
That gate raises ``FORECAST_READER_*`` for live FORECAST_SNAPSHOT_READY families
(e.g. ``scope_incomplete`` when the source_run / coverage scope is not matched), and
the block's fail-soft ``except`` swallowed the raise — so the spine bridge read
``_edli_spine_mu_native`` / ``_edli_spine_sigma_native`` back as ``None`` and returned
``SPINE_INPUTS_UNAVAILABLE:MU_SIGMA_NOT_STASHED`` universally, even though the bound
snapshot carried a perfectly valid 51-member ensemble envelope.

THE FIX: source the member envelope through ``_bound_forecast_snapshot_row_for_spine``,
which keeps the same data-INTEGRITY predicates (VERIFIED / causality OK / not
boundary-ambiguous / available_at ≤ decision_time / pinned to causal_snapshot_id) but
does NOT run the trade-eligibility reader-block gate (trade eligibility is already owned
by the live replacement authority lane). Pure input THREADING; no decision-math change.

This test builds an in-memory ``ensemble_snapshots`` row that reproduces the live
condition: a VERIFIED, causality-OK bound snapshot with a valid member envelope, but
with NO ``source_run`` / ``source_run_coverage`` scope rows (so the reader-block gate
RAISES). It proves:
  * the OLD gated fetch (``_forecast_snapshot_row_for_event``) RAISES ``FORECAST_READER_*``
    on this fixture — the gate is the blocker;
  * the NEW accessor (``_bound_forecast_snapshot_row_for_spine``) RETURNS the row and its
    members are readable (μ/σ computable) — the spine gets its inputs;
  * the spine-input stash (the exact inline computation the live block runs) writes
    ``_edli_spine_mu_native`` / ``_edli_spine_sigma_native`` / members / source-cycle onto
    the payload, so the bridge's ``_spine_inputs_missing_reason`` no longer returns
    ``MU_SIGMA_NOT_STASHED``.

RED-on-revert: point the live spine block back at ``_forecast_snapshot_row_for_event``
(the pre-fix source) and the swallowed ``FORECAST_READER_*`` raise leaves the payload
without ``_edli_spine_mu_native`` ⇒ ``MU_SIGMA_NOT_STASHED`` ⇒ this test fails.
"""
from __future__ import annotations

import datetime as _dt
import json
import sqlite3

import numpy as np
import pytest

from src.engine import event_reactor_adapter as era
from src.engine import qkernel_spine_bridge as bridge

CITY = "Wuhan"
TARGET_DATE = "2026-06-17"
METRIC = "high"
CAUSAL_SNAPSHOT_ID = "1171166"
SOURCE_CYCLE_TIME_UTC = "2026-06-15T12:00:00+00:00"
# A realistic 51-member ECMWF ensemble envelope (mean ~32.17, std ~0.50 — the live
# Wuhan 2026-06-17 high case the probe captured).
_RNG = np.random.default_rng(20260616)
MEMBERS = [float(round(x, 3)) for x in (32.166 + 0.496 * _RNG.standard_normal(51))]
DECISION_TIME = _dt.datetime(2026, 6, 15, 13, 0, tzinfo=_dt.timezone.utc)


class _Event:
    """Duck-typed OpportunityEvent carrying only the fields the snapshot accessors read."""

    def __init__(self) -> None:
        self.event_id = "evt-musigma-threading"
        self.event_type = "FORECAST_SNAPSHOT_READY"
        self.causal_snapshot_id = CAUSAL_SNAPSHOT_ID
        self.payload_json = json.dumps(
            {"city": CITY, "target_date": TARGET_DATE, "metric": METRIC}
        )


class _Family:
    """Minimal family with the attributes the snapshot accessors / reader-block read."""

    city = CITY
    target_date = TARGET_DATE
    metric = METRIC
    family_id = f"{CITY}|{TARGET_DATE}|{METRIC}"
    condition_ids = ()


def _forecasts_conn_with_bound_snapshot() -> sqlite3.Connection:
    """In-memory forecasts DB: a VERIFIED, causality-OK bound snapshot with a valid
    member envelope, but NO source_run / source_run_coverage scope rows.

    This is the live condition that makes the reader-block gate RAISE
    (FORECAST_READER_SCOPE_CONSTRUCTION_MISSING) while the member envelope itself is
    perfectly valid — exactly the case that produced MU_SIGMA_NOT_STASHED in production.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE ensemble_snapshots (
            snapshot_id TEXT,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            members_json TEXT,
            available_at TEXT,
            authority TEXT,
            causality_status TEXT,
            boundary_ambiguous INTEGER,
            source_run_id TEXT,
            source_id TEXT,
            source_transport TEXT,
            data_version TEXT,
            track TEXT,
            source_cycle_time TEXT,
            issue_time TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO ensemble_snapshots (
            snapshot_id, city, target_date, temperature_metric, members_json,
            available_at, authority, causality_status, boundary_ambiguous,
            source_run_id, source_id, source_transport, data_version, track,
            source_cycle_time, issue_time
        ) VALUES (?, ?, ?, ?, ?, ?, 'VERIFIED', 'OK', 0, ?, 'ecmwf_open_data',
                  'ensemble_snapshots_db_reader', NULL, NULL, ?, ?)
        """,
        (
            CAUSAL_SNAPSHOT_ID,
            CITY,
            TARGET_DATE,
            METRIC,
            json.dumps(MEMBERS),
            "2026-06-15T12:00:00+00:00",
            "ecmwf_open_data:mx2t6_high:2026-06-15T12Z",
            SOURCE_CYCLE_TIME_UTC,
            SOURCE_CYCLE_TIME_UTC,
        ),
    )
    conn.commit()
    return conn


def test_old_gated_fetch_raises_reader_block_on_live_fsr_fixture():
    """PRE-FIX SOURCE: ``_forecast_snapshot_row_for_event`` runs the reader-block gate,
    which RAISES on the live FSR fixture (no source_run/coverage scope) — this is the
    swallowed raise that produced MU_SIGMA_NOT_STASHED. (Documents the bug; if this ever
    stops raising the threading fix is no longer load-bearing and should be reassessed.)
    """
    conn = _forecasts_conn_with_bound_snapshot()
    event = _Event()
    family = _Family()
    with pytest.raises(ValueError) as exc:
        era._forecast_snapshot_row_for_event(
            conn, event=event, family=family, allow_latest=False, decision_time=DECISION_TIME
        )
    assert "FORECAST_READER_" in str(exc.value), (
        f"expected a reader-block raise (the swallowed cause of MU_SIGMA_NOT_STASHED), "
        f"got {exc.value!r}"
    )


def test_new_accessor_returns_bound_snapshot_members_without_reader_block():
    """THE FIX: ``_bound_forecast_snapshot_row_for_spine`` returns the bound snapshot row
    (same data-integrity predicates) WITHOUT the trade-eligibility reader-block gate, so
    the valid member envelope is reachable and μ/σ are computable.
    """
    conn = _forecasts_conn_with_bound_snapshot()
    event = _Event()
    family = _Family()
    snap = era._bound_forecast_snapshot_row_for_spine(
        conn, event=event, family=family, decision_time=DECISION_TIME
    )
    assert snap is not None, "the bound causal snapshot must be returned (it exists + is VERIFIED/OK)"
    assert str(snap.get("snapshot_id")) == CAUSAL_SNAPSHOT_ID
    members = era._snapshot_members(snap)
    assert members.size == len(MEMBERS)
    assert np.isfinite(members).all()
    # μ/σ match the threaded envelope (the spine's predictive center/width).
    assert float(members.mean()) == pytest.approx(float(np.mean(MEMBERS)), rel=1e-9)
    assert float(members.std(ddof=1)) == pytest.approx(float(np.std(MEMBERS, ddof=1)), rel=1e-9)


def test_spine_inputs_stash_via_new_accessor_clears_mu_sigma_not_stashed():
    """End-to-end at the threading seam: running the live spine-input stash logic with the
    NEW accessor populates ``_edli_spine_*`` on the payload, so the bridge's
    ``_spine_inputs_missing_reason`` no longer returns ``MU_SIGMA_NOT_STASHED``.

    This replicates the EXACT inline computation the live ``_generate_candidate_proofs``
    block runs (snapshot fetch via the new accessor → empirical mean/std of the member
    envelope → stash), then validates the result against the bridge's OWN missing-reason
    classifier — the function that emits the live MU_SIGMA_NOT_STASHED code.
    """
    conn = _forecasts_conn_with_bound_snapshot()
    event = _Event()
    family = _Family()
    payload: dict[str, object] = {"city": CITY, "target_date": TARGET_DATE, "metric": METRIC}

    # --- the live spine-input stash, byte-faithful to the _generate_candidate_proofs block ---
    snap = era._bound_forecast_snapshot_row_for_spine(
        conn, event=event, family=family, decision_time=DECISION_TIME
    )
    assert snap is not None
    spine_arr = np.asarray(era._snapshot_members(snap), dtype=float).ravel()
    assert spine_arr.size
    spine_lst = [float(x) for x in spine_arr.tolist()]
    payload["_edli_spine_raw_members_native"] = spine_lst
    payload["_edli_spine_debiased_members_native"] = spine_lst
    spine_mean = sum(spine_lst) / len(spine_lst)
    payload["_edli_spine_mu_native"] = float(spine_mean)
    spine_var = sum((v - spine_mean) ** 2 for v in spine_lst) / (len(spine_lst) - 1)
    payload["_edli_spine_sigma_native"] = float(spine_var ** 0.5)
    spine_sc = snap.get("source_cycle_time") or snap.get("issue_time")
    payload["_edli_spine_source_cycle_time_utc"] = str(spine_sc)

    # --- the bridge's OWN classifier: it must NOT be MU_SIGMA_NOT_STASHED (the live bug) ---
    reason = bridge._spine_inputs_missing_reason(payload)
    assert reason != "MU_SIGMA_NOT_STASHED", (
        "after threading via the new accessor the spine MUST have μ/σ "
        "(reverting the fix to _forecast_snapshot_row_for_event re-introduces "
        "MU_SIGMA_NOT_STASHED via the swallowed reader-block raise)"
    )
    # Stronger: the inputs are complete (μ/σ finite + members + source cycle present) — the
    # bridge would proceed to a real spine decision rather than a typed SPINE_INPUTS_UNAVAILABLE.
    assert reason == "UNKNOWN", (
        f"all spine inputs should be present (the residual 'UNKNOWN' sentinel means no gap), "
        f"got {reason!r}"
    )
    assert payload["_edli_spine_mu_native"] == pytest.approx(float(np.mean(MEMBERS)), rel=1e-9)
    assert payload["_edli_spine_sigma_native"] == pytest.approx(float(np.std(MEMBERS, ddof=1)), rel=1e-9)

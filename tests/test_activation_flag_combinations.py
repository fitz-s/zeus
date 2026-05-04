# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/activation/UNLOCK_CRITERIA.md — relationship tests that gate flag-flip authorization for ZEUS_ENTRY_FORECAST_{ROLLOUT_GATE,READINESS_WRITER,HEALTHCHECK_BLOCKERS}.
"""Activation-flag combination relationship tests.

These are the antibodies that gate flag flips. Each test encodes one
cross-flag invariant: any subset of {writer, gate, healthcheck} flags
ON, with any evidence state on disk, must remain fail-closed unless the
**exact** combination (writer ON + evidence valid) is present.

Why these and not just per-flag tests:
- Per-flag tests prove each flag's local logic.
- These tests prove that **out-of-order** flips never open an unsafe
  path, that the writer/gate evidence-cache view is consistent across
  call sites, and that the daemon's two read sites of
  ``read_promotion_evidence`` (rollout-blocker and writer) converge on
  the same disposition for a given on-disk file.

When a flag is about to be flipped, this whole module must pass under
the operator's actual evidence state. ``produce_activation_evidence.py``
runs these and dumps the verdict to ``evidence/activation/``.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from src.config import (
    EntryForecastRolloutMode,
    entry_forecast_config,
)
from src.contracts.ensemble_snapshot_provenance import (
    ECMWF_OPENDATA_HIGH_DATA_VERSION,
)
from src.control import entry_forecast_promotion_evidence_io as evidence_io
from src.control.entry_forecast_promotion_evidence_io import (
    PromotionEvidenceCorruption,
    clear_evidence_read_cache,
    read_promotion_evidence,
    write_promotion_evidence,
)
from src.control.entry_forecast_rollout import (
    EntryForecastPromotionEvidence,
    evaluate_entry_forecast_rollout_gate,
)
from src.data.entry_readiness_writer import ENTRY_FORECAST_STRATEGY_KEY
from src.data.live_entry_status import LiveEntryForecastStatus
from src.engine import evaluator as evaluator_module
from src.state.db import init_schema
from src.state.schema.v2_schema import apply_v2_schema
from src.types.metric_identity import HIGH_LOCALDAY_MAX

UTC = timezone.utc


@pytest.fixture(autouse=True)
def _clear_cache_each_test():
    """Drop the lru_cache between tests so an mtime cached in test N
    cannot leak into test N+1 (different ``tmp_path``).
    """

    clear_evidence_read_cache()
    yield
    clear_evidence_read_cache()


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    apply_v2_schema(conn)
    return conn


def _city():
    from src.config import City

    return City(
        name="London",
        lat=51.4775,
        lon=-0.4614,
        timezone="Europe/London",
        settlement_unit="C",
        cluster="London",
        wu_station="EGLL",
    )


def _ready_status() -> LiveEntryForecastStatus:
    return LiveEntryForecastStatus(
        status="LIVE_ELIGIBLE",
        blockers=(),
        executable_row_count=4,
        producer_readiness_count=4,
        producer_live_eligible_count=4,
    )


def _complete_evidence() -> EntryForecastPromotionEvidence:
    return EntryForecastPromotionEvidence(
        operator_approval_id="op-2026-05-04",
        g1_evidence_id="g1-2026-05-04",
        status_snapshot=_ready_status(),
        calibration_promotion_approved=True,
        canary_success_evidence_id="canary-2026-05-04",
    )


def _live_cfg():
    return replace(entry_forecast_config(), rollout_mode=EntryForecastRolloutMode.LIVE)


def _invoke_writer(monkeypatch, evidence_path: Path, conn: sqlite3.Connection) -> None:
    """Invoke ``_write_entry_readiness_for_candidate`` with the evidence
    file remapped via monkeypatch. The helper reads
    ``DEFAULT_PROMOTION_EVIDENCE_PATH`` internally; we redirect it.
    """

    monkeypatch.setattr(evidence_io, "DEFAULT_PROMOTION_EVIDENCE_PATH", evidence_path)
    evaluator_module._write_entry_readiness_for_candidate(
        conn,
        cfg=_live_cfg(),
        city=_city(),
        target_local_date=date(2026, 5, 8),
        temperature_metric=HIGH_LOCALDAY_MAX,
        market_family="POLY_TEMP_LONDON",
        condition_id="condition-123",
        decision_time=datetime(2026, 5, 4, 12, tzinfo=UTC),
    )


def _read_back_row(conn: sqlite3.Connection):
    return conn.execute(
        "SELECT status, reason_codes_json FROM readiness_state "
        "WHERE strategy_key = ?",
        (ENTRY_FORECAST_STRATEGY_KEY,),
    ).fetchone()


# -------------------------------------------------------------------- #
# INV-A: Out-of-order flag flips never open an unsafe path
# -------------------------------------------------------------------- #


@pytest.mark.skip(reason="rollout gate retired 2026-05-04 (see evaluator.py:759 docstring); test asserts retired blocker behaviour")
def test_inv_a_kill_switch_zero_disables_flags(monkeypatch, tmp_path):
    """Post-2026-05-04 default-ON activation: the kill-switch is
    ``ZEUS_ENTRY_FORECAST_ROLLOUT_GATE=0`` /
    ``ZEUS_ENTRY_FORECAST_READINESS_WRITER=0``. Setting either to ``"0"``
    must restore the legacy rollout-mode-only check for that flag's
    call site, without touching the other flag.

    This is the operator's emergency-disable contract — if the gate or
    writer misbehaves in production, set the env to ``"0"`` and the
    daemon falls back to the pre-Phase-C behavior on next cycle.
    """

    monkeypatch.setenv("ZEUS_ENTRY_FORECAST_ROLLOUT_GATE", "0")
    monkeypatch.setenv("ZEUS_ENTRY_FORECAST_READINESS_WRITER", "0")
    monkeypatch.setenv("ZEUS_ENTRY_FORECAST_HEALTHCHECK_BLOCKERS", "1")

    assert evaluator_module._entry_forecast_rollout_gate_flag_on() is False
    assert evaluator_module._entry_forecast_readiness_writer_flag_on() is False

    blocked_cfg = replace(_live_cfg(), rollout_mode=EntryForecastRolloutMode.BLOCKED)
    assert (
        evaluator_module._live_entry_forecast_rollout_blocker(blocked_cfg)
        == "ENTRY_FORECAST_ROLLOUT_BLOCKED"
    )
    live_cfg = _live_cfg()
    assert evaluator_module._live_entry_forecast_rollout_blocker(live_cfg) is None


def test_inv_a_default_on_unset_env_treats_flags_as_active(monkeypatch, tmp_path):
    """Post-2026-05-04: with both env vars unset (operator never set
    them), the flag predicates default to ON. This pins the new
    default-on contract.
    """

    monkeypatch.delenv("ZEUS_ENTRY_FORECAST_ROLLOUT_GATE", raising=False)
    monkeypatch.delenv("ZEUS_ENTRY_FORECAST_READINESS_WRITER", raising=False)

    assert evaluator_module._entry_forecast_rollout_gate_flag_on() is True
    assert evaluator_module._entry_forecast_readiness_writer_flag_on() is True


def test_inv_a_empty_string_env_treats_flag_as_active(monkeypatch, tmp_path):
    """Empty-string env var ⇒ default behavior (ON) because the
    kill-switch only fires on the literal string ``"0"``. This guards
    against the failure mode where some shell config sets the var to
    ``""`` accidentally and silently disables the gate.
    """

    monkeypatch.setenv("ZEUS_ENTRY_FORECAST_ROLLOUT_GATE", "")
    monkeypatch.setenv("ZEUS_ENTRY_FORECAST_READINESS_WRITER", "")

    assert evaluator_module._entry_forecast_rollout_gate_flag_on() is True
    assert evaluator_module._entry_forecast_readiness_writer_flag_on() is True


def test_inv_a_flag2_alone_writes_blocked_when_evidence_missing(monkeypatch, tmp_path):
    """Flag 2 (READINESS_WRITER) ON without flag 1 ON and without
    on-disk evidence ⇒ writer must land BLOCKED with
    ENTRY_FORECAST_PROMOTION_EVIDENCE_MISSING. This is the "writer
    flipped first per runbook" path. Reader sees row → reader emits
    typed blocker → fail-closed.
    """

    monkeypatch.setenv("ZEUS_ENTRY_FORECAST_ROLLOUT_GATE", "0")
    monkeypatch.setenv("ZEUS_ENTRY_FORECAST_READINESS_WRITER", "1")
    monkeypatch.delenv("ZEUS_ENTRY_FORECAST_HEALTHCHECK_BLOCKERS", raising=False)

    conn = _conn()
    _invoke_writer(monkeypatch, tmp_path / "absent.json", conn)
    row = _read_back_row(conn)
    assert row is not None
    assert row["status"] == "BLOCKED"
    assert "ENTRY_FORECAST_PROMOTION_EVIDENCE_MISSING" in row["reason_codes_json"]


@pytest.mark.skip(reason="rollout gate retired 2026-05-04 (see evaluator.py:759 docstring); test asserts retired blocker behaviour")
def test_inv_a_flag1_alone_blocks_when_evidence_missing(monkeypatch, tmp_path):
    """Flag 1 (ROLLOUT_GATE) ON, flag 2 killed ⇒ rollout blocker
    surfaces EVIDENCE_MISSING. Even though no row is ever written,
    the upstream gate refuses live submission.
    """

    monkeypatch.setenv("ZEUS_ENTRY_FORECAST_ROLLOUT_GATE", "1")
    monkeypatch.setenv("ZEUS_ENTRY_FORECAST_READINESS_WRITER", "0")
    monkeypatch.setattr(
        evidence_io,
        "DEFAULT_PROMOTION_EVIDENCE_PATH",
        tmp_path / "absent.json",
    )

    blocker = evaluator_module._live_entry_forecast_rollout_blocker(_live_cfg())
    assert blocker == "ENTRY_FORECAST_PROMOTION_EVIDENCE_MISSING"


@pytest.mark.skip(reason="rollout gate retired 2026-05-04 (see evaluator.py:759 docstring); test asserts retired blocker behaviour")
def test_inv_a_flags_1_and_2_on_no_evidence_both_sites_fail_closed(monkeypatch, tmp_path):
    """Both flags ON, no evidence file ⇒ rollout blocker emits
    EVIDENCE_MISSING AND writer lands BLOCKED. The two daemon read
    sites of ``read_promotion_evidence`` must converge on the same
    fail-closed disposition for the same on-disk state.
    """

    monkeypatch.setenv("ZEUS_ENTRY_FORECAST_ROLLOUT_GATE", "1")
    monkeypatch.setenv("ZEUS_ENTRY_FORECAST_READINESS_WRITER", "1")
    target = tmp_path / "absent.json"
    monkeypatch.setattr(evidence_io, "DEFAULT_PROMOTION_EVIDENCE_PATH", target)

    # Site 1: rollout blocker.
    assert (
        evaluator_module._live_entry_forecast_rollout_blocker(_live_cfg())
        == "ENTRY_FORECAST_PROMOTION_EVIDENCE_MISSING"
    )

    # Site 2: writer.
    conn = _conn()
    _invoke_writer(monkeypatch, target, conn)
    row = _read_back_row(conn)
    assert row is not None
    assert row["status"] == "BLOCKED"
    assert "ENTRY_FORECAST_PROMOTION_EVIDENCE_MISSING" in row["reason_codes_json"]


# -------------------------------------------------------------------- #
# INV-B: Promotion-evidence corruption disposition
# -------------------------------------------------------------------- #


@pytest.mark.skip(reason="rollout gate retired 2026-05-04 (see evaluator.py:759 docstring); test asserts retired blocker behaviour")
def test_inv_b_flag1_corrupt_evidence_typed_blocker(monkeypatch, tmp_path):
    """Flag 1 ON + corrupt JSON ⇒ rollout blocker prefixed
    ``ENTRY_FORECAST_PROMOTION_EVIDENCE_CORRUPT:`` (never crashes the
    cycle).
    """

    target = tmp_path / "corrupt.json"
    target.write_text("{not valid json")

    monkeypatch.setenv("ZEUS_ENTRY_FORECAST_ROLLOUT_GATE", "1")
    monkeypatch.setattr(evidence_io, "DEFAULT_PROMOTION_EVIDENCE_PATH", target)

    blocker = evaluator_module._live_entry_forecast_rollout_blocker(_live_cfg())
    assert blocker is not None
    assert blocker.startswith("ENTRY_FORECAST_PROMOTION_EVIDENCE_CORRUPT:")


def test_inv_b_flag2_corrupt_evidence_writer_treats_as_missing(monkeypatch, tmp_path):
    """Flag 2 ON + corrupt JSON ⇒ writer catches the corruption and
    treats it as missing evidence (lands BLOCKED row). Writer must NOT
    crash the cycle.

    Reasoning: writer's docstring says corruption → ``evidence = None``;
    the rollout decision then sees no evidence and emits
    EVIDENCE_MISSING. The row reason therefore includes
    EVIDENCE_MISSING, NOT EVIDENCE_CORRUPT — the corruption is hidden
    from the row but visible at the rollout-blocker site if flag 1 is
    also ON. Operators relying on the writer alone will see
    EVIDENCE_MISSING; flag-1's CORRUPT discrimination is the surface
    that distinguishes the two.
    """

    target = tmp_path / "corrupt.json"
    target.write_text("{not valid json")

    monkeypatch.setenv("ZEUS_ENTRY_FORECAST_READINESS_WRITER", "1")
    conn = _conn()
    _invoke_writer(monkeypatch, target, conn)
    row = _read_back_row(conn)
    assert row is not None
    assert row["status"] == "BLOCKED"
    assert "ENTRY_FORECAST_PROMOTION_EVIDENCE_MISSING" in row["reason_codes_json"]


@pytest.mark.skip(reason="rollout gate retired 2026-05-04 (see evaluator.py:759 docstring); test asserts retired blocker behaviour")
def test_inv_b_flags_1_and_2_corrupt_evidence_dual_signal(monkeypatch, tmp_path):
    """Both flags ON + corrupt JSON ⇒ blocker site says CORRUPT, writer
    site says MISSING. The two surfaces are intentionally different so
    the operator can distinguish "no evidence written yet" from
    "evidence file got clobbered". This test pins that contract.
    """

    target = tmp_path / "corrupt.json"
    target.write_text("oops")

    monkeypatch.setenv("ZEUS_ENTRY_FORECAST_ROLLOUT_GATE", "1")
    monkeypatch.setenv("ZEUS_ENTRY_FORECAST_READINESS_WRITER", "1")
    monkeypatch.setattr(evidence_io, "DEFAULT_PROMOTION_EVIDENCE_PATH", target)

    blocker = evaluator_module._live_entry_forecast_rollout_blocker(_live_cfg())
    assert blocker is not None
    assert blocker.startswith("ENTRY_FORECAST_PROMOTION_EVIDENCE_CORRUPT:")

    conn = _conn()
    _invoke_writer(monkeypatch, target, conn)
    row = _read_back_row(conn)
    assert row is not None
    assert row["status"] == "BLOCKED"
    # Writer treats corruption-as-missing per its catch.
    assert "ENTRY_FORECAST_PROMOTION_EVIDENCE_MISSING" in row["reason_codes_json"]


# -------------------------------------------------------------------- #
# INV-C: Evidence-file rotation visibility (lru_cache invalidation)
# -------------------------------------------------------------------- #


def test_inv_c_evidence_file_rotation_invalidates_cache(monkeypatch, tmp_path):
    """Phase C-perf-cache: cache key is ``(path, mtime_ns, size)``.
    When the operator rewrites the evidence file (mtime advances), the
    next read must pick up the NEW content, not return a stale parse.

    This is the relationship test for the cache-invalidation invariant
    that makes the writer + gate safe to run with the perf cache. If
    this regresses, every flag flip silently uses stale evidence.
    """

    target = tmp_path / "evidence.json"
    monkeypatch.setattr(evidence_io, "DEFAULT_PROMOTION_EVIDENCE_PATH", target)

    # First write: complete evidence.
    write_promotion_evidence(_complete_evidence(), path=target)
    first = read_promotion_evidence(path=target)
    assert first is not None
    assert first.canary_success_evidence_id == "canary-2026-05-04"

    # Second write: same payload but canary cleared (operator-rotated).
    rotated = replace(_complete_evidence(), canary_success_evidence_id=None)
    # Bump mtime explicitly: APFS mtime resolution is ns-level but the
    # write is fast enough that two rapid writes can land in the same
    # mtime tick on some kernels. Sleep is forbidden by tooling; we
    # touch the file with a deliberate offset using os.utime instead.
    write_promotion_evidence(rotated, path=target)
    stat_after = target.stat()
    os.utime(target, ns=(stat_after.st_atime_ns, stat_after.st_mtime_ns + 1_000_000))

    second = read_promotion_evidence(path=target)
    assert second is not None
    assert second.canary_success_evidence_id is None  # picked up the rotation


@pytest.mark.skip(reason="rollout gate retired 2026-05-04 (see evaluator.py:759 docstring); test asserts retired blocker behaviour")
def test_inv_c_rollout_gate_sees_rotated_evidence_without_explicit_cache_clear(
    monkeypatch, tmp_path
):
    """End-to-end version of INV-C: flip flag 1 ON, write incomplete
    evidence, evaluate gate (BLOCKED), rotate evidence to complete,
    re-evaluate gate (PASSES). The daemon never calls
    ``clear_evidence_read_cache`` — the mtime-based invalidation must
    be sufficient.
    """

    target = tmp_path / "evidence.json"
    monkeypatch.setenv("ZEUS_ENTRY_FORECAST_ROLLOUT_GATE", "1")
    monkeypatch.setattr(evidence_io, "DEFAULT_PROMOTION_EVIDENCE_PATH", target)

    incomplete = replace(_complete_evidence(), canary_success_evidence_id=None)
    write_promotion_evidence(incomplete, path=target)
    blocker = evaluator_module._live_entry_forecast_rollout_blocker(_live_cfg())
    assert blocker == "ENTRY_FORECAST_CANARY_SUCCESS_MISSING"

    write_promotion_evidence(_complete_evidence(), path=target)
    stat_after = target.stat()
    os.utime(target, ns=(stat_after.st_atime_ns, stat_after.st_mtime_ns + 1_000_000))

    blocker_after = evaluator_module._live_entry_forecast_rollout_blocker(_live_cfg())
    assert blocker_after is None


# -------------------------------------------------------------------- #
# INV-D: Healthcheck flag composition with rollout/writer flags
# -------------------------------------------------------------------- #


def test_inv_d_healthcheck_flag_predicate_independent_of_writer_flag(monkeypatch, tmp_path):
    """``ZEUS_ENTRY_FORECAST_HEALTHCHECK_BLOCKERS`` only consults the
    ``entry_forecast_blockers`` field that healthcheck already
    populates. It must NOT depend on whether the writer flag is ON;
    its participation in the healthy predicate is purely an env-flag
    decision local to ``scripts/healthcheck.py``.

    Pinning this so a future refactor cannot accidentally couple flag 3
    to flag 2.
    """

    import scripts.healthcheck as hc

    monkeypatch.setenv("ZEUS_ENTRY_FORECAST_HEALTHCHECK_BLOCKERS", "1")
    monkeypatch.delenv("ZEUS_ENTRY_FORECAST_READINESS_WRITER", raising=False)

    src = Path(hc.__file__).read_text()
    # The flag's only consumer is the predicate combinator. Confirm
    # the writer-flag string is NOT referenced from healthcheck.py at
    # all; if it appears, the cross-coupling check below is wrong.
    assert "ZEUS_ENTRY_FORECAST_READINESS_WRITER" not in src
    assert "ZEUS_ENTRY_FORECAST_ROLLOUT_GATE" not in src
    assert "ZEUS_ENTRY_FORECAST_HEALTHCHECK_BLOCKERS" in src


# -------------------------------------------------------------------- #
# INV-E: Full chain progression with all three flags ON
# -------------------------------------------------------------------- #


def test_inv_e_all_flags_on_complete_evidence_writes_live_eligible(monkeypatch, tmp_path):
    """All three flags ON + complete evidence ⇒ writer lands
    LIVE_ELIGIBLE row AND rollout blocker site returns None. This is
    the ONE state where live entry-forecast trades may flow. The test
    pins that the safe state is reachable end-to-end.
    """

    target = tmp_path / "evidence.json"
    write_promotion_evidence(_complete_evidence(), path=target)

    monkeypatch.setenv("ZEUS_ENTRY_FORECAST_ROLLOUT_GATE", "1")
    monkeypatch.setenv("ZEUS_ENTRY_FORECAST_READINESS_WRITER", "1")
    monkeypatch.setenv("ZEUS_ENTRY_FORECAST_HEALTHCHECK_BLOCKERS", "1")
    monkeypatch.setattr(evidence_io, "DEFAULT_PROMOTION_EVIDENCE_PATH", target)

    assert evaluator_module._live_entry_forecast_rollout_blocker(_live_cfg()) is None

    conn = _conn()
    _invoke_writer(monkeypatch, target, conn)
    row = _read_back_row(conn)
    assert row is not None
    assert row["status"] == "LIVE_ELIGIBLE"


@pytest.mark.skip(reason="rollout gate retired 2026-05-04 (see evaluator.py:759 docstring); test asserts retired blocker behaviour")
def test_inv_e_all_flags_on_evidence_lacks_canary_writer_lands_blocked(
    monkeypatch, tmp_path
):
    """All three flags ON + canary missing in evidence ⇒ writer must
    land BLOCKED with the canary reason; rollout blocker emits the
    same upstream. Mirrors the production state operators encounter
    when promotion evidence is partially populated.
    """

    target = tmp_path / "evidence.json"
    write_promotion_evidence(
        replace(_complete_evidence(), canary_success_evidence_id=None),
        path=target,
    )

    monkeypatch.setenv("ZEUS_ENTRY_FORECAST_ROLLOUT_GATE", "1")
    monkeypatch.setenv("ZEUS_ENTRY_FORECAST_READINESS_WRITER", "1")
    monkeypatch.setattr(evidence_io, "DEFAULT_PROMOTION_EVIDENCE_PATH", target)

    blocker = evaluator_module._live_entry_forecast_rollout_blocker(_live_cfg())
    assert blocker == "ENTRY_FORECAST_CANARY_SUCCESS_MISSING"

    conn = _conn()
    _invoke_writer(monkeypatch, target, conn)
    row = _read_back_row(conn)
    assert row is not None
    assert row["status"] == "BLOCKED"
    reasons = json.loads(row["reason_codes_json"])
    # Writer surfaces the canary reason from the rollout decision.
    assert any("CANARY" in r for r in reasons)

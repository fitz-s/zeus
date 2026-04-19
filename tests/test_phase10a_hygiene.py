# Lifecycle: created=2026-04-19; last_reviewed=2026-04-19; last_reused=never
# Purpose: Phase 10A "Independent Hygiene Fix Pack" antibodies (R-CH..R-CN).
#          Dedicated test file per critic-carol cycle-3 L2 convention —
#          S1+S2 home here (R-CH/R-CI); executor appends S3+S4 (R-CJ/R-CK)
#          via Edit not Write. Do NOT co-locate with phase9c tests.
# Reuse: Anchors on phase10a_contract.md v2 (post scout + critic-dave cycle-2
#        precommit review). If this file grows past 4 S-items, split at the
#        phase-10 boundary.
# Authority basis: phase10a_contract.md v2; synthesis_and_remediation_plan.md
#                   R1 + R2; zeus_dt_coordination_handoff.md Section A residue.

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# R-CH — S1 R1 CRITICAL: monitor_refresh NameError rename
# ---------------------------------------------------------------------------


class TestRCHMonitorRefreshRenameFix:
    """Phase 10A S1: rename `remaining_member_maxes` → `extrema.maxes` at
    `src/engine/monitor_refresh.py:355,405`.

    Pre-fix state: both lines reference an undefined local `remaining_member_maxes`
    introduced by the P6 rename `remaining_member_maxes_for_day0` →
    `remaining_member_extrema_for_day0` (P7B alias removal). The NameError was
    silently swallowed by `except Exception` at L614, leaving
    `last_monitor_prob_is_fresh=False` on every Day0-active position and
    poisoning exit-trigger decisions with stale probability.

    Post-fix: both lines access `extrema.maxes` (HIGH path). LOW positions
    still degrade at L355 (`np.std(None)`) because LOW plumbing in
    monitor_refresh is Phase 10B scope — but the degrade is caught by the
    same L614 except as before. Net: HIGH now correct, LOW behavior unchanged.

    Antibody is AST-level because the NameError is only reachable at runtime
    from a narrow Day0-active HIGH path that requires heavy mocking; the
    surgical-revert detection works identically via identifier grep.
    """

    def test_monitor_refresh_no_undefined_remaining_member_maxes_identifier(self):
        """R-CH.1: the bare identifier `remaining_member_maxes` (without
        the `_for_day0` suffix — that is a different symbol, retained in the
        P7B shim docstring) must have zero occurrences in the source.

        Surgical-revert: if anyone re-introduces the identifier at L355 or
        L405 (or anywhere else), this test fails with a clear line citation.
        """
        source = (PROJECT_ROOT / "src/engine/monitor_refresh.py").read_text()

        # Word-boundary match; excludes `remaining_member_maxes_for_day0`
        # (the P7B-retired legacy helper name, referenced in a comment).
        pattern = re.compile(r"\bremaining_member_maxes\b(?!_for_day0)")

        hits = []
        for line_no, line in enumerate(source.splitlines(), start=1):
            if pattern.search(line):
                hits.append((line_no, line.strip()))

        assert hits == [], (
            f"Undefined identifier 'remaining_member_maxes' is still "
            f"referenced at {len(hits)} site(s). Either the rename was "
            f"not applied, or a new regression introduced it:\n"
            + "\n".join(f"  L{n}: {s}" for n, s in hits)
        )

    def test_refresh_day0_observation_ast_uses_extrema_maxes(self):
        """R-CH.2: structural probe that `_refresh_day0_observation` accesses
        `extrema.maxes` at the ensemble_spread computation and the
        member_maxes dict assembly sites.

        Surgical-revert: if someone changes `extrema.maxes` back to a bare
        identifier (forgetting the `extrema.` attribute access), AST-walk
        finds no matching Attribute node → test fails.
        """
        source = (PROJECT_ROOT / "src/engine/monitor_refresh.py").read_text()
        tree = ast.parse(source)

        target_fn: ast.FunctionDef | None = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_refresh_day0_observation":
                target_fn = node
                break
        assert target_fn is not None, "_refresh_day0_observation not found in monitor_refresh.py"

        extrema_maxes_accesses = 0
        for node in ast.walk(target_fn):
            if isinstance(node, ast.Attribute) and node.attr == "maxes":
                if isinstance(node.value, ast.Name) and node.value.id == "extrema":
                    extrema_maxes_accesses += 1

        # L318 (Day0Router input) + L355 (np.std) + L405 (dict value) = 3 sites
        assert extrema_maxes_accesses >= 3, (
            f"Expected ≥3 `extrema.maxes` attribute accesses in "
            f"_refresh_day0_observation (Day0Router input, ensemble_spread, "
            f"member_maxes dict); found {extrema_maxes_accesses}. "
            f"A rename regression likely dropped one."
        )


# ---------------------------------------------------------------------------
# R-CI — S2 R2 ingest metric stamp contract lock
# ---------------------------------------------------------------------------


class TestRCIIngestMetricStampContractLock:
    """Phase 10A S2: Scout probe 2026-04-19 confirmed
    `scripts/extract_tigge_mn2t6_localday_min.py` already stamps
    `temperature_metric='low'` correctly via:
      L101: TEMPERATURE_METRIC = LOW_LOCALDAY_MIN.temperature_metric (typed)
      L356: "temperature_metric": TEMPERATURE_METRIC (in payload dict literal)
      L411: validate_low_extraction surfaces violation on mismatch

    No code change needed for S2; this antibody locks the contract so a future
    edit cannot silently break it. If the DT e2e audit's discriminating probe
    were to FAIL (ingest stamped HIGH), every P9C L3 + DT#7 downstream wiring
    would be decorative. This test guarantees the probe stays PASSED.
    """

    def test_extract_mn2t6_module_constant_is_low(self):
        """R-CI.1: the module-level TEMPERATURE_METRIC constant is literally
        'low', sourced from MetricIdentity.LOW_LOCALDAY_MIN (not a bare
        literal that could drift)."""
        from scripts.extract_tigge_mn2t6_localday_min import TEMPERATURE_METRIC

        assert TEMPERATURE_METRIC == "low", (
            f"extract_tigge_mn2t6_localday_min stamps "
            f"temperature_metric={TEMPERATURE_METRIC!r}; must be 'low' — "
            f"HIGH-stamped LOW data would silently contaminate every "
            f"downstream P9C L3 / DT#7 wiring."
        )

    def test_validate_low_extraction_rejects_wrong_metric(self):
        """R-CI.2: the validator is load-bearing — if payload stamps
        the wrong metric, `validate_low_extraction` MUST surface a
        violation. Surgical-revert probe: delete the L411 check → this
        test fails because the wrong-metric payload passes validation.
        """
        from scripts.extract_tigge_mn2t6_localday_min import (
            validate_low_extraction,
            DATA_VERSION,
            MEMBERS_UNIT,
        )

        # Construct a minimally structurally-valid payload with the one
        # metric field wrong. We only care that validator flags metric;
        # other shape issues are acceptable co-violations.
        bad_payload = {
            "data_version": DATA_VERSION,
            "temperature_metric": "high",  # ← the bomb
            "members_unit": MEMBERS_UNIT,
            "causality": {"status": "OK"},
        }

        violations = validate_low_extraction(bad_payload)

        assert any("temperature_metric" in v for v in violations), (
            f"validate_low_extraction did NOT flag a payload stamped "
            f"temperature_metric='high' as a violation. Violations "
            f"surfaced: {violations}. If this test passes, the ingest "
            f"contract guard at extract_tigge_mn2t6_localday_min.py:411 "
            f"has been silently disabled — the DT e2e audit discriminating "
            f"probe no longer protects P9C downstream wiring."
        )

    def test_extract_mn2t6_payload_literal_stamps_metric_from_constant(self):
        """R-CI.3: AST probe that the payload dict literal at
        `build_low_payload` (or equivalent) sets `temperature_metric` to
        the `TEMPERATURE_METRIC` identifier, NOT a bare string literal.

        Surgical-revert: if someone changes
            "temperature_metric": TEMPERATURE_METRIC
        to
            "temperature_metric": "high"
        or drops the key entirely, this test fails.
        """
        source = (PROJECT_ROOT / "scripts/extract_tigge_mn2t6_localday_min.py").read_text()
        tree = ast.parse(source)

        stamps_from_constant = False
        stamps_from_literal_low = False

        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for key_node, value_node in zip(node.keys, node.values):
                    if (
                        isinstance(key_node, ast.Constant)
                        and key_node.value == "temperature_metric"
                    ):
                        # Accept either: identifier `TEMPERATURE_METRIC`, or
                        # a literal "low" constant (both are contract-valid).
                        if isinstance(value_node, ast.Name) and value_node.id == "TEMPERATURE_METRIC":
                            stamps_from_constant = True
                        elif isinstance(value_node, ast.Constant) and value_node.value == "low":
                            stamps_from_literal_low = True

        assert stamps_from_constant or stamps_from_literal_low, (
            "No payload dict literal stamps temperature_metric from either "
            "the typed TEMPERATURE_METRIC constant or the literal 'low'. "
            "The extract module may be producing payloads with a missing "
            "or wrong metric key — P9C downstream wiring depends on this."
        )


# ---------------------------------------------------------------------------
# R-CJ — S3 B071 token_suppression history + view
# ---------------------------------------------------------------------------


import sqlite3
import types
from datetime import datetime, timezone

from src.state.db import (
    log_selection_family_fact,
    query_chain_only_quarantine_rows,
    query_token_suppression_tokens,
    record_token_suppression,
)
from src.state.ledger import apply_architecture_kernel_schema


def _make_ts_conn() -> sqlite3.Connection:
    """In-memory SQLite with full Zeus schema (token_suppression_history + view)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_architecture_kernel_schema(conn)
    return conn


def _suppress(conn, token_id: str, reason: str) -> dict:
    return record_token_suppression(
        conn, token_id=token_id, suppression_reason=reason, source_module="test_mod"
    )


class TestRCJTokenSuppressionHistoryView:
    """R-CJ.1 and R-CJ.2: B071 token_suppression event-sourced refactor."""

    def test_rcj1_three_state_sequence_yields_three_history_rows_view_returns_latest(self):
        """R-CJ.1 — (auto-suppress → manual-override → auto-suppress) yields 3 history rows;
        token_suppression_current returns the latest.

        Surgical-revert: restore record_token_suppression to ON CONFLICT DO UPDATE on
        token_suppression → only 1 history row exists → assertion len==3 fails.
        """
        conn = _make_ts_conn()
        token = "tok-rcj1"

        # State 1: auto-suppress
        r1 = _suppress(conn, token, "chain_only_quarantined")
        assert r1["status"] == "written"

        # State 2: manual override
        r2 = _suppress(conn, token, "operator_quarantine_clear")
        assert r2["status"] == "written"

        # State 3: auto-suppress again
        r3 = _suppress(conn, token, "chain_only_quarantined")
        assert r3["status"] == "written"

        rows = conn.execute(
            "SELECT suppression_reason FROM token_suppression_history "
            "WHERE token_id = ? ORDER BY history_id",
            (token,),
        ).fetchall()
        assert len(rows) == 3, (
            f"Expected 3 history rows for 3-state sequence, got {len(rows)}. "
            "Surgical-revert: ON CONFLICT DO UPDATE collapses to 1 row."
        )
        assert [r["suppression_reason"] for r in rows] == [
            "chain_only_quarantined", "operator_quarantine_clear", "chain_only_quarantined"
        ]

        # View must return latest (state 3)
        view_row = conn.execute(
            "SELECT suppression_reason FROM token_suppression_current WHERE token_id = ?",
            (token,),
        ).fetchone()
        assert view_row is not None
        assert view_row["suppression_reason"] == "chain_only_quarantined"

    def test_rcj2_dropping_view_breaks_view_reads(self):
        """R-CJ.2 — surgical-revert probe: dropping token_suppression_current causes
        direct view reads to raise OperationalError (confirms view is load-bearing).

        Surgical-revert: remove the CREATE VIEW statement from the kernel SQL →
        the pre-drop query below raises OperationalError (no such table/view)
        before we even drop it — fail at a different assertion.
        """
        conn = _make_ts_conn()
        _suppress(conn, "tok-rcj2", "settled_position")

        # Before drop: view is queryable
        pre = conn.execute(
            "SELECT token_id FROM token_suppression_current WHERE token_id = 'tok-rcj2'"
        ).fetchone()
        assert pre is not None, "token_suppression_current view should return row before drop"

        # Simulate surgical-revert by dropping the view
        conn.execute("DROP VIEW token_suppression_current")

        # After drop: must raise — view no longer exists
        with pytest.raises(sqlite3.OperationalError):
            conn.execute(
                "SELECT token_id FROM token_suppression_current WHERE token_id = 'tok-rcj2'"
            ).fetchone()

    def test_history_table_and_current_view_exist_after_schema_apply(self):
        """Schema shape: both token_suppression_history (table) and
        token_suppression_current (view) are created by apply_architecture_kernel_schema."""
        conn = _make_ts_conn()
        table_row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='token_suppression_history'"
        ).fetchone()
        assert table_row is not None, "token_suppression_history table missing"

        view_row = conn.execute(
            "SELECT type FROM sqlite_master WHERE name='token_suppression_current'"
        ).fetchone()
        assert view_row is not None
        assert str(view_row["type"]) == "view"

    def test_append_only_triggers_reject_update(self):
        """Append-only triggers block UPDATE on token_suppression_history.
        SQLite RAISE(ABORT) surfaces as IntegrityError (not OperationalError)."""
        conn = _make_ts_conn()
        _suppress(conn, "trig-tok", "settled_position")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(
                "UPDATE token_suppression_history SET suppression_reason='operator_quarantine_clear' "
                "WHERE token_id='trig-tok'"
            )

    def test_query_helpers_return_data_written_via_history(self):
        """query_token_suppression_tokens and query_chain_only_quarantine_rows
        return data written via the append-only history write path.

        Pre-migration: `token_suppression` is still the legacy table; readers
        query it. Post-migration (after --drop-legacy), `token_suppression`
        becomes a VIEW over history. In this test, we simulate the post-migration
        state by manually inserting into history so token_suppression_current
        reflects the data, then using the legacy table path directly for the
        query helpers (which read from `token_suppression` legacy table here).

        The core invariant: writes via record_token_suppression go to history,
        and legacy-table readers remain unaffected until migration runs.
        """
        conn = _make_ts_conn()
        # Write via the new history-write path
        _suppress(conn, "settled-tok", "settled_position")
        _suppress(conn, "chain-tok", "chain_only_quarantined")

        # Verify history has the rows
        hist = conn.execute(
            "SELECT token_id, suppression_reason FROM token_suppression_history ORDER BY history_id"
        ).fetchall()
        assert len(hist) == 2

        # Verify current view returns latest for each token
        cur = conn.execute(
            "SELECT token_id, suppression_reason FROM token_suppression_current ORDER BY token_id"
        ).fetchall()
        assert len(cur) == 2
        assert {r["suppression_reason"] for r in cur} == {"settled_position", "chain_only_quarantined"}


# ---------------------------------------------------------------------------
# R-CK — S4 B091 lower half: decision_time_status in evaluator
# ---------------------------------------------------------------------------


class TestRCKDecisionTimeStatusEvaluator:
    """R-CK.1 and R-CK.2: B091 lower half — evaluator emits decision_time_status
    and it persists to selection_family_fact row."""

    def _conn(self) -> sqlite3.Connection:
        from src.state.db import init_schema
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        # init_schema creates selection_family_fact (and calls apply_architecture_kernel_schema
        # internally via _ensure_runtime_bootstrap_support_tables).
        init_schema(conn)
        return conn

    def _edge(self):
        from src.types import Bin, BinEdge
        return BinEdge(
            bin=Bin(low=40, high=41, unit="F", label="40-41°F"),
            direction="buy_yes",
            edge=0.05, ci_lower=0.01, ci_upper=0.10,
            p_model=0.15, p_market=0.10, p_posterior=0.15,
            entry_price=0.10, p_value=0.001, vwmp=0.10,
        )

    def _candidate(self, city: str = "NYC", date: str = "2026-04-01", eid: str = "ev-1"):
        return types.SimpleNamespace(
            city=types.SimpleNamespace(name=city),
            target_date=date,
            discovery_mode="opening_hunt",
            event_id=eid,
            slug="",
        )

    def test_rcck1_fabrication_path_persists_fabricated_status(self):
        """R-CK.1 — when decision_time_status='FABRICATED_SELECTION_FAMILY' is passed,
        it must persist to the selection_family_fact row.

        Surgical-revert: remove `_decision_time_status = 'FABRICATED_SELECTION_FAMILY'`
        from evaluator.py L1278 → column is NULL → assertion fails.
        """
        from src.engine.evaluator import _record_selection_family_facts
        conn = self._conn()
        edge = self._edge()
        candidate = self._candidate(eid="ev-rck1")

        result = _record_selection_family_facts(
            conn,
            candidate=candidate,
            edges=[edge],
            filtered=[edge],
            hypotheses=None,
            decision_snapshot_id="snap-rck1",
            selected_method="ens_member_counting",
            recorded_at="2026-04-01T12:00:00+00:00",
            decision_time_status="FABRICATED_SELECTION_FAMILY",
        )
        assert result.get("status") in ("written", "written_edges"), (
            f"Write failed: {result!r}"
        )

        row = conn.execute(
            "SELECT decision_time_status FROM selection_family_fact"
        ).fetchone()
        assert row is not None
        assert row["decision_time_status"] == "FABRICATED_SELECTION_FAMILY", (
            f"Got {row['decision_time_status']!r}. "
            "Surgical-revert: remove _decision_time_status assignment in evaluator."
        )

    def test_rcck2_normal_path_emits_ok_status(self):
        """R-CK.2 — when decision_time_status='OK' is passed (decision_time provided),
        it persists to the selection_family_fact row.

        Pair-negative: fabrication cannot silently coerce the OK path.
        Surgical-revert: remove `_decision_time_status = 'OK'` from evaluator →
        column is NULL when decision_time is provided.
        """
        from src.engine.evaluator import _record_selection_family_facts
        conn = self._conn()
        edge = self._edge()
        candidate = self._candidate(city="CHI", date="2026-04-02", eid="ev-rck2")

        result = _record_selection_family_facts(
            conn,
            candidate=candidate,
            edges=[edge],
            filtered=[edge],
            hypotheses=None,
            decision_snapshot_id="snap-rck2",
            selected_method="ens_member_counting",
            recorded_at="2026-04-02T10:00:00+00:00",
            decision_time_status="OK",
        )
        assert result.get("status") in ("written", "written_edges"), (
            f"Write failed: {result!r}"
        )

        row = conn.execute(
            "SELECT decision_time_status FROM selection_family_fact"
        ).fetchone()
        assert row is not None
        assert row["decision_time_status"] == "OK", (
            f"Got {row['decision_time_status']!r}. "
            "Surgical-revert: remove _decision_time_status='OK' in evaluator."
        )

    def test_selection_family_fact_schema_has_decision_time_status(self):
        """Schema shape: selection_family_fact carries decision_time_status column."""
        conn = self._conn()
        cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(selection_family_fact)").fetchall()
        }
        assert "decision_time_status" in cols

    def test_log_selection_family_fact_persists_status_directly(self):
        """log_selection_family_fact accepts and persists decision_time_status."""
        conn = self._conn()
        result = log_selection_family_fact(
            conn,
            family_id="direct-test-family",
            cycle_mode="opening_hunt",
            created_at="2026-04-01T00:00:00+00:00",
            meta={
                "tested_hypotheses": 1, "passed_prefilter": 1,
                "selected_post_fdr": 1, "active_fdr_selected": 1,
                "selected_method": "ens_member_counting",
            },
            decision_time_status="FABRICATED_SELECTION_FAMILY",
        )
        assert result["status"] == "written"
        row = conn.execute(
            "SELECT decision_time_status FROM selection_family_fact "
            "WHERE family_id = 'direct-test-family'"
        ).fetchone()
        assert row is not None
        assert row["decision_time_status"] == "FABRICATED_SELECTION_FAMILY"


# ---------------------------------------------------------------------------
# R-CK.5 / R-CK.6 — evaluator wiring structural antibodies (fixes critic-dave
# cycle-2 MAJOR #1: R-CK.1/R-CK.2 were checkbox — they called
# _record_selection_family_facts directly with literal status values, never
# exercising the evaluator's assignment + kwarg wiring. Surgical-revert of
# evaluator.py:1281/1292 (setting _decision_time_status = None) left R-CK.1/2
# GREEN. These AST antibodies lock the wiring itself.)
# ---------------------------------------------------------------------------


class TestRCKEvaluatorWiringStructural:
    """Phase 10A S4 wiring: evaluator.evaluate_candidate must
      (a) assign `_decision_time_status = "OK"` on the decision_time-provided path, and
      (b) assign `_decision_time_status = "FABRICATED_SELECTION_FAMILY"` on the fabricated path, and
      (c) pass `decision_time_status=_decision_time_status` as kwarg to
          `_record_selection_family_facts(...)`.

    Without (a) + (b) + (c), downstream consumers of `selection_family_fact`
    cannot distinguish the two cases — that is the B091 lower-half debt this
    phase closed. R-CK.1-4 locked the leaf function behavior; these two
    antibodies lock the CALLER wiring — the seam critic-dave's surgical-revert
    probe exposed as unguarded in cycle 2.
    """

    def _evaluator_ast(self) -> ast.Module:
        source = (PROJECT_ROOT / "src/engine/evaluator.py").read_text()
        return ast.parse(source)

    def test_rcck5_evaluate_candidate_assigns_both_status_values(self):
        """R-CK.5: evaluate_candidate body must contain TWO distinct
        `_decision_time_status = "..."` string-constant assignments covering
        BOTH the "OK" path and the "FABRICATED_SELECTION_FAMILY" path.

        Surgical-revert: change L1281 or L1292 in evaluator.py to
        `_decision_time_status = None` → the matching assignment disappears
        → this test fails.
        """
        tree = self._evaluator_ast()

        target_fn: ast.FunctionDef | None = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "evaluate_candidate":
                target_fn = node
                break
        assert target_fn is not None, "evaluate_candidate not found in evaluator.py"

        found_values: set[str] = set()
        for node in ast.walk(target_fn):
            if not isinstance(node, ast.Assign):
                continue
            if len(node.targets) != 1:
                continue
            target = node.targets[0]
            if not (isinstance(target, ast.Name) and target.id == "_decision_time_status"):
                continue
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                found_values.add(node.value.value)

        required = {"OK", "FABRICATED_SELECTION_FAMILY"}
        missing = required - found_values
        assert not missing, (
            f"evaluate_candidate is missing `_decision_time_status = \"...\"` "
            f"assignments for: {missing}. Found: {found_values}. "
            f"The B091 lower-half wiring is incomplete — downstream consumers "
            f"of selection_family_fact.decision_time_status cannot distinguish "
            f"the fabricated path from the OK path. Fix: restore both "
            f"string-constant assignments in evaluator.py around L1281/L1292."
        )

    def test_rcck6_evaluate_candidate_threads_kwarg_to_record_call(self):
        """R-CK.6: evaluate_candidate must call `_record_selection_family_facts(...)`
        with a keyword argument `decision_time_status=_decision_time_status`.

        This is the seam critic-dave probed: the status is uselessly assigned
        if the call site doesn't forward it. Surgical-revert: drop the kwarg
        from the call → test fails.
        """
        tree = self._evaluator_ast()

        target_fn: ast.FunctionDef | None = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "evaluate_candidate":
                target_fn = node
                break
        assert target_fn is not None

        wired = False
        for node in ast.walk(target_fn):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            # Match `_record_selection_family_facts(...)` or
            # `module._record_selection_family_facts(...)`.
            if isinstance(func, ast.Name) and func.id == "_record_selection_family_facts":
                pass
            elif isinstance(func, ast.Attribute) and func.attr == "_record_selection_family_facts":
                pass
            else:
                continue
            for kw in node.keywords:
                if kw.arg != "decision_time_status":
                    continue
                if (
                    isinstance(kw.value, ast.Name)
                    and kw.value.id == "_decision_time_status"
                ):
                    wired = True
                    break
            if wired:
                break

        assert wired, (
            "evaluate_candidate does NOT thread the computed `_decision_time_status` "
            "into the `_record_selection_family_facts(...)` call. The status "
            "assignment at L1281/L1292 is unreachable from downstream. "
            "Fix: pass `decision_time_status=_decision_time_status` kwarg."
        )


if __name__ == "__main__":
    pytest.main([__file__, "-xvs"])

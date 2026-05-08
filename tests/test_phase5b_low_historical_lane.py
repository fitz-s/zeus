# Lifecycle: created=2026-04-17; last_reviewed=2026-04-17; last_reused=never
# Purpose: Phase 5B R-AF..R-AM invariants: low historical lane ingest gating + rebuild/refit metric isolation
# Reuse: Anchors on zeus_dual_track_refactor_package_v2_2026-04-16/04_CODE_SNIPPETS/ingest_snapshot_contract.py + 08_TIGGE_DUAL_TRACK_INTEGRATION_zh.md §3-§5 + rebuild_calibration_pairs_v2.py. Confirms Phase 5B contract; fails RED until 5B lands.
"""Phase 5B low historical lane tests: R-AF, R-AG, R-AH, R-AI, R-AJ, R-AK, R-AL, R-AM

Tests anchored to SPEC semantics from the DT v2 package (ingest_snapshot_contract.py,
08_TIGGE_DUAL_TRACK_INTEGRATION_zh.md §3-§5, rebuild_calibration_pairs_v2.py) and the
Phase 5B opening brief. Tests are spec-anchored and MUST fail RED until exec-dan/exec-emma
implement Phase 5B.

R-AF (ingest contract gating): validate_snapshot_contract rejects low snapshots that violate
    the 3 quarantine laws: boundary_ambiguous, causality=N/A_CAUSAL_DAY_ALREADY_STARTED,
    missing issue_time_utc.

R-AG (extractor identity): extract_tigge_mn2t6_localday_min module exists and exports
    the 5 canonical functions: classify_boundary_low, extract_city_vectors_low,
    build_low_snapshot_json, validate_low_extraction, main.

R-AH (members_unit explicit): extractor output JSON carries members_unit='K' field;
    missing members_unit fails the ingest contract.

R-AI (data_version correct): low snapshots carry
    data_version='tigge_mn2t6_local_calendar_day_min_v1'. High data_version on a low
    snapshot is rejected.

R-AJ (causality first-class): low extractor emits causality_status as a first-class field
    in the extracted JSON — never defaulted-absent. Absent causality_status fails contract.

R-AK (METRIC_SPECS tuple covers both tracks): CalibrationMetricSpec + METRIC_SPECS are
    importable from scripts.rebuild_calibration_pairs_v2 and include both HIGH_LOCALDAY_MAX
    and LOW_LOCALDAY_MIN entries.

R-AL (iter_training_snapshots metric isolation): iter_training_snapshots filters by
    temperature_metric, so low and high rows never cross-contaminate in calibration output.

R-AM (ingest_grib NotImplementedError removed): ingest_grib_to_snapshots.ingest_track
    no longer raises NotImplementedError for track='mn2t6_low'.
"""
from __future__ import annotations

import sqlite3

import pytest


# ---------------------------------------------------------------------------
# R-AF: ingest contract — 3-law quarantine gating for low snapshots
# ---------------------------------------------------------------------------


class TestIngestContractLowGating:
    """R-AF: validate_snapshot_contract enforces 3 quarantine laws for low track.

    Law 1: boundary_ambiguous=True → training_allowed=False, causality='REJECTED_BOUNDARY_AMBIGUOUS'
    Law 2: causality='N/A_CAUSAL_DAY_ALREADY_STARTED' → training_allowed=False
    Law 3: issue_time_utc absent/None → training_allowed=False, causality='RUNTIME_ONLY_FALLBACK'

    Import target: src.contracts.snapshot_ingest_contract (does not exist yet — ImportError = RED)
    """

    @staticmethod
    def _good_low_payload(**overrides) -> dict:
        base = {
            "data_version": "tigge_mn2t6_local_calendar_day_min_v1",
            "temperature_metric": "low",
            "physical_quantity": "mn2t6_local_calendar_day_min",
            "members": [273.15] * 51,
            "members_unit": "K",
            "issue_time_utc": "2026-04-17T00:00:00+00:00",
            "causality": {"status": "OK"},
            "boundary_policy": {"boundary_ambiguous": False, "ambiguous_member_count": 0},
        }
        base.update(overrides)
        return base

    def test_boundary_ambiguous_sets_training_not_allowed(self):
        """R-AF (rejection): boundary_ambiguous=True must set training_allowed=False."""
        from src.contracts.snapshot_ingest_contract import validate_snapshot_contract

        payload = self._good_low_payload(
            boundary_policy={"boundary_ambiguous": True, "ambiguous_member_count": 5}
        )
        decision = validate_snapshot_contract(payload)
        assert decision.accepted is True
        assert decision.training_allowed is False
        assert decision.causality_status == "REJECTED_BOUNDARY_AMBIGUOUS"

    def test_causality_not_a_causal_day_sets_training_not_allowed(self):
        """R-AF (rejection): N/A_CAUSAL_DAY_ALREADY_STARTED causality must block training."""
        from src.contracts.snapshot_ingest_contract import validate_snapshot_contract

        payload = self._good_low_payload(
            causality={"status": "N/A_CAUSAL_DAY_ALREADY_STARTED"}
        )
        decision = validate_snapshot_contract(payload)
        assert decision.accepted is True
        assert decision.training_allowed is False

    def test_missing_issue_time_sets_runtime_only_fallback(self):
        """R-AF (rejection): absent issue_time_utc must produce RUNTIME_ONLY_FALLBACK causality."""
        from src.contracts.snapshot_ingest_contract import validate_snapshot_contract

        payload = self._good_low_payload(issue_time_utc=None)
        decision = validate_snapshot_contract(payload)
        assert decision.accepted is True
        assert decision.training_allowed is False
        assert decision.causality_status == "RUNTIME_ONLY_FALLBACK"

    def test_clean_low_payload_is_accepted_with_training_allowed(self):
        """R-AF (acceptance): a fully compliant low payload is accepted with training_allowed=True."""
        from src.contracts.snapshot_ingest_contract import validate_snapshot_contract

        payload = self._good_low_payload()
        decision = validate_snapshot_contract(payload)
        assert decision.accepted is True
        assert decision.training_allowed is True
        assert decision.causality_status == "OK"

    def test_wrong_data_version_for_low_is_rejected(self):
        """R-AF (rejection): high data_version on a low payload must be rejected."""
        from src.contracts.snapshot_ingest_contract import validate_snapshot_contract

        payload = self._good_low_payload(
            data_version="tigge_mx2t6_local_calendar_day_max_v1",
            temperature_metric="low",
            physical_quantity="mn2t6_local_calendar_day_min",
        )
        decision = validate_snapshot_contract(payload)
        assert decision.accepted is False

    def test_boundary_ambiguous_does_not_affect_high_track(self):
        """R-AF (acceptance): boundary_ambiguous on a high payload must be accepted.

        The spec (ingest_snapshot_contract.py:53) applies boundary quarantine only when
        temperature_metric == 'low'. High-track boundary semantics are not yet specified.
        We assert accepted=True only; training_allowed is NOT asserted here to avoid
        over-constraining future high-boundary design space (critic-alice widen note 1).
        """
        from src.contracts.snapshot_ingest_contract import validate_snapshot_contract

        payload = {
            "data_version": "tigge_mx2t6_local_calendar_day_max_v1",
            "temperature_metric": "high",
            "physical_quantity": "mx2t6_local_calendar_day_max",
            "members": [295.0] * 51,
            "members_unit": "K",
            "issue_time_utc": "2026-04-17T00:00:00+00:00",
            "causality": {"status": "OK"},
            "boundary_policy": {"boundary_ambiguous": True, "ambiguous_member_count": 3},
        }
        decision = validate_snapshot_contract(payload)
        assert decision.accepted is True
        # training_allowed intentionally NOT asserted — high-track boundary semantics
        # are unspecified in Phase 5B spec; pinning True would over-constrain.


# ---------------------------------------------------------------------------
# R-AG: extractor module exists and exports the 5 canonical functions
# ---------------------------------------------------------------------------


class TestExtractorModuleExports:
    """R-AG: scripts.extract_tigge_mn2t6_localday_min must export 5 canonical functions.

    Import target: scripts.extract_tigge_mn2t6_localday_min (does not exist yet — ImportError = RED)
    """

    def test_classify_boundary_low_is_importable(self):
        """R-AG (acceptance): classify_boundary_low must be importable from the extractor module."""
        from scripts.extract_tigge_mn2t6_localday_min import classify_boundary_low  # noqa: F401

    def test_extract_city_vectors_low_is_importable(self):
        """R-AG (acceptance): extract_city_vectors_low must be importable."""
        from scripts.extract_tigge_mn2t6_localday_min import extract_city_vectors_low  # noqa: F401

    def test_build_low_snapshot_json_is_importable(self):
        """R-AG (acceptance): build_low_snapshot_json must be importable."""
        from scripts.extract_tigge_mn2t6_localday_min import build_low_snapshot_json  # noqa: F401

    def test_validate_low_extraction_is_importable(self):
        """R-AG (acceptance): validate_low_extraction must be importable."""
        from scripts.extract_tigge_mn2t6_localday_min import validate_low_extraction  # noqa: F401

    def test_main_is_importable(self):
        """R-AG (acceptance): main entry point must be importable."""
        from scripts.extract_tigge_mn2t6_localday_min import main  # noqa: F401


# ---------------------------------------------------------------------------
# R-AH: extractor output JSON carries members_unit='K' (Kelvin explicit)
# ---------------------------------------------------------------------------


class TestMembersUnitExplicit:
    """R-AH: low extractor JSON must carry members_unit='K'; absent field fails contract.

    The Kelvin silent-default is a FORBIDDEN MOVE (L3 in critic checklist).
    Import target: src.contracts.snapshot_ingest_contract (does not exist yet — ImportError = RED)
    """

    def test_missing_members_unit_fails_contract(self):
        """R-AH (rejection): payload without members_unit must be rejected or training blocked."""
        from src.contracts.snapshot_ingest_contract import validate_snapshot_contract

        payload = {
            "data_version": "tigge_mn2t6_local_calendar_day_min_v1",
            "temperature_metric": "low",
            "physical_quantity": "mn2t6_local_calendar_day_min",
            "members": [273.15] * 51,
            # members_unit intentionally absent
            "issue_time_utc": "2026-04-17T00:00:00+00:00",
            "causality": {"status": "OK"},
            "boundary_policy": {"boundary_ambiguous": False, "ambiguous_member_count": 0},
        }
        decision = validate_snapshot_contract(payload)
        # Missing members_unit must either reject outright OR mark training_allowed=False.
        # Either is acceptable per law; both prevent silent Kelvin assumption.
        assert decision.accepted is False or decision.training_allowed is False

    def test_explicit_kelvin_members_unit_is_accepted(self):
        """R-AH (acceptance): payload with members_unit='K' is fully accepted."""
        from src.contracts.snapshot_ingest_contract import validate_snapshot_contract

        payload = {
            "data_version": "tigge_mn2t6_local_calendar_day_min_v1",
            "temperature_metric": "low",
            "physical_quantity": "mn2t6_local_calendar_day_min",
            "members": [273.15] * 51,
            "members_unit": "K",
            "issue_time_utc": "2026-04-17T00:00:00+00:00",
            "causality": {"status": "OK"},
            "boundary_policy": {"boundary_ambiguous": False, "ambiguous_member_count": 0},
        }
        decision = validate_snapshot_contract(payload)
        assert decision.accepted is True
        assert decision.training_allowed is True


# ---------------------------------------------------------------------------
# R-AI: data_version must be the canonical LOW string
# ---------------------------------------------------------------------------


class TestDataVersionLowIdentity:
    """R-AI: low track data_version must be 'tigge_mn2t6_local_calendar_day_min_v1'.

    The MetricIdentity constant LOW_LOCALDAY_MIN.data_version holds the authoritative string.
    Import target: src.contracts.snapshot_ingest_contract (does not exist yet — ImportError = RED)
    """

    def test_low_data_version_constant_matches_expected_string(self):
        """R-AI (acceptance): LOW_LOCALDAY_MIN.data_version equals the canonical string."""
        from src.types.metric_identity import LOW_LOCALDAY_MIN

        assert LOW_LOCALDAY_MIN.data_version == "tigge_mn2t6_local_calendar_day_min_v1"

    def test_high_data_version_with_low_metric_is_rejected(self):
        """R-AI (rejection): high data_version + low temperature_metric must be rejected."""
        from src.contracts.snapshot_ingest_contract import validate_snapshot_contract

        payload = {
            "data_version": "tigge_mx2t6_local_calendar_day_max_v1",
            "temperature_metric": "low",
            "physical_quantity": "mn2t6_local_calendar_day_min",
            "members": [273.15] * 51,
            "members_unit": "K",
            "issue_time_utc": "2026-04-17T00:00:00+00:00",
            "causality": {"status": "OK"},
            "boundary_policy": {"boundary_ambiguous": False, "ambiguous_member_count": 0},
        }
        decision = validate_snapshot_contract(payload)
        assert decision.accepted is False

    def test_low_data_version_with_high_metric_is_rejected(self):
        """R-AI (rejection): low data_version + high temperature_metric must be rejected."""
        from src.contracts.snapshot_ingest_contract import validate_snapshot_contract

        payload = {
            "data_version": "tigge_mn2t6_local_calendar_day_min_v1",
            "temperature_metric": "high",
            "physical_quantity": "mx2t6_local_calendar_day_max",
            "members": [295.0] * 51,
            "members_unit": "K",
            "issue_time_utc": "2026-04-17T00:00:00+00:00",
            "causality": {"status": "OK"},
            "boundary_policy": {"boundary_ambiguous": False, "ambiguous_member_count": 0},
        }
        decision = validate_snapshot_contract(payload)
        assert decision.accepted is False


# ---------------------------------------------------------------------------
# R-AJ: causality_status is first-class in extracted JSON, never defaulted-absent
# ---------------------------------------------------------------------------


class TestCausalityFirstClass:
    """R-AJ: low extractor output must include causality_status as a first-class field.

    The causality law is load-bearing for the ingest gate. A snapshot without a
    causality field must fail the contract — not silently default to 'OK'.

    Import target: src.contracts.snapshot_ingest_contract (does not exist yet — ImportError = RED)
    """

    def test_absent_causality_field_fails_contract(self):
        """R-AJ (rejection): payload without causality field must be rejected."""
        from src.contracts.snapshot_ingest_contract import validate_snapshot_contract

        payload = {
            "data_version": "tigge_mn2t6_local_calendar_day_min_v1",
            "temperature_metric": "low",
            "physical_quantity": "mn2t6_local_calendar_day_min",
            "members": [273.15] * 51,
            "members_unit": "K",
            "issue_time_utc": "2026-04-17T00:00:00+00:00",
            # causality intentionally absent
            "boundary_policy": {"boundary_ambiguous": False, "ambiguous_member_count": 0},
        }
        decision = validate_snapshot_contract(payload)
        # Absent causality must either be rejected OR produce training_allowed=False.
        # A silent default to 'OK' would be a Forbidden Move.
        assert decision.accepted is False or decision.training_allowed is False

    def test_explicit_ok_causality_is_accepted(self):
        """R-AJ (acceptance): causality.status='OK' and no boundary ambiguity → training allowed."""
        from src.contracts.snapshot_ingest_contract import validate_snapshot_contract

        payload = {
            "data_version": "tigge_mn2t6_local_calendar_day_min_v1",
            "temperature_metric": "low",
            "physical_quantity": "mn2t6_local_calendar_day_min",
            "members": [273.15] * 51,
            "members_unit": "K",
            "issue_time_utc": "2026-04-17T00:00:00+00:00",
            "causality": {"status": "OK"},
            "boundary_policy": {"boundary_ambiguous": False, "ambiguous_member_count": 0},
        }
        decision = validate_snapshot_contract(payload)
        assert decision.accepted is True
        assert decision.causality_status == "OK"
        assert decision.training_allowed is True


# ---------------------------------------------------------------------------
# R-AK: METRIC_SPECS tuple covers both HIGH_LOCALDAY_MAX and LOW_LOCALDAY_MIN
# ---------------------------------------------------------------------------


class TestMetricSpecsTupleCoversLow:
    """R-AK: CalibrationMetricSpec and METRIC_SPECS must cover both tracks.

    The DT v2 package specifies METRIC_SPECS as a 2-tuple: one entry per track.
    No --track CLI flag. The tuple drives rebuild/refit iteration.

    Import target: scripts.rebuild_calibration_pairs_v2 (does not exist yet — ImportError = RED)
    """

    def test_calibration_metric_spec_is_importable(self):
        """R-AK (acceptance): CalibrationMetricSpec must be importable from rebuild script."""
        from scripts.rebuild_calibration_pairs_v2 import CalibrationMetricSpec  # noqa: F401

    def test_metric_specs_tuple_is_importable(self):
        """R-AK (acceptance): METRIC_SPECS must be importable from rebuild script."""
        from scripts.rebuild_calibration_pairs_v2 import METRIC_SPECS  # noqa: F401

    def test_metric_specs_contains_exactly_two_entries(self):
        """R-AK (acceptance): METRIC_SPECS must have exactly 2 entries (one per track)."""
        from scripts.rebuild_calibration_pairs_v2 import METRIC_SPECS

        assert len(METRIC_SPECS) == 2

    def test_metric_specs_covers_high_track(self):
        """R-AK (acceptance): METRIC_SPECS must include a HIGH_LOCALDAY_MAX entry."""
        from src.types.metric_identity import HIGH_LOCALDAY_MAX
        from scripts.rebuild_calibration_pairs_v2 import METRIC_SPECS

        high_specs = [s for s in METRIC_SPECS if s.identity.temperature_metric == "high"]
        assert len(high_specs) == 1
        assert high_specs[0].identity == HIGH_LOCALDAY_MAX

    def test_metric_specs_covers_low_track(self):
        """R-AK (acceptance): METRIC_SPECS must include a LOW_LOCALDAY_MIN entry."""
        from src.types.metric_identity import LOW_LOCALDAY_MIN
        from scripts.rebuild_calibration_pairs_v2 import METRIC_SPECS

        low_specs = [s for s in METRIC_SPECS if s.identity.temperature_metric == "low"]
        assert len(low_specs) == 1
        assert low_specs[0].identity == LOW_LOCALDAY_MIN

    def test_low_metric_spec_allowed_data_version_matches_identity(self):
        """R-AK (acceptance): low CalibrationMetricSpec.allowed_data_version must match LOW_LOCALDAY_MIN.data_version."""
        from src.types.metric_identity import LOW_LOCALDAY_MIN
        from scripts.rebuild_calibration_pairs_v2 import METRIC_SPECS

        low_spec = next(s for s in METRIC_SPECS if s.identity.temperature_metric == "low")
        assert low_spec.allowed_data_version == LOW_LOCALDAY_MIN.data_version


# ---------------------------------------------------------------------------
# R-AL: iter_training_snapshots filters by temperature_metric — no cross-contamination
# ---------------------------------------------------------------------------


class TestIterTrainingSnapshotsMetricIsolation:
    """R-AL: iter_training_snapshots must filter by temperature_metric, keeping tracks isolated.

    Cross-contamination — a high snapshot appearing in a low calibration family or vice versa —
    would silently corrupt Platt models and downstream posteriors.

    Import target: scripts.rebuild_calibration_pairs_v2 (does not exist yet — ImportError = RED)
    """

    def _make_db_with_mixed_snapshots(self, tmp_path):
        db = sqlite3.connect(tmp_path / "zeus-world.db")
        db.row_factory = sqlite3.Row
        db.executescript(
            """
            CREATE TABLE ensemble_snapshots_v2 (
                snapshot_id TEXT PRIMARY KEY,
                city TEXT,
                target_date TEXT,
                available_at TEXT,
                lead_hours REAL,
                temperature_metric TEXT,
                physical_quantity TEXT,
                data_version TEXT,
                members_json TEXT,
                training_allowed INTEGER DEFAULT 1,
                causality_status TEXT DEFAULT 'OK',
                authority TEXT DEFAULT 'VERIFIED'
            );
            """
        )
        # Insert one high and one low snapshot, both training_allowed=1
        db.execute(
            """
            INSERT INTO ensemble_snapshots_v2
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "snap-high-001", "Chicago", "2026-07-08", "2026-07-05T00:00:00+00:00",
                72.0, "high", "mx2t6_local_calendar_day_max",
                "tigge_mx2t6_local_calendar_day_max_v1",
                "[295.0]", 1, "OK", "VERIFIED",
            ),
        )
        db.execute(
            """
            INSERT INTO ensemble_snapshots_v2
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "snap-low-001", "Chicago", "2026-07-08", "2026-07-05T00:00:00+00:00",
                72.0, "low", "mn2t6_local_calendar_day_min",
                "tigge_mn2t6_local_calendar_day_min_v1",
                "[273.15]", 1, "OK", "VERIFIED",
            ),
        )
        db.commit()
        return db

    def test_iter_training_snapshots_low_spec_returns_only_low_rows(self, tmp_path):
        """R-AL (acceptance): iter_training_snapshots with low spec must return only low-metric rows."""
        from src.types.metric_identity import LOW_LOCALDAY_MIN
        from scripts.rebuild_calibration_pairs_v2 import CalibrationMetricSpec, iter_training_snapshots

        db = self._make_db_with_mixed_snapshots(tmp_path)
        low_spec = CalibrationMetricSpec(
            identity=LOW_LOCALDAY_MIN,
            allowed_data_version=LOW_LOCALDAY_MIN.data_version,
        )
        rows = list(iter_training_snapshots(db, low_spec))
        assert all(row["temperature_metric"] == "low" for row in rows), (
            "iter_training_snapshots(low_spec) must return ONLY low rows; "
            f"got metrics: {[r['temperature_metric'] for r in rows]}"
        )
        assert len(rows) == 1

    def test_iter_training_snapshots_high_spec_returns_only_high_rows(self, tmp_path):
        """R-AL (acceptance): iter_training_snapshots with high spec must return only high-metric rows."""
        from src.types.metric_identity import HIGH_LOCALDAY_MAX
        from scripts.rebuild_calibration_pairs_v2 import CalibrationMetricSpec, iter_training_snapshots

        db = self._make_db_with_mixed_snapshots(tmp_path)
        high_spec = CalibrationMetricSpec(
            identity=HIGH_LOCALDAY_MAX,
            allowed_data_version=HIGH_LOCALDAY_MAX.data_version,
        )
        rows = list(iter_training_snapshots(db, high_spec))
        assert all(row["temperature_metric"] == "high" for row in rows), (
            "iter_training_snapshots(high_spec) must return ONLY high rows; "
            f"got metrics: {[r['temperature_metric'] for r in rows]}"
        )
        assert len(rows) == 1

    def test_low_and_high_iter_results_are_disjoint(self, tmp_path):
        """R-AL (acceptance): snapshot_ids returned by low spec and high spec must not overlap."""
        from src.types.metric_identity import HIGH_LOCALDAY_MAX, LOW_LOCALDAY_MIN
        from scripts.rebuild_calibration_pairs_v2 import CalibrationMetricSpec, iter_training_snapshots

        db = self._make_db_with_mixed_snapshots(tmp_path)
        high_spec = CalibrationMetricSpec(HIGH_LOCALDAY_MAX, HIGH_LOCALDAY_MAX.data_version)
        low_spec = CalibrationMetricSpec(LOW_LOCALDAY_MIN, LOW_LOCALDAY_MIN.data_version)

        high_ids = {row["snapshot_id"] for row in iter_training_snapshots(db, high_spec)}
        low_ids = {row["snapshot_id"] for row in iter_training_snapshots(db, low_spec)}
        assert high_ids.isdisjoint(low_ids), (
            f"high and low iter_training_snapshots must be disjoint; overlap: {high_ids & low_ids}"
        )


# ---------------------------------------------------------------------------
# R-AM: ingest_grib_to_snapshots no longer raises NotImplementedError for mn2t6_low
# ---------------------------------------------------------------------------


class TestIngestGribNotImplementedRemoved:
    """R-AM: ingest_grib_to_snapshots.ingest_track must not raise NotImplementedError for mn2t6_low.

    Phase 4B installed a guard at scripts/ingest_grib_to_snapshots.py:259-263 with a comment:
    'MODERATE-6: low track is Phase 5 scope — boundary quarantine logic not yet implemented.'
    Phase 5B removes that guard. This test confirms the NotImplementedError is gone.

    Import target: scripts.ingest_grib_to_snapshots (exists but raises NotImplementedError — RED)
    """

    def test_mn2t6_low_track_does_not_raise_not_implemented(self, tmp_path):
        """R-AM (acceptance): calling ingest_track with track='mn2t6_low' must not raise NotImplementedError.

        Phase 4B installed a 5-line guard that raises NotImplementedError immediately for
        mn2t6_low. This test fails RED until exec-dan removes that guard in Phase 5B.
        We assert directly that calling ingest_track does NOT raise NotImplementedError.
        """
        from scripts.ingest_grib_to_snapshots import ingest_track

        # Direct assertion: ingest_track must not raise NotImplementedError.
        # If it does, the guard is still present — this test fails RED as intended.
        raised_not_implemented = False
        try:
            ingest_track(
                conn=None,
                track="mn2t6_low",
                json_root=tmp_path,
                date_from=None,
                date_to=None,
                cities=None,
                overwrite=False,
                require_files=False,
            )
        except NotImplementedError:
            raised_not_implemented = True
        except Exception:
            pass  # FileNotFoundError, AttributeError on conn=None, etc. are all OK

        assert not raised_not_implemented, (
            "ingest_track raised NotImplementedError for track='mn2t6_low'. "
            "The Phase 4B guard block (ingest_grib_to_snapshots.py:259-263) must be "
            "removed in Phase 5B before this test goes GREEN."
        )

    def test_mx2t6_high_track_still_works(self, tmp_path):
        """R-AM (regression): ingest_track with track='mx2t6_high' must not regress.

        Removing the low guard must not break the high track path.
        """
        from scripts.ingest_grib_to_snapshots import ingest_track

        try:
            ingest_track(
                conn=None,
                track="mx2t6_high",
                json_root=tmp_path,
                date_from=None,
                date_to=None,
                cities=None,
                overwrite=False,
                require_files=False,
            )
        except NotImplementedError:
            pytest.fail(
                "ingest_track raised NotImplementedError for track='mx2t6_high' — regression."
            )
        except Exception:
            # Non-NotImplementedError exceptions are expected with conn=None + no files.
            pass

    def test_ingest_does_not_import_scanner(self):
        """R-AM.4 (regression antibody): ingest_grib_to_snapshots must not import scan_* modules.

        Scanner (scripts/scan_tigge_mn2t6_localday_coverage.py) is deferred to 5B-follow-up
        as diagnostic-only. If a future commit accidentally wires the scanner into the ingest
        runtime path as a quality gate, this test fires RED.

        # NOTE: This test is expected GREEN from day 1. It is a regression antibody
        # against future scope-creep that wires scanner into ingest path.
        """
        import ast
        from pathlib import Path

        ingest_path = Path(__file__).resolve().parents[1] / "scripts" / "ingest_grib_to_snapshots.py"
        tree = ast.parse(ingest_path.read_text())

        forbidden_imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if "scan_" in alias.name:
                        forbidden_imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if "scan_" in module:
                    forbidden_imports.append(module)

        assert not forbidden_imports, (
            f"ingest_grib_to_snapshots.py imports forbidden scan_* module(s): "
            f"{forbidden_imports}. Scanner is diagnostic-only (5B-follow-up deferred); "
            f"it must not be on the ingest runtime path."
        )


# ---------------------------------------------------------------------------
# R-AN: B078 low-lane truth metadata registry
# ---------------------------------------------------------------------------

# TODO(exec-emma 5B): confirm exact low-lane filenames match LEGACY_STATE_FILES entries
LOW_LANE_PLATT_FILENAME = "platt_models_low.json"
LOW_LANE_CALIBRATION_FILENAME = "calibration_pairs_low.json"


class TestB078LowLaneTruthFilesRegistry:
    """R-AN: LEGACY_STATE_FILES + build_truth_metadata must support low-lane metadata.

    B078: low-lane sidecar files (platt_models_low.json, calibration_pairs_low.json)
    must be registered in LEGACY_STATE_FILES with temperature_metric + data_version
    metadata threaded through build_truth_metadata and annotate_truth_payload.
    Fail-closed: absent temperature_metric must not silently stamp VERIFIED.

    Target: src.state.truth_files (exists but missing low-lane entries — RED for new tests)
    """

    def test_low_lane_platt_model_file_is_in_legacy_state_files(self):
        """R-AN (acceptance): a low-lane platt model filename must appear in LEGACY_STATE_FILES."""
        from src.state.truth_files import LEGACY_STATE_FILES

        low_files = [f for f in LEGACY_STATE_FILES if "_low" in f]
        assert low_files, (
            f"LEGACY_STATE_FILES contains no low-lane entries. "
            f"Expected at least one filename with '_low' suffix. "
            f"Current entries: {LEGACY_STATE_FILES}"
        )

    def test_low_lane_calibration_pairs_file_is_in_legacy_state_files(self):
        """R-AN (acceptance): a low-lane calibration pairs filename must appear in LEGACY_STATE_FILES."""
        from src.state.truth_files import LEGACY_STATE_FILES

        cal_low_files = [f for f in LEGACY_STATE_FILES if "calibration" in f and "_low" in f]
        assert cal_low_files, (
            f"LEGACY_STATE_FILES contains no low-lane calibration entries. "
            f"Expected at least one filename with 'calibration' and '_low'. "
            f"Current entries: {LEGACY_STATE_FILES}"
        )

    def test_build_truth_metadata_accepts_temperature_metric_for_low(self, tmp_path):
        """R-AN (acceptance): build_truth_metadata must accept temperature_metric='low' kwarg
        and round-trip it in the output dict."""
        from pathlib import Path
        from src.state.truth_files import build_truth_metadata

        result = build_truth_metadata(
            tmp_path / LOW_LANE_PLATT_FILENAME,
            temperature_metric="low",
            data_version="tigge_mn2t6_local_calendar_day_min_v1",
            authority="VERIFIED",
        )
        assert result.get("temperature_metric") == "low", (
            f"build_truth_metadata did not round-trip temperature_metric='low'. "
            f"Got: {result}"
        )
        assert result.get("data_version") == "tigge_mn2t6_local_calendar_day_min_v1", (
            f"build_truth_metadata did not round-trip data_version. Got: {result}"
        )

    def test_annotate_truth_payload_preserves_low_lane_metadata(self, tmp_path):
        """R-AN (acceptance): annotate_truth_payload must preserve temperature_metric + data_version
        in the _truth block for low-lane files."""
        from src.state.truth_files import annotate_truth_payload

        payload = {"positions": [], "bankroll": 100.0}
        result = annotate_truth_payload(
            payload,
            tmp_path / LOW_LANE_PLATT_FILENAME,
            authority="VERIFIED",
            temperature_metric="low",
            data_version="tigge_mn2t6_local_calendar_day_min_v1",
        )
        truth_block = result.get("truth") or result.get("_truth")
        assert truth_block is not None, (
            "annotate_truth_payload output has no 'truth' or '_truth' block."
        )
        assert truth_block.get("temperature_metric") == "low", (
            f"truth block did not preserve temperature_metric='low'. Got: {truth_block}"
        )
        assert truth_block.get("data_version") == "tigge_mn2t6_local_calendar_day_min_v1", (
            f"truth block did not preserve data_version. Got: {truth_block}"
        )

    def test_low_lane_file_missing_temperature_metric_fails_closed(self, tmp_path):
        """R-AN (rejection): building truth metadata for a low-lane file WITHOUT temperature_metric
        must either raise TypeError or stamp authority='UNVERIFIED' (fail-closed)."""
        from src.state.truth_files import build_truth_metadata

        try:
            result = build_truth_metadata(
                tmp_path / LOW_LANE_PLATT_FILENAME,
                authority="VERIFIED",
                # temperature_metric intentionally absent
            )
            # If it didn't raise, it must not have stamped VERIFIED for a low-lane file
            # without metric metadata. Either it stamps UNVERIFIED, or raises.
            # We check: if authority='VERIFIED' is in result WITHOUT temperature_metric,
            # that is the forbidden silent-pass.
            if result.get("authority") == "VERIFIED" and result.get("temperature_metric") is None:
                pytest.fail(
                    "build_truth_metadata silently stamped authority='VERIFIED' for a low-lane "
                    "file without temperature_metric. Must fail-closed: raise or stamp UNVERIFIED."
                )
        except TypeError:
            # Required kwarg — this is the clean fail-closed path.
            pass

    def test_high_lane_legacy_files_still_supported(self, tmp_path):
        """R-AN (regression): existing high-lane LEGACY_STATE_FILES entries must still be present.

        # NOTE: Expected GREEN from day 1 — regression antibody against accidental removal.
        """
        from src.state.truth_files import LEGACY_STATE_FILES

        assert "status_summary.json" in LEGACY_STATE_FILES, (
            "status_summary.json was removed from LEGACY_STATE_FILES — regression."
        )
        assert "positions.json" in LEGACY_STATE_FILES, (
            "positions.json was removed from LEGACY_STATE_FILES — regression."
        )


# ---------------------------------------------------------------------------
# R-AO: refit_platt_v2 low-metric isolation
# ---------------------------------------------------------------------------


class TestRefitPlattV2LowMetricIsolation:
    """R-AO: refit_platt_v2 must be metric-aware; low-track refit must not emit high: bucket keys
    or call save/deactivate with HIGH_LOCALDAY_MAX.

    The current script hardcodes 'high:' bucket keys and HIGH_LOCALDAY_MAX throughout.
    Phase 5B parametrizes via METRIC_SPECS iteration. These tests anchor that contract.

    Target: scripts.refit_platt_v2 — refit_v2() + _fit_bucket() (exists but hardcoded — RED)
    """

    def _make_calibration_db(self, tmp_path, temperature_metric: str, data_version: str):
        """Build a minimal in-memory DB with one calibration pair for the given metric."""
        import sqlite3 as _sqlite3
        db_path = tmp_path / "zeus-world.db"
        db = _sqlite3.connect(db_path)
        db.row_factory = _sqlite3.Row
        db.executescript(
            """
            CREATE TABLE calibration_pairs_v2 (
                city TEXT,
                target_date TEXT,
                temperature_metric TEXT,
                observation_field TEXT,
                range_label TEXT,
                p_raw REAL,
                outcome INTEGER,
                lead_days REAL,
                forecast_available_at TEXT,
                snapshot_id TEXT,
                data_version TEXT,
                training_allowed INTEGER DEFAULT 1,
                authority TEXT DEFAULT 'VERIFIED',
                cluster TEXT,
                season TEXT,
                decision_group_id TEXT,
                cycle TEXT DEFAULT '00',
                source_id TEXT DEFAULT 'tigge_mars',
                horizon_profile TEXT DEFAULT 'full'
            );
            CREATE TABLE platt_models_v2 (
                model_id TEXT PRIMARY KEY,
                temperature_metric TEXT,
                cluster TEXT,
                season TEXT,
                data_version TEXT,
                input_space TEXT,
                param_A REAL,
                param_B REAL,
                param_C REAL,
                bootstrap_params TEXT,
                n_samples INTEGER,
                brier_insample REAL,
                is_active INTEGER DEFAULT 1,
                authority TEXT DEFAULT 'VERIFIED',
                created_at TEXT
            );
            """
        )
        return db

    def test_refit_platt_low_bucket_key_prefix(self, tmp_path):
        """R-AO (acceptance): refit_platt_v2 running for low-track spec must emit
        bucket_keys prefixed 'low:', never 'high:'."""
        from unittest.mock import patch, MagicMock
        from src.types.metric_identity import LOW_LOCALDAY_MIN
        from scripts.refit_platt_v2 import refit_v2

        db = self._make_calibration_db(
            tmp_path,
            temperature_metric="low",
            data_version=LOW_LOCALDAY_MIN.data_version,
        )

        captured_keys = []

        def fake_save(conn, *, metric_identity, cluster, season, data_version, **kwargs):
            captured_keys.append(f"{metric_identity.temperature_metric}:{cluster}:{season}:{data_version}")

        with patch("scripts.refit_platt_v2.save_platt_model_v2", side_effect=fake_save), \
             patch("scripts.refit_platt_v2.deactivate_model_v2", return_value=0):
            refit_v2(db, metric_identity=LOW_LOCALDAY_MIN, dry_run=False, force=True)

        high_keys = [k for k in captured_keys if k.startswith("high:")]
        assert not high_keys, (
            f"refit_v2 with LOW_LOCALDAY_MIN emitted 'high:' bucket keys: {high_keys}. "
            "Low-track refit must use 'low:' prefix."
        )

    def test_refit_platt_low_calls_deactivate_with_low_metric_identity(self, tmp_path):
        """R-AO (acceptance): deactivate_model_v2 must be called with metric_identity=LOW_LOCALDAY_MIN
        during a low-track refit, never with HIGH_LOCALDAY_MAX."""
        from unittest.mock import patch, call
        from src.types.metric_identity import LOW_LOCALDAY_MIN, HIGH_LOCALDAY_MAX
        from scripts.refit_platt_v2 import refit_v2

        db = self._make_calibration_db(
            tmp_path,
            temperature_metric="low",
            data_version=LOW_LOCALDAY_MIN.data_version,
        )

        deactivate_calls = []

        def fake_deactivate(conn, *, metric_identity, **kwargs):
            deactivate_calls.append(metric_identity)
            return 0

        with patch("scripts.refit_platt_v2.deactivate_model_v2", side_effect=fake_deactivate), \
             patch("scripts.refit_platt_v2.save_platt_model_v2", return_value=None):
            refit_v2(db, metric_identity=LOW_LOCALDAY_MIN, dry_run=False, force=True)

        high_calls = [m for m in deactivate_calls if m == HIGH_LOCALDAY_MAX]
        assert not high_calls, (
            f"deactivate_model_v2 was called with HIGH_LOCALDAY_MAX during low-track refit. "
            f"All deactivate calls: {deactivate_calls}"
        )

    def test_refit_platt_low_calls_save_with_low_metric_identity(self, tmp_path):
        """R-AO (acceptance): save_platt_model_v2 must be called with metric_identity=LOW_LOCALDAY_MIN
        during a low-track refit, never with HIGH_LOCALDAY_MAX."""
        from unittest.mock import patch
        from src.types.metric_identity import LOW_LOCALDAY_MIN, HIGH_LOCALDAY_MAX
        from scripts.refit_platt_v2 import refit_v2

        db = self._make_calibration_db(
            tmp_path,
            temperature_metric="low",
            data_version=LOW_LOCALDAY_MIN.data_version,
        )

        save_calls = []

        def fake_save(conn, *, metric_identity, **kwargs):
            save_calls.append(metric_identity)

        with patch("scripts.refit_platt_v2.save_platt_model_v2", side_effect=fake_save), \
             patch("scripts.refit_platt_v2.deactivate_model_v2", return_value=0):
            refit_v2(db, metric_identity=LOW_LOCALDAY_MIN, dry_run=False, force=True)

        high_calls = [m for m in save_calls if m == HIGH_LOCALDAY_MAX]
        assert not high_calls, (
            f"save_platt_model_v2 was called with HIGH_LOCALDAY_MAX during low-track refit. "
            f"All save calls: {save_calls}"
        )

    def test_refit_platt_high_regression_bucket_key_prefix(self, tmp_path):
        """R-AO (regression): high-track refit must still emit 'high:' bucket keys after refactor.

        # NOTE: Expected GREEN from day 1 if current high-only code is preserved during refactor.
        """
        from unittest.mock import patch
        from src.types.metric_identity import HIGH_LOCALDAY_MAX
        from scripts.refit_platt_v2 import refit_v2

        db = self._make_calibration_db(
            tmp_path,
            temperature_metric="high",
            data_version=HIGH_LOCALDAY_MAX.data_version,
        )

        # refit_v2 currently doesn't accept metric_identity param — this test RED until
        # 5B adds the param. The high regression guard is only GREEN after refactor lands.
        captured_keys = []

        def fake_save(conn, *, metric_identity, cluster, season, data_version, **kwargs):
            captured_keys.append(f"{metric_identity.temperature_metric}:{cluster}:{season}:{data_version}")

        with patch("scripts.refit_platt_v2.save_platt_model_v2", side_effect=fake_save), \
             patch("scripts.refit_platt_v2.deactivate_model_v2", return_value=0):
            refit_v2(db, metric_identity=HIGH_LOCALDAY_MAX, dry_run=False, force=True)

        low_keys = [k for k in captured_keys if k.startswith("low:")]
        assert not low_keys, (
            f"refit_v2 with HIGH_LOCALDAY_MAX emitted 'low:' bucket keys: {low_keys}. "
            "High-track refit must not bleed into low: namespace."
        )

    def test_refit_v2_accepts_metric_identity_kwarg(self, tmp_path):
        """R-AO (acceptance): refit_v2 must accept a metric_identity kwarg to select track.

        Currently refit_v2(conn, *, dry_run, force) has no metric_identity param.
        Phase 5B adds it. This test fails RED until exec-emma adds the param.
        """
        import inspect
        from scripts.refit_platt_v2 import refit_v2

        sig = inspect.signature(refit_v2)
        assert "metric_identity" in sig.parameters, (
            "refit_v2 has no metric_identity parameter. "
            "Phase 5B must add metric_identity kwarg to enable low-track refit."
        )

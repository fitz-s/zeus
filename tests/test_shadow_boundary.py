"""ZDM-02: Shadow advisory surface boundary tests.

Ensures blocked_oos and effective_sample_size remain advisory-only
and never enter the live evaluator/control gate import graph.
"""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"


def _imports_in_file(filepath: Path) -> set[str]:
    """Extract all imported module names from a Python file."""
    source = filepath.read_text()
    tree = ast.parse(source)
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module)
    return imports


class TestShadowModuleMarkers:
    def test_blocked_oos_shadow_only_constant(self):
        from src.calibration.blocked_oos import SHADOW_ONLY
        assert SHADOW_ONLY is True

    def test_effective_sample_size_shadow_only_constant(self):
        from src.calibration.effective_sample_size import SHADOW_ONLY
        assert SHADOW_ONLY is True


class TestShadowReturnDicts:
    def test_blocked_oos_report_carries_shadow_only(self, tmp_path):
        from src.calibration.blocked_oos import evaluate_blocked_oos_calibration
        from src.state.db import get_connection, init_schema

        conn = get_connection(tmp_path / "shadow_test.db")
        init_schema(conn)
        report = evaluate_blocked_oos_calibration(
            conn,
            train_start="2025-01-01",
            train_end="2025-06-01",
            test_start="2025-06-01",
            test_end="2025-12-01",
            write=False,
        )
        assert report["shadow_only"] is True
        conn.close()

    def test_blocked_oos_promotion_carries_shadow_only(self, tmp_path):
        from src.calibration.blocked_oos import (
            evaluate_blocked_oos_calibration,
            recommend_calibration_promotion,
        )
        from src.state.db import get_connection, init_schema

        conn = get_connection(tmp_path / "shadow_test2.db")
        init_schema(conn)
        report = evaluate_blocked_oos_calibration(
            conn,
            train_start="2025-01-01",
            train_end="2025-06-01",
            test_start="2025-06-01",
            test_end="2025-12-01",
            write=False,
        )
        rec = recommend_calibration_promotion(report)
        assert rec["shadow_only"] is True
        conn.close()

    def test_bucket_health_carries_shadow_only(self):
        from src.calibration.effective_sample_size import (
            CalibrationDecisionGroup,
            summarize_bucket_health,
        )

        group = CalibrationDecisionGroup(
            group_id="test",
            city="NYC",
            target_date="2026-01-01",
            forecast_available_at="2025-12-30T00:00:00Z",
            cluster="northeast",
            season="DJF",
            lead_days=3.0,
            settlement_value=None,
            winning_range_label=None,
            bias_corrected=False,
            n_pair_rows=11,
            n_positive_rows=1,
        )
        rows = summarize_bucket_health([group])
        assert all(r["shadow_only"] is True for r in rows)

    def test_maturity_shadow_carries_shadow_only(self):
        from src.calibration.effective_sample_size import (
            CalibrationDecisionGroup,
            summarize_maturity_shadow,
        )

        group = CalibrationDecisionGroup(
            group_id="test",
            city="NYC",
            target_date="2026-01-01",
            forecast_available_at="2025-12-30T00:00:00Z",
            cluster="northeast",
            season="DJF",
            lead_days=3.0,
            settlement_value=None,
            winning_range_label=None,
            bias_corrected=False,
            n_pair_rows=11,
            n_positive_rows=1,
        )
        rows = summarize_maturity_shadow([group])
        assert all(r["shadow_only"] is True for r in rows)


class TestImportBoundaryIsolation:
    """Static import checks: shadow modules must not be imported by live gate modules."""

    SHADOW_MODULES = {"blocked_oos", "effective_sample_size"}
    FORBIDDEN_IMPORTERS = [
        SRC / "engine" / "evaluator.py",
    ]

    def test_evaluator_does_not_import_shadow_modules(self):
        for filepath in self.FORBIDDEN_IMPORTERS:
            if not filepath.exists():
                continue
            imports = _imports_in_file(filepath)
            source = filepath.read_text()
            for mod in self.SHADOW_MODULES:
                assert mod not in source, (
                    f"{filepath.name} must not reference shadow module '{mod}'"
                )

    def test_no_engine_module_imports_shadow(self):
        """No file under src/engine/ should import shadow calibration modules."""
        engine_dir = SRC / "engine"
        if not engine_dir.exists():
            return
        for py_file in engine_dir.glob("*.py"):
            source = py_file.read_text()
            for mod in self.SHADOW_MODULES:
                assert mod not in source, (
                    f"src/engine/{py_file.name} must not reference shadow module '{mod}'"
                )

    def test_harvester_only_imports_write_helpers(self):
        """harvester.py is the allowed settlement-path consumer of effective_sample_size.

        It must only import write-side functions (build/write), never reporting
        functions (summarize_*) or the SHADOW_ONLY sentinel.
        """
        harvester = SRC / "execution" / "harvester.py"
        if not harvester.exists():
            return
        source = harvester.read_text()
        forbidden_reporting = [
            "summarize_bucket_health",
            "summarize_maturity_shadow",
            "SHADOW_ONLY",
        ]
        for name in forbidden_reporting:
            assert name not in source, (
                f"harvester.py must not import reporting function '{name}' from "
                f"effective_sample_size — only write helpers are allowed"
            )

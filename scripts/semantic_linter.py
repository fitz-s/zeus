#!/usr/bin/env python3
"""
Semantic Provenance Linter for Zeus.
AST-based static analyzer to enforce that AI implementation logic 
never reads a semantically heavy value (like a derived probability) 
without ALSO reading its provenance context (like the entry method).
"""

import ast
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple

SEMANTIC_RULES = {
    ("p_posterior", "p_held_side", "last_monitor_prob"): {
        "required_context": ("entry_method", "selected_method"),
        "message": "Semantic Loss Detected: You accessed a context-dependent probability but did not evaluate its entry provenance in the same scope."
    },
    ("p_raw",): {
        "required_context": ("bias_correction", "calibration", "platt", "sigma_instrument"),
        "message": "Semantic Loss Detected: Raw probability 'p_raw' accessed without evaluating bias_correction or calibration. This violates the structural invariant."
    }
}

TIME_SEMANTICS_ALLOWED_FILES = {"diurnal.py", "day0_signal.py", "solar.py", "day0_residual.py"}
P_RAW_CALIBRATION_FILES = {"blocked_oos.py"}


class SemanticAnalyzer(ast.NodeVisitor):
    def __init__(self, filepath: Path):
        self.filepath = filepath
        self.violations: List[str] = []
        self.current_function = None
        self.function_attributes: Dict[str, Set[str]] = {}

    def visit_FunctionDef(self, node: ast.FunctionDef):
        # A new function scope
        prev_function = self.current_function
        self.current_function = node.name
        self.function_attributes[node.name] = set()
        # Track parameter names separately for provenance-as-param checks
        self._function_params: set[str] = set()
        for arg in node.args.args + node.args.kwonlyargs:
            self._function_params.add(arg.arg)
        
        # Visit all nodes inside the function
        self.generic_visit(node)
        
        # After visiting, evaluate the rules for this function
        attrs = self.function_attributes[node.name]
        
        for targets, rule in SEMANTIC_RULES.items():
            # Check if any target attribute was accessed
            found_targets = [t for t in targets if t in attrs]
            if found_targets:
                # Skip p_raw rule for files that ARE the calibration layer
                if set(found_targets) <= {"p_raw"} and self.filepath.name in P_RAW_CALIBRATION_FILES:
                    continue
                # Target accessed. Did we also access the required context?
                required = rule["required_context"]
                params = getattr(self, "_function_params", set())
                if not any(req in attrs or req in params for req in required):
                    self.violations.append(
                        f"{self.filepath}:{node.lineno} in function '{node.name}':\n"
                        f"  [ERROR] {rule['message']}\n"
                        f"  Detected attributes: {', '.join(found_targets)}\n"
                        f"  Missing required context: ANY of {', '.join(required)}\n"
                    )

        self.current_function = prev_function

    def _check_time_semantics_symbol(self, symbol: str, lineno: int) -> None:
        if self.filepath.name in TIME_SEMANTICS_ALLOWED_FILES:
            return
        if symbol in {"local_hour", "current_local_hour"} and "tests" not in self.filepath.parts:
            self.violations.append(
                f"{self.filepath}:{lineno}:\n"
                "  [ERROR] Raw local-hour semantics must stay inside the approved "
                "time-semantics layer (solar.py / diurnal.py / day0_signal.py).\n"
                "  Use ObservationInstant / Day0TemporalContext instead.\n"
            )

    def visit_Attribute(self, node: ast.Attribute):
        self._check_time_semantics_symbol(node.attr, node.lineno)
        # Ban direct settings.mode access outside config.py
        if (
            node.attr == "mode"
            and isinstance(node.value, ast.Name)
            and node.value.id == "settings"
            and self.filepath.name != "config.py"
        ):
            self.violations.append(
                f"{self.filepath}:{node.lineno}:\n"
                "  [ERROR] Direct `settings.mode` access is banned outside config.py.\n"
                "  Use `from src.config import get_mode` and call `get_mode()` instead.\n"
            )
        if self.current_function:
            self.function_attributes[self.current_function].add(node.attr)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name):
        self._check_time_semantics_symbol(node.id, node.lineno)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        func_name = ""
        attr_name = ""
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            attr_name = node.func.attr
            func_name = attr_name

        if func_name == "Bin":
            has_unit_keyword = any(k.arg == "unit" for k in node.keywords if k.arg is not None)
            if not has_unit_keyword:
                self.violations.append(
                    f"{self.filepath}:{node.lineno}:\n"
                    "  [ERROR] Bin construction must specify unit= explicitly.\n"
                    "  Missing required keyword: unit\n"
                )

        if attr_name in {"default_wu_fahrenheit", "default_wu_celsius"}:
            if self.filepath.name != "settlement_semantics.py":
                self.violations.append(
                    f"{self.filepath}:{node.lineno}:\n"
                    "  [ERROR] Do not call SettlementSemantics.default_wu_* outside "
                    "settlement_semantics.py. Use SettlementSemantics.for_city() instead.\n"
                )

        if func_name == "build_day0_temporal_context":
            has_observation_time = any(k.arg == "observation_time" for k in node.keywords if k.arg is not None)
            in_tests = "tests" in self.filepath.parts or self.filepath.name.startswith("test_")
            if not has_observation_time and not in_tests and self.filepath.name != "diurnal.py":
                self.violations.append(
                    f"{self.filepath}:{node.lineno}:\n"
                    "  [ERROR] build_day0_temporal_context must receive observation_time= "
                    "outside tests so Day0 runtime logic does not fall back to blind clock time.\n"
                )

        self.generic_visit(node)


def run_linter(src_path: Path) -> int:
    """Run the linter over all Python files in the source tree."""
    total_violations = 0
    checked_files = 0
    
    python_files = list(src_path.rglob("*.py"))
    
    for py_file in python_files:
        if py_file.name.startswith("test_"):
            continue  # Skip tests unless we decide invariant specs need linters too
            
        try:
            tree = ast.parse(py_file.read_text())
        except SyntaxError as e:
            print(f"Error parsing {py_file}: {e}")
            continue
            
        analyzer = SemanticAnalyzer(py_file)
        analyzer.visit(tree)
        
        if analyzer.violations:
            for violation in analyzer.violations:
                print(violation)
                total_violations += 1
                
        checked_files += 1

    if total_violations > 0:
        print(f"\n[FAIL] {total_violations} semantic violation(s) found across {checked_files} files.")
        print("These must be fixed before proceeding to verify AI-written logic.")
        return 1
    
    print(f"\n[PASS] Verified {checked_files} files. No AST semantic violations detected.")
    return 0


if __name__ == "__main__":
    src_dir = Path(__file__).parent.parent / "src"
    if not src_dir.exists():
        print(f"Error: {src_dir} not found.")
        sys.exit(1)
        
    sys.exit(run_linter(src_dir))

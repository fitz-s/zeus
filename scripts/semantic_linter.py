#!/usr/bin/env python3
"""
Semantic Provenance Linter for Zeus.
AST-based static analyzer to enforce that AI implementation logic
never reads a semantically heavy value (like a derived probability)
without ALSO reading its provenance context (like the entry method).

Also enforces K3 cluster collapse: no regional cluster literal strings in src/.
"""
# Lifecycle: created=2026-03-31; last_reviewed=2026-04-24; last_reused=2026-04-24
# Purpose: Enforce static semantic/provenance containment rules for Zeus code.
# Reuse: Inspect architecture/script_manifest.yaml and current packet allowlists before relying on new lint coverage.

import argparse
import ast
import re
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple

# K3: forbid regional cluster literal strings in src/
# These strings should not appear in any Python source after the cluster->city collapse.
FORBIDDEN_REGIONAL_CLUSTERS: frozenset[str] = frozenset({
    "US-Northeast", "US-Southeast", "US-GreatLakes", "US-Texas-Triangle",
    "US-Mountain", "US-Pacific", "US-West", "US-Florida", "US-Southeast-Inland",
    "US-California-Coast", "US-Pacific-Northwest", "US-Rockies",
    "Asia-Northeast", "Asia-Subtropical", "Asia-East-China", "Asia-Maritime",
    "China-Central", "China-North",
    "Europe-Maritime", "Europe-Continental", "Europe-Atlantic", "Europe-Eastern",
    "Europe-Mediterranean",
    "Oceania-Temperate", "Oceania-Maritime",
    "Southeast-Asia-Equatorial", "Latin-America-Temperate", "Latin-America-Tropical",
    "Latin-America-Highland", "South-America-Pampas", "South-America-Tropical",
    "Middle-East", "Middle-East-Levant", "Middle-East-Arabian",
    "Africa-Tropical", "Africa-Temperate", "Africa-West-Tropical", "Africa-South-Maritime",
    "North-America-Great-Lakes", "India-North", "Turkey",
})

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

TIME_SEMANTICS_ALLOWED_FILES = {"diurnal.py", "day0_signal.py", "solar.py"}
P_RAW_CALIBRATION_FILES = {"blocked_oos.py"}

# K2_struct: forbid bare FROM calibration_pairs outside the allowlist.
# Only src/calibration/store.py and files under migrations/ may query this table directly.
# scripts/ is explicitly carved out: operator-run scripts are reviewed at PR time.
# Named gap: scripts/ not enforced by this rule (see K2_struct ADR note).
CALIBRATION_PAIRS_SELECT_ALLOWLIST: frozenset[str] = frozenset({
    "store.py",              # src/calibration/store.py — canonical query layer
    "blocked_oos.py",       # src/calibration/blocked_oos.py — K2_struct approved, has authority_filter
    "effective_sample_size.py",  # src/calibration/effective_sample_size.py — K2_struct approved
})

HOURLY_OBSERVATIONS_SELECT_ALLOWLIST: frozenset[str] = frozenset({
    "scripts/etl_hourly_observations.py",  # compatibility writer for the legacy table
})

# H3 (2026-04-24): forbid bare SELECT FROM/JOIN settlements without a
# `temperature_metric` predicate. `settlements` is dual-track (high|low);
# any reader that filters by (city, target_date) without pinning the metric
# will silently match BOTH HIGH and LOW rows once both metrics exist, and
# spurious extra rows corrupt MAE/Brier/delta computations.
#
# Allowlist is narrow: writers (harvester INSERT path) and the data-readiness
# audit tooling that intentionally inspects cross-metric rows. Migration and
# test files are carved out at the directory level.
SETTLEMENTS_METRIC_SELECT_ALLOWLIST: frozenset[str] = frozenset({
    "src/execution/harvester.py",          # writer path — INSERT, not SELECT
    "src/state/db.py",                     # schema migration queries are metric-agnostic by design
})


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


def _check_regional_cluster_literals(py_file: Path, content: str) -> list[str]:
    """K3: Scan for forbidden regional cluster literal strings outside comments."""
    violations = []
    for lineno, line in enumerate(content.splitlines(), 1):
        # Strip inline comment portion
        stripped = line.split("#")[0]
        for cluster in FORBIDDEN_REGIONAL_CLUSTERS:
            if cluster in stripped:
                violations.append(
                    f"{py_file}:{lineno}:\n"
                    f"  [ERROR] K3 cluster collapse: regional cluster literal {cluster!r} "
                    "found in src/. After K3 all clusters equal city names.\n"
                    f"  Line: {line.rstrip()}\n"
                )
    return violations


def _check_calibration_pairs_select(py_file: Path, content: str) -> list[str]:
    """K2_struct: forbid direct FROM calibration_pairs outside the allowlist.

    Allowlist: src/calibration/store.py, migrations/.
    scripts/ is carved out (operator-run, reviewed at PR time).
    """
    # Skip allowlisted files by name
    if py_file.name in CALIBRATION_PAIRS_SELECT_ALLOWLIST:
        return []
    # Skip migrations/ directory
    if "migrations" in py_file.parts:
        return []
    # Skip scripts/ directory (named gap)
    if "scripts" in py_file.parts:
        return []

    violations = []
    pattern = re.compile(r'FROM\s+calibration_pairs', re.IGNORECASE)
    for lineno, line in enumerate(content.splitlines(), 1):
        # Strip Python inline comments (#) and SQL inline comments (--)
        stripped = line.split("#")[0].split("--")[0]
        if pattern.search(stripped):
            violations.append(
                f"{py_file}:{lineno}:\n"
                "  [ERROR] K2_struct: direct FROM calibration_pairs query outside allowlist.\n"
                "  Use src/calibration/store.py query functions instead.\n"
                f"  Line: {line.rstrip()}\n"
            )
    return violations


def _literal_mapping_value(
    node: ast.AST,
    constants: dict[str, str],
    mappings: dict[str, dict[str, str]],
) -> dict[str, str] | None:
    if isinstance(node, ast.Name) and node.id in mappings:
        return mappings[node.id]
    if not isinstance(node, ast.Dict):
        return None
    result = {}
    for key, value_node in zip(node.keys, node.values):
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
            return None
        value = _literal_string_value(value_node, constants, mappings)
        if value is None:
            return None
        result[key.value] = value
    return result


def _format_operand_value(
    node: ast.AST,
    constants: dict[str, str],
    mappings: dict[str, dict[str, str]],
) -> object | None:
    if isinstance(node, ast.Tuple):
        values = [_format_operand_value(item, constants, mappings) for item in node.elts]
        if any(value is None for value in values):
            return None
        return tuple(values)
    mapping = _literal_mapping_value(node, constants, mappings)
    if mapping is not None:
        return mapping
    return _literal_string_value(node, constants, mappings)


def _literal_string_value(
    node: ast.AST,
    constants: dict[str, str] | None = None,
    mappings: dict[str, dict[str, str]] | None = None,
) -> str | None:
    constants = constants or {}
    mappings = mappings or {}
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name) and node.id in constants:
        return constants[node.id]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _literal_string_value(node.left, constants, mappings)
        right = _literal_string_value(node.right, constants, mappings)
        if left is not None and right is not None:
            return left + right
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        left = _literal_string_value(node.left, constants, mappings)
        right = _format_operand_value(node.right, constants, mappings)
        if left is not None and right is not None:
            try:
                return left % right
            except (TypeError, ValueError):
                return None
    if isinstance(node, ast.JoinedStr):
        pieces = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                pieces.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                rendered = _literal_string_value(value.value, constants, mappings)
                if rendered is None:
                    return None
                pieces.append(rendered)
            else:
                return None
        return "".join(pieces)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "format"
    ):
        template = _literal_string_value(node.func.value, constants, mappings)
        if template is None:
            return None
        args = [_literal_string_value(arg, constants, mappings) for arg in node.args]
        if any(arg is None for arg in args):
            return None
        kwargs = {}
        for keyword in node.keywords:
            if keyword.arg is None:
                mapping = _literal_mapping_value(keyword.value, constants, mappings)
                if mapping is None:
                    return None
                kwargs.update(mapping)
                continue
            value = _literal_string_value(keyword.value, constants, mappings)
            if value is None:
                return None
            kwargs[keyword.arg] = value
        try:
            return template.format(*args, **kwargs)
        except (IndexError, KeyError, ValueError):
            return None
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "format_map"
    ):
        template = _literal_string_value(node.func.value, constants, mappings)
        if template is None or len(node.args) != 1 or node.keywords:
            return None
        mapping = _literal_mapping_value(node.args[0], constants, mappings)
        if mapping is None:
            return None
        try:
            return template.format_map(mapping)
        except (KeyError, ValueError):
            return None
    return None


class _StringConstantCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.constants: dict[str, str] = {}
        self.mappings: dict[str, dict[str, str]] = {}

    def visit_Assign(self, node: ast.Assign) -> None:
        mapping = _literal_mapping_value(node.value, self.constants, self.mappings)
        if mapping is not None:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.mappings[target.id] = mapping
        value = _literal_string_value(node.value, self.constants, self.mappings)
        if value is not None:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.constants[target.id] = value
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Name) and node.value is not None:
            mapping = _literal_mapping_value(node.value, self.constants, self.mappings)
            if mapping is not None:
                self.mappings[node.target.id] = mapping
            value = _literal_string_value(node.value, self.constants, self.mappings)
            if value is not None:
                self.constants[node.target.id] = value
        self.generic_visit(node)


def _sql_call_literal_args(content: str) -> list[tuple[int, int, str]]:
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return []

    collector = _StringConstantCollector()
    collector.visit(tree)
    constants = collector.constants
    mappings = collector.mappings
    sql_call_names = {"execute", "executemany", "executescript", "read_sql", "read_sql_query"}
    sql_keyword_names = {"sql", "query", "statement"}
    literals = []

    def append_literal(arg: ast.AST) -> None:
        value = _literal_string_value(arg, constants, mappings)
        if value is None:
            return
        literals.append(
            (
                getattr(arg, "lineno", 1),
                getattr(arg, "end_lineno", getattr(arg, "lineno", 1)),
                value,
            )
        )

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute):
            call_name = node.func.attr
        elif isinstance(node.func, ast.Name):
            call_name = node.func.id
        else:
            continue
        if call_name not in sql_call_names:
            continue
        for arg in node.args:
            append_literal(arg)
        for keyword in node.keywords:
            if keyword.arg not in sql_keyword_names:
                continue
            append_literal(keyword.value)
    return literals


def _check_legacy_hourly_observations_select(py_file: Path, content: str) -> list[str]:
    """P0 containment: forbid bare canonical reads from hourly_observations.

    `hourly_observations` is a lossy compatibility table. Canonical training,
    replay, and live paths must use v2/evidence adapters instead.
    """
    try:
        repo_relative = py_file.resolve().relative_to(Path(__file__).resolve().parents[1]).as_posix()
    except ValueError:
        repo_relative = py_file.as_posix()
    if repo_relative in HOURLY_OBSERVATIONS_SELECT_ALLOWLIST:
        return []
    if "migrations" in py_file.parts or "tests" in py_file.parts:
        return []

    stripped_lines = [line.split("#")[0].split("--")[0] for line in content.splitlines()]
    stripped_content = "\n".join(stripped_lines)
    stripped_content = re.sub(
        r"/\*.*?\*/",
        lambda match: "\n" * match.group(0).count("\n") + " ",
        stripped_content,
        flags=re.DOTALL,
    )
    sql_identifier = r'(?:[A-Za-z_][A-Za-z0-9_]*|"[^"]+"|`[^`]+`|\[[^\]]+\])'
    optional_schema = rf"(?:{sql_identifier}\s*\.\s*)?"
    table_ref = r'(?:"hourly_observations"|`hourly_observations`|\[hourly_observations\]|hourly_observations)'
    pattern = re.compile(
        rf"\b(FROM|JOIN)\s+{optional_schema}{table_ref}(?=\W|$)",
        re.IGNORECASE | re.MULTILINE,
    )
    violations = []
    violation_lines: set[int] = set()
    source_lines = content.splitlines()
    for match in pattern.finditer(stripped_content):
        lineno = stripped_content[:match.start()].count("\n") + 1
        line = source_lines[lineno - 1] if lineno <= len(source_lines) else ""
        violations.append(
            f"{py_file}:{lineno}:\n"
            "  [ERROR] P0_unsafe_table: bare read from legacy hourly_observations.\n"
            "  Use a v2 eligibility surface or an explicit evidence adapter instead.\n"
            f"  Line: {line.rstrip()}\n"
        )
        violation_lines.add(lineno)

    for start_lineno, end_lineno, sql in _sql_call_literal_args(content):
        if any(start_lineno <= line <= end_lineno for line in violation_lines):
            continue
        normalized_sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
        if not pattern.search(normalized_sql):
            continue
        line = source_lines[start_lineno - 1] if start_lineno <= len(source_lines) else ""
        violations.append(
            f"{py_file}:{start_lineno}:\n"
            "  [ERROR] P0_unsafe_table: bare read from legacy hourly_observations.\n"
            "  Use a v2 eligibility surface or an explicit evidence adapter instead.\n"
            f"  Line: {line.rstrip()}\n"
        )
        violation_lines.add(start_lineno)
    return violations


def _check_settlements_metric_filter(py_file: Path, content: str) -> list[str]:
    """H3 (2026-04-24): require temperature_metric predicate on settlements reads.

    Any SELECT FROM settlements or JOIN settlements in canonical (src/) or
    training-path scripts must restrict `temperature_metric` so that dual-
    track high/low rows for the same (city, target_date) do not silently
    both match. Writers (INSERT/UPDATE/DELETE) and migration / test files
    are exempt. The allowlist covers writer modules and intentional cross-
    metric audit tooling.

    Detection approach: scan stripped SQL regions for `\bFROM settlements\b`
    or `\bJOIN settlements\b` (excluding partial matches like
    `settlements_authority_monotonic`), and within the same SQL literal /
    near vicinity require a `temperature_metric` token. The heuristic is
    conservative — it matches on the SQL literal text, not on the full
    query AST — but it catches the four pre-H3 bare-JOIN sites and does
    not fire false positives on writer INSERT paths.
    """
    try:
        repo_relative = py_file.resolve().relative_to(Path(__file__).resolve().parents[1]).as_posix()
    except ValueError:
        repo_relative = py_file.as_posix()
    if repo_relative in SETTLEMENTS_METRIC_SELECT_ALLOWLIST:
        return []
    if "migrations" in py_file.parts or "tests" in py_file.parts:
        return []
    # H3 is a canonical-path rule. scripts/ contains operator-run audit,
    # backfill, and migration tools that legitimately inspect settlements
    # rows cross-metric. scripts/ is carved out (same pattern as K2_struct
    # at `_check_calibration_pairs_select`); the 4 training-path scripts
    # pre-hardened by the S3 slice (etl_historical_forecasts,
    # etl_forecast_skill_from_forecasts, validate_dynamic_alpha, and
    # monitor_refresh's FROM settlements in src/engine/) carry the metric
    # filter inline. Future training-path scripts/rebuild_*.py /
    # scripts/refit_*.py should be promoted into the rule via an
    # explicit training-path allowlist extension.
    if "scripts" in py_file.parts:
        return []

    # Match bare `FROM settlements` / `JOIN settlements` but not
    # `settlements_xxx` composites. The word-boundary (?!\w) ensures a
    # non-word character follows.
    pattern = re.compile(
        r"\b(FROM|JOIN)\s+settlements(?!\w)",
        re.IGNORECASE,
    )
    violations: list[str] = []
    source_lines = content.splitlines()

    # Per-SQL-literal scope: only flag when the match appears inside an
    # actual `.execute(...)` / `.executemany(...)` / `executescript(...)`
    # literal argument. Docstrings, module prose, and comments cannot
    # hit a DB, so they're excluded. Same-literal `temperature_metric`
    # token satisfies the predicate requirement; cross-literal reference
    # does not (each query must defend itself).
    for start_lineno, end_lineno, sql in _sql_call_literal_args(content):
        normalized_sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
        if pattern.search(normalized_sql) and "temperature_metric" not in normalized_sql:
            line = source_lines[start_lineno - 1] if start_lineno <= len(source_lines) else ""
            violations.append(
                f"{py_file}:{start_lineno}:\n"
                "  [ERROR] H3: settlements read without temperature_metric predicate.\n"
                "  Dual-track settlements schema requires `AND s.temperature_metric = 'high'|'low'`\n"
                "  (or metric-axis equivalent) on every SELECT/JOIN; otherwise a future LOW\n"
                "  row silently matches alongside HIGH and corrupts downstream MAE/Brier.\n"
                f"  Line: {line.rstrip()}\n"
            )

    return violations


def _python_files_for_target(target: Path) -> list[Path]:
    if target.is_file():
        return [target] if target.suffix == ".py" else []
    return list(target.rglob("*.py"))


def run_linter(src_path: Path) -> int:
    """Run the linter over a Python file or every Python file under a directory."""
    total_violations = 0
    checked_files = 0

    python_files = _python_files_for_target(src_path)
    target_is_dir = src_path.is_dir()

    for py_file in python_files:
        if target_is_dir and py_file.name.startswith("test_"):
            continue  # Skip tests unless we decide invariant specs need linters too

        try:
            content = py_file.read_text()
            tree = ast.parse(content)
        except SyntaxError as e:
            print(f"Error parsing {py_file}: {e}")
            continue

        analyzer = SemanticAnalyzer(py_file)
        analyzer.visit(tree)

        if analyzer.violations:
            for violation in analyzer.violations:
                print(violation)
                total_violations += 1

        # K3: check for forbidden regional cluster literals
        cluster_violations = _check_regional_cluster_literals(py_file, content)
        for violation in cluster_violations:
            print(violation)
            total_violations += 1

        # K2_struct: check for bare FROM calibration_pairs outside allowlist
        cp_violations = _check_calibration_pairs_select(py_file, content)
        for violation in cp_violations:
            print(violation)
            total_violations += 1

        hourly_violations = _check_legacy_hourly_observations_select(py_file, content)
        for violation in hourly_violations:
            print(violation)
            total_violations += 1

        metric_violations = _check_settlements_metric_filter(py_file, content)
        for violation in metric_violations:
            print(violation)
            total_violations += 1

        checked_files += 1

    if total_violations > 0:
        print(f"\n[FAIL] {total_violations} semantic violation(s) found across {checked_files} files.")
        print("These must be fixed before proceeding to verify AI-written logic.")
        return 1
    
    print(f"\n[PASS] Verified {checked_files} files. No AST semantic violations detected.")
    return 0


def run_linter_targets(targets: list[Path]) -> int:
    if not targets:
        return run_linter(Path(__file__).parent.parent / "src")
    exit_code = 0
    for target in targets:
        if not target.exists():
            print(f"Error: {target} not found.")
            exit_code = 1
            continue
        exit_code = max(exit_code, run_linter(target))
    return exit_code


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        nargs="*",
        default=None,
        help="Check specific Python files or directories. Omit for full src/ scan.",
    )
    args = parser.parse_args()
    targets = [Path(item) for item in args.check] if args.check is not None else []
    sys.exit(run_linter_targets(targets))

import ast
from pathlib import Path
from scripts.semantic_linter import SemanticAnalyzer

def test_linter_catches_p_raw_missing_bias():
    """Prove the AST linter blocks access to p_raw if bias/calibration is not checked."""
    code_violating = '''
def bad_function(signal):
    # Reads p_raw without any context!
    return signal.p_raw
'''
    analyzer_violating = SemanticAnalyzer(Path("fake_violating.py"))
    tree_violating = ast.parse(code_violating)
    analyzer_violating.visit(tree_violating)
    
    assert len(analyzer_violating.violations) == 1
    assert "Raw probability 'p_raw' accessed without evaluating bias_correction" in analyzer_violating.violations[0]

def test_linter_allows_p_raw_with_bias():
    """Prove the AST linter allows access to p_raw if bias is verified."""
    code_valid = '''
def good_function(signal):
    if signal.bias_correction:
        return signal.p_raw
    return fallbacks.platt(signal.p_raw)
'''
    analyzer_valid = SemanticAnalyzer(Path("fake_valid.py"))
    tree_valid = ast.parse(code_valid)
    analyzer_valid.visit(tree_valid)
    
    assert len(analyzer_valid.violations) == 0

def test_linter_allows_p_raw_with_sigma():
    """Prove the AST linter allows p_raw if sigma_instrument establishes the context."""
    code_valid = '''
def day0_logic(p_raw, sigma_instrument):
    return apply_noise(p_raw, sigma_instrument)
'''
    analyzer_valid = SemanticAnalyzer(Path("fake_valid_2.py"))
    tree_valid = ast.parse(code_valid)
    analyzer_valid.visit(tree_valid)
    
    assert len(analyzer_valid.violations) == 0

def test_linter_catches_bin_without_unit():
    code_violating = '''
from src.types import Bin
def bad_bin():
    return Bin(low=39, high=40, label="39-40°F")
'''
    analyzer = SemanticAnalyzer(Path("fake_bin_missing_unit.py"))
    tree = ast.parse(code_violating)
    analyzer.visit(tree)

    assert any("Bin construction must specify unit=" in v for v in analyzer.violations)

def test_linter_catches_direct_settlement_factory_call():
    code_violating = '''
from src.contracts.settlement_semantics import SettlementSemantics
def bad_semantics():
    return SettlementSemantics.default_wu_fahrenheit("KLGA")
'''
    analyzer = SemanticAnalyzer(Path("fake_semantics_call.py"))
    tree = ast.parse(code_violating)
    analyzer.visit(tree)

    assert any("Use SettlementSemantics.for_city()" in v for v in analyzer.violations)

def test_linter_catches_raw_local_hour_outside_time_semantics_layer():
    code_violating = '''
def bad_time_logic(local_hour):
    return local_hour > 15
'''
    analyzer = SemanticAnalyzer(Path("/tmp/src/evaluator.py"))
    tree = ast.parse(code_violating)
    analyzer.visit(tree)

    assert any("Raw local-hour semantics must stay inside the approved time-semantics layer" in v for v in analyzer.violations)

def test_linter_allows_local_hour_inside_diurnal_layer():
    code_valid = '''
def diurnal_logic(current_local_hour):
    return current_local_hour + 1
'''
    analyzer = SemanticAnalyzer(Path("/tmp/diurnal.py"))
    tree = ast.parse(code_valid)
    analyzer.visit(tree)

    assert not analyzer.violations

def test_linter_catches_day0_temporal_context_without_observation_time():
    code_violating = '''
from src.signal.diurnal import build_day0_temporal_context
def bad_day0(city, target_date):
    return build_day0_temporal_context(city.name, target_date, city.timezone)
'''
    analyzer = SemanticAnalyzer(Path("src/engine/fake_day0.py"))
    tree = ast.parse(code_violating)
    analyzer.visit(tree)

    assert any("build_day0_temporal_context must receive observation_time=" in v for v in analyzer.violations)

def test_entire_repo_passes_linter():
    """ACTUAL GATE: Enforce that the actual codebase obeys all semantic invariants."""
    from scripts.semantic_linter import run_linter
    src_dir = Path(__file__).parent.parent / "src"
    # run_linter returns 0 on success, 1 on failure
    result = run_linter(src_dir)
    assert result == 0, "Structural Governance Linter found active semantic violations in the repo."


def test_linter_check_accepts_single_file_target():
    from scripts.semantic_linter import run_linter_targets
    target = Path(__file__).parent.parent / "src" / "calibration" / "platt.py"

    result = run_linter_targets([target])

    assert result == 0

def test_all_repo_bin_calls_use_explicit_unit():
    repo = Path(__file__).parent.parent
    violations = []

    for py_file in list((repo / "src").rglob("*.py")) + list((repo / "tests").rglob("*.py")) + list((repo / "scripts").rglob("*.py")):
        tree = ast.parse(py_file.read_text())

        imports_src_bin = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in {"src.types", "src.types.market"}:
                if any(alias.name == "Bin" for alias in node.names):
                    imports_src_bin = True
                    break

        if not imports_src_bin:
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Bin":
                has_unit = any(kw.arg == "unit" for kw in node.keywords if kw.arg is not None)
                if not has_unit:
                    violations.append(f"{py_file}:{node.lineno}")

    assert not violations, "All src.types.Bin constructors must use explicit unit=. Violations: " + ", ".join(violations)

def test_no_direct_default_settlement_factory_calls_in_repo_code():
    repo = Path(__file__).parent.parent
    violations = []

    for py_file in list((repo / "src").rglob("*.py")) + list((repo / "scripts").rglob("*.py")):
        if py_file.name == "settlement_semantics.py":
            continue
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in {"default_wu_fahrenheit", "default_wu_celsius"}:
                    violations.append(f"{py_file}:{node.lineno}")

    assert not violations, (
        "SettlementSemantics.default_wu_* must not be called directly outside settlement_semantics.py. "
        "Use SettlementSemantics.for_city(). Violations: " + ", ".join(violations)
    )

def test_time_sensitive_etl_no_longer_reads_legacy_observations_local_hour():
    repo = Path(__file__).parent.parent
    violations = []

    for rel in ["scripts/etl_hourly_observations.py", "scripts/etl_diurnal_curves.py"]:
        path = repo / rel
        text = path.read_text(encoding="utf-8")
        if "FROM observations" in text and "local_hour" in text:
            violations.append(str(path))

    assert not violations, (
        "Time-sensitive ETL must promote DST-safe observation_instants, not legacy observations.local_hour. "
        "Violations: " + ", ".join(violations)
    )

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

def test_entire_repo_passes_linter():
    """ACTUAL GATE: Enforce that the actual codebase obeys all semantic invariants."""
    from scripts.semantic_linter import run_linter
    src_dir = Path(__file__).parent.parent / "src"
    # run_linter returns 0 on success, 1 on failure
    result = run_linter(src_dir)
    assert result == 0, "Structural Governance Linter found active semantic violations in the repo."

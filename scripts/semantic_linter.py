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

# A mapping of { (Target Attributes...) : (Required Context Attributes...) }
SEMANTIC_RULES = {
    ("p_posterior", "p_held_side", "last_monitor_prob"): {
        "required_context": ("entry_method", "selected_method"),
        "message": "Semantic Loss Detected: You accessed a context-dependent probability but did not evaluate its entry provenance in the same scope."
    }
}


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
        
        # Visit all nodes inside the function
        self.generic_visit(node)
        
        # After visiting, evaluate the rules for this function
        attrs = self.function_attributes[node.name]
        
        for targets, rule in SEMANTIC_RULES.items():
            # Check if any target attribute was accessed
            found_targets = [t for t in targets if t in attrs]
            if found_targets:
                # Target accessed. Did we also access the required context?
                required = rule["required_context"]
                if not any(req in attrs for req in required):
                    self.violations.append(
                        f"{self.filepath}:{node.lineno} in function '{node.name}':\n"
                        f"  [ERROR] {rule['message']}\n"
                        f"  Detected attributes: {', '.join(found_targets)}\n"
                        f"  Missing required context: ANY of {', '.join(required)}\n"
                    )

        self.current_function = prev_function

    def visit_Attribute(self, node: ast.Attribute):
        if self.current_function:
            self.function_attributes[self.current_function].add(node.attr)
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

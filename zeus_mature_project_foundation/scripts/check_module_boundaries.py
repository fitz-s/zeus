from __future__ import annotations

import ast
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]

def load_yaml(path: str) -> dict:
    return yaml.safe_load((ROOT / path).read_text())

def iter_py_files() -> list[Path]:
    return [p for p in (ROOT / "src").rglob("*.py")]

def module_name_for(path: Path) -> str:
    rel = path.relative_to(ROOT).with_suffix("")
    return ".".join(rel.parts)

def import_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module)
    return names

def matches_prefix(name: str, prefixes: list[str]) -> bool:
    return any(name == p or name.startswith(p + ".") for p in prefixes)

def main() -> int:
    rules = load_yaml("architecture/zones.yaml")["forbidden_imports"]
    errors: list[str] = []

    for path in iter_py_files():
        module = module_name_for(path)
        tree = ast.parse(path.read_text())
        imports = import_names(tree)
        for rule in rules:
            if rule.get("stage") != "immediate":
                continue
            if not matches_prefix(module, rule["source_modules"]):
                continue
            for imp in imports:
                if matches_prefix(imp, rule["forbidden_modules"]):
                    errors.append(f"{path}: forbidden import {imp} via {rule['id']}")

    if errors:
        print("\n".join(errors))
        return 1
    print("module boundaries ok")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

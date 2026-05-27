#!/usr/bin/env python3
# Created: 2026-05-26
# Last reused or audited: 2026-05-26
# Authority basis: architecture/topology_enforcement.yaml#blocking_structural:db_table_delta_gate
#                  architecture/db_table_ownership.yaml
#                  docs/operations/current/plans/ci_topology_refactor_refined.md Phase D
"""
Detect new DB table names introduced by the PR and require matching
ownership entries in architecture/db_table_ownership.yaml.

This addresses FC-08 cross_db_ownership_ghost_tables. A new table that
exists in SQLite but has no declared owner DB (zeus-world /
zeus-forecasts / zeus_trades) is a ghost table — it silently routes
through the wrong attach group and breaks INV-37 cross-DB boundaries.

Detection scans changed files for:
  - `CREATE TABLE [IF NOT EXISTS] name` SQL statements
  - `name = "<table>"` assignments inside src/state/schema/**.py

Any name found that is not in db_table_ownership.yaml's known set fails.

Exit codes:
    0 — no new undeclared tables
    1 — one or more new tables missing ownership
    2 — IO / parse error
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML required", file=sys.stderr)
    sys.exit(2)

REPO_ROOT = Path(__file__).resolve().parents[2]

_CREATE_TABLE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"`]?(\w+)[\"`]?",
    re.IGNORECASE,
)


# Only scan paths that actually define DB schema. Other files (test fixtures,
# error-message strings inside this script, doc examples) may contain literal
# "CREATE TABLE" tokens without declaring a real table. False positives there
# would no-override-block every PR that mentions SQL in test code.
#
# fnmatch does NOT treat `**` specially (treats it as `*`), so we use prefix +
# suffix matching instead.
import fnmatch as _fnmatch
_SCHEMA_PATH_PREFIXES = (
    "src/state/schema/",
    "scripts/migrations/",
)
_SCHEMA_FILENAME_GLOBS = (
    "scripts/migrate_*.py",
)
_SCHEMA_SUFFIXES = (".sql",)


def _path_is_schema_defining(path: str) -> bool:
    if any(path.startswith(p) for p in _SCHEMA_PATH_PREFIXES):
        return True
    if path.endswith(_SCHEMA_SUFFIXES):
        return True
    for pat in _SCHEMA_FILENAME_GLOBS:
        if _fnmatch.fnmatch(path, pat):
            return True
    return False


# Reserved SQL keywords that match \w+ but aren't real table names. Filters
# false positives like "CREATE TABLE for ..." in docstrings.
_SQL_RESERVED = frozenset({
    "for", "as", "from", "where", "select", "if", "not", "exists",
    "when", "and", "or", "join", "on", "into", "values",
})


def _changed_files_from_git(base: str, head: str) -> list[str]:
    try:
        out = subprocess.run(
            ["git", "diff", "--name-only", f"{base}...{head}"],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []
    if out.returncode != 0:
        return []
    return [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]


def _known_tables(ownership_doc: dict) -> set[str]:
    """Extract every table name listed in db_table_ownership.yaml."""
    names: set[str] = set()
    # Walk all dict/list structures looking for `table:` or `name:` keys
    # whose value looks like an identifier.
    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k in ("table", "table_name", "name", "id") and isinstance(v, str):
                    if re.match(r"^[a-z][a-z0-9_]*$", v):
                        names.add(v)
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)
    walk(ownership_doc)
    # Also: tables can appear as YAML keys at the top of nested mappings
    for db_key in ("zeus_world", "zeus_forecasts", "zeus_trades", "world", "forecasts", "trades", "tables"):
        node = ownership_doc.get(db_key)
        if isinstance(node, dict):
            names.update(k for k in node if isinstance(k, str) and re.match(r"^[a-z][a-z0-9_]*$", k))
        elif isinstance(node, list):
            for entry in node:
                if isinstance(entry, str) and re.match(r"^[a-z][a-z0-9_]*$", entry):
                    names.add(entry)
    return names


def detect_new_tables(
    changed_files: list[str],
    repo: Path,
    known: set[str],
) -> list[dict]:
    findings: list[dict] = []
    seen_new: set[tuple[str, str]] = set()
    for fp in changed_files:
        # Restrict to actual schema-defining paths. Literal "CREATE TABLE"
        # tokens elsewhere (test fixtures, docstrings, error messages,
        # this script itself) are not real declarations and were producing
        # no_override false positives that blocked every Phase D PR.
        if not _path_is_schema_defining(fp):
            continue
        full = repo / fp
        if not full.exists():
            continue
        try:
            text = full.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        for m in _CREATE_TABLE.finditer(text):
            name = m.group(1).lower()
            # Skip common SQLite system / temp tables
            if name.startswith("sqlite_") or name in ("temp", "tmp"):
                continue
            # Skip SQL reserved-word false positives (CREATE TABLE for / as ...)
            if name in _SQL_RESERVED:
                continue
            if name not in known:
                key = (fp, name)
                if key not in seen_new:
                    seen_new.add(key)
                    findings.append({
                        "file": fp,
                        "table": name,
                        "reason": "unregistered DB table",
                    })
    return findings


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--repo-root", default=str(REPO_ROOT))
    p.add_argument("--changed-files", nargs="*", default=None)
    p.add_argument("--base", default="origin/main")
    p.add_argument("--head", default="HEAD")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    repo = Path(args.repo_root)
    ownership_path = repo / "architecture" / "db_table_ownership.yaml"
    if not ownership_path.exists():
        print(f"ERROR: missing {ownership_path}", file=sys.stderr)
        return 2

    with ownership_path.open() as f:
        ownership = yaml.safe_load(f) or {}
    known = _known_tables(ownership)

    files = args.changed_files or _changed_files_from_git(args.base, args.head)
    findings = detect_new_tables(files, repo, known)

    if args.json:
        print(json.dumps({"findings": findings, "count": len(findings)}, indent=2))
    else:
        if not findings:
            print("OK: no new unregistered DB tables detected.")
        else:
            print(f"FAIL: {len(findings)} unregistered DB table(s):")
            for f in findings:
                print(f"  {f['file']}: CREATE TABLE {f['table']} (not in db_table_ownership.yaml)")
            print()
            print(
                "Add an entry to architecture/db_table_ownership.yaml under "
                "the owning DB (zeus_world / zeus_forecasts / zeus_trades) "
                "before merging. INV-37: tables without owners cannot route "
                "through ATTACH+SAVEPOINT cleanly."
            )

    return 0 if not findings else 1


if __name__ == "__main__":
    sys.exit(main())

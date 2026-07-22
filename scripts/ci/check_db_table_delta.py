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
    "ddl", "sql",
})

# Known non-canonical sidecar/scratch tables created in a SEPARATE sidecar FILE
# (not a canonical DB): _capsule_meta = W0-a rollback-capsule metadata;
# _migrations_applied = the per-DB migration ledger. NARROWED (consult re-review
# 2026-07-22) from a blanket leading-underscore skip so a future _-named table
# created against a CANONICAL connection is NOT silently exempted.
_KNOWN_SIDECAR_TABLES = frozenset({"_capsule_meta", "_migrations_applied"})


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


_DB_OWNER_KEYS = (
    "zeus_world", "zeus_forecasts", "zeus_trades",
    "world", "forecasts", "trades",
)
_IDENT_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _known_tables(ownership_doc: dict) -> set[str]:
    """
    Extract table names from architecture/db_table_ownership.yaml.

    Only collects names that appear in a DB-owner scope. The previous
    implementation walked the whole document and treated any `name:` or
    `id:` string as a table — that incorrectly captured `name: applied_at`
    inside `required_columns` entries (Copilot finding on PR #345),
    which let real new tables sneak past as already-known.

    Recognized shapes:
      <db>:
        <table_name>:           # dict-of-tables
          ...
        - <table_name>          # list-of-strings
        - {table: <name>}       # list-of-objects (table key)
        - {name: <name>, ...}   # list-of-objects (name key — common shape)

      tables:
        <table_name>: ...
        - <name>
    """
    names: set[str] = set()

    def _scan_table_scope(node):
        """Within a known DB-owner scope, extract table-level names."""
        if isinstance(node, dict):
            # dict-of-tables: keys ARE table names
            for k, v in node.items():
                if isinstance(k, str) and _IDENT_RE.match(k):
                    # Only treat as table name if value is a dict-shaped
                    # table descriptor (skip when value itself looks like
                    # a column list or scalar).
                    if isinstance(v, (dict, list, type(None))):
                        names.add(k)
        elif isinstance(node, list):
            for entry in node:
                if isinstance(entry, str) and _IDENT_RE.match(entry):
                    names.add(entry)
                elif isinstance(entry, dict):
                    for key in ("table", "table_name", "name", "id"):
                        v = entry.get(key)
                        if isinstance(v, str) and _IDENT_RE.match(v):
                            names.add(v)
                            break

    for db_key in _DB_OWNER_KEYS + ("tables",):
        node = ownership_doc.get(db_key)
        if node is not None:
            _scan_table_scope(node)

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
            # Rebuild migrations commonly create transient *_new tables and
            # rename them into the registered canonical table in the same
            # transaction. They are not boot-visible ownership surfaces.
            if name.endswith("_new"):
                continue
            # Known non-canonical sidecar/scratch tables (created in a separate
            # sidecar FILE, not a canonical DB) — same class as *_new above. A
            # NARROW allowlist, not a blanket leading-underscore skip, so a future
            # _-named table created against a CANONICAL connection still trips the
            # gate rather than being silently exempted (consult re-review
            # 2026-07-22). The registry's own _IDENT_RE (^[a-z]...) also cannot
            # register these names.
            if name in _KNOWN_SIDECAR_TABLES:
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

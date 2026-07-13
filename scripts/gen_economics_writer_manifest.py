#!/usr/bin/env python3
# Created: 2026-07-13
# Last reused or audited: 2026-07-13
# Authority basis: docs/rebuild/local_ledger_excision_2026-07-12.md (LX-0R
#   deliverable 3) + docs/rebuild/consult_answers/local_ledger_excision_delta_round2_2026-07-13.txt
#   (BLOCKER "cutover authority": a generated static+runtime writer manifest is
#   required because local per-site flags are vulnerable to an omitted writer
#   or a stale daemon).
"""Static writer/reader manifest generator for the forbidden economics columns
(``src/contracts/economics_ownership.py``).

Scans every ``.py`` file under ``src/`` (AST + SQL-string regex — no import,
no execution) for:

  - WRITERS: every ``INSERT``/``UPDATE`` SQL string that names a forbidden
    table and touches at least one forbidden column, whether the SQL is a
    plain string literal or an f-string. An f-string whose column list is
    itself dynamic (e.g. ``f"UPDATE position_current SET {', '.join(cols)}"``)
    is resolved by scanning the enclosing function's own source for the
    forbidden column names as string literals (covers the
    ``position_duplicate_consolidator.py``-style dict-driven UPDATE). If that
    also finds nothing (the column list is an imported constant, e.g.
    ``src/state/projection.py``'s canonical-column funnel), the writer is
    still emitted — conservatively assumed to touch EVERY forbidden column of
    that table (``resolved=no``) rather than silently dropped. Complete >
    precise: a missed bypass writer is exactly the failure mode this manifest
    exists to prevent (round-2 delta BLOCKER).

  - READERS (best-effort): every ``SELECT ... FROM <forbidden table>`` SQL
    string that names a forbidden column in its own text. This is NOT a
    completeness claim for readers (a bare ``SELECT *`` is invisible to a
    static column-name scan) — writers are the graded completeness target
    this packet's seed expectation names; readers are informational.

Usage
-----
    python scripts/gen_economics_writer_manifest.py
        # (re)writes docs/rebuild/census_local_ledger/economics_writer_manifest.md

    python scripts/gen_economics_writer_manifest.py --check
        # exits 1 if the committed manifest has drifted from a fresh scan
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.contracts.economics_ownership import FORBIDDEN_COLUMNS_BY_TABLE  # noqa: E402

SRC_ROOT = ROOT / "src"
OUTPUT_PATH = ROOT / "docs" / "rebuild" / "census_local_ledger" / "economics_writer_manifest.md"

_WRITE_RE = re.compile(r"\b(INSERT\s+INTO|UPDATE)\s+([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)
_SELECT_RE = re.compile(r"\bSELECT\b", re.IGNORECASE)
_FROM_RE = re.compile(r"\bFROM\s+([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)


@dataclass(frozen=True)
class Hit:
    file: str
    line: int
    kind: str  # "WRITE" | "READ"
    verb: str  # "INSERT" | "UPDATE" | "SELECT"
    table: str
    columns: tuple[str, ...]
    function: str
    dynamic: bool
    resolved: bool

    def sort_key(self) -> tuple:
        return (self.file, self.line, self.kind, self.table, self.columns)


@dataclass
class _FuncSpan:
    start: int
    end: int
    qualname: str
    node: ast.AST


def _function_spans(tree: ast.AST) -> list[_FuncSpan]:
    spans: list[_FuncSpan] = []

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualname = f"{prefix}{child.name}"
                end = getattr(child, "end_lineno", child.lineno)
                spans.append(_FuncSpan(start=child.lineno, end=end, qualname=qualname, node=child))
                visit(child, f"{qualname}.")
            elif isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.")
            else:
                visit(child, prefix)

    visit(tree, "")
    return spans


def _enclosing_function(spans: list[_FuncSpan], lineno: int) -> _FuncSpan | None:
    candidates = [s for s in spans if s.start <= lineno <= s.end]
    if not candidates:
        return None
    # Innermost: smallest span wins.
    return min(candidates, key=lambda s: (s.end - s.start))


def _joinedstr_child_ids(tree: ast.AST) -> set[int]:
    """id() of every ast.Constant that is a literal segment INSIDE some
    JoinedStr (f-string). ast.walk() visits these as independent nodes too;
    without this exclusion set they get scanned a second time as if they were
    their own top-level string, double-counting the same hit."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            for value in node.values:
                ids.add(id(value))
    return ids


def _formatted_value_names(joined: ast.JoinedStr) -> set[str]:
    """Every identifier referenced inside any {...} interpolation of an
    f-string (not the literal text segments)."""
    names: set[str] = set()
    for value in joined.values:
        if isinstance(value, ast.FormattedValue):
            for n in ast.walk(value.value):
                if isinstance(n, ast.Name):
                    names.add(n.id)
    return names


def _locally_assigned_names(func_node: ast.AST) -> set[str]:
    """Every name assigned (=, augmented-assign, for-target) or bound as a
    parameter WITHIN func_node's own body. Used to tell apart 'this f-string's
    dynamic column list is built from a local variable in THIS function'
    (safe to resolve via a same-function literal-name scan, e.g.
    position_duplicate_consolidator.py's dict-driven UPDATE) from 'the column
    list is an imported/module-level constant' (e.g. src/state/projection.py's
    CANONICAL_POSITION_CURRENT_COLUMNS funnel) — the latter must NOT be
    "resolved" by a coincidental unrelated literal elsewhere in the function."""
    names: set[str] = set()
    if isinstance(func_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        args = func_node.args
        for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs):
            names.add(arg.arg)
        if args.vararg:
            names.add(args.vararg.arg)
        if args.kwarg:
            names.add(args.kwarg.arg)
    for node in ast.walk(func_node):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                for n in ast.walk(target):
                    if isinstance(n, ast.Name):
                        names.add(n.id)
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            for n in ast.walk(node.target):
                if isinstance(n, ast.Name):
                    names.add(n.id)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            for n in ast.walk(node.target):
                if isinstance(n, ast.Name):
                    names.add(n.id)
        elif isinstance(node, ast.NamedExpr):
            if isinstance(node.target, ast.Name):
                names.add(node.target.id)
    return names


def _literal_text_and_dynamic(node: ast.AST) -> tuple[str, bool]:
    """Return (literal-only text, has_interpolation) for a string-producing
    AST node. For a JoinedStr (f-string), interpolated {} segments are
    dropped from the text but their presence is flagged via the bool — this
    lets a purely-literal SET clause inside an f-string (e.g. a WHERE clause
    with an interpolated IN-list) still be matched directly."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value, False
    if isinstance(node, ast.JoinedStr):
        parts = []
        has_placeholder = False
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                has_placeholder = True
        return "".join(parts), has_placeholder
    return "", False


def _columns_mentioned(text: str, candidates: frozenset[str]) -> frozenset[str]:
    return frozenset(c for c in candidates if re.search(rf"\b{re.escape(c)}\b", text))


def _quoted_literals_mentioned(text: str, candidates: frozenset[str]) -> frozenset[str]:
    return frozenset(c for c in candidates if re.search(rf"""['"]{re.escape(c)}['"]""", text))


def scan_file(path: Path) -> list[Hit]:
    try:
        source = path.read_text()
        tree = ast.parse(source, filename=str(path))
    except (SyntaxError, UnicodeDecodeError) as exc:
        print(f"WARNING: skipping unparseable file {path}: {exc}", file=sys.stderr)
        return []

    spans = _function_spans(tree)
    lines = source.splitlines()
    rel = path.relative_to(ROOT).as_posix()
    hits: list[Hit] = []
    joinedstr_child_ids = _joinedstr_child_ids(tree)

    for node in ast.walk(tree):
        if not isinstance(node, (ast.Constant, ast.JoinedStr)):
            continue
        if isinstance(node, ast.Constant) and id(node) in joinedstr_child_ids:
            continue  # already covered via its owning JoinedStr — skip to avoid double count
        text, has_placeholder = _literal_text_and_dynamic(node)
        if not text:
            continue
        lineno = getattr(node, "lineno", 0)
        span = _enclosing_function(spans, lineno)
        func_name = span.qualname if span else "<module>"

        write_match = _WRITE_RE.search(text)
        if write_match:
            table = write_match.group(2)
            forbidden = FORBIDDEN_COLUMNS_BY_TABLE.get(table)
            if not forbidden:
                continue
            verb = "INSERT" if write_match.group(1).upper().startswith("INSERT") else "UPDATE"
            matched = _columns_mentioned(text, forbidden)
            dynamic = has_placeholder
            resolved = True
            if not matched and has_placeholder:
                referenced_names: set[str] = (
                    _formatted_value_names(node) if isinstance(node, ast.JoinedStr) else set()
                )
                local_names = _locally_assigned_names(span.node) if span is not None else set()
                # A name the interpolation references that is NOT assigned inside
                # this function (an import / module-level constant, e.g.
                # projection.py's CANONICAL_POSITION_CURRENT_COLUMNS) means the
                # true column list lives OUTSIDE this function's own text — a
                # same-function literal scan would find nothing real, or worse,
                # a coincidental unrelated match. Skip straight to the
                # conservative full-set fallback in that case.
                externally_sourced = bool(referenced_names - local_names) or span is None
                if not externally_sourced:
                    func_text = "\n".join(lines[span.start - 1:span.end])
                    matched = _quoted_literals_mentioned(func_text, forbidden)
                if not matched:
                    matched = forbidden
                    resolved = False
            if not matched:
                continue
            hits.append(
                Hit(
                    file=rel, line=lineno, kind="WRITE", verb=verb, table=table,
                    columns=tuple(sorted(matched)), function=func_name,
                    dynamic=dynamic, resolved=resolved,
                )
            )
            continue

        if _SELECT_RE.search(text):
            from_match = _FROM_RE.search(text)
            if not from_match:
                continue
            table = from_match.group(1)
            forbidden = FORBIDDEN_COLUMNS_BY_TABLE.get(table)
            if not forbidden:
                continue
            matched = _columns_mentioned(text, forbidden)
            if not matched:
                continue
            hits.append(
                Hit(
                    file=rel, line=lineno, kind="READ", verb="SELECT", table=table,
                    columns=tuple(sorted(matched)), function=func_name,
                    dynamic=has_placeholder, resolved=True,
                )
            )

    return hits


def scan_all() -> list[Hit]:
    hits: list[Hit] = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        hits.extend(scan_file(path))
    hits.sort(key=lambda h: h.sort_key())
    return hits


def render_manifest(hits: list[Hit]) -> str:
    writers = [h for h in hits if h.kind == "WRITE"]
    readers = [h for h in hits if h.kind == "READ"]

    lines: list[str] = []
    lines.append("# Economics Writer/Reader Manifest (GENERATED — do not hand-edit)")
    lines.append("")
    lines.append(
        "Generated by `scripts/gen_economics_writer_manifest.py` (LX-0R deliverable 3, "
        "docs/rebuild/local_ledger_excision_2026-07-12.md). Static AST + SQL-string-regex "
        "scan over `src/` for every INSERT/UPDATE touching a forbidden economics column "
        "(`src/contracts/economics_ownership.py`) plus best-effort SELECT read sites. "
        "Run `--check` in CI; drift (a bypass writer added or removed without updating "
        "this file) exits 1."
    )
    lines.append("")
    lines.append(
        f"Forbidden column set: {sum(len(v) for v in FORBIDDEN_COLUMNS_BY_TABLE.values())} "
        f"columns across {len(FORBIDDEN_COLUMNS_BY_TABLE)} tables "
        f"({', '.join(sorted(FORBIDDEN_COLUMNS_BY_TABLE))})."
    )
    lines.append("")
    lines.append(f"## Writers ({len(writers)})")
    lines.append("")
    lines.append("| file:line | verb | table | columns | dynamic | resolved | function |")
    lines.append("|---|---|---|---|---|---|---|")
    for h in writers:
        lines.append(
            f"| `{h.file}:{h.line}` | {h.verb} | {h.table} | {', '.join(h.columns)} | "
            f"{'yes' if h.dynamic else 'no'} | {'yes' if h.resolved else 'no (assumed full set)'} | "
            f"`{h.function}` |"
        )
    lines.append("")
    lines.append(f"## Readers ({len(readers)}, best-effort — literal SELECT column lists only)")
    lines.append("")
    lines.append("| file:line | table | columns | function |")
    lines.append("|---|---|---|---|")
    for h in readers:
        lines.append(
            f"| `{h.file}:{h.line}` | {h.table} | {', '.join(h.columns)} | `{h.function}` |"
        )
    lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument(
        "--check", action="store_true",
        help="Do not write; exit 1 if a fresh scan differs from the committed manifest.",
    )
    args = parser.parse_args(argv)

    hits = scan_all()
    content = render_manifest(hits)

    if args.check:
        existing = OUTPUT_PATH.read_text() if OUTPUT_PATH.exists() else ""
        if content != existing:
            print(f"DRIFT: {OUTPUT_PATH} does not match a fresh scan.", file=sys.stderr)
            print(
                "Run `python scripts/gen_economics_writer_manifest.py` and commit the "
                "result.",
                file=sys.stderr,
            )
            return 1
        print(f"OK: {OUTPUT_PATH} matches a fresh scan "
              f"({len([h for h in hits if h.kind == 'WRITE'])} writers, "
              f"{len([h for h in hits if h.kind == 'READ'])} readers).")
        return 0

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(content)
    print(f"Wrote {OUTPUT_PATH} "
          f"({len([h for h in hits if h.kind == 'WRITE'])} writers, "
          f"{len([h for h in hits if h.kind == 'READ'])} readers).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

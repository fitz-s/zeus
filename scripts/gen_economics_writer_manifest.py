#!/usr/bin/env python3
# Created: 2026-07-13
# Last reused or audited: 2026-07-13
# Authority basis: docs/rebuild/local_ledger_excision_2026-07-12.md (LX-0R
#   deliverable 3) + docs/rebuild/consult_answers/local_ledger_excision_delta_round2_2026-07-13.txt
#   (BLOCKER "cutover authority": a generated static+runtime writer manifest is
#   required because local per-site flags are vulnerable to an omitted writer
#   or a stale daemon) + wave-1.5 repair
#   (docs/rebuild/consult_answers/local_ledger_excision_wave1_local_verifier_2026-07-13.md
#   "MAJOR DEFECT" — a full-text ``--check`` keys drift on raw ``file:line``,
#   so any line-shifting commit elsewhere in the tree turns the gate red for
#   no real reason, training operators to ignore it) + a read-only LX-3R
#   firewall-scoping pass over this manifest's own output (2026-07-13) that
#   found the reader scan blind to a bare ``SELECT *`` against either
#   forbidden table, undercounting ``position_current`` readers and missing
#   every ``edli_live_profit_audit`` reader outright.
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
    exists to prevent (round-2 delta BLOCKER). The TABLE name itself can also
    be dynamic (e.g. ``src/state/projection.py``'s canonical funnel taking a
    ``table_name: str = "position_current"`` parameter so the same funnel can
    target a shadow/reduce table too) — ``_WRITE_RE``'s literal-identifier
    capture finds nothing at all in that shape, not just an unresolved column
    list, so it is resolved separately (``_dynamic_write_table``): only when
    the placeholder is a bare name bound, in the enclosing function's own
    signature/body, to exactly one literal string that is itself a known
    forbidden table. Ambiguous or unbound is left unresolved, never guessed.

  - READERS (best-effort): every ``SELECT ... FROM <forbidden table>`` SQL
    string that either names a forbidden column in its own text, or is a bare
    ``SELECT *`` / ``SELECT <alias>.*`` against a forbidden table — the
    latter is attributed to the table's FULL forbidden column set (same
    "can't name it, assume the worst" fallback the writer side uses for an
    unresolved dynamic column list), since a wildcard select always reads
    every column whether or not any forbidden name also happens to appear
    elsewhere in the same string (e.g. inside a WHERE/COALESCE clause). This
    closes the worst reader blind spot — a bare ``SELECT *`` used to be
    invisible to a literal column-name scan — but is still NOT a
    completeness claim: a forbidden column read only in Python after the row
    has already left SQL (a downstream function handed an already-fetched
    dict, e.g. ``row.get("promotion_eligible")``, with no SQL string of its
    own) is invisible to a source-text scanner by construction. Writers
    remain the graded completeness target this packet's seed expectation
    names; readers are best-effort-plus-seeded.

Usage
-----
    python scripts/gen_economics_writer_manifest.py
        # (re)writes docs/rebuild/census_local_ledger/economics_writer_manifest.md

    python scripts/gen_economics_writer_manifest.py --check
        # exits 1 if the committed manifest has drifted from a fresh scan.
        # Drift here means a WRITER/READER IDENTITY change — (file, function,
        # verb, table, sorted columns) — not a raw file:line renumbering. A
        # commit that only moves code around (shifting every subsequent
        # writer's line number) must not trip this gate; only a writer or
        # reader actually added, removed, or column-changed does. Line
        # numbers are still DISPLAYED in the rendered table for humans, they
        # just don't participate in the --check comparison.
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
# A bare wildcard select — `SELECT *` or `SELECT <alias>.*` — reads every
# column on the table, forbidden or not, and (unlike a literal column list)
# never names any of them. Matched independently of _columns_mentioned() so
# an incidental forbidden-column mention elsewhere in the same string (e.g.
# `COALESCE(shares, 0)` inside a WHERE clause on a `SELECT *`) can never
# narrow a wildcard read down to just that one name.
#
# Deliberately NOT anchored to the SAME "FROM <table>" that _FROM_RE resolved
# the table from: a `WITH cte AS (SELECT pc.* FROM position_current pc ...)
# SELECT * FROM cte` (see command_recovery.py's
# _invalid_open_entry_authority_candidates) has the forbidden table's first
# FROM feeding a CTE that is itself re-wildcarded by a second, later
# `SELECT * FROM <cte-name>` — two different clauses that both, correctly,
# imply a full read of the forbidden table. Requiring the two matches to name
# the same identifier would silently re-lose that CTE-wrapped case back into
# the blind spot this fix exists to close. Verified empirically (2026-07-13):
# across the whole src/ tree today, every SELECT_STAR_RE hit against a
# forbidden table either matches the exact same FROM _FROM_RE used, or is
# this one CTE shape — never an unrelated table.
_SELECT_STAR_RE = re.compile(r"SELECT\s+(?:[A-Za-z_][A-Za-z0-9_]*\.)?\*\s+FROM", re.IGNORECASE)
# A write verb with no literal table name to its right at all — used only as
# a fallback when _WRITE_RE has already failed to match, i.e. the table name
# itself is an f-string placeholder (``f"INSERT INTO {table_name} ..."``),
# which strips to nothing in the literal-only text (see
# _dynamic_write_table below).
_WRITE_VERB_TAIL_RE = re.compile(r"\b(INSERT\s+INTO|UPDATE)\s*$", re.IGNORECASE)
# Cheap pre-filter before attempting the (more expensive) dynamic-table-name
# resolution below: is there even a write verb ANYWHERE in this text? Unlike
# _WRITE_VERB_TAIL_RE this is not end-anchored — it just gates whether
# _dynamic_write_table's own per-segment/enclosing-function walk is worth
# attempting at all, since most string literals in the tree contain neither
# word.
_WRITE_VERB_ANYWHERE_RE = re.compile(r"\b(?:INSERT\s+INTO|UPDATE)\b", re.IGNORECASE)


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


def _default_and_assigned_string_literals(func_node: ast.AST) -> dict[str, str]:
    """Map of ``{name: literal string value}`` for every name in
    func_node's own signature/body bound to EXACTLY ONE literal string
    constant — a parameter default (``table_name: str = "position_current"``)
    or a simple ``name = "literal"`` assignment. A name bound more than once
    (or to anything other than a bare string constant) is excluded rather
    than guessed. Table-name analogue of the column-list resolution
    _locally_assigned_names/_quoted_literals_mentioned already do together —
    used only by _dynamic_write_table below."""
    seen: dict[str, str | None] = {}

    def bind(name: str, value_node: ast.AST | None) -> None:
        value = (
            value_node.value
            if isinstance(value_node, ast.Constant) and isinstance(value_node.value, str)
            else None
        )
        if name in seen and seen[name] != value:
            seen[name] = None  # conflicting binding — refuse to guess
        elif name not in seen:
            seen[name] = value

    if isinstance(func_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        args = func_node.args
        positional = (*args.posonlyargs, *args.args)
        for arg, default in zip(reversed(positional), reversed(args.defaults)):
            bind(arg.arg, default)
        for arg, default in zip(args.kwonlyargs, args.kw_defaults):
            if default is not None:
                bind(arg.arg, default)
    for node in ast.walk(func_node):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            bind(node.targets[0].id, node.value)
    return {name: value for name, value in seen.items() if value is not None}


def _dynamic_write_table(node: ast.AST, func_node: ast.AST | None) -> tuple[str, str] | None:
    """(verb, table) for an INSERT/UPDATE whose table name is ITSELF an
    f-string placeholder immediately after the verb — e.g.
    ``f"INSERT INTO {table_name} (...)"``. _WRITE_RE's literal-identifier
    capture finds nothing at all for this shape (not just an unresolved
    column list — the whole write is invisible), so it needs a separate
    resolution path: only when the placeholder is a bare Name, and that name
    is bound, inside the enclosing function's own signature/body, to exactly
    one literal string that is itself a known forbidden table. Zero or
    ambiguous candidates return None — refuse to guess which table, same
    discipline the column fallback already uses."""
    if not isinstance(node, ast.JoinedStr) or func_node is None:
        return None
    bound = _default_and_assigned_string_literals(func_node)
    for index, part in enumerate(node.values):
        if not (isinstance(part, ast.Constant) and isinstance(part.value, str)):
            continue
        verb_match = _WRITE_VERB_TAIL_RE.search(part.value)
        if not verb_match or index + 1 >= len(node.values):
            continue
        nxt = node.values[index + 1]
        if not (isinstance(nxt, ast.FormattedValue) and isinstance(nxt.value, ast.Name)):
            continue
        table = bound.get(nxt.value.id)
        if table and table in FORBIDDEN_COLUMNS_BY_TABLE:
            verb = "INSERT" if verb_match.group(1).upper().startswith("INSERT") else "UPDATE"
            return verb, table
    return None


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
        table = write_match.group(2) if write_match else None
        verb = (
            ("INSERT" if write_match.group(1).upper().startswith("INSERT") else "UPDATE")
            if write_match else None
        )
        if table not in FORBIDDEN_COLUMNS_BY_TABLE and has_placeholder and _WRITE_VERB_ANYWHERE_RE.search(text):
            # Either _WRITE_RE found nothing (the table name is entirely an
            # f-string placeholder, so it strips to nothing in the literal
            # text), or it found a SPURIOUS match elsewhere in the same text
            # once the real one was stripped out — e.g. "INSERT INTO
            # {table_name} (...) ON CONFLICT(...) DO UPDATE SET ..."
            # degenerates, with {table_name} gone, into a second "UPDATE
            # SET" match where "SET" looks like a (bogus) table identifier.
            # Either way, try resolving the real table via a same-function
            # literal binding before giving up (see _dynamic_write_table).
            dynamic_write = _dynamic_write_table(node, span.node if span else None)
            if dynamic_write is not None:
                verb, table = dynamic_write
        if write_match or table is not None:
            forbidden = FORBIDDEN_COLUMNS_BY_TABLE.get(table) if table else None
            if not forbidden:
                continue
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
            if _SELECT_STAR_RE.search(text):
                matched = forbidden
                resolved = False
            else:
                matched = _columns_mentioned(text, forbidden)
                resolved = True
            if not matched:
                continue
            hits.append(
                Hit(
                    file=rel, line=lineno, kind="READ", verb="SELECT", table=table,
                    columns=tuple(sorted(matched)), function=func_name,
                    dynamic=has_placeholder, resolved=resolved,
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
    lines.append(
        f"## Readers ({len(readers)}, best-effort — literal SELECT column lists "
        "plus bare SELECT * / SELECT <alias>.* against a forbidden table)"
    )
    lines.append("")
    lines.append("| file:line | table | columns | function |")
    lines.append("|---|---|---|---|")
    for h in readers:
        lines.append(
            f"| `{h.file}:{h.line}` | {h.table} | {', '.join(h.columns)} | `{h.function}` |"
        )
    lines.append("")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# Drift identity — line-insensitive (wave-1.5 repair)                         #
# --------------------------------------------------------------------------- #
#
# A row's DRIFT IDENTITY is (file, function, verb, table, sorted columns) —
# deliberately excluding ``line``. Two scans that agree on every identity are
# the SAME writer/reader set even if unrelated commits moved code around and
# shifted line numbers. This is what --check compares; the rendered
# file:line in the markdown table remains purely a human-readable pointer.

_RowIdentity = tuple  # (file, function, verb, table, tuple[str, ...] columns)


def _identity(h: Hit) -> "_RowIdentity":
    return (h.file, h.function, h.verb, h.table, h.columns)


_WRITER_ROW_RE = re.compile(
    r"^\|\s*`(?P<file>[^`:]+):(?P<line>\d+)`\s*\|\s*(?P<verb>INSERT|UPDATE)\s*\|\s*"
    r"(?P<table>[^|]+?)\s*\|\s*(?P<columns>[^|]*?)\s*\|\s*(?:yes|no)\s*\|\s*"
    r"(?:yes|no \(assumed full set\))\s*\|\s*`(?P<function>[^`]+)`\s*\|\s*$"
)
_READER_ROW_RE = re.compile(
    r"^\|\s*`(?P<file>[^`:]+):(?P<line>\d+)`\s*\|\s*(?P<table>[^|]+?)\s*\|\s*"
    r"(?P<columns>[^|]*?)\s*\|\s*`(?P<function>[^`]+)`\s*\|\s*$"
)


def _parse_manifest_identities(text: str) -> tuple[frozenset, frozenset]:
    """Parse a rendered manifest's Writer/Reader tables back into drift-identity
    tuples, ignoring the embedded file:line. Used by --check so a pure
    line-shift (an unrelated commit moving code around) never trips drift
    detection — only a writer/reader actually added, removed, or with
    changed table/columns does."""
    writers: set[tuple] = set()
    readers: set[tuple] = set()
    section = None
    for line in text.splitlines():
        if line.startswith("## Writers"):
            section = "writers"
            continue
        if line.startswith("## Readers"):
            section = "readers"
            continue
        if section == "writers":
            m = _WRITER_ROW_RE.match(line)
            if m:
                cols = tuple(sorted(c.strip() for c in m.group("columns").split(",") if c.strip()))
                writers.add((m.group("file"), m.group("function"), m.group("verb"), m.group("table"), cols))
        elif section == "readers":
            m = _READER_ROW_RE.match(line)
            if m:
                cols = tuple(sorted(c.strip() for c in m.group("columns").split(",") if c.strip()))
                readers.add((m.group("file"), m.group("function"), "SELECT", m.group("table"), cols))
    return frozenset(writers), frozenset(readers)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument(
        "--check", action="store_true",
        help="Do not write; exit 1 if a fresh scan's writer/reader IDENTITY set "
             "(file, function, verb, table, columns — not line number) differs "
             "from the committed manifest's.",
    )
    args = parser.parse_args(argv)

    hits = scan_all()
    content = render_manifest(hits)

    if args.check:
        existing = OUTPUT_PATH.read_text() if OUTPUT_PATH.exists() else ""
        existing_writers, existing_readers = _parse_manifest_identities(existing)
        fresh_writers = frozenset(_identity(h) for h in hits if h.kind == "WRITE")
        fresh_readers = frozenset(_identity(h) for h in hits if h.kind == "READ")
        added_writers = fresh_writers - existing_writers
        removed_writers = existing_writers - fresh_writers
        added_readers = fresh_readers - existing_readers
        removed_readers = existing_readers - fresh_readers
        if added_writers or removed_writers or added_readers or removed_readers:
            print(f"DRIFT: {OUTPUT_PATH} writer/reader identity set does not match a fresh scan.",
                  file=sys.stderr)
            for label, rows in (
                ("writer added", added_writers), ("writer removed", removed_writers),
                ("reader added", added_readers), ("reader removed", removed_readers),
            ):
                for row in sorted(rows):
                    print(f"  {label}: {row}", file=sys.stderr)
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

"""Representation-contract checker family for topology_doctor (`--repr`)."""
# Lifecycle: created=2026-07-08; last_reviewed=2026-07-08; last_reused=never
# Purpose: advisory-only heuristic checks against docs/rebuild/representation_contract_2026-07-08.md
#   (comment law banned patterns, canonical_vocabulary.yaml forbidden aliases in new defs, AGENTS.md
#   token budgets). Always returns ok=True / exit 0 -- a signal surface for R0-h, promoted to blocking
#   only under contract Sec 5 R8. Scope: NEW or changed files only (legacy is migration debt).
# Reuse: mirror this module's shape (api-facade pattern, dict payload) for future --repr sub-checks;
#   keep the representation-contract domain separate from topology_doctor_docs_checks.py per contract
#   Sec 4 (doctor stays split by domain).

from __future__ import annotations

import ast
import re
from typing import Any

# --- (a) banned comment patterns -------------------------------------------------

# Lifecycle header fields banned as free-form comment prose by the comment law
# (contract Sec 1.1): created/last_reviewed/last_reused/audited. NOTE: this is
# distinct from topology_doctor_freshness_checks.py's *required* machine-checked
# `Lifecycle: created=...; last_reviewed=...; last_reused=...` header on scripts/
# tests -- that is a structured, checked field, not banned prose. This pattern
# targets free-text variants such as "# Created: 2026-06-14" / "# Last reused or
# audited: 2026-06-29" seen verbatim in src/decision/family_decision_engine.py and
# src/engine/event_reactor_adapter.py.
LIFECYCLE_HEADER_PATTERN = re.compile(
    r"^\s*#\s*(created|last[\s_-]?reviewed|last[\s_-]?reused(?:\s+or\s+audited)?|audited)\s*:",
    re.IGNORECASE,
)

# Dated incident narrative: an ISO date within 3 lines of an incident/fix keyword.
DATE_PATTERN = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
INCIDENT_KEYWORD_PATTERN = re.compile(
    r"\b(fix|incident|directive|hotfix|root cause)\b", re.IGNORECASE
)

# Authority/wiring-status exclusivity claims -- the "most-banned" class per contract
# Sec 1.1 (4/4 verified-WRONG comments were this shape). Curated compound phrases,
# not the bare word "only" (too common in ordinary prose to be a useful signal).
AUTHORITY_CLAIM_PATTERNS = [
    re.compile(r"\bONLY\b[^.\n]{0,60}\bauthority\b", re.IGNORECASE),
    re.compile(r"\bsole\b[^.\n]{0,60}\bauthority\b", re.IGNORECASE),
    re.compile(r"\bunconditional single\b", re.IGNORECASE),
    re.compile(r"\bnothing wires\b", re.IGNORECASE),
    re.compile(r"\bunwired dead\b", re.IGNORECASE),
]

COMMENT_LINE_PATTERN = re.compile(r"^\s*#")


def _comment_lines(text: str) -> list[tuple[int, str]]:
    return [
        (i + 1, line)
        for i, line in enumerate(text.splitlines())
        if COMMENT_LINE_PATTERN.match(line)
    ]


def check_banned_comment_patterns(api: Any, path: str, text: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    lines = text.splitlines()
    comment_lines = _comment_lines(text)

    for lineno, line in comment_lines:
        if LIFECYCLE_HEADER_PATTERN.search(line):
            findings.append(
                {
                    "code": "repr_banned_lifecycle_header_comment",
                    "path": f"{path}:{lineno}",
                    "message": "lifecycle-header prose (created/last_reviewed/last_reused/audited) "
                    "is banned in comments per representation contract Sec 1.1 -- history belongs to git",
                    "severity": "warning",
                }
            )

    for lineno, line in comment_lines:
        if not DATE_PATTERN.search(line):
            continue
        window_start = max(0, lineno - 1 - 3)
        window_end = min(len(lines), lineno + 3)
        window = "\n".join(lines[window_start:window_end])
        if INCIDENT_KEYWORD_PATTERN.search(window):
            findings.append(
                {
                    "code": "repr_dated_incident_narrative_comment",
                    "path": f"{path}:{lineno}",
                    "message": "dated incident/fix narrative in a comment is banned per representation "
                    "contract Sec 1.1 -- incident history belongs to git, not prose that rots in place",
                    "severity": "warning",
                }
            )

    for lineno, line in comment_lines:
        for pattern in AUTHORITY_CLAIM_PATTERNS:
            if pattern.search(line):
                findings.append(
                    {
                        "code": "repr_authority_claim_comment",
                        "path": f"{path}:{lineno}",
                        "message": "exclusivity/wiring-status claim ('ONLY'/'sole'/'unconditional single'/"
                        "'nothing wires'/'unwired dead') in a comment -- the highest-rot comment class per "
                        "contract Sec 1.1 (4/4 verified-WRONG comments were this shape); state the mechanism "
                        "(flag name, wiring point) instead of an exclusivity adjective",
                        "severity": "warning",
                    }
                )
                break

    return findings


# --- (b) forbidden aliases in NEW function/class defs ----------------------------


def _forbidden_alias_index(vocabulary: dict[str, Any]) -> dict[str, str]:
    """Map lowercase forbidden-alias token -> canonical term that should be used instead.

    A token that is itself the canonical name of ANOTHER concept in the vocabulary is
    excluded: several clusters use each other's canonical term as a sibling's forbidden
    alias (e.g. "resolve" is forbidden_alias of settlement_grade but is ALSO the canonical
    name of settlement.resolve; "certificate" is forbidden_alias of decision_receipt but is
    ALSO canonical for proof.certificate). A purely lexical token match cannot tell which
    concept a given use means, so flagging it either way risks giving actively wrong
    advice. Conservative choice for an advisory linter: only flag aliases that are not
    themselves canonical anywhere.
    """
    canonical_names = {str(term.get("canonical") or "").lower() for term in vocabulary.get("terms") or []}
    index: dict[str, str] = {}
    for term in vocabulary.get("terms") or []:
        canonical = str(term.get("canonical") or "")
        for alias in term.get("forbidden_aliases") or []:
            alias_lower = str(alias).lower()
            if alias_lower in canonical_names:
                continue
            index[alias_lower] = canonical
    return index


def check_forbidden_aliases_in_new_defs(
    api: Any, path: str, text: str, vocabulary: dict[str, Any]
) -> list[dict[str, Any]]:
    if not path.endswith(".py"):
        return []
    alias_index = _forbidden_alias_index(vocabulary)
    if not alias_index:
        return []

    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []

    findings: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        tokens = [tok.lower() for tok in node.name.split("_") if tok]
        for tok in tokens:
            canonical = alias_index.get(tok)
            if canonical:
                findings.append(
                    {
                        "code": "repr_forbidden_alias_in_new_def",
                        "path": f"{path}:{node.lineno}",
                        "message": f"new def/class {node.name!r} uses forbidden-alias token {tok!r}; "
                        f"canonical_vocabulary.yaml maps this concept to {canonical!r}",
                        "severity": "warning",
                    }
                )
                break  # one finding per def is enough signal
    return findings


# --- (c) AGENTS.md token budgets (report-only) ------------------------------------

ROOT_AGENTS_TOKEN_BUDGET = 2500
SCOPED_AGENTS_TOKEN_BUDGET = 500
# contract Sec 1.3: root <=2.5K tokens ~= 10KB, scoped <=500 tokens ~= 2KB -> ~4 chars/token.
CHARS_PER_TOKEN_ESTIMATE = 4


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN_ESTIMATE)


def check_agents_token_budgets(api: Any) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    tracked = api._git_ls_files()
    agents_paths = sorted(p for p in tracked if p == "AGENTS.md" or p.endswith("/AGENTS.md"))

    for rel in agents_paths:
        target = api.ROOT / rel
        if not target.exists() or not target.is_file():
            continue
        text = target.read_text(encoding="utf-8", errors="ignore")
        tokens = _estimate_tokens(text)
        is_root = rel == "AGENTS.md"
        budget = ROOT_AGENTS_TOKEN_BUDGET if is_root else SCOPED_AGENTS_TOKEN_BUDGET
        findings.append(
            {
                "code": "repr_agents_token_budget_report",
                "path": rel,
                "message": f"~{tokens} tokens (budget {budget}, {'root' if is_root else 'scoped'}) "
                f"{'OVER BUDGET' if tokens > budget else 'within budget'}",
                "severity": "info" if tokens <= budget else "warning",
            }
        )
    return findings


# --- entry point -------------------------------------------------------------------


def run_repr(api: Any, files: list[str] | None = None) -> dict[str, Any]:
    """Run all --repr checks. Advisory-only: always returns ok=True. Exit code is always 0.

    files: explicit file list (--files), or None/empty to use changed-vs-HEAD (git status).
    """
    changes = api._map_maintenance_changes(files or [])
    target_files = sorted(
        path for path, kind in changes.items() if kind != "deleted" and path.endswith(".py")
    )

    vocabulary = api.load_canonical_vocabulary()

    findings: list[dict[str, Any]] = []
    for path in target_files:
        target = api.ROOT / path
        if not target.exists() or not target.is_file():
            continue
        text = target.read_text(encoding="utf-8", errors="ignore")
        findings.extend(check_banned_comment_patterns(api, path, text))
        findings.extend(check_forbidden_aliases_in_new_defs(api, path, text, vocabulary))

    findings.extend(check_agents_token_budgets(api))

    return {
        "ok": True,
        "advisory": True,
        "scope": "changed-files (--files or git status vs HEAD) for comment/alias checks; "
        "full repo for AGENTS.md token budget report",
        "files_checked": target_files,
        "finding_count": len(findings),
        "findings": findings,
    }

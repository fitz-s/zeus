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
import subprocess
from pathlib import Path
from typing import Any

try:
    from scripts._yaml_bootstrap import import_yaml
except ModuleNotFoundError:  # direct script execution from scripts/
    from _yaml_bootstrap import import_yaml

yaml = import_yaml()

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


# --- (a2) stale comment references (file/symbol no longer exists) ----------------

# Repo-relative path literals mentioned in comments, e.g. `src/solve/solver.py:873`
# or `architecture/invariants.yaml`. Anchored to known top-level dirs to avoid
# matching prose fractions or URLs.
FILE_REF_PATTERN = re.compile(
    r"\b((?:src|tests|scripts|architecture|docs|config|data)/[\w./-]+\."
    r"(?:py|yaml|yml|md|sql|json))\b"
)

# Backtick-quoted call-looking symbol refs -- a comment naming `<some_helper>()` or
# `<Class>.<method>()` verbatim -- take the last dotted segment as the symbol to verify.
SYMBOL_REF_PATTERN = re.compile(r"`(?:[A-Za-z_][A-Za-z0-9_]*\.)*([A-Za-z_][A-Za-z0-9_]*)\(\)`")

_DEF_LINE_PATTERN = re.compile(
    r"^\s*(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(|^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)\b"
)


def build_repo_symbol_index(api: Any) -> set[str]:
    """All def/class names across tracked .py files. Built once per run() call --
    O(2.6K files, <1s) -- and passed into check_stale_symbol_reference_comments so
    per-file checks don't re-scan the repo."""
    names: set[str] = set()
    for rel in api._git_ls_files():
        if not rel.endswith(".py"):
            continue
        target = api.ROOT / rel
        if not target.exists() or not target.is_file():
            continue
        text = target.read_text(encoding="utf-8", errors="ignore")
        for line in text.splitlines():
            m = _DEF_LINE_PATTERN.match(line)
            if m:
                names.add(m.group(1) or m.group(2))
    return names


def check_stale_file_reference_comments(api: Any, path: str, text: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for lineno, line in _comment_lines(text):
        for m in FILE_REF_PATTERN.finditer(line):
            ref = m.group(1).rstrip(".,;:)")
            if not (api.ROOT / ref).exists():
                findings.append(
                    {
                        "code": "repr_stale_file_reference_comment",
                        "path": f"{path}:{lineno}",
                        "message": f"comment cites {ref!r}, which does not exist in the tree -- "
                        "verifiably stale per representation contract Sec 1.1 (comments must state "
                        "facts code+git cannot derive; a dead path is not one)",
                        "severity": "warning",
                    }
                )
    return findings


def check_stale_symbol_reference_comments(
    api: Any, path: str, text: str, symbol_index: set[str]
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for lineno, line in _comment_lines(text):
        for m in SYMBOL_REF_PATTERN.finditer(line):
            symbol = m.group(1)
            if symbol not in symbol_index:
                findings.append(
                    {
                        "code": "repr_stale_symbol_reference_comment",
                        "path": f"{path}:{lineno}",
                        "message": f"comment cites `{symbol}()`, which has no matching def/class "
                        "anywhere in tracked .py files -- verifiably stale per contract Sec 1.1",
                        "severity": "warning",
                    }
                )
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


# --- (d) metadata law: checked_policy_input rows must declare a drift_detector ---

# contract Sec 1.3 hard rule: writer != none AND drift_detector != none, else the
# row is a delete-candidate. The named checked_policy_input registries (invariants,
# negative_constraints, fatal_misreads) are hand-written (writer=hand, a legitimate
# declared writer for this class) but MUST cite an executable checker per row --
# tests/semgrep_rule_ids/schema/proof_files. A row citing none of these has no
# drift_detector and is invalid per contract Sec 1.3 ("无 checker 的行无效").
METADATA_ROW_REGISTRIES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    # (relpath, list-key, enforced_by dict keys that count as a binding)
    ("architecture/invariants.yaml", "invariants", ("spec_sections", "semgrep_rule_ids", "tests", "schema")),
    ("architecture/negative_constraints.yaml", "constraints", ("scripts", "semgrep_rule_ids", "tests", "docs")),
)


def _load_yaml_file(target: Path) -> dict[str, Any]:
    return yaml.safe_load(target.read_text(encoding="utf-8")) or {}


def check_metadata_row_drift_detector(api: Any) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []

    for relpath, list_key, binding_keys in METADATA_ROW_REGISTRIES:
        target = api.ROOT / relpath
        if not target.exists():
            continue
        doc = _load_yaml_file(target)
        for row in doc.get(list_key) or []:
            row_id = row.get("id") or "<no-id>"
            enforced = row.get("enforced_by") or {}
            has_binding = any(enforced.get(k) for k in binding_keys)
            if not has_binding:
                findings.append(
                    {
                        "code": "repr_metadata_row_no_drift_detector",
                        "path": f"{relpath}#{row_id}",
                        "message": f"{row_id!r} in {relpath} has no enforced_by binding "
                        f"({'/'.join(binding_keys)}) -- writer=hand but drift_detector=none, "
                        "delete-candidate per representation contract Sec 1.3 hard rule "
                        "(report-only: this checker does not delete registry rows)",
                        "severity": "warning",
                    }
                )

    # fatal_misreads.yaml: required_misread_fields includes `tests` directly (no
    # nested enforced_by wrapper) -- same hard rule, different row schema.
    fatal_misreads_path = api.ROOT / "architecture" / "fatal_misreads.yaml"
    if fatal_misreads_path.exists():
        doc = _load_yaml_file(fatal_misreads_path)
        for row in doc.get("misreads") or []:
            row_id = row.get("id") or "<no-id>"
            if not row.get("tests"):
                findings.append(
                    {
                        "code": "repr_metadata_row_no_drift_detector",
                        "path": f"architecture/fatal_misreads.yaml#{row_id}",
                        "message": f"{row_id!r} has no `tests` binding -- writer=hand but "
                        "drift_detector=none, delete-candidate per contract Sec 1.3 hard rule "
                        "(report-only)",
                        "severity": "warning",
                    }
                )

    return findings


# --- (e) anchor law: bidirectional lint for the 2 registered ID families ---------

# contract Sec 1.4: 7 of 9 anchor families have no registry (K1, R3, W0-W5,
# F-numbers, BLOCKER-N, C1/C2/C3, gate-135) -- registering them is R8/operator
# territory, not machine-checkable here. INV- and FC- are the only 2 registered
# families (contract Sec 0), so this check is scoped to those two.
ANCHOR_FAMILIES: tuple[tuple[str, str], ...] = (("INV", "invariants.yaml"), ("FC", "failure_chains.yaml"))


def _registered_inv_ids(api: Any) -> set[str]:
    inv = api.load_invariants()
    return {str(item.get("id")) for item in inv.get("invariants") or [] if item.get("id")}


def _registered_fc_ids(api: Any) -> set[str]:
    target = api.ROOT / "architecture" / "failure_chains.yaml"
    if not target.exists():
        return set()
    doc = _load_yaml_file(target)
    return {str(k) for k in (doc.get("chains") or {}).keys()}


def _git_grep_numeric_ids(api: Any, prefix: str) -> list[tuple[str, int, str]]:
    """Return (path, lineno, matched-id) for every numeric `<prefix>-<digits>` hit
    in tracked files. Placeholder tokens like INV-NN/FC-id (non-digit suffix) are
    excluded by the pattern itself."""
    proc = subprocess.run(
        ["git", "grep", "-noE", rf"{prefix}-[0-9]+"],
        cwd=api.ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    hits: list[tuple[str, int, str]] = []
    for line in proc.stdout.splitlines():
        parts = line.split(":", 2)
        if len(parts) != 3:
            continue
        rel, lineno_s, matched = parts
        try:
            lineno = int(lineno_s)
        except ValueError:
            continue
        hits.append((rel, lineno, matched))
    return hits


def _exact_id_referenced(api: Any, rid: str, exclude_path: str) -> bool:
    """Non-numeric-suffix IDs (e.g. INV-Harvester-Liveness) fall outside the
    zero-padding logic below; check by exact literal grep instead."""
    proc = subprocess.run(
        ["git", "grep", "-nF", rid],
        cwd=api.ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    for line in proc.stdout.splitlines():
        rel = line.split(":", 1)[0]
        if rel != exclude_path:
            return True
    return False


def check_anchor_bidirectional(api: Any) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    registries = {"INV": _registered_inv_ids(api), "FC": _registered_fc_ids(api)}
    registry_files = {"INV": "architecture/invariants.yaml", "FC": "architecture/failure_chains.yaml"}

    for prefix, all_registered in registries.items():
        registry_path = registry_files[prefix]
        numeric_registered = {rid for rid in all_registered if rid.split("-", 1)[1].isdigit()}
        non_numeric_registered = all_registered - numeric_registered

        for rid in sorted(non_numeric_registered):
            if not _exact_id_referenced(api, rid, registry_path):
                findings.append(
                    {
                        "code": "repr_anchor_registered_no_reference",
                        "path": f"{registry_path}#{rid}",
                        "message": f"{rid!r} is registered but has zero references outside its own "
                        "declaration -- contract Sec 1.4 requires every registry row have "
                        "≥1 binding point",
                        "severity": "warning",
                    }
                )

        registered = numeric_registered
        registered_norm = {rid: rid.split("-", 1)[1].lstrip("0") or "0" for rid in registered}
        norm_to_registered = {v: k for k, v in registered_norm.items()}
        referenced_norm: set[str] = set()

        for rel, lineno, matched in _git_grep_numeric_ids(api, prefix):
            if rel == registry_files[prefix]:
                continue  # the registry's own declaration line is not a "reference"
            digits = matched.split("-", 1)[1]
            norm = digits.lstrip("0") or "0"
            referenced_norm.add(norm)
            canonical = norm_to_registered.get(norm)
            if canonical is None:
                findings.append(
                    {
                        "code": "repr_anchor_unregistered_reference",
                        "path": f"{rel}:{lineno}",
                        "message": f"{matched!r} is not registered in {registry_files[prefix]} -- "
                        "an unregistered ID is an anti-anchor per contract Sec 1.4",
                        "severity": "warning",
                    }
                )
            elif matched != canonical:
                findings.append(
                    {
                        "code": "repr_anchor_unpadded_form",
                        "path": f"{rel}:{lineno}",
                        "message": f"{matched!r} should be {canonical!r} (unpadded/malformed form "
                        "of a registered ID -- contract Sec 1.4.4 treats this as an error)",
                        "severity": "warning",
                    }
                )

        for rid in sorted(registered):
            norm = registered_norm[rid]
            if norm not in referenced_norm:
                findings.append(
                    {
                        "code": "repr_anchor_registered_no_reference",
                        "path": f"{registry_files[prefix]}#{rid}",
                        "message": f"{rid!r} is registered but has zero code/doc references outside "
                        f"its own declaration -- contract Sec 1.4 requires every registry row have "
                        f"≥1 binding point",
                        "severity": "warning",
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
    symbol_index = build_repo_symbol_index(api)

    findings: list[dict[str, Any]] = []
    for path in target_files:
        target = api.ROOT / path
        if not target.exists() or not target.is_file():
            continue
        text = target.read_text(encoding="utf-8", errors="ignore")
        findings.extend(check_banned_comment_patterns(api, path, text))
        findings.extend(check_stale_file_reference_comments(api, path, text))
        findings.extend(check_stale_symbol_reference_comments(api, path, text, symbol_index))
        findings.extend(check_forbidden_aliases_in_new_defs(api, path, text, vocabulary))

    findings.extend(check_agents_token_budgets(api))
    findings.extend(check_metadata_row_drift_detector(api))
    findings.extend(check_anchor_bidirectional(api))

    return {
        "ok": True,
        "advisory": True,
        "scope": "changed-files (--files or git status vs HEAD) for comment/alias/stale-reference "
        "checks; full repo for AGENTS.md token budget, metadata-row drift-detector, and anchor "
        "bidirectional-lint reports",
        "files_checked": target_files,
        "finding_count": len(findings),
        "findings": findings,
    }

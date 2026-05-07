"""Digest builder family for topology_doctor.

Architecture (post-P0 admission repair):

    build_digest(task, files)
      -> _collect_evidence(task, files, topology)
      -> _resolve_profile(evidence, topology)
      -> _reconcile_admission(profile, requested_files, evidence, topology)
      -> envelope { command_ok, schema_version: "2", admission, ... }

Two contracts the previous version conflated, now split:

  * Profile selection ("which route") is a *suggestion*. It can fall back
    to a generic advisory profile that grants no admission.
  * Admission ("which files may this agent change for this task") is a
    separate decision. forbidden_files always wins. Out-of-scope requested
    files surface as `scope_expansion_required`, not silently approved.

The legacy top-level `allowed_files` is preserved as a mirror of
`profile_suggested_files` and is annotated with `legacy_advisory: true`. It
is no longer load-bearing for write authorization. Callers must read
`admission.admitted_files` and `admission.status`.
"""
# Lifecycle: created=2026-04-15; last_reviewed=2026-04-25; last_reused=2026-04-25
# Purpose: Build bounded topology digests with explicit admission reconciliation.
# Reuse: Keep profile suggestion and admission decision separate. Do not merge.

from __future__ import annotations

import os
import re
from fnmatch import fnmatch, translate as _fnmatch_translate
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

try:
    from _yaml_bootstrap import import_yaml
except ImportError:
    from scripts._yaml_bootstrap import import_yaml

_yaml = import_yaml()

_DIGEST_ROOT = Path(__file__).resolve().parents[1]
_ADMISSION_SEVERITY_PATH = _DIGEST_ROOT / "architecture" / "admission_severity.yaml"


# ---------------------------------------------------------------------------
# Performance: cached glob -> regex translation
#
# fnmatch.fnmatch re-translates the glob pattern on every call. With ~50
# patterns x N requested files, this dominated _reconcile_admission for
# large file lists. The compiled pattern is small (a few hundred regexes
# at most across the whole manifest), so an unbounded LRU is safe.
# ---------------------------------------------------------------------------

@lru_cache(maxsize=512)
def _compile_glob(pattern: str) -> re.Pattern[str]:
    return re.compile(_fnmatch_translate(pattern))


def _glob_match(path: str, pattern: str) -> bool:
    if not pattern:
        return False
    return _compile_glob(pattern).match(path) is not None


# ---------------------------------------------------------------------------
# F1 fix: blocked_globs per typed_intent — read from admission_severity.yaml
#
# plan_only and audit declare blocks_path_globs in the typed_intent_enum section.
# _apply_typed_intent_shortcut checks these BEFORE admitting any file, so that
# src/** (and other blocked patterns) are never admitted for read-only intents.
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _load_typed_intent_blocked_globs() -> dict[str, tuple[str, ...]]:
    """Return mapping of intent_id -> blocked glob patterns.

    Reads architecture/admission_severity.yaml once (cached). Falls back to
    an empty dict when the file is absent or malformed (safe default: no blocks
    beyond the pre-existing forbidden_hits mechanism).
    """
    if not _ADMISSION_SEVERITY_PATH.exists():
        return {}
    try:
        data = _yaml.safe_load(_ADMISSION_SEVERITY_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    result: dict[str, tuple[str, ...]] = {}
    for entry in data.get("typed_intent_enum", []):
        intent_id = entry.get("id")
        globs = entry.get("blocks_path_globs") or []
        if intent_id and globs:
            result[intent_id] = tuple(str(g) for g in globs)
    return result


# ---------------------------------------------------------------------------
# Input hygiene: normalize caller-supplied file paths
#
# Callers (CLI, tests, CI hooks) frequently submit:
#   * empty strings from `xargs`-style splits
#   * leading "./" from `git diff --name-only` style outputs
#   * trailing whitespace from copy-paste
#   * duplicates from concatenated lists
# Normalizing once at the kernel boundary keeps admission predictable.
# ---------------------------------------------------------------------------

def _normalize_paths(paths: Iterable[Any] | None) -> list[str]:
    if not paths:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for raw in paths:
        if raw is None:
            continue
        s = str(raw).strip()
        if not s:
            continue
        # Strip leading "./" but preserve absolute and "../" semantics so the
        # caller still sees the same path back. We deliberately do NOT call
        # os.path.normpath: that would resolve ".." segments and could allow
        # path traversal to bypass forbidden globs.
        while s.startswith("./"):
            s = s[2:]
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out

# Tokens that previously caused single-substring false positives. Profiles
# whose flat `match` list contains only such generic terms should never
# select a profile on their own. This is enforced by `single_terms_can_select`
# defaulting to False. Profiles that *legitimately* select on a single
# domain-specific token (e.g. "platt", "kelly") must declare a typed
# `match_policy.weak_terms` entry; until then they fall under flat-list
# behavior.
_GENERIC_FALSE_POSITIVE_TOKENS = frozenset(
    {
        "source",
        "code",
        "test",
        "tests",
        "docs",
        "doc",
        "documentation",
        "history",
        "type",
        "types",
        "data",
        "scripts",
        "script",
        "review",
        "summary",
        "note",
        "notes",
        "daily",
        "kernel",
        "module",
        "modules",
        "fix",
        "cleanup",
        "freeze",
        "format",
        "lint",
        "signal",
        "signals",
    }
)


_DEFAULT_SHARED_COMPANION_FILE_PATTERNS = (
    "AGENTS.md",
    "workspace_map.md",
    "docs/AGENTS.md",
    "docs/README.md",
    "docs/operations/AGENTS.md",
    "docs/operations/current_state.md",
    "tests/AGENTS.md",
    "architecture/AGENTS.md",
    "architecture/topology.yaml",
    "architecture/digest_profiles.py",
    "architecture/docs_registry.yaml",
    "architecture/test_topology.yaml",
    "architecture/module_manifest.yaml",
    "architecture/source_rationale.yaml",
    "tests/test_digest_profile_matching.py",
    "tests/test_digest_profiles_equivalence.py",
    "tests/test_topology_doctor.py",
)


# ---------------------------------------------------------------------------
# Existing helpers (unchanged)
# ---------------------------------------------------------------------------


def data_rebuild_digest(api: Any) -> dict[str, Any]:
    topology = api.load_data_rebuild_topology()
    rows = topology.get("rebuilt_row_contract", {}).get("tables", {})
    return {
        "live_math_certification": topology.get("live_math_certification", {}),
        "row_contract_tables": {
            name: {
                "required_fields": spec.get("required_fields", []),
                "producer": spec.get("producer_contract") or spec.get("producer_script", ""),
            }
            for name, spec in rows.items()
        },
        "replay_coverage_rule": topology.get("replay_coverage_rule", {}),
        "diagnostic_non_promotion": topology.get("diagnostic_non_promotion", {}),
    }


def script_lifecycle_digest(api: Any) -> dict[str, Any]:
    manifest = api.load_script_manifest()
    naming = api.load_naming_conventions() if api.NAMING_CONVENTIONS_PATH.exists() else {}
    script_naming = (((naming.get("file_naming") or {}).get("scripts") or {}).get("long_lived") or {})
    scripts = manifest.get("scripts") or {}
    return {
        "allowed_lifecycles": manifest.get("allowed_lifecycles", []),
        "long_lived_naming": script_naming or manifest.get("long_lived_naming", {}),
        "naming_conventions": manifest.get("naming_conventions", "architecture/naming_conventions.yaml"),
        "required_effective_fields": manifest.get("required_effective_fields", []),
        "existing_scripts": {
            name: {
                "class": api._effective_script_entry(manifest, name).get("class"),
                "status": api._effective_script_entry(manifest, name).get("status"),
                "lifecycle": api._effective_script_entry(manifest, name).get("lifecycle"),
                "write_targets": api._effective_script_entry(manifest, name).get("write_targets", []),
                "dangerous_if_run": api._effective_script_entry(manifest, name).get("dangerous_if_run", False),
            }
            for name in sorted(scripts)
        },
    }


def compact_lore_card(card: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": card.get("id"),
        "status": card.get("status"),
        "severity": card.get("severity"),
        "failure_mode": card.get("failure_mode"),
        "wrong_moves": card.get("wrong_moves", []),
        "correct_rule": card.get("correct_rule"),
        "antibodies": card.get("antibodies", {}),
        "residual_risk": card.get("residual_risk"),
        "downstream_blast_radius": card.get("downstream_blast_radius", []),
        "zero_context_digest": card.get("zero_context_digest"),
    }


def matched_history_lore(api: Any, task: str, files: list[str]) -> list[dict[str, Any]]:
    lore = api.load_history_lore()
    task_l = task.lower()
    matched: list[dict[str, Any]] = []
    for card in lore.get("cards") or []:
        routing = card.get("routing") or {}
        terms = [str(term).lower() for term in routing.get("task_terms", [])]
        patterns = [str(pattern) for pattern in routing.get("file_patterns", [])]
        term_hit = any(term and term in task_l for term in terms)
        file_hit = any(fnmatch(file, pattern) for file in files for pattern in patterns)
        if term_hit or file_hit:
            matched.append(compact_lore_card(card))
    severity_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    return sorted(
        matched,
        key=lambda item: (
            severity_rank.get(str(item.get("severity")), 99),
            str(item.get("id")),
        ),
    )


# ---------------------------------------------------------------------------
# Evidence classifier
# ---------------------------------------------------------------------------


def _word_boundary_hit(token: str, task_lower: str) -> bool:
    """Match `token` as a word, not as a substring.

    Example: "source" matches "modify source" but not "open source code".
    """
    pattern = r"(?<![A-Za-z0-9_])" + re.escape(token.lower()) + r"(?![A-Za-z0-9_])"
    return re.search(pattern, task_lower) is not None


def _phrase_hit(phrase: str, task_lower: str) -> bool:
    """Match a multi-word phrase ignoring extra whitespace."""
    if not phrase:
        return False
    norm_phrase = re.sub(r"\s+", " ", phrase.lower()).strip()
    norm_task = re.sub(r"\s+", " ", task_lower).strip()
    return norm_phrase in norm_task


def _profile_match_policy(profile: dict[str, Any]) -> dict[str, Any]:
    """Return typed match policy or a derived one from the legacy `match` list.

    Legacy profiles using only a flat `match` list are interpreted as
    `weak_terms` with `single_terms_can_select=False`, which prevents the
    historical substring-overreach behavior.
    """
    typed = profile.get("match_policy")
    if typed:
        return {
            "strong_phrases": list(typed.get("strong_phrases", []) or []),
            "weak_terms": list(typed.get("weak_terms", []) or []),
            "negative_phrases": list(typed.get("negative_phrases", []) or []),
            "single_terms_can_select": bool(typed.get("single_terms_can_select", False)),
            "min_confidence": float(typed.get("min_confidence", 0.5)),
            "required_any": typed.get("required_any") or {},
        }
    legacy = [str(term).lower() for term in profile.get("match", []) or []]
    strong = [term for term in legacy if " " in term]
    weak = [term for term in legacy if " " not in term]
    return {
        "strong_phrases": strong,
        "weak_terms": weak,
        "negative_phrases": [],
        "single_terms_can_select": False,
        "min_confidence": 0.5,
        "required_any": {},
    }


def _unique(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _profile_selection_config(topology: dict[str, Any]) -> dict[str, Any]:
    return topology.get("digest_profile_selection") or {}


def _shared_companion_patterns(topology: dict[str, Any]) -> list[str]:
    configured = _profile_selection_config(topology).get("shared_companion_patterns") or []
    return _unique([str(pattern) for pattern in configured] or list(_DEFAULT_SHARED_COMPANION_FILE_PATTERNS))


def _pattern_hits(files: list[str], patterns: Iterable[Any]) -> list[str]:
    normalized_patterns = [str(pattern) for pattern in patterns or [] if str(pattern)]
    return _unique(
        f
        for f in files
        for pattern in normalized_patterns
        if fnmatch(f, pattern)
    )


def _matches_pattern_set(path: str, patterns: Iterable[Any]) -> bool:
    return any(fnmatch(path, str(pattern)) for pattern in patterns or [] if str(pattern))


def _file_evidence_for_profile(
    profile: dict[str, Any],
    files: list[str],
    shared_patterns: list[str],
) -> dict[str, list[str]]:
    legacy_patterns = list(profile.get("file_patterns", []) or [])
    semantic_patterns = list(profile.get("semantic_file_patterns", []) or [])
    companion_patterns = list(profile.get("companion_file_patterns", []) or [])

    legacy_hits = _pattern_hits(files, legacy_patterns)
    explicit_semantic_hits = _pattern_hits(files, semantic_patterns)
    explicit_companion_hits = _pattern_hits(files, companion_patterns)
    explicit_semantic = set(explicit_semantic_hits)
    explicit_companion = set(explicit_companion_hits)

    shared_hits = [
        path
        for path in legacy_hits
        if path not in explicit_semantic and _matches_pattern_set(path, shared_patterns)
    ]
    shared = set(shared_hits)
    semantic_hits = _unique(
        explicit_semantic_hits
        + [
            path
            for path in legacy_hits
            if path not in shared and path not in explicit_companion
        ]
    )
    companion_hits = _unique(explicit_companion_hits)
    return {
        "file_hits": _unique(legacy_hits + explicit_semantic_hits + explicit_companion_hits),
        "semantic_file_hits": semantic_hits,
        "companion_file_hits": companion_hits,
        "shared_file_hits": _unique(shared_hits),
    }


def _evidence_for_profile(
    profile: dict[str, Any],
    task_lower: str,
    files: list[str],
    shared_patterns: list[str],
) -> dict[str, Any]:
    policy = _profile_match_policy(profile)
    strong_hits = [p for p in policy["strong_phrases"] if _phrase_hit(p, task_lower)]
    weak_hits = [t for t in policy["weak_terms"] if _word_boundary_hit(t, task_lower)]
    negative_hits = [p for p in policy["negative_phrases"] if _phrase_hit(p, task_lower)]
    file_evidence = _file_evidence_for_profile(profile, files, shared_patterns)

    # Confidence score (bounded [0, 1]).
    score = 0.0
    if strong_hits:
        score = max(score, 0.85)
    if file_evidence["semantic_file_hits"]:
        score = max(score, 0.75)
    elif file_evidence["companion_file_hits"] or file_evidence["shared_file_hits"]:
        score = max(score, 0.25)
    if weak_hits and policy["single_terms_can_select"]:
        # Domain-specific weak terms (declared explicitly) carry weight.
        score = max(score, 0.6)
    if weak_hits and not policy["single_terms_can_select"]:
        # Generic weak hits are evidence-of-intent only, not selection-worthy.
        score = max(score, 0.3)
    if negative_hits:
        score = 0.0  # vetoed
    return {
        "profile_id": profile.get("id"),
        "strong_hits": strong_hits,
        "weak_hits": weak_hits,
        "negative_hits": negative_hits,
        **file_evidence,
        "policy": policy,
        "score": score,
        "evidence_class": _evidence_class(strong_hits, weak_hits, policy, file_evidence),
    }


def _evidence_class(
    strong_hits: list[str],
    weak_hits: list[str],
    policy: dict[str, Any],
    file_evidence: dict[str, list[str]],
) -> str:
    if strong_hits:
        return "semantic_phrase"
    if file_evidence["semantic_file_hits"]:
        return "semantic_file"
    if weak_hits and policy["single_terms_can_select"]:
        return "weak_term"
    if file_evidence["shared_file_hits"]:
        return "shared_file_only"
    if file_evidence["companion_file_hits"]:
        return "companion_file_only"
    if weak_hits:
        return "weak_term_nonselectable"
    return "none"


def _collect_evidence(
    topology: dict[str, Any], task: str, files: list[str]
) -> dict[str, Any]:
    task_lower = task.lower()
    shared_patterns = _shared_companion_patterns(topology)
    per_profile = [
        _evidence_for_profile(profile, task_lower, files, shared_patterns)
        for profile in topology.get("digest_profiles", []) or []
    ]
    return {
        "task": task,
        "task_lower": task_lower,
        "files": list(files),
        "shared_companion_patterns": shared_patterns,
        "per_profile": per_profile,
    }


def _weak_selectable(evidence: dict[str, Any]) -> bool:
    return bool(
        evidence.get("weak_hits")
        and (evidence.get("policy") or {}).get("single_terms_can_select")
    )


def _file_only_semantic_candidate(evidence: dict[str, Any]) -> bool:
    return bool(
        evidence.get("semantic_file_hits")
        and not evidence.get("strong_hits")
        and not _weak_selectable(evidence)
    )


def _semantic_file_fanout(candidates: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for candidate in candidates:
        for path in candidate.get("semantic_file_hits") or []:
            counts[path] = counts.get(path, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Profile resolver
# ---------------------------------------------------------------------------


def _resolve_profile(
    evidence: dict[str, Any], topology: dict[str, Any]
) -> dict[str, Any]:
    """Choose at most one profile, or signal `needs_profile` / `ambiguous`.

    Output shape:
        {
            "profile_id": str | None,
            "selected_by": "phrase" | "file" | "weak_term" | "fallback" |
                "shared_file_only" | "companion_file_only" |
                "weak_term_nonselectable" | "high_fanout_file_only" |
                "typed_intent",
            "confidence": float,
            "candidates": [...],
            "ambiguous": bool,
            "why": [str, ...],
        }
    """
    candidates = [
        e for e in evidence["per_profile"]
        if e["score"] > 0 and not e["negative_hits"]
    ]
    candidates.sort(key=lambda e: e["score"], reverse=True)

    if not candidates:
        return {
            "profile_id": None,
            "selected_by": "fallback",
            "confidence": 0.0,
            "candidates": [],
            "ambiguous": False,
            "why": ["no profile matched task or files"],
        }

    top = candidates[0]
    runner_up = candidates[1] if len(candidates) > 1 else None

    # Hub files such as src/engine/evaluator.py appear in many profile
    # allowed-file lists. If a profile would be selected only because such a
    # file was touched, the resolver must not let profile declaration order
    # choose the task semantics. Strong phrases or typed intent can still
    # select a profile; high-fanout file-only evidence requires narrowing.
    if _file_only_semantic_candidate(top):
        file_fanout = _semantic_file_fanout(candidates)
        ambiguous_hits = [
            path for path in top.get("semantic_file_hits") or []
            if file_fanout.get(path, 0) > 1
        ]
        ambiguous_candidates = [
            candidate["profile_id"]
            for candidate in candidates
            if set(candidate.get("semantic_file_hits") or []) & set(ambiguous_hits)
        ]
        if ambiguous_hits and len(ambiguous_candidates) > 1:
            return {
                "profile_id": None,
                "selected_by": "high_fanout_file_only",
                "confidence": top["score"],
                "candidates": [c["profile_id"] for c in candidates],
                "ambiguous": True,
                "strong_hits": list(top.get("strong_hits") or []),
                "weak_hits": list(top.get("weak_hits") or []),
                "negative_hits": list(top.get("negative_hits") or []),
                "file_hits": list(top.get("file_hits") or []),
                "semantic_file_hits": list(top.get("semantic_file_hits") or []),
                "companion_file_hits": list(top.get("companion_file_hits") or []),
                "shared_file_hits": list(top.get("shared_file_hits") or []),
                "evidence_class": "high_fanout_file_only",
                "why": [
                    "file-only profile selection is ambiguous for high-fanout "
                    f"file(s) {ambiguous_hits}: {ambiguous_candidates}; pass "
                    "typed intent or include a profile-specific semantic phrase"
                ],
            }

    # Ambiguity: top two within delta and both based on strong evidence.
    if (
        runner_up is not None
        and top["score"] - runner_up["score"] < 0.1
        and top["strong_hits"]
        and runner_up["strong_hits"]
    ):
        return {
            "profile_id": None,
            "selected_by": "fallback",
            "confidence": top["score"],
            "candidates": [c["profile_id"] for c in candidates],
            "ambiguous": True,
            "why": [
                f"profiles tied within delta: {top['profile_id']} vs "
                f"{runner_up['profile_id']}"
            ],
        }

    weak_selectable = bool(top["weak_hits"] and top["policy"]["single_terms_can_select"])
    if not top["strong_hits"] and not top["semantic_file_hits"] and not weak_selectable:
        selected_by = top.get("evidence_class") or "shared_file_only"
        return {
            "profile_id": None,
            "selected_by": selected_by,
            "confidence": 0.0,
            "candidates": [c["profile_id"] for c in candidates],
            "ambiguous": False,
            "strong_hits": list(top.get("strong_hits") or []),
            "weak_hits": list(top.get("weak_hits") or []),
            "negative_hits": list(top.get("negative_hits") or []),
            "file_hits": list(top.get("file_hits") or []),
            "semantic_file_hits": list(top.get("semantic_file_hits") or []),
            "companion_file_hits": list(top.get("companion_file_hits") or []),
            "shared_file_hits": list(top.get("shared_file_hits") or []),
            "evidence_class": selected_by,
            "why": [
                f"{selected_by}: {top['profile_id']} matched only non-semantic routing evidence; "
                "pass typed intent or include a semantic profile phrase/file"
            ],
        }

    selected_by = (
        "phrase" if top["strong_hits"]
        else "file" if top["semantic_file_hits"]
        else "weak_term"
    )

    # Single weak hit on a globally generic token never selects.
    if (
        selected_by == "weak_term"
        and not top["policy"]["single_terms_can_select"]
        and all(t in _GENERIC_FALSE_POSITIVE_TOKENS for t in top["weak_hits"])
    ):
        return {
            "profile_id": None,
            "selected_by": "fallback",
            "confidence": 0.0,
            "candidates": [c["profile_id"] for c in candidates],
            "ambiguous": False,
            "why": [
                f"weak term(s) {top['weak_hits']!r} are globally generic; "
                "cannot select a Zeus profile on their own"
            ],
        }

    if top["score"] < top["policy"]["min_confidence"]:
        return {
            "profile_id": None,
            "selected_by": "fallback",
            "confidence": top["score"],
            "candidates": [c["profile_id"] for c in candidates],
            "ambiguous": False,
            "why": [
                f"top candidate {top['profile_id']} confidence "
                f"{top['score']:.2f} below profile min {top['policy']['min_confidence']:.2f}"
            ],
        }

    return {
        "profile_id": top["profile_id"],
        "selected_by": selected_by,
        "confidence": top["score"],
        "candidates": [c["profile_id"] for c in candidates],
        "ambiguous": False,
        "strong_hits": list(top.get("strong_hits") or []),
        "weak_hits": list(top.get("weak_hits") or []),
        "negative_hits": list(top.get("negative_hits") or []),
        "file_hits": list(top.get("file_hits") or []),
        "semantic_file_hits": list(top.get("semantic_file_hits") or []),
        "companion_file_hits": list(top.get("companion_file_hits") or []),
        "shared_file_hits": list(top.get("shared_file_hits") or []),
        "evidence_class": top.get("evidence_class"),
        "why": [
            f"selected by {selected_by}: "
            f"strong_hits={top['strong_hits']} semantic_file_hits={top['semantic_file_hits']} "
            f"shared_file_hits={top['shared_file_hits']}"
        ],
    }


def _normalize_intent(value: str) -> str:
    return " ".join(value.strip().lower().replace("_", " ").split())


# K3: canonical typed_intent_enum values that short-circuit profile selection.
# Source: architecture/admission_severity.yaml typed_intent_enum (9-value enum).
# plan_only / audit admit all requested paths (pure read-only intents; blocked_globs apply).
# hygiene / hotfix / rebase_keepup / create_new / modify_existing / refactor / other use
# normal profile resolution for admission but are still recognised as canonical (non-invalid).
_CANONICAL_TYPED_INTENTS: frozenset[str] = frozenset({
    "plan_only",
    "create_new",
    "modify_existing",
    "refactor",
    "audit",
    "hygiene",
    "hotfix",
    "rebase_keepup",
    "other",
})

# Intents that admit ALL requested paths without a profile match.
# These are read-only or non-source-modifying intents per admission_severity.yaml.
# NOTE: hotfix and rebase_keepup are NOT in this set — they have scope and must
# go through normal profile match. Only pure read-only intents bypass admission.
_ADMIT_ALL_PATHS_INTENTS: frozenset[str] = frozenset({
    "plan_only",
    "audit",
})


def _resolve_typed_intent(intent: str | None, topology: dict[str, Any]) -> dict[str, Any] | None:
    """Resolve an explicit caller intent before free-text scoring.

    K3 amendment: when intent is a canonical typed_intent_enum value, short-circuit
    profile selection and return a non-ambiguous resolution. Intents in
    _ADMIT_ALL_PATHS_INTENTS bypass profile allowed_files entirely (admitted directly).
    For other canonical intents (create_new, modify_existing, refactor, other), the
    resolution is non-ambiguous but profile-id is None — _reconcile_admission will
    use generic fallback and _apply_typed_intent_shortcut promotes to admitted.

    Legacy: when intent matches a digest_profile.id exactly, that profile is selected
    (unchanged behaviour for profile-id intents).
    """
    if not intent or not intent.strip():
        return None

    # Normalise for comparison: lowercase, spaces collapsed, underscores as spaces.
    wanted = _normalize_intent(intent)
    wanted_canonical = wanted.replace(" ", "_")  # e.g. "plan only" → "plan_only"

    # K3: canonical typed-intent short-circuit — before profile lookup.
    if wanted_canonical in _CANONICAL_TYPED_INTENTS:
        return {
            "profile_id": None,
            "selected_by": "typed_intent_short_circuit",
            "confidence": 1.0,
            "candidates": [wanted_canonical],
            "ambiguous": False,
            "strong_hits": [wanted_canonical],
            "file_hits": [],
            "semantic_file_hits": [],
            "companion_file_hits": [],
            "shared_file_hits": [],
            "evidence_class": "typed_intent",
            "negative_hits": [],
            "why": [f"K3 typed_intent short-circuit: {wanted_canonical}"],
        }

    # Legacy: try to match a digest profile id.
    profiles = [
        profile
        for profile in topology.get("digest_profiles", []) or []
        if profile.get("id")
    ]
    for profile in profiles:
        profile_id = str(profile["id"])
        if _normalize_intent(profile_id) == wanted:
            return {
                "profile_id": profile_id,
                "selected_by": "typed_intent",
                "confidence": 1.0,
                "candidates": [profile_id],
                "ambiguous": False,
                "strong_hits": [profile_id],
                "file_hits": [],
                "semantic_file_hits": [],
                "companion_file_hits": [],
                "shared_file_hits": [],
                "evidence_class": "typed_intent",
                "negative_hits": [],
                "why": [f"selected by typed intent: {profile_id}"],
            }
    return {
        "profile_id": None,
        "selected_by": "typed_intent_invalid",
        "confidence": 0.0,
        "candidates": [str(profile["id"]) for profile in profiles],
        "ambiguous": True,
        "strong_hits": [],
        "file_hits": [],
        "semantic_file_hits": [],
        "companion_file_hits": [],
        "shared_file_hits": [],
        "evidence_class": "typed_intent_invalid",
        "negative_hits": [],
        "why": [f"typed intent did not match a digest profile or canonical enum: {intent!r}"],
    }


def _profile_by_id(topology: dict[str, Any], profile_id: str) -> dict[str, Any] | None:
    for profile in topology.get("digest_profiles", []) or []:
        if profile.get("id") == profile_id:
            return profile
    return None


def _requested_within_profile(profile: dict[str, Any], requested: list[str]) -> bool:
    if not requested:
        return True
    allowed = list(profile.get("allowed_files", []) or [])
    forbidden = list(profile.get("forbidden_files", []) or []) + list(_GENERIC_FORBIDDEN_FILES)
    return all(_matches_any(path, allowed) and not _matches_any(path, forbidden) for path in requested)


def _operation_vector_resolution(
    operation_payload: dict[str, Any],
    requested: list[str],
    topology: dict[str, Any],
    task: str = "",
) -> dict[str, Any] | None:
    """Select a profile from finite operation facts, not wording aliases.

    This is deliberately conservative: it only selects when the operation
    vector and requested files point at one canonical route. Free text can
    still suggest profiles, but it is not the authority for these cases.
    """
    vector = operation_payload.get("vector") or {}
    field_sources = operation_payload.get("field_sources") or {}
    mutation_surfaces = set(vector.get("mutation_surfaces") or [])
    operation_stage = str(vector.get("operation_stage") or "")
    artifact_target = str(vector.get("artifact_target") or "none")
    mutation_source = str(field_sources.get("mutation_surfaces") or "")
    task_l = task.lower()
    feedback_task_hint = any(
        phrase in task_l
        for phrase in (
            "direct operation feedback capsule",
            "operation feedback capsule",
            "feedback capsule",
            "context recycling",
            "context recovery",
            "topology usage note",
            "topology helped/blocked",
            "topology helped blocked",
            "topology experience",
            "回收 context",
            "回收context",
            "使用体验",
            "拓扑使用体验",
        )
    )
    feedback_artifact_target = artifact_target in {
        "final_response",
        "runtime_scratch",
        "new_evidence",
        "new_findings",
    }
    source_canary_task_hint = any(
        phrase in task_l
        for phrase in (
            "source canary",
            "canary",
            "provider hot-swap",
            "hot-swap",
            "source readiness",
            "source recovery",
            "source-change",
            "source change",
            "station/source policy",
        )
    )

    candidate_ids: list[str] = []
    if operation_stage == "plan" or artifact_target == "plan_packet":
        candidate_ids.append("operation planning packet")
    if operation_stage == "closeout" and (feedback_artifact_target or feedback_task_hint):
        candidate_ids.append("direct operation feedback capsule")
    if "source_behavior" in mutation_surfaces and (
        mutation_source == "cli" or source_canary_task_hint
    ):
        candidate_ids.append("source canary readiness hot-swap")
    if "evaluator_behavior" in mutation_surfaces and mutation_source == "cli":
        candidate_ids.append("evaluator script import bridge")
    if (
        mutation_surfaces == {"docs"}
        and mutation_source == "cli"
        and any(path.startswith("docs/operations/") for path in requested)
    ):
        candidate_ids.append("docs navigation cleanup")
    if (
        "runtime_tooling" in mutation_surfaces
        and mutation_source == "cli"
        and any(
            path.startswith(
                (
                    "scripts/topology_doctor",
                    "architecture/topology",
                    "docs/reference/modules/topology",
                )
            )
            for path in requested
        )
    ):
        candidate_ids.append("topology graph agent runtime upgrade")
    runtime_governance_paths = {
        ".gitignore",
        ".claude/CLAUDE.md",
        ".claude/settings.json",
        ".claude/hooks/pre-commit-invariant-test.sh",
        ".claude/hooks/pre-edit-architecture.sh",
        ".claude/hooks/pre-merge-contamination-check.sh",
        "architecture/kernel_manifest.yaml",
        "architecture/inv_prototype.py",
        "architecture/ast_rules/semgrep_zeus.yml",
        "architecture/ast_rules/forbidden_patterns.md",
        "scripts/check_kernel_manifests.py",
    }
    if any(path in runtime_governance_paths for path in requested):
        candidate_ids.append("topology graph agent runtime upgrade")

    matches: list[dict[str, Any]] = []
    for profile_id in dict.fromkeys(candidate_ids):
        profile = _profile_by_id(topology, profile_id)
        if profile and _requested_within_profile(profile, requested):
            matches.append(profile)

    if not matches:
        return None
    if len(matches) > 1:
        return {
            "profile_id": None,
            "selected_by": "operation_vector_conflict",
            "confidence": 0.0,
            "candidates": [str(profile.get("id")) for profile in matches],
            "ambiguous": True,
            "strong_hits": [],
            "file_hits": [],
            "semantic_file_hits": [],
            "companion_file_hits": [],
            "shared_file_hits": [],
            "evidence_class": "operation_vector_conflict",
            "negative_hits": [],
            "why": ["operation vector maps to multiple admitted profiles; split the operation"],
        }
    profile_id = str(matches[0]["id"])
    return {
        "profile_id": profile_id,
        "selected_by": "operation_vector",
        "confidence": 1.0,
        "candidates": [profile_id],
        "ambiguous": False,
        "strong_hits": [],
        "file_hits": list(requested),
        "semantic_file_hits": list(requested),
        "companion_file_hits": [],
        "shared_file_hits": [],
        "evidence_class": "operation_vector",
        "negative_hits": [],
        "why": [f"selected by operation vector: {profile_id}"],
    }


# ---------------------------------------------------------------------------
# Admission reconciler
# ---------------------------------------------------------------------------


_GENERIC_FORBIDDEN_FILES = (
    ".claude/worktrees/**",
    ".omx/**",
    "state/*.db",
)


def _matches_any(path: str, patterns: list[str]) -> bool:
    if not patterns:
        return False
    # Verbatim equality is the common case (admission lists exact files);
    # it is also far cheaper than regex evaluation, so test it first.
    if path in patterns:
        return True
    for pat in patterns:
        if pat and ("*" in pat or "?" in pat or "[" in pat):
            if _glob_match(path, pat):
                return True
        elif path == pat:
            return True
    return False


def _reconcile_admission(
    selected_profile: dict[str, Any] | None,
    requested_files: list[str] | Iterable[Any] | None,
    resolution: dict[str, Any],
    topology: dict[str, Any],
    *,
    write_intent: str | None = None,
) -> dict[str, Any]:
    """Decide which requested files are admitted for write under this profile.

    Status values:
      * admitted: every requested file is in profile.allowed_files and
        none hit forbidden patterns.
      * advisory_only: no requested files (caller asked for routing only)
        OR profile is the generic fallback and so cannot grant admission.
      * scope_expansion_required: at least one requested file is outside
        profile.allowed_files but inside no forbidden pattern.
      * blocked: at least one requested file matches a forbidden pattern.
      * ambiguous: profile resolver returned ambiguous.
      * route_contract_conflict: profile.allowed_files and
        profile.forbidden_files overlap on a requested file (manifest bug).
    """
    # Normalize once: drop empty strings/None, strip whitespace and "./"
    # prefixes, deduplicate while preserving order. All downstream comparisons
    # operate on these canonical strings.
    requested = _normalize_paths(requested_files)
    intent = (write_intent or "").strip().lower()
    normalized_intent = intent.replace("_", "-").replace(" ", "-")
    read_only = normalized_intent in {"read-only", "readonly", "none"}

    # 1. Ambiguity short-circuits. File-only high-fanout ambiguity is a
    # soft routing uncertainty: it must not admit edits, but it also should
    # not look like a topology failure. Strong phrase ties and invalid typed
    # intents remain hard ambiguous states.
    if resolution.get("ambiguous"):
        selected_by = resolution.get("selected_by", "fallback")
        forbidden_hits = [f for f in requested if _matches_any(f, list(_GENERIC_FORBIDDEN_FILES))]
        if selected_by == "high_fanout_file_only" and not forbidden_hits:
            return {
                "status": "advisory_only",
                "profile_id": "generic",
                "confidence": resolution.get("confidence", 0.0),
                "admitted_files": [],
                "profile_suggested_files": [],
                "out_of_scope_files": list(requested),
                "forbidden_hits": [],
                "companion_required": [],
                "decision_basis": {
                    "task_phrases": [],
                    "file_globs": _decision_globs(resolution),
                    "negative_hits": [],
                    "selected_by": selected_by,
                    "candidates": resolution.get("candidates", []),
                    "why": resolution.get("why", []) + [
                        "soft ambiguity: high-fanout file-only evidence cannot select a profile or admit edits"
                    ],
                },
            }
        return {
            "status": "ambiguous",
            "profile_id": None,
            "confidence": resolution.get("confidence", 0.0),
            "admitted_files": [],
            "profile_suggested_files": [],
            "out_of_scope_files": list(requested),
            "forbidden_hits": [],
            "companion_required": [],
            "decision_basis": {
                "task_phrases": [],
                "file_globs": [],
                "negative_hits": [],
                "selected_by": resolution.get("selected_by", "fallback"),
                "candidates": resolution.get("candidates", []),
                "why": resolution.get("why", []),
            },
        }

    # 2. Generic fallback: never admits caller files.
    if selected_profile is None:
        forbidden_hits = [f for f in requested if _matches_any(f, list(_GENERIC_FORBIDDEN_FILES))]
        if read_only:
            return {
                "status": "advisory_only",
                "profile_id": "generic",
                "confidence": resolution.get("confidence", 0.0),
                "admitted_files": [],
                "profile_suggested_files": [],
                "out_of_scope_files": list(requested),
                "forbidden_hits": [],
                "companion_required": [],
                "decision_basis": {
                    "task_phrases": [],
                    "file_globs": [],
                    "negative_hits": [],
                    "selected_by": resolution.get("selected_by", "fallback"),
                    "candidates": resolution.get("candidates", []),
                    "why": resolution.get("why", []) + [
                        "read-only intent: requested files are context references, not write admission"
                    ],
                },
            }
        return {
            "status": "blocked" if forbidden_hits else "advisory_only",
            "profile_id": "generic",
            "confidence": resolution.get("confidence", 0.0),
            "admitted_files": [],
            "profile_suggested_files": [],
            "out_of_scope_files": [f for f in requested if f not in forbidden_hits],
            "forbidden_hits": forbidden_hits,
            "companion_required": [],
            "decision_basis": {
                "task_phrases": [],
                "file_globs": [],
                "negative_hits": [],
                "selected_by": resolution.get("selected_by", "fallback"),
                "candidates": resolution.get("candidates", []),
                "why": resolution.get("why", []),
            },
        }

    allowed = list(selected_profile.get("allowed_files", []) or [])
    forbidden = list(selected_profile.get("forbidden_files", []) or [])
    # Generic forbidden patterns always apply on top of the profile's list.
    forbidden_combined = list(dict.fromkeys(forbidden + list(_GENERIC_FORBIDDEN_FILES)))

    # 3. forbidden-wins.
    forbidden_hits = [f for f in requested if _matches_any(f, forbidden_combined)]
    if read_only:
        return {
            "status": "advisory_only",
            "profile_id": selected_profile.get("id"),
            "confidence": resolution.get("confidence", 0.0),
            "admitted_files": [],
            "profile_suggested_files": allowed,
            "out_of_scope_files": list(requested),
            "forbidden_hits": [],
            "companion_required": [],
            "decision_basis": {
                "task_phrases": _decision_phrases(resolution),
                "file_globs": _decision_globs(resolution),
                "negative_hits": _decision_negatives(resolution),
                "selected_by": resolution.get("selected_by", "fallback"),
                "candidates": resolution.get("candidates", []),
                "why": resolution.get("why", []) + [
                    "read-only intent: forbidden/out-of-scope files may be cited but not edited"
                ],
            },
        }

    # 4. Detect route_contract_conflict: a requested file simultaneously
    # appears (verbatim or by glob) in both the profile's allowed list and
    # the combined forbidden list. This is a manifest authoring bug; surface
    # it instead of silently picking a side.
    conflict_files = [
        f for f in requested
        if (f in allowed or _matches_any(f, allowed))
        and _matches_any(f, forbidden_combined)
    ]

    if conflict_files:
        return {
            "status": "route_contract_conflict",
            "profile_id": selected_profile.get("id"),
            "confidence": resolution.get("confidence", 0.0),
            "admitted_files": [],
            "profile_suggested_files": allowed,
            "out_of_scope_files": [],
            "forbidden_hits": forbidden_hits,
            "companion_required": [],
            "decision_basis": {
                "task_phrases": _decision_phrases(resolution),
                "file_globs": _decision_globs(resolution),
                "negative_hits": _decision_negatives(resolution),
                "selected_by": resolution.get("selected_by", "fallback"),
                "candidates": resolution.get("candidates", []),
                "why": resolution.get("why", []) + [
                    f"manifest conflict: {conflict_files} appear in allowed AND forbidden"
                ],
            },
        }

    if forbidden_hits:
        return {
            "status": "blocked",
            "profile_id": selected_profile.get("id"),
            "confidence": resolution.get("confidence", 0.0),
            "admitted_files": [],
            "profile_suggested_files": allowed,
            "out_of_scope_files": [],
            "forbidden_hits": forbidden_hits,
            "companion_required": [],
            "decision_basis": {
                "task_phrases": _decision_phrases(resolution),
                "file_globs": _decision_globs(resolution),
                "negative_hits": _decision_negatives(resolution),
                "selected_by": resolution.get("selected_by", "fallback"),
                "candidates": resolution.get("candidates", []),
                "why": resolution.get("why", []) + [
                    f"forbidden-wins: {forbidden_hits} matched forbidden patterns"
                ],
            },
        }

    # 5. Caller asked for routing only — advisory_only.
    if not requested:
        return {
            "status": "advisory_only",
            "profile_id": selected_profile.get("id"),
            "confidence": resolution.get("confidence", 0.0),
            "admitted_files": [],
            "profile_suggested_files": allowed,
            "out_of_scope_files": [],
            "forbidden_hits": [],
            "companion_required": [],
            "decision_basis": {
                "task_phrases": _decision_phrases(resolution),
                "file_globs": _decision_globs(resolution),
                "negative_hits": _decision_negatives(resolution),
                "selected_by": resolution.get("selected_by", "fallback"),
                "candidates": resolution.get("candidates", []),
                "why": resolution.get("why", []),
            },
        }

    # 6. Scope check.
    admitted = [f for f in requested if f in allowed or _matches_any(f, allowed)]
    out_of_scope = [f for f in requested if f not in admitted]

    if out_of_scope:
        return {
            "status": "scope_expansion_required",
            "profile_id": selected_profile.get("id"),
            "confidence": resolution.get("confidence", 0.0),
            "admitted_files": admitted,
            "profile_suggested_files": allowed,
            "out_of_scope_files": out_of_scope,
            "forbidden_hits": [],
            "companion_required": [],
            "decision_basis": {
                "task_phrases": _decision_phrases(resolution),
                "file_globs": _decision_globs(resolution),
                "negative_hits": _decision_negatives(resolution),
                "selected_by": resolution.get("selected_by", "fallback"),
                "candidates": resolution.get("candidates", []),
                "why": resolution.get("why", []) + [
                    f"out_of_scope: {out_of_scope} not declared in profile.allowed_files"
                ],
            },
        }

    return {
        "status": "admitted",
        "profile_id": selected_profile.get("id"),
        "confidence": resolution.get("confidence", 0.0),
        "admitted_files": admitted,
        "profile_suggested_files": allowed,
        "out_of_scope_files": [],
        "forbidden_hits": [],
        "companion_required": [],
        "decision_basis": {
            "task_phrases": _decision_phrases(resolution),
            "file_globs": _decision_globs(resolution),
            "negative_hits": _decision_negatives(resolution),
            "selected_by": resolution.get("selected_by", "fallback"),
            "candidates": resolution.get("candidates", []),
            "why": resolution.get("why", []),
        },
    }


def _decision_phrases(resolution: dict[str, Any]) -> list[str]:
    return [
        phrase
        for cand_id in [resolution.get("profile_id")]
        if cand_id
        for phrase in _resolved_phrase_hits(resolution, cand_id)
    ]


def _decision_globs(resolution: dict[str, Any]) -> list[str]:
    return _resolved_file_hits(resolution)


def _decision_negatives(resolution: dict[str, Any]) -> list[str]:
    return _resolved_negative_hits(resolution)


def _resolved_phrase_hits(resolution: dict[str, Any], profile_id: str) -> list[str]:
    """Stub helper to keep decision_basis populated post-resolution.

    Resolver currently reduces to candidate ids; phrase hits live on the
    evidence record. Tests that need full traceback should consume
    `evidence` directly. We expose this to allow future enrichment without
    breaking the envelope shape.
    """
    return list(resolution.get("strong_hits", []) or [])


def _resolved_file_hits(resolution: dict[str, Any]) -> list[str]:
    return list(resolution.get("file_hits", []) or [])


def _resolved_negative_hits(resolution: dict[str, Any]) -> list[str]:
    return list(resolution.get("negative_hits", []) or [])


# ---------------------------------------------------------------------------
# K2 companion-loop-break (Navigation Topology v2 Phase 2B)
# ---------------------------------------------------------------------------

_COMPANION_LOOP_BREAK_PAIRS: list[tuple[str, str]] = [
    # (parent_glob, companion_path)
    ("scripts/**", "architecture/script_manifest.yaml"),
    ("tests/test_*.py", "architecture/test_topology.yaml"),
    ("docs/operations/task_*/**", "docs/operations/AGENTS.md"),
    ("src/**", "architecture/source_rationale.yaml"),
]

_COMPANION_LOOP_INTENTS = {"create_new", "refactor"}

_DEFAULT_COMPANION_BATCH_CAP = 50


def _apply_companion_loop_break(
    admission: dict[str, Any],
    requested: list[str],
    intent: str | None,
    *,
    batch_cap: int = _DEFAULT_COMPANION_BATCH_CAP,
) -> dict[str, Any]:
    """K2 fix — auto-admit manifest companion when typed_intent is create_new/refactor
    and the diff already includes both the new file AND its companion path.

    Loop-break for the new-file ↔ manifest-edit ↔ planning-lock cycle (F3/F4/F6).

    Rules:
    - Only fires when typed_intent ∈ {create_new, refactor}.
    - Only admits a file if its companion is ALSO in requested (never silently widens).
    - When parent is in requested but companion is missing, emits advisory companion_missing.
    - M4 batch-cap: len(requested) > batch_cap → advisory companion_loop_batch_advisory.
    - Returns admission unchanged when typed_intent does not match (backward compat).
    """
    normalized_intent = (intent or "").strip().lower().replace("-", "_").replace(" ", "_")
    if normalized_intent not in _COMPANION_LOOP_INTENTS:
        return admission

    # M4 batch-cap advisory (non-blocking).
    advisories: list[dict[str, Any]] = list(admission.get("companion_loop_advisories") or [])
    if len(requested) > batch_cap:
        advisories.append({
            "code": "companion_loop_batch_advisory",
            "severity": "info",
            "message": (
                f"requested_files count ({len(requested)}) exceeds companion_loop_batch_cap "
                f"({batch_cap}). Verify this is not an adversarial batch-add inflation. "
                "Auto-admit still applies per companion pairs."
            ),
        })

    out_of_scope: list[str] = list(admission.get("out_of_scope_files") or [])
    requested_set = set(requested)

    newly_admitted: list[str] = []
    companion_missing: list[str] = []
    companions_to_admit: set[str] = set()

    for path in out_of_scope:
        for parent_glob, companion_path in _COMPANION_LOOP_BREAK_PAIRS:
            if not fnmatch(path, parent_glob):
                continue
            # Parent matches a loop-break pair.
            if companion_path in requested_set:
                # Companion present → auto-admit the parent AND the companion.
                newly_admitted.append(path)
                companions_to_admit.add(companion_path)
            else:
                # Companion absent → emit advisory guidance (not blocking).
                companion_missing.append(companion_path)
            break  # first matching pair wins

    # Also admit companion paths that are themselves out-of-scope (common when
    # the profile has no allowed_files or the ambiguous path includes all requested).
    for path in out_of_scope:
        if path in companions_to_admit and path not in newly_admitted:
            newly_admitted.append(path)

    for companion_path in sorted(set(companion_missing)):
        advisories.append({
            "code": "companion_missing",
            "severity": "info",
            "message": (
                f"companion edit not found in --files: {companion_path}. "
                "Add it to enable companion-loop-break auto-admit."
            ),
            "expected_companion": companion_path,
        })

    if not newly_admitted:
        if advisories:
            admission = dict(admission)
            admission["companion_loop_advisories"] = advisories
        return admission

    # Promote newly_admitted files out of out_of_scope.
    remaining_out_of_scope = [f for f in out_of_scope if f not in newly_admitted]
    current_admitted = list(admission.get("admitted_files") or [])
    merged_admitted = current_admitted + newly_admitted

    new_admission = dict(admission)
    new_admission["admitted_files"] = merged_admitted
    new_admission["out_of_scope_files"] = remaining_out_of_scope
    new_admission["companion_loop_break"] = True
    new_admission["auto_admitted"] = newly_admitted
    new_admission["companion_pair"] = [
        path for path in newly_admitted
    ]
    new_admission["companion_loop_advisories"] = advisories

    # Upgrade status when all out-of-scope files were resolved.
    # Handles both scope_expansion_required and ambiguous (when typed_intent short-circuits
    # profile selection but profile lookup fails because typed-intent ids are not digest
    # profile ids — PLAN §2.3 K3 design).
    upgradeable_statuses = {"scope_expansion_required", "ambiguous", "advisory_only"}
    if not remaining_out_of_scope and admission.get("status") in upgradeable_statuses:
        new_admission["status"] = "admitted"
        why = list((admission.get("decision_basis") or {}).get("why") or [])
        why.append(
            f"companion_loop_break: typed_intent={normalized_intent}; "
            f"auto_admitted={newly_admitted}"
        )
        new_admission["decision_basis"] = dict(admission.get("decision_basis") or {})
        new_admission["decision_basis"]["why"] = why

    return new_admission


def _apply_typed_intent_shortcut(
    admission: dict[str, Any],
    requested: list[str],
    intent: str | None,
) -> dict[str, Any]:
    """K3 fix — upgrade admission to 'admitted' for canonical read-intent typed_intents.

    When the caller passes --intent plan_only or audit, the intent signals a read-only
    operation. Profile selection cannot select a matching profile because these ids are
    NOT digest profile ids. As a result, _reconcile_admission returns advisory_only or
    ambiguous.

    This function short-circuits: if the normalised intent is in _ADMIT_ALL_PATHS_INTENTS
    (plan_only, audit) and the current admission status is advisory_only / ambiguous /
    scope_expansion_required, ALL requested files are admitted directly — UNLESS a file
    matches a blocked_glob for that intent (checked first via _check_typed_intent_blocked_globs).

    hotfix, hygiene, and rebase_keepup are NOT in _ADMIT_ALL_PATHS_INTENTS and go through
    normal profile match.

    For create_new / modify_existing / refactor / other, this function is a no-op;
    _apply_companion_loop_break handles create_new admission upgrades via K2.

    Called after _apply_companion_loop_break in build_digest so K2 + K3 compose
    without conflict.
    """
    if not intent:
        return admission

    normalised = intent.strip().lower().replace("-", "_").replace(" ", "_")
    if normalised not in _ADMIT_ALL_PATHS_INTENTS:
        return admission

    upgradeable = {"advisory_only", "ambiguous", "scope_expansion_required"}
    if admission.get("status") not in upgradeable:
        return admission

    # F1 fix: check blocked_globs for this intent BEFORE admitting any file.
    # blocked_globs are defined per typed_intent in admission_severity.yaml.
    # A file matching a blocked glob emits advisory typed_intent_path_outside_scope
    # and falls through to normal admission (NOT admitted by K3 shortcut).
    intent_blocked_globs = _load_typed_intent_blocked_globs().get(normalised, ())
    blocked_by_intent: list[str] = []
    if intent_blocked_globs:
        blocked_by_intent = [
            f for f in requested
            if any(_glob_match(f, g) for g in intent_blocked_globs)
        ]

    # Admit all requested files that do not hit global forbidden patterns
    # AND do not match the intent's own blocked_globs.
    # Forbidden-wins invariant is preserved: blocked paths stay out.
    forbidden_hits = list(admission.get("forbidden_hits") or [])
    excluded_set = set(forbidden_hits) | set(blocked_by_intent)
    admitted_files = [f for f in requested if f not in excluded_set]

    new_admission = dict(admission)
    new_admission["status"] = "admitted"
    new_admission["admitted_files"] = admitted_files
    # Files blocked by intent's blocked_globs fall through to normal admission
    # (they are not in admitted_files and not in out_of_scope_files — they remain
    # subject to profile-based admission in a downstream pass).
    new_admission["out_of_scope_files"] = blocked_by_intent
    new_admission["typed_intent_short_circuit"] = True
    if blocked_by_intent:
        new_admission["typed_intent_blocked_files"] = blocked_by_intent
        new_admission["typed_intent_blocked_advisory"] = [
            {
                "code": "typed_intent_path_outside_scope",
                "path": f,
                "message": (
                    f"intent={normalised} blocks path via blocks_path_globs; "
                    f"file not admitted by K3 shortcut"
                ),
                "severity": "advisory",
            }
            for f in blocked_by_intent
        ]

    why = list((admission.get("decision_basis") or {}).get("why") or [])
    why.append(
        f"K3 typed_intent_short_circuit: intent={normalised}; "
        f"admitted={admitted_files}; blocked_by_intent={blocked_by_intent}"
    )
    new_admission["decision_basis"] = dict(admission.get("decision_basis") or {})
    new_admission["decision_basis"]["why"] = why
    new_admission["decision_basis"]["selected_by"] = "typed_intent_short_circuit"
    return new_admission


# ---------------------------------------------------------------------------
# build_digest (envelope assembly)
# ---------------------------------------------------------------------------


def build_digest(
    api: Any,
    task: str,
    files: list[str] | None = None,
    *,
    intent: str | None = None,
    task_class: str | None = None,
    write_intent: str | None = None,
    claims: list[str] | None = None,
    operation_stage: str | None = None,
    mutation_surfaces: list[str] | None = None,
    side_effect: str | None = None,
    artifact_target: str | None = None,
    merge_state: str | None = None,
    companion_loop_batch_cap: int | None = None,
) -> dict[str, Any]:
    topology = api.load_topology()
    # Normalize at the kernel boundary: drop None/empty/whitespace, strip
    # leading "./" prefixes, dedupe. Every downstream stage (evidence
    # collection, resolution, admission) consumes the canonical list, so
    # behavior cannot diverge between layers.
    requested = _normalize_paths(files)

    evidence = _collect_evidence(topology, task, requested)
    preliminary_operation_vector = (
        api.build_operation_vector(
            task=task,
            files=requested,
            profile="generic",
            write_intent=write_intent,
            claims=claims,
            operation_stage=operation_stage,
            mutation_surfaces=mutation_surfaces,
            side_effect=side_effect,
            artifact_target=artifact_target,
            merge_state=merge_state,
        )
        if hasattr(api, "build_operation_vector")
        else {}
    )
    typed_resolution = _resolve_typed_intent(intent, topology)
    operation_resolution = (
        None
        if typed_resolution
        else _operation_vector_resolution(preliminary_operation_vector, requested, topology, task=task)
    )
    resolution = typed_resolution or operation_resolution or _resolve_profile(evidence, topology)

    selected = None
    if resolution["profile_id"]:
        for profile in topology.get("digest_profiles", []) or []:
            if profile.get("id") == resolution["profile_id"]:
                selected = profile
                break

    admission = _reconcile_admission(selected, requested, resolution, topology, write_intent=write_intent)
    # K2 companion-loop-break: auto-admit manifest companion when typed_intent=create_new/refactor
    # and the companion path is also in requested. Non-blocking; falls through unchanged otherwise.
    _batch_cap = (
        companion_loop_batch_cap
        if companion_loop_batch_cap is not None
        else int(os.environ.get("ZEUS_COMPANION_LOOP_BATCH_CAP", str(_DEFAULT_COMPANION_BATCH_CAP)))
    )
    admission = _apply_companion_loop_break(admission, requested, intent, batch_cap=_batch_cap)
    # K3 typed_intent short-circuit: admit all paths for read-only canonical intents
    # (plan_only, audit, hygiene, rebase_keepup, hotfix). Runs after K2 so the two
    # loop-break mechanisms compose without conflict.
    admission = _apply_typed_intent_shortcut(admission, requested, intent)
    evidence_class = resolution.get("evidence_class") or resolution.get("selected_by") or "none"
    profile_selection = {
        "selected_by": resolution.get("selected_by"),
        "confidence": resolution.get("confidence", 0.0),
        "candidates": list(resolution.get("candidates") or []),
        "evidence_class": evidence_class,
        "semantic_file_hits": list(resolution.get("semantic_file_hits") or []),
        "companion_file_hits": list(resolution.get("companion_file_hits") or []),
        "shared_file_hits": list(resolution.get("shared_file_hits") or []),
        "needs_typed_intent": resolution.get("selected_by") in {
            "shared_file_only",
            "companion_file_only",
            "weak_term_nonselectable",
            "high_fanout_file_only",
            "typed_intent_invalid",
        } or bool(resolution.get("ambiguous")),
    }

    if selected is None:
        selected = {
            "id": "generic",
            "required_law": ["Read root AGENTS.md and scoped AGENTS.md before editing."],
            "allowed_files": [],
            "forbidden_files": list(_GENERIC_FORBIDDEN_FILES),
            "gates": ["Run focused tests for touched files."],
            "downstream": [],
            "stop_conditions": [
                "Stop and plan if authority, lifecycle, control, or DB truth is touched.",
                "Generic profile cannot authorize file edits; rephrase the task or expand topology.yaml.",
            ],
        }

    profile_suggested = list(selected.get("allowed_files", []) or [])
    source_files = (
        requested
        or [path for path in profile_suggested if isinstance(path, str) and path.startswith("src/")]
    )
    operation_vector = (
        api.build_operation_vector(
            task=task,
            files=requested,
            profile=str(selected.get("id", "generic")),
            write_intent=write_intent,
            claims=claims,
            operation_stage=operation_stage,
            mutation_surfaces=mutation_surfaces,
            side_effect=side_effect,
            artifact_target=artifact_target,
            merge_state=merge_state,
        )
        if hasattr(api, "build_operation_vector")
        else {}
    )

    payload: dict[str, Any] = {
        "task": task,
        "profile": selected.get("id", "generic"),
        "files": requested,
        # --- new admission contract ---
        "command_ok": True,
        "schema_version": "2",
        "admission": admission,
        "profile_selection": profile_selection,
        "ok_semantics": "command_success_only_not_write_authorization",
        "typed_runtime_inputs": {
            "intent": intent,
            "task_class": task_class,
            "write_intent": write_intent,
            "claims": api.normalize_runtime_claims(claims),
            "intent_selected": bool(typed_resolution and typed_resolution.get("profile_id")),
            "operation_stage": operation_stage,
            "mutation_surfaces": list(mutation_surfaces or []),
            "side_effect": side_effect,
            "artifact_target": artifact_target,
            "merge_state": merge_state,
        },
        "operation_vector": operation_vector,
        # --- legacy advisory mirrors (do not load-bear write authorization) ---
        "required_law": list(selected.get("required_law", []) or []),
        "allowed_files": profile_suggested,
        "legacy_advisory": True,
        "forbidden_files": list(selected.get("forbidden_files", []) or []),
        "gates": list(selected.get("gates", []) or []),
        "downstream": list(selected.get("downstream", []) or []),
        "stop_conditions": list(selected.get("stop_conditions", []) or []),
    }
    if selected.get("reference_reads"):
        payload["reference_reads"] = list(selected["reference_reads"])

    # Annotate gates with test trust status.
    test_topology = api.load_test_topology()
    trust_policy = test_topology.get("test_trust_policy", {})
    trusted_tests = set((trust_policy.get("trusted_tests") or {}).keys())
    gate_trust = []
    for gate in selected.get("gates", []) or []:
        if gate.startswith("pytest"):
            parts = gate.split()
            test_files = [p for p in parts if p.startswith("tests/")]
            untrusted = [t for t in test_files if t not in trusted_tests]
            if untrusted:
                gate_trust.append({
                    "gate": gate,
                    "status": "audit_required",
                    "untrusted_tests": untrusted,
                })
            else:
                gate_trust.append({"gate": gate, "status": "trusted"})
    if gate_trust:
        payload["gate_trust"] = gate_trust

    source_entries = api._source_rationale_for(source_files)
    payload["source_rationale"] = source_entries
    payload["context_assumption"] = api.build_context_assumption(
        profile=str(selected.get("id", "generic")),
        source_entries=source_entries,
        confidence_basis=["topology_manifest"],
    )
    if selected.get("id") == "add a data backfill":
        payload["data_rebuild_topology"] = data_rebuild_digest(api)
    if selected.get("id") == "add or change script":
        payload["script_lifecycle"] = script_lifecycle_digest(api)
    payload["history_lore"] = matched_history_lore(api, task, requested)
    if hasattr(api, "build_runtime_route_card"):
        payload["route_card"] = api.build_runtime_route_card(
            task=task,
            files=requested,
            digest=payload,
            mode="digest",
            intent=intent,
            task_class=task_class,
            write_intent=write_intent,
            claims=claims,
        )
    return payload

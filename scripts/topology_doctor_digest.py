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

import re
from fnmatch import fnmatch, translate as _fnmatch_translate
from functools import lru_cache
from typing import Any, Iterable


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


def _evidence_for_profile(
    profile: dict[str, Any], task_lower: str, files: list[str]
) -> dict[str, Any]:
    policy = _profile_match_policy(profile)
    strong_hits = [p for p in policy["strong_phrases"] if _phrase_hit(p, task_lower)]
    weak_hits = [t for t in policy["weak_terms"] if _word_boundary_hit(t, task_lower)]
    negative_hits = [p for p in policy["negative_phrases"] if _phrase_hit(p, task_lower)]
    file_globs = profile.get("file_patterns", []) or []
    file_hits = [
        f for f in files for pat in file_globs if fnmatch(f, pat)
    ]
    # Distinct files only, preserving order.
    file_hits = list(dict.fromkeys(file_hits))

    # Confidence score (bounded [0, 1]).
    score = 0.0
    if strong_hits:
        score = max(score, 0.85)
    if file_hits:
        score = max(score, 0.75)
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
        "file_hits": file_hits,
        "policy": policy,
        "score": score,
    }


def _collect_evidence(
    topology: dict[str, Any], task: str, files: list[str]
) -> dict[str, Any]:
    task_lower = task.lower()
    per_profile = [
        _evidence_for_profile(profile, task_lower, files)
        for profile in topology.get("digest_profiles", []) or []
    ]
    return {
        "task": task,
        "task_lower": task_lower,
        "files": list(files),
        "per_profile": per_profile,
    }


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
            "selected_by": "phrase" | "file" | "weak_term" | "fallback",
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

    selected_by = (
        "phrase" if top["strong_hits"]
        else "file" if top["file_hits"]
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
        "why": [
            f"selected by {selected_by}: "
            f"strong_hits={top['strong_hits']} file_hits={top['file_hits']}"
        ],
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

    # 1. Ambiguity short-circuits.
    if resolution.get("ambiguous"):
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
                "selected_by": "fallback",
                "candidates": resolution.get("candidates", []),
                "why": resolution.get("why", []),
            },
        }

    # 2. Generic fallback: never admits caller files.
    if selected_profile is None:
        forbidden_hits = [f for f in requested if _matches_any(f, list(_GENERIC_FORBIDDEN_FILES))]
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
                "selected_by": "fallback",
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
# build_digest (envelope assembly)
# ---------------------------------------------------------------------------


def build_digest(api: Any, task: str, files: list[str] | None = None) -> dict[str, Any]:
    topology = api.load_topology()
    # Normalize at the kernel boundary: drop None/empty/whitespace, strip
    # leading "./" prefixes, dedupe. Every downstream stage (evidence
    # collection, resolution, admission) consumes the canonical list, so
    # behavior cannot diverge between layers.
    requested = _normalize_paths(files)

    evidence = _collect_evidence(topology, task, requested)
    resolution = _resolve_profile(evidence, topology)

    selected = None
    if resolution["profile_id"]:
        for profile in topology.get("digest_profiles", []) or []:
            if profile.get("id") == resolution["profile_id"]:
                selected = profile
                break

    admission = _reconcile_admission(selected, requested, resolution, topology)

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

    payload: dict[str, Any] = {
        "task": task,
        "profile": selected.get("id", "generic"),
        "files": requested,
        # --- new admission contract ---
        "command_ok": True,
        "schema_version": "2",
        "admission": admission,
        "ok_semantics": "command_success_only_not_write_authorization",
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
    return payload

# Created: 2026-05-26
# Last reused or audited: 2026-05-26
# Authority basis: docs/operations/current/plans/ci_topology_refactor_refined.md Phase B
#                  architecture/context_pack_schema.yaml
#                  architecture/topology_surfaces.yaml
#                  architecture/failure_chains.yaml
"""
Context Pack assembler — Phase B of the CI Topology refactor.

Reads changed-file inputs and produces Context Pack JSON conforming to
architecture/context_pack_schema.yaml. The output augments
scripts/topology_doctor.py's existing route_card with deterministic
path-based surface matching, failure chain attribution, fatal misread
injection, and per-surface required relationship tests.

Design rules:
- Deterministic path → surface → failure chain mapping. No phrase-based
  routing. Multi-file inputs UNION across surfaces (operator caveat
  2026-05-26: existing doctor degrades on multi-file PRs).
- No new admission engine. No new profile matcher. Existing topology_doctor
  routing remains authoritative; Context Pack assembly is a deterministic
  surface/failure-chain projection.
- Standalone module: importable as `from scripts.topology_doctor_context_pack
  import assemble_context_packs`. No coupling to doctor internals.
- Pure read-only over architecture manifests. No DB access. No network.

CLI usage:
    python scripts/topology_doctor_context_pack.py \\
        --changed-files <f1> <f2> ... \\
        [--task "<label>"] \\
        [--mode emit_per_surface|emit_merged] \\
        [--format json|markdown] \\
        [--render-mode compact|expanded]
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import sys
from pathlib import Path
from typing import Any, Literal

try:
    import yaml
except ImportError:  # pragma: no cover - PyYAML is in venv
    print(
        "ERROR: PyYAML is required. Install with: pip install pyyaml",
        file=sys.stderr,
    )
    sys.exit(2)


REPO_ROOT = Path(__file__).resolve().parents[1]

# Manifest paths
PATH_SCHEMA = REPO_ROOT / "architecture" / "context_pack_schema.yaml"
PATH_SURFACES = REPO_ROOT / "architecture" / "topology_surfaces.yaml"
PATH_FAILURE_CHAINS = REPO_ROOT / "architecture" / "failure_chains.yaml"
PATH_FATAL_MISREADS = REPO_ROOT / "architecture" / "fatal_misreads.yaml"
PATH_MONEY_PATH_CI = REPO_ROOT / "architecture" / "money_path_ci.yaml"
PATH_MONEY_PATH_OBJECTS = REPO_ROOT / "architecture" / "money_path_objects.yaml"
PATH_TOPOLOGY_ENFORCEMENT = REPO_ROOT / "architecture" / "topology_enforcement.yaml"


# ---------------------------------------------------------------------------
# Manifest loading
# ---------------------------------------------------------------------------


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open() as f:
        return yaml.safe_load(f) or {}


class TopologyManifests:
    """Cached manifest loader. Pass repo_root for non-default test fixtures."""

    def __init__(self, repo_root: Path | None = None) -> None:
        self.repo_root = repo_root or REPO_ROOT
        self.surfaces = _load_yaml(self.repo_root / "architecture" / "topology_surfaces.yaml")
        self.failure_chains = _load_yaml(self.repo_root / "architecture" / "failure_chains.yaml")
        self.fatal_misreads = _load_yaml(self.repo_root / "architecture" / "fatal_misreads.yaml")
        self.enforcement = _load_yaml(
            self.repo_root / "architecture" / "topology_enforcement.yaml"
        )
        self.money_path_ci = _load_yaml(
            self.repo_root / "architecture" / "money_path_ci.yaml"
        )


# ---------------------------------------------------------------------------
# Surface matching — deterministic path → surface_id
# ---------------------------------------------------------------------------


def _path_matches_glob(path: str, glob: str) -> bool:
    """
    fnmatch with ** support.

    Pure-python translation: `a/**/b` matches any number of intermediate
    directories. We split on `/` and walk segments.
    """
    if "**" not in glob:
        return fnmatch.fnmatch(path, glob)

    # Replace ** with explicit greedy match by splitting.
    parts = glob.split("**")
    if len(parts) > 2:
        # Multiple ** — fall back to a regex translation
        import re
        pattern = re.escape(glob).replace(r"\*\*", r".*").replace(r"\*", r"[^/]*").replace(r"\?", r".")
        return bool(re.fullmatch(pattern, path))
    left, right = parts
    # Trim trailing slash from left, leading slash from right
    left = left.rstrip("/")
    right = right.lstrip("/")
    if left and not path.startswith(left + "/") and path != left:
        return False
    if right and not (path.endswith("/" + right) or path == right or fnmatch.fnmatch(path.rsplit("/", 1)[-1], right)):
        # Try fnmatch on the trailing portion
        return fnmatch.fnmatch(path, glob.replace("**", "*"))
    if not right:
        return True
    return fnmatch.fnmatch(path, glob.replace("**", "*"))


def match_surfaces_for_file(
    file_path: str, manifests: TopologyManifests
) -> list[dict[str, Any]]:
    """
    Returns the list of surface match dicts (one per matched surface) for a
    single file path. Match dict shape:
        {"surface_id": str, "match_type": str, "matched_value": str,
         "confidence": "high"|"medium"|"weak"}
    """
    hits: list[dict[str, Any]] = []
    surfaces = manifests.surfaces.get("surfaces", {})
    for sid, surface in surfaces.items():
        patterns = surface.get("patterns", {}) or {}
        for glob in patterns.get("path_globs", []) or []:
            if _path_matches_glob(file_path, glob):
                hits.append(
                    {
                        "surface_id": sid,
                        "match_type": "path",
                        "matched_value": glob,
                        "confidence": "high",
                    }
                )
                break  # one match per surface per file is enough
    return hits


def match_surfaces(
    changed_files: list[str], manifests: TopologyManifests
) -> dict[str, list[dict[str, Any]]]:
    """
    Returns a dict mapping surface_id → list of match dicts (one per file
    that matched that surface).
    """
    by_surface: dict[str, list[dict[str, Any]]] = {}
    for fp in changed_files:
        for hit in match_surfaces_for_file(fp, manifests):
            sid = hit["surface_id"]
            entry = {**hit, "matched_value": fp}  # preserve the file path that hit
            by_surface.setdefault(sid, []).append(entry)
    return by_surface


# ---------------------------------------------------------------------------
# Failure chain lookup
# ---------------------------------------------------------------------------


def failure_chains_for_surface(
    surface_id: str, manifests: TopologyManifests
) -> list[dict[str, Any]]:
    """
    Returns the list of FC entries that touch the given surface.
    Each entry: {"id": "FC-NN", "relevance": "root", "reason": ...}
    """
    chains = manifests.failure_chains.get("chains", {})
    out: list[dict[str, Any]] = []
    for cid, chain in chains.items():
        if surface_id in (chain.get("touched_surfaces") or []):
            out.append(
                {
                    "id": cid,
                    "relevance": "root",  # surface match → root precedent
                    "reason": chain.get("root_hazard", "").strip().split("\n")[0],
                }
            )
    return out


# ---------------------------------------------------------------------------
# Fatal misread injection
# ---------------------------------------------------------------------------


def fatal_misreads_for_chains(
    chain_ids: list[str], manifests: TopologyManifests
) -> list[dict[str, Any]]:
    """
    Returns fatal_misread entries injected into the Context Pack for the
    given failure chains.

    Mapping direction: failure_chains.yaml#chains[<FC-id>].fatal_misread_refs
    lists ids that resolve in architecture/fatal_misreads.yaml#misreads[].id.
    fatal_misreads.yaml itself uses domain task_classes (source_routing,
    settlement_semantics, ...) rather than FC refs, so the join goes
    through our new failure_chains.yaml field (added 2026-05-26 per
    Copilot finding on PR #343).

    Misread fields used (per actual fatal_misreads.yaml shape):
      id, false_equivalence, correction, severity
    """
    chains = (manifests.failure_chains or {}).get("chains", {}) or {}
    misreads_doc = manifests.fatal_misreads or {}
    misread_list = misreads_doc.get("misreads") or []
    # Index misreads by id for O(1) lookup
    misread_by_id: dict[str, dict[str, Any]] = {}
    if isinstance(misread_list, list):
        for entry in misread_list:
            if isinstance(entry, dict) and entry.get("id"):
                misread_by_id[entry["id"]] = entry
    elif isinstance(misread_list, dict):
        for k, v in misread_list.items():
            if isinstance(v, dict):
                misread_by_id[k] = {"id": k, **v}

    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for cid in chain_ids:
        chain = chains.get(cid, {})
        refs = chain.get("fatal_misread_refs") or []
        if isinstance(refs, str):
            refs = [refs]
        for ref in refs:
            if ref in seen:
                continue
            misread = misread_by_id.get(ref)
            if not misread:
                # Unresolvable ref: still surface so reviewers notice the drift.
                out.append(
                    {
                        "id": ref,
                        "inject_into_prompt": False,
                        "reason": "REVIEW_REQUIRED: misread id not found in architecture/fatal_misreads.yaml",
                    }
                )
                seen.add(ref)
                continue
            false_eq = (misread.get("false_equivalence") or "").strip()
            correction = (misread.get("correction") or "").strip().split("\n")[0]
            severity = misread.get("severity") or "unknown"
            reason = f"[{severity}] {false_eq} — {correction}" if false_eq else correction
            out.append(
                {
                    "id": ref,
                    "inject_into_prompt": True,
                    "reason": reason.strip(),
                }
            )
            seen.add(ref)
    return out


# ---------------------------------------------------------------------------
# Required reads / required tests / static gates assembly
# ---------------------------------------------------------------------------


def required_reads_for_surface(
    surface: dict[str, Any], manifests: TopologyManifests
) -> dict[str, Any]:
    repo_files = []
    arch_manifests = []
    scoped = []
    # repo_files: matched path_globs are the must-read file set
    for glob in (surface.get("patterns", {}) or {}).get("path_globs", []) or []:
        repo_files.append(
            {"path": glob, "reason": "changed surface path", "mode": "must_read"}
        )
    # architecture_manifests: which manifests this surface requires
    mr = surface.get("manifests_required", {}) or {}
    if mr.get("money_path_ci"):
        arch_manifests.append(
            {
                "path": "architecture/money_path_ci.yaml",
                "keys": [],
                "reason": "money-path invariants and selected tests",
            }
        )
    if mr.get("money_path_objects"):
        arch_manifests.append(
            {
                "path": "architecture/money_path_objects.yaml",
                "keys": [],
                "reason": "economic objects/state machines",
            }
        )
    if mr.get("db_table_ownership"):
        arch_manifests.append(
            {
                "path": "architecture/db_table_ownership.yaml",
                "keys": [],
                "reason": "DB ownership authority",
            }
        )
    if mr.get("source_rationale"):
        arch_manifests.append(
            {
                "path": "architecture/source_rationale.yaml",
                "keys": [],
                "reason": "source/provider rationale",
            }
        )
    if mr.get("test_topology"):
        arch_manifests.append(
            {
                "path": "architecture/test_topology.yaml",
                "keys": [],
                "reason": "test trust registry",
            }
        )
    if mr.get("workflow_refs"):
        arch_manifests.append(
            {
                "path": ".github/workflows/",
                "keys": [],
                "reason": "workflow run path integrity",
            }
        )
    # scoped_agents: surface-declared AGENTS files
    for agents in surface.get("scoped_agents", []) or []:
        scoped.append({"path": agents, "reason": "surface-scoped agent law"})
    return {
        "repo_files": repo_files,
        "architecture_manifests": arch_manifests,
        "scoped_agents": scoped,
    }


def required_relationship_tests_for_surface(
    surface: dict[str, Any], failure_chain_ids: list[str]
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for entry in surface.get("relationship_tests", []) or []:
        if not entry.get("required_when_changed", False):
            continue
        out.append(
            {
                "path": entry["path"],
                "selector_id": None,
                "historical_chain": failure_chain_ids[0] if failure_chain_ids else None,
                "runtime_path_hit": "direct",  # default; tighter routing in Phase E
                "blocking": True,
            }
        )
    return out


def required_static_gates_for_surface(
    surface: dict[str, Any], manifests: TopologyManifests
) -> list[dict[str, Any]]:
    enforcement = manifests.enforcement or {}
    rules_by_id = {
        rule["id"]: rule for rule in enforcement.get("blocking_structural", []) or []
    }
    out: list[dict[str, Any]] = []
    for gate in surface.get("static_gates", []) or []:
        gid = gate.get("id")
        rule = rules_by_id.get(gid, {})
        out.append(
            {
                "id": gid,
                "script": gate.get("script") or rule.get("enforcer", "REVIEW_REQUIRED"),
                "blocking": gate.get("blocking", True),
                "reason": rule.get("description", "structural gate"),
            }
        )
    return out


# ---------------------------------------------------------------------------
# Money-path invariant lookup
# ---------------------------------------------------------------------------


def active_invariants_for_surface(
    surface: dict[str, Any], manifests: TopologyManifests
) -> list[dict[str, Any]]:
    """
    Returns active invariants whose `tests:` overlap with the surface's
    relationship_tests. This wires money_path_ci.yaml → context pack
    without duplicating the manifest content.
    """
    surface_tests = {e["path"] for e in (surface.get("relationship_tests") or [])}
    if not surface_tests:
        return []
    out: list[dict[str, Any]] = []
    invariants = manifests.money_path_ci.get("invariants", {}) or {}
    for inv_id, inv in invariants.items():
        inv_tests = set(inv.get("tests") or [])
        if surface_tests & inv_tests:
            out.append(
                {
                    "id": inv_id,
                    "source": "architecture/money_path_ci.yaml",
                    "reason": inv.get("description", ""),
                }
            )
    return out


# ---------------------------------------------------------------------------
# Context Pack assembly
# ---------------------------------------------------------------------------


def _agent_runtime_warnings_for_chains(
    chain_ids: list[str], manifests: TopologyManifests
) -> list[str]:
    chains = manifests.failure_chains.get("chains", {}) or {}
    warnings: list[str] = []
    seen: set[str] = set()
    for cid in chain_ids:
        for w in chains.get(cid, {}).get("agent_runtime_warnings", []) or []:
            if w not in seen:
                seen.add(w)
                warnings.append(w)
    return warnings


def _not_topology_responsibility_for_chains(
    chain_ids: list[str], manifests: TopologyManifests
) -> list[str]:
    chains = manifests.failure_chains.get("chains", {}) or {}
    items: list[str] = []
    seen: set[str] = set()
    for cid in chain_ids:
        for n in chains.get(cid, {}).get("not_topology_responsibility", []) or []:
            if n not in seen:
                seen.add(n)
                items.append(n)
    return items


def build_context_pack(
    surface_id: str,
    surface_matches: list[dict[str, Any]],
    manifests: TopologyManifests,
    *,
    task_label: str | None = None,
) -> dict[str, Any]:
    surface = manifests.surfaces["surfaces"][surface_id]
    pack_ids = surface.get("context_packs", []) or [surface_id]
    pack_id = pack_ids[0]
    failure_chains = failure_chains_for_surface(surface_id, manifests)
    fc_ids = [fc["id"] for fc in failure_chains]
    misreads = fatal_misreads_for_chains(fc_ids, manifests)
    invariants = active_invariants_for_surface(surface, manifests)
    rel_tests = required_relationship_tests_for_surface(surface, fc_ids)
    static_gates = required_static_gates_for_surface(surface, manifests)
    reads = required_reads_for_surface(surface, manifests)

    ci_classification = {
        "blocking_static": [g["id"] for g in static_gates if g["blocking"]],
        "blocking_relationship": [t["path"] for t in rel_tests if t["blocking"]],
        "advisory": list(surface.get("advisory_signals") or []),
        "nightly": [],
        "manual": [],
    }

    pack: dict[str, Any] = {
        "id": pack_id,
        "title": f"Context Pack: {surface.get('description', surface_id)}",
        "status": "active",
        "owner": (surface.get("owners") or ["ci-infra"])[0],
        "sunset_condition": None,
        "task_change_label": task_label or surface.get("description", surface_id),
        "matched_surfaces": surface_matches,
        "money_path_segments": list(surface.get("money_path_segments") or []),
        "risk_tier": surface.get("risk_tier", "T4"),
        "scoped_agents": [
            {"path": p, "reason": "surface-scoped agent law"}
            for p in surface.get("scoped_agents") or []
        ],
        "required_reads": reads,
        "ownership_checks": {
            k: {"required": bool(v), "reason": None}
            for k, v in (surface.get("manifests_required") or {}).items()
        },
        "failure_chains": failure_chains,
        "fatal_misreads": misreads,
        "active_invariants": invariants,
        "required_relationship_tests": rel_tests,
        "required_static_gates": static_gates,
        "optional_nightly_gates": [],
        "pr_template_questions": [],
        "reviewer_checklist": [],
        "ci_classification": ci_classification,
        "override": {
            "allowed": True,
            "no_override_rules": list(
                manifests.enforcement.get("no_override_rules") or []
            ),
            "override_file": "architecture/ci_overrides.yaml",
        },
        "renderer": {
            "compact_budget_lines": 40,
            "expanded_budget_lines": 200,
            "include_evidence_links": True,
        },
        "evidence_sources": [
            {
                "type": "manifest",
                "ref": "architecture/topology_surfaces.yaml",
                "note": f"surface_id={surface_id}",
            },
            *(
                {
                    "type": "manifest",
                    "ref": "architecture/failure_chains.yaml",
                    "note": fc["id"],
                }
                for fc in failure_chains
            ),
        ],
        "not_topology_responsibility": _not_topology_responsibility_for_chains(
            fc_ids, manifests
        ),
        "review_required": [],
        # Convenience: agent runtime warnings rendered into preamble.
        "agent_runtime_warnings": _agent_runtime_warnings_for_chains(fc_ids, manifests),
    }
    return pack


def assemble_context_packs(
    changed_files: list[str],
    *,
    task_label: str | None = None,
    repo_root: Path | None = None,
    mode: Literal["emit_per_surface", "emit_merged"] = "emit_per_surface",
) -> dict[str, Any]:
    """
    Top-level entry point. Returns:
        {
          "schema_version": 1,
          "matched_files": [...],
          "packs": [<context_pack>, ...],
          "missing_surfaces_for_files": [<file_path>, ...]
        }
    """
    manifests = TopologyManifests(repo_root)
    by_surface = match_surfaces(changed_files, manifests)

    matched_files: set[str] = set()
    for matches in by_surface.values():
        for m in matches:
            matched_files.add(m["matched_value"])
    missing = [fp for fp in changed_files if fp not in matched_files]

    packs: list[dict[str, Any]] = []
    if not by_surface:
        return {
            "schema_version": 1,
            "matched_files": [],
            "packs": [],
            "missing_surfaces_for_files": missing,
            "review_required": [
                "No surface matched. Either changed files are out of money-path "
                "scope, or topology_surfaces.yaml needs a new surface entry."
            ]
            if changed_files
            else [],
        }

    if mode == "emit_per_surface":
        for sid, matches in by_surface.items():
            packs.append(build_context_pack(sid, matches, manifests, task_label=task_label))
    elif mode == "emit_merged":
        # Stable anchor selection: highest tier rank first, then alphabetical
        # surface_id. Without the secondary sort, equivalent inputs in
        # different file order could pick different anchors (per Copilot
        # finding on PR #343).
        anchor_sid = sorted(
            by_surface.keys(),
            key=lambda s: (
                -_tier_rank(manifests.surfaces["surfaces"][s].get("risk_tier", "T4")),
                s,
            ),
        )[0]
        anchor_pack = build_context_pack(
            anchor_sid, by_surface[anchor_sid], manifests, task_label=task_label
        )
        # Union matched_surfaces across all hits — deterministic order
        # (by surface_id then matched_value) for downstream stability.
        all_matches: list[dict[str, Any]] = []
        for sid in sorted(by_surface.keys()):
            all_matches.extend(
                sorted(by_surface[sid], key=lambda m: m["matched_value"])
            )
        anchor_pack["matched_surfaces"] = all_matches
        anchor_pack["id"] = f"merged_{anchor_pack['id']}"
        packs.append(anchor_pack)
    else:
        raise ValueError(f"unknown mode: {mode!r}")

    return {
        "schema_version": 1,
        "matched_files": sorted(matched_files),
        "packs": packs,
        "missing_surfaces_for_files": missing,
        "review_required": [],
    }


def _tier_rank(tier: str) -> int:
    """T0 highest, T4 lowest."""
    return {"T0": 4, "T1": 3, "T2": 2, "T3": 1, "T4": 0}.get(tier, 0)


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------


def render_markdown(
    bundle: dict[str, Any], *, mode: Literal["compact", "expanded"] = "compact"
) -> str:
    lines: list[str] = []
    packs = bundle.get("packs") or []
    if not packs:
        missing = bundle.get("missing_surfaces_for_files") or []
        if missing:
            lines.append("# Zeus Context Pack: REVIEW_REQUIRED")
            lines.append("")
            lines.append("No surface registry hit. Changed files may be outside money-path scope,")
            lines.append("or `architecture/topology_surfaces.yaml` may need a new surface entry.")
            lines.append("")
            lines.append("Files without surface coverage:")
            for fp in missing:
                lines.append(f"- `{fp}`")
        else:
            lines.append("# Zeus Context Pack: (no changed files)")
        return "\n".join(lines)

    for pack in packs:
        lines.append(f"# Zeus Context Pack: {pack['id']}")
        lines.append("")
        lines.append("## Why you are seeing this")
        # Dedup surface_ids while preserving first-appearance order — same
        # surface can match multiple files in multi-file diffs (per-file
        # match detail still available in pack["matched_surfaces"]).
        seen_sid: dict[str, None] = {}
        for s in pack.get("matched_surfaces", []):
            seen_sid.setdefault(s["surface_id"], None)
        surfaces_str = ", ".join(seen_sid.keys())
        segs = " → ".join(pack.get("money_path_segments", []))
        lines.append(
            f"Changed files matched surface(s): **{surfaces_str}**. Money path segments: {segs}."
        )
        lines.append("")

        lines.append("## Read before reasoning")
        idx = 1
        for entry in pack.get("required_reads", {}).get("repo_files", []):
            lines.append(f"{idx}. `{entry['path']}` — {entry['reason']}")
            idx += 1
        for entry in pack.get("required_reads", {}).get("architecture_manifests", []):
            lines.append(f"{idx}. `{entry['path']}` — {entry['reason']}")
            idx += 1
        lines.append("")

        lines.append("## Scoped AGENTS")
        for entry in pack.get("scoped_agents", []) or pack.get("required_reads", {}).get(
            "scoped_agents", []
        ):
            lines.append(f"- `{entry['path']}` — {entry.get('reason', '')}")
        lines.append("")

        if pack.get("active_invariants"):
            lines.append("## Active invariants")
            for inv in pack["active_invariants"]:
                lines.append(f"- {inv['id']}: {inv.get('reason', '')}")
            lines.append("")

        if pack.get("failure_chains"):
            lines.append("## Historical failure chains")
            for fc in pack["failure_chains"]:
                lines.append(f"- {fc['id']}: {fc.get('reason', '')}")
            lines.append("")

        if pack.get("fatal_misreads"):
            lines.append("## Fatal misreads (injected)")
            for fm in pack["fatal_misreads"]:
                inject_mark = "" if fm.get("inject_into_prompt") else " (NOT injected — review_required)"
                lines.append(f"- {fm['id']}{inject_mark}: {fm.get('reason', '')}")
            lines.append("")

        if pack.get("agent_runtime_warnings"):
            lines.append("## Runtime warnings (from failure chains)")
            for w in pack["agent_runtime_warnings"]:
                lines.append(f"- {w}")
            lines.append("")

        if mode == "expanded" or pack.get("ci_classification", {}).get(
            "blocking_static"
        ) or pack.get("ci_classification", {}).get("blocking_relationship"):
            lines.append("## Required gates")
            ci = pack.get("ci_classification", {})
            if ci.get("blocking_static"):
                lines.append("Blocking static:")
                for g in ci["blocking_static"]:
                    lines.append(f"- {g}")
            if ci.get("blocking_relationship"):
                lines.append("")
                lines.append("Blocking relationship tests:")
                for t in ci["blocking_relationship"]:
                    lines.append(f"- {t}")
            if ci.get("advisory"):
                lines.append("")
                lines.append("Advisory:")
                for s in ci["advisory"]:
                    lines.append(f"- {s}")
            lines.append("")

        if pack.get("not_topology_responsibility"):
            lines.append("## Topology boundary")
            lines.append("Topology routes context; it does not prove:")
            for n in pack["not_topology_responsibility"]:
                lines.append(f"- {n}")
            lines.append("")

        if mode == "expanded" and pack.get("evidence_sources"):
            lines.append("## Evidence sources")
            for e in pack["evidence_sources"]:
                lines.append(f"- {e['type']}: `{e['ref']}` ({e.get('note', '')})")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Assemble Context Pack(s) for changed files. "
            "Deterministic path → surface → failure chain mapping."
        )
    )
    parser.add_argument(
        "--changed-files",
        nargs="+",
        required=True,
        help="List of changed file paths (repo-relative)",
    )
    parser.add_argument(
        "--task", default=None, help="Optional task label for the pack title"
    )
    parser.add_argument(
        "--mode",
        choices=("emit_per_surface", "emit_merged"),
        default="emit_per_surface",
        help="UNION mode for multi-surface inputs (default: emit_per_surface)",
    )
    parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="json",
        help="Output format (default: json)",
    )
    parser.add_argument(
        "--render-mode",
        choices=("compact", "expanded"),
        default="compact",
        help="Markdown render mode (default: compact)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Write output to file instead of stdout",
    )
    args = parser.parse_args(argv)

    bundle = assemble_context_packs(
        args.changed_files,
        task_label=args.task,
        mode=args.mode,
    )

    if args.format == "json":
        payload = json.dumps(bundle, indent=2)
    else:
        payload = render_markdown(bundle, mode=args.render_mode)

    if args.output:
        Path(args.output).write_text(payload)
    else:
        print(payload)

    # Exit code 0 unless no packs AND there were changed files (caller can
    # decide if that's REVIEW_REQUIRED).
    return 0


if __name__ == "__main__":
    sys.exit(main())

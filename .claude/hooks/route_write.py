# Created: 2026-06-14
# Last reused or audited: 2026-06-14
# Authority basis: docs/operations/current/workspace-routing-redesign/PLAN.md (design of record), §2/§3/§6
#
# route_write — PreToolUse(Write) handler that puts a NEW file in its canonical
# home at write time by rewriting file_path via updatedInput. The Write tool
# result reports the rewritten path (verified: code.claude.com/docs/en/hooks),
# so the agent's own path model is corrected through the reliable channel — zero
# friction, nothing to remember, nothing to evade.
#
# CONTRACT (dispatch.py main() understands these return shapes):
#   None                         -> no-op
#   str                          -> NUDGE (additionalContext; write lands as-asked)
#   "__BLOCK__"                  -> HARD-STOP (exit 2; reason already on stderr)
#   {"updatedInput": {...},
#    "additionalContext": str}   -> SILENT-ROUTE (rewrite tool args before run)
#   {"permissionDecision": "ask",
#    "permissionDecisionReason": str} -> ASK
#
# CHARTER: fail-open on ANY exception — a crash NEVER blocks a write. WRITE-ONLY:
# this handler is wired on the `Write` matcher; it must never reroute Edit/
# MultiEdit (those carry old_string built against an existing file — rerouting
# corrupts them, see PLAN §2a). Edits are filtered out both by the matcher and by
# the "path already exists -> no-op" guard below.
#
# S1 SCOPE (first slice, smallest blast radius): SILENT-ROUTE high-precision
# scratch into the already-gitignored .omx/ ; HARD-STOP cross-tenant worktree
# writes. Work-artifact by-work routing is intentionally INERT here (returns
# None) until S0 lands the by-work file_arrangement policy + the work-name
# resolver (PLAN §4/§3a). Anti-data-loss (R16): anything not unambiguously
# scratch is left exactly where the agent asked — never silent-buried.

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

_BLOCK_SENTINEL = "__BLOCK__"  # must match dispatch.py

REPO_ROOT = Path(__file__).resolve().parents[2]
_OMX = ".omx/"

# --- R16 high-precision scratch allow-list -------------------------------------
# A write is routed to .omx/ ONLY when its basename matches one of these
# unambiguous scratch shapes. Deliberately narrow: a real-looking file that
# merely lacks a home must NOT match (it falls through to None = lands as-asked,
# visible). "When in doubt, make it visible, never hide it."
_SCRATCH_BASENAME_RE = re.compile(
    r"""^(
        wf_[A-Za-z0-9_-]+\.(?:js|mjs|cjs)      # workflow scratch scripts
      | .+_scratch(?:\.[A-Za-z0-9]+)?          # *_scratch / *_scratch.ext
      | .+\.tmp                                 # *.tmp
      | scratch_.+                              # scratch_*
      | tmp_[A-Za-z0-9_-]+\.[A-Za-z0-9]+        # tmp_*.ext
    )$""",
    re.VERBOSE,
)

# Directories that are already scratch / out of scope — never touch a write here.
_ALREADY_HOME_PREFIXES = (
    ".omx/",
    ".omc/",
    "state/",
)

# --- work-artifact detection (S2 increment: NUDGE loose drops) -----------------
# A work-artifact written to a LOOSE location (repo root / docs root /
# docs/operations root / current/ root) is misplaced — its home is a by-work
# folder docs/operations/current/<work>/ (file_arrangement.yaml by-work canonical).
# We NUDGE (never silent-route): work-name resolution is not yet reliable (no
# clean active-work pointer; current/ is a by-kind/by-work hybrid), so silently
# guessing the wrong work is worse than one visible nudge. Silent-route for work
# kinds lands with the full resolver + zpkt by-work creation. Anti-data-loss: a
# work-artifact is NEVER buried — it lands exactly where asked, plus a nudge.
_WORK_ARTIFACT_RE = re.compile(
    r"""(?ix)^(
        (?:.*[_-])?plan\.md            # PLAN.md / <slug>_PLAN.md
      | .*_report.*\.md | report\.md   # *_REPORT*.md / report.md
      | .*_evidence.*                  # *_EVIDENCE*
      | closeout.*                     # closeout*.md
      | scope\.yaml                    # scope.yaml
    )$""",
)
# Loose dirs where a work-artifact does NOT belong. A deeper subfolder (a by-kind
# legacy dir like current/plans/, or an actual by-work current/<work>/) is left
# alone — legacy is recognized (migration regroups it); by-work is already correct.
_LOOSE_WORK_DIRS = frozenset({
    "",                          # repo root
    "docs",
    "docs/operations",
    "docs/operations/current",
})

# Legacy by-kind subdirs under current/ — recognized, left alone (migration
# regroups them); a write here is NOT "loose" and NOT a placed by-work folder.
_BYKIND_LEGACY = frozenset({"plans", "evidence", "reports", "closeouts", "agent_runtime"})
_CURRENT_PREFIX = "docs/operations/current/"


def _work_kind_and_slug(basename: str):
    """Classify a work-artifact basename -> (kind, slug). slug is None when it
    can't be inferred from the filename (bare PLAN.md/report.md/scope.yaml).
    Returns (None, None) if not a work-artifact."""
    low = basename.lower()
    if low == "scope.yaml":
        return ("scope", None)
    if low == "plan.md":
        return ("plan", None)
    if low == "report.md":
        return ("report", None)
    m = re.match(r"(?i)^(.+?)[_-]plan\.md$", basename)
    if m:
        return ("plan", m.group(1))
    m = re.match(r"(?i)^(.+?)[_-]report.*\.md$", basename)
    if m:
        return ("report", m.group(1))
    m = re.match(r"(?i)^(.+?)[_-]evidence.*$", basename)
    if m:
        return ("evidence", m.group(1))
    m = re.match(r"(?i)^closeout[_-]?(.+?)(?:\.md)?$", basename)
    if m:
        return ("closeout", m.group(1))
    return (None, None)


def _work_target(kind: str, slug: str, basename: str) -> str:
    """Canonical slot for a kind inside the by-work folder current/<slug>/."""
    base = f"{_CURRENT_PREFIX}{slug}"
    if kind == "plan":
        return f"{base}/PLAN.md"
    if kind == "scope":
        return f"{base}/scope.yaml"
    if kind == "report":
        return f"{base}/report.md"
    if kind == "closeout":
        return f"{base}/closeout.md"
    if kind == "evidence":
        return f"{base}/evidence/{basename}"
    return f"{base}/{basename}"


def _rel(file_path: str) -> str | None:
    """Repo-relative POSIX path, or None if outside the repo / unparseable."""
    try:
        p = Path(file_path)
        if p.is_absolute():
            try:
                return p.relative_to(REPO_ROOT).as_posix()
            except ValueError:
                return None
        return p.as_posix()
    except Exception:
        return None


def _worktree_root_for(rel: str) -> str | None:
    """If rel is under .claude/worktrees/<agent>/, return that worktree prefix."""
    m = re.match(r"(\.claude/worktrees/[^/]+)/", rel)
    return m.group(1) if m else None


def _run_advisory_check_route_write(payload: dict[str, Any]) -> Any:
    """See module docstring for the return contract. Fail-open on everything."""
    try:
        tool = payload.get("tool_name", "") or payload.get("tool", "")
        # WRITE-ONLY guard (belt-and-suspenders; matcher already restricts this).
        if tool and tool != "Write":
            return None

        tool_input = payload.get("tool_input", {}) or {}
        if not isinstance(tool_input, dict):
            return None
        raw_path = tool_input.get("file_path", "") or ""
        if not raw_path:
            return None

        rel = _rel(raw_path)
        if rel is None:
            return None  # outside repo — not ours to route

        # --- HARD-STOP: cross-tenant worktree write ---------------------------
        # A Write whose target sits inside ANOTHER agent's worktree, issued from
        # outside that worktree, is the cross-tenant corruption case (PLAN §2).
        # We are running for the current session; CWD-based ownership is not
        # available here, so we only block the unambiguous case: a write that
        # names a peer worktree path AND we are not that worktree. Conservative:
        # only block when the path is a peer worktree AND repo cwd is the main
        # tree (the common orchestrator case). Anything uncertain -> fall open.
        wt = _worktree_root_for(rel)
        if wt is not None:
            try:
                cwd_rel = Path.cwd().resolve().relative_to(REPO_ROOT).as_posix()
            except Exception:
                cwd_rel = ""
            # If our cwd is NOT inside that same worktree, this is cross-tenant.
            if not (cwd_rel == wt or cwd_rel.startswith(wt + "/")):
                sys.stderr.write(
                    f"[route_write] BLOCK: cross-tenant write into {wt}/ from "
                    f"outside that worktree. Agents own their own worktree only; "
                    f"write within your tree or to the main tree.\n"
                )
                return _BLOCK_SENTINEL
            return None  # in-tenant write — leave it alone

        # --- already-home / out-of-scope: no-op -------------------------------
        for pref in _ALREADY_HOME_PREFIXES:
            if rel.startswith(pref):
                return None

        # --- existence guard: a Write over an EXISTING file is an overwrite of
        # something already homed; routing it is wrong (and Edits never reach
        # here). Only the truth-clobber case would inspect it (not in S1).
        try:
            if (REPO_ROOT / rel).exists():
                return None
        except Exception:
            return None

        basename = rel.rsplit("/", 1)[-1]

        # --- SILENT-ROUTE: high-precision scratch -> .omx/ --------------------
        if _SCRATCH_BASENAME_RE.match(basename):
            # Already under .omx? handled above. Route to .omx/<basename>.
            canonical = _OMX + basename
            if canonical == rel:
                return None
            new_input = dict(tool_input)
            new_input["file_path"] = canonical
            return {
                "updatedInput": new_input,
                "additionalContext": (
                    f"route_write: scratch artifact routed -> {canonical} "
                    f"(gitignored). The tool result shows the path it landed at; "
                    f"write scratch there directly next time."
                ),
            }

        # --- work-artifact handling (S2-full) ---------------------------------
        parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
        if _WORK_ARTIFACT_RE.match(basename):
            # Already placed in a by-work folder current/<work>/ (a single segment
            # after current/ that is NOT a by-kind legacy name) -> correct, no-op.
            if parent.startswith(_CURRENT_PREFIX):
                seg = parent[len(_CURRENT_PREFIX):].split("/", 1)[0]
                if seg and seg not in _BYKIND_LEGACY:
                    return None
            # Loose location -> try SILENT-ROUTE into an EXISTING matching work
            # folder (work-name resolved by slug from the filename); else NUDGE.
            if parent in _LOOSE_WORK_DIRS:
                kind, slug = _work_kind_and_slug(basename)
                if kind and slug:
                    work_dir = REPO_ROOT / _CURRENT_PREFIX / slug
                    target = _work_target(kind, slug, basename)
                    # Silent-route ONLY when the work folder already exists AND the
                    # target slot is empty (never clobber, never create a phantom
                    # work — anti-misroute / anti-data-loss).
                    try:
                        folder_ok = work_dir.is_dir()
                        slot_free = not (REPO_ROOT / target).exists()
                    except Exception:
                        folder_ok = slot_free = False
                    if folder_ok and slot_free and target != rel:
                        new_input = dict(tool_input)
                        new_input["file_path"] = target
                        return {
                            "updatedInput": new_input,
                            "additionalContext": (
                                f"route_write: {kind} routed -> {target} (its by-work "
                                f"home; matched existing work '{slug}'). The tool "
                                f"result shows where it landed."
                            ),
                        }
                # No resolvable existing work -> NUDGE (never silent-misroute).
                return (
                    f"route_write: '{basename}' looks like a work-artifact "
                    f"(plan/report/evidence/closeout/scope) at a loose location "
                    f"({parent or 'repo root'}). Its home is a by-work folder "
                    f"docs/operations/current/<work>/ (one folder per mission, "
                    f"holding PLAN.md + scope.yaml + evidence/ + report.md together). "
                    f"Write it under docs/operations/current/<work>/ or run "
                    f"`zpkt start <work>`. (Left where you asked — nudge, not block.)"
                )

        # --- everything else: leave the write exactly where asked -------------
        # Anti-data-loss (R16): ambiguous / unknown kinds are never silent-routed
        # and never buried.
        return None
    except Exception:
        # Charter: a crash never blocks or alters a write.
        return None

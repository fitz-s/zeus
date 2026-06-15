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

        # --- everything else: INERT in S1 (anti-data-loss: never bury) --------
        # Work-artifact by-work routing arrives in S0+S2. Until then, leave the
        # write exactly where asked.
        return None
    except Exception:
        # Charter: a crash never blocks or alters a write.
        return None

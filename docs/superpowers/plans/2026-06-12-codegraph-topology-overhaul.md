# Codegraph adoption + topology de-ritualization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make codegraph (and code-review-graph) the structural-context tools agents actually use, by rebalancing prose away from the topology routing ritual and adding a strong UserPromptSubmit context-injection hook — without touching topology's governance gates or any live-trading code.

**Architecture:** Three independent components. (1) Freshness: an installable post-commit `codegraph sync` hook so the index never silently goes stale. (2) Prose rebalance: global `CLAUDE.md` promotes codegraph as the default structural-lookup tool; `AGENTS.md` stops framing `topology_doctor --navigation` as the "canonical pre-edit gate" while keeping planning-lock/map-maintenance/closeout mandatory. (3) A strong, default-on `UserPromptSubmit` advisory hook (`codegraph_context_inject`) in the existing dispatch.py dispatcher that injects a prominent codegraph banner + real `file:line` context for code-task prompts, fail-open with an "index missing" hint.

**Tech Stack:** Python 3 (`.claude/hooks/dispatch.py` advisory-hook framework), pytest, YAML registry (`.claude/hooks/registry.yaml`), Claude Code hook JSON protocol, `codegraph` CLI 0.9.4.

**Authority basis:** Design spec `docs/superpowers/specs/2026-06-12-codegraph-topology-overhaul-design.md`. Operator directives 2026-06-12.

**Governance-file caution:** Tasks 4–7 touch `.claude/hooks/**` and `.claude/settings.json`. After each such change run the maintenance-worker checklist: `python3 .claude/hooks/dispatch.py boot_self_test_only`, `python3 -c "import json; json.load(open('.claude/settings.json'))"`, confirm BLOCKING hooks (`pr_create_loc_accumulation`, `pre_merge_comment_check`, `cotenant_staging_guard`) are NOT demoted, registry.yaml existing severities unchanged. Set `ZEUS_MW_DRY_RUN_VERIFIED=1` on the commit env. Stage by explicit pathspec only (cotenant guard blocks broad `git add`).

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `/Users/leofitz/.claude/CLAUDE.md` | Global agent instructions — "Code graph routing" section | Modify (NOT in zeus repo — committed to user's global config, not zeus git) |
| `AGENTS.md` | Zeus root agent doc — pre-edit flow framing | Modify |
| `scripts/install_codegraph_hooks.sh` | Installs a post-commit `codegraph sync` git hook (idempotent, embeds interpreter-independent CLI path) | Create |
| `.claude/hooks/codegraph_context_inject.py` | UserPromptSubmit handler: detect code task, shell to `codegraph context`, build banner + capped context, fail-open | Create |
| `tests/hooks/test_codegraph_context_inject.py` | Unit tests for the handler (mock the codegraph subprocess seam) | Create |
| `.claude/hooks/dispatch.py` | Import + register the new handler in `_ADVISORY_HANDLERS` | Modify (~line 1716 import block, ~line 1745 handler map) |
| `.claude/hooks/registry.yaml` | Add `codegraph_context_inject` hook spec | Modify (append to `hooks:`, bump `catalog_size`) |
| `.claude/settings.json` | Wire `UserPromptSubmit` → `dispatch.py codegraph_context_inject` | Modify (add/extend UserPromptSubmit hook array) |

Components are independent and independently revertible. Recommended order: Task 1 (freshness) → Tasks 2–3 (prose) → Tasks 4–7 (hook). The hook (Component 3) is the highest-risk; do it last so the cheap wins land first.

---

## Task 1: Freshness — installable post-commit `codegraph sync` hook

**Files:**
- Create: `scripts/install_codegraph_hooks.sh`

- [ ] **Step 1: Write the install script**

```bash
#!/usr/bin/env bash
# Created: 2026-06-12
# Authority basis: docs/superpowers/specs/2026-06-12-codegraph-topology-overhaul-design.md (Component 1)
# Purpose: install an idempotent post-commit hook that runs `codegraph sync`
#          so the local index never silently goes stale after commits.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
HOOK="$REPO_ROOT/.git/hooks/post-commit"
CG_BIN="$(command -v codegraph || true)"

if [ -z "$CG_BIN" ]; then
  echo "codegraph not on PATH — install it first (npm i -g @graphify/codegraph or equivalent)." >&2
  exit 1
fi

MARKER="# >>> codegraph sync hook >>>"
END_MARKER="# <<< codegraph sync hook <<<"

# Create the hook file with a shebang if absent.
if [ ! -f "$HOOK" ]; then
  printf '#!/usr/bin/env bash\n' > "$HOOK"
  chmod +x "$HOOK"
fi

# Idempotent: remove any prior managed block, then append a fresh one.
if grep -qF "$MARKER" "$HOOK"; then
  # Delete the existing managed block in place.
  sed -i.bak "/$MARKER/,/$END_MARKER/d" "$HOOK" && rm -f "$HOOK.bak"
fi

cat >> "$HOOK" <<EOF
$MARKER
# Incremental index refresh; backgrounded + silenced so commits stay fast.
( "$CG_BIN" sync "$REPO_ROOT" >/dev/null 2>&1 & )
$END_MARKER
EOF

chmod +x "$HOOK"
echo "Installed codegraph post-commit sync hook at $HOOK (CLI: $CG_BIN)"
```

- [ ] **Step 2: Make it executable and run it**

Run: `chmod +x scripts/install_codegraph_hooks.sh && bash scripts/install_codegraph_hooks.sh`
Expected: `Installed codegraph post-commit sync hook at .../.git/hooks/post-commit (CLI: /Users/leofitz/.npm-global/bin/codegraph)`

- [ ] **Step 3: Verify idempotency**

Run: `bash scripts/install_codegraph_hooks.sh && grep -c "codegraph sync hook >>>" .git/hooks/post-commit`
Expected: `1` (running twice does not duplicate the block)

- [ ] **Step 4: Verify the hook fires on a no-op commit**

Run: `git commit --allow-empty -m "test: codegraph sync hook fires" && sleep 2 && codegraph status 2>&1 | grep -i "up to date"`
Expected: `Index is up to date` (sync ran in background, no error). Then drop the test commit: `git reset --soft HEAD~1`

- [ ] **Step 5: Commit**

```bash
ZEUS_MW_DRY_RUN_VERIFIED=1 git add scripts/install_codegraph_hooks.sh
git commit -m "tooling: installable post-commit codegraph sync hook (freshness)"
```

---

## Task 2: Prose — promote codegraph in global CLAUDE.md

**Files:**
- Modify: `/Users/leofitz/.claude/CLAUDE.md` ("Code graph routing" section)

> Note: this file is the user's global config, NOT zeus-tracked. The edit lands outside zeus git; there is no zeus commit for this task. State that in the handoff.

- [ ] **Step 1: Read the current section**

Run: `grep -n "Code graph routing" -A 8 /Users/leofitz/.claude/CLAUDE.md`
Expected: shows the existing 2-line section that says "Use CodeGraph for fast structural lookup … Use code-review-graph for review-specific evidence …".

- [ ] **Step 2: Replace the section with a stronger default-first framing**

Replace the existing "## Code graph routing" section body with:

```markdown
## Code graph routing

CodeGraph is the DEFAULT first step for any structural lookup — definition,
callers/callees, trace, symbol source, file structure, "how does X work",
"where is Y", "what calls Z". Query it BEFORE grep/Glob/Read. It is a live
indexed graph (sub-millisecond reads, ~20x fewer tokens than reading files);
a grep+read loop repeats work the index already did. Use `codegraph_context`
first, then ONE `codegraph_explore` for the source it surfaces. Reach for raw
Read/Grep only to confirm a specific detail the graph did not cover.

Per checkout the index must exist: run `codegraph init -i` once (the db is
gitignored and does NOT travel with a clone — a missing index is the usual
cause of "codegraph returned nothing"). Keep it fresh with the post-commit
sync hook (`scripts/install_codegraph_hooks.sh` in repos that ship it).

Use code-review-graph for review-specific evidence: changed-file review
context, risk scoring, affected flows, large-function triage, blast radius.
topology_doctor is NOT a code-search tool — it owns governance gates
(planning-lock, manifests, receipts, ownership, authority order), not routing.
```

- [ ] **Step 3: Verify the edit**

Run: `grep -n "DEFAULT first step\|codegraph init -i\|NOT a code-search tool" /Users/leofitz/.claude/CLAUDE.md`
Expected: three matching lines.

- [ ] **Step 4: No commit (out-of-repo file)** — note in handoff that global CLAUDE.md changed.

---

## Task 3: Prose — de-ritualize topology navigation in AGENTS.md

**Files:**
- Modify: `AGENTS.md` (lines ~36, ~247, ~286, ~374, ~382 — the `--navigation` "canonical pre-edit gate" framing)

- [ ] **Step 1: Find every "canonical pre-edit gate" / navigation-mandate occurrence**

Run: `grep -n "canonical pre-edit gate\|--navigation\|navigation, the canonical" AGENTS.md`
Expected: ~6 line numbers (the framing at the "Additional topology commands" bullet ~374 and the "Code Review Graph" Stage 1 block ~382, plus the top-of-file references ~36/247/286).

- [ ] **Step 2: Reframe the `--navigation` bullet (~line 374)**

Change the bullet that reads:
```
- `python3 scripts/topology_doctor.py --navigation --task "<task>" --intent <intent> --write-intent <write-intent> --files <files>` — typed-intent navigation, the canonical pre-edit gate
```
to:
```
- Structural context (which files a task touches, callers/callees, traces) →
  **codegraph** (`codegraph_context` / `codegraph_trace`), the default
  structural-lookup tool. `topology_doctor.py --navigation` is LEGACY routing
  (substring task-matching, superseded by codegraph) — not a required gate.
- `python3 scripts/topology_doctor.py --planning-lock --changed-files <files>` — REQUIRED gate: does this change need planning evidence?
- `python3 scripts/topology_doctor.py --map-maintenance --changed-files <files>` — REQUIRED gate: companion registry updates
```

- [ ] **Step 3: Reframe the Code Review Graph "Stage 1 (required)" block (~line 382)**

Change the Stage 1 text that mandates `--navigation` as "typed-intent admission (required)" so that admission is no longer pinned to navigation routing. Replace the Stage 1 sentence:
```
Stage 1 (required): typed-intent admission via
`python3 scripts/topology_doctor.py --navigation --task "<task>" --intent <intent> --write-intent <write-intent> --files <files> --json`
— emits admission status, risk tier, gate budget, and blocking reasons.
```
with:
```
Stage 1 (structural context): use **codegraph** for file discovery, callers,
callees, and traces. The governance admission that still matters — planning
evidence, map maintenance, authority order — comes from the REQUIRED gates
(`--planning-lock`, `--map-maintenance`) and the boot profiles, NOT from
navigation routing.
```

- [ ] **Step 4: Soften the top-of-file references (~lines 36, 247, 286)**

For each remaining `topology_doctor.py --navigation` reference that presents it as the mandatory pre-edit step, change "the canonical pre-edit gate" / "run this first" phrasing to "(legacy routing — prefer codegraph for structural lookup)". Leave the `--task-boot-profiles` and `--planning-lock` mandates intact — those are governance gates and stay required.

- [ ] **Step 5: Verify no mandatory-gate framing of navigation remains**

Run: `grep -n "canonical pre-edit gate" AGENTS.md`
Expected: no output (zero matches).
Run: `grep -n "planning-lock\|task-boot-profiles" AGENTS.md | head`
Expected: still present (governance mandates intact).

- [ ] **Step 6: Commit**

```bash
ZEUS_MW_DRY_RUN_VERIFIED=1 git add AGENTS.md
git commit -m "docs: de-ritualize topology --navigation; codegraph is default structural lookup

Navigation routing reframed as legacy (substring matching, superseded by
codegraph graph proximity). planning-lock/map-maintenance/task-boot-profiles
governance gates unchanged and still required."
```

---

## Task 4: Hook handler — failing test first

**Files:**
- Create: `tests/hooks/test_codegraph_context_inject.py`
- (Handler created in Task 5; this test drives its interface.)

The handler is `_run_advisory_check_codegraph_context_inject(payload: dict) -> str | None`. It must be unit-testable WITHOUT a live codegraph: the subprocess call is isolated behind a module-level seam `_run_codegraph_context(prompt: str) -> tuple[bool, str]` (returns `(ok, text)`), which tests monkeypatch.

- [ ] **Step 1: Write the failing tests**

```python
# Created: 2026-06-12
# Authority basis: docs/superpowers/specs/2026-06-12-codegraph-topology-overhaul-design.md (Component 3)
import importlib.util
from pathlib import Path

import pytest

_MOD_PATH = Path(__file__).resolve().parents[2] / ".claude" / "hooks" / "codegraph_context_inject.py"
_spec = importlib.util.spec_from_file_location("codegraph_context_inject", _MOD_PATH)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


def test_emits_banner_and_context_on_code_task(monkeypatch):
    monkeypatch.setattr(mod, "_run_codegraph_context", lambda p: (True, "evaluator.py:42 score_edge()"))
    out = mod._run_advisory_check_codegraph_context_inject(
        {"hook_event_name": "UserPromptSubmit", "prompt": "fix the edge calc in evaluator.py"}
    )
    assert out is not None
    assert "codegraph" in out.lower()
    assert "evaluator.py:42" in out  # real context surfaced
    assert "FIRST" in out or "before grep" in out.lower()  # imperative banner present


def test_no_emit_on_pure_non_code_prompt(monkeypatch):
    monkeypatch.setattr(mod, "_run_codegraph_context", lambda p: (True, "should not be called"))
    out = mod._run_advisory_check_codegraph_context_inject(
        {"hook_event_name": "UserPromptSubmit", "prompt": "thanks, that looks great!"}
    )
    assert out is None


def test_fail_open_index_missing(monkeypatch):
    monkeypatch.setattr(mod, "_run_codegraph_context", lambda p: (False, "Not initialized"))
    out = mod._run_advisory_check_codegraph_context_inject(
        {"hook_event_name": "UserPromptSubmit", "prompt": "refactor the executor daemon"}
    )
    assert out is not None
    assert "codegraph init -i" in out  # surfaces the fix, does not go silent


def test_review_prompt_also_surfaces_code_review_graph(monkeypatch):
    monkeypatch.setattr(mod, "_run_codegraph_context", lambda p: (True, "x.py:1 f()"))
    out = mod._run_advisory_check_codegraph_context_inject(
        {"hook_event_name": "UserPromptSubmit", "prompt": "review this PR diff for blast radius"}
    )
    assert out is not None
    assert "code-review-graph" in out.lower()


def test_default_on_no_recent_call_suppression(monkeypatch):
    # Strength choice: emit even if a codegraph call already happened — no suppression.
    calls = []
    monkeypatch.setattr(mod, "_run_codegraph_context", lambda p: (calls.append(p), (True, "a.py:1 g()"))[1])
    out = mod._run_advisory_check_codegraph_context_inject(
        {"hook_event_name": "UserPromptSubmit", "prompt": "where is place_limit_order defined"}
    )
    assert out is not None
    assert len(calls) == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/hooks/test_codegraph_context_inject.py -q`
Expected: FAIL — `codegraph_context_inject.py` does not exist yet (module load error).

---

## Task 5: Hook handler — implementation

**Files:**
- Create: `.claude/hooks/codegraph_context_inject.py`

- [ ] **Step 1: Write the handler**

```python
#!/usr/bin/env python3
# Created: 2026-06-12
# Authority basis: docs/superpowers/specs/2026-06-12-codegraph-topology-overhaul-design.md (Component 3)
# Purpose: UserPromptSubmit advisory hook — inject a strong codegraph-first
#          banner + real file:line context for code-task prompts. Fail-open.
"""Strong codegraph context injection (advisory; never blocks)."""
from __future__ import annotations

import re
import shutil
import subprocess
from typing import Any

# Code-task signal: a path, a dotted/camel/snake symbol, or an action verb.
_CODE_SIGNAL = re.compile(
    r"""(
        [\w/]+\.(?:py|yaml|yml|sql|sh|js|ts|md)\b   # a file path
      | \b(?:fix|refactor|debug|trace|implement|add|where|why|how\s+does|
            caller|callee|impact|review|rename|wire|hook|daemon|executor|
            calc|function|class|method|module|edit|change)\b
      | \b[a-z_]+\.[a-z_]+\(                          # call expr foo.bar(
      | \b[a-z]+_[a-z_]+\b                            # snake_case symbol
    )""",
    re.IGNORECASE | re.VERBOSE,
)
_REVIEW_SIGNAL = re.compile(r"\b(review|pr\b|diff|blast\s*radius|impact|affected\s+test)\b", re.IGNORECASE)

_MAX_CONTEXT_LINES = 35
_TIMEOUT_S = 8


def _run_codegraph_context(prompt: str) -> tuple[bool, str]:
    """Seam: shell to the codegraph CLI. Returns (ok, text). Monkeypatched in tests."""
    cg = shutil.which("codegraph")
    if not cg:
        return (False, "Not initialized")
    try:
        proc = subprocess.run(
            [cg, "context", prompt[:400]],
            capture_output=True, text=True, timeout=_TIMEOUT_S,
        )
    except Exception:
        return (False, "error")
    out = proc.stdout or ""
    if proc.returncode != 0 or "Not initialized" in out or "Not initialized" in (proc.stderr or ""):
        return (False, out or proc.stderr or "Not initialized")
    return (True, out)


def _is_code_task(prompt: str) -> bool:
    return bool(_CODE_SIGNAL.search(prompt or ""))


def _cap(text: str) -> str:
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    if len(lines) <= _MAX_CONTEXT_LINES:
        return "\n".join(lines)
    return "\n".join(lines[:_MAX_CONTEXT_LINES]) + "\n… (call codegraph_context for the full graph)"


def _run_advisory_check_codegraph_context_inject(payload: dict[str, Any]) -> str | None:
    prompt = payload.get("prompt", "") or payload.get("user_prompt", "")
    if not _is_code_task(prompt):
        return None

    review = bool(_REVIEW_SIGNAL.search(prompt))
    ok, text = _run_codegraph_context(prompt)

    if not ok:
        return (
            "[codegraph] index missing or errored — structural lookups will be "
            "slow. Run `codegraph init -i` once for this checkout, then use "
            "codegraph_context/codegraph_trace before grep/Read."
        )

    banner = (
        "[codegraph — USE FIRST] This is a code task. codegraph is the live "
        "indexed graph of this repo; query it (codegraph_context / "
        "codegraph_trace / codegraph_callers) BEFORE grep/Read — it is faster "
        "and ~20x cheaper in tokens. Relevant context for your prompt:"
    )
    body = _cap(text)
    extra = (
        "\n[code-review-graph] Review/impact task — also use code-review-graph "
        "for blast radius, affected tests, and risk-ordered review."
        if review else ""
    )
    return f"{banner}\n{body}{extra}"
```

- [ ] **Step 2: Run the tests**

Run: `.venv/bin/python -m pytest tests/hooks/test_codegraph_context_inject.py -q`
Expected: PASS (5 passed).

- [ ] **Step 3: Commit (handler + test together)**

```bash
ZEUS_MW_DRY_RUN_VERIFIED=1 git add .claude/hooks/codegraph_context_inject.py tests/hooks/test_codegraph_context_inject.py
git commit -m "hooks: codegraph_context_inject handler + tests (strong UserPromptSubmit injection)"
```

---

## Task 6: Register the handler in dispatch.py + registry.yaml

**Files:**
- Modify: `.claude/hooks/dispatch.py` (import block ~1714-1717; `_ADVISORY_HANDLERS` map ~1745)
- Modify: `.claude/hooks/registry.yaml` (append hook spec; bump `catalog_size`)

- [ ] **Step 1: Add the conditional import after the citation_grep_gate import (~line 1717)**

Insert after the existing `_run_advisory_check_citation_grep_gate` try/except block:

```python
try:
    from codegraph_context_inject import _run_advisory_check_codegraph_context_inject  # type: ignore[import-not-found]
except Exception:  # noqa: BLE001 - fail-open per dispatcher charter
    _run_advisory_check_codegraph_context_inject = None  # type: ignore[assignment]
```

- [ ] **Step 2: Register it after the citation_grep_gate conditional registration (~line 1748)**

Insert after the `if _run_advisory_check_citation_grep_gate is not None:` block:

```python
if _run_advisory_check_codegraph_context_inject is not None:
    _ADVISORY_HANDLERS["codegraph_context_inject"] = _run_advisory_check_codegraph_context_inject
```

- [ ] **Step 3: Append the hook spec to registry.yaml**

Add under `hooks:` (end of list):

```yaml
  - id: codegraph_context_inject
    event: UserPromptSubmit
    matcher: "*"
    intent: >
      Inject a strong codegraph-first banner + real file:line context on
      code-task prompts so agents use the live graph before grep/Read.
      ADVISORY: emits additionalContext; never blocks. Fail-open with an
      "index missing" hint when codegraph is uninitialized.
    blocked_when: []
    severity: ADVISORY
    sunset_date: 2026-09-12
    telemetry:
      ritual_signal_emitted: true
    owner_module: .claude/hooks/codegraph_context_inject.py
```

Then bump `metadata.catalog_size` from `20` to `21`.

- [ ] **Step 4: Run the boot self-test (handler↔registry pairing)**

Run: `python3 .claude/hooks/dispatch.py boot_self_test_only 2>&1 | tail -2`
Expected: `[hook integrity] OK: all 21 registry hooks have handlers` (no "no handler" warning for `codegraph_context_inject`).

- [ ] **Step 5: Confirm BLOCKING hooks not demoted + YAML valid**

Run: `grep -n "severity: BLOCKING" .claude/hooks/registry.yaml | wc -l && python3 -c "import yaml; yaml.safe_load(open('.claude/hooks/registry.yaml'))" && echo YAML_OK`
Expected: the pre-existing BLOCKING count unchanged + `YAML_OK`.

- [ ] **Step 6: Commit**

```bash
ZEUS_MW_DRY_RUN_VERIFIED=1 git add .claude/hooks/dispatch.py .claude/hooks/registry.yaml
git commit -m "hooks: register codegraph_context_inject (UserPromptSubmit, advisory)"
```

---

## Task 7: Wire the hook in settings.json + end-to-end verify

**Files:**
- Modify: `.claude/settings.json` (UserPromptSubmit hook array)

- [ ] **Step 1: Inspect the current UserPromptSubmit wiring**

Run: `python3 -c "import json; d=json.load(open('.claude/settings.json')); import pprint; pprint.pprint(d.get('hooks',{}).get('UserPromptSubmit'))"`
Expected: either `None` or an existing array of hook matcher objects. Note the exact shape used by sibling events (e.g. PreToolUse) so the new entry matches the project's convention.

- [ ] **Step 2: Add the dispatch entry**

Add to `.claude/settings.json` under `hooks.UserPromptSubmit`, matching the existing project shape (an array of `{matcher, hooks:[{type:"command", command:"python3 .claude/hooks/dispatch.py codegraph_context_inject"}]}`). If `UserPromptSubmit` is absent, create it. Preserve all other hook events byte-for-byte.

Example entry (align with sibling-event formatting actually present in the file):

```json
{
  "matcher": "*",
  "hooks": [
    { "type": "command", "command": "python3 .claude/hooks/dispatch.py codegraph_context_inject" }
  ]
}
```

- [ ] **Step 3: Validate JSON parses**

Run: `python3 -c "import json; json.load(open('.claude/settings.json')); print('JSON_OK')"`
Expected: `JSON_OK`

- [ ] **Step 4: End-to-end — simulate a code-task prompt through dispatch**

Run:
```bash
echo '{"hook_event_name":"UserPromptSubmit","prompt":"where is place_limit_order defined and what calls it"}' | python3 .claude/hooks/dispatch.py codegraph_context_inject
```
Expected: stdout JSON with `hookSpecificOutput.additionalContext` containing `[codegraph — USE FIRST]` and real `file:line` entries (or the "index missing" hint if the index is absent).

- [ ] **Step 5: End-to-end — non-code prompt emits nothing**

Run:
```bash
echo '{"hook_event_name":"UserPromptSubmit","prompt":"thanks that is perfect"}' | python3 .claude/hooks/dispatch.py codegraph_context_inject
```
Expected: no `additionalContext` payload (empty/`{}`-level output; the handler returned None).

- [ ] **Step 6: Boot self-test + settings parse (governance checklist)**

Run: `python3 .claude/hooks/dispatch.py boot_self_test_only 2>&1 | tail -1 && python3 -c "import json; json.load(open('.claude/settings.json')); print('settings_ok')"`
Expected: `OK: all 21 registry hooks have handlers` + `settings_ok`.

- [ ] **Step 7: Commit**

```bash
ZEUS_MW_DRY_RUN_VERIFIED=1 git add .claude/settings.json
git commit -m "hooks: wire codegraph_context_inject on UserPromptSubmit"
```

---

## Task 8: Spec verification + adoption-baseline note

**Files:**
- Modify: `docs/superpowers/specs/2026-06-12-codegraph-topology-overhaul-design.md` (append a "Deployed" note with the pre-change baseline)

- [ ] **Step 1: Record the adoption baseline for later comparison**

Append to the spec a short "Deployed 2026-06-12" block stating the pre-change baseline (codegraph 2 calls / 8 sessions; topology --navigation ~85) so a future session can measure whether the rebalance + injection moved the numbers. The real success metric is post-deploy: codegraph MCP calls per session rise materially; topology navigation ritual falls.

- [ ] **Step 2: Full hook regression (no other hook broke)**

Run: `.venv/bin/python -m pytest tests/hooks/ -q 2>&1 | tail -3`
Expected: all hook tests pass (new + pre-existing).

- [ ] **Step 3: Commit**

```bash
ZEUS_MW_DRY_RUN_VERIFIED=1 git add docs/superpowers/specs/2026-06-12-codegraph-topology-overhaul-design.md
git commit -m "docs: record codegraph adoption baseline + deployed note"
```

---

## Self-review notes

- **Spec coverage:** Component 1 → Task 1. Component 2 → Tasks 2–3. Component 3 → Tasks 4–7. Known-limitation (md blindspot) + Graphify rejection are documented in the spec, intentionally out of plan scope. Adoption metric → Task 8.
- **Governance safety:** every `.claude/**` change is followed by boot self-test + JSON/YAML parse + BLOCKING-count check; staging is explicit-pathspec only.
- **No live-trading path touched:** all files are docs, hooks, scripts, tests.
- **Topology gates untouched:** planning-lock / map-maintenance / closeout code and their AGENTS.md mandates remain; only `--navigation` routing framing is demoted.

# Codegraph adoption + topology de-ritualization — design

Created: 2026-06-12
Authority basis: operator directive 2026-06-12 ("先装上 codegraph 真正使用 …
topology 根本没有 agent 用 … 需要一个彻底的革新"); enforcement-mechanism
choice "codegraph 在 claude.md 里的说明还需要加强" + scope choice "prose + 软 hook".

## Problem

Zeus runs two structural-context systems and a doc-authority layer. Measured
usage across 8 zeus session logs:

- `mcp__codegraph__*`: **2 calls** (both `codegraph_search`).
- `mcp__code-review-graph__*`: **1 call** (a `build`, not a query).
- `topology_doctor.py --navigation`: **~85 invocations**.
- Bare `Read`: **897**.

The codegraph index was not even live: `.codegraph/graph.db` is gitignored and
was never built on this checkout, so `codegraph status` reported **Not
initialized** while the SessionStart banner printed a stale cached
`Nodes: 13788`. (Fixed during this investigation — see Done-already.)

### K design failure (K ≪ N)

All symptoms — 2 codegraph calls, 85 hollow topology calls, substring routing,
stale index, markdown invisibility — are one failure:

> **Zeus frames the worse structural-context tool (topology substring routing)
> as a mandatory gate, and the better one (codegraph, a real 56,918-node graph)
> as an optional optimization.**

`AGENTS.md` (lines ~374, ~382, ~390) repeatedly calls
`topology_doctor.py --navigation` *"the canonical pre-edit gate"*. An agent
facing "mandatory gate vs optional optimization" runs the gate every time. The
route-card output is **invoked but not consumed** — ritual, not use. That is why
topology has 85 calls and codegraph has 2.

### Why "just strengthen codegraph prose" is insufficient (counter-evidence)

codegraph's MCP instructions are **already maximal** — verbatim *"PRIMARY TOOL —
call this FIRST"*, *"answer DIRECTLY"*, *"20x fewer tokens"*. With that prose in
place it still got 2 calls/8 sessions. Strong prose pointed at codegraph did not
move adoption. The lever is therefore **rebalancing away from the competing
topology ritual**, plus a soft injection that puts codegraph output in front of
the agent without requiring a remembered call.

## Goals / non-goals

**Goals**
1. codegraph index is live and stays fresh (no silent staleness recurrence).
2. Structural lookup (task → relevant `file:line`) routes to codegraph by
   default, before grep/Read.
3. topology stops being framed as a routing gate; it keeps only the governance
   gates no graph tool can replace.
4. Agents receive relevant codegraph context without a remembered tool call.

**Non-goals**
- Deleting topology's substring-routing **code** this round (that is
  `scripts/**` architecture surgery requiring planning-lock + ARCH_PLAN_EVIDENCE;
  defer until the rebalance proves codegraph adoption).
- Installing Graphify (rejected: single-maintainer v0.4.x, `graphifyy`
  typosquat surface, per-rebuild LLM token cost, a third redundant index). Its
  one real edge — doc↔code edges in a multi-modal graph — is the genuine future
  gap (see Known limitation) but does not justify a third graph now.
- Touching planning-lock / map-maintenance / closeout / manifest / receipt /
  ownership / authority-order logic. Those are the irreplaceable core and stay
  exactly as they are.

## The two concerns topology_doctor conflated

| Concern | Today | After |
|---|---|---|
| **Structural routing** (task → file:line) | topology `--navigation`, ~40 `if "x" in task_l` substring branches, miss → generic fallback | **codegraph** (native graph proximity over 56,918 nodes) |
| **Governance gates** (planning-lock, manifests, receipts, ownership, authority-order, closeout) | topology_doctor sub-commands | **topology_doctor, unchanged** — still mandatory for architecture edits |

Coupling verified safe: `run_planning_lock` and `run_closeout` do **not** call
`build_runtime_route_card` or any `_route_card_*` / navigation function. Routing
and gates are already code-independent inside `topology_doctor.py`; only the
**prose** fuses them. So demoting navigation prose cannot weaken the gates.

## Design

### Component 1 — Freshness (done already, documented here for completeness)

`codegraph init -i` rebuilt the index: **2,498 files / 56,918 nodes** (python
2,497 + yaml 94 + js 1), up from the stale 661/13,788. The MCP file-watcher
keeps it ~1s behind writes once the db exists. Add a `codegraph sync` post-commit
git hook as belt-and-suspenders so a fresh clone/`uninit` can't silently drop
back to an empty index (this was the root cause of the stale state).

### Component 2 — Prose rebalance

- **Global `CLAUDE.md` "Code graph routing"** — promote codegraph to the
  explicit default for structural lookup (definitions, callers/callees, traces,
  symbol source, file structure) **before** grep/Read. State the freshness
  prerequisite (`codegraph init -i` once per checkout).
- **`AGENTS.md` ~330–395** — remove the "canonical pre-edit gate" framing from
  `--navigation`. Reframe the pre-edit flow as: structural context = codegraph;
  governance gates = topology (`--planning-lock`, `--map-maintenance`,
  `--task-boot-profiles`, closeout) which **remain mandatory** for the surfaces
  they guard. `--navigation` is documented as legacy/deprecated for routing.

Both edits are documentation; no code-path behavior changes.

### Component 3 — Strong codegraph-context injection hook

Operator directive 2026-06-12: *"这些好用的工具被 agent 使用的不够多，注入
context 信号不够强烈"* — the injection signal must be **strong and present**,
not a quiet conditional. A weak fail-quiet hook would reproduce the exact
under-use we are fixing. So this is a loud, default-on injection for code tasks.

A `UserPromptSubmit` advisory hook in the existing `.claude/hooks/dispatch.py`
dispatcher that injects, as `additionalContext`:

1. **A prominent imperative banner** stating codegraph is the live index
   (node/file count, freshness state) and is the **first** structural-lookup
   tool — before grep/Read — naming the exact MCP tools
   (`codegraph_context` / `codegraph_trace` / `codegraph_callers`).
2. **Actual relevant context** for the prompt: the top entry points + key
   `file:line` symbols codegraph surfaces for the task.

Strength choices (per the directive — stronger than the original conservative
draft):
- **Default-on for any code-task prompt.** Drop the "suppress if a codegraph
  call already happened recently" condition — that condition weakens the signal
  and is what the directive rejects. Code-task detection still gates out pure
  non-code chat so unrelated conversation is not spammed.
- **Lead with the imperative line**, then the context. The banner is the signal;
  the context is the payoff that makes the signal worth obeying.
- **Cover the review/impact lane too**: for review/PR/impact-shaped prompts,
  also surface `code-review-graph` (blast radius, affected tests) — the directive
  is about "these tools" plural, both graphs are under-used.

Guardrails (strength without breakage):
- **Advisory wrapper** (same `_emit_advisory` path as other dispatch.py checks);
  never blocks.
- **Cap** the context payload (~30–40 lines) so the loud banner stays readable;
  truncate with a "call `codegraph_context` for the full graph" pointer.
- **Fail-open**: if codegraph is uninitialized/errors, emit a one-line
  "run `codegraph init -i` — index missing" hint instead of silence (a missing
  index is itself a signal worth surfacing, given that is how it went stale).

## Known limitation (carry-forward)

codegraph indexes python + yaml but **not markdown**. The 392 `docs/**.md`
authority layer — the "why", the operator law, the twin-authority context — is
invisible to it. This is the structural blind spot behind the recurring
twin-authority incidents and is exactly the doc↔code edge Graphify would fill.
Out of scope here; logged as the candidate future lever (a markdown-aware index
or a scoped Graphify trial over `architecture/` + `docs/authority/` only).

## Testing / verification

- **Freshness**: `codegraph status` reports `Index is up to date`; post-commit
  hook re-syncs on a test commit.
- **Hook behavior**: unit-style check that the new dispatch.py check (a) emits a
  prominent codegraph banner + context on a code-task prompt, (b) emits nothing
  on a pure non-code prompt, (c) fails open with the "index missing" hint when
  codegraph is uninitialized, (d) surfaces code-review-graph on review/impact
  prompts. Run `python3 .claude/hooks/dispatch.py boot_self_test_only` + JSON
  parse of settings.json (governance-file guard checklist).
- **Prose**: no remaining `AGENTS.md` reference frames `--navigation` as a
  mandatory/canonical gate; planning-lock/map-maintenance mandates intact.
- **Adoption (the real metric, post-deploy)**: codegraph MCP call count per
  session rises materially from the 2/8-session baseline; topology `--navigation`
  ritual invocations fall. Measured from session logs after a few sessions.

## Rollout

1. Freshness hook (lowest risk).
2. Prose rebalance (CLAUDE.md + AGENTS.md).
3. Soft hook in dispatch.py — governance file; follow the maintenance-worker
   dry-run checklist (boot self-test, JSON parse, BLOCKING hooks not demoted,
   registry.yaml 64–102 unchanged).

Each step independently revertible. No live-trading code path touched.

## Deployed 2026-06-12

All three components shipped (commits `3155f19b17` freshness, `1adc299253`
AGENTS.md, `dd4a3e5c46` handler+tests, `3d2cd0d154` register, `33b7786e98`
wire). Global `CLAUDE.md` edit is out-of-repo (user config).

**Pre-change adoption baseline** (for later comparison — the real success metric
is whether these numbers move):
- codegraph MCP calls: **2 / 8 sessions** (both `codegraph_search`).
- code-review-graph queries: **~0**.
- topology `--navigation` ritual invocations: **~85**.
- codegraph index at session start: **absent** (`Not initialized`; stale cached
  boot banner). Rebuilt to 2,498 files / 56,918 nodes.

**Success = post-deploy:** codegraph MCP calls/session rise materially from 2/8;
topology navigation ritual falls. Re-measure from session logs after several
sessions. If adoption still does not rise, the spec's next lever is making the
UserPromptSubmit injection unconditional for code tasks (it is already
default-on; the remaining lever is widening the code-task detector).

**Pre-existing test debt (NOT introduced here, surfaced during T6):**
`tests/test_dispatch_session_start.py::test_registry_catalog_size_updated`
hard-codes `catalog_size == 15` (comment "was 12 + 3") while the real catalog is
23 — red on HEAD before this work. Three `test_dispatch_session_start.py`
worktree/session_start emit tests also fail on clean HEAD. This change FIXED
`test_hook_registry_schema.py::test_registry_metadata_catalog_size`
(catalog_size now equals the actual hook-list length). The stale `==15` test is
left for a separate cleanup.

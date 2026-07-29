# Zeus — System Card: Claims, Evidence, and Limits

Snapshot date: 2026-07-29
Commit: `c0f092008c101c24e55ac395a81f2d601de7737f`
Author: Fitz

## What it is

Zeus is an independent, live-money trading system for Polymarket weather
derivatives, running across 54 cities. It is a personal, risk-capped
deployment — one operator's capital, sized and gated for that scale rather
than institutional throughput, built and operated with substantial
coding-agent participation under the change-control regime described below.

## Three demonstrated capabilities

**(a) End-to-end live-money decision engine.** A repeating cycle turns
forecast data into settlement-aware probabilities, sizes and places orders,
holds an immutable record of each decision, and reconciles positions against
on-chain truth every pass — no step is a mockup or a backtest-only path. See
`src/engine/cycle_runtime.py` (cycle), `src/execution/command_bus.py` and
`src/execution/exit_lifecycle.py` (decision and exit state machine), and
`src/state/chain_reconciliation.py` plus `src/state/chain_mirror_reconciler.py`
(reconciliation against the chain, run every cycle rather than periodically).

**(b) Auditable AI-assisted change control.** Coding-agent access to this
repository is mediated by public, versioned hooks
(`.claude/hooks/registry.yaml`), a three-tier write policy for the
autonomous improvement loop (`loop/prompts/l1.md:74-101`: AUTO writes
`loop/`/`docs/`/`tests/` directly, PREPARE produces an unapplied patch plus
a red test for any `src/**` change, NEVER surfaces config/live-DB/deploy/risk
changes as proposal text only), a sandboxed execution boundary
(`loop/tick.sh`; Seatbelt-restricted filesystem and network access), and an
allowlist-enforced rollback path for anything outside the sandbox's own
guarantees. Several of the hooks exist because a prior incident happened,
not by design foresight — see `AI_ASSISTANCE.md` for two of them in detail.

**(c) Statistical evaluation discipline.** Trade outcomes are graded against
their frozen pre-trade decision record using a six-category attribution
taxonomy (SKILL_WIN / LUCKY_WIN-recorded-as-MISS / SKILL_LOSS / UNLUCKY_LOSS
/ NO_EDGE / INSUFFICIENT), with walk-forward calibration rather than
in-sample fitting (`src/decision/selection_calibrator.py`). Only SKILL_WIN
supports an edge claim; "insufficient evidence" is treated as a legitimate,
required conclusion rather than a failure to explain away. A full
calibration report against this taxonomy is forthcoming and not part of
this snapshot — this card claims the discipline exists, not a specific
result.

## Claim, evidence, limit

| Claim | Evidence | Limit |
|---|---|---|
| **[Demonstrated]** end-to-end live engine | pipeline + immutable decisions + reconciliation | personal scale, no institutional throughput |
| **[Demonstrated]** auditable AI-assisted change control | public hooks, AUTO/PREPARE/NEVER tiers, sandbox, allowlist rollback, incident-derived guards | no causal productivity/quality estimate; documented control failures |
| **[Supported]** calibration-evaluable probability outputs | walk-forward learning, six-class attribution, (calibration report forthcoming) | modest sample, stratification reduces power |
| **[Unknown]** durable net alpha | prospective hypothesis only | not established |
| **[Implemented, runtime not claimed]** unattended improvement loop | scripts + sandbox policy + launchd template inspectable | installation/current runtime not claimed by this snapshot |

## Human authority

Zeus's own change-control constitution states it plainly (CONST-10,
`docs/authority/zeus_change_control_constitution.md`): "LLM 输出永远不是
authority；只有 spec、tests、machine checks、runtime evidence 才是
authority" — LLM output is never authority; only spec, tests, machine
checks, and runtime evidence are. In practice: **generated output is never
authority. The operator owns research assumptions, acceptance decisions,
deployment, and capital risk.** Nothing in this repository's hooks, agents,
or loop can deploy, change risk posture, or move capital — those actions
are structurally excluded from every automated write path (`loop/prompts/l1.md:91`
NEVER list; `deploy_live.py` is operator-invoked only). This boundary has
been tested by real incidents, not only asserted — `AI_ASSISTANCE.md`
documents two cases where the control layer itself failed and how each was
caught and closed.

## Reading paths

Three entry points into the codebase, each with two links, for readers who
want depth in a specific direction rather than a full tour:

**Quant** — the probability and calibration machinery:
- `docs/reference/theory_map.md`
- `src/decision/selection_calibrator.py`

**Systems** — the execution and architecture spine:
- `src/execution/exit_lifecycle.py`
- `architecture/INDEX.md`

**AI-assisted engineering** — how coding agents are governed here:
- `.claude/README.md`
- `loop/README.md`

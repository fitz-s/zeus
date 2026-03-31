# Plan: Zeus Architecture Doctrine Consolidation
> Created: 2026-03-30 | Status: IN PROGRESS

## Goal
Consolidate Zeus's highest-authority architectural doctrine into the working plan so future implementation stays anchored on cross-module semantic preservation, lifecycle correctness, and truth hierarchy.

## Context
- Zeus's highest-authority design docs say the core failure mode is not signal math but semantic loss at module boundaries.
- The earlier framing incorrectly mixed two different concerns:
  - Zeus architecture: how the trading system itself should be designed.
  - Codex agent-team usage: how Codex should organize its own execution workflow.
- The user clarified that `agent team` is about Codex using more delegation internally, not about changing Zeus into an agent-team-shaped system.
- This work should leave behind durable artifacts that survive compact and can guide future implementation.

## Approach
Use the Zeus docs to extract the system's non-negotiable architectural laws, then record them in this plan as implementation constraints. Keep Codex execution strategy explicitly separate: agent-team workflow may help delivery, but it is not itself a Zeus product requirement.

## Core File Map
- Repo root: `/Users/leofitz/.openclaw/workspace-venus/zeus`
- Working plan: `/Users/leofitz/.openclaw/workspace-venus/zeus/PLAN.md`

## Docs Design Map
- Highest-authority framework/design principles: `/Users/leofitz/.openclaw/workspace-venus/zeus/docs/architecture/zeus_design_philosophy.md`
- Core architecture spec for the current position-centric system: `/Users/leofitz/.openclaw/workspace-venus/zeus/docs/architecture/zeus_blueprint_v2.md`
- First-principles critique and redesign pressure test: `/Users/leofitz/.openclaw/workspace-venus/zeus/docs/reference/zeus_first_principles_rethink.md`
- Historical v1 architecture baseline for contrast / regression tracing: `/Users/leofitz/.openclaw/workspace-venus/zeus/docs/reference/architecture_blueprint.md`
- Data-layer architecture and ETL/utilization constraints: `/Users/leofitz/.openclaw/workspace-venus/zeus/docs/DATA_STRATEGY.md`
- Market-structure reference when framework questions touch execution assumptions: `/Users/leofitz/.openclaw/workspace-venus/zeus/docs/reference/market_microstructure.md`
- Quant/stat references when framework questions touch modeling assumptions: `/Users/leofitz/.openclaw/workspace-venus/zeus/docs/reference/quantitative_research.md`, `/Users/leofitz/.openclaw/workspace-venus/zeus/docs/reference/statistical_methodology.md`
- Runtime policy companion: `/Users/leofitz/.openclaw/workspace-venus/TRADING_RULES.md`
- Agent-team operating model reference: `/Users/leofitz/.npm/_npx/43414d9b790239bb/node_modules/@paperclipai/adapter-codex-local/skills/paperclip/SKILL.md`
- Agent hiring / role-shape reference: `/Users/leofitz/.npm/_npx/43414d9b790239bb/node_modules/@paperclipai/adapter-codex-local/skills/paperclip-create-agent/SKILL.md`

## Core System Doctrine To Preserve
- Primary failure mode is semantic loss at module boundaries, not weak local math. Any team design that preserves module quality but loses cross-module meaning will recreate Zeus's current failure pattern.
- `Position` is the cross-module identity carrier. Direction, held-side probability space, entry method, decision snapshot, chain state, and attribution must travel with it end to end. Downstream inference is an architectural defect, not a convenience.
- Blueprint v2's center of gravity is lifecycle, not signal discovery. The system's real job is to manage positions from discovery to settlement without losing context; signal quality is valuable only insofar as lifecycle preserves it.
- `CycleRunner` should stay a pure orchestrator: housekeeping and reconciliation first, then monitor existing value, then scan for new entries. `opening_hunt`, `update_reaction`, and `day0_capture` are discovery modes on one lifecycle, not separate engines.
- Truth hierarchy is non-negotiable: `Chain > Chronicler > Portfolio`. Local working state is cache; immutable artifacts and decision history exist so the system can recover truth after crashes, restarts, or reconciliation disagreements.
- Natural-language design intent is not enough. The durable protection layer must be executable invariants, typed semantic boundaries, and relationship tests that check cross-module contracts rather than only function outputs.
- Complexity budget should move toward lifecycle, attribution, and provenance. The docs explicitly warn that adding signal sophistication while lifecycle remains weak is value-destructive.
- `Market Before Model` and `Risk Before Profit` are operating laws, not slogans. Market fusion, fail-closed risk behavior, monitor-only degradation, and per-strategy tracking must shape implementation and agent ownership.
- Four strategies are independent businesses inside Zeus: settlement capture, shoulder sell, center buy, and opening inertia. Architecture, reporting, and agent review must preserve strategy attribution so edge decay can be measured per strategy.
- Data policy is strict: runtime code should consume validated Zeus artifacts, not raw external files or ad hoc historical sources. ETL and snapshot provenance are part of correctness, not a later optimization.

## Clarification On Agent Team
- Codex may use an internal agent team to execute work on Zeus more effectively.
- Zeus itself should not be re-described as an `agent team architecture` project unless a separate product-level design decision is made.
- Any future plan item about delegation, subagents, heartbeat, or control-plane workflow belongs to Codex execution methodology, not Zeus runtime architecture.

## Tasks

- [x] 1. Re-anchor on Zeus's core design constraints
  - Files: `docs/architecture/zeus_design_philosophy.md`, `docs/architecture/zeus_blueprint_v2.md`, `docs/reference/zeus_first_principles_rethink.md`, `TRADING_RULES.md`
  - What: Restate the architectural failure mode in terms that can shape implementation constraints, invariants, and future refactors.

- [x] 2. Consolidate core doctrine into the working plan
  - Files: `PLAN.md`
  - What: Carry the non-negotiable Zeus principles into a compact-safe artifact so future turns do not drift back toward signal-heavy / lifecycle-light thinking.

- [ ] 3. Map doctrine to implementation workstreams
  - Files: `PLAN.md`
  - What: Turn the doctrine into concrete workstreams around position identity, truth hierarchy, provenance, invariant testing, and fail-closed runtime behavior.

- [ ] 4. Keep Codex execution strategy separate
  - Files: `PLAN.md`
  - What: If agent-team workflow guidance is needed later, record it as Codex operating practice rather than Zeus architecture.

## Risks / Open Questions
- Any future planning must keep a hard boundary between product architecture and delivery workflow; mixing them distorts both.
- Strategy, data, lifecycle, and risk are coupled; implementation planning must separate ownership without making integration someone else's accidental job.
- Data and ETL ownership is architectural, not support work. If no agent owns provenance, the same "latest snapshot" and contamination failures will return in a different form.
- If future Codex delegation increases without stronger executable invariants, compact/handoff loss will still worsen even if the Zeus design is correct.

## Compact Resume Note
- After compact, start from `Docs Design Map`, then continue from Zeus doctrine rather than agent-team framing.
- Treat this task as Zeus architecture work first, Codex workflow guidance second.
- Carry forward `Core System Doctrine To Preserve` as hard constraints; future design work should treat them as the architectural equivalent of invariants, not optional background context.

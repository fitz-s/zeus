# Rule Surface Map

Where each class of rule or law lives in Zeus. Read this before hunting across eight surfaces.

Precedence order is stated in [root AGENTS.md §1](../../AGENTS.md): direct instructions > AGENTS routers > executable law > authority docs > current facts. The table below maps to that order.

---

## Surfaces by rule class

| Surface | Rule class owned | When to consult |
|---------|-----------------|-----------------|
| **`AGENTS.md`** (root) | Operating contract: mission, money-path mental models, authority precedence, canonical DBs, lifecycle, routing protocol, docs/packet layering | First read on every task |
| **`src/**/AGENTS.md`** (nested, 20 packages) | Zone law: local invariants, edit gates, file registries, hazards specific to that package (e.g., `src/execution/AGENTS.md` for live-money boundary rules) | Before touching any file in that package subtree |
| **`REVIEW.md`** | Code-review protocol: runtime-risk tier ordering, cross-DB write rules (INV-37), PR review surface priority | Before reviewing or merging a PR |
| **`.github/instructions/*.instructions.md`** (10 files) | CI review lanes scoped by `applyTo` glob: `runtime-review` (Tier 0 execution/settlement/state), `zeus-money-path` (engine/contracts/strategy), `zeus-schema-state` (state schema/migrations), `zeus-ci-tests` (CI/workflow), `zeus-execution-settlement`, `zeus-forecast-source`, `zeus-risk-learning`, `docs-agent-review` (docs/AGENTS/architecture), `tier-scope` (global), `agent-workflow` (global) | GitHub Copilot and review automation; also useful as a checklist of what a reviewer should verify per surface |
| **`architecture/invariants.yaml`** | Machine-checkable runtime invariants (INV-##): lifecycle sequencing, cross-DB write boundaries, settlement semantics, advisory-only risk prohibition | When adding code near settlement, lifecycle, or cross-DB writes; CI enforces these |
| **`architecture/negative_constraints.yaml`** | Prohibited patterns (NC-##): cross-zone patch rules, no JSON-to-authority promotion, structural anti-patterns | Before any cross-zone or K0/K3 co-edit |
| **`docs/authority/**`** | Durable architecture and delivery law: replacement chain, regime unification, statistical calibration, exit authority, change-control constitution | When changing or verifying strategy, probability, settlement, or delivery posture; conflicts resolve toward code/manifests/runtime over dated prose |
| **`CLAUDE.md`** (root + `.claude/CLAUDE.md`) | Global and project-level agent preferences: tool routing, subagent tiers, operator laws (no caps, no shadow, no over-engineering), memory index | Agent behavior rules — not Zeus trading law |

---

## What lives where (quick-look)

| Question | Surface |
|----------|---------|
| "Is this change allowed in this zone?" | Scoped `src/**/AGENTS.md` |
| "What invariant does this violate?" | `architecture/invariants.yaml` (INV-##) |
| "Which patterns are permanently banned?" | `architecture/negative_constraints.yaml` (NC-##) |
| "What is the canonical probability chain?" | `docs/authority/replacement_final_form_2026_06_09.md` |
| "How should I review this PR surface?" | `REVIEW.md`, then the matching `.github/instructions/` lane |
| "What terms and math does Zeus use?" | `docs/reference/glossary.md`, `docs/reference/theory_map.md` |
| "What is the current live posture?" | `docs/operations/current_state.md` (and its companions — see §Current facts below) |

---

## Current-facts surfaces (not durable law)

These expire and must be verified live before relying on them:

- `docs/operations/current_state.md` — live control pointer
- `docs/operations/current_data_state.md` — data posture
- `docs/operations/current_source_validity.md` — source posture
- `docs/operations/current/` — active packets, plans, evidence

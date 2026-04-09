# ZEUS_AUTHORITY

> Role: root authority guide for humans and coding agents.
> This file summarizes Zeus's foundation, active invariant law, negative constraints, and core system boundaries.
> It is not a replacement for the exact authority sources. When precision or precedence matters, use `architecture/self_check/authority_index.md`, `architecture/invariants.yaml`, `architecture/negative_constraints.yaml`, `docs/architecture/zeus_durable_architecture_spec.md`, and `docs/zeus_FINAL_spec.md`.

## What Zeus is

Zeus is a durable, position-governed weather-arbitrage runtime. Its center is not a model, a dashboard, or a prompt. Its center is a **single authority-bearing trading system** with:

- one canonical lifecycle truth path,
- one bounded governance key (`strategy_key`),
- one point-in-time learning chain,
- one executable protective spine,
- one finite lifecycle grammar,
- one operator-facing derived surface,
- and one packetized change discipline that prevents local edits from outranking system law.

## Where authority lives

- **Present-tense architecture authority:** `docs/architecture/zeus_durable_architecture_spec.md`
- **Terminal target-state / endgame authority:** `docs/zeus_FINAL_spec.md`
- **Machine-checkable semantic authority:** `architecture/kernel_manifest.yaml`, `architecture/invariants.yaml`, `architecture/zones.yaml`, `architecture/negative_constraints.yaml`
- **Change-control authority:** `docs/governance/zeus_change_control_constitution.md`, `docs/governance/zeus_autonomous_delivery_constitution.md`
- **Repo operating brief:** `AGENTS.md`

This guide exists so a zero-context reader can see the whole foundation in one file before diving into the exact sources.

## The 10 live invariants

| ID | Invariant |
| --- | --- |
| `INV-01` | Exit is not local close. |
| `INV-02` | Settlement is not exit. |
| `INV-03` | Canonical authority is append-first and projection-backed. |
| `INV-04` | `strategy_key` is the sole governance key. |
| `INV-05` | Risk must change behavior. |
| `INV-06` | Point-in-time truth beats hindsight truth. |
| `INV-07` | Lifecycle grammar is finite and authoritative. |
| `INV-08` | Canonical write path has one transaction boundary. |
| `INV-09` | Missing data is first-class truth. |
| `INV-10` | LLM output is never authority. |

## The 10 live negative constraints

| ID | Negative constraint |
| --- | --- |
| `NC-01` | No broad prompt may edit K0 and K3 in the same patch without explicit packet justification. |
| `NC-02` | JSON exports may not be promoted back to authority. |
| `NC-03` | No downstream strategy fallback or re-inference when `strategy_key` is already available. |
| `NC-04` | No direct lifecycle terminalization from orchestration code. |
| `NC-05` | No silent fallback from missing decision snapshot to latest snapshot for learning truth. |
| `NC-06` | No memory-only durable governance. |
| `NC-07` | No raw phase/state string assignment outside lifecycle kernel. |
| `NC-08` | No bare implicit unit assumptions in semantic code paths. |
| `NC-09` | No ad hoc probability complements across architecture boundaries when semantic contracts exist. |
| `NC-10` | No new shadow persistence surface without deletion or demotion plan. |

## The 5 boundary rules

These are the cross-cutting boundary rules that keep the system from collapsing back into multi-truth patchwork.

1. **Authority boundary**  
   Canonical truth lives in append-first lifecycle events plus deterministic projection. Derived JSON, archive material, comments, and LLM output do not outrank that path.  
   **Sources:** `INV-03`, `INV-10`, `NC-02`

2. **Lifecycle boundary**  
   Exit intent, economic exit, settlement, and terminal lifecycle completion are distinct facts. Orchestration code may not collapse them into one local close action, and raw phase strings may not escape the lifecycle kernel.  
   **Sources:** `INV-01`, `INV-02`, `INV-07`, `NC-04`, `NC-07`

3. **Governance boundary**  
   `strategy_key` is the only governance key. Metadata and downstream heuristics may annotate behavior, but they may not become alternate strategy centers or fallback labels.  
   **Sources:** `INV-04`, `NC-03`

4. **Temporal-truth boundary**  
   Decision-time truth is primary. When a decision snapshot or upstream fact is missing, the system must degrade explicitly instead of silently upgrading to hindsight or latest-known state.  
   **Sources:** `INV-06`, `INV-09`, `NC-05`

5. **Durability boundary**  
   Durable control and truth must live in explicit repo-governed surfaces. Memory-only governance, hidden persistence, and new shadow stores without deletion/demotion plans are invalid.  
   **Sources:** `INV-09`, `INV-10`, `NC-06`, `NC-10`

## How to use this file

- Use this file first to understand the system's foundation and the shape of the law.
- Use `architecture/self_check/authority_index.md` to resolve precedence and exact read order.
- Use the machine-checkable manifests and architecture specs when exact wording, enforcement, or tie-breaking matters.
- Do not use this guide to overrule the exact authority sources it summarizes.

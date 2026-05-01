# Polymarket CLOB V2 Migration Packet

Created: 2026-04-26
Authority basis: this packet's `v2_system_impact_report.md` + `plan.md`
Status: R3 Z0 active; source code remains gated by R3 phase cards
Critic owner: critic-opus (zeus-midstream-critic) OR surrogate code-reviewer@opus

## Default entry point

Read in this order:

1. `v2_system_impact_report.md` — what V2 is, what changes, system-level impact
2. `plan.md` — execution plan (Phase 0 → Phase 4)
3. `open_questions.md` — operator decision points blocking phase advancement
4. `work_log.md` — running session log, updated per slice
5. `zeus_touchpoint_inventory.md` — grep-verified file:line registry of every Zeus CLOB integration site

## File registry

| File | Class | Purpose |
|------|-------|---------|
| `AGENTS.md` | packet router | This file — packet navigation |
| `v2_system_impact_report.md` | report | Deep capability + system impact analysis |
| `plan.md` | execution plan | Phase + slice plan with allowed_files + acceptance |
| `zeus_touchpoint_inventory.md` | reference | Grep-verified Zeus CLOB integration points |
| `open_questions.md` | decision tracker | Operator-owned questions blocking phase advancement |
| `polymarket_live_money_contract.md` | contract evidence | Packet-local R3 Z0 live-money invariant summary; not a new docs authority plane |
| `work_log.md` | running log | Per-slice closure record |
| `evidence/` | evidence dir | Operator-collected proofs (probes, inquiries, SDK diffs) |
| `receipt.json` | receipt | Slice closure receipt (created when first slice ships) |

## Scope

This packet plans and tracks Zeus's migration from Polymarket CLOB V1 to CLOB V2. It is independent of any other workstream — no implicit ordering against P0-P4 training-readiness, midstream remediation, or data-readiness packets. V2 work is sequenced **only** by V2-internal dependencies and operator-side prerequisites.

Out of scope:

- Strategy redesign that incidentally touches CLOB (e.g. WS-driven reactive monitor_refresh) — covered as optional Phase 2 strategic slices, not migration mandates.
- Polymarket account / KYC / wallet provisioning — handled by operator outside this packet, but tracked here as Phase 0 prerequisites.
- Adjacent venues (e.g. derivative venues) — Polymarket-only.

## Freeze point

Until Phase 0 evidence files land, **no code change is authorized**. Phase 1 may begin once Phase 0 questions Q1-Q3 (V2 host reachable, py-clob-client-v2 OrderArgs schema, getClobMarketInfo capability) are answered with on-disk evidence. Phase 2 requires Phase 1 + Phase 0 questions Q5 (USDC.e → pUSD bridge path) and Q6 (V1 EOL date) answered.

## Map-maintenance hook

This packet is registered in `docs/operations/AGENTS.md`. New files inside this directory must:

- Carry a date provenance header (`Created: YYYY-MM-DD` + `Last reused/audited:` + `Authority basis:`)
- Be added to the file registry in this `AGENTS.md` if they are durable artifacts (not throwaway scratch)
- Receive a one-line entry in `work_log.md` if they were produced as part of a slice

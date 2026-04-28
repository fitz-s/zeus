# Cross-cuts X1-X4 — Master Verdict

Created: 2026-04-26
HEAD anchor: 874e00cc0244135f49708682cab434b4d151d25d (main)
Status: ALL 4 CONVERGED — pending judge accept

Signed-by:
- proponent @ 2026-04-26
- opponent @ 2026-04-26

Turns: 2 each direction.

---

## 4-row verdict summary

| Cross-cut | Topic | Verdict | Lock |
|---|---|---|---|
| X1 | Signing-surface seam parallelizability (X-MD-1 fold) | PARALLELIZABLE | Triple-belt: NC-NEW-A semgrep + Mid single-emit antibody + Down AST-shape antibody. Contract is AST shape, NOT line numbers |
| X2 | F-012 RED-test audit (20 cards) | 17 Class A + 3 Class B + 0 violators | Class B mints lighter text-artifact antibodies for down-02/04/05; F-012 fully closed |
| X3 | INV-29 closed-enum amendment + planning-lock gate (X-UM-1 fold) | SCHEDULED, GATE-LOCKED | mid-03 implementation requires: (i) INV-29 amendment commit, (ii) compat-map artifact, (iii) topology_doctor.py planning-lock PASS receipt cited in PR |
| X4 | F-011 raw-payload coverage (X-UD-1 fold) | FULLY COVERED | Up cards via up-03 + up-04 + up-05; up-03 EXTENDS with `raw_orderbook_jsonb` for orderbook depth at decision time. Replay test as F-011 acceptance |

## Cross-references

- X1 ↔ Mid R3L3 NC-NEW-A: Up R3L3 mint covers seam single-emit
- X1 ↔ Down R3L3 §A4: V2 SDK contract antibody asserts two-step seam intact
- X2 ↔ Mid R3L3 + Down R3L3 + Up R3L3: 35+ runnable assertion banks per region cover Class A
- X2 ↔ Down R3L3: down-02/04/05 lighter antibodies are R3L3 X4 mint
- X3 ↔ mid-03 yaml: critic_gate extended to require planning-lock receipt citation
- X3 ↔ memory `feedback_grep_gate_before_contract_lock`: planning-lock is Zeus's grep-gate
- X3 ↔ NC-NEW-E (Mid R3L3): RESTING-not-enum negative-antibody enforces grammar-additive only
- X4 ↔ Apr26 F-011 (Zeus_Apr26_review.md:525-540, Phase 3 §1183-1210)
- X4 ↔ up-03 schema: raw_orderbook_jsonb extension is L4 impl-packet detail

## R1L1 cross-region asks (all folded)

- X-UD-1 (Up↔Down F-011 sequencing on D-phase collapse) → folded into X4 ✓
- X-MD-1 (Mid↔Down signing-surface seam) → folded into X1 ✓
- X-UM-1 (Up↔Mid condition_id coordinated migration) → folded into X3 ✓

## L4 deferred (cleanly scoped across all 4 cross-cuts)

- X1: mid-02 + down-01 implementation packets keep AST-shape contract; line numbers may rot but antibodies stay green
- X2: Class B implementations ~3 test files (~60 LOC total); slice cards gain `class: A|B` annotation
- X3: INV-29 amendment commit + compat-map.md + planning-lock PASS receipt before mid-03 lands
- X4: up-03.yaml schema_changes update for raw_orderbook_jsonb + cross-module replay test + replay tooling

## Dependency graph implications

After X1-X4 lock:

```
up-04 (15-col ALTER, owns ALL identity + signing + payload columns)
  ↓
up-03 (NEW table + raw_orderbook_jsonb extension per X4)
  ↓
mid-02 (event-payload writes, anchored on AST seam per X1)
  ↓
mid-01 (cycle_runner CommandBus emission)

up-06 (UNVERIFIED rejection 7-row matrix)
  ↓ lands FIRST (per Up R2L2 Attack-L2-4)
up-04 then proceeds

mid-03 (state grammar amend) ← REQUIRES INV-29 amendment commit + planning-lock receipt per X3
  ↓
mid-04 + mid-05 + mid-06

down-01 (V2 SDK swap) ← seam preservation per X1
  ↓
down-07 (pUSD redemption, gated by Q-FX-1 dual-gate at polymarket_client.py:353)

down-06 (V2 heartbeat, D2-gated on Q-HB cite)
```

## Closure conditions met

- 2 turns each direction (≥2 required) ✓
- All 4 cross-cuts resolved ✓
- 5 disk files committed (X1.md, X2.md, X3.md, X4.md, MASTER_VERDICT.md) ✓
- All 3 R1L1 cross-region asks (X-MD-1, X-UD-1, X-UM-1) folded ✓
- 0 F-012 violators ✓
- Triple-belt seam enforcement locked ✓
- Planning-lock gate for INV-29 amendment locked ✓
- F-011 raw_orderbook_jsonb gap identified + closed ✓

L2-cross-cut closed. Ready for aggregation phase (slice_cards → dependency_graph.mmd → ULTIMATE_PLAN.md).

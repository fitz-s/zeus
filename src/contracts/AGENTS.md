# src/contracts AGENTS — Zone K0 (Kernel)

## WHY this zone matters

Contracts define the **typed semantic boundaries** that prevent cross-layer meaning collapse. When a float crosses from signal→strategy or strategy→execution, these contracts enforce that it carries provenance: what it represents, how confident we are, and whether fees have been deducted.

Without typed contracts, Zeus degrades into "just a bunch of floats" where no one knows whether a probability is raw/calibrated/posterior, or whether a price includes vig.

## Key files

| File | What it does | Danger level |
|------|-------------|--------------|
| `settlement_semantics.py` | Integer rounding contract for all DB writes | CRITICAL — INV-06 enforcement |
| `execution_price.py` | Typed price wrappers (native side, held side) | HIGH — prevents price semantic confusion |
| `edge_context.py` | Edge provenance (source, confidence, costs) | HIGH — INV-12 enforcement |
| `epistemic_context.py` | Cross-layer uncertainty context | HIGH |
| `execution_intent.py` | Entry/exit intent typing | HIGH |
| `alpha_decision.py` | Alpha target declaration | MEDIUM |
| `decision_evidence.py` | Decision evidence bundles | MEDIUM |
| `provenance_registry.py` | INV-13 constant registration | HIGH — cascade safety |
| `reality_contract.py` | External assumption contracts (INV-11) | HIGH |
| `reality_contracts_loader.py` | YAML → RealityContract loader | MEDIUM |
| `reality_verifier.py` | Stale/drift detection for reality contracts | MEDIUM |
| `semantic_types.py` | Base semantic type definitions | MEDIUM |
| `tail_treatment.py` | Tail probability treatment contracts | MEDIUM |
| `vig_treatment.py` | Vig/fee treatment contracts | MEDIUM |
| `expiring_assumption.py` | TTL-bound assumption contracts | MEDIUM |
| `hold_value.py` | Position hold-value computation contracts | LOW |
| `exceptions.py` | Contract violation exceptions | LOW |

## Domain rules

- `assert_settlement_value()` MUST gate every DB write of a settlement value — no exceptions
- Price direction matters: buy_yes prices and buy_no prices are semantically different — always use typed wrappers
- Contracts are **frozen dataclasses** — immutability is intentional
- This is K0 (kernel) — changes here require planning lock and packet discipline

## Common mistakes

- Bypassing `SettlementSemantics` for "just a quick write" → precision drift
- Passing bare floats across signal→strategy boundaries → INV-12 violation
- Adding new contracts without frozen=True → mutability bugs
- Modifying contracts without understanding downstream consumers

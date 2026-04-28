# src/contracts AGENTS — Zone K0 (Kernel)

Module book: `docs/reference/modules/contracts.md`
Machine registry: `architecture/module_manifest.yaml`

## WHY this zone matters

Contracts define the **typed semantic boundaries** that prevent cross-layer meaning collapse. When a float crosses from signal→strategy or strategy→execution, these contracts enforce that it carries provenance: what it represents, how confident we are, and whether fees have been deducted.

Without typed contracts, Zeus degrades into "just a bunch of floats" where no one knows whether a probability is raw/calibrated/posterior, or whether a price includes vig.

## Key files

| File | What it does | Danger level |
|------|-------------|--------------|
| `settlement_semantics.py` | Integer rounding contract for all DB writes | CRITICAL — INV-06 enforcement |
| `calibration_bins.py` | Canonical bin grid partitions (F=2°F pairs, C=1°C points, plus shoulders) — decouples Platt training from `market_events` | HIGH — training/inference alignment law |
| `execution_price.py` | Typed price wrappers (native side, held side) | HIGH — prevents price semantic confusion |
| `edge_context.py` | Edge provenance (source, confidence, costs) | HIGH — INV-12 enforcement |
| `epistemic_context.py` | Cross-layer uncertainty context | HIGH |
| `execution_intent.py` | Entry/exit intent typing | HIGH |
| `venue_submission_envelope.py` | Polymarket V2 submission provenance envelope | HIGH — live venue provenance contract |
| `fx_classification.py` | Operator-selected pUSD/USDC.e accounting enum gate | HIGH — no stringly redemption accounting |
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
- `FXClassification` enum values are the only accepted redemption/accounting gate values; raw strings must be rejected.
- Contracts are **frozen dataclasses** — immutability is intentional
- This is K0 (kernel) — changes here require planning lock and packet discipline

### Settlement Rounding Rules

| Rule | Semantics | Cities | Source |
|------|-----------|--------|--------|
| `wmo_half_up` | `floor(x + 0.5)` — WMO asymmetric half-up | 49 WU/NOAA/CWA cities | ASOS/AWOS integer °F/°C |
| `oracle_truncate` | `floor(x)` — UMA truncation bias | Hong Kong (HKO) | HKO 0.1°C API |

**⚠️ DANGER: `oracle_truncate` 仅限 HKO 等受到 UMA 截断偏见污染的合约使用！**
**严禁用于正常的气象学 P_raw 模拟！**

UMA 截断偏见 (Truncation Bias): 当 PM 合约选项只有整数 Bin (如 28°C/29°C) 且合约
未明确 WMO 四舍五入规则时，UMA 投票者对 28.7°C 的认知是 "没到 29.0 就是 28 的区间"，
事实上执行了 `floor()` 而非 `floor(x + 0.5)`。

验证数据: HKO 14/14 同源日 100% 匹配 (floor) vs 5/14 36% 匹配 (wmo_half_up)。

## Common mistakes

- Bypassing `SettlementSemantics` for "just a quick write" → precision drift
- Passing bare floats across signal→strategy boundaries → INV-12 violation
- Adding new contracts without frozen=True → mutability bugs
- Modifying contracts without understanding downstream consumers

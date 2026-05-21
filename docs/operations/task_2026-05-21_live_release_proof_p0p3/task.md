# Live Release Proof P0-P3 Task Ledger

Created: 2026-05-21  
Branch/worktree: `fix/live-release-proof-p0p3-20260521` /
`/Users/leofitz/.openclaw/workspace-venus/zeus-live-release-proof-p0p3-20260521`

This task starts from latest `origin/main` at
`1d63ad4450085e6b1c0ef7ab84fa92436768e8d9`. The source analysis recorded
`656e73fe5a71893ef7751ac4cac7de6003540ea8`; keep both facts visible because
the branch intentionally follows the newest main while preserving the original
analysis context.

## Source Reference

- `analysis_live_release_proof_p0p3.md`

## Mandatory Work Rule

Before starting a new finding repair, read the corresponding original section
in `analysis_live_release_proof_p0p3.md` and record the section id in this
ledger. Do not start P1 before all P0 rows are either FIXED with tests or
explicitly marked BLOCKED with evidence. Do not start P2/P3 cleanup before P1
runtime/code findings are handled.

## Finding Ledger

| ID | Source section | Required action | Status | Last source re-read | Evidence / next action |
| --- | --- | --- | --- | --- | --- |
| P0-1 | §5 P0-1, §15, §16 step 1/6/7 | Hard release gate proving loaded SHA, DB schema/hash, clean command/redeem state, source/forecast/snapshot freshness, and paper lifecycle proof before live entries | OPEN | pending | Implement release smoke/gate with tests |
| P0-2 | §5 P0-2, §8 row #184/#186/#199 vs #252/#253, §16 step 1 | Unified negRisk tradeability object across scanner, snapshot assertion, persisted reader, executor preflight | OPEN | pending | Add contract object + E2E tests |
| P0-3 | §5 P0-3, §10, §16 step 2 | Live schema fail-closed; only paper/backfill may downgrade no-trade reasons as degraded/excluded from live trust | OPEN | pending | Add live-mode schema guard and tests |
| P0-4 | §5 P0-4, §11, §14, §16 step 3 | Integrated crash/recovery/redeem replay across command/order/trade/position/redeem lifecycle | OPEN | pending | Add deterministic replay harness/test |
| P1-1 | §6 P1-1 | Align decision/no-trade failure policy: live fail-closed, paper/backfill degrade with marking | BLOCKED_BY_P0-3 | pending | Implement with P0-3 |
| P1-2 | §6 P1-2 | Live executable entry requires fresh CLOB/orderbook; discovery fallback cannot authorize live execution | BLOCKED_BY_P0-2 | pending | Implement with P0-2 |
| P1-3 | §6 P1-3, §16 step 4 | Split `REDEEM_OPERATOR_REQUIRED` semantic overload or add `autoretry_eligible` | OPEN | pending | Inspect settlement command schema/state transitions |
| P1-4 | §6 P1-4, §8 family row | Canonical `WeatherFamilyExposure` reducer consumed by evaluator/runtime/no-trade | OPEN | pending | Inspect current family reducer after #252/#253 |
| P1-5 | §6 P1-5, §13, §16 step 5 | Dynamic EffectiveKellyContext proof across live modes/order types | OPEN | pending | Add integration matrix tests |
| P1-6 | §6 P1-6, §10 | Reports treat `unknown_legacy` as non-authoritative | OPEN | pending | Locate settlement/redeem reports and add tests |
| P1-7 | §6 P1-7, §16 step 7 | Required money-path CI, not advisory full sweep | OPEN | pending | Add required workflow/job for money-path replay |
| P2-1 | §7 P2 docs/scaffold drift, §16 step 8 | Refresh authority headers after runtime verification | DEFER_UNTIL_RUNTIME_FIXES | pending | Docs cleanup last |
| P2-2 | §7 topology registries | Runtime-to-registry assertion in CI | OPEN | pending | Add focused registry assertion if no existing one |
| P2-3 | §7 monitor nowcast fail-soft | Counter/alert on persistent nowcast write failures | OPEN | pending | Inspect monitor nowcast writer |
| P2-4 | §7 hardcoded strategy/cluster maps | Move risk metadata to reviewed config with tests | OPEN | pending | Inspect current maps; avoid broad refactor if already config-backed |
| P3-1 | §7 sed-revert antibodies | Convert key antibodies to relationship/replay tests | IN_PROGRESS | §14 | Covered by P0/P1 replay tests as they land |
| P3-2 | §7 pre-existing failures | Track failure IDs centrally | OPEN | pending | Add/update failure registry only if routed |

## Work Order

1. P0-2 negRisk tradeability object and E2E tests.
2. P0-3/P1-1 live schema fail-closed plus degraded compatibility marking.
3. P0-1/P1-2/P1-7 release smoke, business-plane live gate, and required CI.
4. P0-4 lifecycle replay harness.
5. P1-3 redeem operator-required split/flag.
6. P1-4/P1-5/P1-6 targeted authority/test repairs.
7. P2/P3 cleanup and registry/docs alignment.

## Verification Log

| Timestamp | Command | Result |
| --- | --- | --- |
| pending | pending | pending |

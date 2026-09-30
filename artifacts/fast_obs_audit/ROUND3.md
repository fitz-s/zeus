# Round 3 — REQ-20260930-114240-ee2a70

## Scope and constraints
Feature branch only: feat/fast-obs-best-source. No production database writes, live-branch mutation, daemon restart, or deployment. Rebased from ee67338ca onto fetched origin/live 514cdc7d938997bd1e0d9e7ccd5f687edabb73e6; regenerated the schema fingerprint to resolve the sole conflict. Rebase result 65d079fc8b664fc164db8a233c5e3bdc2e744903.

## Execution plan
1. Reproduce current exit-safety and materializer/reader/command failures; repair the side inconsistent with current law, without skipping tests or weakening live safety. Commit/push each validated item.
2. Finish the configured-city national-origin inventory with exact candidate identifiers, access/probe outcomes, pointwise contract-value comparisons and bounded first-availability intervals. A missing credential or no transition is unknown, never evidence of inferiority. Only value-identical and demonstrably faster transports become active fast sources.
3. Extend Tokyo/Toronto to at least 48 exact-time pairs if accessible history permits; preserve all mismatches and demote when contradicted.
4. Diagnose and repair universal US resolver-equivalent precision across all 11 US stations, preserving hourly/SPECI product membership.
5. Exercise real entry and resting-order replacement auction selection downstream of committed observations with nonproduction databases and a fake external venue. Report actual hop times, not enqueue-as-completion.
6. Copy the canonical observation table read-only into a nonproduction database, measure index migration and revision preservation, and write a deployment/rollback runbook; do not deploy.

## Safety and review boundaries
Retain single probability/execution authority, K1 DB ownership and INV-37. Network acquisition stays outside canonical write leases. Data absence may degrade evidence but must not fabricate probability or block an otherwise valid incumbent from serving. No q-vs-market gate or shrink. Compare under exact contract units/rounding at equal station/UTC-valid times; do not substitute nearby observations. After schema migration, retain a revision-capable index on rollback and never remove historical evidence.

## Results
In progress. Only executed measurements and tests will be recorded below.

## Item 1a — exit safety (completed)
Reproduced 18 exit-safety and 7 materializer-suite failures on rebased 514cdc7d9: 25 failed, 864 passed across the four modules. Exit safety now passes all 359 tests (40.16 s); the targeted RED handoff/B2/negative-authority subset passes 30. No skipped or xfailed tests were introduced.

Real defects fixed: final SDK expiry recheck after client/certificate preparation; cycle-owned RED handoff accepted by protective SELL only after existing exact adjacent-event/hash verification; explicit outer transaction and rollback for atomic RED M/I/projection commit; existing canonical RED resting SELL adoption makes no new submission and no longer falls into a fresh-capital replacement path merely because POSTED retired the old submit handoff. Negative tests cover forged writer identity and append/commit failure.

Fixture corrections: command execution atoms are distinct from position-level lifecycle telemetry; terminal cancellation following partial fill remains partial execution. Collateral tests now assert network -> pre-submit lease -> venue -> final SDK receipt lease -> ACK lease. RED scenarios use the actual cycle handoff writer and canonical chain fields; missing handoffs never mint execution authority. Snapshot/lease fixtures satisfy current APIs. A raw external order id without command/size binding does not close exposure on RED status alone.

Validation: round3_exit_green.xml; round3_baseline_feature.xml/log. Baseline comparison reported by operator is confirmed at the same failure names on the newly fetched base; no additional baseline checkout was needed to establish that the old tests failed before these edits.

## Item 1b — materializer/reader/command baseline (completed)
All 531 tests pass (31.93 s), including all seven reproduced baseline failures. Production math and migration code unchanged. Three old migration tests assumed the retired trade_authority_status label should be translated to runtime authority or physically dropped. Current migration preserves obsolete schema columns but retires rows with no current runtime_layer; the tests now assert that behavior and explicit legacy constraints. Four Day0 cases now provide a real current observation plus complete causal provider/ENS vectors and a content-hashed typed residual, rather than assuming an old SimpleNamespace residual alone can sponsor a current carrier. The original owner, source/clock override, and no-likelihood-recompute-under-writer assertions remain.

Item 1 total: 359 exit-safety tests and 531 materializer/reader/command tests pass (890 combined test cases across separate suite runs). No xfail/skip/production-law relaxation was added.

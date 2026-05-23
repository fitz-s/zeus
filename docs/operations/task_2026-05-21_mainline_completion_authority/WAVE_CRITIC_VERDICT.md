# Deterministic Wave — Opus Wave-Critic Verdict (2026-05-22)

Branch `wave/deterministic-strategies-20260522` @ 7530db6b94 (off origin/main 9c152eb654). VERDICT: **FIX_REQUIRED**. Money-safety CLEAN (all 5 strategies shadow/kelly 0, none in live_allowed_keys). Two CRITICAL migration landmines + one MAJOR routing gap block merge-to-live. All pass CI because wave tests build FRESH DBs (CREATE TABLE uses current CHECK); they break only on a pre-existing v28 production DB.

## CRITICAL-1 — stale no_trade_events rebuild guard → silent total shadow-evidence loss on prod
`src/state/schema/no_trade_events_schema.py:~163` `_rebuild_stale_no_trade_events_table` guards on hardcoded substring "14, 15, ..., 26, 27". Prod world DB is at user_version=28 → that substring is already present → guard returns early → rebuild never fires → the 9 new NoTradeReason members are NOT added to the `reason` CHECK. Then `write_no_trade_event` fail-closes per-write (IntegrityError, fail-soft logged at cycle_runtime.py:4353 — no crash, but ZERO shadow no_trade rows captured). The wave's deliverable is that evidence → defeated.
- FIX (make category impossible, not instance): replace the version-substring guard with enum-iteration: rebuild if `any(r.value not in table_sql for r in NoTradeReason) or str(SCHEMA_VERSION) not in table_sql`.

## CRITICAL-2 — stale evidence_tier_assignments guard → hard IntegrityError on tribunal write
`src/state/schema/phase6_evidence_schema.py` `_migrate_evidence_tier_assignments_schema` returns early on structural markers WITHOUT checking the `schema_version IN (...)` CHECK list → v27/v28 table keeps `CHECK (schema_version IN (25,26,27))`. Writer `src/state/evidence_tier_assignments.py:74` passes SCHEMA_VERSION=29; caller `src/analysis/live_readiness_tribunal.py:332` has NO try/except → first PROMOTE/DEMOTE raises IntegrityError. The promotion surface the wave enables hard-fails.
- FIX: guard must rebuild/widen when `str(SCHEMA_VERSION) not in table_sql` (or explicitly check the CHECK list).

## MAJOR — shoulder_impossible_tail_capture routes to Pipeline B (should be A)
`src/analysis/promotion_proof_router.py:44` `_PIPELINE_A_STRATEGY_KEYS` omits it; it emits DeterministicEdgeDecision (§7) → must use §16-A deterministic pipeline. Mitigated (shadow + data-gated → can't mispromote yet) so fix-before-promotion, but do it now.
- FIX: add "shoulder_impossible_tail_capture" to `_PIPELINE_A_STRATEGY_KEYS`. Also reconcile the §16-A list in docs/reference/zeus_strategy_spec.md (it omits it too).

## ANTIBODY (required — this is why CI missed both CRITICALs)
Add a regression test that CREATES a v28-shaped no_trade_events table AND a v27/v28 evidence_tier_assignments table, runs the boot migration (init_schema / migrate_*), then asserts: (a) a new-reason INSERT (e.g. physical_interval_data_gated) succeeds; (b) an evidence_tier_assignments INSERT at schema_version=29 succeeds. Fresh-DB tests cannot catch this class — the antibody must start from the prior version's table shape.

## OPERATOR RUNBOOK (attach to the PR)
Daemon self-migrates user_version (main.py:1980), so no boot halt — but post-merge+restart, verify: the live no_trade_events CHECK contains the new reasons AND a tribunal write at v29 succeeds. Document as a post-merge verification step.

## CLEAN (no action)
Money-safety (5 shadow/kelly 0, live_allowed_keys unchanged); fees via fees.phi (not hardcoded); D6 routing + C-2 key fix compose without collision; data-gating emits no_trade (not crash/fake-edge) per strategy; 253 wave tests + 32 schema-coherence pass on fresh DBs.

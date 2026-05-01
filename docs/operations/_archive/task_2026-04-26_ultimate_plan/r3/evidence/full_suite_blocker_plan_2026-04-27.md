# Full-suite blocker remediation plan — 2026-04-27

Scope: safe, non-operator remediation after R3 G1 verification found full-suite failures that must not be hidden behind targeted R3 green evidence.

Allowed actions:
- Update stale test fixtures to satisfy current explicit metric/slippage/bin-topology laws without weakening production fail-closed behavior.
- Preserve governance attribution (`strategy_key`) only where the candidate reaches strategy evaluation; do not mask invalid bin-topology rejections.
- Fix operator control-plane resume so an explicit `resume` command expires durable entry-pause overrides and clears the fail-closed tombstone rather than leaving entries stuck paused.
- Refresh assumption/test metadata for the Hong Kong HKO floor/truncate exception while preserving `SettlementSemantics.for_city()` as authority.
- Adjust structural linter allowlist only for canonical observation-time schema/ingest boundary files that must carry persisted `local_hour` columns; do not allow arbitrary signal logic to read local-hour fields outside the time-semantics layer.

Forbidden actions:
- Do not fabricate Q1 Zeus-egress or staged-live-smoke evidence.
- Do not transition CutoverGuard to live, activate credentials, place/cancel/redeem live orders, or mutate production DB truth.
- Do not weaken metric identity, SlippageBps, settlement semantics, risk fail-closed, or WS gap laws.

Verification target:
- Focused tests for each remediated failure class.
- R3 targeted/broad aggregate remains green.
- `live_readiness_check.py --json` remains fail-closed until external evidence exists.
- Full-suite sample should progress beyond the remediated failures, but full-suite green is not required/claimed unless actually achieved.

## Expansion — residual no-operator full-suite blockers (2026-04-27)

After targeted R3 gates passed, a repo sample `pytest -q -p no:cacheprovider --maxfail=30` still failed at 30 failures. These are not operator authorization actions, so the allowed follow-up is to remediate safe local correctness/test-contract blockers without live venue side effects and without fabricating Q1/staged evidence.

Expanded scope:
- Add/repair missing non-live settlement rebuild helper expected by authority tests (`scripts/rebuild_settlements.py`) and register it.
- Refresh stale fixtures that insert legacy `settlements` rows without `temperature_metric` while preserving current dual-track law.
- Repair compatibility/math helper seams where tests instantiate objects via `__new__` only to exercise rounding helpers.
- Repair test/runtime seams that should fail closed but also remain unit-testable without installed live SDKs.
- Update packet evidence/docs after verification.

Still forbidden:
- no live submit/cancel/redeem/wrap side effects;
- no CutoverGuard transition to `LIVE_ENABLED`;
- no production DB mutation;
- no Q1/staged evidence fabrication;
- no weakening of settlement semantics, dual-track metric identity, RED fail-closed risk behavior, or source truth boundaries.

Stop conditions:
- a failure requires live credentials, network egress to Polymarket/TIGGE, or production host evidence;
- a proposed fix changes canonical truth/lifecycle/schema semantics rather than stale fixture compatibility;
- a test asserts behavior contradicted by current AGENTS/K0/K1 law and cannot be updated narrowly.

## Expansion — cutover readiness binding hardening (2026-04-27)

Read-only critic verification found that `CutoverGuard` could accept an arbitrary existing operator evidence file when transitioning to `LIVE_ENABLED`, while the G1 readiness contract requires a 17/17 readiness report plus staged smoke. This is a no-operator code hardening gap: it should be impossible to flip the runtime switch with a generic note file.

Allowed follow-up:
- strengthen `src/control/cutover_guard.py` so `LIVE_ENABLED` evidence must be a JSON readiness report proving `status=PASS`, `gate_count=17`, `passed_gates=17`, `staged_smoke_status=PASS`, and `live_deploy_authorized=false`;
- update `tests/test_cutover_guard.py` to use/lock that evidence shape.

Still forbidden:
- no transition of the real runtime state to `LIVE_ENABLED`;
- no creation of fake production Q1/staged evidence;
- no live venue side effects.

## Expansion — active script hardcoded WU key removal (2026-04-27)

Security review found plaintext WU key fallbacks in active transition shell scripts. Although the key is documented elsewhere as a public web key for Weather Underground browser traffic, active scripts exporting a key value create avoidable security-review noise and stale-credential risk.

Allowed follow-up:
- remove plaintext fallback exports from `scripts/resume_backfills_sequential.sh` and `scripts/post_sequential_fillback.sh`;
- fail closed with an operator-supplied `WU_API_KEY` requirement before running WU backfill steps.

Still forbidden:
- no key rotation attempt from this agent;
- no external WU calls;
- no production DB writes.

## Expansion — residual full-suite blocker batch 2 (2026-04-27)

After the first blocker batch, `pytest -q -p no:cacheprovider --maxfail=30` improved to 16 failed / 14 evidence-fixture errors before maxfail. Remaining safe no-operator work is limited to stale unit fixture compatibility and fail-closed contract assertions. This batch may update tests or narrow compatibility shims, but must not weaken live-money safety.

Allowed examples:
- adjust stale tests to current typed boundaries (`SlippageBps`, explicit metric identity, current dual-track schema);
- add defensive compatibility fallback where it does not affect production semantics (e.g. RNG forwarding helpers using legacy `member_maxes` test doubles);
- mark missing external evidence fixtures as explicit skipped/non-current when the evidence artifact is not present;
- update assertions whose old expectation contradicts current fail-closed law.

Still forbidden:
- no production evidence fabrication;
- no relaxing CutoverGuard / WS / collateral / executable snapshot guards;
- no live SDK calls or external data fetches;
- no DB truth mutation outside temp/in-memory tests.


## Expansion — post-resume residual blocker status (2026-04-28)

A resumed full-suite sample after safe residual repairs still stops at 30 failures:

```text
pytest -q -p no:cacheprovider --maxfail=30: 30 failed, 2566 passed, 91 skipped, 16 deselected, 1 xfailed, 1 xpassed, 7 warnings
```

Current residual clusters:

- `riskguard`: several tests expect legacy fallback/projection behavior, while current runtime code requires canonical `position_current` truth and now reports different fail-closed details/levels. This is a contract decision, not a fixture-only edit; do not weaken runtime risk law casually.
- `harvester`: tests expect return keys such as `pairs_created` / `settlements_found`, but the current preflight/fail-closed path returns a different shape. Requires a narrow harvester contract review before source edits.
- Runtime guards / cycle runner: some harness fakes have stale signatures (`get_trade_connection_with_world(mode)`, `save_portfolio` kwargs, cleanup kwargs), and expected telemetry is absent when discovery does not execute. Needs isolated harness update vs runtime-contract decision.
- Rebuild pipeline: remaining settlement unit/unknown-unit tests need review against the new dry-run high-track helper and current settlement semantics.
- Strategy tracker / audit: fixture drift remains around market topology, `strategy_key`, and canonical trade/audit surfaces.

Allowed next action remains: narrow, evidence-backed triage of these clusters with topology navigation and planning-lock evidence before touching high-risk riskguard/harvester/runtime source. Full-suite green is still not claimed, and G1/live deploy remain NO-GO until this is either fixed or explicitly waived by packet authority plus operator evidence.

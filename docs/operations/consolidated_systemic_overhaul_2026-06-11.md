# Consolidated Systemic Overhaul — 2026-06-11
# Created: 2026-06-10 (night session)
# Authority basis: operator directive "不要再追着打地鼠,直接整合从源头解决" — every accumulated
# debt + every known systemic deficiency, consolidated into K structural decisions for one
# complete implementation pass.
# Operator-ratified inputs: /tmp/decomp_audit.md, /tmp/funnel_autopsy.md, /tmp/kelly_stack_audit.md,
# /tmp/deep_verify_report.md, /tmp/hk_settlement_audit.md, /tmp/time_semantics_report.md,
# /tmp/staleness_cycle_audit.md, /tmp/contracts_report.md, /tmp/maker_quote_report.md,
# /tmp/cert_bridge_report.md, /tmp/floor_recal_report.md, /tmp/tradeable_latest_report.md,
# /tmp/complement_law_verdict.md (verdict may land after this file; rebase on it).

## PRIME DIRECTIVES (unchanged, non-negotiable)
- Live trading keeps running; every change lands behind tests; never weaken an honest gate;
  settlement is the only truth; q_lcb + fractional Kelly (single application, equity basis);
  DIRECTION LAW buy_yes ⟺ bin≈forecast / buy_no ⟺ bin≠forecast; mainstream forecasts NEVER
  enter trade judgment (observability only); complement PRICE semantics banned (probability
  complement on certified bounds is the lawful exception; see K2.4 / the complement verdict).
- Relationship tests BEFORE implementation. Each batch independently shippable + committable.
- Daemon restarts are the deployment vehicle; never restart with another agent's half-edited
  hot file (check git status + ast.parse before kickstart).

## WHY THIS FILE EXISTS (the pattern to kill)
Tonight found, one mole at a time: scout stubs poisoning the queue; a download cron with a 12h
dead zone; bounds-less waves clobbering live eligibility; a p=1.0 FDR hardcode; a climate-std
sigma floor; a dropped per-city rounding rule at one seam; TWO mode-decider formulas; TWO
maturity checks (verifier + compiler); a Kelly basis reading spendable cash; a cert wall
demanding the wrong chain's credential; a maker system refusing to quote empty books; an
unregistered runtime table crashing boot; a status file with two writers. ~30 commits, all
real — but each was CAUGHT IN PRODUCTION. The categories below exist so the next hundred of
these die in CI instead.

---

# K1 — TWIN-AUTHORITY ELIMINATION (one rule = one implementation)
Incidents: mode decided by different formulas at proof-side vs fresh-side (93% MODE_FLIPPED
waste); calibration maturity checked in BOTH decision_kernel/verifier.py AND compiler.py with
divergent carve-out tuples (53/h false rejects); status_summary written by two processes;
settlement rounding declared per-bin AND per-city AND hardcoded at the q integrator.
1.1 Systematic audit: enumerate every rule implemented in 2+ places. Method: for each known
    money-path predicate (mode choice, maturity, staleness bounds, q_mode eligibility, fee
    computation, min-order arithmetic, readiness coverage, direction law, spread guard,
    preimage offsets), grep all evaluation sites; any rule with 2+ independent formulas gets
    refactored to ONE shared function consumed everywhere. Deliverable: table of rule →
    sites → shared-function refactor done/why-not.
    [STATUS 2026-06-10: DONE, commit aca801dc77. Table:
     - maturity: TWIN→UNIFIED (shared ALT constant+predicate in verifier.py)
     - relative spread: TWIN→UNIFIED (execution.py dispatches to mode_consistent_ev)
     - min-order shares: TWIN→UNIFIED (desired_shares_for_reserved_notional, float contract)
     - mode choice: single authority (proof-side); fresh seam = fail-closed divergence
       detector, intentionally distinct formula, shared margin pinned (why-not: detector
       role + abort-not-trade failure cost)
     - fee shape: licensed float/Decimal twin, golden-equivalence pinned
     - preimage offsets / direction law / q_mode eligibility / readiness: single source
     - staleness: per-surface heterogeneous by design; freshness_registry central]
1.2 CI antibody where mechanically possible: a test that imports both call sites and asserts
    they dispatch to the same function object (or golden-case equivalence tests fed the same
    fixtures through both paths).
    [STATUS 2026-06-10: DONE, commit aca801dc77 —
     tests/decision_kernel/test_k1_shared_authority_predicates.py (identity + AST
     no-local-formula) + tests/contracts/test_k1_twin_formula_equivalence.py (golden
     matrices: fee float/Decimal, desired-shares, relative-spread both seams).]
1.3 Known instances to verify already-unified (regression-pin): mode hysteresis both seams
    (a8a1c80536), maturity ALT tuple (verifier L945 + compiler L582 — extract ONE shared
    constant + one shared predicate), settlement preimage (1687be9343 contract).
    [STATUS 2026-06-10: DONE, commit aca801dc77. Maturity tuple extracted to ONE shared
     constant+predicate; mode margin AST-pinned (no local literal can fork); preimage
     single-def grep-pinned. Production proof for tradeable-latest read semantics noted:
     Karachi fill 2026-06-10T22:19Z skipped newer bounds-less 06Z posterior (id=1404,
     q_lcb NULL) and served certified 00Z row (id=1456) — canonical golden case.]

# K2 — TYPED CONTRACTS AT EVERY BOUNDARY
Incidents: ChainState 'external_operator_closed' written by reconcile, unknown to riskguard
(crash loop); scout intent dict poisoning the queue; rejection reasons as free-text strings
(every funnel query tonight was substr-matching prose).
2.1 Rejection-reason taxonomy: no_trade_regret_events.rejection_reason becomes a typed
    registry (src/contracts/rejection_reasons.py): every reason is a declared enum member
    with category (HONEST_MARKET / HONEST_DATA / DESIGNED_GATE / ARTIFICIAL_SUSPECT), a
    docstring, and optional structured detail schema. Writers emit registry members (string
    value preserved for DB compat — `REASON.value`); a CI test fails on any emit site using a
    string literal not in the registry (grep-based AST check). The standing funnel report
    (K5.2) groups by category for free.
2.2 Enum writer⊆consumer relationship tests, generalized from the ChainState fix: for every
    enum crossing a process/DB boundary (ChainState, CandidateLifecycleState, ReversalReason,
    replacement_q_mode, readiness status, calibration authority, order state machine), one
    test asserts writer-producible set ⊆ enum ⊆ every consumer's handled set. Inventory the
    enums from architecture/money_path_objects.yaml.
2.3 Pipeline file contracts (src/contracts/replacement_pipeline_files.py) — extend to ALL
    cross-daemon file kinds still unvalidated: manifest JSONs, floor artifact
    (state/settlement_sigma_floor.json — loader must validate schema+provenance fields),
    status files, scout intents (consumption side when built).
2.4 Complement-law codification: whatever /tmp/complement_law_verdict.md decides, encode it:
    the law doc text + the relationship test that the banned category (edge/entry/fill priced
    from complement values) is unconstructible. If verdict = revert, the empty-book maker
    lane is already out; if carve-out, the non-crossing-bound-only constraint is pinned.
    [STATUS 2026-06-10: DONE at law level. Verdict = LEGAL-WITH-CARVE-OUT
    (/tmp/complement_law_verdict.md). NC-09 scope+carve_out encoded in
    architecture/negative_constraints.yaml + FM-09 cross-ref in
    architecture/ast_rules/forbidden_patterns.md; pinned by
    tests/engine/test_maker_quote_empty_no_ask.py::test_quote_edge_non_positive_still_rejects
    + tests/test_probability_complement_ast_guard.py. CARRY-FORWARD to K5/K4: the verdict's
    empirical leg (in-DB mirrored-book check) could not run — executable_market_snapshots was
    empty in the probe and market_price_history tracks only YES tokens; add the
    belt-and-suspenders empirical check (complementary book capture or first real
    mint-matched fill observation) when building the K5 organs.]

# K3 — PROCESS TOPOLOGY (kill the god process)
Authority: /tmp/decomp_audit.md (23 jobs classified; operator ratified harvester/redeem
independence and "market truth must survive order-daemon death").
3.1 Phase 0: scout↔warmer IPC → DB table new_family_scouts (zeus-world). No process change.
3.2 Phase 1: substrate cursor → single-row table. No process change.
3.3 Phase 2: market_discovery + edli_market_substrate_warm → new com.zeus.market-truth daemon
    (entrypoint src/market_truth_main.py mirroring ingest_main.py; dual_run_lock for the
    substrate refresh; plist + deployment_freshness coverage).
3.4 Phase 3: new_listing_scout joins market-truth daemon.
3.5 Phase 5 (defer Phase 4 WS channel unless time allows): mainstream warm cache → DB-backed.
3.6 **[RESCOPED by operator directive 2026-06-10 ~22:55Z: "完全抛弃redeem" — Zeus must NEVER
    submit redeem transactions again. Third-party auto-redeem takes over. Zeus's remaining
    duties: correct ACCOUNTING + "Confirm pending deposit" handling.]**
    a) Kill the redeem-submission lanes: ZEUS_AUTONOMOUS_REDEEM_ENABLED set to 0 in
       com.zeus.live-trading.plist (done 22:55Z, applies at next load; backup
       /tmp/com.zeus.live-trading.plist.bak_redeem_pivot). Add the code-level antibody:
       submit_redeem and any redeem-submitting scheduler job hard-refuse (raise) unless an
       operator-only override env is set — a flag flip alone must not be the only barrier.
       Tests pin: no codepath reachable from daemon schedulers can broadcast a redeem tx.
    b) EXTERNAL-REDEMPTION ABSORPTION (extends the operator-activity family — this IS the
       K6.9 "fourth variant as a table row"): third-party redeem looks like (i) winning
       position balance → 0 on-chain without a Zeus order/redeem, (ii) USDC inflow to the
       proxy wallet. Detect both, book as EXTERNAL_REDEMPTION with provenance (tx hash,
       amount), settle the corresponding settlement_commands rows to a terminal
       EXTERNALLY_REDEEMED state (new enum member — writer⊆enum⊆consumer test per K2.2),
       and do NOT raise ghost/drift findings for them.
    c) CONFIRM-PENDING-DEPOSIT organ (NEW capability; no deposit code exists in src/ today):
       detect proxy-wallet USDC that needs the Polymarket deposit-confirm/sweep step to
       become tradeable collateral, perform that step (it is NOT a redeem — it remains in
       scope), and book the ledger transition. Cadence + staleness bound from the time
       registry; receipts in collateral_ledger_snapshots.
    d) Accounting truth for the legacy rows: 19 REDEEM_REVIEW_REQUIRED + 1 REDEEM_TX_HASHED
       (the $19, tx 0xd4780c8c... broadcast 22:52Z BEFORE the directive) — reconcile each to
       chain truth (confirmed / externally-redeemed / zero-balance) ONCE, terminally, with
       no resubmission. The rest of the original 3.6 (state-truth daemon for harvester
       OBSERVATION + wrap cycles + chain_sync) stands — observation yes, submission never.
3.7 Single-writer audit per truth surface: status_summary done; repeat for every state/*.json
    + heartbeat files (one writer each, asserted by a registry test).
3.8 WAL/db-locked chronic noise: the exit-fill projection repair loop (trade_fact_id=28 class)
    and "Failed to log trade exit: database is locked" — root-cause the writer contention
    (likely cross-daemon long write transactions); fix with bounded busy_timeout + write
    batching or single-writer queueing on zeus_trades hot tables. Measure before/after lock
    error rate. (This was muted in monitors, never fixed.)

# K4 — MEASUREMENT-BASED CONSTANTS (no more guesses)
Authority: src/contracts/time_semantics.py registry (21 entries, 13 relations, 10 basis=guess).
4.0 **[OPERATOR-ESCALATED 2026-06-10 ~22:45Z — FRONT-RUN within K4; coordinate restart]**
    Taker-only execution root cause + REST-THEN-CROSS policy. Evidence + measured KM
    fill-hazard curve: docs/evidence/maker_taker/2026-06-10_taker_only_root_cause.md.
    All 6 live fills are FOK crosses paying 4.0% of notional to spread (books up to 8¢
    wide); cause = (a) p_fill_maker=0.10 flat GUESS (measured KM on our own 108 resting
    facts: 0.188@15min all-band, 0.39@120min, and 9/9 fills in the [0.4,1.0) price band)
    handicapping maker EV ~10×, and (b) the one-shot maker-XOR-taker decision shape, which
    cannot represent rest-then-cross at all — that is the design failure, not the constant.
    Implement the K-decision from the evidence doc: default entry = post_only GTC at
    min(bid+tick, reservation, mint-boundary cap) with a measured escalation deadline
    (start 120 min, registry constant basis=MEASURED); at deadline, unfilled + re-certified
    edge ≥ ts → taker cross; edge gone → cancel + receipt the decay. Taker-immediate only
    for fleeting-edge / near-event-end / one-sided-book lanes (registry constants).
    Antibody: no taker cross may exist while an unexpired same-family maker rest exists.
    Receipts carry mode + deadline so the settlement loop recalibrates the hazard curve and
    λ (K4.3) from realized maker markout. Subsumes the old "verify N≥30 trigger fires"
    framing — the trigger's measurement population (25-min deep-longshot rests) was the
    wrong conditioning; fix the population, not just the constant.
    [STATUS 2026-06-10: IMPLEMENTED (deploys at next restart window).
     - Policy: select_rest_then_cross_mode (mode_consistent_ev.py) — REST_DEFAULT
       post_only GTC; taker-immediate lanes ESCALATED_AFTER_REST / EVENT_END_NEAR /
       FLEETING_EDGE (0.15, honest GUESS w/ measurement plan) / MAKER_INADMISSIBLE;
       antibody lane HOLD_REST_IN_PROGRESS (chosen_ev=-inf: NO new order while an
       unexpired same-family rest exists). p_fill 0.10 GUESS retired -> 0.39
       MEASURED@120min (provenance only; the policy decides).
     - Registry: maker_rest_escalation_deadline 2.0h MEASURED +
       taker_immediate_event_end_floor 3.0h DERIVED w/ MUST_EXCEED relation.
     - Adapter: proof seam dispatches the policy; family inputs from venue truth
       (_family_rest_state: open rest blocks family; cancelled-unfilled >= deadline
       licenses the escalation cross); policy + deadline travel proof -> receipt ->
       actionable; fresh-mode witness SUBORDINATED to the policy (legacy proofs
       witness MAKER, fail-closed migration); FRESH_BOOK_EV_FAVORS_CROSS tripwire
       scoped to legacy proofs.
     - Escalation job: src/execution/maker_rest_escalation.py + 5-min scheduler job
       (cancel-only deadline owner; GTC rests had NO other TTL owner). Re-cert is
       the FULL standard pipeline on the next cycle, never shortcut math.
     - #28c bundled: edli_command_recovery 3-min job (stuck-SUBMITTING owner for
       the edli lane; legacy cycle_runner was the only sweep before).
     - Tests: 19 policy + 15 adapter-seam + 8 escalation-job relationship tests.
     - Maker fee bonus cited: maker pays zero taker fee -> saves spread (~4.0%
       notional measured) PLUS fee (1.0-2.5% at our band) per avoided cross.
     - DEFERRED: hazard-curve/lambda auto-recal from escalation receipts (K4.3).]
4.1 For each basis=guess entry: build the measurement (from existing telemetry where possible)
    and replace guess→measured with the evidence recorded in the registry. Priority: maker
    p_fill by distance-from-touch (fill_tracker resting facts; auto-recalibration trigger at
    N≥30 already coded — verify it fires and persists), gamma/CLOB API latencies (extend the
    p95 pattern), WU publication latency per city (config/wu_obs_latency.json exists — wire
    into registry), anomaly TTLs, queue drain throughput vs scope count.
4.2 Sigma floor growth path: per-city cohorts activate automatically when no-leak n ≥
    threshold (currently metric pools; the fit script supports tiers — add the scheduled
    refit job: weekly, in the forecast-live daemon, with provenance).
4.3 MAKER_ADVERSE_SELECTION_LAMBDA=1.0 (SL-4): recalibration trigger from settled maker
    fills (≥10), registry-tracked.
4.4 Platt LOW pin + cohort growth: surface in a weekly report which (city,metric) cohorts
    newly qualify; promotion stays operator-gated. (The cert bridge made this non-blocking
    for the replacement chain; this item is about the LEGACY lane's health.)

# K5 — STANDING OBSERVABILITY ORGANS (nothing discovered-in-production again)
5.1 Pipeline liveness (scripts/verify_pipeline_liveness.py) → a scheduled job in EVERY
    relevant daemon's scheduler (or the market-truth daemon once split): each stage declares
    expected cadence (from the time registry); stall ⇒ ERROR log + push notification within
    minutes. Include: download cron firings, instrument capture, posterior production,
    readiness fresh count, candidate-bearing receipts, submit-stage activity, venue commands.
5.2 Daily funnel decomposition report (the manual autopsy, automated): markets → books →
    tradeable posteriors → events → candidates → per-stage kills (by K2.1 category) → submits
    → fills. Daily 09:25Z alongside settlement_guard (09:15) + shadow comparator (09:20).
    Artifact: state/funnel_report.json + markdown in docs/evidence/.
5.3 Shadow comparator day0 pairing: day0_remaining_day_q_enabled is now ON (shadow-only);
    verify enriched day0 receipts flow with real q + the comparator pairs them; fix the
    empty replacement_shadow_decisions audit table (kelly audit found it never populated).
5.4 Post-fill verification organ: scripts/verify_fill_e2e.py — fix its schema drift (it
    broke on executable_market_snapshots columns), extend with the corrected-math re-check
    (the Paris/HK manual verifications, automated), run automatically on every FILL event +
    push the verdict. Mainstream consensus may be used HERE (post-fill observability tripwire
    — operator law: never admission).
5.5 Wrong-trade drill ledger: docs/evidence/wrong_trades.md — Milan (two-measures), Paris
    (sigma-floor tail), each with the antibody that killed its category; every future wrong
    trade gets a row + an antibody before trading resumes in that class.

# K6 — ENUMERATED DEBT CLOSURE (the one-liners and cleanups)
6.1 src/main.py:6651 bypasses SettlementSemantics.for_city() (HK audit) — route through the
    contract.
6.2 SL-3 dead stub _forecast_snapshot_probability_and_fdr_proof — delete.
6.3 SL-1/SL-2 canonical-path buy_no stubs (market_analysis_family_scan.py:111 bare continue;
    market_analysis.py:1118 _bootstrap_bin_no p=1.0): the canonical path is not live; either
    implement the native-NO bootstrap properly (preferred if cheap — mirror the replacement
    derivation) or mark both sites with loud NotImplementedError-on-live-flag guards so the
    lane can never silently trade wrong if the replacement flag is turned off.
6.4 day0_metric_fact: schema-only table with no writer — wire the day0 fast-obs lane to write
    it (it was designed as the day0 observation fact store) or drop it from schema+registry.
6.5 Branch/worktree hygiene: local main consolidation (origin/main d13d93c37a is a squash —
    local fix/opportunity-book-selector diverges in history, matches in content+30 commits;
    decide with operator: merge branch → main via PR or reset main to branch; single-worktree
    end state; delete stale worktrees zeus-binselect/zeus-decomp//tmp/zeus-day0-pr).
    DO NOT switch branches under live daemons — coordinate a restart window.
6.6 CI: the two non-required failing checks from the #403 era (selected-relationship-tests,
    money-path-integration) — triage to green or formally quarantine with PEF entries.
6.7 PEF registry: re-verify PEF-2026-05-27-D2-HARVESTER scope after tonight's harvester
    condition_id fix (agent says different surface — confirm and update review_by).
6.8 Carry-forward data quality: KMA forecast-latest endpoint (Seoul/Busan precision);
    icon_global Jeddah/SF fusion-weight anomalies; oracle staleness-laundering HK-cousin
    watch — each gets a measurement/probe, not a guess.
6.9 Operator-activity contract: unify the three shared-wallet antibodies (out-of-domain ghost
    auto-resolve; in-domain unfilled acknowledged ghost; acknowledged external-close
    absorption with double-count repair) into one documented OperatorActivity module with a
    single decision table; the fourth variant should be a table row, not an incident.
6.10 Receipt price field: enriched receipts carry q/lcb/score but not the admitted execution
    price — add it (day0 evaluation needed it and had to reconstruct from snapshots).

## EXECUTION DISCIPLINE FOR THE IMPLEMENTING AGENT
- Order: K1.3/K2.1/K2.2 (foundations) → K5 (organs) → K3 phases (topology, each phase = one
  batch + restart window) → K4 (measurements) → K6 (closures interleaved as small batches).
- Each batch: relationship tests red-first → implement → full affected suites → commit (no
  Co-Authored-By trailers) → push → restart only the affected daemons in a clean window.
- Known pre-existing test failures (do NOT fix, do NOT count as regressions): see
  /tmp/preexisting_test_triage.md + per-report baselines (taker_execution_law 6, lifecycle
  flash-crash, certificate_ledger causal mismatch, pnl_flow 16, conftest writer-lock antibody
  needs ZEUS_DISABLE_WRITER_LOCK_ANTIBODY=1 for isolated runs).
- Protected: config/settings.json (operator domain; flag flips only on operator word),
  eliminated gates stay dead (mainstream admission, stale_book, tiny_live, concentration cap,
  old PRICE_MOVED ceiling, curPrice prefilter), live DBs only via sanctioned paths.
- The live daemons during this work: live-trading + forecast-live + riskguard are RUNNING and
  trading. Restart vehicle rules in PRIME DIRECTIVES.

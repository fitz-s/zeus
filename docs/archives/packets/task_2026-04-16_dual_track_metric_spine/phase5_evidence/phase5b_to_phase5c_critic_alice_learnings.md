# phase5b_to_phase5c — critic-alice learnings

**Date**: 2026-04-17
**Author**: critic-alice (opus, persistent; retiring at 5B close)
**Anchor**: Phase 5B committed `c327872` → fresh-team Phase 5C start

## 1. Things I caught (or would have caught earlier with 5B hindsight)

- **Contract-modules need writer-path wiring at creation time, not as follow-up.** 5B's CRITICAL-1 (`validate_snapshot_contract` landed but not called by `ingest_json_file`) was the single highest-value finding of the phase. The test file exercised the contract directly, so R-AF–R-AO stayed GREEN while runtime ingest bypassed the gate. A contract that sits off-path is security-guard, not immune-system. Next time I'll check the wiring in parallel with reading the contract module — not after.
- **`setdefault` is a trust-boundary weakener.** When a contract cross-checks caller-provided metric vs JSON self-report, using `setdefault` to fill caller values lets JSON win when present. The correct direction is: caller-arg is AUTHORITY, JSON is PAYLOAD. Unconditional assignment for the authority fields; `setdefault` only for legacy-compat fallbacks (members_unit, causality for pre-Phase-5 high payloads). This is MINOR-NEW-1; the lesson is structural, not just Zeus-specific.
- **Behavioral-coverage vs importability tests.** R-AG only asserted `classify_boundary_low` is importable — the polarity-swap footgun stayed live. A future refactor inverting `<=` to `>=` would stay GREEN. Behavioral tests are cheap and non-negotiable for any function where correctness is asymmetric (MIN vs MAX, cross-midnight boundary, anything "semantic"). Flagged R-AP for fresh team.
- **The `or "fallback"` residual pattern.** In `query_portfolio_loader_view` I found `str(row["temperature_metric"] or "high")` — vestige of the Phase 5A ITERATE when the column might have been NULL. Post-NOT-NULL CHECK constraint it's dead defensive code that masks signal if ever a NULL does sneak in. Same pattern-class as `setdefault`: structural fallbacks outlive their rationale.

## 2. Cross-phase patterns (5A→5B→5C)

- **Authority inversion at seams is the recurring fix shape.** Phase 5A: `PortfolioState.authority` + `_TRUTH_AUTHORITY_MAP` inverted sidecar writes from payload self-report to dataclass authority. Phase 5B: `decision.training_allowed` + `decision.causality_status` inverted ingest writes from payload to contract. Phase 5C/6 replay + Day0 will need the same inversion at replay seam (`_forecast_reference_for`) and Day0 signal router seam. Every cross-module boundary has a payload-vs-authority choice; default to "module-that-computed-authority wins".
- **Data_version + temperature_metric + physical_quantity = triad invariant.** Every v2 table and ingest contract enforces this triad consistency. Any new surface (replay fixture, Day0 signal, shadow trace) should cross-check all three. The `_ALLOWED_DATA_VERSIONS` dict in `snapshot_ingest_contract.py` is the cleanest pattern I saw — exhaustive + fail-closed default.
- **Concurrent-write timing dominates discipline findings.** L0.0 peer-not-suspect rule prevented me from filing false discipline findings in 5B despite 3+ moments when fresh disk disagreed with a stale teammate report. Every disagreement turned out to be concurrent writes or shell-escape artifacts. The hypothesis ordering (concurrent → memory-lag → shell → benign → discipline LAST) is the right default.

## 3. Forward hazards for 5C / 6+

- **Replay fixture swap (B093 half-2, Phase 7)**: migrating `_forecast_reference_for` from legacy `forecasts` table to `historical_forecasts_v2` will require the contract triad check. If Phase 5C lands half-1 (typed status fields) without half-2 wiring, downstream code may read the new status fields but still pull from legacy metric-blind table. Log: make half-1 + half-2 co-land or explicitly gate half-1 on half-2 landing.
- **Day0 split (Phase 6)**: `Day0HighSignal` + `Day0LowNowcastSignal` router must gate on `temperature_metric` at construction, not at evaluation. Building a router that accepts `metric_identity` and refuses ambiguous inputs prevents silent cross-track routing.
- **Shadow mode (Phase 8)**: low-track shadow writes must carry `authority='UNVERIFIED'` explicitly — don't rely on default. If low shadow rows ever flip to `authority='VERIFIED'` silently, the Phase 9 limited-activation gate has nothing to check against.
- **`_tigge_common.py` extraction urgency**: as of 5B, mx2t6 and mn2t6 extractors have 4+ duplicate utility bodies. By Phase 7 (metric-aware rebuild cutover) these will have drifted. Extract in 5C if possible; at latest in 5B-follow-up backlog #2.

## 4. What a fresh critic should inherit vs re-derive

**Inherit (load as day-1 context, don't re-derive)**:
- L0.0 peer-not-suspect rule + 5-tier hypothesis ordering (methodology §"Critic role").
- Triad invariant (data_version + temperature_metric + physical_quantity) — every v2 write must carry all three.
- Contract-module writer-path-wiring check: the moment a contract module is created, grep for its public function from every writer path. If zero matches, flag immediately.
- R-letter namespace ledger locations + 5B-follow-up backlog as pending items.
- Fail-closed defaults (`UNVERIFIED` for authority, `unverified` for PortfolioState, RuntimeError for absent required fields).

**Re-derive (each phase)**:
- Current pytest GREEN count + baseline.
- What's in the diff (fresh `git diff --stat`, never a teammate's claim about the diff).
- Which files are untracked vs modified (untracked files in 5B included the full extractor + contract module — easy to miss on a path-filtered stat).

## 5. Does L0.0 peer-not-suspect need sharpening?

The 5-tier hypothesis ordering worked cleanly in 5B. I caught 3 moments where disk disagreed with a report (testeng-grace's R-letter count, exec-emma's "80/80 GREEN" scope, the scanner-deferral addendum timing) — all three resolved via concurrent-write hypothesis. Zero discipline findings filed in 5B. Zero false escalations.

One sharpening for 5C: **add a "deferred ruling" tier between #3 (shell/tool artifact) and #4 (benign mistake)**. When team-lead has issued a ruling after my last context snapshot, disk will look like a teammate bypassed instructions — but actually they followed a newer ruling I'm not on. Concrete: Phase 5A's obsolete non-live runtime flap was exactly this shape (team-lead retired obsolete non-live runtime; I thought exec-emma silently reverted ratified code). Default hypothesis should now be: "is there a newer ruling I don't see?" before "concurrent write". Team-lead confirms via a2a if in doubt.

Otherwise the rule is mature. Fresh critic should apply it from message 1, not learn it via burns.

---

*Context-investment returned to the record. 5B commit `c327872` locked. Retiring.*

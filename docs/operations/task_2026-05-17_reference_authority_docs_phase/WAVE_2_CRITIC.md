# WAVE 2 Critic Report

Generated: 2026-05-16 (fresh-context sonnet critic, no prior session state)
Branch: feat/ref-authority-docs-2026-05-17
Scope: commits a04e32bd49 / c73690809e / 45ce3b217d / 1bbb27200d (Part A) + 25d7217654 / 467f91f8ae (Part B)

---

## VERDICT: ACCEPT_WITH_FOLLOWUPS

---

## Per-Probe Disposition Table

| Probe | Rule | Commit(s) | Result | Evidence |
|---|---|---|---|---|
| Rule 1 SURGICAL | <50 lines/drift | All 6 | PASS | Part A: 13/7/4/6 lines changed. Part B: 332 new (new file, justified). 467f: 44+/-2 (3 files, Part B finalization). No rewrite violation. |
| Rule 2 ESSENCE | Added lines justified | All 6 | PASS | Sampled: backfill_uma lifecycle change (required by loader schema), hazard_badge definitions (must exist before use), date string updates (4 date-only changes, 0 net lines). All additions distill insight. |
| Rule 3 ATOMIC | One file per commit | a04e / c736 / 45ce / 1bbb | PASS | Each Part A commit: 1 file changed, confirmed via `git show --stat`. |
| Rule 4 PROVENANCE (OLD matches pre-commit) | 3 sampled drifts | Multiple | PASS | Verified: (1) backfill_london lifecycle=packet_ephemeral pre-commit confirmed via `git show a04e32bd49~1:architecture/script_manifest.yaml`. (2) cc5081d65d date 2026-05-09 confirmed via `git show cc5081d65d --format="%ci"`. (3) src/contracts/world_view/settlements.py MISSING confirmed via filesystem. |
| Rule 5 STOP CONDITION | Post-commit citations exist | Part A files | PASS (with 1 exception — see Finding #1) | test_topology, script_manifest, topology_v_next: all cited paths/dirs verified. source_rationale: dr33_live_enablement path INCORRECT (see below). |
| Drift reduction | topology_doctor errors cleared | All Part A | PASS | Post-wave: 0 script_* errors (was 12+), 0 source_unknown_hazard (was 2), 0 source_rationale_stale (was 1), 0 script_promotion_candidate_expired (was 1). `--navigation --strict-health` exits 1 only due to pre-existing out-of-scope errors (docs:docs_registry_unclassified_doc ×491, source_rationale_missing ×50). |
| Part B: verifier spec compliance | read-only, rate-limit, dry-run, 60s timeout | 25d7217654 | PASS | REQUEST_TIMEOUT=60, RATE_LIMIT_DELAY=1.0, --dry-run flag, urllib read-only (no Polymarket writes). Confirmed at lines 46-47, 216, 248, 273. |
| Part B: UNMAPPED legitimacy | 23 contracts unmappable | 467f91f8ae | PASS | SETTLEMENT_SOURCE_* (×15): require active token_id for market resolution lookup — correct. FEE_RATE_WEATHER, MAKER_REBATE_RATE: require live token for /fee-rate — correct. TICK_SIZE_STANDARD, MIN_ORDER_SIZE_SHARES: require /book with live token — correct. RATE_LIMIT_BEHAVIOR, RESOLUTION_TIMELINE: require manual docs review — correct. Classification is accurate. |
| Part B: last_verified provenance | NOAA_TIME_SCALE + WEBSOCKET_REQUIRED | 467f91f8ae | PASS | Commit body cites api_called (GET api.open-meteo.com/v1/forecast, TCP to ws-subscriptions-clob.polymarket.com:443) and result (timezone=GMT, TCP succeeded). VERIFIER_REPORT.md confirms timestamp 2026-05-17T04:52:04+00:00 for both. |
| Audit-of-audit spot-check (5 random claims) | Worker self-reported all 5 rules PASS | All | PASS WITH 1 EXCEPTION | 4/5 independently verified. Exception: Rule 5 STOP CONDITION claim for source_rationale is partially false (see Finding #1). |

---

## Finding #1 — MINOR: source_rationale dr33_live_enablement path is wrong archive location

**Severity: MINOR**

The commit `c73690809e` updated `architecture/source_rationale.yaml` line 41 and 44 to point to:

```
docs/operations/task_2026-04-23_live_harvester_enablement_dr33.archived/plan.md
```

This path does NOT exist on disk. The actual archive location is:

```
docs/operations/archive/2026-Q2/task_2026-04-23_live_harvester_enablement_dr33/plan.md
```

Confirmed: `test -f <.archived path>` → MISSING. `test -f <archive/2026-Q2 path>` → EXISTS.

Root cause: SCOUT_0B_DRIFTS.md drift #1 stated `"folder archived as task_2026-04-23_live_harvester_enablement_dr33.archived"` — this was wrong. The actual archive is a subdirectory under `docs/operations/archive/2026-Q2/`. The worker followed the SCOUT's incorrect archive-strategy claim without verifying the target path's existence post-edit.

The comment lines (41-43) have a split: line 41 uses `.archived/` (wrong) while lines 42-43 use `archive/2026-Q2/` (correct), creating internal inconsistency within the same 4-line block.

topology_doctor does NOT validate write_route key values (only tracks source file existence for `source_rationale_missing`), so `--strict-health` passes silently despite the dead path.

**Why this matters:** The `dr33_live_enablement` key is a documentation path used for provenance tracing. A broken path defeats the purpose of the drift fix — the drift category was dead-path-ref and the replacement path is also dead.

**Fix for WAVE 3:** Update `source_rationale.yaml` line 41 and 44:

```yaml
# - docs/operations/archive/2026-Q2/task_2026-04-23_live_harvester_enablement_dr33/plan.md
...
dr33_live_enablement: docs/operations/archive/2026-Q2/task_2026-04-23_live_harvester_enablement_dr33/plan.md
```

Grep-verify before committing: `test -f docs/operations/archive/2026-Q2/task_2026-04-23_live_harvester_enablement_dr33/plan.md`

---

## Finding #2 — MINOR: topology_doctor --strict-health cannot be run standalone

The probe instruction in the operator brief specified:

```
PYTHONPATH=. python -m scripts.topology_doctor --strict-health
```

Running this command without `--navigation` prints usage and exits with code 2 (argument error). The correct invocation is `--navigation --strict-health`. This does not affect the validity of WAVE 2 changes, but the probe instruction itself was defective. Post-wave `--navigation --strict-health` exits 1 (pre-existing out-of-scope errors only; no WAVE 2 target errors remain).

---

## What's Good

- All 4 Part A commits are genuinely surgical: largest is 13+/7- lines on a 12-drift file.
- PROVENANCE OLD quotes independently verifiable against pre-commit git state.
- Deferred drift #12 (learning_loop) reasoning is sound: topology_doctor false-positive on `dict.update()` vs SQL mutation. Deferral is correctly documented.
- test_topology drift #5 self-corrects SCOUT's claimed date (2026-05-17) to actual git evidence (2026-05-14). This is correct behavior per §8.5 Rule 4.
- Part B verifier script is clean: proper rate-limit, timeout, dry-run, read-only enforcement, and provenance on the 2 verified contracts.
- 23 UNMAPPED classification is accurate; no obviously automatable contracts were incorrectly deferred.

---

## WAVE 3 Action Required

1. Fix `source_rationale.yaml` line 41 and 44: replace `.archived/` path with `archive/2026-Q2/` path (verified exists). Single-line surgical edit, §8.5 compliant.
2. No other blocking items.

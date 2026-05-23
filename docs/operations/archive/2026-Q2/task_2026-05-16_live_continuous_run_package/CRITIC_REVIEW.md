# Critic Review: Live Continuous Run Package Plan

Created: 2026-05-16  
Review target: `LIVE_CONTINUOUS_RUN_PACKAGE_PLAN.md`  
Verdict: REVISE CLEARED AFTER PLAN UPDATE

## Critic Question

Does the plan actually remove the current live continuous-run blockers, or does it merely add more process and observability around a broken runtime?

## Attack 1: The plan could overfocus on `entered` and miss broader event-status mixing.

Assessment: addressed.

The plan explicitly defines the root cause as untyped `payload.status`, not the single value `entered`. It requires an exit-event-only extraction rule, a negative relationship test for non-exit `status="entered"`, and a positive preservation test for real exit states such as `sell_pending`. It also includes `query_position_current_status_view`, which the Spark critic flagged as a second same-pattern site.

Required implementation gate:

- The fix must not be `if status == "entered": ignore`. That would fail this critic. It must be event-domain based.

## Attack 2: The plan might claim live readiness while code is still running from a dirty non-main worktree.

Assessment: addressed.

The plan makes runtime commit attestation a mandatory acceptance criterion. It names the structural problem: launchd is path-based and points to a mutable worktree. It requires health probe output comparing runtime commit with expected mainline commit.

Required implementation gate:

- A future PR must expose runtime commit/dirty status in a probe or heartbeat before any final live-ready claim.

## Attack 3: The DB lock issue could be hand-waved as harmless because SQLite degrades gracefully.

Assessment: mostly addressed.

The plan distinguishes degrade-not-crash from acceptance. It requires no repeated critical write lock failures during a 10-15 minute window. This is stronger than simply noting that `connect_or_degrade` returns `None`.

Required implementation gate:

- Acceptance evidence must include both `lsof` holder classification and a log window proving collateral heartbeat is not repeatedly failing closed.

## Attack 4: Source health could be incorrectly merged into forecast-live readiness.

Assessment: addressed.

The plan explicitly separates forecast readiness from source-health probe freshness. It names writer ownership in `ingest_main` / `source_health_probe` and says `forecast_live_daemon` is not the writer.

Required implementation gate:

- Final acceptance must show source health freshness or controlled degradation separately from `check_data_pipeline_live_e2e.py`.

## Attack 5: launchd KeepAlive could create a crash loop and make live worse.

Assessment: conditionally addressed.

The plan does not blindly mandate `KeepAlive=true`; it requires a deterministic policy. If keepalive is enabled, throttle/minimum runtime and a non-trading-safe restart test must exist. If it remains disabled, an external supervisor proof is required.

Required implementation gate:

- The implementation must include crash-loop containment or an explicit external-supervisor proof.

## Attack 6: The plan could still be too broad to implement safely.

Assessment: acceptable as a follow-up package, not a single patch.

This is a cross-module live package. The plan properly splits into phases: semantic loader fix first, runtime attestation second, launchd third, DB/source health after. It does not authorize broad edits without separate topology admission.

Required implementation gate:

- Phase B should be the first code PR slice because it removes the current RiskGuard/live cycle crash. Later phases can be separate PRs if review size becomes unsafe.

## External Critic Round

A frontier critic returned `REVISE`, finding that the first draft could pass with controlled degradation, detect runtime drift without preventing it, and use too weak a 15-minute smoke window. The plan was revised to add explicit verdict semantics, deployment identity invariants, live DB connection policy, and cadence-based acceptance.

## Final Verdict

REVISE CLEARED AFTER PLAN UPDATE.

The revised plan is not merely adding complexity. It directly targets the four current live continuity blockers:

1. `payload.status` crossing into `exit_state`.
2. running live code not proven to equal mainline.
3. repeated live DB write contention.
4. stale source-health writer contract.

The plan is only approved if future implementation preserves these stop rules:

- No final live-ready claim without a cadence-based `LIVE_CONTINUOUS_READY` acceptance window.
- No special-case `entered` patch.
- No DB mutation shortcut.
- No source-health/forecast-readiness conflation.
- No deployment claim while runtime commit differs from expected mainline or the runtime tree is dirty.
- Controlled degradation must be labeled `CONTROLLED_DEGRADED`; it cannot satisfy package completion.

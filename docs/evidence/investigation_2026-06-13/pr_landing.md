# PR Landing — investigation 2026-06-13 → live/iteration-2026-06-13

# Created: 2026-06-14
# Last reused or audited: 2026-06-14
# Authority basis: operator git-landing mandate (land real agent-worktree fixes onto
#   the live iteration branch, test, push, open PR vs main). Settlement-station edge
#   thesis: cold_bias_metadata_root.md (per-city grid-vs-station representativeness ROOT)
#   + harvest_cadence.md (market-close anatomy refuting the afternoon-cron premise).

## Result

**LANDED + PR: https://github.com/fitz-s/zeus/pull/408**

Branch `live/iteration-2026-06-13` @ `af90efa93b` (pushed; remote == local, plain
fast-forward, no force). Tree clean. Live daemon safe (all touched runtime modules
import cleanly; de-bias is byte-identical-until-fitted).

## Landed commits (4)

| New SHA | Source branch | Source SHA | Change |
|---|---|---|---|
| `cd5aa7bcfb` | `claude/agent-a38f22851d1093d5a` | `4bb9cb9abe` | Per-city representativeness de-bias (EB-shrunk, activation-guarded δ_city; artifact-gated) |
| `13c9bb1fc0` | `claude/agent-af862bd482b53a2f8` | `0976a1892f` | B1 terminal-chain-closed phantom absorber (unfreeze M5 latch on swept winners) |
| `82044459e2` | `claude/agent-a5cac7a371b08bda3` | `6dd7b065cf` | Post-peak microstructure harvester (SCANNER only — no auto-trade, not scheduled) |
| `af90efa93b` | `claude/agent-a58031117ef08966a` | `4dc3ed8340` (partial) | Scanner slug fix only — always include today in slug discovery |

## What was deliberately NOT landed

- **B2 penalty adjudication** (`claude/agent-a54ad5b8210fa99ee` @ `0b553c90fd`):
  `merge-base == its own rev` → already an ANCESTOR of the live branch. No delta. Skip.
- **The afternoon-snapshot-capture cron** (`main.py _afternoon_snapshot_capture_cycle`,
  bundled in a58's commit, plus `afternoon_capture_fix.md`): **DROPPED — refuted premise.**
  The job filters to `hours_to_resolution ∈ (0,12]`, where `hours_to_resolution` is computed
  from `endDate = 12:00 UTC`. The harvest_cadence finding (§B) proves `endDate=12:00 UTC` is
  the UMA resolution DEADLINE, not the trading cutoff — the real close is
  `gameStartTime_next_day = local midnight UTC` (London closes 23:00 UTC, not 12:00). The
  ≤12h window mis-tracks the true settlement window, so the cron is not landed. The slug fix
  (the genuine bug) was kept; the two new slug tests assert only on `_slug_pattern_target_dates`
  and pass with the slug fix alone.
- **Reactor NO loss-class gate** (`claude/agent-a7d43465f42dcf1fe` @ `a70b091b10`): a real,
  well-justified Tier-0 change (reverts the 2026-06-12 forecast-NO else-branch regression on
  non-executable-YES bins; settled-replay antibodies n=485, 98.1% admit). **Out of the explicit
  mandate scope** and it changes live `event_reactor_adapter` decision semantics — NOT landed
  in this batch without explicit operator direction. Candidate for a follow-up.
- The 8 pure-evidence/log commits across a38/a5c (live-order placement logs, microstructure
  test write-ups) — investigation artifacts, not deployable; the de-bias and harvester code
  commits already carried their own plan/impl docs.

## Conflicts resolved

None. All four landings were clean cherry-picks (3 full-commit `-x`, 1 partial `-n` with the
`main.py` afternoon-cron + refuted docs file pruned before commit). No merge conflicts.

## Tests

Run with the project `.venv` (Python 3.14.3, scikit-learn 1.8.0 present — the system `python3`
lacks sklearn, which caused spurious collection errors until the venv was used).

- **Landed-change suites** — `test_anchor_representativeness_debias.py`,
  `test_terminal_chain_closed_phantom_absorber.py`, `test_post_peak_harvester.py`,
  `test_scanner_slug_pattern.py`: **34 passed, 1 failed** — the single failure
  (`test_slug_path_clob_check_rejects_archived`) is **pre-existing on baseline `31c6f2823e`**
  (verified by checking the same test out on the pre-landing HEAD; it uses explicit
  `target_dates`, bypassing the slug-offset change entirely).
- **Broad smoke** — `-k "probability_uncertainty or scanner or post_peak or absorber or
  reconcile"`: **391 passed, 8 skipped, 3 failed.** All 3 failures
  (`test_slug_path_clob_check_rejects_archived`, `test_identity_column_default_scanner_finds_all_known_baseline_sites`,
  `test_zero_insert_warning_on_duplicate_events`) confirmed **pre-existing on baseline
  `31c6f2823e`** — none introduced by this landing, none on a landed code path.
- **Daemon safety** — `import` of `src.data.market_scanner`,
  `src.data.replacement_forecast_materializer`, `src.execution.exchange_reconcile`,
  `src.calibration.anchor_representativeness_debias`, `src.strategy.post_peak_harvester`:
  all load cleanly.

## PR diff size vs main

`git diff --stat main...live/iteration-2026-06-13 | tail -1`:

> **750 files changed, 136705 insertions(+), 29018 deletions(-)**

(This reflects the full 55-commit divergence of the live iteration branch from `main`, of which
this session added the 4 commits above. PR #408.)

## Settlement-station edge thesis (one paragraph)

The per-city forecast cold/hot bias has a single ROOT — a 9km grid-cell-vs-settlement-station
representativeness offset (Tokyo −2.18°C … Karachi +2.48°C; two-sign, lead-stable,
raw-anchor-resident), correctable only by a per-city de-bias (fix #1). The live trading edge is
NOT forecast skill but reprice latency: once a city passes its daily peak and the settlement-station
METAR running max locks, Polymarket is slow to reprice now-impossible bins, so their NO trades
cheap (proven 2026-06-13, London 22°C BUY NO). The harvester (fix #3) surfaces those post-peak
opportunities as a scanner. The reconcile absorber (fix #2) keeps the submit latch from freezing
when the third-party auto-redeemer sweeps settled winners off the shared wallet. The scanner slug
fix (#4) ensures same-day markets are discoverable through the afternoon window.

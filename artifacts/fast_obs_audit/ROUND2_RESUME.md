# Round 2 resume — REQ-20260930-103945-9ead65

## Access, state recovery and delivery

The notice's 6087a2e32/uncommitted worktree was no longer current. Operator-side preservation had committed and pushed 6af79949f, then removed the worktree. The connector restored `feat/fast-obs-best-source` in `/Users/leofitz/zeus/.claude/worktrees/fast-obs-best-source` and rebased it without conflicts onto fetched `origin/live=49acb2fbe643fc20decbdad8fea3d284e2ee1da7`.

Only the feature branch was committed/pushed. No production database writes, live-branch edits/commits/pushes, daemon restarts or deployments occurred. Public endpoint reads, isolated SQLite databases, Git, Python and tests were available through WebCodex.

New implementation commits, each tested and pushed separately:

- `655b9da4f61a4d752b5897569f5bcfe96232b090`: include unprojected resting entries and ended-day rests in current-temperature delivery scope.
- `f7b4b1ff0fafaa01dcce1232021d37609ac8443a`: emit posterior-ready after commit but before wake; replace synthetic ACK telemetry test with a real CLI/socket/reader/executor/journal integration.
- `cdbff6e1cf33f0f0c6f0720f91015166bf2e23ac`: one value-identity rule including FMI; refresh measured proofs; demote mismatched Warsaw IMGW; preserve reproducible public observations and all comparisons.

Recovered changes remain in rebased commits `f9f87cf60668168b1bbbe24d4b831c05099b4e13`, `a939d06847d63ff5bc639cd5b23db4e2776300ef`, and `1a6f6955a59ad788837c8706a9b2806263778f40`.

## 1. Warm chain

`src/data/physical_current_delivery.py` now combines existing held-position discovery with the substrate owner's strict venue-command/snapshot/condition family resolution. A resting order does not need an already-projected position to retain its family. Separate read-only TRADE and FORECAST handles preserve K1; no ATTACH writer or alternate execution authority was introduced. A lookup error preserves known held progress, reports an unavailable scope, and retries on the next sweep.

The existing source-revision comparator still owns recovery, not process flags. First materialization requires a real eligible ensemble; missing physical inputs are not fabricated. Current-day and ended-day held/rest scopes remain eligible. The periodic sweep and immediate source-commit reseed use the same input-identity mechanism.

The production materializer now timestamps POSTERIOR_READY before publishing its socket/durable-queue wake. Previously an immediate consumer could read q before the producer logged READY, giving a false reversed trace. The real q reader timestamps Q_SERVED; SUBMIT_ACKED supplies the actual executor response clock and command identity. No ACK is invented for HOLD/no-action.

The strengthened test runs separate on-disk WORLD, FORECAST and TRADE files, read-only WORLD attachment, actual revision comparison, CLI dependency witness/revalidation, posterior calculation/persistence, Unix datagram and durable wake, held-family selection, q reader, reduce-only executor, and committed command/event journal. Only external forecast inputs, host gate fixtures and the external venue response are controlled. It tests both no incumbent and an incumbent without current-temperature provenance.

Saved targeted-run measurements (`warm_chain_evidence.json`), milliseconds:

| Hop | No incumbent | Incumbent without carrier |
|---|---:|---:|
| Receipt → WORLD commit | 0 | 1 |
| WORLD commit → posterior ready | 233 | 56 |
| Posterior ready → received wake | 11 | 4 |
| Wake → served q | 8 | 8 |
| Served q → executor venue acknowledgment | 107 | 46 |
| Receipt → acknowledgment | 359 | 115 |

Integer zero is sub-millisecond clock quantization. These are controlled examples, not live latency percentiles. The scenario requests a SELL after q is served; the full global-auction selection policy is not exercised. Queue waiting time and real venue/network latency are not benchmarked by this harness. Existing one-second scheduling/fairness can add delay; not every production path is proved sub-second. Newer revisions may supersede earlier intermediate revisions; the durable comparison prevents treating unconsumed current evidence as completed.

## 2. Original six failures and remaining HIGHs

The recovered script-manifest entry for `backfill_noaa_wrh.py` and repaired Day0 fixtures remain green. The five Day0 cases required correct settlement-family metadata and complete causal provider/ensemble windows; no production law was relaxed merely to make them pass.

Retained tested fixes:

- Both harvesters use the same configured `wu_station` identity. The fallback selector uses WRH first and the contract's WU fallback only after the following-day 23:59 America/New_York deadline and a successful station-validated empty-source witness. Local transport/credential failure never proves upstream absence; Ogimet is not silently promoted as the resolver product.
- Faithfulness import failure retains physical observations while marking settlement faithfulness unknown, rather than claiming zero divergence.
- Recovery has a TRADE-owned bounded review retry pass with attempt-count/authority-revision CAS and oldest-open telemetry. Only appropriate native proof resolves a review; retry does not manufacture resolution.
- Failed balance RPC or missing funder retains exposure and opens review. Retry exhaustion cannot administratively close the position. Six RPC/funder/state regression cases pass.

## 3. Value identity and source comparisons

The operator's rule is implemented: exact matching station/UTC-valid-time values under contract units/rounding are sufficient for settlement-grade fast-source admission. No separate instrument certificate is demanded. Sparse intraday observations are not falsely labeled a complete final daily product.

The new twelve-round experiment ran 2026-09-30 15:49–16:00 UTC. It was combined with retained earlier rounds, deduplicated by exact station/channel/valid time, and compared without nearest-time matching. All observed versions and mismatches are retained. The archive covers 54 configured-city inventory rows, measurements for 53, and 1,158 unique pairs: 1,129 exact. Hong Kong's native HKO lanes were not newly compared in this experiment.

| City / candidate | Exact / overlap | Registry decision |
|---|---:|---|
| Helsinki / FMI 100968 | 37 / 49 | Physical only: 12 mismatches |
| Tokyo / JMA Haneda 44166 | 23 / 23 | Settlement-grade fast source |
| Toronto / ECCC CYYZ | 6 / 6 | Settlement-grade fast source |
| Warsaw / IMGW 12375 | 1 / 2 | Demoted to physical only |
| Munich / DWD 01262 | 34 / 48 | Physical only: 14 mismatches |
| Amsterdam / KNMI 06240 | 0 / 0 | Decoded, but no exact-time overlap; not promoted |
| Jinan / WU station history | 40 / 40 | Settlement-grade; same resolver endpoint, not an independent faster mirror |
| Jinan / WU current | 0 / 0 | Working fresh physical source; unmatched clock, not promoted |

Warsaw's new mismatch is 2026-09-30 15:00 UTC: IMGW 19.3°C → 19 versus WRH 20°C. Thus the earlier 1/1 promotion is explicitly revised. Helsinki examples include 10.5°C → 11 versus resolver 10 at 06:50 UTC, and 14.4°C → 14 versus 15 at 14:50. Full values for every mismatch are in `source_identity_pairs.json` and the report.

Existing AWC transport: 688/690 pairs across 52 cities. Chicago has two exact-time disagreements: 16.7°C → 62°F versus resolver 62.6°F → 63°F at 15:30 and 15:40 UTC. This audit does not blanket-promote all AWC values or replace its existing per-family semantic path. Every city's measured channels/counts appear in the CSV and JSON inventory. The national-origin survey is not exhaustive for every U row; an unmeasured channel is not declared inferior or nonexistent.

Bounded first availability in the newest window (same endpoint/client route, not a global publisher guarantee):

- Helsinki 15:50 UTC: AWC 21.358–81.564 s; FMI 82.041–142.277 s; WRH 196.249–256.618 s.
- Jinan WU current 15:56:38 UTC: 45.885–106.653 s. This replaces the unusable stale mirror as working physical-current input; the history product was still at 14:00 during the sweep.
- Tokyo JMA 15:50 UTC: 384.114–442.752 s. Higher cadence does not prove lower latency than every other channel.
- Munich DWD's 15:20 UTC sample: 1,774.580–1,835.679 s. This channel was not demonstrated fastest.
- KNMI: one decoded file, then eleven HTTP429 responses on the shared anonymous credential. No valid lower first-availability bound; no further anonymous probing after the experiment. Four AWC timeouts are recorded separately, not treated as station outages.

`source_availability_intervals.json` excludes KNMI listing-receipt clocks and the old multi-resource JMA lower-bound estimates. Initial positive observations are left-censored, not first-publication measurements. ECCC/IMGW had no newly bounded transition in this window. AEMET and several other national-origin alternatives remain unmeasured rather than being declared unavailable or slower.

## 4. Validation and reproducibility

- Final original/requested-plus-new scoped suite: **477 passed, 0 failed, 0 skipped**. Includes isolated NetCDF4 decoding.
- New scope patch: 121 passed. Timing/CLI focused set: 8 passed. Source identity set: 105 passed. These overlap; do not add them as unique test counts.
- Broader materializer/reader/command suite: **529 passed, 7 failed**. Untouched 49acb2fbe baseline: **524 passed, the same 7 failed**. The names and failure comparison are in `warm_chain_evidence.json`; this is not a fully green repository-wide suite.
- Schema fingerprint passed: `f35b91212e13742dfbea087b9d1f330bc39a0f9ee3bdcc82e0c0be4dddce1109`.
- Explicit committed-scope map maintenance across 54 changed source/test/config/script/architecture files: `ok=true`, no issues. `git diff --check` passed.

Recompute the exact comparisons with `python -B artifacts/fast_obs_audit/compare_sources.py`. It accepts the checked-in compressed parsed public observations when raw JSON is absent. Raw endpoint bodies, transient signed-URL responses, local dependencies and test scratch are not included in the public commit.

## 5. Safety, migration and remaining operator-only work

Optional source failure does not stop existing belief serving. No q-versus-market confidence gate or sizing haircut was introduced. The shared source registry controls station routing without city-name branches. New telemetry is not authority; the harness verifies the ACK's actual committed event ID. Writer ordering and the existing executor/envelope/collateral paths remain authoritative.

The recovered correction fix changes the observation uniqueness index to retain A→B→A revisions. Before any later production deployment, measure index migration on a representative non-production database. After such revisions exist, rollback must retain the revision-capable index; rebuilding the old uniqueness constraint can fail or lose semantics. No production schema migration was executed here.

Only operator-owned credentials and separately authorized deployment/live latency collection genuinely need the operator. A dedicated KNMI credential is needed for reliable runtime access after the observed anonymous throttling; licensed provider access was not obtained. No live restart/deploy is requested or authorized by this report. Full global-auction end-to-end policy measurement and the remaining national-origin survey are unfinished engineering, not tasks falsely delegated as local-only access limitations.

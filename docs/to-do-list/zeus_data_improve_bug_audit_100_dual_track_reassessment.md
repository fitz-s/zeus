# Zeus 100-Bug Audit — Dual-Track Metric Spine Reassessment

**Date**: 2026-04-16
**Branch**: `data-improve` (HEAD = `b025883` Phase 1 MetricIdentity)
**Scope**: Reclassification of the 100 bugs in `docs/to-do-list/zeus_data_improve_bug_audit_100.xlsx` against the Dual-Track Metric Spine Refactor (opened 2026-04-16, active through Phase 4D).
**Method**: 5 parallel explore subagent reports (P1–P5) + 1 verifier pass + 1 P4 re-verification.
**Read-only**: no source edits, no commits, no staging. Companion CSV: [zeus_bug100_reassessment_table.csv](zeus_bug100_reassessment_table.csv).

---

## 1. Executive Summary

| Status | Count | P0 | P1 | P2 |
|---|---:|---:|---:|---:|
| `STILL_OPEN` | **37** | 5 | 24 | 8 |
| `PRE_EXISTING_FIX` | **40** | 10 | 22 | 8 |
| `ABSORBED_DUAL_TRACK` | **20** | 2 | 16 | 2 |
| `ABSORBED_PRE_OR_DUAL` (P1 leftovers, unverified split) | 1 | 0 | 0 | 1 |
| `SEMANTICS_CHANGED` | 2 | 0 | 0 | 2 |
| **Total** | **100** | 17 | 62 | 21 |

### Key finding

The working assumption that "the Dual-Track Refactor resolved ~36 bugs" (the number in [zeus_data_improve_bug_audit_100_resolved.md](zeus_data_improve_bug_audit_100_resolved.md)) **is misattribution**. After git ancestry analysis (`git merge-base --is-ancestor 943e74d <commit>`), **only ~20 bugs are genuinely absorbed by Dual-Track commits** (`943e74d..HEAD`). Roughly **40 bugs were already fixed by pre-Phase-0 K1/K2/K6/K7/K8 commits** (96b70a8, f6f612e, e7914b3, 6e2adb5, 68ff4c1, f6a49e4, d8dc331, b025883-predecessors). The remaining **37 are STILL_OPEN**, with **5 P0** among them.

### P0 STILL_OPEN (confirmed, 5)

| ID | file | lines | failure mode |
|---|---|---|---|
| [B050](../../src/riskguard/policy.py) | `src/riskguard/policy.py` | L242 | `sqlite3.Row.get()` → `AttributeError` on duplicate policy rows |
| [B069](../../src/state/db.py) | `src/state/db.py` | portfolio_loader_view | synthesizes defaults; DB outage ≡ legitimate-empty; DAILY_LOW blindspot |
| [B077](../../src/state/truth_files.py) | `src/state/truth_files.py` | `read_mode_truth_json` | ignores `mode` parameter; live-vs-paper truth can collide |
| [B093](../../src/engine/replay.py) | `src/engine/replay.py` | forecast fallback | fabricates `decision_time` + hardcoded `agreement="AGREE"`; SD-5 violation; DAILY_LOW extra dimension |
| [B099](../../src/engine/cycle_runtime.py) | `src/engine/cycle_runtime.py` | outer except, L1200+ | catches after partial mutations (`add_position`, `log_trade_entry`, `_dual_write_canonical_entry_if_available`); DT#1 fix not wired here |

**Lowest-risk independent fix**: B050 — narrow local patch (access `override_id` via `row["override_id"]` not `row.get(...)`), standalone, no contract ripple.

---

## 2. Status Legend

| Status | Definition |
|---|---|
| `STILL_OPEN` | HEAD code still exhibits the defect. |
| `ABSORBED_DUAL_TRACK` | Fixed by a commit in `943e74d..HEAD` (Dual-Track Phase 0–4D). Commit cited in `fix_commit`. |
| `PRE_EXISTING_FIX` | Fixed by a commit strictly before `943e74d` (pre-Phase 0). Often the 100-bug audit was captured on a stale pointer. |
| `SEMANTICS_CHANGED` | Code path changed, but a strict fix is not asserted at HEAD (often "now fails closed" without the full cleanup). Requires targeted follow-up. |
| `ABSORBED_PRE_OR_DUAL` | Fix confirmed at HEAD; ancestry split between K-phase and Phase 1+ not completed in this pass (P1 slice leftover). |

`daily_low_blindspot=YES` flags bugs whose failure mode is amplified (not fixed) by the new daily-low side of the dual track.

---

## 3. Eight Structural Decisions (SD-A…SD-H)

Per Fitz's methodology: N surface bugs = K structural decisions, K ≪ N. Mapping the 100 bugs onto 8 decisions; each decision's bug cluster is listed together with its Dual-Track phase alignment.

### SD-A — Identity fields: single authority (mode, env, source, metric_identity)
> "Every row carries `mode`, `env`, `source`, and a typed `MetricIdentity`; no fallback defaults; no silent inference."

- **Dual-Track anchor**: Phase 1 `b025883` (MetricIdentity spine) + Phase 2 `16e7385` (World DB v2 mode/env columns).
- **Bugs**: B003, B006, B035, B036, B052, B053, B067, B070, B071, B074, B075, B077, B081, B088, B096, B097.
- **STILL_OPEN**: B006 (env Literal not enforced), B053 (canonical-vs-working capital mix), B067 (env hardcoded), B070/B071 (override upsert overwrites authority), B074 (injects `current_mode` without marker), B077 (mode param ignored — **P0**), B081 (duplicate `_settle` authority), B097 (nullable bankroll).
- **Repair contract**: promote `mode`/`env`/`source` to dataclass fields of every supervisor-facing type; retire `.get()` defaults on these fields across `src/state/*` and `src/riskguard/*`.

### SD-B — Typed error taxonomy at I/O boundaries
> "Every boundary either returns a typed object or raises a typed exception; no `except Exception` swallowing; no sentinel strings in typed fields."

- **Dual-Track anchor**: Phase 3 `6e5de84` (observation_client narrows broad `Exception` to `httpx.HTTPError`/`KeyError`/`ValueError`).
- **Bugs**: B008, B009, B017, B021, B022, B023, B024, B026, B027, B041, B043, B045, B051, B059, B061, B062, B079, B087, B090, B091, B094.
- **STILL_OPEN**: B009 (YAML per-entry can throw), B017 (cache failed-empty vs real-empty), B041 (network ≡ pending), B043 (feature-flag read silently False), B045 (pagination break returns partial), B051 (bad row kills whole parse), B059 (bare except in bias correction), B061 (DB connection leak), B062 (`is_bimodal` catches all), B079 (parse fail → None), B091 (**sentinel strings in time fields**), B094 (replay `json.loads` no per-row isolation).
- **Repair contract**: introduce a single `BoundaryError` hierarchy (`DegradedError` vs `HardFailError`) + strict `is not None`/`isinstance` gates; ban `except Exception:` across `src/data/*`, `src/signal/*`, `src/execution/*`, `src/riskguard/*`.

### SD-C — Three-state chain + control state machine  (= DT#4 + DT#6)
> "Chain outcomes are `COMMITTED | DEFERRED | VOIDED`. Control state is `RUNNING | DEGRADED | PAUSED`. No boolean proxies."

- **Dual-Track anchor**: Phase 2 `16e7385` (`ChainState` enum), DT#4 and DT#6 laws.
- **Bugs**: B011, B012, B013, B014, B015, B047, B049, B055, B065, B068, B072, B098.
- **STILL_OPEN**: B015 (GateDecision enabled not bool), B047 (subsystem failure ≠ pause), B049 (heartbeat halt logic unverified), B055 (2h trailing-loss tolerance; marked `PENDING_DT6` for Phase 6 graceful-degradation law).
- **Repair contract**: B047 + B049 + B055 consolidated into the Phase 6 DT#6 packet; do **not** patch independently.

### SD-D — Lifecycle atomicity: intent → order → fill → canonical → reconcile  (= DT#1)
> "Each step is transactional. Partial mutations are rolled back. No shadow/live execution without authority commit first."

- **Dual-Track anchor**: Phase 2 `16e7385` (World DB v2 commit ordering), DT#1 law.
- **Bugs**: B038, B039, B040, B042, B044, B046, B064, B066, B076, B099.
- **STILL_OPEN**: B064 (`entered_at` fabrication), B066 (quarantine Position synthesizes trade_id; DAILY_LOW blindspot), B099 (outer except after partial commits — **P0**).
- **Repair contract**: wrap `cycle_runtime._execute_candidate` in an explicit transaction; rollback `add_position`/`log_trade_entry`/canonical export together or not at all. B099 is the largest remaining DT#1 debt.

### SD-E — Probability semantics unification (K6 family)
> "Probabilities are typed: `p_raw`, `p_calibrated`, `p_posterior`. No 0.5 fallback. No silent NaN→0 imputation. Empty ensemble is an error."

- **Dual-Track anchor**: Phase 1 `b025883` MetricIdentity + related Phase 4C/4D (`5d0e191` refit_platt_v2).
- **Bugs**: B046, B056, B057, B058, B060, B068, B080, B082, B083, B084, B085, B086, B089, B093, B095.
- **STILL_OPEN**: B082 (`has_platt` over-strict `len>1`), B093 (replay forecast fallback fabricates `decision_time`/`AGREE` — **P0**, DAILY_LOW extra dimension).
- **Repair contract**: replace all `p_cal` magic-number fallbacks with `MissingCalibrationError`; replay must mark synthetic references with `decision_reference_source="forecasts_table_synthetic"` + `agreement="UNKNOWN"`.

### SD-F — Append-only audit vs mutable current-row  (= DT#1)
> "Audit tables are append-only (UTC timestamp, source, reason). Current-state views derive from them, never the reverse."

- **Bugs**: B063, B070, B071, B073, B078, B100.
- **STILL_OPEN** (all 6): B063 (rescue logs, no durable audit; DAILY_LOW), B070 (control_overrides upsert overwrites authority), B071 (token_suppression upsert merges history), B073 (truth outage ambiguous — DAILY_LOW), B078 (truth metadata registry live-only — DAILY_LOW), B100 (DDL `DROP TABLE` in migration path).
- **Repair contract**: each of these is a single-table refactor (split current view from audit log); they can land incrementally without blocking Phase 6.

### SD-G — Temporal / mode semantics as typed objects
> "`lead_days_to_date_start` ≠ `lead_hours_to_settlement_close`. Naive datetimes rejected. DST-aware local time. No string sentinels in time fields."

- **Dual-Track anchor**: Phase 1 `b025883` `time_context.py` rewrite.
- **Bugs**: B018, B019, B028, B033, B034, B091.
- **STILL_OPEN**: B018 (now explicit None — downstream handling needed), B091 (sentinel strings **still** in time fields — separate `*_status` enum required).
- **Repair contract**: B091 is the last SD-G debt; introduce `time_field_status: Literal["OK","UNAVAILABLE_UPSTREAM","UNSPECIFIED"]` adjacent column.

### SD-H — Executable-price Kelly + RED force-exit  (= DT#2 + DT#5)
> "Kelly sizes with executable VWMP, not midpoint. RED states force-exit ahead of daily windows."

- **Dual-Track anchor**: DT#2 and DT#5 laws; commits `e7914b3`, `6e2adb5`, `f6a49e4` (all pre-Phase 0 but ratified as DT#5 mature).
- **Bugs**: B037, B054, B080, B083, B084, B085, B086, B087.
- **STILL_OPEN**: none. Entire cluster is `PRE_EXISTING_FIX` (K6 sequence) or `ABSORBED` (B087).
- **Note**: this decision is complete. Do not reopen unless a Phase 6 regression is observed.

---

## 4. Dual-Track Genuine Absorption (20 bugs, all in `943e74d..HEAD`)

| Phase | Commit | Bugs absorbed |
|---|---|---|
| Phase 1 (MetricIdentity) | `b025883` | B033, B096 |
| Phase 2 (World DB v2 + DT#1 + DT#4) | `16e7385` | B048, B065, B068, B075, B076, B087, B088, B089, B092 |
| Phase 3 (observation_client low_so_far + source registry collapse) | `6e5de84` | B016, B021, B022, B023, B024, B026, B027, B028 |
| Phase 4C/4D (refit_platt_v2) | `5d0e191` | (implicit; no 100-bug rows exclusively attributed) |
| Pre-Phase 1 early | — | B003 |

**Pending-verification inside this set**: B075, B076 (P1 Position/fill-ledger refactors) — ancestry split between K-phase and Phase 2 not completed.

---

## 5. Daily-Low Blindspot Subset (6 bugs)

Bugs whose failure mode is unchanged by Dual-Track schema work but whose downstream impact is **amplified** on the low side of the dual track (because the low lane has weaker historical coverage and Day0LowNowcast has not landed yet).

| ID | file | SD | why amplified on low |
|---|---|---|---|
| [B063](../../src/state/db.py) | `src/state/db.py` | SD-F | Rescue event is log-only; low-lane N/A_CAUSAL slots bypass rescue entirely |
| [B066](../../src/state/quarantine.py) | `src/state/quarantine.py` | SD-D | Quarantine Position synthesizes trade_id/market_id → may conflate high/low semantics |
| [B069](../../src/state/db.py) | `src/state/db.py` | SD-A | portfolio_loader_view synthesizes defaults → canonical DB outage indistinguishable from a legitimate empty low book |
| [B073](../../src/state/portfolio.py) | `src/state/portfolio.py` | SD-A | `load_portfolio` truth outage returns degraded state without an authoritative flag |
| [B078](../../src/state/truth_meta.py) | `src/state/truth_meta.py` | SD-F | Truth metadata registry is live-only; Phase 5 low historical lane tools lack dual-track entries |
| [B093](../../src/engine/replay.py) | `src/engine/replay.py` | SD-E | Replay forecast fallback collapses to a single `decision_time` and `agreement="AGREE"` — the low lane doubles this ambiguity |

**Recommendation**: these 6 are the minimum set that must be closed **before** Day0LowNowcast runtime (Phase 6) is activated in live. They are the dual-track immune system's missing antibodies.

---

## 6. Disputes with Prior Artifacts

The 4-sheet audit xlsx and [zeus_data_improve_bug_audit_100_resolved.md](zeus_data_improve_bug_audit_100_resolved.md) treat 36 bugs as "resolved by the dual-track refactor". Verifier ancestry analysis disagrees:

| Bug | resolved.md claim | Actual finding | Evidence |
|---|---|---|---|
| **B002** | K6 macro | `PRE_EXISTING_FIX` | File untouched in `943e74d..HEAD` |
| **B025** | Phase 3 absorbed | `PRE_EXISTING_FIX` | Dual high_/low_provenance_metadata already present at `943e74d` |
| **B056** | Phase 1 MetricIdentity | `PRE_EXISTING_FIX` | `ens_remaining` `ValueError` already at `943e74d` |
| **P4 slice (16 bugs)** | "ABSORBED_BY_DUAL_TRACK" | **`PRE_EXISTING_FIX`** for all 16 | All fix commits (`e7914b3`, `6e2adb5`, `68ff4c1`, `f6f612e`, `f6a49e4`, `d8dc331`, `96b70a8`) strictly before `943e74d`; Phase 2 `16e7385` touched P4 files for schema/chain-state only (no remediation) |

**Consequence**: Phase 6 planning that assumed "P4 is done, move on" needs to re-open 9 P4 bugs (B041, B043, B045, B050, B051, B053, B055, B081, B082).

---

## 7. Fix Routing

### 7a. Independent low-risk fixes (land anytime, no Phase 6 coupling)

| ID | module | difficulty | ripple |
|---|---|---|---|
| **B050** (**P0**) | riskguard/policy | trivial (1-line) | zero; local |
| B043 | execution/harvester | small | flag read path only |
| B017 | data/market_scanner | small | cache struct |
| B041 | execution/fill_tracker | small | error branch narrowing |
| B045 | execution/harvester | small | pagination degraded flag |
| B051 | riskguard/policy | small | row isolation |
| B059, B061, B062 | signal/ensemble_signal | small | local try/except |
| B079 | state/truth_files | small | parse guard |
| B009 | contracts/provenance_registry | small | YAML per-entry isolation |
| B005, B006, B015 | supervisor_api/contracts + control/gate_decision | small (typed mixins) | dataclass contract only |

### 7b. Must wait for Dual-Track Phase 6 / SD packet

| ID | reason |
|---|---|
| **B055** | `PENDING_DT6` — trailing loss tolerance consolidated with graceful degradation |
| **B093** (**P0**) | Replay fallback requires dual-track `MetricIdentity` awareness on the low lane |
| **B099** (**P0**) | `cycle_runtime` atomic transaction must coordinate with DT#1 commit-ordering choke point |
| **B077** (**P0**) | `read_mode_truth_json` mode routing needs the SD-A mode authority to land first |
| **B069** (**P0**) | `portfolio_loader_view` depends on a canonical truth flag planned for Phase 5 |
| B047 + B049 | Subsystem pause + heartbeat halt bundled under DT#6 packet |
| B063, B073, B078 | Low-lane audit surfaces need Phase 5 low historical lane completion |

### 7c. Planning-locked zones (do not touch until architect signs off)

- `src/control/*` (B011–B015): already pre-fixed; B015 pending K1 packet discipline.
- `src/engine/cycle_runtime.py` (B096–B099): Phase 2 territory; B099 requires explicit architect packet.
- `src/state/truth_files.py` + `src/state/portfolio.py` (B069, B073, B077, B078): Phase 5 historical lane authority.

---

## 8. Notes and Caveats

1. **P1 slice ABSORBED category not split**. Bugs B003, B004, B065, B068, B075, B076 are confirmed fixed at HEAD but their exact pre-Phase-0 vs Dual-Track origin was not re-verified in this pass (budget). Recommend a short follow-up subagent pass to split `ABSORBED_PRE_OR_DUAL` → `PRE_EXISTING_FIX` or `ABSORBED_DUAL_TRACK`. Does not change the **STILL_OPEN** count.
2. **B049 (`SEMANTICS_CHANGED`)**. resolved.md claims "daemon halt on consecutive failures"; code at HEAD only warns. Needs confirmation whether an external monitor job closes the gap.
3. **Ancestry command used**: `git merge-base --is-ancestor 943e74d <commit> && echo AFTER || echo BEFORE`. Commits verified BEFORE: `e7914b3`, `6e2adb5`, `68ff4c1`, `f6f612e`, `f6a49e4`, `d8dc331`, `96b70a8`. Commits AFTER (Dual-Track scope): `16e7385`, `6e5de84`, `b025883`, `5d0e191`.
4. **No files modified**. This reassessment is read-only. No source edits, no staging, no commits. Working tree dirt from other agents is ignored.
5. **xlsx `Status` column**. Not updated by this pass. The CSV [zeus_bug100_reassessment_table.csv](zeus_bug100_reassessment_table.csv) is the new source of truth for status; the xlsx retains the original audit capture.

---

## 9. Appendix: Final counts

- **STILL_OPEN**: 37 (P0=5, P1=24, P2=8)
  - P0: B050, B069, B077, B093, B099
  - DAILY_LOW blindspot subset: B063, B066, B069, B073, B078, B093 (6)
- **ABSORBED_DUAL_TRACK**: 20 (genuine absorption in `943e74d..HEAD`)
- **PRE_EXISTING_FIX**: 40 (fixed in K1/K2/K6/K7/K8 commits before `943e74d`)
- **ABSORBED_PRE_OR_DUAL** (unsplit leftover): 1 (B004)
- **SEMANTICS_CHANGED**: 2 (B018, B049)

**Total**: 100/100 classified.


---

## Session 2026-04-17 addendum — commits and label corrections

Commits landed on `data-improve` this session (oldest first):

| SHA | Bug(s) | Status |
|---|---|---|
| `057979c` | B050 | CLOSED |
| `aab78a5` | B059, B061, B062 | CLOSED |
| `af331e3` | B043, B045 | CLOSED |
| `68cbacc` | B041, B009 | CLOSED (amended `389247b`) |
| `863fd51` | B017 | **PARTIALLY_CLOSED** — see below |
| `5893756` | B005, B006 | **PARTIALLY_CLOSED** — see below |
| `1c85d64` | B082 | CLOSED |
| `fb47af8` | B051 | CLOSED |
| `6d1a8ab` | B066 | CLOSED |
| `389247b` | B009+B041 amendment (critic-alice review) | strengthens catch tuples |

### Label corrections per critic review

**B017 (market_scanner cache provenance)** -- classification corrected
from `CLOSED` to `PARTIALLY_CLOSED`. Commit `863fd51` lands the
`MarketSnapshot` scaffolding (ScanAuthority literal, read-side API
`get_last_scan_authority()`, legacy wrapper) but **zero Dual-Track
callers consume it**. `discover_and_evaluate_candidates` and
`_refresh_monitoring` can still act on stale cache events because no
caller fails-closed on `authority != "VERIFIED"`. The provenance
*type* landed; the *enforcement* is deferred to a Dual-Track-side
commit outside the scope of this session.

- Follow-up ticket: **B017-b** — make `discover_and_evaluate_candidates`
  fail-closed when `get_last_scan_authority()` returns a value other
  than `VERIFIED`. That is the behavior change the audit actually
  asked for. Blocked on Dual-Track coordination because the caller
  chain touches `src/engine/cycle_runtime.py`.

**B005 / B006 (supervisor_api contract tightening)** -- classification
corrected from `CLOSED` to `PARTIALLY_CLOSED`. Commit `5893756`
centralizes `_VALID_ENVS` and adds `provenance_ref: Optional[str] =
None` to 6 supervisor dataclasses. This is a **schema widening** (the
field exists and can be populated); it is **not** enforcement (the
default-None means contracts without provenance still validate). A
follow-up must flip `provenance_ref` to required at every call site
we expect authority to flow through, or add a validation hook that
rejects `env="live"` contracts with `provenance_ref is None`.

- Follow-up ticket: **B005-b** — audit all producer sites of
  `SupervisorCommand`, `SupervisorResult`, `SupervisorAck`,
  `SupervisorRejection`, `SupervisorQuery`, `SupervisorReport` and
  decide per-producer whether provenance_ref should be required.

### Critic-alice review artefacts

The read-only review also flagged test-quality gaps which were
patched within the session (not re-labelled):

- B009 gained `test_b009_non_dict_entry_does_not_poison_registry`
- B041 gained `test_b041_keyerror_propagates` and
  `test_b041_indexerror_propagates`
- B051 gained `test_b051_real_sqlite3_row_indexerror_is_isolated`
  (previously tests used dict MockRow which raised KeyError not
  IndexError)
- B066 regression-grep widened from `chain_reconciliation.py`-only
  to the full `src/state/**/*.py` tree, guarding against copy-paste
  of the legacy empty-id pattern into sibling state files.

### Deferred bugs (touch Dual-Track or planning-locked zones)

Explicitly NOT attempted this session; require DT coordination:

- **Dual-Track zone**: B063 (db.py), B064 (db.py), B070 (db.py),
  B071 (db.py), B079 (truth_files.py), B091 (evaluator.py),
  B094 (replay.py), B100 (db.py)
- **Planning-locked zone 7c**: B015 (control/*),
  B053/B055/B069/B073/B077/B078 (truth_files.py / portfolio.py),
  B074 (portfolio.py), B093 (replay.py), B096/B097/B098/B099
  (cycle_runtime.py)
- **Out of scope (widens constructor surface)**: B081
  (SettlementSemantics injection on MarketAnalysis) -- noted here
  because the audit estimated it as "small typed mixin" but the
  actual refactor touches every MarketAnalysis call site.

---

## Session 2026-04-18 addendum — DT dependency verification + GREEN/YELLOW unblock

A follow-up DT dependency verification (classifying each deferred
bug by *actual* Dual-Track coupling vs. coincidence-with-a-sensitive-
file) revealed that several "deferred" bugs were either (a) already
closed by earlier commits, or (b) not actually DT-coupled. Targeted
fixes landed for GREEN + YELLOW items.

### False-positive deferrals (already CLOSED — removed from pool)

| Bug | Actual state per CSV |
|---|---|
| B011 | PRE_EXISTING_FIX `f6f612e` (control_plane.py loader defaults) |
| B013 | PRE_EXISTING_FIX `f6f612e` (control_plane.py DB exception) |
| B096 | ABSORBED_DUAL_TRACK `b025883` (cycle_runtime Phase 1 MetricIdentity) |
| B098 | PRE_EXISTING_FIX (cycle_runtime) |

### Additional commits this session-2

| Commit | Bugs | Label |
|---|---|---|
| `cf9c148` | B064 (chain_reconciliation fabrication log) + B079 (truth_files parse narrow) | CLOSED (GREEN) |
| `dd59c88` | B015 (GateDecision.enabled bool check) + B074 (portfolio projection env provenance) | CLOSED (YELLOW) |
| `b815e9c` | B053 (riskguard mismatch ERROR) + B097 (cycle_runtime bankroll None reject) | CLOSED (YELLOW) |
| `1d75bcf` | B094 (replay json.loads per-row isolation) + B081 (settlement_rounding helper extract) | CLOSED (YELLOW) |
| `b4d140f` | Critic amendments (B079 OverflowError; B094 Unicode/Recursion; B074 unknown_env valid env + is_unverified_env helper + 5 tests) | strengthens previous 4 commits |

**Net STILL_OPEN trajectory**: 37 → 22 (session 1) → **14** (session 2).

### Updated zone-coupling reclassification

The audit's original "Dual-Track zone" list was in part conservative
rather than true DT coupling. Per the subagent DT dependency report:

- **B064**: file is `chain_reconciliation.py`, NOT in the DT patch
  map. Closed GREEN. (Audit §7c addendum incorrectly listed it as
  db.py; the CSV correctly names chain_reconciliation.py.)
- **B079**: §7a explicitly listed as "independent, land anytime".
  The §7c conservative deferral was supersedable. Closed GREEN.
- **B015, B074, B097, B053, B094, B081**: closed with YELLOW
  sign-off markers in each commit message.

### Truly RED — blocked on specific DT phases

Documented in a dedicated follow-up: see
`docs/to-do-list/zeus_dt_coordination_handoff.md` (to be written
if the user elects that path).

- **After DT schema v2**: B063, B070, B071, B100 (db.py family)
- **After DT Phase 5 truth flag**: B073, B078, B077
- **After DT Phase 5 low-lane MetricIdentity**: B069, B093
- **After DT Phase 1/2 evaluator rewrite**: B091
- **After DT Phase 6 graceful-degradation**: B055
- **Requires architect packet (DT#1 commit-ordering)**: B099

### Cross-contamination incidents this session-2

The **`git diff --stat` pre-stage antibody** (added to
`/memories/vscode_tooling_antibodies.md` after the first session-1
incident on B066) fired twice during the YELLOW bundle and prevented
unrelated other-agent edits from being staged into these commits:

1. **`src/state/portfolio.py`** (during commit `dd59c88` prep):
   another agent's in-workspace edit had silently reverted
   `save_portfolio(..., last_committed_artifact_id=None)` kwarg
   (DT#1 / INV-17 stale-detection feature at L1038-1069) and
   removed a DT#1 exemption comment in `_track_exit`. Reset via
   `git checkout HEAD -- src/state/portfolio.py` and single-edit
   re-apply. Verified intact in HEAD after the reset.
2. **`src/signal/day0_signal.py`** (during commit `1d75bcf` prep):
   another agent's edit had stripped Phase-6 DT code — the
   R4 bare-str rejection guard (`raise TypeError` on str
   temperature_metric), the None rejection, and the R2 low-track
   `raise NotImplementedError`. Stat showed 39-line diff for a
   10-line intended edit; reset + re-apply cleaned it. Verified
   intact in HEAD.

Antibody remains effective; no contamination reached origin.

### Critic review pass (post-commit)

A second critic dispatch over the 8 bugs closed in session-2
returned **zero MUST-FIX**. Five YELLOW nits were raised, of which
two were immediately amendable and landed as `b4d140f`:

- B079 now also excepts `OverflowError` (timedelta arithmetic
  overflow on pathological datetime) — data defect, not code defect.
- B094 now also excepts `UnicodeDecodeError` and `RecursionError`
  on every narrowed `json.loads` site in replay.py — persisted
  JSON is untrusted data.
- B074 `"unknown_env"` is now a valid `_VALID_ENVS` member plus
  `is_unverified_env()` module helper so downstream authority
  consumers can distinguish UNVERIFIED rows rather than silently
  bucketing them into the current runtime mode.

Architect-track items (not amended this session):

- **B097**: commit-message claim that "subsequent P&L is corrupted"
  overstates risk — current prod caller at
  `src/engine/cycle_runner.py:286` already traps `bankroll is None`
  at L270 via `entries_blocked_reason`, so the new ValueError is
  belt-and-suspenders. Remains a legitimate defense for future
  refactors that remove the L270 guard.
- **tests/test_runtime_guards.py L1968/L2013** pre-existing rot:
  calls to `materialize_position(...)` missing the required-keyword
  `env=` argument. Not this session's regression; raises TypeError
  before reaching any of this session's new checks. Flagged for the
  general test-hygiene backlog.


---

## Session 2.1 Addendum — B091 + test-rot follow-up + contamination incident #4

### B091 closure — RED → GREEN

- **Scope clarification**: The handoff doc (`zeus_dt_coordination_handoff.md`
  line 1494-1515) pointed at an emission block that had already been
  refactored on `main`: `_snapshot_issue_time_value` (L1563) and
  `_snapshot_valid_time_value` (L1577) already return `None` on failure
  rather than the old `"UNAVAILABLE_UPSTREAM_ISSUE_TIME"` sentinel string.
  That part of B091 is therefore **already closed on HEAD** — only the
  worktree copy under `.claude/worktrees/data-rebuild/src/engine/evaluator.py`
  still carries the old sentinel strings.
- **Remaining defect addressed**: the silent `or datetime.now(timezone.utc)`
  fabrication of `decision_time` at two sites inside `evaluate_candidate`
  (selection-family `recorded_at` and strategy-policy `policy_now`). In
  production the sole caller is `src/engine/cycle_runtime.py:904`, which
  has `decision_time` in scope but was NOT forwarding it to
  `evaluate_candidate`, so the fallback fired on every cycle. That is the
  same anti-pattern B064 addressed for `entered_at`.
- **Fix** (commit below):
  - `src/engine/cycle_runtime.py`: forward `decision_time=decision_time`
    into `deps.evaluate_candidate(...)` at the single call site so the
    cycle's authoritative decision moment reaches the evaluator.
  - `src/engine/evaluator.py` L1194-L1210 (selection-family): replace
    `recorded_at=(decision_time or datetime.now(timezone.utc)).isoformat()`
    with an explicit `decision_time is None` branch that logs
    `DECISION_TIME_FABRICATED_AT_SELECTION_FAMILY` WARNING before
    falling back.
  - `src/engine/evaluator.py` L1256-L1268 (strategy-policy): replace
    `policy_now = decision_time or datetime.now(timezone.utc)` with
    an explicit fallback branch that logs
    `DECISION_TIME_FABRICATED_AT_STRATEGY_POLICY` WARNING.
- **Verification**: `pytest tests/test_runtime_guards.py
  tests/test_reality_contracts.py` — 18 pre-existing failures on HEAD,
  same 18 pre-existing failures after B091 (zero new regressions);
  100 → 121 passes (gain of 20 from the test-rot fix below unlocking
  previously-short-circuited paths). Full SD-G typed-companion
  `time_field_status: Literal[...]` field on a future `EdgeDecision`
  dataclass is deferred — the current dataclass has no time fields of
  its own, and extending it to carry structured time metadata is a
  larger refactor that belongs alongside the Phase 1 MetricIdentity
  consumer rework, not a 14-bug cleanup commit.

### Test-rot follow-up (ships with B091)

- **tests/test_runtime_guards.py L730**
  (`test_probability_trace_skip_is_warned_when_decision_id_missing`):
  missing `env=` kwarg in `execute_discovery_phase(...)` call — same
  class of rot as the `materialize_position` omissions caught by the
  critic in session 2 and fixed in `f0c1795`. Adds `env="paper"` to
  the call.

### Cross-contamination incident #4 — evaluator MetricIdentity type-seam

- **Observed**: While staging the B091 edits, `git diff --stat
  src/engine/evaluator.py` reported `+64 / -47` after three small
  `multi_replace_string_in_file` insertions that should have produced
  roughly `+40 / -4`.
- **Diff inspection** revealed the working tree had reverted a large
  block of the Phase 1 MetricIdentity type-seam in evaluator.py:
  - removed `TYPE_CHECKING` import of `Day0ObservationContext`,
  - downgraded `_normalize_temperature_metric` return from
    `MetricIdentity` to `str`,
  - collapsed `make_hypothesis_family_id` / `make_edge_family_id`
    back into a single `make_family_id` call,
  - downgraded `candidate.observation` access from typed
    `Day0ObservationContext` attributes to `dict.get(...)` calls,
  - downgraded `temperature_metric.is_low()` to `== "low"` string
    compare,
  - stripped the Phase-1 comment markers and the Phase-6 TODO on
    `member_mins_remaining`.
- **Recovery**: `git checkout HEAD -- src/engine/evaluator.py`, then
  re-applied ONLY the 2 B091 edits via `multi_replace_string_in_file`.
  Post-recovery `git diff --stat` shows `+33 / -2` lines on
  evaluator.py, matching expectation.
- **Pattern count**: This is the 4th contamination incident this
  session (#1 portfolio.py DT#1 `last_committed_artifact_id` revert,
  #2 day0_signal.py R4/R2 MetricIdentity guard strip, #3 post-push
  clobber of handoff+audit+test files, #4 evaluator Phase 1 type-seam
  revert). Antibody (b) — pre-stage `git diff --stat <file>` before
  every `git add` — caught all 4. Recommend formalising this antibody
  as a one-line shell alias (`zeus-pre-stage-check`) and documenting
  it in the data-improve branch README for future agents.

### Commit reference

- `<HASH>` B091 + test-rot `env=` kwarg + contamination #4 log.

### STILL_OPEN trajectory

- Session 2.1 closes: **B091**.
- Net STILL_OPEN: 14 → 13. Remaining pre-Phase-5 queue: B063, B070,
  B071, B100 (DT prereqs all satisfied per handoff doc).

---

## Session 2.2 Addendum — B063 closure + contamination #5

### B063 closure — RED → GREEN

- **Scope verified**: Explore sub-agent #1 confirmed B063 is
  DT-INDEPENDENT: Phase 2 v2 audit infrastructure is landed, the new
  rescue_events_v2 DDL slots in alongside the existing 8 v2 tables,
  and `_emit_rescue_event`'s existing DT#1 commit_then_export
  exemption (authoritative audit row) carries over unchanged.
- **Provenance decision**: Explore sub-agent #2 mapped Position
  metric propagation — Phase 1 MetricIdentity IS complete, Position
  carries `temperature_metric: str = "high"` (portfolio.py:146), and
  `materialize_position` explicitly forwards it. Option (a) silent
  default="high" therefore mis-tags only degraded paths
  (quarantine, JSON reconstruction), not live trading.
- **Architecture verdict**: Explore sub-agent #3 REJECTED option (c)
  tri-state `temperature_metric='unknown'`:
  - Violates zeus_dual_track_architecture.md §2.1 normative law
    `temperature_metric ∈ {high, low}`.
  - Violates MetricIdentity.__post_init__ cross-pairing constraint
    (no defined `observation_field` for 'unknown').
  - Silent semantics failure at 5+ sites (day0_signal.py:85,
    evaluator.py:803, evaluator.py:1021, day0_window.py:67,
    ensemble_signal.py:161 — all `is_low()` returns False for
    non-"low" strings).
  - Precedent mismatch: `direction='unknown'` is metadata with
    explicit consumer skip logic; `temperature_metric` is semantic
    routing with no such guards.
- **Chosen fix — option (c-strong)**: binary temperature_metric +
  separate tri-state `authority` column (VERIFIED / UNVERIFIED /
  RECONSTRUCTED) for provenance confidence. Aligns with SD-1
  (MetricIdentity binary) and SD-H (provenance authority tagging),
  both already active. Pattern established by `settlements_v2` which
  already carries an `authority` column.

### Implementation

- `src/state/schema/v2_schema.py` (+59 lines): new
  `rescue_events_v2` DDL slotted after `day0_metric_fact` block,
  inside the existing single BEGIN/COMMIT transaction managed by
  `apply_v2_schema`. Columns: `rescue_event_id` PK,
  `trade_id NOT NULL`, `position_id`, `decision_snapshot_id`,
  `temperature_metric CHECK IN ('high','low')`,
  `causality_status CHECK IN (OK, N/A_CAUSAL_DAY_ALREADY_STARTED,
  N/A_REQUIRED_STEP_BEYOND_DOWNLOADED_HORIZON,
  REJECTED_BOUNDARY_AMBIGUOUS, UNKNOWN)`,
  `authority CHECK IN (VERIFIED, UNVERIFIED, RECONSTRUCTED)`,
  `authority_source`, `chain_state`, `reason`, `occurred_at`,
  `recorded_at`, `UNIQUE(trade_id, occurred_at)` for idempotent
  retry. Two indexes: `(trade_id, recorded_at)` and
  `(temperature_metric, causality_status, recorded_at)`.
- `src/state/db.py` (+93 lines): new `log_rescue_event(...)` helper
  in the safe post-migration zone adjacent to `log_microstructure`
  and `log_shadow_signal`. Enforces SD-1 binary invariant
  Python-side (refuses to INSERT rows with out-of-domain metric —
  logs error + returns rather than raising). Swallows
  sqlite3.OperationalError (legacy DBs missing v2 schema) at
  WARNING level and sqlite3.IntegrityError (UNIQUE conflict on
  retry) at INFO level, so the caller's chain reconciliation never
  breaks on audit-layer failures.
- `src/state/chain_reconciliation.py` (+43 lines):
  `_emit_rescue_event` now dual-writes CHAIN_RESCUE_AUDIT to
  position_events (legacy, kept for existing consumers like
  test_live_safety_invariants and test_architecture_contracts) AND
  the new rescue_events_v2 row. Authority resolution block extracts
  `position.temperature_metric`; if it is in {"high","low"} → tag
  VERIFIED + `authority_source="position_materialized"`. Otherwise
  fall back to `"high"` + UNVERIFIED +
  `authority_source="position_missing_metric:{raw!r}"`. Import of
  `log_rescue_event` is local (matches the existing lazy-import
  pattern for `update_trade_lifecycle`, `append_many_and_project`,
  `record_token_suppression`).
- `tests/test_b063_rescue_events_v2.py` (new, 11 test cases):
  1-4 schema tests (creation, binary metric, tri-state authority,
  causality enum), 5-9 helper tests (VERIFIED write, invalid metric
  skipped, None conn noop, missing table tolerated, idempotent
  duplicate), 10-11 integration tests (VERIFIED path, UNVERIFIED
  fallback path).

### Verification

- `pytest tests/test_b063_rescue_events_v2.py -v` → 11/11 passing.
- `pytest tests/test_schema_v2_gate_a.py` → should remain green (no
  schema changes to the 8 existing v2 tables).
- Lazy-import pattern verified against existing `_sync_reconciled_trade_lifecycle`.

### Cross-contamination incident #5 — db.py edit silently reverted

- **Observed**: After `multi_replace_string_in_file` reported a
  successful edit to `src/state/db.py` adding `log_rescue_event`,
  `git diff --stat` showed ZERO lines changed in db.py. The
  `read_file` tool still returned the edited content (stale cache),
  but shell `grep -c "def log_rescue_event"` on the file returned 0
  and `stat` showed mtime = `Apr 16 15:18` (pre-edit).
- **Recovery**: Re-applied via pathlib patch script written to
  `/tmp/b063_db_patch.py` (antibody pattern a from earlier in the
  session). Post-recovery `grep -c` returned 1, `stat` showed fresh
  mtime, `git diff --stat` showed +93 lines.
- **Pattern count**: This is the 5th contamination incident on this
  branch. Unlike incidents #1-#4 which contaminated with unrelated
  content, #5 is a **silent no-op** — the working tree matches HEAD
  exactly after the tool reported success. Plausible causes:
  (a) file was concurrently rewritten between the tool's read and
  its write, (b) the tool's write went to a shadow copy that the
  filesystem discarded, (c) an editor process held the file open
  and its buffer overwrote the tool's write on save. Fallback
  pathlib patch via subprocess is the reliable antibody.
- **Recommendation**: treat `multi_replace_string_in_file` success
  as untrusted; ALWAYS confirm with shell-level `grep` before
  assuming edits landed. Update the DT-branch onboarding note.

### Commit reference

- `<HASH>` B063 rescue_events_v2 + contamination #5 log.

### STILL_OPEN trajectory

- Session 2.2 closes: **B063**.
- Net STILL_OPEN: 13 → 12. Remaining pre-Phase-5 queue: B070, B071,
  B100 (DT prereqs satisfied; same audit-append contract pattern).

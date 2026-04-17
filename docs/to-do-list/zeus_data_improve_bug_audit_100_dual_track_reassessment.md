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

# CRITIC — Consistency Review of the Tribunal Reshape Plan
# Created: 2026-05-29
# Authority basis: ADVERSARIAL read-only code audit on worktree stat-whole-refactor
#   (HEAD 08e6600d2f). Angle = train-serve consistency of the forecast RV +
#   ForecastObject contract completeness + unit-chain + enforcement chokepoint.
# Reviews: TRIBUNAL_DRAFT2_RESPONSE_2026-05-29.md + TRIBUNAL_REFRAME_2026-05-29.md
#   against live code. Evidence files A/B/C/D treated as claims, re-verified.
# Verdict: REVISE. The spine (contract is the right antibody) is sound; but the
#   plan as written would ship a contract that is NOT the only path, omits the
#   decisive serving-side chokepoint, and leaves three unit-provenance + lead-key
#   inconsistencies that let two genuinely-different RVs compare equal.

---

## VERDICT: REVISE (spine accepted; 4 SEV-1 consistency gaps block "antibody" status)

The plan repeatedly invokes its own correct test — "else it's doc, not antibody"
(REFRAME §2 step 2; DRAFT2 §3b) — and then fails that test on its own terms at
four points. None are fatal to the direction; all are fatal to the claim that the
contract makes the bad-residual category *unconstructable*. Each has a concrete fix.

---

## SEV-1 FINDINGS (block the "antibody, not doc" claim)

### SEV-1.A — The serving-side bias lookup is a THIRD chokepoint the plan never names; it is lead-blind and is where train-serve lead consistency actually breaks
- Confidence: HIGH
- The plan names exactly TWO enforcement seams: writer = `scripts/ingest_grib_to_snapshots.py`, reader = `src/data/executable_forecast_reader.py` (DRAFT2 §3b; REFRAME §5.2). But the bias/error-model that defines the served RV is resolved at a THIRD site the plan never mentions:
  - `src/engine/evaluator.py:3296` `_resolve_ft_error_model_for_entry(conn, city, target_date, metric_str)` — **no lead / lead_bucket / cycle argument**. It calls `read_bias_model(city, season, metric, live_data_version, month=None, family)` at `evaluator.py:3331-3339`.
  - Its byte-twin `src/engine/monitor_refresh.py:343` `_resolve_ft_error_model(conn, city, target_d, metric_str)` — same signature, same lead-blindness, used at `monitor_refresh.py:559` and `:604`.
- Why this matters: REFRAME §3c / DRAFT2 §3c assert serving picks "the freshest run *within the target forecast_lead_bucket*" and that evidence is keyed by `forecast_lead_bucket`. But the actual serving lookup has nowhere to pass the elected snapshot's lead. After you re-key `model_bias_ens` by lead_bucket (Phase 4), `read_bias_model` filtered by `(city, season, metric, data_version)` with the new lead column unbound will either (a) return multiple rows (ambiguous → silent first-match) or (b) return zero rows → fail-OPEN to raw (`evaluator.py:4126`, `:4170`; the resolver returns None on missing row, `evaluator.py:3340-3347`). A bias *estimated* at forecast_lead is then *served* with no proof the serving lead matches the key. This is precisely the train-serve lead inconsistency the plan claims to fix, left unfixed at the only site that serves it.
- Fix: Add `_resolve_ft_error_model*` (BOTH evaluator + monitor_refresh — the docstring at `evaluator.py:3310` already warns they must change together) to the named chokepoint list. Thread the elected bundle's `forecast_lead_bucket` (derivable from `executable_forecast_reader` bundle: `floor((target_local_date_start - issue_time)/24h)`) into `read_bias_model` as a REQUIRED filter. The resolver MUST RAISE (not fail-open to raw) when the lead-keyed row is absent under the new contract, else "serve raw on miss" silently masks a key mismatch as a raw fallback.

### SEV-1.B — Contract enforced only at `ingest_grib_to_snapshots` leaves ≥2 independent live writers to the SAME table that bypass it
- Confidence: HIGH
- DRAFT2 §3b: "a single `ForecastObject.from_snapshot_row(row)` constructor ... called at the writer before INSERT." There is no single writer. The production table is `ensemble_snapshots` (DDL `src/state/schema/v2_schema.py:108`; NOTE: the docs call it `ensemble_snapshots_v2` everywhere — that string is only a label in evidence scripts + a temp fixture table at `replay_equivalence_full_transport.py:804`; see SEV-3.G). Independent `INSERT INTO ensemble_snapshots` sites that do NOT route through `ingest_grib_to_snapshots`:
  - `scripts/backfill_ens.py:132` — its OWN raw INSERT, and it computes p_raw via a DIFFERENT path: `ens.p_raw_vector(bins, n_mc=2000)` with `np.random.seed(42)` (`backfill_ens.py:127-129`). That is **2000 MC draws + a fixed legacy seed**, not the production 10k deterministic-sha256 path (`ensemble_signal.py:215-244`). It also writes only 13 columns — no `source_cycle_time`, no window-provenance, no `contributes_to_target_extrema`.
  - `scripts/backfill_low_contract_window_evidence.py:381` — dynamic-column `INSERT OR IGNORE INTO ensemble_snapshots`, no contract constructor.
  - (Also `scripts/seed_isolated_calibration_db.py:139`, `scripts/backfill_tigge_snapshot_p_raw*.py` UPDATE p_raw_json directly.)
- The daemon path IS consistent (it routes through `ingest_grib_to_snapshots.ingest_track` via `src/data/ecmwf_open_data.py:109,1250`), so the writer-chokepoint claim is true for the daemon and false for backfill/seed.
- Why this matters: under the plan's own logic, a residual is constructible iff it can be built from a contract-valid row. A `backfill_ens.py` row carries a `data_version` but no cycle/window fields → if the reader-side constructor is the only backstop, the plan's "called at the writer before INSERT" is simply not happening for these rows. More dangerous: `backfill_ens.py` rows hold p_raw computed at n_mc=2000/seed=42, which the EQUIVALENCE harness (DRAFT2 §2a) would score as a non-zero delta and (correctly) flag as a refactor bug — except these rows predate the harness and would silently sit in the calibration corpus.
- Fix: Either (1) route ALL writers through one `insert_snapshot(contract_row)` funnel that calls the constructor (delete the independent INSERTs in backfill_ens / backfill_low_contract_window / seed), OR (2) explicitly QUARANTINE these scripts (header verdict `DEAD_DELETE` or `--apply` hard-gated + a CI grep that fails if any non-funnel `INSERT ... ensemble_snapshots` appears). The plan must enumerate every writer and assign each a verdict; "name the writer" (singular) is the doc-not-antibody failure it warns against.

### SEV-1.C — `member_unit` is resolved 3 different ways across 4 serving sites; 3 of 4 ignore the validated members provenance → silent °C/°F bias mis-scale (Fitz unit-provenance class)
- Confidence: HIGH
- The bias is °C; `p_raw_vector_with_error_model` converts it to native via `_c_to_native_scale(member_unit)` returning **1.0 for °C, 1.8 for °F** (`ens_error_model.py:201-237`). `corrected = member_extrema - bias_c*scale`. Correctness depends entirely on `member_unit` matching the true unit of `member_extrema`. The four serving sites disagree:
  - `monitor_refresh.py:558` period-branch: `_member_unit = expected_members_unit` (validated against the snapshot's stored `members_unit`). **CORRECT — reads provenance.**
  - `monitor_refresh.py:605` ens-branch: `_member_unit = "degC" if city.settlement_unit=="C" else "degF"`. Derived from city config.
  - `evaluator.py:4122` period-branch: `ens_result.get("members_unit", city.settlement_unit or "F")` — provenance IF present, else city config.
  - `evaluator.py:4166` ens-branch: hardcoded `member_unit=city.settlement_unit or "F"`. **No provenance read at all.**
- The ingest writer DOES validate and store the true unit (`ingest_grib_to_snapshots.py:525-540,666` calls `validate_members_unit`), so the ledger column is trustworthy — but 3 of 4 readers re-derive from `city.settlement_unit` instead. The evidence files state members are °C (`members_unit` °C) while settlement is integer °F. For any city whose `settlement_unit`="F" but whose members are stored °C, the ens-branches pass `member_unit="degF"` → scale=1.8 → the °C bias is over-applied by 1.8×, pre-MC, silently.
- Why this matters: DRAFT2 §3b's target tuple is `product/cycle/lead_bucket/window/contributes` — **`unit` is not a target-equality dimension.** Two RVs identical on that tuple but differing in member unit compare equal, and the bias subtract uses whichever unit the serving site guessed. This is the exact "code perfect, data semantics broken" failure (CLAUDE.md provenance doctrine).
- Fix: (1) Add `members_unit` to the ForecastObject target tuple and to the `Residual` target-equality assertion. (2) Make `member_unit` at ALL serving sites read the elected snapshot's validated `members_unit` (the value the contract guarantees), never `city.settlement_unit`. (3) Add a relationship test: feed members tagged °C with a city tagged settlement_unit="F" and assert the served bias-corrected mean is invariant to which the caller passes — it must come from provenance.

### SEV-1.D — Existing Platt params were trained on 10k-MC p_raw; the plan retires MC for analytic p_raw but never states Platt MUST refit → silent miscalibration
- Confidence: HIGH
- Platt is trained on whatever p_raw shape produced the calibration pairs. The production p_raw is 10k-MC over Gaussian-per-member + WU integer rounding (`ensemble_signal.py:254-258`; `platt.py` docstring: lead is an input feature, A·logit(p_raw)+B·lead+C). DRAFT2 §2a/§3d frames analytic-replaces-MC purely as an EQUIVALENCE check on **p_raw** (|Δ|≈0 per bin). It does NOT address whether `logit(p_raw_analytic)` lands on the same support the existing Platt A/B/C were fit on. Even when per-bin |Δp| is within MC noise (~2e-4), `logit` is nonlinear near 0/1: a 2e-4 shift on a p_raw of 1e-3 moves the logit materially, and Platt was fit on the MC-quantized logit cloud. The C-doc (C3-c) correctly flags that post-rounding the analytic CDF is a staircase, not smooth — so exact equivalence is NOT guaranteed at the bin edges, which is exactly where logit is most sensitive.
- Why this matters: the plan treats Platt as untouched across the MC→analytic cutover. If analytic p_raw is even slightly differently distributed at the tails, the frozen Platt params silently miscalibrate — and the EQUIVALENCE harness as specified (p_raw delta only) would PASS while p_cal regresses.
- Fix: Make the §2a EQUIVALENCE gate score `p_cal` (post-Platt), not only `p_raw`. Add an explicit decision to the plan: either (a) analytic p_raw must pass equivalence on `logit(p_raw)` AND on `p_cal` under the frozen Platt params before MC retires, or (b) Platt refits on analytic-p_raw pairs at Phase 5 and the refit is itself gated by IMPROVEMENT mode. State which. Do not retire MC until p_cal equivalence (not just p_raw) passes under every `settlement_rounding_policy`.

---

## SEV-2 FINDINGS (cause significant rework / weaken consistency)

### SEV-2.E — Target tuple omits settlement station identity, settlement source authority, and bin-grid id → two different RVs can satisfy target-equality
- Confidence: HIGH
- The Residual validity rule is `forecast.target == settlement.target` (DRAFT2 §3b). The enumerated target dims are `(city × metric × target-local-date × product × cycle × lead × window)` (REFRAME §2 final para). Missing dims that genuinely define the RV:
  - **Settlement station / source authority**: the settlement integer comes from a specific station + source (WU vs METAR vs oracle). Two settlements for the same city-date from different stations are different RVs; the tuple has only `city`, not `settlement_station_id` + `settlement_source` + `authority`. (CLAUDE.md: every data source needs `source`+`authority`; `authority:"UNVERIFIED"` must not enter the chain — the tuple cannot enforce this if authority is not a target dim.)
  - **Bin grid id**: p_raw is a vector over a specific bin partition. A residual/score computed on bin-grid A vs B is not comparable. `settlement_rounding_policy` (DRAFT2 §3d implies multiple) changes bin preimages; the tuple has `window` but no `bin_grid_id` / `rounding_policy_id`.
  - **unit** (see SEV-1.C).
- Fix: Extend the target tuple to `(city, settlement_station_id, settlement_source, settlement_authority, metric, target_local_date, product, cycle, forecast_lead_bucket, agg_window, members_unit, bin_grid_id, rounding_policy_id)`. The `Residual.__init__` assertion must compare the FULL tuple. Anything less lets a bad residual slip through equal.

### SEV-2.F — DST local-day interval (23h/25h days) not pinned in the target; lead_bucket arithmetic = floor(lead_hours/24) silently wrong on transition days
- Confidence: MEDIUM (HIGH on the mechanism; MEDIUM on live blast radius given HIGH-only trading)
- D-doc §3 proposes `lead_bucket = floor(lead_hours/24)` and `local_day_start_utc` is nullable on legacy rows (D-doc table, `v2_schema.py` ALTER). On a spring-forward day the local day is 23h; on fall-back 25h. `lead_hours` measured to a fixed `target_date+12:00Z` vs the true local-day boundary diverges by ±1h at DST transitions — exactly the London 2025-03-30 failure class in CLAUDE.md. The target-equality check on `target_local_date` does NOT encode the local-day length, so a forecast whose window was built under a 24h assumption and a settlement on a 23h day compare equal.
- Fix: Make `local_day_start_utc` + `local_day_end_utc` (the DST-aware interval) NOT NULL in the ForecastObject target, and derive lead_bucket from `(issue_time, local_day_start_utc)` not from a fixed-24h arithmetic. Backfill (D-doc §3) must reject rows where `local_day_start_utc` is NULL rather than fall back to `issue_time` (D-doc caveat 1 currently allows the fallback — that re-introduces the ambiguity).

### SEV-2.G — gate-hash re-bump (Phase 4) consistency across consumers is asserted but not verified; B-doc O3 shows a live coverage_months/season hemisphere mismatch that survives the hash
- Confidence: MEDIUM
- DRAFT2 §4 / REFRAME §5: "Gate-hash re-bump happens deliberately at Phase 4." The plan does not enumerate the consumers of `gate_set_hash` and prove they all read the new key. B-doc O3 already documents a live inconsistency that a hash bump does NOT fix: `model_bias_ens.coverage_months` is calendar-month-indexed while `season` is hemisphere-aware (SH-flipped), so a diagnostic checking `target_month in coverage_months` concludes "covered" on a row whose `season` label says otherwise (`ens_bias_repo.py:95`, read path filters by SH-flipped `season`). Re-keying by product/cycle/lead bumps the hash but inherits this calendar-vs-hemisphere ambiguity.
- Fix: Enumerate every `gate_set_hash` reader (drift detector, serving status, scorer, readiness) and add a Phase-4 assertion that each reads the new hash; explicitly resolve the coverage_months indexing (store hemisphere-aware month set, or drop the field) so the re-key does not carry the B-doc O3 ambiguity forward.

---

## SEV-3 FINDINGS (correctness-adjacent / hygiene)

### SEV-3.H — Pervasive `ensemble_snapshots_v2` naming in BOTH plans + evidence A/B/C is a phantom; production table is `ensemble_snapshots`
- Confidence: HIGH
- `ensemble_snapshots_v2` as a real table appears ONLY as a temp/fixture in `scripts/replay_equivalence_full_transport.py:804`. The live DDL is `ensemble_snapshots` (`v2_schema.py:108`); every production writer/reader uses the unsuffixed name. The docs (A_source_extraction §C2 "INSERT/UPDATE to ensemble_snapshots_v2", B-doc title refs, C-doc O1) name a table that is not the one the contract must own.
- Why it matters for consistency: a chokepoint spec that names the wrong table is a grep-trap; an executor wiring "the writer to ensemble_snapshots_v2" will find only the fixture and miss the real funnel. Memory note (grep-gate before contract lock) applies: line/table refs rot.
- Fix: Global replace `ensemble_snapshots_v2` → `ensemble_snapshots` in both plans + A/B/C, with a one-line note that `_v2` was the superseded logical name (the DDL is the v2 schema *module*, but the table is unsuffixed).

### SEV-3.I — `contributes_to_target_extrema` precomputed-at-ingest, trusted-at-read is named as defect #4 but the contract does NOT make it rederivable; the target tuple stores `contributes` as data, not as a checkable function of the window
- Confidence: MEDIUM
- A-doc Claim 3 (CONFIRMED): reader trusts the stored int (`executable_forecast_reader.py:33`); classifier runs only at ingest (`forecast_calibration_domain.py:274-384`). The plan's constructor "RAISES if contributes can't be resolved" (DRAFT2 §3b) still trusts the stored value — it checks presence, not correctness. If the window provenance changes (e.g., the STEP_HOURS=6 vs 3 fix, A-doc §D1), the stored `contributes` flag is stale and the contract happily constructs a ForecastObject around a wrong flag.
- Fix: The contract should rederive `contributes_to_target_extrema` from `(window, target_local_date interval, member values)` at construction and RAISE on disagreement with the stored value, OR drop the stored column and compute on read. "Resolve or raise" must mean "recompute and cross-check," not "is non-null."

---

## Multi-perspective notes
- EXECUTOR: With the table-name phantom (SEV-3.H) and the unnamed third chokepoint (SEV-1.A), an executor following the plan literally wires 2 of the 3 serving seams and targets a fixture table. They WILL get stuck / silently miss the bias-serving site.
- STAKEHOLDER: The plan's honest §5 ("improvement mode underpowered for months") is correct and well-stated; the consistency gaps above do not undermine that framing — they undermine the EQUIVALENCE half, which the plan calls "the solid half that carries Phases 2-5." SEV-1.C/D specifically break equivalence's soundness.
- SKEPTIC: Strongest counter to my SEV-1.B — "backfill scripts are --apply-gated, not live." True, but the plan's claim is categorical ("unconstructable"), and gated ≠ removed; a corpus row from n_mc=2000/seed=42 is a live consistency hazard for any calibration fit that reads it. The fix (funnel or quarantine-verdict) is cheap.

## What's missing (gap analysis)
- No statement of which p_raw consumers must pass equivalence: there are ~7 p_raw callsites (evaluator 4116/4127/4160/4850, monitor_refresh 561/579/606, event_reactor_adapter 2705, rebuild scripts 124/134/309). The GFS crosscheck site (`evaluator.py:4850`, `gfs_p = p_raw_vector_from_maxes`) is explicitly NOT error-model-wired (comment `evaluator.py:4108-4110`) — analytic-replace must preserve that asymmetry or it regresses GFS.
- No relationship test specified for the cross-module invariant "members_unit flowing from ingest into bias-subtract preserves the bias magnitude." Per CLAUDE.md this test must exist BEFORE implementation.
- No plan for the `_resolve_ft_error_model` / `_resolve_ft_error_model_for_entry` byte-twin divergence risk (evaluator.py:3309 warns they must change together but nothing enforces it) — a CI grep or shared helper is the antibody.

## Open questions (unscored)
- Does any city actually have members °C with settlement_unit="F" in the live config? If NO live city triggers it, SEV-1.C is a latent (not active) defect — but it is still a target-tuple completeness gap. (I did not enumerate city configs; the unit-resolution divergence is proven in code regardless.)
- Is `backfill_ens.py` still reachable in any live job, or dead? Its header says "Run only under packet approval" (last_reused 2026-04-25). If dead, downgrade SEV-1.B's backfill_ens limb to SEV-2 and assign DEAD_DELETE.

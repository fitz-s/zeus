# Pre-launch checklist for daemon relock

**Created**: 2026-05-05
**Authority basis**:
- `architecture/calibration_transfer_oos_design_2026-05-05.md` row "PENDING — daemon plist update" (task #176)
- Live entry routing audit 2026-05-05 (task #180): legacy `fetch_ensemble` path is hard-blocked for `ecmwf_open_data` (ingest_class=None) by `src/data/ensemble_client.py:149-160` — only the executable-forecast reader path is viable.
- Default-OFF rationale on `ZEUS_ENTRY_FORECAST_READINESS_WRITER`: `src/engine/evaluator.py:797-811` (critic-opus PR #54 fix-up).

**Scope**: every step the operator must complete BEFORE clearing the daemon lock and reloading the live-trading plist. This consolidates four independent pre-conditions discovered during the 2026-05-05 launch-prep sweep. Skipping any step keeps the live entry path either silently inoperative (calibration transfer flag) or hard-blocked at runtime (writer flag + rollout_mode + promotion evidence).

## Current state snapshot (verified 2026-05-10)

| Item | Current state | Required state for live |
|---|---|---|
| `~/Library/LaunchAgents/com.zeus.live-trading.plist` (active) | present, contains `ZEUS_ENTRY_FORECAST_READINESS_WRITER=1` ✅ | (no change) |
| `control_overrides` precedence-200 row | check via `sqlite3 state/zeus-world.db "SELECT override_id, effective_until FROM control_overrides WHERE precedence=200 AND effective_until IS NULL OR effective_until > datetime('now')"` | cleared (or `effective_until` set) before launch |
| `config/settings.json::entry_forecast.rollout_mode` | `"live"` ✅ (verified 2026-05-10) | (no change) |
| `state/entry_forecast_promotion_evidence.json` | exists, 530B, mtime 2026-05-06 ✅ | re-verify content via `python -m json.tool` before launch |
| `launchctl getenv ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED` | `true` (launchd user-domain leak from prior `launchctl setenv`) | **MUST `launchctl unsetenv` before loading any plist** — see §1.5 + §6 |
| `ZEUS_ENTRY_FORECAST_READINESS_WRITER` in plist | `1` ✅ | (no change) |
| `entry_forecast.source_id` | `ecmwf_open_data` ✅ | (no change) |
| `entry_forecast.source_transport` | `ensemble_snapshots_v2_db_reader` ✅ | (no change) |
| ECMWF/TIGGE physical-equivalence diagnostic | passes locally ✅ (yaml `status: BINDING_2026_05_10`) | (no change) |
| `launchctl list \| grep zeus` | empty (no daemons loaded) | load order: riskguard → data-ingest → live-trading → heartbeat-sensor (skip calibration-transfer-eval) |

## §1 — Plist EnvironmentVariables additions

Source plist: `~/Library/LaunchAgents/com.zeus.live-trading.plist.locked-2026-05-04-cycle-asymmetry-platt-retrain.bak`

Add **one** key inside `<key>EnvironmentVariables</key><dict>`. Alphabetical placement keeps Z-prefix keys ordered; insert between `WU_API_KEY` and `ZEUS_HARVESTER_LIVE_ENABLED`:

```xml
		<key>ZEUS_ENTRY_FORECAST_READINESS_WRITER</key>
		<string>1</string>
```

> **DO NOT SET** `ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED` at initial launch — see §1.5 below.

**Why `ZEUS_ENTRY_FORECAST_READINESS_WRITER=1` is required**:

`ZEUS_ENTRY_FORECAST_READINESS_WRITER=1` — without it, `_entry_forecast_readiness_writer_flag_on()` returns `False`, `use_executable_forecast_cutover` is `False`, and the evaluator falls into the `else` branch at `src/engine/evaluator.py:1763-1792`. That branch calls `fetch_ensemble(... model="ecmwf_ifs025" ...)`, which `src/data/ensemble_client.py:149-160` rejects unconditionally for `ecmwf_open_data` because `ingest_class=None`. **Net effect when this flag is missing: 100% of entry candidates get rejected at `SIGNAL_QUALITY` with `SourceNotEnabled`.**

## §1.5 — Why ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED stays unset at launch

**DO NOT add `ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED=true` to the plist at initial launch.**

Setting this env-var activates the evidence-gated path (`evaluate_calibration_transfer_policy_with_evidence` with flag=on) which queries `validated_calibration_transfers` for ECMWF `target_source_id` rows.  At launch those rows are absent — ECMWF `calibration_pairs_v2` entries accumulate naturally over ~2-4 weeks post-launch.  The sequence of failures when the flag is prematurely set:

1. `_with_evidence` queries `validated_calibration_transfers` for ECMWF target → finds zero rows.
2. Missing row → `SHADOW_ONLY` status.
3. Every ECMWF entry candidate blocked → **silent live-entry kill**.

The correct launch path is the **legacy static mapping** in `evaluate_calibration_transfer_policy` (`src/data/calibration_transfer_policy.py:59`), accessed via the flag-off delegation at lines 161-170. This routes ECMWF Opendata forecasts to their TIGGE Platt models via `_TRANSFER_SOURCE_BY_OPENDATA_VERSION` (lines 38-41), which is correct because ECMWF Opendata and TIGGE archive are the same physical IFS ensemble (TIGGE = +48h archive mirror). Caller-side `live_promotion_approved` is now threaded through (commit 4584c150 + Fix G), so flag-off + caller-True → LIVE_ELIGIBLE for valid candidates.

This is the **operator-accepted at-launch path** (2026-05-10): the paired-equivalence diagnostic (`scripts/diagnose_opendata_tigge_equivalence.py`) now passes locally, confirming the physical-object chain is aligned. The legacy static-mapping path is therefore not a live-blocked workaround but the correctly-authorized launch path pending Phase B accumulation.

Phase B (≥2-4 weeks post-launch, once ECMWF pairs have accumulated) is when `ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED=true` becomes safe.  See `architecture/ecmwf_opendata_tigge_equivalence_2026_05_06.yaml` §4 and §5 for trigger conditions and upgrade steps.

### 1.5.1 — Erratum supersession (2026-05-10)

The `architecture/ecmwf_opendata_tigge_equivalence_2026_05_06.yaml` erratum block (dated 2026-05-07, status `EVIDENCE_GATED`) has been superseded as of 2026-05-10. All 4 local-alignment blockers are resolved:

| Blocker | Resolution | Commit |
|---|---|---|
| Grid 0.5°/0.25° misalignment | Plan A: 0.5°-canonical + 4×4 spatial downsample | `610d8680`, `71207d5b` |
| Same-issue/member/step paired pairs = 0 | A1+3h pipeline realignment; diagnostic script passes | `610d8680` |
| LOW comparator missing | LOW mn2t3 ingest + purity gates + contract-window authority | `71207d5b`, `1d9859d9`, `be302b91` |
| Step horizon capped at ≤240h | STEP_HOURS raised to 282h; closes #134 | `df90cf64` |

The yaml `status` field is now `BINDING_2026_05_10`. The `erratum_2026_05_07.superseded` block in that file contains the full commit-level rationale. The §1.5 "DO NOT SET" warning above remains correct — it is now justified by Phase B readiness timing, not by unresolved physical-object misalignment.

## §2 — Plist install + load procedure

```sh
plutil -lint ~/Library/LaunchAgents/com.zeus.live-trading.plist.locked-2026-05-04-cycle-asymmetry-platt-retrain.bak  # OK before edit
cp ~/Library/LaunchAgents/com.zeus.live-trading.plist.locked-2026-05-04-cycle-asymmetry-platt-retrain.bak \
   ~/Library/LaunchAgents/com.zeus.live-trading.plist
# apply §1 diff
plutil -lint ~/Library/LaunchAgents/com.zeus.live-trading.plist  # must report OK
launchctl unload ~/Library/LaunchAgents/com.zeus.live-trading.plist 2>/dev/null
launchctl load ~/Library/LaunchAgents/com.zeus.live-trading.plist
PID=$(launchctl list com.zeus.live-trading | awk '/PID/{print $3}')
ps -E -p "$PID" | tr ' ' '\n' | grep -E '^(ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED|ZEUS_ENTRY_FORECAST_READINESS_WRITER)='
# Must print both lines.
```

## §3 — Config + state preconditions for the writer path (verified 2026-05-10)

The writer flag in §1 is necessary but not sufficient. The reader path it activates additionally requires:

1. `config/settings.json::entry_forecast.rollout_mode` ≠ `"blocked"`. **Verified 2026-05-10: value is `"live"` ✅** — no operator action needed. Code path: `_write_entry_readiness_for_candidate` emits non-`BLOCKED` `readiness_state` rows when this string is non-blocked (`src/engine/evaluator.py:802-805`).
2. `state/entry_forecast_promotion_evidence.json` must exist and parse via `read_promotion_evidence` (`src/control/entry_forecast_promotion_evidence_io.py:224`). **Verified 2026-05-10: file exists at 530B, mtime 2026-05-06 ✅**. Re-verify content immediately before launch:
   ```sh
   python -m json.tool state/entry_forecast_promotion_evidence.json
   ```
3. `ensemble_snapshots_v2` must contain entries-eligible rows for each (city, target_local_date, source_id=ecmwf_open_data, cycle, horizon_profile) the daemon will price for. **Cross-domain serving (ecmwf_open_data) is now authorized via the legacy static mapping per §1.5.1 supersession** — calibration transfer flag-OFF + `live_promotion_approved=True` (Fix G, commit `4584c150`) routes ECMWF Opendata → TIGGE Platt → LIVE_ELIGIBLE. `validated_calibration_transfers` rows are the Phase B upgrade path, not a current launch precondition.

§3.1 and §3.2 are now both ✅ verified. The writer flag may stay ON (`1`) per §1.

## §4 — Lock release + monitoring

```sh
# Clear the daemon-lock control_overrides row (operator-only):
sqlite3 state/zeus-world.db "UPDATE control_overrides SET effective_until=datetime('now') WHERE override_id='operator:tigge_12z_gap:LIVE_UNSAFE_2026_05_04';"
# Verify daemon log shows no DeprecationWarning + no SourceNotEnabled:
grep -E 'DeprecationWarning|SourceNotEnabled|ENTRY_FORECAST_READER_DB_UNAVAILABLE|ENTRY_READINESS_MISSING' logs/zeus-live.err | tail -20
# Expect empty.
```

## §6 — Launchctl env scrub (P0 — must run before loading any plist)

`launchctl getenv ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED` currently returns `true` from a prior `launchctl setenv` that persists in the launchd user-domain across reboots. Plists do not set this env, but loaded daemons inherit it from the user-domain. With `true` inherited:

1. Daemons activate the evidence-gated path (`evaluate_calibration_transfer_policy_with_evidence`).
2. `validated_calibration_transfers` queries return zero rows for ECMWF target.
3. Every ECMWF entry candidate → SHADOW_ONLY → silent live-entry kill.

There is no log line for this — the daemon appears healthy while shadowing every signal.

**Mandatory pre-launch step**:

```sh
launchctl unsetenv ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED
launchctl getenv ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED
# Expected output: empty (the env is now unset at user-domain level)
```

After unsetenv, proceed to §2 (plist install + load) and §4 (lock release).

Do NOT add `<key>ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED</key><string>false</string>` to plists as a defensive measure — `unsetenv` is sufficient and `<string>false</string>` would still be truthy under Python `os.environ.get` boolean coercion if any code path uses string-truthiness.

## §5 — Rollback

If anything in §3 is uncertain at unlock time, set the writer flag (`ZEUS_ENTRY_FORECAST_READINESS_WRITER`) back to `0` in the plist and re-run §2. The calibration transfer OOS flag (`ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED`) is intentionally absent from the plist at launch — do not add it during rollback either. The legacy static-mapping path is the correct default for ECMWF Opendata at launch (see §1.5).

## Cross-references

- Plist source: `~/Library/LaunchAgents/com.zeus.live-trading.plist.locked-2026-05-04-cycle-asymmetry-platt-retrain.bak`
- Daemon lock: `state/zeus-world.db` `control_overrides` precedence-200 row `operator:tigge_12z_gap:LIVE_UNSAFE_2026_05_04`
- Calibration transfer architecture: `architecture/calibration_transfer_oos_design_2026-05-05.md`
- Entry forecast rollout-gate purge: `src/engine/evaluator.py:783-836` (gate-retirement comment block)
- Hard-block for ecmwf_open_data on legacy fetch path: `src/data/ensemble_client.py:149-160`
- Evidence-gate function: `src/data/calibration_transfer_policy.py:126-253`
- Migrated shadow callsite: `src/data/entry_forecast_shadow.py:175-194` (task #178)
- Phase 1 ingest watcher: `scripts/local_post_extract_chain.sh` → `state/post_extract_pipeline_<ts>.json` (task #165)

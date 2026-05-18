# Observability Structural Fix — Heartbeat/Daemon Pipeline Asymmetry
# Created: 2026-05-17
# Authority: MASS_TRIAGE_2026-05-17.md + findings F33/F85/F87/F91/F99/F100

## Findings Addressed
F33, F85, F87, F91, F99, F100 — all symptoms of ONE K-decision:
**heartbeat/status writers ship without a mandatory consumer registration.**

---

## Writer / Reader Inventory

| Artifact | Writer | Zeus Reader | OpenClaw Reader | Status |
|---|---|---|---|---|
| `state/daemon-heartbeat.json` | `src/main.py:_write_heartbeat` | `scripts/check_daemon_heartbeat.py`, `scripts/live_health_probe.py` | OpenClaw plist `--heartbeat-files` (enforced) | COVERED |
| `state/daemon-heartbeat-ingest.json` | `src/ingest_main.py:_write_ingest_heartbeat` | NONE | Plist `--heartbeat-files` arg listed but **sensor_layer1 treats it as "informational only"** — never actually read | **ORPHAN** |
| `state/forecast-live-heartbeat.json` | `src/ingest/forecast_live_daemon.py:_write_forecast_live_heartbeat` | `scripts/check_forecast_live_ready.py`, `scripts/live_health_probe.py` | Not listed | COVERED |
| `data/oracle_error_rates.heartbeat.json` | `scripts/bridge_oracle_to_calibration.py` via `state/paths.py:write_heartbeat` | NONE | Not listed | **ORPHAN** |
| `state/venue-heartbeat-keeper.json` | `src/control/heartbeat_supervisor.py:write_heartbeat_keeper_status` | `src/control/heartbeat_supervisor.py:ExternalHeartbeatSupervisor` | Not listed | COVERED (self-paired) |

### F33: Oracle MISSING — no escalation
`OracleStatus.MISSING` applies 0.5 Kelly multiplier silently in `src/strategy/oracle_penalty.py`.
No code path ever fires `discord_alerts` on persistent MISSING. A city absent from
`oracle_error_rates.json` for weeks degrades trade sizing without operator awareness.

### F85: heartbeat-sensor.err shows RED
OpenClaw `bin/heartbeat_sensor.py` always writes its severity line to stderr (line 283-286).
This is intentional sensor design — exit code is always 0. **Not a Zeus bug. Out of Zeus scope.**

### F87: forecast-live exit 1
Current state: `forecast-live-heartbeat.json` shows `status: alive`, fresh timestamp 2026-05-18.
F87 is historical — daemon is currently healthy. **RESOLVED; no code change needed.**

### F91: Heartbeat JSONs alert path unverified
The OpenClaw plist passes `--heartbeat-files` to `heartbeat_sensor.py`, but `sensor_layer1.py`
documents the arg as "informational only — currently not enforced." Alert path for
`daemon-heartbeat-ingest.json` staleness is unwired at the OpenClaw layer.

---

## K Decision (1 of N)

**"Writers ship without consumer registration."**

Every writer adds a JSON file to `state/` or `data/` with no enforcement that a reader exists.
The result: two orphan writers accumulate stale/missing data silently for weeks.

This is NOT N separate bugs. It is one structural decision: **no paired-existence contract.**

---

## Fix Shape

### Fix 1: oracle MISSING escalation (code change — deep_heartbeat.py)

`deep_heartbeat.py` is already in the OpenClaw sensor's critical path:
`sensor_layer1.py` → `deep_heartbeat.py exit 2` → `classify.py: RED deep_heartbeat_critical`
→ OpenClaw agent session → Discord alert.

Add `check_oracle_missing()` to `deep_heartbeat.py`. If `oracle_error_rates.json` is absent
or all entries are MISSING for >7 days, exit 2 (critical). This wires the oracle MISSING
state into the existing RED alert chain without new infrastructure.

See: `scripts/deep_heartbeat.py` — `check_oracle_missing` function added by this commit.

### Fix 2: Paired-existence antibody (CI test)

`tests/test_heartbeat_writer_consumer_registry.py` — asserts that every JSON heartbeat
writer registered in `HEARTBEAT_REGISTRY` has ≥1 registered consumer. Fails CI on new
orphan writers. The registry is a hand-curated constant in the test file; adding a new
writer requires adding a consumer entry or it fails immediately.

`daemon-heartbeat-ingest.json` is registered with consumer `PENDING_OPENCLAW_ENFORCEMENT`
as a documented gap (the plist arg is informational) — this surfaces the gap in CI output
rather than suppressing it.

### Fix 3: daemon-heartbeat-ingest consumer (not implemented — operator decision needed)

The ingest heartbeat has no Zeus-side consumer. The correct fix is to make
`sensor_layer1.py` (OpenClaw layer) actually enforce the `--heartbeat-files` arg.
This is OpenClaw-layer code outside Zeus scope. This task documents the gap;
operator must action the OpenClaw plist + sensor_layer1 enforcement separately.

---

## Files Changed

- `scripts/deep_heartbeat.py` — added `check_oracle_missing()` check (F33 fix)
- `tests/test_heartbeat_writer_consumer_registry.py` — new antibody CI test (F99/F100 fix)

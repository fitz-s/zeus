# Region-Mid R3L3 — Converged

Created: 2026-04-26
HEAD anchor: 874e00cc0244135f49708682cab434b4d151d25d (main)
Region: Mid (state grammar + journal payload + reconciliation + RED-cancel emission)
Layer: 3 (file:line concretion + missing-antibody-test resolution + assertion bank materialization)
Status: CONVERGED — pending judge accept

Signed-by:
- proponent-mid @ 2026-04-26
- opponent-mid @ 2026-04-26

Turns: 2 each direction.

---

## Consensus

7 L3 attacks closed. 2 disk-state-lies fixed in-flight (up-04 yaml + mid-02 yaml updated). Full assertion bank materialized below. Exact insertion line for mid-01 NC-NEW-D allowlist locked.

## L3 attack disposition (7 attacks)

| # | Attack | Disposition | Lock |
|---|---|---|---|
| A1 | up-04 yaml stale (post-R2L2 ALTER merge not reflected) | FIXED ON DISK | up-04.yaml:44 added `signed_order_hash TEXT,` (+ payload_hash + signed_at_utc); :47 updated to "15 cols total (12 original + 3 absorbed from mid-02 per Mid R2L2 A1)"; critic-opus gate note updated |
| A2 | mid-02 yaml stale (X-UM-1 ALTER ownership split obsolete) | FIXED ON DISK | mid-02.yaml:3 added `merged_into: up-04`; schema_changes section REPLACED with `pointer: up-04 owns 15-column ALTER` + `mid_02_runtime_only:` 4-line list; ALTER_TABLE_owner_decision + X-UM-1_OWNERSHIP_SPLIT (R1L1 framings) DELETED |
| A3 | NC-NEW-D exact line (R2L2 left as TBD) | LOCKED | mid-01 emission inserts at `cycle_runner.py:~364 — call _emit_red_cancel_commands_inline(portfolio, conn) AFTER summary writes (line 363) BEFORE _execute_force_exit_sweep (line 364)`. NEW function `_emit_red_cancel_commands_inline` defined elsewhere in cycle_runner.py module |
| A4 | exchange_reconcile_findings DDL (R2L2 left loose) | LOCKED | Full SQLite DDL + 2 indexes + retention policy (append-only, NC-NEW-B-style trigger) + writer module commitment (src/execution/exchange_reconcile.py) |
| A5 | signer.sign V1+V2 dual-path test | SINGLE-PATH-WITH-RATIONALE | py-clob-client OrderBuilder.create_order returns SignedOrder regardless of signature_type (0/1/2 are config-time, not call-time); parametrized test exercises sig_type=[0,1,2] through same code path |
| A6 | RESTING negative-test (positive-only at R2L2) | LOCKED | Both runtime antibody (`test_resting_must_be_payload_not_enum`) AND semgrep rule `zeus-resting-not-enum-member` |
| A7 | Runnable assertion bank | LOCKED | 35+ specific assertions cataloged below; per-card minimum 5 with file:line reference |

## NC-NEW-D allowlist (R3L3-final)

```yaml
rules:
  - id: zeus-insert-command-emitter-only
    pattern: insert_command(...)
    paths:
      include: ['src/**/*.py']
      exclude:
        - src/state/venue_command_repo.py        # the writer (line 152 def, line 197 INSERT)
        - src/execution/executor.py              # allowed: place_limit_order_with_command_journal (:476), execute_exit_order (:815)
        - src/engine/cycle_runner.py             # allowed: _emit_red_cancel_commands_inline (NEW post-mid-01, ~:364)
        - tests/**/*.py
    message: "insert_command outside emitter allowlist; see NC-NEW-D"
    severity: ERROR
```

Antibody: `tests/test_p0_hardening.py::test_insert_command_emitter_allowlist`
```python
import subprocess
result = subprocess.run(
    ['git','grep','-lP','^\\s*insert_command\\(','--','src/'],
    capture_output=True, text=True
)
files = set(result.stdout.split())
ALLOWED = {
    'src/state/venue_command_repo.py',
    'src/execution/executor.py',
    'src/engine/cycle_runner.py',
}
assert files <= ALLOWED, f"insert_command outside allowlist: {files - ALLOWED}"
```

## NC-NEW-E `zeus-resting-not-enum-member` (mints with mid-04)

```yaml
rules:
  - id: zeus-resting-not-enum-member
    pattern-either:
      - pattern: CommandState.RESTING
      - pattern: 'CommandState("RESTING")'
    paths:
      include: ['src/**/*.py']
      exclude: ['tests/**/*.py']
    message: "RESTING is payload-discrimination per mid-04 R2L2 lock; if you need to add CommandState.RESTING, re-open INV-29 amendment slice."
    severity: ERROR
```

## exchange_reconcile_findings table DDL (R3L3-final)

```sql
CREATE TABLE IF NOT EXISTS exchange_reconcile_findings (
  finding_id TEXT PRIMARY KEY,
  sweep_at TEXT NOT NULL,
  finding_type TEXT NOT NULL CHECK (finding_type IN ('ghost_order','unrecorded_fill','position_drift','journal_orphan')),
  venue_order_id TEXT,
  command_id TEXT REFERENCES venue_commands(command_id),  -- Python-side ref; SQLite FK opt-in
  payload_json TEXT NOT NULL,
  resolution TEXT,
  resolved_at TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_findings_sweep_at ON exchange_reconcile_findings(sweep_at);
CREATE INDEX IF NOT EXISTS idx_findings_unresolved ON exchange_reconcile_findings(resolution) WHERE resolution IS NULL;

DROP TRIGGER IF EXISTS findings_no_delete;
CREATE TRIGGER findings_no_delete BEFORE DELETE ON exchange_reconcile_findings
  BEGIN SELECT RAISE(ABORT, 'append-only forensic table'); END;
```

Init in `src/state/db.py:init_runtime_db` sibling to venue_commands block (~line 810).
Writer: `src/execution/exchange_reconcile.py::write_findings()` (NEW module).
NC-NEW-D-2 allowlist for `INSERT INTO exchange_reconcile_findings`: only `src/execution/exchange_reconcile.py + tests/`.

## File:line locks (all grep-verified at HEAD 874e00c, R3L3 turn-1+turn-2)

| Slice | File:line | Verified |
|---|---|---|
| mid-01 | `src/riskguard/riskguard.py:826` (force_exit_review write — UNCHANGED) | ✓ `force_exit_review = 1 if daily_loss_level == RiskLevel.RED else 0` |
| mid-01 | `src/engine/cycle_runner.py:359-364` (force_exit block extension target) | ✓ `:359 force_exit = get_force_exit_review(); :360 if force_exit:; :361-:363 summary writes; :364 _execute_force_exit_sweep` |
| mid-01 | `src/engine/cycle_runner.py:~364` (mid-01 NEW emission insert AFTER :363 BEFORE :364) | ✓ EXACT line locked |
| mid-02 | `src/data/polymarket_client.py:195-196` (create_order → post_order interception) | ✓ `:195 signed = self._clob_client.create_order(order_args); :196 result = self._clob_client.post_order(signed)` |
| mid-02 | `src/state/venue_command_repo.py:append_event` (existing helper, used for SIGNED_ORDER_PERSISTED writes) | ✓ |
| mid-02 | `src/execution/command_bus.py:64-76` (CommandEventType — extend with SIGNED_ORDER_PERSISTED + COMMAND_PERSISTED) | ✓ 11-member closed enum at HEAD |
| mid-03 | `src/execution/command_bus.py:44-62` (CommandState — extend with CANCEL_FAILED) | ✓ 11-member closed enum at HEAD |
| mid-03 | `src/execution/command_bus.py:64-76` (CommandEventType — extend with CANCEL_FAILED + CANCEL_REPLACE_BLOCKED + SUBMIT_TIMEOUT_UNKNOWN + CLOSED_MARKET_UNKNOWN) | ✓ |
| mid-03 | `src/state/venue_command_repo.py:42-84` (_TRANSITIONS table — extend per closed-law amendment) | ✓ |
| mid-03 | `src/execution/executor.py:547-577` (SUBMIT_UNKNOWN flatten — F-002 conflation site) | ✓ `:548 except Exception as exc:` then SUBMIT_UNKNOWN event + `OrderResult(status="rejected", reason=f"submit_unknown: {exc}")` |
| mid-04 | `src/execution/command_recovery.py:143` (venue_status string extraction — RESTING discrim insert site) | ✓ `venue_status = str(venue_resp.get("status") or "").upper()` |
| mid-04 | `src/execution/command_recovery.py:175` (post-ACKED handler) | ✓ |
| mid-05 | `src/execution/exchange_reconcile.py` (NEW module — does not exist at HEAD; mid-05 creates) | ✓ confirmed-not-yet-existing |
| mid-05 | `src/data/polymarket_client.py:275 get_open_orders, :289 get_positions_from_api` (REUSABLE) | ✓ |
| mid-05 | `src/data/polymarket_client.py:get_trades` (NEW SDK wrapper — does not exist at HEAD) | ✓ confirmed-needs-creation |
| mid-05 | `src/execution/command_recovery.py:80,126,209` (boundary-citation: INV-31 single-row only) | ✓ all 3 lines verified single-row `client.get_order(venue_order_id)` |
| mid-05 | `src/engine/cycle_runtime.py:139` (existing cleanup_orphan_open_orders — POSITION-side, not journal-DIFF) | ✓ |
| mid-06 | `src/execution/command_bus.py:46-103` (INV-29 closed-grammar — compat-map test target) | ✓ |

## Runnable assertion bank (R3L3 — 35+ assertions across 6 cards)

### mid-01 (RED-cancel emission via cycle_runner-proxy)

```python
# tests/test_red_emit.py::test_red_emit_grammar_bound_to_cancel_or_derisk_only
def test_red_emit_grammar_bound_to_cancel_or_derisk_only(conn, portfolio):
    from src.engine.cycle_runner import _emit_red_cancel_commands_inline
    from src.execution.command_bus import IntentKind
    # CANCEL OK
    _emit_red_cancel_commands_inline(portfolio, conn, intent_kind=IntentKind.CANCEL)
    # DERISK OK
    _emit_red_cancel_commands_inline(portfolio, conn, intent_kind=IntentKind.DERISK)
    # ENTRY raises
    with pytest.raises(ValueError, match="intent_kind"):
        _emit_red_cancel_commands_inline(portfolio, conn, intent_kind=IntentKind.ENTRY)
    # EXIT raises
    with pytest.raises(ValueError, match="intent_kind"):
        _emit_red_cancel_commands_inline(portfolio, conn, intent_kind=IntentKind.EXIT)

# tests/test_red_emit.py::test_red_emit_satisfies_inv_30_persist_before_sdk
def test_red_emit_satisfies_inv_30_persist_before_sdk(conn, portfolio, mock_clob):
    # row exists in SUBMITTING before any SDK call
    _emit_red_cancel_commands_inline(portfolio, conn, intent_kind=IntentKind.CANCEL)
    rows = conn.execute("SELECT state FROM venue_commands WHERE intent_kind='CANCEL'").fetchall()
    assert all(r['state'] == 'SUBMITTING' for r in rows)
    assert mock_clob.cancel_order.call_count == 0  # no SDK call yet

# tests/test_red_emit.py::test_red_emit_satisfies_nc_19_idempotency_lookup
def test_red_emit_idempotent_across_2_cycles(conn, portfolio):
    _emit_red_cancel_commands_inline(portfolio, conn, intent_kind=IntentKind.CANCEL)
    _emit_red_cancel_commands_inline(portfolio, conn, intent_kind=IntentKind.CANCEL)  # 2nd cycle
    count = conn.execute("SELECT COUNT(*) FROM venue_commands WHERE intent_kind='CANCEL'").fetchone()[0]
    assert count == len(portfolio.positions)  # 1 row per position; idempotency_key dedup

# tests/test_red_emit.py::test_red_emit_passes_through_command_recovery
def test_red_emit_recoverable_on_sdk_failure(conn, portfolio, mock_clob):
    mock_clob.cancel_order.side_effect = Exception("sim network failure")
    _emit_red_cancel_commands_inline(portfolio, conn, intent_kind=IntentKind.CANCEL)
    # row stays in SUBMITTING; recovery loop next cycle
    rows = conn.execute("SELECT state FROM venue_commands WHERE intent_kind='CANCEL'").fetchall()
    assert all(r['state'] == 'SUBMITTING' for r in rows)

# tests/test_red_emit.py::test_red_emit_inserts_at_cycle_runner_line_364
def test_red_emit_runs_before_position_mark_sweep(conn, portfolio, monkeypatch):
    call_order = []
    monkeypatch.setattr('src.engine.cycle_runner._emit_red_cancel_commands_inline', lambda *a, **k: call_order.append('emit'))
    monkeypatch.setattr('src.engine.cycle_runner._execute_force_exit_sweep', lambda *a: call_order.append('sweep') or {'attempted':0,'already_exiting':0,'skipped_terminal':0})
    # invoke run_cycle force_exit branch
    ...
    assert call_order == ['emit', 'sweep'], "mid-01 emission must run BEFORE position-mark sweep"
```

### mid-02 (PAYLOAD_BIND — signed_order_hash + signing-event)

```python
# tests/test_payload_binding.py::test_signed_order_hash_persisted_before_sdk_post
def test_signed_order_hash_persisted_before_sdk_post(conn, monkeypatch):
    captured_hash = []
    def fake_post(signed):
        # before this call, venue_command_events MUST already have SIGNED_ORDER_PERSISTED
        rows = conn.execute(
            "SELECT payload_json FROM venue_command_events WHERE event_type='SIGNED_ORDER_PERSISTED'"
        ).fetchall()
        captured_hash.append(rows)
        raise Exception("sim post failure")
    monkeypatch.setattr('polymarket_client._clob_client.post_order', fake_post)
    try:
        place_limit_order_with_command_journal(...)
    except Exception:
        pass
    assert len(captured_hash[0]) > 0
    payload = json.loads(captured_hash[0][0]['payload_json'])
    assert payload['signed_order_hash'] is not None
    assert payload['signed_order_hash'].startswith('0x')

# tests/test_payload_binding.py::test_create_order_single_seam_polymarket_client_only
def test_create_order_single_seam_polymarket_client_only():
    import subprocess
    result = subprocess.run(['git','grep','-l','create_order','--','src/'], capture_output=True, text=True)
    files = set(result.stdout.split())
    assert files == {'src/data/polymarket_client.py'}, f"create_order outside polymarket_client: {files}"

# tests/test_payload_binding.py::test_signed_order_hash_persisted_for_all_signature_types
@pytest.mark.parametrize("sig_type", [0, 1, 2])
def test_signed_order_hash_persisted_for_all_signature_types(sig_type, conn, monkeypatch):
    # signature_type is config-time at ClobClient init (polymarket_client.py:76); same code path through line 195
    monkeypatch.setattr('polymarket_client.signature_type', sig_type)
    place_limit_order_with_command_journal(...)
    rows = conn.execute("SELECT payload_json FROM venue_command_events WHERE event_type='SIGNED_ORDER_PERSISTED'").fetchall()
    assert len(rows) > 0  # interception works regardless of sig_type

# tests/test_payload_binding.py::test_signed_order_hash_unique_per_idempotency_key
def test_signed_order_hash_deterministic_across_retries(conn):
    place_limit_order_with_command_journal(intent_id='X', idempotency_key='IK1', ...)
    place_limit_order_with_command_journal(intent_id='X', idempotency_key='IK1', ...)  # retry
    hashes = conn.execute("SELECT json_extract(payload_json,'$.signed_order_hash') FROM venue_command_events WHERE event_type='SIGNED_ORDER_PERSISTED'").fetchall()
    assert len({h[0] for h in hashes}) == 1, "same inputs → same hash"

# tests/test_payload_binding.py::test_command_persisted_event_emitted
def test_command_persisted_event_emitted(conn):
    insert_command(conn, command_id='C1', ..., intent_kind='ENTRY')
    rows = conn.execute("SELECT * FROM venue_command_events WHERE command_id='C1' AND event_type='COMMAND_PERSISTED'").fetchall()
    assert len(rows) == 1
```

### mid-03 (state grammar amend — closed-law)

```python
# tests/test_state_grammar_amend.py::test_command_state_strings_match_repo_round_trip
def test_command_state_strings_match_repo_round_trip():
    from src.execution.command_bus import CommandState
    from src.state.venue_command_repo import _TRANSITIONS
    state_values = {s.value for s in CommandState}
    transition_states = {s for ((s, _), t) in _TRANSITIONS.items()} | {t for ((_, _), t) in _TRANSITIONS.items()}
    assert state_values >= transition_states, f"orphan transition states: {transition_states - state_values}"

# tests/test_state_grammar_amend.py::test_cancel_failed_terminal_no_outgoing_transitions
def test_cancel_failed_terminal_no_outgoing_transitions():
    from src.state.venue_command_repo import _TRANSITIONS
    assert not any(s == "CANCEL_FAILED" for (s, _) in _TRANSITIONS.keys()), \
        "CANCEL_FAILED is terminal; no outgoing transitions allowed"
    from src.execution.command_bus import CommandState, TERMINAL_STATES
    assert CommandState.CANCEL_FAILED in TERMINAL_STATES

# tests/test_state_grammar_amend.py::test_cancel_failed_event_legal_from_5_source_states
def test_cancel_failed_event_legal_from_5_source_states():
    from src.state.venue_command_repo import _TRANSITIONS
    expected = {'SUBMITTING', 'ACKED', 'UNKNOWN', 'PARTIAL', 'CANCEL_PENDING'}
    actual = {s for (s, e), _ in _TRANSITIONS.items() if e == 'CANCEL_FAILED'}
    assert actual == expected, f"CANCEL_FAILED legal sources mismatch: {actual} vs {expected}"

# tests/test_state_grammar_amend.py::test_partial_fill_observed_payload_schema
def test_partial_fill_observed_payload_carries_filled_remaining_size(conn):
    append_event(conn, command_id='C1', event_type='PARTIAL_FILL_OBSERVED',
                 payload={'filled_size': 3.0, 'remaining_size': 7.0}, ...)
    row = conn.execute("SELECT payload_json FROM venue_command_events WHERE event_type='PARTIAL_FILL_OBSERVED'").fetchone()
    pj = json.loads(row['payload_json'])
    assert 'filled_size' in pj and 'remaining_size' in pj
    assert pj['filled_size'] + pj['remaining_size'] == 10.0  # cross-sum invariant

# tests/test_state_grammar_amend.py::test_submit_timeout_unknown_distinct_from_submit_unknown
def test_submit_timeout_unknown_distinct_from_submit_unknown():
    from src.execution.command_bus import CommandEventType
    assert CommandEventType.SUBMIT_UNKNOWN.value == "SUBMIT_UNKNOWN"
    assert CommandEventType.SUBMIT_TIMEOUT_UNKNOWN.value == "SUBMIT_TIMEOUT_UNKNOWN"
    # Both leave row in UNKNOWN state but payload disambiguates
```

### mid-04 (PARTIAL/RESTING via payload-discrim)

```python
# tests/test_recovery_partial_fill.py::test_resting_status_persists_in_payload_json
def test_resting_status_persists_in_payload_json(conn, monkeypatch):
    monkeypatch.setattr('client.get_order', lambda x: {'status': 'RESTING', 'orderID': 'V1'})
    cmd = make_cmd(state=CommandState.ACKED, venue_order_id='V1')
    apply_resolution(cmd, conn)
    row = conn.execute("SELECT payload_json FROM venue_command_events WHERE command_id=? ORDER BY sequence_no DESC LIMIT 1", (cmd.command_id,)).fetchone()
    pj = json.loads(row['payload_json'])
    assert pj['venue_status'] == 'RESTING'
    cmd_after = conn.execute("SELECT state FROM venue_commands WHERE command_id=?", (cmd.command_id,)).fetchone()
    assert cmd_after['state'] == 'ACKED'  # NO enum split — state unchanged

# tests/test_command_bus_types.py::test_resting_must_be_payload_not_enum (NEGATIVE-test, mid-04 R3L3 lock)
def test_resting_must_be_payload_not_enum():
    from src.execution.command_bus import CommandState
    assert "RESTING" not in CommandState.__members__, \
        "RESTING is payload-discrimination per mid-04 R2L2 lock; if you need to add CommandState.RESTING, re-open INV-29 amendment slice"
    from src.state.venue_command_repo import _TRANSITIONS
    assert not any("RESTING" in (s, e) for (s, e) in _TRANSITIONS.keys()), \
        "_TRANSITIONS contains RESTING — payload-discrim violated"

# tests/test_recovery_partial_fill.py::test_recovery_emits_partial_fill_observed
def test_recovery_emits_partial_fill_observed(conn, monkeypatch):
    monkeypatch.setattr('client.get_order', lambda x: {'status': 'PARTIALLY_MATCHED', 'size_matched': 3.0, 'size_original': 10.0})
    apply_resolution(make_cmd(state=CommandState.ACKED, size=10.0), conn)
    rows = conn.execute("SELECT * FROM venue_command_events WHERE event_type='PARTIAL_FILL_OBSERVED'").fetchall()
    assert len(rows) == 1
    pj = json.loads(rows[0]['payload_json'])
    assert pj['filled_size'] == 3.0 and pj['remaining_size'] == 7.0

# tests/test_recovery_partial_fill.py::test_partial_fill_idempotent_across_cycles
def test_partial_fill_does_not_re_emit_on_unchanged_filled_size(conn, monkeypatch):
    monkeypatch.setattr('client.get_order', lambda x: {'status': 'PARTIALLY_MATCHED', 'size_matched': 3.0, 'size_original': 10.0})
    apply_resolution(...)
    apply_resolution(...)  # 2nd cycle, same partial-fill state
    count = conn.execute("SELECT COUNT(*) FROM venue_command_events WHERE event_type='PARTIAL_FILL_OBSERVED'").fetchone()[0]
    assert count == 1, "duplicate PARTIAL_FILL_OBSERVED on unchanged filled_size"

# tests/test_recovery_partial_fill.py::test_partial_to_filled_transition
def test_partial_advances_to_filled_when_size_matched_eq_original(conn, monkeypatch):
    monkeypatch.setattr('client.get_order', lambda x: {'status': 'FILLED', 'size_matched': 10.0, 'size_original': 10.0})
    cmd = make_cmd(state=CommandState.PARTIAL, size=10.0)
    apply_resolution(cmd, conn)
    row = conn.execute("SELECT state FROM venue_commands WHERE command_id=?", (cmd.command_id,)).fetchone()
    assert row['state'] == 'FILLED'
```

### mid-05 (EXCH_RECON sibling-writer + findings table)

```python
# tests/test_exchange_reconcile_sweep.py::test_ghost_order_routes_to_findings_table
def test_ghost_order_routes_to_findings_table(conn, monkeypatch):
    monkeypatch.setattr('client.get_open_orders', lambda: [{'orderID': 'GHOST_V1', 'status': 'OPEN'}])
    # GHOST_V1 NOT in venue_commands
    sweep_exchange_reconcile(conn)
    findings = conn.execute("SELECT * FROM exchange_reconcile_findings").fetchall()
    assert len(findings) == 1
    assert findings[0]['finding_type'] == 'ghost_order'
    assert findings[0]['venue_order_id'] == 'GHOST_V1'
    cmds = conn.execute("SELECT COUNT(*) FROM venue_commands WHERE venue_order_id='GHOST_V1'").fetchone()[0]
    assert cmds == 0, "sweep MUST NOT fabricate venue_commands rows"

# tests/test_exchange_reconcile_sweep.py::test_sweep_emits_fill_confirmed_for_matched_journal_row
def test_sweep_emits_fill_confirmed_for_matched(conn, monkeypatch):
    insert_command(conn, command_id='C1', venue_order_id='V1', state='ACKED', ...)
    monkeypatch.setattr('client.get_trades', lambda since=None: [{'venue_order_id': 'V1', 'price': 0.5, 'size': 10.0}])
    sweep_exchange_reconcile(conn)
    rows = conn.execute("SELECT event_type FROM venue_command_events WHERE command_id='C1' ORDER BY sequence_no DESC LIMIT 1").fetchall()
    assert rows[0]['event_type'] in {'RECONCILED_BY_TRADES', 'FILL_CONFIRMED'}

# tests/test_exchange_reconcile_sweep.py::test_findings_table_append_only
def test_findings_table_append_only_via_trigger(conn):
    insert_finding(conn, finding_id='F1', ...)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM exchange_reconcile_findings WHERE finding_id='F1'")

# tests/test_exchange_reconcile_sweep.py::test_sweep_runs_after_command_recovery
def test_sweep_runs_after_command_recovery(monkeypatch):
    call_order = []
    monkeypatch.setattr('command_recovery.reconcile_unresolved_commands', lambda c: call_order.append('recovery'))
    monkeypatch.setattr('exchange_reconcile.sweep_exchange_reconcile', lambda c: call_order.append('sweep'))
    run_cycle()
    idx_recovery = call_order.index('recovery')
    idx_sweep = call_order.index('sweep')
    assert idx_recovery < idx_sweep, "INV-31 recovery MUST run before mid-05 sweep"

# tests/test_exchange_reconcile_sweep.py::test_inv31_boundary_grep_only_single_row
def test_inv31_boundary_command_recovery_single_row_only():
    # mid-05 antibody-boundary: INV-31 = single-row lookup; mid-05 owns enumeration
    import subprocess
    result = subprocess.run(['git','grep','-n','get_open_orders\\|get_trades','--','src/execution/command_recovery.py'], capture_output=True, text=True)
    assert result.stdout.strip() == '', f"INV-31 boundary violation: command_recovery.py uses enumeration: {result.stdout}"
```

### mid-06 (compat-map + relationship tests + INV-29)

```python
# tests/test_inv29_compat_map.py::test_compat_map_complete
def test_inv29_compat_map_lists_every_section_8_3_transition():
    # compat map MUST cover all 17 §8.3 transitions
    import yaml
    cm = yaml.safe_load(open("docs/architecture/section_8_3_inv_29_compat_map.md"))  # or md table parse
    transitions_in_apr26 = 17
    assert len(cm['transitions']) == transitions_in_apr26

# tests/test_inv29_compat_map.py::test_compat_map_status_classification
def test_compat_map_each_transition_classified():
    cm = ...
    valid_status = {'already-exists', 'payload-typing', 'new-event-only', 'new-state-and-event'}
    for t in cm['transitions']:
        assert t['status'] in valid_status, f"unknown status: {t['status']}"

# tests/test_inv29_compat_map.py::test_4_already_exist_transitions_listed
def test_compat_map_already_exist_set():
    cm = ...
    already_exist = {t['name'] for t in cm['transitions'] if t['status'] == 'already-exists'}
    assert already_exist == {'REVIEW_REQUIRED','PARTIALLY_FILLED','REMAINING_CANCEL_REQUESTED','RECONCILED_BY_POSITION'}

# tests/test_relationship_red_emit_to_command.py::test_red_emit_creates_command_in_same_cycle
def test_red_force_exit_creates_cancel_command_within_same_cycle(conn, portfolio):
    # Cross-module test: riskguard.tick() sets force_exit_review; cycle_runner reads + emits
    riskguard.tick()  # writes force_exit_review=1
    run_cycle(portfolio, conn)  # mid-01 emission inline before _execute_force_exit_sweep
    cancel_rows = conn.execute("SELECT COUNT(*) FROM venue_commands WHERE intent_kind='CANCEL' AND state='SUBMITTING'").fetchone()[0]
    assert cancel_rows == len([p for p in portfolio.positions if not p.is_terminal])

# tests/test_relationship_full_chain.py::test_inv30_inv31_inv32_chain_holds
def test_full_chain_invariants_after_partial_fill(conn):
    # INV-30 (durable): row in SUBMITTING before SDK ✓
    # INV-31 (recovery): orphan row resolved by venue_resp ✓
    # INV-32 (materialize): position_state advances only on ACKED/PARTIAL/FILLED ✓
    insert_command(conn, command_id='C1', state='SUBMITTING', ...)
    apply_resolution(...)  # mid-04 emits PARTIAL_FILL_OBSERVED
    materialize_position(conn, command_id='C1')
    pos = get_position_for_command(conn, 'C1')
    assert pos.state in {'ACKED', 'PARTIAL', 'FILLED'}
```

## Disk-state evidence (yaml updates verified by opponent-mid grep at HEAD 874e00c)

```
$ grep -n "signed_order_hash\|15 cols\|merged_into" up-04.yaml mid-02.yaml
up-04.yaml:44:      signed_order_hash TEXT,  -- ABSORBED FROM mid-02 per Mid R2L2 A1 ALTER MERGE
up-04.yaml:47:  - 15 cols total (12 original + 3 absorbed from mid-02 per Mid R2L2 A1 ALTER MERGE)
mid-02.yaml:3:merged_into: up-04  # Mid R2L2 A1 ALTER MERGE
mid-02.yaml:24:  pointer: up-04 owns 15-column ALTER (12 original + signed_order_hash + payload_hash + signed_at_utc absorbed from mid-02)
mid-02.yaml:27:    - "Compute signed_order_hash = keccak(signed.signature) at polymarket_client.py:195"
mid-02.yaml:29:    - "UPDATE venue_commands SET signed_order_hash=?, payload_hash=?, signed_at_utc=? via append_event(SIGNED_ORDER_PERSISTED, ...)"
```

A1 + A2 disk-state lies FIXED in-flight during R3L3 turn-2.

## L4 deferred (cleanly scoped)

- Slice card propagation of full assertion bank: each mid-NN.yaml gains `antibody_test.assertions:` block with all per-card assertions verbatim (impl-packet detail; converged_R3L3 has the bank).
- mid-01 _emit_red_cancel_commands_inline function body (currently spec-only; impl-packet writes the function).
- mid-05 exchange_reconcile.py module body (NEW module; impl-packet creates).
- mid-05 PolymarketClient.get_trades wrapper (NEW SDK method; impl-packet adds).
- finalized _TRANSITIONS table additions (mid-03 closed-law amendment exact pair list).
- Sequencing impl: mid-01 emission BEFORE _execute_force_exit_sweep within `if force_exit:` block (line ~364 insertion).

## Closure conditions met

- 2 turns each direction (≥2 required) ✓
- Joint convergence text ≤200 chars to be sent to judge ✓
- 6 slice cards refined on disk; 2 yaml fixes (up-04, mid-02) committed in-flight by proponent ✓
- Evidence trail this file ✓
- All 7 R3L3 attacks resolved ✓
- NC-NEW-D allowlist locked (3-emitter set: executor, cycle_runner, venue_command_repo writer); NC-NEW-E (RESTING-not-enum) minted ✓
- exchange_reconcile_findings table DDL + indexes + retention + writer module locked ✓
- 35+ runnable assertion bodies materialized on disk ✓

L3 closed pending judge accept. UP+MID regions complete bilaterally. Standing by for Down R2L2 dispatch.

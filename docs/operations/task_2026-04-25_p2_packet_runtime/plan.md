# P2 Packet Runtime Plan

Date: 2026-04-25
Branch: `p2-packet-runtime`
Worktree: `/Users/leofitz/.openclaw/workspace-venus/zeus-packet-runtime`
Status: in progress

## Background

Five separate runtime burdens were measured during the topology admission
hardening session (commits `0ca6db9` → `1e5cad9`):

1. Pre-task discovery tax — multiple doctor commands, ~1.6s cold-start each
2. Packet scope is prose only, invisible to git
3. 14 manifest YAMLs hand-maintained per src change
4. Closeout writes touch ~9 files in 4 directories
5. AGENTS.md context permanently consumes 1044 lines

The full design lives in
`/Users/leofitz/.gemini/antigravity/brain/66830979-3b25-490a-b401-8c50adc16d41/implementation_plan.md`.

## Scope

### In scope (machine-enforced via scope.yaml)

- `scripts/zpkt.py` — primary CLI
- `scripts/_zpkt_scope.py` — scope load/match helpers
- `.zeus-githooks/pre-commit` — soft-warn pre-commit hook
- `.zeus-githooks/commit-msg` — Pscb-Bypass trailer enforcement
- `architecture/scope_schema.json` — scope.yaml JSON Schema
- `tests/test_zpkt.py` — CLI behavioural tests
- `tests/test_pscb_hook.py` — hook behavioural tests
- `docs/operations/packet_scope_protocol.md` — protocol reference
- `docs/operations/task_2026-04-25_p2_packet_runtime/**` — packet evidence
- packet companions: `architecture/script_manifest.yaml`,
  `architecture/test_topology.yaml`, `architecture/docs_registry.yaml`,
  `README.md`, `AGENTS.md`

### Out of scope

- Production DB mutation
- AGENTS.md de-bloat (separate future packet)
- topology_doctor daemon mode / cold-start removal
- Wholesale migration of historical packets to scope.yaml
- Hard-block enforcement (deferred until 1 month soft-warn telemetry)

## Deliverables

1. `zpkt` CLI with subcommands: start, status, scope, commit, close,
   setup, audit-bypass, park, unpark.
2. Soft-warn pre-commit + commit-msg hooks installed via
   `core.hooksPath`.
3. Tests covering CLI lifecycle and hook behavior; all existing tests
   stay green.
4. Protocol doc + README setup line.

## Verification

- `pytest tests/test_zpkt.py tests/test_pscb_hook.py` — green
- Reproduce the concurrent-commit scenario; expect soft-warn output.
- Use `zpkt close` to land *this very packet* — dogfooding the
  closeout flow.

## Related artifacts

- `implementation_plan.md` (agent artifact dir): full design rationale
- `process_friction_audit.md`: friction inventory motivating this work
- Predecessor commits on `midstream_remediation`: `0eedc7c` (navigation
  scope), `11c6315` (pytest live_topology), `1e5cad9` (state untrack)

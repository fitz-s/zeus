# Topology Profile Resolver Stability Plan

Date: 2026-04-29
Branch: `topology-profile-resolver-stability-2026-04-29`
Status: implementation packet

## Goal

Prevent agent-runtime routing from selecting a semantic profile solely because
the changed files include shared registry or control-plane files.

## Scope

Allowed:

- topology digest resolver and runtime route-card output
- navigation CLI input contract
- topology schema and generated digest profile mirror
- focused regression tests
- packet evidence and operations registry row

Not allowed:

- live trading behavior
- source-routing, settlement, calibration, risk, or execution semantics
- production DB or state mutation
- broad rewrite of existing profiles

## Required Invariants

- Admission remains the write-authority signal.
- Forbidden files still win.
- Generic fallback still admits no files.
- Typed intent may choose a profile but never bypasses admission.
- Shared registry/control-plane files may create maintenance context but cannot
  choose a business/runtime profile by themselves.

## Implementation Steps

1. Add guard tests for shared registry file contamination and navigation
   `--changed-files` behavior.
2. Split profile file evidence into semantic, companion, and shared hits.
3. Make shared/companion-only hits return advisory routing that asks for typed
   intent or stronger semantic evidence.
4. Preserve profile-specific file routing for files such as
   `scripts/live_readiness_check.py`.
5. Add runtime JSON fields that separate task blockers from legacy aggregate
   issues.
6. Run schema, digest mirror, focused pytest, planning-lock, map-maintenance,
   and closeout gates.

## Acceptance

- Shared registry files no longer route to `r3 live readiness gates implementation`.
- Profile-specific live-readiness files still route to live-readiness.
- `--navigation --changed-files` no longer silently ignores the file list.
- Route cards expose selection evidence and whether typed intent is needed.
- Closeout reports no direct blockers for this packet.

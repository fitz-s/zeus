# task_2026-04-15_data_math_boundary_integration_note

Status: packet-scoped supplement
Subordinate to:
- docs/authority/zeus_live_backtest_shadow_boundary.md
- docs/authority/zeus_openclaw_venus_delivery_boundary.md
- docs/authority/zeus_current_architecture.md

## 1. Shared semantic boundary

These semantics are shared across contexts and may not drift:
- typed settlement semantics
- WMO half-up rounding
- bin contract kind
- verified calibration lineage
- candidate-family FDR scope
- Kelly provenance discipline

Shared semantics do not imply shared authority.

## 2. Live-only boundary

Live may:
- evaluate candidates on the live path,
- execute orders,
- mutate canonical DB truth through existing write paths,
- harvest settlement and learning follow-through.

Live may not:
- use replay output as promotion authority,
- use shadow metrics as blockers unless explicitly promoted,
- rely on stale prose over code truth.

## 3. Backtest-only boundary

Backtest may:
- compare historical behavior,
- compute diagnostic metrics,
- write diagnostic artifacts to `zeus_backtest.db`,
- report replay limitations honestly.

Backtest may not:
- authorize live promotion,
- mutate live truth,
- claim parity it cannot prove.

## 4. Shadow-only boundary

Shadow may:
- compute diagnostic statistics (blocked OOS, effective sample size, Day0 residuals),
- surface advisory metrics,
- write to advisory output surfaces.

Shadow may not:
- gate live execution,
- block candidates,
- masquerade as calibrated live metrics.

## 5. Observe / act separation

- Observe: status_summary, healthcheck, topology_doctor, audit scripts, replay reports.
- Act: evaluator, lifecycle_events, fill_tracker, harvester, control_plane.

Observe surfaces may inform operator confidence.
They may not bypass typed act-path contracts.

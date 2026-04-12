# Zeus data-improve pre-TIGGE repair packet

This packet is a **directly-workable pre-TIGGE cutover + repair bundle** for the current `data-improve` branch.

It is built for the exact situation you described:

- `data-improve` already landed the **truth substrates** and governance cleanup.
- TIGGE backfill is still the long pole, so the highest-value work **right now** is the non-TIGGE cutover and gap repair.
- The next step is **not** another high-level plan. It is: migrate the DB, backfill the additive fact tables, wire the runtime writers, harden stale-truth handling, and finish the Day0 feature surface.

## What this packet fixes now

1. **Probability truth cutover**
   - Adds a real `probability_trace_fact` writer module.
   - Gives evaluator insertion fragments so per-decision probability lineage becomes durable.

2. **Family-wise FDR cutover**
   - Adds a full hypothesis scan helper that records **all tested hypotheses**, not only the prefiltered positive-CI subset.
   - Adds runtime helpers to persist `selection_family_fact` / `selection_hypothesis_fact` and apply BH over the full family.

3. **Calibration decision-group truth**
   - Adds a grouped-sample writer/backfill for `calibration_decision_group`.
   - Adds invariant checks so pair rows stop pretending to be independent sample count.

4. **Harvester learning semantics**
   - Adds a learning-context helper so settlement learning can record `bias_corrected`, group IDs, and degradation state.

5. **Day0 residual feature completion**
   - Finishes the currently-empty feature columns:
     `daylight_progress`, `obs_age_minutes`, `post_peak_confidence`,
     `ens_q50_remaining`, `ens_q90_remaining`, `ens_spread`.

6. **Portfolio stale-truth guard**
   - Adds a small policy layer so `partial_stale` stops silently masquerading as safe authority.

7. **Pre-TIGGE DB repair + audits**
   - Includes migration SQL, backfill scripts, readiness audits, and generated artifact CSVs from the uploaded DB snapshot.

## Apply order

1. Read `docs/01_current_state_audit.md`
2. Run `migrations/2026_04_11_pre_tigge_cutover.sql`
3. Run `scripts/repair_shared_db.py`
4. Read `docs/02_apply_order.md`
5. Patch the runtime files using `patches/*.md`
6. Run the included tests
7. Shadow for at least one observation window
8. Only then continue to the TIGGE packet in `docs/04_post_tigge_packet.md`

## Fast start

```bash
cd ~/.openclaw/workspace-venus/zeus

# 1) Schema + additive truth surfaces
sqlite3 state/zeus-shared.db < /path/to/migrations/2026_04_11_pre_tigge_cutover.sql

# 2) Backfill grouped calibration truth + error profiles + optional day0 facts
python /path/to/scripts/repair_shared_db.py --db state/zeus-shared.db --with-day0 --start-date 2026-01-01

# 3) Read insertion points and wire the runtime
#    - src/engine/evaluator.py
#    - src/strategy/market_analysis.py
#    - src/execution/harvester.py
#    - src/state/portfolio.py
#    - src/signal/day0_residual.py

# 4) Run targeted tests
pytest /path/to/tests -q
```

## Important constraint

This packet is intentionally split into:

- **now / pre-TIGGE** work that can and should be done immediately
- **post-TIGGE** work that depends on the backfill completing

Do **not** wait for TIGGE to do the pre-TIGGE fixes. These are the exact repairs that make the TIGGE expansion safe instead of just larger.

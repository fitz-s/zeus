# Zeus: Durable Weather Trading Runtime

Zeus is a high-fidelity, position-managed trading runtime engineered for **Weather Prediction Markets** on the Polymarket CLOB. It converts superior meteorological forecasts into consistent financial alpha through a combination of physics-aware modeling and an authority-hardened execution spine.

---

## 🌪️ The Technical Moat: Physical Layer Arbitrage
While competitors trade on meteorological data feeds, Zeus trades on **Physics and Station Semantics**.

### A. Monte Carlo Settlement Chain
For every market candidate, Zeus executes **200+ Monte Carlo simulations for each of the 51 ECMWF ensemble members** (Total $N \approx 10,200$ universes per bin):
- **Sensor Noise**: Injects ASOS instrument error ($\sigma_{instrument} \approx 0.2-0.5^\circ F$) to account for METAR precision.
- **Rounding Semantics**: Explicitly models Weather Underground rounding boundaries (e.g., the 74.45/74.5 threshold) to capture "mispriced" rounding risk.
- **The Result**: A high-fidelity probability vector $P(T_{final})$ that accounts for boundary flips invisible to mean-based bots.

### B. Extended Platt Calibration
Raw probabilities are biased. Zeus corrects them using a three-parameter logistic model that treats **Time** as a physical feature:
$$P_{cal} = \frac{1}{1 + \exp(-(A \cdot \text{logit}(P_{raw}) + B \cdot \text{lead\_days} + C))}$$
- **Temporal Decay**: The $B \cdot \text{lead\_days}$ term automatically discounts forecast skill as it decays over time, preventing "hot hands" bias in long-range entries.

### C. Day 0: Diurnal High-Set Decay
For `settlement_capture` strategies, the system utilizes an empirical $P(set|hour)$ model:
- **Solar-Aware Confidence**: Replaces hardcoded peak hours with per-city×season Diurnal Curves.
- **Observation Override**: If the current local hour is past the 15:00 peak and the current high is already extreme, the **Diurnal Decay** model forces the system to trust real-time observations over aging ensemble forecasts.

---

## 🏗️ The Engineering Spine: Anti-Vibe Architecture
Zeus is built to defeat **"Vibe Coding"** — the tendency for agents and humans to make local, context-shallow changes that violate deep architectural invariants.

- **Zoned Authority**: Strict import boundaries (K0..K4) ensure that monitoring/execution cannot depend on math/extension internals.
- **Atomic Work Packets**: Every change requires a frozen **Authority Basis** and a machine-gated **Invariant Checklist**.
- **Adversarial Review**: Institutionalized "Critic" role ensures every high-sensitivity change passes a threat model before landing.
- **Invariant Enforcement**: Managed by `kernel_manifest.yaml` and checked by semgrep/tests ($INV-01$ to $INV-10$).

---

## ⚖️ The Risk Spine: Statistical Governance
Zeus avoids "Random Walk" trading through multi-layered statistical filtering and dynamic risk adjustment.

### A. Benjamini-Hochberg (FDR) Filter
When evaluating 220 simultaneous hypotheses (10 cities × 11 bins × 2 directions), the probability of a "false edge" is high. Zeus employs a strict **FDR alpha (10%)** via the Benjamini-Hochberg procedure:
- **P-Value Bootstrap**: $p$-values are computed via `np.mean(bootstrap_edges <= 0)`.
- **Systemic Guard**: Only trades the top $k$ edges where $p_k \leq \frac{k}{m} \alpha_{fdr}$, ensuring system-wide false positives stay below the target threshold.

### B. Dynamic Kelly Sizing & Drawdown Brakes
The final size $f^*$ is derived from the fractional Kelly criterion but governed by multiplicative penalties:
- **CI Width Penalty**: If the Bootstrap Confidence Interval is $>15\%$, the base multiplier is slashed by 50%.
- **Concentration Guard**: Marginal sizing is reduced as portfolio "heat" exceeds 40% bankroll.
- **Drawdown Brake**: Sizing is forced to zero during a 20% equity drawdown until architectural clearing.

### C. RiskGuard: Enforced Behavior
Advisory risk is theater. **INV-05** mandates that Risk Levels (Green → Orange → Red) must **change behavior**:
- **Orange**: Entries are paused; exits remain active.
- **Red**: Emergency shutdown of all execution intents; manual quarantine of all positions.

---

## 📜 Operational Truth: Hierarchy of Reality
In the high-entropy environment of on-chain trading, Zeus follows a strict **Truth Hierarchy**:
$$\text{Chain} > \text{Chronicler} > \text{Portfolio}$$

### A. The 3-Rule Reconciliation
1.  **Rule 1: Sync**: Local state is updated only when chain evidence is conclusive.
2.  **Rule 2: Phantom (The Rule of Pain)**: If a trade exists locally but NOT on-chain, it is **VOIDED** immediately. We assume the local state is a hallucination.
3.  **Rule 3: Quarantine (The Rule of Caution)**: If unknown assets appear on-chain, they are locked for **48 hours** for architectural audit.

### B. Decision Artifacts & Replay Spine
Every heartbeat cycle generates a **CycleArtifact**.
- **Snapshots**: Captures exactly what the system saw (prices, forecasts, alpha) at $T_{decision}$.
- **Hindsight Parity**: The system supports counterfactual replay. You can ask: *"What would have happened if we used a 0.15 Kelly multiplier during the April 2024 heatwave?"* and get a deterministic result.

---

## 🛠️ Stack & Maturity
- **Core**: Python 3.10+, SQLite (Append-only Event Store).
- **Data**: ECMWF ENS (51-member), GFS Cross-check, ASOS/METAR Real-time.
- **Infrastructure**: Hardened work packets, Zoned Import Linter.
- **Status**: **Phase 2 (Execution Truth)**. Moving toward a zero-vibe runtime where every trade is an idempotent audit event.

---

> [!IMPORTANT]
> Zeus values **bearing capacity** over feature sprawl. It is designed not just to win trades, but to prove **why** it won them and survive the architectural decay common in AI-assisted development.

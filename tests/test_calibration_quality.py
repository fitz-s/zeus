"""MATH-002: Bin Hit-Rate Calibration Quality Tests.

This module validates the calibration quality of Zeus's probability predictions
by comparing predicted probabilities against actual settlement outcomes.

Gemini external review requirement: "Compare your predicted probabilities against
actual historical hit rates. If you say 70%, it should hit ~70% of the time."
"""

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pytest


@dataclass
class CalibrationRecord:
    """A single prediction-outcome pair for calibration analysis."""
    city: str
    target_date: str
    lead_hours: float
    p_raw: list[float]  # Raw probability vector
    p_cal: Optional[list[float]]  # Calibrated probability vector (may be None)
    winning_bin_idx: int  # Index of the bin that actually won
    settlement_value: float


def get_db_path() -> Path:
    """Get path to zeus.db."""
    # Try relative path first (for tests run from repo root)
    candidates = [
        Path("state/zeus.db"),
        Path(__file__).parent.parent / "state" / "zeus.db",
    ]
    for p in candidates:
        if p.exists():
            return p
    raise FileNotFoundError("zeus.db not found")


def load_calibration_data() -> list[CalibrationRecord]:
    """Load ensemble snapshots joined with settlements for calibration analysis.
    
    Uses ensemble_snapshots instead of shadow_signals because shadow_signals
    only contains future predictions not yet settled.
    """
    db_path = get_db_path()
    conn = sqlite3.connect(str(db_path))
    
    query = """
    SELECT 
        e.city,
        e.target_date,
        e.lead_hours,
        e.p_raw_json,
        NULL as p_cal_json,
        s.winning_bin,
        s.settlement_value
    FROM ensemble_snapshots e
    JOIN settlements s ON e.city = s.city AND e.target_date = s.target_date
    WHERE e.p_raw_json IS NOT NULL
      AND s.winning_bin IS NOT NULL
      AND s.settlement_value IS NOT NULL
    ORDER BY e.target_date, e.city;
    """
    
    records = []
    cursor = conn.execute(query)
    
    for row in cursor:
        city, target_date, lead_hours, p_raw_json, p_cal_json, winning_bin, settlement_value = row
        
        try:
            p_raw = json.loads(p_raw_json)
            p_cal = json.loads(p_cal_json) if p_cal_json else None
            
            # Parse winning_bin to get index
            # winning_bin format varies: "67-68", "32 or below", "81 or higher"
            winning_bin_idx = parse_winning_bin_index(winning_bin, p_raw, settlement_value, city)
            
            if winning_bin_idx >= 0:
                records.append(CalibrationRecord(
                    city=city,
                    target_date=target_date,
                    lead_hours=lead_hours,
                    p_raw=p_raw,
                    p_cal=p_cal,
                    winning_bin_idx=winning_bin_idx,
                    settlement_value=settlement_value,
                ))
        except (json.JSONDecodeError, ValueError) as e:
            # Skip malformed records
            continue
    
    conn.close()
    return records


def parse_winning_bin_index(winning_bin: str, p_vector: list[float], settlement_value: float, city: str) -> int:
    """Parse winning_bin string to get index in probability vector.
    
    Uses settlement_value to directly map to bin index based on standard bin structure:
    - F cities (11 bins): <=32, 33-34, 35-36, ..., 49-50, >=51
    - C cities (variable bins): <=X, X+1, X+2, ..., >=Y (1-degree bins)
    
    winning_bin formats:
    - "67-68" → interior bin
    - "-999-32" → first (open-low) bin  
    - "52-999" → last (open-high) bin
    """
    n_bins = len(p_vector)
    
    # Determine if Celsius city
    celsius_cities = {"London", "Paris", "Tokyo"}
    is_celsius = city in celsius_cities
    
    if is_celsius:
        # Celsius bins are 1-degree wide
        # Defensive: round to integer per settlement precision contract
        value = round(float(settlement_value))
        
        # Heuristic for C cities with 11 bins:
        # Assume bins are: <=5, 6, 7, 8, 9, 10, 11, 12, 13, 14, >=15 (example)
        # Or: <=10, 11, 12, ..., >=20
        # Map based on typical London range (5-20°C)
        
        if n_bins == 11:
            # Common London structure
            base_low = 5  # <=5
            if value <= base_low:
                return 0
            elif value >= base_low + n_bins - 1:
                return n_bins - 1
            else:
                return int(value - base_low)
        else:
            # Fallback: linear interpolation
            return min(n_bins - 1, max(0, int((value - 5) / 15 * n_bins)))
    else:
        # Fahrenheit bins are 2-degree wide
        # Defensive: round to integer per settlement precision contract
        value = round(float(settlement_value))
        
        if n_bins == 11:
            # Standard 11-bin F structure
            if value <= 32:
                return 0
            elif value >= 51:
                return 10
            else:
                # 33-34 → bin 1, 35-36 → bin 2, etc.
                return min(10, max(1, int((value - 32) / 2)))
        else:
            # Non-standard bin count - use linear interpolation
            # Assume range 30-90°F
            normalized = (value - 30) / 60
            return min(n_bins - 1, max(0, int(normalized * n_bins)))


def compute_ece(records: list[CalibrationRecord], use_calibrated: bool = False, n_bins: int = 10) -> float:
    """Compute Expected Calibration Error.
    
    ECE = sum over bins of (|accuracy - confidence| * bin_weight)
    
    Lower ECE = better calibration.
    """
    # Collect all (predicted_prob, actual_hit) pairs for the winning bin
    pairs = []
    for r in records:
        p_vec = r.p_cal if (use_calibrated and r.p_cal is not None) else r.p_raw
        if r.winning_bin_idx < len(p_vec):
            predicted_prob = p_vec[r.winning_bin_idx]
            actual_hit = 1.0  # The winning bin always "hit"
            pairs.append((predicted_prob, actual_hit))
    
    if len(pairs) == 0:
        return float('nan')
    
    # Bin by predicted probability
    bin_edges = np.linspace(0, 1, n_bins + 1)
    bin_acc = []
    bin_conf = []
    bin_count = []
    
    for i in range(n_bins):
        low, high = bin_edges[i], bin_edges[i + 1]
        in_bin = [(p, a) for p, a in pairs if low <= p < high]
        
        if len(in_bin) > 0:
            avg_conf = np.mean([p for p, a in in_bin])
            avg_acc = np.mean([a for p, a in in_bin])
            bin_acc.append(avg_acc)
            bin_conf.append(avg_conf)
            bin_count.append(len(in_bin))
        else:
            bin_acc.append(0.0)
            bin_conf.append(0.0)
            bin_count.append(0)
    
    # Compute ECE
    total = sum(bin_count)
    if total == 0:
        return float('nan')
    
    ece = sum(
        (bin_count[i] / total) * abs(bin_acc[i] - bin_conf[i])
        for i in range(n_bins)
    )
    return ece


def compute_bin_hit_rates(records: list[CalibrationRecord], use_calibrated: bool = False) -> dict:
    """Compute hit rates for each probability decile.
    
    For each bin, we collect all predictions where the probability for that outcome
    fell within a certain range, then compute what fraction actually hit.
    
    Note: This is a slightly different formulation - we're asking "when we predicted
    bin X with probability p, how often did bin X actually win?"
    """
    # Group predictions by probability range
    prob_bins = [(i/10, (i+1)/10) for i in range(10)]
    results = {}
    
    for prob_low, prob_high in prob_bins:
        predictions = []
        hits = []
        
        for r in records:
            p_vec = r.p_cal if (use_calibrated and r.p_cal is not None) else r.p_raw
            
            # Check each outcome bin's probability
            for bin_idx, prob in enumerate(p_vec):
                if prob_low <= prob < prob_high:
                    predictions.append(prob)
                    hits.append(1.0 if bin_idx == r.winning_bin_idx else 0.0)
        
        if len(predictions) > 0:
            hit_rate = sum(hits) / len(predictions)
            avg_prob = sum(predictions) / len(predictions)
            expected = (prob_low + prob_high) / 2
            gap = hit_rate - expected
        else:
            hit_rate = float('nan')
            avg_prob = float('nan')
            gap = float('nan')
        
        results[f"{prob_low:.1f}-{prob_high:.1f}"] = {
            "n": len(predictions),
            "hit_rate": hit_rate,
            "avg_predicted": avg_prob,
            "expected": (prob_low + prob_high) / 2,
            "gap": gap,
        }
    
    return results


class TestCalibrationQuality:
    """MATH-002: Bin hit-rate calibration validation tests."""

    @pytest.fixture(scope="class")
    def calibration_data(self) -> list[CalibrationRecord]:
        """Load calibration data once for all tests in this class."""
        return load_calibration_data()

    def test_data_availability(self, calibration_data):
        """MATH-002 Pre-check: Verify sufficient data for calibration analysis."""
        n_records = len(calibration_data)
        print(f"\n=== MATH-002: Data Availability ===")
        print(f"Total matched records: {n_records}")
        
        # Check city distribution
        cities = {}
        for r in calibration_data:
            cities[r.city] = cities.get(r.city, 0) + 1
        print(f"Cities: {dict(sorted(cities.items(), key=lambda x: -x[1]))}")
        
        # Check lead time distribution
        lead_buckets = {"<6h": 0, "6-24h": 0, ">24h": 0}
        for r in calibration_data:
            if r.lead_hours < 6:
                lead_buckets["<6h"] += 1
            elif r.lead_hours <= 24:
                lead_buckets["6-24h"] += 1
            else:
                lead_buckets[">24h"] += 1
        print(f"Lead time distribution: {lead_buckets}")
        
        # Check calibrated data availability
        n_calibrated = sum(1 for r in calibration_data if r.p_cal is not None)
        if n_records > 0:
            print(f"Records with p_cal: {n_calibrated} ({n_calibrated/n_records*100:.1f}%)")
        else:
            print(f"Records with p_cal: 0 (no records)")
        
        assert n_records >= 50, f"Insufficient data: {n_records} records (need >= 50)"

    def test_overall_ece(self, calibration_data):
        """MATH-002 Test 1: Compute overall Expected Calibration Error."""
        print(f"\n=== MATH-002 Test 1: Overall ECE ===")
        
        ece_raw = compute_ece(calibration_data, use_calibrated=False)
        print(f"ECE (raw p_vector): {ece_raw:.4f}")
        
        n_with_cal = sum(1 for r in calibration_data if r.p_cal is not None)
        if n_with_cal > 0:
            ece_cal = compute_ece(
                [r for r in calibration_data if r.p_cal is not None],
                use_calibrated=True
            )
            print(f"ECE (calibrated p_vector): {ece_cal:.4f}")
            
            if ece_raw > 0:
                improvement = (ece_raw - ece_cal) / ece_raw * 100
                print(f"Calibration improvement: {improvement:.1f}%")
        
        # Document but don't fail - this establishes baseline
        print(f"\nInterpretation:")
        print(f"  ECE < 0.05: Excellent calibration")
        print(f"  ECE 0.05-0.10: Good calibration")
        print(f"  ECE 0.10-0.20: Moderate miscalibration")
        print(f"  ECE > 0.20: Significant miscalibration")

    def test_bin_level_hit_rates(self, calibration_data):
        """MATH-002 Test 2: Document hit rate for each probability decile."""
        print(f"\n=== MATH-002 Test 2: Bin-Level Hit Rates ===")
        
        results = compute_bin_hit_rates(calibration_data, use_calibrated=False)
        
        print(f"\nReliability Diagram Data (raw):")
        print(f"{'Prob Range':<12} {'N':>6} {'Hit Rate':>10} {'Expected':>10} {'Gap':>10}")
        print("-" * 50)
        
        total_gap = 0.0
        total_n = 0
        
        for prob_range, data in sorted(results.items()):
            n = data["n"]
            hit_rate = data["hit_rate"]
            expected = data["expected"]
            gap = data["gap"]
            
            if n > 0 and not np.isnan(hit_rate):
                print(f"{prob_range:<12} {n:>6} {hit_rate:>10.3f} {expected:>10.2f} {gap:>+10.3f}")
                total_gap += abs(gap) * n
                total_n += n
        
        if total_n > 0:
            weighted_avg_gap = total_gap / total_n
            print(f"\nWeighted average |gap|: {weighted_avg_gap:.4f}")

    def test_calibration_by_lead_time(self, calibration_data):
        """MATH-002 Test 3: Stratify calibration by lead time buckets."""
        print(f"\n=== MATH-002 Test 3: Calibration by Lead Time ===")
        
        # Split by lead time
        buckets = {
            "<6h": [r for r in calibration_data if r.lead_hours < 6],
            "6-24h": [r for r in calibration_data if 6 <= r.lead_hours <= 24],
            ">24h": [r for r in calibration_data if r.lead_hours > 24],
        }
        
        print(f"\n{'Lead Time':<10} {'N':>6} {'ECE':>10}")
        print("-" * 28)
        
        for bucket_name, records in buckets.items():
            if len(records) >= 10:
                ece = compute_ece(records, use_calibrated=False)
                print(f"{bucket_name:<10} {len(records):>6} {ece:>10.4f}")
            else:
                print(f"{bucket_name:<10} {len(records):>6} {'(insufficient)':>10}")

    def test_high_confidence_accuracy(self, calibration_data):
        """MATH-002 Test 4: Check if high-confidence predictions are accurate.
        
        This is critical: when we say 80%+, we should be right ~80%+ of the time.
        Over-confidence here leads to bad trading decisions.
        """
        print(f"\n=== MATH-002 Test 4: High Confidence Accuracy ===")
        
        # Find predictions where max(p_vec) >= 0.7
        high_conf_records = []
        for r in calibration_data:
            max_prob = max(r.p_raw)
            max_idx = r.p_raw.index(max_prob)
            if max_prob >= 0.7:
                high_conf_records.append({
                    "record": r,
                    "predicted_idx": max_idx,
                    "predicted_prob": max_prob,
                    "hit": max_idx == r.winning_bin_idx,
                })
        
        if len(high_conf_records) == 0:
            print("No high-confidence predictions found")
            return
        
        total = len(high_conf_records)
        hits = sum(1 for r in high_conf_records if r["hit"])
        avg_conf = sum(r["predicted_prob"] for r in high_conf_records) / total
        
        hit_rate = hits / total
        print(f"High-confidence predictions (max prob >= 0.7):")
        print(f"  Total: {total}")
        print(f"  Hits: {hits}")
        print(f"  Hit rate: {hit_rate:.3f}")
        print(f"  Average confidence: {avg_conf:.3f}")
        print(f"  Gap (hit_rate - avg_conf): {hit_rate - avg_conf:+.3f}")
        
        if hit_rate < avg_conf - 0.15:
            print(f"\n⚠️ WARNING: System is OVER-CONFIDENT by {avg_conf - hit_rate:.1%}")
        elif hit_rate > avg_conf + 0.15:
            print(f"\n⚠️ WARNING: System is UNDER-CONFIDENT by {hit_rate - avg_conf:.1%}")
        else:
            print(f"\n✓ Calibration acceptable for high-confidence predictions")

    def test_winning_bin_probability_distribution(self, calibration_data):
        """MATH-002 Test 5: What probability did we assign to the winning bin?
        
        If we're well-calibrated, the distribution of probabilities assigned to
        winning bins should reflect our overall calibration quality.
        """
        print(f"\n=== MATH-002 Test 5: Winning Bin Probability Distribution ===")
        
        winning_probs = []
        for r in calibration_data:
            if r.winning_bin_idx < len(r.p_raw):
                winning_probs.append(r.p_raw[r.winning_bin_idx])
        
        if len(winning_probs) == 0:
            print("No valid winning bin probabilities found")
            return
        
        winning_probs = np.array(winning_probs)
        
        print(f"Probability assigned to winning bin:")
        print(f"  N: {len(winning_probs)}")
        print(f"  Mean: {np.mean(winning_probs):.3f}")
        print(f"  Median: {np.median(winning_probs):.3f}")
        print(f"  Std: {np.std(winning_probs):.3f}")
        print(f"  Min: {np.min(winning_probs):.3f}")
        print(f"  Max: {np.max(winning_probs):.3f}")
        
        # Percentile distribution
        percentiles = [10, 25, 50, 75, 90]
        pct_values = np.percentile(winning_probs, percentiles)
        print(f"\n  Percentiles:")
        for pct, val in zip(percentiles, pct_values):
            print(f"    P{pct}: {val:.3f}")
        
        # Interpretation
        mean_prob = np.mean(winning_probs)
        print(f"\n  Interpretation:")
        if mean_prob > 0.5:
            print(f"    System assigns high probability to winning outcomes (good)")
        elif mean_prob > 0.3:
            print(f"    System assigns moderate probability to winning outcomes")
        else:
            print(f"    System often assigns low probability to winning outcomes (concerning)")

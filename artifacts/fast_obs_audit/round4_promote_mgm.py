"""Apply the operator's measured free-source admission to MGM routes only.
Offline evidence reduction; no network, no canonical DB, no deployment.
New routes require identical exact-time values and an interval lead over EVERY
current measured path at the SAME paired observation instant.
"""
import json
from pathlib import Path

BASE = Path(__file__).parent
ROOT = BASE.resolve().parents[1]
REGISTRY = ROOT / 'config' / 'physical_current_sources.json'


def main():
    comparisons = json.loads((BASE/'round4_comparison.json').read_text())
    selected = []
    for row in comparisons:
        if row['channel'] != 'mgm_metar': continue
        if row['verdict'] != 'VALUE_IDENTICAL_FASTER_OBSERVED': continue
        assert row['n_pairs'] > 0 and row['n_exact'] == row['n_pairs'] and not row['mismatches']
        paired = {p['time'] for p in row['pairs'] if p['match']}
        required = set(row['required_speed_comparators'])
        leads = [r for r in row['races'] if r['observation'] in paired and required and
                 all(any(p['channel'] == ch and p['verdict'] == 'FASTER' and
                         r['candidate']['lag_upper_ms'] < p['interval']['lag_lower_ms']
                         for p in r['comparators']) for ch in required)]
        assert leads, (row['city'], 'no same-observation lead')
        item = dict(row)
        item['first_proven_lead'] = min(leads, key=lambda r:r['observation'])
        selected.append(item)
    data = json.loads(REGISTRY.read_text())
    existing = [r for r in data['sources'] if r['provider'] != 'mgm_metar']
    # The report is the complete preserved Round4 MGM comparison, not a rolling
    # window. A later mismatch removes its route instead of hiding old evidence.
    from src.config import cities
    by_station = {c.wu_station:c for c in cities if c.wu_station}
    for proof in selected:
        city = by_station[proof['station']]
        assert city.settlement_unit == 'C'
        existing.append({
            'provider':'mgm_metar','source_channel':'mgm_metar_temperature',
            'station_id':proof['station'],'settlement_source_types':[city.settlement_source_type],
            'unit':'C','minimum_poll_seconds':60,
            'identity':{'provider_station':proof['station']},'settlement_grade':True,
            'free_access':'Anonymous public national-service METAR; no key, login, or contract.',
            'publisher_role':'NATIONAL_ORIGIN' if city.country_code == 'TR' else 'NATIONAL_SERVICE_REDISTRIBUTION',
            'value_identity_proof':{'city':proof['city'],'channel':'mgm_metar',
                'n_pairs':proof['n_pairs'],'n_exact':proof['n_exact'],'mismatches':[],
                'report_path':'artifacts/fast_obs_audit/round4_mgm_proof.json'},
            'latency_evidence':{'required_comparators':proof['required_speed_comparators'],
                'first_proven_lead':proof['first_proven_lead']},
            'scope_note':'Same station, exact UTC observation instant, contract value. No final daily-product substitution.'})
    (BASE/'round4_mgm_proof.json').write_text(json.dumps(selected,indent=2)+'\n')
    data['sources'] = existing
    REGISTRY.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    print('MGM admitted:',[(r['city'],r['n_exact'],r['n_pairs']) for r in selected])


if __name__ == '__main__':
    import sys
    sys.path.insert(0,str(ROOT))
    main()

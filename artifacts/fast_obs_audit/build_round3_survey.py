"""Reproduce the 54-city survey inventory from configuration and saved measurements.

This inventory deliberately distinguishes completed comparisons from access/discovery
work. A zero is zero recovered overlapping pairs, not proof the provider has no data.
No network, credentials, database writes, or source promotion occurs here.
"""
from pathlib import Path
import csv
import json
import sys

ROOT = Path(__file__).resolve().parents[2]
while not (ROOT / 'src').is_dir():
    ROOT = ROOT.parent
sys.path.insert(0, str(ROOT))
from src.config import cities

OUT = Path(__file__).parent
race = json.loads((OUT/'round3_origin_measurements.json').read_text())
window = {r['city']: r for r in json.loads((OUT/'round3_window/comparison.json').read_text())}
sg = json.loads((OUT/'round3_singapore/comparison.json').read_text())
registry = json.loads((ROOT/'config/physical_current_sources.json').read_text())
races = {r['city']: r for r in race['results']}
# These are reviewed discovery dispositions, not fabricated station measurements.
# The raw anonymous endpoint responses are in round3_origins/. See ROUND3.md
# for the explicit unfinished empirical scope and credential boundaries.
origins = {
 'ES': ('AEMET station 3129 / LEMD airport observation', 'https://opendata.aemet.es/opendata/api/observacion/convencional/datos/estacion/3129', 'Official API discovered; no authenticated airport-value payload obtained. HTML candidate returned 404. Pair and latency comparison unfinished.'),
 'GB': ('Met Office DataHub hourly / EGLC', 'https://datahub.metoffice.gov.uk/docs/o/category/observations/overview', 'Hourly observation API documented; subscription/key and exact EGLC record were not obtained. Network size is not exact-station proof.'),
 'FR': ('Meteo-France observation API / LFPB', 'https://portail-api.meteofrance.fr/', 'WIS2 collection request failed; registered observation API identified. Exact LFPB record and receipt series remain unmeasured.'),
 'BR': ('REDEMET METAR / SBGR', 'https://api-redemet.decea.mil.br/mensagens/metar/SBGR', 'Native endpoint returned HTTP 401. No API credential obtained; no identity or latency inference from refusal.'),
 'MY': ('MET Malaysia aviation / WMKK', 'https://api.met.gov.my/', 'Public MET API documentation describes forecasts/warnings, not this airport temperature observation. Aviation bulletin origin remains unverified; public forecast values rejected as wrong product.'),
 'TW': ('CWA / ANWS aviation / RCSS', 'https://aoaws.anws.gov.tw/', 'Aviation endpoint connection failed. No substitution of RCTP or an arbitrary Taipei surface station; native observation comparison unfinished.'),
 'CN': ('CMA / civil-aviation airport observation origin', 'https://data.cma.cn/', 'CMA catalog reviewed, but no exact ICAO-airport stream acquired. Documented national surface exchange products have publication lag and are not proof of airport identity. Do not replace an airport with a nearby city station.'),
 'IN': ('IMD AMSS OLBS / airport METAR', 'https://olbs.amsschennai.gov.in/nsweb/FlightBriefing/showopmetquery.php', 'Public query form and official API gateway identified; no completed station bulletin extraction/authorized API response. Comparison unfinished, not a provider-outage finding.'),
 'TR': ('MGM Hezarfen RASAT / airport METAR', 'https://rasat.mgm.gov.tr/', 'Public search/result application returned HTML. Native data request contract remains unresolved; no exact-time station series extracted.'),
 'IL': ('IMS XML / LLBG candidate', 'https://ims.gov.il/en/CurrentDataXML', 'Public XML exists; exact LLBG candidate not resolved. Official XML documentation distinguishes hourly and ten-minute products/time conventions. No nearest station or undocumented time shift used.'),
 'SA': ('NCM observation/aviation API / OEJN', 'https://api-doc.ncm.gov.sa/', 'API documentation reached; authenticated exact-station temperature response not obtained. Endpoint and pair comparison remain unfinished.'),
 'PK': ('PMD aviation / OPKC', 'https://rmcsindh.pmd.gov.pk/Services_Aviation.html', 'Official aviation service page reached; exact machine-readable METAR retrieval contract not resolved.'),
 'MX': ('SMN / SENEAM aviation / MMMX', 'https://www.gob.mx/seneam/acciones-y-programas/mas-servicios-de-control', 'Candidate page returned a browser/challenge response; no station payload acquired. General automatic-station data not substituted for MMMX.'),
 'AR': ('Argentina SMN METAR/SPECI / SAEZ', 'https://ws2.smn.gob.ar/mensajes-metar-speci', 'Native bulletin endpoint connection failed in this environment. Equality and first availability remain unknown.'),
 'ZA': ('SAWS aviation / FACT', 'https://aviation.weathersa.co.za/aviationold/avmobile.php', 'Native aviation endpoint connection failed; no license/credential or exact record obtained.'),
 'RU': ('Aviamettelecom / Meteocenter / UUWW', 'https://www.aviamettelecom.ru/services/', 'Official service and display frontend reached; exact UUWW machine record not extracted. Public-app reverse engineering remains unfinished.'),
 'IT': ('Aeronautica Militare METAR / airport', 'https://www.meteoam.it/it/metar-taf', 'Corrected the old /metar-e-taf 404 to the official /metar-taf app. Public JS/frontend checked; direct native record and timing comparison remain unfinished.'),
 'ID': ('BMKG aviation / WIHH', 'https://aviation.bmkg.go.id/', 'Browser/challenge HTTP 403. No bypass attempted and no substitution of WIII.'),
 'NG': ('NiMet aviation / DNMM', 'https://nimet.gov.ng/', 'Redirect/challenge frontend did not yield station observations; origin comparison unfinished.'),
 'NZ': ('MetService Classic one-minute airport observations', 'https://developer.metservice.com/docs/api-catalog/1min-obs-api/', 'Operational commercial API documented; licensed key not available. No claim that the November preview API is currently usable.'),
 'HK': ('HKO headquarters native observations', 'https://www.hko.gov.hk/', 'Existing native headquarters lane retained. No new Round-3 airport substitute or first-availability comparison was performed.'),
 'JP': ('JMA Haneda 44166', 'https://www.jma.go.jp/bosai/amedas/data/point/44166/', 'Extended exact-time identity and independent publication race passed; retain existing wired fast route.'),
 'CA': ('ECCC CYYZ-MAN SWOB', 'https://dd.weather.gc.ca/today/observations/swob-ml/latest/CYYZ-MAN-swob.xml', 'Extended exact-time identity and independent publication race passed; retain existing wired fast route.'),
 'KR': ('KMA aviation native METAR', 'https://global.amo.go.kr/observation/PkObsMetarList.do', 'Native exact-time comparison and faster transition passed. Existing KmaMetarCursor is already the runtime native transport; no duplicate source fetcher introduced.'),
 'PH': ('PAGASA RPLL METAR', 'https://www.pagasa.dost.gov.ph/aviation/metar', 'Exact values matched, but native first availability was slower. Do not activate this alternative.'),
 'PA': ('AAC MPMG METAR', 'https://www.aeronautica.gob.pa/met/met.php?c=metar', 'Exact values matched; availability intervals overlap AWC/WRH. Faster transport is not established, so not activated.'),
 'SG': ('NEA/MSS Changi S24', 'https://api.data.gov.sg/v1/environment/air-temperature', 'S24 named Changi Meteorological Station: 27/49 exact rounded matches, 22 mismatches. Not a resolver-equivalent fast source; no promotion.'),
 'FI': ('FMI WFS 100968', 'https://opendata.fmi.fi/wfs', 'Retained Round-2 systematic value mismatches. Physical-current only, not settlement-grade.'),
 'DE': ('DWD CDC 01262', 'https://opendata.dwd.de/climate_environment/CDC/observations_germany/climate/10_minutes/air_temperature/now/', 'Retained Round-2 systematic value mismatches. Physical-current only, not settlement-grade.'),
 'NL': ('KNMI ten-minute Schiphol 06240', 'https://api.dataplatform.knmi.nl/open-data/v1/datasets/10-minute-in-situ-meteorological-observations/versions/1.0/files', 'NetCDF decoded; zero exact-time overlapping pairs in retained comparison. Anonymous quota refused repeated requests. Not promoted.'),
 'PL': ('IMGW SYNOP 12375', 'https://danepubliczne.imgw.pl/api/data/synop/id/12375', 'Retained 1/2 equality result includes a contradictory pair; physical-only, not promoted.'),
 'US': ('NOAA WRH native station temperature / hourly view', 'https://www.weather.gov/wrh/timeseries', 'Use actual resolver-native Fahrenheit and product membership; one shared batch/minute for all 11 stations. Not a claim that WRH publishes before AWC.'),
}
provider_by_country = {'FI':'fmi_wfs','DE':'dwd_cdc','NL':'knmi_observations','PL':'imgw_synop','US':'noaa_wrh'}
rows=[]
for c in cities:
 cc=c.country_code
 assert cc in origins,(c.name,cc)
 candidate,url,disposition=origins[cc]
 row={'city':c.name,'settlement_station':c.wu_station or 'HKO headquarters','country':cc,
      'settlement_unit':c.settlement_unit,'candidate':candidate,'candidate_url':url,
      'n_pairs':0,'n_exact':0,'mismatches':[],'races':[],
      'decision':'NOT_PROMOTED_UNMEASURED','disposition':disposition,
      'evidence':['round3_origins/discovery.json','round3_origin_probe.py']}
 if c.name in races:
  r=races[c.name]
  row.update(n_pairs=r['n_pairs'],n_exact=r['n_exact'],mismatches=r['mismatches'],races=r['races'])
  row['evidence']=['round3_origin_measurements.json','round3_race/']
  row['decision']='RETAIN_ALREADY_WIRED' if cc in {'KR','JP','CA'} else 'NOT_ACTIVATED_NO_SPEED_ADVANTAGE'
 if c.name in window:
  w=window[c.name];row.update(n_pairs=w['n_pairs'],n_exact=w['n_exact'],mismatches=w['mismatches'])
  row['evidence'].append('round3_window/comparison.json')
 if cc=='SG':
  row.update(n_pairs=sg['n_pairs'],n_exact=sg['n_exact'],mismatches=sg['mismatches'],decision='NOT_PROMOTED_VALUE_MISMATCH')
  row['evidence']=['round3_singapore/comparison.json','round3_singapore/nea_temperature.json.gz','round3_singapore/wrh_wsss.json.gz']
 if cc in provider_by_country:
  provider=provider_by_country[cc]
  matching=[x for x in registry['sources'] if x['provider']==provider and x['station_id']==c.wu_station]
  if matching:
   proof=matching[0].get('value_identity_proof',{})
   row.update(n_pairs=proof.get('n_pairs',0),n_exact=proof.get('n_exact',0),mismatches=proof.get('mismatches',[]))
   row['evidence']=['config/physical_current_sources.json','source_identity_report.json']
   row['decision']='CANONICAL_RESOLVER_PRECISION_NOT_SPEED_PROMOTION' if cc=='US' else 'NOT_PROMOTED_MISMATCH_OR_NO_OVERLAP'
 if cc=='HK':row['decision']='RETAIN_EXISTING_NATIVE_NO_NEW_COMPARISON'
 if c.name=='Jinan':row['disposition']+=' Existing WU current/history routes remain; a CMA native replacement is unproven.'
 rows.append(row)
assert len(rows)==len(cities)==54 and len({r['city'] for r in rows})==54
report={'scope':'ALL_CONFIGURED_CITIES; empirical national-origin completion is PARTIAL',
        'roster_count':len(rows),'exact_pair_comparison_rule':'same UTC valid instant; contract units/rounding; no nearest-time join',
        'zero_pair_meaning':'No valid pair recovered in this investigation; not proof provider data does not exist',
        'rows':rows}
(OUT/'round3_national_survey.json').write_text(json.dumps(report,indent=2,ensure_ascii=False))
with (OUT/'round3_national_survey.csv').open('w',newline='') as f:
 columns=['city','settlement_station','country','candidate','n_pairs','n_exact','decision','disposition','candidate_url']
 w=csv.DictWriter(f,fieldnames=columns,extrasaction='ignore');w.writeheader();w.writerows(rows)
lines=['# Round 3 national-origin inventory','',report['scope'],'',
 'Zero pairs means unmeasured, not an outage or proof that no better source exists. Numerical races, mismatch values and evidence references are in round3_national_survey.json.',
 '', '|City|Station|Candidate|Pairs / exact|Disposition|','|---|---|---|---:|---|']
for r in rows:lines.append(f"|{r['city']}|{r['settlement_station']}|{r['candidate']}|{r['n_pairs']} / {r['n_exact']}|{r['decision']}: {r['disposition']}|")
(OUT/'ROUND3_NATIONAL_SURVEY.md').write_text('\n'.join(lines)+'\n')
print('Wrote',len(rows),'city rows; empirical completion remains partial.')

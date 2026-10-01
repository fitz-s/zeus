"""Offline complete configured-city inventory; measured comparisons vs access dispositions.
No registrations, network calls, source promotion or canonical database changes.
"""
import csv,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))
from round4_compare import BASE,CITY_BY_STATION

# Reviewed public access findings. A portal/homepage is not a weather reading.
ORIGINS={
'ES':('AEMET 3129 public station XML','FREE_ANONYMOUS_PAYLOAD','https://www.aemet.es/es/api-eltiempo/udat/tablas-graficas/horario/9/3129','Revising the old 404/key-only finding: the public website XML works without registration. Temperature mismatches prevent admission.'),
'GB':('Met Office DataHub EGLC','REQUIRES_REGISTRATION','https://datahub.metoffice.gov.uk/pricing/observations','A genuinely free tier is documented, but an account/key is required; no account was created. MGM is a separate tested public redistribution channel.'),
'FR':('Meteo-France LFPB observation API','REQUIRES_REGISTRATION','https://www.data.gouv.fr/dataservices/api-package-observations','The producer describes open access with account. No authenticated call; old anonymous SYNOP request did not yield current station data.'),
'BR':('REDEMET SBGR','REQUIRES_REGISTRATION','https://api-redemet.decea.mil.br/mensagens/metar/SBGR','Native request was refused without registration/key. The Italian national-service public global METAR API independently supplies exact SBGR reports, but is redistribution, not Brazilian origin.'),
'MY':('MET Malaysia WMKK','NO_EXACT_ANONYMOUS_STATION_PAYLOAD','https://www.met.gov.my/en/info/data-terbuka/','The reachable public product documentation describes forecasts/warnings; those are not airport observations. Aviation endpoint errors are recorded, not called a national outage.'),
'TW':('ANWS RCSS','CONNECTOR_TRANSPORT_UNAVAILABLE','https://aoaws.anws.gov.tw/Report','The public web renderer can display the form; connector HTTP attempts failed. No login attempted, no RCTP substitution. MGM RCSS comparison is independent redistribution.'),
'CN':('CMA/civil-aviation exact airport METAR','NO_EXACT_ANONYMOUS_STATION_PAYLOAD','https://aviation.nmc.cn/','Catalog/aviation pages did not yield an anonymous exact ICAO stream. No nearby city surface station was substituted. Exact ICAO METARs were compared through MGM.'),
'IN':('IMD OLBS VILK','FREE_ANONYMOUS_PAYLOAD','https://olbs.amsschennai.gov.in/nsweb/FlightBriefing/showopmetquery.php','Revising the empty-form finding: public POST fields icaos and type return the report. No registration or credential is needed.'),
'TR':('MGM Hezarfen native METAR','FREE_ANONYMOUS_PAYLOAD','https://rasat.mgm.gov.tr/result','Revising the unresolved frontend: public Next data contains exact station/time reports. Actual response maximum is ten stations, enforced in the tested universal adapter.'),
'IL':('IMS LLBG candidate','NO_IDENTIFIED_LLBG_ROW','https://ims.gov.il/en/CurrentDataXML','The free XML candidate was checked, but no exact LLBG reading was established. An unmatched surface station was not renamed to LLBG. MGM supplies exact airport raw reports.'),
'SA':('NCM OEJN aviation','NO_EXACT_ANONYMOUS_STATION_PAYLOAD','https://api-doc.ncm.gov.sa/','Public service/API documentation did not provide an anonymous exact OEJN response. Contract/commercial routes were excluded.'),
'PK':('PMD OPKC aviation','NO_EXACT_ANONYMOUS_STATION_PAYLOAD','https://rmcsindh.pmd.gov.pk/Services_Aviation.html','The public aviation service description is not a station observation response. Exact reports were available via MGM, without pretending it is the Pakistani origin.'),
'MX':('SMN/SENEAM MMMX','PUBLIC_ACCESS_FAILED','https://www.gob.mx/seneam/acciones-y-programas/mas-servicios-de-control','Government pages returned challenge content; candidate service hosts failed connection. No challenge/login bypass. The Italian public global METAR API was independently queried for MMMX.'),
'AR':('Argentina SMN SAEZ','PUBLIC_ACCESS_FAILED','https://www.smn.gob.ar/metar','The public route returned a browser challenge; an older service URL failed transport. No bypass. MGM SAEZ exact reports were compared.'),
'ZA':('SAWS FACT aviation','PUBLIC_ACCESS_FAILED','https://aviation.weathersa.co.za/','Public root returned access denial and the older mobile endpoint failed connection. No credentials or paid access used.'),
'RU':('Aviamettelecom UUWW raw METAR','FREE_ANONYMOUS_PAYLOAD','http://display.meteocenter.ru/219','Revising the unextracted frontend: its public modal contains the exact raw METAR. Native lead measured. HTTPS failed; active route uses fixed-host plaintext HTTP and that security limitation is explicit.'),
'IT':('Aeronautica Militare LIMC METAR','FREE_ANONYMOUS_PAYLOAD','https://api.meteoam.it/deda-ows/metar-taf-icao/','Revising the old missing path: ICAO/start-UTC/end-UTC returns public JSON without a key. Complete dated path is retained in the transport evidence.'),
'ID':('BMKG WIHH aviation','PUBLIC_ACCESS_FAILED','https://web-aviation.bmkg.go.id/web/metar_speci.php','Public page returned a browser challenge; no bypass. No substitution of WIII for WIHH.'),
'NG':('NiMet DNMM aviation','PUBLIC_ACCESS_FAILED','https://nimet.gov.ng/','Public page returned redirect/JavaScript challenge. No login/challenge bypass. Exact DNMM reports were available through MGM.'),
'NZ':('New Zealand free airport sources','COMMERCIAL_ORIGIN_EXCLUDED','https://rasat.mgm.gov.tr/result','MetService Classic commercial API is excluded, not deferred for a paid key. No independently verified free native endpoint was obtained; public MGM NZAA/NZWN redistribution was measured.'),
'HK':('HKO headquarters native temperature CSV','FREE_ANONYMOUS_PAYLOAD','https://data.weather.gov.hk/weatherAPI/hko_data/regional-weather/latest_1min_temperature.csv','The native station CSV is retained. Public RHR JSON and native CSV disagree under the contract truncation law. This compares spot products, not a final daily-max/min settlement value.'),
'JP':('JMA Haneda 44166','FREE_ANONYMOUS_PAYLOAD','https://www.jma.go.jp/bosai/amedas/data/point/44166/','Existing native route retained only while the accumulated exact-time proof remains uncontradicted.'),
'CA':('ECCC CYYZ-MAN SWOB','FREE_ANONYMOUS_PAYLOAD','https://dd.weather.gc.ca/today/observations/swob-ml/latest/CYYZ-MAN-swob.xml','Existing native route retained. A new alternative must beat this route, not merely a slower AWC response.'),
'KR':('KMA AMO native METAR','FREE_ANONYMOUS_PAYLOAD','https://global.amo.go.kr/observation/PkObsMetarList.do','Existing shared KMA runtime cursor retained; no duplicate source polling implementation.'),
'PH':('PAGASA RPLL METAR','FREE_ANONYMOUS_PAYLOAD','https://www.pagasa.dost.gov.ph/aviation/metar','The free origin matched exact values; the measured speed evidence does not justify replacing the current path.'),
'PA':('Panama AAC MPMG METAR','FREE_ANONYMOUS_PAYLOAD','https://www.aeronautica.gob.pa/met/met.php?c=metar','Exact airport reports were extracted. Overlapping/absent speed intervals are not called a proven lead.'),
'SG':('NEA/MSS Changi S24','PREVIOUS_MEASURED_MISMATCH_RETAINED','https://api.data.gov.sg/v1/environment/air-temperature','Round-3 same-clock 27/49 proof with 22 mismatches is retained; no re-promotion from matching MGM redistributions.'),
'FI':('FMI WFS 100968','FREE_ANONYMOUS_PAYLOAD','https://opendata.fmi.fi/wfs','Repeated same-clock mismatches: physical-current only. No instrument certificate is demanded.'),
'DE':('DWD CDC 01262','FREE_ANONYMOUS_PAYLOAD','https://opendata.dwd.de/climate_environment/CDC/observations_germany/climate/10_minutes/air_temperature/now/','Repeated same-clock mismatches: physical-current only.'),
'NL':('KNMI 06240 NetCDF and public EHAM METAR','FREE_ANONYMOUS_PAYLOAD','https://www.knmi.nl/nederland-nu/luchtvaart/vliegveldwaarnemingen','Published anonymous API key obtained without registration; one NetCDF decoded, then 429 and stopped. Ten-minute clock has no exact overlap. Separately, the free public METAR page yields an exact EHAM report without any key.'),
'PL':('IMGW 12375 SYNOP','FREE_ANONYMOUS_PAYLOAD','https://danepubliczne.imgw.pl/api/data/synop/id/12375','Round-4 matches do not erase the Round-3 1/2 contradiction. Remains physical-only.'),
'US':('NOAA resolver-native Fahrenheit product','RETAIN_EXISTING_NATIVE_PRODUCT','https://www.weather.gov/wrh/timeseries','The existing native field/view route remains; AWC independent values are compared without pretending the C body universally reconstructs resolver-native F. No unrelated US precision changes in this round.'),
}
DOMESTIC={'ES':{'aemet_station_xml'},'TR':{'mgm_metar'},'RU':{'metaviatelecom_display'},'IT':{'meteoam_metar'},
'IN':{'imd_olbs_metar'},'HK':{'hko_native_csv','hko_rhr_json'},'JP':{'jma_amedas'},'CA':{'eccc_swob'},'KR':{'kma_amo_raw_metar'},
'PH':{'pagasa_metar'},'PA':{'aac_metar'},'FI':{'fmi_wfs'},'DE':{'dwd_cdc'},'NL':{'knmi_observations','knmi_public_metar'},'PL':{'imgw_synop'},'US':{'awc'}}

def short_interval(r):
    meaningful=[race for race in r.get('races',[]) if race['comparators']]
    if not meaningful:return 'No bracketed new paired transition'
    required=set(r.get('required_speed_comparators',[]))
    race=next((x for x in meaningful if x.get('paired_value_identity') and required and
        all(any(y['channel']==ch and y['verdict']=='FASTER' for y in x['comparators']) for ch in required)),meaningful[0])
    a=race['candidate'];peers=[x for x in race['comparators'] if x['channel'] in required]
    line=f"{race['observation']}: candidate [{a['lag_lower_ms']/1000:.3f},{a['lag_upper_ms']/1000:.3f}]s"
    for peer in peers:
        p=peer['interval'];line+=f"; {peer['channel']} [{p['lag_lower_ms']/1000:.3f},{p['lag_upper_ms']/1000:.3f}]s ({peer['verdict']})"
    return line

def main():
    comparisons=json.loads((BASE/'round4_comparison.json').read_text())
    cumulative=json.loads((BASE/'round4_cumulative_identity.json').read_text())
    prior=json.loads((BASE/'round3_national_survey.json').read_text())['rows']
    registry=json.loads(Path('config/physical_current_sources.json').read_text())
    new_admissions={r['station_id'] for r in registry['sources'] if r.get('settlement_grade')
                   and r['provider'] in {'metaviatelecom_metar','imd_olbs_metar','mgm_metar'}}
    results=[]
    for sid,city in CITY_BY_STATION.items():
        country=city.country_code;name,status,url,note=ORIGINS[country]
        items=[]
        for r in comparisons:
            if r['station']!=sid:continue
            rr=dict(r);rr['publisher_role']=('NATIONAL_ORIGIN' if r['channel'] in DOMESTIC.get(country,set()) else
                  'CURRENT_PUBLIC_REFERENCE' if r['channel']=='awc' else 'NATIONAL_SERVICE_REDISTRIBUTION')
            if r['channel']=='knmi_observations':rr['publisher_role']='NATIONAL_ORIGIN_OTHER_CLOCK_GRID'
            rr['timing_summary']=short_interval(r);items.append(rr)
        history=[r for r in cumulative if r['station']==sid]
        native=[r for r in items if r['publisher_role'].startswith('NATIONAL_ORIGIN')]
        alternatives=[r for r in items if r['publisher_role']=='NATIONAL_SERVICE_REDISTRIBUTION']
        baseline=[r for r in items if r['channel']=='awc']
        pair_text='; '.join(f"{r['channel']} {r['n_exact']}/{r['n_pairs']}" for r in native) or '0/0 native (access disposition below)'
        alternative_text='; '.join(f"{r['channel']} {r['n_exact']}/{r['n_pairs']}" for r in alternatives) or 'none acquired'
        old=next(r for r in prior if r['city']==city.name)
        decision=('PROMOTED_NATIVE_FAST' if sid in new_admissions else 'RETAIN_ADMITTED_NATIVE' if country in {'JP','CA','KR'}
           else 'RETAIN_NATIVE_RESOLVER' if country in {'HK','US'} else 'PHYSICAL_ONLY_PRIOR_OR_NEW_MISMATCH' if country in {'ES','FI','DE','PL','SG'}
           else 'NO_ALTERNATIVE_SPEED_PROMOTION')
        timing='; '.join(r['channel']+': '+r['timing_summary'] for r in native+alternatives
                        if r.get('races')) or 'No bracketed paired alternative transition; publication lag unknown'
        row={'city':city.name,'station':sid,'country':country,'unit':city.settlement_unit,'native_candidate':name,
             'access_status':status,'public_url':url,'disposition':note,'decision':decision,
             'native_pair_summary':pair_text,'redistribution_pair_summary':alternative_text,
             'measured_timing_summary':timing,
             'reference_pair_summary':'; '.join(f"AWC {r['n_exact']}/{r['n_pairs']}" for r in baseline),
             'comparisons':items,'cumulative':history,
             'prior_contradiction':{'n_pairs':old['n_pairs'],'n_exact':old['n_exact'],'mismatches':old['mismatches']} if country in {'FI','DE','PL','SG'} else None}
        results.append(row)
    assert len(results)==54 and len({r['city'] for r in results})==54
    report={'roster_source':'src.config.cities at pinned base 6824cbc06; actual import, not memory',
        'roster_count':54,'policy':'FREE_PUBLIC_ONLY; registration without operator action unavailable => requires registration; no paid/commercial/login acquisition',
        'completion_boundary':'Every city has a reviewed access disposition and measured reference/alternative evidence. This is not a claim of exact native pairs or finite publication bounds where access/clock grid or absence of a transition prevented them.',
        'value_rule':'same station and UTC valid instant; contract-native units and SettlementSemantics.round_single; any recorded contradiction retained',
        'counts':'exact matches / unique paired instants; neither repeated polls nor cross-window overlapping observations are independent new pairs',
        'clock_rule':'first positive complete response bounded by preceding negative request; negative lower bounds are preserved; no observation-age-as-publication-lag substitution',
        'rows':results}
    (BASE/'round4_national_survey.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    cols=['city','station','country','native_candidate','access_status','native_pair_summary','redistribution_pair_summary','reference_pair_summary','measured_timing_summary','decision','disposition','public_url']
    with (BASE/'round4_national_survey.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=cols,extrasaction='ignore',lineterminator='\n');writer.writeheader();writer.writerows(results)
    lines=['# Round 4 — every configured city, free public sources only','',report['completion_boundary'],'',
       'Pairs are exact matches / distinct exact-time overlaps. Full mismatch values and interval bounds are in the JSON. National origin and redistribution are different roles.',
       '', '|City / station|Free native candidate / access|Native exact / paired|Other public channel exact / paired|Decision|','|---|---|---|---|---|']
    for r in results:lines.append(f"|{r['city']} / {r['station']}|{r['native_candidate']} — {r['access_status']}|{r['native_pair_summary']}|{r['redistribution_pair_summary']}|{r['decision']}|")
    lines+=['','## Measured per-city first-availability comparisons','',
        'Intervals are seconds after the stated UTC observation instant. Unknown is not zero. Negative lower bounds are uncertainty bounds, not negative physical latency.','']
    for r in results:lines.append(f"**{r['city']} ({r['station']})** — {r['measured_timing_summary']}")
    lines+=['','## Access and source references','']
    for r in results:lines.append(f"**{r['city']} ({r['station']})** — {r['disposition']} Source: {r['public_url']}")
    (BASE/'ROUND4_NATIONAL_SURVEY.md').write_text('\n'.join(lines)+'\n')
    print('Wrote complete',len(results),'city inventory; no unmeasured numeric claim has been filled in.')
if __name__=='__main__':main()

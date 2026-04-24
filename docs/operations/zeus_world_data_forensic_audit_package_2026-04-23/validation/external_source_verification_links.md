# external_source_verification_links.md

These are the concrete external/source references that should be rechecked before changing source behavior. They are listed as source classes because market-specific pages and station pages change over time.

## ECMWF / TIGGE

- ECMWF TIGGE Confluence documentation — THORPEX Interactive Grand Global Ensemble: https://confluence.ecmwf.int/display/TIGGE
- TIGGE public data access update / ECMWF Data Stores transition: https://confluence.ecmwf.int/display/TIGGE/TIGGE+public+data+access+update%3A+transition+to+ECMWF+Data+Stores
- ECMWF Open Data documentation: https://www.ecmwf.int/en/forecasts/datasets/open-data

## Open-Meteo

- Historical Weather API: https://open-meteo.com/en/docs/historical-weather-api
- Historical Forecast API / Previous Runs: https://open-meteo.com/en/docs/historical-forecast-api
- Forecast API documentation: https://open-meteo.com/en/docs

## Meteostat

- Meteostat developer documentation: https://dev.meteostat.net/
- Meteostat bulk data interface: https://dev.meteostat.net/bulk/
- Meteostat hourly data endpoint: https://dev.meteostat.net/api/point/hourly.html
- Meteostat data quality/provider caveats: https://dev.meteostat.net/faq.html

## Ogimet

- Ogimet METAR query service: https://www.ogimet.com/metars.phtml.en
- Ogimet SYNOP query service: https://www.ogimet.com/synops.phtml.en
- Ogimet documentation home: https://www.ogimet.com/home.phtml.en

## WU / Weather Company

- Weather Underground history entry point: https://www.wunderground.com/history
- Example WU station page for Chicago O'Hare: https://www.wunderground.com/history/daily/us/il/chicago/KORD
- Weather Company current/historical API documentation entry point: https://developer.weather.com/docs/current-historical  
  LOCAL_VERIFICATION_REQUIRED: identify the exact Weather Company/WU contract page or API document used by the current code. If no contractual documentation exists for the private WU endpoint, treat it as unstable.

## HKO

- Hong Kong Observatory climatological information: https://www.weather.gov.hk/en/cis/climat.htm
- HKO data services entry point: https://www.hko.gov.hk/en/abouthko/opendata_intro.htm

## Polymarket weather rules

- Polymarket market pages for each active/resolved weather contract must be captured per market. Example rule text observed during audit tied markets to station/source pages and finalization/revision behavior.
- Polymarket CLOB API documentation: https://docs.polymarket.com/

## Source-risk/news context

- April 2026 reports about suspected Paris weather-station tampering around Polymarket weather contracts are source-risk evidence. They should trigger station/oracle-risk monitoring, not broad assumptions about all markets.

"""P0 — ping every public source and print status + notes. Run on Replit (outbound internet): python -m scripts.verify_sources"""
import httpx

from app.core.config import get_settings

CHECKS = [
    ("CMS catalog", "https://data.cms.gov/data.json", {}),
    ("openFDA label", "https://api.fda.gov/drug/label.json", {"search": 'openfda.generic_name:"palbociclib"', "limit": 1}),
    ("ClinicalTrials.gov v2", "https://clinicaltrials.gov/api/v2/studies", {"query.cond": "breast cancer", "pageSize": 1}),
    ("RxNav", "https://rxnav.nlm.nih.gov/REST/rxcui.json", {"name": "palbociclib"}),
    ("CDC PLACES", f"https://data.cdc.gov/resource/{get_settings().places_dataset_id}.json", {"$limit": 1}),
    ("Census ACS", f"https://api.census.gov/data/{get_settings().acs_year}/acs/acs5", {"get": "NAME", "for": "state:25"}),
    ("Census trade", "https://api.census.gov/data/timeseries/intltrade/imports/hs",
     {"get": "CTY_NAME,GEN_VAL_YR", "I_COMMODITY": "30", "COMM_LVL": "HS2", "time": "2025-12"}),
    ("Federal Register", "https://www.federalregister.gov/api/v1/documents.json", {"per_page": 1}),
    ("PubMed", "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi", {"db": "pubmed", "term": "palbociclib",
                                                                               "retmode": "json", "rettype": "count"}),
]

if __name__ == "__main__":
    ua = {"User-Agent": get_settings().user_agent}
    with httpx.Client(timeout=30, follow_redirects=True, headers=ua) as c:
        for name, url, params in CHECKS:
            try:
                r = c.get(url, params=params)
                print(f"{'OK ' if r.status_code == 200 else 'ERR'} {r.status_code}  {name:24} {len(r.content):>10,} bytes")
            except httpx.HTTPError as exc:
                print(f"ERR ---  {name:24} {type(exc).__name__}")

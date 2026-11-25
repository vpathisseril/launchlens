"""Public-data connectors. Each returns (data, Citation) and goes through the lineage cache.

Endpoints were chosen from official API documentation; dataset identifiers that change between releases are
resolved at runtime (CMS catalog) or configurable via env (see .env.example).
"""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.data.fetch import SourceUnavailable, cached_json
from app.research.contract import Citation

CMS_CATALOG = "https://data.cms.gov/data.json"
CMS_TITLES = {
    "partd_prescriber_drug": "Medicare Part D Prescribers - by Provider and Drug",
    "partd_spending": "Medicare Part D Spending by Drug",
    "partb_spending": "Medicare Part B Spending by Drug",
}

STATE_FIPS = {
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO", "09": "CT", "10": "DE", "11": "DC",
    "12": "FL", "13": "GA", "15": "HI", "16": "ID", "17": "IL", "18": "IN", "19": "IA", "20": "KS", "21": "KY",
    "22": "LA", "23": "ME", "24": "MD", "25": "MA", "26": "MI", "27": "MN", "28": "MS", "29": "MO", "30": "MT",
    "31": "NE", "32": "NV", "33": "NH", "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND", "39": "OH",
    "40": "OK", "41": "OR", "42": "PA", "44": "RI", "45": "SC", "46": "SD", "47": "TN", "48": "TX", "49": "UT",
    "50": "VT", "51": "VA", "53": "WA", "54": "WV", "55": "WI", "56": "WY",
}
STATES = sorted(STATE_FIPS.values())


def _num(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", "").replace("$", ""))
    except ValueError:
        return None


# ------------------------------------------------------------------ CMS (data.cms.gov data API)
def _reduce_catalog(payload: Any) -> dict[str, list[list[str]]]:
    out: dict[str, list[list[str]]] = {}
    wanted = set(CMS_TITLES.values())
    for ds in payload.get("dataset", []):
        title = ds.get("title", "")
        if title not in wanted:
            continue
        dists = []
        for d in ds.get("distribution", []):
            url = d.get("accessURL", "")
            if d.get("format") == "API" and "/data-api/v1/dataset/" in url:
                temporal = str(d.get("temporal", ""))
                year = temporal[:4] if temporal[:4].isdigit() else ""
                dists.append([year, url])
        out[title] = sorted(dists, key=lambda x: x[0], reverse=True)
    return out


def cms_distributions(db: Session, source_key: str) -> list[tuple[str, str]]:
    """Returns [(year, api_url)] newest first for a CMS dataset title."""
    cat, _ = cached_json(db, "cms_catalog", CMS_CATALOG, ttl_hours=24 * 7, reducer=_reduce_catalog)
    rows = (cat or {}).get(CMS_TITLES[source_key], [])
    seen, out = set(), []
    for year, url in rows:
        if url not in seen:
            seen.add(url)
            out.append((year, url))
    if not out:
        raise SourceUnavailable(source_key, f"Could not find '{CMS_TITLES[source_key]}' in the CMS catalog.")
    return out


def cms_rows(db: Session, source_key: str, api_url: str, filters: dict[str, str], year: str, *,
             cap: int = 20000, columns: list[str] | None = None) -> tuple[list[dict[str, Any]], Citation, bool]:
    """Pages through a CMS data-api dataset with column filters. Returns (rows, citation, truncated)."""
    rows: list[dict[str, Any]] = []
    size, offset, truncated = 5000, 0, False
    cite: Citation | None = None

    def red(p: Any) -> Any:
        if not isinstance(p, list):
            return []
        if columns:
            return [{k: r.get(k) for k in columns} for r in p]
        return p

    while True:
        params = {f"filter[{k}]": v for k, v in filters.items()}
        params.update({"size": size, "offset": offset})
        page, c = cached_json(db, source_key, api_url, params, ttl_hours=24 * 30, vintage=year,
                              locator=", ".join(f"{k}={v}" for k, v in filters.items()), reducer=red)
        cite = cite or c
        page = page or []
        rows.extend(page)
        if len(page) < size:
            break
        offset += size
        if offset >= cap:
            truncated = True
            break
    assert cite is not None
    return rows, cite, truncated


def cms_drug_rows(db: Session, source_key: str, generic: str, year_index: int = 0, extra: dict[str, str] | None = None,
                  columns: list[str] | None = None, cap: int = 20000) -> tuple[list[dict[str, Any]], Citation, bool, str]:
    dists = cms_distributions(db, source_key)
    if year_index >= len(dists):
        raise SourceUnavailable(source_key, "Requested vintage not available.")
    year, url = dists[year_index]
    f = {"Gnrc_Name": generic.strip().title()}
    f.update(extra or {})
    rows, cite, trunc = cms_rows(db, source_key, url, f, year, cap=cap, columns=columns)
    if not rows and generic.strip().title() != generic.strip().upper():
        f["Gnrc_Name"] = generic.strip().upper()
        rows, cite, trunc = cms_rows(db, source_key, url, f, year, cap=cap, columns=columns)
    return rows, cite, trunc, year


SPEND_COLS_PREFIX = ("Brnd_Name", "Gnrc_Name", "Tot_Mftr", "Mftr_Name", "Tot_Spndng_", "Tot_Clms_", "Tot_Benes_")


def _reduce_spending(payload: Any) -> Any:
    if not isinstance(payload, list):
        return []
    out = []
    for r in payload:
        if str(r.get("Mftr_Name", "")).strip().lower() != "overall":
            continue
        out.append({k: v for k, v in r.items() if k.startswith(SPEND_COLS_PREFIX)})
    return out


def spending_all(db: Session, source_key: str = "partd_spending") -> tuple[list[dict[str, Any]], Citation, str]:
    """All 'Overall' rows of Part D/B Spending by Drug (multi-year columns). Cached 30 days."""
    year, url = cms_distributions(db, source_key)[0]
    rows: list[dict[str, Any]] = []
    cite: Citation | None = None
    offset = 0
    while True:
        page, c = cached_json(db, source_key, url, {"size": 5000, "offset": offset}, ttl_hours=24 * 30,
                              vintage=year, reducer=_reduce_spending)
        cite = cite or c
        raw_len = len(page or [])
        rows.extend(page or [])
        if raw_len == 0 or offset > 60000:
            break
        offset += 5000
    assert cite is not None
    return rows, cite, year


def spend_years(row: dict[str, Any]) -> dict[int, float]:
    out = {}
    for k, v in row.items():
        m = re.match(r"Tot_Spndng_(\d{4})$", k)
        if m and _num(v) is not None:
            out[int(m.group(1))] = float(_num(v) or 0)
    return dict(sorted(out.items()))


# ------------------------------------------------------------------ openFDA
def openfda(db: Session, endpoint: str, search: str, limit: int = 100, count: str | None = None,
            locator: str = "") -> tuple[dict[str, Any], Citation]:
    params: dict[str, Any] = {"search": search}
    if count:
        params["count"] = count
    else:
        params["limit"] = limit
    if get_settings().openfda_api_key:
        params["api_key"] = get_settings().openfda_api_key
    data, cite = cached_json(db, "openfda", f"https://api.fda.gov/drug/{endpoint}.json", params,
                             ttl_hours=24 * 7, locator=locator or search)
    return data or {}, cite


def _fda_term(text: str) -> str:
    return re.sub(r'["\\]', "", text).strip()


def label_search(db: Session, indication: str, limit: int = 100) -> tuple[list[dict[str, Any]], Citation]:
    data, cite = openfda(db, "label", f'indications_and_usage:"{_fda_term(indication)}"', limit,
                         locator=f"label · indications_and_usage contains '{indication}'")
    return data.get("results", []), cite


def label_for_generic(db: Session, generic: str) -> tuple[dict[str, Any] | None, Citation]:
    data, cite = openfda(db, "label", f'openfda.generic_name:"{_fda_term(generic)}"', 5,
                         locator=f"label · generic_name={generic}")
    res = data.get("results", [])
    # prefer the most recently revised label
    res = sorted(res, key=lambda r: r.get("effective_time", ""), reverse=True)
    return (res[0] if res else None), cite


def drugsfda_for_generic(db: Session, generic: str) -> tuple[list[dict[str, Any]], Citation]:
    data, cite = openfda(db, "drugsfda", f'openfda.generic_name:"{_fda_term(generic)}"', 20,
                         locator=f"drugsfda · generic_name={generic}")
    return data.get("results", []), cite


def drugsfda_for_brand(db: Session, brand: str) -> tuple[list[dict[str, Any]], Citation]:
    data, cite = openfda(db, "drugsfda", f'openfda.brand_name:"{_fda_term(brand)}"', 10,
                         locator=f"drugsfda · brand_name={brand}")
    return data.get("results", []), cite


def first_approval(apps: list[dict[str, Any]]) -> tuple[str | None, str]:
    """Earliest original approval date (YYYYMMDD) and application type (NDA/BLA/ANDA)."""
    best: tuple[str, str] | None = None
    for a in apps:
        appno = str(a.get("application_number", ""))
        kind = "BLA" if appno.startswith("BLA") else ("ANDA" if appno.startswith("ANDA") else "NDA")
        if kind == "ANDA":
            continue
        for sub in a.get("submissions", []):
            if sub.get("submission_type") == "ORIG" and sub.get("submission_status") == "AP":
                d = sub.get("submission_status_date", "")
                if d and (best is None or d < best[0]):
                    best = (d, kind)
    return (best[0], best[1]) if best else (None, "")


def faers_count(db: Session, generic: str) -> tuple[int | None, Citation]:
    data, cite = openfda(db, "event", f'patient.drug.openfda.generic_name:"{_fda_term(generic)}"', limit=1,
                         locator=f"FAERS reports · generic_name={generic}")
    total = (data.get("meta", {}).get("results", {}) or {}).get("total")
    return total, cite


# ------------------------------------------------------------------ ClinicalTrials.gov v2
CT_FIELDS = ("NCTId,BriefTitle,OverallStatus,Phase,PrimaryCompletionDate,LeadSponsorName,InterventionName,"
             "PrimaryOutcomeMeasure,EnrollmentCount,StartDate")


def ctgov_studies(db: Session, condition: str, phases: list[str], statuses: list[str]) -> tuple[list[dict], Citation]:
    params = {"query.cond": condition, "filter.overallStatus": ",".join(statuses), "pageSize": 100,
              "fields": CT_FIELDS, "countTotal": "true"}
    if phases:
        params["filter.advanced"] = "AREA[Phase](" + " OR ".join(phases) + ")"
    data, cite = cached_json(db, "ctgov", "https://clinicaltrials.gov/api/v2/studies", params, ttl_hours=24,
                             locator=f"condition={condition}; phases={','.join(phases)}")
    out = []
    for s in (data or {}).get("studies", []):
        p = s.get("protocolSection", {})
        ident, status = p.get("identificationModule", {}), p.get("statusModule", {})
        design, spons = p.get("designModule", {}), p.get("sponsorCollaboratorsModule", {})
        arms, outc = p.get("armsInterventionsModule", {}), p.get("outcomesModule", {})
        out.append({
            "nct": ident.get("nctId", ""), "title": ident.get("briefTitle", ""),
            "status": status.get("overallStatus", ""), "phases": design.get("phases", []),
            "primary_completion": (status.get("primaryCompletionDateStruct") or {}).get("date", ""),
            "start": (status.get("startDateStruct") or {}).get("date", ""),
            "sponsor": (spons.get("leadSponsor") or {}).get("name", ""),
            "interventions": [i.get("name", "") for i in arms.get("interventions", [])][:4],
            "primary_outcome": ((outc.get("primaryOutcomes") or [{}])[0]).get("measure", ""),
            "enrollment": (design.get("enrollmentInfo") or {}).get("count"),
        })
    return out, cite


# ------------------------------------------------------------------ RxNorm / RxClass (NLM)
def rxcui_for(db: Session, name: str) -> tuple[str | None, Citation]:
    data, cite = cached_json(db, "rxnorm", "https://rxnav.nlm.nih.gov/REST/rxcui.json",
                             {"name": name, "search": 2}, ttl_hours=24 * 30, locator=f"name={name}")
    ids = ((data or {}).get("idGroup") or {}).get("rxnormId") or []
    return (ids[0] if ids else None), cite


def related_product_rxcuis(db: Session, rxcui: str) -> list[str]:
    """Clinical/branded drug RxCUIs (SCD/SBD/GPCK/BPCK) — the level used in CMS formulary files."""
    data, _ = cached_json(db, "rxnorm", f"https://rxnav.nlm.nih.gov/REST/rxcui/{rxcui}/related.json",
                          {"tty": "SCD SBD GPCK BPCK"}, ttl_hours=24 * 30)
    out = []
    for grp in ((data or {}).get("relatedGroup") or {}).get("conceptGroup") or []:
        for c in grp.get("conceptProperties") or []:
            out.append(c.get("rxcui"))
    return [x for x in out if x]


def class_peers(db: Session, generic: str) -> tuple[list[tuple[str, str]], Citation | None]:
    """ATC-4 class of a drug and its member ingredients — used to suggest competitors."""
    data, cite = cached_json(db, "rxnorm", "https://rxnav.nlm.nih.gov/REST/rxclass/class/byDrugName.json",
                             {"drugName": generic, "relaSource": "ATC"}, ttl_hours=24 * 30,
                             locator=f"ATC class of {generic}")
    infos = ((data or {}).get("rxclassDrugInfoList") or {}).get("rxclassDrugInfo") or []
    if not infos:
        return [], cite
    cls = infos[0].get("rxclassMinConceptItem", {})
    cid, cname = cls.get("classId"), cls.get("className", "")
    members, _ = cached_json(db, "rxnorm", "https://rxnav.nlm.nih.gov/REST/rxclass/classMembers.json",
                             {"classId": cid, "relaSource": "ATC"}, ttl_hours=24 * 30)
    out = []
    for m in ((members or {}).get("drugMemberGroup") or {}).get("drugMember") or []:
        nm = (m.get("minConcept") or {}).get("name", "")
        if nm:
            out.append((nm, cname))
    return out, cite


# ------------------------------------------------------------------ CDC PLACES / Census ACS
PLACES_MEASURES = {
    "": "— none —", "ARTHRITIS": "Arthritis", "BPHIGH": "High blood pressure", "CANCER": "Cancer (non-skin)",
    "CASTHMA": "Current asthma", "CHD": "Coronary heart disease", "COPD": "COPD", "DEPRESSION": "Depression",
    "DIABETES": "Diagnosed diabetes", "HIGHCHOL": "High cholesterol", "KIDNEY": "Chronic kidney disease",
    "OBESITY": "Obesity", "STROKE": "Stroke",
}


def places_state_prevalence(db: Session, measure: str) -> tuple[dict[str, float], Citation]:
    """Population-weighted state prevalence (%) computed from county crude prevalence."""
    ds = get_settings().places_dataset_id
    data, cite = cached_json(db, "places", f"https://data.cdc.gov/resource/{ds}.json",
                             {"$select": "stateabbr,data_value,totalpopulation,year",
                              "$where": f"measureid='{re.sub(r'[^A-Z]', '', measure)}' AND datavaluetypeid='CrdPrv'",
                              "$limit": 5000}, ttl_hours=24 * 30, locator=f"measure={measure}, crude prevalence")
    num: dict[str, float] = {}
    den: dict[str, float] = {}
    years = set()
    for r in data or []:
        st, v, pop = r.get("stateabbr"), _num(r.get("data_value")), _num(r.get("totalpopulation"))
        if st and v is not None and pop:
            num[st] = num.get(st, 0.0) + v * pop
            den[st] = den.get(st, 0.0) + pop
            years.add(str(r.get("year", "")))
    if years:
        cite.vintage = "/".join(sorted(y for y in years if y))
    return {st: round(num[st] / den[st], 2) for st in num if den.get(st)}, cite


ACS_65 = [f"B01001_{i:03d}E" for i in list(range(20, 26)) + list(range(44, 50))]


def acs_states(db: Session) -> tuple[dict[str, dict[str, float]], Citation]:
    s = get_settings()
    params: dict[str, Any] = {"get": "NAME,B01001_001E," + ",".join(ACS_65), "for": "state:*"}
    if s.census_api_key:
        params["key"] = s.census_api_key
    data, cite = cached_json(db, "acs", f"https://api.census.gov/data/{s.acs_year}/acs/acs5", params,
                             ttl_hours=24 * 90, vintage=f"ACS 5-year {s.acs_year}", locator="state population, 65+")
    p2: dict[str, Any] = {"get": "NAME,S2701_C05_001E", "for": "state:*"}
    if s.census_api_key:
        p2["key"] = s.census_api_key
    unins, _ = cached_json(db, "acs", f"https://api.census.gov/data/{s.acs_year}/acs/acs5/subject", p2,
                           ttl_hours=24 * 90, vintage=f"ACS 5-year {s.acs_year}", locator="percent uninsured")
    out: dict[str, dict[str, float]] = {}
    if data:
        hdr = data[0]
        for row in data[1:]:
            rec = dict(zip(hdr, row, strict=False))
            st = STATE_FIPS.get(rec.get("state", ""))
            if not st:
                continue
            pop = _num(rec.get("B01001_001E")) or 0
            p65 = sum(_num(rec.get(v)) or 0 for v in ACS_65)
            out[st] = {"pop": pop, "pop65": p65, "pct65": round(100 * p65 / pop, 1) if pop else 0.0}
    if unins:
        hdr = unins[0]
        for row in unins[1:]:
            rec = dict(zip(hdr, row, strict=False))
            st = STATE_FIPS.get(rec.get("state", ""))
            if st in out:
                out[st]["pct_uninsured"] = _num(rec.get("S2701_C05_001E")) or 0.0
    return out, cite


# ------------------------------------------------------------------ Policy sources
FR_FIELDS = ["title", "type", "abstract", "document_number", "html_url", "publication_date", "agencies",
             "effective_on", "comments_close_on"]


def federal_register(db: Session, term: str, per_page: int = 25) -> tuple[list[dict[str, Any]], Citation]:
    params: dict[str, Any] = {"conditions[term]": term, "order": "newest", "per_page": per_page,
                              "fields[]": FR_FIELDS}
    data, cite = cached_json(db, "federal_register", "https://www.federalregister.gov/api/v1/documents.json",
                             params, ttl_hours=12, locator=f"term={term}")
    return (data or {}).get("results", []), cite


def regulations_documents(db: Session, term: str) -> tuple[list[dict[str, Any]], Citation | None]:
    key = get_settings().regulations_api_key
    if not key:
        return [], None
    data, cite = cached_json(db, "regulations", "https://api.regulations.gov/v4/documents",
                             {"filter[searchTerm]": term, "sort": "-postedDate", "page[size]": 25, "api_key": key},
                             ttl_hours=12, locator=f"searchTerm={term}")
    return (data or {}).get("data", []), cite


def trade_imports_hs30(db: Session, year: int) -> tuple[list[dict[str, Any]], Citation]:
    params: dict[str, Any] = {"get": "CTY_CODE,CTY_NAME,GEN_VAL_YR", "I_COMMODITY": "30", "COMM_LVL": "HS2",
                              "time": f"{year}-12"}
    if get_settings().census_api_key:
        params["key"] = get_settings().census_api_key
    data, cite = cached_json(db, "trade", "https://api.census.gov/data/timeseries/intltrade/imports/hs", params,
                             ttl_hours=24 * 30, vintage=str(year), locator="HS chapter 30 imports, year-to-date Dec")
    out = []
    if data:
        hdr = data[0]
        for row in data[1:]:
            rec = dict(zip(hdr, row, strict=False))
            code, name = str(rec.get("CTY_CODE", "")), str(rec.get("CTY_NAME", ""))
            if not code.isdigit() or int(code) < 1000 or "TOTAL" in name.upper():
                continue
            out.append({"country": name.title(), "value": _num(rec.get("GEN_VAL_YR")) or 0.0})
    out.sort(key=lambda r: r["value"], reverse=True)
    return out, cite


def pubmed_count(db: Session, term: str) -> tuple[int | None, Citation]:
    data, cite = cached_json(db, "pubmed", "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
                             {"db": "pubmed", "term": term, "rettype": "count", "retmode": "json"},
                             ttl_hours=24 * 30, locator=f"term={term}")
    cnt = ((data or {}).get("esearchresult") or {}).get("count")
    return (int(cnt) if cnt is not None else None), cite

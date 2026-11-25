# Public Data & Document Catalog (P0: verify each endpoint, latest vintage, auth/key needs, and terms)
Every source is public. Commercial claims are not public, so Medicare/Medicaid serve as transparent proxies. State this on every Model Card.

## Access (R1)
| Key | Source | Access | Grain |
|---|---|---|---|
| partd_formulary | CMS Quarterly Prescription Drug Plan Formulary, Pharmacy Network & Pricing files | data.cms.gov bulk | Plan × RxCUI (tier, PA, ST, QL) |
| plan_enrollment | CMS Monthly Contract/Plan Enrollment | cms.gov / data.cms.gov | Contract × plan × state |
| medicaid_sdud | State Drug Utilization Data | data.medicaid.gov API | State × NDC × quarter |

## Pricing (R2)
| Key | Source | Access | Grain |
|---|---|---|---|
| negotiation_program | CMS Medicare Drug Price Negotiation Program: selected drug lists (by IPAY), MFPs, explanations | cms.gov pages/files (store as documents + parsed tables) | Drug × IPAY |
| partd_spending | Medicare Part D Spending by Drug | data.cms.gov | Drug × year |
| partb_spending | Medicare Part B Spending by Drug | data.cms.gov | Drug × year |
| asp_files | CMS Part B ASP Pricing Files | cms.gov quarterly files | HCPCS × quarter |
| nadac | Medicaid NADAC | data.medicaid.gov | NDC × week |
| mfn_items | Official MFN announcements (whitehouse.gov, hhs.gov, company press releases as secondary) | stored as policy documents | Item |

## Policy (R3)
| Key | Source | Access | Grain |
|---|---|---|---|
| federal_register | Federal Register API | federalregister.gov/api/v1 (no key) | Document |
| congress | Congress.gov API | api.congress.gov (free key) | Bill × action |
| regulations | Regulations.gov API | api.regulations.gov (free key) | Docket / comment period |
| ftc_pbm | FTC PBM 6(b) study reports | ftc.gov (documents) | Report |
| trade | Census International Trade API (HTS ch. 30) / USITC DataWeb | api.census.gov | HTS × country × month |
| edgar | SEC EDGAR full-text and company APIs | efts.sec.gov / data.sec.gov | Filing |

## Targeting (R4)
| Key | Source | Access | Grain |
|---|---|---|---|
| partd_prescriber_drug | Medicare Part D Prescribers by Provider & Drug (multi-year) | data.cms.gov | NPI × drug × year (<11 suppressed) |
| nppes | NPI Registry | npiregistry.cms.hhs.gov API + monthly file | NPI |
| open_payments | Open Payments | openpaymentsdata.cms.gov API | Payment × NPI × product |
| places | CDC PLACES | data.cdc.gov (Socrata) | County/tract × measure |
| acs | ACS 5-year | api.census.gov (free key) | County/tract |
| hrsa | HRSA Shortage Areas | data.hrsa.gov | Area |

## Differentiation (R5)
| Key | Source | Access | Grain |
|---|---|---|---|
| openfda | openFDA: label, drugsfda, ndc, event | api.fda.gov (free key) | Product / report |
| orange_purple | Orange Book / Purple Book | FDA downloads | Product |
| ctgov | ClinicalTrials.gov API v2 | clinicaltrials.gov/api/v2 | Study |
| rxnorm | RxNav / RxClass | rxnav.nlm.nih.gov | RxCUI / class |
| pubmed | PubMed E-utilities | eutils.ncbi.nlm.nih.gov | Article count |

## Rules
Use official APIs or bulk files only; be polite (concurrency ≤ 4, backoff, identify the app in the User-Agent); cache with TTL; every curated row and every
document chunk carries `source_key, vintage_or_date, url, retrieved_at`. Documents are chunked by section or paragraph for agent citations.

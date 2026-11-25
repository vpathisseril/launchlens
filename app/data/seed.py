"""Idempotent seed: modules, data-source registry, AI policy, model registry, agent templates, and the CMS
Medicare Drug Price Negotiation Program selected-drug lists (transcribed from official CMS publications; each row
links to its CMS source). Admins can correct or extend rows in Admin → Pricing data.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Agent, DataSource, DefaultKeyPolicy, ModelPrice, Module, NegotiationDrug
from app.services.accounts import set_setting

MODULES = [
    ("access", "Market Access & Payer Barriers", "shield-check", 1),
    ("pricing", "Drug Pricing Policy · IRA + MFN", "badge-dollar-sign", 2),
    ("policy", "Policy Convergence Radar", "radar", 3),
    ("targeting", "Precision Targeting & Time-to-Peak", "crosshair", 4),
    ("differentiation", "Market Understanding & Differentiation", "git-compare", 5),
    ("agents", "Agent Builder", "bot", 6),
]

SOURCES = [
    ("cms_catalog", "CMS data catalog (data.json)", "CMS", "platform", "https://data.cms.gov/data.json", "weekly"),
    ("partd_prescriber_drug", "Medicare Part D Prescribers – by Provider and Drug", "CMS", "targeting",
     "https://data.cms.gov/provider-summary-by-type-of-service/medicare-part-d-prescribers", "annual"),
    ("partd_spending", "Medicare Part D Spending by Drug", "CMS", "pricing",
     "https://data.cms.gov/summary-statistics-on-use-and-payments/medicare-medicaid-spending-by-drug", "annual"),
    ("partb_spending", "Medicare Part B Spending by Drug", "CMS", "pricing",
     "https://data.cms.gov/summary-statistics-on-use-and-payments/medicare-medicaid-spending-by-drug", "annual"),
    ("partd_formulary", "Part D Formulary, Pharmacy Network & Pricing files (SPUF)", "CMS", "access",
     "https://www.cms.gov/research-statistics-data-and-systems/files-for-order/nonidentifiabledatafiles/"
     "prescriptiondrugplanformularypharmacynetworkandpricinginformationfiles", "quarterly"),
    ("negotiation_program", "Medicare Drug Price Negotiation Program publications", "CMS", "pricing",
     "https://www.cms.gov/priorities/medicare-prescription-drug-affordability/overview/"
     "medicare-drug-price-negotiation-program/selected-drugs-negotiated-prices", "per cycle"),
    ("openfda", "openFDA drug label, Drugs@FDA, FAERS", "FDA", "differentiation", "https://open.fda.gov/apis/drug/",
     "weekly"),
    ("ctgov", "ClinicalTrials.gov API v2", "NLM/NIH", "differentiation", "https://clinicaltrials.gov/data-api/api",
     "daily"),
    ("rxnorm", "RxNorm / RxClass (RxNav)", "NLM/NIH", "platform", "https://lhncbc.nlm.nih.gov/RxNav/APIs/", "monthly"),
    ("places", "CDC PLACES county estimates", "CDC", "targeting", "https://www.cdc.gov/places/", "annual"),
    ("acs", "American Community Survey 5-year", "U.S. Census Bureau", "targeting", "https://www.census.gov/data/developers.html",
     "annual"),
    ("federal_register", "Federal Register API", "Office of the Federal Register", "policy",
     "https://www.federalregister.gov/developers/documentation/api/v1", "daily"),
    ("regulations", "Regulations.gov API v4", "GSA eRulemaking", "policy", "https://open.gsa.gov/api/regulationsgov/",
     "daily"),
    ("trade", "International Trade (imports, HS)", "U.S. Census Bureau", "policy",
     "https://www.census.gov/foreign-trade/reference/guides/Guide_to_International_Trade_Datasets.pdf", "monthly"),
    ("pubmed", "PubMed E-utilities", "NLM/NIH", "differentiation", "https://www.ncbi.nlm.nih.gov/books/NBK25501/",
     "daily"),
]

CMS_2026 = ("https://www.cms.gov/files/document/fact-sheet-negotiated-prices-initial-price-applicability-year-2026.pdf",
            "IPAY 2026 negotiated prices (published Aug 2024)")
CMS_2027 = ("https://www.cms.gov/priorities/medicare-prescription-drug-affordability/overview/"
            "medicare-drug-price-negotiation-program/selected-drugs-negotiated-prices",
            "IPAY 2027 negotiated prices (published Nov 2025)")
CMS_2028 = ("https://www.cms.gov/newsroom/press-releases/cms-announces-selection-drugs-third-cycle-medicare-drug-price-"
            "negotiation-program-including-first", "IPAY 2028 selected drugs (announced Jan 27, 2026)")
CMS_2028_FS = ("https://www.cms.gov/files/document/factsheet-medicare-negotiation-selected-drug-list-ipay-2028.pdf",
               "IPAY 2028 selected drug fact sheet (Jan 2026)")
CMS_TOP50 = ("https://www.cms.gov/files/document/factsheet-medicare-top-50-negotiation-eligible-drug-list-ipay-2028.pdf",
             "IPAY 2028 top-50 negotiation-eligible list (Jan 2026)")

# IPAY 2028 'Part' is "—" for products with physician-administered forms: CMS lists 5 Part B selections; see fact sheet.
# (ipay, drug, generic, sponsor, part, conditions, type, spend, benes, list30, mfp30, discount, status, src)
NEGOTIATION = [
    (2026, "Eliquis", "apixaban", "Bristol Myers Squibb", "D", "Prevention and treatment of blood clots", "negotiation",
     18275108000, 3928000, 521, 231, 56, "MFP in effect (Jan 1, 2026)", CMS_2026),
    (2026, "Jardiance", "empagliflozin", "Boehringer Ingelheim", "D", "Diabetes; Heart failure; Chronic kidney disease",
     "negotiation", None, None, None, 197, 66, "MFP in effect (Jan 1, 2026)", CMS_2026),
    (2026, "Xarelto", "rivaroxaban", "Janssen", "D", "Prevention and treatment of blood clots", "negotiation",
     None, None, None, 197, 62, "MFP in effect (Jan 1, 2026)", CMS_2026),
    (2026, "Januvia", "sitagliptin", "Merck Sharp & Dohme", "D", "Diabetes", "negotiation", 4091399000, 843000, 527, 113,
     79, "MFP in effect (Jan 1, 2026)", CMS_2026),
    (2026, "Farxiga", "dapagliflozin", "AstraZeneca", "D", "Diabetes; Heart failure; Chronic kidney disease",
     "negotiation", 4342594000, 994000, 556, 178.50, 68, "MFP in effect (Jan 1, 2026)", CMS_2026),
    (2026, "Entresto", "sacubitril/valsartan", "Novartis", "D", "Heart failure", "negotiation", 3430753000, 664000, 628,
     295, 53, "MFP in effect (Jan 1, 2026)", CMS_2026),
    (2026, "Enbrel", "etanercept", "Immunex", "D", "Rheumatoid arthritis; Psoriasis; Psoriatic arthritis",
     "negotiation", 2951778000, 48000, 7106, 2355, 67, "MFP in effect (Jan 1, 2026)", CMS_2026),
    (2026, "Imbruvica", "ibrutinib", "Pharmacyclics", "D", "Blood cancers", "negotiation", None, None, 14934, 9319, 38,
     "MFP in effect (Jan 1, 2026)", CMS_2026),
    (2026, "Stelara", "ustekinumab", "Janssen", "D", "Psoriasis; Psoriatic arthritis; Crohn's disease; Ulcerative colitis",
     "negotiation", None, None, 13836, 4695, 66, "MFP in effect (Jan 1, 2026)", CMS_2026),
    (2026, "Fiasp; NovoLog", "insulin aspart", "Novo Nordisk", "D", "Diabetes", "negotiation", 2612719000, 785000, 495,
     119, 76, "MFP in effect (Jan 1, 2026)", CMS_2026),
    (2027, "Ozempic; Rybelsus; Wegovy", "semaglutide", "Novo Nordisk", "D",
     "Type 2 diabetes; Chronic kidney disease; Cardiovascular disease; Obesity/overweight", "negotiation", None, None,
     959, 274, 71, "MFP published; effective Jan 1, 2027", CMS_2027),
    (2027, "Trelegy Ellipta", "fluticasone furoate/umeclidinium/vilanterol", "GSK", "D", "Asthma; COPD", "negotiation",
     5296660000, 1269000, 654, 175, 73, "MFP published; effective Jan 1, 2027", CMS_2027),
    (2027, "Xtandi", "enzalutamide", "Astellas", "D", "Prostate cancer", "negotiation", 3401099000, 35000, 13480, 7004,
     48, "MFP published; effective Jan 1, 2027", CMS_2027),
    (2027, "Pomalyst", "pomalidomide", "Bristol Myers Squibb", "D", "Kaposi sarcoma; Multiple myeloma", "negotiation",
     2150644000, 14000, 21744, 8650, 60, "MFP published; effective Jan 1, 2027", CMS_2027),
    (2027, "Ibrance", "palbociclib", "Pfizer", "D", "Breast cancer", "negotiation", None, None, 15741, 7871, 50,
     "MFP published; effective Jan 1, 2027", CMS_2027),
    (2027, "Ofev", "nintedanib", "Boehringer Ingelheim", "D", "Idiopathic pulmonary fibrosis", "negotiation", None, None,
     12622, 6350, 50, "MFP published; effective Jan 1, 2027", CMS_2027),
    (2027, "Linzess", "linaclotide", "AbbVie", "D", "Chronic idiopathic constipation; IBS with constipation",
     "negotiation", None, None, 539, 136, 75, "MFP published; effective Jan 1, 2027", CMS_2027),
    (2027, "Calquence", "acalabrutinib", "AstraZeneca", "D", "CLL/SLL; Mantle cell lymphoma", "negotiation", None, None,
     None, None, None, "MFP published; effective Jan 1, 2027", CMS_2027),
    (2027, "Austedo; Austedo XR", "deutetrabenazine", "Teva", "D", "Chorea (Huntington's disease); Tardive dyskinesia",
     "negotiation", None, None, None, None, None, "MFP published; effective Jan 1, 2027", CMS_2027),
    (2027, "Breo Ellipta", "fluticasone furoate/vilanterol", "GSK", "D", "Asthma; COPD", "negotiation", None, None, None,
     None, None, "MFP published; effective Jan 1, 2027", CMS_2027),
    (2027, "Tradjenta", "linagliptin", "Boehringer Ingelheim", "D", "Type 2 diabetes", "negotiation", None, None, None,
     None, 84, "MFP published; effective Jan 1, 2027", CMS_2027),
    (2027, "Xifaxan", "rifaximin", "Salix", "D", "Hepatic encephalopathy; IBS with diarrhea", "negotiation", None, None,
     None, None, None, "MFP published; effective Jan 1, 2027", CMS_2027),
    (2027, "Vraylar", "cariprazine", "AbbVie", "D", "Bipolar I disorder; Major depressive disorder; Schizophrenia",
     "negotiation", None, None, None, None, 44, "MFP published; effective Jan 1, 2027", CMS_2027),
    (2027, "Janumet; Janumet XR", "sitagliptin/metformin", "Merck Sharp & Dohme", "D", "Type 2 diabetes", "negotiation",
     None, None, None, None, 85, "MFP published; effective Jan 1, 2027", CMS_2027),
    (2027, "Otezla", "apremilast", "Amgen", "D", "Psoriasis; Psoriatic arthritis; Behçet's disease", "negotiation", None,
     None, None, None, None, "MFP published; effective Jan 1, 2027", CMS_2027),
]
IPAY_2028 = [
    ("Anoro Ellipta", "umeclidinium/vilanterol", "GSK", "COPD", None, None, CMS_2028),
    ("Biktarvy", "bictegravir/emtricitabine/tenofovir alafenamide", "Gilead Sciences", "HIV-1 infection", 3904486000,
     101000, CMS_2028_FS),
    ("Botox; Botox Cosmetic", "onabotulinumtoxinA", "AbbVie", "Chronic migraine; overactive bladder; spasticity",
     1143070000, None, CMS_TOP50),
    ("Cimzia", "certolizumab pegol", "UCB", "Rheumatoid arthritis; Crohn's disease; other autoimmune", None, None, CMS_2028),
    ("Cosentyx", "secukinumab", "Novartis", "Psoriasis; psoriatic arthritis; other", None, None, CMS_2028),
    ("Entyvio", "vedolizumab", "Takeda", "Crohn's disease; ulcerative colitis", None, None, CMS_2028),
    ("Erleada", "apalutamide", "Janssen", "Prostate cancer", None, None, CMS_2028),
    ("Kisqali", "ribociclib", "Novartis", "Breast cancer", None, None, CMS_2028),
    ("Lenvima", "lenvatinib", "Eisai", "Cancer", None, None, CMS_2028),
    ("Orencia", "abatacept", "Bristol Myers Squibb", "Rheumatoid arthritis; other", None, None, CMS_2028),
    ("Rexulti", "brexpiprazole", "Otsuka/Lundbeck", "Schizophrenia; MDD adjunct; other", None, None, CMS_2028),
    ("Trulicity", "dulaglutide", "Eli Lilly", "Type 2 diabetes; CV risk reduction", 4898378000, 617000, CMS_2028_FS),
    ("Verzenio", "abemaciclib", "Eli Lilly", "Breast cancer", None, None, CMS_2028),
    ("Xeljanz; Xeljanz XR", "tofacitinib", "Pfizer", "Rheumatoid arthritis; ulcerative colitis; other", None, None,
     CMS_2028),
    ("Xolair", "omalizumab", "Genentech/Novartis", "Asthma; chronic urticaria; food allergy", 1077271000, None, CMS_TOP50),
]

TEMPLATES = [
    ("Access Analyst", "shield-check", "access", "A payer-access analyst who knows Part D formulary mechanics.",
     "Identify where coverage restrictions block patients and which states to fix first.",
     ["access.access_summary", "access.state_access", "access.access_trend", "targeting.white_space"]),
    ("Pricing Policy Analyst", "badge-dollar-sign", "pricing", "A neutral drug-pricing policy analyst.",
     "Explain negotiation exposure, negotiated prices and likely candidates, citing CMS publications.",
     ["pricing.selected_drugs", "pricing.candidates", "pricing.class_exposure", "pricing.mfn_items"]),
    ("Policy Radar Scout", "radar", "policy", "A government-affairs researcher who reports only official status.",
     "Summarize what changed, upcoming deadlines and which channels are exposed.",
     ["policy.search_policy", "policy.deadlines", "policy.exposure", "policy.trade"]),
    ("Targeting Strategist", "crosshair", "targeting", "A commercial analytics lead focused on time-to-peak.",
     "Recommend which prescriber segments and states to prioritize and why.",
     ["targeting.top_hcps", "targeting.white_space", "targeting.analog_ramp", "targeting.explain_score"]),
    ("Competitive Intel Analyst", "git-compare", "differentiation", "A competitive-intelligence analyst.",
     "Map competitors, label differences and pipeline threats; find white space.",
     ["differentiation.find_competitors", "differentiation.compare_labels", "differentiation.pipeline",
      "differentiation.market_size"]),
    ("Executive Brief Writer", "file-text", "", "A chief-of-staff who writes board-ready briefs.",
     "Synthesize evidence across all five hubs into a decision-ready summary.",
     ["access.access_summary", "pricing.class_exposure", "policy.deadlines", "targeting.top_hcps",
      "differentiation.pipeline"]),
]


def seed(db: Session) -> None:
    for key, name, icon, order in MODULES:
        m = db.scalar(select(Module).where(Module.key == key))
        if not m:
            db.add(Module(key=key, name=name, icon=icon, sort_order=order, description=name))
    for key, name, pub, hub, url, cadence in SOURCES:
        if not db.scalar(select(DataSource).where(DataSource.key == key)):
            db.add(DataSource(key=key, name=name, publisher=pub, hub_key=hub, url=url, refresh_cadence=cadence))
    for prov, allow, enabled in (("anthropic", ["claude-haiku-5-5"], True), ("openai", [], False), ("google", [], False)):
        if not db.scalar(select(DefaultKeyPolicy).where(DefaultKeyPolicy.provider == prov)):
            db.add(DefaultKeyPolicy(provider=prov, model_allowlist=allow, is_enabled=enabled))
    for prov, model, tier in (("anthropic", "claude-haiku-5-5", "economy"), ("anthropic", "claude-sonnet-5-5", "standard"),
                              ("anthropic", "claude-opus-5-5", "premium")):
        if not db.scalar(select(ModelPrice).where(ModelPrice.provider == prov, ModelPrice.model == model)):
            db.add(ModelPrice(provider=prov, model=model, tier=tier))
    if not db.scalar(select(NegotiationDrug.id).limit(1)):
        for ipay, drug, gen, sp, part, cond, typ, spend, benes, lst, mfp, disc, status, (url, vint) in NEGOTIATION:
            db.add(NegotiationDrug(ipay=ipay, drug=drug, generic=gen, sponsor=sp, part=part, conditions=cond,
                                   selection_type=typ, medicare_spend_usd=spend, beneficiaries=benes,
                                   list_price_30d=lst, mfp_30d=mfp, discount_pct=disc, status=status, source_url=url,
                                   vintage=vint))
        for drug, gen, sp, cond, spend, benes, (url, vint) in IPAY_2028:
            db.add(NegotiationDrug(ipay=2028, drug=drug, generic=gen, sponsor=sp, part="—" if drug.startswith(
                ("Botox", "Xolair", "Entyvio", "Orencia", "Cimzia", "Cosentyx")) else "D", conditions=cond,
                medicare_spend_usd=spend, beneficiaries=benes, status="Negotiating in 2026; prices effective Jan 1, 2028",
                source_url=url, vintage=vint))
        db.add(NegotiationDrug(ipay=2028, drug="Tradjenta", generic="linagliptin", sponsor="Boehringer Ingelheim",
                               part="D", conditions="Type 2 diabetes", selection_type="renegotiation",
                               status="Selected for renegotiation; effective Jan 1, 2028", source_url=CMS_2028[0],
                               vintage=CMS_2028[1]))
    for name, icon, hub, persona, goal, tools in TEMPLATES:
        if not db.scalar(select(Agent).where(Agent.is_template.is_(True), Agent.name == name)):
            db.add(Agent(owner_id=None, name=name, icon=icon, hub_key=hub, persona=persona, goal=goal, tools=tools,
                         is_template=True, tone="executive", provider="anthropic"))
    from app.services.accounts import get_setting
    if get_setting(db, "approval_mode", None) is None:
        set_setting(db, "approval_mode", "AUTO_AFTER_24H")
        set_setting(db, "approval_window_hours", 24)
    db.commit()

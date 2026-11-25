"""CMS catalog parsing — both the 2026 per-year shape and the legacy multi-distribution shape."""
from app.data.connectors import _reduce_catalog

TITLE = "Medicare Part D Prescribers - by Provider and Drug"


def test_catalog_2026_shape_per_year_datasets():
    payload = {"dataset": [
        {"title": f"{TITLE} : 2024-12-01", "distribution": [
            {"format": "API", "accessURL": "https://data.cms.gov/data-api/v1/dataset/a/data",
             "temporal": [{"@type": "PeriodOfTime", "startDate": "2024-01-01", "endDate": "2024-12-31"}]}]},
        {"title": f"{TITLE} : 2013-12-01", "distribution": [
            {"accessURL": "https://data.cms.gov/data-api/v1/dataset/old/data"}]},
        {"title": f"{TITLE} : 2023-12-01", "distribution": [
            {"downloadURL": "https://data.cms.gov/file.csv"},
            {"accessURL": "https://data.cms.gov/data-api/v1/dataset/b/data", "temporal": [{"startDate": "2023-01-01"}]}]},
    ]}
    out = _reduce_catalog(payload)[TITLE]
    assert [y for y, _ in out] == ["2024", "2023", "2013"]
    assert out[0][1].endswith("/a/data")


def test_catalog_legacy_shape():
    payload = {"dataset": [{"title": TITLE, "distribution": [
        {"format": "API", "accessURL": "https://data.cms.gov/data-api/v1/dataset/x/data", "temporal": "2022-01-01/2022-12-31"},
        {"format": "API", "accessURL": "https://data.cms.gov/data-api/v1/dataset/y/data", "temporal": "2023-01-01/2023-12-31"}]}]}
    assert [y for y, _ in _reduce_catalog(payload)[TITLE]] == ["2023", "2022"]


def test_catalog_ignores_similar_titles():
    payload = {"dataset": [{"title": "Medicare Part D Prescribers - by Provider : 2024-12-01", "distribution": [
        {"accessURL": "https://data.cms.gov/data-api/v1/dataset/z/data"}]}]}
    assert TITLE not in _reduce_catalog(payload)

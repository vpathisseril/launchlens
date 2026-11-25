.PHONY: dev test check sources
dev:      ## run locally with auto-reload
	uvicorn app.main:app --reload --port 8000
test:     ## run the test suite with coverage
	pytest --cov=app --cov-report=term-missing
check:    ## lint + security + tests (Definition of Done)
	ruff check app tests scripts
	bandit -q -r app -c pyproject.toml
	pip-audit -r requirements.txt
	pytest -q
sources:  ## P0: ping every public data source (needs internet)
	python -m scripts.verify_sources

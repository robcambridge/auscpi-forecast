.PHONY: install collect build test lint forecast

install:
	python -m venv .venv && .venv/bin/pip install -e ".[model,dev]"

collect:            ## Run every enabled collector once
	auscpi collect --all

build:              ## Rebuild data/curated from data/raw
	auscpi build

test:
	pytest -q

lint:
	ruff check src tests && ruff format --check src tests

forecast:           ## Produce and log a forecast for the next reference month
	auscpi forecast --log

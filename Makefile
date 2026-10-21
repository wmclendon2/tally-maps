# Copyright (c) 2026 Woody McLendon
# SPDX-License-Identifier: MIT
#
# Shortcuts for the commands in README.md; each target runs exactly what README.md shows.
# `make help` lists them. Countries are ISO3 codes: make site COUNTRIES="KEN TZA UGA"

COUNTRIES ?= CMR CAF
PORT ?= 8081
COUNTRY_FLAGS := $(foreach c,$(COUNTRIES),--country $(c))

.PHONY: help setup catalog packs site serve demo test lint check browser-setup parity countries-check verify overlaps split update-check

help: ## List the targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-16s %s\n", $$1, $$2}'
	@echo "  Countries default to $(COUNTRIES); override with COUNTRIES=\"KEN TZA\"."

setup: ## Install Python and the dependencies into .venv (needs uv)
	uv sync

catalog: ## List every country Meta has data for, with its download size
	uv run python -m ingest.hdx

packs: ## Download and build data packs for COUNTRIES into data/packs/
	uv run python scripts/build_packs.py $(COUNTRY_FLAGS)

site: ## Build the app for COUNTRIES into dist/ (builds missing packs first)
	uv run python scripts/build_static.py $(COUNTRY_FLAGS)

serve: ## Serve dist/ at http://127.0.0.1:8081, or PORT=... (Ctrl-C stops it)
	@echo "Open http://127.0.0.1:$(PORT)"
	python3 -m http.server $(PORT) --directory dist --bind 127.0.0.1

demo: site serve ## Build the app for COUNTRIES, then serve it

test: ## Run the test suite
	uv run pytest -q

lint: ## Check style with ruff
	uv run ruff check .
	uv run ruff format --check .

check: lint test ## Lint and test

browser-setup: ## Install the browser checks' headless Chromium (once)
	uv sync --group browser
	uv run --group browser playwright install chromium

parity: ## Compare the browser's answers with the Python resolver (needs browser-setup and a built dist/)
	uv run --group browser python scripts/browser_parity.py

countries-check: ## Check every country in dist/ in the browser, and its memory use
	uv run --group browser python scripts/browser_countries.py

update-check: ## Check that a tab left open survives a release (needs a built dist/)
	uv run --group browser python scripts/browser_update.py

overlaps: ## Find people counted in two countries where outlines overlap (all packs; ~1 h)
	uv run python scripts/audit_overlaps.py

split: ## Split overlapping outlines between their countries, taking in COUNTRIES (all packs)
	uv run python scripts/split_overlaps.py $(COUNTRY_FLAGS)

verify: ## Check COUNTRIES' totals and city positions from their packs
	@for c in $(COUNTRIES); do uv run python scripts/verify_country.py --country $$c || exit 1; done
	uv run python scripts/verify_cities.py --min-pop 0 $(COUNTRY_FLAGS)

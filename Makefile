# GeoNexus Reference Stack MVP - Makefile
PYTHON ?= .venv/bin/python
PIP     = $(PYTHON) -m pip
CLI     = $(PYTHON) -m geonexus.cli
PYTEST  = $(PYTHON) -m pytest

.PHONY: venv install dev test card node demo clean structure

## Create the virtual environment (Python 3.11+ recommended)
venv:
	python3.12 -m venv .venv

## Install the package and dev dependencies
install:
	$(PIP) install -e ".[dev]"

## Alias for install
dev: install

## Run the test suite
test:
	$(PYTEST)

## Validate the Amazon NDVI demo GeoCard
card:
	$(CLI) card validate examples/amazon_ndvi/geocard.yaml

## Start the Local GeoNode (GeoMCP server on 127.0.0.1:8787)
node:
	$(CLI) node start

## Run the Amazon NDVI demo (synthetic data)
demo:
	$(CLI) demo amazon-ndvi

## Remove generated artifacts
clean:
	rm -rf .pytest_cache .venv *.egg-info build dist
	find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true

## Print the project tree
structure:
	find . -path ./.venv -prune -o -type f -print | sort

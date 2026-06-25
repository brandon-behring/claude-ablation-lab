.PHONY: help install hooks test lint format type ci clean

EVAL_TOOLKIT ?= $(HOME)/Claude/eval-toolkit

help:
	@echo "Targets: install hooks test lint format type ci clean"

install:  ## Create venv deps; eval-toolkit installed editable from the local sibling repo (not on PyPI)
	pip install -e "$(EVAL_TOOLKIT)"
	pip install -e ".[dev]"

hooks:
	pre-commit install
	pre-commit install --hook-type pre-push

test:
	pytest

lint:
	ruff check src/ tests/
	black --check src/ tests/

format:
	ruff check --fix src/ tests/
	black src/ tests/

type:
	mypy src/

ci: lint type test

clean:
	rm -rf build/ dist/ *.egg-info .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
	find . -type d -name __pycache__ -exec rm -rf {} +

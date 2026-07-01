.PHONY: help install hooks test lint format type ci clean

# eval-toolkit is not on PyPI. `make install` fetches it from GitHub (works for anyone).
# For local editable dev, point EVAL_TOOLKIT at a checkout:
#   EVAL_TOOLKIT=~/eval-toolkit make install
EVAL_TOOLKIT ?=
EVAL_TOOLKIT_GIT ?= git+https://github.com/brandon-behring/eval-toolkit.git

help:
	@echo "Targets: install hooks test lint format type ci clean"

install:  ## eval-toolkit (editable if EVAL_TOOLKIT set, else from GitHub) + this package [dev]
	@if [ -n "$(EVAL_TOOLKIT)" ] && [ -d "$(EVAL_TOOLKIT)" ]; then \
		echo ">> eval-toolkit: editable from $(EVAL_TOOLKIT)"; \
		pip install -e "$(EVAL_TOOLKIT)"; \
	else \
		echo ">> eval-toolkit: from GitHub ($(EVAL_TOOLKIT_GIT))"; \
		pip install "$(EVAL_TOOLKIT_GIT)"; \
	fi
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

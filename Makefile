.PHONY: help install hooks test lint format type ci clean

# eval-toolkit is not on PyPI. `make install` fetches a pinned release from GitHub (works for anyone).
# For local editable dev, point EVAL_TOOLKIT at a checkout:
#   EVAL_TOOLKIT=~/eval-toolkit make install
EVAL_TOOLKIT ?=
EVAL_TOOLKIT_GIT ?= git+https://github.com/brandon-behring/eval-toolkit.git@v1.12.0

help:
	@echo "Targets: install hooks test lint format type ci clean"

install:  ## eval-toolkit (editable if EVAL_TOOLKIT set, else pinned from GitHub) + this package [dev]
	@if [ -n "$(EVAL_TOOLKIT)" ]; then \
		if [ ! -d "$(EVAL_TOOLKIT)" ]; then \
			echo "ERROR: EVAL_TOOLKIT=$(EVAL_TOOLKIT) is not a directory (unset it to install from GitHub)" >&2; \
			exit 1; \
		fi; \
		echo ">> eval-toolkit: editable from $(EVAL_TOOLKIT)"; \
		python -m pip install -e "$(EVAL_TOOLKIT)"; \
	else \
		echo ">> eval-toolkit: pinned from GitHub ($(EVAL_TOOLKIT_GIT))"; \
		python -m pip install "$(EVAL_TOOLKIT_GIT)"; \
	fi
	python -m pip install -e ".[dev,plot]"

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

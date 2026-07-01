"""Shared pytest fixtures for the suite."""

from __future__ import annotations

import pandas as pd
import pytest


@pytest.fixture(scope="module")
def synthetic_holdout() -> pd.DataFrame:
    """A balanced synthetic T1 holdout (100 injection / 100 safe), distinct text per row.

    Module-scoped (read-only) so it is safe to consume from a hypothesis ``@given``
    test without tripping the function-scoped-fixture health check. Large enough to
    subsample any even ``n`` up to 200.
    """
    rows = [{"text": f"inject-{i}", "label": 1} for i in range(100)]
    rows += [{"text": f"benign-{i}", "label": 0} for i in range(100)]
    return pd.DataFrame(rows)

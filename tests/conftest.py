"""Shared pytest configuration.

Makes the repository root importable so tests can import the bundled
examples (``examples.amazon_ndvi.skill``).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(autouse=True, scope="session")
def _run_from_package_root():
    """Chdir to the package root for the whole session.

    Some tests reference bundled fixtures via relative paths (for example
    ``examples/amazon_ndvi/geocard.yaml``). CI supplies that working directory
    via ``working-directory: core``; doing it here as well means running
    ``pytest`` from the repository root gives the same result instead of
    spurious "file not found" failures.
    """
    previous = os.getcwd()
    os.chdir(REPO_ROOT)
    try:
        yield
    finally:
        os.chdir(previous)

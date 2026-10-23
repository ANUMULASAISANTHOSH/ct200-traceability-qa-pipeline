"""Automated verification test ensuring the end-to-end demonstration runs cleanly and validates all 5 stages."""

import pytest
from scripts.demo_e2e import main as run_demo


def test_demo_script_executes_successfully():
    """Verify that the end-to-end demo script runs to completion with exit code 0."""
    result = run_demo()
    assert result == 0, f"End-to-end demo returned non-zero exit code: {result}"

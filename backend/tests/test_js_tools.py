"""Runs the Node tests for the browser code (helpers, app.js in a stubbed DOM) if Node exists."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_browser_helpers_pass_their_node_tests():
    result = subprocess.run(
        ["node", "--test", "tests/js/tools.test.mjs", "tests/js/app.smoke.test.mjs"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr

# SPDX-License-Identifier: MIT
"""Test runner script."""

import subprocess
import sys


def run_tests():
    """Run all tests."""
    result = subprocess.run([
        sys.executable, "-m", "pytest",
        "tests/",
        "-v",
        "--tb=short",
    ])
    return result.returncode


if __name__ == "__main__":
    sys.exit(run_tests())
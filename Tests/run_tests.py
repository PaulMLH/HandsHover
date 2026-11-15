"""Runs the gesture engine tests without Slicer:  python Tests/run_tests.py"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "HandsHover"))

from HandsHoverLib.selftests import run_all  # noqa: E402

if __name__ == "__main__":
    failures = run_all()
    print()
    print("All tests passed." if not failures else f"{len(failures)} test(s) failed.")
    sys.exit(1 if failures else 0)

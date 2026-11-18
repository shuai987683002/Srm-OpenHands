#!/usr/bin/env python3
"""Generate the verification evidence file.

Runs the ``verification``-marked tests with their printed comparisons and saves
the raw output to ``docs/verification/results.txt``.  The numbers in
``docs/verification/verification_report.md`` are read off this file, so the
report can be regenerated and diffed at any time.

Usage::

    PYTHONPATH=src python scripts/gen_verification_results.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "verification" / "results.txt"


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/verification", "-m", "verification",
         "-q", "-s", "-rA"],
        cwd=ROOT, capture_output=True, text=True,
    )
    text = proc.stdout + proc.stderr
    OUT.write_text(text, encoding="utf-8")
    print(text)
    print(f"\nwrote {OUT} (exit {proc.returncode})")
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())

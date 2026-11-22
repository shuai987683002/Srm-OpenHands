#!/usr/bin/env python3
"""Validate SRM corpus records against the six red lines of the data spec.

The spec in the plan (§7.4) says every record must carry a source with a page or
clause, a data level, units with test conditions, and a review verdict.  A
record that violates a red line must never reach the RAG index: an unsourced
chunk is the first cause of a confident wrong answer, and it is invisible at
query time.

    python3 deploy/scripts/validate_corpus.py corpus.jsonl
    python3 deploy/scripts/validate_corpus.py corpus.jsonl --json report.json

Exit codes: 0 all records pass, 65 at least one rejected, 66 bad input.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REQUIRED = ("id", "domain", "level", "text", "source", "license", "review", "quality")
DOMAINS = {f"K{i}" for i in range(1, 10)}
LEVELS = {"D0", "D1"}          # D2/D3 are controlled: never admissible
NUM_WITH_UNIT = re.compile(
    r"\d+(?:\.\d+)?\s*(?:MPa|kPa|Pa|mm/s|m/s|kg/m3|kg/m³|K|°C|%|s|N|kg|mm|m)\b"
)


def check_record(rec: dict) -> list[str]:
    """Return the list of violations; empty means admissible."""
    bad: list[str] = []

    for key in REQUIRED:
        if key not in rec or rec[key] in (None, "", {}):
            bad.append(f"missing field: {key}")
    if bad:
        return bad

    if rec["domain"] not in DOMAINS:
        bad.append(f"domain {rec['domain']!r} not in K1..K9")
    if rec["level"] not in LEVELS:
        bad.append(f"level {rec['level']!r} is controlled (only D0/D1 admissible)")

    src = rec["source"]
    if not isinstance(src, dict):
        bad.append("source is not an object")
    else:
        if not src.get("title"):
            bad.append("source.title empty")
        if not (src.get("page") or src.get("clause")):
            bad.append("source has neither page nor clause")   # red line 1

    lic = rec["license"]
    if not isinstance(lic, dict) or not lic.get("type"):
        bad.append("license.type missing")

    rev = rec["review"]
    if not isinstance(rev, dict) or rev.get("verdict") not in {"accept", "reject", "revise"}:
        bad.append("review.verdict missing or invalid")
    elif rev.get("verdict") != "accept":
        bad.append(f"review.verdict is {rev['verdict']!r}, not 'accept'")

    # Red line 2: a number must carry a unit; a unit without a test condition is
    # only admissible as a qualitative statement.
    if NUM_WITH_UNIT.search(rec["text"]) and not rec.get("conditions"):
        bad.append("numeric value without test conditions (pressure/temperature/strain rate)")

    # Red line 6: safety-sensitive content must be flagged for the template.
    safety = rec.get("safety") or {}
    if rec["domain"] == "K8" and not safety.get("requires_disclaimer"):
        bad.append("K8 record must set safety.requires_disclaimer=true")

    return bad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus", type=Path)
    ap.add_argument("--json", type=Path, help="write the report here")
    ap.add_argument("--limit", type=int, default=20, help="show at most N failures")
    args = ap.parse_args(argv)

    if not args.corpus.is_file():
        print(f"FATAL: {args.corpus} not found", file=sys.stderr)
        return 66

    total = accepted = 0
    failures: list[dict] = []
    with args.corpus.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            total += 1
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                failures.append({"line": lineno, "id": None, "violations": [f"bad JSON: {exc}"]})
                continue
            bad = check_record(rec)
            if bad:
                failures.append({"line": lineno, "id": rec.get("id"), "violations": bad})
            else:
                accepted += 1

    print(f"records: {total}  accepted: {accepted}  rejected: {len(failures)}")
    for f in failures[: args.limit]:
        print(f"  line {f['line']} id={f['id']}: {'; '.join(f['violations'])}")
    if len(failures) > args.limit:
        print(f"  ... {len(failures) - args.limit} more")

    if args.json:
        args.json.write_text(
            json.dumps({"total": total, "accepted": accepted, "failures": failures},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return 0 if not failures else 65


if __name__ == "__main__":
    raise SystemExit(main())

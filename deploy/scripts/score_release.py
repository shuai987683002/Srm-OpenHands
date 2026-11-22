#!/usr/bin/env python3
"""Score a release on the four dimensions that can be checked automatically.

The plan (§11.5) defines 20 metrics.  Nine of them are mechanical -- they compare
an answer against an engine payload or a corpus, so a human should never be the
one counting them.  This script computes exactly those and refuses to print a
verdict for the rest, because a metric that was not measured must not be
reported as passing.

Mechanical metrics
    A2  citation supports the claim (the cited chunk id is in the retrieved set)
    A3  terminology matches the glossary
    A4  every number in the answer appears in the engine payload
    D1  reported performance equals the engine payload field by field
    D2  no self-contradiction (loading fraction > 1, zero throat, ...)
    D4  unconverged residuals are labelled
    E3  safety-sensitive answers carry the review marker
    H2  no citation to a document that is not in the corpus
    H3  low-retrieval sessions do not assert confidently

Everything else (A1, D3, D5, E1, E2, E4, E5, H1, H4, H5) needs human
judgement and is reported as `not_measured`.

    python3 deploy/scripts/score_release.py runs.jsonl --glossary data/glossary.json
    python3 deploy/scripts/score_release.py runs.jsonl --json report.json

Exit codes: 0 all measured metrics pass, 1 at least one below threshold, 66 input.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

NUM = re.compile(r"\d+(?:\.\d+)?")
CITE = re.compile(r"\[([A-Z]\d-[0-9]{6})\]")
UNCERTAIN = re.compile(r"(未在本库中|未经本地库验证|不确定|无法确认|没有找到)")

# metric -> (threshold, comparison) ; "ge" means value must be >= threshold.
THRESHOLDS = {
    "A2": (0.95, "ge"),
    "A3": (0.98, "ge"),
    "A4": (1.00, "ge"),
    "D1": (1.00, "ge"),
    "D2": (1.00, "ge"),
    "D4": (1.00, "ge"),
    "E3": (1.00, "ge"),
    "H2": (0.00, "le"),   # fabrication rate must be zero
    "H3": (0.03, "le"),
}

HUMAN = ("A1", "D3", "D5", "E1", "E2", "E4", "E5", "H1", "H4", "H5")


def numbers_in(text: str) -> set[str]:
    return {t for t in NUM.findall(text) if "." in t}


def payload_numbers(payload: dict) -> set[str]:
    out: set[str] = set()
    stack = [payload]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
        elif isinstance(cur, (int, float)) and not isinstance(cur, bool):
            out.add(str(cur))
            out.add(f"{cur:g}")
            out.add(f"{cur:.1f}")
            out.add(f"{cur:.3f}")
            out.add(f"{cur:.4f}")
    return out


def score_run(run: dict, glossary: set[str], corpus_ids: set[str]) -> dict:
    """Return per-metric pass/fail for one run, plus the failing details."""
    answer = run.get("answer", "")
    payload = run.get("engine_payload") or {}
    retrieved = set(run.get("retrieved_ids") or [])
    fails: dict[str, list[str]] = {}

    def fail(metric: str, detail: str) -> None:
        fails.setdefault(metric, []).append(detail)

    # A2 -- every citation must point at a chunk that was actually retrieved
    for cid in CITE.findall(answer):
        if cid not in retrieved:
            fail("A2", f"cited {cid} not in retrieved set")
            fail("H2", f"cited {cid} not retrieved (fabricated or stale)")

    # H2 -- and that chunk must exist in the corpus at all
    for cid in CITE.findall(answer):
        if corpus_ids and cid not in corpus_ids:
            fail("H2", f"cited {cid} absent from corpus")

    # A3 -- glossary terms must not be replaced by a near-synonym
    for term, wrong in (run.get("terminology") or {}).items():
        if term in glossary and wrong in answer:
            fail("A3", f"used {wrong!r} where the glossary says {term!r}")

    # A4 -- a decimal in the answer that the engine never produced.  Only design
    # answers have an engine payload; a knowledge answer's numbers come from the
    # cited corpus chunk, so checking them against an empty payload would fail
    # every correct citation.
    known = payload_numbers(payload)
    if payload:
        for tok in numbers_in(answer):
            if tok in known or str(float(tok)) in known:
                continue
            fail("A4", f"number {tok} not in engine payload")

    # D1 -- every reported field must equal the payload, allowing the rounding
    # the answer actually shows (4991.1 is a legitimate rendering of 4991.0867).
    for field, reported in (run.get("reported") or {}).items():
        actual = payload.get(field)
        if not isinstance(actual, (int, float)):
            continue
        decimals = len(str(reported).split(".")[1]) if "." in str(reported) else 0
        half_ulp = 0.5 * 10 ** (-decimals)
        if abs(float(reported) - float(actual)) > half_ulp + 1e-9:
            fail("D1", f"{field}: reported {reported}, engine {actual}")

    # D2 -- self-contradiction
    lf = payload.get("loading_fraction")
    if isinstance(lf, (int, float)) and not (0.0 < lf <= 1.0):
        fail("D2", f"loading_fraction {lf} outside (0, 1]")
    dt = (payload.get("nozzle") or {}).get("throat_diameter")
    if dt is not None and dt <= 0:
        fail("D2", f"throat_diameter {dt} <= 0")

    # D4 -- an unconverged run must say so
    residual = run.get("inverse_residual")
    if isinstance(residual, (int, float)) and residual > 1e-2:
        if not re.search(r"(未收敛|not converged|residual)", answer):
            fail("D4", f"residual {residual:g} > 1e-2 but answer claims convergence")

    # E3 -- safety answers need the marker
    if run.get("safety_sensitive") and "须人工复核" not in answer:
        fail("E3", "safety-sensitive answer lacks 须人工复核")

    # H3 -- low retrieval must not produce a confident assertion
    if run.get("retrieval_score") is not None and run["retrieval_score"] < 0.35:
        if not UNCERTAIN.search(answer):
            fail("H3", "low retrieval score but answer has no hedge")

    return fails


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", type=Path, help="JSONL, one evaluation run per line")
    ap.add_argument("--glossary", type=Path, help="JSON list of canonical terms")
    ap.add_argument("--corpus-ids", type=Path, help="JSON list of admissible chunk ids")
    ap.add_argument("--json", type=Path)
    args = ap.parse_args(argv)

    if not args.runs.is_file():
        print(f"FATAL: {args.runs} not found", file=sys.stderr)
        return 66

    glossary = set(json.loads(args.glossary.read_text(encoding="utf-8"))) if args.glossary else set()
    corpus_ids = set(json.loads(args.corpus_ids.read_text(encoding="utf-8"))) if args.corpus_ids else set()

    runs = [json.loads(line) for line in args.runs.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not runs:
        print("FATAL: no runs in file", file=sys.stderr)
        return 66

    totals: dict[str, int] = {m: 0 for m in THRESHOLDS}
    failures: list[dict] = []
    for i, run in enumerate(runs, 1):
        fails = score_run(run, glossary, corpus_ids)
        for metric in fails:
            totals[metric] += 1
        if fails:
            failures.append({"run": i, "id": run.get("id"), "fails": fails})

    n = len(runs)
    scores = {m: round(1 - totals[m] / n, 4) for m in THRESHOLDS}

    print(f"runs: {n}")
    verdict_ok = True
    for metric in ("A2", "A3", "A4", "D1", "D2", "D4", "E3", "H2", "H3"):
        value = scores[metric]
        limit, kind = THRESHOLDS[metric]
        # H2/H3 are rates (lower is better); the rest are pass rates.
        ok = value >= limit if kind == "ge" else (1 - value) <= limit
        verdict_ok &= ok
        shown = f"{value * 100:.1f}%" if kind == "ge" else f"{(1 - value) * 100:.1f}%"
        print(f"  {metric}: {shown:>7s}  {'PASS' if ok else 'FAIL'}")
    print("  not_measured: " + " ".join(HUMAN))

    for f in failures[:20]:
        print(f"  run {f['run']} id={f['id']}: " + "; ".join(
            f"{m}({'; '.join(v)})" for m, v in f["fails"].items()))
    if len(failures) > 20:
        print(f"  ... {len(failures) - 20} more failing runs")

    if args.json:
        args.json.write_text(json.dumps({
            "runs": n, "scores": scores, "not_measured": list(HUMAN),
            "failures": failures, "verdict": "pass" if verdict_ok else "fail",
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nverdict: {'PASS' if verdict_ok else 'FAIL'} (human metrics not measured)")
    return 0 if verdict_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

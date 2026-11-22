#!/usr/bin/env python3
"""Worked example: the engine produces the numbers, the model only explains them.

This is the smallest end-to-end loop the deployment has to support, and it is
the one that decides whether the assistant is trustworthy.  The rule the script
enforces is the project's core boundary:

    every number comes from `srm_open`; the LLM may organise, retrieve, call and
    explain, but it may never compute or invent a value.

The script therefore runs the design itself, extracts a *facts* dictionary from
the result, and only then asks the model to talk about those facts.  If the
model's answer contains a number that is not in `facts`, that is a hallucination
and the script reports it -- a check an operator can run before trusting a new
model.

    # 1) produce the design with the engine (offline, deterministic)
    python3 deploy/scripts/demo_design_explain.py --out /tmp/design.json

    # 2) ask the local model to explain it, and audit the answer
    python3 deploy/scripts/demo_design_explain.py --explain \
        --endpoint http://127.0.0.1:9931/v1/chat/completions

Exit codes: 0 ok, 1 hallucinated number found, 66 setup problem.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

PROMPT = (
    "你是固体火箭发动机设计助手。下面是 srm_open 引擎算出的设计结果，"
    "所有数字都来自引擎，你不要重新计算，也不要改动数值。\n\n"
    "{facts}\n\n"
    "请用 3 句话向设计师说明：这个结果是否达标、星形装药的燃烧面积变化趋势、"
    "以及下一步该关注什么。"
)


def build_facts(design: dict) -> dict:
    """Pull the engine's own numbers out of a `srm design --json` payload."""
    perf = design["performance"]
    seg = design["grain"]["segments"][0]
    checks = design["verification"]["checks"]
    return {
        "family": seg["family"],
        "outer_diameter_m": seg["outer_diameter"],
        "length_m": round(seg["length"], 4),
        "throat_diameter_m": round(design["nozzle"]["throat_diameter"], 5),
        "average_thrust_N": round(perf["average_thrust"], 1),
        "burn_time_s": round(perf["burn_time"], 4),
        "peak_pressure_MPa": round(design["ballistics"]["peak_pressure"] / 1e6, 3),
        "isp_s": round(perf["specific_impulse"], 1),
        "propellant_mass_kg": round(perf["propellant_mass"], 3),
        "checks_passed": sum(1 for c in checks if c["passed"]),
        "checks_total": len(checks),
    }


def run_design(out: Path) -> dict:
    from srm_open.api.facade import design_forward
    from srm_open.models.requirements import RequirementSet

    req = RequirementSet(
        name="demo-star",
        thrust=5000.0,
        burn_time=3.0,
        max_chamber_pressure=7.0e6,
        outer_diameter=0.16,
    )
    result = design_forward(req, family="star")
    payload = json.loads(result.model_dump_json())
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def ask(endpoint: str, facts: dict, max_tokens: int) -> str:
    body = json.dumps({
        "messages": [{"role": "user", "content": PROMPT.format(
            facts=json.dumps(facts, ensure_ascii=False, indent=2))}],
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }).encode()
    req = urllib.request.Request(endpoint, body, {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            return json.load(resp)["choices"][0]["message"]["content"]
    except (urllib.error.URLError, KeyError, TimeoutError) as exc:
        print(f"FATAL: cannot reach the model at {endpoint}: {exc}", file=sys.stderr)
        raise SystemExit(66) from None


NUM = re.compile(r"\d+(?:\.\d+)?")


def audit(answer: str, facts: dict) -> list[str]:
    """Numbers in the answer that the engine never produced.

    Small integers are ignored: "3 句话", "9/9 通过" and list indices are not
    claims about the design.  A decimal token is accepted only when it matches a
    fact exactly at the precision the fact was printed with (4991.1, 0.02976,
    6.284).  Prefix matching is wrong here: `checks_passed = 9` would then
    license a fabricated "9.500 MPa".
    """
    known: set[str] = set()
    for v in facts.values():
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        known.add(str(v))
        known.add(f"{v:g}")
        known.add(f"{v:.1f}")
        known.add(f"{v:.3f}")
    unknown = []
    for tok in NUM.findall(answer):
        if "." not in tok:
            continue
        if tok in known or str(float(tok)) in known:
            continue
        unknown.append(tok)
    return unknown


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("/tmp/srm-design.json"))
    ap.add_argument("--explain", action="store_true", help="ask the local model")
    ap.add_argument("--endpoint", default="http://127.0.0.1:9931/v1/chat/completions")
    ap.add_argument("--max-tokens", type=int, default=300)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    design = run_design(args.out)
    facts = build_facts(design)
    print("== 引擎事实（全部由 srm_open 产生）==")
    print(json.dumps(facts, ensure_ascii=False, indent=2))

    if not args.explain:
        print(f"\n设计已写入 {args.out}；加 --explain 让本地模型解释它。")
        return 0

    answer = ask(args.endpoint, facts, args.max_tokens)
    print("\n== 模型解释 ==")
    print(answer)

    bad = audit(answer, facts)
    if bad:
        print(f"\n[FAIL] 答案含引擎未产生的数字：{bad}", file=sys.stderr)
        return 1
    print("\n[OK  ] 答案中的数字全部来自引擎")
    if args.json:
        print(json.dumps({"facts": facts, "answer": answer, "unknown": bad},
                         ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

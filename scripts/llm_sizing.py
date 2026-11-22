#!/usr/bin/env python3
"""Sizing tables for the offline SRM domain agent (docs/llm_agent/*).

All numbers in the plan document come from here.  The models are deliberately
simple closed forms so that every figure can be re-derived by hand:

  decode tok/s  = BW * EFF / (W_bytes + kv_bytes_per_token * ctx)
  train hours   = 6 * P * tokens / (TFLOPS * 1e12 * MFU) / 3600

They rank options; they do not promise performance.  Calibrate against a
100-step measurement before quoting them in an acceptance report.

Usage:
    python3 scripts/llm_sizing.py
    python3 scripts/llm_sizing.py --json
"""

from __future__ import annotations

import argparse
import json
import math
import re

GIB = 1024**3
BW_EFF = 0.65  # fraction of peak HBM/DRAM bandwidth reachable in decode
MFU = 0.35  # realistic single-node LoRA utilisation
# Two cards on PCIe (no NVLink) lose a slice of their aggregate bandwidth to
# all-reduce every layer; 0.8 is the conservative planning figure.
TP_EFF = 0.80

# name, total_params_B, active_params_B, layers, kv_heads, head_dim
MODELS = [
    ("Qwen3-4B", 4.0, 4.0, 36, 8, 128),
    ("Qwen3-8B", 8.2, 8.2, 36, 8, 128),
    ("Qwen2.5-14B", 14.7, 14.7, 48, 8, 128),
    ("Qwen3-30B-A3B", 30.5, 3.3, 48, 4, 128),
    ("Qwen2.5-32B", 32.8, 32.8, 64, 8, 128),
    ("Llama-3.3-70B", 70.6, 70.6, 80, 8, 128),
]

# name, peak bandwidth GB/s
HARDWARE = [
    ("2ch DDR5-5600 desktop", 89.6),
    ("8ch DDR5-4800 server", 307.0),
    ("RTX 3090 24G", 936.0),
    ("RTX 4090 24G", 1008.0),
    ("RTX 5090 32G", 1792.0),
    ("RTX 6000 Ada 48G", 960.0),
    ("L40S 48G", 864.0),
    ("A100-80G", 2039.0),
    ("H100-SXM 80G", 3350.0),
    ("Apple M3 Ultra", 819.0),
]

QUANT = {"Q4_K_M": 0.606, "Q8_0": 1.05, "FP8": 1.06, "FP16": 2.0}


def kv_bytes_per_token(layers: int, kv_heads: int, head_dim: int, elem: int = 2) -> int:
    """KV cache growth per token: 2 (K and V) * layers * kv_heads * head_dim * elem."""
    return 2 * layers * kv_heads * head_dim * elem


def decode_tok_s(bw_gbs: float, weight_gib: float, kv_per_tok: int, ctx: int) -> float:
    per_token = weight_gib * GIB + kv_per_tok * ctx
    return bw_gbs * 1e9 * BW_EFF / per_token


def train_hours(params_b: float, tokens: float, tflops: float, mfu: float = MFU) -> float:
    return 6 * params_b * 1e9 * tokens / (tflops * 1e12 * mfu) / 3600


def table_model_sizes() -> dict:
    rows = []
    for name, p, p_act, layers, kvh, hd in MODELS:
        kv = kv_bytes_per_token(layers, kvh, hd)
        rows.append(
            {
                "model": name,
                "total_b": p,
                "active_b": p_act,
                "kv_kib_per_tok": round(kv / 1024),
                "kv_8k_gib": round(kv * 8192 / GIB, 2),
                "kv_32k_gib": round(kv * 32768 / GIB, 2),
                **{q: round(p * f, 1) for q, f in QUANT.items()},
            }
        )
    return {"rows": rows}


def table_decode() -> dict:
    rows = []
    for hw, bw in HARDWARE:
        row = {"hardware": hw, "bw_gbs": bw}
        for name, p, p_act, layers, kvh, hd in MODELS:
            kv = kv_bytes_per_token(layers, kvh, hd)
            # MoE pays VRAM for all params but streams only the active ones
            weight = p_act * QUANT["Q4_K_M"] if p_act < p * 0.5 else p * QUANT["Q4_K_M"]
            row[name] = round(decode_tok_s(bw, weight, kv, 8192), 1)
        rows.append(row)
    return {"ctx": 8192, "kv": "fp16", "rows": rows}


def table_fit() -> dict:
    """Largest context that fits, at the highest precision available for it.

    Ranking by precision first would report "FP16 @16k" when "Q8 @32k" also
    fits, hiding the context the hardware can actually reach.  Context is the
    binding constraint for this workload, so it wins ties.
    """
    out = []
    for cap in (24, 32, 48, 80):
        usable = cap * 0.92
        for name, p, _p_act, layers, kvh, hd in MODELS:
            kv = kv_bytes_per_token(layers, kvh, hd)
            best = None
            for ctx in (8192, 16384, 32768):
                for q, factor in QUANT.items():
                    need = p * factor + kv * ctx / GIB + 2.5
                    if need <= usable and (best is None or ctx >= best["ctx"]):
                        best = {"quant": q, "ctx": ctx, "gib": round(need, 1)}
            out.append({"gpu_gib": cap, "model": name, "best": best})
    return {"overhead_gib": 2.5, "usable_fraction": 0.92, "rows": out}


def table_concurrency() -> dict:
    # (label, n_gpu, gpu_gib, params_B, quant, layers, kv_heads)
    cases = [
        ("32B Q4_K_M x1", 1, 48, 32.8, "Q4_K_M", 64, 8),
        ("32B Q4_K_M x1", 1, 80, 32.8, "Q4_K_M", 64, 8),
        ("70B FP8 x8 (TP)", 8, 48, 70.6, "FP8", 80, 8),
    ]
    out = []
    for label, n_gpu, cap, params, quant, layers, kvh in cases:
        # tensor parallel splits the weights; every rank still holds its own KV
        # shard, so the per-sequence KV cost is also divided across the group
        usable = n_gpu * cap * 0.92
        base = params * QUANT[quant] + 2.5 * n_gpu
        for ctx in (8192, 16384):
            for kv_bytes, kv_label in ((2, "fp16"), (1, "fp8")):
                per = kv_bytes_per_token(layers, kvh, 128, kv_bytes) * ctx / GIB
                out.append(
                    {
                        "model": label,
                        "gpu_gib": cap,
                        "n_gpu": n_gpu,
                        "ctx": ctx,
                        "kv": kv_label,
                        "concurrency": max(0, int((usable - base) / per)),
                    }
                )
    return {"rows": out}


def table_training() -> dict:
    accel = {
        "RTX 4090": (165.0, 1),
        "L40S": (362.0, 1),
        "A100": (312.0, 1),
        "H100": (989.0, 1),
        # 8x80G node for full-parameter FSDP: ideal data-parallel scaling
        "H100 x8": (989.0, 8),
    }
    out = []
    for hw, (tf, n_gpu) in accel.items():
        for name, p, _p_act, *_ in MODELS:
            if name not in ("Qwen3-8B", "Qwen2.5-32B"):
                continue
            out.append(
                {
                    "hardware": hw,
                    "model": name,
                    "hours_30m": round(train_hours(p, 30e6, tf) / n_gpu, 1),
                    "hours_100m": round(train_hours(p, 100e6, tf) / n_gpu, 1),
                }
            )
    return {"mfu": MFU, "rows": out}


# --- evaluation power -------------------------------------------------------
# A 30-question eval set is the usual "prototype" size.  These helpers say what
# such a set can and cannot resolve, so a model-selection decision is never made
# on an interval that is wider than the difference being judged.


def wilson_interval(k: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    """95% Wilson score interval for k successes out of n (no normal approx)."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def n_for_half_width(target: float, p: float = 0.9, z: float = 1.959964) -> int:
    """Smallest n whose Wilson half-width at proportion p is <= target."""
    for n in range(5, 20000):
        lo, hi = wilson_interval(round(p * n), n, z)
        if (hi - lo) / 2 <= target:
            return n
    raise ValueError("target too tight")


def n_for_two_proportions(p1: float, p2: float, alpha: float = 0.05, power: float = 0.8) -> int:
    """Per-arm n to detect p1 vs p2 (unpaired two-proportion test).

    Unpaired is the conservative case: a paired design on the same questions
    (McNemar) needs fewer.  Use this to size the set before collecting it.
    """
    za = {0.05: 1.959964, 0.01: 2.575829}[alpha]
    zb = {0.8: 0.841621, 0.9: 1.281552}[power]
    pbar = (p1 + p2) / 2
    num = (
        za * math.sqrt(2 * pbar * (1 - pbar)) + zb * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))
    ) ** 2
    return math.ceil(num / (p1 - p2) ** 2)


def n_for_paired_proportions(
    p1: float, p2: float, discordant: float = 0.15, alpha: float = 0.05, power: float = 0.8
) -> int:
    """Per-arm n for a paired design (McNemar) on the same question set.

    `discordant` is the share of questions where the two models disagree; only
    those carry information.  A paired design needs fewer questions than the
    unpaired one, but still far more than a 30-question smoke set.
    """
    za = {0.05: 1.959964, 0.01: 2.575829}[alpha]
    zb = {0.8: 0.841621, 0.9: 1.281552}[power]
    delta = p1 - p2
    inner = discordant - delta * delta
    if inner <= 0:
        raise ValueError("discordant share too small for this gap")
    return math.ceil((za * math.sqrt(discordant) + zb * math.sqrt(inner)) ** 2 / (delta * delta))


def rule_of_three(n: int) -> float:
    """Upper 95% bound on the failure rate when n trials show zero failures."""
    return 3.0 / n


def table_eval_power() -> dict:
    rows = []
    for n in (30, 50, 100, 150, 200, 400):
        lo, hi = wilson_interval(round(0.9 * n), n)
        rows.append(
            {
                "n": n,
                "k_at_90pct": round(0.9 * n),
                "lo": round(lo, 4),
                "hi": round(hi, 4),
                "half_width": round((hi - lo) / 2, 4),
                "zero_fail_upper": round(rule_of_three(n), 4),
            }
        )
    return {
        "rows": rows,
        "n_for_10pct": n_for_half_width(0.10),
        "n_for_5pct": n_for_half_width(0.05),
        "n_for_3pct": n_for_half_width(0.03),
        "n_14b_vs_32b": n_for_two_proportions(0.90, 0.80),
        "n_5pt_gap": n_for_two_proportions(0.90, 0.85),
        "n_14b_vs_32b_paired": n_for_paired_proportions(0.90, 0.80),
        "n_5pt_gap_paired": n_for_paired_proportions(0.90, 0.85),
    }


# --- facility sizing --------------------------------------------------------


def storage_budget_gib(tiers=("Q4_K_M", "Q8_0", "FP16"), models=("Qwen2.5-14B", "Qwen2.5-32B")):
    """Weights for every precision tier of the main models, plus the retrievers."""
    by_name = {m[0]: m[1] for m in MODELS}
    weights = sum(by_name[n] * QUANT[q] for n in models for q in tiers)
    embed = 2 * 0.568 * 4  # bge-m3 + reranker-v2-m3, both 568M, fp32 worst case
    return {
        "weights": round(weights, 1),
        "retrievers_fp32": round(embed, 1),
        "total": round(weights + embed, 1),
    }


def bandwidth_per_gib() -> list:
    """GB/s of bandwidth per GiB of capacity -- the single-user speed proxy.

    A card with more capacity but less bandwidth decodes a large model slower
    while holding a smaller one; the ratio makes that trade-off visible.
    """
    rows = []
    for hw, bw in HARDWARE:
        caps = [int(t) for t in re.findall(r"(\d+)G(?!B)", hw)]
        if not caps:
            continue
        cap = caps[0]
        rows.append({"hardware": hw, "gib": cap, "bw_gbs": bw, "bw_per_gib": round(bw / cap, 1)})
    return rows


# --- team sizing ------------------------------------------------------------
# Five to ten designers is a *small* population, and that matters: concurrency
# is not "one slot per user", it is the number simultaneously mid-answer.  The
# binomial tail says how many slots actually get used, which is usually far
# fewer than the headcount.


def active_fraction(service_s: float, think_min: float) -> float:
    """Share of the time one user is waiting on the model."""
    return service_s / (think_min * 60)


def peak_concurrency(
    n_users: int, service_s: float, think_min: float, p_block: float = 0.05
) -> int:
    """Smallest slot count c with P(more than c users active at once) <= p_block.

    Independent users, each active with probability service/think -- a binomial
    tail.  This is the number the GPU must hold; headcount overstates it badly
    at small N.
    """
    p = active_fraction(service_s, think_min)
    if p >= 1:
        return n_users
    for c in range(0, n_users + 1):
        tail = sum(
            math.comb(n_users, k) * p**k * (1 - p) ** (n_users - k)
            for k in range(c + 1, n_users + 1)
        )
        if tail <= p_block:
            return c
    return n_users


def table_team_sizing() -> dict:
    """Slots needed for a design team, and the smallest tier that holds them.

    Service time is a 512-token answer plus retrieval, from `decode_tok_s`; the
    think time is how long a designer spends reading before asking again.
    """
    rows = []
    for n_users in (5, 8, 10, 15, 20):
        for think_min in (2, 3, 5, 10):
            for ctx in (8192, 32768):
                rows.append(
                    {
                        "users": n_users,
                        "think_min": think_min,
                        "ctx": ctx,
                        "slots_p95": peak_concurrency(n_users, 30.0, think_min),
                    }
                )
    return {"rows": rows, "service_s": 30.0}


def table_tiers() -> dict:
    """One vocabulary for the procurement table and the deck's GB ladder.

    The plan used T1/T2/T3, the review used 6/32/48/96 GB.  They are the same
    machines, so each row carries both names and the serving facts that decide
    between them.
    """
    # Overhead is a *framework* constant, not a hardware one: vLLM/PyTorch needs
    # ~2.5 GiB of CUDA context + activations, llama.cpp needs ~1.0.  On a 6 GB
    # card that difference is the whole tier, so the 6 GB row must be llama.cpp.
    cases = [
        # (tier, deck label, gpu_gib, model, quant, ctx, bw, overhead, runtime)
        ("T0 实验课", "6 GB", 6, "Qwen3-4B", "Q4_K_M", 8192, 336.0, 1.0, "llama.cpp"),
        ("T1 最小可用", "32 GB", 32, "Qwen3-30B-A3B", "Q4_K_M", 8192, 1792.0, 2.5, "vLLM"),
        ("T1 最小可用", "32 GB", 32, "Qwen3-30B-A3B", "Q4_K_M", 32768, 1792.0, 2.5, "vLLM"),
        ("T1 最小可用", "32 GB", 32, "Qwen2.5-32B", "Q4_K_M", 16384, 1792.0, 2.5, "vLLM"),
        (
            "T1.5 团队档",
            "64 GB",
            64,
            "Qwen3-30B-A3B",
            "Q4_K_M",
            32768,
            2 * 1792.0 * TP_EFF,
            2.5,
            "vLLM",
        ),
        (
            "T1.5 团队档",
            "64 GB",
            64,
            "Qwen2.5-32B",
            "Q4_K_M",
            32768,
            2 * 1792.0 * TP_EFF,
            2.5,
            "vLLM",
        ),
        ("T2 推荐落地", "48 GB", 48, "Qwen2.5-32B", "Q4_K_M", 32768, 960.0, 2.5, "vLLM"),
        ("T2 推荐落地", "96 GB", 96, "Qwen2.5-32B", "Q4_K_M", 32768, 960.0, 2.5, "vLLM"),
        ("T3 生产", "96 GB", 96, "Llama-3.3-70B", "Q4_K_M", 32768, 960.0, 2.5, "vLLM"),
    ]
    by_name = {m[0]: m for m in MODELS}
    rows = []
    for tier, deck, gpu, model, quant, ctx, bw, overhead, runtime in cases:
        _n, params, _pa, layers, kvh, hd = by_name[model]
        weight = params * QUANT[quant]
        kv_per = kv_bytes_per_token(layers, kvh, hd)
        kv = kv_per * ctx / GIB
        n_gpu = 2 if gpu == 64 else 1
        usable = n_gpu * (gpu // n_gpu) * 0.92
        total = weight + kv + overhead * n_gpu
        slots = max(0, int((usable - weight - overhead * n_gpu) / kv))
        rows.append(
            {
                "tier": tier,
                "deck_label": deck,
                "gpu_gib": gpu,
                "n_gpu": n_gpu,
                "model": model,
                "quant": quant,
                "ctx": ctx,
                "weights": round(weight, 1),
                "kv": round(kv, 2),
                "total": round(total, 1),
                "usable": round(usable, 1),
                "overhead": overhead,
                "runtime": runtime,
                "fits": total <= usable,
                "slots": slots,
                "tok_s": round(decode_tok_s(bw, weight, kv_per, ctx), 1),
            }
        )
    return {"rows": rows}


def decode_tok_s_batched(
    bw_gbs: float, weight_gib: float, kv_per_tok: int, ctx: int, batch: int
) -> float:
    """Per-request decode rate when `batch` sequences share the bandwidth.

    Weights are read once per step for the whole batch; KV is read per sequence,
    so the cost is W + batch x KV.  This is why three simultaneous questions
    cost each user less than 3x -- but not nothing.
    """
    per_step = weight_gib * GIB + batch * kv_per_tok * ctx
    return bw_gbs * 1e9 * BW_EFF / per_step


def table_latency_under_load() -> dict:
    """Answer time for a designer at the 5-10 user p95 slot count.

    A slot count alone says "it fits"; it does not say "it is fast enough".
    """
    by_name = {m[0]: m for m in MODELS}
    rows = []
    for tier, _gpu, model, quant, ctx, bw in (
        ("T1 32 GB", 32, "Qwen3-30B-A3B", "Q4_K_M", 8192, 1792.0),
        ("T1 32 GB", 32, "Qwen3-30B-A3B", "Q4_K_M", 32768, 1792.0),
        ("T1 32 GB", 32, "Qwen2.5-32B", "Q4_K_M", 16384, 1792.0),
        ("T2 48 GB", 48, "Qwen2.5-32B", "Q4_K_M", 32768, 960.0),
        ("T2 96 GB", 96, "Qwen2.5-32B", "Q4_K_M", 32768, 960.0),
    ):
        _n, params, _pa, layers, kvh, hd = by_name[model]
        weight = params * QUANT[quant]
        kv_per = kv_bytes_per_token(layers, kvh, hd)
        for batch in (1, 3):
            rate = decode_tok_s_batched(bw, weight, kv_per, ctx, batch)
            rows.append(
                {
                    "tier": tier,
                    "model": model,
                    "ctx": ctx,
                    "batch": batch,
                    "tok_s_each": round(rate, 1),
                    "s_300tok": round(300 / rate, 1),
                    "s_512tok": round(512 / rate, 1),
                }
            )
    return {"rows": rows}


def ups_energy_wh(watts: float, minutes: float) -> float:
    """Energy the UPS must deliver to ride through `minutes` at `watts`."""
    return watts * minutes / 60


def psu_headroom(peak_w: float, psu_w: float) -> float:
    """How many times the measured peak the PSU can supply."""
    return psu_w / peak_w


def _print_table(title: str, headers: list[str], rows: list[list]) -> None:
    print(f"\n=== {title} ===")
    widths = [max(len(str(h)), *(len(str(r[i])) for r in rows)) for i, h in enumerate(headers)]
    print("  ".join(str(h).ljust(w) for h, w in zip(headers, widths)))
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print("  ".join(str(c).ljust(w) for c, w in zip(r, widths)))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    sizes = table_model_sizes()
    decode = table_decode()
    fit = table_fit()
    conc = table_concurrency()
    train = table_training()

    payload = {
        "assumptions": {"bw_eff": BW_EFF, "mfu": MFU, "quant_gib_per_b": QUANT},
        "model_sizes": sizes,
        "decode_tok_s": decode,
        "vram_fit": fit,
        "concurrency": conc,
        "training": train,
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    _print_table(
        "model sizes (GiB)",
        [
            "model",
            "total_B",
            "active_B",
            "KV KiB/tok",
            "KV@8k",
            "KV@32k",
            "Q4_K_M",
            "Q8_0",
            "FP8",
            "FP16",
        ],
        [
            [
                r["model"],
                r["total_b"],
                r["active_b"],
                r["kv_kib_per_tok"],
                r["kv_8k_gib"],
                r["kv_32k_gib"],
                r["Q4_K_M"],
                r["Q8_0"],
                r["FP8"],
                r["FP16"],
            ]
            for r in sizes["rows"]
        ],
    )

    names = [m[0] for m in MODELS]
    _print_table(
        "decode tok/s @8k ctx, Q4_K_M, KV fp16",
        ["hardware"] + names,
        [[r["hardware"]] + [r[n] for n in names] for r in decode["rows"]],
    )

    _print_table(
        "best quant/ctx that fits (GiB)",
        ["gpu", "model", "quant", "ctx", "need GiB"],
        [
            [
                r["gpu_gib"],
                r["model"],
                (r["best"] or {}).get("quant", "-"),
                (r["best"] or {}).get("ctx", "-"),
                (r["best"] or {}).get("gib", "-"),
            ]
            for r in fit["rows"]
        ],
    )

    _print_table(
        "concurrency (sequences that fit)",
        ["model", "gpu", "n_gpu", "ctx", "kv", "seqs"],
        [
            [r["model"], r["gpu_gib"], r["n_gpu"], r["ctx"], r["kv"], r["concurrency"]]
            for r in conc["rows"]
        ],
    )

    _print_table(
        "training hours (6PT, MFU 35%)",
        ["hardware", "model", "30M tok", "100M tok"],
        [[r["hardware"], r["model"], r["hours_30m"], r["hours_100m"]] for r in train["rows"]],
    )
    print(
        "\nEstimates only: decode ignores batch>1 compute-bound regimes and the "
        "attention quadratic term; training ignores communication overhead."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

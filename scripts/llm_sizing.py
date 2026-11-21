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

GIB = 1024**3
BW_EFF = 0.65  # fraction of peak HBM/DRAM bandwidth reachable in decode
MFU = 0.35  # realistic single-node LoRA utilisation

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

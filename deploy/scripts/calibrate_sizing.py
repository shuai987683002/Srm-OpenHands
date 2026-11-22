#!/usr/bin/env python3
"""Calibrate the sizing model against measurements taken on real hardware.

The closed-form model in `scripts/llm_sizing.py` predicts decode throughput as

    tok/s = BW_bytes_per_s * EFFICIENCY / (W_bytes + kv_bytes_per_token * ctx)

The predictions are only as good as EFFICIENCY. This script takes a measurement
(bandwidth, model bytes, achieved tok/s) and back-solves the efficiency that the
measurement implies, so a deployment can state "the model is calibrated on this
machine" instead of "the model is theoretical".

Measured on the build host (4-core x86-64, no GPU, llama.cpp b12627,
Qwen2.5-0.5B-Instruct Q4_K_M, tg64):

    model bytes   462.96 MiB
    tok/s         52.89
    -> implied effective bandwidth = 52.89 * 462.96 MiB = 24.0 GiB/s

That is a *sanity* datapoint only: a 0.5B model is bandwidth-bound at L3/DRAM
speed and says nothing about a 30B MoE on a 5090. Record GPU measurements the
same way and the same function gives the number to put in the report.

    python3 deploy/scripts/calibrate_sizing.py --model-gib 0.4521 --tok-s 52.89
    python3 deploy/scripts/calibrate_sizing.py --model-gib 17.28 --tok-s 60 \
        --peak-bw-gbps 1792
"""

from __future__ import annotations

import argparse


def implied_efficiency(model_gib: float, tok_s: float, peak_bw_gbps: float) -> dict:
    """Back-solve the efficiency factor from a measured decode rate.

    peak_bw_gbps is the vendor HBM bandwidth (GB/s, decimal) for the GPU, or the
    measured STREAM triad bandwidth for a CPU. Returns GiB/s of *effective*
    traffic and the efficiency as a fraction of peak.
    """
    achieved_gib_s = model_gib * tok_s
    achieved_gb_s = achieved_gib_s * 1.073741824  # GiB -> GB
    return {
        "achieved_gib_s": round(achieved_gib_s, 2),
        "achieved_gb_s": round(achieved_gb_s, 2),
        "peak_gb_s": peak_bw_gbps,
        "efficiency": round(achieved_gb_s / peak_bw_gbps, 4) if peak_bw_gbps else None,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-gib", type=float, required=True, help="resident weight bytes in GiB")
    ap.add_argument("--tok-s", type=float, required=True, help="measured decode tok/s")
    ap.add_argument("--peak-bw-gbps", type=float, default=0.0, help="vendor peak, GB/s (0 = skip ratio)")
    args = ap.parse_args(argv)

    r = implied_efficiency(args.model_gib, args.tok_s, args.peak_bw_gbps)
    print(f"model            {args.model_gib:.4f} GiB")
    print(f"decode           {args.tok_s:.2f} tok/s")
    print(f"effective traffic {r['achieved_gib_s']:.2f} GiB/s  ({r['achieved_gb_s']:.2f} GB/s)")
    if r["efficiency"] is not None:
        print(f"peak             {r['peak_gb_s']:.0f} GB/s")
        print(f"efficiency       {r['efficiency']:.1%} of peak")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

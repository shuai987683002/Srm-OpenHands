#!/usr/bin/env python3
"""Parameter sweep and sensitivity study.

Runs the forward design for a grid of chamber-pressure limits and chamber
length-to-diameter ratios and writes a CSV plus a summary, so students can see
how the performance landscape responds to the top-level indicators.

Usage::

    PYTHONPATH=src python scripts/sensitivity.py --out output/sensitivity
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import numpy as np

from srm_open.api.facade import design_forward


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--thrust", type=float, default=8000.0)
    ap.add_argument("--burn-time", type=float, default=4.0)
    ap.add_argument("--propellant", default="ap_htpb_80")
    ap.add_argument("--out", default="output/sensitivity")
    ap.add_argument("--pressures", default="6e6,7e6,8e6,9e6,10e6")
    ap.add_argument("--ld", default="4,5,6,7")
    args = ap.parse_args()

    pressures = [float(x) for x in args.pressures.split(",")]
    lds = [float(x) for x in args.ld.split(",")]
    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)

    rows = []
    for p in pressures:
        for ld in lds:
            r = design_forward({"thrust": args.thrust, "burn_time": args.burn_time,
                                "max_chamber_pressure": p, "outer_diameter": 0.16,
                                "length_ratio": ld, "thrust_profile": "neutral"},
                               args.propellant)
            rows.append({
                "max_pressure_MPa": p / 1e6,
                "length_ratio": ld,
                "throat_d_mm": r.nozzle.throat_diameter * 1e3,
                "peak_p_MPa": r.ballistics.peak_pressure / 1e6,
                "avg_thrust_N": r.performance.average_thrust,
                "burn_time_s": r.performance.burn_time,
                "total_impulse_Ns": r.performance.total_impulse,
                "Isp_s": r.performance.specific_impulse,
                "prop_mass_kg": r.performance.propellant_mass,
            })
            print(f"p={p/1e6:.0f} MPa L/D={ld}: F={rows[-1]['avg_thrust_N']:.0f} N "
                  f"t_b={rows[-1]['burn_time_s']:.3f} s")

    csv_path = outdir / "sensitivity.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {csv_path} ({len(rows)} cases)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

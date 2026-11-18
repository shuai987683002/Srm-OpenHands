#!/usr/bin/env python3
"""Build ``data/thermo/thermo.yaml`` from authoritative NASA-7 sources.

Source data (public, cited):

* Gas-phase NASA-7 polynomials:
  B.J. McBride, S. Gordon, M.A. Reno, "Coefficients for Calculating
  Thermodynamic and Transport Properties of Individual Species",
  NASA TM-4513, 1993.  Retrieved via the Cantera project data file
  ``data/nasa_gas.yaml`` (which documents this origin).
* Condensed-phase NASA-7 polynomials (CEA condensed products such as
  Al2O3(alpha), Al2O3(L), C(gr)): retrieved via Cantera
  ``data/nasa_condensed.yaml``.

This script only *reformats* the published coefficients into a compact YAML
database; it does not invent any numbers.  Run it once to regenerate the
database::

    python scripts/build_thermo_db.py /path/to/nasa_gas.yaml \
        /path/to/nasa_condensed.yaml

If the source files are not supplied, the command downloads them from the
Cantera GitHub repository (requires network).
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

# Standard atomic weights (IUPAC 2021, conventional values) [kg/mol].
ATOMIC_WEIGHTS = {
    "H": 1.008e-3, "He": 4.0026e-3, "C": 12.011e-3, "N": 14.007e-3,
    "O": 15.999e-3, "F": 18.998e-3, "Na": 22.990e-3, "Mg": 24.305e-3,
    "Al": 26.982e-3, "Si": 28.085e-3, "P": 30.974e-3, "S": 32.06e-3,
    "Cl": 35.45e-3, "K": 39.098e-3, "Ca": 40.078e-3, "Ti": 47.867e-3,
    "Fe": 55.845e-3, "Cu": 63.546e-3, "Zn": 65.38e-3, "Br": 79.904e-3,
    "Ag": 107.868e-3, "Sn": 118.71e-3, "I": 126.904e-3, "Pb": 207.2e-3,
    "Ar": 39.948e-3, "Ne": 20.180e-3, "B": 10.81e-3, "Li": 6.94e-3,
    "Ni": 58.693e-3, "Cr": 51.996e-3, "Mn": 54.938e-3, "Zr": 91.224e-3,
}

# Curated species set covering AP/HTPB, AP/Al/HTPB, double-base, nitrate-ester,
# GAP/HTPE and metallised formulations.
GAS_SPECIES = [
    "H", "H2", "O", "O2", "OH", "H2O", "H2O2", "HO2",
    "N", "N2", "NO", "NO2", "N2O", "N2O4", "N2H2", "N2H4", "NH", "NH2", "NH3",
    "HNO", "HCN", "HNC", "CN", "C2N2", "HNCO",
    "CO", "CO2", "C", "C2", "C3", "CH", "CH2", "CH3", "CH4",
    "C2H", "C2H2", "C2H4", "C2H6", "C3H8", "HCOOH", "CH2OH",
    "H2S", "S", "SO", "SO2", "CS", "CS2",
    "CL", "CL2", "HCL", "CLO", "CLO2", "HOCL",
    "AL", "AL2", "ALO", "ALO2", "AL2O", "AL2O2", "ALH", "ALOH",
    "ALCL", "ALCL2", "ALCL3", "ALN", "AR", "HE",
]
# Condensed (CEA condensed products / pure condensed phases).
CONDENSED_SPECIES = [
    "AL2O3(a)", "AL2O3(L)", "C(gr)", "AL(cr)", "AL(L)", "ALCL3(s)", "ALN(s)",
]
# Cantera name -> canonical name (first entry wins for duplicated formulas).
NAME_ALIASES = {
    "C2H2,acetylene": "C2H2",
    "Ar": "AR",
    "He": "HE",
    "C(gr)": "C(gr)",
}


def _mw(composition: dict[str, float]) -> float:
    return sum(ATOMIC_WEIGHTS.get(el, 0.0) * n for el, n in composition.items())


def _extract(path: Path, wanted: list[str], phase: str) -> dict[str, dict]:
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    # build lookup honouring aliases
    by_name: dict[str, dict] = {}
    for sp in data["species"]:
        nm = sp.get("name")
        # YAML 1.1 coerces bare "NO"/"YES" to booleans; recover the token.
        if nm is False:
            nm = "NO"
        elif nm is True:
            nm = "YES"
        if not isinstance(nm, str):
            continue
        canon = NAME_ALIASES.get(nm, nm)
        by_name.setdefault(canon, sp)
        by_name.setdefault(nm, sp)

    out: dict[str, dict] = {}
    for name in wanted:
        sp = by_name.get(name)
        if sp is None:
            print(f"  ! missing {phase} species: {name}", file=sys.stderr)
            continue
        thermo = sp["thermo"]
        if thermo.get("model") != "NASA7":
            print(f"  ! non-NASA7 {name}", file=sys.stderr)
            continue
        comp = {k: v for k, v in sp.get("composition", {}).items() if k != "E"}
        ranges = thermo["temperature-ranges"]
        coeffs = [list(map(float, row)) for row in thermo["data"]]
        entry = {
            "phase": phase,
            "composition": comp,
            "temperature_ranges": [float(r) for r in ranges],
            "coeffs": coeffs,
            "molecular_weight": _mw(comp),
        }
        if "note" in sp:
            entry["note"] = sp["note"]
        out[name] = entry
    return out


def main(argv: list[str]) -> int:
    if len(argv) >= 3:
        gas_path, cond_path = Path(argv[1]), Path(argv[2])
    else:
        import urllib.request

        base = "https://raw.githubusercontent.com/Cantera/cantera/main/data/"
        tmp = Path("/tmp")
        gas_path, cond_path = tmp / "nasa_gas.yaml", tmp / "nasa_condensed.yaml"
        for url, dst in ((base + "nasa_gas.yaml", gas_path), (base + "nasa_condensed.yaml", cond_path)):
            print(f"downloading {url}")
            urllib.request.urlretrieve(url, dst)

    species = {}
    species.update(_extract(gas_path, GAS_SPECIES, "gas"))
    species.update(_extract(cond_path, CONDENSED_SPECIES, "condensed"))

    db = {
        "meta": {
            "source": "NASA-7 polynomials (McBride, Gordon & Reno, NASA TM-4513, 1993)",
            "retrieved_from": "Cantera project data files nasa_gas.yaml / nasa_condensed.yaml",
            "note": "Coefficients are the published NASA-7 fits; no values invented.",
            "ranges_units": "K",
        },
        "species": dict(sorted(species.items())),
    }
    root = Path(__file__).resolve().parents[1]
    out = root / "data" / "thermo" / "thermo.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        yaml.safe_dump(db, fh, sort_keys=False, allow_unicode=True, width=100)
    print(f"wrote {out} with {len(species)} species")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

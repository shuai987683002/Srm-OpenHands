"""Thermochemical species with NASA-7 (NASA-7 coefficient) polynomials.

NASA-7 model (McBride, Gordon & Reno, NASA TM-4513)::

    cp/R  = a1 + a2 T + a3 T^2 + a4 T^3 + a5 T^4
    h/RT  = a1 + a2 T/2 + a3 T^2/3 + a4 T^3/4 + a5 T^4/5 + a6/T
    s/R   = a1 ln T + a2 T + a3 T^2/2 + a4 T^3/3 + a5 T^4/4 + a7

Two coefficient rows cover a low and a high temperature interval; a single row
covers the whole range (common for condensed species).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..constants import R_UNIVERSAL


@dataclass
class ThermoSpecies:
    name: str
    phase: str  # "gas" | "condensed"
    composition: dict[str, float]
    temperature_ranges: list[float]
    coeffs: list[list[float]]
    molecular_weight: float
    note: str = ""

    def _row(self, T: float) -> list[float]:
        ranges = self.temperature_ranges
        if len(self.coeffs) == 1:
            return self.coeffs[0]
        # choose the interval containing T (clamped at the ends)
        if T < ranges[1]:
            return self.coeffs[0]
        return self.coeffs[1]

    def cp(self, T: float) -> float:
        """Specific heat at constant pressure [J/mol/K]."""
        a = self._row(T)
        cp_r = a[0] + a[1] * T + a[2] * T**2 + a[3] * T**3 + a[4] * T**4
        return cp_r * R_UNIVERSAL

    def h(self, T: float) -> float:
        """Enthalpy [J/mol] (formation enthalpy included)."""
        a = self._row(T)
        h_rt = (
            a[0]
            + a[1] * T / 2
            + a[2] * T**2 / 3
            + a[3] * T**3 / 4
            + a[4] * T**4 / 5
            + a[5] / T
        )
        return h_rt * R_UNIVERSAL * T

    def s(self, T: float) -> float:
        """Entropy [J/mol/K]."""
        a = self._row(T)
        s_r = (
            a[0] * math.log(T)
            + a[1] * T
            + a[2] * T**2 / 2
            + a[3] * T**3 / 3
            + a[4] * T**4 / 4
            + a[6]
        )
        return s_r * R_UNIVERSAL

    def g(self, T: float) -> float:
        """Gibbs free energy [J/mol]."""
        return self.h(T) - T * self.s(T)

    def g_rt(self, T: float) -> float:
        return self.h(T) / (R_UNIVERSAL * T) - self.s(T) / R_UNIVERSAL

    @property
    def is_gas(self) -> bool:
        return self.phase == "gas"


@dataclass
class SpeciesDatabase:
    species: dict[str, ThermoSpecies] = field(default_factory=dict)

    def get(self, name: str) -> ThermoSpecies:
        if name not in self.species:
            raise KeyError(f"species '{name}' not in thermo database")
        return self.species[name]

    def names(self) -> list[str]:
        return sorted(self.species)

    @classmethod
    def from_file(cls, path) -> SpeciesDatabase:
        import yaml

        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        out: dict[str, ThermoSpecies] = {}
        for name, rec in data.get("species", {}).items():
            out[name] = ThermoSpecies(
                name=name,
                phase=rec.get("phase", "gas"),
                composition=rec.get("composition", {}),
                temperature_ranges=rec["temperature_ranges"],
                coeffs=rec["coeffs"],
                molecular_weight=rec["molecular_weight"],
                note=rec.get("note", ""),
            )
        return cls(out)

    @classmethod
    def load_default(cls) -> SpeciesDatabase:
        from ..config import get_config

        return cls.from_file(get_config().resolve_path("paths.thermo_db"))

"""Gibbs free-energy minimisation for combustion products.

Method
------
The chamber products are modelled as an ideal-gas mixture plus optional pure
condensed phases.  At a fixed temperature the equilibrium composition minimises
the total Gibbs free energy (per kg of propellant)

    G(n) = sum_gas  n_i [ g_i^0(T)/(RT) + ln(p^0/P) + ln(n_i / N) ] R T
         + sum_cond n_k  g_k^0(T)

with ``N = sum_gas n_i``, subject to element conservation ``A^T n = b`` and
``n >= 0``.

* The gas part ``sum n_i ln(n_i/N)`` is convex, the condensed part is linear and
  the constraints are linear, so the problem is a **convex program** with a
  unique global minimum.  We solve it with a sequential-quadratic-programming
  (SLSQP) solver; this is the same Gibbs-minimisation problem solved by NASA
  CEA (Gordon & McBride), only with a different (SQP) algorithm than CEA's
  linear-programming iteration.
* The **adiabatic flame temperature** is found by an outer root solve on the
  enthalpy balance ``H_products(T) = H_reactants``.

Inputs are the published NASA-7 polynomials (McBride/Gordon/Reno TM-4513) and
the propellant composition/formation enthalpy supplied by the user; nothing is
invented here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.optimize import brentq, minimize, nnls

from ..constants import P_ATM, R_UNIVERSAL
from .species import SpeciesDatabase, ThermoSpecies

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


@dataclass
class EquilibriumResult:
    temperature: float
    pressure: float
    n_gas: dict[str, float]  # moles per kg propellant
    n_cond: dict[str, float]
    mole_fractions: dict[str, float]  # gas mole fractions
    molecular_weight: float  # mean molar mass of gas [kg/mol]
    gamma: float
    cp_molar: float  # [J/mol/K] of gas
    enthalpy: float  # total product enthalpy per kg [J/kg]
    total_moles_gas: float
    converged: bool = True
    iterations: int = 0


def _element_list(composition: dict[str, float]) -> list[str]:
    return [el for el, v in composition.items() if abs(v) > 0]


def formula_molar_mass(composition: dict[str, float]) -> float:
    return sum(ATOMIC_WEIGHTS[el] * n for el, n in composition.items())


class GibbsEquilibrium:
    """Equilibrium solver bound to a species database."""

    def __init__(self, db: SpeciesDatabase, p_ref: float = P_ATM) -> None:
        self.db = db
        self.p_ref = p_ref

    def _candidates(self, elements: list[str]):
        gases: list[ThermoSpecies] = []
        conds: list[ThermoSpecies] = []
        for name in self.db.names():
            sp = self.db.get(name)
            if not sp.composition or not set(sp.composition).issubset(set(elements)):
                continue
            (gases if sp.is_gas else conds).append(sp)
        return gases, conds

    def solve(self, temperature: float, pressure: float,
              composition: dict[str, float]) -> EquilibriumResult:
        """Equilibrium composition at fixed (T, P) for one mole-formula of propellant."""
        els = _element_list(composition)
        mf = formula_molar_mass(composition)
        b = np.array([composition[el] for el in els]) / mf  # element moles / kg
        gases, conds = self._candidates(els)
        if not gases:
            raise ValueError(f"no gas species available for elements {els}")

        species = gases + conds
        nsp = len(species)
        ngas = len(gases)
        A = np.array([[sp.composition.get(el, 0.0) for el in els] for sp in species])  # (nsp, nel)
        is_gas = np.array([sp.is_gas for sp in species])
        g_rt = np.array([sp.g_rt(temperature) for sp in species])
        lnp = math.log(self.p_ref / pressure)

        # feasible positive start from non-negative least squares on gas species
        try:
            n0_gas, _ = nnls(A[:ngas].T, b)
        except Exception:
            n0_gas = np.full(ngas, b.sum() / ngas)
        if n0_gas.sum() <= 0:
            n0_gas = np.full(ngas, b.sum() / ngas)
        n0 = np.maximum(np.concatenate([n0_gas, np.full(nsp - ngas, 1e-12)]), 1e-12)

        def gibbs(n: np.ndarray) -> float:
            ng = n[is_gas]
            N = ng.sum()
            if N <= 0:
                return 1e30
            lnx = np.where(ng > 0, np.log(np.maximum(ng, 1e-300) / N), 0.0)
            g_gas = float(np.sum(ng * (g_rt[is_gas] + lnp + lnx)))
            g_cond = float(np.sum(n[~is_gas] * g_rt[~is_gas]))
            return g_gas + g_cond

        constraints = [{"type": "eq", "fun": lambda n: A.T @ n - b}]
        bounds = [(1e-14, None)] * nsp
        res = minimize(gibbs, n0, method="SLSQP", bounds=bounds, constraints=constraints,
                       options={"maxiter": 500, "ftol": 1e-14})
        n = np.maximum(res.x, 0.0)
        # second polish pass from the found point
        res2 = minimize(gibbs, np.maximum(n, 1e-12), method="SLSQP", bounds=bounds,
                        constraints=constraints, options={"maxiter": 500, "ftol": 1e-16})
        if gibbs(np.maximum(res2.x, 0.0)) <= gibbs(n):
            n = np.maximum(res2.x, 0.0)

        ngas_tot = float(n[is_gas].sum())
        xg = n[is_gas] / ngas_tot if ngas_tot > 0 else np.zeros(ngas)
        n_gas = {gases[i].name: float(n[i]) for i in range(ngas)}
        n_cond = {conds[k].name: float(n[ngas + k]) for k in range(len(conds))}

        mw = float(sum(xg[i] * gases[i].molecular_weight for i in range(ngas)))
        cp_molar = float(sum(xg[i] * gases[i].cp(temperature) for i in range(ngas)))
        cv_molar = cp_molar - R_UNIVERSAL
        gamma = cp_molar / cv_molar if cv_molar > 0 else 1.2
        H = float(sum(n[i] * species[i].h(temperature) for i in range(nsp)))

        elem_resid = float(np.linalg.norm(A.T @ n - b))
        return EquilibriumResult(
            temperature=temperature, pressure=pressure, n_gas=n_gas, n_cond=n_cond,
            mole_fractions={gases[i].name: float(xg[i]) for i in range(ngas)},
            molecular_weight=mw, gamma=gamma, cp_molar=cp_molar, enthalpy=H,
            total_moles_gas=ngas_tot,
            converged=bool(res.success and elem_resid < 1e-6), iterations=int(res.nit),
        )

    def solve_adiabatic(self, pressure: float, composition: dict[str, float],
                        formation_enthalpy_per_kg: float,
                        t_lo: float = 400.0, t_hi: float = 5000.0) -> EquilibriumResult:
        """Adiabatic flame temperature at fixed pressure."""

        def h_res(T: float) -> float:
            return self.solve(T, pressure, composition).enthalpy - formation_enthalpy_per_kg

        f_lo, f_hi = h_res(t_lo), h_res(t_hi)
        if f_lo * f_hi > 0:
            T = t_lo if abs(f_lo) < abs(f_hi) else t_hi
            out = self.solve(T, pressure, composition)
            out.converged = False
            return out
        T_c = brentq(h_res, t_lo, t_hi, xtol=0.2, rtol=1e-7, maxiter=120)
        return self.solve(T_c, pressure, composition)

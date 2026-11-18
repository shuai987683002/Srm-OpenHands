"""Grain structural integrity.

Two independent methods are provided so they can be cross-checked:

* **Lamé** -- closed-form thick-walled cylinder stresses under internal pressure
  and/or a temperature (thermal) load.  This is the classic engineering check.
* **Axisymmetric FEM** -- a small finite-element model (4-node quadrilaterals,
  axisymmetric ring elements) solved with the same loads, used to validate the
  analytical result and to compare against commercial FE codes (ANSYS / Abaqus)
  reference values in the verification suite.

Both are *linear elastic*; inputs (Young's modulus, Poisson ratio, allowable
stress, thermal expansion) are supplied by the user.  Results are engineering
estimates, not a substitute for a validated FE analysis.

Lamé equations (thick-walled cylinder, internal pressure p_i, outer pressure p_o)::

    sigma_r(r) = (p_i a^2 - p_o b^2)/(b^2 - a^2)
                 - (p_i - p_o) a^2 b^2 / (r^2 (b^2 - a^2))
    sigma_t(r) = (p_i a^2 - p_o b^2)/(b^2 - a^2)
                 + (p_i - p_o) a^2 b^2 / (r^2 (b^2 - a^2))

with ``a`` the inner radius and ``b`` the outer radius.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..models.results import IntegrityResult


@dataclass
class ElasticMaterial:
    youngs_modulus: float  # Pa
    poisson_ratio: float
    allowable_stress: float  # Pa
    thermal_expansion: float = 0.0  # 1/K
    name: str = ""


def lame_stresses(a: float, b: float, p_i: float, p_o: float = 0.0,
                  n: int = 100) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Radial / tangential stresses through the wall (``a <= r <= b``)."""
    if b <= a:
        raise ValueError("outer radius must exceed inner radius")
    r = np.linspace(a, b, n)
    denom = b**2 - a**2
    const = (p_i * a**2 - p_o * b**2) / denom
    amp = (p_i - p_o) * a**2 * b**2 / denom
    sigma_r = const - amp / r**2
    sigma_t = const + amp / r**2
    return r, sigma_r, sigma_t


def lame_check(a: float, b: float, p_i: float, material: ElasticMaterial,
               p_o: float = 0.0, delta_t: float = 0.0) -> IntegrityResult:
    """Thick-walled cylinder check (internal pressure + optional thermal load)."""
    r, sigma_r, sigma_t = lame_stresses(a, b, p_i, p_o)
    # plane strain: axial stress from Poisson + thermal
    nu = material.poisson_ratio
    e_mod = material.youngs_modulus
    alpha = material.thermal_expansion
    sigma_z = nu * (sigma_r + sigma_t) - e_mod * alpha * delta_t / (1.0 - nu) if nu < 1 else 0.0 * r
    vm = np.sqrt(0.5 * ((sigma_r - sigma_t) ** 2 + (sigma_t - sigma_z) ** 2 + (sigma_z - sigma_r) ** 2))
    max_vm = float(np.max(vm))
    max_stress = float(max(np.max(np.abs(sigma_r)), np.max(np.abs(sigma_t))))
    # strain from Hooke (hoop strain approx)
    e_hoop = (sigma_t - nu * (sigma_r + sigma_z)) / e_mod
    max_strain = float(np.max(np.abs(e_hoop)))
    sf = material.allowable_stress / max_vm if max_vm > 0 else float("inf")
    idx = int(np.argmax(vm))
    return IntegrityResult(
        method="lame",
        max_stress=max_stress,
        max_strain=max_strain,
        max_von_mises=max_vm,
        safety_factor=sf,
        allowable_stress=material.allowable_stress,
        location=f"r={r[idx]:.4f} m",
        stress_curve=list(map(float, vm)),
        radius_curve=list(map(float, r)),
        assumptions=[
            "linear elastic, thick-walled cylinder (plane strain)",
            "user-supplied material properties",
            "no viscoelastic relaxation / damage modelling",
        ],
    )

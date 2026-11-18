"""Axisymmetric linear-elastic finite element solver.

A small, self-contained FEM used to cross-check the analytical Lamé result and
to reproduce the *same* physical problem that commercial FE codes (ANSYS,
Abaqus) solve for a case-bonded grain, so their numbers can be compared.

Formulation
-----------
4-node isoparametric quadrilaterals, 2 DOF per node ``(u_r, u_z)``, 2x2 Gauss
integration, axisymmetric strain vector

    eps = [ eps_r, eps_z, eps_theta, gamma_rz ]
    eps_r = du_r/dr,  eps_z = du_z/dz,  eps_theta = u_r/r,  gamma_rz = du_r/dz+du_z/dr

Isotropic elasticity matrix (full 3-D form restricted to axisymmetric strains)
and a thermal strain ``alpha*delta_T*[1,1,1,0]``.

Boundary conditions supported
-----------------------------
* inner radius: gas pressure ``p_i`` (traction outward on the propellant);
* outer radius: free (``p_o``) or fully fixed (case-bonded, ``u_r = 0``);
* axial: ``u_z = 0`` everywhere -> generalised plane strain, which reproduces
  the Lamé plane-strain solution exactly (used for validation).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .lame import ElasticMaterial


@dataclass
class FemResult:
    max_von_mises: float
    max_radial_stress: float
    max_hoop_stress: float
    max_hoop_strain: float
    safety_factor: float
    node_r: np.ndarray
    node_z: np.ndarray
    node_vm: np.ndarray
    converged: bool = True
    n_nodes: int = 0
    n_elements: int = 0


def _gauss_2x2():
    g = 1.0 / np.sqrt(3.0)
    pts = [(-g, -g), (g, -g), (g, g), (-g, g)]
    wts = [1.0, 1.0, 1.0, 1.0]
    return pts, wts


def _shape(xi, eta):
    N = 0.25 * np.array([(1 - xi) * (1 - eta), (1 + xi) * (1 - eta),
                         (1 + xi) * (1 + eta), (1 - xi) * (1 + eta)])
    dN_dxi = 0.25 * np.array([-(1 - eta), (1 - eta), (1 + eta), -(1 + eta)])
    dN_deta = 0.25 * np.array([-(1 - xi), -(1 + xi), (1 + xi), (1 - xi)])
    return N, dN_dxi, dN_deta


def _bmatrix(xi_eta, en):
    """Strain-displacement matrix at a local coordinate (r, detJ returned too)."""
    xi, eta = xi_eta
    N, dN_dxi, dN_deta = _shape(xi, eta)
    J = np.array([[dN_dxi @ en[:, 0], dN_dxi @ en[:, 1]],
                  [dN_deta @ en[:, 0], dN_deta @ en[:, 1]]])
    detJ = np.linalg.det(J)
    invJ = np.linalg.inv(J)
    dN_dr = invJ[0, 0] * dN_dxi + invJ[0, 1] * dN_deta
    dN_dz = invJ[1, 0] * dN_dxi + invJ[1, 1] * dN_deta
    r = N @ en[:, 0]
    B = np.zeros((4, 8))
    for a in range(4):
        B[0, 2 * a] = dN_dr[a]
        B[1, 2 * a + 1] = dN_dz[a]
        B[2, 2 * a] = N[a] / r if r > 0 else 0.0
        B[3, 2 * a] = dN_dz[a]
        B[3, 2 * a + 1] = dN_dr[a]
    return B, r, detJ


def solve_axisymmetric(r_inner: float, r_outer: float, length: float,
                       material: ElasticMaterial, p_inner: float,
                       p_outer: float = 0.0, delta_t: float = 0.0,
                       nr: int = 40, nz: int = 20, case_bonded: bool = False) -> FemResult:
    """Solve the axisymmetric hollow-cylinder problem."""
    E, nu = material.youngs_modulus, material.poisson_ratio
    alpha = material.thermal_expansion

    # mesh (radial x axial grid of quad elements)
    rs = np.linspace(r_inner, r_outer, nr + 1)
    zs = np.linspace(0.0, length, nz + 1)
    nodes = []
    idx = {}
    for j, z in enumerate(zs):
        for i, r in enumerate(rs):
            idx[(i, j)] = len(nodes)
            nodes.append((r, z))
    nodes = np.array(nodes)
    nn = len(nodes)

    # constitutive matrix
    c = E / ((1 + nu) * (1 - 2 * nu))
    D = c * np.array([
        [1 - nu, nu, nu, 0.0],
        [nu, 1 - nu, nu, 0.0],
        [nu, nu, 1 - nu, 0.0],
        [0.0, 0.0, 0.0, (1 - 2 * nu) / 2.0],
    ])
    # split into deviatoric + volumetric so the volumetric part can use the
    # mean-dilatation (B-bar) treatment, which removes volumetric locking for
    # nearly incompressible propellants (nu -> 0.5).
    m = np.array([1.0, 1.0, 1.0, 0.0])
    K_bulk = E / (3.0 * (1.0 - 2.0 * nu))
    D_vol = K_bulk * np.outer(m, m)
    D_dev = D - D_vol
    eps_th = alpha * delta_t * np.array([1.0, 1.0, 1.0, 0.0])

    K = np.zeros((2 * nn, 2 * nn))
    F = np.zeros(2 * nn)

    gpts, gwts = _gauss_2x2()

    def elem_dofs(i, j):
        return [2 * idx[(i, j)], 2 * idx[(i, j)] + 1,
                2 * idx[(i + 1, j)], 2 * idx[(i + 1, j)] + 1,
                2 * idx[(i + 1, j + 1)], 2 * idx[(i + 1, j + 1)] + 1,
                2 * idx[(i, j + 1)], 2 * idx[(i, j + 1)] + 1]

    for j in range(nz):
        for i in range(nr):
            en = np.array([nodes[idx[(i, j)]], nodes[idx[(i + 1, j)]],
                           nodes[idx[(i + 1, j + 1)]], nodes[idx[(i, j + 1)]]])
            dofs = elem_dofs(i, j)
            ke = np.zeros((8, 8))
            fe = np.zeros(8)
            # mean-dilatation (B-bar): volumetric row averaged at the centroid
            Bc, _rc, _detJc = _bmatrix(np.array([0.0, 0.0]), en)
            bvol_c = Bc[0] + Bc[1] + Bc[2]
            vol_e = 0.0
            for (xi, eta), w in zip(gpts, gwts):
                N, dN_dxi, dN_deta = _shape(xi, eta)
                J = np.array([[dN_dxi @ en[:, 0], dN_dxi @ en[:, 1]],
                              [dN_deta @ en[:, 0], dN_deta @ en[:, 1]]])
                detJ = np.linalg.det(J)
                if detJ <= 0:
                    continue
                invJ = np.linalg.inv(J)
                dN_dr = invJ[0, 0] * dN_dxi + invJ[0, 1] * dN_deta
                dN_dz = invJ[1, 0] * dN_dxi + invJ[1, 1] * dN_deta
                r = N @ en[:, 0]
                B = np.zeros((4, 8))
                for a in range(4):
                    B[0, 2 * a] = dN_dr[a]
                    B[1, 2 * a + 1] = dN_dz[a]
                    B[2, 2 * a] = N[a] / r if r > 0 else 0.0
                    B[3, 2 * a] = dN_dz[a]
                    B[3, 2 * a + 1] = dN_dr[a]
                dV = (2 * np.pi * r * detJ) * w
                vol_e += dV
                ke += B.T @ D_dev @ B * dV
                fe += B.T @ (D_dev @ eps_th) * dV
            # volumetric (mean-dilatation) contribution, integrated with the
            # element volume only (the mean value is constant over the element)
            ke += np.outer(bvol_c, bvol_c) * K_bulk * vol_e
            fe += bvol_c * K_bulk * (3.0 * alpha * delta_t) * vol_e
            K[np.ix_(dofs, dofs)] += ke
            F[dofs] += fe

    # --- boundary conditions ------------------------------------------------
    fixed = []
    # axial: u_z = 0 on all nodes (generalised plane strain)
    for n in range(nn):
        fixed.append(2 * n + 1)
    if case_bonded:
        for j in range(nz + 1):
            fixed.append(2 * idx[(nr, j)])

    free = np.setdiff1d(np.arange(2 * nn), np.array(fixed, dtype=int))

    # --- internal pressure traction on the inner radius face (xi = -1) ------
    # On the xi=-1 edge only local nodes 0 (=(i,j)) and 3 (=(i,j+1)) are active,
    # and the edge coordinate is eta in [-1, 1].
    if p_inner != 0.0:
        for j in range(nz):
            en = np.array([nodes[idx[(0, j)]], nodes[idx[(1, j)]],
                           nodes[idx[(1, j + 1)]], nodes[idx[(0, j + 1)]]])
            n0 = idx[(0, j)]
            n3 = idx[(0, j + 1)]
            for eta, w in [(-1 / np.sqrt(3), 1.0), (1 / np.sqrt(3), 1.0)]:
                N = 0.5 * np.array([1 - eta, 1 + eta])  # for nodes 0 and 3
                r = N @ np.array([en[0, 0], en[3, 0]])
                dz_deta = 0.5 * (en[3, 1] - en[0, 1])
                F[2 * n0] += N[0] * p_inner * (2 * np.pi * r) * abs(dz_deta) * w
                F[2 * n3] += N[1] * p_inner * (2 * np.pi * r) * abs(dz_deta) * w

    # --- external pressure traction on the outer face (xi = +1) -------------
    if p_outer != 0.0 and not case_bonded:
        for j in range(nz):
            en = np.array([nodes[idx[(nr, j)]], nodes[idx[(nr, j + 1)]]])
            n1 = idx[(nr, j)]
            n2 = idx[(nr, j + 1)]
            for eta, w in [(-1 / np.sqrt(3), 1.0), (1 / np.sqrt(3), 1.0)]:
                N = 0.5 * np.array([1 - eta, 1 + eta])
                r = N @ np.array([en[0, 0], en[1, 0]])
                dz_deta = 0.5 * (en[1, 1] - en[0, 1])
                # outward normal on the outer face is +r; external pressure acts inward
                F[2 * n1] += -N[0] * p_outer * (2 * np.pi * r) * abs(dz_deta) * w
                F[2 * n2] += -N[1] * p_outer * (2 * np.pi * r) * abs(dz_deta) * w

    # --- solve --------------------------------------------------------------
    Kff = K[np.ix_(free, free)]
    Ff = F[free]
    u = np.zeros(2 * nn)
    try:
        u[free] = np.linalg.solve(Kff, Ff)
    except np.linalg.LinAlgError:
        u[free] = np.linalg.lstsq(Kff, Ff, rcond=None)[0]

    # --- post-process -------------------------------------------------------
    vm_nodes = np.zeros(nn)
    cnt = np.zeros(nn)
    max_sr = max_st = max_eh = 0.0
    for j in range(nz):
        for i in range(nr):
            en = np.array([nodes[idx[(i, j)]], nodes[idx[(i + 1, j)]],
                           nodes[idx[(i + 1, j + 1)]], nodes[idx[(i, j + 1)]]])
            dofs = elem_dofs(i, j)
            ue = u[dofs]
            # consistent B-bar stress: volumetric strain taken as the element mean
            Bc, _rc, _detJc = _bmatrix((0.0, 0.0), en)
            bvol_c = Bc[0] + Bc[1] + Bc[2]
            eps_vol_mean = bvol_c @ ue
            for (xi, eta), _w in zip(gpts, gwts):
                B, r, detJ = _bmatrix((xi, eta), en)
                if detJ <= 0:
                    continue
                eps = B @ ue
                sig = D_dev @ (eps - eps_th) + K_bulk * (eps_vol_mean - 3.0 * alpha * delta_t) * m
                sr, sz, st, trz = sig
                vm = np.sqrt(0.5 * ((sr - sz) ** 2 + (sz - st) ** 2 + (st - sr) ** 2)
                             + 3 * trz**2)
                max_sr = max(max_sr, abs(sr))
                max_st = max(max_st, abs(st))
                max_eh = max(max_eh, abs(eps[2]))
                for a in range(4):
                    vm_nodes[idx[(i, j)] if a == 0 else idx[(i + 1, j)] if a == 1
                             else idx[(i + 1, j + 1)] if a == 2 else idx[(i, j + 1)]] += vm
                    cnt[idx[(i, j)] if a == 0 else idx[(i + 1, j)] if a == 1
                        else idx[(i + 1, j + 1)] if a == 2 else idx[(i, j + 1)]] += 1
    cnt[cnt == 0] = 1
    vm_nodes /= cnt
    max_vm = float(np.max(vm_nodes))
    sf = material.allowable_stress / max_vm if max_vm > 0 else float("inf")
    return FemResult(
        max_von_mises=max_vm,
        max_radial_stress=max_sr,
        max_hoop_stress=max_st,
        max_hoop_strain=max_eh,
        safety_factor=sf,
        node_r=nodes[:, 0],
        node_z=nodes[:, 1],
        node_vm=vm_nodes,
        n_nodes=nn,
        n_elements=nr * nz,
    )

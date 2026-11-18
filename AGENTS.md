# AGENTS.md — repository knowledge for AI/collaborators

## What this repo is

`Srm-Open` is an open-source **solid rocket motor (SRM) design** toolkit for
research, teaching and non-commercial R&D. It closes the loop

```
top-level indicators <-> grain geometry <-> burn area <-> internal ballistics <-> performance
```

in both directions (forward synthesis and inverse recovery) with a built-in
verification chain. See `docs/开源固体火箭发动机设计软件实现方案.md` for the
full plan.

## Build / run / test

```bash
pip install -e ".[dev,all]"                 # editable install with dev + extras
PYTHONPATH=src python -m pytest tests -q    # full suite (unit + integration + verification)
PYTHONPATH=src python -m pytest tests/verification -m verification -q -s   # V&V cases with printed results
srm forward --template star_6_point --thrust 8000 --burn-time 4 --max-pressure 9e6
PYTHONPATH=src python examples/run_case.py examples/projects/06_synthesis_star
PYTHONPATH=src python scripts/sensitivity.py --out output/sensitivity
```

The core engine runs **offline**. `cantera` / `rocketcea` are only used to
generate reference values in the verification tests, never at runtime.

## Layout (actual)

```
src/srm_open/
  models/        pydantic data models (requirements, grain, propellant, nozzle, results, design_result)
  config.py      layered YAML config (note: YAML 1.1 parses 1.0e4 as str -> _coerce handles it)
  propellant/    burn-rate laws + propellant database loader
  thermo/        self-contained Gibbs equilibrium solver (NASA-7, 74 species) + adapters
  nozzle/        isentropic nozzle (Cf, mdot, exit Mach)
  geometry/      parameterised port families, 2-D analysis, sketch/export
  burnback/      geometric (shapely Minkowski) + level-set channels + crosscheck
  ballistics/    equilibrium (closed form) + transient (solve_ivp) solvers
  performance/   thrust/impulse/Isp integration
  grain_body/    cylindrical main-body calculation (web, sliver, loading)
  integrity/     Lame analytical + axisymmetric FEM (B-bar, no volumetric locking)
  inverse/       design space, inverse_design, fit_burn_area, analyze_existing_grain, optimizers
  orchestration/ requirements completion, forward pipeline, grain synthesis
  verification/  design-level checks
  io/            project save/load, template library
  report/        markdown/HTML report + PNG plots
  api/           high-level facade + argparse CLI
data/            templates.yaml, propellants.yaml, thermo.yaml
configs/         default.yaml
tests/           unit/ integration/ verification/
examples/        projects/ (cases), notebooks/, run_case.py
scripts/         build_thermo_db.py, sensitivity.py, gen_verification_results.py
docs/            plan, architecture.md, theory/, user_guide/, verification/
```

## Conventions and gotchas

- **One grain is a list of segments** (`GrainGeometry.segments`); each segment
  carries `family`, `params`, `burn_mode`, `outer_diameter`, `length`.
- **Burn area lives in `burnback.geometric`** (vector, exact for polygons) and
  `burnback.levelset` (grid). Ballistics uses `burn_area_at`, so forward/inverse
  share one geometry channel.
- **Equilibrium pressure** is closed form: `p = (rho_p c* a A_b / A_t)^(1/(1-n))`.
- **Throat sizing** is from the target chamber pressure (`size_throat_from_pressure`),
  not from thrust — keeps the chain physically consistent.
- **Grain synthesis** (`orchestration/synthesis.py`) matches propellant volume,
  mean burn area and web; `design_forward` refines the web target from the
  realized (burn-rate-weighted) mean pressure so burn time lands on target.
- **Forward thrust is not exactly hit by design**: sizing the throat from the
  design pressure makes delivered thrust slightly above target (the 0D model's
  `C_F` differs from the nozzle's). This is a documented limitation; exact
  thrust/impulse matching is the *inverse* design's job (`inverse_design`
  converges to 9/9 on the example). Do not "fix" it by sizing the throat from
  thrust — that drops the operating pressure and degrades every other metric.
- **`inverse_convergence`** is judged on the achieved objective residual, not the
  optimiser's `success` flag (Nelder-Mead reports `success=False` on simplex
  shrink even when the physical targets are met).
- **FEM must stay locking-free**: `integrity/fem.py` uses a mean-dilatation
  (B-bar) split. Do not revert to the plain `B.T @ D @ B` form — it locks at
  propellant-like `nu ~ 0.5` and gives mesh-dependent garbage.
- **YAML 1.1 exponent trap**: `1.0e4` is parsed as a string; `config._coerce`
  converts numeric-looking strings to float. Use `1.0e+4` style in configs.
- **AP/HTPB Gibbs T_ad is low** vs Cantera (chlorine/condensed-phase
  simplification). Default to literature `direct` thermo; `ap_htpb_gibbs` is a
  demo entry only.

## Verification status

All of `tests/` passes. V&V cases (V1–V6) and their recorded results are in
`docs/verification/verification_report.md`: thermochemistry vs Cantera (<0.1%),
nozzle isentropic identities, tube geometry closed forms, two-channel burn-area
crosscheck, FEM vs Lamé (<0.4%), equilibrium pressure closed form + mass
balance (~1e-15).

## Boundaries

Research / teaching / non-commercial R&D only. No weaponisation. Results are
not engineering deliverables and must be independently validated.

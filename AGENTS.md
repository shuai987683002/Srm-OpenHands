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
PYTHONPATH=src python scripts/build_schema.py   # regenerate data/schema after a model change
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
  inverse/       design space, inverse_design, fit_burn_area, fit_outline,
                 analyze_existing_grain, sensitivity_matrix/identifiability, optimizers
  orchestration/ requirements completion, forward pipeline, grain synthesis,
                 study.py (sweep / monte_carlo)
  verification/  design-level checks
  io/            project save/load, template library
  report/        markdown/HTML/PDF report + 2-D plots + 3-D previews
  viz/           matplotlib 3-D grain / burnback previews (optional [viz])
  api/           high-level facade + argparse CLI; rest.py = optional FastAPI
data/            templates.yaml, propellants.yaml, thermo.yaml
data/schema/     generated JSON Schemas (build_schema.py) — do not hand-edit
configs/         default.yaml
tests/           unit/ integration/ verification/
examples/        projects/ (cases), notebooks/, run_case.py
scripts/         build_thermo_db.py, build_schema.py, sensitivity.py,
                 gen_verification_results.py
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
- **Forward synthesis is two-stage** (`api/facade.py:design_forward`): after the
  analytical synthesis, a warm-started Nelder-Mead **local refinement**
  (`_refine_synthesis`, plan §5.1 step 7) polishes the grain so thrust / burn
  time / total impulse hit the targets on the same forward model. Examples
  06/07 reach 9/9. Pass `refine=False` for fast parameter sweeps. The refinement
  is *local*: a far-off synthesis seed needs a larger `refine_max_iter` or the
  global `inverse_design` optimiser.
- **Do not size the throat from thrust.** Throat sizing is from the target
  chamber pressure (`size_throat_from_pressure`); sizing from thrust drops the
  operating pressure and degrades every other metric. Thrust matching belongs to
  the refinement/inverse layer, not the nozzle sizing.
- **The three multi-cavity families are distinct by construction**: wagon wheel
  = bore + thin radial channels + circular outer bulbs; slotted = bore + constant
  width square-ended slots; finocyl = bore + fins tapering to 40 % width at the
  tip. They were once three renames of one "bore + rectangles" builder — keep
  `test_slotted_finocyl_wagon_wheel_are_distinct` green. Ports must stay a single
  connected `Polygon`; a disconnected union is silently truncated by
  `_safe_polygon`, which is how the wagon-wheel bulbs once vanished.
- **`shapely` is a hard core dependency** (`geometry/analysis.py`,
  `geometry/families.py`, `burnback/levelset.py`). It is declared in
  `pyproject.toml` core deps — keep it there; a missing shapely breaks the
  geometry channel that everything else builds on.
- **`inverse_convergence`** is judged on the achieved objective residual, not the
  optimiser's `success` flag (Nelder-Mead reports `success=False` on simplex
  shrink even when the physical targets are met).
- **Three inverse paths, do not conflate them**: A = `fit_outline` (2-D port
  outline / point cloud -> theta), B = `fit_burn_area` (A_b(e) -> theta), C =
  `inverse_design` (indicators -> theta). A only sees one cross-section, so the
  axial length must be supplied and is held fixed.
- **`fit_outline` identifies the lobe count from the angular harmonic signature
  of `r(theta)`, not from area/perimeter/moment** — those three scalars alone are
  near-degenerate in lobe count (a 6-lobe and 7-lobe port can match them). The
  integer count is enumerated on the outside, continuous parameters fitted
  inside (Nelder-Mead). Do not "simplify" it to a pure 3-scalar objective.
- **`sensitivity_matrix` excludes `frozen` (integer counts) and `inactive`
  (zero-response) parameters from the SVD**; a single dead parameter otherwise
  reports the whole set as singular. VIF > 10 means `confounded` (the parameters
  trade off — e.g. star bore diameter vs length, both scaling A_b). Verified
  against an actual fit in `test_v8_identifiability_matches_recovery`.
- **Star `point_half_angle` is clamped** to the feasible band for the given radii
  (~14 deg for r_valley=25 mm / r_tip=65 mm); beyond it the outline stops
  changing and the parameter is correctly reported `inactive`. Pick test angles
  inside the band.
- **`study.sweep` / `monte_carlo` take the same forward model** as `forward`; MC
  draws are seeded (`seed=12345` default) so runs are bit-identical. Inputs are
  treated as independent (no correlation matrix yet).
- **FEM must stay locking-free**: `integrity/fem.py` uses a mean-dilatation
  (B-bar) split. Do not revert to the plain `B.T @ D @ B` form — it locks at
  propellant-like `nu ~ 0.5` and gives mesh-dependent garbage.
- **YAML 1.1 exponent trap**: `1.0e4` is parsed as a string; `config._coerce`
  converts numeric-looking strings to float. Use `1.0e+4` style in configs.
- **`data/schema/` is generated, never hand-edited.** `scripts/build_schema.py`
  emits it from the pydantic models. Nested models must stay cross-file `$ref`
  (inlining them makes `#/$defs/*` dangle), and each sub-schema needs a
  `$schema` key or `referencing` cannot detect its spec. `test_schema.py` has a
  drift guard and CI re-runs the generator + `git diff --exit-code`.
- **`api/rest.py` is a thin wrapper over `api/facade.py`** — no numerics. It
  must stay importable only with the `[api]` extra, so the engine never depends
  on FastAPI. Check field names against the real models: `NozzleModel` has
  `exit_area` (not `exit_diameter`) and `VerificationReport.checks` is a list of
  **dicts** (`c["passed"]`), not objects.
- **`thrust_profile` is a `Literal`** (`neutral|progressive|regressive`). It used
  to be a free string, and a typo silently fell through to the "no preference"
  branch in `recommend_families`, changing the selected family with no error.
- **Example 08 is script-driven** (`study.py`, no `project.yaml`), so
  `run_case.py` over every directory reports it as skipped-by-design. Its
  `study_result.json` is gitignored.
- **AP/HTPB Gibbs T_ad is low** vs Cantera (chlorine/condensed-phase
  simplification). Default to literature `direct` thermo; `ap_htpb_gibbs` is a
  demo entry only.

## Verification status

All of `tests/` passes (134 tests: unit + integration + verification +
performance). The REST and 3-D preview suites `importorskip` FastAPI /
matplotlib, so they skip rather than fail when the optional extras are absent. V&V cases (V1–V9) and their recorded results are in
`docs/verification/verification_report.md`: thermochemistry vs Cantera (<0.1%),
nozzle isentropic identities, tube geometry closed forms, two-channel burn-area
crosscheck, FEM vs Lamé (<0.4%), equilibrium pressure closed form + mass
balance (~1e-15), inverse path-A round trip (objective < 1e-8 on 4 families),
identifiability verdicts cross-checked against real fits, and Monte-Carlo
reproducibility. Coverage tests assert every `GrainFamily` has a geometry
builder and (except `custom_polygon`, which is outline data rather than a
parametric family) an inverse design space that builds a valid port.

Note: `cylindrical` is an end-burning solid grain — its design space has only
`length` and forces `BurnMode.END`. Asking it for a port outline (`fit_outline`)
raises rather than silently returning an empty port.

## Reproducibility and reporting conventions

- `write_report()` renders Markdown once and derives HTML and PDF from the same
  block list, so the three formats cannot disagree. PDF needs matplotlib and is
  opt-out via `include_pdf=False`; without matplotlib it is silently skipped.
- `Provenance.dependency_versions` and `Provenance.input_hash` are filled by
  `orchestration/forward.py` via `srm_open.provenance`. The hash covers
  requirement + grain + propellant + nozzle as canonical JSON. Two designs with
  the same hash are the same design; do not add fields that vary per run (paths,
  timestamps) to the hashed payload.
- Never define two functions with the same name in `api/cli.py` — `_emit`
  (design printer) and `_emit_json` (payload dumper) are distinct; a duplicate
  `_emit` once shadowed the design printer and broke `srm forward --report`.
- `docs/requirements_compliance.md` maps every FR/NFR to a concrete test. When
  adding a feature, add its evidence row there; an unverified claim is recorded
  as partial, not as done (see NFR-7).

## Boundaries

Research / teaching / non-commercial R&D only. No weaponisation. Results are
not engineering deliverables and must be independently validated.

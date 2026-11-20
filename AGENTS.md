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

`-o addopts=""` is needed to see the pass/fail counts: `addopts = "-q"` in
`pyproject.toml` suppresses the summary line, so a green run prints only dots
and an `EXIT=0`. Current state: **143 passed, 1 skipped**, ~6.5 min.

Four integration tests guard the design-loop failure modes, because those
failures were silent (a plausible-looking design that missed the target) rather
than loud: `test_forward_design_hits_targets_for_every_internal_burning_family`,
`test_wagon_wheel_leaves_web_between_bulbs_and_case`,
`test_forward_design_rejects_a_port_that_fills_the_case`,
`test_inverse_design_converges_from_a_documented_space`. Keep them.

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
  analytical synthesis, a **local refinement** (`_refine_synthesis`, plan §5.1
  step 7) polishes the grain so thrust / burn time / total impulse hit the
  targets on the same forward model. Examples 06/07 reach 9/9, and every grain
  family reaches 9/9 for 5000 N / 3 s in 1–13 s. Pass `refine=False` for fast
  parameter sweeps. The refinement is *local*: a far-off synthesis seed needs a
  larger `refine_max_iter` or the global `inverse_design` optimiser.
- **The refinement is coordinate-wise, not a simplex.** One finite difference
  per coordinate ranks them by thrust and by burn-time sensitivity; the thrust
  coordinate takes a multiplicative step that is accepted only if the combined
  objective improves, and the burn-time coordinate is solved by marching
  (doubling the step until the target is crossed) and then a bracketed
  secant/bisection. Do not replace it with Nelder-Mead: each evaluation costs a
  full burn-back sweep, the objective has flat shelves, and bounds clip.
- **Burn time is solved by crossing the target, not by shrinking the error.**
  A burn-time probe over one small parameter step routinely jumps past the
  target, so "accept only if closer" leaves the solver stuck with no direction
  (`best_dir=0`) and burn time never moves. It must accept a step that lands
  closer *after* bracketing, which is what the march-and-bisect does.
- **One coordinate may serve both thrust and burn time.** Families with a
  single free shape coordinate (wagon-wheel `spoke_length`) move thrust and burn
  time together; `i_tb` therefore falls back to `i_f` instead of insisting on a
  distinct coordinate.
- **Integer parameters must be rounded inside the optimiser too.** `clipped()`
  rounds `Parameter(integer=True)` entries; without that the refinement
  evaluates several distinct vectors that map to one geometry and wastes
  evaluations (wagon wheel went 29 s -> 12 s).
- **`refine_iters=1` is enough.** The refinement is strong enough that a single
  synthesis pass suffices; raising it multiplies runtime for no accuracy gain.
- **Do not size the throat from thrust.** Throat sizing is from the target
  chamber pressure (`size_throat_from_pressure`); sizing from thrust drops the
  operating pressure and degrades every other metric. Thrust matching belongs to
  the refinement/inverse layer, not the nozzle sizing.
- **`cylindrical` is an end-burner and cannot meet short burn times.** It burns
  as a cigarette (t_b = L / r), so 5000 N / 3 s at 160 mm is physically out of
  reach (it returns ~32 s and 5/8 checks). The verification checks reporting the
  miss is the correct outcome, not a bug: do not "fix" it by widening bounds.
- **A grain with no burning surface must fail loudly.** `forward_design` raises
  when the peak burning area is zero (port fills the cross-section); otherwise
  the throat diameter becomes 0 and pydantic rejects it far from the cause.
- **The three multi-cavity families are distinct by construction**: wagon wheel
  = bore + thin radial channels + circular outer bulbs; slotted = bore + constant
  width square-ended slots; finocyl = bore + fins tapering to 40 % width at the
  tip. They were once three renames of one "bore + rectangles" builder — keep
  `test_slotted_finocyl_wagon_wheel_are_distinct` green. Those three must each
  stay a *single* connected `Polygon` (a bulb or fin that only touches the bore
  at one point yields a disconnected union), which is why their builders bury the
  feature root inside the bore.
- **Multi-perforation holes need a real web.** The ring pitch used to be exactly
  `2*rp`, which makes the centre hole tangent to all six ring holes and each ring
  hole tangent to its neighbours. Zero web: the port fuses into one blob after
  0.5 mm of burn and the burning *perimeter* falls, so the thrust came out wrong.
  `web_fraction` (default 0.15 of the hole diameter) sets the pitch; keep it.
- **The refinement must close on the full model.** `_refine_synthesis` screens
  with `fast=True` (quad=16, 48 burn-back steps) but the user receives a
  full-resolution result. The screening model reads 5000 N / 3.000 s while the
  full model reads 4897 N / 3.043 s on a 7-perf grain — the coarse sweep
  straddles the burn-area step where the holes merge. `_polish_on_full_model`
  runs afterwards, closing burn time first (on the web-carrying coordinate, which
  also moves thrust) then thrust (on length, which does not touch the web). Never
  drop that polish, and never trust the screening residual as the reported one.
- **A port may legitimately be a `MultiPolygon`.** Seven perforations are seven
  separate voids. `_safe_polygon` / `analysis._safe` used to reduce a
  MultiPolygon to its largest component, which silently kept 2 of the 7 holes and
  halved the burning area — a wrong thrust level with no error. They now keep
  every component, and `test_multi_perforation_keeps_every_hole` guards it.
  Anything that walks a port must iterate components (`geometry.analysis._parts`,
  `_boundary_coords`, `sketch.segment_outlines`, `viz._port_rings`), never
  `port.exterior`.
- **A radial flank can be collinear with a sampling ray.** `_angular_signature`
  intersects each ray with the port boundary; on a wedge or slot the flank lies
  *along* the ray, so the intersection is a `LineString`, not a `Point`. Handle
  every geometry type (take the far end of a segment), or the wedge fit crashes.
- **Embedded wires add burning area, they are not voids.** `embedded_wire` is a
  bore plus inert radial wires: the port is the plain bore, and
  `analysis._wire_area` adds `N_w*2*pi*r_w*min(e, e_max)` — a term that *grows*
  with the web, which is the whole point of the family. Their cross-section is
  subtracted from the propellant area. Wire ablation/thermal effects are not
  modelled.
- **Wagon-wheel bulbs must stop short of the case wall.** Clamping them to
  `r_outer - r_bulb` puts the port exactly on the outer surface, leaving zero web
  and a degenerate burn-back; `families.build_wagon_wheel` keeps a tenth of the
  radius as web.
- **`shapely` is a hard core dependency** (`geometry/analysis.py`,
  `geometry/families.py`, `burnback/levelset.py`). It is declared in
  `pyproject.toml` core deps — keep it there; a missing shapely breaks the
  geometry channel that everything else builds on.
- **`inverse_convergence`** is judged on the achieved objective residual, not the
  optimiser's `success` flag (Nelder-Mead reports `success=False` on simplex
  shrink even when the physical targets are met).
- **`inverse_design` is coarse global search plus polish.** The differential
  evolution population only has to land in the right basin (`max_iter=25`), then
  the same `_refine_synthesis` polish the forward design uses closes the loop.
  Driving the population itself to a tight tolerance cost 4x the runtime
  (finocyl 461 s -> 112 s). The inner objective screens with `fast=True`; the
  reported `inverse_convergence` residual is recomputed on the full-resolution
  result, so it reads ~1e-5 (the screening model alone would flatter it to
  1e-13). Never report the screening residual: it describes a design the user
  did not receive.
- **Compare candidates on the full model, not the screening one.** The population
  search may use `fast=True`, but the decision "is the polished point better than
  the raw one?" must use `full_objective`. Judging it on the coarse model threw
  away the correct multi-perforated point: the full model read 5000 N / 2.99 s
  while the coarse model scored it *worse* than the unpolished candidate, because
  the 48-step sweep straddles the hole-merge step. The reported result then
  stayed 2.06 % low even though the right design had been found.
  `test_inverse_design_selects_candidates_on_the_full_model` guards this.
- **The inverse design space needs a usable length range.** `inverse_design`
  cannot converge if the caller passes a `length_range` that excludes the
  required grain length: every candidate then misses thrust, the objective
  bottoms out at the bound, and the residual stays near 1e-1. Example: 5000 N /
  3 s at 160 mm needs L ≈ 0.28 m, so `(0.4, 1.2)` is infeasible while
  `(0.1, 1.2)` converges.
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

All of `tests/` passes (140 tests: unit + integration + verification +
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
- The web UI (`src/srm_open/web/`) is plain HTML/CSS/JS with no build step, so
  it must work straight from `StaticFiles`. Anything requiring npm, bundling or
  a transpile step is out of scope by design — the frozen binary ships these
  three files verbatim.
- Number inputs use `step="any"`. A fixed `step` (e.g. `min=1 step=100`) makes
  the browser's constraint validation reject perfectly legal defaults such as
  `2.5` in `min=0.01 step=0.1`, and a rejected form fails *silently*: no request
  is sent, so the server log shows nothing and the UI simply appears inert.
- `rest._FRONTEND` is derived from `__file__`, so PyInstaller must ship the
  assets at `srm_open/web` inside `_MEIPASS` (see `packaging/srm_open.spec`).
  Flattening them to `web/` makes `srm serve` print "front end assets not
  found" from the frozen binary while the source checkout still works.
- The front end never computes design values; it only renders what `/design`
  and `/inverse` return. Keep it that way so the UI, CLI and REST API cannot
  drift into two different models.
- Verifying the UI headlessly: `chromium --headless --virtual-time-budget=...`
  with `--dump-dom` executes `app.js` and the fetch calls, which is enough to
  assert on rendered metrics and SVG. Playwright is not installed in this
  container and is not required.
- `data/` is shipped twice on purpose: the repo-root copy is what scripts and
  CI regenerate, and `src/srm_open/data/` is the copy that lands in the wheel.
  `package-data` is relative to the package directory, so the root copy alone
  never gets packaged and a plain `pip install` ends up with empty databases.
  `tests/unit/test_package_data.py` compares every file; after regenerating
  schemas or the thermo DB, run `python scripts/sync_package_data.py`.
- `repo_root()` falls back to the package directory (not just the checkout) so
  a wheel install resolves `data/...` correctly. Without that fallback the
  paths resolve against the caller's cwd and `srm` reports empty databases.

## Boundaries

Research / teaching / non-commercial R&D only. No weaponisation. Results are
not engineering deliverables and must be independently validated.

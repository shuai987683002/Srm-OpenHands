# Software bill of materials (SBOM) and license inventory

Runtime dependencies are intentionally light so the core engine runs offline.
Optional dependencies add visualisation, CAD export, external thermochemistry
and optimisation. All listed licenses are permissive (Apache-2.0 / BSD / MIT)
and compatible with this project's Apache-2.0 license.

## Runtime (required)

| Package | Version (tested) | License | Used for |
|---|---|---|---|
| numpy | 2.5.5 | BSD-3-Clause | arrays, integration |
| scipy | 1.18.1 | BSD-3-Clause | ODE solver, optimisation |
| pydantic | 2.14.3 | MIT | data models / validation |
| PyYAML | 6.0.3 | MIT | config, database, project files |
| shapely | 2.2.0 | BSD-3-Clause | port polygons, Minkowski burn-back |

> `shapely` is listed as required because the geometric burn-back channel (the
> default) needs it. The level-set channel alone does not.

## Optional

| Package | Version (tested) | License | Used for |
|---|---|---|---|
| matplotlib | 3.11.2 | PSF-based (BSD-compatible) | report plots |
| cadquery | 2.8.0 | Apache-2.0 | STEP export |
| scikit-image | 0.26.0 | BSD-3-Clause | level-set helpers |
| cantera | 3.2.0 | BSD-3-Clause | reference thermo (tests only) |
| rocketcea | — | Apache-2.0 | optional thermo backend |
| pymoo | — | Apache-2.0 | optional multi-objective optimiser |
| pytest | 9.2.0 | MIT | test runner |
| ruff | — | MIT | linter |

## Data

See `data_registry/data_sources.md` for the provenance of every shipped dataset.

## Reproducibility

- Pin exact versions in a lock file (`uv.lock` recommended) for byte-identical
  environments.
- `DesignResult.provenance.dependency_versions` records the versions used for a
  given result.
- Run `pip-licenses` (or `reuse`) in CI to regenerate this inventory.

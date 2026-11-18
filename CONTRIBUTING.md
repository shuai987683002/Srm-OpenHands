# Contributing to Srm-Open

Thank you for helping build an open, verifiable solid rocket motor design
toolkit for research, teaching and non-commercial R&D.

## Scope and boundaries

Contributions must stay within the project's scope: single-chamber, single-stage
solid motors, textbook-level zero-dimensional internal ballistics, engineering
structural estimates, and teaching-grade visualisation. Do **not** submit
weaponisation work, real classified/controlled propellant formulations, or data
whose redistribution is restricted.

## Getting started

```bash
git clone <repo> && cd Srm-OpenHands
pip install -e ".[dev,all]"
PYTHONPATH=src python -m pytest tests -q
```

## Contribution rules

1. **Every new model needs a verification case.** A new burn-rate law, burn-area
   channel, thermochemistry backend or structural model must come with a test in
   `tests/verification/` that compares it against an analytical result, a
   reference code, or published data, and records the residual.
2. **Keep the two burn-area channels independent.** The geometric (shapely) and
   level-set channels must not share code paths, so their agreement stays a
   meaningful check.
3. **No invented numbers.** Propellant coefficients, thermochemistry data and
   material properties are user- or database-supplied. If you add a database
   entry, cite its public source in `data_registry/`.
4. **Provenance is mandatory.** Any result-producing change must keep
   `DesignResult.provenance` populated (version, model choices, seed).
5. **Style.** Line length 100, `ruff` clean, type hints on public functions.
   Comments explain *why*, not *what*.
6. **Sign-off (DCO).** Add a `Signed-off-by: Name <email>` line to each commit
   (`git commit -s`).

## Data compliance

- Do not commit export-controlled, proprietary or personal data.
- Record every dataset's origin in `data_registry/data_sources.md`.
- Third-party dependencies must be license-compatible with Apache-2.0; record
  them in `data_registry/sbom.md`.

## Pull requests

- One logical change per PR, with a description of the physics and the tests.
- Run the full suite (`pytest tests`) before opening the PR.
- Keep the PR title and purpose stable; push new commits rather than rewriting.

## Code of conduct

Be respectful and constructive. This is a shared scientific resource.

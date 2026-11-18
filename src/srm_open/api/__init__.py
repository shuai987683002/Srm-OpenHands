"""High-level API and CLI for Srm-Open."""

from .facade import (
    design_forward,
    design_from_geometry,
    design_from_template,
    evaluate_propellant_thermo,
    get_propellant,
    list_propellants,
    list_templates,
    make_grain,
)

__all__ = [
    "design_forward", "design_from_geometry", "design_from_template", "evaluate_propellant_thermo",
    "get_propellant", "list_propellants", "list_templates", "make_grain",
]

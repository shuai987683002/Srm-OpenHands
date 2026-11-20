"""Grain geometry: families, 2-D sketch analysis, 3-D solids."""

from .analysis import (
    SegmentAnalysis,
    analyze_segment,
    burning_area,
    burning_perimeter,
    initial_burning_area,
    port_polygon,
    web_thickness,
)
from .families import FAMILY_PARAMS, build_port, register_family
from .sketch import (
    geometry_backend_available,
    grain_to_step,
    grain_to_stl,
    segment_outline,
    segment_outlines,
    sketch_payload,
)

__all__ = [
    "SegmentAnalysis", "analyze_segment", "burning_area", "burning_perimeter",
    "initial_burning_area", "port_polygon", "web_thickness",
    "FAMILY_PARAMS", "build_port", "register_family",
    "geometry_backend_available", "grain_to_step", "grain_to_stl",
    "segment_outline", "segment_outlines", "sketch_payload",
]

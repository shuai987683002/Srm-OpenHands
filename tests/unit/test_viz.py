"""3-D preview tests (optional ``viz`` extra).

The point of these is not "a file was created" — a blank PNG passes that. Each
test checks the image has actual drawn content, and that every grain family can
be rendered, since a family that raises here would break the report for that
design.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("matplotlib", reason="3-D previews need the optional 'viz' extra")

import matplotlib.image as mpimg  # noqa: E402

from srm_open.io.templates import TemplateLibrary  # noqa: E402
from srm_open.viz import burn_modes_summary, plot_burnback_3d, plot_grain_3d  # noqa: E402


def _drawn_fraction(path) -> float:
    rgb = mpimg.imread(path)[..., :3]
    return float((rgb < 0.98).any(axis=2).mean())


def test_grain_plot_has_content(tmp_path):
    grain = TemplateLibrary.load_default().geometry("star_6_point")
    out = plot_grain_3d(grain, tmp_path / "g.png")
    assert out.exists() and out.stat().st_size > 10_000
    assert _drawn_fraction(out) > 0.05, "image is essentially blank"


def test_burnback_plot_shows_regression(tmp_path):
    from srm_open.models.results import BurnbackResult

    grain = TemplateLibrary.load_default().geometry("tube_simple")
    burnback = BurnbackResult(web_thickness=0.02, burn_distance=[0.0, 0.02])

    out = plot_burnback_3d(grain, burnback, tmp_path / "b.png")
    assert out.exists()
    assert _drawn_fraction(out) > 0.05


def test_burnback_plot_without_result_still_renders(tmp_path):
    """Inverse designs may have no burnback yet; the report must not break."""
    grain = TemplateLibrary.load_default().geometry("tube_simple")
    out = plot_burnback_3d(grain, None, tmp_path / "b0.png")
    assert out.exists() and _drawn_fraction(out) > 0.05


def test_all_templates_render(tmp_path):
    """Every shipped family must survive the report path."""
    lib = TemplateLibrary.load_default()
    for tid in lib.ids():
        grain = lib.geometry(tid)
        out = plot_grain_3d(grain, tmp_path / f"{tid}.png")
        assert out.exists(), tid
        assert _drawn_fraction(out) > 0.01, f"{tid} rendered blank"


def test_burn_mode_summary_covers_each_segment():
    grain = TemplateLibrary.load_default().geometry("two_segment_booster")
    lines = burn_modes_summary(grain)
    assert len(lines) == len(grain.segments)
    assert all(line.startswith("segment ") for line in lines)


def test_missing_matplotlib_message_is_actionable(monkeypatch):
    """If the extra is absent the error must name the install command."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("matplotlib"):
            raise ImportError("simulated absence")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    from srm_open.viz import _require_matplotlib

    with pytest.raises(ImportError, match=r"\[viz\]"):
        _require_matplotlib()


def test_numpy_arrays_are_finite():
    """Guard against NaN coordinates leaking into the poly collections."""
    grain = TemplateLibrary.load_default().geometry("finocyl_6")
    from srm_open.viz import _port_ring

    ring = _port_ring(grain.segments[0])
    assert np.isfinite(ring).all()
    assert ring.shape[0] >= 4

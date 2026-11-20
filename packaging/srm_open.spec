# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build for the ``srm`` command-line tool.

The frozen binary must run with no Python installed and no checkout on disk, so
the propellant / template / thermo databases and the default configuration are
bundled as data files and located at run time through ``sys._MEIPASS`` (see
``srm_open.config.repo_root``).

Build with::

    pyinstaller packaging/srm_open.spec --noconfirm

The result is ``dist/srm`` (``dist/srm.exe`` on Windows).
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent  # repository root

# Bundled read-only data.  ``data/`` holds the YAML databases and the JSON
# schemas; ``configs/`` holds the layered default configuration.
datas = [
    (str(ROOT / "data"), "data"),
    (str(ROOT / "configs"), "configs"),
    # mirrors the package layout so rest._FRONTEND resolves inside _MEIPASS
    (str(ROOT / "src" / "srm_open" / "web"), "srm_open/web"),
]

hiddenimports = [
    # The CLI dispatches to these lazily, so static analysis can miss them.
    "srm_open.api.rest",
    "srm_open.report.report",
    "srm_open.geometry.sketch",
]

# Optional backends: collect them when installed, skip silently when not.  The
# core engine never requires them.
for optional in ("fastapi", "uvicorn", "matplotlib", "pymoo"):
    try:
        hiddenimports += collect_submodules(optional)
    except Exception:  # pragma: no cover - depends on the build environment
        pass

a = Analysis(
    [str(ROOT / "packaging" / "srm_entry.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "PyQt5", "PySide2", "IPython", "pytest",
              "vtk", "cadquery", "OCP", "pyvista", "cantera", "rocketcea",
              "torch", "jax"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="srm",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

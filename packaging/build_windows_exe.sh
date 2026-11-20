#!/usr/bin/env bash
# Build the standalone Windows ``srm.exe`` from this Linux checkout.
#
# PyInstaller cannot cross-compile, so a Windows CPython runs under Wine inside a
# container.  Windows wheels are fetched here (on the host, where TLS works) and
# handed to the container as an offline wheelhouse.
#
#   ./packaging/build_windows_exe.sh            # -> release/srm.exe
#
# Requirements: docker (with daemon access), python3 with pip, ~2 GB free disk.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

image=srm-win
wheelhouse=packaging/wheelhouse
out=release

echo "==> preparing win_amd64 wheelhouse"
rm -rf "$wheelhouse"
python3 -m pip download --only-binary=:all: \
    --platform win_amd64 --python-version 3.11 --implementation cp \
    --dest "$wheelhouse" \
    numpy scipy shapely pyyaml matplotlib fastapi uvicorn pymoo scikit-image pyinstaller
# ``pefile``/``pywin32-ctypes`` ship as platform-independent wheels but are
# dropped by the ``--platform`` filter, so fetch them without dependency checks.
python3 -m pip download --no-deps --dest "$wheelhouse" pefile pywin32-ctypes

echo "==> building image"
docker build -f packaging/Dockerfile.windows -t "$image" .

echo "==> extracting binary"
mkdir -p "$out"
rm -f "$out/srm.exe"
docker run --rm -v "$root/$out:/out" "$image"

echo "==> done: $out/srm.exe"
ls -lh "$out/srm.exe"

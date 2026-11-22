#!/usr/bin/env bash
# Build the air-gapped bundle ON A CONNECTED MACHINE, then carry it across.
#
#   ./deploy/scripts/build_offline_bundle.sh --out /media/usb/srm-llm-bundle \
#       --llama-tag b12627 --tier T1
#
# The bundle is self-describing: MANIFEST.json lists every file with its sha256,
# so the receiving side can prove nothing was swapped in transit and nothing is
# missing before a single byte is installed.
set -euo pipefail

OUT=""; LLAMA_TAG="b12627"; TIER="T1"; WITH_WHEELS=1
while [[ $# -gt 0 ]]; do
    case "$1" in
        --out) OUT="$2"; shift 2 ;;
        --llama-tag) LLAMA_TAG="$2"; shift 2 ;;
        --tier) TIER="$2"; shift 2 ;;
        --no-wheels) WITH_WHEELS=0; shift ;;
        *) echo "unknown arg: $1" >&2; exit 64 ;;
    esac
done
[[ -n "$OUT" ]] || { echo "usage: $0 --out DIR [--llama-tag b12627] [--tier T1]" >&2; exit 64; }

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
mkdir -p "$OUT"/{models,bin,wheels,config,docs,scripts}

echo "==> 1/6 llama.cpp binaries ($LLAMA_TAG, CPU + CUDA builds)"
LLAMA_URL="https://github.com/ggml-org/llama.cpp/releases/download/${LLAMA_TAG}/llama-${LLAMA_TAG}-bin-ubuntu-x64.tar.gz"
CUDA_URL="https://github.com/ggml-org/llama.cpp/releases/download/${LLAMA_TAG}/cudart-llama-${LLAMA_TAG}-bin-ubuntu-cuda-12.8-x64.tar.gz"
curl -fL --retry 3 -o "$OUT/bin/llama-cpu.tar.gz" "$LLAMA_URL"
# The CUDA runtime tarball is optional: skip cleanly when the tag lacks it.
curl -fL --retry 3 -o "$OUT/bin/llama-cudart.tar.gz" "$CUDA_URL" || {
    echo "    (no cudart asset for $LLAMA_TAG, skipping)"; rm -f "$OUT/bin/llama-cudart.tar.gz"; }

echo "==> 2/6 model weights (per deploy/config/models.yaml)"
python3 "$REPO_ROOT/deploy/scripts/fetch_models.py" \
    --manifest "$REPO_ROOT/deploy/config/models.yaml" --dest "$OUT/models"

if [[ "$WITH_WHEELS" == "1" ]]; then
    echo "==> 3/6 python wheels (binary only, for the target's python version)"
    # --only-binary=:all: keeps source tarballs out: building from source on an
    # air-gapped host fails when a header is missing, and the failure is obscure.
    python3 -m pip download --only-binary=:all: --dest "$OUT/wheels" \
        -r "$REPO_ROOT/deploy/config/requirements-offline.txt"
else
    echo "==> 3/6 wheels skipped (--no-wheels)"
fi

echo "==> 4/6 configs and scripts"
cp "$REPO_ROOT/deploy/config/"*.yaml "$OUT/config/"
python3 "$REPO_ROOT/deploy/scripts/render_env.py" \
    --config "$REPO_ROOT/deploy/config/server.yaml" --tier "$TIER" \
    --out "$OUT/config/server.env"
cp -r "$REPO_ROOT/deploy/scripts" "$OUT/scripts/"
cp -r "$REPO_ROOT/deploy/systemd" "$OUT/"
cp -r "$REPO_ROOT/deploy/monitoring" "$OUT/" 2>/dev/null || true
cp "$REPO_ROOT/docs/llm_agent/离线部署实施手册.md" "$OUT/docs/" 2>/dev/null || true

echo "==> 5/6 install runbook"
cat > "$OUT/INSTALL.txt" <<'RUNBOOK'
SRM-LLM air-gapped install
==========================
1. verify the bundle before touching the system:
     python3 scripts/verify_bundle.py --bundle . --check
   A single FAIL means stop: do not install a partial bundle.

2. unpack the binaries:
     mkdir -p /opt/llama.cpp && tar xzf bin/llama-cpu.tar.gz -C /opt/llama.cpp --strip-components=1
     [ -f bin/llama-cudart.tar.gz ] && tar xzf bin/llama-cudart.tar.gz -C /opt/llama.cpp --strip-components=1

3. place the models (keep the names exactly as in config/models.yaml):
     install -d -o srmllm -g srmllm /srv/srm-llm/models
     cp models/*.gguf /srv/srm-llm/models/

4. python environment, fully offline:
     python3 -m venv /opt/srm-llm/venv
     /opt/srm-llm/venv/bin/pip install --no-index --find-links wheels -r config/requirements-offline.txt

5. config:
     install -d /etc/srm-llm && cp config/*.yaml config/server.env /etc/srm-llm/
     install -m 0600 /dev/null /etc/srm-llm/api-keys.txt   # then add one key per line

6. service:
     cp srm-llm.service /etc/systemd/system/ && systemctl daemon-reload
     systemctl enable --now srm-llm
     scripts/healthcheck.py --port 8080 --ready
RUNBOOK

echo "==> 6/6 MANIFEST.json"
python3 "$REPO_ROOT/deploy/scripts/verify_bundle.py" --bundle "$OUT" --write

du -sh "$OUT"; echo "bundle ready: $OUT"

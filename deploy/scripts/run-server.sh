#!/usr/bin/env bash
# 启动 llama-server。所有参数来自 /etc/srm-llm/server.env（由 render_env.py 生成）。
# 设计要点：
#   1. 启动前先校验模型哈希 —— 哈希不符直接退出，不启动半坏的模型
#   2. exec 替换 shell，让 systemd 直接管理 llama-server 的 PID 与信号
#   3. 端口占用先探测，给出可读错误而不是让 llama-server 自己 panic
set -euo pipefail

ENV_FILE="${SRM_LLM_ENV:-/etc/srm-llm/server.env}"
# shellcheck disable=SC1090
[[ -r "$ENV_FILE" ]] || { echo "FATAL: cannot read $ENV_FILE" >&2; exit 78; }
set -a; . "$ENV_FILE"; set +a

: "${SRM_MODEL_PATH:?SRM_MODEL_PATH is not set}"
: "${SRM_PORT:?SRM_PORT is not set}"

BIN="${SRM_LLAMA_BIN:-/opt/llama.cpp/llama-server}"
[[ -x "$BIN" ]] || { echo "FATAL: $BIN is not executable" >&2; exit 78; }

# Repo root, so the script works from a checkout as well as from /opt/srm-open.
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$HERE/../.." && pwd)"

# The verifier needs PyYAML, which the system python3 may not have on a minimal
# offline host. Point SRM_PYTHON at the venv that has it.
PY="${SRM_PYTHON:-python3}"
"$PY" -c 'import yaml' 2>/dev/null || {
    echo "FATAL: $PY cannot import yaml (set SRM_PYTHON to the venv python)" >&2
    exit 78
}

# --- 1. 模型完整性 -----------------------------------------------------------
# 大文件哈希要几十秒，所以放在启动路径上而不是每次请求；不通过就不启动。
if [[ "${SRM_SKIP_HASH:-0}" != "1" ]]; then
    "$PY" "${SRM_VERIFY_SCRIPT:-$REPO_ROOT/deploy/scripts/verify_models.py}" \
        --manifest "${SRM_MANIFEST:-/etc/srm-llm/models.yaml}" \
        --root "${SRM_MODEL_ROOT:-/srv/srm-llm}" \
        --only "$(basename "$SRM_MODEL_PATH")" \
        || { echo "FATAL: model integrity check failed" >&2; exit 65; }
fi

# --- 2. 端口占用 -------------------------------------------------------------
if (exec 3<>"/dev/tcp/127.0.0.1/$SRM_PORT") 2>/dev/null; then
    echo "FATAL: port $SRM_PORT already in use" >&2
    exit 98
fi

# --- 3. 组装参数 -------------------------------------------------------------
args=(
    --model "$SRM_MODEL_PATH"
    --host "$SRM_HOST"
    --port "$SRM_PORT"
    --alias "${SRM_ALIAS:-local}"
    --ctx-size "$SRM_CTX"
    --parallel "$SRM_PARALLEL"
    --n-gpu-layers "$SRM_GPU_LAYERS"
    --threads "$SRM_THREADS"
    --threads-http "$SRM_THREADS_HTTP"
    --flash-attn "$SRM_FLASH_ATTN"
    --cache-type-k "$SRM_CTK"
    --cache-type-v "$SRM_CTV"
    --timeout "${SRM_TIMEOUT:-600}"
    --log-file "${SRM_LOG_FILE:-/var/log/srm-llm/llama-server.log}"
)
[[ "${SRM_METRICS:-0}" == "1" ]]   && args+=(--metrics)
[[ "${SRM_CONT_BATCH:-1}" == "1" ]] && args+=(--cont-batching)
[[ "${SRM_JINJA:-1}" == "1" ]]     && args+=(--jinja)
[[ "${SRM_MLOCK:-0}" == "1" ]]     && args+=(--mlock)
[[ -n "${SRM_SLOT_DIR:-}" ]]       && args+=(--slot-save-path "$SRM_SLOT_DIR")
[[ -r "${SRM_API_KEY_FILE:-/nonexistent}" ]] && args+=(--api-key-file "$SRM_API_KEY_FILE")

echo "starting: $BIN ${args[*]}"
exec "$BIN" "${args[@]}"

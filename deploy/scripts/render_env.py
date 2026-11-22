#!/usr/bin/env python3
"""Render deploy/config/server.yaml into a systemd EnvironmentFile.

Keeping the parameters in YAML and generating the env file means the running
service and the documentation read the same numbers, and a diff of the rendered
file shows exactly what changed at the next restart.

    python3 deploy/scripts/render_env.py --config deploy/config/server.yaml \
        --tier T1 --out /etc/srm-llm/server.env

The tier block overrides ctx/parallel/overhead, so moving a machine from T1 to
T1.5 is one flag rather than five hand edits.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    print("FATAL: PyYAML is required", file=sys.stderr)
    raise SystemExit(66) from None

# yaml key -> env var. Explicit so a typo in the YAML surfaces as a KeyError
# here rather than as a silently-ignored server flag.
MAP = {
    ("server", "host"): "SRM_HOST",
    ("server", "port"): "SRM_PORT",
    ("server", "alias"): "SRM_ALIAS",
    ("server", "api_key_file"): "SRM_API_KEY_FILE",
    ("model", "path"): "SRM_MODEL_PATH",
    ("model", "ctx_size"): "SRM_CTX",
    ("model", "parallel"): "SRM_PARALLEL",
    ("model", "gpu_layers"): "SRM_GPU_LAYERS",
    ("model", "flash_attn"): "SRM_FLASH_ATTN",
    ("model", "cache_type_k"): "SRM_CTK",
    ("model", "cache_type_v"): "SRM_CTV",
    ("model", "mlock"): "SRM_MLOCK",
    ("model", "jinja"): "SRM_JINJA",
    ("runtime", "threads"): "SRM_THREADS",
    ("runtime", "threads_http"): "SRM_THREADS_HTTP",
    ("runtime", "cont_batching"): "SRM_CONT_BATCH",
    ("runtime", "metrics"): "SRM_METRICS",
    ("runtime", "log_file"): "SRM_LOG_FILE",
    ("runtime", "timeout"): "SRM_TIMEOUT",
    ("runtime", "slot_save_path"): "SRM_SLOT_DIR",
}

BOOL_VARS = {"SRM_MLOCK", "SRM_JINJA", "SRM_CONT_BATCH", "SRM_METRICS"}


def to_env_value(value) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if value is None:
        return ""
    return str(value)


def render(cfg: dict, tier: str | None) -> list[str]:
    lines: list[str] = [f"# generated from server.yaml (tier={tier or 'as-written'})", "# do not edit by hand"]
    if tier:
        tiers = cfg.get("tiers") or {}
        if tier not in tiers:
            raise KeyError(f"tier {tier!r} not in {sorted(tiers)}")
        t = tiers[tier]
        # tier wins over the per-model defaults
        cfg = {**cfg, "model": {**cfg["model"], "ctx_size": t["ctx"], "parallel": t["parallel"]}}
    for (section, key), var in MAP.items():
        if section not in cfg or key not in cfg[section]:
            raise KeyError(f"missing {section}.{key}")
        val = cfg[section][key]
        if var in BOOL_VARS:
            val = to_env_value(val)
        lines.append(f"{var}={to_env_value(val)}")
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", type=Path, required=True)
    ap.add_argument("--tier", help="override ctx/parallel from the tiers block")
    ap.add_argument("--out", type=Path, help="write here (default: stdout)")
    args = ap.parse_args(argv)

    cfg = yaml.safe_load(args.config.read_text(encoding="utf-8")) or {}
    try:
        lines = render(cfg, args.tier)
    except KeyError as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 66

    text = "\n".join(lines) + "\n"
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"wrote {args.out} ({len(lines) - 2} variables)")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

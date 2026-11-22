#!/usr/bin/env python3
"""Health and load probe for llama-server. Used by monitoring and by the gate.

Two distinct questions, deliberately kept apart:

  health   -- is the server answering?        (/health, no auth needed)
  ready    -- can it actually generate?       (one tiny completion, needs auth)

A server that passes /health but cannot generate is a common failure after an
OOM kill of the KV cache: the process is up, the model is gone. The gate must
use `ready`.

    python3 deploy/scripts/healthcheck.py --port 8080 --api-key-file /etc/srm-llm/api-keys.txt
    python3 deploy/scripts/healthcheck.py --port 8080 --ready --json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

TIMEOUT = 30.0


def _get(url: str, key: str | None, timeout: float = TIMEOUT):
    req = urllib.request.Request(url)
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read()


def _post(url: str, payload: dict, key: str | None, timeout: float):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def read_key(path: str | None) -> str | None:
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line
    return None


def parse_metrics(text: str) -> dict[str, float]:
    """Pull the handful of counters the monitor cares about.

    llama.cpp exposes Prometheus text with the `llamacpp:` prefix. We keep the
    parser dumb on purpose: a new metric upstream must not break monitoring.
    """
    wanted = {
        "llamacpp:requests_processing": "requests_processing",
        "llamacpp:requests_deferred": "requests_deferred",
        "llamacpp:prompt_tokens_total": "prompt_tokens",
        "llamacpp:tokens_predicted_total": "tokens_predicted",
        "llamacpp:kv_cache_usage_ratio": "kv_usage",
        "llamacpp:kv_cache_tokens": "kv_tokens",
    }
    out: dict[str, float] = {}
    for line in text.splitlines():
        if line.startswith("#") or " " not in line:
            continue
        name, _, value = line.rpartition(" ")
        if name in wanted:
            try:
                out[wanted[name]] = float(value)
            except ValueError:
                pass
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--api-key-file")
    ap.add_argument("--api-key")
    ap.add_argument("--ready", action="store_true", help="also run one real completion")
    ap.add_argument("--concurrency", type=int, default=0, help="probe N parallel requests")
    ap.add_argument("--max-tokens", type=int, default=32)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    base = f"http://{args.host}:{args.port}"
    key = args.api_key or read_key(args.api_key_file)
    result: dict = {"base": base, "checks": {}, "ok": True}

    def record(name: str, ok: bool, **extra):
        result["checks"][name] = {"ok": ok, **extra}
        if not ok:
            result["ok"] = False

    try:
        status, body = _get(f"{base}/health", None, timeout=5)
        record("health", status == 200 and b'"ok"' in body, status=status)
    except (urllib.error.URLError, OSError) as exc:
        record("health", False, error=str(exc))

    if result["checks"].get("health", {}).get("ok"):
        try:
            status, body = _get(f"{base}/v1/models", key, timeout=10)
            models = json.loads(body).get("data", []) if status == 200 else []
            record("models", bool(models), count=len(models), ids=[m.get("id") for m in models])
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            record("models", False, error=str(exc))

        try:
            status, body = _get(f"{base}/metrics", None, timeout=10)
            parsed = parse_metrics(body.decode("utf-8", "replace"))
            result["metrics"] = parsed
        except (urllib.error.URLError, OSError) as exc:
            result["metrics"] = {"error": str(exc)}

    if args.ready and result["ok"]:
        try:
            t0 = time.perf_counter()
            payload = {
                "messages": [{"role": "user", "content": "Reply with the single word: ok"}],
                "max_tokens": args.max_tokens,
                "temperature": 0.0,
            }
            data = _post(f"{base}/v1/chat/completions", payload, key, timeout=120)
            elapsed = time.perf_counter() - t0
            text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
            record("generate", bool(text.strip()), latency_s=round(elapsed, 2), sample=text.strip()[:60])
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            record("generate", False, error=str(exc))

    if args.concurrency > 0 and result["ok"]:
        payload = {
            "messages": [{"role": "user", "content": "Count from 1 to 30."}],
            "max_tokens": 60,
            "temperature": 0.0,
        }

        def one(_i: int) -> float:
            t0 = time.perf_counter()
            _post(f"{base}/v1/chat/completions", payload, key, timeout=180)
            return time.perf_counter() - t0

        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            times = list(pool.map(one, range(args.concurrency)))
        result["concurrency"] = {
            "n": args.concurrency,
            "wall_s": round(max(times), 2),
            "p50_s": round(statistics.median(times), 2),
            "mean_s": round(statistics.fmean(times), 2),
        }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for name, check in result["checks"].items():
            print(f"[{'OK  ' if check['ok'] else 'FAIL'}] {name}: {check}")
        if "metrics" in result:
            print(f"       metrics: {result['metrics']}")
        if "concurrency" in result:
            print(f"       concurrency: {result['concurrency']}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

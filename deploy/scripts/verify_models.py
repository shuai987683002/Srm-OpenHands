#!/usr/bin/env python3
"""Verify model files against the manifest's SHA256 chain.

Runs at service start (see run-server.sh) and again in the acceptance gate.
A model that fails here must never be served: a truncated or substituted GGUF
loads fine and then answers garbage, which is the worst failure mode for a
domain assistant.

    python3 deploy/scripts/verify_models.py --manifest deploy/config/models.yaml --root /srv/srm-llm
    python3 deploy/scripts/verify_models.py ... --only Qwen3-30B-A3B-Instruct-2507-Q4_K_M.gguf
    python3 deploy/scripts/verify_models.py ... --write-sums   # fill the manifest

Exit codes: 0 all good, 65 integrity failure, 66 manifest/IO problem.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - yaml is a core dependency of srm_open
    print("FATAL: PyYAML is required", file=sys.stderr)
    raise SystemExit(66)

CHUNK = 8 * 1024 * 1024


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def gguf_magic_ok(path: Path) -> bool:
    """A GGUF file starts with the ASCII magic 'GGUF'.

    Checked separately from the hash so that a swapped-in file with a valid but
    wrong hash gives a clearer message than "mismatch".
    """
    try:
        with path.open("rb") as fh:
            return fh.read(4) == b"GGUF"
    except OSError:
        return False


def load_manifest(path: Path) -> list[dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    models = data.get("models") or []
    if not models:
        raise ValueError(f"{path} has no 'models'")
    return models


def iter_expected(models: list[dict], only: str | None):
    for model in models:
        for entry in model.get("files", []):
            if only and entry["name"] != only:
                continue
            yield model, entry


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--root", type=Path, required=True, help="directory containing models/")
    ap.add_argument("--only", help="check a single file name")
    ap.add_argument("--write-sums", action="store_true", help="compute and print sha256 for every file")
    ap.add_argument("--json", action="store_true", help="machine-readable result")
    args = ap.parse_args(argv)

    try:
        models = load_manifest(args.manifest)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 66

    results: list[dict] = []
    worst = 0
    for model, entry in iter_expected(models, args.only):
        path = args.root / "models" / entry["name"]
        row = {"model": model["id"], "file": entry["name"], "path": str(path)}
        if not path.is_file():
            row.update(status="missing")
            worst = max(worst, 65)
        else:
            size_gib = path.stat().st_size / 1024**3
            row["size_gib"] = round(size_gib, 2)
            if not gguf_magic_ok(path):
                row.update(status="not-gguf")
                worst = max(worst, 65)
            else:
                digest = sha256_of(path)
                row["sha256"] = digest
                want = (entry.get("sha256") or "").strip().lower()
                if args.write_sums:
                    row["status"] = "computed"
                elif not want:
                    # An empty hash is a manifest gap, not a pass: an unfilled
                    # manifest must fail the gate, or it silently approves anything.
                    row["status"] = "no-expected-hash"
                    worst = max(worst, 65)
                elif digest == want:
                    row["status"] = "ok"
                else:
                    row.update(status="mismatch", expected=want)
                    worst = max(worst, 65)
        results.append(row)

    if args.write_sums:
        for row in results:
            if "sha256" in row:
                print(f'{row["file"]}: {row["sha256"]}')
    elif args.json:
        print(json.dumps({"ok": worst == 0, "results": results}, ensure_ascii=False, indent=2))
    else:
        for row in results:
            mark = {"ok": "OK  ", "computed": "SUMS"}.get(row["status"], "FAIL")
            extra = f" ({row['size_gib']} GiB)" if "size_gib" in row else ""
            print(f"[{mark}] {row['file']}{extra} -> {row['status']}")

    return worst


if __name__ == "__main__":
    raise SystemExit(main())

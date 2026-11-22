#!/usr/bin/env python3
"""Fetch model files listed in models.yaml and record their sha256.

Runs on the CONNECTED staging machine. The air-gapped host never reaches the
network; it only ever sees the bundle.

    python3 deploy/scripts/fetch_models.py --manifest deploy/config/models.yaml --dest /media/usb/b/models

Files are written atomically (`.part` then rename) so an interrupted transfer
leaves no half-file that a later hash check would have to explain.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.error
import urllib.request
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    print("FATAL: PyYAML is required", file=sys.stderr)
    raise SystemExit(66) from None

HF = "https://huggingface.co/{repo}/resolve/main/{name}"


def download(url: str, dest: Path) -> tuple[str, int]:
    tmp = dest.with_suffix(dest.suffix + ".part")
    h = hashlib.sha256()
    size = 0
    with urllib.request.urlopen(url, timeout=120) as resp, tmp.open("wb") as fh:
        while True:
            block = resp.read(8 * 1024 * 1024)
            if not block:
                break
            h.update(block)
            size += len(block)
            fh.write(block)
    tmp.replace(dest)
    return h.hexdigest(), size


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--dest", type=Path, required=True)
    ap.add_argument("--only", help="fetch a single model id")
    args = ap.parse_args(argv)

    models = (yaml.safe_load(args.manifest.read_text(encoding="utf-8")) or {}).get("models", [])
    if not models:
        print("FATAL: manifest has no models", file=sys.stderr)
        return 66
    args.dest.mkdir(parents=True, exist_ok=True)

    failures = 0
    for model in models:
        if args.only and model["id"] != args.only:
            continue
        print(f"== {model['id']} ({model['role']})")
        for entry in model["files"]:
            dest = args.dest / entry["name"]
            if dest.is_file():
                print(f"   skip (present): {entry['name']}")
                continue
            url = HF.format(repo=model["repo"], name=entry["name"])
            try:
                digest, size = download(url, dest)
            except (urllib.error.URLError, OSError) as exc:
                print(f"   FAIL {entry['name']}: {exc}", file=sys.stderr)
                failures += 1
                continue
            gib = size / 1024**3
            print(f"   got {entry['name']} {gib:.2f} GiB sha256={digest}")
            want = (entry.get("sha256") or "").strip().lower()
            if want and want != digest:
                print(f"   !! hash mismatch: manifest says {want}", file=sys.stderr)
                failures += 1
            elif not want:
                print(f"   (manifest has no hash yet; paste the line above into {args.manifest})")
    return 0 if failures == 0 else 65


if __name__ == "__main__":
    raise SystemExit(main())

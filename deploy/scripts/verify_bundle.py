#!/usr/bin/env python3
"""Write and check the bundle manifest (the transport integrity gate).

    # on the connected machine, after staging the files
    python3 deploy/scripts/verify_bundle.py --bundle /media/usb/srm-llm-bundle --write

    # on the air-gapped machine, before installing anything
    python3 deploy/scripts/verify_bundle.py --bundle /media/usb/srm-llm-bundle --check

`--write` records size and sha256 for every file except MANIFEST.json itself.
`--check` re-hashes and reports added, removed and changed files separately:
"changed" is tampering or corruption, "added" is usually an operator leaving a
file behind, and they need different responses.

Exit codes: 0 clean, 65 mismatch, 66 missing manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

CHUNK = 8 * 1024 * 1024
MANIFEST = "MANIFEST.json"
# Large weights dominate hashing time; a check that takes an hour will be skipped
# by operators, so keep it to the artifacts whose integrity actually matters.
SKIP_DIRS = {"__pycache__", ".git"}


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def walk(bundle: Path) -> list[Path]:
    out = []
    for path in sorted(bundle.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.name == MANIFEST and path.parent == bundle:
            continue
        out.append(path)
    return out


def write(bundle: Path) -> int:
    entries = []
    for path in walk(bundle):
        entries.append(
            {
                "path": str(path.relative_to(bundle)),
                "size": path.stat().st_size,
                "sha256": sha256_of(path),
            }
        )
    payload = {"bundle": bundle.name, "count": len(entries), "files": entries}
    (bundle / MANIFEST).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    total = sum(e["size"] for e in entries)
    print(f"wrote {MANIFEST}: {len(entries)} files, {total / 1024**3:.2f} GiB")
    return 0


def check(bundle: Path) -> int:
    manifest_path = bundle / MANIFEST
    if not manifest_path.is_file():
        print(f"FATAL: {manifest_path} not found", file=sys.stderr)
        return 66
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = {e["path"]: e for e in payload["files"]}

    present = {str(p.relative_to(bundle)): p for p in walk(bundle)}
    missing = sorted(set(expected) - set(present))
    added = sorted(set(present) - set(expected))
    changed = []
    for rel, entry in expected.items():
        path = present.get(rel)
        if path is None:
            continue
        if path.stat().st_size != entry["size"]:
            changed.append((rel, "size"))
            continue
        if sha256_of(path) != entry["sha256"]:
            changed.append((rel, "sha256"))

    for rel in missing:
        print(f"[MISSING] {rel}")
    for rel, why in changed:
        print(f"[CHANGED] {rel} ({why})")
    for rel in added:
        print(f"[EXTRA  ] {rel}")

    # An unexpected file in the bundle is a failure, not a warning: on a
    # tampered carrier the planted payload is exactly the file nobody expects.
    ok = not missing and not changed and not added
    print(f"{'OK' if ok else 'FAIL'}: {len(expected)} expected, "
          f"{len(missing)} missing, {len(changed)} changed, {len(added)} extra")
    return 0 if ok else 65


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", type=Path, required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--write", action="store_true")
    g.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    if not args.bundle.is_dir():
        print(f"FATAL: {args.bundle} is not a directory", file=sys.stderr)
        return 66
    return write(args.bundle) if args.write else check(args.bundle)


if __name__ == "__main__":
    raise SystemExit(main())

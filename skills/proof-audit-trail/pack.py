#!/usr/bin/env python3
"""Package a finished Proof run into a hash-stamped evidence folder. Offline.

Standard library only, no network. It copies the whole proof/ ledger and every
evidence file into a timestamped folder, writes a manifest with the SHA-256 of each
file, and records the computed verdict at the top, so a reviewer can trust that the
report and the evidence behind it have not changed since the run.

    python3 pack.py build --dir proof --out proof/audit
    python3 pack.py verify proof/audit/run-2026-10-30-120000   # re-check the hashes
"""

import argparse
import datetime
import hashlib
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402

SKILL = "proof-audit-trail"


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _iter_files(root):
    for base, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d != "audit")  # never pack the audit folder into itself
        for f in sorted(files):
            yield os.path.join(base, f)


def cmd_build(a):
    src = ledger.root(a.dir)
    if not os.path.isdir(src):
        raise SystemExit("no %s folder to package. Run a Proof pass first." % a.dir)
    stamp = datetime.datetime.now().strftime("run-%Y-%m-%d-%H%M%S")
    out_base = a.out or os.path.join(src, "audit")
    out = os.path.join(out_base, stamp)
    os.makedirs(out, exist_ok=True)

    try:
        source_label = os.path.relpath(src)
    except ValueError:
        source_label = src  # different drive on Windows; keep the absolute path
    manifest = {"generated": ledger.now(), "source": source_label, "files": []}
    # Pull the verdict in if the report ran.
    verdict_path = os.path.join(src, "verdict.json")
    if os.path.exists(verdict_path):
        try:
            with open(verdict_path, encoding="utf-8") as fh:
                manifest["verdict"] = json.load(fh).get("verdict")
        except (OSError, ValueError):
            pass

    copied = 0
    for path in _iter_files(src):
        rel = os.path.relpath(path, src).replace(os.sep, "/")
        if rel.startswith("audit/"):
            continue
        dest = os.path.join(out, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(path, dest)
        manifest["files"].append({"path": rel, "sha256": _sha256(path), "bytes": os.path.getsize(path)})
        copied += 1

    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    # A short human summary at the top of the pack.
    with open(os.path.join(out, "README.txt"), "w", encoding="utf-8") as fh:
        fh.write("Proof audit pack\n")
        fh.write("Generated: %s\n" % manifest["generated"])
        fh.write("Verdict: %s\n" % manifest.get("verdict", "(report not run)"))
        fh.write("Files: %d, each hashed in manifest.json\n" % copied)
        fh.write("Verify later with: pack.py verify %s\n" % out)
    print("%s packed %d file(s) into %s" % (ledger.OK, copied, out))
    print("Verdict in the pack: %s" % manifest.get("verdict", "(report not run; run /proof-report first for a verdict)"))
    return 0


def cmd_verify(a):
    manifest_path = os.path.join(a.pack, "manifest.json")
    if not os.path.exists(manifest_path):
        raise SystemExit("no manifest.json in %s" % a.pack)
    with open(manifest_path, encoding="utf-8") as fh:
        manifest = json.load(fh)
    changed, missing = [], []
    for entry in manifest["files"]:
        path = os.path.join(a.pack, entry["path"])
        if not os.path.isfile(path):
            missing.append(entry["path"])
        elif _sha256(path) != entry["sha256"]:
            changed.append(entry["path"])
    ok = not changed and not missing
    if ok:
        print("%s all %d file(s) match the manifest. The pack is intact." % (ledger.OK, len(manifest["files"])))
    else:
        for p in missing:
            print("%s missing: %s" % (ledger.BAD, p))
        for p in changed:
            print("%s changed since packing: %s" % (ledger.BAD, p))
    return 0 if ok else 1


def main(argv=None):
    ledger.console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="package the run with a hash manifest")
    b.add_argument("--dir", default="proof")
    b.add_argument("--out", help="where to write the pack (default <dir>/audit)")
    b.set_defaults(func=cmd_build)
    v = sub.add_parser("verify", help="re-check a pack against its manifest")
    v.add_argument("pack", help="a run folder created by build")
    v.set_defaults(func=cmd_verify)
    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())

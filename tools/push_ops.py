#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Portable incremental sync of ops/ into a GitHub repo via the REST Git Data API.

Why REST instead of ``git push``: on some networks the git HTTPS transport
fails behind a proxy (``CONNECT tunnel failed 502``) while api.github.com over
HTTPS works. This script needs no git binary for the write path, only a token.

Semantics:
  * compare the local git blob SHA1 of every ops/ file with the remote one;
    upload only NEW / CHANGED files, skip identical ones (SAME).
  * build the tree with base_tree, so nothing outside ops/ is touched.
  * never delete a remote file that has no local counterpart.
  * --dry-run prints the change set without writing anything.

Credentials (never written to disk, never printed):
  1. GITHUB_TOKEN environment variable, if set;
  2. otherwise ``git credential fill`` for host github.com (OS credential store).

Configuration (CLI flag > environment variable > default):
  --owner / KINGO_GITHUB_OWNER       default 07xh
  --repo  / KINGO_GITHUB_REPO        default kingo-live
  --branch/ KINGO_GITHUB_BRANCH      default main
  --ops-dir / KINGO_OPS_DIR          default <repo root>/ops
  --api   / KINGO_GITHUB_API         default https://api.github.com

Usage from the repository root:
  python tools/push_ops.py --dry-run
  python tools/push_ops.py --message "ops: <what changed>"
  python tools/push_ops.py --ops-dir /path/to/ops --owner me --repo myrepo
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXTS = {".py", ".md", ".json"}
DEFAULT_MESSAGE = "ops: incremental sync"


def credential() -> str:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        return token
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")
    p = subprocess.run(
        ["git", "credential", "fill"],
        input=b"protocol=https\nhost=github.com\n\n",
        capture_output=True, env=env, timeout=30,
    )
    if p.returncode != 0:
        raise RuntimeError("git credential fill failed: "
                           + p.stderr.decode("utf-8", "replace")[:200])
    creds = dict(line.split("=", 1) for line in
                 p.stdout.decode("utf-8", "replace").splitlines() if "=" in line)
    if not creds.get("password"):
        raise RuntimeError("no token: set GITHUB_TOKEN or configure "
                           "the git credential store for github.com")
    return creds["password"]


class API:
    def __init__(self, token: str, api_root: str):
        self.root = api_root.rstrip("/")
        self.headers = {
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "User-Agent": "kingo-ops-sync/2.0",
            "Content-Type": "application/json",
        }

    def call(self, method: str, path: str, data=None):
        body = json.dumps(data).encode("utf-8") if data is not None else None
        req = urllib.request.Request(self.root + path, data=body,
                                     method=method, headers=self.headers)
        try:
            with urllib.request.urlopen(req, timeout=40) as resp:
                raw = resp.read().decode("utf-8")
                return resp.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            raise RuntimeError(f"{method} {path} -> {e.code}: {detail}") from None


def blob_sha(raw: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()


def local_files(ops_dir: Path) -> list[str]:
    if not ops_dir.is_dir():
        raise RuntimeError(f"ops dir not found: {ops_dir}")
    return sorted(
        f.name for f in ops_dir.iterdir()
        if f.is_file() and not f.name.startswith(".")
        and f.suffix.lower() in EXTS
    )


def remote_sha(api: API, base: str, branch: str, name: str) -> str:
    q = urllib.parse.quote(name)
    try:
        _, item = api.call("GET", f"{base}/contents/ops/{q}?ref={branch}")
    except RuntimeError as exc:
        if "-> 404:" in str(exc):
            return ""
        raise
    return str(item.get("sha", ""))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Portable incremental ops/ sync")
    ap.add_argument("--owner", default=os.environ.get("KINGO_GITHUB_OWNER", "07xh"))
    ap.add_argument("--repo", default=os.environ.get("KINGO_GITHUB_REPO", "kingo-live"))
    ap.add_argument("--branch", default=os.environ.get("KINGO_GITHUB_BRANCH", "main"))
    ap.add_argument("--ops-dir", default=os.environ.get("KINGO_OPS_DIR", str(ROOT / "ops")))
    ap.add_argument("--api", default=os.environ.get("KINGO_GITHUB_API", "https://api.github.com"))
    ap.add_argument("--message", default=DEFAULT_MESSAGE)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    ops_dir = Path(args.ops_dir).expanduser().resolve()
    api = API(credential(), args.api)
    base = f"/repos/{args.owner}/{args.repo}"
    _, ref = api.call("GET", f"{base}/git/ref/heads/{args.branch}")
    base_sha = ref["object"]["sha"]
    _, head = api.call("GET", f"{base}/git/commits/{base_sha}")
    base_tree = head["tree"]["sha"]
    print(f"target={args.owner}/{args.repo}@{args.branch} "
          f"ops_dir={ops_dir}")
    print(f"base={base_sha[:12]} tree={base_tree[:12]} "
          f"head_msg={head['message'].splitlines()[0][:60]}")

    changes = []
    for name in local_files(ops_dir):
        raw = (ops_dir / name).read_bytes()
        local = blob_sha(raw)
        remote = remote_sha(api, base, args.branch, name)
        state = "NEW" if not remote else ("SAME" if remote == local else "CHANGED")
        print(f"  {state:7} ops/{name}  {len(raw):6} bytes  local={local[:12]}"
              + ("" if not remote else f" remote={remote[:12]}"))
        if state != "SAME":
            changes.append((name, raw, local))
    if not changes:
        print("no changes; remote branch is already in sync")
        return 0
    if args.dry_run:
        print(f"dry-run: {len(changes)} file(s) would be committed, nothing written")
        return 0

    tree = []
    for name, raw, sha in changes:
        _, blob = api.call("POST", f"{base}/git/blobs", {
            "content": base64.b64encode(raw).decode("ascii"),
            "encoding": "base64",
        })
        assert blob["sha"] == sha, f"blob sha mismatch for ops/{name}"
        tree.append({"path": f"ops/{name}", "mode": "100644",
                     "type": "blob", "sha": blob["sha"]})
    _, new_tree = api.call("POST", f"{base}/git/trees", {
        "base_tree": base_tree, "tree": tree,
    })
    _, commit = api.call("POST", f"{base}/git/commits", {
        "message": args.message, "tree": new_tree["sha"],
        "parents": [base_sha],
    })
    _, updated = api.call("PATCH", f"{base}/git/refs/heads/{args.branch}", {
        "sha": commit["sha"], "force": False,
    })
    assert updated["object"]["sha"] == commit["sha"], "ref update mismatch"

    _, listing = api.call("GET", f"{base}/contents/ops?ref={args.branch}")
    remote_map = {x["name"]: x["sha"] for x in listing}
    for name, _raw, sha in changes:
        assert remote_map.get(name) == sha, f"remote ops/{name} sha mismatch"
    _, check = api.call("GET", f"{base}/git/commits/{commit['sha']}")
    assert check["parents"][0]["sha"] == base_sha, "parent mismatch"
    print(f"changed={len(changes)} ops_files_on_remote={len(remote_map)}")
    print(f"OK  https://github.com/{args.owner}/{args.repo}/commit/{commit['sha']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

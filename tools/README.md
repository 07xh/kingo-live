# tools/ — repository maintenance

## push_ops.py

Portable, dependency-free incremental sync for `ops/` via the GitHub REST Git
Data API. It exists because the git HTTPS transport can fail behind a proxy
(`CONNECT tunnel failed 502`) while `api.github.com` over HTTPS still works.

```bash
# from the repository root
python tools/push_ops.py --dry-run                     # show the change set
python tools/push_ops.py --message "ops: <change>"    # commit NEW / CHANGED only

# point at another checkout or another repo
python tools/push_ops.py --ops-dir /path/to/ops --owner me --repo myrepo --branch main
```

| Flag | Environment variable | Default |
|---|---|---|
| `--owner` | `KINGO_GITHUB_OWNER` | `07xh` |
| `--repo` | `KINGO_GITHUB_REPO` | `kingo-live` |
| `--branch` | `KINGO_GITHUB_BRANCH` | `main` |
| `--ops-dir` | `KINGO_OPS_DIR` | `<repo root>/ops` |
| `--api` | `KINGO_GITHUB_API` | `https://api.github.com` |
| `--message` | — | `ops: incremental sync` |
| `--dry-run` | — | off |

Token resolution: `GITHUB_TOKEN` first, otherwise `git credential fill` against
`host=github.com` (the OS credential store). The token is never printed,
written to disk, or committed. A personal access token needs `contents:write`
on the target repository.

Guarantees:

- Only files under `ops/` with `.py`, `.md` or `.json` extensions are synced.
- Each file is compared by its git blob SHA1; identical files are skipped.
- The tree is built with `base_tree`, so no path outside `ops/` is modified.
- Remote files with no local counterpart are never deleted.
- After the ref update, every changed blob is re-read from the remote and its
  SHA1 is asserted, and the commit parent is checked against the previous HEAD.

`ops/` content itself is documented in [`ops/README.md`](../ops/README.md).

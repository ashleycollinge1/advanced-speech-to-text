#!/usr/bin/env python3
"""Write GitHub Release notes for a tag.

    python scripts/release_notes.py --version v1.2.0 --image ghcr.io/owner/repo \
        --digest sha256:... --repo owner/repo [--previous v1.1.0] > notes.md

Changes are the commit subjects since the previous tag (the release workflow's
own "release: compose file …" commits are skipped).
"""

from __future__ import annotations

import argparse
import subprocess
import sys


def commit_subjects(previous: str | None, version: str) -> list[str]:
    rng = f"{previous}..{version}" if previous else version
    out = subprocess.run(
        ["git", "log", "--no-merges", "--pretty=%s", rng], capture_output=True, text=True, check=True
    ).stdout
    return [s for s in out.splitlines() if s.strip() and not s.startswith("release: compose file")]


def previous_tag(version: str) -> str | None:
    res = subprocess.run(
        ["git", "describe", "--tags", "--abbrev=0", "--match", "v*", f"{version}^"],
        capture_output=True,
        text=True,
        check=False,
    )
    return res.stdout.strip() or None


def render(version: str, image: str, digest: str, repo: str, previous: str | None, changes: list[str]) -> str:
    tag = version.removeprefix("v")
    lines = ["## Changes", ""]
    lines += [f"- {c}" for c in changes] or ["- Maintenance release."]
    lines += [
        "",
        "## Install / upgrade",
        "",
        "Download `docker-compose.yml` and `.env.example` below (rename the latter to `.env` and fill it in),",
        "or replace your existing `docker-compose.yml` with this release's, then:",
        "",
        "```bash",
        "docker compose pull && docker compose up -d",
        "```",
        "",
        "Database migrations run automatically on start.",
        "",
        "## Image",
        "",
        f"`{image.lower()}:{tag}`",
        f"`{digest}`",
    ]
    if previous:
        lines += ["", f"**Full changelog:** https://github.com/{repo}/compare/{previous}...{version}"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", required=True)
    p.add_argument("--image", required=True)
    p.add_argument("--digest", required=True)
    p.add_argument("--repo", required=True)
    p.add_argument("--previous", help="previous tag (default: found with git describe)")
    args = p.parse_args(argv)
    prev = args.previous or previous_tag(args.version)
    sys.stdout.write(
        render(args.version, args.image, args.digest, args.repo, prev, commit_subjects(prev, args.version))
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

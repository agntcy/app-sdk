# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""Validate that a release tag points at a properly prepared release.

Runs first in the release workflow, so an unprepared tag fails early and
clearly instead of at the PyPI upload. Standard library only. Files are read
from the tagged commit (not the working tree), so it works the same in CI and
locally: ``task release:check-tag -- v0.7.0``.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

TAG_PATTERN = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
MANIFEST_PATH = ".github/release-manifest.json"
PYPROJECT_PATH = "pyproject.toml"
CHANGELOG_PATH = "CHANGELOG.md"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=False
    )


def _read_at(repo: Path, commit: str, path: str) -> str | None:
    result = _git(repo, "show", f"{commit}:{path}")
    return result.stdout if result.returncode == 0 else None


def validate(repo: Path, tag: str, main_ref: str = "origin/main") -> list[str]:
    """Return one message per failed check; an empty list means the tag is a valid release."""
    if not TAG_PATTERN.match(tag):
        return [
            f"Tag '{tag}' does not match vMAJOR.MINOR.PATCH (numeric parts, no suffix, e.g. v0.7.0)."
        ]
    version = tag[1:]

    resolved = _git(
        repo, "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}^{{commit}}"
    )
    if resolved.returncode != 0:
        return [f"Tag '{tag}' not found in the repository."]
    commit = resolved.stdout.strip()

    problems: list[str] = []

    pyproject = _read_at(repo, commit, PYPROJECT_PATH)
    if pyproject is None:
        problems.append(f"{PYPROJECT_PATH} is missing at the tagged commit.")
    else:
        try:
            project = tomllib.loads(pyproject).get("project", {})
        except tomllib.TOMLDecodeError as exc:
            problems.append(f"{PYPROJECT_PATH} is not valid TOML: {exc}")
        else:
            found = project.get("version") if isinstance(project, dict) else None
            if found != version:
                problems.append(
                    f"{PYPROJECT_PATH} has version '{found}', the tag needs '{version}'."
                )

    manifest = _read_at(repo, commit, MANIFEST_PATH)
    if manifest is None:
        problems.append(f"{MANIFEST_PATH} is missing at the tagged commit.")
    else:
        try:
            data = json.loads(manifest)
        except json.JSONDecodeError as exc:
            problems.append(f"{MANIFEST_PATH} is not valid JSON: {exc}")
        else:
            if not isinstance(data, dict):
                problems.append(
                    f"{MANIFEST_PATH} must be a JSON object, got {type(data).__name__}."
                )
            elif data.get(".") != version:
                problems.append(
                    f"{MANIFEST_PATH} has version '{data.get('.')}' for '.', the tag needs '{version}'."
                )

    changelog = _read_at(repo, commit, CHANGELOG_PATH)
    heading = re.compile(rf"^## \[{re.escape(version)}\]", re.MULTILINE)
    if changelog is None or not heading.search(changelog):
        problems.append(f"{CHANGELOG_PATH} has no '## [{version}]' release heading.")

    on_main = _git(repo, "merge-base", "--is-ancestor", commit, main_ref)
    if on_main.returncode != 0:
        problems.append(
            f"The tagged commit is not on {main_ref}. Releases are cut from main."
        )

    return problems


def format_failure(tag: str, problems: list[str]) -> str:
    """Build the failure message: problems first, the bot as the preferred path, then the manual fallback."""
    listed = "\n".join(f"  - {p}" for p in problems)
    return f"""Release tag {tag} was rejected. Nothing was built or published.

Problems:
{listed}

Preferred: let the release-please bot do the release. It opens a release PR
that updates pyproject.toml, .github/release-manifest.json and CHANGELOG.md,
and pushes the tag when that PR is merged. Do not push release tags by hand
unless the bot is unavailable.

Manual fallback (only if the bot cannot be used):
  1. Delete the bad tag: git push origin :refs/tags/{tag} && git tag -d {tag}
  2. In a pull request, set the new version in pyproject.toml and
     .github/release-manifest.json (field ".") and add a '## [X.Y.Z]'
     entry to CHANGELOG.md. Keeping the manifest in sync stops release-please
     from proposing the same release again.
  3. Merge the pull request into main.
  4. Tag the merge commit as vX.Y.Z and push the tag.

Full runbook: docs/RELEASING.md
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a release tag.")
    parser.add_argument("tag", help="the release tag, e.g. v0.7.0")
    parser.add_argument("--repo", type=Path, default=Path("."), help="repository root")
    parser.add_argument(
        "--main-ref", default="origin/main", help="ref the tag must be reachable from"
    )
    args = parser.parse_args(argv)

    problems = validate(args.repo, args.tag, args.main_ref)
    if problems:
        print(format_failure(args.tag, problems), file=sys.stderr)
        return 1
    print(f"Release tag {args.tag} is valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

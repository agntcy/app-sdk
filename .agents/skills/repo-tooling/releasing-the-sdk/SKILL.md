---
name: releasing-the-sdk
description: >-
  Guides releasing agntcy-app-sdk and diagnosing a failed release run: the
  release-please bot flow, the tag guard in release.yaml, the manual fallback and
  recovery. Use when the user asks to release, cut a version, push a tag, or
  asks why a release or PyPI publish failed.
---

# Releasing the SDK

The single source of truth is [docs/RELEASING.md](../../../../docs/RELEASING.md). Read it first and follow it. Do not restate its steps from memory.

## Rules

- **Prefer the release-please bot.** Releases come from merging its release PR. Never suggest or create a `v*` tag by hand unless the user confirms the bot is unavailable.
- **Never push tags, delete tags or merge PRs yourself.** These are remote writes: give the user the exact commands and let them run them.
- A manual tag must come after a merged PR that sets the version in `pyproject.toml` and `.github/release-manifest.json` and adds a `## [X.Y.Z]` entry to `CHANGELOG.md`. The `validate-tag` job in `.github/workflows/release.yaml` rejects anything else.
- Tags are `vMAJOR.MINOR.PATCH` only, on a commit reachable from `main`.

## Workflow

```
- [ ] 1. Read docs/RELEASING.md
- [ ] 2. Find out the situation: normal release, failed run, or manual fallback
- [ ] 3. Normal release: point to the release PR, check version and changelog, stop there
- [ ] 4. Failed run: read the failing job (validate-tag, build-sdk-wheel, pypi-publish) and map it with the "Reading a failed release run" table
- [ ] 5. Before any manual tag: run `task release:check-tag -- vX.Y.Z` and fix what it reports
- [ ] 6. Report the next step for the user to run, preferring the bot path
```

## Checks

- `task release:check-tag -- vX.Y.Z` runs the same validation as the pipeline (`scripts/check_release_tag.py`) against a local tag.
- Do not check `uv.lock` for the SDK version: release-please does not update it.

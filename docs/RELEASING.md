# Releasing the SDK

Releases are made by the [release-please](https://github.com/googleapis/release-please) bot. **Use the bot.** Pushing a tag by hand is a fallback for when the bot is unavailable, and the release pipeline rejects tags that were not prepared correctly (see [Manual fallback](#manual-fallback)).

## How it works

```
PR merged to main -> release-please opens/updates a release PR -> merge it -> tag vX.Y.Z + GitHub Release -> validate tag -> build -> publish to PyPI
```

| Step       | Where                                                                                  | What it does                                                                                                                                                                                |
| ---------- | -------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Release PR | [release-check.yaml](../.github/workflows/release-check.yaml), on every push to `main` | Runs release-please with [release-config.json](../.github/release-config.json) and [release-manifest.json](../.github/release-manifest.json), using the `AGNTCY_BUILD_BOT_GH_TOKEN` secret. |
| Tag        | Merging the release PR                                                                 | release-please pushes `vX.Y.Z` and creates the GitHub Release.                                                                                                                              |
| Pipeline   | [release.yaml](../.github/workflows/release.yaml), on any `v*` tag push                | `validate-tag`, then `build-sdk-wheel` (`task packaging`, one pure-Python wheel plus sdist built with `uv build`), then `pypi-publish` (the `pypi` environment, `PYPI_API_TOKEN` secret).   |

The release PR changes exactly three files, and the tag guard requires all three to agree with the tag:

| File                            | Required content       |
| ------------------------------- | ---------------------- |
| `pyproject.toml`                | `version = "X.Y.Z"`    |
| `.github/release-manifest.json` | `".": "X.Y.Z"`         |
| `CHANGELOG.md`                  | a `## [X.Y.Z]` heading |

`uv.lock` is not part of this: release-please does not update the SDK's own version in it.

## Normal release

1. Merge pull requests with [Conventional Commits](https://www.conventionalcommits.org/) titles. While the version is below 1.0, a breaking change bumps the minor version and `feat`/`fix` bump the patch version (`bump-minor-pre-major`, `bump-patch-for-minor-pre-major`).
2. Open the release PR that release-please maintains (title `chore(main): release X.Y.Z`). Check the version and the changelog text.
3. Merge it. This pushes the tag and starts the release pipeline.
4. Watch the `sdk-release-python` run in GitHub Actions and confirm the version on [PyPI](https://pypi.org/project/agntcy-app-sdk/).

If no release PR appears, check the latest `ci-release-please-check` run: it needs releasable commits (`feat`, `fix`, breaking changes) since the last release, and a valid bot token.

## Reading a failed release run

| Failing job       | Meaning                                                                                                                                      | Action                                                                                                       |
| ----------------- | -------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| `validate-tag`    | The tag is not a prepared release. The log lists every failed check and the fix. Nothing was built or published.                             | Follow [Manual fallback](#manual-fallback) step 1 onward, or delete the tag and use the bot.                 |
| `build-sdk-wheel` | Build problem. Nothing was published.                                                                                                        | Fix on `main`, then re-run the failed job if the tag commit is still right, otherwise release a new version. |
| `pypi-publish`    | Upload rejected or failed. If PyPI says the file already exists, that version is already published (PyPI never accepts the same file twice). | See [Recovery](#recovery).                                                                                   |

## Manual fallback

Only when the bot cannot produce the release. Releases are cut from `main`, with `vMAJOR.MINOR.PATCH` tags (no pre-release suffixes).

1. If a bad tag was already pushed, delete it: `git push origin :refs/tags/vX.Y.Z` and `git tag -d vX.Y.Z`.
2. In a pull request, set `X.Y.Z` in `pyproject.toml`, in `.github/release-manifest.json` (the `"."` entry) and add a `## [X.Y.Z]` entry to `CHANGELOG.md`. Updating the manifest keeps release-please from proposing the same release again.
3. Merge the pull request into `main`.
4. Tag the merge commit and push: `git tag vX.Y.Z <merge-commit> && git push origin vX.Y.Z`.
5. Check the pipeline run. A manual tag does not create a GitHub Release, so create it from the tag if you want one.

Before pushing the tag, run `git fetch origin` and then `task release:check-tag -- vX.Y.Z`. It runs the same check as the pipeline against the local tag. The fetch matters: the check compares the tag with your local `origin/main`, and a stale ref right after a merge gives a false "not on origin/main".

## Recovery

- **Bad tag, nothing published** (`validate-tag` failed): delete the tag and redo the release as above.
- **Version already on PyPI**: it cannot be replaced. Fix forward by releasing the next patch version. If the published files are broken, yank that version on PyPI.
- **Transient failure before the upload** (runner or network error): re-run the failed jobs of the same run.

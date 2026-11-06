# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""Tests for scripts/check_release_tag.py, run against temporary git repos."""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_release_tag.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_release_tag", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = _load()


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "tag.gpgsign=false",
            *args,
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _write_release_files(
    repo: Path,
    pyproject: str = "0.7.0",
    manifest: str = "0.7.0",
    changelog: str = "0.7.0",
) -> None:
    (repo / ".github").mkdir(exist_ok=True)
    (repo / "pyproject.toml").write_text(
        f'[project]\nname = "x"\nversion = "{pyproject}"\n'
    )
    (repo / ".github" / "release-manifest.json").write_text(json.dumps({".": manifest}))
    (repo / "CHANGELOG.md").write_text(
        f"# Changelog\n\n## [{changelog}](https://example.test/compare/a...b) (2026-01-01)\n"
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-b", "main")
    _write_release_files(tmp_path)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "release")
    return tmp_path


def _check(repo: Path, tag: str) -> list[str]:
    result: list[str] = guard.validate(repo, tag, main_ref="main")
    return result


def test_prepared_release_passes(repo: Path) -> None:
    _git(repo, "tag", "v0.7.0")
    assert _check(repo, "v0.7.0") == []


@pytest.mark.parametrize("tag", ["v0.7.0-rc1", "0.7.0", "v0.7", "v0.7.0.1", "vx.y.z"])
def test_bad_tag_format_is_rejected(repo: Path, tag: str) -> None:
    _git(repo, "tag", tag)
    problems = _check(repo, tag)
    assert len(problems) == 1
    assert "vMAJOR.MINOR.PATCH" in problems[0]


def test_unknown_tag_is_rejected(repo: Path) -> None:
    problems = _check(repo, "v9.9.9")
    assert any("v9.9.9" in p and "not found" in p for p in problems)


def test_wrong_pyproject_version_is_rejected(repo: Path) -> None:
    _write_release_files(repo, pyproject="0.6.0")
    _git(repo, "commit", "-am", "x")
    _git(repo, "tag", "v0.7.0")
    problems = _check(repo, "v0.7.0")
    assert len(problems) == 1
    assert "pyproject.toml" in problems[0]


def test_wrong_manifest_version_is_rejected(repo: Path) -> None:
    _write_release_files(repo, manifest="0.6.0")
    _git(repo, "commit", "-am", "x")
    _git(repo, "tag", "v0.7.0")
    problems = _check(repo, "v0.7.0")
    assert len(problems) == 1
    assert "release-manifest.json" in problems[0]


def test_missing_changelog_entry_is_rejected(repo: Path) -> None:
    _write_release_files(repo, changelog="0.6.0")
    _git(repo, "commit", "-am", "x")
    _git(repo, "tag", "v0.7.0")
    problems = _check(repo, "v0.7.0")
    assert len(problems) == 1
    assert "CHANGELOG.md" in problems[0]


def test_changelog_prefix_version_does_not_match(repo: Path) -> None:
    _write_release_files(repo, changelog="0.7.01")
    _git(repo, "commit", "-am", "x")
    _git(repo, "tag", "v0.7.0")
    assert any("CHANGELOG.md" in p for p in _check(repo, "v0.7.0"))


def test_tag_off_main_is_rejected(repo: Path) -> None:
    _git(repo, "checkout", "-b", "side")
    (repo / "extra.txt").write_text("x")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "side")
    _git(repo, "tag", "v0.7.0")
    problems = _check(repo, "v0.7.0")
    assert len(problems) == 1
    assert "main" in problems[0]


def test_files_are_read_from_the_tag_not_the_working_tree(repo: Path) -> None:
    _git(repo, "tag", "v0.7.0")
    _write_release_files(repo, pyproject="9.9.9", manifest="9.9.9", changelog="9.9.9")
    assert _check(repo, "v0.7.0") == []


def test_all_failures_are_reported_together(repo: Path) -> None:
    _write_release_files(repo, pyproject="0.6.0", manifest="0.6.0", changelog="0.6.0")
    _git(repo, "commit", "-am", "x")
    _git(repo, "tag", "v0.7.0")
    assert len(_check(repo, "v0.7.0")) == 3


def test_message_recommends_the_bot_before_the_fallback() -> None:
    message: str = guard.format_failure("v0.7.0", ["problem one", "problem two"])
    assert "problem one" in message and "problem two" in message
    assert message.index("release-please") < message.index("Manual fallback")
    assert "docs/RELEASING.md" in message


def test_main_returns_nonzero_and_prints_message(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_release_files(repo, pyproject="0.6.0")
    _git(repo, "commit", "-am", "x")
    _git(repo, "tag", "v0.7.0")
    code: int = guard.main(["v0.7.0", "--repo", str(repo), "--main-ref", "main"])
    assert code == 1
    assert "pyproject.toml" in capsys.readouterr().err


def test_main_returns_zero_for_a_prepared_release(repo: Path) -> None:
    _git(repo, "tag", "v0.7.0")
    assert guard.main(["v0.7.0", "--repo", str(repo), "--main-ref", "main"]) == 0

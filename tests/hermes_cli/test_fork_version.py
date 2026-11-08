"""The fork's version comes from fork-* tags and never stands in for upstream's release version."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from hermes_cli.fork_version import fork_version
from hermes_cli.version_info import _git_version_info


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=repo, check=True,
                   capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL)


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "commit", "-q", "--allow-empty", "-m", "first")
    fork_version.cache_clear()
    yield tmp_path
    fork_version.cache_clear()


def test_a_checkout_before_the_first_release_has_no_fork_version(repo):
    assert fork_version(repo) is None


def test_the_release_tag_names_the_version_and_later_commits_count_past_it(repo):
    git(repo, "tag", "-a", "fork-2026.11.08", "-m", "release")
    assert fork_version(repo) == "2026.11.08"

    git(repo, "commit", "-q", "--allow-empty", "-m", "next")
    fork_version.cache_clear()
    assert fork_version(repo) == "2026.11.08+1"


def test_a_fork_release_is_never_read_as_an_upstream_release(repo):
    git(repo, "tag", "-a", "fork-2026.11.08", "-m", "release")

    assert _git_version_info(repo).base_version == "unknown"

    git(repo, "tag", "v1.4.0")
    fork_version.cache_clear()
    assert _git_version_info(repo).base_version == "1.4.0"
    assert fork_version(repo) == "2026.11.08"

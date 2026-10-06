"""Tests for explicit version-source snapshots.

Targeted run:

    uv run pytest vcs-versioning/testing_vcs/test_version_snapshot.py -v

or, without uv, from the repository root:

    PYTHONPATH=vcs-versioning/src python -m pytest \
        vcs-versioning/testing_vcs/test_version_snapshot.py -v

The three source-state examples are:

- ``test_describe_source_example`` -- tag selected by ``git describe``
- ``test_no_tag_source_example`` -- tag unavailable, fallback base version
- ``test_dirty_worktree_source_example`` -- worktree changes (dirty)
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from datetime import date, datetime

import pytest
from vcs_versioning import (
    Configuration,
    SnapshotCaptureError,
    SnapshotSource,
    VersionSourceSnapshot,
    capture_git_snapshot,
    version_from_git_snapshot,
)
from vcs_versioning._run_cmd import CompletedProcess
from vcs_versioning.test_api import TEST_SOURCE_DATE, DebugMode, WorkDir


# a fixed clock for every fake-git capture: 2009-02-13T23:31:30+00:00
def FIXED_CLOCK() -> datetime:
    return TEST_SOURCE_DATE


NODE_A = "1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d"
NODE_B = "ffeeddccbbaa99887766554433221100ffeeddcc"


class FakeGit:
    """Scripted git runner.

    Each command consumes its queued ``(returncode, stdout)`` responses in
    order, so a test can make the *second* answer differ from the first and
    prove the snapshot only ever used the first derivation.
    """

    def __init__(self) -> None:
        self.responses: dict[tuple[str, ...], list[tuple[int, str]]] = {}
        self.calls: list[tuple[str, ...]] = []

    def add(self, *args: str, results: list[tuple[int, str]] | tuple[int, str]) -> None:
        if isinstance(results, tuple):
            results = [results]
        self.responses[tuple(args)] = list(results)

    def __call__(self, args: Sequence[str]) -> CompletedProcess:
        key = tuple(str(a) for a in args)
        self.calls.append(key)
        queue = self.responses.get(key)
        if not queue:
            return CompletedProcess(
                args=list(args),
                returncode=128,
                stdout="",
                stderr=f"fake git: no response scripted for {key!r}",
            )
        returncode, stdout = queue.pop(0)
        return CompletedProcess(
            args=list(args), returncode=returncode, stdout=stdout, stderr=""
        )

    def count(self, command: str) -> int:
        return sum(1 for call in self.calls if call and call[0] == command)


def script_common_git(git: FakeGit, *, branch: str = "main") -> None:
    """Script the repo probe plus branch/date queries shared by all captures."""
    git.add("rev-parse", "--git-dir", results=(0, ".git"))
    git.add("rev-parse", "--abbrev-ref", "HEAD", results=(0, branch))
    git.add(
        "-c",
        "log.showSignature=false",
        "log",
        "-n",
        "1",
        "HEAD",
        "--format=%cI",
        results=(0, "2009-02-13T23:31:30+00:00"),
    )


def describe_args(config: Configuration) -> tuple[str, ...]:
    return (
        "describe",
        "--dirty",
        "--tags",
        "--long",
        "--abbrev=40",
        "--match",
        config.tag.describe_match_glob(),
    )


BASE_CONFIG = Configuration()


def test_describe_source_example() -> None:
    """Source state 1: a tag was selected by ``git describe``."""
    git = FakeGit()
    script_common_git(git)
    git.add(*describe_args(BASE_CONFIG), results=(0, f"v1.2.3-2-g{NODE_A}"))

    snapshot = capture_git_snapshot(BASE_CONFIG, run_git=git, now=FIXED_CLOCK)

    assert snapshot.source is SnapshotSource.DESCRIBE
    assert snapshot.tag_available
    assert not snapshot.worktree_changed
    assert snapshot.tag == "v1.2.3"
    assert snapshot.distance == 2
    assert snapshot.node == "g" + NODE_A
    assert snapshot.dirty is False
    assert snapshot.branch == "main"
    assert snapshot.node_date == date(2009, 2, 13)
    assert snapshot.time == TEST_SOURCE_DATE
    assert snapshot.exact is False
    assert snapshot.format(BASE_CONFIG) == "1.2.4.dev2+g1a2b3c4d5"


def test_no_tag_source_example() -> None:
    """Source state 2: no usable tag, base version is the fallback."""
    git = FakeGit()
    script_common_git(git)
    git.add(*describe_args(BASE_CONFIG), results=(128, ""))
    git.add("rev-parse", "--verify", "--quiet", "HEAD", results=(0, NODE_A))
    git.add("rev-list", "HEAD", results=(0, "c1\nc2\nc3"))
    git.add("status", "--porcelain", "--untracked-files=no", results=(0, ""))

    snapshot = capture_git_snapshot(BASE_CONFIG, run_git=git, now=FIXED_CLOCK)

    assert snapshot.source is SnapshotSource.NO_TAG
    assert not snapshot.tag_available
    assert snapshot.tag is None
    assert snapshot.distance == 3
    assert snapshot.node == "g" + NODE_A
    assert snapshot.dirty is False
    # base version comes from the implicit 0.0 fallback
    assert snapshot.format(BASE_CONFIG) == "0.1.dev3+g1a2b3c4d5"

    # an explicit fallback_version is honored as the base instead
    config = dataclasses.replace(BASE_CONFIG, fallback_version="7.8")
    assert snapshot.format(config) == "7.9.dev3+g1a2b3c4d5"


def test_dirty_worktree_source_example() -> None:
    """Source state 3: the worktree had uncommitted changes at capture time."""
    git = FakeGit()
    script_common_git(git)
    git.add(*describe_args(BASE_CONFIG), results=(0, f"v1.2.3-0-g{NODE_A}-dirty"))

    snapshot = capture_git_snapshot(BASE_CONFIG, run_git=git, now=FIXED_CLOCK)

    assert snapshot.source is SnapshotSource.DESCRIBE
    assert snapshot.worktree_changed
    assert snapshot.dirty is True
    assert snapshot.exact is False
    # dirty state and version output agree: the dirty date stamp shows up
    assert snapshot.format(BASE_CONFIG) == "1.2.4.dev0+g1a2b3c4d5.d20090213"


def test_snapshot_freezes_first_derivation() -> None:
    """A second, different git answer never leaks into the frozen snapshot."""
    git = FakeGit()
    script_common_git(git)
    git.add(
        *describe_args(BASE_CONFIG),
        results=[
            (0, f"v1.2.3-2-g{NODE_A}"),  # first derivation: captured
            (0, f"v9.9.9-0-g{NODE_B}-dirty"),  # later moment: must be ignored
        ],
    )

    snapshot = capture_git_snapshot(BASE_CONFIG, run_git=git, now=FIXED_CLOCK)

    first = snapshot.format(BASE_CONFIG)
    second = snapshot.format(BASE_CONFIG)
    assert first == second == "1.2.4.dev2+g1a2b3c4d5"
    assert "9.9.9" not in first
    assert snapshot.dirty is False
    # every git question was asked exactly once, during the capture
    assert git.count("describe") == 1
    assert git.responses[describe_args(BASE_CONFIG)] == [
        (0, f"v9.9.9-0-g{NODE_B}-dirty")
    ], "the second describe answer was never consumed"
    # repeated ScmVersion derivations come from the same frozen metadata
    assert snapshot.scm_version(BASE_CONFIG) == snapshot.scm_version(BASE_CONFIG)


def test_version_scheme_runs_once() -> None:
    """The version scheme executes exactly once per snapshot formatting."""
    git = FakeGit()
    script_common_git(git)
    git.add(*describe_args(BASE_CONFIG), results=(0, f"v1.2.3-2-g{NODE_A}"))

    seen: list[str] = []

    def counting_scheme(version: object) -> str:
        seen.append(str(version))
        return "1.2.4.dev2"

    config = dataclasses.replace(
        BASE_CONFIG, version_scheme=counting_scheme, local_scheme="no-local-version"
    )
    snapshot = capture_git_snapshot(config, run_git=git, now=FIXED_CLOCK)

    assert snapshot.format(config) == "1.2.4.dev2"
    assert len(seen) == 1


def test_dirty_state_matches_version_output() -> None:
    """The dirty flag in the snapshot and the rendered version always agree."""
    clean_git = FakeGit()
    script_common_git(clean_git)
    clean_git.add(*describe_args(BASE_CONFIG), results=(0, f"v1.2.3-0-g{NODE_A}"))
    clean = capture_git_snapshot(BASE_CONFIG, run_git=clean_git, now=FIXED_CLOCK)

    dirty_git = FakeGit()
    script_common_git(dirty_git)
    dirty_git.add(*describe_args(BASE_CONFIG), results=(0, f"v1.2.3-0-g{NODE_A}-dirty"))
    dirty = capture_git_snapshot(BASE_CONFIG, run_git=dirty_git, now=FIXED_CLOCK)

    assert clean.dirty is False
    assert "d20090213" not in clean.format(BASE_CONFIG)
    assert dirty.dirty is True
    assert "d20090213" in dirty.format(BASE_CONFIG)


def test_capture_failure_is_an_error_not_version_zero() -> None:
    """A failed capture raises; it never degrades into a ``0.0`` version."""
    git = FakeGit()
    git.add(
        "rev-parse",
        "--git-dir",
        results=(128, "fatal: not a git repository"),
    )

    with pytest.raises(SnapshotCaptureError, match="not a 0.0 version"):
        capture_git_snapshot(BASE_CONFIG, run_git=git, now=FIXED_CLOCK)

    with pytest.raises(SnapshotCaptureError):
        version_from_git_snapshot(BASE_CONFIG, run_git=git, now=FIXED_CLOCK)

    # git was asked once per attempt and gave up -- no fallback was fabricated
    assert git.calls == [("rev-parse", "--git-dir")] * 2


def test_empty_repository_snapshot() -> None:
    """A repo without commits is a valid NO_TAG snapshot, not a failure."""
    git = FakeGit()
    git.add("rev-parse", "--git-dir", results=(0, ".git"))
    git.add(*describe_args(BASE_CONFIG), results=(128, ""))
    git.add("rev-parse", "--verify", "--quiet", "HEAD", results=(128, ""))
    git.add("rev-parse", "--abbrev-ref", "HEAD", results=(128, ""))
    git.add("symbolic-ref", "--short", "HEAD", results=(0, "main"))
    git.add(
        "-c",
        "log.showSignature=false",
        "log",
        "-n",
        "1",
        "HEAD",
        "--format=%cI",
        results=(128, ""),
    )

    snapshot = capture_git_snapshot(BASE_CONFIG, run_git=git, now=FIXED_CLOCK)

    assert snapshot.source is SnapshotSource.NO_TAG
    assert snapshot.node is None
    assert snapshot.distance == 0
    assert snapshot.dirty is True
    assert snapshot.branch == "main"
    # no commit date available: the injected clock supplies the date
    assert snapshot.node_date == TEST_SOURCE_DATE.date()
    assert snapshot.format(BASE_CONFIG) == "0.1.dev0+d20090213"


def test_snapshot_holds_no_file_contents() -> None:
    """The snapshot stores metadata only -- no file content fields."""
    names = {f.name for f in dataclasses.fields(VersionSourceSnapshot)}
    assert names == {
        "tag",
        "distance",
        "node",
        "dirty",
        "branch",
        "node_date",
        "source",
        "time",
    }


@pytest.fixture
def wd(wd: WorkDir, monkeypatch: pytest.MonkeyPatch, debug_mode: DebugMode) -> WorkDir:
    """Set up git for the real-git snapshot tests."""
    debug_mode.disable()
    wd.setup_git(monkeypatch)
    debug_mode.enable()
    return wd


def test_real_git_snapshot_matches_get_version(wd: WorkDir) -> None:
    """Default (non-injected) capture agrees with the normal get_version."""
    wd.commit_testfile()
    wd.create_tag("v1.0.0")
    config = Configuration(root=str(wd.cwd))

    snapshot = capture_git_snapshot(config)
    assert snapshot.source is SnapshotSource.DESCRIBE
    assert snapshot.tag == "v1.0.0"
    assert snapshot.distance == 0
    assert snapshot.dirty is False
    assert snapshot.branch is not None
    assert snapshot.format(config) == "1.0.0"
    # the normal get_version pipeline is unchanged and agrees
    assert wd.get_version() == "1.0.0"

    # a worktree change is a new capture with its own dirty source state
    wd.write("test.txt", "changed")
    dirty_snapshot = capture_git_snapshot(config)
    assert dirty_snapshot.dirty is True
    assert dirty_snapshot.worktree_changed
    dirty_version = dirty_snapshot.format(config)
    assert ".dev0" in dirty_version
    assert dirty_version == wd.get_version()

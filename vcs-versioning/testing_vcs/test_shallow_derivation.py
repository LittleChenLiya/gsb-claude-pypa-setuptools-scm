"""Tests for the shallow-repository source marker on ScmVersion.

The git backend marks versions derived from a shallow clone
(``ScmVersion.shallow``), and callers may reject such incomplete-history
derivations via ``fail_on_shallow``.  Exported archives without ``.git``
and other non-git sources must not be misjudged as shallow.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from vcs_versioning import Configuration
from vcs_versioning._backends import _git as git
from vcs_versioning._get_version_impl import _fail_if_shallow_derivation, get_version
from vcs_versioning._run_cmd import run
from vcs_versioning._scm_version import meta
from vcs_versioning.test_api import DebugMode, WorkDir


@pytest.fixture
def wd(wd: WorkDir, monkeypatch: pytest.MonkeyPatch, debug_mode: DebugMode) -> WorkDir:
    """Set up git for git-specific tests."""
    debug_mode.disable()
    wd.setup_git(monkeypatch)
    debug_mode.enable()
    return wd


@pytest.fixture
def shallow_wd(wd: WorkDir, tmp_path: Path) -> Path:
    """A minimal local shallow clone; no remote history is fetched."""
    wd.commit_testfile()
    wd.commit_testfile()
    wd.commit_testfile()
    target = tmp_path / "wd_shallow"
    run(["git", "clone", f"file://{wd.cwd}", target, "--depth=1"], tmp_path, check=True)
    return target


@pytest.fixture
def shallow_tagged_wd(wd: WorkDir, tmp_path: Path) -> Path:
    """A shallow clone checked out exactly on a tag."""
    wd.commit_testfile()
    wd("git tag v0.1.0")
    target = tmp_path / "wd_shallow_tag"
    run(
        [
            "git",
            "clone",
            f"file://{wd.cwd}",
            str(target),
            "--depth=1",
            "--branch",
            "v0.1.0",
        ],
        tmp_path,
        check=True,
    )
    return target


def test_shallow_marker_set_for_shallow_clone(
    shallow_wd: Path, recwarn: pytest.WarningsRecorder
) -> None:
    version = git.parse(shallow_wd, Configuration())
    assert version is not None
    assert version.shallow is True


def test_shallow_marker_not_set_for_full_history(wd: WorkDir) -> None:
    wd.commit_testfile()
    version = git.parse(wd.cwd, Configuration())
    assert version is not None
    assert version.shallow is False


def test_get_version_default_allows_shallow(
    shallow_wd: Path, recwarn: pytest.WarningsRecorder
) -> None:
    version = get_version(root=shallow_wd)
    assert version.startswith("0.1.dev")


def test_get_version_fail_on_shallow_rejects(
    shallow_wd: Path, recwarn: pytest.WarningsRecorder
) -> None:
    with pytest.raises(ValueError, match="shallow") as excinfo:
        get_version(root=shallow_wd, fail_on_shallow=True)
    # the rejection message keeps the actual source of the derivation
    assert str(shallow_wd.resolve()) in str(excinfo.value)


def test_get_version_fail_on_shallow_allows_exact_tag(
    shallow_tagged_wd: Path,
) -> None:
    # an exact tag checkout does not need history
    assert get_version(root=shallow_tagged_wd, fail_on_shallow=True) == "0.1.0"


def test_archival_without_git_dir_is_not_shallow(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    archive.mkdir()
    archive.joinpath(".git_archival.txt").write_text(
        "node: 1234567890abcdef1234567890abcdef12345678\n"
        "describe-name: v1.2.3\n"
        "ref-names: tag: v1.2.3\n",
        encoding="utf-8",
    )
    # an exported archive has no .git and must not be misjudged as shallow
    assert get_version(root=archive, fail_on_shallow=True) == "1.2.3"


def test_fallback_version_source_is_not_shallow(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    version = get_version(root=empty, fallback_version="1.0", fail_on_shallow=True)
    assert version == "1.0"


def test_meta_passes_shallow_marker() -> None:
    config = Configuration()
    assert meta("1.0", config=config, distance=2, shallow=True).shallow is True
    assert meta("1.0", config=config, distance=2).shallow is False


def test_fail_if_shallow_derivation_in_memory() -> None:
    config = Configuration(fail_on_shallow=True)
    shallow = meta("1.0", config=config, distance=2, shallow=True)
    with pytest.raises(ValueError, match="shallow"):
        _fail_if_shallow_derivation(shallow, config)
    # an exact tag checkout (distance 0) does not need history
    _fail_if_shallow_derivation(meta("1.0", config=config, shallow=True), config)
    # a version from full history is always allowed
    _fail_if_shallow_derivation(meta("1.0", config=config, distance=2), config)
    # and everything is allowed when the caller did not opt in
    lax = Configuration()
    _fail_if_shallow_derivation(shallow, lax)

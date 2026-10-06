"""Targeted tests for the strong-source version consistency check.

Only git and PKG-INFO are strong sources.  Unexpanded archival placeholders
are ignored, weak sources (parent-directory prefix, fallback version) are
not consulted, and a conflict reports both categories instead of silently
overriding either.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from vcs_versioning import (
    VersionSourceConflict,
    check_version_sources,
    collect_strong_version_sources,
)
from vcs_versioning._test_utils import WorkDir

PKGINFO_TEMPLATE = "Metadata-Version: 2.1\nName: fake-package\nVersion: {version}\n"


def _write_pkginfo(wd: WorkDir, version: str) -> Path:
    return wd.write("PKG-INFO", PKGINFO_TEMPLATE.format(version=version))


@pytest.fixture
def git_wd(wd: WorkDir, monkeypatch: pytest.MonkeyPatch) -> WorkDir:
    """Local fake git source tagged 1.2."""
    wd.setup_git(monkeypatch)
    wd.commit_testfile()
    wd.create_tag("1.2")
    return wd


def test_git_and_pkginfo_conflict_is_rejected(git_wd: WorkDir) -> None:
    # git says 1.2, PKG-INFO says 1.3 -> refuse, report both categories
    _write_pkginfo(git_wd, "1.3")

    with pytest.raises(VersionSourceConflict) as excinfo:
        check_version_sources(git_wd.cwd)

    assert excinfo.value.git_version == "1.2"
    assert excinfo.value.pkginfo_version == "1.3"
    message = str(excinfo.value)
    assert "git" in message
    assert "PKG-INFO" in message
    assert "1.2" in message and "1.3" in message

    sources = collect_strong_version_sources(git_wd.cwd)
    assert {source.category for source in sources} == {"git", "PKG-INFO"}


def test_single_strong_source_succeeds(git_wd: WorkDir, tmp_path: Path) -> None:
    # git alone
    assert str(check_version_sources(git_wd.cwd).tag) == "1.2"

    # PKG-INFO alone (no VCS metadata anywhere)
    pkginfo_only = tmp_path / "pkginfo_only"
    pkginfo_only.mkdir()
    solo = WorkDir(pkginfo_only)
    _write_pkginfo(solo, "1.3")
    assert str(check_version_sources(pkginfo_only).tag) == "1.3"


def test_agreement_between_sources_returns_version(git_wd: WorkDir) -> None:
    _write_pkginfo(git_wd, "1.2")
    assert str(check_version_sources(git_wd.cwd).tag) == "1.2"


def test_unexpanded_archival_placeholder_is_ignored(tmp_path: Path) -> None:
    # Local fake sources: an unexpanded git archival placeholder plus a
    # valid PKG-INFO.  The placeholder must not count as a source, so the
    # single strong source (PKG-INFO) wins without a conflict.
    root = tmp_path / "placeholder_sdist"
    root.mkdir()
    wd = WorkDir(root)
    wd.write(".git_archival.txt", "node: $Format:%H$\n")
    _write_pkginfo(wd, "1.3")

    sources = collect_strong_version_sources(root)
    assert [source.category for source in sources] == ["PKG-INFO"]
    assert str(check_version_sources(root).tag) == "1.3"


def test_no_strong_source_raises_lookup_error(tmp_path: Path) -> None:
    root = tmp_path / "empty"
    root.mkdir()
    with pytest.raises(LookupError):
        check_version_sources(root)

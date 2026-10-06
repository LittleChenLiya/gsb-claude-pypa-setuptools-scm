"""Targeted tests for the git/PKG-INFO strong-source consistency check."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from vcs_versioning._backends._scm_workdir import ScmWorkdir
from vcs_versioning._config import Configuration
from vcs_versioning._fallback_workdir import ArchivedWorkdir, PkgInfoWorkdir
from vcs_versioning._scm_version import ScmVersion, meta
from vcs_versioning._source_consistency import (
    GIT_SOURCE,
    PKGINFO_SOURCE,
    VersionSource,
    VersionSourceConsistencyError,
    check_version_sources_consistency,
)
from vcs_versioning._worktree_discovery import discover_workdir

# ---------------------------------------------------------------------------
# Three local fake sources: no subprocesses, no real VCS.
# ---------------------------------------------------------------------------


def fake_git_source(version: str = "1.2") -> VersionSource:
    """A strong git source reporting *version*."""
    return VersionSource(category=GIT_SOURCE, version=version)


def fake_pkginfo_source(version: str = "1.3") -> VersionSource:
    """A strong PKG-INFO source reporting *version*."""
    return VersionSource(category=PKGINFO_SOURCE, version=version)


def fake_unexpanded_archival_source() -> VersionSource | None:
    """An unexpanded .git_archival.txt placeholder is never a source."""
    return None


class _FakeGitWorkdir(ScmWorkdir):
    """Minimal ScmWorkdir standing in for a live git checkout."""

    version_source_category: str | None = "git"

    def __init__(self, path: Path, version: str) -> None:
        super().__init__(path=path)
        self._fake_version = version

    def get_scm_version(self) -> ScmVersion:
        return meta(self._fake_version, preformatted=True, config=self.config)

    def list_tracked_files(self, path: Path | str = "") -> list[str]:
        return []

    def is_file_tracked(self, path: Path) -> bool:
        return False


def _fake_git_factory(version: str) -> Any:
    def factory(path: Path, *, config: Configuration) -> _FakeGitWorkdir | None:
        if (path / ".fake-git-marker").is_file():
            wd = _FakeGitWorkdir(path, version)
            wd._config = config
            return wd
        return None

    return factory


def _pkginfo_factory(path: Path, *, config: Configuration) -> PkgInfoWorkdir | None:
    if (path / "PKG-INFO").is_file():
        return PkgInfoWorkdir(path=path)
    return None


def _archival_factory(path: Path, *, config: Configuration) -> ArchivedWorkdir | None:
    if (path / ".git_archival.txt").is_file():
        return ArchivedWorkdir(path=path)
    return None


# ---------------------------------------------------------------------------
# Unit-level checks against the three local fake sources
# ---------------------------------------------------------------------------


class TestSourceConsistency:
    def test_git_and_pkginfo_conflict_is_rejected(self) -> None:
        """git 1.2 vs PKG-INFO 1.3 must be refused, naming both categories."""
        with pytest.raises(VersionSourceConsistencyError) as excinfo:
            check_version_sources_consistency(
                [fake_git_source("1.2"), fake_pkginfo_source("1.3")]
            )
        message = str(excinfo.value)
        assert GIT_SOURCE in message
        assert PKGINFO_SOURCE in message
        assert "1.2" in message
        assert "1.3" in message

    def test_single_git_source_succeeds(self) -> None:
        """One strong source alone never conflicts (placeholder ignored)."""
        sources = [fake_git_source("1.2"), fake_unexpanded_archival_source()]
        check_version_sources_consistency(
            [source for source in sources if source is not None]
        )

    def test_single_pkginfo_source_succeeds(self) -> None:
        sources = [fake_pkginfo_source("1.3"), fake_unexpanded_archival_source()]
        check_version_sources_consistency(
            [source for source in sources if source is not None]
        )

    def test_no_source_succeeds(self) -> None:
        check_version_sources_consistency([])

    def test_agreement_succeeds(self) -> None:
        check_version_sources_consistency(
            [fake_git_source("1.2"), fake_pkginfo_source("1.2")]
        )

    def test_agreement_after_canonicalization(self) -> None:
        """v1.2 and 1.2 describe the same release."""
        check_version_sources_consistency(
            [fake_git_source("v1.2"), fake_pkginfo_source("1.2")]
        )

    def test_unexpanded_placeholder_does_not_count_as_source(self) -> None:
        """The placeholder source is None: it neither agrees nor conflicts."""
        assert fake_unexpanded_archival_source() is None
        sources: list[VersionSource] = []
        placeholder = fake_unexpanded_archival_source()
        if placeholder is not None:
            sources.append(placeholder)
        sources.append(fake_git_source("1.2"))
        check_version_sources_consistency(sources)


# ---------------------------------------------------------------------------
# End-to-end discovery with local fake factories
# ---------------------------------------------------------------------------


class TestDiscoveryConsistency:
    def _config(self, tmp_path: Path) -> Configuration:
        return Configuration(relative_to=str(tmp_path / "pyproject.toml"))

    def _write_pkginfo(self, tmp_path: Path, version: str) -> None:
        (tmp_path / "PKG-INFO").write_text(
            f"Metadata-Version: 2.1\nName: pkg\nVersion: {version}\n",
            encoding="utf-8",
        )

    def _patch_factories(
        self, monkeypatch: pytest.MonkeyPatch, factories: list[Any]
    ) -> None:
        from vcs_versioning import _worktree_discovery

        monkeypatch.setattr(
            _worktree_discovery, "_load_discovery_factories", lambda: factories
        )

    def test_conflicting_git_and_pkginfo_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / ".fake-git-marker").write_text("x", encoding="utf-8")
        self._write_pkginfo(tmp_path, "1.3")
        self._patch_factories(
            monkeypatch,
            [("fake-git", _fake_git_factory("1.2")), ("pkginfo", _pkginfo_factory)],
        )

        with pytest.raises(VersionSourceConsistencyError, match="git"):
            discover_workdir(self._config(tmp_path))

    def test_git_alone_succeeds(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / ".fake-git-marker").write_text("x", encoding="utf-8")
        self._patch_factories(monkeypatch, [("fake-git", _fake_git_factory("1.2"))])

        result = discover_workdir(self._config(tmp_path))
        assert isinstance(result, _FakeGitWorkdir)

    def test_pkginfo_alone_succeeds(self, tmp_path: Path) -> None:
        """Single valid source: the PKG-INFO fallback still works unchanged."""
        self._write_pkginfo(tmp_path, "1.3")
        result = discover_workdir(self._config(tmp_path))
        version = result.get_scm_version()
        assert version is not None
        assert str(version.tag) == "1.3"

    def test_unexpanded_archival_ignored_with_git(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An unexpanded placeholder must not take part in the cross-check."""
        (tmp_path / ".fake-git-marker").write_text("x", encoding="utf-8")
        (tmp_path / ".git_archival.txt").write_text(
            "node: $Format:%H$\nref-names: $Format:%D$\n",
            encoding="utf-8",
        )
        self._patch_factories(
            monkeypatch,
            [
                ("fake-git", _fake_git_factory("1.2")),
                ("archival", _archival_factory),
            ],
        )

        result = discover_workdir(self._config(tmp_path))
        assert isinstance(result, _FakeGitWorkdir)

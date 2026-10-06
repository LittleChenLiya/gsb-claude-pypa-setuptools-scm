"""Consistency check between the two strong version sources: git and PKG-INFO.

Only these two sources are treated as authoritative enough to be compared:

- ``git`` -- a live checkout whose tags describe the version;
- ``PKG-INFO`` -- the static metadata shipped in an sdist.

When both are present they must agree.  A mismatch is reported naming both
source categories instead of silently preferring one of them.  An unexpanded
``.git_archival.txt`` / ``.hg_archival.txt`` (one still carrying
``$Format...$`` placeholders) is *not* a valid source and is ignored.

Deliberately excluded:

- weak sources such as a parent-directory prefix or ``fallback_version``;
- any generic priority/ordering policy (see the discovery pipeline instead).

The default version pipeline (:func:`vcs_versioning.get_version` and friends)
is unchanged; this is an opt-in cross-check.
"""

from __future__ import annotations

import dataclasses
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from . import _types as _t
from ._fallbacks import parse_pkginfo
from ._scm_version import ScmVersion

if TYPE_CHECKING:
    from ._config import Configuration

log = logging.getLogger(__name__)

#: Category labels reported alongside conflicting versions.
GIT_SOURCE = "git"
PKGINFO_SOURCE = "PKG-INFO"


class VersionSourceConflict(LookupError):
    """Raised when the strong version sources disagree.

    Both source categories are reported -- one must never silently override
    the other.
    """

    def __init__(
        self,
        *,
        git_version: str,
        pkginfo_version: str,
        root: _t.PathT,
    ) -> None:
        self.git_version = git_version
        self.pkginfo_version = pkginfo_version
        self.root = Path(root)
        super().__init__(
            f"conflicting versions from strong sources in {self.root}: "
            f"{GIT_SOURCE} reports {git_version} but "
            f"{PKGINFO_SOURCE} reports {pkginfo_version}"
        )


@dataclasses.dataclass(frozen=True)
class VersionSource:
    """A version together with the strong source category it came from."""

    category: str
    version: ScmVersion

    @property
    def version_string(self) -> str:
        return str(self.version.tag)


def _archival_is_unexpanded(path: Path) -> bool:
    """True when *path* is an archival file whose placeholders were not filled.

    ``git archive`` only substitutes placeholders when the archive is created
    from a tree carrying ``.gitattributes`` export-subst rules; an sdist built
    another way ships the raw template.  Such a file carries no version and
    must not count as a source.
    """
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return "$FORMAT" in content.upper()


def _read_git_version(root: Path, config: Configuration) -> ScmVersion | None:
    """Read the version from a live git checkout, if any."""
    from ._backends._git import parse as git_parse

    try:
        return git_parse(root, config)
    except Exception:
        log.debug("git did not yield a version at %s", root, exc_info=True)
        return None


def collect_strong_version_sources(
    root: _t.PathT = ".",
    config: Configuration | None = None,
) -> list[VersionSource]:
    """Collect the valid strong version sources available in *root*.

    Only ``git`` and ``PKG-INFO`` qualify.  An unexpanded archival placeholder
    is ignored.  Weak sources (parent-directory prefix, configured fallback
    version) are never considered, and no source is preferred over another
    here -- :func:`check_version_sources` enforces agreement.
    """
    if config is None:
        from ._config import Configuration

        config = Configuration(root=root)

    root_path = Path(root)
    sources: list[VersionSource] = []

    # Unexpanded archival placeholders are not a valid source (e.g. an sdist
    # carrying a raw .git_archival.txt template); note and ignore them.
    for name in (".git_archival.txt", ".hg_archival.txt"):
        archival = root_path / name
        if archival.is_file() and _archival_is_unexpanded(archival):
            log.debug("ignoring unexpanded archival placeholder %s", archival)

    git_version = _read_git_version(root_path, config)
    if git_version is not None:
        sources.append(VersionSource(GIT_SOURCE, git_version))

    pkginfo_file = root_path / "PKG-INFO"
    if pkginfo_file.is_file():
        pkginfo_version = parse_pkginfo(root_path, config)
        if pkginfo_version is not None:
            sources.append(VersionSource(PKGINFO_SOURCE, pkginfo_version))

    return sources


def _versions_equal(left: ScmVersion, right: ScmVersion) -> bool:
    return str(left.tag) == str(right.tag)


def check_version_sources(
    root: _t.PathT = ".",
    config: Configuration | None = None,
) -> ScmVersion:
    """Check that the strong version sources in *root* agree.

    - Both git and PKG-INFO present and equal: return the version.
    - Exactly one valid source present: return its version.
    - Both present but different: raise :class:`VersionSourceConflict`
      naming both categories.
    - No valid source: raise :class:`LookupError`.

    Unexpanded archival placeholders do not count as a source.  The default
    version interface is untouched by this check.
    """
    sources = collect_strong_version_sources(root, config)

    if not sources:
        raise LookupError(
            f"no strong version source (git or PKG-INFO) found in {Path(root)}"
        )

    first = sources[0]
    for other in sources[1:]:
        if not _versions_equal(first.version, other.version):
            git_source = next(s for s in sources if s.category == GIT_SOURCE)
            pkginfo_source = next(s for s in sources if s.category == PKGINFO_SOURCE)
            raise VersionSourceConflict(
                git_version=git_source.version_string,
                pkginfo_version=pkginfo_source.version_string,
                root=root,
            )

    return first.version

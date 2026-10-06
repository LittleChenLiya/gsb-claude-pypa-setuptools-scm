"""Consistency check between the two strong version sources: git and ``PKG-INFO``.

Only a live git checkout and a populated ``PKG-INFO`` file are treated as
strong sources.  When both are present they have to agree on the version:
silently preferring one over the other would let a stale sdist metadata file
mask the version the repository actually describes, so a mismatch is reported
with both sides named.

Archival markers (``.git_archival.txt`` / ``.hg_archival.txt``) carrying
unexpanded ``$Format:...$`` placeholders, static fallback configuration and
parent directory names are deliberately *not* sources here: they either carry
no usable version or are weak by design, and this check never reorders the
existing source priorities.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from ._config import Configuration
from ._scm_version import ScmVersion
from ._version_cls import Version

log = logging.getLogger(__name__)

#: Source categories covered by the consistency check.
GIT_SOURCE = "git"
PKGINFO_SOURCE = "PKG-INFO"


class VersionSourceConsistencyError(ValueError):
    """The strong version sources disagree on the version."""


@dataclass(frozen=True)
class VersionSource:
    """A strong version source: a category label plus the version it yields.

    ``version`` is the already formatted public version string; categories
    other than :data:`GIT_SOURCE` / :data:`PKGINFO_SOURCE` are not compared.
    """

    category: str
    version: str


def _canonical(version: str) -> str:
    """Normalize a version string for comparison (e.g. ``v1.2`` -> ``1.2``).

    Falls back to the raw string when it does not parse, so unusual but
    intentionally non-normalized versions never raise from this check.
    """
    try:
        return str(Version(version))
    except Exception:
        log.debug("cannot canonicalize version %r, comparing raw", version)
        return version


def version_from_scm_version(scm_version: ScmVersion) -> str:
    """Render the public version string an ``ScmVersion`` describes."""
    return str(scm_version.tag.public)


def check_version_sources_consistency(
    sources: list[VersionSource],
) -> None:
    """Require the strong sources in *sources* to agree on the version.

    Only git and ``PKG-INFO`` entries participate; everything else is
    ignored.  A single strong source (or none) always passes.  With both
    present, canonicalized versions must be equal or
    :class:`VersionSourceConsistencyError` is raised naming both categories
    and their reported versions -- nothing is overridden silently.
    """
    strong = [
        source for source in sources if source.category in (GIT_SOURCE, PKGINFO_SOURCE)
    ]
    if len(strong) < 2:
        return

    first, *rest = strong
    first_canonical = _canonical(first.version)
    mismatches = [
        other for other in rest if _canonical(other.version) != first_canonical
    ]
    if not mismatches:
        return

    reported = ", ".join(
        f"{source.category}={source.version!r}" for source in [first, *mismatches]
    )
    raise VersionSourceConsistencyError(
        "conflicting versions reported by the strong sources "
        f"({reported}); refusing to pick one silently -- make the sources agree"
    )


def pkginfo_source(path: Path, config: Configuration) -> VersionSource | None:
    """Build the ``PKG-INFO`` strong source at *path*, or ``None``.

    A missing or unversioned (``UNKNOWN``) ``PKG-INFO`` is not a strong
    source.
    """
    from ._fallback_workdir import PkgInfoWorkdir

    workdir = PkgInfoWorkdir(path=path, _config=config)
    scm_version = workdir.get_scm_version()
    if scm_version is None:
        return None
    return VersionSource(
        category=PKGINFO_SOURCE,
        version=version_from_scm_version(scm_version),
    )

"""Explicit version-source snapshots.

A :class:`VersionSourceSnapshot` freezes every piece of VCS metadata a
version derivation needs -- the selected tag, the distance from it, the
dirty flag, the branch, the node and the relevant dates -- in a *single*
capture pass.  Formatting a version from the snapshot never re-runs git,
so a worktree that changes *while* a build is running cannot produce a
version that mixes metadata captured at two different moments.

The snapshot stores metadata only -- never file contents.

Both the git command runner and the clock are injectable, which keeps the
capture deterministic in tests and lets embedders route git through their
own execution layer.

The default ``get_version`` pipeline is unchanged: snapshots are an
explicit, opt-in API on top of it.
"""

from __future__ import annotations

import dataclasses
import logging
import shlex
from collections.abc import Callable, Sequence
from datetime import date, datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING

from . import _types as _t
from ._backends._git import (
    _git_parse_describe,
    get_working_directory,
    make_describe_command,
)
from ._run_cmd import CompletedProcess
from ._scm_version import ScmVersion, _time_from_source_date_epoch, meta

if TYPE_CHECKING:
    from ._backends._git import GitWorkdir
    from ._config import Configuration

log = logging.getLogger(__name__)

__all__ = [
    "Clock",
    "RunGit",
    "SnapshotCaptureError",
    "SnapshotSource",
    "VersionSourceSnapshot",
    "capture_git_snapshot",
    "version_from_git_snapshot",
]

#: Injectable git runner: takes argv (without the leading ``git``) and
#: returns a completed process.  Defaults to ``GitWorkdir.run_git``.
RunGit = Callable[[Sequence[str]], CompletedProcess]

#: Injectable clock: returns the "current" time of the capture.
Clock = Callable[[], datetime]


class SnapshotCaptureError(LookupError):
    """Raised when a version-source snapshot cannot be captured.

    A failed capture is a failure -- it never degrades into a made-up
    ``0.0`` version string.
    """


class SnapshotSource(Enum):
    """How the snapshot obtained its base version."""

    DESCRIBE = "describe"
    """A tag was selected by ``git describe``."""

    NO_TAG = "no-tag"
    """No usable tag exists; the base version is the configured fallback."""

    @property
    def tag_available(self) -> bool:
        """Whether a real tag backs this source."""
        return self is SnapshotSource.DESCRIBE


@dataclasses.dataclass(frozen=True)
class VersionSourceSnapshot:
    """Immutable snapshot of version-source metadata from one capture pass.

    All attributes were observed in a single derivation; deriving any number
    of versions from the snapshot reuses them as-is and never re-consults
    the worktree.  Only metadata is stored -- never file contents.
    """

    tag: str | None
    """the raw tag selected by ``git describe``, or None when unavailable"""
    distance: int
    """commits between the tag and HEAD (0 when exactly on the tag)"""
    node: str | None
    """the HEAD node id with a ``g`` prefix, or None for an empty repo"""
    dirty: bool
    """source state for worktree changes: True when the worktree had
    uncommitted changes at capture time"""
    branch: str | None
    """the branch name at capture time, if any"""
    node_date: date | None
    """the commit date of HEAD, falling back to the capture date"""
    source: SnapshotSource
    """source state for tag availability (describe vs. no-tag fallback)"""
    time: datetime
    """the capture time, taken from the injected clock"""

    @property
    def tag_available(self) -> bool:
        """Whether the snapshot is backed by a real tag."""
        return self.source.tag_available

    @property
    def worktree_changed(self) -> bool:
        """Source state for worktree changes (alias of ``dirty``)."""
        return self.dirty

    @property
    def exact(self) -> bool:
        """True when captured exactly on a tag with a clean worktree."""
        return self.distance == 0 and not self.dirty

    def scm_version(self, config: Configuration) -> ScmVersion:
        """Build an :class:`ScmVersion` purely from the frozen metadata."""
        base = self.tag if self.tag is not None else config.fallback_version or "0.0"
        return meta(
            tag=base,
            distance=self.distance,
            dirty=self.dirty,
            node=self.node,
            branch=self.branch,
            node_date=self.node_date,
            time=self.time,
            config=config,
        )

    def format(self, config: Configuration) -> str:
        """Render the final version string from this snapshot.

        The configured version scheme runs exactly once per call, against
        the frozen metadata only.
        """
        from ._version_schemes import format_version

        return format_version(self.scm_version(config))


def _capture_branch(run_git: RunGit) -> str | None:
    res = run_git(["rev-parse", "--abbrev-ref", "HEAD"])
    if res.returncode == 0 and res.stdout.strip():
        return res.stdout.strip()
    res = run_git(["symbolic-ref", "--short", "HEAD"])
    if res.returncode == 0 and res.stdout.strip():
        return res.stdout.strip()
    return None


def _capture_node_date(run_git: RunGit) -> date | None:
    res = run_git(
        ["-c", "log.showSignature=false", "log", "-n", "1", "HEAD", "--format=%cI"]
    )
    if res.returncode != 0:
        return None
    text = res.stdout.strip()
    if not text or "%c" in text:
        # empty repo, or git too old to support %cI
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        log.debug("unparseable git commit date %r", text)
        return None
    return parsed.astimezone(timezone.utc).date()


def _describe_args(
    config: Configuration, describe_command: _t.CMD_TYPE | None
) -> list[str]:
    if describe_command is None:
        describe_command = config.scm.git.describe_command
    if describe_command is None:
        return make_describe_command(config.tag.describe_match_glob())[1:]
    if isinstance(describe_command, str):
        args = shlex.split(describe_command)
    else:
        args = [str(a) for a in describe_command]
    if args and args[0] == "git":
        args = args[1:]
    return args


def capture_git_snapshot(
    config: Configuration,
    *,
    wd: GitWorkdir | None = None,
    run_git: RunGit | None = None,
    now: Clock | None = None,
    describe_command: _t.CMD_TYPE | None = None,
) -> VersionSourceSnapshot:
    """Capture all version-source metadata of a git worktree in one pass.

    Every git command needed for the derivation runs here, exactly once;
    the returned snapshot is self-contained.  A capture failure raises
    :class:`SnapshotCaptureError` -- it never degrades into a ``0.0``
    version.

    Parameters:
        config: the version configuration
        wd: optional workdir whose ``run_git`` is used by default
        run_git: injectable git runner (takes precedence over *wd*)
        now: injectable clock; defaults to ``SOURCE_DATE_EPOCH`` or utcnow
        describe_command: overrides ``scm.git.describe_command``
    """
    if run_git is None:
        if wd is None:
            wd = get_working_directory(config, config.root)
        if wd is None:
            raise SnapshotCaptureError(
                f"no git worktree found for root {config.root!r};"
                " cannot capture a version-source snapshot"
            )
        run_git = wd.run_git

    clock: Clock = (
        now
        if now is not None
        else lambda: _time_from_source_date_epoch(config.env.source_date_epoch)
    )
    capture_time = clock()

    probe = run_git(["rev-parse", "--git-dir"])
    if probe.returncode != 0:
        raise SnapshotCaptureError(
            f"git cannot answer for root {config.root!r}"
            f" (rev-parse exited {probe.returncode}: {probe.stderr.strip()!r});"
            " a failed capture is an error, not a 0.0 version"
        )

    describe_res = run_git(_describe_args(config, describe_command))

    tag: str | None
    node: str | None
    if describe_res.returncode == 0 and describe_res.stdout.strip():
        tag, distance, node, dirty = _git_parse_describe(describe_res.stdout.strip())
        source = SnapshotSource.DESCRIBE
    else:
        source = SnapshotSource.NO_TAG
        tag = None
        node_res = run_git(["rev-parse", "--verify", "--quiet", "HEAD"])
        raw_node = node_res.stdout.strip() if node_res.returncode == 0 else ""
        if raw_node:
            node = "g" + raw_node
            revs = run_git(["rev-list", "HEAD"])
            distance = revs.stdout.count("\n") + 1 if revs.returncode == 0 else 0
            status = run_git(["status", "--porcelain", "--untracked-files=no"])
            # an unreadable worktree status is treated as changed, never clean
            dirty = bool(status.stdout.strip()) if status.returncode == 0 else True
        else:
            # repository without any commit yet
            node = None
            distance = 0
            dirty = True

    branch = _capture_branch(run_git)
    node_date = _capture_node_date(run_git)
    if node_date is None:
        node_date = capture_time.date()

    snapshot = VersionSourceSnapshot(
        tag=tag,
        distance=distance,
        node=node,
        dirty=dirty,
        branch=branch,
        node_date=node_date,
        source=source,
        time=capture_time,
    )
    log.debug("captured version-source snapshot %r", snapshot)
    return snapshot


def version_from_git_snapshot(
    config: Configuration,
    *,
    wd: GitWorkdir | None = None,
    run_git: RunGit | None = None,
    now: Clock | None = None,
    describe_command: _t.CMD_TYPE | None = None,
) -> str:
    """Capture a snapshot and render the version string from it, scheme once."""
    return capture_git_snapshot(
        config, wd=wd, run_git=run_git, now=now, describe_command=describe_command
    ).format(config)

"""Tests for setuptools-scm specific dump_version functionality.

Core version scheme tests have been moved to vcs-versioning/testing_vcs/test_version_schemes.py
"""

from __future__ import annotations

import os
import shutil
import subprocess

from datetime import datetime
from datetime import timezone
from pathlib import Path

import pytest

from setuptools_scm import Configuration
from setuptools_scm import dump_version
from setuptools_scm import get_version
from setuptools_scm.version import meta
from vcs_versioning._overrides import PRETEND_KEY

c = Configuration()

# Use explicit time to avoid triggering auto-creation of GlobalOverrides at import time
VERSIONS = {
    "exact": meta(
        "1.1",
        distance=0,
        dirty=False,
        config=c,
        time=datetime(2009, 2, 13, 23, 31, 30, tzinfo=timezone.utc),
    ),
}


def test_dump_version_doesnt_bail_on_value_error(tmp_path: Path) -> None:
    write_to = "VERSION"
    version = str(VERSIONS["exact"].tag)
    scm_version = meta(VERSIONS["exact"].tag, config=c)
    with pytest.raises(ValueError, match=r"^bad file format:"):
        dump_version(tmp_path, version, write_to, scm_version=scm_version)


@pytest.mark.parametrize(
    "version", ["1.0", "1.2.3.dev1+ge871260", "1.2.3.dev15+ge871260.d20180625"]
)
def test_dump_version_works_with_pretend(
    version: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(PRETEND_KEY, version)
    name = "VERSION.txt"
    target = tmp_path.joinpath(name)
    get_version(root=tmp_path, write_to=name)
    assert target.read_text(encoding="utf-8") == version


def test_dump_version_modern(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    version = "1.2.3"
    monkeypatch.setenv(PRETEND_KEY, version)
    name = "VERSION.txt"

    project = tmp_path.joinpath("project")
    target = project.joinpath(name)
    project.mkdir()

    get_version(root="..", relative_to=target, version_file=name)
    assert target.read_text(encoding="utf-8") == version


def dump_a_version(tmp_path: Path) -> None:
    from vcs_versioning._dump_version import write_version_to_path

    version = "1.2.3"
    scm_version = meta(version, config=c)
    write_version_to_path(
        tmp_path / "VERSION.py", template=None, version=version, scm_version=scm_version
    )


def test_dump_version_on_old_python(tmp_path: Path) -> None:
    python37 = shutil.which("python3.7")
    if python37 is None:
        pytest.skip("python3.7 not found")
    dump_a_version(tmp_path)
    subprocess.run(
        [python37, "-c", "import VERSION;print(VERSION.version)"],
        cwd=tmp_path,
        check=True,
    )


def test_dump_version_mypy(tmp_path: Path) -> None:
    uvx = shutil.which("uvx")
    if uvx is None:
        pytest.skip("uvx not found")
    dump_a_version(tmp_path)
    # Use mypy 1.11.2 - last version supporting Python 3.8
    subprocess.run(
        [uvx, "mypy==1.11.2", "--python-version=3.8", "--strict", "VERSION.py"],
        cwd=tmp_path,
        check=True,
    )


def test_dump_version_flake8(tmp_path: Path) -> None:
    flake8 = shutil.which("flake8")
    if flake8 is None:
        pytest.skip("flake8 not found")
    dump_a_version(tmp_path)
    subprocess.run([flake8, "VERSION.py"], cwd=tmp_path, check=True)


def test_dump_version_ruff(tmp_path: Path) -> None:
    ruff = shutil.which("ruff")
    if ruff is None:
        pytest.skip("ruff not found")
    dump_a_version(tmp_path)
    subprocess.run([ruff, "check", "--no-fix", "VERSION.py"], cwd=tmp_path, check=True)


def test_write_version_to_path_deprecation_warning_none(tmp_path: Path) -> None:
    """Test that write_version_to_path warns when scm_version=None is passed."""
    from vcs_versioning._dump_version import write_version_to_path

    target_file = tmp_path / "version.py"

    # This should raise a deprecation warning when scm_version=None is not explicitly passed
    with pytest.warns(
        DeprecationWarning, match="write_version_to_path called without scm_version"
    ):
        write_version_to_path(
            target=target_file,
            template=None,  # Use default template
            version="1.2.3",
        )

    # Verify the file was created and contains the expected content
    assert target_file.exists()
    content = target_file.read_text(encoding="utf-8")

    # Check that the version is correctly formatted
    assert "__version__ = version = '1.2.3'" in content
    assert "__version_tuple__ = version_tuple = (1, 2, 3)" in content

    # Check that commit_id is set to None when scm_version is None
    assert "__commit_id__ = commit_id = None" in content


def test_write_version_to_path_deprecation_warning_missing(tmp_path: Path) -> None:
    """Test that write_version_to_path warns when scm_version parameter is not provided."""
    from vcs_versioning._dump_version import write_version_to_path

    target_file = tmp_path / "version.py"

    # This should raise a deprecation warning when scm_version is not provided
    with pytest.warns(
        DeprecationWarning, match="write_version_to_path called without scm_version"
    ):
        write_version_to_path(
            target=target_file,
            template=None,  # Use default template
            version="1.2.3",
            # scm_version not provided - should warn
        )

    # Verify the file was created and contains the expected content
    assert target_file.exists()
    content = target_file.read_text(encoding="utf-8")

    # Check that the version is correctly formatted
    assert "__version__ = version = '1.2.3'" in content
    assert "__version_tuple__ = version_tuple = (1, 2, 3)" in content

    # Check that commit_id is set to None when scm_version is None
    assert "__commit_id__ = commit_id = None" in content


def test_write_version_to_path_skips_unchanged_content(tmp_path: Path) -> None:
    """Same content must short-circuit: no rewrite, mtime preserved."""
    from vcs_versioning._dump_version import write_version_to_path

    target_file = tmp_path / "version.txt"
    version = "1.2.3"
    scm_version = meta(version, config=c)

    assert write_version_to_path(
        target_file, template=None, version=version, scm_version=scm_version
    )

    expected = target_file.read_bytes()
    # pin an old mtime so a rewrite would be visible regardless of fs granularity
    old_mtime_ns = 1_000_000_000
    os.utime(target_file, ns=(old_mtime_ns, old_mtime_ns))

    changed = write_version_to_path(
        target_file, template=None, version=version, scm_version=scm_version
    )

    assert changed is False
    assert target_file.read_bytes() == expected
    assert target_file.stat().st_mtime_ns == old_mtime_ns


def test_write_version_to_path_rewrites_changed_content(tmp_path: Path) -> None:
    """Different content is safely replaced and the new content is accurate."""
    from vcs_versioning._dump_version import write_version_to_path

    target_file = tmp_path / "version.txt"
    scm_version = meta("1.2.3", config=c)

    write_version_to_path(
        target_file, template=None, version="1.2.3", scm_version=scm_version
    )
    old_mtime_ns = 1_000_000_000
    os.utime(target_file, ns=(old_mtime_ns, old_mtime_ns))

    new_scm_version = meta("2.0.0", config=c)
    changed = write_version_to_path(
        target_file, template=None, version="2.0.0", scm_version=new_scm_version
    )

    assert changed is True
    assert target_file.read_text(encoding="utf-8") == "2.0.0"
    assert target_file.stat().st_mtime_ns != old_mtime_ns


def test_write_version_to_path_bad_template_keeps_old_file(tmp_path: Path) -> None:
    """A failing render leaves the previously written file untouched."""
    from vcs_versioning._dump_version import write_version_to_path

    scm_version = meta("1.2.3", config=c)

    # unknown suffix -> ValueError while validating the default template
    bad_suffix = tmp_path / "VERSION"
    bad_suffix.write_text("precious old content", encoding="utf-8")
    with pytest.raises(ValueError, match=r"^bad file format:"):
        write_version_to_path(
            bad_suffix, template=None, version="9.9.9", scm_version=scm_version
        )
    assert bad_suffix.read_text(encoding="utf-8") == "precious old content"

    # unknown placeholder in the template -> KeyError while rendering
    target_file = tmp_path / "version.txt"
    write_version_to_path(
        target_file, template="{version}", version="1.2.3", scm_version=scm_version
    )
    old_bytes = target_file.read_bytes()
    old_mtime_ns = target_file.stat().st_mtime_ns
    with pytest.raises(KeyError):
        write_version_to_path(
            target_file,
            template="{does_not_exist}",
            version="1.2.3",
            scm_version=scm_version,
        )
    assert target_file.read_bytes() == old_bytes
    assert target_file.stat().st_mtime_ns == old_mtime_ns


def test_write_version_to_path_compares_bytes_exactly(tmp_path: Path) -> None:
    """Encoding and trailing-newline differences count as a change."""
    from vcs_versioning._dump_version import write_version_to_path

    target_file = tmp_path / "version.txt"
    scm_version = meta("1.2.3", config=c)

    # pre-existing content without the template's trailing newline -> rewrite
    target_file.write_text("1.2.3", encoding="utf-8")
    changed = write_version_to_path(
        target_file, template="{version}\n", version="1.2.3", scm_version=scm_version
    )
    assert changed is True
    assert target_file.read_bytes() == b"1.2.3\n"

    # same rendered bytes now -> short-circuit
    old_mtime_ns = 1_000_000_000
    os.utime(target_file, ns=(old_mtime_ns, old_mtime_ns))
    changed = write_version_to_path(
        target_file, template="{version}\n", version="1.2.3", scm_version=scm_version
    )
    assert changed is False
    assert target_file.stat().st_mtime_ns == old_mtime_ns

    # a differently encoded character is detected via the byte comparison
    unicode_target = tmp_path / "unicode.txt"
    unicode_target.write_text("\u00e9\n", encoding="latin-1")
    changed = write_version_to_path(
        unicode_target,
        template="\u00e9\n",
        version="1.2.3",
        scm_version=scm_version,
    )
    assert changed is True
    assert unicode_target.read_text(encoding="utf-8") == "\u00e9\n"


def test_dump_version_skips_unchanged_content(tmp_path: Path) -> None:
    """dump_version (the write_to path) keeps the no-op rewrite behavior."""
    version = "1.2.3"
    scm_version = meta(version, config=c)

    dump_version(tmp_path, version, "first.txt", scm_version=scm_version)
    target_file = tmp_path / "first.txt"
    expected = target_file.read_bytes()
    old_mtime_ns = 1_000_000_000
    os.utime(target_file, ns=(old_mtime_ns, old_mtime_ns))

    dump_version(tmp_path, version, "first.txt", scm_version=scm_version)

    assert target_file.read_bytes() == expected
    assert target_file.stat().st_mtime_ns == old_mtime_ns

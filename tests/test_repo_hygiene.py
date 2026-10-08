"""Guards for what may be committed: no secrets, no large data files.

Runs against the git index (tracked and staged files), so it also catches a
large file that was `git add`-ed but not committed yet.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from etl.config import PROJECT_ROOT

MAX_TRACKED_DATA_BYTES = 5 * 1024 * 1024  # the simulator output is far below this


def _tracked_files() -> list[str]:
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=PROJECT_ROOT, capture_output=True, text=True
    )
    if result.returncode != 0:
        pytest.skip("not a git repository")
    return [path for path in result.stdout.split("\0") if path]


def test_env_file_is_not_tracked() -> None:
    assert ".env" not in _tracked_files()


def test_no_large_files_under_data() -> None:
    too_big = {
        path: size
        for path in _tracked_files()
        if path.startswith("data/")
        and (PROJECT_ROOT / path).exists()
        and (size := (PROJECT_ROOT / path).stat().st_size) > MAX_TRACKED_DATA_BYTES
    }
    assert not too_big, f"data files over {MAX_TRACKED_DATA_BYTES} bytes are tracked: {too_big}"

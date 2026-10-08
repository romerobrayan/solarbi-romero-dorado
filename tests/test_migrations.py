"""Unit tests for etl/migrations.py; they need no database."""

from __future__ import annotations

from pathlib import Path

import pytest

from etl.migrations import (
    MIGRATIONS_DIR,
    AppliedMigration,
    MigrationError,
    checksum,
    discover,
    pending,
)


def _write(directory: Path, name: str, text: str = "SELECT 1;\n") -> None:
    (directory / name).write_text(text, encoding="utf-8", newline="")


def _applied(migration_dir: Path, *versions: int) -> list[AppliedMigration]:
    by_version = {m.version: m for m in discover(migration_dir)}
    return [
        AppliedMigration(version=v, name=by_version[v].name, checksum=by_version[v].checksum)
        for v in versions
    ]


def test_repository_migrations_are_valid_and_only_0001_runs_as_owner() -> None:
    migrations = discover(MIGRATIONS_DIR)

    assert [m.version for m in migrations] == list(range(1, len(migrations) + 1))
    assert [m.label for m in migrations if m.run_as_owner] == ["0001_etl_writer_role"]


def test_discover_orders_by_version(tmp_path: Path) -> None:
    _write(tmp_path, "0002_second.sql")
    _write(tmp_path, "0001_first.sql")

    assert [m.label for m in discover(tmp_path)] == ["0001_first", "0002_second"]


@pytest.mark.parametrize("name", ["1_short.sql", "0001-dash.sql", "0001_Upper.sql", "0001_.sql"])
def test_badly_named_file_is_refused(tmp_path: Path, name: str) -> None:
    _write(tmp_path, name)
    with pytest.raises(MigrationError, match="must be named NNNN_description.sql"):
        discover(tmp_path)


def test_duplicate_versions_are_refused(tmp_path: Path) -> None:
    _write(tmp_path, "0001_a.sql")
    _write(tmp_path, "0001_b.sql")
    with pytest.raises(MigrationError, match="duplicate migration versions"):
        discover(tmp_path)


def test_checksum_ignores_line_endings() -> None:
    assert checksum("SELECT 1;\r\nSELECT 2;\r\n") == checksum("SELECT 1;\nSELECT 2;\n")
    assert checksum("SELECT 1;\n") != checksum("SELECT 2;\n")


def test_run_as_owner_is_read_from_the_leading_comments_only(tmp_path: Path) -> None:
    _write(tmp_path, "0001_owner.sql", "-- migrate:run-as owner\n-- why\nCREATE ROLE x;\n")
    _write(tmp_path, "0002_default.sql", "CREATE TABLE t ();\n-- migrate:run-as owner\n")

    owner, default = discover(tmp_path)
    assert owner.run_as_owner
    assert not default.run_as_owner


def test_nothing_pending_when_everything_is_applied(tmp_path: Path) -> None:
    _write(tmp_path, "0001_a.sql")
    _write(tmp_path, "0002_b.sql")

    assert pending(discover(tmp_path), _applied(tmp_path, 1, 2)) == []
    assert [m.label for m in pending(discover(tmp_path), _applied(tmp_path, 1))] == ["0002_b"]


def test_editing_an_applied_migration_is_an_error(tmp_path: Path) -> None:
    _write(tmp_path, "0001_a.sql", "SELECT 1;\n")
    applied = _applied(tmp_path, 1)
    _write(tmp_path, "0001_a.sql", "SELECT 2;\n")

    with pytest.raises(MigrationError, match="0001_a was modified after it was applied"):
        pending(discover(tmp_path), applied)


def test_deleting_an_applied_migration_is_an_error(tmp_path: Path) -> None:
    _write(tmp_path, "0001_a.sql")
    applied = _applied(tmp_path, 1)
    (tmp_path / "0001_a.sql").unlink()

    with pytest.raises(MigrationError, match="applied but its file is missing"):
        pending(discover(tmp_path), applied)


def test_new_migration_older_than_the_latest_applied_is_an_error(tmp_path: Path) -> None:
    _write(tmp_path, "0002_b.sql")
    applied = _applied(tmp_path, 2)
    _write(tmp_path, "0001_late.sql")

    with pytest.raises(MigrationError, match="0001_late is older than the latest applied"):
        pending(discover(tmp_path), applied)

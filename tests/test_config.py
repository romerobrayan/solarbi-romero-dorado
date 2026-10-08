"""Unit tests for etl.config; they need no database."""

from __future__ import annotations

from pathlib import Path

import pytest

from etl.config import ETL_ROLE, PROJECT_ROOT, ConfigError, load_settings

BASE_ENV = {
    "POSTGRES_HOST": "127.0.0.1",
    "POSTGRES_PORT": "5432",
    "POSTGRES_DB": "solarbi",
    "POSTGRES_USER": "solarbi_owner",
    "POSTGRES_PASSWORD": "p@ss:w/rd#1",  # dummy value for tests
    "ETL_WRITER_PASSWORD": "etl-dummy",
}


def test_url_is_built_from_parts_and_percent_encoded() -> None:
    settings = load_settings(BASE_ENV)
    assert (
        settings.admin_db.url
        == "postgresql://solarbi_owner:p%40ss%3Aw%2Frd%231@127.0.0.1:5432/solarbi"
    )


def test_etl_connects_as_etl_writer_and_admin_as_owner() -> None:
    settings = load_settings(BASE_ENV)
    assert settings.db.user == ETL_ROLE == "etl_writer"
    assert settings.admin_db.user == "solarbi_owner"
    assert (settings.db.host, settings.db.port, settings.db.name) == ("127.0.0.1", 5432, "solarbi")


def test_password_never_appears_in_repr_or_safe_url() -> None:
    settings = load_settings(BASE_ENV)
    assert "p@ss" not in repr(settings)
    assert "etl-dummy" not in repr(settings)
    assert settings.admin_db.safe_url == "postgresql://solarbi_owner:***@127.0.0.1:5432/solarbi"


@pytest.mark.parametrize(
    "missing",
    ["POSTGRES_PORT", "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD", "ETL_WRITER_PASSWORD"],
)
def test_missing_required_variable_raises(missing: str) -> None:
    env = {k: v for k, v in BASE_ENV.items() if k != missing}
    with pytest.raises(ConfigError, match=missing):
        load_settings(env)


def test_invalid_port_raises() -> None:
    with pytest.raises(ConfigError, match="POSTGRES_PORT"):
        load_settings({**BASE_ENV, "POSTGRES_PORT": "abc"})


def test_data_paths_default_to_repo_and_accept_overrides(tmp_path: Path) -> None:
    defaults = load_settings(BASE_ENV).paths
    assert defaults.bronze_dir == PROJECT_ROOT / "data" / "bronze"
    assert defaults.contracts_dir == PROJECT_ROOT / "contracts"

    overridden = load_settings({**BASE_ENV, "BRONZE_DIR": str(tmp_path)}).paths
    assert overridden.bronze_dir == tmp_path
    assert overridden.silver_dir == PROJECT_ROOT / "data" / "silver"

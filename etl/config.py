"""Runtime settings, read from environment variables and an optional .env file.

No secret lives in code: credentials always come from the environment.
Real environment variables take precedence over values in .env.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Non-superuser role that runs the ETL and owns the tables (created by migration 0001).
ETL_ROLE = "etl_writer"


class ConfigError(RuntimeError):
    """A required setting is missing or invalid."""


@dataclass(frozen=True)
class DatabaseSettings:
    host: str
    port: int
    name: str
    user: str
    password: str = field(repr=False)

    @property
    def url(self) -> str:
        """libpq connection URL; user, password and name are percent-encoded."""
        return self._build_url(quote(self.password, safe=""))

    @property
    def safe_url(self) -> str:
        """Same URL with the password masked, for logs and error messages."""
        return self._build_url("***")

    def _build_url(self, password: str) -> str:
        user = quote(self.user, safe="")
        name = quote(self.name, safe="")
        return f"postgresql://{user}:{password}@{self.host}:{self.port}/{name}"


@dataclass(frozen=True)
class PathSettings:
    data_dir: Path
    bronze_dir: Path
    silver_dir: Path
    samples_dir: Path
    contracts_dir: Path


@dataclass(frozen=True)
class Settings:
    db: DatabaseSettings  # ETL runtime: etl_writer
    admin_db: DatabaseSettings  # database owner: migrations and environment checks
    paths: PathSettings


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    """Build the settings object.

    With no argument, loads PROJECT_ROOT/.env (without overriding variables that
    are already set) and reads os.environ. Tests pass an explicit mapping.
    """
    if environ is None:
        load_dotenv(PROJECT_ROOT / ".env", override=False)
        environ = os.environ

    host = environ.get("POSTGRES_HOST") or "127.0.0.1"
    port = _port(_require(environ, "POSTGRES_PORT"))
    name = _require(environ, "POSTGRES_DB")
    db = DatabaseSettings(
        host=host,
        port=port,
        name=name,
        user=ETL_ROLE,
        password=_require(environ, "ETL_WRITER_PASSWORD"),
    )
    admin_db = DatabaseSettings(
        host=host,
        port=port,
        name=name,
        user=_require(environ, "POSTGRES_USER"),
        password=_require(environ, "POSTGRES_PASSWORD"),
    )

    data_dir = _path(environ, "DATA_DIR", PROJECT_ROOT / "data")
    paths = PathSettings(
        data_dir=data_dir,
        bronze_dir=_path(environ, "BRONZE_DIR", data_dir / "bronze"),
        silver_dir=_path(environ, "SILVER_DIR", data_dir / "silver"),
        samples_dir=_path(environ, "SAMPLES_DIR", data_dir / "samples"),
        contracts_dir=_path(environ, "CONTRACTS_DIR", PROJECT_ROOT / "contracts"),
    )
    return Settings(db=db, admin_db=admin_db, paths=paths)


def _require(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name)
    if not value:
        raise ConfigError(
            f"Missing environment variable {name}. Copy .env.example to .env and fill it in."
        )
    return value


def _port(raw: str) -> int:
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"POSTGRES_PORT must be an integer, got {raw!r}.") from exc


def _path(environ: Mapping[str, str], name: str, default: Path) -> Path:
    raw = environ.get(name)
    if not raw:
        return default
    path = Path(raw)
    return path if path.is_absolute() else PROJECT_ROOT / path

"""Minimal Grafana HTTP API client and dashboard JSON normalization.

Used by scripts/export_grafana.py (dashboards as code) and scripts/replay_live.py
(to print the alert state). Admin credentials come from .env, like the rest of
the settings; nothing is hardcoded.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from dotenv import load_dotenv

from etl.config import PROJECT_ROOT, ConfigError

# Keys Grafana adds or bumps on every save; they would make the exported JSON churn.
VOLATILE_KEYS = ("id", "version", "iteration")
TIMEOUT_SECONDS = 15


@dataclass(frozen=True)
class GrafanaSettings:
    url: str
    user: str
    password: str = field(repr=False)


def load_grafana_settings(environ: Mapping[str, str] | None = None) -> GrafanaSettings:
    if environ is None:
        load_dotenv(PROJECT_ROOT / ".env", override=False)
        environ = os.environ
    missing = [
        name
        for name in ("GRAFANA_PORT", "GRAFANA_ADMIN_USER", "GRAFANA_ADMIN_PASSWORD")
        if not environ.get(name)
    ]
    if missing:
        raise ConfigError(f"Missing environment variables: {', '.join(missing)} (see .env.example)")
    host = environ.get("GRAFANA_HOST") or "127.0.0.1"
    return GrafanaSettings(
        url=f"http://{host}:{environ['GRAFANA_PORT']}",
        user=environ["GRAFANA_ADMIN_USER"],
        password=environ["GRAFANA_ADMIN_PASSWORD"],
    )


class GrafanaError(RuntimeError):
    """The Grafana API answered with an error or could not be reached."""


class GrafanaClient:
    def __init__(self, settings: GrafanaSettings) -> None:
        self.settings = settings
        token = base64.b64encode(f"{settings.user}:{settings.password}".encode()).decode()
        self._headers = {
            "Authorization": f"Basic {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def get(self, path: str) -> Any:
        return self._request("GET", path)

    def post(self, path: str, body: Any) -> Any:
        return self._request("POST", path, body)

    def _request(self, method: str, path: str, body: Any = None) -> Any:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(
            self.settings.url + path, data=data, method=method, headers=self._headers
        )
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            raise GrafanaError(f"{method} {path} -> HTTP {exc.code}: {detail}") from exc
        except OSError as exc:
            raise GrafanaError(
                f"{method} {path}: Grafana not reachable at {self.settings.url}"
            ) from exc
        return json.loads(payload) if payload else None


def normalize_dashboard(dashboard: Mapping[str, Any]) -> str:
    """Stable JSON text for the repo: sorted keys, no volatile keys, trailing newline."""
    clean = {key: value for key, value in dashboard.items() if key not in VOLATILE_KEYS}
    return json.dumps(clean, indent=2, ensure_ascii=False, sort_keys=True) + "\n"

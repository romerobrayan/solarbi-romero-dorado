"""Export the provisioned dashboards from Grafana into grafana/dashboards/ (normalized).

The JSON files in the repo are the source of truth. Edit a dashboard in the Grafana
UI if it is easier, then run this script and commit the result; it rewrites each
file with sorted keys and without the keys Grafana bumps on every save (id,
version), so an unchanged dashboard produces no diff.

Usage, from the repo root with the virtual environment active and Grafana running:
    python scripts/export_grafana.py           # every dashboard file in grafana/dashboards/
    python scripts/export_grafana.py --check   # exit 1 if a file differs from Grafana

Exit codes: 0 = OK, 1 = differences found with --check, 2 = configuration/API error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from etl.config import PROJECT_ROOT, ConfigError
from etl.grafana import GrafanaClient, GrafanaError, load_grafana_settings, normalize_dashboard

DASHBOARDS_DIR = PROJECT_ROOT / "grafana" / "dashboards"


def export(client: GrafanaClient, path: Path, check: bool) -> bool:
    """Write (or compare) one dashboard file; returns True if it changed or differs."""
    uid = json.loads(path.read_text(encoding="utf-8"))["uid"]
    dashboard = client.get(f"/api/dashboards/uid/{uid}")["dashboard"]
    exported = normalize_dashboard(dashboard)
    current = path.read_text(encoding="utf-8")
    changed = exported != current
    label = path.relative_to(PROJECT_ROOT).as_posix()
    if check:
        print(f"{label}: {'DIFFERS from Grafana' if changed else 'matches Grafana'}")
    else:
        if changed:
            path.write_text(exported, encoding="utf-8", newline="\n")
        print(f"{label}: {'updated from Grafana' if changed else 'unchanged'} (uid {uid})")
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export Grafana dashboards to the repo.")
    parser.add_argument("--check", action="store_true", help="only compare, do not write")
    args = parser.parse_args(argv)
    try:
        client = GrafanaClient(load_grafana_settings())
        changed = [
            export(client, path, args.check) for path in sorted(DASHBOARDS_DIR.glob("*.json"))
        ]
    except (ConfigError, GrafanaError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 1 if args.check and any(changed) else 0


if __name__ == "__main__":
    sys.exit(main())

"""Grafana as code: the committed dashboard and alerting files (no Grafana needed)."""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
import yaml

from etl.config import PROJECT_ROOT
from etl.contract import Contract, load_contract
from etl.grafana import normalize_dashboard

GRAFANA = PROJECT_ROOT / "grafana"
DASHBOARD = GRAFANA / "dashboards" / "solarbi-operacion.json"
ALERTING = GRAFANA / "provisioning" / "alerting"
DATASOURCE_UID = "solarbi-postgres"
WRITE_SQL = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|COPY)\b", re.IGNORECASE
)


@pytest.fixture(scope="module")
def contract() -> Contract:
    return load_contract(PROJECT_ROOT / "contracts" / "telemetria.yaml")


@pytest.fixture(scope="module")
def dashboard() -> dict[str, Any]:
    return json.loads(DASHBOARD.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def rules() -> dict[str, dict[str, Any]]:
    groups = yaml.safe_load((ALERTING / "rules.yaml").read_text(encoding="utf-8"))["groups"]
    return {r["uid"]: r for group in groups for r in group["rules"]}


@pytest.fixture(scope="module")
def rule(rules: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return rules["solarbi-potencia-cero"]


@pytest.fixture(scope="module")
def silent_rule(rules: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return rules["solarbi-sin-datos"]


def _panel(dashboard: dict[str, Any], panel_id: int) -> dict[str, Any]:
    return next(p for p in dashboard["panels"] if p["id"] == panel_id)


def _all_sql(dashboard: dict[str, Any]) -> list[str]:
    queries = [t["rawSql"] for p in dashboard["panels"] for t in p["targets"]]
    queries += [v["query"] for v in dashboard["templating"]["list"]]
    queries += [
        a["target"]["rawSql"]
        for a in dashboard["annotations"]["list"]
        if "rawSql" in a.get("target", {})
    ]
    return queries


def _anchor(heading: str) -> str:
    """GitHub-style anchor of a Markdown heading."""
    text = re.sub(r"[^\w\- ]", "", heading.strip().lower())
    return text.replace(" ", "-")


# --- dashboard -------------------------------------------------------------------------------


def test_committed_dashboard_is_normalized(dashboard: dict[str, Any]) -> None:
    """The file is exactly what scripts/export_grafana.py writes (no churn on export)."""
    assert DASHBOARD.read_text(encoding="utf-8") == normalize_dashboard(dashboard)
    assert "id" not in dashboard and "version" not in dashboard
    assert dashboard["uid"] == "solarbi-operacion"


def test_dashboard_settings(dashboard: dict[str, Any]) -> None:
    assert dashboard["timezone"] == "America/Bogota"
    assert dashboard["time"] == {"from": "now-7d", "to": "now"}  # no future data on screen
    assert dashboard["refresh"] == "30s"
    variables = {v["name"]: v for v in dashboard["templating"]["list"]}
    assert set(variables) == {"dispositivo", "dia"}
    assert "silver.lectura_5min" in variables["dispositivo"]["query"]
    assert "dwh.fact_energia_dia" in variables["dia"]["query"]


def test_time_series_panel_uses_time_filter_and_contract_threshold(
    dashboard: dict[str, Any], contract: Contract
) -> None:
    panel = _panel(dashboard, 1)
    assert panel["type"] == "timeseries"
    for target in panel["targets"]:
        assert "$__timeFilter(ts)" in target["rawSql"]
        assert "dispositivo_id IN ($dispositivo)" in target["rawSql"]
    zero_power = next(r for r in contract.fault_rules if r.id == "zero_power_daylight")
    steps = panel["fieldConfig"]["defaults"]["thresholds"]["steps"]
    assert steps[-1]["value"] == zero_power.threshold
    assert panel["fieldConfig"]["defaults"]["custom"]["thresholdsStyle"]["mode"] == "line"


def test_hourly_panel_uses_time_group_alias(dashboard: dict[str, Any]) -> None:
    sql = _panel(dashboard, 2)["targets"][0]["rawSql"]
    assert sql.startswith("SELECT $__timeGroupAlias(ts, '1h'),")
    assert "avg(p_ac_kw)" in sql and "$__timeFilter(ts)" in sql


def test_stat_panel_reads_gold_for_the_selected_day(dashboard: dict[str, Any]) -> None:
    panel = _panel(dashboard, 3)
    sql = panel["targets"][0]["rawSql"]
    assert panel["type"] == "stat"
    assert "FROM dwh.fact_energia_dia" in sql and "f.fecha_key = $dia" in sql
    # % valid as a ratio of sums, the same definition as the Power BI measure
    assert "sum(f.lecturas_validas) / sum(f.lecturas_esperadas)" in sql


def test_fault_events_feed_annotations_and_a_table(dashboard: dict[str, Any]) -> None:
    annotations = {a["name"]: a for a in dashboard["annotations"]["list"]}
    faults = annotations["Eventos de falla (dq.fault_event)"]["target"]["rawSql"]
    assert "FROM dq.fault_event" in faults and "$__timeFilter(ts_inicio)" in faults
    region = annotations["Horario solar (09:00–15:00)"]["target"]["timeRegion"]
    assert (region["from"], region["to"], region["timezone"]) == (
        "09:00",
        "15:00",
        "America/Bogota",
    )
    assert "FROM dq.fault_event" in _panel(dashboard, 6)["targets"][0]["rawSql"]


def test_every_query_is_read_only_and_uses_the_provisioned_datasource(
    dashboard: dict[str, Any],
) -> None:
    for sql in _all_sql(dashboard):
        assert not WRITE_SQL.search(sql), sql
    for panel in dashboard["panels"]:
        assert panel["datasource"]["uid"] == DATASOURCE_UID
        assert all(t["datasource"]["uid"] == DATASOURCE_UID for t in panel["targets"])


def test_normalize_dashboard_drops_volatile_keys_and_sorts() -> None:
    text = normalize_dashboard({"version": 7, "uid": "x", "id": 3, "iteration": 1, "b": 1, "a": 2})
    assert text == '{\n  "a": 2,\n  "b": 1,\n  "uid": "x"\n}\n'
    assert normalize_dashboard(json.loads(text)) == text


# --- alerting ----------------------------------------------------------------------------------


def test_alert_rule_matches_the_contract(rule: dict[str, Any], contract: Contract) -> None:
    zero_power = next(r for r in contract.fault_rules if r.id == "zero_power_daylight")
    site_tz = contract.sites[0].timezone.key
    sql = rule["data"][0]["model"]["rawSql"]
    assert f"p_ac_kw {zero_power.operator} {zero_power.threshold}" in sql
    assert f"(ts AT TIME ZONE '{site_tz}')::time >= TIME '{zero_power.window.start:%H:%M}'" in sql
    assert f"(ts AT TIME ZONE '{site_tz}')::time <  TIME '{zero_power.window.end:%H:%M}'" in sql
    assert "$__timeFilter(ts)" in sql
    assert "$$" not in yaml.safe_dump(rule)  # rule files are not env-interpolated by Grafana


def test_alert_rule_settings(rule: dict[str, Any], contract: Contract) -> None:
    zero_power = next(r for r in contract.fault_rules if r.id == "zero_power_daylight")
    assert rule["title"] == "Potencia cero en horario solar"
    assert rule["for"] == "10m"
    assert rule["labels"] == {"severity": zero_power.severity}
    assert rule["data"][0]["relativeTimeRange"] == {"from": 900, "to": 0}
    assert rule["data"][0]["datasourceUid"] == DATASOURCE_UID
    assert rule["noDataState"] == "OK"
    assert "{{ $labels.dispositivo }}" in rule["annotations"]["summary"]


def test_silent_device_rule_matches_the_contract(
    silent_rule: dict[str, Any], contract: Contract
) -> None:
    """Same drift test as the zero-power rule, against missing_daytime_reading."""
    missing = next(r for r in contract.fault_rules if r.id == "missing_daytime_reading")
    sql = silent_rule["data"][0]["model"]["rawSql"]
    local_now = "($__timeTo()::timestamptz AT TIME ZONE s.zona_horaria)::time"
    assert f"{local_now} >= TIME '{missing.window.start:%H:%M}'" in sql
    assert f"{local_now} <  TIME '{missing.window.end:%H:%M}'" in sql
    # 15 minutes = the contract's outage: min_consecutive_readings missing 5-minute readings
    window = silent_rule["data"][0]["relativeTimeRange"]
    assert window == {
        "from": missing.min_consecutive_readings * contract.frequency_seconds,
        "to": 0,
    }
    assert silent_rule["labels"] == {"severity": missing.severity}
    assert "$$" not in yaml.safe_dump(silent_rule)


def test_silent_device_rule_starts_from_the_catalog(silent_rule: dict[str, Any]) -> None:
    """A device that sends nothing has no rows in Silver: the rule must list it anyway."""
    sql = silent_rule["data"][0]["model"]["rawSql"]
    assert "FROM dwh.dim_dispositivo AS d" in sql
    assert "LEFT JOIN silver.lectura_5min AS l" in sql and "$__timeFilter(l.ts)" in sql
    assert "count(l.ts) = 0" in sql
    assert silent_rule["title"] == "Inversor sin datos en horario solar"
    assert silent_rule["noDataState"] != "OK"  # one row per device: no rows is a problem
    assert "{{ $labels.dispositivo }}" in silent_rule["annotations"]["summary"]


def test_every_rule_links_to_the_power_panel(rules: dict[str, dict[str, Any]]) -> None:
    assert len(rules) == 2
    for rule in rules.values():
        assert (rule["dashboardUid"], rule["panelId"]) == ("solarbi-operacion", 1)
        assert rule["data"][0]["datasourceUid"] == DATASOURCE_UID


def test_runbook_links_point_to_existing_sections(rules: dict[str, dict[str, Any]]) -> None:
    for rule in rules.values():
        url = rule["annotations"]["runbook_url"]
        doc, _, anchor = url.partition("blob/main/")[2].partition("#")
        headings = [
            line.lstrip("#").strip()
            for line in (PROJECT_ROOT / doc).read_text(encoding="utf-8").splitlines()
            if line.startswith("#")
        ]
        assert anchor in {_anchor(h) for h in headings}, url


def test_contact_points_and_policy() -> None:
    contact = yaml.safe_load((ALERTING / "contact-points.yaml").read_text(encoding="utf-8"))
    receivers = {cp["name"]: cp["receivers"][0] for cp in contact["contactPoints"]}
    assert receivers["webhook-local"]["settings"]["url"].startswith("http://alert-receiver:8080/")
    assert receivers["correo-operador"]["settings"]["addresses"] == "${ALERT_EMAIL_TO}"
    compose = yaml.safe_load((PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    assert compose["services"]["alert-receiver"]["profiles"] == ["alerting"]

    policy = yaml.safe_load((ALERTING / "policies.yaml").read_text(encoding="utf-8"))["policies"][0]
    critical = policy["routes"][0]
    assert critical["receiver"] == "webhook-local"
    assert critical["object_matchers"] == [["severity", "=", "critical"]]
    assert "dispositivo" in critical["group_by"]
    assert critical["repeat_interval"] == "1h"
    # warnings (silent device) match no route: they go to the default receiver, email only
    assert policy["receiver"] == "correo-operador"
    assert not any(["severity", "=", "warning"] in r["object_matchers"] for r in policy["routes"])


def test_alerting_files_hold_no_secrets() -> None:
    for path in ALERTING.glob("*.yaml"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"(password|token|secret)\s*:", text, re.IGNORECASE), path.name

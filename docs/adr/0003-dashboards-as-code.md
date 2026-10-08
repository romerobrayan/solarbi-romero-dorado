# 0003. Version dashboards as code (Grafana provisioning and Power BI .pbip)

- **Status:** Accepted
- **Date:** 2026-10-08
- **Phase:** 0

## Context

Dashboards are a graded deliverable and will evolve across phases 3, 6 and 7. If they live only in
Grafana's internal database or in a binary `.pbix` file, a fresh clone cannot reproduce them, git
diffs are meaningless, and a broken container volume can lose work.

## Decision

**Grafana** (image `grafana/grafana:13.2.3`, the Open Source edition):

- The PostgreSQL datasource is provisioned from
  `grafana/provisioning/datasources/postgres.yaml`, using the read-only role `grafana_reader`.
  Credentials are injected through environment variables; the file holds no secret.
- Dashboards are loaded from `grafana/dashboards/*.json` by the provider in
  `grafana/provisioning/dashboards/dashboards.yaml`. The JSON in the repo is the source of truth
  (`allowUiUpdates: false`): edit in the UI, export the JSON, overwrite the file and commit.

**Power BI**: save the report as a **Power BI Project (`.pbip`)** in `powerbi/`, not as a `.pbix`.
A `.pbip` stores the report definition and the semantic model as text files that git can diff and
merge. The local cache (`.pbi/cache.abf`) and user settings (`.pbi/localSettings.json`) are
ignored; `.pbix` files are ignored too.

## Consequences

- `docker compose up -d` on a fresh clone restores the datasource and every dashboard.
- Changes to dashboards show up in pull requests and in the git history, like code.
- Grafana UI edits are lost on restart unless exported; this is deliberate but must be remembered.
- The `grafana/grafana-oss` repository on Docker Hub stopped receiving releases (its newest tag is
  older than the current `grafana/grafana` releases), so the officially documented OSS image
  `grafana/grafana` is used instead.
- Power BI Desktop must be configured to save as `.pbip`. If the course requires a `.pbix` file for
  submission, it is exported from the project at delivery time rather than versioned.

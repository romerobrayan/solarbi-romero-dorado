# SolarBI Pascual

**Autor:** Brayan Romero Dorado
**Curso:** Inteligencia de Negocios — Grupo 01 — Institución Universitaria Pascual Bravo, semestre 2026-II
**Docente:** Ramiro Grisales Montoya

> Trabajo **individual**: la regla de parejas del curso no aplica a este proyecto.

## Descripción

SolarBI Pascual monitorea una planta solar cuyos inversores envían telemetría cada 5 minutos. El
enfoque del Grupo 01 es el **monitoreo operativo**: potencia en el tiempo y detección de fallas. Los
datos siguen una arquitectura *medallion*: **Bronze** (CSV crudo, nunca se modifica) → **Silver**
(lecturas limpias de 5 minutos, `silver.lectura_5min`) → **Gold** (hecho diario
`dwh.fact_energia_dia`, cargado con un UPSERT idempotente). Dos consumidores leen la misma base de
datos: Grafana (operador, tiempo real) y Power BI (gerencia). Hoy se trabaja con un simulador
pequeño (3 días, 1 inversor, ~870 filas); más adelante llegará un dataset real de más de 4 millones
de filas con un esquema aún desconocido, por eso nada en el repositorio depende de columnas fijas
ni de datos pequeños.

> "One governed dataset, two views: Grafana for real-time operations and Power BI for business decisions."
> — Ramiro Grisales Montoya

El diagrama del flujo está en [docs/architecture.md](docs/architecture.md) y las decisiones de
arquitectura en [docs/adr/](docs/adr/).

## Prerrequisitos

| Herramienta | Versión | Para qué |
|---|---|---|
| Windows + PowerShell | 5.1 o superior | Todos los comandos de esta guía |
| Docker Desktop | Con Docker Compose v2 | PostgreSQL + TimescaleDB y Grafana |
| Python | 3.12 o superior | ETL y verificaciones |
| Git | 2.x | Control de versiones |
| Power BI Desktop | Reciente, con guardado como proyecto (`.pbip`) | Fases 3 y 7 |

## Cómo reproducir el entorno (PowerShell)

Todos los comandos se ejecutan desde la raíz del repositorio.

1. **Crear el archivo de entorno** y cambiar cada valor `change-me` por una contraseña propia
   (sin el carácter `$`):

   ```powershell
   Copy-Item .env.example .env
   notepad .env
   ```

2. **Levantar la infraestructura** (la primera vez descarga las imágenes):

   ```powershell
   docker compose up -d
   docker compose ps
   ```

   Ambos servicios deben aparecer como `healthy`.

3. **Crear el entorno virtual e instalar dependencias:**

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install --upgrade pip
   pip install -e ".[dev]"
   ```

   Si PowerShell bloquea `Activate.ps1`, ejecutar una vez
   `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` o usar `.\.venv\Scripts\python.exe`
   directamente.

4. **Verificar el entorno:**

   ```powershell
   python scripts/check_env.py
   pytest
   ruff check .
   ```

   `check_env.py` imprime la versión de PostgreSQL y de TimescaleDB y la lista de esquemas
   (`bronze`, `dq`, `dwh`, `public`, `silver`).

5. **Abrir Grafana** en <http://localhost:3000> (o el `GRAFANA_PORT` elegido) con el usuario y la
   contraseña de administrador definidos en `.env`. El datasource *SolarBI PostgreSQL* ya viene
   configurado.

Para detener todo: `docker compose down`. Para borrar también los datos y empezar desde cero:
`docker compose down -v` (los scripts de `sql/init/` se vuelven a ejecutar en el siguiente arranque).

## Credenciales y roles de base de datos

| Rol | Uso | Permisos |
|---|---|---|
| `POSTGRES_USER` (por defecto `solarbi_owner`) | ETL y administración | Dueño de la base de datos |
| `grafana_reader` | Datasource de Grafana | Solo lectura sobre `silver`, `dwh` y `dq` |
| `powerbi_reader` | Power BI Desktop | Solo lectura sobre `silver`, `dwh` y `dq` |

Las contraseñas viven únicamente en `.env` (ignorado por git). Los roles de solo lectura se crean
en el **primer arranque** con un volumen vacío (`sql/init/01_roles.sh`). Si después se cambia una
contraseña en `.env`, hay que aplicarla también en la base de datos:

```powershell
docker compose exec db psql -U solarbi_owner -d solarbi -c "ALTER ROLE grafana_reader PASSWORD 'nueva-contraseña';"
docker compose restart grafana
```

La base de datos se publica en `127.0.0.1:5433` (no en 5432) para no chocar con un PostgreSQL
instalado en el equipo. Si el puerto 5433 o 3000 ya está ocupado, basta con cambiar
`POSTGRES_PORT` o `GRAFANA_PORT` en `.env`. Para conectar Power BI Desktop: servidor
`127.0.0.1:5433`, base de datos `solarbi`, usuario `powerbi_reader`.

## Estructura del repositorio

| Ruta | Contenido |
|---|---|
| `README.md` | Este documento |
| `CLAUDE.md` | Contexto y reglas para sesiones de trabajo con asistente de IA |
| `.env.example` | Plantilla de variables de entorno (sin secretos reales) |
| `docker-compose.yml` | PostgreSQL 16 + TimescaleDB y Grafana OSS |
| `pyproject.toml` | Proyecto Python, dependencias fijadas, configuración de pytest y ruff |
| `contracts/` | Contratos de datos en YAML (fase 1) |
| `data/bronze/` | CSV crudo e inmutable (solo se versiona la salida pequeña del simulador) |
| `data/silver/` | Exportaciones limpias (solo archivos pequeños) |
| `data/samples/` | Muestras pequeñas versionadas |
| `docs/` | Enunciado (PDF), arquitectura y ADRs |
| `etl/` | Código del ETL: configuración, `simulador.py` y `run_etl.py` (fases siguientes) |
| `scripts/` | Utilidades; `check_env.py` verifica el entorno |
| `sql/init/` | Scripts que se ejecutan en el primer arranque de la base de datos |
| `grafana/provisioning/` | Datasource y proveedor de dashboards (configuración como código) |
| `grafana/dashboards/` | JSON exportado de los dashboards (fuente de verdad) |
| `powerbi/` | Proyecto de Power BI en formato `.pbip` |
| `tests/` | Pruebas automáticas (pytest) |

## Hoja de ruta

| Fase | Nombre | Estado |
|---|---|---|
| 0 | Fundamentos: repositorio, infraestructura local y convenciones | ✅ Completada |
| 1 | Arquitectura y contrato de datos | ⏳ Siguiente |
| 2 | ETL (Bronze → Silver → Gold) con reglas de calidad | Pendiente |
| 3 | Dashboards (Grafana y Power BI) | Pendiente |
| 4 | Investigación | Pendiente |
| 5 | Entrega | Pendiente |
| 6 | Escalamiento al dataset real (4M+ filas) | Pendiente |
| 7 | Dashboard final y modelo estrella | Pendiente |

## Quién hizo qué

| Integrante | Responsabilidades |
|---|---|
| Brayan Romero Dorado | Todas las fases: arquitectura, infraestructura, ETL, calidad de datos, dashboards, investigación y documentación |

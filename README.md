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

4. **Crear o actualizar las tablas** con las migraciones (se puede repetir: si no hay nada
   pendiente, no cambia nada):

   ```powershell
   python scripts/migrate.py
   ```

   La primera vez crea el rol `etl_writer`, las tablas de las capas Bronze, Silver, Gold y de
   calidad, y activa el inicio de sesión de `etl_writer` con `ETL_WRITER_PASSWORD`. Con
   `python scripts/migrate.py --status` se ven las migraciones aplicadas y pendientes.

5. **Verificar el entorno:**

   ```powershell
   python scripts/check_env.py
   pytest
   ruff check .
   ```

   `check_env.py` imprime la versión de PostgreSQL y de TimescaleDB, los esquemas
   (`bronze`, `dq`, `dwh`, `meta`, `public`, `silver`), los roles y el estado de las migraciones.

6. **Abrir Grafana** en <http://localhost:3000> (o el `GRAFANA_PORT` elegido) con el usuario y la
   contraseña de administrador definidos en `.env`. El datasource *SolarBI PostgreSQL* ya viene
   configurado.

Para detener todo: `docker compose down`. Para borrar también los datos y empezar desde cero:
`docker compose down -v` (en el siguiente arranque se vuelven a ejecutar `sql/init/` y, con
`python scripts/migrate.py`, todas las migraciones). Nunca hace falta borrar el volumen para
agregar tablas: los cambios de esquema llegan como migraciones nuevas.

## Contrato de datos

El archivo [`contracts/telemetria.yaml`](contracts/telemetria.yaml) es la única fuente de verdad
sobre los datos de telemetría. Define:

- **Responsables** (*owner*, *steward*, *custodian*) y la **política de cambios**: un cambio que
  rompe la compatibilidad exige una versión mayor nueva, y el ETL se niega a procesar archivos que
  no coinciden con el contrato.
- **La fuente**: formato, separador, zona horaria (`America/Bogota`) y frecuencia (300 s, es
  decir 288 lecturas por día).
- **Las columnas**: nombre canónico, nombre en el archivo (`source_name`), tipo, unidad, si admite
  vacíos y rango físico válido.
- **Las reglas de calidad** con su acción (`reject`, `flag` o `dedupe`), las **reglas de falla**
  (potencia nula entre 09:00 y 15:00 hora local), el catálogo de sitios y dispositivos y el SLA
  (95 % de datos válidos por día).

`etl/contract.py` valida el archivo con el esquema [`contracts/telemetria.schema.json`](contracts/telemetria.schema.json)
y con reglas adicionales (referencias entre secciones, rangos, zonas horarias), y reporta todos los
errores juntos.

**Para conectar un dataset nuevo** (por ejemplo, el dataset real de la fase 6) no se cambia código
Python: se escribe su propio contrato, se pone en `source_name` el nombre de cada columna en el
archivo, se ajustan unidades, rangos, zona horaria y frecuencia, y se publica con una versión nueva.
Si sus columnas son distintas, su tabla de aterrizaje en Bronze llega como una migración SQL.

## Credenciales y roles de base de datos

| Rol | Uso | Permisos |
|---|---|---|
| `POSTGRES_USER` (por defecto `solarbi_owner`) | Migraciones y administración | Dueño de la base de datos |
| `etl_writer` | ETL | Sin superusuario; dueño de las tablas; escribe en `bronze`, `silver`, `dwh`, `dq` y `meta` |
| `grafana_reader` | Datasource de Grafana | Solo lectura sobre `silver`, `dwh` y `dq` |
| `powerbi_reader` | Power BI Desktop | Solo lectura sobre `silver`, `dwh` y `dq` |

Las contraseñas viven únicamente en `.env` (ignorado por git). Los roles de solo lectura se crean
en el **primer arranque** con un volumen vacío (`sql/init/01_roles.sh`). El rol `etl_writer` lo
crea la migración 0001, y `scripts/migrate.py` le aplica `ETL_WRITER_PASSWORD`; si esa contraseña
cambia en `.env`, basta con volver a ejecutar `python scripts/migrate.py`. Para los roles de
lectura, un cambio de contraseña en `.env` hay que aplicarlo también en la base de datos:

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
| `contracts/` | Contrato de datos (`telemetria.yaml`) y su esquema JSON |
| `data/bronze/` | CSV crudo e inmutable (solo se versiona la salida pequeña del simulador) |
| `data/silver/` | Exportaciones limpias (solo archivos pequeños) |
| `data/samples/` | Muestras pequeñas versionadas |
| `docs/` | Enunciado (PDF), arquitectura y ADRs |
| `etl/` | Código del ETL: configuración, contrato, migraciones; `simulador.py` y `run_etl.py` en la fase 2 |
| `scripts/` | `migrate.py` aplica las migraciones; `check_env.py` verifica el entorno |
| `sql/init/` | Arranque de la base de datos: extensión, esquemas y roles de lectura |
| `sql/migrations/` | Migraciones numeradas que crean las tablas (solo hacia adelante) |
| `grafana/provisioning/` | Datasource y proveedor de dashboards (configuración como código) |
| `grafana/dashboards/` | JSON exportado de los dashboards (fuente de verdad) |
| `powerbi/` | Proyecto de Power BI en formato `.pbip` |
| `tests/` | Pruebas automáticas (pytest) |

## Hoja de ruta

| Fase | Nombre | Estado |
|---|---|---|
| 0 | Fundamentos: repositorio, infraestructura local y convenciones | ✅ Completada |
| 1 | Arquitectura y contrato de datos | ✅ Completada |
| 2 | ETL (Bronze → Silver → Gold) con reglas de calidad | ⏳ Siguiente |
| 3 | Dashboards (Grafana y Power BI) | Pendiente |
| 4 | Investigación | Pendiente |
| 5 | Entrega | Pendiente |
| 6 | Escalamiento al dataset real (4M+ filas) | Pendiente |
| 7 | Dashboard final y modelo estrella | Pendiente |

## Quién hizo qué

| Integrante | Responsabilidades |
|---|---|
| Brayan Romero Dorado | Todas las fases: arquitectura, infraestructura, ETL, calidad de datos, dashboards, investigación y documentación |

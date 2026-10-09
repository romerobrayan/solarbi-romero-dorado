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

## Cómo reproducir la práctica (PowerShell)

Todos los comandos se ejecutan desde la raíz del repositorio. El flujo completo es: levantar la
infraestructura → migrar → simular → ejecutar el ETL → ejecutarlo otra vez → comprobar los conteos.

1. **Crear el archivo de entorno** y cambiar cada valor `change-me` por una contraseña propia
   (sin el carácter `$`):

   ```powershell
   Copy-Item .env.example .env
   notepad .env
   ```

2. **Levantar la infraestructura** (la primera vez descarga las imágenes) y comprobar que ambos
   servicios aparecen como `healthy`:

   ```powershell
   docker compose up -d
   docker compose ps
   ```

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

4. **Crear o actualizar las tablas** con las migraciones (repetirlo no cambia nada si no hay
   pendientes) y verificar el entorno:

   ```powershell
   python scripts/migrate.py
   python scripts/check_env.py
   ```

   La primera vez crea el rol `etl_writer` y las tablas de Bronze, Silver, Gold y calidad.
   `python scripts/migrate.py --status` lista las migraciones aplicadas y pendientes.

5. **Generar los datos crudos (Paso 1, Bronze).** El simulador del docente escribe
   `data/bronze/telemetria.csv` (3 días, un inversor de 5 kWp, con anomalías):

   ```powershell
   python etl/simulador.py
   ```

   El repositorio ya trae el archivo con el que se tomaron las evidencias; volver a generarlo
   produce valores distintos, porque el simulador del docente no fija semilla. Para datos
   reproducibles con fallas de planta: `python etl/simulador_fallas.py --seed 42 --inject-faults`
   (escribe `data/samples/telemetria_fallas_seed42.csv`).

6. **Ejecutar el ETL (Pasos 2 y 3)** con un solo comando:

   ```powershell
   python -m etl.run_etl --file data/bronze/telemetria.csv
   ```

   Imprime los 8 pasos con su duración y el reporte de calidad: filas leídas, rechazadas por cada
   regla, deduplicadas, marcadas, válidas, porcentaje de datos válidos, días cargados en
   `dwh.fact_energia_dia` y eventos de falla.

7. **Ejecutarlo otra vez y comprobar que nada cambia (carga idempotente):**

   ```powershell
   python scripts/conteos.py
   python -m etl.run_etl --file data/bronze/telemetria.csv
   python scripts/conteos.py
   ```

   La segunda corrida reconoce el archivo por su SHA-256, no lo vuelve a copiar a Bronze, pero
   recalcula Silver y Gold con UPSERT: los conteos y la energía por día quedan idénticos. Las
   salidas reales están en [docs/evidencias/fase-2-ejecucion.md](docs/evidencias/fase-2-ejecucion.md).

8. **Pruebas y estilo:**

   ```powershell
   pytest
   ruff check .
   ```

   Las pruebas de integración crean una base temporal `solarbi_test` en el mismo servidor y la
   borran al terminar, así que nunca tocan los datos de desarrollo.

9. **Ver los tableros (Pasos 4 y 5):** Grafana y Power BI, en la sección siguiente.

Para detener todo: `docker compose down`. Para borrar también los datos y empezar desde cero:
`docker compose down -v` (en el siguiente arranque se vuelven a ejecutar `sql/init/` y, con
`python scripts/migrate.py`, todas las migraciones). Nunca hace falta borrar el volumen para
agregar tablas: los cambios de esquema llegan como migraciones nuevas.

## Tableros: Grafana y Power BI

Los mismos datos, dos vistas ([ADR 0008](docs/adr/0008-dashboards-grafana-import-powerbi.md)):
Grafana para el operador (lecturas de 5 minutos, alertas, calidad) y Power BI para la gerencia
(modelo estrella de Gold). Ambos se conectan con usuarios de solo lectura.

### Grafana

1. Levantar la pila con el receptor de notificaciones:

   ```powershell
   docker compose --profile alerting up -d
   ```

2. Abrir <http://localhost:3000> (o el `GRAFANA_PORT` elegido) con el usuario y la contraseña de
   administrador de `.env`. En **Dashboards → SolarBI** está **SolarBI · Operación de la planta**:
   potencia cada 5 minutos con irradiancia, umbral de 0,01 kW, franja 09:00–15:00 y eventos de falla
   como anotaciones; potencia promedio por hora; energía del día elegido en la variable `Día (Gold)`;
   eventos de falla; últimas corridas del ETL y filas afectadas por regla.
3. La regla **Potencia cero en horario solar** está en **Alerting → Alert rules**. Qué hacer cuando
   se dispara: [docs/operacion.md](docs/operacion.md#alerta-potencia-cero).

**Ver la alerta en vivo.** Los datos simulados son de días pasados, y una regla de alerta mira los
últimos minutos. Este script simula el envío IoT (no hay un inversor real): carga las lecturas de
hoy hasta ahora y luego una lectura cada 5 minutos por el ETL normal, con un disparo del inversor
unos minutos después. **Ejecútelo entre las 09:00 y las 15:00 hora de Colombia**; fuera de ese
horario la alerta sigue en Normal, como debe ser.

```powershell
python scripts/replay_live.py --trip-in 5m --trip-minutes 20
```

Cada línea muestra la lectura, el resultado del ETL y el estado de la alerta: Normal → Pending →
Firing (unos 10 minutos después del disparo) → Normal (cuando vuelve la potencia). La notificación
entregada se ve con:

```powershell
docker compose --profile alerting logs alert-receiver
```

**Exportar el tablero.** Se puede editar en la interfaz de Grafana; después, este script escribe el
JSON normalizado en `grafana/dashboards/` para versionarlo (con `--check` solo compara):

```powershell
python scripts/export_grafana.py
```

### Power BI

El proyecto `powerbi/SolarBI.pbip` se crea en Power BI Desktop siguiendo
[docs/powerbi.md](docs/powerbi.md): conexión en modo Importar a `127.0.0.1:5433` con
`powerbi_reader`, las cuatro tablas de `dwh`, y luego `python scripts/powerbi_model.py --apply`
agrega relaciones, tabla de fechas y medidas DAX al modelo (TMDL).

## Programación diaria

El ETL se programaría todos los días a la medianoche, hora de Colombia. Las instrucciones están
escritas, no instaladas:

```text
# cron (la zona horaria del servidor debe ser America/Bogota, o CRON_TZ=America/Bogota donde se soporte)
0 0 * * * cd /ruta/solarbi-romero-dorado && .venv/bin/python -m etl.run_etl --file data/bronze/telemetria.csv
```

```powershell
# Programador de tareas de Windows
schtasks /Create /SC DAILY /ST 00:00 /TN SolarBI_ETL /TR "\"D:\ruta\solarbi-romero-dorado\.venv\Scripts\python.exe\" -m etl.run_etl --file \"D:\ruta\solarbi-romero-dorado\data\bronze\telemetria.csv\""
```

Detalles, códigos de salida y diagnóstico en [docs/operacion.md](docs/operacion.md).

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
| `data/samples/` | Muestras pequeñas versionadas (`telemetria_fallas_seed42.csv`) |
| `docs/` | Enunciado (PDF), arquitectura, ADRs, operación (`operacion.md`) y evidencias (`evidencias/`) |
| `etl/` | `run_etl.py` (punto de entrada), `pipeline.py`, `bronze.py`, `sqlgen.py` (SQL generado desde el contrato), `contract.py`, `migrations.py`, `config.py`, `simulador.py` (del docente) y `simulador_fallas.py` |
| `scripts/` | `migrate.py`, `check_env.py`, `conteos.py`; `replay_live.py` (alerta en vivo), `export_grafana.py` (tablero a JSON), `powerbi_model.py` (medidas DAX en TMDL) |
| `sql/init/` | Arranque de la base de datos: extensión, esquemas y roles de lectura |
| `sql/migrations/` | Migraciones numeradas que crean las tablas (solo hacia adelante) |
| `grafana/provisioning/` | Datasource, proveedor de dashboards y alertas (regla, contact points, política), como código |
| `grafana/dashboards/` | JSON exportado de los dashboards (fuente de verdad) |
| `powerbi/` | Proyecto de Power BI en formato `.pbip` (guía en `docs/powerbi.md`) |
| `tests/` | Pruebas automáticas (pytest) y archivos de prueba (`tests/fixtures/`) |
| `.github/workflows/ci.yml` | Integración continua: ruff y pytest con TimescaleDB, en Python 3.12 y 3.14 |

## Hoja de ruta

| Fase | Nombre | Estado |
|---|---|---|
| 0 | Fundamentos: repositorio, infraestructura local y convenciones | ✅ Completada |
| 1 | Arquitectura y contrato de datos | ✅ Completada |
| 2 | ETL (Bronze → Silver → Gold) con reglas de calidad | ✅ Completada |
| 3 | Dashboards (Grafana y Power BI) | ✅ Grafana completo; Power BI pendiente de los pasos en Desktop |
| 4 | Investigación (Parte A, fuera del repositorio) | ⏳ En curso |
| 5 | Entrega | Pendiente |
| 6 | Escalamiento al dataset real (4M+ filas) | Pendiente |
| 7 | Dashboard final y modelo estrella | Pendiente |

## Quién hizo qué

| Integrante | Responsabilidades |
|---|---|
| Brayan Romero Dorado | Todas las fases: arquitectura, infraestructura, ETL, calidad de datos, dashboards, investigación y documentación |

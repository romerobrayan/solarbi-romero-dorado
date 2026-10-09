# Arquitectura de SolarBI Pascual

Arquitectura *medallion* sobre una sola base de datos PostgreSQL 16 + TimescaleDB. Los datos fluyen
en una sola dirección, cada capa tiene un esquema propio y los consumidores solo leen. El contrato
de datos (`contracts/telemetria.yaml`) gobierna todo el flujo: columnas, unidades, rangos, reglas
de calidad, reglas de falla y niveles de servicio.

## 1. Flujo general

```mermaid
flowchart LR
    SRC["Simulador / inversores IoT<br/>telemetría cada 5 min"]
    CSV[/"data/bronze/telemetria.csv<br/>archivo inmutable"/]
    CON[/"contracts/telemetria.yaml<br/>contrato de datos v1.0.0"/]
    ETL["ETL en Python<br/>etl/run_etl.py"]
    RAW[("bronze.telemetria_raw<br/>todo como texto, solo inserción")]
    SLV[("silver.lectura_5min<br/>hypertable, UTC<br/>reglas reject / flag / dedupe")]
    GLD[("dwh.fact_energia_dia<br/>día local, UPSERT idempotente")]
    DIM[("dwh.dim_fecha · dim_sitio<br/>dim_dispositivo")]
    DQ[("dq.etl_run_log · dq.rule_result<br/>dq.fault_event")]
    GRA["Grafana<br/>operación en tiempo real"]
    PBI["Power BI<br/>decisiones de negocio"]

    SRC --> CSV
    CSV -->|COPY| RAW
    CON -. esquema, reglas y umbrales .-> ETL
    RAW --> ETL
    ETL --> SLV
    ETL -. auditoría de cada corrida .-> DQ
    SLV -->|agregación por día local| GLD
    DIM --- GLD
    SLV -->|reglas de falla| DQ
    SLV -->|potencia cada 5 min| GRA
    DQ -->|alarmas y calidad| GRA
    GLD --> GRA
    GLD --> PBI
    DIM --> PBI
```

## 2. El viaje de una lectura

Una sola lectura (inversor 1, 5 de octubre de 2026, 12:00 hora local, 4,2 kW) desde el dispositivo
hasta un visual, capa por capa. Las capas IoT de borde y red son la arquitectura de referencia; hoy
el simulador las reemplaza escribiendo directamente el CSV.

```mermaid
flowchart TB
    subgraph IOT["Capas IoT"]
        D["1 · Dispositivo<br/>inversor 5 kWp mide p_ac_kw = 4,2 kW a las 12:00 locales"]
        G["2 · Gateway / edge<br/>agrega cada 5 min y agrega la marca de tiempo"]
        B["3 · Red / broker (futuro MQTT)<br/>topic pascualbravo/solar/PB-01/1/p_ac_kw"]
    end
    subgraph BR["BRONZE · crudo, inmutable"]
        F["4 · Archivo<br/>data/bronze/telemetria.csv<br/>'2026-10-05 12:00:00,1,4.2,...'"]
        R["5 · bronze.telemetria_raw<br/>misma fila, todo texto,<br/>con run_id, source_file y source_row"]
    end
    subgraph SV["SILVER · limpio, validado"]
        S["6 · silver.lectura_5min<br/>ts = 2026-10-05 17:00 UTC, p_ac_kw = 4.2<br/>pasó rango y duplicados; dq_flags = {}"]
    end
    subgraph GD["GOLD · modelo para el negocio"]
        FA["7 · dwh.fact_energia_dia<br/>fecha_key 20261005 (día local), dispositivo_key 1<br/>aporta 4,2 × 5/60 = 0,35 kWh"]
    end
    subgraph VIS["Visualización"]
        GR["8a · Grafana<br/>punto de la serie de potencia (lee Silver)<br/>y energía del día (lee Gold)"]
        PB["8b · Power BI<br/>medida DAX de energía total y diaria (lee Gold)"]
    end
    D --> G --> B --> F --> R --> S --> FA
    S --> GR
    FA --> GR
    FA --> PB
```

Si la misma lectura hubiera llegado con `p_ac_kw = -1`, se habría quedado en Bronze (intacta) y en
`dq.rule_result` como rechazo de `range_p_ac_kw`; nunca llegaría a Silver ni a Gold. Si hubiera
llegado sin irradiancia, entraría a Silver con `dq_flags = {missing_irradiancia}` y seguiría
sumando energía (ver [ADR 0005](adr/0005-quality-rule-actions-reject-flag-dedupe.md)).

## 3. Capas y tablas

| Capa | Tabla | Grano y contenido | Regla principal |
|---|---|---|---|
| Bronze | `data/bronze/*.csv` | Archivo tal como llega | Nunca se modifica |
| Bronze | `bronze.telemetria_raw` | Una fila por fila del archivo, todo como texto | Solo inserción (un *trigger* rechaza UPDATE, DELETE y TRUNCATE) |
| Silver | `silver.lectura_5min` | Una lectura por `(dispositivo_id, ts)`; `ts` en UTC, ajustado al intervalo de 5 minutos (la hora original queda en `ts_origen`) | Solo entran filas que pasan las reglas `reject` y `dedupe` |
| Gold | `dwh.fact_energia_dia` | Un día **local** por dispositivo | UPSERT idempotente sobre `(fecha_key, dispositivo_key)` |
| Gold | `dwh.dim_fecha`, `dwh.dim_sitio`, `dwh.dim_dispositivo` | Dimensiones del modelo estrella | Sitios y dispositivos salen del contrato |
| Calidad | `dq.etl_run_log`, `dq.rule_result` | Una fila por corrida y una por regla y corrida | Toda corrida queda registrada |
| Calidad | `dq.fault_event` | Un evento por falla detectada | Reglas de falla del contrato, evaluadas sobre Silver |
| Control | `meta.schema_migrations` | Una fila por migración aplicada | Migraciones solo hacia adelante |

## 4. Modelo de datos de `dwh`

```mermaid
erDiagram
    dim_fecha ||--o{ fact_energia_dia : "fecha_key"
    dim_dispositivo ||--o{ fact_energia_dia : "dispositivo_key"
    dim_sitio ||--o{ dim_dispositivo : "sitio_key"

    dim_fecha {
        int fecha_key PK "YYYYMMDD del día local"
        date fecha
        smallint anio
        smallint trimestre
        smallint mes
        text nombre_mes
        smallint dia
        smallint dia_semana "ISO, 1 = lunes"
        text nombre_dia
        smallint semana_iso
        boolean es_fin_de_semana
    }
    dim_sitio {
        int sitio_key PK
        text sitio_id UK
        text nombre
        text ciudad
        text zona_horaria "IANA, define el día local"
    }
    dim_dispositivo {
        int dispositivo_key PK
        text dispositivo_id UK
        text nombre
        text tipo
        numeric nominal_kwp
        int sitio_key FK
    }
    fact_energia_dia {
        int fecha_key PK, FK
        int dispositivo_key PK, FK
        numeric energia_kwh "suma de p_ac_kw x 5/60"
        int lecturas_validas
        int lecturas_esperadas "288"
        numeric pct_datos_validos "calculada"
        boolean cumple_sla "pct >= 95"
        numeric p_max_kw
        numeric irradiacion_kwh_m2
        uuid run_id FK "dq.etl_run_log"
        timestamptz updated_at
    }
```

## 5. Seguridad y roles

| Rol | Uso | Permisos |
|---|---|---|
| `solarbi_owner` (`POSTGRES_USER`) | Migraciones y administración | Dueño de la base de datos (superusuario de la imagen) |
| `etl_writer` | ETL | No es superusuario; dueño de todas las tablas; `USAGE, CREATE` en `bronze, silver, dwh, dq, meta` |
| `grafana_reader`, `powerbi_reader` | Consumidores | Solo lectura en `silver, dwh, dq` (rol `bi_readonly`); sin acceso a `bronze` |

Las tablas que crea `etl_writer` quedan legibles para los consumidores automáticamente
(`ALTER DEFAULT PRIVILEGES FOR ROLE etl_writer`, migración 0001). Ver
[ADR 0006](adr/0006-forward-only-sql-migrations-and-etl-role.md).

## 6. Infraestructura local

`docker-compose.yml` levanta dos servicios en la red `solarbi`:

| Servicio | Imagen | Puerto en el equipo | Volumen |
|---|---|---|---|
| `db` | `timescale/timescaledb:2.30.2-pg16` | `127.0.0.1:${POSTGRES_PORT}` (5433) | `pgdata` |
| `grafana` | `grafana/grafana:13.2.3` (edición OSS) | `127.0.0.1:${GRAFANA_PORT}` (3000) | `grafana-data` |

`sql/init/` crea la extensión, los esquemas y los roles de lectura en el primer arranque; todo lo
demás llega por migraciones (`python scripts/migrate.py`), de modo que la base evoluciona sin
borrar datos.

## 7. Preparado para el dataset real (fase 6)

- El esquema del dataset vive en `contracts/` y no en el código
  ([ADR 0002](adr/0002-config-driven-etl-with-data-contracts.md)): para una fuente nueva se mapean
  sus columnas en `source_name` y se ajustan unidades, rangos y zona horaria.
- `silver.lectura_5min` ya es *hypertable* con particiones de 7 días; el primer agregado continuo
  será la potencia horaria por dispositivo ([ADR 0001](adr/0001-postgres-with-timescaledb.md)).
- Bronze se carga con `COPY`, la vía que escala a millones de filas.
- El día se calcula en hora local del sitio, aunque se almacene en UTC
  ([ADR 0004](adr/0004-local-day-grain-and-utc-storage.md)).
- El dataset grande nunca entra al repositorio (`.gitignore` y `tests/test_repo_hygiene.py`).

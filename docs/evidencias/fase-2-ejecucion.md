# Evidencias de la fase 2: ejecución del ETL

Salidas **reales** de la terminal, copiadas sin editar, de la ejecución del 8 de octubre de 2026
sobre la base local (PostgreSQL 16.15 + TimescaleDB 2.30.2 en Docker), con el contrato
`contracts/telemetria.yaml` v1.1.0. Los comandos se ejecutaron desde la raíz del repositorio con el
entorno virtual activo. El archivo `data/bronze/telemetria.csv` es el que generó el simulador del
docente (`python etl/simulador.py`) y está versionado en el repositorio.

## 1. Migraciones y estado inicial

La migración 0007 agrega lo que necesita la fase 2 (`ts_origen` en Silver y las columnas de cuadre
en `dq.etl_run_log`). Antes de la primera carga todas las tablas están vacías.

```
Database: postgresql://solarbi_owner:***@127.0.0.1:5433/solarbi
  applied 0007_etl_fase2 (55 ms)
Applied 1 migration(s); database is at version 0007.
etl_writer login OK
```

```
tabla                       filas
bronze.telemetria_raw           0
silver.lectura_5min             0
dwh.fact_energia_dia            0
dq.fault_event                  0
dq.etl_run_log                  0

fecha_key  disp.   energia_kwh  lecturas  % válidos
```

## 2. Primera ejecución (Paso 2 y Paso 3)

```
$ python -m etl.run_etl --file data/bronze/telemetria.csv
[1/8] Contrato y archivo                              ok    0.02 s
      contrato telemetria v1.1.0; archivo data/bronze/telemetria.csv
      sha256 bca6d50ed9a61c9afae7cd697c453c0faa0d70024eb25dd5d70a4a258b6f4a69
[2/8] Abrir corrida en dq.etl_run_log                 ok    0.01 s
      run_id 3ad80b40-5448-4028-a32d-69f9f37ff999
[3/8] Bronze                                          ok    0.03 s
      879 filas copiadas a bronze.telemetria_raw
[4/8] Silver (reglas de calidad + UPSERT)             ok    0.29 s
[5/8] Dimensiones                                     ok    0.02 s
[6/8] Gold (dwh.fact_energia_dia por día local)       ok    0.01 s
[7/8] Fallas (dq.fault_event)                         ok    0.10 s
[8/8] Cerrar corrida (succeeded)                      ok    0.00 s

Reporte de calidad (Paso 2)
Corrida                 : 3ad80b40-5448-4028-a32d-69f9f37ff999
Archivo                 : data/bronze/telemetria.csv (contrato v1.1.0)
Bronze                  : cargado (879 filas nuevas)
Filas leídas            : 879
Rechazadas por regla    : missing_key=0, invalid_format=0, unknown_device=0, off_grid=0, range_p_ac_kw=22, missing_p_ac_kw=0
Rechazadas (distintas)  : 22
Deduplicadas            : 15
Marcadas (flag)         : snapped_to_grid=0, missing_irradiancia=23, range_irradiancia=0, range_temp_modulo=0  (23 filas)
Filas válidas           : 842
% datos válidos         : 95.79 %
Días cargados en Gold   : 3   (energía total = 80.956 kWh)
Eventos de falla        : 0

Nota: los conteos por regla pueden solaparse (una fila puede fallar varias reglas).
Cuadre: leídas = válidas + rechazadas distintas + deduplicadas -> 879 = 842 + 22 + 15 (OK)
exit=0
```

Conteos después de la primera ejecución:

```
$ python scripts/conteos.py
tabla                       filas
bronze.telemetria_raw         879
silver.lectura_5min           842
dwh.fact_energia_dia            3
dq.fault_event                  0
dq.etl_run_log                  1

fecha_key  disp.   energia_kwh  lecturas  % válidos
20261005   1           27.4233       282      97.92
20261006   1           26.3838       279      96.88
20261007   1           27.1486       281      97.57
```

## 3. Segunda ejecución con el mismo archivo (idempotencia)

El archivo tiene el mismo SHA-256, así que **no se vuelve a copiar a Bronze**; aun así la corrida
recalcula Silver, Gold y las fallas a partir de las filas de Bronze de la primera corrida y las
escribe con UPSERT.

```
$ python -m etl.run_etl --file data/bronze/telemetria.csv
[1/8] Contrato y archivo                              ok    0.04 s
      contrato telemetria v1.1.0; archivo data/bronze/telemetria.csv
      sha256 bca6d50ed9a61c9afae7cd697c453c0faa0d70024eb25dd5d70a4a258b6f4a69
[2/8] Abrir corrida en dq.etl_run_log                 ok    0.01 s
      run_id d5f8cef3-6572-4355-b239-d155d4be9048
[3/8] Bronze                                          ok    0.01 s
      archivo ya cargado (mismo sha256): se reutilizan sus filas de Bronze de la corrida 3ad80b40-5448-4028-a32d-69f9f37ff999
[4/8] Silver (reglas de calidad + UPSERT)             ok    0.07 s
[5/8] Dimensiones                                     ok    0.02 s
[6/8] Gold (dwh.fact_energia_dia por día local)       ok    0.01 s
[7/8] Fallas (dq.fault_event)                         ok    0.08 s
[8/8] Cerrar corrida (succeeded)                      ok    0.00 s

Reporte de calidad (Paso 2)
Corrida                 : d5f8cef3-6572-4355-b239-d155d4be9048
Archivo                 : data/bronze/telemetria.csv (contrato v1.1.0)
Bronze                  : omitido: archivo ya cargado (filas de la corrida 3ad80b40-5448-4028-a32d-69f9f37ff999)
Filas leídas            : 879
Rechazadas por regla    : missing_key=0, invalid_format=0, unknown_device=0, off_grid=0, range_p_ac_kw=22, missing_p_ac_kw=0
Rechazadas (distintas)  : 22
Deduplicadas            : 15
Marcadas (flag)         : snapped_to_grid=0, missing_irradiancia=23, range_irradiancia=0, range_temp_modulo=0  (23 filas)
Filas válidas           : 842
% datos válidos         : 95.79 %
Días cargados en Gold   : 3   (energía total = 80.956 kWh)
Eventos de falla        : 0

Nota: los conteos por regla pueden solaparse (una fila puede fallar varias reglas).
Cuadre: leídas = válidas + rechazadas distintas + deduplicadas -> 879 = 842 + 22 + 15 (OK)
exit=0
```

Conteos después de la segunda ejecución: **idénticos** a los de la primera en Bronze, Silver, Gold y
eventos de falla, y con la misma energía por día. Solo `dq.etl_run_log` suma una fila (la corrida
nueva queda registrada).

```
$ python scripts/conteos.py
tabla                       filas
bronze.telemetria_raw         879
silver.lectura_5min           842
dwh.fact_energia_dia            3
dq.fault_event                  0
dq.etl_run_log                  2

fecha_key  disp.   energia_kwh  lecturas  % válidos
20261005   1           27.4233       282      97.92
20261006   1           26.3838       279      96.88
20261007   1           27.1486       281      97.57
```

La segunda corrida sí procesó: queda registrada como `skipped_duplicate_file` apuntando a las filas de
Bronze de la primera, y las 842 filas de Silver (y las 3 de Gold) llevan ahora su `run_id`.

```
solarbi=# SELECT run_id, status, bronze_status, bronze_run_id, filas_leidas, filas_validas, filas_rechazadas, filas_deduplicadas, pct_validas, duracion_s FROM dq.etl_run_log ORDER BY started_at;
                run_id                |  status   |     bronze_status      |            bronze_run_id             | filas_leidas | filas_validas | filas_rechazadas | filas_deduplicadas | pct_validas | duracion_s 
--------------------------------------+-----------+------------------------+--------------------------------------+--------------+---------------+------------------+--------------------+-------------+------------
 3ad80b40-5448-4028-a32d-69f9f37ff999 | succeeded | loaded                 | 3ad80b40-5448-4028-a32d-69f9f37ff999 |          879 |           842 |               22 |                 15 |       95.79 |      0.461
 d5f8cef3-6572-4355-b239-d155d4be9048 | succeeded | skipped_duplicate_file | 3ad80b40-5448-4028-a32d-69f9f37ff999 |          879 |           842 |               22 |                 15 |       95.79 |      0.196
(2 rows)

solarbi=# SELECT run_id, count(*) AS filas_silver FROM silver.lectura_5min GROUP BY run_id;
                run_id                | filas_silver 
--------------------------------------+--------------
 d5f8cef3-6572-4355-b239-d155d4be9048 |          842
(1 row)
```

## 4. `dwh.fact_energia_dia`

Una fila por día **local** (America/Bogota) y dispositivo. `energia_kwh` = Σ `p_ac_kw` × 5/60;
`pct_datos_validos` = lecturas válidas únicas / 288.

```
solarbi=# SELECT * FROM dwh.fact_energia_dia ORDER BY fecha_key;
 fecha_key | dispositivo_key | energia_kwh | lecturas_validas | lecturas_esperadas | pct_datos_validos | cumple_sla | p_max_kw | irradiacion_kwh_m2 |                run_id                |          updated_at           
-----------+-----------------+-------------+------------------+--------------------+-------------------+------------+----------+--------------------+--------------------------------------+-------------------------------
  20261005 |               1 |     27.4233 |              282 |                288 |             97.92 | t          |    4.305 |             6.2778 | d5f8cef3-6572-4355-b239-d155d4be9048 | 2026-10-09 01:31:51.768927+00
  20261006 |               1 |     26.3838 |              279 |                288 |             96.88 | t          |    4.388 |             5.9143 | d5f8cef3-6572-4355-b239-d155d4be9048 | 2026-10-09 01:31:51.768927+00
  20261007 |               1 |     27.1486 |              281 |                288 |             97.57 | t          |    4.278 |             6.2350 | d5f8cef3-6572-4355-b239-d155d4be9048 | 2026-10-09 01:31:51.768927+00
(3 rows)
```

## 5. Archivo con fallas inyectadas

`data/samples/telemetria_fallas_seed42.csv` se generó con
`python etl/simulador_fallas.py --seed 42 --inject-faults` (días 2026-10-08 a 2026-10-10, para no
pisar los datos del simulador del docente). Incluye un disparo del inversor (potencia 0 de 12:00 a
12:40 del segundo día, con irradiancia normal) y un corte de comunicación (sin lecturas de 10:00 a
10:20 del tercer día).

```
$ python -m etl.run_etl --file data/samples/telemetria_fallas_seed42.csv
[1/8] Contrato y archivo                              ok    0.03 s
      contrato telemetria v1.1.0; archivo data/samples/telemetria_fallas_seed42.csv
      sha256 11af6d489ba97636fed81cd4343e79e1d26650e0e3233ac967a7d32538eb5007
[2/8] Abrir corrida en dq.etl_run_log                 ok    0.01 s
      run_id b33145a3-2bf7-4e54-924c-186393f267e4
[3/8] Bronze                                          ok    0.02 s
      883 filas copiadas a bronze.telemetria_raw
[4/8] Silver (reglas de calidad + UPSERT)             ok    0.07 s
[5/8] Dimensiones                                     ok    0.02 s
[6/8] Gold (dwh.fact_energia_dia por día local)       ok    0.01 s
[7/8] Fallas (dq.fault_event)                         ok    0.09 s
[8/8] Cerrar corrida (succeeded)                      ok    0.00 s

Reporte de calidad (Paso 2)
Corrida                 : b33145a3-2bf7-4e54-924c-186393f267e4
Archivo                 : data/samples/telemetria_fallas_seed42.csv (contrato v1.1.0)
Bronze                  : cargado (883 filas nuevas)
Filas leídas            : 883
Rechazadas por regla    : missing_key=0, invalid_format=0, unknown_device=0, off_grid=0, range_p_ac_kw=17, missing_p_ac_kw=0
Rechazadas (distintas)  : 17
Deduplicadas            : 23
Marcadas (flag)         : snapped_to_grid=0, missing_irradiancia=15, range_irradiancia=0, range_temp_modulo=0  (15 filas)
Filas válidas           : 843
% datos válidos         : 95.47 %
Días cargados en Gold   : 3   (energía total = 77.437 kWh)
Eventos de falla        : 2

Nota: los conteos por regla pueden solaparse (una fila puede fallar varias reglas).
Cuadre: leídas = válidas + rechazadas distintas + deduplicadas -> 883 = 843 + 17 + 23 (OK)
exit=0
```

Eventos detectados en `dq.fault_event` (horas en hora local):

```
solarbi=# SELECT fault_id, dispositivo_id, rule_id, severity, ts_inicio AT TIME ZONE 'America/Bogota' AS inicio_local, ts_fin AT TIME ZONE 'America/Bogota' AS fin_local, lecturas FROM dq.fault_event ORDER BY ts_inicio;
 fault_id | dispositivo_id |         rule_id         | severity |    inicio_local     |      fin_local      | lecturas 
----------+----------------+-------------------------+----------+---------------------+---------------------+----------
        1 | 1              | zero_power_daylight     | critical | 2026-10-09 12:00:00 | 2026-10-09 12:40:00 |        8
        2 | 1              | missing_daytime_reading | warning  | 2026-10-10 10:00:00 | 2026-10-10 10:20:00 |        4
(2 rows)
```

## 6. SQL generado desde el contrato

El ETL no escribe SQL a mano por columna: lo genera `etl/sqlgen.py` a partir del contrato con
`psycopg.sql` (identificadores con `sql.Identifier`, valores con `sql.Literal`). Este es el SQL
generado para una regla de calidad y el UPSERT completo de Gold (`<run_id>` reemplaza el
identificador de la corrida).

```sql
-- 1) Expression generated for rule range_p_ac_kw (check: range, action: reject)
COALESCE(("p_ac_kw" < 0.0 OR "p_ac_kw" > 1.1 * "dev__nominal_kwp"), false) AS "rule__range_p_ac_kw"

-- 2) Where it is used: the 'checked' CTE computes every rule, 'decided' ORs the reject rules
decided AS (
    SELECT checked.*, ("rule__missing_key" OR "rule__invalid_format" OR "rule__unknown_device" OR "rule__off_grid" OR "rule__range_p_ac_kw" OR "rule__missing_p_ac_kw") AS "rechazada"
    FROM checked
)

-- 3) Its count for dq.rule_result (reject rules count over all rows read)
count(*) FILTER (WHERE "rule__range_p_ac_kw") AS "rule__range_p_ac_kw",

-- 4) Full UPSERT of dwh.fact_energia_dia
WITH agg AS (
    SELECT dd.fecha, dd.dispositivo_key,
           count(*) AS lecturas,
           sum(l."p_ac_kw") AS suma_potencia,
           max(l."p_ac_kw") AS potencia_max,
           sum(l."irradiancia_wm2") FILTER (WHERE NOT (l.dq_flags && ARRAY['missing_irradiancia', 'range_irradiancia']::text[])) AS suma_irradiancia
    FROM "etl_dias" AS dd
    JOIN "silver"."lectura_5min" AS l
      ON l."dispositivo_id" = dd.dispositivo_id AND l.ts >= dd.dia_inicio AND l.ts < dd.dia_fin
    GROUP BY dd.fecha, dd.dispositivo_key
), upserted AS (
    INSERT INTO dwh.fact_energia_dia AS f (
        fecha_key, dispositivo_key, energia_kwh, lecturas_validas, lecturas_esperadas,
        cumple_sla, p_max_kw, irradiacion_kwh_m2, run_id, updated_at
    )
    SELECT to_char(a.fecha, 'YYYYMMDD')::integer,
           a.dispositivo_key,
           -- energy of a reading = power x frequency_seconds / 3600 (5/60 h for 300 s)
           round((a.suma_potencia * 300 / 3600.0)::numeric, 4),
           a.lecturas,
           288,
           round(100.0 * a.lecturas / 288, 2) >= 95.0,
           round(a.potencia_max::numeric, 3),
           round((a.suma_irradiancia * 300 / 3600.0 / 1000.0)::numeric, 4),
           '<run_id>'::uuid,
           now()
    FROM agg AS a
    ON CONFLICT (fecha_key, dispositivo_key) DO UPDATE SET
        energia_kwh = EXCLUDED.energia_kwh,
        lecturas_validas = EXCLUDED.lecturas_validas,
        lecturas_esperadas = EXCLUDED.lecturas_esperadas,
        cumple_sla = EXCLUDED.cumple_sla,
        p_max_kw = EXCLUDED.p_max_kw,
        irradiacion_kwh_m2 = EXCLUDED.irradiacion_kwh_m2,
        run_id = EXCLUDED.run_id,
        updated_at = EXCLUDED.updated_at
    RETURNING f.energia_kwh
)
SELECT count(*), COALESCE(sum(energia_kwh), 0) FROM upserted
```

## 7. Programación diaria (escrita, no instalada)

```text
# cron (servidor con zona horaria America/Bogota): todos los días a la medianoche
0 0 * * * cd /ruta/solarbi-romero-dorado && .venv/bin/python -m etl.run_etl --file data/bronze/telemetria.csv
```

El equivalente en el Programador de tareas de Windows y los detalles de operación están en
[docs/operacion.md](../operacion.md).

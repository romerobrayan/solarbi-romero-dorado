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

`data/samples/telemetria_fallas_seed42.csv` se genera con el simulador extendido (semilla fija: los
mismos argumentos producen siempre el mismo archivo). Cubre del **2 al 4 de octubre de 2026**, los
tres días anteriores al archivo del docente, para no pisar sus datos ni quedar en el futuro. Incluye
un disparo del inversor (potencia 0 de 12:00 a 12:40 del segundo día, con irradiancia normal) y un
corte de comunicación (sin lecturas de 10:00 a 10:20 del tercer día).

```
$ python etl/simulador_fallas.py --seed 42 --inject-faults
883 filas escritas en data\samples\telemetria_fallas_seed42.csv
```

```
$ python -m etl.run_etl --file data/samples/telemetria_fallas_seed42.csv
[1/8] Contrato y archivo                              ok    0.04 s
      contrato telemetria v1.1.0; archivo data/samples/telemetria_fallas_seed42.csv
      sha256 1ae0fe6c169243cce70658a8c3911623cf8072fb26d030f8aea76f8ad668d89c
[2/8] Abrir corrida en dq.etl_run_log                 ok    0.01 s
      run_id 218cbb39-3d00-4304-8dcd-351a9a666041
[3/8] Bronze                                          ok    0.03 s
      883 filas copiadas a bronze.telemetria_raw
[4/8] Silver (reglas de calidad + UPSERT)             ok    0.11 s
[5/8] Dimensiones                                     ok    0.02 s
[6/8] Gold (dwh.fact_energia_dia por día local)       ok    0.02 s
[7/8] Fallas (dq.fault_event)                         ok    0.03 s
[8/8] Cerrar corrida (succeeded)                      ok    0.01 s

Reporte de calidad (Paso 2)
Corrida                 : 218cbb39-3d00-4304-8dcd-351a9a666041
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
        3 | 1              | zero_power_daylight     | critical | 2026-10-03 12:00:00 | 2026-10-03 12:40:00 |        8
        4 | 1              | missing_daytime_reading | warning  | 2026-10-04 10:00:00 | 2026-10-04 10:20:00 |        4
(2 rows)
```

Conteos finales: Silver, Gold y los eventos quedan del 2 al 7 de octubre; Bronze conserva todo lo
que se recibió, incluido lo purgado (sección 5.1).

```
$ python scripts/conteos.py
tabla                       filas
bronze.telemetria_raw        2904
silver.lectura_5min          1685
dwh.fact_energia_dia            6
dq.fault_event                  2
dq.etl_run_log                 11

fecha_key  disp.   energia_kwh  lecturas  % válidos
20261002   1           26.9448       284      98.61
20261003   1           24.9322       284      98.61
20261004   1           25.5603       275      95.49
20261005   1           27.4233       282      97.92
20261006   1           26.3838       279      96.88
20261007   1           27.1486       281      97.57
```

### 5.1 Cambio de fechas del archivo con fallas (fase 3.1)

La primera versión de este archivo empezaba el 8 de octubre de 2026, el día de la ejecución, y
llegaba al 10: parte de sus lecturas eran del futuro, y para verlas el tablero de Grafana tenía que
terminar en `now+2d`. El simulador ahora empieza el 2 de octubre, y los días 8 a 10 se retiraron
con `scripts/purge_days.py`, que se ejecuta como `etl_writer` en una sola transacción. La purga
también se llevó lo que la réplica en vivo de la fase 3 había cargado ese día.

Bronze no se puede borrar ni modificar (su *trigger* lo impide), así que la purga **no borra**
Bronze: su fila en `dq.purga_log` (migración 0008) marca como reemplazadas las filas de Bronze de
esos días que se cargaron antes de la purga, y el ETL ya no las transforma
([ADR 0007](../adr/0007-elt-in-sql-generated-from-contract.md#update--phase-31-2026-10-08-purging-days-without-touching-bronze)).

Migración:

```
Database: postgresql://solarbi_owner:***@127.0.0.1:5433/solarbi
  applied 0008_dq_purga_log (78 ms)
Applied 1 migration(s); database is at version 0008.
etl_writer login OK
```

Antes de la purga:

```
$ python scripts/conteos.py
tabla                       filas
bronze.telemetria_raw        2021
silver.lectura_5min          1689
dwh.fact_energia_dia            6
dq.fault_event                  2
dq.etl_run_log                  8

fecha_key  disp.   energia_kwh  lecturas  % válidos
20261005   1           27.4233       282      97.92
20261006   1           26.3838       279      96.88
20261007   1           27.1486       281      97.57
20261008   1           27.4413       288     100.00
20261009   1           24.9322       284      98.61
20261010   1           25.5603       275      95.49
```

Sin `--apply`, la purga solo informa y revierte:

```
$ python scripts/purge_days.py --from 2026-10-08 --to 2026-10-10 --motivo "Muestra simulada con fallas fechada del 8 al 10 de octubre (incluye días futuros); se reemplaza por la del 2 al 4 de octubre (fase 3.1)"
Purga de días 2026-10-08 a 2026-10-10 (America/Bogota)
  rango             2026-10-08 05:00 UTC -> 2026-10-11 05:00 UTC (fin excluido)
  bronze.telemetria_raw   1142 filas marcadas como reemplazadas (no se borran: Bronze es de solo inserción)
  silver.lectura_5min      847 filas borradas
  dwh.fact_energia_dia       3 filas borradas
  dq.fault_event             2 eventos borrados
SIMULACIÓN: no se borró nada (se revirtió). Agregue --apply para ejecutarla.
exit=0
```

Con `--apply`; después, los conteos (Bronze no cambia):

```
$ python scripts/purge_days.py --from 2026-10-08 --to 2026-10-10 --motivo "Muestra simulada con fallas fechada del 8 al 10 de octubre (incluye días futuros); se reemplaza por la del 2 al 4 de octubre (fase 3.1)" --apply
Purga de días 2026-10-08 a 2026-10-10 (America/Bogota)
  rango             2026-10-08 05:00 UTC -> 2026-10-11 05:00 UTC (fin excluido)
  bronze.telemetria_raw   1142 filas marcadas como reemplazadas (no se borran: Bronze es de solo inserción)
  silver.lectura_5min      847 filas borradas
  dwh.fact_energia_dia       3 filas borradas
  dq.fault_event             2 eventos borrados
Aplicada y registrada en dq.purga_log (purga_id 8e394931-4253-489c-8025-fe62a3dd4eab).
exit=0

$ python scripts/conteos.py
tabla                       filas
bronze.telemetria_raw        2021
silver.lectura_5min           842
dwh.fact_energia_dia            3
dq.fault_event                  0
dq.etl_run_log                  8

fecha_key  disp.   energia_kwh  lecturas  % válidos
20261005   1           27.4233       282      97.92
20261006   1           26.3838       279      96.88
20261007   1           27.1486       281      97.57
```

Registro de la purga:

```
solarbi=# SELECT dia_desde, dia_hasta, zona_horaria, ts_desde, ts_hasta, ejecutada_por, filas_bronze_reemplazadas AS bronze, filas_silver AS silver, filas_gold AS gold, eventos_falla AS fallas, motivo FROM dq.purga_log;
 dia_desde  | dia_hasta  |  zona_horaria  |        ts_desde        |        ts_hasta        | ejecutada_por | bronze | silver | gold | fallas |                                                                 motivo                                                                  
------------+------------+----------------+------------------------+------------------------+---------------+--------+--------+------+--------+-----------------------------------------------------------------------------------------------------------------------------------------
 2026-10-08 | 2026-10-10 | America/Bogota | 2026-10-08 05:00:00+00 | 2026-10-11 05:00:00+00 | etl_writer    |   1142 |    847 |    3 |      2 | Muestra simulada con fallas fechada del 8 al 10 de octubre (incluye días futuros); se reemplaza por la del 2 al 4 de octubre (fase 3.1)
(1 row)
```

Volver a ejecutar el archivo viejo (mismo SHA-256, así que se reutilizan sus filas de Bronze) **no**
trae de vuelta los días purgados: sus 883 filas aparecen como reemplazadas y no se lee ninguna. Se
ejecutó dos veces; la primera, antes de agregar la línea "Reemplazadas (purga)" al reporte, dio el
mismo resultado (por eso `dq.etl_run_log` suma dos corridas).

```
$ python -m etl.run_etl --file data/samples/telemetria_fallas_seed42.csv   # archivo viejo (8-10 oct), después de la purga
[1/8] Contrato y archivo                              ok    0.12 s
      contrato telemetria v1.1.0; archivo data/samples/telemetria_fallas_seed42.csv
      sha256 11af6d489ba97636fed81cd4343e79e1d26650e0e3233ac967a7d32538eb5007
[2/8] Abrir corrida en dq.etl_run_log                 ok    0.01 s
      run_id c432b705-a2bb-45dd-a782-4ca59604ed0c
[3/8] Bronze                                          ok    0.01 s
      archivo ya cargado (mismo sha256): se reutilizan sus filas de Bronze de la corrida b33145a3-2bf7-4e54-924c-186393f267e4
[4/8] Silver (reglas de calidad + UPSERT)             ok    0.08 s
[5/8] Dimensiones                                     ok    0.03 s
[6/8] Gold (dwh.fact_energia_dia por día local)       ok    0.01 s
[7/8] Fallas (dq.fault_event)                         ok    0.03 s
[8/8] Cerrar corrida (succeeded)                      ok    0.00 s

Reporte de calidad (Paso 2)
Corrida                 : c432b705-a2bb-45dd-a782-4ca59604ed0c
Archivo                 : data/samples/telemetria_fallas_seed42.csv (contrato v1.1.0)
Bronze                  : omitido: archivo ya cargado (filas de la corrida b33145a3-2bf7-4e54-924c-186393f267e4)
Reemplazadas (purga)    : 883 filas de Bronze en días purgados (dq.purga_log): no se transforman
Filas leídas            : 0
Rechazadas por regla    : missing_key=0, invalid_format=0, unknown_device=0, off_grid=0, range_p_ac_kw=0, missing_p_ac_kw=0
Rechazadas (distintas)  : 0
Deduplicadas            : 0
Marcadas (flag)         : snapped_to_grid=0, missing_irradiancia=0, range_irradiancia=0, range_temp_modulo=0  (0 filas)
Filas válidas           : 0
% datos válidos         : 0.00 %
Días cargados en Gold   : 0   (energía total = 0.000 kWh)
Eventos de falla        : 0

Nota: los conteos por regla pueden solaparse (una fila puede fallar varias reglas).
Cuadre: leídas = válidas + rechazadas distintas + deduplicadas -> 0 = 0 + 0 + 0 (OK)
exit=0

$ python scripts/conteos.py
tabla                       filas
bronze.telemetria_raw        2021
silver.lectura_5min           842
dwh.fact_energia_dia            3
dq.fault_event                  0
dq.etl_run_log                 10

fecha_key  disp.   energia_kwh  lecturas  % válidos
20261005   1           27.4233       282      97.92
20261006   1           26.3838       279      96.88
20261007   1           27.1486       281      97.57
```

Después se generó y cargó el archivo nuevo, con las salidas del comienzo de esta sección.

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

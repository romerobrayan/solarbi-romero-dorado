# Operación del ETL

Guía para ejecutar, programar y diagnosticar el ETL de SolarBI Pascual. Todos los comandos se
ejecutan desde la raíz del repositorio con el entorno virtual activo y la base arriba
(`docker compose up -d`).

## Ejecutar

```powershell
python -m etl.run_etl --file data/bronze/telemetria.csv
```

También funciona `python etl/run_etl.py --file ...`. Cada corrida imprime los 8 pasos con su
duración y, al final, el reporte de calidad del Paso 2 (filas leídas, rechazadas por regla,
deduplicadas, marcadas, válidas, porcentaje de datos válidos, días cargados en Gold y eventos de
falla).

| Código de salida | Significado |
|---|---|
| 0 | Corrida exitosa (`status = 'succeeded'` en `dq.etl_run_log`) |
| 1 | La corrida falló y se revirtió por completo; el error quedó en `dq.etl_run_log.error_message` |
| 2 | Problema de configuración, de contrato o de conexión; no se abrió ninguna corrida |

## Volver a ejecutar el mismo archivo

Es seguro y es la prueba de idempotencia: si el SHA-256 del archivo ya fue cargado por una corrida
exitosa, el archivo **no** se copia otra vez a Bronze, pero Silver, Gold y las fallas se recalculan
con UPSERT a partir de las filas de Bronze ya cargadas. Los conteos no cambian. La corrida queda
registrada con `bronze_status = 'skipped_duplicate_file'`.

Para comprobarlo:

```powershell
python scripts/conteos.py
python -m etl.run_etl --file data/bronze/telemetria.csv
python scripts/conteos.py
```

Para forzar una recarga deliberada en Bronze (por ejemplo, después de corregir el contrato y querer
conservar ambas versiones crudas), use `--force-reload`. Bronze crece; Silver y Gold quedan igual
si los datos son los mismos.

## Programar la ejecución diaria

La carga se programa todos los días a la medianoche, hora de Colombia (`sla.freshness` del
contrato). **Estas instrucciones están escritas, no instaladas**: ninguna tarea programada se
registró en el equipo de desarrollo.

### Linux / macOS (cron)

```text
# m h dom mon dow  comando
0 0 * * * cd /ruta/a/solarbi-romero-dorado && .venv/bin/python -m etl.run_etl --file data/bronze/telemetria.csv >> logs/etl.log 2>&1
```

`0 0 * * *` usa la zona horaria del servidor: debe ser `America/Bogota`
(`timedatectl set-timezone America/Bogota`). Donde cron lo soporta (cronie), también se puede
fijar en la tabla: `CRON_TZ=America/Bogota` en una línea antes de la tarea.

### Windows (Programador de tareas)

```powershell
schtasks /Create /SC DAILY /ST 00:00 /TN SolarBI_ETL /TR "\"D:\ruta\solarbi-romero-dorado\.venv\Scripts\python.exe\" -m etl.run_etl --file \"D:\ruta\solarbi-romero-dorado\data\bronze\telemetria.csv\""
```

La tarea usa la hora local del equipo (Colombia). Como `-m etl.run_etl` resuelve el proyecto desde
el paquete instalado y el contrato desde `CONTRACTS_DIR`, no depende del directorio de trabajo;
`.env` se busca en la raíz del proyecto. Para quitarla: `schtasks /Delete /TN SolarBI_ETL /F`.

## Diagnóstico

```sql
-- Últimas corridas
SELECT run_id, started_at, status, bronze_status, filas_leidas, filas_validas,
       pct_validas, duracion_s, error_message
FROM dq.etl_run_log ORDER BY started_at DESC LIMIT 10;

-- Filas afectadas por regla en una corrida
SELECT rule_id, action, filas_afectadas FROM dq.rule_result WHERE run_id = '<run_id>';

-- Lecturas marcadas en Silver
SELECT ts, dq_flags, ts_origen FROM silver.lectura_5min WHERE dq_flags <> '{}' ORDER BY ts;
```

- **El archivo no coincide con el contrato** (columna renombrada, faltante o nueva sin anunciar): el
  ETL se detiene antes de abrir la corrida (código 2). Se publica una versión nueva del contrato o
  se corrige la fuente; nunca se edita el CSV de Bronze.
- **Una corrida falló a mitad de camino**: no hay que limpiar nada; la transacción revirtió Bronze,
  Silver, Gold y las fallas. Se corrige la causa y se vuelve a ejecutar.
- **Bronze es de solo inserción**: un *trigger* rechaza UPDATE, DELETE y TRUNCATE en
  `bronze.telemetria_raw`. Borrar una carga equivocada exige que el dueño de la base desactive el
  trigger a propósito.

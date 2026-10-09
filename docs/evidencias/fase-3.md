# Evidencias de la fase 3: tableros en Grafana y Power BI

Salidas **reales** obtenidas el 8 de octubre de 2026 (noche, hora de Colombia) contra la pila local
(Grafana 13.2.3, PostgreSQL 16 + TimescaleDB 2.30.2), copiadas sin editar. Incluyen los ajustes de
la fase 3.1: ningún dato del futuro (el archivo con fallas pasó al 2–4 de octubre y el tablero
muestra por defecto los últimos 7 días) y una segunda regla de alerta para un inversor que deja de
enviar datos. Donde dice **[CAPTURA]** va una imagen que debe tomar Brayan en su equipo.

## 1. Grafana aprovisionado como código

Estado consultado por la API HTTP de Grafana (usuario administrador de `.env`). Cada consulta de
paneles, variables y anotaciones se ejecutó a través del datasource (usuario `grafana_reader`) sobre
el rango por defecto del tablero, los últimos 7 días.

```text
GET /api/health -> database=ok version=13.2.3
GET /api/datasources/uid/solarbi-postgres/health -> OK: Database Connection OK
GET /api/search -> dashboard uid=solarbi-operacion title='SolarBI · Operación de la planta' folder='SolarBI'
GET /api/dashboards/uid/solarbi-operacion -> provisioned=False time={'from': 'now-7d', 'to': 'now'} file='solarbi-operacion.json' panels=6
GET /api/v1/provisioning/alert-rules -> uid=solarbi-potencia-cero title='Potencia cero en horario solar' for=10m labels={'severity': 'critical'} provenance='file'
GET /api/v1/provisioning/alert-rules -> uid=solarbi-sin-datos title='Inversor sin datos en horario solar' for=0s labels={'severity': 'warning'} provenance='file'
GET /api/v1/provisioning/contact-points -> correo-operador (email)
GET /api/v1/provisioning/contact-points -> webhook-local (webhook)
GET /api/v1/provisioning/policies -> default=correo-operador routes=[{"receiver": "webhook-local", "object_matchers": [["severity", "=", "critical"]], "group_by": ["alertname", "dispositivo"], "repeat_interval": "1h"}]

panel 1 A (Potencia AC cada 5 minutos (kW) e irra)         ok  frames=1 rows=1954 cols=['Time', 'Potencia (kW) · 1']
panel 1 B (Potencia AC cada 5 minutos (kW) e irra)         ok  frames=1 rows=1954 cols=['Time', 'Irradiancia (W/m²) · 1']
panel 2 A (Potencia promedio por hora (kW))                ok  frames=1 rows=167 cols=['Time', 'Promedio horario · 1']
panel 3 A (Energía del día seleccionado (${dia:te)         ok  frames=1 rows=1 cols=['Energía del día', 'Datos válidos', 'Cumple SLA']
panel 6 A (Eventos de falla (dq.fault_event))              ok  frames=1 rows=2 cols=['Inicio', 'Fin', 'Dispositivo', 'Regla', 'Severidad', 'Lecturas', 'Minutos']
panel 4 A (Calidad: últimas corridas del ETL (dq.)         ok  frames=1 rows=15 cols=['Inicio', 'Archivo', 'Estado', 'Bronze', 'Leídas', 'Válidas', '% válidas', 'Duración (s)']
panel 5 A (Calidad: filas afectadas por regla (co)         ok  frames=1 rows=11 cols=['Regla', 'Filas']
variable $dispositivo                                      ok  frames=1 rows=1 cols=['dispositivo_id']
variable $dia                                              ok  frames=1 rows=7 cols=['__text', '__value']
annotation Eventos de falla (dq.fault_eve                  ok  frames=1 rows=2 cols=['time', 'timeend', 'text', 'tags']
```

**[CAPTURA 1: tablero "SolarBI · Operación de la planta" con su rango por defecto (últimos 7 días):
potencia cada 5 minutos con irradiancia, franjas de 09:00–15:00 y anotaciones de falla del 3 y el 4
de octubre; promedio horario; energía del día; tablas de fallas y de calidad.]**

## 2. Reglas de alerta

Dos reglas, porque una regla sobre las lecturas no puede ver las lecturas que nunca llegan:

| Falla | ¿Hay filas en Silver? | Regla que la detecta |
|---|---|---|
| Disparo del inversor (potencia cero) | Sí, en 0 kW | **Potencia cero en horario solar** (crítica, webhook) |
| Inversor o gateway sin enviar | No | **Inversor sin datos en horario solar** (advertencia, correo) |
| Noche | No | Ninguna: ambas se limitan a 09:00–15:00 |

La consulta de cada regla, tal como quedó guardada en Grafana, evaluada sobre ventanas de 15
minutos con resultado conocido (el disparo del 3 de octubre, el corte del 4, un día sin ninguna
lectura y la noche). Al final, el estado real de las reglas.

```text
$ GET /api/v1/provisioning/alert-rules
  solarbi-potencia-cero  Potencia cero en horario solar         severity=critical for=10m  noDataState=OK     provenance=file
  solarbi-sin-datos      Inversor sin datos en horario solar    severity=warning  for=0s   noDataState=NoData provenance=file

Regla 'Potencia cero en horario solar' (ventana de 15 min que termina en la hora indicada):
  Disparo del 3 oct (última lectura 12:20, p = 0)              10-03 12:06-12:21 -> [('1', 1)]
  Después del disparo (última lectura 12:55, p > 0)            10-03 12:41-12:56 -> [('1', 0)]
  Corte del 4 oct (sin lecturas 10:00-10:15)                   10-04 09:59-10:14 -> sin filas (NoData)
  Ahora (noche)                                                10-08 22:20-22:35 -> [('1', 0)]

Regla 'Inversor sin datos en horario solar':
  Antes del corte del 4 oct (lecturas 09:50 y 09:55)           10-04 09:50-10:05 -> [('1', 0)]
  Corte del 4 oct (sin lecturas 10:00-10:15)                   10-04 09:59-10:14 -> [('1', 1)]
  Corte terminado (lectura 10:20 recibida)                     10-04 10:06-10:21 -> [('1', 0)]
  Disparo del 3 oct (llegan lecturas en cero)                  10-03 12:06-12:21 -> [('1', 0)]
  1 oct 10:00 (no hay ninguna lectura de ese día)              10-01 09:45-10:00 -> [('1', 1)]
  Ahora (noche)                                                10-08 22:20-22:35 -> [('1', 0)]

estado actual de 'Potencia cero en horario solar': inactive (health ok) [1=Normal]
estado actual de 'Inversor sin datos en horario solar': inactive (health ok) [1=Normal]
```

## 3. Notificación entregada por el webhook

Prueba del *contact point* `webhook-local` con la API de Grafana 13 y lo que recibió el servicio
`alert-receiver` (perfil `alerting` de Docker Compose):

```text
POST /apis/notifications.alerting.grafana.app/v1beta1/namespaces/default/receivers/d2ViaG9vay1sb2NhbA/test -> {"status": "success", "duration": "32ms"}

$ docker compose --profile alerting logs alert-receiver   (extracto: cuerpo recibido y línea de acceso)
    "json": {
        "receiver": "webhook",
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": "Potencia cero en horario solar",
                    "dispositivo": "1",
                    "instance": "Grafana",
                    "severity": "critical"
                },
                "annotations": {
                    "summary": "Prueba del contact point webhook-local (SolarBI)"
                },
                "startsAt": "2026-10-09T02:09:35.563550793Z",
                "endsAt": "0001-01-01T00:00:00Z",
                "generatorURL": "",
                "fingerprint": "a1f8327b740a4b4f",
                "dashboardURL": "",
                "panelURL": "",
                "values": null,
                "valueString": "[ metric='foo' labels={instance=bar} value=10 ]"
            }
        ],
        "groupLabels": {
            "alertname": "Potencia cero en horario solar",
            "dispositivo": "1",
            "instance": "Grafana",
            "severity": "critical"
        },
        "commonLabels": {
            "alertname": "Potencia cero en horario solar",
            "dispositivo": "1",
            "instance": "Grafana",
            "severity": "critical"
        },
        "commonAnnotations": {
            "summary": "Prueba del contact point webhook-local (SolarBI)"
        },
        "externalURL": "http://localhost:3000/",
        "appVersion": "13.2.3",
        "version": "1",
        "groupKey": "webhook-a1f8327b740a4b4f-1791511775",
        "truncatedAlerts": 0,
        "orgId": 1,
        "title": "[FIRING:1] Potencia cero en horario solar 1 Grafana critical ",
        "state": "alerting",
        "message": "**Firing**\n\nValue: [no value]\nLabels:\n - alertname = Potencia cero en horario solar\n - dispositivo = 1\n - instance = Grafana\n - sever
    }
::ffff:172.19.0.4 - - [09/Oct/2026:02:09:35 +0000] "POST /grafana/alertas HTTP/1.1" 200 4683 "-" "Grafana"
```

## 4. Réplica en vivo (de noche)

`scripts/replay_live.py` simula el envío IoT con la hora actual. Esta ejecución, con `--gap-in`, fue
a las 22:09, fuera del horario solar: el script lo advierte, completa el día de hoy hasta la última
ranura, deja de enviar 15 minutos y vuelve a enviar. Las lecturas pasan por el ETL normal y las dos
reglas se mantienen en **Normal**, que es lo correcto de noche.

```text
$ python scripts/replay_live.py --gap-in 5m --gap-minutes 15 --recovery-minutes 5
Réplica en vivo (simula el envío IoT; no hay un dispositivo real)
  ahora 2026-10-08 22:09:26 (America/Bogota); dispositivo 1
  corte 22:15-22:30 (no se envía nada); fin 22:35
  AVISO: el corte (22:15-22:30) queda fuera del horario solar de la regla (09:00-15:00 hora local): los datos se cargan, pero la alerta seguirá en Normal. Ejecútelo entre esas horas.
[22:09:26] respaldo de hoy 00:00-22:05: 266 lecturas | ETL ok (266/266 válidas, 0 eventos) | alertas: potencia cero inactive [1=Normal (NoData)] · sin datos inactive [1=Normal]
[22:10:00] lectura 22:10         p_ac_kw=0.0    irr=0.0    | ETL ok (1/1 válidas, 0 eventos) | alertas: potencia cero inactive [1=Normal (NoData)] · sin datos inactive [1=Normal]
[22:15:00] lectura 22:15 CORTE    sin envío (el inversor no reporta) | alertas: potencia cero inactive [1=Normal] · sin datos inactive [1=Normal]
[22:20:00] lectura 22:20 CORTE    sin envío (el inversor no reporta) | alertas: potencia cero inactive [1=Normal] · sin datos inactive [1=Normal]
[22:25:00] lectura 22:25 CORTE    sin envío (el inversor no reporta) | alertas: potencia cero inactive [1=Normal] · sin datos inactive [1=Normal]
[22:30:00] lectura 22:30         p_ac_kw=0.0    irr=0.0    | ETL ok (1/1 válidas, 0 eventos) | alertas: potencia cero inactive [1=Normal (NoData)] · sin datos inactive [1=Normal]
[22:35:00] lectura 22:35         p_ac_kw=0.0    irr=0.0    | ETL ok (1/1 válidas, 0 eventos) | alertas: potencia cero inactive [1=Normal] · sin datos inactive [1=Normal]
Fin de la réplica. Estado final: potencia cero inactive [1=Normal] · sin datos inactive [1=Normal]
exit=0
```

### 4.1 Réplicas en horario solar (pendiente)

**[PENDIENTE: ejecutar entre las 09:00 y las 15:00 hora de Colombia]**

```powershell
docker compose --profile alerting up -d
python scripts/replay_live.py --trip-in 5m --trip-minutes 20 | Tee-Object docs/evidencias/replay_disparo.txt
python scripts/replay_live.py --gap-in 5m --gap-minutes 20 | Tee-Object docs/evidencias/replay_corte.txt
docker compose --profile alerting logs alert-receiver > docs/evidencias/webhook_dia.txt
```

Se espera, con `--trip-in`: `potencia cero inactive [1=Normal]` → `pending [1=Pending]` en la
primera lectura en cero → `firing [1=Alerting]` unos 10 minutos después → `inactive [1=Normal]`
cuando vuelve la potencia, y en el log del receptor un mensaje `[FIRING:1] Potencia cero en horario
solar` seguido de uno `[RESOLVED]`. Con `--gap-in`: `sin datos inactive [1=Normal]` →
`firing [1=Alerting]` 15 minutos después de la última lectura → `inactive [1=Normal]` cuando llega
la siguiente; esta regla notifica por correo (sin SMTP configurado, el envío falla en el log de
Grafana, pero el estado se ve en la interfaz). Al volver los datos, el ETL registra el hueco como
evento `missing_daytime_reading`.

**[CAPTURA 2: Alerting → Alert rules con "Potencia cero en horario solar" en Firing.]**

**[CAPTURA 3: Alerting → Alert rules con "Inversor sin datos en horario solar" en Firing.]**

**[CAPTURA 4: panel de potencia durante las réplicas: la caída a cero, el hueco del corte y las
anotaciones.]**

## 5. Panel "Energía del día" frente a `dwh.fact_energia_dia`

Para cada opción de la variable `$dia`, el valor que calcula el panel (consulta del panel ejecutada
por Grafana como `grafana_reader`) junto al valor de la tabla de hechos leído directamente. El 8 de
octubre es el día en curso, cargado por la réplica hasta la noche.

```text
$dia (texto)  $dia (valor)   Panel stat (kWh)  fact_energia_dia (kWh)  % válidos  SLA  ¿Iguales?
2026-10-08    20261008                27.4413                 27.4413       93.4   No  sí
2026-10-07    20261007                27.1486                 27.1486      97.57   Sí  sí
2026-10-06    20261006                26.3838                 26.3838      96.88   Sí  sí
2026-10-05    20261005                27.4233                 27.4233      97.92   Sí  sí
2026-10-04    20261004                25.5603                 25.5603      95.49   Sí  sí
2026-10-03    20261003                24.9322                 24.9322      98.61   Sí  sí
2026-10-02    20261002                26.9448                 26.9448      98.61   Sí  sí

Todos los días coinciden: sí (7 días)
```

## 6. Exportación del tablero (ida y vuelta)

```text
$ python scripts/export_grafana.py
grafana/dashboards/solarbi-operacion.json: unchanged (uid solarbi-operacion)
$ python scripts/export_grafana.py --check
grafana/dashboards/solarbi-operacion.json: matches Grafana
exit=0
```

## 7. Consultas SQL para el informe

### Panel 1: potencia cada 5 minutos (con `$__timeFilter`)

```sql
SELECT ts AS "time",
       'Potencia (kW) · ' || dispositivo_id AS metric,
       p_ac_kw AS value
FROM silver.lectura_5min
WHERE $__timeFilter(ts)
  AND dispositivo_id IN ($dispositivo)
ORDER BY 1

SELECT ts AS "time",
       'Irradiancia (W/m²) · ' || dispositivo_id AS metric,
       irradiancia_wm2 AS value
FROM silver.lectura_5min
WHERE $__timeFilter(ts)
  AND dispositivo_id IN ($dispositivo)
ORDER BY 1
```

### Panel 2: potencia promedio por hora (pregunta D4, `$__timeGroupAlias`)

```sql
SELECT $__timeGroupAlias(ts, '1h'),
       'Promedio horario · ' || dispositivo_id AS metric,
       avg(p_ac_kw) AS potencia_promedio_kw
FROM silver.lectura_5min
WHERE $__timeFilter(ts)
  AND dispositivo_id IN ($dispositivo)
GROUP BY 1, 2
ORDER BY 1
```

### Panel 3: energía del día seleccionado (Gold)

```sql
SELECT sum(f.energia_kwh) AS "Energía del día",
       round(100.0 * sum(f.lecturas_validas) / sum(f.lecturas_esperadas), 2) AS "Datos válidos",
       bool_and(f.cumple_sla)::int AS "Cumple SLA"
FROM dwh.fact_energia_dia AS f
JOIN dwh.dim_dispositivo AS d USING (dispositivo_key)
WHERE f.fecha_key = $dia
  AND d.dispositivo_id IN ($dispositivo)
```

### Regla "Potencia cero en horario solar"

```sql
SELECT DISTINCT ON (dispositivo_id)
       dispositivo_id AS dispositivo,
       CASE WHEN p_ac_kw <= 0.01
             AND (ts AT TIME ZONE 'America/Bogota')::time >= TIME '09:00'
             AND (ts AT TIME ZONE 'America/Bogota')::time <  TIME '15:00'
            THEN 1 ELSE 0 END AS potencia_cero
FROM silver.lectura_5min
WHERE $__timeFilter(ts)
ORDER BY dispositivo_id, ts DESC
```

### Regla "Inversor sin datos en horario solar"

```sql
SELECT d.dispositivo_id AS dispositivo,
       CASE WHEN ($__timeTo()::timestamptz AT TIME ZONE s.zona_horaria)::time >= TIME '09:00'
             AND ($__timeTo()::timestamptz AT TIME ZONE s.zona_horaria)::time <  TIME '15:00'
             AND count(l.ts) = 0
            THEN 1 ELSE 0 END AS sin_datos
FROM dwh.dim_dispositivo AS d
JOIN dwh.dim_sitio AS s USING (sitio_key)
LEFT JOIN silver.lectura_5min AS l
       ON l.dispositivo_id = d.dispositivo_id
      AND $__timeFilter(l.ts)
GROUP BY d.dispositivo_id, s.zona_horaria
```

## 8. Power BI

**[PENDIENTE: pasos en Power BI Desktop según docs/powerbi.md]**

**[CAPTURA 5: vista Modelo con las tres relaciones y la tabla `_Medidas`.]**

**[CAPTURA 6: página con la tarjeta "Energía total (kWh)", el gráfico de líneas "Energía diaria
(kWh)" por fecha y la tarjeta "% datos válidos".]**

**[CAPTURA 7: comprobación cruzada: tarjeta de energía filtrada al 2026-10-06 (26,384 kWh) junto
al panel de Grafana con `$dia = 2026-10-06`.]**

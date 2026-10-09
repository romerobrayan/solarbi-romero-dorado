# Evidencias de la fase 3: tableros en Grafana y Power BI

Salidas **reales** obtenidas el 8 de octubre de 2026 (noche, hora de Colombia) contra la pila local
(Grafana 13.2.3, PostgreSQL 16 + TimescaleDB 2.30.2), copiadas sin editar. Donde dice
**[CAPTURA]** va una imagen que debe tomar Brayan en su equipo.

## 1. Grafana aprovisionado como código

Estado consultado por la API HTTP de Grafana (usuario administrador de `.env`). Cada consulta de
paneles, variables y anotaciones se ejecutó a través del datasource (usuario `grafana_reader`) sobre
el rango 5–11 de octubre.

```text
GET /api/health -> database=ok version=13.2.3
GET /api/datasources/uid/solarbi-postgres/health -> OK: Database Connection OK
GET /api/search -> dashboard uid=solarbi-operacion title='SolarBI · Operación de la planta' folder='SolarBI'
GET /api/dashboards/uid/solarbi-operacion -> provisioned=False file='solarbi-operacion.json' panels=6
GET /api/v1/provisioning/alert-rules -> uid=solarbi-potencia-cero title='Potencia cero en horario solar' for=10m labels={'severity': 'critical'} provenance='file'
GET /api/v1/provisioning/contact-points -> correo-operador (email)
GET /api/v1/provisioning/contact-points -> webhook-local (webhook)
GET /api/v1/provisioning/policies -> default=correo-operador routes=[{"receiver": "webhook-local", "object_matchers": [["severity", "=", "critical"]], "group_by": ["alertname", "dispositivo"], "repeat_interval": "1h"}]

panel 1 A (Potencia AC cada 5 minutos (kW) e irra)         ok  frames=1 rows=1685 cols=['Time', 'Potencia (kW) · 1']
panel 1 B (Potencia AC cada 5 minutos (kW) e irra)         ok  frames=1 rows=1685 cols=['Time', 'Irradiancia (W/m²) · 1']
panel 2 A (Potencia promedio por hora (kW))                ok  frames=1 rows=144 cols=['Time', 'Promedio horario · 1']
panel 3 A (Energía del día seleccionado (${dia:te)         ok  frames=1 rows=1 cols=['Energía del día', 'Datos válidos', 'Cumple SLA']
panel 6 A (Eventos de falla (dq.fault_event))              ok  frames=1 rows=2 cols=['Inicio', 'Fin', 'Dispositivo', 'Regla', 'Severidad', 'Lecturas', 'Minutos']
panel 4 A (Calidad: últimas corridas del ETL (dq.)         ok  frames=1 rows=3 cols=['Inicio', 'Archivo', 'Estado', 'Bronze', 'Leídas', 'Válidas', '% válidas', 'Duración (s)']
panel 5 A (Calidad: filas afectadas por regla (co)         ok  frames=1 rows=11 cols=['Regla', 'Filas']
variable $dispositivo                                      ok  frames=1 rows=1 cols=['dispositivo_id']
variable $dia                                              ok  frames=1 rows=6 cols=['__text', '__value']
annotation Eventos de falla (dq.fault_eve                  ok  frames=1 rows=2 cols=['time', 'timeend', 'text', 'tags']
```

**[CAPTURA 1: tablero "SolarBI · Operación de la planta" con rango del 5 al 10 de octubre: potencia
cada 5 minutos con irradiancia, franjas de 09:00–15:00 y anotaciones de falla; promedio horario;
energía del día; tablas de fallas y de calidad.]**

## 2. Regla de alerta "Potencia cero en horario solar"

La consulta de la regla tal como quedó guardada en Grafana, evaluada sobre tres ventanas de 15
minutos (la regla mira siempre los últimos 15 minutos): durante el disparo del 9 de octubre (archivo
con fallas), después del disparo y en este momento (noche). Al final, el estado real de la regla.

```text
Stored rule query (as Grafana keeps it after env expansion):
SELECT DISTINCT ON (dispositivo_id)
       dispositivo_id AS dispositivo,
       CASE WHEN p_ac_kw <= 0.01
             AND (ts AT TIME ZONE 'America/Bogota')::time >= TIME '09:00'
             AND (ts AT TIME ZONE 'America/Bogota')::time <  TIME '15:00'
            THEN 1 ELSE 0 END AS potencia_cero
FROM silver.lectura_5min
WHERE $__timeFilter(ts)
ORDER BY dispositivo_id, ts DESC
contains '$__timeFilter(ts)': True | contains '$$': False
summary annotation: Inversor {{ $labels.dispositivo }} sin potencia en horario solar

During the trip (Oct 9, latest reading 12:20, p=0)   window 12:06-12:21 local -> [('1', 1)]
After the trip (Oct 9, latest reading 12:55, p>0)    window 12:41-12:56 local -> [('1', 0)]
Right now (night, outside 09:00-15:00)               window 20:51-21:06 local -> [('1', 0)]

current state of 'Potencia cero en horario solar': pending (health error, last evaluation 2026-10-09T02:06:10Z)
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

`scripts/replay_live.py` simula el envío IoT con la hora actual. Esta ejecución fue a las 21:12,
fuera del horario solar: el script lo advierte, las lecturas pasan por el ETL normal y la alerta se
mantiene en **Normal**, que es lo correcto (la regla solo considera lecturas entre 09:00 y 15:00).

```text
Réplica en vivo (simula el envío IoT; no hay un dispositivo real)
  ahora 2026-10-08 21:12:16 (America/Bogota); dispositivo 1
  disparo 21:15-21:25; fin 21:30
  AVISO: el disparo (21:15-21:25) queda fuera del horario solar de la regla (09:00-15:00 hora local): los datos se cargan, pero la alerta seguirá en Normal. Ejecútelo entre esas horas.
[21:12:16] respaldo de hoy hasta 21:10: 255 lecturas | ETL ok (255/255 válidas, 0 eventos) | alerta: inactive [1=Normal]
[21:15:00] lectura 21:15 DISPARO p_ac_kw=0.0    irr=0.0    | ETL ok (1/1 válidas, 0 eventos) | alerta: inactive [1=Normal]
[21:20:00] lectura 21:20 DISPARO p_ac_kw=0.0    irr=0.0    | ETL ok (1/1 válidas, 0 eventos) | alerta: inactive [1=Normal]
[21:25:00] lectura 21:25         p_ac_kw=0.0    irr=0.0    | ETL ok (1/1 válidas, 0 eventos) | alerta: inactive [1=Normal]
[21:30:00] lectura 21:30         p_ac_kw=0.0    irr=0.0    | ETL ok (1/1 válidas, 0 eventos) | alerta: inactive [1=Normal]
Fin de la réplica. Estado final de la alerta: inactive [1=Normal]
```

### 4.1 Réplica en horario solar (pendiente)

**[PENDIENTE: ejecutar entre las 09:00 y las 15:00 hora de Colombia]**

```powershell
docker compose --profile alerting up -d
python scripts/replay_live.py --trip-in 5m --trip-minutes 20 | Tee-Object docs/evidencias/replay_dia.txt
docker compose --profile alerting logs alert-receiver > docs/evidencias/webhook_dia.txt
```

Se espera: `alerta: inactive [1=Normal]` → `pending [1=Pending]` en la primera lectura en cero →
`firing [1=Alerting]` unos 10 minutos después → `inactive [1=Normal]` cuando vuelve la potencia, y en
el log del receptor un mensaje `[FIRING:1] Potencia cero en horario solar` seguido de uno
`[RESOLVED]`.

**[CAPTURA 2: Alerting → Alert rules con la regla en estado Firing.]**

**[CAPTURA 3: panel de potencia durante la réplica, con la caída a cero y la anotación de la alerta.]**

## 5. Panel "Energía del día" frente a `dwh.fact_energia_dia`

Para cada opción de la variable `$dia`, el valor que calcula el panel (consulta del panel ejecutada
por Grafana como `grafana_reader`) junto al valor de la tabla de hechos leído directamente:

```text
$dia (texto)  $dia (valor)   Panel stat (kWh)  fact_energia_dia (kWh)  % válidos  SLA  ¿Iguales?
2026-10-10    20261010                25.5603                 25.5603      95.49   Sí  sí
2026-10-09    20261009                24.9322                 24.9322      98.61   Sí  sí
2026-10-08    20261008                27.4413                 27.4413        100   Sí  sí
2026-10-07    20261007                27.1486                 27.1486      97.57   Sí  sí
2026-10-06    20261006                26.3838                 26.3838      96.88   Sí  sí
2026-10-05    20261005                27.4233                 27.4233      97.92   Sí  sí

Todos los días coinciden: sí (6 días)
```

## 6. Exportación del tablero (ida y vuelta)

```text
$ python scripts/export_grafana.py
grafana/dashboards/solarbi-operacion.json: unchanged (uid solarbi-operacion)
$ python scripts/export_grafana.py --check
grafana/dashboards/solarbi-operacion.json: matches Grafana
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

### Regla de alerta

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

## 8. Power BI

**[PENDIENTE: pasos en Power BI Desktop según docs/powerbi.md]**

**[CAPTURA 4: vista Modelo con las tres relaciones y la tabla `_Medidas`.]**

**[CAPTURA 5: página con la tarjeta "Energía total (kWh)", el gráfico de líneas "Energía diaria
(kWh)" por fecha y la tarjeta "% datos válidos".]**

**[CAPTURA 6: comprobación cruzada: tarjeta de energía filtrada al 2026-10-06 (26,384 kWh) junto
al panel de Grafana con `$dia = 2026-10-06`.]**

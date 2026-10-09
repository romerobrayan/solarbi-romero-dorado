# Fixtures de prueba

`telemetria_anomalias.csv` tiene una anomalía conocida por fila; `tests/test_etl_integration.py`
verifica los conteos exactos. Fechas en 2030 para no mezclarse con los datos del simulador.

| Fila | Contenido | Resultado esperado |
|---|---|---|
| 1, 2, 7 | Lecturas normales | Válidas |
| 3 | `p_ac_kw = -1` | `range_p_ac_kw` (reject) |
| 4 | Potencia vacía | `missing_p_ac_kw` (reject) |
| 5 | Irradiancia vacía | Válida, `missing_irradiancia` (flag) |
| 6 | Temperatura 95 °C | Válida, `range_temp_modulo` (flag) |
| 8 | Copia exacta de la fila 7 | `duplicate_key` (dedupe) |
| 9 | 10:35:30 (30 s fuera del intervalo) | Válida, ajustada a 10:35, `snapped_to_grid` (flag) |
| 10 | 10:42:30 (150 s fuera) | `off_grid` (reject) |
| 11 | Dispositivo `99` | `unknown_device` (reject) |
| 12 | Potencia `abc` | `invalid_format` (reject) |
| 13 | 7 kW (> 1,1 × 5 kWp) | `range_p_ac_kw` (reject) |
| 14 | Irradiancia 1600 W/m² | Válida, `range_irradiancia` (flag) |
| 15 | `ts` vacío | `missing_key` (reject) |
| 16 | 23:55 hora local del 15 | Válida; cuenta en el día local 2030-01-15, no en el día UTC 16 |
| 17 | 00:00 del 16 | Válida |
| 18 | Hora 25:00 | `invalid_format` (reject) |

Totales: 18 leídas = 9 válidas + 8 rechazadas distintas + 1 deduplicada; 4 válidas marcadas.

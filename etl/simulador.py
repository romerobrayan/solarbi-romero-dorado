# Professor's base simulator (assignment "SolarBI Pascual", Part B, Step 1), kept verbatim.
# Writes data/bronze/telemetria.csv (relative to the current directory): 3 days of 5-minute
# telemetry for one 5 kWp inverter with injected anomalies. Run it from the repository root:
#     python etl/simulador.py
# Extensions (seed, faults, more days or devices) live in etl/simulador_fallas.py.
import csv, math, os, random
from datetime import datetime, timedelta

inicio = datetime(2026, 10, 5, 0, 0)
os.makedirs("data/bronze", exist_ok=True)

with open("data/bronze/telemetria.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["ts", "dispositivo_id", "p_ac_kw", "irradiancia_wm2", "temp_modulo_c"])
    for i in range(3 * 288):                          # 3 dias, cada 5 min
        ts = inicio + timedelta(minutes=5 * i)
        sol = max(0.0, math.sin(math.pi * (ts.hour + ts.minute / 60 - 6) / 12))
        irr = round(1000 * sol * random.uniform(0.7, 1.0), 1)
        p_ac = round(5.0 * irr / 1000 * random.uniform(0.80, 0.90), 3)
        fila = [ts.isoformat(sep=" "), 1, p_ac, irr, round(22 + 30 * sol, 1)]
        r = random.random()
        if r < 0.02:
            fila[2] = -1                              # anomalia: potencia negativa
        elif r < 0.04:
            fila[3] = ""                              # anomalia: dato faltante
        w.writerow(fila)
        if r > 0.98:
            w.writerow(fila)                          # anomalia: fila duplicada

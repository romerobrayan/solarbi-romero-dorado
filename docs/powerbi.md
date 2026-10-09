# Power BI: proyecto `powerbi/SolarBI.pbip`

Power BI Desktop es una aplicación gráfica, así que el trabajo se reparte:

1. **Tú** conectas Power BI Desktop a PostgreSQL, importas las tablas de Gold y guardas el
   proyecto como `.pbip` (pasos 1 a 5).
2. **El script** `scripts/powerbi_model.py --apply` agrega al modelo (en TMDL, texto) las
   relaciones, marca la tabla de fechas y crea la tabla `_Medidas` con las medidas DAX (paso 6).
3. **Tú** reabres el proyecto y armas la página con los tres visuales del Paso 4 (pasos 7 a 9).

Modo de conexión: **Importar** (ver [ADR 0008](adr/0008-dashboards-grafana-import-powerbi.md)).
Usuario: `powerbi_reader`, de solo lectura; nunca el dueño de la base. Los nombres de menús pueden
variar un poco según la versión de Power BI Desktop.

## Antes de empezar

- La base está arriba: `docker compose up -d` y `python scripts/check_env.py` dice OK.
- Hay datos en Gold: `python scripts/conteos.py` muestra filas en `dwh.fact_energia_dia`.
- Tienes a mano la contraseña `POWERBI_READER_PASSWORD` de tu archivo `.env`.

## 1. Activar el formato de proyecto (solo la primera vez)

**Archivo → Opciones y configuración → Opciones → GLOBAL → Características en versión
preliminar**, y marca, si aparecen:

- **Opción para guardar el proyecto de Power BI (.pbip)**
- **Almacenar el modelo semántico con el formato TMDL**

Acepta y reinicia Power BI Desktop. En versiones recientes estas opciones ya no están en
versión preliminar (vienen activas) y no aparecen en esa lista: en ese caso no hay nada que hacer.
El formato TMDL es necesario: el script edita archivos `.tmdl`, no `model.bim`.

## 2. Conectar a PostgreSQL

1. **Inicio → Obtener datos → Más…**
2. **Base de datos → Base de datos PostgreSQL → Conectar.**
3. En el cuadro de diálogo:
   - **Servidor:** `127.0.0.1:5433`
   - **Base de datos:** `solarbi`
   - **Modo Conectividad de datos:** **Importar**
   - **Aceptar.**
4. Credenciales: a la izquierda elige **Base de datos** (no Windows).
   - **Nombre de usuario:** `powerbi_reader`
   - **Contraseña:** el valor de `POWERBI_READER_PASSWORD` en `.env`
   - **Seleccionar el nivel al que se aplica esta configuración:** `127.0.0.1:5433`
   - **Conectar.**
5. Si aparece un aviso de que **no se puede usar una conexión cifrada** y pregunta si deseas
   conectarte sin cifrar, elige **Aceptar**. Es esperado: el PostgreSQL de Docker no tiene SSL y
   la conexión va a `127.0.0.1`, es decir, el tráfico no sale de tu equipo. En un servidor real se
   configuraría SSL.

## 3. Elegir las tablas

En el **Navegador**, marca exactamente estas cuatro (del esquema `dwh`, la capa Gold):

- `dwh.fact_energia_dia`
- `dwh.dim_fecha`
- `dwh.dim_dispositivo`
- `dwh.dim_sitio`

No importes `silver`, `dq` ni `bronze`: Power BI es el consumidor de negocio y trabaja sobre Gold.

## 4. Nombres limpios de tabla

Haz clic en **Transformar datos** (no en Cargar). En el Editor de Power Query, panel
**Consultas** de la izquierda, cambia el nombre de cada consulta quitando el prefijo `dwh `
(clic derecho → **Cambiar nombre**):

| Nombre que pone Power BI | Nombre final |
|---|---|
| `dwh fact_energia_dia` | `fact_energia_dia` |
| `dwh dim_fecha` | `dim_fecha` |
| `dwh dim_dispositivo` | `dim_dispositivo` |
| `dwh dim_sitio` | `dim_sitio` |

Luego **Inicio → Cerrar y aplicar**. (Si te saltas este paso el script igual funciona, pero las
fórmulas DAX quedan con nombres como `'dwh fact_energia_dia'[energia_kwh]`.)

Si Power BI crea relaciones automáticamente al cargar, déjalas: el script no las duplica.

## 5. Guardar como proyecto

1. **Archivo → Guardar como → Examinar este dispositivo.**
2. Carpeta: `powerbi\` dentro del repositorio (`...\solarbi-romero-dorado\powerbi`).
3. **Tipo:** **Archivos de proyecto de Power BI (\*.pbip)**. **Nombre:** `SolarBI`. **Guardar.**
4. **Cierra Power BI Desktop.** El script edita los archivos del proyecto y no deben estar abiertos.

Debe quedar:

```text
powerbi/
├── SolarBI.pbip
├── SolarBI.Report/
└── SolarBI.SemanticModel/
    └── definition/        (tables/*.tmdl, relationships.tmdl, model.tmdl, ...)
```

## 6. Agregar relaciones, tabla de fechas y medidas (script)

Con Power BI Desktop **cerrado**, desde la raíz del repositorio:

```powershell
python scripts/powerbi_model.py --apply
```

El script:

- crea las relaciones `fact_energia_dia[fecha_key] → dim_fecha[fecha_key]`,
  `fact_energia_dia[dispositivo_key] → dim_dispositivo[dispositivo_key]` y
  `dim_dispositivo[sitio_key] → dim_sitio[sitio_key]` (muchos a uno, una dirección);
- marca `dim_fecha` como tabla de fechas, con `fecha` como columna de fecha;
- crea la tabla `_Medidas` con las medidas de la tabla siguiente.

Se puede ejecutar varias veces: si todo ya está, dice que no hay nada que cambiar. Con
`python scripts/powerbi_model.py --print` se ve el TMDL que agrega.

| Medida | DAX | Formato | Carpeta |
|---|---|---|---|
| `Energía total (kWh)` | `SUM ( fact_energia_dia[energia_kwh] )` | `#,0.000` | Energía |
| `Energía diaria (kWh)` | `[Energía total (kWh)]` | `#,0.000` | Energía |
| `% datos válidos` | `DIVIDE ( SUM ( fact_energia_dia[lecturas_validas] ), SUM ( fact_energia_dia[lecturas_esperadas] ) )` | `0.00%` | Calidad |
| `Días que cumplen SLA` | `CALCULATE ( COUNTROWS ( fact_energia_dia ), fact_energia_dia[cumple_sla] = TRUE () )` | `#,0` | Calidad |
| `Potencia máxima (kW)` | `MAX ( fact_energia_dia[p_max_kw] )` | `#,0.000` | Energía |
| `Yield (kWh/kWp)` | `DIVIDE ( [Energía total (kWh)], SUMX ( SUMMARIZE ( fact_energia_dia, dim_dispositivo[dispositivo_key], dim_dispositivo[nominal_kwp] ), dim_dispositivo[nominal_kwp] ) )` | `#,0.00` | Energía |

**Por qué `% datos válidos` es un cociente de sumas.** Promediar los porcentajes diarios da el
mismo peso a un día con 10 lecturas esperadas que a uno con 288, y sesga el indicador. Sumar
primero las lecturas válidas y las esperadas, y dividir después, da el porcentaje real del
periodo; para un solo día coincide con `pct_datos_validos` de Gold y con el panel de Grafana.

## 7. Reabrir y revisar el modelo

1. Abre `powerbi\SolarBI.pbip`.
2. Vista **Modelo** (icono de diagrama, a la izquierda): deben verse las tres relaciones y la
   tabla `_Medidas`.
3. **Inicio → Actualizar** para traer los datos de Gold (la primera vez en otro equipo pide las
   credenciales del paso 2).

Si Power BI muestra un error al abrir, copia el mensaje completo y compártelo para corregir el
TMDL.

## 8. Página del Paso 4

En la vista **Informe**, con el panel **Datos** a la derecha:

| Visual | Dónde | Campo |
|---|---|---|
| **Tarjeta** | Campos | `_Medidas` → `Energía total (kWh)` |
| **Gráfico de líneas** | Eje X | `dim_fecha` → `fecha` (en la flecha del campo elige `fecha`, no `Jerarquía de fechas`) |
| | Eje Y | `_Medidas` → `Energía diaria (kWh)` |
| **Tarjeta** | Campos | `_Medidas` → `% datos válidos` |

Opcional: una segmentación con `dim_fecha[fecha]` y otra con `dim_dispositivo[nombre]`.

Para que la tarjeta coincida con el reporte del ETL de la Fase 2 (3 días, 80,956 kWh), agrega un
filtro de página: **Filtros → Filtros en esta página →** arrastra `dim_fecha[fecha]` y elige del
5 al 7 de octubre de 2026. Sin filtro se ven todos los días cargados (incluidos los del archivo
con fallas y la réplica en vivo).

**Comprobación cruzada con Grafana:** pon una segmentación en un solo día (por ejemplo
2026-10-06): la tarjeta de energía debe mostrar el mismo valor que el panel "Energía del día
seleccionado" de Grafana con `$dia = 2026-10-06` (26,384 kWh).

## 9. Guardar y versionar

**Ctrl+S**. Solo se versiona el texto del proyecto; `.pbi/localSettings.json` y `.pbi/cache.abf`
(la caché con los datos) están en `.gitignore`, igual que cualquier `.pbix`. Toma las capturas
para el PDF y avisa para hacer el commit.

"""Semantic model of the Power BI project as code: relationships, date table, measures.

Workflow (docs/powerbi.md): Brayan connects Power BI Desktop to PostgreSQL, imports
the four dwh tables and saves powerbi/SolarBI.pbip (TMDL format). With Desktop
CLOSED, this script then edits the TMDL files to:

- add the star-schema relationships (fact -> dim_fecha, fact -> dim_dispositivo,
  dim_dispositivo -> dim_sitio), skipping any Power BI already auto-detected, and
  force all three to many-to-one with single-direction filtering;
- mark dim_fecha as the date table (dataCategory: Time, fecha as key);
- create the _Medidas table with the DAX measures, format strings and folders.

It finds the real table names (Power BI may call them "dwh fact_energia_dia") and
is idempotent: running it again leaves the files unchanged.

Usage, from the repo root:
    python scripts/powerbi_model.py --print            # show the generated TMDL
    python scripts/powerbi_model.py --apply            # edit powerbi/SolarBI.SemanticModel
"""

from __future__ import annotations

import argparse
import re
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

from etl.config import PROJECT_ROOT

SEMANTIC_MODEL = PROJECT_ROOT / "powerbi" / "SolarBI.SemanticModel"
LOGICAL_TABLES = ("fact_energia_dia", "dim_fecha", "dim_dispositivo", "dim_sitio")
MEASURES_TABLE = "_Medidas"
DATE_COLUMN = "fecha"
_NAMESPACE = uuid.UUID("6f1c5c4e-2f0e-4b1e-9a57-50a4b1c0de01")  # stable lineage tags


@dataclass(frozen=True)
class Measure:
    name: str
    expression: str  # DAX; {fact} / {dispositivo} are replaced by the real table names
    format_string: str
    folder: str
    description: str


MEASURES = (
    Measure(
        "Energía total (kWh)",
        "SUM ( {fact}[energia_kwh] )",
        "#,0.000",
        "Energía",
        "Energía de los días y dispositivos del contexto (suma de dwh.fact_energia_dia).",
    ),
    Measure(
        "Energía diaria (kWh)",
        "[Energía total (kWh)]",
        "#,0.000",
        "Energía",
        "Misma base que la energía total; se usa en el gráfico de líneas por dim_fecha[fecha].",
    ),
    Measure(
        "% datos válidos",
        "// Ratio of sums, never the average of the daily percentages: a day with few\n"
        "// expected readings must not weigh as much as a full day. It matches\n"
        "// dwh.fact_energia_dia.pct_datos_validos for a single day.\n"
        "DIVIDE (\n"
        "    SUM ( {fact}[lecturas_validas] ),\n"
        "    SUM ( {fact}[lecturas_esperadas] )\n"
        ")",
        "0.00%",
        "Calidad",
        "Lecturas válidas únicas / lecturas esperadas (cociente de sumas, no promedio de "
        "porcentajes diarios).",
    ),
    Measure(
        "Días que cumplen SLA",
        "CALCULATE ( COUNTROWS ( {fact} ), {fact}[cumple_sla] = TRUE () )",
        "#,0",
        "Calidad",
        "Filas día-dispositivo con pct_datos_validos >= 95 % (SLA del contrato de datos).",
    ),
    Measure(
        "Potencia máxima (kW)",
        "MAX ( {fact}[p_max_kw] )",
        "#,0.000",
        "Energía",
        "Mayor potencia AC de 5 minutos registrada en el contexto.",
    ),
    Measure(
        "Yield (kWh/kWp)",
        "// Energy per installed kWp of the devices that produced in the context.\n"
        "DIVIDE (\n"
        "    [Energía total (kWh)],\n"
        "    SUMX (\n"
        "        SUMMARIZE (\n"
        "            {fact},\n"
        "            {dispositivo}[dispositivo_key],\n"
        "            {dispositivo}[nominal_kwp]\n"
        "        ),\n"
        "        {dispositivo}[nominal_kwp]\n"
        "    )\n"
        ")",
        "#,0.00",
        "Energía",
        "Rendimiento específico: energía / potencia nominal instalada (dim_dispositivo).",
    ),
)

# (from table, from column) -> (to table, to column); many-to-one, single direction.
RELATIONSHIPS = (
    ("fact_energia_dia", "fecha_key", "dim_fecha", "fecha_key"),
    ("fact_energia_dia", "dispositivo_key", "dim_dispositivo", "dispositivo_key"),
    ("dim_dispositivo", "sitio_key", "dim_sitio", "sitio_key"),
)


class ModelError(RuntimeError):
    """The saved project is missing or not in the expected TMDL layout."""


# --- naming ----------------------------------------------------------------------------


def tmdl_name(name: str) -> str:
    """TMDL object names with spaces or symbols are quoted with single quotes."""
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        return name
    return "'" + name.replace("'", "''") + "'"


def dax_table(name: str) -> str:
    return tmdl_name(name)


def lineage(*parts: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, "/".join(parts)))


# --- rendering -------------------------------------------------------------------------


def render_measures_table(tables: dict[str, str]) -> str:
    fact, dispositivo = dax_table(tables["fact_energia_dia"]), dax_table(tables["dim_dispositivo"])
    lines = [f"table {MEASURES_TABLE}", f"\tlineageTag: {lineage('table', MEASURES_TABLE)}", ""]
    for measure in MEASURES:
        dax = measure.expression.replace("{fact}", fact).replace("{dispositivo}", dispositivo)
        lines.append(f"\t/// {measure.description}")
        if "\n" in dax:
            lines.append(f"\tmeasure {tmdl_name(measure.name)} =")
            lines += [f"\t\t\t{line}" for line in dax.split("\n")]
        else:
            lines.append(f"\tmeasure {tmdl_name(measure.name)} = {dax}")
        lines += [
            f"\t\tformatString: {measure.format_string}",
            f"\t\tdisplayFolder: {measure.folder}",
            f"\t\tlineageTag: {lineage('measure', measure.name)}",
            "",
        ]
    lines += [
        "\tcolumn Columna",
        "\t\tdataType: string",
        "\t\tisHidden",
        f"\t\tlineageTag: {lineage('column', MEASURES_TABLE, 'Columna')}",
        "\t\tsummarizeBy: none",
        "\t\tsourceColumn: Columna",
        "",
        "\t\tannotation SummarizationSetBy = Automatic",
        "",
        f"\tpartition {MEASURES_TABLE} = m",
        "\t\tmode: import",
        "\t\tsource = #table(type table [Columna = text], {})",
        "",
        "\tannotation PBI_ResultType = Table",
        "",
    ]
    return "\n".join(lines)


def render_relationship(tables: dict[str, str], rel: tuple[str, str, str, str]) -> str:
    from_table, from_column, to_table, to_column = rel
    return "\n".join(
        [
            f"relationship {lineage('relationship', *rel)}",
            f"\tfromColumn: {tmdl_name(tables[from_table])}.{from_column}",
            f"\ttoColumn: {tmdl_name(tables[to_table])}.{to_column}",
            "",
        ]
    )


# --- applying to a saved project -------------------------------------------------------------


def definition_dir(model_dir: Path) -> Path:
    if (model_dir / "model.bim").exists():
        raise ModelError(
            f"{model_dir} is saved as model.bim (TMSL). Enable 'Store semantic model using TMDL "
            "format' in Power BI Desktop (docs/powerbi.md, step 2) and save the project again."
        )
    definition = model_dir / "definition"
    if not (definition / "tables").is_dir():
        raise ModelError(
            f"{definition} not found: save the project from Power BI Desktop first "
            "(docs/powerbi.md)."
        )
    return definition


def table_name(path: Path) -> str:
    """Name declared on the first line of a table file ("table 'dwh x'" -> "dwh x")."""
    first = path.read_text(encoding="utf-8").lstrip("﻿").splitlines()[0]
    match = re.match(r"table\s+(?:'((?:[^']|'')+)'|(\S+))", first)
    return (match[1] or match[2]).replace("''", "'") if match else ""


def find_tables(definition: Path) -> dict[str, str]:
    """Map logical table names to the names Power BI gave them."""
    declared = {table_name(path): path for path in (definition / "tables").glob("*.tmdl")}
    found = {}
    for logical in LOGICAL_TABLES:
        candidates = [n for n in declared if n == logical or re.fullmatch(rf"dwh[ ._]{logical}", n)]
        if len(candidates) != 1:
            raise ModelError(
                f"table {logical!r} not found among {sorted(declared)}: import dwh.{logical} "
                "(docs/powerbi.md, step 4)."
            )
        found[logical] = candidates[0]
    return found


def mark_date_table(text: str) -> str:
    """dataCategory: Time on the table and isKey on the fecha column (idempotent)."""
    lines = text.splitlines()
    if not any(line.strip() == "dataCategory: Time" for line in lines):
        lines.insert(1, "\tdataCategory: Time")
    headers = [f"\tcolumn {DATE_COLUMN}", f"\tcolumn '{DATE_COLUMN}'"]
    start = next((i for i, line in enumerate(lines) if line.rstrip() in headers), None)
    if start is None:
        raise ModelError(f"column {DATE_COLUMN!r} not found in the date table")
    end = next(
        (
            i
            for i in range(start + 1, len(lines))
            if lines[i].startswith("\t") and lines[i][1] != "\t"
        ),
        len(lines),
    )
    block = lines[start:end]
    if not any(line.strip() == "isKey" for line in block):
        data_type = next(i for i, line in enumerate(block) if line.strip().startswith("dataType:"))
        lines.insert(start + data_type + 1, "\t\tisKey")
    return "\n".join(lines) + "\n"


def _column_ref(line: str) -> tuple[str, str]:
    key, value = line.strip().split(":", 1)
    return key, value.strip().replace("'", "").replace('"', "")


def existing_relationship_pairs(text: str) -> set[tuple[str, str]]:
    pairs, current = set(), {}
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("relationship "):
            current = {}
        elif stripped.startswith(("fromColumn:", "toColumn:")):
            key, value = _column_ref(stripped)
            current[key] = value
            if len(current) == 2:
                pairs.add((current["fromColumn"], current["toColumn"]))
    return pairs


# Properties Desktop writes when it auto-detects a star relationship as one-to-one and
# bidirectional (with a single device every key looks unique). Without them TMDL falls
# back to its defaults: many-to-one, single-direction filtering.
NON_STAR_PROPERTIES = frozenset({"fromCardinality: one", "crossFilteringBehavior: bothDirections"})


def enforce_many_to_one(text: str, star_pairs: set[tuple[str, str]]) -> str:
    """Drop the one-to-one / bidirectional properties from the star relationships only."""
    blocks: list[list[str]] = [[]]
    for line in text.splitlines(keepends=True):
        if line.startswith("relationship "):
            blocks.append([])
        blocks[-1].append(line)
    out = []
    for block in blocks:
        refs = dict(
            _column_ref(line)
            for line in block
            if line.strip().startswith(("fromColumn:", "toColumn:"))
        )
        if (refs.get("fromColumn"), refs.get("toColumn")) in star_pairs:
            block = [line for line in block if line.strip() not in NON_STAR_PROPERTIES]
        out += block
    return "".join(out)


def apply(model_dir: Path = SEMANTIC_MODEL) -> list[str]:
    """Edit the saved TMDL project; returns a description of each change."""
    definition = definition_dir(model_dir)
    tables = find_tables(definition)
    changes = []

    measures_path = definition / "tables" / f"{MEASURES_TABLE}.tmdl"
    rendered = render_measures_table(tables)
    if not measures_path.exists() or measures_path.read_text(encoding="utf-8") != rendered:
        measures_path.write_text(rendered, encoding="utf-8", newline="\n")
        changes.append(f"{MEASURES_TABLE}: {len(MEASURES)} measures written")

    model_path = definition / "model.tmdl"
    if model_path.exists():
        model_text = model_path.read_text(encoding="utf-8")
        ref = f"ref table {MEASURES_TABLE}"
        if "ref table " in model_text and ref not in model_text:
            model_path.write_text(
                model_text.rstrip("\n") + f"\n{ref}\n", encoding="utf-8", newline="\n"
            )
            changes.append(f"model.tmdl: {ref}")

    relationships_path = definition / "relationships.tmdl"
    current = relationships_path.read_text(encoding="utf-8") if relationships_path.exists() else ""
    present = existing_relationship_pairs(current)
    star_pairs = {(f"{tables[r[0]]}.{r[1]}", f"{tables[r[2]]}.{r[3]}") for r in RELATIONSHIPS}
    text = enforce_many_to_one(current, star_pairs)
    if text != current:
        changes.append("relationships: star relationships set to many-to-one, single direction")
    additions = []
    for rel in RELATIONSHIPS:
        pair = (f"{tables[rel[0]]}.{rel[1]}", f"{tables[rel[2]]}.{rel[3]}")
        if pair not in present:
            additions.append(render_relationship(tables, rel))
            changes.append(f"relationship {pair[0]} -> {pair[1]}")
    if additions:
        text = (text.rstrip("\n") + "\n\n" if text.strip() else "") + "\n".join(additions)
    if text != current:
        relationships_path.write_text(text, encoding="utf-8", newline="\n")

    date_path = next(
        p for p in (definition / "tables").glob("*.tmdl") if table_name(p) == tables["dim_fecha"]
    )
    date_text = date_path.read_text(encoding="utf-8")
    marked = mark_date_table(date_text)
    if marked != date_text:
        date_path.write_text(marked, encoding="utf-8", newline="\n")
        changes.append(f"{tables['dim_fecha']}: marked as date table ({DATE_COLUMN} is the key)")
    return changes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Power BI semantic model as code (TMDL).")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--print", action="store_true", help="print the TMDL to be added")
    group.add_argument("--apply", action="store_true", help="edit the saved project")
    args = parser.parse_args(argv)
    if args.print:
        names = {name: name for name in LOGICAL_TABLES}
        print(render_measures_table(names))
        for rel in RELATIONSHIPS:
            print(render_relationship(names, rel))
        return 0
    try:
        changes = apply()
    except ModelError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    for change in changes or ["nothing to change: the model already has everything"]:
        print(f"  {change}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""The Power BI model script on a minimal TMDL project like the one Desktop saves."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.powerbi_model import (
    MEASURES,
    ModelError,
    apply,
    mark_date_table,
    render_measures_table,
)

FACT = """table 'dwh fact_energia_dia'
\tlineageTag: 11111111-0000-0000-0000-000000000001

\tcolumn fecha_key
\t\tdataType: int64
\t\tsummarizeBy: none
\t\tsourceColumn: fecha_key

\tcolumn energia_kwh
\t\tdataType: decimal
\t\tsourceColumn: energia_kwh

\tpartition 'dwh fact_energia_dia' = m
\t\tmode: import
\t\tsource =
\t\t\t\tlet
\t\t\t\t    Source = PostgreSQL.Database("127.0.0.1:5433", "solarbi"),
\t\t\t\t    t = Source{[Schema="dwh",Item="fact_energia_dia"]}[Data]
\t\t\t\tin
\t\t\t\t    t
"""

DIM_FECHA = """table 'dwh dim_fecha'
\tlineageTag: 11111111-0000-0000-0000-000000000002

\tcolumn fecha_key
\t\tdataType: int64
\t\tsourceColumn: fecha_key

\tcolumn fecha
\t\tdataType: dateTime
\t\tformatString: Long Date
\t\tsourceColumn: fecha

\tpartition 'dwh dim_fecha' = m
\t\tmode: import
\t\tsource = PostgreSQL.Database("127.0.0.1:5433", "solarbi")
"""


def _project(tmp_path: Path, with_auto_relationship: bool = False) -> Path:
    model = tmp_path / "SolarBI.SemanticModel"
    tables = model / "definition" / "tables"
    tables.mkdir(parents=True)
    (tables / "dwh fact_energia_dia.tmdl").write_text(FACT, encoding="utf-8")
    (tables / "dwh dim_fecha.tmdl").write_text(DIM_FECHA, encoding="utf-8")
    for name in ("dim_dispositivo", "dim_sitio"):
        (tables / f"dwh {name}.tmdl").write_text(
            f"table 'dwh {name}'\n\tlineageTag: x\n", encoding="utf-8"
        )
    (model / "definition" / "model.tmdl").write_text(
        "model Model\n\tculture: es-CO\n\nref table 'dwh fact_energia_dia'\n", encoding="utf-8"
    )
    if with_auto_relationship:
        (model / "definition" / "relationships.tmdl").write_text(
            "relationship abc\n\tfromColumn: 'dwh fact_energia_dia'.fecha_key\n"
            "\ttoColumn: 'dwh dim_fecha'.fecha_key\n",
            encoding="utf-8",
        )
    return model


def test_measures_use_real_table_names_and_ratio_of_sums() -> None:
    names = {
        "fact_energia_dia": "dwh fact_energia_dia",
        "dim_fecha": "dwh dim_fecha",
        "dim_dispositivo": "dwh dim_dispositivo",
        "dim_sitio": "dwh dim_sitio",
    }
    tmdl = render_measures_table(names)
    assert "measure 'Energía total (kWh)' = SUM ( 'dwh fact_energia_dia'[energia_kwh] )" in tmdl
    assert "\t\t\t    SUM ( 'dwh fact_energia_dia'[lecturas_validas] )," in tmdl
    assert "AVERAGE" not in tmdl  # % valid is never an average of daily percentages
    assert tmdl.count("\tmeasure ") == len(MEASURES) == 6
    assert "\t\tformatString: 0.00%" in tmdl and "\t\tdisplayFolder: Calidad" in tmdl


def test_apply_adds_relationships_date_table_and_measures(tmp_path: Path) -> None:
    model = _project(tmp_path)
    changes = apply(model)

    definition = model / "definition"
    assert (definition / "tables" / "_Medidas.tmdl").exists()
    relationships = (definition / "relationships.tmdl").read_text(encoding="utf-8")
    assert relationships.count("relationship ") == 3
    assert "fromColumn: 'dwh dim_dispositivo'.sitio_key" in relationships
    date_table = (definition / "tables" / "dwh dim_fecha.tmdl").read_text(encoding="utf-8")
    assert date_table.splitlines()[1] == "\tdataCategory: Time"
    assert "\tcolumn fecha\n\t\tdataType: dateTime\n\t\tisKey\n" in date_table
    assert "ref table _Medidas" in (definition / "model.tmdl").read_text(encoding="utf-8")
    assert len(changes) == 6


def test_apply_is_idempotent_and_keeps_auto_detected_relationships(tmp_path: Path) -> None:
    model = _project(tmp_path, with_auto_relationship=True)
    apply(model)
    snapshot = {p: p.read_text(encoding="utf-8") for p in model.rglob("*.tmdl")}

    assert apply(model) == []
    assert {p: p.read_text(encoding="utf-8") for p in model.rglob("*.tmdl")} == snapshot
    relationships = snapshot[model / "definition" / "relationships.tmdl"]
    assert relationships.count("toColumn: 'dwh dim_fecha'.fecha_key") == 1


def test_apply_turns_auto_detected_one_to_one_into_many_to_one(tmp_path: Path) -> None:
    model = _project(tmp_path)
    relationships_path = model / "definition" / "relationships.tmdl"
    local_date = (
        "relationship ld\n\tjoinOnDateBehavior: datePartOnly\n"
        "\tcrossFilteringBehavior: bothDirections\n"
        "\tfromColumn: 'dwh dim_fecha'.fecha\n\ttoColumn: LocalDateTable_x.Date\n\n"
    )
    relationships_path.write_text(
        local_date + "relationship AutoDetected_abc\n\tfromCardinality: one\n"
        "\tcrossFilteringBehavior: bothDirections\n"
        "\tfromColumn: 'dwh fact_energia_dia'.fecha_key\n\ttoColumn: 'dwh dim_fecha'.fecha_key\n",
        encoding="utf-8",
    )

    changes = apply(model)
    text = relationships_path.read_text(encoding="utf-8")

    assert "relationships: star relationships set to many-to-one, single direction" in changes
    assert "fromCardinality" not in text
    assert text.count("crossFilteringBehavior: bothDirections") == 1  # LocalDateTable untouched
    assert text.startswith(local_date)
    assert "relationship AutoDetected_abc\n\tfromColumn: 'dwh fact_energia_dia'.fecha_key\n" in text
    assert text.count("toColumn: 'dwh dim_fecha'.fecha_key") == 1
    assert apply(model) == []
    assert relationships_path.read_text(encoding="utf-8") == text


def test_mark_date_table_requires_the_fecha_column() -> None:
    with pytest.raises(ModelError, match="fecha"):
        mark_date_table("table dim_fecha\n\tcolumn otra\n\t\tdataType: string\n")


def test_refuses_a_project_saved_as_model_bim(tmp_path: Path) -> None:
    model = tmp_path / "SolarBI.SemanticModel"
    model.mkdir()
    (model / "model.bim").write_text("{}", encoding="utf-8")
    with pytest.raises(ModelError, match="TMDL"):
        apply(model)


def test_refuses_when_the_project_was_not_saved_yet(tmp_path: Path) -> None:
    with pytest.raises(ModelError, match="save the project"):
        apply(tmp_path / "SolarBI.SemanticModel")

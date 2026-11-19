"""Карта кромления: публичный API и PDF."""

import json
from pathlib import Path

import pytest
from kitchen import api
from kitchen.cli import app
from kitchen.errors import KitchenError
from pypdf import PdfReader
from typer.testing import CliRunner

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ORDER = "2026-047-petrov"


def test_edges_order_pdf_and_totals(home):
    api.brief_save(ORDER, FIXTURES / "petrov_brief.json", home)
    api.import_project(ORDER, FIXTURES / "petrov_project.json", home=home)
    result = api.save(calc := api.calculation(order="petrov", home=home), calc.edging())[0]
    parts = api.calculation(order="petrov", home=home).parts()
    assert result.edges == parts.edges
    assert result.source.version == 1
    assert result.output.source_version == 1
    assert result.output.kind == "edging"
    pdf = PdfReader(result.output.file)
    text = "\n".join(page.extract_text() for page in pdf.pages)
    assert "Н1-бок-Л" in text
    assert "L1" in text and "W2" in text
    assert "edge_08_white" in text
    assert "МОК-ДАННЫЕ — НЕ ДЛЯ КЛИЕНТА" in text
    assert "версия проекта 1" in text
    assert [(o.kind, o.source_version) for o in api.list_outputs("petrov", home)] == [("edging", 1)]


def test_edges_cli_order_and_file_contract(home, base_door_project):
    runner = CliRunner()
    draft = runner.invoke(app, ["edges", "--project", str(base_door_project), "--json"])
    assert draft.exit_code == 0, draft.output
    assert json.loads(draft.output)["output"] is None
    api.brief_save(ORDER, FIXTURES / "petrov_brief.json", home)
    api.import_project(ORDER, FIXTURES / "petrov_project.json", home=home)
    result = runner.invoke(app, ["edges", "petrov", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == json.loads(
        api.save(calc := api.calculation(order="petrov", home=home), calc.edging())[0].model_dump_json()
    )
    text = runner.invoke(app, ["edges", "petrov"])
    assert text.exit_code == 0, text.output
    assert "edging-map.pdf" in text.output and "МОК-ДАННЫЕ" in text.output


def test_every_edged_part_and_totals_on_multipage_pdf(home):
    api.brief_save(ORDER, FIXTURES / "petrov_brief.json", home)
    api.import_project(ORDER, FIXTURES / "petrov_project.json", home=home)
    result = api.save(calc := api.calculation(order=ORDER, home=home), calc.edging())[0]
    pages = [page.extract_text() for page in PdfReader(result.output.file).pages]
    assert len(pages) > 1
    assert all("МОК-ДАННЫЕ — НЕ ДЛЯ КЛИЕНТА" in text and "версия проекта 1" in text for text in pages)
    text = "\n".join(pages)
    for part in result.parts:
        assert part.id in text
        assert f"{part.length}×{part.width}" in text
    for edge_id, total in result.edges.items():
        assert edge_id in text
        assert f"{total.clean_m:.3f}" in text
        assert f"{total.with_allowance_m:.3f}" in text
        assert f"{total.purchase_m:.1f}" in text  # ticket 58: «к закупке» с одним знаком


def test_empty_edging_and_non_mock_pdf(home, base_door_project):
    for workshop_file in (home / "workshop").glob("*.yaml"):
        workshop_file.write_text(workshop_file.read_text().replace("mock: true", "mock: false"))
    project = json.loads(base_door_project.read_text())
    module = project["walls"][0]["rows"][0]["modules"][0]
    project["walls"][0]["length"] = 600
    module["template"] = "dishwasher_panel"
    module.pop("shelves")
    base_door_project.write_text(json.dumps(project, ensure_ascii=False))
    api.order_new(ORDER, home=home)
    api.import_project(ORDER, base_door_project, home=home)
    result = api.save(calc := api.calculation(order=ORDER, home=home), calc.edging())[0]
    assert result.parts == [] and result.edges == {}
    pages = [page.extract_text() for page in PdfReader(result.output.file).pages]
    assert len(pages) == 1 and "Нет деталей с кромкой" in pages[0]
    assert "МОК-ДАННЫЕ" not in pages[0]


def test_edging_meters_match_the_base_door_golden(home, base_door_project):
    result = api.calculation(project=base_door_project, home=home).edging()
    assert result.edges["edge_08_white"].model_dump() == {
        "clean_m": 3.71,
        "with_allowance_m": 3.89,
        "purchase_m": 4.3,
    }
    assert len(result.parts) == 6


def test_new_version_replaces_only_edging_output(home):
    api.brief_save(ORDER, FIXTURES / "petrov_brief.json", home)
    api.import_project(ORDER, FIXTURES / "petrov_project.json", home=home)
    api.save(calc := api.calculation(order=ORDER, home=home), calc.cut())[0]
    api.save(calc := api.calculation(order=ORDER, home=home), calc.edging())[0]
    project = json.loads((FIXTURES / "petrov_project.json").read_text())
    project["ceiling_height"] = 2650
    path = home / "project-v2.json"
    path.write_text(json.dumps(project, ensure_ascii=False))
    api.import_project(ORDER, path, home=home)
    result = api.save(calc := api.calculation(order=ORDER, home=home), calc.edging())[0]
    assert sorted((o.kind, o.source_version) for o in api.list_outputs(ORDER, home)) == [
        ("cutting", 1),
        ("edging", 2),
    ]
    assert all("версия проекта 2" in p.extract_text() for p in PdfReader(result.output.file).pages)


def test_long_edge_id_does_not_overflow_the_footer(home, base_door_project):
    materials = home / "workshop" / "materials.yaml"
    materials.write_text(materials.read_text().replace("edge_08_white", "x" * 1130))
    project = json.loads(base_door_project.read_text())
    project["walls"][0]["length"] = 600
    base_door_project.write_text(json.dumps(project, ensure_ascii=False))
    api.order_new(ORDER, home=home)
    api.import_project(ORDER, base_door_project, home=home)
    with pytest.raises(KitchenError, match="слишком велика"):
        api.save(calc := api.calculation(order=ORDER, home=home), calc.edging())[0]
    assert not api.list_outputs(ORDER, home)


def test_summary_page_uses_summary_headers(home, base_door_project):
    project = json.loads(base_door_project.read_text())
    row = project["walls"][0]["rows"][0]
    template = row["modules"][0]
    row["modules"] = [{**template, "label": f"Н{i}"} for i in range(1, 8)]
    project["walls"][0]["length"] = 4200
    base_door_project.write_text(json.dumps(project, ensure_ascii=False))
    api.order_new(ORDER, home=home)
    api.import_project(ORDER, base_door_project, home=home)
    result = api.save(calc := api.calculation(order=ORDER, home=home), calc.edging())[0]
    assert len(result.parts) == 42
    pages = [page.extract_text() for page in PdfReader(result.output.file).pages]
    summary_pages = [p for p in pages if f"{result.edges['edge_08_white'].clean_m:.3f}" in p]
    assert len(summary_pages) == 1
    assert "Итог по кромке" in summary_pages[0] and "Чистая, м" in summary_pages[0]

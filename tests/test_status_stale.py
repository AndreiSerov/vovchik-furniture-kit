"""Тикет 24: «Устарело» в status.md — выводы, посчитанные из версий старше текущей."""

import json
import re
import typing
from pathlib import Path

import pytest
import typer
from kitchen import api
from kitchen.cli import app
from kitchen.result import MOCK_BANNER
from typer.testing import CliRunner

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ORDER = "2026-047-petrov"
runner = CliRunner()


def _petrov_v1(home):
    api.brief_save(ORDER, FIXTURES / "petrov_brief.json", home)
    api.import_project(ORDER, FIXTURES / "petrov_project.json", home=home)


def _import_v2(home):
    project = json.loads((FIXTURES / "petrov_project.json").read_text(encoding="utf-8"))
    project["ceiling_height"] = 2650
    path = home / "project-v2.json"
    path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    return api.import_project(ORDER, path, home=home)


def _stale_section(text: str) -> str:
    match = re.search(r"^## Устарело\n(.*?)(?=^## |\Z)", text, re.DOTALL | re.MULTILINE)
    assert match, "в status.md нет раздела «Устарело»"
    return match.group(1)


def _stale_kinds(home) -> set[str]:
    return {s.kind for s in api.status(ORDER, home).stale}


ALL_KINDS = {"cutting", "edging", "purchase_md", "purchase_json"}


def test_v2_marks_every_v1_output_with_version_and_command(home):
    _petrov_v1(home)
    api.production_and_save(ORDER, home)
    _import_v2(home)
    r = api.status(ORDER, home)
    section = _stale_section(r.text)
    assert {s.kind for s in r.stale} == ALL_KINDS
    assert all(s.source_version == 1 for s in r.stale)
    for stale in r.stale:
        line = next(ln for ln in section.splitlines() if stale.path in ln)
        assert "версия 1" in line and f"`{stale.command}`" in line
    commands = {s.kind: s.command for s in r.stale}
    assert commands["cutting"] == f"kitchen cut {ORDER}"
    assert commands["edging"] == f"kitchen edges {ORDER}"
    assert commands["purchase_md"] == commands["purchase_json"] == f"kitchen purchase {ORDER}"


def test_recompute_removes_only_that_output(home):
    _petrov_v1(home)
    api.production_and_save(ORDER, home)
    _import_v2(home)
    api.cut_and_save(ORDER, home)
    assert _stale_kinds(home) == ALL_KINDS - {"cutting"}
    assert "cutting-map.pdf" not in _stale_section(api.status(ORDER, home).text)


def test_production_and_estimate_leave_section_empty(home):
    _petrov_v1(home)
    api.production_and_save(ORDER, home)
    api.estimate_and_save(ORDER, home)
    _import_v2(home)
    assert len(api.status(ORDER, home).stale) == 6
    api.production_and_save(ORDER, home)
    api.estimate_and_save(ORDER, home)
    r = api.status(ORDER, home)
    assert r.stale == []
    assert _stale_section(r.text).strip() == "Пока ничего"


def test_no_outputs_section_follows_empty_rule(home):
    _petrov_v1(home)
    r = api.status(ORDER, home)
    assert r.stale == [] and _stale_section(r.text).strip() == "Пока ничего"


def test_no_project_section_follows_empty_rule(home):
    api.order_new(ORDER, client="Петров П. П.", home=home)
    assert _stale_section(api.status(ORDER, home).text).strip() == "Пока ничего"


def test_fresh_outputs_are_not_marked(home):
    _petrov_v1(home)
    api.production_and_save(ORDER, home)
    assert api.status(ORDER, home).stale == []


def test_reimport_without_changes_marks_nothing(home):
    _petrov_v1(home)
    api.production_and_save(ORDER, home)
    again = api.import_project(ORDER, FIXTURES / "petrov_project.json", home=home)
    assert not again.changed and again.version == 1
    assert api.status(ORDER, home).stale == []


def test_json_stale_matches_text(home):
    _petrov_v1(home)
    api.production_and_save(ORDER, home)
    _import_v2(home)
    result = runner.invoke(app, ["status", "petrov", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    section = _stale_section(data["text"])
    assert len(data["stale"]) == len(ALL_KINDS)
    lines = [ln for ln in section.splitlines() if ln.startswith("- ")]
    assert len(lines) == len(data["stale"])
    for item in data["stale"]:
        assert sum(item["path"] in ln and f"`{item['command']}`" in ln for ln in lines) == 1
        assert f"версия {item['source_version']}" in next(ln for ln in lines if item["path"] in ln)


def test_status_md_file_has_section_and_mock_banner(home):
    _petrov_v1(home)
    api.production_and_save(ORDER, home)
    _import_v2(home)
    r = api.status(ORDER, home)
    written = Path(r.path).read_text(encoding="utf-8")
    assert "## Устарело" in written and "cutting-map.pdf" in written
    lines = written.strip().splitlines()
    assert lines[0] == MOCK_BANNER and lines[-1] == MOCK_BANNER


def test_every_output_kind_has_an_existing_cli_command():
    """Ограждение: новый вид вывода без команды пересчёта роняет тест."""
    kinds = set(typing.get_args(api.OutputKind))
    assert set(api.OUTPUT_RECOMPUTE) == kinds, "у каждого OutputKind должна быть запись в реестре пересчёта"
    cli_commands = set(typer.main.get_command(app).commands)
    for kind, recompute in api.OUTPUT_RECOMPUTE.items():
        assert recompute.command in cli_commands, f"{kind}: команды `kitchen {recompute.command}` нет в CLI"


@pytest.mark.parametrize("kind", sorted(typing.get_args(api.OutputKind)))
def test_registry_entry_has_russian_label(kind):
    assert api.OUTPUT_RECOMPUTE[kind].label

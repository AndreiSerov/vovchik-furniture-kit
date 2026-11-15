"""Контракт `kitchen estimate --economy`: вариант «экономия» в тексте, `--json`, estimate.md и estimate.json.

Петров считается с экономией один раз на модуль тестов (два вызова CLI: `--json` и текст): расчёт тяжёлый."""

import copy
import json
import shutil
import sqlite3

import pytest
from kitchen import api
from kitchen.cli import app
from kitchen.result import MOCK_BANNER
from typer.testing import CliRunner

from tests.conftest import BASE_DOOR_PROJECT, REPO

FIXTURES = REPO / "tests" / "fixtures"
ORDER = "2026-047-petrov"
runner = CliRunner()


def snapshot(home):
    conn = sqlite3.connect(home / "orders" / "kitchen.db")
    try:
        return conn.execute("SELECT * FROM project_versions ORDER BY id").fetchall()
    finally:
        conn.close()


def read_outputs(home):
    out = home / "orders" / ORDER / "out"
    return {name: (out / name).read_text(encoding="utf-8") for name in ("estimate.md", "estimate.json")}


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    home = tmp_path_factory.mktemp("economy_cli")
    shutil.copytree(REPO / "workshop", home / "workshop")
    patch = pytest.MonkeyPatch()
    patch.setenv("KITCHEN_HOME", str(home))
    try:
        api.brief_save(ORDER, FIXTURES / "petrov_brief.json", home)
        api.import_project(ORDER, FIXTURES / "petrov_project.json", home=home)
        before = snapshot(home)
        as_json = runner.invoke(app, ["estimate", "petrov", "--economy", "--json"])
        written_json = read_outputs(home)["estimate.json"]
        as_text = runner.invoke(app, ["estimate", "petrov", "--economy"])
        with_flag = read_outputs(home)
        after = snapshot(home)
        plain = runner.invoke(app, ["estimate", "petrov"])
        yield {
            "json": as_json,
            "written_json": written_json,
            "text": as_text,
            "with_flag": with_flag,
            "plain": plain,
            "without_flag": read_outputs(home),
            "versions": (before, after),
        }
    finally:
        patch.undo()


def test_economy_json_has_both_prices_and_rows_with_modules_and_client_text(run):
    assert run["json"].exit_code == 0, run["json"].output
    data = json.loads(run["json"].output)
    economy = data["economy"]
    assert economy["price_as_is"] == data["price"] and economy["price"] < economy["price_as_is"]
    assert economy["saving"] == economy["price_as_is"] - economy["price"]
    rows = {row["id"]: row for row in economy["replacements"]}
    assert rows["lift_to_door"]["modules"] == ["В1", "В5"]
    assert rows["carousel_to_shelf"]["client_text"].startswith("В угловом шкафу")
    assert all(row["saving"] > 0 and row["price"] for row in rows.values())
    assert economy["not_offered"] == [] and economy["problems"] == []
    assert data["warning"] == MOCK_BANNER
    assert data == json.loads(run["written_json"])  # та же модель, что пишет API


def test_economy_text_and_files_show_section_only_with_flag(run):
    text = run["text"]
    assert text.exit_code == 0, text.output
    assert "Экономия" in text.output and "Карусель → полка" in text.output and "В1, В5" in text.output
    lines = text.output.strip().splitlines()
    assert lines[0] == MOCK_BANNER and lines[-1] == MOCK_BANNER
    markdown = run["with_flag"]["estimate.md"]
    assert "## Экономия" in markdown and "Цена как в проекте" in markdown and "Цена «экономии»" in markdown
    assert "Сумма разниц" in markdown and "В угловом шкафу" in markdown
    assert "economy" in json.loads(run["with_flag"]["estimate.json"])
    assert run["plain"].exit_code == 0 and "Экономия" not in run["plain"].output
    assert "Экономия" not in run["without_flag"]["estimate.md"]
    assert "economy" not in json.loads(run["without_flag"]["estimate.json"])


def test_economy_leaves_project_versions_table_untouched(run):
    before, after = run["versions"]
    assert len(before) == 1 and after == before


def test_economy_with_project_file_writes_nothing(home):
    project = copy.deepcopy(BASE_DOOR_PROJECT)
    project["walls"][0]["length"] = 3600
    project["walls"][0]["rows"][0]["modules"] = [{"label": "Н1", "template": "base_drawers", "width": 600}]
    path = home / "p.json"
    path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    result = runner.invoke(app, ["estimate", "--project", str(path), "--economy", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["outputs"] == [] and data["economy"]["price"] < data["economy"]["price_as_is"]
    assert not (home / "orders").exists()

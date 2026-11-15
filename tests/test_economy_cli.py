"""Контракт `kitchen estimate --economy`: вариант «экономия» в тексте, `--json`, estimate.md и estimate.json."""

import json

from kitchen import api
from kitchen.cli import app
from kitchen.result import MOCK_BANNER
from typer.testing import CliRunner

from tests.conftest import REPO

FIXTURES = REPO / "tests" / "fixtures"
ORDER = "2026-047-petrov"
runner = CliRunner()


def petrov(home):
    api.brief_save(ORDER, FIXTURES / "petrov_brief.json", home)
    api.import_project(ORDER, FIXTURES / "petrov_project.json", home=home)


def test_economy_json_has_both_prices_and_rows_with_modules_and_client_text(home):
    petrov(home)
    result = runner.invoke(app, ["estimate", "petrov", "--economy", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    economy = data["economy"]
    assert economy["price_as_is"] == data["price"] and economy["price"] < economy["price_as_is"]
    assert economy["saving"] == economy["price_as_is"] - economy["price"]
    rows = {row["id"]: row for row in economy["replacements"]}
    assert rows["lift_to_door"]["modules"] == ["В1", "В5"]
    assert rows["carousel_to_shelf"]["client_text"].startswith("В угловом шкафу")
    assert all(row["saving"] > 0 and row["price"] for row in rows.values())
    assert economy["not_offered"] == [] and economy["problems"] == []
    assert data["warning"] == MOCK_BANNER
    written = home / "orders" / ORDER / "out" / "estimate.json"
    assert data == json.loads(written.read_text(encoding="utf-8"))  # та же модель, что пишет API


def test_economy_text_and_files_show_section_only_with_flag(home):
    petrov(home)
    text = runner.invoke(app, ["estimate", "petrov", "--economy"])
    assert text.exit_code == 0, text.output
    assert "Экономия" in text.output and "Карусель → полка" in text.output and "В1, В5" in text.output
    lines = text.output.strip().splitlines()
    assert lines[0] == MOCK_BANNER and lines[-1] == MOCK_BANNER
    out = home / "orders" / ORDER / "out"
    markdown = (out / "estimate.md").read_text(encoding="utf-8")
    assert "## Экономия" in markdown and "Цена как в проекте" in markdown and "Цена «экономии»" in markdown
    assert "Сумма разниц" in markdown and "В угловом шкафу" in markdown
    assert "economy" in json.loads((out / "estimate.json").read_text(encoding="utf-8"))
    plain = runner.invoke(app, ["estimate", "petrov"])
    assert plain.exit_code == 0 and "Экономия" not in plain.output
    assert "Экономия" not in (out / "estimate.md").read_text(encoding="utf-8")
    assert "economy" not in json.loads((out / "estimate.json").read_text(encoding="utf-8"))


def test_economy_with_project_file_writes_nothing(home):
    path = str(FIXTURES / "petrov_project.json")
    result = runner.invoke(app, ["estimate", "--project", path, "--economy", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["outputs"] == [] and data["economy"]["price"] < data["economy"]["price_as_is"]
    assert not (home / "orders").exists()

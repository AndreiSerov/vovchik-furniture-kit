"""Тикет 67: слои движка (ADR 0002) закреплены контрактами import-linter в `pyproject.toml`."""

import ast
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / "engine" / "kitchen"


def _contracts() -> dict[str, dict]:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return {c["name"]: c for c in config["tool"]["importlinter"]["contracts"]}


def _modules() -> set[str]:
    return {f"kitchen.{p.stem}" for p in ENGINE.glob("*.py") if p.stem != "__init__"}


def test_contract_names() -> None:
    assert set(_contracts()) == {
        "Слои ADR 0002",
        "CLI ходит только в api",
        "Писатели независимы",
        "Расчёты не ходят в БД",
    }


def test_layers_cover_every_module() -> None:
    placed = {m.strip() for line in _contracts()["Слои ADR 0002"]["layers"] for m in line.split(":")}
    assert placed == _modules() - {"kitchen.skill_lint"}


def test_cli_forbidden_list_covers_all_other_modules() -> None:
    forbidden = set(_contracts()["CLI ходит только в api"]["forbidden_modules"])
    allowed = {"cli", "api", "errors", "result", "skill_lint"}
    assert forbidden == _modules() - {f"kitchen.{name}" for name in allowed}
    assert "kitchen.calculation" in forbidden  # тикет 69: CLI ходит в api, а не в расчёт за ним


def _kitchen(*names: str) -> set[str]:
    return {f"kitchen.{name}" for name in names}


def test_contracts_match_the_spec() -> None:
    """Состав каждого слоя, писателей и источников контракта 4 — ровно по спеке: перенос модуля между слоями
    или выпавший писатель линтер не заметит, а этот тест — да. `kitchen.calculation` — в слое `api` (тикет 69)."""
    contracts = _contracts()
    layers = [{m.strip() for m in line.split(":")} for line in contracts["Слои ADR 0002"]["layers"]]
    writers = (
        "cutting_pdf",
        "edging_pdf",
        "drilling_pdf",
        "assembly_pdf",
        "trip_pdf",
        "quote_pdf",
        "views_pdf",
    )
    assert layers == [
        _kitchen("cli"),
        _kitchen("api", "calculation"),
        _kitchen(*writers, "views_html", "scene3d", "pdf"),
        _kitchen(
            "assembly",
            "brief",
            "checks",
            "common",
            "cutting",
            "drilling",
            "economy",
            "estimate",
            "geometry",
            "models",
            "parts",
            "placement",
            "project",
            "purchase",
            "quote",
            "store",
            "tall",
            "trip",
            "views",
            "workshop",
        ),
        _kitchen("errors", "format", "formulas", "metrics", "ral", "result"),
    ]
    assert set(contracts["Писатели независимы"]["modules"]) == _kitchen(*writers, "views_html")
    db = contracts["Расчёты не ходят в БД"]
    assert set(db["source_modules"]) == _kitchen(
        "parts",
        "cutting",
        "drilling",
        "assembly",
        "trip",
        "estimate",
        "economy",
        "quote",
        "purchase",
        "checks",
        "placement",
        "views",
        "tall",
        "common",
    )
    assert set(db["forbidden_modules"]) == _kitchen("store", "api", "calculation")
    assert contracts["CLI ходит только в api"]["allow_indirect_imports"] is True
    assert db["allow_indirect_imports"] is True


def test_check_script_runs_lint_imports() -> None:
    assert "lint-imports" in (ROOT / "scripts" / "check").read_text(encoding="utf-8")


def test_linter_bites_on_cli_importing_a_calculation(tmp_path: Path) -> None:
    package = tmp_path / "kitchen"
    shutil.copytree(ENGINE, package, ignore=shutil.ignore_patterns("__pycache__"))
    with (package / "cli.py").open("a", encoding="utf-8") as cli:
        cli.write("\nimport kitchen.parts\n")
    shutil.copy(ROOT / "pyproject.toml", tmp_path / "pyproject.toml")
    env = {**os.environ, "PYTHONPATH": str(tmp_path)}
    lint = Path(sys.executable).parent / "lint-imports"
    result = subprocess.run(
        [str(lint), "--no-cache"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=25,
        check=False,
    )
    out = result.stdout + result.stderr
    assert result.returncode != 0, out
    # имя контракта печатается и при KEPT: ждём именно BROKEN у CLI и KEPT у остальных
    assert re.search(r"CLI ходит только в api\s+BROKEN", out), out
    for other in ("Слои ADR 0002", "Писатели независимы", "Расчёты не ходят в БД"):
        assert re.search(rf"{other}\s+KEPT", out), out


def test_adr_has_layers_section() -> None:
    text = (ROOT / "docs" / "adr" / "0002-engine-layers.md").read_text(encoding="utf-8")
    assert "## Слои движка" in text
    assert "дополнено 2026-11-19" in text
    for name in ("kitchen.metrics", "kitchen.cli", "kitchen.api"):
        assert name in text
    assert re.search(r"pyproject\.toml", text)


def test_text_line_and_metrics_live_where_the_spec_says() -> None:
    def defined(name: str, module: str) -> bool:
        source = (ENGINE / f"{module}.py").read_text(encoding="utf-8")
        return re.search(rf"^(class|def) {name}\b|^{name} =", source, re.MULTILINE) is not None

    assert defined("TextLine", "pdf")
    assert not defined("TextLine", "drilling_pdf")
    for name in ("FONT", "FONT_BOLD", "register_fonts", "page_size", "page_area", "Area", "wrap"):
        assert defined(name, "metrics"), name
        assert not defined(name, "pdf"), name


def test_calculation_does_not_import_api() -> None:
    """Тикет 69: `api` берёт расчёт из `kitchen.calculation`, обратного импорта нет — цикла нет."""
    tree = ast.parse((ENGINE / "calculation.py").read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "kitchen":
            found += [f"from kitchen import {a.name}" for a in node.names if a.name == "api"]
        elif isinstance(node, ast.ImportFrom) and node.module == "kitchen.api":
            found.append("from kitchen.api import …")
        elif isinstance(node, ast.Import):
            found += [f"import {a.name}" for a in node.names if a.name == "kitchen.api"]
    assert not found, found


def test_agents_names_calculation_where_things_live() -> None:
    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    table = text.split("## Где что лежит", 1)[1].split("\n## ", 1)[0]
    assert "kitchen.calculation" in table
    assert "kitchen.api" in table

"""Тикет 67: слои движка (ADR 0002) закреплены контрактами import-linter в `pyproject.toml`."""

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
    assert result.returncode != 0
    assert "CLI ходит только в api" in result.stdout + result.stderr


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

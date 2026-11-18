import json
import os
import shutil
from pathlib import Path

# typer включает цвет rich в GitHub Actions (`GITHUB_ACTIONS`, `FORCE_COLOR`): справка CLI в тестах
# приходит с ANSI-кодами и не совпадает с локальной. Флаг читается при импорте typer — ставим до него.
os.environ["_TYPER_FORCE_DISABLE_TERMINAL"] = "1"

import pytest

REPO = Path(__file__).resolve().parent.parent
GOLDEN_DIR = Path(__file__).resolve().parent / "golden"


def load_golden(name: str) -> dict:
    """Эталон `tests/golden/<name>.json` без служебных ключей (`_проверить_мастеру`, `_выкладка`)."""
    raw = json.loads((GOLDEN_DIR / f"{name}.json").read_text(encoding="utf-8"))
    return {k: v for k, v in raw.items() if not k.startswith("_")}


# Поля результата `parts`, которых нет в эталонах (golden сравнивает детали, фурнитуру, кромку, фасады).
FIELDS_NOT_IN_GOLDEN = {
    "mock",
    "mock_files",
    "warning",
    "source",
    "findings",
    "common",
    "facade_area_by_color",
    "gaps",  # пустые места рядов (тикет 32): эталоны их не знают
}
# Поля детали, которых нет в эталонах: отверстия присадки (тикет 19) дополняют деталь, эталоны их не знают.
PART_FIELDS_NOT_IN_GOLDEN = {"holes", "base_edges"}


def golden_dump(result) -> dict:
    """Результат `parts` в виде эталона: без служебных полей и без отверстий присадки у деталей."""
    data = result.model_dump(exclude=FIELDS_NOT_IN_GOLDEN, exclude_none=True)
    for module in data["modules"]:
        module.pop("hardware", None)  # фурнитура по модулям (тикет 22): эталоны знают только итог проекта
        for part in module["parts"]:
            for key in PART_FIELDS_NOT_IN_GOLDEN:
                part.pop(key, None)
    return data


# Минимальный проект (тикет 05): один нижний модуль base_door W600.
BASE_DOOR_PROJECT = {
    "order": "2026-047-petrov",
    "ceiling_height": 2700,
    "walls": [
        {
            "label": "A",
            "length": 2800,
            "rows": [
                {
                    "kind": "lower",
                    "modules": [{"label": "Н1", "template": "base_door", "width": 600, "shelves": 1}],
                }
            ],
        }
    ],
    "materials": {
        "body": "ldsp_16_white",
        "facades_lower": "mdf_enamel_matt",
        "facades_upper": "mdf_enamel_matt",
        "handles": "handle_profile",
    },
}


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Временный KITCHEN_HOME с копией workshop/."""
    shutil.copytree(REPO / "workshop", tmp_path / "workshop")
    monkeypatch.setenv("KITCHEN_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def base_door_project(home):
    p = home / "project.json"
    p.write_text(json.dumps(BASE_DOOR_PROJECT, ensure_ascii=False), encoding="utf-8")
    return p


@pytest.fixture
def repo() -> Path:
    return REPO

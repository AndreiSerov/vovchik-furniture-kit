"""Проверка брифа: подгружает `skills/kitchen-brief/scripts/check_brief.py` как модуль.

Скрипт остаётся самостоятельным (только стандартная библиотека, работает и из командной строки);
движок лишь вызывает его `check`, ничего в нём не меняя.
"""

import importlib.util
import json
import sys
from datetime import date
from functools import cache
from pathlib import Path
from types import ModuleType
from typing import Any

from kitchen.errors import OrderError

SKILL = Path(__file__).resolve().parents[2] / "skills" / "kitchen-brief"
_MODULE = "kitchen_check_brief"


@cache
def _checker() -> ModuleType:
    script = SKILL / "scripts" / "check_brief.py"
    if not script.is_file():
        raise OrderError(f"Не найден скрипт проверки брифа {script}. Запустите kitchen из репозитория.")
    spec = importlib.util.spec_from_file_location(_MODULE, script)
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE] = module  # нужно dataclass внутри скрипта
    spec.loader.exec_module(module)
    return module


def client_name(brief: dict[str, Any]) -> str:
    cb = _checker()
    value = cb.get(brief, "client.name")
    return "" if cb.is_empty(value) else str(value).strip()


def check(brief: dict[str, Any], today: date) -> dict[str, Any]:
    """Результат check_brief.py; бриф с `errors` или `unknown_fields` не принимается."""
    cb = _checker()
    schema = json.loads(cb.SCHEMA_PATH.read_text(encoding="utf-8"))
    try:
        result = cb.check(brief, cb.load_settings(cb.SETTINGS_PATH, today), schema)
    except (TypeError, ValueError, KeyError, AttributeError) as e:
        raise OrderError(
            f"Бриф не удалось разобрать: {e!r}. Проверьте структуру по brief_schema.json."
        ) from e
    problems = [f"Неизвестное поле брифа: {name} (опечатка?)." for name in result["unknown_fields"]]
    problems += [f"Ошибка в брифе: {err}" for err in result["errors"]]
    if problems:
        raise OrderError([*problems, "Бриф не сохранён."])
    return result

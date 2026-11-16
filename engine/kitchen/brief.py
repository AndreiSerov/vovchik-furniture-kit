"""Проверка брифа: подгружает `skills/kitchen-brief/scripts/check_brief.py` как модуль.

Скрипт остаётся самостоятельным (только стандартная библиотека, работает и из командной строки);
движок лишь вызывает его `check`, ничего в нём не меняя.
"""

import importlib.util
import json
import re
import sys
from dataclasses import dataclass, field
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


@cache
def _schema() -> dict[str, Any]:
    """Схема брифа читается один раз: шапка чек-листа спрашивает подписи по пять раз."""
    return json.loads(_checker().SCHEMA_PATH.read_text(encoding="utf-8"))


def client_name(brief: dict[str, Any]) -> str:
    found = value(brief, "client.name")
    return "" if found is None else str(found)


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


# --- техника из брифа ---------------------------------------------------------

_DIMENSIONS = re.compile(r"^\s*(\d+)\s*[×xхXХ*]\s*(\d+)\s*[×xхXХ*]\s*(\d+)\s*(?:мм)?\s*$")
# Вытяжка из объекта `appliances.hood` ставится в модуль только этих видов; остальные висят сами.
HOOD_IN_MODULE = ("built_in", "telescopic")


def parse_dimensions(text: Any) -> tuple[int, int, int] | None:
    """Строка «Ш×В×Г» старых брифов → (ширина, высота, глубина); не распознана — `None`."""
    match = _DIMENSIONS.match(str(text or ""))
    return tuple(int(g) for g in match.groups()) if match else None  # type: ignore[return-value]


@dataclass
class BriefAppliance:
    """Прибор из брифа. Размеры корпуса — справочные; ниша — размеры встраивания из инструкции."""

    type: str
    label: str  # для сообщений: «посудомойка 45 см Bosch SMV»
    built_in: bool
    model: str | None = None
    width: int | None = None
    height: int | None = None
    depth: int | None = None
    niche_width: int | None = None
    niche_height: int | None = None
    niche_depth: int | None = None
    warnings: list[str] = field(default_factory=list)


def _mm(value: Any) -> int | None:
    return (
        int(value) if isinstance(value, int | float) and not isinstance(value, bool) and value > 0 else None
    )


def _appliance(item: dict[str, Any], type_: str, label: str, built_in: bool) -> BriefAppliance:
    width, height, depth = (_mm(item.get(k)) for k in ("width_mm", "height_mm", "depth_mm"))
    warnings: list[str] = []
    text = item.get("dimensions")
    if text and None in (width, height, depth):
        parsed = parse_dimensions(text)
        if parsed is None:
            warnings.append(f"размеры «{text}» не в формате Ш×В×Г, не разобраны")
        else:
            width, height, depth = (
                given or new for given, new in zip((width, height, depth), parsed, strict=True)
            )
    niche = (_mm(item.get(k)) for k in ("niche_width_mm", "niche_height_mm", "niche_depth_mm"))
    model = item.get("model")
    return BriefAppliance(
        type_, label, built_in, str(model) if model else None, width, height, depth, *niche, warnings
    )


def appliances(brief: dict[str, Any]) -> list[BriefAppliance]:
    """Техника брифа: список `appliances.items` и встраиваемая вытяжка из `appliances.hood`."""
    cb = _checker()
    found = []
    for item in cb.appliances(brief):
        label = " ".join(x for x in (cb.appliance_name(item), item.get("model") or item.get("brand")) if x)
        found.append(_appliance(item, str(item.get("type") or ""), label, item.get("built_in") is True))
    fridge = cb.get(brief, "appliances.fridge")
    if isinstance(fridge, dict) and any(v not in (None, "", [], {}) for v in fridge.values()):
        label = " ".join(x for x in ("холодильник", fridge.get("model") or fridge.get("brand")) if x)
        found.append(_appliance(fridge, "fridge", label, fridge.get("built_in") is True))
    hood = cb.get(brief, "appliances.hood")
    if isinstance(hood, dict) and hood.get("kind") in HOOD_IN_MODULE:
        label = " ".join(x for x in ("вытяжка", hood.get("model")) if x)
        found.append(_appliance(hood, "hood", label, True))
    return found


# --- значения и подписи для документов ----------------------------------------


def value(brief: dict[str, Any], path: str) -> Any:
    """Значение поля брифа по пути «раздел.поле» (обёртка `{value, source}` снята, у строки обрезаны пробелы по
    краям); пусто — `None`."""
    cb = _checker()
    found = cb.get(brief, path)
    if cb.is_empty(found):
        return None
    return found.strip() if isinstance(found, str) else found


def value_label(path: str, raw: Any) -> str:
    """Подпись значения перечня из `value_labels` схемы брифа («floor_evenness», «even» → «ровный»);
    подписи нет — само значение."""
    schema = _schema()
    section, _, name = path.partition(".")
    labels = schema["sections"][section]["fields"][name].get("value_labels", {})
    return labels.get(raw, str(raw))

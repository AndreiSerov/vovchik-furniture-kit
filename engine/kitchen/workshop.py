"""Поиск мастерской и загрузка её файлов с русскими сообщениями об ошибках."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

import yaml
from pydantic import ValidationError

from kitchen.errors import WorkshopError
from kitchen.models import (
    Construction,
    Equipment,
    Hardware,
    Labor,
    Materials,
    Pricing,
    Workshop,
)

FILES: tuple[str, ...] = (
    "equipment.yaml",
    "construction.yaml",
    "materials.yaml",
    "hardware.yaml",
    "labor.yaml",
    "pricing.yaml",
)

NOT_FOUND = (
    "Мастерская не найдена: нет папки workshop/ с файлами. "
    "Задайте KITCHEN_HOME (папка, где лежит workshop/) или запустите команду внутри репозитория."
)


def resolve_home(start: Path | None = None) -> Path:
    env = os.environ.get("KITCHEN_HOME")
    if env:
        home = Path(env).expanduser()
        if not (home / "workshop").is_dir():
            raise WorkshopError(f"KITCHEN_HOME={env}: в этой папке нет workshop/.")
        return home
    here = (start or Path.cwd()).resolve()
    for d in (here, *here.parents):
        if (d / "workshop" / "construction.yaml").is_file():
            return d
    raise WorkshopError(NOT_FOUND)


def read_raw(home: Path) -> tuple[dict[str, dict], list[str]]:
    """Читает YAML-файлы как есть. Возвращает (данные по файлам, проблемы чтения)."""
    raw: dict[str, dict] = {}
    problems: list[str] = []
    for name in FILES:
        path = home / "workshop" / name
        if not path.is_file():
            problems.append(f"{name}: файл не найден в {path.parent}.")
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            mark = getattr(e, "problem_mark", None)
            where = f"строка {mark.line + 1}, столбец {mark.column + 1}" if mark else "место не определено"
            problems.append(f"{name}: {where}: ошибка синтаксиса YAML.")
            continue
        if not isinstance(data, dict):
            problems.append(f"{name}: ожидался набор «ключ: значение» в корне файла.")
            continue
        raw[name] = data
    return raw, problems


_EXPECT = {
    "missing": "обязательный ключ отсутствует",
    "extra_forbidden": "неизвестный ключ (опечатка?)",
    "int_parsing": "ожидалось число",
    "int_type": "ожидалось число",
    "int_from_float": "ожидалось целое число",
    "float_parsing": "ожидалось число",
    "float_type": "ожидалось число",
    "bool_parsing": "ожидалось true или false",
    "bool_type": "ожидалось true или false",
    "string_type": "ожидалась строка",
    "list_type": "ожидался список",
    "tuple_type": "ожидался список из нескольких значений",
    "too_short": "в списке не хватает значений",
    "too_long": "в списке лишние значения",
    "model_type": "ожидался раздел с вложенными ключами",
    "dict_type": "ожидался раздел с вложенными ключами",
}


def _path(loc: tuple) -> str:
    out = ""
    for part in loc:
        out += f"[{part}]" if isinstance(part, int) else (f".{part}" if out else str(part))
    return out


_TYPE_RU = {
    bool: "логическое значение",
    int: "целое число",
    float: "число",
    str: "строка",
    list: "список",
    dict: "раздел",
    type(None): "пусто",
}


def translate(name: str, err: ValidationError) -> list[str]:
    msgs = []
    for e in err.errors():
        path = _path(e["loc"])
        if e["type"] == "value_error":
            expect = str(e["ctx"]["error"])
        elif e["type"] == "missing" and isinstance(e["loc"][-1], int):
            expect = "в строке списка не хватает значения"
        else:
            expect = _EXPECT.get(e["type"]) or f"значение не подходит ({e['type']}: {e['msg']})"
        if e["type"] == "missing" and not isinstance(e["loc"][-1], int):
            msgs.append(f"{name}: ключ «{path}»: {expect}.")
        else:
            shown = f"«{e['input']}»"
            if e["type"] != "value_error" and type(e["input"]) in _TYPE_RU:
                shown += f" ({_TYPE_RU[type(e['input'])]})"
            msgs.append(f"{name}: ключ «{path}»: значение {shown} — {expect}.")
    return msgs


def _cross_refs(w: Workshop) -> list[str]:
    ids = {m.id for m in w.materials.sheets}
    probs = []
    for path, ref in (
        ("задняя_стенка.материал", w.construction.back_panel.material),
        ("ящик_tandembox.материал", w.construction.tandembox.material),
    ):
        if ref not in ids:
            probs.append(
                f"construction.yaml: ключ «{path}»: значение «{ref}» — нет такого id в листовых "
                f"материалах materials.yaml (есть: {', '.join(sorted(ids))})."
            )
    return probs


M = TypeVar("M", Equipment, Construction, Materials, Hardware, Labor, Pricing)


@dataclass
class Loaded:
    """Итог одного чтения мастерской: модель (если всё верно), проблемы, сырые данные, моки."""

    workshop: Workshop | None
    problems: list[str]
    raw: dict[str, dict]
    mock_files: list[str] = field(default_factory=list)


def load(home: Path) -> Loaded:
    raw, problems = read_raw(home)
    parsed: dict[str, bool | None] = {}  # файл -> mock (None: файл не прошёл проверку)

    def parse(name: str, cls: type[M]) -> M | None:
        if name not in raw:
            return None
        try:
            model = cls.model_validate(raw[name])
        except ValidationError as e:
            problems.extend(translate(name, e))
            parsed[name] = None
            return None
        parsed[name] = model.mock
        return model

    equipment = parse("equipment.yaml", Equipment)
    construction = parse("construction.yaml", Construction)
    materials = parse("materials.yaml", Materials)
    hardware = parse("hardware.yaml", Hardware)
    labor = parse("labor.yaml", Labor)
    pricing = parse("pricing.yaml", Pricing)

    # Файл, который не прочитался или не прошёл проверку, считаем моком, пока он явно не `mock: false`.
    mock_files = [
        n
        for n in FILES
        if parsed.get(n) is True or (parsed.get(n) is None and raw.get(n, {}).get("mock") is not False)
    ]

    workshop = None
    if not problems and equipment and construction and materials and hardware and labor and pricing:
        workshop = Workshop(
            equipment=equipment,
            construction=construction,
            materials=materials,
            hardware=hardware,
            labor=labor,
            pricing=pricing,
            mock_files=mock_files,
        )
        problems = _cross_refs(workshop)
        if problems:
            workshop = None
    return Loaded(workshop, problems, raw, mock_files)


def load_workshop(home: Path | None = None) -> Workshop:
    loaded = load(home or resolve_home())
    if loaded.workshop is None:
        raise WorkshopError(loaded.problems)
    return loaded.workshop

"""Поиск мастерской и загрузка её файлов с русскими сообщениями об ошибках."""

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError

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

FILES: dict[str, type[BaseModel]] = {
    "equipment.yaml": Equipment,
    "construction.yaml": Construction,
    "materials.yaml": Materials,
    "hardware.yaml": Hardware,
    "labor.yaml": Labor,
    "pricing.yaml": Pricing,
}

NOT_FOUND = (
    "Мастерская не найдена: нет папки workshop/. "
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
        if (d / "workshop").is_dir():
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
            problems.append(f"{name}: не читается как YAML: {e}".replace("\n", " "))
            continue
        if not isinstance(data, dict):
            problems.append(f"{name}: ожидался набор «ключ: значение» в корне файла.")
            continue
        raw[name] = data
    return raw, problems


def mock_files_of(raw: dict[str, dict]) -> list[str]:
    return [n for n in FILES if raw.get(n, {}).get("mock") is True]


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


def translate(name: str, err: ValidationError) -> list[str]:
    msgs = []
    for e in err.errors():
        path = _path(e["loc"])
        if e["type"] == "value_error":
            expect = str(e["ctx"]["error"])
        else:
            expect = _EXPECT.get(e["type"], e["msg"])
        if e["type"] == "missing":
            msgs.append(f"{name}: ключ «{path}»: {expect}.")
        else:
            msgs.append(f"{name}: ключ «{path}»: значение «{e['input']}» — {expect}.")
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


def load_workshop(home: Path | None = None) -> Workshop:
    home = home or resolve_home()
    raw, problems = read_raw(home)
    models: dict[str, BaseModel] = {}
    for name, cls in FILES.items():
        if name not in raw:
            continue
        try:
            models[name] = cls.model_validate(raw[name])
        except ValidationError as e:
            problems += translate(name, e)
    if not problems:
        w = Workshop(
            equipment=models["equipment.yaml"],  # type: ignore[arg-type]
            construction=models["construction.yaml"],  # type: ignore[arg-type]
            materials=models["materials.yaml"],  # type: ignore[arg-type]
            hardware=models["hardware.yaml"],  # type: ignore[arg-type]
            labor=models["labor.yaml"],  # type: ignore[arg-type]
            pricing=models["pricing.yaml"],  # type: ignore[arg-type]
            mock_files=mock_files_of(raw),
        )
        problems = _cross_refs(w)
        if not problems:
            return w
    raise WorkshopError(problems)

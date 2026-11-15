"""Поиск мастерской и загрузка её файлов с русскими сообщениями об ошибках."""

import os
from collections.abc import Hashable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

import yaml
from pydantic import ValidationError

from kitchen.errors import WorkshopError
from kitchen.models import (
    APPLIANCE_KINDS,
    Appliances,
    Construction,
    Economy,
    Equipment,
    Hardware,
    Labor,
    Master,
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
    "appliances.yaml",
    "economy.yaml",
    "master.yaml",
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


class DuplicateKeyError(yaml.YAMLError):
    """Один и тот же ключ дважды в одном разделе YAML: вторая запись молча затёрла бы первую."""

    def __init__(self, key: object, mark: yaml.Mark | None):
        super().__init__(f"повторный ключ {key}")
        self.key = key
        self.problem_mark = mark


class _StrictLoader(yaml.SafeLoader):
    def compose_mapping_node(self, anchor):
        """Повторы ключей ищем при композиции: каждый словарь проверяется сам, по своим ключам, один раз.
        Подмешанный `<<` словарь — отдельный узел, его повторы ловятся при его композиции; законное
        перекрытие подмешанного ключа своим повтором не считается."""
        node = super().compose_mapping_node(anchor)
        seen = set()
        for key_node, _ in node.value:
            if key_node.tag == "tag:yaml.org,2002:merge" or not isinstance(key_node, yaml.ScalarNode):
                continue
            key = self.construct_object(key_node, deep=True)
            if not isinstance(key, Hashable):
                continue
            if key in seen:
                raise DuplicateKeyError(key, key_node.start_mark)
            seen.add(key)
        return node


def _syntax_problem(name: str, e: yaml.YAMLError) -> str:
    mark = getattr(e, "problem_mark", None)
    where = f"строка {mark.line + 1}, столбец {mark.column + 1}" if mark else "место не определено"
    if isinstance(e, DuplicateKeyError):
        return f"{name}: {where}: ключ «{e.key}» повторяется (вторая запись затёрла бы первую)."
    return f"{name}: {where}: ошибка синтаксиса YAML."


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
            data = yaml.load(path.read_text(encoding="utf-8-sig"), Loader=_StrictLoader)
        except UnicodeDecodeError:
            problems.append(f"{name}: файл не в кодировке UTF-8; сохраните его как UTF-8.")
            continue
        except yaml.YAMLError as e:
            problems.append(_syntax_problem(name, e))
            continue
        except RecursionError:
            problems.append(f"{name}: слишком глубокая вложенность разделов; упростите файл.")
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
    "string_too_short": "строка не должна быть пустой",
    "too_long": "в списке лишние значения",
    "finite_number": "ожидалось конечное число (не inf и не nan)",
    "model_type": "ожидался раздел с вложенными ключами",
    "dict_type": "ожидался раздел с вложенными ключами",
    "literal_error": "значение не из допустимых (см. подсказку в шапке файла)",
    "string_pattern_mismatch": "значение не того формата (см. подсказку в шапке файла)",
}


_BOUNDS = {
    "greater_than": ("больше", "gt"),
    "greater_than_equal": ("не меньше", "ge"),
    "less_than": ("меньше", "lt"),
    "less_than_equal": ("не больше", "le"),
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
        elif e["type"] in _BOUNDS:
            word, key = _BOUNDS[e["type"]]
            expect = f"значение должно быть {word} {e['ctx'][key]:g}"
        else:
            expect = _EXPECT.get(e["type"]) or f"значение не подходит ({e['type']}: {e['msg']})"
        if not e["loc"]:  # правило раздела целиком (повторный id и т. п.)
            msgs.append(f"{name}: {expect}.")
        elif e["type"] == "missing" and not isinstance(e["loc"][-1], int):
            msgs.append(f"{name}: ключ «{path}»: {expect}.")
        else:
            shown = f"«{e['input']}»"
            if e["type"] != "value_error" and type(e["input"]) in _TYPE_RU:
                shown += f" ({_TYPE_RU[type(e['input'])]})"
            msgs.append(f"{name}: ключ «{path}»: значение {shown} — {expect}.")
    return msgs


def _cross_refs(w: Workshop) -> list[str]:
    ids = {m.id for m in w.materials.sheets}
    edge_ids = {m.id for m in w.materials.edges}
    hw_ids = {i.id for i in w.hardware.items}
    probs = []
    kinds = APPLIANCE_KINDS
    probs += [
        f"construction.yaml: техника_в_столешницу: неизвестный вид «{kind}» (допустимы виды: {', '.join(sorted(kinds))})."
        for kind in w.construction.countertop_appliances
        if kind not in kinds
    ]
    for path, ref, known, where in (
        (
            "задняя_стенка.материал",
            w.construction.back_panel.material,
            ids,
            "листовых материалах materials.yaml",
        ),
        (
            "ящик_tandembox.материал",
            w.construction.tandembox.material,
            ids,
            "листовых материалах materials.yaml",
        ),
        ("угловые_модули.карусель", w.construction.corner_rules.carousel, hw_ids, "позициях hardware.yaml"),
        (
            "угловые_модули.материал_заглушки",
            w.construction.corner_rules.filler_material,
            ids,
            "листовых материалах materials.yaml",
        ),
        *(
            (f"верхние_модули.{key}", ref, hw_ids, "позициях hardware.yaml")
            for key, ref in (
                ("навес", w.construction.upper_rules.hanger),
                ("подъёмник", w.construction.upper_rules.lift),
                ("сушка", w.construction.upper_rules.dryer),
            )
        ),
        *(
            (
                f"правила_пенала.{key}",
                ref,
                {item.id for item in w.appliances.items if item.type == kind},
                f"технике типа {kind} в appliances.yaml",
            )
            for key, ref, kind in (
                ("духовка", w.construction.tall_rules.oven, "oven"),
                ("свч", w.construction.tall_rules.microwave, "microwave"),
            )
        ),
        (
            "верхние_модули.вытяжка",
            w.construction.upper_rules.hood,
            {item.id for item in w.appliances.items if item.type == "hood"},
            "вытяжках appliances.yaml",
        ),
        *(
            (f"общие_позиции.{key}", ref, hw_ids, "позициях hardware.yaml")
            for key, ref in (
                ("цоколь.позиция", w.construction.common.plinth.item),
                ("шина.позиция", w.construction.common.rail.item),
                ("led.профиль", w.construction.common.led.profile),
                ("led.блок_питания", w.construction.common.led.power_supply),
            )
        ),
        *(
            (f"фурнитура.{key}", ref, hw_ids, "позициях hardware.yaml")
            for key, ref in (
                ("петли", w.construction.furniture.hinges),
                ("ответные_планки", w.construction.furniture.plates),
                ("конфирматы", w.construction.furniture.confirmats),
                ("заглушки", w.construction.furniture.caps),
                ("полкодержатели", w.construction.furniture.shelf_pins),
                ("опоры", w.construction.furniture.legs),
                ("саморезы", w.construction.furniture.screws),
            )
        ),
    ):
        if ref not in known:
            probs.append(
                f"construction.yaml: ключ «{path}»: значение «{ref}» — нет такого id в {where} "
                f"(есть: {', '.join(sorted(known))})."
            )
    for material_id, edge_id in w.materials.edge_by_material.items():
        if material_id not in ids:
            probs.append(
                f"materials.yaml: ключ «кромка_к_материалу»: «{material_id}» — нет такого id в "
                f"листовых материалах (есть: {', '.join(sorted(ids))})."
            )
        if edge_id not in edge_ids:
            probs.append(
                f"materials.yaml: ключ «кромка_к_материалу»: «{material_id}: {edge_id}» — нет такой "
                f"кромки в кромке materials.yaml (есть: {', '.join(sorted(edge_ids))})."
            )
    probs += _economy_refs(w, hw_ids)
    return probs


def _economy_refs(w: Workshop, hw_ids: set[str]) -> list[str]:
    """Шаблоны и вставки правил `economy.yaml` существуют; шаблон замены стоит в том же ряду, что исходный."""
    from kitchen.parts import TEMPLATES  # локально: `parts` импортирует `project`, а тот — этот модуль

    probs = []
    known = ", ".join(sorted(TEMPLATES))
    for i, rule in enumerate(w.economy.rules):
        for j, case in enumerate(rule.cases):
            where = f"замены[{i}].случаи[{j}]"
            for key, template in (("где.шаблон", case.where.template), ("на.шаблон", case.change.template)):
                if template is not None and template not in TEMPLATES:
                    probs.append(
                        f"economy.yaml: ключ «{where}.{key}»: значение «{template}» — нет такого шаблона (есть: {known})."
                    )
            old, new = TEMPLATES.get(case.where.template), TEMPLATES.get(case.change.template or "")
            if old and new and old.row_kind != new.row_kind:
                probs.append(
                    f"economy.yaml: ключ «{where}.на.шаблон»: «{case.change.template}» стоит в другом ряду, "
                    f"чем «{case.where.template}» ({new.row_kind} вместо {old.row_kind})."
                )
            for key, insert in (
                ("где.вставка", case.where.insert),
                ("на.убрать_вставку", case.change.remove_insert),
            ):
                if insert is not None and insert not in hw_ids:
                    probs.append(
                        f"economy.yaml: ключ «{where}.{key}»: значение «{insert}» — нет такого id в позициях "
                        f"hardware.yaml (есть: {', '.join(sorted(hw_ids))})."
                    )
    return probs


M = TypeVar("M", Equipment, Construction, Materials, Hardware, Labor, Pricing, Appliances, Economy, Master)


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
    appliances = parse("appliances.yaml", Appliances)
    economy = parse("economy.yaml", Economy)
    master = parse("master.yaml", Master)

    # Файл, который не прочитался или не прошёл проверку, считаем моком, пока он явно не `mock: false`.
    mock_files = [
        n
        for n in FILES
        if parsed.get(n) is True or (parsed.get(n) is None and raw.get(n, {}).get("mock") is not False)
    ]

    workshop = None
    if (
        not problems
        and equipment
        and construction
        and materials
        and hardware
        and labor
        and pricing
        and appliances
        and economy
        and master
    ):
        workshop = Workshop(
            equipment=equipment,
            construction=construction,
            materials=materials,
            hardware=hardware,
            labor=labor,
            pricing=pricing,
            appliances=appliances,
            economy=economy,
            master=master,
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

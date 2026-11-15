"""Общие позиции кухни (1.7): цоколь, шина, навесы, LED с блоком питания, столешница.

Правила и позиции — в `construction.yaml` (`общие_позиции`, МОК). Длины берутся из рядов проекта:
цоколь — по фронту всех нижних модулей (пенал и ПММ тоже стоят на цоколе), шина и LED — по верхним
рядам, столешница — по общей длине нижних модулей без пенала, целыми листами. Угловой модуль считается на своей стене:
смещение на соседней стене его не удлиняет (стык в углу «встык»).
"""

from collections.abc import Iterable
from decimal import ROUND_CEILING, Decimal
from typing import Literal

from pydantic import BaseModel

from kitchen.errors import OrderError
from kitchen.models import Workshop
from kitchen.parts import TEMPLATES
from kitchen.project import Project

__all__ = ["CommonItem", "CountertopSize", "compute_common", "is_tall", "row_layouts", "size_countertop"]


class CommonItem(BaseModel):
    """Общая позиция: что купить и на каком основании. `item` — id из hardware.yaml или столешницы."""

    kind: Literal["plinth", "rail", "hangers", "led", "led_power_supply", "countertop"]
    item: str
    name: str
    quantity: int
    unit: str  # шт, м, лист
    length_mm: int | None = None  # длина, по которой посчитано (без запаса)
    basis: str  # откуда число, по-русски
    in_hardware: bool = False  # уже входит в итог фурнитуры (навесы считают шаблоны модулей)


def _ceil(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_CEILING))


def is_tall(m) -> bool:
    """Модуль записан в одном ряду стены и занимает оба — свойство шаблона в реестре."""
    return TEMPLATES[m.template].both_rows if m.template in TEMPLATES else False


def row_layouts(wall) -> list[list]:
    """Модули каждого ряда стены по порядку вдоль стены, с пеналами из других рядов: пенал входит в оба ряда
    (и в ряд со своим пеналом тоже). Единственное место, где решается, что стоит в ряду: сумма ряда
    (`checks`), цоколь, шина и столешница берут модули отсюда. Место пенала — отступ от начала стены:
    его ряд считает отступ по своим модулям и пеналам, уже попавшим в этот ряд, поэтому отступы находятся
    повторными проходами, пока не перестанут меняться. Отступ пенала записывается сразу, как только ряд его
    посчитал: следующий ряд того же прохода уже видит его (иначе пеналы в разных рядах на одном месте
    менялись бы местами на каждом проходе). Не сошлось за отведённое число проходов — ошибка."""
    at: dict[int, float] = {}  # id пенала -> отступ от начала стены
    for _ in range(len(wall.rows) * 4 + 2):
        before = dict(at)
        layouts: list[list] = []
        for number, row in enumerate(wall.rows):
            found: dict[int, float] = {}
            talls = sorted(
                (
                    (at[id(m)], m)
                    for o, other in enumerate(wall.rows)
                    if o != number
                    for m in other.modules
                    if is_tall(m) and id(m) in at
                ),
                key=lambda t: t[0],
            )
            unplaced = [
                m
                for o, other in enumerate(wall.rows)
                if o != number
                for m in other.modules
                if is_tall(m) and id(m) not in at
            ]
            merged: list = []
            x = 0.0
            for m in row.modules:
                while talls and talls[0][0] <= x:
                    tall = talls.pop(0)[1]
                    merged.append(tall)
                    x += tall.width
                if is_tall(m):
                    found[id(m)] = x
                merged.append(m)
                x += m.width
            merged += [t for _, t in talls] + unplaced
            layouts.append(merged)
            at.update(found)
        if at == before:
            return layouts
    raise OrderError(
        "Раскладка рядов стены не сходится: отступы пеналов не перестают меняться — проверьте ширины модулей."
    )


def _row_modules(wall, row) -> list:
    """Модули ряда для длин: нижний ряд — с пеналами из других рядов; верхний — свои."""
    if row.kind != "lower":
        return list(row.modules)
    return row_layouts(wall)[next(i for i, r in enumerate(wall.rows) if r is row)]


def _length(project: Project, kinds: Iterable[str], with_tall: bool) -> int:
    """Сумма ширин модулей рядов нужного вида по всем стенам; пенал входит в сумму только при `with_tall`."""
    return sum(
        int(m.width)  # целую ширину обеспечивает `module_dims`
        for wall in project.walls
        for row in wall.rows
        if row.kind in kinds
        for m in _row_modules(wall, row)
        if with_tall or not is_tall(m)
    )


def _named(items: list, item_id: str) -> str:
    return next((i.name for i in items if i.id == item_id), item_id)


def _pieces(total_mm: int, rule_piece_mm: int, reserve: Decimal) -> int:
    return _ceil(Decimal(total_mm) * (1 + reserve) / rule_piece_mm)


class CountertopSize(BaseModel):
    """Столешница: общая длина под ней и целые листы (без нарезки на куски)."""

    item: str
    name: str
    sheet_length: int
    sheet_width: int
    sheet_count: int
    total_length_mm: int  # сумма ширин нижних модулей без пенала, угол без смещения


def size_countertop(project: Project, ws: Workshop) -> CountertopSize | None:
    """Общая длина столешницы и число листов (вверх до целого); `None`, если столешница в проекте не задана.
    Одна функция для общих позиций, раскроя, закупки и сметы."""
    top_id = project.materials.countertop
    if not top_id:
        return None
    top = next((t for t in ws.materials.countertops if t.id == top_id), None)
    if top is None:
        known = ", ".join(sorted(t.id for t in ws.materials.countertops))
        raise OrderError(f"Столешница «{top_id}» не найдена в materials.yaml (столешницы: {known}).")
    total = _length(project, ("lower",), with_tall=False)
    return CountertopSize(
        item=top_id,
        name=top.name,
        sheet_length=top.sheet[0],
        sheet_width=top.sheet[1],
        sheet_count=_ceil(Decimal(total) / top.sheet[0]),
        total_length_mm=total,
    )


def compute_common(project: Project, ws: Workshop, hardware: dict[str, int]) -> list[CommonItem]:
    """Общие позиции кухни по правилам `общие_позиции`; LED — если в проекте `led`, столешница — если задана."""
    rules = ws.construction.common
    reserve = Decimal(str(rules.joint_reserve))
    percent = f"{rules.joint_reserve:.0%}"
    hw = ws.hardware.items
    items: list[CommonItem] = []

    corner = ws.construction.corner_rules.lower_offset
    dead = corner * sum(  # глухая часть угловых модулей нижних рядов цоколем не закрывается
        1
        for wall in project.walls
        for row in wall.rows
        if row.kind == "lower"
        for m in row.modules
        if m.corner
    )
    lower = _length(project, ("lower",), with_tall=True) - dead
    n = _pieces(lower, rules.plinth.piece_length, reserve)
    items.append(
        CommonItem(
            kind="plinth",
            item=rules.plinth.item,
            name=_named(hw, rules.plinth.item),
            quantity=n,
            unit="шт",
            length_mm=lower,
            basis=f"фронт нижних рядов {lower} мм (без глухой части углов {dead} мм) + запас {percent}, штуки по {rules.plinth.piece_length} мм",
        )
    )
    upper = _length(project, ("upper",), with_tall=False)
    n = _pieces(upper, rules.rail.piece_length, reserve)
    items.append(
        CommonItem(
            kind="rail",
            item=rules.rail.item,
            name=_named(hw, rules.rail.item),
            quantity=n,
            unit="шт",
            length_mm=upper,
            basis=f"верхние ряды {upper} мм + запас {percent}, штуки по {rules.rail.piece_length} мм",
        )
    )
    hanger = ws.construction.upper_rules.hanger
    items.append(
        CommonItem(
            kind="hangers",
            item=hanger,
            name=_named(hw, hanger),
            quantity=hardware.get(hanger, 0),
            unit="шт",
            basis="считают шаблоны верхних модулей; в итоге фурнитуры уже учтены",
            in_hardware=True,
        )
    )
    if project.led:
        led = rules.led
        meters = Decimal(upper) * (1 + reserve) / 1000
        items.append(
            CommonItem(
                kind="led",
                item=led.profile,
                name=_named(hw, led.profile),
                quantity=_ceil(meters / led.meter_rounding) * led.meter_rounding,
                unit="м",
                length_mm=upper,
                basis=f"верхние ряды {upper} мм + запас {percent}, вверх до кратного {led.meter_rounding} м",
            )
        )
        items.append(
            CommonItem(
                kind="led_power_supply",
                item=led.power_supply,
                name=_named(hw, led.power_supply),
                quantity=_ceil(meters / Decimal(str(led.meters_per_supply))),
                unit="шт",
                basis=f"{led.meters_per_supply:g} м ленты на блок",
            )
        )
    size = size_countertop(project, ws)
    if size is not None:
        items.append(
            CommonItem(
                kind="countertop",
                item=size.item,
                name=size.name,
                quantity=size.sheet_count,
                unit="лист",
                length_mm=size.total_length_mm,
                basis=f"общая длина {size.total_length_mm} мм, листы по {size.sheet_length} мм вверх до целого",
            )
        )
    return items

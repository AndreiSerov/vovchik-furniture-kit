"""Общие позиции кухни (1.7): цоколь, шина, навесы, LED с блоком питания, столешница.

Правила и позиции — в `construction.yaml` (`общие_позиции`, МОК). Длины берутся из рядов проекта:
цоколь — по фронту всех нижних модулей (пенал и ПММ тоже стоят на цоколе), шина и LED — по верхним
рядам, столешница — по нижним модулям без пенала. Угловой модуль считается на своей стене:
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

__all__ = ["CommonItem", "CountertopCut", "compute_common", "plan_countertop"]


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


def _is_tall(m) -> bool:
    return TEMPLATES[m.template].both_rows if m.template in TEMPLATES else False


def _row_modules(wall, row) -> list:
    """Модули ряда по порядку вдоль стены. В нижний ряд добавлены пеналы стены, описанные в других рядах
    (даже если в нём есть свой): пенал входит в оба ряда. Место пенала — по его отступу от начала стены
    в своём ряду."""
    if row.kind != "lower":
        return list(row.modules)
    talls: list[tuple[float, object]] = []
    for other in wall.rows:
        if other is row:
            continue
        x = 0.0
        for m in other.modules:
            if _is_tall(m):
                talls.append((x, m))
            x += m.width
    talls.sort(key=lambda t: t[0])
    merged: list = []
    x = 0.0
    for m in row.modules:
        while talls and talls[0][0] <= x:
            tall = talls.pop(0)[1]
            merged.append(tall)
            x += tall.width
        merged.append(m)
        x += m.width
    merged += [t for _, t in talls]
    return merged


def _runs(project: Project, kinds: Iterable[str], with_tall: bool) -> list[tuple[str, list[int]]]:
    """Непрерывные участки модулей нужного ряда по стенам: пенал либо входит в участок, либо разрывает его."""
    runs: list[tuple[str, list[int]]] = []
    for wall in project.walls:
        for row in wall.rows:
            if row.kind not in kinds:
                continue
            current: list[int] = []
            for m in _row_modules(wall, row):
                tall = TEMPLATES[m.template].both_rows if m.template in TEMPLATES else False
                if tall and not with_tall:
                    if current:
                        runs.append((wall.label, current))
                    current = []
                else:
                    current.append(round(m.width))
            if current:
                runs.append((wall.label, current))
    return runs


def _total(runs: list[tuple[str, list[int]]]) -> int:
    return sum(sum(widths) for _, widths in runs)


def _named(items: list, item_id: str) -> str:
    return next((i.name for i in items if i.id == item_id), item_id)


def _pieces(total_mm: int, rule_piece_mm: int, reserve: Decimal) -> int:
    return _ceil(Decimal(total_mm) * (1 + reserve) / rule_piece_mm)


def _countertop_sheets(runs: list[tuple[str, list[int]]], sheet_mm: int, kerf: int) -> list[list[int]]:
    """Куски столешницы (участок = кусок, длиннее листа режется по длине листа) по листам: первый подходящий,
    куски по убыванию; между кусками одного листа пропил. Результат — длины кусков каждого листа."""
    pieces: list[int] = []
    for _, widths in runs:
        length = sum(widths)
        while length > sheet_mm:
            pieces.append(sheet_mm)
            length -= sheet_mm
        if length:
            pieces.append(length)
    sheets: list[list[int]] = []
    for piece in sorted(pieces, reverse=True):
        for sheet in sheets:
            if sum(sheet) + kerf * len(sheet) + piece <= sheet_mm:
                sheet.append(piece)
                break
        else:
            sheets.append([piece])
    return sheets


class CountertopCut(BaseModel):
    """Линейный раскрой столешницы: куски по листам (длины вдоль листа, мм)."""

    item: str
    name: str
    sheet_length: int
    sheet_width: int
    sheets: list[list[int]]
    sheet_count: int
    total_length_mm: int  # сумма длин нижних рядов без пенала


def plan_countertop(project: Project, ws: Workshop) -> CountertopCut | None:
    """Куски столешницы по листам; `None`, если столешница в проекте не задана."""
    top_id = project.materials.countertop
    if not top_id:
        return None
    top = next((t for t in ws.materials.countertops if t.id == top_id), None)
    if top is None:
        known = ", ".join(sorted(t.id for t in ws.materials.countertops))
        raise OrderError(f"Столешница «{top_id}» не найдена в materials.yaml (столешницы: {known}).")
    runs = _runs(project, ("lower",), with_tall=False)
    sheets = _countertop_sheets(runs, top.sheet[0], ws.equipment.saw.kerf_mm)
    return CountertopCut(
        item=top_id,
        name=top.name,
        sheet_length=top.sheet[0],
        sheet_width=top.sheet[1],
        sheets=sheets,
        sheet_count=len(sheets),
        total_length_mm=_total(runs),
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
    lower = _total(_runs(project, ("lower",), with_tall=True)) - dead
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
    upper_runs = _runs(project, ("upper",), with_tall=False)
    upper = _total(upper_runs)
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
    plan = plan_countertop(project, ws)
    if plan is not None:
        listed = " + ".join(str(p) for p in sorted((p for sheet in plan.sheets for p in sheet), reverse=True))
        items.append(
            CommonItem(
                kind="countertop",
                item=plan.item,
                name=plan.name,
                quantity=plan.sheet_count,
                unit="лист",
                length_mm=plan.total_length_mm,
                basis=f"куски {listed} мм из листов по {plan.sheet_length} мм, стык в углу встык",
            )
        )
    return items

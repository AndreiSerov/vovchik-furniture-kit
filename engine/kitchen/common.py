"""Общие позиции кухни (1.7): цоколь, шина, навесы, LED с блоком питания, столешница.

Правила и позиции — в `construction.yaml` (`общие_позиции`, МОК). Длины берутся из рядов проекта:
цоколь — по фронту всех нижних модулей (пенал и ПММ тоже стоят на цоколе), шина и LED — по верхним
рядам, столешница — по общей длине нижних модулей без пенала, целыми листами. Угловой модуль считается на своей стене:
смещение на соседней стене его не удлиняет (стык в углу «встык»).
"""

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from kitchen.errors import OrderError
from kitchen.format import label_order
from kitchen.models import Workshop
from kitchen.parts import TEMPLATES, corner_on_left, gap_width, upper_corner_offset
from kitchen.project import ModuleSpec, Project, Row, Wall, is_gap

__all__ = [
    "CommonItem",
    "CornerOffsets",
    "CountertopSize",
    "RowOffsets",
    "compute_common",
    "corner_modules",
    "corner_offsets",
    "corner_wall_message",
    "is_tall",
    "pair_by_model_then_order",
    "row_layouts",
    "row_positions",
    "size_countertop",
]


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
    if is_gap(m):
        return False
    return TEMPLATES[m.template].both_rows if m.template in TEMPLATES else False


@dataclass
class RowOffsets:
    """Угловые смещения соседних стен в одном ряду стены: в начале и в конце ряда, мм, и угловые модули,
    которые их дают (метки)."""

    start: float = 0
    end: float = 0
    at_start: list[str] = field(default_factory=list)
    at_end: list[str] = field(default_factory=list)


@dataclass
class CornerOffsets:
    """Угловые смещения проекта по (стена, вид ряда). `unknown` — ряды, чьё смещение не посчитать: габариты
    углового не строятся (дробные мм); причину называет `module_build`."""

    rows: dict[tuple[str, str], RowOffsets] = field(default_factory=dict)
    unknown: set[tuple[str, str]] = field(default_factory=set)

    def of(self, wall: str, kind: str) -> RowOffsets:
        return self.rows.get((wall, kind), RowOffsets())


def corner_modules(project: Project) -> Iterator[tuple[Wall, Row, ModuleSpec, str]]:
    """Угловые модули проекта: (стена, ряд, модуль, соседняя стена). Один обход для суммы ряда (`checks`),
    смещений (`corner_offsets`) и связей стен (`placement`)."""
    for wall in project.walls:
        for row in wall.rows:
            for m in row.cabinets:
                if m.corner is not None:
                    yield wall, row, m, m.corner.adjacent_wall


def corner_wall_message(module: str, adjacent: str, wall: str) -> str:
    """Угол с несуществующей или своей же стеной (проверка `corner_wall` и ошибка расстановки)."""
    return f"Модуль {module}: соседняя стена «{adjacent}» должна существовать и отличаться от стены «{wall}»."


def corner_offsets(project: Project, ws: Workshop) -> CornerOffsets:
    """Угловые смещения соседних стен — единственное место, где решаются величина и сторона смещения: сумма
    ряда (`checks`), порядок модулей в рядах (`row_layouts`) и расстановка в комнате берут его отсюда. Нижнее —
    `угловые_модули.смещение_нижнего`, верхнее — `parts.upper_corner_offset`. Сторона — `parts.corner_on_left`:
    угловой у правого края ряда (последний, единственный) — стена идёт слева направо, соседняя начинается от
    угла: смещение в начале соседней стены; у левого (первый при других) — в её конце. Угол с несуществующей
    или своей же стеной не считается (его называет проверка `corner_wall`)."""
    rules = ws.construction.corner_rules
    labels = {w.label for w in project.walls}
    found = CornerOffsets()
    for wall, row, m, adjacent in corner_modules(project):
        if adjacent == wall.label or adjacent not in labels:
            continue
        key = (adjacent, row.kind)
        try:
            size = rules.lower_offset if row.kind == "lower" else upper_corner_offset(m, row, ws)
        except OrderError:
            found.unknown.add(key)
            continue
        offsets = found.rows.setdefault(key, RowOffsets())
        if corner_on_left(row, m):
            offsets.end += size
            offsets.at_end.append(m.label)
        else:
            offsets.start += size
            offsets.at_start.append(m.label)
    return found


def _goes_first(m, other: tuple[float, ModuleSpec], x: float) -> bool:
    """Пенал `m` ряда встаёт на отступ `x` раньше пенала другого ряда с тем же отступом, если его метка раньше
    по `format.label_order` (решение мастера 2026-11-16, тикет 54): порядок не зависит от того, какой ряд
    записан первым. Пенал другого ряда с меньшим отступом и не пенал на его отступе — после пенала, как раньше."""
    at, tall = other
    return is_tall(m) and at == x and label_order(m.label) < label_order(tall.label)


def row_layouts(wall, offsets: CornerOffsets) -> list[list]:
    """Модули каждого ряда стены по порядку вдоль стены, с пеналами из других рядов: пенал входит в оба ряда
    (и в ряд со своим пеналом тоже). Единственное место, где решается, что стоит в ряду: сумма ряда
    (`checks`), цоколь, шина, столешница и расстановка берут модули отсюда. Место пенала — отступ от начала
    стены: его ряд считает отступ по своим модулям и пеналам, уже попавшим в этот ряд, начиная с углового
    смещения соседней стены в начале ряда (`offsets` — результат `corner_offsets`), поэтому отступы находятся
    повторными проходами, пока не перестанут меняться. Отступ пенала записывается сразу, как только ряд его
    посчитал: следующий ряд того же прохода уже видит его (иначе пеналы в разных рядах на одном месте
    менялись бы местами на каждом проходе). Пеналы разных рядов на одном отступе стоят по порядку меток
    (`_goes_first`). Не сошлось за отведённое число проходов — ошибка."""
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
                key=lambda t: (t[0], label_order(t[1].label)),
            )
            unplaced = [
                m
                for o, other in enumerate(wall.rows)
                if o != number
                for m in other.modules
                if is_tall(m) and id(m) not in at
            ]
            merged: list = []
            x = float(offsets.of(wall.label, row.kind).start)
            for m in row.modules:
                while talls and talls[0][0] <= x and not _goes_first(m, talls[0], x):
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
        "Порядок модулей в рядах стены не сходится: отступы пеналов не перестают меняться — проверьте ширины модулей."
    )


def row_positions(wall, offsets: CornerOffsets) -> list[list[tuple[float, object]]]:
    """`row_layouts` с отступом каждой записи от начала стены: ряд начинается с углового смещения в его
    начале, записи идут вплотную. Своё место пенала — отступ в его собственном ряду."""
    found: list[list[tuple[float, object]]] = []
    for row, merged in zip(wall.rows, row_layouts(wall, offsets), strict=True):
        x = float(offsets.of(wall.label, row.kind).start)
        placed: list[tuple[float, object]] = []
        for entry in merged:
            placed.append((x, entry))
            x += entry.width
        found.append(placed)
    return found


def _row_modules(wall, row, offsets: CornerOffsets) -> list:
    """Модули ряда для длин: нижний ряд — с пеналами из других рядов; верхний — свои."""
    if row.kind != "lower":
        return list(row.modules)
    return row_layouts(wall, offsets)[next(i for i, r in enumerate(wall.rows) if r is row)]


def _entries_of_kinds(project: Project, ws: Workshop, kinds: Iterable[str], with_tall: bool) -> list:
    """Записи рядов нужного вида по всем стенам (модули и пустые места); пенал входит только при `with_tall`.
    Нижний ряд берёт пеналы стены из других рядов; на стене без нижнего ряда пенал тоже стоит на цоколе."""
    found: list = []
    offsets = corner_offsets(project, ws)
    for wall in project.walls:
        lowers = [row for row in wall.rows if row.kind == "lower"]
        for row in wall.rows:
            if row.kind in kinds:
                found += [m for m in _row_modules(wall, row, offsets) if with_tall or not is_tall(m)]
        if with_tall and "lower" in kinds and not lowers:
            found += [m for row in wall.rows for m in row.modules if is_tall(m)]
    return found


def _length(project: Project, ws: Workshop, kinds: Iterable[str], with_tall: bool) -> int:
    """Сумма ширин модулей рядов нужного вида по всем стенам, без пустых мест."""
    return sum(
        int(m.width)  # целую ширину обеспечивает `module_dims`
        for m in _entries_of_kinds(project, ws, kinds, with_tall)
        if not is_gap(m)
    )


def _gaps_mm(project: Project, ws: Workshop, kinds: Iterable[str], *, under_top: bool | None = None) -> int:
    """Сумма ширин пустых мест рядов нужного вида; `under_top` отбирает места по `under_countertop`."""
    return sum(
        gap_width(m)
        for m in _entries_of_kinds(project, ws, kinds, False)
        if is_gap(m) and (under_top is None or m.under_countertop == under_top)
    )


def _tail(mm: int, own: bool = False) -> str:
    """Хвост основания при пустых местах: «, без пустых мест 600 мм» внутри скобок, `own` — в своих скобках
    « (без пустых мест 600 мм)»; без пустых мест — пусто."""
    if not mm:
        return ""
    return f" (без пустых мест {mm} мм)" if own else f", без пустых мест {mm} мм"


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
    total_length_mm: (
        int  # сумма ширин нижних модулей без пенала, угол без смещения, плюс места под столешницей
    )
    gaps_mm: int = Field(
        default=0, exclude=True
    )  # пустые места, не вошедшие в длину; только для основания, не в JSON


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
    # места под общей столешницей входят в её длину, остальные — нет
    total = _length(project, ws, ("lower",), with_tall=False) + _gaps_mm(
        project, ws, ("lower",), under_top=True
    )
    return CountertopSize(
        item=top_id,
        name=top.name,
        sheet_length=top.sheet[0],
        sheet_width=top.sheet[1],
        sheet_count=_ceil(Decimal(total) / top.sheet[0]),
        total_length_mm=total,
        gaps_mm=_gaps_mm(project, ws, ("lower",), under_top=False),
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
        for m in row.cabinets
        if m.corner
    )
    lower = _length(project, ws, ("lower",), with_tall=True) - dead
    lower_gaps = _gaps_mm(project, ws, ("lower",))
    n = _pieces(lower, rules.plinth.piece_length, reserve)
    items.append(
        CommonItem(
            kind="plinth",
            item=rules.plinth.item,
            name=_named(hw, rules.plinth.item),
            quantity=n,
            unit="шт",
            length_mm=lower,
            basis=f"фронт нижних рядов {lower} мм (без глухой части углов {dead} мм{_tail(lower_gaps)}) + запас {percent}, штуки по {rules.plinth.piece_length} мм",
        )
    )
    upper = _length(project, ws, ("upper",), with_tall=False)
    upper_gaps = _gaps_mm(project, ws, ("upper",))
    n = _pieces(upper, rules.rail.piece_length, reserve)
    items.append(
        CommonItem(
            kind="rail",
            item=rules.rail.item,
            name=_named(hw, rules.rail.item),
            quantity=n,
            unit="шт",
            length_mm=upper,
            basis=f"верхние ряды {upper} мм{_tail(upper_gaps, True)} + запас {percent}, штуки по {rules.rail.piece_length} мм",
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
                basis=f"верхние ряды {upper} мм{_tail(upper_gaps, True)} + запас {percent}, вверх до кратного {led.meter_rounding} м",
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
                basis=f"общая длина {size.total_length_mm} мм{_tail(size.gaps_mm, True)}, листы по {size.sheet_length} мм вверх до целого",
            )
        )
    return items


def pair_by_model_then_order(
    mine: list[Any], theirs: list[Any], model_of: Callable[[Any], object]
) -> tuple[list[tuple[Any, Any]], list[Any], bool]:
    """Пары приборов одного вида: сначала по модели (без учёта регистра), затем по порядку. Единственное правило
    пар для «Состава техники» (проект и бриф) и для `diff` (две версии проекта). Возвращает пары по порядку `mine`
    (без пары — `None`), непарные из `theirs` и признак, что пары по порядку были."""
    items = list(theirs)
    pairs: list[tuple[Any, Any]] = []
    rest: list[Any] = []
    for a in mine:
        model = model_of(a)
        hit = next(
            (b for b in items if model and model_of(b) and str(model).lower() == str(model_of(b)).lower()),
            None,
        )
        if hit is not None:
            items.remove(hit)
            pairs.append((a, hit))
        else:
            rest.append(a)
            pairs.append((a, None))
    by_order = bool(rest and items)
    for a in rest:
        index = next(i for i, (x, _) in enumerate(pairs) if x is a)
        pairs[index] = (a, items.pop(0) if items else None)
    return pairs, items, by_order

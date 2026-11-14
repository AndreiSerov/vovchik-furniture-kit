"""Раскрой (2.1): гильотинная укладка листовых деталей по листам.

Лист рисуем в осях `x` (вдоль текстуры, у нетекстурного — первая сторона листа) и `y`. Рабочее поле —
лист минус обрезка по периметру. Пропил моделируем так: к каждой детали и к полю прибавляется пропил,
`rectpack` укладывает увеличенные прямоугольники, настоящие детали остаются в их углу — между любыми двумя
деталями зазор не меньше пропила. Обрезка по периметру отдельными резами не нумеруется (DECISIONS.md).
"""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel
from rectpack import (
    SORT_AREA,
    SORT_DIFF,
    SORT_LSIDE,
    SORT_PERI,
    SORT_RATIO,
    SORT_SSIDE,
    GuillotineBafLas,
    GuillotineBafLlas,
    GuillotineBafMaxas,
    GuillotineBafMinas,
    GuillotineBafSas,
    GuillotineBafSlas,
    GuillotineBlsfLas,
    GuillotineBlsfLlas,
    GuillotineBlsfMaxas,
    GuillotineBlsfMinas,
    GuillotineBlsfSas,
    GuillotineBlsfSlas,
    GuillotineBssfLas,
    GuillotineBssfLlas,
    GuillotineBssfMaxas,
    GuillotineBssfMinas,
    GuillotineBssfSas,
    GuillotineBssfSlas,
    PackingBin,
    PackingMode,
    newPacker,
)

from kitchen.checks import along_texture, part_fit_findings
from kitchen.errors import KitchenError, OrderError
from kitchen.models import SheetMaterial, Workshop
from kitchen.parts import ModuleParts, Part

__all__ = ["Cut", "MaterialCut", "Placement", "SheetCut", "cut_order", "cut_parts"]


class Placement(BaseModel):
    """Деталь на листе: левый верхний угол (мм от края листа, с обрезкой) и размеры по осям листа."""

    part: str
    x: int
    y: int
    w: int  # размер по оси x листа
    h: int  # размер по оси y листа
    rotated: bool  # длина детали лежит вдоль y (только нетекстурные)


class Cut(BaseModel):
    """Рез. `vertical` — линия `x = position` (рез идёт вдоль y), `horizontal` — линия `y = position`.
    Пропил занимает `position … position + kerf`; рез идёт от `start` до `end` вдоль линии."""

    number: int
    direction: Literal["vertical", "horizontal"]
    position: int
    start: int
    end: int
    length: int
    kind: Literal["separate", "trim"]  # разделяет детали или отрезает отход


class SheetCut(BaseModel):
    number: int
    placements: list[Placement]
    cuts: list[Cut]
    parts_area_m2: float
    utilization: float  # доля площади листа, занятая деталями


class MaterialCut(BaseModel):
    material: str
    name: str
    sheet_size: tuple[int, int]  # (x, y) — x вдоль текстуры
    texture: bool
    sheet_count: int
    parts_count: int
    parts_area_m2: float
    sheet_area_m2: float
    utilization: float
    waste_m2: float
    algorithm: str
    sheets: list[SheetCut]


_ALGOS = [
    GuillotineBafLas,
    GuillotineBafLlas,
    GuillotineBafMaxas,
    GuillotineBafMinas,
    GuillotineBafSas,
    GuillotineBafSlas,
    GuillotineBlsfLas,
    GuillotineBlsfLlas,
    GuillotineBlsfMaxas,
    GuillotineBlsfMinas,
    GuillotineBlsfSas,
    GuillotineBlsfSlas,
    GuillotineBssfLas,
    GuillotineBssfLlas,
    GuillotineBssfMaxas,
    GuillotineBssfMinas,
    GuillotineBssfSas,
    GuillotineBssfSlas,
]
_SORTS = [
    ("area", SORT_AREA),
    ("peri", SORT_PERI),
    ("diff", SORT_DIFF),
    ("ssize", SORT_SSIDE),
    ("lside", SORT_LSIDE),
    ("ratio", SORT_RATIO),
]
_MODES = ["BNF", "BFF", "BBF"]


@dataclass(frozen=True)
class _Rect:
    x: int
    y: int
    w: int
    h: int
    id: str


def _placed(packer) -> list[list[_Rect]]:
    sheets: dict[int, list[_Rect]] = {}
    for bin_index, x, y, w, h, rid in packer.rect_list():
        sheets.setdefault(bin_index, []).append(_Rect(int(x), int(y), int(w), int(h), rid))
    return [sheets[i] for i in sorted(sheets)]


def _layouts(
    parts: dict[str, Part], field: tuple[int, int], kerf: int, rotation: bool
) -> Iterator[tuple[str, list[list[_Rect]]]]:
    """Все раскладки по всем гильотинным эвристикам; увеличенные на пропил прямоугольники."""
    ordered = sorted(parts.values(), key=lambda p: p.id)
    for algo in _ALGOS:
        for sort_name, sort in _SORTS:
            for mode in _MODES:
                packer = newPacker(
                    mode=PackingMode.Offline,
                    bin_algo=getattr(PackingBin, mode),
                    pack_algo=algo,
                    sort_algo=sort,
                    rotation=rotation,
                )
                packer.add_bin(field[0] + kerf, field[1] + kerf, count=len(ordered), merge=False)
                for p in ordered:
                    packer.add_rect(p.length + kerf, p.width + kerf, p.id)
                packer.pack()
                if len(packer.rect_list()) != len(ordered):
                    continue
                yield f"{algo.__name__}/{sort_name}/{mode}", _placed(packer)


def cut_order(placements: list[Placement], size: tuple[int, int], kerf: int, trim: int) -> list[Cut]:
    """Порядок резов листа: рекурсивно ищем сквозной рез куска (от края до края), делим, повторяем.
    Сначала отрезаем отход вокруг деталей куска (`trim`), затем делим детали (`separate`); левый/верхний
    кусок режем раньше правого/нижнего. Раскладка без сквозного реза — ошибка движка."""
    cuts: list[Cut] = []

    def add(direction: Literal["vertical", "horizontal"], position: int, start: int, end: int, kind) -> None:
        cuts.append(
            Cut(
                number=len(cuts) + 1,
                direction=direction,
                position=position,
                start=start,
                end=end,
                length=end - start,
                kind=kind,
            )
        )

    def through(items: list[Placement], lo: int, hi: int, vertical: bool) -> int | None:
        """Край детали `e`, по которому проходит сквозной рез: никто его не пересекает, обе стороны не пусты."""
        edge = (lambda r: r.x + r.w) if vertical else (lambda r: r.y + r.h)
        begin = (lambda r: r.x) if vertical else (lambda r: r.y)
        for e in sorted({edge(r) for r in items}):
            if e >= hi:
                continue
            if (
                all(edge(r) <= e or begin(r) >= e + kerf for r in items)
                and any(edge(r) <= e for r in items)
                and any(
                    begin(r) >= e + kerf for r in items
                )  # справа от реза есть деталь (край куска мог не стянуться)
            ):
                return e
        return None

    def walk(items: list[Placement], x0: int, y0: int, x1: int, y1: int) -> None:
        minx, maxx = min(r.x for r in items), max(r.x + r.w for r in items)
        miny, maxy = min(r.y for r in items), max(r.y + r.h for r in items)
        if minx > x0:
            add("vertical", minx - kerf, y0, y1, "trim")
        if maxx < x1:
            add("vertical", maxx, y0, y1, "trim")
        if miny > y0:
            add("horizontal", miny - kerf, minx, maxx, "trim")
        if maxy < y1:
            add("horizontal", maxy, minx, maxx, "trim")
        x0, y0, x1, y1 = minx, miny, maxx, maxy
        if len(items) == 1:
            return
        e = through(items, x0, x1, vertical=True)
        if e is not None:
            add("vertical", e, y0, y1, "separate")
            walk([r for r in items if r.x + r.w <= e], x0, y0, e, y1)
            walk([r for r in items if r.x >= e + kerf], e + kerf, y0, x1, y1)
            return
        e = through(items, y0, y1, vertical=False)
        if e is None:
            raise KitchenError(
                "Ошибка движка: детали листа не режутся гильотинно (нет сквозного реза у детали "
                + ", ".join(r.part for r in items[:4])
                + ")."
            )
        add("horizontal", e, x0, x1, "separate")
        walk([r for r in items if r.y + r.h <= e], x0, y0, x1, e)
        walk([r for r in items if r.y >= e + kerf], x0, e + kerf, x1, y1)

    if placements:
        walk(placements, trim, trim, size[0] - trim, size[1] - trim)
    return cuts


def _real(rect: _Rect, part: Part, kerf: int, trim: int) -> Placement:
    """Увеличенный прямоугольник -> деталь настоящего размера в координатах листа (с обрезкой)."""
    rotated = (rect.w - kerf, rect.h - kerf) != (part.length, part.width)
    w, h = (part.width, part.length) if rotated else (part.length, part.width)
    return Placement(part=part.id, x=rect.x + trim, y=rect.y + trim, w=w, h=h, rotated=rotated)


def _score(layout: list[list[_Rect]], parts: dict[str, Part]):
    """Меньше листов, затем больше площадь деталей на последнем листе (спека 2.1)."""
    last = sum(parts[r.id].length * parts[r.id].width for r in layout[-1])
    return (len(layout), -last)


def _material_axes(sheet: SheetMaterial) -> tuple[int, int]:
    """Размер листа по осям раскроя: x — вдоль текстуры; у нетекстурного — как записано в materials.yaml."""
    a, b = sheet.sheet
    if isinstance(sheet.texture, str):
        along, across = along_texture(sheet, sheet.sheet)
        return round(along), round(across)
    return round(a), round(b)


def _cut_material(sheet: SheetMaterial, parts: list[Part], ws: Workshop) -> MaterialCut:
    kerf = round(ws.equipment.saw.kerf)
    trim = round(ws.equipment.sheet.perimeter_trim)
    max_cut = round(ws.equipment.saw.max_cut_length)
    size = _material_axes(sheet)
    field = (size[0] - 2 * trim, size[1] - 2 * trim)
    textured = bool(sheet.texture)
    by_id = {p.id: p for p in parts}
    for p in parts:
        findings = part_fit_findings(p, sheet, ws)
        if findings:
            raise OrderError(findings[0].message)
    if max(size) > max_cut:
        raise OrderError(
            f"Материал {sheet.id}: подрезка листа {max(size)} мм по периметру длиннее максимума "
            f"станка {max_cut} мм (equipment.yaml, пила)."
        )
    best = min(
        _layouts(by_id, field, kerf, rotation=not textured),
        key=lambda item: _score(item[1], by_id),
        default=None,
    )
    if best is None:
        raise KitchenError(
            f"Раскрой {sheet.id}: ни одна эвристика не уложила все детали."
        )  # pragma: no cover
    algorithm, layout = best
    sheets = []
    for number, rects in enumerate(layout, 1):
        placements = [
            _real(r, by_id[r.id], kerf, trim) for r in sorted(rects, key=lambda r: (r.y, r.x, r.id))
        ]
        area = sum(pl.w * pl.h for pl in placements)
        cuts = cut_order(placements, size, kerf, trim)
        too_long = [c for c in cuts if c.length > max_cut]
        if too_long:
            raise OrderError(
                f"Лист {number} материала {sheet.id}: рез {too_long[0].number} длиной {too_long[0].length} мм "
                f"длиннее максимума станка {max_cut} мм (equipment.yaml, пила)."
            )
        sheets.append(
            SheetCut(
                number=number,
                placements=placements,
                cuts=cuts,
                parts_area_m2=round(area / 1e6, 4),
                utilization=round(area / (size[0] * size[1]), 4),
            )
        )
    parts_area = sum(p.length * p.width for p in parts)
    sheet_area = len(sheets) * size[0] * size[1]
    return MaterialCut(
        material=sheet.id,
        name=sheet.name,
        sheet_size=size,
        texture=textured,
        sheet_count=len(sheets),
        parts_count=len(parts),
        parts_area_m2=round(parts_area / 1e6, 4),
        sheet_area_m2=round(sheet_area / 1e6, 4),
        utilization=round(parts_area / sheet_area, 4),
        waste_m2=round((sheet_area - parts_area) / 1e6, 4),
        algorithm=algorithm,
        sheets=sheets,
    )


def cut_parts(modules: list[ModuleParts], ws: Workshop) -> list[MaterialCut]:
    """Раскрой листовых деталей по материалам, в порядке materials.yaml. Фасады в раскрой не идут."""
    sheets = {m.id: m for m in ws.materials.sheets}
    facades = {f.id for f in ws.materials.facades}
    grouped: dict[str, list[Part]] = {}
    for module in modules:
        for part in module.parts:
            if part.material in sheets:
                grouped.setdefault(part.material, []).append(part)
            elif part.material not in facades:
                raise OrderError(f"Деталь {part.id}: материал «{part.material}» не найден в materials.yaml.")
    return [_cut_material(sheets[mid], grouped[mid], ws) for mid in sheets if mid in grouped]

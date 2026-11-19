"""Карта присадки PDF (4.2): на деталь с отверстиями — схема в масштабе, отверстия, цепочки размеров от
базовых кромок (выделены), вид и сторона сверления, таблица отверстий. 2–4 схемы на страницу.

Раскладка страницы (`map_layout`: строки текста с кеглем и шириной, контур, базовые линии, цепочки с рамками
подписей, отметки отверстий) считается отдельно от рисования; её же читают тесты через `api.drilling_map_layout`.
"""

from datetime import date
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from typing import Literal

from pydantic import BaseModel
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas

from kitchen.drilling import DrillingCheck, part_extent
from kitchen.errors import KitchenError
from kitchen.geometry import PURPOSE_RU, Hole, Mm, ModuleFace, to_mm
from kitchen.parts import Part
from kitchen.pdf import FONT, FONT_BOLD, Area, PdfDocument, TextLine, mm, page_area, register_fonts

__all__ = ["PageLayout", "map_layout", "page_sizes", "write_drilling_map"]

PER_PAGE = 4
_HEADER_H = 12 * mm
_GAP = 4 * mm
_TITLE_SIZES = (7.5, 7.0, 6.5, 6.0, 5.5)
_TEXT_SIZES = (6.0, 5.5, 5.0)
MIN_FONT = _TEXT_SIZES[-1]
_LINE = 2.7 * mm
_DIM_ROW = 3.0 * mm  # строка цепочки размеров
_MIN_R = 0.7 * mm  # отверстие на схеме не меньше
_EDGE_RU = {"-z": "нижняя", "+z": "верхняя", "+y": "передняя", "-y": "задняя", "-x": "левая", "+x": "правая"}
_FROM_RU = {"-z": "нижней", "+z": "верхней", "+y": "передней", "-y": "задней", "-x": "левой", "+x": "правой"}
# с какой стороны смотрим и сверлим: грань в осях модуля -> по-русски
_SIDE_RU = {
    "+y": "с лица",
    "-y": "с тыльной стороны",
    "+x": "справа",
    "-x": "слева",
    "+z": "сверху",
    "-z": "снизу",
}
_COLORS = {
    "confirmat": (0.1, 0.3, 0.8),
    "hinge_cup": (0.8, 0.1, 0.1),
    "hinge_plate": (0.1, 0.55, 0.2),
    "shelf_pin": (0.55, 0.2, 0.65),
    "runner": (0.0, 0.55, 0.6),
}
_BASE_COLOR = (0.85, 0.35, 0.0)

Box = tuple[float, float, float, float]  # x0, y0, x1, y1, пункты


class ChainLabel(BaseModel):
    """Подпись цепочки: расстояние от базовой кромки, место отметки на оси и рамка подписи."""

    value: Mm
    tick: float
    box: Box


class DimChain(BaseModel):
    """Цепочка размеров от базовой кромки `face` вдоль оси схемы. `base` — координата базовой кромки на этой
    оси, `line` — координата линии цепочки поперёк оси."""

    face: ModuleFace
    direction: Literal["horizontal", "vertical"]
    base: float
    line: float
    labels: list[ChainLabel]


class BaseLine(BaseModel):
    """Выделенная базовая кромка на схеме."""

    face: ModuleFace
    x1: float
    y1: float
    x2: float
    y2: float


class HoleMark(BaseModel):
    """Отверстие на схеме: в пласть — кружок радиуса `r`, в торец — отрезок до (`x2`, `y2`)."""

    purpose: str
    surface: str
    through: bool
    x: float
    y: float
    r: float
    x2: float
    y2: float


class SchemeLayout(BaseModel):
    """Схема одной детали в ячейке страницы."""

    part: str
    slot: Box
    outline: Box
    scale: float  # пунктов на мм
    right: ModuleFace  # какая грань модуля смотрит вправо на схеме
    up: ModuleFace  # и вверх
    view: ModuleFace  # с какой стороны смотрим
    texts: list[TextLine]
    base_lines: list[BaseLine]
    chains: list[DimChain]
    holes: list[HoleMark]


class PageLayout(BaseModel):
    """Страница карты: шапка и 0–4 схемы."""

    header: list[TextLine]
    schemes: list[SchemeLayout]


def page_sizes(count: int) -> list[int]:
    """Сколько схем на каждой странице: не больше четырёх и, если деталей больше одной, не меньше двух
    (5 → 3 + 2, 9 → 3 + 3 + 3)."""
    pages = max(-(-count // PER_PAGE), 1)
    return [count // pages + (1 if i < count % pages else 0) for i in range(pages)] if count else []


def _fit(
    candidates: list[str], width: float, sizes: tuple[float, ...], bold: bool, what: str
) -> tuple[str, float]:
    """Первый вариант строки и самый крупный кегль, при которых строка не шире `width`: сначала мельче кегль
    до минимума, затем короче вариант. Не помещается и так — ошибка, а не строка за краем."""
    font = FONT_BOLD if bold else FONT
    for text in candidates:
        for size in sizes:
            if stringWidth(text, font, size) <= width:
                return text, size
    raise KitchenError(f"{what} не помещается на карте присадки даже кеглем {sizes[-1]}: «{candidates[-1]}».")


def _line(text: str, size: float, bold: bool, x: float, y: float) -> TextLine:
    return TextLine(
        text=text, size=size, bold=bold, x=x, y=y, width=stringWidth(text, FONT_BOLD if bold else FONT, size)
    )


_UNIT = {"x": (1, 0, 0), "y": (0, -1, 0), "z": (0, 0, 1)}  # оси модуля в правой тройке (y модуля — к зрителю)


def _view_faces(part: Part) -> tuple[ModuleFace, ModuleFace, ModuleFace]:
    """Вправо — x модуля, если он в плоскости детали, иначе y (перед справа); вверх — z, если в плоскости,
    иначе задняя кромка (деталь лёжа видна сверху, перед внизу). Третья грань — откуда смотрим."""
    plane = {part.orientation.length, part.orientation.width}
    right: ModuleFace = "+x" if "x" in plane else "+y"
    up: ModuleFace = "+z" if "z" in plane else "-y"

    def vec(face: str) -> tuple[int, int, int]:
        s = 1 if face[0] == "+" else -1
        return tuple(s * c for c in _UNIT[face[1]])  # type: ignore[return-value]

    a, b = vec(right), vec(up)
    n = (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])
    module = (n[0], -n[1], n[2])  # назад в оси модуля
    axis = "xyz"[next(i for i, v in enumerate(module) if v)]
    sign = "+" if sum(module) > 0 else "-"
    return right, up, f"{sign}{axis}"  # type: ignore[return-value]


def view_text(part: Part, right: ModuleFace, up: ModuleFace, view: ModuleFace) -> tuple[str, list[str]]:
    """Строка вида и варианты строки сверления (полный, короткий и без назначений), из граней отверстий:
    «вид с лица (+y)», «сверлить: с тыльной стороны (-y, обратная) — чашка петли»."""
    full: dict[str, list[str]] = {}
    short: dict[str, list[str]] = {}
    for h in part.holes or []:
        side = "видимая" if h.from_face == view else "обратная"
        if h.surface == "face":
            how = ", насквозь" if h.through else ""
            keys = (f"{_SIDE_RU[h.from_face]} ({h.from_face}, {side}{how})", f"{h.from_face} {side}{how}")
        else:
            keys = (f"в торец {h.edge_side} ({h.from_face})", f"торец {h.edge_side}")
        for groups, key in zip((full, short), keys, strict=True):
            names = groups.setdefault(key, [])
            if PURPOSE_RU[h.purpose] not in names:
                names.append(PURPOSE_RU[h.purpose])
    drill = ["сверлить: " + "; ".join(f"{k} — {', '.join(v)}" for k, v in g.items()) for g in (full, short)]
    drill.append("сверлить: " + "; ".join(short))  # назначения — в таблице отверстий
    return f"вид {_SIDE_RU[view]} ({view}), схема: {right}→, {up}↑", drill


def _where(h: Hole) -> str:
    if h.surface == "face":
        return f"пласть {h.from_face}" + (", сквозное" if h.through else "")
    return f"торец {h.edge_side} ({h.from_face}), {h.in_thickness} от пласти"


_ROW_MIN = 3  # столько отверстий подряд на одной линии таблица пишет одной строкой (тикет 20)


def _series(holes: list[Hole]) -> int | None:
    """Ось расстояний (0 или 1), вдоль которой идут отверстия, если всё остальное у них одинаково, иначе None."""
    a, rest = holes[0], holes[1:]
    if any(
        (h.purpose, _where(h), h.diameter, h.depth) != (a.purpose, _where(a), a.diameter, a.depth)
        for h in rest
    ):
        return None
    for i in (0, 1):
        same = all(h.from_base[1 - i] == a.from_base[1 - i] for h in rest)
        values = [h.from_base[i] for h in holes]
        if same and all(x < y for x, y in pairwise(values)):
            return i
    return None


def _span(values: list[Mm]) -> str:
    steps = {to_mm(Decimal(str(b)) - Decimal(str(a))) for a, b in pairwise(values)}
    if len(steps) == 1:
        return f"{values[0]}…{values[-1]} шаг {steps.pop()}"
    return ", ".join(str(v) for v in values)


def _distance(part: Part, h: Hole, i: int) -> str:
    """Расстояние от i-й базовой кромки. Чашка петли ближе к противоположной вертикальной кромке (правый фасад:
    22 от правой, а не 275 от левой) пишется от неё — решение мастера; в модели `from_base` не меняется."""
    value = h.from_base[i]
    face = part.base_edges[i] if part.base_edges else ""
    o = part.orientation
    if h.purpose == "hinge_cup" and face[1:] == "x" and "x" in (o.length, o.width):
        size = part.length if o.length == "x" else part.width
        if size - value < value:
            opposite = ("+" if face[0] == "-" else "-") + "x"
            return f"{to_mm(Decimal(str(size)) - Decimal(str(value)))} от {_FROM_RU[opposite]} кромки"
    return str(value)


def _rows(part: Part) -> list[str]:
    """Строки таблицы отверстий: по отверстию на строку; ряд из трёх и больше одинаковых отверстий подряд
    на одной линии (полкодержатели, направляющая) — одной строкой с номерами «11–15» и расстояниями. Ряд
    полкодержателей, сдвинутый у дна или крыши (отверстие под полку не посередине), называет его: «под полку …»."""
    assert part.holes and part.base_edges
    first, second = part.base_edges
    rows = [f"№  назначение · где · Ø×глубина · от {_FROM_RU[first]} · от {_FROM_RU[second]}"]
    holes, n = part.holes, 0
    while n < len(holes):
        end = n + 1
        while end < len(holes) and _series(holes[n : end + 1]) is not None:
            end += 1
        h = holes[n]
        number, base = f"{n + 1}", [_distance(part, h, i) for i in (0, 1)]
        axis = _series(holes[n:end]) if end - n >= _ROW_MIN else None
        if axis is None:
            end = n + 1
        else:
            number = f"{n + 1}–{end}"
            group = holes[n:end]
            base[axis] = _span([x.from_base[axis] for x in group])
            held = [i for i, x in enumerate(group) if x.holds_shelf]
            if held and 2 * held[0] != len(group) - 1:
                base.append(f"под полку {group[held[0]].from_base[axis]}")
        rows.append(
            f"{number}. {PURPOSE_RU[h.purpose]} · {_where(h)} · Ø{h.diameter}×{h.depth} · " + " · ".join(base)
        )
        n = end
    return rows


def _levels(values: list[Mm], k: float, size: float) -> list[int]:
    """Строка каждой подписи цепочки: близкие подписи уходят на следующую строку, чтобы не налезать."""
    levels: list[int] = []
    ends: list[float] = []  # дальний край последней подписи на каждой строке, пункты от базы
    for v in values:
        half = stringWidth(str(v), FONT, size) / 2 + 1
        at = v * k
        level = next((i for i, end in enumerate(ends) if at - half > end), len(ends))
        if level == len(ends):
            ends.append(at + half)
        else:
            ends[level] = at + half
        levels.append(level)
    return levels


def _grown_reserve(reserved: dict[str, int], need: dict[str, int]) -> dict[str, int]:
    """Запас строк цепочек по осям после очередного масштаба: ни одна ось не теряет запас, иначе подбор масштаба мог бы ходить по кругу."""
    return {a: max(reserved[a], need[a]) for a in reserved}


def _scheme(part: Part, slot: Area) -> SchemeLayout:
    """Раскладка одной схемы: строки, масштаб, контур, базовые линии, цепочки, отверстия, таблица."""
    assert part.holes and part.base_edges
    ext = part_extent(part)
    right, up, view = _view_faces(part)
    (h0, h1), (v0, v1) = ext[right[1]], ext[up[1]]
    width_mm, height_mm = h1 - h0, v1 - v0
    top = slot.y + slot.h
    texts: list[TextLine] = []
    size_line = f"{part.length}×{part.width}×{part.thickness} мм"
    title, title_size = _fit(
        [f"Деталь {part.id} — {part.name} {size_line}", f"Деталь {part.id} — {size_line}"],
        slot.w,
        _TITLE_SIZES,
        True,
        f"Заголовок детали {part.id}",
    )
    texts.append(_line(title, title_size, True, slot.x, top - _TITLE_SIZES[0]))
    bases = ", ".join(_EDGE_RU[f] for f in part.base_edges)
    sub, sub_size = _fit(
        [f"{part.material} · базовые кромки: {bases} (выделены)", f"базовые кромки: {bases} (выделены)"],
        slot.w,
        _TEXT_SIZES,
        False,
        f"Подзаголовок детали {part.id}",
    )
    texts.append(_line(sub, sub_size, False, slot.x, top - _TITLE_SIZES[0] - _LINE))
    seen_line, drill_lines = view_text(part, right, up, view)
    seen, seen_size = _fit([seen_line], slot.w, _TEXT_SIZES, False, f"Вид детали {part.id}")
    texts.append(_line(seen, seen_size, False, slot.x, top - _TITLE_SIZES[0] - 2 * _LINE))
    drill, drill_size = _fit(drill_lines, slot.w, _TEXT_SIZES, False, f"Сторона сверления детали {part.id}")
    texts.append(_line(drill, drill_size, False, slot.x, top - _TITLE_SIZES[0] - 3 * _LINE))
    rows = _rows(part)
    table_h = len(rows) * _LINE
    by_axis = {f[1]: f for f in part.base_edges}

    def values(face: str) -> list[Mm]:
        i = part.base_edges.index(face)  # type: ignore[union-attr]
        return sorted({h.from_base[i] for h in part.holes or []})

    axes = [a for a in (right[1], up[1]) if a in by_axis]
    # подпись у края контура выходит за него на половину своей ширины: справа оставляем под неё место
    edge = max(
        [2 * mm]
        + [stringWidth(str(v), FONT, _TEXT_SIZES[0]) / 2 + 1 for a in axes for v in values(by_axis[a])]
    )
    # заголовок, подзаголовок, вид, сверление и отступ над схемой — под половину подписи вертикальной цепочки
    head = 4 * _LINE + edge

    def rows_at(k: float) -> dict[str, int]:
        return {a: max(_levels(values(by_axis[a]), k, _TEXT_SIZES[0]), default=-1) + 1 for a in axes}

    # Масштаб зависит от места под строки цепочек, а строки — от масштаба (мельче — подписи ближе).
    # Уменьшаем масштаб, пока строк при нём не больше, чем под них отведено: подписи не налезают.
    k, reserved = 0.0, rows_at(1.0)
    ok = False
    for _ in range(20):
        below = (reserved[right[1]] + 1) * _DIM_ROW if right[1] in reserved else 0
        left = (reserved[up[1]] + 1) * _DIM_ROW + 4 * mm if up[1] in reserved else 0
        avail_w = slot.w - left - edge
        avail_h = slot.h - head - below - table_h - 4 * mm
        if avail_w <= 0 or avail_h <= 10 * mm:
            break
        k = min(avail_w / width_mm, avail_h / height_mm)
        need = rows_at(k)
        if all(need[a] <= reserved[a] for a in axes):
            ok = True
            break
        reserved = _grown_reserve(reserved, need)
    if not ok:
        raise KitchenError(
            f"Схема детали {part.id} не помещается на карте присадки: отверстий {len(part.holes)}."
        )
    ox = slot.x + left
    oy = top - head - height_mm * k  # левый нижний угол контура

    def px(value: Mm) -> float:
        return ox + ((value - h0) if right[0] == "+" else (h1 - value)) * k

    def py(value: Mm) -> float:
        return oy + ((value - v0) if up[0] == "+" else (v1 - value)) * k

    x0, x1, y0, y1 = ox, ox + width_mm * k, oy, oy + height_mm * k
    base_lines = []
    for face in part.base_edges:
        lo, hi = ext[face[1]]
        edge = lo if face[0] == "-" else hi
        if face[1] == right[1]:
            x = px(edge)
            base_lines.append(BaseLine(face=face, x1=x, y1=y0, x2=x, y2=y1))
        else:
            y = py(edge)
            base_lines.append(BaseLine(face=face, x1=x0, y1=y, x2=x1, y2=y))
    chains = []
    for axis in axes:
        face = by_axis[axis]
        lo, hi = ext[axis]
        horizontal = axis == right[1]
        to_screen = px if horizontal else py
        base = to_screen(lo if face[0] == "-" else hi)
        step = to_screen(lo + 1) - to_screen(lo)  # знак: куда растёт координата модуля на схеме
        away = step if face[0] == "-" else -step  # от базовой кромки внутрь детали
        vals = values(face)
        line = oy - _DIM_ROW if horizontal else ox - 2 * mm
        labels = []
        for value, level in zip(vals, _levels(vals, k, _TEXT_SIZES[0]), strict=True):
            tick = base + value * away
            w = stringWidth(str(value), FONT, _TEXT_SIZES[0])
            size = _TEXT_SIZES[0]
            if horizontal:
                baseline = line - (level + 1) * _DIM_ROW + 1
                box = (tick - w / 2, baseline - 0.25 * size, tick + w / 2, baseline + 0.75 * size)
            else:
                baseline = line - (level + 1) * _DIM_ROW + 1
                box = (baseline - 0.75 * size, tick - w / 2, baseline + 0.25 * size, tick + w / 2)
            labels.append(ChainLabel(value=value, tick=tick, box=box))
        chains.append(
            DimChain(
                face=face,
                direction="horizontal" if horizontal else "vertical",
                base=base,
                line=line,
                labels=labels,
            )
        )
    holes = []
    for h in part.holes:
        c = h.center.model_dump()
        x, y = px(c[right[1]]), py(c[up[1]])
        if h.surface == "face":
            holes.append(
                HoleMark(
                    purpose=h.purpose, surface="face", through=h.through, x=x, y=y,
                    r=max(h.diameter / 2 * k, _MIN_R), x2=x, y2=y,
                )
            )  # fmt: skip
        else:  # отверстие в торец — отрезок вглубь детали от кромки
            length = max(h.depth * k, 1.5 * mm)
            mx, my = px(c[right[1]] + (-1 if h.from_face[0] == "+" else 1)), py(c[up[1]])
            if h.from_face[1] == right[1]:
                direction = 1 if mx > x else -1
                end = (x + direction * length, y)
            else:
                my = py(c[up[1]] + (-1 if h.from_face[0] == "+" else 1))
                direction = 1 if my > y else -1
                end = (x, y + direction * length)
            holes.append(
                HoleMark(
                    purpose=h.purpose,
                    surface="end",
                    through=False,
                    x=x,
                    y=y,
                    r=_MIN_R * 0.8,
                    x2=end[0],
                    y2=end[1],
                )
            )
    table_top = oy - below - 2 * mm
    widest = max(rows, key=lambda r: stringWidth(r, FONT_BOLD, _TEXT_SIZES[0]))
    _, table_size = _fit([widest], slot.w, _TEXT_SIZES, True, f"Строка таблицы отверстий детали {part.id}")
    for i, row in enumerate(rows):
        texts.append(_line(row, table_size, i == 0, slot.x, table_top - (i + 1) * _LINE))
    # `avail_h` уже вычел высоту таблицы, так что строки таблицы лежат в ячейке
    assert texts[-1].y >= slot.y, f"Таблица отверстий детали {part.id} вышла из ячейки: строк {len(rows)}"
    return SchemeLayout(
        part=part.id,
        slot=(slot.x, slot.y, slot.x + slot.w, slot.y + slot.h),
        outline=(x0, y0, x1, y1),
        scale=k,
        right=right,
        up=up,
        view=view,
        texts=texts,
        base_lines=base_lines,
        chains=chains,
        holes=holes,
    )


def _summary(checks: list[DrillingCheck]) -> list[str]:
    full = "Сверка с фурнитурой: " + "; ".join(f"{c.name} {c.holes} = {c.expected}" for c in checks)
    short = "Сверка: " + "; ".join(f"{c.short} {c.holes}={c.expected}" for c in checks)
    return [full, short]


def map_layout(parts: list[Part], checks: list[DrillingCheck]) -> list[PageLayout]:
    """Раскладка карты: страницы по 2–4 детали (сетка 2×2) в порядке проекта; без деталей — одна страница."""
    register_fonts()
    area = page_area()
    cell_w = (area.w - _GAP) / 2
    cell_h = (area.h - _HEADER_H - _GAP) / 2
    top = area.y + area.h
    summary, summary_size = _fit(_summary(checks), area.w, _TEXT_SIZES, False, "Строка сверки")
    header = [
        _line("Карта присадки", 13, True, area.x, top - 13),
        _line(summary, summary_size, False, area.x, top - 13 - _LINE - 1),
    ]
    if not parts:
        header.append(
            _line("Деталей с отверстиями нет.", _TEXT_SIZES[0], False, area.x, top - _HEADER_H - _LINE)
        )
    pages, start = [], 0
    for size in page_sizes(len(parts)) or [0]:
        schemes = []
        for i, part in enumerate(parts[start : start + size]):
            col, row = i % 2, i // 2
            slot = Area(
                area.x + col * (cell_w + _GAP),
                top - _HEADER_H - (row + 1) * cell_h - row * _GAP,
                cell_w,
                cell_h,
            )
            schemes.append(_scheme(part, slot))
        start += size
        pages.append(PageLayout(header=header, schemes=schemes))
    return pages


def _draw_text(canvas: Canvas, line: TextLine) -> None:
    canvas.setFont(FONT_BOLD if line.bold else FONT, line.size)
    canvas.drawString(line.x, line.y, line.text)


def _draw_scheme(canvas: Canvas, s: SchemeLayout) -> None:
    canvas.setFillColorRGB(0, 0, 0)
    for line in s.texts:
        _draw_text(canvas, line)
    x0, y0, x1, y1 = s.outline
    canvas.setStrokeColorRGB(0, 0, 0)
    canvas.setLineWidth(0.6)
    canvas.rect(x0, y0, x1 - x0, y1 - y0)
    canvas.setStrokeColorRGB(*_BASE_COLOR)
    canvas.setLineWidth(2.2)
    for b in s.base_lines:
        canvas.line(b.x1, b.y1, b.x2, b.y2)
    for h in s.holes:
        canvas.setStrokeColorRGB(*_COLORS[h.purpose])
        canvas.setFillColorRGB(*_COLORS[h.purpose])
        canvas.setLineWidth(0.5)
        if h.surface == "end":
            canvas.line(h.x, h.y, h.x2, h.y2)
        canvas.circle(h.x, h.y, h.r, stroke=1, fill=1 if h.through or h.surface == "end" else 0)
    canvas.setStrokeColorRGB(0, 0, 0)
    canvas.setFillColorRGB(0, 0, 0)
    canvas.setLineWidth(0.3)
    canvas.setFont(FONT, _TEXT_SIZES[0])
    for ch in s.chains:
        if ch.direction == "horizontal":
            canvas.line(x0, ch.line, x1, ch.line)
            for lb in ch.labels:
                canvas.line(lb.tick, ch.line - 1, lb.tick, ch.line + 1)
                canvas.drawCentredString(lb.tick, lb.box[1] + 0.25 * _TEXT_SIZES[0], str(lb.value))
        else:
            canvas.line(ch.line, y0, ch.line, y1)
            for lb in ch.labels:
                canvas.line(ch.line - 1, lb.tick, ch.line + 1, lb.tick)
                canvas.saveState()
                canvas.translate(lb.box[2] - 0.25 * _TEXT_SIZES[0], lb.tick)
                canvas.rotate(90)
                canvas.drawCentredString(0, 0, str(lb.value))
                canvas.restoreState()


def write_drilling_map(
    path: Path,
    parts: list[Part],
    checks: list[DrillingCheck],
    *,
    order: str,
    version: int | None,
    mock: bool,
    on: date,
) -> None:
    """PDF по раскладке `map_layout`. Отверстие на схеме и в таблице — те же числа, что в `parts`."""
    doc = PdfDocument(path, order=order, version=version, mock=mock, on=on, title=f"Карта присадки {order}")
    for page in map_layout(parts, checks):

        def draw(canvas: Canvas, a: Area, page=page) -> None:
            for line in page.header:
                _draw_text(canvas, line)
            for scheme in page.schemes:
                _draw_scheme(canvas, scheme)

        doc.add_page(draw)
    doc.save()

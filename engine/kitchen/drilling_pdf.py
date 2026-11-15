"""Карта присадки PDF (4.2): на деталь с отверстиями — схема в масштабе, отверстия, цепочки размеров от
базовых кромок (выделены), таблица отверстий. 2–4 схемы на страницу, каждая деталь своей схемой."""

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas

from kitchen.drilling import DrillingCheck, part_extent
from kitchen.errors import KitchenError
from kitchen.parts import PURPOSE_RU, Hole, Part
from kitchen.pdf import FONT, FONT_BOLD, Area, PdfDocument, mm, page_area

__all__ = ["page_sizes", "write_drilling_map"]

PER_PAGE = 4
_HEADER_H = 12 * mm
_GAP = 4 * mm
_TITLE = 7.5
_TEXT = 6.0
_LINE = 2.7 * mm
_DIM_ROW = 3.0 * mm  # строка цепочки размеров
_MIN_R = 0.7 * mm  # отверстие на схеме не меньше
_EDGE_RU = {"-z": "нижняя", "+z": "верхняя", "+y": "передняя", "-y": "задняя", "-x": "левая", "+x": "правая"}
_FROM_RU = {"-z": "нижней", "+z": "верхней", "+y": "передней", "-y": "задней", "-x": "левой", "+x": "правой"}
_COLORS = {"confirmat": (0.1, 0.3, 0.8), "hinge_cup": (0.8, 0.1, 0.1), "hinge_plate": (0.1, 0.55, 0.2)}
_BASE_COLOR = (0.85, 0.35, 0.0)


def page_sizes(count: int) -> list[int]:
    """Сколько схем на каждой странице: не больше четырёх и, если деталей больше одной, не меньше двух
    (5 → 3 + 2, 9 → 3 + 3 + 3)."""
    pages = max(-(-count // PER_PAGE), 1)
    return [count // pages + (1 if i < count % pages else 0) for i in range(pages)] if count else []


def _view_axes(part: Part) -> tuple[str, str]:
    """Оси схемы: вправо — x модуля, если она в плоскости детали, иначе y; вверх — z, если в плоскости, иначе y."""
    plane = {part.orientation.length, part.orientation.width}
    right = "x" if "x" in plane else "y"
    up = "z" if "z" in plane else "y"
    return right, up


def _where(h: Hole) -> str:
    if h.surface == "face":
        return f"пласть {h.from_face}" + (", сквозное" if h.through else "")
    return f"торец {h.edge_side} ({h.from_face}), {h.in_thickness} от пласти"


def _rows(part: Part) -> list[str]:
    assert part.holes and part.base_edges
    first, second = part.base_edges
    rows = [f"№  назначение · где · Ø×глубина · от {_FROM_RU[first]} · от {_FROM_RU[second]}"]
    for n, h in enumerate(part.holes, 1):
        rows.append(
            f"{n}. {PURPOSE_RU[h.purpose]} · {_where(h)} · Ø{h.diameter}×{h.depth} · "
            f"{h.from_base[0]} · {h.from_base[1]}"
        )
    return rows


@dataclass(frozen=True)
class _Chain:
    """Цепочка размеров от базовой кромки вдоль оси схемы: значения и строки подписей."""

    face: str
    values: list[int]
    levels: list[int]  # строка каждой подписи: соседние близкие подписи уходят на следующую строку

    @property
    def rows(self) -> int:
        return max(self.levels, default=-1) + 1


def _chain(face: str, values: list[int], k: float) -> _Chain:
    ordered = sorted(set(values))
    levels: list[int] = []
    ends: list[float] = []  # правый край последней подписи на каждой строке, пункты
    for v in ordered:
        half = stringWidth(str(v), FONT, _TEXT) / 2 + 1
        at = v * k
        level = next((i for i, end in enumerate(ends) if at - half > end), len(ends))
        if level == len(ends):
            ends.append(at + half)
        else:
            ends[level] = at + half
        levels.append(level)
    return _Chain(face, ordered, levels)


def _draw_part(canvas: Canvas, slot: Area, part: Part) -> None:
    """Схема одной детали в своей ячейке: заголовок, контур, отверстия, цепочки, таблица отверстий."""
    assert part.holes and part.base_edges
    ext = part_extent(part)
    right, up = _view_axes(part)
    (h0, h1), (v0, v1) = ext[right], ext[up]
    width_mm, height_mm = h1 - h0, v1 - v0
    rows = _rows(part)
    top = slot.y + slot.h
    canvas.setFillColorRGB(0, 0, 0)
    canvas.setFont(FONT_BOLD, _TITLE)
    canvas.drawString(
        slot.x, top - _TITLE, f"Деталь {part.id} — {part.name} {part.length}×{part.width}×{part.thickness} мм"
    )
    canvas.setFont(FONT, _TEXT)
    bases = ", ".join(_EDGE_RU[f] for f in part.base_edges)
    canvas.drawString(
        slot.x,
        top - _TITLE - _LINE,
        f"{part.material} · базовые кромки: {bases} (выделены) · схема: {right}→, {up}↑",
    )
    table_h = len(rows) * _LINE
    k_guess = 1.0
    # цепочки по базовым кромкам, лежащим на осях схемы: горизонтальная под схемой, вертикальная слева
    by_axis = {f[1]: f for f in part.base_edges}

    def values(face: str) -> list[int]:
        i = part.base_edges.index(face)  # type: ignore[union-attr]
        return [h.from_base[i] for h in part.holes or []]

    def layout(k: float) -> dict[str, _Chain]:
        return {a: _chain(by_axis[a], values(by_axis[a]), k) for a in (right, up) if a in by_axis}

    # Масштаб зависит от места под строки цепочек, а строки — от масштаба (мельче — подписи ближе).
    # Уменьшаем масштаб, пока строк при нём не больше, чем под них отведено: подписи не налезают.
    chains = layout(k_guess)
    for _ in range(20):
        below = (chains[right].rows + 1) * _DIM_ROW if right in chains else 0
        left = (chains[up].rows + 1) * _DIM_ROW + 4 * mm if up in chains else 0
        avail_w = slot.w - left - 2 * mm
        avail_h = slot.h - _TITLE - 2 * _LINE - below - table_h - 6 * mm  # отступы над схемой и над таблицей
        if avail_w <= 0 or avail_h <= 10 * mm:
            break
        k_guess = min(avail_w / width_mm, avail_h / height_mm)
        fitted = layout(k_guess)
        if all(fitted[a].rows <= chains[a].rows for a in chains):
            chains = fitted
            break
        chains = fitted
    else:
        avail_h = 0
    if avail_w <= 0 or avail_h <= 10 * mm:
        raise KitchenError(
            f"Схема детали {part.id} не помещается на карте присадки: отверстий {len(part.holes)}."
        )
    k = k_guess
    ox = slot.x + left
    oy = top - _TITLE - 2 * _LINE - 2 * mm - height_mm * k  # левый нижний угол контура

    def px(value: int) -> float:
        return ox + (value - h0) * k

    def py(value: int) -> float:
        return oy + (value - v0) * k

    canvas.setStrokeColorRGB(0, 0, 0)
    canvas.setLineWidth(0.6)
    canvas.rect(ox, oy, width_mm * k, height_mm * k)
    # базовые кромки — толстой цветной линией
    canvas.setStrokeColorRGB(*_BASE_COLOR)
    canvas.setLineWidth(2.2)
    for face in part.base_edges:
        axis, at_lo = face[1], face[0] == "-"
        if axis == right:
            x = px(h0 if at_lo else h1)
            canvas.line(x, py(v0), x, py(v1))
        else:
            y = py(v0 if at_lo else v1)
            canvas.line(px(h0), y, px(h1), y)
    for h in part.holes:
        c = h.center.model_dump()
        canvas.setStrokeColorRGB(*_COLORS[h.purpose])
        canvas.setFillColorRGB(*_COLORS[h.purpose])
        canvas.setLineWidth(0.5)
        axis = h.from_face[1]
        if h.surface == "face":
            r = max(h.diameter / 2 * k, _MIN_R)
            canvas.circle(px(c[right]), py(c[up]), r, stroke=1, fill=1 if h.through else 0)
        else:  # отверстие в торец — отрезок вглубь детали от кромки
            inward = -1 if h.from_face[0] == "+" else 1
            length = max(h.depth * k, 1.5 * mm)
            x, y = px(c[right]), py(c[up])
            if axis == right:
                canvas.line(x, y, x + inward * length, y)
            else:
                canvas.line(x, y, x, y + inward * length)
            canvas.circle(x, y, _MIN_R * 0.8, stroke=1, fill=1)
    canvas.setStrokeColorRGB(0, 0, 0)
    canvas.setFillColorRGB(0, 0, 0)
    canvas.setFont(FONT, _TEXT)
    canvas.setLineWidth(0.3)
    if right in chains:
        ch = chains[right]
        base_x = px(h0 if ch.face[0] == "-" else h1)
        y_line = oy - _DIM_ROW
        canvas.line(px(h0), y_line, px(h1), y_line)
        for value, level in zip(ch.values, ch.levels, strict=True):
            x = base_x + value * k if ch.face[0] == "-" else base_x - value * k
            canvas.line(x, y_line - 1, x, y_line + 1)
            canvas.drawCentredString(x, y_line - (level + 1) * _DIM_ROW + 1, str(value))
    if up in chains:
        ch = chains[up]
        base_y = py(v0 if ch.face[0] == "-" else v1)
        x_line = ox - 2 * mm
        canvas.line(x_line, py(v0), x_line, py(v1))
        for value, level in zip(ch.values, ch.levels, strict=True):
            y = base_y + value * k if ch.face[0] == "-" else base_y - value * k
            canvas.line(x_line - 1, y, x_line + 1, y)
            canvas.saveState()
            canvas.translate(x_line - (level + 1) * _DIM_ROW + 1, y)
            canvas.rotate(90)
            canvas.drawCentredString(0, 0, str(value))
            canvas.restoreState()
    table_top = oy - below - 2 * mm
    for i, row in enumerate(rows):
        canvas.setFont(FONT_BOLD if i == 0 else FONT, _TEXT)
        if stringWidth(row, FONT_BOLD if i == 0 else FONT, _TEXT) > slot.w:
            raise KitchenError(f"Строка таблицы отверстий детали {part.id} не помещается в схему.")
        canvas.drawString(slot.x, table_top - (i + 1) * _LINE, row)


def _summary(checks: list[DrillingCheck]) -> str:
    return "Сверка с фурнитурой: " + "; ".join(f"{c.name} {c.holes} = {c.expected}" for c in checks)


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
    """Страницы по 2–4 детали (сетка 2×2), детали в порядке проекта; без деталей с отверстиями — одна страница
    с пометкой. Отверстие на схеме и в таблице — те же числа, что в `parts`."""
    doc = PdfDocument(path, order=order, version=version, mock=mock, on=on, title=f"Карта присадки {order}")
    area = page_area()
    cell_w = (area.w - _GAP) / 2
    cell_h = (area.h - _HEADER_H - _GAP) / 2
    sizes = page_sizes(len(parts)) or [0]
    start = 0
    for size in sizes:
        chunk = parts[start : start + size]
        start += size

        def draw(canvas: Canvas, a: Area, chunk=chunk) -> None:
            canvas.setFont(FONT_BOLD, 13)
            canvas.drawString(a.x, a.y + a.h - 13, "Карта присадки")
            canvas.setFont(FONT, _TEXT)
            canvas.drawString(a.x, a.y + a.h - 13 - _LINE - 1, _summary(checks))
            if not chunk:
                canvas.drawString(a.x, a.y + a.h - _HEADER_H - _LINE, "Деталей с отверстиями нет.")
            for i, part in enumerate(chunk):
                col, row = i % 2, i // 2
                slot = Area(
                    a.x + col * (cell_w + _GAP),
                    a.y + a.h - _HEADER_H - (row + 1) * cell_h - row * _GAP,
                    cell_w,
                    cell_h,
                )
                _draw_part(canvas, slot, part)

        doc.add_page(draw)
    doc.save()

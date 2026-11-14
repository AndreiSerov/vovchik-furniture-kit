"""Карта раскроя PDF (2.2): страница на лист — схема в масштабе, список деталей и резов, использование."""

import heapq
import itertools
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas

from kitchen.cutting import Cut, MaterialCut, Placement, SheetCut
from kitchen.errors import KitchenError
from kitchen.pdf import FONT, FONT_BOLD, Area, PdfDocument, mm, page_area, register_fonts

__all__ = ["PartLabel", "place_lines", "scheme_labels", "scheme_scale", "write_cutting_map"]

_SCHEME_MAX_H = 150 * mm
_LINE = 3.3 * mm  # шаг строк при кегле _FONTS[0]
_COL_GAP = 3 * mm
# Кегль списков: пробуем от крупного к мелкому; мельче MIN_FONT не уходим, а переносим на страницу-продолжение.
_FONTS = (6.5, 6.0, 5.5)
MIN_FONT = _FONTS[-1]
_CONT_HEADER_H = 16 * mm  # шапка страницы-продолжения
_LABEL_FONT = 6
_LABEL_H = 7.0
_LABEL_GAP = 2.0


@dataclass(frozen=True)
class Placed:
    """Строка списка, поставленная на страницу: левый нижний угол текста, пункты."""

    text: str
    bold: bool
    x: float
    y: float
    size: float


def _cut_line(c: Cut) -> str:
    axis = "верт., x" if c.direction == "vertical" else "гориз., y"
    what = ", обрезка" if c.kind == "trim" else ""
    return f"Рез {c.number}: {axis}={c.position}, {c.start}…{c.end}, {c.length} мм{what}"


def _part_line(n: int, pl: Placement) -> str:
    turn = ", повёрнута" if pl.rotated else ""
    return f"{n}. {pl.part} {pl.w}×{pl.h} мм, x={pl.x} y={pl.y}{turn}"


def place_lines(lines: list[tuple[str, bool]], area: Area, first_top: float) -> list[list[Placed]]:
    """Строки (текст, жирная?) потоком по колонкам; ширина колонки — по самой длинной строке (`stringWidth`).

    Первая страница начинается под `first_top` (под схемой), следующие — под шапкой продолжения. Кегль не
    меньше MIN_FONT: не поместилось — продолжение на следующей странице."""
    register_fonts()
    first_h = first_top - area.y
    next_h = area.h - _CONT_HEADER_H
    chosen = _FONTS[-1]
    for size in _FONTS:
        cols, per = _grid(lines, size, area.w, first_h)
        if cols * per >= len(lines):
            chosen = size
            break
    cols, per_first = _grid(lines, chosen, area.w, first_h)
    _, per_next = _grid(lines, chosen, area.w, next_h)
    step = _LINE * chosen / _FONTS[0]
    width = area.w / cols
    pages: list[list[Placed]] = []
    i = 0
    while i < len(lines) or not pages:
        top, per = (first_top, per_first) if not pages else (area.y + next_h, per_next)
        page = []
        for k, (text, bold) in enumerate(lines[i : i + cols * per]):
            col, row = divmod(k, per)
            page.append(Placed(text, bold, area.x + col * width, top - (row + 1) * step, chosen))
        pages.append(page)
        i += max(cols * per, 1)
    return pages


def _grid(lines: list[tuple[str, bool]], size: float, width: float, height: float) -> tuple[int, int]:
    """Число колонок (по ширине самой длинной строки) и строк в колонке для кегля `size`."""
    widest = max((stringWidth(t, FONT_BOLD if b else FONT, size) for t, b in lines), default=0)
    cols = max(1, int(width // (widest + _COL_GAP)))
    return cols, max(1, int(height // (_LINE * size / _FONTS[0])))


Box = tuple[
    float, float, float, float
]  # x0, y0, x1, y1 в координатах схемы: пункты от левого верхнего угла листа


@dataclass(frozen=True)
class PartLabel:
    """Подпись детали на схеме: что написано, кегль, вертикально ли, и её прямоугольник (центр — центр детали)."""

    n: int
    text: str
    size: float
    vertical: bool
    box: Box


def scheme_scale(width: float, sheet_size: tuple[int, int]) -> float:
    """Масштаб схемы, пунктов на мм: одна функция для рисования и для тестов."""
    return min(width / sheet_size[0], _SCHEME_MAX_H / sheet_size[1])


def _overlap(a: Box, b: Box) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def _pad(box: Box, amount: float) -> Box:
    return box[0] - amount, box[1] - amount, box[2] + amount, box[3] + amount


def _label_levels(pl: Placement, n: int, k: float) -> list[PartLabel]:
    """Варианты подписи детали от полного к короткому: «маркировка размеры» → «маркировка» → номер.
    Вариант есть в списке, только если помещается внутри детали (по горизонтали, затем вертикально, кегль
    от 6.5 до 4 пт); последний (номер) есть всегда: не поместился — рисуется мелко (4 пт) как есть."""
    x, y, w, h = pl.x * k, pl.y * k, pl.w * k, pl.h * k
    cx, cy = x + w / 2, y + h / 2
    levels: list[PartLabel] = []
    texts = [f"{pl.part} {pl.w}×{pl.h}", pl.part, str(n)]
    for i, text in enumerate(texts):
        found = None
        for size in (6.5, 6, 5.5, 5, 4.5, 4):
            tw = stringWidth(text, FONT, size)
            for vertical in (False, True):
                room, across = (h, w) if vertical else (w, h)
                if tw <= room - 2 and size <= across - 1:
                    found = (size, vertical)
                    break
            if found:
                break
        if found is None and i == len(texts) - 1:
            found = (4.0, w < h and w < 8)
        if found:
            size, vertical = found
            tw = stringWidth(text, FONT, size)
            bw, bh = (size, tw) if vertical else (tw, size)
            levels.append(
                PartLabel(n, text, size, vertical, (cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2))
            )
    return levels


def scheme_labels(
    sheet_size: tuple[int, int], s: SheetCut, kerf: int, k: float
) -> tuple[list[PartLabel], list[tuple[int, Box]]]:
    """Подписи схемы без наложений: подписи деталей и рамки номеров резов (номер, прямоугольник).

    Подписи деталей берутся самыми полными. Номер реза ставится у середины линии, затем сдвигается вдоль
    линии и на шаг в сторону, чтобы не закрыть ни одну подпись и ни один другой номер. Не нашлось места —
    подписи деталей под номером укорачиваются (размеры → маркировка → номер) и номер ставится у линии;
    подпись детали не бывает закрыта частично: она целиком видна или заменена более короткой."""
    register_fonts()
    levels = {n: _label_levels(pl, n, k) for n, pl in enumerate(s.placements, 1)}
    level = dict.fromkeys(levels, 0)
    boxes: list[tuple[int, Box]] = []

    def part_box(n: int, lvl: int | None = None) -> Box:
        return levels[n][level[n] if lvl is None else lvl].box

    def free(box: Box) -> bool:
        return (
            inside(box)
            and all(not _overlap(_pad(box, _LABEL_GAP), b) for _, b in boxes)
            and all(not _overlap(_pad(box, _LABEL_GAP), part_box(n)) for n in levels)
        )

    def inside(box: Box) -> bool:
        return 0 <= box[0] and box[2] <= sheet_size[0] * k and 0 <= box[1] and box[3] <= sheet_size[1] * k

    def downgrade_plan(box: Box) -> dict[int, int] | None:
        """Какие подписи деталей нужно укоротить, чтобы рамка встала; `None` — даже номером не обойтись."""
        if not inside(box) or any(_overlap(_pad(box, _LABEL_GAP), b) for _, b in boxes):
            return None
        plan: dict[int, int] = {}
        for n, options in levels.items():
            lvl = level[n]
            while _overlap(_pad(box, _LABEL_GAP), part_box(n, lvl)):
                lvl += 1
                if lvl >= len(options):
                    return None
            if lvl != level[n]:
                plan[n] = lvl
        return plan

    for c in s.cuts:
        w = stringWidth(str(c.number), FONT_BOLD, _LABEL_FONT) + 2
        across = (c.position + kerf / 2) * k  # координата линии
        lo, hi = c.start * k, c.end * k
        mid = (lo + hi) / 2

        def candidates(shifts, c=c, w=w, across=across, lo=lo, hi=hi, mid=mid):
            for shift in shifts:
                for j in range(int((hi - lo) // _LABEL_H) + 1):
                    for off in dict.fromkeys((0.0, j * _LABEL_H, -j * _LABEL_H)):
                        at = mid + off
                        if hi - lo >= _LABEL_H and (
                            at - _LABEL_H / 2 < lo - 0.01 or at + _LABEL_H / 2 > hi + 0.01
                        ):
                            continue
                        if c.direction == "vertical":
                            yield (
                                across + shift - w / 2,
                                at - _LABEL_H / 2,
                                across + shift + w / 2,
                                at + _LABEL_H / 2,
                            )
                        else:
                            yield (
                                at - w / 2,
                                across + shift - _LABEL_H / 2,
                                at + w / 2,
                                across + shift + _LABEL_H / 2,
                            )

        near = (0, _LABEL_H, -_LABEL_H)
        wide = (0, *(sgn * j * _LABEL_H for j in range(1, 40) for sgn in (1, -1)))
        chosen = next((b for b in candidates(near) if free(b)), None)
        if chosen is None:  # места нет: укорачиваем подписи деталей под рамкой, у самой линии
            for b in candidates((0,)):
                plan = downgrade_plan(b)
                if plan is not None:
                    level.update(plan)
                    chosen = b
                    break
        if chosen is None:  # короткие соседние резы: ставим номер за концом линии с выноской

            def beyond(c=c, w=w, across=across, lo=lo, hi=hi):
                for j in range(80):
                    for at in (hi + _LABEL_H / 2 + j * _LABEL_H, lo - _LABEL_H / 2 - j * _LABEL_H):
                        if c.direction == "vertical":
                            yield across - w / 2, at - _LABEL_H / 2, across + w / 2, at + _LABEL_H / 2
                        else:
                            yield at - w / 2, across - _LABEL_H / 2, at + w / 2, across + _LABEL_H / 2

            chosen = next((b for b in beyond() if free(b)), None)
        if chosen is None:  # совсем тесно: рядом с линией, но свободно
            chosen = next((b for b in candidates(wide) if free(b)), None)
        if chosen is None:  # ищем свободное место по всему листу, с выноской к резу
            max_x, max_y = sheet_size[0] * k - w, sheet_size[1] * k - _LABEL_H
            chosen = next(
                (
                    (x, y, x + w, y + _LABEL_H)
                    for y in range(0, max(0, int(max_y)) + 1, 7)
                    for x in range(0, max(0, int(max_x)) + 1, 7)
                    if free((x, y, x + w, y + _LABEL_H))
                ),
                None,
            )
        if chosen is None:
            raise KitchenError("Не удалось разместить номера резов на схеме листа без наложений.")
        boxes.append((c.number, chosen))
    return [levels[n][level[n]] for n in levels], boxes


def _leader(c: Cut, box: Box, kerf: int, k: float) -> tuple[float, float, float, float] | None:
    """От центра рамки к ближайшей точке реза; когда рез проходит через рамку, выноска не нужна."""
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    across, lo, hi = (c.position + kerf / 2) * k, c.start * k, c.end * k
    if c.direction == "vertical":
        if x0 <= across <= x1 and y0 <= hi and y1 >= lo:
            return None
        return cx, cy, across, min(max(cy, lo), hi)
    if y0 <= across <= y1 and x0 <= hi and x1 >= lo:
        return None
    return cx, cy, min(max(cx, lo), hi), across


def _leader_path(
    c: Cut, box: Box, kerf: int, k: float, obstacles: list[Box], bounds: tuple[float, float]
) -> list[tuple[float, float]] | None:
    """Выноска по свободным коридорам, не проходящая сквозь чужие подписи."""
    direct = _leader(c, box, kerf, k)
    if direct is None:
        return None
    sx, sy, tx, ty = direct
    width, height = bounds
    obstacles = [_pad(b, 0.7) for b in obstacles]

    def clear(a: tuple[float, float], b: tuple[float, float]) -> bool:
        x0, y0 = a
        x1, y1 = b
        if x0 == x1:
            return all(
                not (left < x0 < right and max(min(y0, y1), top) < min(max(y0, y1), bottom))
                for left, top, right, bottom in obstacles
            )
        return all(
            not (top < y0 < bottom and max(min(x0, x1), left) < min(max(x0, x1), right))
            for left, top, right, bottom in obstacles
        )

    start, end = (sx, sy), (tx, ty)

    def point_free(p: tuple[float, float]) -> bool:
        return all(
            not (left < p[0] < right and top < p[1] < bottom) for left, top, right, bottom in obstacles
        )

    for corner in ((tx, sy), (sx, ty)):
        if point_free(end) and clear(start, corner) and clear(corner, end):
            return [start, corner, end]

    # Граница каждой подписи задаёт возможный свободный коридор. Поиск по этой сетке
    # находит обход без привязки к разрешению изображения и не выводит линию за лист.
    lo, hi = c.start * k, c.end * k
    xs = sorted(
        {
            0.0,
            width,
            sx,
            tx,
            *(v for v in (lo, hi) if c.direction == "horizontal" and 0 <= v <= width),
            *(v for left, _, right, _ in obstacles for v in (left, right) if 0 <= v <= width),
        }
    )
    ys = sorted(
        {
            0.0,
            height,
            sy,
            ty,
            *(v for v in (lo, hi) if c.direction == "vertical" and 0 <= v <= height),
            *(v for _, top, _, bottom in obstacles for v in (top, bottom) if 0 <= v <= height),
        }
    )
    origin = (xs.index(sx), ys.index(sy))
    queue = [(0.0, 0.0, origin)]
    distance = {origin: 0.0}
    parent: dict[tuple[int, int], tuple[int, int]] = {}
    while queue:
        _, cost, node = heapq.heappop(queue)
        if cost != distance[node]:
            continue
        point = (xs[node[0]], ys[node[1]])
        if point_free(point) and (
            (c.direction == "vertical" and point[0] == tx and lo <= point[1] <= hi)
            or (c.direction == "horizontal" and point[1] == ty and lo <= point[0] <= hi)
        ):
            path = []
            while node != origin:
                path.append((xs[node[0]], ys[node[1]]))
                node = parent[node]
            return [start, *reversed(path)]
        x, y = node
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if not (0 <= nx < len(xs) and 0 <= ny < len(ys)):
                continue
            a, b = (xs[x], ys[y]), (xs[nx], ys[ny])
            next_cost = cost + abs(a[0] - b[0]) + abs(a[1] - b[1])
            neighbour = (nx, ny)
            if next_cost < distance.get(neighbour, float("inf")) and clear(a, b):
                distance[neighbour] = next_cost
                parent[neighbour] = node
                if c.direction == "vertical":
                    heuristic = abs(b[0] - tx) + max(lo - b[1], 0, b[1] - hi)
                else:
                    heuristic = abs(b[1] - ty) + max(lo - b[0], 0, b[0] - hi)
                heapq.heappush(queue, (next_cost + heuristic, next_cost, neighbour))
    raise KitchenError(f"Не удалось провести выноску к резу {c.number} без наложений.")


def _scheme(
    canvas: Canvas, area: Area, top: float, m: MaterialCut, s: SheetCut, kerf: int, trim: int
) -> float:
    """Рисует лист в масштабе под отметкой `top`; возвращает y нижнего края схемы."""
    sx, sy = m.sheet_size
    k = scheme_scale(area.w, m.sheet_size)
    x0, y0 = area.x, top

    def px(x: float) -> float:
        return x0 + x * k

    def py(y: float) -> float:
        return y0 - y * k

    canvas.setLineWidth(0.8)
    canvas.rect(px(0), py(sy), sx * k, sy * k)
    canvas.setDash(2, 2)
    canvas.setLineWidth(0.3)
    canvas.rect(px(trim), py(sy - trim), (sx - 2 * trim) * k, (sy - 2 * trim) * k)
    canvas.setDash()
    part_labels, cut_boxes = scheme_labels(m.sheet_size, s, kerf, k)
    for pl in s.placements:
        canvas.setFillColorRGB(0.86, 0.92, 0.98)
        canvas.setLineWidth(0.4)
        canvas.rect(px(pl.x), py(pl.y + pl.h), pl.w * k, pl.h * k, fill=1)
    canvas.setStrokeColorRGB(0.8, 0, 0)
    for c in s.cuts:
        canvas.setLineWidth(max(kerf * k, 0.3))
        along = c.position + kerf / 2
        if c.direction == "vertical":
            canvas.line(px(along), py(c.start), px(along), py(c.end))
        else:
            canvas.line(px(c.start), py(along), px(c.end), py(along))
    canvas.setLineWidth(0.4)
    canvas.setDash(1, 2)
    cuts_by_number = {c.number: c for c in s.cuts}
    for number, box in cut_boxes:
        obstacles = [b for n, b in cut_boxes if n != number] + [label.box for label in part_labels]
        leader = _leader_path(cuts_by_number[number], box, kerf, k, obstacles, (sx * k, sy * k))
        if leader is not None:
            for (x1, y1), (x2, y2) in itertools.pairwise(leader):
                canvas.line(px(x1 / k), py(y1 / k), px(x2 / k), py(y2 / k))
    canvas.setDash()
    for lb in part_labels:
        bx0, by0, bx1, by1 = lb.box
        canvas.saveState()
        canvas.setFont(FONT, lb.size)
        canvas.setFillColorRGB(0, 0, 0)
        canvas.translate(x0 + (bx0 + bx1) / 2, y0 - (by0 + by1) / 2)
        if lb.vertical:
            canvas.rotate(90)
        canvas.drawCentredString(0, -lb.size * 0.35, lb.text)
        canvas.restoreState()
    canvas.setStrokeColorRGB(0.8, 0, 0)
    canvas.setLineWidth(0.3)
    canvas.setFont(FONT_BOLD, _LABEL_FONT)
    for number, (bx0, by0, bx1, by1) in cut_boxes:
        canvas.setFillColorRGB(1, 1, 1)
        canvas.rect(x0 + bx0, y0 - by1, bx1 - bx0, by1 - by0, fill=1)
        canvas.setFillColorRGB(0.8, 0, 0)
        canvas.drawCentredString(x0 + (bx0 + bx1) / 2, y0 - (by0 + by1) / 2 - _LABEL_FONT * 0.35, str(number))
    canvas.setStrokeColorRGB(0, 0, 0)
    canvas.setFillColorRGB(0, 0, 0)
    return py(sy)


def _draw_lines(canvas: Canvas, page: list[Placed]) -> None:
    for p in page:
        canvas.setFont(FONT_BOLD if p.bold else FONT, p.size)
        canvas.drawString(p.x, p.y, p.text)


def _what(page: list[Placed]) -> str:
    """Что на странице-продолжении: детали, резы или то и другое."""
    kinds = {"Резы" if p.text.startswith("Рез ") else "Детали" for p in page if not p.bold}
    return (
        " и ".join(k for k in ("Детали", "Резы") if k in kinds).replace("и Резы", "и резы") or "Детали и резы"
    )


def _sheet_pages(m: MaterialCut, s: SheetCut, kerf: int, trim: int) -> list[tuple[Callable, str]]:
    """Страница листа и, если списки не помещаются, страницы-продолжения: [(рисовальщик, пометка)]."""
    lines = [("Детали", True)] + [(_part_line(n, pl), False) for n, pl in enumerate(s.placements, 1)]
    lines += [("Резы по порядку", True)] + [(_cut_line(c), False) for c in s.cuts]
    holder: dict[str, list[list[Placed]]] = {}

    def first(canvas: Canvas, area: Area) -> None:
        y = area.y + area.h
        canvas.setFont(FONT_BOLD, 13)
        canvas.drawString(area.x, y - 13, f"Карта раскроя: {m.material} — {m.name}")
        canvas.setFont(FONT, 9)
        canvas.drawString(
            area.x,
            y - 26,
            f"Лист {s.number} из {m.sheet_count} · {m.sheet_size[0]}×{m.sheet_size[1]} мм · деталей {len(s.placements)}"
            f" · использование листа {s.utilization:.1%}",
        )
        canvas.drawString(
            area.x,
            y - 37,
            f"Итог по материалу: листов {m.sheet_count}, деталей {m.parts_count}, использование {m.utilization:.1%},"
            f" отход {m.waste_m2:.3f} м²",
        )
        canvas.setFont(FONT, 7)
        canvas.drawString(
            area.x,
            y - 47,
            f"Пропил {kerf} мм, обрезка {trim} мм — пунктир по периметру. Красные: линии — резы,"
            " пунктир — выноски; номер детали — строка списка.",
        )
        bottom = _scheme(canvas, area, y - 52, m, s, kerf, trim)
        holder["pages"] = place_lines(lines, area, bottom - 5 * mm)
        _draw_lines(canvas, holder["pages"][0])

    def cont(index: int):
        def draw(canvas: Canvas, area: Area) -> None:
            y = area.y + area.h
            canvas.setFont(FONT_BOLD, 13)
            canvas.drawString(area.x, y - 13, f"Карта раскроя: {m.material} — {m.name}")
            canvas.setFont(FONT, 9)
            canvas.drawString(
                area.x,
                y - 26,
                f"Лист {s.number} из {m.sheet_count} · {_what(holder['pages'][index])} (продолжение)",
            )
            _draw_lines(canvas, holder["pages"][index])

        return draw

    # Число страниц известно только после расчёта схемы (её высота зависит от масштаба): считаем заранее
    # по тем же правилам, что и в `first`.
    return [
        (first, ""),
        *((cont(i), f"лист {s.number}, продолжение") for i in range(1, _extra_pages(m, lines))),
    ]


def _scheme_bottom(area: Area, m: MaterialCut) -> float:
    k = scheme_scale(area.w, m.sheet_size)
    return area.y + area.h - 52 - m.sheet_size[1] * k - 5 * mm


def _extra_pages(m: MaterialCut, lines: list[tuple[str, bool]]) -> int:
    area = page_area()
    return len(place_lines(lines, area, _scheme_bottom(area, m)))


def write_cutting_map(
    path: Path,
    materials: list[MaterialCut],
    *,
    kerf: int,
    trim: int,
    order: str,
    version: int | None,
    mock: bool,
    on: date,
) -> None:
    """Пишет PDF: по странице на каждый лист всех материалов (порядок — как в раскрое)."""
    doc = PdfDocument(path, order=order, version=version, mock=mock, on=on, title=f"Карта раскроя {order}")
    for m in materials:
        for s in m.sheets:
            for draw, note in _sheet_pages(m, s, kerf, trim):
                doc.add_page(draw, note)
    doc.save()

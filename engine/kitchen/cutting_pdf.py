"""Карта раскроя PDF (2.2): страница на лист — схема в масштабе, список деталей и резов, использование."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas

from kitchen.cutting import Cut, MaterialCut, Placement, SheetCut
from kitchen.pdf import FONT, FONT_BOLD, Area, PdfDocument, mm, page_area, register_fonts

__all__ = ["cut_label_boxes", "place_lines", "write_cutting_map"]

_SCHEME_MAX_H = 150 * mm
_LINE = 3.3 * mm  # шаг строк при кегле _FONTS[0]
_COL_GAP = 3 * mm
# Кегль списков: пробуем от крупного к мелкому; мельче MIN_FONT не уходим, а переносим на страницу-продолжение.
_FONTS = (6.5, 6.0, 5.5)
MIN_FONT = _FONTS[-1]
_CONT_HEADER_H = 16 * mm  # шапка страницы-продолжения
_LABEL_FONT = 6
_LABEL_H = 7.0


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


def _fit_label(canvas: Canvas, texts: list[str], x: float, y: float, w: float, h: float) -> None:
    """Подпись детали по центру прямоугольника: самый полный текст, который влезает (по горизонтали, затем
    вертикально, затем мельче до 4 пт); если не влез никакой — последний (короткий номер) всё равно рисуем."""
    for text in texts:
        for size in (6.5, 6, 5.5, 5, 4.5, 4):
            for vertical in (False, True):
                room, across = (h, w) if vertical else (w, h)
                if canvas.stringWidth(text, FONT, size) <= room - 2 and size <= across - 1:
                    _draw_centered(canvas, text, x, y, w, h, size, vertical)
                    return
    _draw_centered(canvas, texts[-1], x, y, w, h, 4, w < h and w < 8)


def _draw_centered(
    canvas: Canvas, text: str, x: float, y: float, w: float, h: float, size: float, vertical: bool
) -> None:
    canvas.saveState()
    canvas.setFont(FONT, size)
    canvas.setFillColorRGB(0, 0, 0)
    canvas.translate(x + w / 2, y + h / 2)
    if vertical:
        canvas.rotate(90)
    canvas.drawCentredString(0, -size * 0.35, text)
    canvas.restoreState()


def cut_label_boxes(
    sheet_size: tuple[int, int], cuts: list[Cut], kerf: int, k: float
) -> list[tuple[int, float, float, float, float]]:
    """Подписи-номера резов в координатах схемы (пункты от левого верхнего угла листа, y вниз):
    (номер, x0, y0, x1, y1). Подпись ставится у середины реза и сдвигается вдоль линии, пока не
    перестанет пересекать уже поставленные; нигде не уместилась — остаётся у середины."""
    register_fonts()
    boxes: list[tuple[int, float, float, float, float]] = []

    def free(box: tuple[float, float, float, float]) -> bool:
        return all(box[2] <= b[1] or b[3] <= box[0] or box[3] <= b[2] or b[4] <= box[1] for b in boxes)

    for c in cuts:
        w = stringWidth(str(c.number), FONT_BOLD, _LABEL_FONT) + 2
        across = (c.position + kerf / 2) * k  # координата линии
        lo, hi = c.start * k, c.end * k
        mid = (lo + hi) / 2
        chosen = None
        # Сначала вдоль линии (в пределах реза), затем со сдвигом в сторону от линии на шаг подписи.
        for shift in (0, *(sgn * j * _LABEL_H for j in range(1, 40) for sgn in (1, -1))):
            for j in range(int((hi - lo) // _LABEL_H) + 1):
                for off in {0.0, j * _LABEL_H, -j * _LABEL_H}:
                    at = mid + off
                    if hi - lo >= _LABEL_H and (
                        at - _LABEL_H / 2 < lo - 0.01 or at + _LABEL_H / 2 > hi + 0.01
                    ):
                        continue
                    if c.direction == "vertical":
                        box = (
                            across + shift - w / 2,
                            at - _LABEL_H / 2,
                            across + shift + w / 2,
                            at + _LABEL_H / 2,
                        )
                    else:
                        box = (
                            at - w / 2,
                            across + shift - _LABEL_H / 2,
                            at + w / 2,
                            across + shift + _LABEL_H / 2,
                        )
                    if free(box):
                        chosen = box
                        break
                if chosen:
                    break
            if chosen:
                break
        if chosen is None:  # не должно случиться: шагов в сторону с запасом
            chosen = (mid - w / 2, across - _LABEL_H / 2, mid + w / 2, across + _LABEL_H / 2)
        boxes.append((c.number, *chosen))
    return boxes


def _scheme(
    canvas: Canvas, area: Area, top: float, m: MaterialCut, s: SheetCut, kerf: int, trim: int
) -> float:
    """Рисует лист в масштабе под отметкой `top`; возвращает y нижнего края схемы."""
    sx, sy = m.sheet_size
    k = min(area.w / sx, _SCHEME_MAX_H / sy)
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
    for n, pl in enumerate(s.placements, 1):
        canvas.setFillColorRGB(0.86, 0.92, 0.98)
        canvas.setLineWidth(0.4)
        canvas.rect(px(pl.x), py(pl.y + pl.h), pl.w * k, pl.h * k, fill=1)
        mark = f"{pl.part} {pl.w}×{pl.h}"
        _fit_label(canvas, [mark, pl.part, str(n)], px(pl.x), py(pl.y + pl.h), pl.w * k, pl.h * k)
    canvas.setStrokeColorRGB(0.8, 0, 0)
    for c in s.cuts:
        canvas.setLineWidth(max(kerf * k, 0.3))
        along = c.position + kerf / 2
        if c.direction == "vertical":
            canvas.line(px(along), py(c.start), px(along), py(c.end))
        else:
            canvas.line(px(c.start), py(along), px(c.end), py(along))
    canvas.setLineWidth(0.3)
    canvas.setFont(FONT_BOLD, _LABEL_FONT)
    for number, bx0, by0, bx1, by1 in cut_label_boxes(m.sheet_size, s.cuts, kerf, k):
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
            f"Пропил {kerf} мм, обрезка по периметру {trim} мм (пунктир). Красные линии — резы, цифры — порядок;"
            " номер в схеме — строка в списке деталей.",
        )
        bottom = _scheme(canvas, area, y - 52, m, s, kerf, trim)
        holder["pages"] = place_lines(lines, area, bottom - 5 * mm)
        _draw_lines(canvas, holder["pages"][0])

    def cont(index: int):
        def draw(canvas: Canvas, area: Area) -> None:
            y = area.y + area.h
            canvas.setFont(FONT_BOLD, 13)
            canvas.drawString(
                area.x,
                y - 13,
                f"Карта раскроя: {m.material} — лист {s.number} из {m.sheet_count}, продолжение",
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
    k = min(area.w / m.sheet_size[0], _SCHEME_MAX_H / m.sheet_size[1])
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

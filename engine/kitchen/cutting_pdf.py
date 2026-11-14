"""Карта раскроя PDF (2.2): страница на лист — схема в масштабе, список деталей и резов, использование."""

from datetime import date
from pathlib import Path

from reportlab.pdfgen.canvas import Canvas

from kitchen.cutting import Cut, MaterialCut, Placement, SheetCut
from kitchen.pdf import FONT, FONT_BOLD, Area, PdfDocument, mm

__all__ = ["write_cutting_map"]

_SCHEME_MAX_H = 150 * mm
_LINE = 3.3 * mm
# (число колонок, кегль) — пробуем от крупного к мелкому, пока всё не поместится под схемой
_TEXT_LAYOUTS = ((3, 6.5), (4, 5.5), (5, 4.5))


def _cut_line(c: Cut) -> str:
    axis = "верт., x" if c.direction == "vertical" else "гориз., y"
    what = "обрезка" if c.kind == "trim" else "разделяет"
    return f"Рез {c.number}: {axis}={c.position}, {c.start}…{c.end} ({c.length} мм), {what}"


def _part_line(pl: Placement) -> str:
    turn = ", повёрнута" if pl.rotated else ""
    return f"{pl.part} {pl.w}×{pl.h} мм, x={pl.x} y={pl.y}{turn}"


def _fit_text(canvas: Canvas, text: str, x: float, y: float, w: float, h: float, size: float) -> None:
    """Текст по центру прямоугольника; не лезет по ширине — вдоль длинной стороны вертикально, затем мельче."""
    vertical = w < h and canvas.stringWidth(text, FONT, size) > w - 1
    room, across = (h, w) if vertical else (w, h)
    while size > 3 and (canvas.stringWidth(text, FONT, size) > room - 2 or size > across - 1):
        size -= 0.5
    if size <= 3 and canvas.stringWidth(text, FONT, size) > room:
        return  # деталь слишком мала для подписи: маркировка есть в списке деталей
    canvas.saveState()
    canvas.setFont(FONT, size)
    canvas.setFillColorRGB(0, 0, 0)
    canvas.translate(x + w / 2, y + h / 2)
    if vertical:
        canvas.rotate(90)
    canvas.drawCentredString(0, -size * 0.35, text)
    canvas.restoreState()


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
    for pl in s.placements:
        canvas.setFillColorRGB(0.86, 0.92, 0.98)
        canvas.setLineWidth(0.4)
        canvas.rect(px(pl.x), py(pl.y + pl.h), pl.w * k, pl.h * k, fill=1)
        _fit_text(canvas, f"{pl.part} {pl.w}×{pl.h}", px(pl.x), py(pl.y + pl.h), pl.w * k, pl.h * k, 6.5)
    canvas.setStrokeColorRGB(0.8, 0, 0)
    canvas.setFillColorRGB(0.8, 0, 0)
    for c in s.cuts:
        canvas.setLineWidth(max(kerf * k, 0.3))
        if c.direction == "vertical":
            a, b = (px(c.position + kerf / 2), py(c.start)), (px(c.position + kerf / 2), py(c.end))
        else:
            a, b = (px(c.start), py(c.position + kerf / 2)), (px(c.end), py(c.position + kerf / 2))
        canvas.line(*a, *b)
        canvas.setFont(FONT_BOLD, 6)
        canvas.drawString(a[0] + 1, a[1] - 6, str(c.number))
    canvas.setStrokeColorRGB(0, 0, 0)
    canvas.setFillColorRGB(0, 0, 0)
    return py(sy)


def _columns(canvas: Canvas, area: Area, top: float, lines: list[tuple[str, bool]]) -> None:
    """Строки (текст, жирная?) потоком по колонкам под схемой."""
    avail = top - area.y
    for cols, size in _TEXT_LAYOUTS:
        per = int(avail // (_LINE * size / 6.5))
        if per * cols >= len(lines):
            break
    step = _LINE * size / 6.5
    width = area.w / cols
    for i, (text, bold) in enumerate(lines):
        col, row = divmod(i, per)
        canvas.setFont(FONT_BOLD if bold else FONT, size)
        canvas.drawString(area.x + col * width, top - (row + 1) * step, text)


def _sheet_page(m: MaterialCut, s: SheetCut, kerf: int, trim: int):
    def draw(canvas: Canvas, area: Area) -> None:
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
            f"Пропил {kerf} мм, обрезка по периметру {trim} мм (пунктир). Красные линии — резы, цифры — порядок.",
        )
        bottom = _scheme(canvas, area, y - 52, m, s, kerf, trim)
        lines = [("Детали", True)] + [(_part_line(pl), False) for pl in s.placements]
        lines += [("Резы по порядку", True)] + [(_cut_line(c), False) for c in s.cuts]
        _columns(canvas, area, bottom - 5 * mm, lines)

    return draw


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
            doc.add_page(_sheet_page(m, s, kerf, trim))
    doc.save()

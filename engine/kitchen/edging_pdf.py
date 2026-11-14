"""PDF карты кромления: таблица деталей и итоги рассчитанного метража."""

from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Paragraph

from kitchen.errors import KitchenError
from kitchen.parts import EdgeTotals, Part
from kitchen.pdf import FONT, FONT_BOLD, Area, PdfDocument, page_area

_STYLE = ParagraphStyle("edging", fontName=FONT, fontSize=8, leading=10, splitLongWords=True)
_HEADING = ParagraphStyle("edging-heading", parent=_STYLE, fontName=FONT_BOLD)
_COLS = (0.23, 0.13, 0.16, 0.16, 0.16, 0.16)
_HEADER = ("Деталь", "Размеры, мм", "L1", "L2", "W1", "W2")
_ROW_PAD = 3
_HEADER_H = 35


def _paragraphs(cells: tuple[str, ...], widths: list[float], *, bold: bool = False):
    style = _HEADING if bold else _STYLE
    items = [Paragraph(escape(str(value)), style) for value in cells]
    heights = [p.wrap(width - 2 * _ROW_PAD, 10000)[1] for p, width in zip(items, widths, strict=True)]
    return items, max(heights, default=0) + 2 * _ROW_PAD


def write_edging_map(
    path: Path,
    parts: list[Part],
    totals: dict[str, EdgeTotals],
    *,
    order: str,
    version: int | None,
    mock: bool,
    on: date,
) -> None:
    """Таблица всех кромлёных деталей, затем итог каждой кромки, с продолжением на страницах."""
    doc = PdfDocument(path, order=order, version=version, mock=mock, on=on, title=f"Карта кромления {order}")
    area = page_area()
    widths = [area.w * fraction for fraction in _COLS]
    details = [
        (p.id, f"{p.length}×{p.width}", *(p.edges.get(side) or "—" for side in ("L1", "L2", "W1", "W2")))
        for p in parts
    ] or [("Нет деталей с кромкой", "", "", "", "", "")]
    summaries = [
        (edge, "", f"{e.clean_m:.3f}", f"{e.with_allowance_m:.3f}", f"{e.purchase_m:.3f}", "")
        for edge, e in sorted(totals.items())
    ] or [("Нет кромки", "", "", "", "", "")]
    sections = [
        ([_HEADER], details),
        (
            [
                ("Итог по кромке", "", "", "", "", ""),
                ("Кромка", "", "Чистая, м", "С припуском, м", "К закупке, м", ""),
            ],
            summaries,
        ),
    ]
    pages: list[list[tuple[list[Paragraph], float]]] = [[]]
    available = area.h - _HEADER_H
    remaining = available
    for headings, rows in sections:
        header_rows = [_paragraphs(h, widths, bold=True) for h in headings]
        header_height = sum(row[1] for row in header_rows)
        for index, cells in enumerate(rows):
            row = _paragraphs(cells, widths)
            if header_height + row[1] > available:
                raise KitchenError("Строка карты кромления слишком велика для страницы.")
            needed = row[1] + (header_height if index == 0 else 0)
            if needed > remaining:
                pages.append([])
                remaining = available
                index_is_first = True
            else:
                index_is_first = index == 0
            if index_is_first:
                pages[-1].extend(header_rows)
                remaining -= header_height
            pages[-1].append(row)
            remaining -= row[1]

    for page in pages:

        def draw(canvas, a: Area, page=page):
            y = a.y + a.h
            canvas.setFont(FONT_BOLD, 13)
            canvas.drawString(a.x, y - 13, "Карта кромления")
            y -= _HEADER_H
            for items, height in page:
                x = a.x
                for item, width in zip(items, widths, strict=True):
                    _, h = item.wrap(width - 2 * _ROW_PAD, height)
                    item.drawOn(canvas, x + _ROW_PAD, y - _ROW_PAD - h)
                    canvas.setLineWidth(0.3)
                    canvas.rect(x, y - height, width, height)
                    x += width
                y -= height

        doc.add_page(draw)
    doc.save()

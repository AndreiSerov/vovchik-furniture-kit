"""PDF чек-листа выезда (4.4, тикет 40): шапка и разделы со строками «☐ ...».

Раскладка (`layout`) считается отдельно от рисования, как у сборочного листа. Заголовок раздела не остаётся
последней строкой страницы. Колонтитул и водяной знак — общий слой `kitchen.pdf`.
"""

from datetime import date
from pathlib import Path

from pydantic import BaseModel
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas

from kitchen.drilling_pdf import TextLine
from kitchen.pdf import FONT, FONT_BOLD, Area, PdfDocument, page_area, register_fonts, wrap
from kitchen.trip import DASH, TripHeader, TripSection

__all__ = ["BOX", "TripPageLayout", "layout", "write_trip"]

BOX = "☐"  # U+2610, есть в DejaVu
_TITLE_SIZE = 13
_HEAD_SIZE = 10.5
_TEXT_SIZE = 9.5
_LEADING = 13.0
_GAP = 8.0
_INDENT = 12.0
_HANGING = 14.0  # перенесённая часть строки идёт под текстом, а не под ящиком


class TripPageLayout(BaseModel):
    top: float  # верх рабочего поля страницы: от него считается первая строка
    lines: list[TextLine]


class _Row(BaseModel):
    text: str
    bold: bool = False
    size: float = _TEXT_SIZE
    indent: float = 0
    hang: float = 0
    heading: bool = False
    gap_before: float = 0


def _rows(header: TripHeader, sections: list[TripSection]) -> list[_Row]:
    rows = [_Row(text=line, size=_TEXT_SIZE + 0.5) for line in header.lines()]
    for section in sections:
        rows.append(_Row(text=section.title, bold=True, size=_HEAD_SIZE, heading=True, gap_before=_GAP))
        if not section.items:
            rows.append(_Row(text=DASH, indent=_INDENT))
        rows += [_Row(text=f"{BOX} {i.text}", indent=_INDENT, hang=_HANGING) for i in section.items]
    return rows


def layout(header: TripHeader, sections: list[TripSection]) -> list[TripPageLayout]:
    """Страницы чек-листа: заголовок «Чек-лист выезда», шапка, разделы подряд."""
    register_fonts()
    area = page_area()
    top = area.y + area.h
    pages: list[list[TextLine]] = [[]]

    def text(value: str, size: float, bold: bool, x: float, base: float) -> TextLine:
        width = stringWidth(value, FONT_BOLD if bold else FONT, size)
        return TextLine(text=value, size=size, bold=bold, x=x, y=base, width=width)

    physical: list[_Row] = []
    for row in _rows(header, sections):
        pieces = wrap(row.text, row.bold, row.size, area.w - row.indent - row.hang)
        for i, piece in enumerate(pieces):
            physical.append(
                row.model_copy(
                    update={
                        "text": piece,
                        "indent": row.indent + (row.hang if i else 0),
                        "gap_before": row.gap_before if i == 0 else 0,
                        "heading": row.heading,
                    }
                )
            )
    y = top - _TITLE_SIZE
    pages[0].append(text("Чек-лист выезда", _TITLE_SIZE, True, area.x, y))
    y -= 8
    for n, row in enumerate(physical):
        need = _LEADING + row.gap_before
        if row.heading:  # заголовок целиком и ещё строка после него: он не остаётся последним на странице
            pieces = next((k for k, r in enumerate(physical[n:]) if not r.heading), len(physical) - n)
            need += _LEADING * (pieces - 1 + (n + pieces < len(physical)))
        if y - need < area.y and pages[-1]:
            pages.append([])
            y = top
            row = row.model_copy(update={"gap_before": 0})
        y -= _LEADING + row.gap_before
        pages[-1].append(
            text(row.text, row.size, row.bold, area.x + row.indent, y + (_LEADING - row.size) / 2)
        )
    return [TripPageLayout(top=top, lines=lines) for lines in pages]


def write_trip(
    path: Path,
    header: TripHeader,
    sections: list[TripSection],
    *,
    order: str,
    version: int | None,
    mock: bool,
    on: date,
) -> None:
    """PDF по раскладке `layout`."""
    doc = PdfDocument(path, order=order, version=version, mock=mock, on=on, title=f"Чек-лист выезда {order}")
    for page in layout(header, sections):

        def draw(canvas: Canvas, a: Area, page=page) -> None:
            canvas.setFillColorRGB(0, 0, 0)
            for line in page.lines:
                canvas.setFont(FONT_BOLD if line.bold else FONT, line.size)
                canvas.drawString(line.x, line.y, line.text)

        doc.add_page(draw)
    doc.save()

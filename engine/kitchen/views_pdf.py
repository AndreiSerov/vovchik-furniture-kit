"""PDF видов кухни (тикет 41): рисует страницы `views.layout` на альбомном листе общего слоя `kitchen.pdf`
(колонтитул и водяной знак мока — его)."""

from datetime import date
from pathlib import Path

from reportlab.pdfgen.canvas import Canvas

from kitchen.pdf import FONT, FONT_BOLD, Area, PdfDocument
from kitchen.views import Label, Segment, Shape, ViewPage

__all__ = ["write_views"]

_HATCH_STEP = 4.0


def _rgb(canvas: Canvas, color: tuple[int, int, int], fill: bool) -> None:
    r, g, b = (c / 255 for c in color)
    (canvas.setFillColorRGB if fill else canvas.setStrokeColorRGB)(r, g, b)


def _shape(canvas: Canvas, s: Shape) -> None:
    canvas.saveState()
    canvas.setLineWidth(s.line)
    _rgb(canvas, s.stroke, fill=False)
    if s.dashed:
        canvas.setDash(3, 2)
    if s.fill is not None:
        _rgb(canvas, s.fill, fill=True)
    canvas.rect(s.x, s.y, s.w, s.h, stroke=1, fill=1 if s.fill is not None else 0)
    if s.hatch:
        path = canvas.beginPath()
        path.rect(s.x, s.y, s.w, s.h)
        canvas.clipPath(path, stroke=0, fill=0)
        canvas.setDash()
        canvas.setLineWidth(0.3)
        canvas.setStrokeColorRGB(0.45, 0.45, 0.45)
        k = -s.h
        while k < s.w:
            canvas.line(s.x + k, s.y, s.x + k + s.h, s.y + s.h)
            k += _HATCH_STEP
    canvas.restoreState()


def _segment(canvas: Canvas, seg: Segment) -> None:
    canvas.saveState()
    canvas.setLineWidth(seg.line)
    if seg.dashed:
        canvas.setDash(3, 2)
    canvas.line(seg.x0, seg.y0, seg.x1, seg.y1)
    if seg.arrows:
        canvas.setDash()
        length = ((seg.x1 - seg.x0) ** 2 + (seg.y1 - seg.y0) ** 2) ** 0.5 or 1.0
        ux, uy = (seg.x1 - seg.x0) / length, (seg.y1 - seg.y0) / length
        for (px, py), sign in (((seg.x0, seg.y0), 1), ((seg.x1, seg.y1), -1)):
            path = canvas.beginPath()
            path.moveTo(px, py)
            path.lineTo(px + sign * 5 * ux - 1.8 * uy, py + sign * 5 * uy + 1.8 * ux)
            path.lineTo(px + sign * 5 * ux + 1.8 * uy, py + sign * 5 * uy - 1.8 * ux)
            path.close()
            canvas.drawPath(path, stroke=0, fill=1)
    canvas.restoreState()


def _label(canvas: Canvas, label: Label) -> None:
    canvas.saveState()
    if label.background:
        x0, y0, x1, y1 = label.box()
        canvas.setFillColorRGB(1, 1, 1)
        canvas.rect(x0, y0, x1 - x0, y1 - y0, stroke=0, fill=1)
    _rgb(canvas, label.color, fill=True)
    canvas.setFont(FONT_BOLD if label.bold else FONT, label.size)
    canvas.translate(label.x, label.y)
    if label.rotated:
        canvas.rotate(90)
    canvas.drawString(0, 0, label.text)
    canvas.restoreState()


def write_views(
    path: Path, pages: list[ViewPage], *, order: str, version: int | None, mock: bool, on: date
) -> None:
    """PDF по раскладке `views.layout`: A4 альбомная, страница на элемент списка."""
    doc = PdfDocument(
        path, order=order, version=version, mock=mock, on=on, title=f"Виды кухни {order}", landscape=True
    )
    for page in pages:

        def draw(canvas: Canvas, a: Area, page=page) -> None:
            for s in page.shapes:
                _shape(canvas, s)
            for seg in page.segments:
                _segment(canvas, seg)
            for label in page.labels:
                _label(canvas, label)

        doc.add_page(draw)
    doc.save()

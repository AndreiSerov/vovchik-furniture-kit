"""PDF сборочного листа (4.4, тикет 22): модули подряд, модуль не разрывается между страницами.

Раскладка (`layout`: строки текста с кеглем, положением и шириной, по страницам) считается отдельно от
рисования; её же читают тесты через `api.assembly_layout`. Модуль, который не помещается на страницу целиком,
продолжается на следующей с заголовком «П1, продолжение». Колонтитул и водяной знак — общий слой `kitchen.pdf`.
"""

from datetime import date
from pathlib import Path

from pydantic import BaseModel
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas

from kitchen.assembly import AssemblyModule
from kitchen.drilling_pdf import TextLine
from kitchen.pdf import FONT, FONT_BOLD, Area, PdfDocument, page_area, register_fonts, wrap

__all__ = ["AssemblyPageLayout", "layout", "write_assembly"]

_TITLE_SIZE = 13
_HEAD_SIZE = 10.5
_SUB_SIZE = 9
_TEXT_SIZE = 9
_LEADING = 12.0
_GAP = 9.0  # между модулями
_INDENT = 12.0
_HANGING = 12.0  # продолжение длинной строки шага и детали


class AssemblyPageLayout(BaseModel):
    """Страница листа: строки и метки модулей, чьи блоки (или их продолжения) на ней начинаются."""

    lines: list[TextLine]
    modules: list[str]


class _Row(BaseModel):
    text: str
    bold: bool
    size: float
    indent: float
    heading: bool = False  # заголовок раздела: не остаётся последней строкой страницы


def _rows(module: AssemblyModule) -> list[_Row]:
    """Логические строки блока модуля (до переноса по ширине)."""
    if not module.carcass:
        return [_Row(text=f"{module.title}: {module.note}", bold=False, size=_TEXT_SIZE, indent=0)]

    def sub(text: str, *, heading: bool = True) -> _Row:
        return _Row(text=text, bold=True, size=_SUB_SIZE, indent=0, heading=heading)

    def item(text: str) -> _Row:
        return _Row(text=text, bold=False, size=_TEXT_SIZE, indent=_INDENT)

    rows = [
        _Row(text=module.title, bold=True, size=_HEAD_SIZE, indent=0, heading=True),
        sub(f"Детали ({len(module.parts)}):"),
        *(item(f"{p.mark} · {p.name} · {p.size} · {p.material}") for p in module.parts),
    ]
    if module.hardware:
        rows += [sub("Фурнитура:"), *(item(f"{h.name} — {h.quantity} {h.unit}") for h in module.hardware)]
    else:
        rows.append(sub("Фурнитура: нет", heading=False))
    if module.appliances:
        rows.append(sub(f"Техника: {', '.join(module.appliances)}", heading=False))
    if module.steps:
        rows += [sub("Порядок сборки:"), *(item(f"{n}. {step}") for n, step in enumerate(module.steps, 1))]
    return rows


def _physical(rows: list[_Row], width: float) -> list[_Row]:
    """Строки с переносами по ширине листа; перенесённая часть идёт с висячим отступом."""
    out = []
    for row in rows:
        hang = _HANGING if row.indent else 0
        for i, piece in enumerate(wrap(row.text, row.bold, row.size, width - row.indent - hang)):
            out.append(row.model_copy(update={"text": piece, "indent": row.indent + (hang if i else 0)}))
    return out


def layout(modules: list[AssemblyModule]) -> list[AssemblyPageLayout]:
    """Страницы листа. Модуль целиком на странице; длинный продолжается с заголовком «П1, продолжение».
    Пустой список модулей — одна страница «Модулей нет»."""
    register_fonts()
    area = page_area()
    top = area.y + area.h
    pages: list[tuple[list[TextLine], list[str]]] = []

    def text(value: str, size: float, bold: bool, x: float, base: float) -> TextLine:
        width = stringWidth(value, FONT_BOLD if bold else FONT, size)
        return TextLine(text=value, size=size, bold=bold, x=x, y=base, width=width)

    def new_page() -> float:
        pages.append(([], []))
        if len(pages) > 1:
            return top
        pages[0][0].append(text("Сборочный лист", _TITLE_SIZE, True, area.x, top - _TITLE_SIZE))
        return top - _TITLE_SIZE - 6

    y = new_page()
    if not modules:
        pages[0][0].append(text("Модулей нет", _TEXT_SIZE, False, area.x, y - _LEADING))
    for module in modules:
        rows = _physical(_rows(module), area.w)
        if len(rows) * _LEADING > y - area.y and pages[-1][1]:
            y = new_page()
        pages[-1][1].append(module.label)
        while True:
            room = max(int((y - area.y) // _LEADING), 1)
            chunk, rest = rows[:room], rows[room:]
            while (
                rest and len(chunk) > 1 and chunk[-1].heading
            ):  # заголовок раздела не остаётся внизу страницы
                rest.insert(0, chunk.pop())
            for row in chunk:
                y -= _LEADING
                pages[-1][0].append(
                    text(row.text, row.size, row.bold, area.x + row.indent, y + (_LEADING - row.size) / 2)
                )
            if not rest:
                break
            y = new_page()
            pages[-1][1].append(f"{module.label}, продолжение")
            head = _Row(
                text=f"{module.label}, продолжение", bold=True, size=_HEAD_SIZE, indent=0, heading=True
            )
            rows = [head, *rest]
        y -= _GAP
    return [AssemblyPageLayout(lines=lines, modules=labels) for lines, labels in pages]


def _draw_text(canvas: Canvas, line: TextLine) -> None:
    canvas.setFont(FONT_BOLD if line.bold else FONT, line.size)
    canvas.drawString(line.x, line.y, line.text)


def write_assembly(
    path: Path,
    modules: list[AssemblyModule],
    *,
    order: str,
    version: int | None,
    mock: bool,
    on: date,
) -> None:
    """PDF по раскладке `layout`."""
    doc = PdfDocument(path, order=order, version=version, mock=mock, on=on, title=f"Сборочный лист {order}")
    for page in layout(modules):

        def draw(canvas: Canvas, a: Area, page=page) -> None:
            canvas.setFillColorRGB(0, 0, 0)
            for line in page.lines:
                _draw_text(canvas, line)

        doc.add_page(draw)
    doc.save()

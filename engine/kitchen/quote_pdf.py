"""PDF коммерческого предложения: реквизиты, состав кухни словами, итог, предоплата, срок.

Колонтитул и водяной знак мока — общий PDF-слой (`kitchen.pdf`). Страницу строит поток абзацев:
не помещается — следующая страница.
"""

from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Paragraph

from kitchen.format import plural, working_days
from kitchen.pdf import FONT, FONT_BOLD, Area, PdfDocument, page_area
from kitchen.quote import QuoteBody, money, percent

__all__ = ["write_quote"]

_BODY = ParagraphStyle("quote", fontName=FONT, fontSize=10, leading=13, spaceAfter=2)
_BOLD = ParagraphStyle("quote-bold", parent=_BODY, fontName=FONT_BOLD)
_TITLE = ParagraphStyle(
    "quote-title", parent=_BODY, fontName=FONT_BOLD, fontSize=16, leading=20, spaceAfter=6
)
_HEADING = ParagraphStyle(
    "quote-heading", parent=_BODY, fontName=FONT_BOLD, fontSize=12, leading=15, spaceBefore=8, spaceAfter=3
)


def _countertop(value: str) -> str:
    """«Столешница: Столешница Egger…» — слово не повторяется, если название уже с него начинается."""
    return value if value.lower().startswith("столешниц") else f"Столешница: {value}"


def _blocks(quote: QuoteBody, order: str) -> list[tuple[ParagraphStyle, str]]:
    master = quote.master
    blocks = [
        (_TITLE, "Коммерческое предложение"),
        (_BODY, f"Заказ {order}"),
        (_BOLD, master.name),
        (_BODY, f"{master.status}, ИНН {master.inn}"),
        (_BODY, f"{master.phone}, {master.city}"),
        (_HEADING, "Состав кухни"),
        *((_BODY, f"{m.label} — {m.text}") for m in quote.modules),
        *(
            (_BODY, f"{title}: {value}")
            for title, value in (
                ("Корпус", quote.body),
                ("Фасады нижние", quote.facades_lower),
                ("Фасады верхние", quote.facades_upper),
            )
            if value
        ),
        *([(_BODY, _countertop(quote.countertop))] if quote.countertop else []),
        (_HEADING, "Стоимость и сроки"),
        (_BOLD, f"Итого: {money(quote.price)}"),
        (_BODY, f"Предоплата ({percent(quote.prepayment_share)}): {money(quote.prepayment)}"),
        (_BODY, f"Остаток: {money(quote.balance)}"),
        (
            _BODY,
            f"Срок изготовления и монтажа: {working_days(quote.days)}",
        ),
        (_BODY, f"Предложение действует {plural(master.offer_days, 'день', 'дня', 'дней')}"),
    ]
    if quote.economy:
        e = quote.economy
        blocks += [
            (_HEADING, "Вариант «экономия»"),
            (_BODY, f"Цена как в проекте: {money(e.price_as_is)}"),
            (_BOLD, f"Цена «экономии»: {money(e.price)}"),
            (_BODY, f"Разница: {money(e.saving)}"),
            *(
                (
                    _BODY,
                    (
                        f"{r.client_text.rstrip('.')} "
                        f"({'модуль' if len(r.modules) == 1 else 'модули'} {', '.join(r.modules)})."
                    ),
                )
                for r in e.replacements
            ),
            *([] if e.replacements else [(_BODY, "Подходящих замен нет: цена не меняется.")]),
        ]
    return blocks


def write_quote(
    path: Path, quote: QuoteBody, *, order: str, version: int | None, mock: bool, on: date
) -> None:
    doc = PdfDocument(
        path, order=order, version=version, mock=mock, on=on, title=f"Коммерческое предложение {order}"
    )
    area = page_area()
    pages: list[list[tuple[Paragraph, float]]] = [[]]
    left = area.h
    for style, text in _blocks(quote, order):
        paragraph = Paragraph(escape(text), style)
        _, height = paragraph.wrap(area.w, area.h)
        height += style.spaceBefore + style.spaceAfter
        if height > left and pages[-1]:
            pages.append([])
            left = area.h
        pages[-1].append((paragraph, height))
        left -= height
    for page in pages:

        def draw(canvas, a: Area, page=page):
            y = a.y + a.h
            for paragraph, height in page:
                style = paragraph.style
                y -= style.spaceBefore
                paragraph.wrapOn(canvas, a.w, a.h)
                paragraph.drawOn(canvas, a.x, y - (height - style.spaceBefore - style.spaceAfter))
                y -= height - style.spaceBefore

        doc.add_page(draw)
    doc.save()

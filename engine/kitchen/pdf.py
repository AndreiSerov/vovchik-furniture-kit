"""Общий PDF-слой этапа 2 (2.2): шрифт DejaVu из пакета, водяной знак мока, колонтитул.

Им пользуются все PDF мастерской (карта раскроя, кромление, присадка). Страницу рисует вызывающий в
переданной функции `draw(canvas, area)`; слой добавляет водяной знак (под содержимым), колонтитул и
номера страниц «из N» (число страниц известно только в `save`).
"""

from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas

from kitchen.metrics import (
    FONT,
    FONT_BOLD,
    FOOTER_H,
    MARGIN,
    Area,
    Stamp,
    page_area,
    page_size,
    register_fonts,
    wrap,
)
from kitchen.result import MOCK_BANNER

__all__ = [
    "FONT",
    "FONT_BOLD",
    "Area",
    "PdfDocument",
    "Stamp",
    "TextLine",
    "mm",
    "page_area",
    "page_size",
    "register_fonts",
    "wrap",
]


class TextLine(BaseModel):
    """Строка текста на странице: левый нижний угол, кегль и ширина при этом кегле, пункты."""

    text: str
    size: float
    bold: bool
    x: float
    y: float
    width: float


class PdfDocument:
    """Многостраничный PDF A4 (книжный, с `landscape` — альбомный). `version` — версия проекта-источника,
    `None` — не записана."""

    def __init__(
        self,
        path: Path,
        *,
        stamp: Stamp,
        title: str = "",
        landscape: bool = False,
    ) -> None:
        register_fonts()
        self.path = Path(path)
        self.stamp = stamp
        self.title = title
        self.landscape = landscape
        self._pages: list[tuple[Callable[[Canvas, Area], None], str]] = []

    def add_page(self, draw: Callable[[Canvas, Area], None], note: str = "") -> None:
        """`note` — пометка страницы в колонтитуле (например, «лист 2, продолжение»)."""
        self._pages.append((draw, note))

    def save(self) -> None:
        width, height = page_size(self.landscape)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        # invariant: без случайного id и текущего времени в файле, одинаковый вход даёт одинаковый PDF
        canvas = Canvas(str(tmp), pagesize=(width, height), invariant=1, pageCompression=0)
        try:
            canvas.setTitle(self.title or self.path.stem)
            area = page_area(self.landscape)
            total = max(len(self._pages), 1)
            for number, (draw, note) in enumerate(self._pages or [(lambda c, a: None, "")], 1):
                canvas.setFillColorRGB(0, 0, 0)
                canvas.setStrokeColorRGB(0, 0, 0)
                draw(canvas, area)
                if self.stamp.mock:  # поверх содержимого, чтобы заливка деталей его не закрывала
                    self._watermark(canvas, width, height)
                self._footer(canvas, width, number, total, note)
                canvas.showPage()
            canvas.save()
            tmp.replace(self.path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    def _watermark(self, canvas: Canvas, width: float, height: float) -> None:
        canvas.saveState()
        canvas.setFillColorRGB(0.5, 0.5, 0.5)
        canvas.setFillAlpha(0.2)
        canvas.setFont(FONT_BOLD, 40)
        canvas.translate(width / 2, height / 2)
        canvas.rotate(55)
        canvas.drawCentredString(0, 0, MOCK_BANNER)
        canvas.restoreState()

    def _footer(self, canvas: Canvas, width: float, number: int, total: int, note: str) -> None:
        version = (
            f"версия проекта {self.stamp.version}"
            if self.stamp.version is not None
            else "версия проекта не записана"
        )
        canvas.setFont(FONT, 7)
        canvas.setFillColorRGB(0.2, 0.2, 0.2)
        canvas.line(MARGIN, MARGIN + FOOTER_H - 2 * mm, width - MARGIN, MARGIN + FOOTER_H - 2 * mm)
        canvas.drawString(
            MARGIN,
            MARGIN + 3 * mm,
            f"Заказ {self.stamp.order} · {version} · {self.stamp.on.strftime('%d.%m.%Y')} · стр. {number} из {total}"
            + (f" · {note}" if note else ""),
        )
        if self.stamp.mock:
            canvas.setFont(FONT_BOLD, 7)
            canvas.setFillColorRGB(0.7, 0, 0)
            canvas.drawRightString(width - MARGIN, MARGIN + 3 * mm, MOCK_BANNER)

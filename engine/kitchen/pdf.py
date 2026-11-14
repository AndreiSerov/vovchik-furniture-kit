"""Общий PDF-слой этапа 2 (2.2): шрифт DejaVu из пакета, водяной знак мока, колонтитул.

Им пользуются все PDF мастерской (карта раскроя, кромление, присадка). Страницу рисует вызывающий в
переданной функции `draw(canvas, area)`; слой добавляет водяной знак (под содержимым), колонтитул и
номера страниц «из N» (число страниц известно только в `save`).
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

from kitchen.result import MOCK_BANNER

__all__ = ["FONT", "FONT_BOLD", "Area", "PdfDocument", "mm"]

FONT = "DejaVuSans"
FONT_BOLD = "DejaVuSans-Bold"
_FONTS_DIR = Path(__file__).parent / "fonts"
_MARGIN = 10 * mm
_FOOTER_H = 9 * mm


@dataclass(frozen=True)
class Area:
    """Свободная область страницы над колонтитулом: левый нижний угол и размеры, пункты."""

    x: float
    y: float
    w: float
    h: float


def _register_fonts() -> None:
    for name, file in ((FONT, "DejaVuSans.ttf"), (FONT_BOLD, "DejaVuSans-Bold.ttf")):
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(_FONTS_DIR / file)))


class PdfDocument:
    """Многостраничный PDF A4 (книжный). `version` — версия проекта-источника, `None` — не записана."""

    def __init__(
        self, path: Path, *, order: str, version: int | None, mock: bool, on: date, title: str = ""
    ) -> None:
        _register_fonts()
        self.path = Path(path)
        self.order = order
        self.version = version
        self.mock = mock
        self.on = on
        self.title = title
        self._pages: list[Callable[[Canvas, Area], None]] = []

    def add_page(self, draw: Callable[[Canvas, Area], None]) -> None:
        self._pages.append(draw)

    def save(self) -> None:
        width, height = A4
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        # invariant: без случайного id и текущего времени в файле, одинаковый вход даёт одинаковый PDF
        canvas = Canvas(str(tmp), pagesize=A4, invariant=1, pageCompression=0)
        canvas.setTitle(self.title or self.path.stem)
        area = Area(_MARGIN, _MARGIN + _FOOTER_H, width - 2 * _MARGIN, height - 2 * _MARGIN - _FOOTER_H)
        total = max(len(self._pages), 1)
        for number, draw in enumerate(self._pages or [lambda c, a: None], 1):
            if self.mock:
                self._watermark(canvas, width, height)
            canvas.setFillColorRGB(0, 0, 0)
            canvas.setStrokeColorRGB(0, 0, 0)
            draw(canvas, area)
            self._footer(canvas, width, number, total)
            canvas.showPage()
        canvas.save()
        tmp.replace(self.path)

    def _watermark(self, canvas: Canvas, width: float, height: float) -> None:
        canvas.saveState()
        canvas.setFillColorRGB(0.85, 0.85, 0.85)
        canvas.setFont(FONT_BOLD, 40)
        canvas.translate(width / 2, height / 2)
        canvas.rotate(55)
        canvas.drawCentredString(0, 0, MOCK_BANNER)
        canvas.restoreState()

    def _footer(self, canvas: Canvas, width: float, number: int, total: int) -> None:
        version = (
            f"версия проекта {self.version}" if self.version is not None else "версия проекта не записана"
        )
        canvas.setFont(FONT, 7)
        canvas.setFillColorRGB(0.2, 0.2, 0.2)
        canvas.line(_MARGIN, _MARGIN + _FOOTER_H - 2 * mm, width - _MARGIN, _MARGIN + _FOOTER_H - 2 * mm)
        canvas.drawString(
            _MARGIN,
            _MARGIN + 3 * mm,
            f"Заказ {self.order} · {version} · {self.on.strftime('%d.%m.%Y')} · стр. {number} из {total}",
        )
        if self.mock:
            canvas.setFont(FONT_BOLD, 7)
            canvas.setFillColorRGB(0.7, 0, 0)
            canvas.drawRightString(width - _MARGIN, _MARGIN + 3 * mm, MOCK_BANNER)

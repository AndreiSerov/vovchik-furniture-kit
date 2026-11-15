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
from reportlab.lib.pagesizes import landscape as _landscape
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

from kitchen.result import MOCK_BANNER

__all__ = [
    "FONT",
    "FONT_BOLD",
    "Area",
    "PdfDocument",
    "mm",
    "page_area",
    "page_size",
    "register_fonts",
    "wrap",
]

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


def page_size(landscape: bool = False) -> tuple[float, float]:
    """Размер листа A4, пункты: книжный или альбомный (виды кухни, тикет 41)."""
    return _landscape(A4) if landscape else A4


def page_area(landscape: bool = False) -> Area:
    """Область содержимого страницы A4 над колонтитулом."""
    width, height = page_size(landscape)
    return Area(_MARGIN, _MARGIN + _FOOTER_H, width - 2 * _MARGIN, height - 2 * _MARGIN - _FOOTER_H)


def register_fonts() -> None:
    for name, file in ((FONT, "DejaVuSans.ttf"), (FONT_BOLD, "DejaVuSans-Bold.ttf")):
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(_FONTS_DIR / file)))


class PdfDocument:
    """Многостраничный PDF A4 (книжный, с `landscape` — альбомный). `version` — версия проекта-источника,
    `None` — не записана."""

    def __init__(
        self,
        path: Path,
        *,
        order: str,
        version: int | None,
        mock: bool,
        on: date,
        title: str = "",
        landscape: bool = False,
    ) -> None:
        register_fonts()
        self.path = Path(path)
        self.order = order
        self.version = version
        self.mock = mock
        self.on = on
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
                if self.mock:  # поверх содержимого, чтобы заливка деталей его не закрывала
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
            f"версия проекта {self.version}" if self.version is not None else "версия проекта не записана"
        )
        canvas.setFont(FONT, 7)
        canvas.setFillColorRGB(0.2, 0.2, 0.2)
        canvas.line(_MARGIN, _MARGIN + _FOOTER_H - 2 * mm, width - _MARGIN, _MARGIN + _FOOTER_H - 2 * mm)
        canvas.drawString(
            _MARGIN,
            _MARGIN + 3 * mm,
            f"Заказ {self.order} · {version} · {self.on.strftime('%d.%m.%Y')} · стр. {number} из {total}"
            + (f" · {note}" if note else ""),
        )
        if self.mock:
            canvas.setFont(FONT_BOLD, 7)
            canvas.setFillColorRGB(0.7, 0, 0)
            canvas.drawRightString(width - _MARGIN, _MARGIN + 3 * mm, MOCK_BANNER)


def wrap(text: str, bold: bool, size: float, width: float) -> list[str]:
    """Строка по словам до ширины `width`; слово шире строки режется по буквам."""
    font = FONT_BOLD if bold else FONT
    lines: list[str] = []
    current = ""
    for word in text.split(" "):
        candidate = f"{current} {word}" if current else word
        if stringWidth(candidate, font, size) <= width:
            current = candidate
            continue
        if current:
            lines.append(current)
        while stringWidth(word, font, size) > width:
            cut = len(word)
            while cut > 1 and stringWidth(word[:cut], font, size) > width:
                cut -= 1
            lines.append(word[:cut])
            word = word[cut:]
        current = word
    return [*lines, current] if current else lines

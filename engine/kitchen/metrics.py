"""Основа PDF и видов: шрифты DejaVu, размер и область страницы A4, перенос строк по ширине.

Ничего не рисует: слой `kitchen.pdf` и раскладка видов (`kitchen.views`) берут отсюда общие размеры.
"""

from dataclasses import dataclass
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.pagesizes import landscape as _landscape
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfbase.ttfonts import TTFont

__all__ = [
    "FONT",
    "FONT_BOLD",
    "FOOTER_H",
    "MARGIN",
    "Area",
    "page_area",
    "page_size",
    "register_fonts",
    "wrap",
]

FONT = "DejaVuSans"
FONT_BOLD = "DejaVuSans-Bold"
_FONTS_DIR = Path(__file__).parent / "fonts"
MARGIN = 10 * mm
FOOTER_H = 9 * mm


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
    return Area(MARGIN, MARGIN + FOOTER_H, width - 2 * MARGIN, height - 2 * MARGIN - FOOTER_H)


def register_fonts() -> None:
    for name, file in ((FONT, "DejaVuSans.ttf"), (FONT_BOLD, "DejaVuSans-Bold.ttf")):
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(_FONTS_DIR / file)))


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

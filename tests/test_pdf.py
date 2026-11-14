"""Общий PDF-слой (2.2): шрифт с кириллицей, водяной знак мока, колонтитул с заказом, версией и датой."""

from datetime import date

from kitchen.pdf import FONT, PdfDocument
from kitchen.result import MOCK_BANNER
from pypdf import PdfReader


def make(path, *, mock, pages=2, version=3):
    doc = PdfDocument(
        path, order="2026-047-petrov", version=version, mock=mock, on=date(2026, 11, 14), title="Карта"
    )

    def draw(c, area, i):
        c.setFont(FONT, 10)
        c.drawString(area.x, area.y + area.h - 12, f"Страница-{i + 1} Ёж")

    for i in range(pages):
        doc.add_page(lambda c, area, i=i: draw(c, area, i))
    doc.save()


def texts(path):
    return [p.extract_text() for p in PdfReader(path).pages]


def test_every_page_has_watermark_and_footer_when_mock(tmp_path):
    make(tmp_path / "a.pdf", mock=True)
    pages = texts(tmp_path / "a.pdf")
    assert len(pages) == 2
    for i, text in enumerate(pages, 1):
        assert MOCK_BANNER in text
        assert f"Страница-{i} Ёж" in text
        assert "2026-047-petrov" in text and "версия проекта 3" in text and "14.11.2026" in text
        assert f"стр. {i} из 2" in text


def test_no_watermark_without_mock(tmp_path):
    make(tmp_path / "a.pdf", mock=False)
    for text in texts(tmp_path / "a.pdf"):
        assert "МОК" not in text and "2026-047-petrov" in text


def test_unknown_version_is_named(tmp_path):
    make(tmp_path / "a.pdf", mock=False, version=None)
    assert "версия проекта не записана" in texts(tmp_path / "a.pdf")[0]

"""Общий PDF-слой (2.2): шрифт с кириллицей, водяной знак мока, колонтитул с заказом, версией и датой."""

from datetime import date

import pytest
from kitchen.pdf import FONT, FONT_BOLD, PdfDocument, register_fonts, wrap
from kitchen.result import MOCK_BANNER
from pypdf import PdfReader
from reportlab.pdfbase.pdfmetrics import stringWidth


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
        # два вхождения: сам водяной знак и строка колонтитула; без знака осталось бы одно
        assert text.count(MOCK_BANNER) == 2
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


def test_failing_page_leaves_no_files_and_keeps_previous_pdf(tmp_path):
    path = tmp_path / "a.pdf"
    make(path, mock=False)
    before = path.read_bytes()
    doc = PdfDocument(path, order="2026-047-petrov", version=3, mock=False, on=date(2026, 11, 14))

    def boom(c, area):
        raise RuntimeError("draw failed")

    doc.add_page(boom)
    with pytest.raises(RuntimeError):
        doc.save()
    assert path.read_bytes() == before
    assert [p.name for p in tmp_path.iterdir()] == ["a.pdf"]


def test_continuation_note_in_footer(tmp_path):
    doc = PdfDocument(
        tmp_path / "a.pdf", order="2026-047-petrov", version=1, mock=False, on=date(2026, 11, 14)
    )
    doc.add_page(lambda c, a: None)
    doc.add_page(lambda c, a: None, note="лист 2, продолжение")
    doc.save()
    first, second = texts(tmp_path / "a.pdf")
    assert "продолжение" not in first and "лист 2, продолжение" in second


def test_wrap_measures_bold_text_in_the_bold_font():
    register_fonts()
    text = " ".join(["Столешница"] * 8)
    width = stringWidth(text, FONT, 10) + 0.1  # обычным шрифтом строка влезает, жирным — нет
    lines = wrap(text, True, 10, width)
    assert len(lines) > 1
    assert all(stringWidth(line, FONT_BOLD, 10) <= width for line in lines)


def test_landscape_page_keeps_footer_and_watermark(tmp_path):
    # тикет 41: виды кухни — A4 альбомная с тем же колонтитулом и водяным знаком
    from kitchen.pdf import page_area
    from reportlab.lib.pagesizes import A4, landscape

    doc = PdfDocument(
        tmp_path / "a.pdf",
        order="2026-047-petrov",
        version=1,
        mock=True,
        on=date(2026, 11, 14),
        landscape=True,
    )
    seen = []
    doc.add_page(lambda c, area: seen.append(area))
    doc.save()
    page = PdfReader(tmp_path / "a.pdf").pages[0]
    width, height = landscape(A4)
    assert (round(float(page.mediabox.width)), round(float(page.mediabox.height))) == (
        round(width),
        round(height),
    )
    assert seen == [page_area(landscape=True)] and seen[0].w > seen[0].h
    text = page.extract_text()
    assert text.count(MOCK_BANNER) == 2 and "стр. 1 из 1" in text


def test_wrap_keeps_a_line_that_is_exactly_as_wide_as_the_limit():
    register_fonts()
    width = stringWidth("слово слово", FONT, 10)
    assert wrap("слово слово", False, 10, width) == ["слово слово"]
    assert wrap("слово слово", False, 10, width - 0.01) == ["слово", "слово"]


def test_wrap_cuts_a_word_wider_than_the_limit_down_to_one_letter():
    register_fonts()
    width = stringWidth("Ш", FONT, 10) + 0.01  # помещается одна буква, две — нет
    assert wrap("ШШШ", False, 10, width) == ["Ш", "Ш", "Ш"]

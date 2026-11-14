"""Карта раскроя PDF (2.2) и учёт выводов: через `kitchen.api`, PDF читаем обратно `pypdf`."""

import json
from pathlib import Path

from kitchen import api
from kitchen.cutting import Cut, MaterialCut, Placement, SheetCut
from kitchen.cutting_pdf import cut_label_boxes, place_lines, write_cutting_map
from kitchen.pdf import FONT, FONT_BOLD, Area, register_fonts
from pypdf import PdfReader
from reportlab.pdfbase.pdfmetrics import stringWidth

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ORDER = "2026-047-petrov"
MOCK = "МОК-ДАННЫЕ — НЕ ДЛЯ КЛИЕНТА"


def petrov(home):
    api.brief_save(ORDER, FIXTURES / "petrov_brief.json", home)
    api.import_project(ORDER, FIXTURES / "petrov_project.json", home=home)


def pages_of(result):
    return [p.extract_text() for p in PdfReader(result.output.file).pages]


def sheets_of(result):
    return [(m, s) for m in result.materials for s in m.sheets]


def test_pdf_has_page_per_sheet_with_markings_cuts_and_utilization(home):
    petrov(home)
    r = api.cut_and_save("petrov", home)
    assert r.output.file == str(home / "orders" / ORDER / "out" / "cutting-map.pdf")
    pages = pages_of(r)
    sheets = sheets_of(r)
    assert len(pages) == len(sheets) == 6
    for text, (m, s) in zip(pages, sheets, strict=True):
        assert m.material in text and f"Лист {s.number} из {m.sheet_count}" in text
        assert f"{s.utilization:.1%}" in text
        for pl in s.placements:
            assert pl.part in text
        for c in s.cuts:
            assert f"Рез {c.number}" in text
        assert MOCK in text and "версия проекта 1" in text and ORDER in text
        assert text.count(MOCK) == 2  # водяной знак и колонтитул


def test_pdf_without_mock_has_no_watermark(home):
    petrov(home)
    for p in (home / "workshop").glob("*.yaml"):
        p.write_text(p.read_text(encoding="utf-8").replace("mock: true", "mock: false"), encoding="utf-8")
    r = api.cut_and_save("petrov", home)
    assert not r.mock
    assert all("МОК" not in t and ORDER in t for t in pages_of(r))


def test_output_registered_with_source_version_and_replaced_on_rerun(home):
    petrov(home)
    r = api.cut_and_save("petrov", home)
    api.cut_and_save("petrov", home)
    outs = api.list_outputs("petrov", home)
    assert [(o.kind, o.source_version, o.path) for o in outs] == [
        ("cutting", 1, f"orders/{ORDER}/out/cutting-map.pdf")
    ]
    assert r.output.source_version == 1

    project = json.loads((FIXTURES / "petrov_project.json").read_text(encoding="utf-8"))
    project["ceiling_height"] = 2650
    p = home / "p2.json"
    p.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    assert api.import_project(ORDER, p, home=home).version == 2
    r2 = api.cut_and_save("petrov", home)
    assert [o.source_version for o in api.list_outputs("petrov", home)] == [2]
    assert "версия проекта 2" in pages_of(r2)[0]


def test_cut_from_order_writes_nothing(home):
    petrov(home)
    r = api.cut_from_order("petrov", home)
    assert r.output is None and not (home / "orders" / ORDER / "out").exists()


def test_every_part_has_a_numbered_line_in_the_list(home):
    petrov(home)
    r = api.cut_and_save("petrov", home)
    for text, (m, s) in zip(pages_of(r), sheets_of(r), strict=True):
        for i, pl in enumerate(s.placements, 1):
            assert f"{i}. {pl.part} {pl.w}×{pl.h}" in text


def test_cut_number_labels_do_not_overlap(home):
    petrov(home)
    r = api.cut_and_save("petrov", home)
    for m, s in sheets_of(r):
        boxes = cut_label_boxes(m.sheet_size, s.cuts, r.kerf, 0.06)
        assert {b[0] for b in boxes} == {c.number for c in s.cuts}
        for i, a in enumerate(boxes):
            for b in boxes[i + 1 :]:
                assert a[3] <= b[1] or b[3] <= a[1] or a[4] <= b[2] or b[4] <= a[2], (s.number, a, b)


def big_sheet(n=130):
    """Синтетический лист: сетка из n деталей и по резу на деталь."""
    placements = [
        Placement(part=f"Д{i:03}-длинная-маркировка", x=10 + i, y=10, w=20, h=20, rotated=i % 2 == 0)
        for i in range(n)
    ]
    cuts = [
        Cut(
            number=i + 1,
            direction="vertical",
            position=30 + i,
            start=10,
            end=2060,
            length=2050,
            kind="separate",
        )
        for i in range(n)
    ]
    sheet = SheetCut(number=1, placements=placements, cuts=cuts, parts_area_m2=0.1, utilization=0.5)
    return MaterialCut(
        material="ldsp_16_white", name="ЛДСП", sheet_size=(2800, 2070), texture=False, sheet_count=1,
        parts_count=n, parts_area_m2=0.1, sheet_area_m2=5.8, utilization=0.5, waste_m2=1.0, algorithm="x",
        sheets=[sheet],
    )  # fmt: skip


def test_text_lists_stay_in_their_columns_and_in_the_page():
    register_fonts()
    area = Area(28, 50, 540, 760)
    lines = [(f"{i}. Д{i:03}-длинная-маркировка 20×20 мм, x={i} y=10, повёрнута", False) for i in range(250)]
    pages = place_lines(lines, area, first_top=300)
    assert len(pages) >= 2
    boxes = []
    for page in pages:
        placed = [
            (p.x, p.y, p.x + stringWidth(p.text, FONT_BOLD if p.bold else FONT, p.size), p.y + p.size)
            for p in page
        ]
        assert all(b[0] >= area.x and b[2] <= area.x + area.w and b[1] >= area.y for b in placed)
        for i, a in enumerate(placed):
            for b in placed[i + 1 :]:
                assert a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1], (a, b)
        boxes += page
    assert [p.text for p in boxes] == [t for t, _ in lines]  # ничего не потеряно, порядок сохранён
    assert min(p.size for p in boxes) >= 5.5  # шрифт не мельчает ниже читаемого


def test_huge_sheet_continues_on_extra_pages_with_all_text(tmp_path):
    m = big_sheet()
    path = tmp_path / "c.pdf"
    write_cutting_map(
        path,
        [m],
        kerf=4,
        trim=10,
        order=ORDER,
        version=1,
        mock=False,
        on=__import__("datetime").date(2026, 11, 14),
    )
    pages = [p.extract_text() for p in PdfReader(path).pages]
    assert len(pages) >= 2
    assert "лист 1, продолжение" not in pages[0] and all("лист 1, продолжение" in t for t in pages[1:])
    joined = "\n".join(pages)
    for pl in m.sheets[0].placements:
        assert pl.part in joined
    for c in m.sheets[0].cuts:
        assert f"Рез {c.number}:" in joined

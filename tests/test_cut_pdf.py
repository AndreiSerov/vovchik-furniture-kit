"""Карта раскроя PDF (2.2) и учёт выводов: через `kitchen.api`, PDF читаем обратно `pypdf`."""

import json
from pathlib import Path

from kitchen import api
from pypdf import PdfReader

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

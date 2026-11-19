"""Карта раскроя PDF (2.2) и учёт выводов: через `kitchen.api`, PDF читаем обратно `pypdf`."""

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import pytest
from kitchen import api
from kitchen.cutting import Cut, MaterialCut, Placement, SheetCut
from kitchen.cutting_pdf import place_lines, scheme_labels, scheme_scale, write_cutting_map
from kitchen.errors import OrderError
from kitchen.pdf import FONT, FONT_BOLD, Area, Stamp, page_area, register_fonts
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


def test_failed_output_commit_preserves_previous_pdf_and_record(home):
    petrov(home)
    original = api.cut_and_save("petrov", home)
    path = Path(original.output.file)
    before = path.read_bytes()
    previous = api.list_outputs("petrov", home)

    project = json.loads((FIXTURES / "petrov_project.json").read_text(encoding="utf-8"))
    project["ceiling_height"] = 2650
    p = home / "p2.json"
    p.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    assert api.import_project(ORDER, p, home=home).version == 2

    # Отложенный внешний ключ вызывает сбой именно при commit, после записи новой строки outputs.
    with sqlite3.connect(home / "orders" / "kitchen.db") as conn:
        conn.executescript("""
            CREATE TABLE output_commit_failure (
                order_id INTEGER REFERENCES orders(id) DEFERRABLE INITIALLY DEFERRED
            );
            CREATE TRIGGER fail_output_commit AFTER INSERT ON outputs BEGIN
                INSERT INTO output_commit_failure VALUES (-1);
            END;
        """)

    with pytest.raises(sqlite3.IntegrityError):
        api.cut_and_save("petrov", home)
    assert path.read_bytes() == before
    assert api.list_outputs("petrov", home) == previous
    assert list(path.parent.iterdir()) == [path]


def test_concurrent_failed_saves_restore_the_original_pdf(home, monkeypatch):
    petrov(home)
    original = api.cut_and_save("petrov", home, on=date(2026, 1, 1))
    path = Path(original.output.file)
    before = path.read_bytes()
    previous = api.list_outputs("petrov", home)
    with sqlite3.connect(home / "orders" / "kitchen.db") as conn:
        conn.executescript("""
            CREATE TABLE output_commit_failure (
                order_id INTEGER REFERENCES orders(id) DEFERRABLE INITIALLY DEFERRED
            );
            CREATE TRIGGER fail_output_commit AFTER INSERT ON outputs BEGIN
                INSERT INTO output_commit_failure VALUES (-1);
            END;
        """)

    installed = threading.Event()
    release = threading.Event()
    restoring = threading.Event()
    finish_restore = threading.Event()
    second_ready = threading.Event()
    copied = threading.Event()
    replace = Path.replace
    copy2 = api.shutil.copy2

    def controlled_replace(source, target):
        if threading.current_thread().name == "first" and source.name == "previous.pdf":
            restoring.set()
            assert finish_restore.wait(10)
        result = replace(source, target)
        if threading.current_thread().name == "first" and target == path and source.name != "previous.pdf":
            installed.set()
            assert release.wait(10)
        if threading.current_thread().name == "second" and target.name == path.name and target != path:
            second_ready.set()
        return result

    def observed_copy(source, target, *args, **kwargs):
        if threading.current_thread().name == "second" and source == path:
            copied.set()
        return copy2(source, target, *args, **kwargs)

    monkeypatch.setattr(Path, "replace", controlled_replace)
    monkeypatch.setattr(api.shutil, "copy2", observed_copy)

    def save(name, day):
        threading.current_thread().name = name
        with pytest.raises(sqlite3.IntegrityError):
            api.cut_and_save("petrov", home, on=date(2026, 1, day))

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(save, "first", 2)
        try:
            assert installed.wait(10)
            second = pool.submit(save, "second", 3)
            assert second_ready.wait(10)
            # Дать второму потоку подойти к установке, пока первый держит запись.
            assert not copied.wait(1)
            release.set()
            assert restoring.wait(10)
            # Блокировка должна пережить неудачный commit и дождаться восстановления.
            assert not copied.wait(1)
        finally:
            release.set()
            finish_restore.set()
        first.result(timeout=10)
        second.result(timeout=10)

    assert path.read_bytes() == before
    assert api.list_outputs("petrov", home) == previous
    assert list(path.parent.iterdir()) == [path]


def test_old_calculation_cannot_replace_newer_output(home, monkeypatch):
    petrov(home)
    prepared = threading.Event()
    release = threading.Event()
    write = api.write_cutting_map

    def delayed_write(*args, **kwargs):
        write(*args, **kwargs)
        if threading.current_thread().name == "old-calculation":
            prepared.set()
            assert release.wait(10)

    monkeypatch.setattr(api, "write_cutting_map", delayed_write)

    def old_calculation():
        threading.current_thread().name = "old-calculation"
        with pytest.raises(OrderError, match="верси"):
            api.cut_and_save("petrov", home)

    with ThreadPoolExecutor(max_workers=1) as pool:
        old = pool.submit(old_calculation)
        try:
            assert prepared.wait(10)
            project = json.loads((FIXTURES / "petrov_project.json").read_text(encoding="utf-8"))
            project["ceiling_height"] = 2650
            p = home / "p2.json"
            p.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
            assert api.import_project(ORDER, p, home=home).version == 2
            new = api.cut_and_save("petrov", home)
            path = Path(new.output.file)
            before = path.read_bytes()
            assert [o.source_version for o in api.list_outputs("petrov", home)] == [2]
        finally:
            release.set()
        old.result(timeout=10)

    assert path.read_bytes() == before
    assert [o.source_version for o in api.list_outputs("petrov", home)] == [2]
    assert "версия проекта 2" in pages_of(new)[0]
    assert list(path.parent.iterdir()) == [path]


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


def test_cut_numbers_overlap_neither_each_other_nor_part_labels_at_real_scale(home):
    petrov(home)
    r = api.cut_and_save("petrov", home)
    k = scheme_scale(page_area().w, (2800, 2070))
    assert 0.18 < k < 0.2  # реальный масштаб страницы, а не придуманный
    for m, s in sheets_of(r):
        assert scheme_scale(page_area().w, m.sheet_size) == k
        parts, cuts = scheme_labels(m.sheet_size, s, r.kerf, k)
        assert {n for n, _ in cuts} == {c.number for c in s.cuts}
        assert [p.n for p in parts] == list(range(1, len(s.placements) + 1))

        def clear(a, b):
            return a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1]

        for i, (_, a) in enumerate(cuts):
            assert all(clear(a, b) for _, b in cuts[i + 1 :]), (s.number, a)
            assert all(clear(a, p.box) for p in parts), (s.number, a)
        for p, pl in zip(parts, s.placements, strict=True):
            # подпись — одна из трёх допустимых целиком, а не обрезок
            assert p.text in (f"{pl.part} {pl.w}×{pl.h}", pl.part, str(p.n)), p


def test_label_is_shortened_when_cut_number_would_cover_it():
    """Короткий рез у середины подписи: свободного места для номера нет, подпись детали укорачивается."""
    sheet = SheetCut(
        number=1,
        placements=[Placement(part="Деталь-длинная", x=10, y=10, w=300, h=40, rotated=False)],
        cuts=[Cut(number=7, direction="vertical", position=122, start=26, end=34, length=8, kind="separate")],
        parts_area_m2=0.01,
        utilization=0.1,
    )
    parts, cuts = scheme_labels((400, 100), sheet, 4, 1.0)
    (label,), ((_, box),) = parts, cuts
    assert label.text != "Деталь-длинная 300×40"  # полная подпись закрыта линией реза и номером
    assert (
        box[2] <= label.box[0] or label.box[2] <= box[0] or box[3] <= label.box[1] or label.box[3] <= box[1]
    )


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
        stamp=Stamp(ORDER, 1, False, date(2026, 11, 14)),
    )
    pages = [p.extract_text() for p in PdfReader(path).pages]
    assert len(pages) >= 2
    assert "лист 1, продолжение" not in pages[0] and all("лист 1, продолжение" in t for t in pages[1:])
    joined = "\n".join(pages)
    for pl in m.sheets[0].placements:
        assert pl.part in joined
    for c in m.sheets[0].cuts:
        assert f"Рез {c.number}:" in joined

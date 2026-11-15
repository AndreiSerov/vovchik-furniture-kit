"""Тикет 19, срез (б): карта присадки PDF, `kitchen drilling`, учёт вывода. PDF читаем обратно `pypdf`."""

import json
import re
from pathlib import Path

import pytest
from kitchen import api
from kitchen.cli import app
from kitchen.errors import KitchenError
from pypdf import PdfReader
from typer.testing import CliRunner

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ORDER = "2026-047-petrov"
MOCK = "МОК-ДАННЫЕ — НЕ ДЛЯ КЛИЕНТА"
runner = CliRunner()
TITLE = re.compile(r"^Деталь (\S+) — ", re.MULTILINE)


def petrov(home):
    api.brief_save(ORDER, FIXTURES / "petrov_brief.json", home)
    api.import_project(ORDER, FIXTURES / "petrov_project.json", home=home)


def pages_of(result):
    return [p.extract_text() for p in PdfReader(result.output.file).pages]


def test_map_has_each_drilled_part_once_and_no_part_without_holes(home):
    petrov(home)
    r = api.drilling_and_save(ORDER, home)
    assert r.output.file == str(home / "orders" / ORDER / "out" / "drilling-map.pdf")
    pages = pages_of(r)
    titles = [t for text in pages for t in TITLE.findall(text)]
    drilled = [p.id for p in r.parts]
    assert sorted(titles) == sorted(drilled) and len(titles) == len(set(titles))
    everything = {p.id for m in api.parts_from_order(ORDER, home).modules for p in m.parts}
    assert not set(titles) & (everything - set(drilled))  # детали без отверстий страниц не занимают
    assert "Н6-фасад" not in drilled and "В1-фасад" not in drilled  # ПММ и подъёмник без чашек
    for text in pages:
        assert 2 <= len(TITLE.findall(text)) <= 4
        assert text.count(MOCK) == 2 and "версия проекта 1" in text  # водяной знак и колонтитул
    assert len(pages) == -(-len(drilled) // 4)


def test_map_without_mock_has_no_watermark_and_shows_hole_numbers(home):
    petrov(home)
    for p in (home / "workshop").glob("*.yaml"):
        p.write_text(p.read_text(encoding="utf-8").replace("mock: true", "mock: false"), encoding="utf-8")
    r = api.drilling_and_save(ORDER, home)
    assert not r.mock
    pages = pages_of(r)
    assert all("МОК" not in text for text in pages)
    text = "\n".join(pages)
    door = next(p for p in r.parts if p.id == "Н2-фасад")
    assert "базовые кромки: нижняя, левая" in text
    for h in door.holes:  # строка таблицы отверстий с расстояниями от базовых кромок
        assert f"чашка петли · пласть -y · Ø35×13 · {h.from_base[0]} · {h.from_base[1]}" in text


def test_output_is_registered_and_becomes_stale_after_new_version(home):
    petrov(home)
    r = api.drilling_and_save(ORDER, home)
    outputs = [o for o in api.list_outputs(ORDER, home) if o.kind == "drilling"]
    assert [(o.path, o.source_version) for o in outputs] == [(f"orders/{ORDER}/out/drilling-map.pdf", 1)]
    assert r.output.source_version == 1
    project = json.loads((FIXTURES / "petrov_project.json").read_text(encoding="utf-8"))
    project["ceiling_height"] = 2650
    v2 = home / "v2.json"
    v2.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    api.import_project(ORDER, v2, home=home)
    stale = [s for s in api.status(ORDER, home).stale if s.kind == "drilling"]
    assert [s.command for s in stale] == [f"kitchen drilling {ORDER}"]
    assert "карта присадки" in api.status(ORDER, home).text
    api.drilling_and_save(ORDER, home)
    assert not [s for s in api.status(ORDER, home).stale if s.kind == "drilling"]


def test_drilling_from_order_writes_nothing(home):
    petrov(home)
    r = api.drilling_from_order(ORDER, home)
    assert r.output is None and r.source.version == 1
    assert not (home / "orders" / ORDER / "out" / "drilling-map.pdf").exists()


def test_cli_order_json_matches_api_and_text_names_pdf(home):
    petrov(home)
    result = runner.invoke(app, ["drilling", "petrov", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    expected = json.loads(api.drilling_from_order(ORDER, home).model_dump_json())
    assert data["parts"] == expected["parts"] and data["checks"] == expected["checks"]
    assert data["output"]["kind"] == "drilling" and data["mock"] is True
    text = runner.invoke(app, ["drilling", "petrov"])
    assert text.exit_code == 0, text.output
    assert "drilling-map.pdf" in text.output and "конфирматы: отверстий в пласть 102 = 102" in text.output
    assert MOCK in text.output


def test_cli_project_writes_no_files_and_needs_exactly_one_source(home):
    project = home / "p.json"
    project.write_text((FIXTURES / "petrov_project.json").read_text(encoding="utf-8"), encoding="utf-8")
    result = runner.invoke(app, ["drilling", "--project", str(project), "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["output"] is None
    assert not (home / "orders").exists()
    for args in (["drilling"], ["drilling", "petrov", "--project", str(project)]):
        bad = runner.invoke(app, args)
        assert bad.exit_code != 0 and "либо заказ" in bad.output


def test_order_without_drilled_parts_gets_one_page_saying_so(home):
    """Только фасад ПММ: отверстий нет — одна страница с пометкой, сверка по нулям."""
    project = json.loads((FIXTURES / "petrov_project.json").read_text(encoding="utf-8"))
    project["walls"] = [
        {
            "label": "A",
            "length": 450,
            "rows": [
                {"kind": "lower", "modules": [{"label": "Н6", "template": "dishwasher_panel", "width": 450}]}
            ],
        }
    ]
    project["appliances"] = [{"type": "dishwasher", "width": 450, "module": "Н6"}]
    path = home / "pmm.json"
    path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    api.order_new(ORDER, home=home)
    api.import_project(ORDER, path, home=home)
    r = api.drilling_and_save(ORDER, home)
    assert r.parts == [] and {(c.holes, c.expected) for c in r.checks} == {(0, 0)}
    pages = pages_of(r)
    assert len(pages) == 1 and "Деталей с отверстиями нет." in pages[0]


def petrov_layout(home):
    result = api.drilling_from_project(FIXTURES / "petrov_project.json", home)
    return result, api.drilling_map_layout(result)


def schemes(pages):
    return {s.part: s for page in pages for s in page.schemes}


def test_drilling_headers_fit(home):
    """Заголовок детали, подзаголовок, вид, строки таблицы — в своей ячейке; шапка — в поле страницы.
    Длинный заголовок (`В1-дно-выт-1` — «передняя планка дна вытяжки») ужимается, а не вылезает."""
    _, pages = petrov_layout(home)
    mm = 72 / 25.4
    for page in pages:
        for line in page.header:
            assert 10 * mm - 1e-6 <= line.x and line.x + line.width <= 200 * mm + 1e-6, (
                line.text
            )  # A4 без полей 10 мм
        for s in page.schemes:
            x0, y0, x1, y1 = s.slot
            for t in s.texts:
                assert x0 <= t.x and t.x + t.width <= x1 + 1e-6, (s.part, t.text, t.width, x1 - x0)
                assert y0 <= t.y <= y1, (s.part, t.text)
                assert t.size >= 5.0
    title = next(t for t in schemes(pages)["В1-дно-выт-1"].texts if t.bold)
    assert title.text.startswith("Деталь В1-дно-выт-1 — ")


def test_view_and_drilling_side_are_named_from_hole_faces(home):
    """Фасад виден с лица, чашки сверлят с тыльной стороны; у правой боковины планки и конфирматы — с разных граней."""
    result, pages = petrov_layout(home)
    by_id = {p.id: p for p in result.parts}
    s = schemes(pages)
    facade = [t.text for t in s["Н2-фасад"].texts]
    assert s["Н2-фасад"].view == "+y"
    assert "вид с лица (+y), схема: +x→, +z↑" in facade
    assert "сверлить: с тыльной стороны (-y, обратная) — чашка петли" in facade
    for pid in ("Н2-бок-П", "Н2-бок-Л", "Н2-дно", "Н2-фасад"):
        sc = s[pid]
        text = " ".join(t.text for t in sc.texts[:4])
        for h in by_id[pid].holes:
            if h.surface == "face":
                visible = "видимая" if h.from_face == sc.view else "обратная"
                assert re.search(rf"{re.escape(h.from_face)},? {visible}", text), (pid, h.from_face, text)
            else:
                assert f"торец {h.edge_side}" in text, (pid, text)
    assert s["Н2-дно"].view == "+z"  # лежачая деталь — вид сверху, перед внизу


def test_base_edges_are_drawn_on_their_outline_side(home):
    _, pages = petrov_layout(home)
    s = schemes(pages)
    side = s["П1-бок-Л"]  # вправо +y (перед), вверх +z; базы нижняя и передняя
    x0, y0, x1, y1 = side.outline
    lines = {b.face: b for b in side.base_lines}
    assert lines["-z"].y1 == lines["-z"].y2 == y0 and (lines["-z"].x1, lines["-z"].x2) == (x0, x1)
    assert lines["+y"].x1 == lines["+y"].x2 == x1 and (lines["+y"].y1, lines["+y"].y2) == (y0, y1)
    bottom = s["П1-дно"]  # вправо +x, вверх −y: перед внизу; базы передняя и левая
    x0, y0, _, _ = bottom.outline
    lines = {b.face: b for b in bottom.base_lines}
    assert lines["+y"].y1 == lines["+y"].y2 == y0
    assert lines["-x"].x1 == lines["-x"].x2 == x0


def test_dimension_chains_run_from_the_base_edge(home):
    """Отметка цепочки — на расстоянии `значение × масштаб` от базовой кромки, внутрь детали."""
    result, pages = petrov_layout(home)
    by_id = {p.id: p for p in result.parts}
    for s in (sc for page in pages for sc in page.schemes):
        part = by_id[s.part]
        x0, y0, x1, y1 = s.outline
        for ch in s.chains:
            i = part.base_edges.index(ch.face)
            assert sorted(lb.value for lb in ch.labels) == sorted({h.from_base[i] for h in part.holes})
            lo, hi = (x0, x1) if ch.direction == "horizontal" else (y0, y1)
            assert ch.base in (pytest.approx(lo), pytest.approx(hi))
            inward = 1 if ch.base == pytest.approx(lo) else -1
            for lb in ch.labels:
                assert lb.tick == pytest.approx(ch.base + inward * lb.value * s.scale), (s.part, ch.face, lb)
    side = next(c for c in schemes(pages)["П1-бок-Л"].chains if c.face == "+y")
    ticks = {lb.value: lb.tick for lb in side.labels}
    assert ticks[37] > ticks[510]  # от передней кромки (справа) — влево


def test_chain_labels_do_not_overlap(home):
    _, pages = petrov_layout(home)
    for s in (sc for page in pages for sc in page.schemes):
        boxes = [lb.box for ch in s.chains for lb in ch.labels]
        texts = [(t.x, t.y - 0.25 * t.size, t.x + t.width, t.y + 0.75 * t.size) for t in s.texts]
        for a in boxes:
            for t in texts:
                assert a[2] <= t[0] or t[2] <= a[0] or a[3] <= t[1] or t[3] <= a[1], (s.part, a, t)
        for i, a in enumerate(boxes):
            for b in boxes[i + 1 :]:
                assert a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1], (s.part, a, b)
            sx0, sy0, sx1, sy1 = s.slot
            assert sx0 <= a[0] and a[2] <= sx1 and sy0 <= a[1] and a[3] <= sy1, (s.part, a)


def test_part_with_too_many_holes_is_an_error_not_a_cut_table(home):
    result = api.drilling_from_project(FIXTURES / "petrov_project.json", home)
    side = next(p for p in result.parts if p.id == "П1-бок-Л")
    crowded = side.model_copy(update={"holes": side.holes * 6})  # 84 строки таблицы
    with pytest.raises(KitchenError, match="П1-бок-Л.*не помещается"):
        api.drilling_map_layout(result.model_copy(update={"parts": [crowded]}))


# Повторное ревью тикета 19: отметки отверстий на схеме стоят на числах цепочки (не зеркально).
def test_hole_marks_sit_at_their_distances_from_base_edges(home):
    result, pages = petrov_layout(home)
    by_id = {p.id: p for p in result.parts}
    for s in (sc for page in pages for sc in page.schemes):
        part = by_id[s.part]
        x0, y0, x1, y1 = s.outline
        for h, m in zip(part.holes, s.holes, strict=True):
            for i, face in enumerate(part.base_edges):
                if face[1] == s.right[1]:
                    d = abs(m.x - (x1 if face[0] == s.right[0] else x0))
                else:
                    d = abs(m.y - (y1 if face[0] == s.up[0] else y0))
                assert d == pytest.approx(h.from_base[i] * s.scale), (s.part, face, h.from_base)


# Уборка этапа 4 (тикет 46): направление отрезка, короткие варианты строк, запас строк цепочек.
def test_end_hole_segment_runs_from_its_mouth_into_the_part(home):
    """Отверстие в торец на схеме — отрезок от устья на кромке внутрь детали, а не наружу."""
    _, pages = petrov_layout(home)
    marks = [(s, h) for s in schemes(pages).values() for h in s.holes if h.surface == "end"]
    assert marks
    eps = 0.01
    for s, h in marks:
        x0, y0, x1, y1 = s.outline
        on_edge = min(abs(h.x - x0), abs(h.x - x1)) < eps or min(abs(h.y - y0), abs(h.y - y1)) < eps
        assert on_edge, (s.part, h)
        assert x0 - eps <= h.x2 <= x1 + eps and y0 - eps <= h.y2 <= y1 + eps, (s.part, h)
        assert (h.x2, h.y2) != (h.x, h.y)


def test_long_part_name_and_material_fall_back_to_short_lines(home):
    result = api.drilling_from_project(FIXTURES / "petrov_project.json", home)
    side = next(p for p in result.parts if p.id == "П1-бок-Л")
    long_name = "очень длинное название детали " * 6
    long_part = side.model_copy(update={"name": long_name, "material": "материал с длинным названием " * 6})
    scheme = schemes(api.drilling_map_layout(result.model_copy(update={"parts": [long_part]})))["П1-бок-Л"]
    title, sub = scheme.texts[0].text, scheme.texts[1].text
    assert title.startswith("Деталь П1-бок-Л — ") and long_name not in title
    assert title.endswith(f"{side.length}×{side.width}×{side.thickness} мм")
    assert sub.startswith("базовые кромки: ") and "материал" not in sub


def test_chain_reserve_never_shrinks_when_another_axis_grows(home, monkeypatch):
    """Строк цепочек при подборе масштаба: ось, где запас был больше, запас не теряет, пока растёт другая ось.
    Число строк подставляем по порядку вызовов: сначала запас (вправо, вверх), затем каждый новый масштаб."""
    result = api.drilling_from_project(FIXTURES / "petrov_project.json", home)
    bottom = next(p for p in result.parts if p.id == "П1-дно")

    def scale(plan):
        calls = iter(plan)

        def rows(values, k, size):
            out = [0] * len(values)
            if out:
                out[-1] = next(calls, 1) - 1
            return out

        monkeypatch.setattr("kitchen.drilling_pdf._levels", rows)
        pages = api.drilling_map_layout(result.model_copy(update={"parts": [bottom]}))
        scheme = schemes(pages)["П1-дно"]
        return scheme.scale, scheme.outline, [t.y for t in scheme.texts]

    shrinking = scale([3, 1, 2, 2, 2, 2])  # запас (3, 1); при новом масштабе (2, 2): рост по второй оси
    steady = scale([3, 2, 3, 2])  # запас (3, 2) сразу
    assert shrinking == steady  # под контуром остаётся место на три строки, как в запасе


def test_end_hole_segment_into_front_and_back_edge_is_vertical_and_inward(home):
    """У детали в плоскости x–y отверстие в передний и задний торец рисуется отрезком вдоль вертикали схемы."""
    result = api.drilling_from_project(FIXTURES / "petrov_project.json", home)
    bottom = next(p for p in result.parts if p.id == "П1-дно")
    base = next(h for h in bottom.holes if h.surface == "end")
    back = base.model_copy(
        update={
            "from_face": "+y",
            "edge_side": "L1",
            "center": base.center.model_copy(update={"x": 300, "y": 560}),
        }
    )
    front = back.model_copy(
        update={"from_face": "-y", "edge_side": "L2", "center": back.center.model_copy(update={"y": 0})}
    )
    part = bottom.model_copy(update={"holes": [back, front]})
    scheme = schemes(api.drilling_map_layout(result.model_copy(update={"parts": [part]})))["П1-дно"]
    _, y0, _, y1 = scheme.outline
    marks = [h for h in scheme.holes if h.surface == "end"]
    assert len(marks) == 2
    for h in marks:
        assert abs(h.x2 - h.x) < 1e-6 and abs(h.y2 - h.y) > 1  # отрезок вертикальный
        assert y0 - 0.01 <= h.y2 <= y1 + 0.01  # и уходит внутрь контура

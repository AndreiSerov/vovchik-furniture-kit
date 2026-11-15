"""Тикет 42: 3D в HTML (`kitchen 3d` пишет рядом с PDF видов `kitchen-3d.html`).

Логика — через API (`api.views_and_save`, `api.views_from_order`, `api.views_from_project`); HTML разбирает
`html.parser`, встроенный JSON — `json`. Работу скрипта в браузере проверяет `tests/test_views_js.py` (node)."""

import json
import re
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

import pytest
from kitchen import api
from kitchen.cli import app
from kitchen.errors import OrderError
from kitchen.result import MOCK_BANNER
from typer.testing import CliRunner

from tests.test_views import ORDER, PETROV, import_v2, petrov_labels, petrov_order, unmock

runner = CliRunner()


class Page(HTMLParser):
    """Теги страницы: тексты скриптов по `id`, атрибуты с адресами, видимый текст вне скриптов."""

    def __init__(self, text: str):
        super().__init__(convert_charrefs=True)
        self.scripts: list[tuple[dict, str]] = []
        self.tags: list[tuple[str, dict]] = []
        self.texts: list[str] = []
        self.by_id: dict[str, str] = {}
        self.styles: list[str] = []
        self._stack: list[tuple[str, dict]] = []
        self._buf = ""
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.append((tag, attrs))
        self._stack.append((tag, attrs))
        self._buf = ""

    def handle_endtag(self, tag):
        top = self._stack.pop() if self._stack else (tag, {})
        if tag == "script":
            self.scripts.append((top[1], self._buf))
        elif tag == "style":
            self.styles.append(self._buf)
        elif top[1].get("id"):
            self.by_id[top[1]["id"]] = self._buf
        self._buf = ""

    def handle_data(self, data):
        self._buf += data
        if not self._stack or self._stack[-1][0] != "script":
            self.texts.append(data)


def html_of(home, **kw):
    result = api.views_and_save(ORDER, home, **kw)
    path = Path(result.output_html.file)
    return result, path.read_text(encoding="utf-8"), Page(path.read_text(encoding="utf-8"))


def embedded(page: Page) -> dict:
    (data,) = [body for attrs, body in page.scripts if attrs.get("id") == "scene-data"]
    return json.loads(data)


def test_html_is_written_next_to_the_pdf_with_title_and_header(home):
    petrov_order(home)
    result, text, page = html_of(home)
    assert result.output_html.file == str(home / "orders" / ORDER / "out" / "kitchen-3d.html")
    assert result.output.file.endswith("kitchen-views.pdf")  # PDF видов — как в тикете 41
    assert f"<title>Кухня {ORDER} · версия проекта 1</title>" in text
    header = "".join(page.texts)
    assert re.search(rf"Заказ {ORDER} · версия проекта 1 · \d\d\.\d\d\.\d\d\d\d", header)


def test_embedded_json_has_every_module_label_with_its_dimensions(home):
    petrov_order(home)
    _, _, page = html_of(home)
    scene = embedded(page)
    labels = [label for ms in petrov_labels().values() for label in ms]
    assert sorted({m["label"] for m in scene["modules"]}) == sorted(set(labels))  # П1 — один раз
    placed = api.placement_from_order(ORDER, home)
    boxes = {m.label: m for m in placed.modules}
    by_label = {m["label"]: m for m in scene["modules"]}
    for label, place in boxes.items():
        w, h, d = (round(v) for v in (place.size.x, place.size.z, place.size.y))
        assert by_label[label]["size_label"] == f"Ш×В×Г {w}×{h}×{d} мм", label
        assert [by_label[label][k] for k in ("width", "height", "depth")] == [w, h, d]
    gap = by_label["ПМ1"]
    assert gap["gap"] is True and gap["width"] == 264


def test_embedded_scene_is_the_scene_of_json(home):
    petrov_order(home)
    result, _, page = html_of(home)
    data = json.loads(runner.invoke(app, ["3d", "petrov", "--json"]).output)
    assert embedded(page) == data["scene3d"] == json.loads(result.scene3d.model_dump_json())


def test_scene_structure_keeps_room_for_parts_later(home):
    """Тикет 37 добавит детали и фасады: у модуля уже массив `boxes`, у коробки — вид (`part`)."""
    petrov_order(home)
    scene = embedded(html_of(home)[2])
    module = next(m for m in scene["modules"] if m["label"] == "Н1")
    assert [b["part"] for b in module["boxes"]] == ["body"]
    box = module["boxes"][0]
    assert len(box["min"]) == len(box["max"]) == 3 and all(a < b for a, b in zip(box["min"], box["max"]))
    assert scene["camera"]["height"] == 1600
    assert {w["label"] for w in scene["walls"]} == {"A", "Б"}


def test_three_version_is_written_and_there_are_no_network_links(home):
    petrov_order(home)
    _, text, page = html_of(home)
    assert "three.js r147 (0.147.0)" in text
    # теги: ни src/href на сеть, ни link, ни iframe, ни внешние скрипты
    for tag, attrs in page.tags:
        assert tag not in {"link", "iframe", "object", "embed"}, tag
        assert tag != "script" or "src" not in attrs
        for name in ("src", "href", "action", "data", "poster", "srcset"):
            assert not re.match(r"\s*(https?:)?//", attrs.get(name) or ""), (tag, name)
    # собственный код страницы: ни загрузок, ни импортов, ни адресов
    own = [body for attrs, body in page.scripts if attrs.get("id") in {"viewer", "scene-data", "boot"}]
    assert len(own) == 3
    for body in own:
        assert not re.search(r"https?:|\bimport\b|\bfetch\b|XMLHttpRequest|WebSocket|sendBeacon|//cdn", body)
    # стили: ни url(), ни @import
    css = "".join(page.styles)
    assert "@import" not in css and "url(" not in css
    # сторонний код (three.js) — только его собственные константы-пространства имён, а не адреса загрузки
    vendor = "".join(body for attrs, body in page.scripts if attrs.get("id") in {"three", "orbit"})
    urls = set(re.findall(r"https?://[^\s\"'`)]+", vendor))
    assert urls == {"http://www.w3.org/1999/xhtml", "https://github.com/mrdoob/three.js/pull/23228"}


def test_mock_banner_is_red_and_cannot_be_closed_and_absent_without_mock(home):
    petrov_order(home)
    _, text, page = html_of(home)
    assert page.by_id["mock-banner"].strip() == MOCK_BANNER
    assert "button" not in {t for t, a in page.tags} and "close" not in text.lower().split("<script")[0]
    unmock(home)
    result, text, page = html_of(home)
    assert not result.mock and "mock-banner" not in page.by_id and "МОК" not in text.split("<script")[0]


def test_fallback_text_is_present_for_when_3d_does_not_start(home):
    petrov_order(home)
    _, _, page = html_of(home)
    assert page.by_id["fallback"].strip() == "Откройте файл в браузере (Safari или Chrome)"


def test_captions_are_the_composition_lines_of_the_quote(home):
    petrov_order(home)
    scene = embedded(html_of(home)[2])
    quote = api.quote_from_order(ORDER, 35, home)
    lines = {m.label: m.text for m in quote.modules}
    captions = {m["label"]: m["caption"] for m in scene["modules"]}
    assert set(captions) == set(lines)
    for label, text in lines.items():
        assert captions[label] == f"{label} — {text}", label
    assert captions["ПМ1"] == "ПМ1 — пустое место, 264 мм"
    assert captions["Н1"].startswith("Н1 — ") and "модуль" in captions["Н1"]


def test_gap_with_an_appliance_is_named_by_it(home):
    from tests.test_placement import petrov

    data = petrov()
    data["appliances"].append({"type": "fridge", "width": 600, "height": 1800, "depth": 600, "module": "ПМ1"})
    path = home / "p.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    scene = api.views_from_project(path, home).scene3d
    assert {m.label: m.caption for m in scene.modules}["ПМ1"] == "ПМ1 — место под холодильник, 264 мм"


def break_module_n3(home):
    """Правка мастерской: Н3 перестал строиться (ошибка проверок `module_build`), проект тот же."""
    yaml = home / "workshop" / "construction.yaml"
    yaml.write_text(
        yaml.read_text(encoding="utf-8").replace("макс_ширина: 150}", "макс_ширина: 100}"), encoding="utf-8"
    )


def test_project_with_check_errors_gets_the_banner_and_still_draws(home):
    petrov_order(home)
    break_module_n3(home)
    result, _, page = html_of(home)
    assert [f.code for f in result.errors] == ["module_build"]
    assert page.by_id["errors-banner"].strip() == "Проверки: 1 ошибка — сверьте проект"
    scene = embedded(page)
    n3 = next(m for m in scene["modules"] if m["label"] == "Н3")
    assert n3["caption"].startswith("Н3 — ") and n3["boxes"]  # голая коробка с подписью без наполнения
    assert len(scene["modules"]) == sum(len(ms) for ms in petrov_labels().values())  # все на месте


def test_row_sum_error_gets_the_banner_and_still_draws(home):
    petrov_order(home)
    yaml = home / "workshop" / "construction.yaml"
    text = yaml.read_text(encoding="utf-8")
    before = "верхний:\n  высота: 720\n  глубина: 320"
    assert before in text
    # глубже верхние — иное угловое смещение: сумма верхнего ряда стены Б больше не сходится с длиной стены
    yaml.write_text(text.replace(before, before.replace("320", "360")), encoding="utf-8")
    result, _, page = html_of(home)
    codes = [f.code for f in result.errors]
    assert "row_sum" in codes
    assert (
        page.by_id["errors-banner"].strip()
        == f"Проверки: {len(codes)} {'ошибка' if len(codes) == 1 else 'ошибки'} — сверьте проект"
    )
    assert len(embedded(page)["modules"]) == 14


def test_clean_project_has_no_errors_banner(home):
    petrov_order(home)
    assert "errors-banner" not in html_of(home)[2].by_id


def test_placement_error_refuses_and_writes_no_html(home):
    from tests.test_placement import petrov

    petrov_order(home)
    # верх пенала 2800 выше потолка 2700: проверки проходят, расстановка нет
    path = home / "v2.json"
    path.write_text(json.dumps(petrov(П1={"height": 2700}), ensure_ascii=False), encoding="utf-8")
    api.import_project(ORDER, path, home=home)
    with pytest.raises(OrderError) as e:
        api.views_and_save(ORDER, home)
    assert "Верх верхних модулей 2800 мм выше потолка 2700 мм." in e.value.problems
    assert not (home / "orders" / ORDER / "out" / "kitchen-3d.html").exists()


def test_walls_without_a_corner_module_refuse(home):
    from tests.test_placement import petrov

    path = home / "p.json"
    path.write_text(
        json.dumps(petrov(Н4={"corner": None}, В4={"corner": None}), ensure_ascii=False), encoding="utf-8"
    )
    with pytest.raises(OrderError) as e:
        api.views_from_project(path, home)
    assert e.value.problems[0].startswith("Стены A и Б не соединены угловым модулем")


def test_both_outputs_are_registered_and_stale_after_new_version(home):
    petrov_order(home)
    api.views_and_save(ORDER, home)
    outputs = {o.kind: o for o in api.list_outputs(ORDER, home) if o.kind in {"views", "views_html"}}
    assert outputs["views"].path == f"orders/{ORDER}/out/kitchen-views.pdf"
    assert outputs["views_html"].path == f"orders/{ORDER}/out/kitchen-3d.html"
    assert {o.source_version for o in outputs.values()} == {1}
    import_v2(home)
    stale = {s.kind: s for s in api.status(ORDER, home).stale if s.kind in {"views", "views_html"}}
    assert set(stale) == {"views", "views_html"}
    assert {s.command for s in stale.values()} == {f"kitchen 3d {ORDER}"}
    api.views_and_save(ORDER, home)
    assert not [s for s in api.status(ORDER, home).stale if s.kind.startswith("views")]


def test_project_mode_writes_no_files_and_carries_the_scene(home):
    path = home / "p.json"
    path.write_text(PETROV.read_text(encoding="utf-8"), encoding="utf-8")
    result = api.views_from_project(path, home)
    assert result.output is None and result.output_html is None
    assert result.scene3d.modules and not (home / "orders").exists()
    data = json.loads(runner.invoke(app, ["3d", "--project", str(path), "--json"]).output)
    assert data["output_html"] is None and data["scene3d"]["camera"]["height"] == 1600


def test_cli_prints_the_3d_line_after_the_pdf_line(home):
    petrov_order(home)
    out = runner.invoke(app, ["3d", "petrov"]).output.strip().splitlines()
    body = [line for line in out if line and line != MOCK_BANNER]
    html = home / "orders" / ORDER / "out" / "kitchen-3d.html"
    pdf = next(i for i, line in enumerate(body) if line.startswith("Виды PDF: "))
    assert body[pdf + 1] == f"3D: {html}"


def test_html_is_deterministic_for_one_date(home):

    petrov_order(home)
    a = api.views_and_save(ORDER, home, on=date(2026, 11, 16))
    first = Path(a.output_html.file).read_bytes()
    api.views_and_save(ORDER, home, on=date(2026, 11, 16))
    assert Path(a.output_html.file).read_bytes() == first


def test_camera_looks_at_the_corner_from_the_room_side_at_1600(home):
    """Камера может отойти за стены напротив, чтобы кухня влезла в кадр (решение мастера 2026-11-16)."""
    petrov_order(home)
    scene = embedded(html_of(home)[2])
    cam = scene["camera"]
    corner = scene["walls"][0]["end"]  # конец A встречается с началом Б
    assert cam["target"][:2] == corner and cam["height"] == 1600
    floor = scene["floor"]
    dx, dy = cam["direction"]
    assert abs(dx * dx + dy * dy - 1) < 1e-3
    probe = (corner[0] + dx * 100, corner[1] + dy * 100)  # шаг от угла в сторону камеры остаётся в комнате
    assert floor["min"][0] <= probe[0] <= floor["max"][0] and floor["min"][1] <= probe[1] <= floor["max"][1]


def test_walls_face_into_the_room(home):
    petrov_order(home)
    scene = embedded(html_of(home)[2])
    floor = scene["floor"]
    cx, cy = (floor["min"][0] + floor["max"][0]) / 2, (floor["min"][1] + floor["max"][1]) / 2
    for wall in scene["walls"]:
        mx, my = (wall["start"][0] + wall["end"][0]) / 2, (wall["start"][1] + wall["end"][1]) / 2
        nx, ny = wall["normal"]
        assert (cx - mx) * nx + (cy - my) * ny > 0, wall["label"]
        assert wall["height"] == 2400 or wall["height"] > 0


def test_boxes_are_in_the_facade_colour_and_unknown_colour_is_grey(home):
    data = json.loads(PETROV.read_text(encoding="utf-8"))
    data["materials"]["facades_upper_color"] = "сиреневый"
    path = home / "p.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    rgb = {m.label: m.boxes[0].rgb for m in api.views_from_project(path, home).scene3d.modules}
    assert rgb["Н1"] == "#CAC4B0"  # RAL 7044
    assert rgb["В2"] == "#B0B0B0"  # нераспознанный — серый


def test_broken_module_caption_is_the_template_title_not_its_id(home):
    petrov_order(home)
    break_module_n3(home)
    n3 = next(m for m in embedded(html_of(home)[2])["modules"] if m["label"] == "Н3")
    assert "base_bottle" not in n3["caption"]
    assert re.fullmatch(r"Н3 — [а-яё -]+, 150 мм", n3["caption"]), n3["caption"]


def test_own_code_has_no_protocol_relative_addresses_or_loaders(home):
    petrov_order(home)
    _, _, page = html_of(home)
    for tag, attrs in page.tags:
        assert not (tag == "meta" and "refresh" in (attrs.get("http-equiv") or "").lower())
    own = [b for a, b in page.scripts if a.get("id") in {"viewer", "scene-data", "boot"}]
    for body in own:
        assert not re.search(r"(?<![:\w])//[\w-]+(\.[\w-]+)+", body)
        assert not re.search(
            r"createElement\(\s*['\"](script|link|iframe|img)|importScripts|EventSource|\bWorker\(", body
        )


def test_vendored_three_files_match_the_sha256_in_their_readme():
    """Тикет 47: «файлы не правятся» закреплено суммами из `vendor/three/README.md`."""
    import hashlib

    folder = Path(api.__file__).parent / "vendor" / "three"
    items = (folder / "README.md").read_text(encoding="utf-8").split("\n- ")
    for name in ("three.min.js", "OrbitControls.js"):
        item = next(i for i in items if i.lstrip("- ").startswith(f"`{name}`"))
        expected = re.search(r"sha256 `([0-9a-f]{64})`", item).group(1)
        assert hashlib.sha256((folder / name).read_bytes()).hexdigest() == expected, name

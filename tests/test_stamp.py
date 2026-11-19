"""Тикет 68: штамп (заказ, версия, мок, дата) — один объект у всех писателей PDF и HTML 3D.

Писатели вызываются напрямую: через `api` писатель со штампом не достать без записи заказа
(DECISIONS, тема «PDF»). Входные данные — из файла Петрова через `api`.
"""

import ast
from datetime import date
from pathlib import Path

import pytest
from kitchen import api
from kitchen.assembly_pdf import write_assembly
from kitchen.cutting_pdf import write_cutting_map
from kitchen.drilling_pdf import write_drilling_map
from kitchen.edging_pdf import write_edging_map
from kitchen.quote_pdf import write_quote
from kitchen.result import MOCK_BANNER
from kitchen.trip_pdf import write_trip
from kitchen.views_html import render_html
from kitchen.views_pdf import write_views
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / "engine" / "kitchen"
PETROV = Path(__file__).resolve().parent / "fixtures" / "petrov_project.json"
ORDER = "2026-047-petrov"
DAY = date(2026, 11, 18)


def stamp(*, version=None, mock=True):
    return api.Stamp(order=ORDER, version=version, mock=mock, on=DAY)


def _cutting(home, path, st):
    r = api.calculation(project=PETROV, home=home).cut()
    write_cutting_map(path, r.materials, kerf=r.kerf, kerf_declared=r.kerf_declared, trim=r.trim, stamp=st)


def _edging(home, path, st):
    r = api.calculation(project=PETROV, home=home).edging()
    write_edging_map(path, r.parts, r.edges, purchase_digits=r.purchase_digits, stamp=st)


def _drilling(home, path, st):
    r = api.calculation(project=PETROV, home=home).drilling()
    write_drilling_map(path, r.parts, r.checks, stamp=st)


def _assembly(home, path, st):
    write_assembly(path, api.calculation(project=PETROV, home=home).assembly().modules, stamp=st)


def _trip(home, path, st):
    r = api.calculation(project=PETROV, home=home).trip()
    write_trip(path, r.header, r.sections, stamp=st)


def _quote(home, path, st):
    write_quote(path, api.calculation(project=PETROV, home=home).quote(20), stamp=st)


def _views(home, path, st):
    write_views(path, api.views_layout(api.calculation(project=PETROV, home=home).views()), stamp=st)


WRITERS = [_cutting, _edging, _drilling, _assembly, _trip, _quote, _views]


def pages(home, tmp_path, writer, st):
    path = tmp_path / "out.pdf"
    writer(home, path, st)
    return [p.extract_text() for p in PdfReader(path).pages]


@pytest.mark.parametrize("writer", WRITERS)
def test_footer_and_mock_banner_come_from_the_stamp(home, tmp_path, writer):
    texts = pages(home, tmp_path, writer, stamp())
    assert f"Заказ {ORDER} · версия проекта не записана · 18.11.2026 · стр. 1 из" in texts[0]
    assert all(MOCK_BANNER in t for t in texts)


@pytest.mark.parametrize("writer", WRITERS)
def test_no_banner_without_mock(home, tmp_path, writer):
    texts = pages(home, tmp_path, writer, stamp(mock=False))
    assert not any(MOCK_BANNER in t for t in texts)


@pytest.mark.parametrize("writer", WRITERS)
def test_footer_names_the_version(home, tmp_path, writer):
    texts = pages(home, tmp_path, writer, stamp(version=3))
    assert "версия проекта 3" in texts[0] and "не записана" not in texts[0]


def html(home, st):
    scene = api.calculation(project=PETROV, home=home).views().scene3d
    return render_html(scene, stamp=st, errors=0)


def test_html_head_comes_from_the_stamp(home):
    assert f"Заказ {ORDER} · версия проекта не записана · 18.11.2026" in html(home, stamp())


def test_html_mock_banner_follows_the_stamp(home):
    assert 'id="mock-banner"' in html(home, stamp(mock=True))
    assert 'id="mock-banner"' not in html(home, stamp(mock=False))


# 68.1: у писателей и PdfDocument нет россыпи из четырёх параметров штампа
WRITER_FILES = {
    "cutting_pdf.py": ["write_cutting_map"],
    "edging_pdf.py": ["write_edging_map"],
    "drilling_pdf.py": ["write_drilling_map"],
    "assembly_pdf.py": ["write_assembly"],
    "trip_pdf.py": ["write_trip"],
    "quote_pdf.py": ["write_quote"],
    "views_pdf.py": ["write_views"],
    "views_html.py": ["write_html", "render_html"],
    "pdf.py": ["__init__"],
}
LOOSE = {"order", "version", "mock", "on"}


def test_writers_take_the_stamp_whole():
    problems = []
    for file, names in WRITER_FILES.items():
        funcs = {
            n.name: n
            for n in ast.walk(ast.parse((ENGINE / file).read_text(encoding="utf-8")))
            if isinstance(n, ast.FunctionDef)
        }
        for name in names:
            assert name in funcs, f"{file}: нет {name}"
            args = funcs[name].args
            got = {a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]}
            if got & LOOSE:
                problems.append(f"{file}:{name}: {sorted(got & LOOSE)}")
            assert "stamp" in got, f"{file}:{name}: нет параметра stamp"
    assert not problems, "Штамп — один объект kitchen.metrics.Stamp:\n" + "\n".join(problems)


# 68.5
def test_glossary_defines_the_stamp_term():
    glossary = (ROOT / "GLOSSARY.md").read_text(encoding="utf-8")
    assert any(line.startswith("| Штамп |") for line in glossary.splitlines())

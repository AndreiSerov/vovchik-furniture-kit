"""Тикет 23 (наработка остановленного варианта тикета 35): ряд начинается с углового смещения соседней стены
в его начале (`common.corner_offsets`, `row_layouts`). Суммы рядов, длины и положения расстановки берут
смещение из одного правила."""

import json
import random
from pathlib import Path

from kitchen import api

from tests.test_placement import write

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_start_offset_keeps_petrov_sums_and_lengths(home):
    result = api.calculation(project=FIXTURES / "petrov_project.json", home=home).parts()
    assert [f for f in result.findings if f.code == "row_sum"] == []
    common = {c.kind: c.length_mm for c in result.common}
    # руками (тикет 11, 32): нижний фронт 2800 + 1200 − 600 = 3400, верхние 2200 + 1200 = 3400, столешница 3400
    assert (common["plinth"], common["rail"], common["countertop"]) == (3400, 3400, 3400)
    # тот же ряд в расстановке: Б нижний — с 600, верхний — с 336
    placed = api.calculation(project=FIXTURES / "petrov_project.json", home=home).placement()
    starts = {(m.wall, m.row): m.x for m in placed.modules if m.label in ("Н5", "В5")}
    assert starts == {("Б", "lower"): 600, ("Б", "upper"): 600}
    assert [(g.label, g.x) for g in placed.gaps] == [("ПМ1", 336)]


def test_tall_of_other_row_after_start_offset_stands_after_its_row_neighbour(home):
    # Н4 последний на A: нижний ряд Б с 600; пенал П1 верхнего ряда Б на x 1200 встаёт в нижнем ряду после Н5
    # (600 + 600 = 1200), а не после Н6, как было бы при отступе ряда с нуля
    walls = [
        {"label": "A", "length": 1650, "rows": [{"kind": "lower", "modules": [
            {"label": "Н1", "template": "base_door", "width": 600},
            {"label": "Н4", "template": "base_corner", "width": 1050, "corner": {"adjacent_wall": "Б"}},
        ]}]},
        {"label": "Б", "length": 2400, "rows": [
            {"kind": "lower", "modules": [
                {"label": "Н5", "template": "base_door", "width": 600},
                {"label": "Н6", "template": "base_door", "width": 600},
            ]},
            {"kind": "upper", "modules": [
                {"label": "В5", "template": "wall_door", "width": 600},
                {"label": "В6", "template": "wall_door", "width": 600},
                {"label": "П1", "template": "tall_oven", "width": 600},
                {"label": "В7", "template": "wall_door", "width": 600},
            ]},
        ]},
    ]  # fmt: skip
    path = write(home, walls)
    findings = api.calculation(project=path, home=home).check().findings
    assert [f for f in findings if f.code in ("row_sum", "tall_overlap")] == []
    at = {m.label: m.x for m in api.calculation(project=path, home=home).placement().modules}
    assert (at["Н5"], at["П1"], at["Н6"]) == (600, 1200, 1800)


# --- фазз: две стены A → Б с угловыми и пеналами в разных рядах -----------------------------------------------

OFFSET = {"lower": 600, "upper": 336}  # мок: смещение_нижнего; верхний.глубина 320 + толщина_корпуса 16
CORNER = {"lower": ("base_corner", 1050), "upper": ("wall_corner", 600)}
PLAIN = {"lower": ("Н", "base_door"), "upper": ("В", "wall_door")}


def split(total, rnd):
    """Ширины модулей с суммой `total` (не меньше 300): куски 300–600, последний — остаток 300–900."""
    out = []
    while total > 900:
        out.append(rnd.choice([p for p in (300, 450, 600) if total - p >= 300]))
        total -= out[-1]
    return [*out, total]


def build_case(rnd):
    """Угол A–Б задан угловым модулем нижнего и (или) верхнего ряда: последним на A или первым на Б.
    Ожидание — ручные отступы от начала стены по блокам, без движка."""
    holder = {k: rnd.choice(["A", "Б", None]) for k in ("lower", "upper")}
    if holder == {"lower": None, "upper": None}:
        holder["lower"] = "A"
    n = 0

    def label(prefix):
        nonlocal n
        n += 1
        return f"{prefix}{n}"

    walls, expected = [], {}
    for w in ("A", "Б"):
        start = {k: OFFSET[k] if w == "Б" and holder[k] == "A" else 0 for k in holder}
        end = {k: OFFSET[k] if w == "A" and holder[k] == "Б" else 0 for k in holder}
        blocks = [("span", rnd.randrange(2400, 3301, 150))]
        for _ in range(rnd.randint(0, 4)):
            if rnd.random() < 0.5:
                # пеналы подряд — в одном ряду: порядок соседних пеналов из разных рядов проект не задаёт
                row = blocks[-1][2] if blocks[-1][0] == "tall" else rnd.choice(["lower", "upper"])
                blocks.append(("tall", rnd.choice([450, 600]), row))
            else:
                blocks.append(("span", rnd.randrange(600, 2101, 150)))
        blocks.append(("span", rnd.randrange(2400, 3301, 150)))
        rows = {"lower": [], "upper": []}
        x = 0
        for i, block in enumerate(blocks):
            first, last = i == 0, i == len(blocks) - 1
            if block[0] == "tall":
                name = label("П")
                rows[block[2]].append({"label": name, "template": "tall_oven", "width": block[1]})
                expected[name] = (w, x)
                x += block[1]
                continue
            for kind in ("lower", "upper"):
                at = x + (start[kind] if first else 0)
                width = block[1] - (start[kind] if first else 0) - (end[kind] if last else 0)
                pieces = []
                if first and w == "Б" and holder[kind] == "Б":
                    pieces.append(("corner", CORNER[kind][1]))
                tail = last and w == "A" and holder[kind] == "A"
                width -= sum(p for _, p in pieces) + (CORNER[kind][1] if tail else 0)
                pieces += [("plain", p) for p in split(width, rnd)]
                if tail:
                    pieces.append(("corner", CORNER[kind][1]))
                for what, p in pieces:
                    prefix, template = PLAIN[kind]
                    name = label(prefix)
                    module = {"label": name, "template": template, "width": p}
                    if what == "corner":
                        module.update(
                            template=CORNER[kind][0], corner={"adjacent_wall": "Б" if w == "A" else "A"}
                        )
                    rows[kind].append(module)
                    expected[name] = (w, at)
                    at += p
            x += block[1]
        walls.append({"label": w, "length": x, "rows": [{"kind": k, "modules": rows[k]} for k in rows]})
    return walls, expected, holder


def test_fuzz_sums_and_positions_match_hand_layout(home):
    rnd = random.Random(23)
    for trial in range(60):
        walls, expected, holder = build_case(rnd)
        path = write(home, walls)
        findings = api.calculation(project=path, home=home).check().findings
        assert [f.message for f in findings if f.code in ("row_sum", "tall_overlap")] == [], (trial, walls)
        placed = api.calculation(project=path, home=home).placement()
        assert {m.label: (m.wall, m.x) for m in placed.modules} == expected, (trial, json.dumps(walls))
        # ряд без углового модуля доходит до угла на обеих стенах — модули сходятся в углу
        codes = {f.code for f in placed.findings}
        assert codes == ({"corner_overlap"} if None in holder.values() else set()), (trial, holder)

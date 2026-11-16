"""Расстановка модулей в комнате (5.1, тикет 23): начало, поворот стены, габарит и высота каждого модуля.

Начало координат — начало первой стены цепочки на полу; x вдоль неё, y от стены в комнату, z вверх, как у
`Position` (решение координатора). Поворот стены — градусы вокруг вертикали (0, 90, 180, 270). Коробка модуля —
корпус плюс фасад; вне её только задняя стенка ХДФ (y < 0). Высоты рядов (решение мастера 2026-11-15): низ
нижних и пенала — на ножках (`ножки.высота`), верх верхних = верх самого высокого пенала проекта (`ножки.высота`
+ своя `height` пенала, иначе `пенал.высота`; без пенала — `пенал.высота`), верхний модуль выравнивается по верху;
пеналы разной высоты — предупреждение `tall_heights` (тикет 41).

Стены соединены, только если между ними угловой модуль (решение мастера 2026-11-15): угловой у правого края
ряда стены A с соседом Б — конец A встречается с началом Б; у левого — начало A с концом Б (сторона —
`parts.corner_on_left`). Все углы внутренние: следующая стена цепочки повёрнута на 90° и идёт от угла в комнату.
Расстановку читают виды (тикет 41) и HTML (тикет 42).
"""

import math
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from kitchen.checks import Finding
from kitchen.common import corner_modules, corner_offsets, corner_wall_message, is_tall, row_positions
from kitchen.errors import OrderError
from kitchen.format import num
from kitchen.geometry import Mm, Point, turn
from kitchen.models import Workshop
from kitchen.parts import (
    NO_COLOR,
    ROW_GEN,
    Part,
    corner_on_left,
    facade_color,
    find_material,
    gap_width,
    is_facade,
    lower_facades,
    module_dims,
    required_thickness,
    resolve_materials,
)
from kitchen.project import ModuleSpec, Project, Row, Wall, is_gap

__all__ = [
    "Box",
    "GapPlace",
    "ModulePlace",
    "OffsetPlace",
    "Placement",
    "SceneObject",
    "WallPlace",
    "part_in_room",
    "place_project",
]

RowKind = Literal["lower", "upper"]


class Box(BaseModel):
    """Коробка в комнате: `position` — угол у стены слева внизу (начало осей коробки), `size` — по осям
    коробки (x вдоль стены, y от стены, z вверх), `rotation` — поворот осей коробки вокруг вертикали
    через `position`."""

    position: Point
    size: Point
    rotation: int

    def bounds(self) -> tuple[tuple[Mm, Mm, Mm], tuple[Mm, Mm, Mm]]:
        """Границы коробки в осях комнаты: (минимум, максимум)."""
        dx, dy = turn(self.rotation, self.size.x, self.size.y)
        xs = (self.position.x, self.position.x + dx)
        ys = (self.position.y, self.position.y + dy)
        z = (self.position.z, self.position.z + self.size.z)
        return (min(xs), min(ys), z[0]), (max(xs), max(ys), z[1])


class OffsetPlace(BaseModel):
    """Угловое смещение на стене: участок ряда `start`–`end` (отступ от начала стены), который занимает
    угловой модуль `module` соседней стены."""

    row: RowKind
    start: Mm
    end: Mm
    module: str


class WallPlace(BaseModel):
    """Стена в комнате: начало (на полу), поворот, длина и угловые смещения на ней."""

    label: str
    origin: Point
    rotation: int
    length: Mm
    offsets: list[OffsetPlace]


class ModulePlace(Box):
    """Модуль в комнате: стена, ряд, отступ от начала стены и коробка с фасадом."""

    label: str
    template: str
    wall: str
    row: RowKind
    x: Mm  # отступ от начала стены


class GapPlace(BaseModel):
    """Пустое место ряда (тикет 32): деталей нет, следующие модули ряда сдвинуты на его ширину."""

    label: str
    wall: str
    row: RowKind
    x: Mm  # отступ от начала стены
    width: int


class SceneObject(Box):
    """Объект сцены (модель для видов и HTML): вид, метка (модуля, стены, маркировка детали), модуль, цвет
    (цвет фасадов модуля из проекта, как записан: «RAL 7044»; нет — `None`), признак фасада. Стена — плоскость
    по линии стены до потолка проекта, пол — прямоугольник под стенами и модулями (толщины нет в данных)."""

    kind: Literal["module", "wall", "floor", "part"]
    label: str
    module: str | None = None
    color: str | None = None
    facade: bool = False


def part_in_room(place: ModulePlace, part: Part) -> SceneObject:
    """Деталь модуля в комнате: положение детали из системы модуля (`Part.position`, размеры по осям
    `orientation`) переводится той же расстановкой, что и коробка модуля."""
    size = {part.orientation.length: part.length, part.orientation.width: part.width}
    size[part.orientation.thickness] = part.thickness
    du, dv = turn(place.rotation, part.position.x, part.position.y)
    return SceneObject(
        kind="part",
        label=part.id,
        module=place.label,
        position=Point(
            x=place.position.x + du, y=place.position.y + dv, z=place.position.z + part.position.z
        ),
        size=Point(x=size["x"], y=size["y"], z=size["z"]),
        rotation=place.rotation,
        facade=is_facade(part),
    )


def _scene(
    project: Project, walls: list[WallPlace], modules: list[ModulePlace], colors: dict[str, str | None]
):
    scene = [
        SceneObject(
            kind="module",
            label=m.label,
            module=m.label,
            position=m.position,
            size=m.size,
            rotation=m.rotation,
            color=colors[m.label],
        )
        for m in modules
    ]
    for w in walls:
        scene.append(
            SceneObject(
                kind="wall",
                label=w.label,
                position=w.origin,
                size=Point(x=w.length, y=0, z=num(project.ceiling_height)),
                rotation=w.rotation,
            )
        )
    lows = [o.bounds()[0] for o in scene]
    highs = [o.bounds()[1] for o in scene]
    x0, y0 = min(p[0] for p in lows), min(p[1] for p in lows)
    x1, y1 = max(p[0] for p in highs), max(p[1] for p in highs)
    scene.append(
        SceneObject(
            kind="floor",
            label="пол",
            position=Point(x=x0, y=y0, z=0),
            size=Point(x=num(x1 - x0), y=num(y1 - y0), z=0),
            rotation=0,
        )
    )
    return scene


class Placement(BaseModel):
    walls: list[WallPlace]
    modules: list[ModulePlace]
    gaps: list[GapPlace]
    top_of_uppers: int
    # находки, при которых положения есть: `corner_overlap` (error), `tall_heights` (warning)
    findings: list[Finding]
    scene: list[SceneObject]


@dataclass(frozen=True)
class _Link:
    """Угол: конец стены `end_of` встречается с началом стены `start_of`; его задаёт угловой модуль `module`,
    который стоит на стене `wall`."""

    end_of: str
    start_of: str
    row: str
    module: str
    wall: str


def _listed(labels: list[str]) -> str:
    """«Н4 и Н5», «Н4, Н5 и Н7»."""
    return labels[0] if len(labels) == 1 else f"{', '.join(labels[:-1])} и {labels[-1]}"


def _how(link: _Link, a: str, b: str) -> str:
    """Как угол соединяет стены пары (a, b): «конец A к началу Б» или «начало A к концу Б»."""
    return f"конец {a} к началу {b}" if link.end_of == a else f"начало {a} к концу {b}"


def _links(project: Project) -> list[_Link]:
    """Углы проекта по угловым модулям. Угловой посередине ряда, несуществующая соседняя стена, два угловых
    одного ряда в одном углу, противоречие рядов в одном углу, стена, конец или начало которой встречается с
    двумя стенами, и стены, не связанные углами, — ошибки расстановки (все сразу)."""
    labels = {w.label for w in project.walls}
    order = {w.label: i for i, w in enumerate(project.walls)}
    problems: list[str] = []
    found: list[_Link] = []
    for wall, row, m, adjacent in corner_modules(project):
        place = next(i for i, e in enumerate(row.modules) if e is m)
        if 0 < place < len(row.modules) - 1:
            problems.append(
                f"Модуль {m.label} стоит посередине ряда, угловой модуль должен быть первым или последним."
            )
            continue
        if adjacent == wall.label or adjacent not in labels:
            problems.append(corner_wall_message(m.label, adjacent, wall.label))
            continue
        if corner_on_left(row, m):
            found.append(_Link(adjacent, wall.label, row.kind, m.label, wall.label))
        else:
            found.append(_Link(wall.label, adjacent, row.kind, m.label, wall.label))
    pairs: dict[tuple[str, str], list[_Link]] = {}
    for link in found:
        key = tuple(sorted((link.end_of, link.start_of), key=order.__getitem__))
        pairs.setdefault(key, []).append(link)  # type: ignore[arg-type]
    for (a, b), group in pairs.items():
        by_row = {kind: [link for link in group if link.row == kind] for kind in ("lower", "upper")}
        for kind, same in by_row.items():
            for end in (a, b):  # направление связи: один угол — один конец стены
                corner = [link for link in same if link.end_of == end]
                if len(corner) > 1:
                    problems.append(
                        f"В углу {a}–{b} два угловых модуля {ROW_GEN[kind]} ряда: "
                        f"{_listed([s.module for s in corner])}."
                    )
            for w, other in ((a, b), (b, a)):  # оба конца ряда стены w ведут к одной стене (тикет 54)
                own = [link for link in same if link.wall == w]
                if len({link.end_of for link in own}) > 1:
                    problems.append(
                        f"Стена {w}: угловые модули {_listed([s.module for s in own])} {ROW_GEN[kind]} ряда "
                        f"стоят на разных концах ряда, но оба угла ведут к {other}."
                    )
        lower, upper = by_row["lower"], by_row["upper"]
        if lower and upper and len(lower) == len(upper) == 1 and lower[0].end_of != upper[0].end_of:
            problems.append(
                f"Угол между {a} и {b} задан противоречиво: нижний ряд — {_how(lower[0], a, b)}, "
                f"верхний — {_how(upper[0], a, b)}."
            )
    if problems:
        raise OrderError(problems)
    for side, of, other in (("Конец", "end_of", "start_of"), ("Начало", "start_of", "end_of")):
        meets: dict[str, list[str]] = {}
        for link in found:
            walls_met = meets.setdefault(getattr(link, of), [])
            if getattr(link, other) not in walls_met:
                walls_met.append(getattr(link, other))
        for w, met in meets.items():
            if len(met) > 1:
                problems.append(f"{side} стены {w} встречается с несколькими стенами: {_listed(met)}.")
    if problems:
        raise OrderError(problems)
    group = {w.label: w.label for w in project.walls}

    def root(label: str) -> str:
        while group[label] != label:
            label = group[label]
        return label

    for link in found:
        group[root(link.end_of)] = root(link.start_of)
    first = project.walls[0].label
    apart = [w.label for w in project.walls if root(w.label) != root(first)]
    seen: set[str] = set()
    for label in apart:
        if root(label) not in seen:
            seen.add(root(label))
            problems.append(
                f"Стены {first} и {label} не соединены угловым модулем: расстановка строит только стены, связанные углами."
            )
    if problems:
        raise OrderError(problems)
    return found


def _chain(project: Project, links: list[_Link]) -> list[Wall]:
    """Стены по порядку цепочки: от стены, начало которой ни с чем не соединено."""
    after = {link.end_of: link.start_of for link in links}
    starts = {link.start_of for link in links}
    by_label = {w.label: w for w in project.walls}
    first = next((w.label for w in project.walls if w.label not in starts), project.walls[0].label)
    order = [first]
    while after.get(order[-1]) is not None and after[order[-1]] not in order:
        order.append(after[order[-1]])
    return [by_label[label] for label in order]


def _ring_gap(chain: list[Wall], links: list[_Link], tolerance: int) -> list[str]:
    """Стены по кругу (последняя стена цепочки ведёт к первой, тикет 54): конец последней стены должен прийти в
    начало первой. Расхождение — расстояние между ними, округлённое до мм; больше допуска `допуск_стены` —
    ошибка расстановки. Цепочка без замыкания не проверяется."""
    if not any(link.end_of == chain[-1].label and link.start_of == chain[0].label for link in links):
        return []
    x: Mm = 0
    y: Mm = 0
    for rotation, w in enumerate(chain):
        du, dv = turn(rotation * 90, w.length, 0)
        x, y = x + du, y + dv
    gap = num(round(math.hypot(x, y)))
    if gap <= tolerance:
        return []
    labels = ", ".join(w.label for w in chain)
    return [f"Стены {labels} соединены по кругу, а длины не сходятся на {gap} мм."]


def _facade_thickness(module: ModuleSpec, row: Row, project: Project, ws: Workshop) -> int:
    materials = resolve_materials(module, row, project.materials)
    facade_id = materials.facades_lower if lower_facades(module, row) else materials.facades_upper
    facade = find_material(ws.materials.facades, facade_id, "Материал фасадов", "фасады")
    return required_thickness(facade, "фасадов")


def place_project(project: Project, ws: Workshop) -> Placement:
    """Расстановка проекта. Ошибки расстановки — `OrderError` со всеми сообщениями."""
    if not project.walls:
        raise OrderError("В проекте нет стен: расставлять нечего.")
    c = ws.construction
    floor = c.legs.height
    talls = [
        (m.label, module_dims(m, row.kind, ws).height)
        for w in project.walls
        for row in w.rows
        for m in row.cabinets
        if is_tall(m)
    ]
    tallest = max(talls, key=lambda t: t[1], default=None)
    top = floor + (tallest[1] if tallest else c.tall.height)
    tall_findings = (
        [
            Finding(
                code="tall_heights",
                level="warning",
                subject=tallest[0],
                message=(
                    "Пеналы разной высоты: "
                    + ", ".join(f"{label} {height} мм" for label, height in talls)
                    + f" — верх верхних по {tallest[0]} ({top} мм)."
                ),
            )
        ]
        if tallest and len({h for _, h in talls}) > 1
        else []
    )
    problems: list[str] = []
    rises = any(
        row.kind == "upper" or is_tall(m) for w in project.walls for row in w.rows for m in row.cabinets
    )
    if rises and top > project.ceiling_height:
        problems.append(f"Верх верхних модулей {top} мм выше потолка {num(project.ceiling_height)} мм.")
    try:
        links = _links(project)
    except OrderError as e:
        raise OrderError(problems + e.problems) from e
    chain = _chain(project, links)
    problems += _ring_gap(chain, links, c.corner_rules.wall_tolerance)
    if problems:
        raise OrderError(problems)
    offsets = corner_offsets(project, ws)
    walls: list[WallPlace] = []
    modules: list[ModulePlace] = []
    gaps: list[GapPlace] = []
    colors: dict[str, str | None] = {}
    origin: tuple[Mm, Mm] = (0, 0)
    rotation = 0
    for w in chain:
        marks: list[OffsetPlace] = []
        for kind in ("lower", "upper"):
            o = offsets.of(w.label, kind)
            if o.at_start:
                marks.append(OffsetPlace(row=kind, start=0, end=num(o.start), module=o.at_start[0]))
            if o.at_end:
                marks.append(
                    OffsetPlace(row=kind, start=num(w.length - o.end), end=num(w.length), module=o.at_end[0])
                )
        walls.append(
            WallPlace(
                label=w.label,
                origin=Point(x=origin[0], y=origin[1], z=0),
                rotation=rotation,
                length=num(w.length),
                offsets=marks,
            )
        )
        for row, entries in zip(w.rows, row_positions(w, offsets), strict=True):
            for at, entry in entries:
                x = num(at)
                own = any(entry is e for e in row.modules)
                if is_gap(entry):
                    gaps.append(
                        GapPlace(label=entry.label, wall=w.label, row=row.kind, x=x, width=gap_width(entry))
                    )
                elif own:
                    dims = module_dims(entry, row.kind, ws)
                    z = floor if row.kind == "lower" or is_tall(entry) else top - dims.height
                    du, dv = turn(rotation, x, 0)
                    modules.append(
                        ModulePlace(
                            label=entry.label,
                            template=entry.template,
                            wall=w.label,
                            row=row.kind,
                            x=x,
                            position=Point(x=origin[0] + du, y=origin[1] + dv, z=z),
                            size=Point(
                                x=dims.width,
                                y=dims.depth + _facade_thickness(entry, row, project, ws),
                                z=dims.height,
                            ),
                            rotation=rotation,
                        )
                    )
                    color = facade_color(entry, row, project.materials)
                    colors[entry.label] = None if color == NO_COLOR else color
        du, dv = turn(rotation, w.length, 0)
        origin = (num(origin[0] + du), num(origin[1] + dv))
        rotation = (rotation + 90) % 360
    return Placement(
        walls=walls,
        modules=modules,
        gaps=gaps,
        top_of_uppers=top,
        findings=tall_findings + _corner_overlaps(modules, links),
        scene=_scene(project, walls, modules, colors),
    )


def _overlap(a: Box, b: Box) -> bool:
    """Коробки пересекаются по объёму; касание — не пересечение."""
    (a0, a1), (b0, b1) = a.bounds(), b.bounds()
    return all(a0[i] < b1[i] and b0[i] < a1[i] for i in range(3))


def _corner_overlaps(modules: list[ModulePlace], links: list[_Link]) -> list[Finding]:
    """Модули разных стен, чьи коробки пересекаются (решение агента, MVP, по поручению мастера 2026-11-15):
    например, угловой модуль только в нижнем ряду, а верхние ряды обеих стен доходят до угла. Угловой модуль
    с модулями своего ряда на соседней стене не сравнивается: их разводит угловое смещение (тикет 50), а
    коробка с фасадом у глухой части шире, чем модуль на самом деле (заглушка тоньше фасада)."""
    corner_walls: dict[str, set[str]] = {}  # угловой модуль -> стены его угла
    for link in links:
        corner_walls.setdefault(link.module, set()).update({link.end_of, link.start_of})
    found: list[Finding] = []
    for i, a in enumerate(modules):
        for b in modules[i + 1 :]:
            if a.wall == b.wall or not _overlap(a, b):
                continue
            if any(
                m.label in corner_walls and o.wall in corner_walls[m.label] and o.row == m.row
                for m, o in ((a, b), (b, a))
            ):
                continue
            # нижние ряды обеих стен — нужен нижний угловой (ПРОВЕРИТЬ МАСТЕРУ)
            lower = a.row == b.row == "lower"
            found.append(
                Finding(
                    code="corner_overlap",
                    level="error",
                    subject=a.label,
                    message=(
                        f"{a.label} (стена {a.wall}) и {b.label} (стена {b.wall}) пересекаются в углу: "
                        + (
                            "нужен нижний угловой модуль или отступ нижнего ряда"
                            if lower
                            else "нужен верхний угловой модуль или отступ верхнего ряда"
                        )
                    ),
                )
            )
    return found

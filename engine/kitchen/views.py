"""Виды кухни (5.1, тикет 41): план сверху и вид на каждую стену спереди из расстановки тикета 23.

Две ступени, как у карты присадки: `build_scene` собирает из расстановки (`placement.place_project`) и деталей
(`parts.compute_parts`) сцену видов в миллиметрах — модули с фасадами в координатах стены (фасады — детали-фасады,
переведённые в комнату `placement.part_in_room`), пустые места, угловые смещения, глухие части; `layout` считает
страницы в пунктах листа: фигуры, линии и подписи. Рисует PDF `views_pdf`, тесты проверяют раскладку.

Лист — A4 альбомная (решение мастера 2026-11-15): страница 1 — план сверху, дальше по странице на стену; масштаб —
наименьший из ряда `SCALES`, при котором вид помещается, подпись «масштаб 1:N». Подписи не налезают друг на друга
и не выходят за лист: каждая ищет свободное место среди своих вариантов (кегль, перенос, поворот, сдвиг);
не нашлось — ошибка, а не наложение.
"""

from collections.abc import Iterable, Iterator
from typing import Literal

from pydantic import BaseModel
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth

from kitchen.common import is_tall
from kitchen.errors import OrderError
from kitchen.format import num, plural
from kitchen.geometry import turn
from kitchen.models import Workshop
from kitchen.parts import (
    APPLIANCE_NAMES,
    ROW_PL_GEN,
    ROW_PL_NOM,
    ModuleParts,
    corner_on_left,
    gap_name,
    is_facade,
)
from kitchen.pdf import FONT, FONT_BOLD, Area, page_area, register_fonts
from kitchen.placement import Placement, WallPlace, part_in_room
from kitchen.project import Project, is_gap
from kitchen.ral import UNKNOWN, ral_rgb

__all__ = [
    "SCALES",
    "Chain",
    "Label",
    "Level",
    "Rect",
    "Segment",
    "Shape",
    "ViewGap",
    "ViewModule",
    "ViewOffset",
    "ViewPage",
    "ViewScene",
    "ViewWall",
    "build_scene",
    "checks_banner",
    "layout",
]

# Ряд масштабов 1:N (решение агента, ПРОВЕРИТЬ МАСТЕРУ): берётся наименьший, при котором вид помещается на лист.
SCALES = (5, 10, 15, 20, 25, 30, 40, 50, 60, 75, 100, 150, 200, 250, 300, 400, 500)

RowKind = Literal["lower", "upper"]


# --- сцена видов (мм) ---------------------------------------------------------


class Rect(BaseModel):
    """Прямоугольник на виде спереди, мм: `x` — от начала стены, `z` — от пола."""

    x: float
    z: float
    width: float
    height: float


class Footprint(BaseModel):
    """Прямоугольник на плане в осях комнаты, мм."""

    x0: float
    y0: float
    x1: float
    y1: float


class ViewModule(BaseModel):
    label: str
    template: str
    wall: str
    row: RowKind
    tall: bool
    front: Rect  # коробка с фасадом на виде спереди
    depth: float  # глубина коробки с фасадом
    plan: Footprint
    facades: list[Rect]  # детали-фасады (двери, фасады ящиков, панели) на виде спереди
    color: str | None  # цвет фасадов, как записан в проекте
    rgb: tuple[int, int, int]  # заливка: таблица RAL, нераспознанный — серый
    color_known: bool
    appliances: list[str]
    blind: Rect | None = None  # глухая часть углового модуля
    blind_width: int | None = None


class ViewGap(BaseModel):
    label: str
    wall: str
    row: RowKind
    front: Rect
    plan: Footprint
    width: int
    appliances: list[str]


class ViewOffset(BaseModel):
    """Угловое смещение на стене: участок ряда, который занимает угловой модуль соседней стены."""

    row: RowKind
    start: float
    end: float
    module: str
    front: Rect  # по высоте углового модуля


class ViewWall(BaseModel):
    label: str
    length: float
    rotation: int
    start: tuple[float, float]
    end: tuple[float, float]
    offsets: list[ViewOffset]
    depths: dict[str, int]  # глубина ряда с фасадом: «lower», «upper»


class ViewScene(BaseModel):
    walls: list[ViewWall]
    modules: list[ViewModule]
    gaps: list[ViewGap]
    legs: int
    top_of_uppers: int
    ceiling: float


def _footprint(
    start: tuple[float, float], rotation: int, x: float, width: float, v0: float, v1: float
) -> Footprint:
    """Полоса плана вдоль стены: участок `x`–`x + width` от начала стены на расстоянии `v0`–`v1` от неё."""
    corners = [turn(rotation, u, v) for u in (x, x + width) for v in (v0, v1)]
    xs = [start[0] + c[0] for c in corners]
    ys = [start[1] + c[1] for c in corners]
    return Footprint(x0=min(xs), y0=min(ys), x1=max(xs), y1=max(ys))


def _plan_box(wall: WallPlace, x: float, width: float, depth: float) -> Footprint:
    return _footprint((wall.origin.x, wall.origin.y), wall.rotation, x, width, 0, depth)


def _along(wall: WallPlace, x: float, y: float) -> float:
    """Отступ точки комнаты вдоль стены от её начала."""
    back = turn((360 - wall.rotation) % 360, x - wall.origin.x, y - wall.origin.y)
    return back[0]


def _lower_top(modules: list[ViewModule], wall: str) -> float | None:
    """«Верх нижних» стены: верх самого высокого нижнего модуля, пенал не в счёт; нижних нет — `None`."""
    tops = [m.front.z + m.front.height for m in modules if m.wall == wall and m.row == "lower" and not m.tall]
    return max(tops) if tops else None


def _nearest(labels: list[str], depth_of: dict[str, float], tall_of: dict[str, bool]) -> float | None:
    """Глубина первого модуля списка меток, не считая пеналов и пустых мест; таких нет — `None`."""
    return next((depth_of[x] for x in labels if x in depth_of and not tall_of[x]), None)


def build_scene(project: Project, placed: Placement, parts: list[ModuleParts], ws: Workshop) -> ViewScene:
    """Сцена видов из расстановки: модули, их фасады (детали-фасады через `part_in_room`), пустые места,
    угловые смещения и глухие части угловых модулей."""
    c = ws.construction
    walls = {w.label: w for w in placed.walls}
    by_label = {m.label: m for m in placed.modules}
    colors = {o.label: o.color for o in placed.scene if o.kind == "module"}
    parts_of = {m.module: m.parts for m in parts}
    specs = {e.label: (e, row) for w in project.walls for row in w.rows for e in row.modules if not is_gap(e)}

    def appliances(label: str, gap: bool) -> list[str]:
        return [
            gap_name(a.type, 0) if gap else APPLIANCE_NAMES.get(a.type, a.type)
            for a in project.appliances_at(label)
        ]

    modules: list[ViewModule] = []
    for place in placed.modules:
        wall = walls[place.wall]
        spec, row = specs[place.label]
        facades = []
        for part in parts_of.get(place.label, []):
            if not is_facade(part):
                continue
            room = part_in_room(place, part)
            facades.append(
                Rect(
                    x=num(_along(wall, room.position.x, room.position.y)),
                    z=room.position.z,
                    width=room.size.x,
                    height=room.size.z,
                )
            )
        color = colors.get(place.label)
        rgb = ral_rgb(color)
        front = Rect(x=place.x, z=place.position.z, width=place.size.x, height=place.size.z)
        blind = blind_width = None
        if spec.corner is not None and facades:
            # глухая часть — от края фасада на её стороне до края модуля (решение мастера 2026-11-16, 9)
            left = corner_on_left(row, spec)
            edge = min(f.x for f in facades) if left else max(f.x + f.width for f in facades)
            x0, x1 = (place.x, edge) if left else (edge, place.x + place.size.x)
            blind_width = round(x1 - x0)
            if blind_width > 0:
                blind = Rect(x=x0, z=place.position.z, width=blind_width, height=place.size.z)
            else:
                blind_width = None
        modules.append(
            ViewModule(
                label=place.label,
                template=place.template,
                wall=place.wall,
                row=place.row,
                tall=is_tall(spec),
                front=front,
                depth=place.size.y,
                plan=_plan_box(wall, place.x, place.size.x, place.size.y),
                facades=facades,
                color=color,
                rgb=rgb or UNKNOWN,
                color_known=rgb is not None,
                appliances=appliances(place.label, gap=False),
                blind=blind,
                blind_width=blind_width,
            )
        )
    view_walls = []
    for w in placed.walls:
        offsets = []
        for o in w.offsets:
            corner = by_label[o.module]
            offsets.append(
                ViewOffset(
                    row=o.row,
                    start=o.start,
                    end=o.end,
                    module=o.module,
                    front=Rect(x=o.start, z=corner.position.z, width=o.end - o.start, height=corner.size.z),
                )
            )
        end = turn(w.rotation, w.length, 0)
        depths = {}
        for kind in ("lower", "upper"):
            values = [
                round(m.depth)
                for m in modules
                if m.wall == w.label and (m.row == kind and not m.tall or kind == "lower" and m.tall)
            ]
            if values:
                depths[kind] = max(values)
        view_walls.append(
            ViewWall(
                label=w.label,
                length=w.length,
                rotation=w.rotation,
                start=(w.origin.x, w.origin.y),
                end=(w.origin.x + end[0], w.origin.y + end[1]),
                offsets=offsets,
                depths=depths,
            )
        )
    depth_of = {m.label: m.depth for m in modules}
    tall_of = {m.label: m.tall for m in modules}
    wall_depths = {w.label: w.depths for w in view_walls}
    gaps = []
    for g in placed.gaps:
        wall = walls[g.wall]
        if g.row == "lower":
            # до верха нижних стены; нижних модулей нет — ножки и нижний модуль мастерской (решение мастера, 8)
            z0, height = 0, _lower_top(modules, g.wall) or c.legs.height + c.lower.height
            fallback = c.lower.depth
        else:
            z0, height = placed.top_of_uppers - c.upper.height, c.upper.height
            fallback = c.upper.depth
        entries = next(
            [e.label for e in row.modules]
            for w in project.walls
            if w.label == g.wall
            for row in w.rows
            if row.kind == g.row and any(e.label == g.label for e in row.modules)
        )
        i = entries.index(g.label)
        # глубина — ближайший модуль ряда с каждой стороны (пеналы и пустые места не в счёт), более глубокий
        near = [
            d
            for d in (
                _nearest(entries[i + 1 :], depth_of, tall_of),
                _nearest(entries[:i][::-1], depth_of, tall_of),
            )
            if d is not None
        ]
        depth = max(near) if near else wall_depths[g.wall].get(g.row, fallback)
        gaps.append(
            ViewGap(
                label=g.label,
                wall=g.wall,
                row=g.row,
                front=Rect(x=g.x, z=z0, width=g.width, height=height),
                plan=_plan_box(wall, g.x, g.width, depth),
                width=g.width,
                appliances=appliances(g.label, gap=True),
            )
        )
    return ViewScene(
        walls=view_walls,
        modules=modules,
        gaps=gaps,
        legs=c.legs.height,
        top_of_uppers=placed.top_of_uppers,
        ceiling=project.ceiling_height,
    )


# --- раскладка страниц (пункты листа) ----------------------------------------


class Shape(BaseModel):
    """Прямоугольник на листе: `x`, `y` — левый нижний угол, пункты."""

    kind: Literal["wall", "lower", "upper", "tall", "facade", "gap", "blind", "offset", "banner"]
    x: float
    y: float
    w: float
    h: float
    line: float = 0.6
    dashed: bool = False
    hatch: bool = False
    fill: tuple[int, int, int] | None = None
    stroke: tuple[int, int, int] = (0, 0, 0)


class Segment(BaseModel):
    x0: float
    y0: float
    x1: float
    y1: float
    line: float = 0.4
    dashed: bool = False
    arrows: bool = False


class Label(BaseModel):
    """Подпись: начало базовой линии (`x`, `y`), ширина строки; `rotated` — повёрнута на 90° (читается снизу
    вверх); `background` — на белой подложке поверх линий."""

    text: str
    size: float
    x: float
    y: float
    width: float
    bold: bool = False
    rotated: bool = False
    background: bool = False
    color: tuple[int, int, int] = (0, 0, 0)

    def box(self) -> tuple[float, float, float, float]:
        """Рамка подписи на листе: (x0, y0, x1, y1)."""
        ascent, descent = 0.95 * self.size, 0.25 * self.size
        if self.rotated:
            return (self.x - ascent, self.y, self.x + descent, self.y + self.width)
        return (self.x, self.y - descent, self.x + self.width, self.y + ascent)


class Chain(BaseModel):
    """Цепочка ширин ряда на виде стены: ширины модулей, пустых мест и угловых смещений по порядку."""

    row: RowKind
    values: list[int | float]


class Level(BaseModel):
    name: str
    z: int | float


class ViewPage(BaseModel):
    kind: Literal["plan", "wall"]
    wall: str | None = None
    title: str
    scale: int
    shapes: list[Shape]
    segments: list[Segment]
    labels: list[Label]
    chains: list[Chain] = []
    levels: list[Level] = []


def checks_banner(errors: int) -> str:
    """Плашка проекта с ошибками проверок: «Проверки: 2 ошибки — сверьте проект»."""
    return f"Проверки: {plural(errors, 'ошибка', 'ошибки', 'ошибок')} — сверьте проект"


_TITLE = 12.0
_SMALL = 6.0
_LABEL_SIZES = (6.5, 6.0, 5.5, 5.0, 4.5)
_LEADING = 1.2
_PAD = 0.6  # зазор между подписями
_RED = (190, 0, 0)

Box = tuple[float, float, float, float]


def _width(text: str, size: float, bold: bool) -> float:
    return stringWidth(text, FONT_BOLD if bold else FONT, size)


def _label(text: str, size: float, x: float, y: float, **kw) -> Label:
    return Label(text=text, size=size, x=x, y=y, width=_width(text, size, kw.get("bold", False)), **kw)


def _block(lines: list[str], size: float, cx: float, cy: float, rotated: bool, **kw) -> list[Label]:
    """Строки подписи с центром в (cx, cy); повёрнутая читается снизу вверх, строки идут слева направо."""
    step = _LEADING * size
    total = step * len(lines)
    out = []
    for i, text in enumerate(lines):
        width = _width(text, size, kw.get("bold", False))
        if rotated:
            x = cx - total / 2 + 0.95 * size + i * step
            out.append(Label(text=text, size=size, x=x, y=cy - width / 2, width=width, rotated=True, **kw))
        else:
            y = cy + total / 2 - 0.95 * size - i * step
            out.append(Label(text=text, size=size, x=cx - width / 2, y=y, width=width, **kw))
    return out


def _extent(lines: list[str], size: float, bold: bool) -> tuple[float, float]:
    """Ширина и высота блока строк без поворота."""
    return max(_width(t, size, bold) for t in lines), _LEADING * size * len(lines)


def _inside(box: Box, outer: Box) -> bool:
    return box[0] >= outer[0] and box[1] >= outer[1] and box[2] <= outer[2] and box[3] <= outer[3]


def _hits(a: Box, b: Box) -> bool:
    return a[0] < b[2] + _PAD and b[0] < a[2] + _PAD and a[1] < b[3] + _PAD and b[1] < a[3] + _PAD


class _Placer:
    """Подписи страницы: каждая встаёт на первый свободный вариант внутри листа и своей области."""

    def __init__(self, sheet: Area) -> None:
        self.sheet: Box = (sheet.x, sheet.y, sheet.x + sheet.w, sheet.y + sheet.h)
        self.labels: list[Label] = []

    def free(self, block: list[Label], region: Box | None = None) -> bool:
        boxes = [label.box() for label in block]
        for b in boxes:  # строки одного блока идут через интерлиньяж и друг на друга не налезают
            if not _inside(b, self.sheet) or (region is not None and not _inside(b, region)):
                return False
            if any(_hits(b, placed.box()) for placed in self.labels):
                return False
        return True

    def put(self, candidates: Iterable[list[Label]], what: str, region: Box | None = None) -> list[Label]:
        for block in candidates:
            if self.free(block, region):
                self.labels += block
                return block
        raise OrderError(
            f"Виды кухни: подпись «{what}» не помещается на листе без наложения на другие подписи."
        )


def _steps(span: float, step: float) -> list[float]:
    """Сдвиги от центра 0, ±step, ±2·step… в пределах половины `span`."""
    out = [0.0]
    k = 1
    while k * step <= span / 2:
        out += [k * step, -k * step]
        k += 1
    return out


def _in_region(variants: list[list[str]], region: Box, *, bold: bool = False, background: bool = False,
               rotate: bool = True, sizes: tuple[float, ...] = _LABEL_SIZES) -> Iterator[list[Label]]:  # fmt: skip
    """Варианты подписи внутри области: по строкам вариантов, кеглю, повороту и сдвигу от центра."""
    x0, y0, x1, y1 = region
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    for rotated in (False, True) if rotate else (False,):
        for lines in variants:
            for size in sizes:
                w, h = _extent(lines, size, bold)
                along, across = (h, w) if rotated else (w, h)
                if along > x1 - x0 or across > y1 - y0:
                    continue
                shifts = sorted(
                    ((dx, dy) for dx in _steps(x1 - x0 - along, 2.0) for dy in _steps(y1 - y0 - across, 2.0)),
                    key=lambda d: abs(d[0]) + abs(d[1]),
                )
                for dx, dy in shifts:
                    yield _block(lines, size, cx + dx, cy + dy, rotated, bold=bold, background=background)


def _variants(head: str, extra: list[str]) -> list[list[str]]:
    """Строки подписи модуля: целиком, затем метка и шаблон раздельно."""
    full = [head, *extra]
    out = [full]
    if " " in head:
        first, rest = head.split(" ", 1)
        out.append([first, rest, *extra])
    return out


def _scale(width_mm: float, height_mm: float, room_w: float, room_h: float) -> int:
    for n in SCALES:
        if width_mm / n * mm <= room_w and height_mm / n * mm <= room_h:
            return n
    raise OrderError(
        f"Виды кухни: {num(round(width_mm))}×{num(round(height_mm))} мм не помещаются на лист даже в масштабе 1:{SCALES[-1]}."
    )


def _header(placer: _Placer, area: Area, title: str, scale: int, errors: int) -> tuple[list[Shape], float]:
    """Заголовок, масштаб и плашка ошибок проверок; возвращает фигуры и низ шапки."""
    top = area.y + area.h
    shapes: list[Shape] = []
    placer.put([[_label(title, _TITLE, area.x, top - _TITLE, bold=True)]], title)
    scale_text = f"масштаб 1:{scale}"
    width = _width(scale_text, 8, False)
    placer.put([[_label(scale_text, 8, area.x + area.w - width, top - _TITLE)]], scale_text)
    bottom = top - _TITLE - 6
    if errors:
        text = checks_banner(errors)
        size = 9.0
        width = _width(text, size, True)
        y = bottom - size - 2
        placer.put([[_label(text, size, area.x + 4, y, bold=True, color=_RED)]], text)
        shapes.append(
            Shape(
                kind="banner",
                x=area.x,
                y=y - 4,
                w=width + 8,
                h=size + 8,
                line=1.0,
                stroke=_RED,
                fill=(255, 235, 235),
            )
        )
        bottom = y - 8
    return shapes, bottom


def _rect_box(shape: Shape, inset: float = 0.8) -> Box:
    return (shape.x + inset, shape.y + inset, shape.x + shape.w - inset, shape.y + shape.h - inset)


# --- план сверху --------------------------------------------------------------

_PLAN_MARGIN = 14 * mm


def _plan(scene: ViewScene, errors: int) -> ViewPage:
    area = page_area(landscape=True)
    placer = _Placer(area)
    points = [p for w in scene.walls for p in (w.start, w.end)]
    for f in [m.plan for m in scene.modules] + [g.plan for g in scene.gaps]:
        points += [(f.x0, f.y0), (f.x1, f.y1)]
    bx0, by0 = min(p[0] for p in points), min(p[1] for p in points)
    bx1, by1 = max(p[0] for p in points), max(p[1] for p in points)
    header_h = _TITLE + 6 + (25 if errors else 0)
    room_w = area.w - 2 * _PLAN_MARGIN
    room_h = area.h - header_h - 2 * _PLAN_MARGIN
    n = _scale(bx1 - bx0, by1 - by0, room_w, room_h)
    s = mm / n
    ox = area.x + _PLAN_MARGIN + (room_w - (bx1 - bx0) * s) / 2
    top = area.y + area.h - header_h - _PLAN_MARGIN - (room_h - (by1 - by0) * s) / 2

    def at(x: float, y: float) -> tuple[float, float]:
        # y комнаты — вниз по листу: мастер смотрит на стену A сверху листа, стены идут слева направо
        return ox + (x - bx0) * s, top - (y - by0) * s

    def rect(f: Footprint, kind, **kw) -> Shape:
        (px0, py0), (px1, py1) = at(f.x0, f.y0), at(f.x1, f.y1)
        return Shape(kind=kind, x=min(px0, px1), y=min(py0, py1), w=abs(px1 - px0), h=abs(py1 - py0), **kw)

    shapes, _ = _header(placer, area, "План сверху", n, errors)
    segments: list[Segment] = []
    lower = [m for m in scene.modules if m.row == "lower" or m.tall]
    upper = [m for m in scene.modules if m.row == "upper" and not m.tall]
    module_shapes = {m.label: rect(m.plan, "tall" if m.tall else "lower", line=0.8) for m in lower}
    module_shapes |= {m.label: rect(m.plan, "upper", dashed=True) for m in upper}
    gap_shapes = {g.label: rect(g.plan, "gap", hatch=True, dashed=g.row == "upper") for g in scene.gaps}
    shapes += list(gap_shapes.values()) + [module_shapes[m.label] for m in lower + upper]
    for w in scene.walls:
        (sx, sy), (ex, ey) = at(*w.start), at(*w.end)
        segments.append(Segment(x0=sx, y0=sy, x1=ex, y1=ey, line=3.0))
    # подписи: угловые смещения и стены — снаружи, модули и пустые места — внутри своих контуров
    for w in scene.walls:
        (sx, sy), (ex, ey) = at(*w.start), at(*w.end)
        length = ((ex - sx) ** 2 + (ey - sy) ** 2) ** 0.5
        dx, dy = (ex - sx) / length, (ey - sy) / length
        rx, ry = turn(w.rotation, 0, -1)  # наружу от комнаты в осях комнаты
        nx, ny = rx, -ry
        horizontal = abs(dy) < 1e-9
        for o in w.offsets:
            a, b = o.start * s, o.end * s
            gap = 3.0 if o.row == "lower" else 6.0
            p0 = (sx + dx * a + nx * gap, sy + dy * a + ny * gap)
            p1 = (sx + dx * b + nx * gap, sy + dy * b + ny * gap)
            segments.append(
                Segment(x0=p0[0], y0=p0[1], x1=p1[0], y1=p1[1], line=0.8, dashed=o.row == "upper")
            )
            row = ROW_PL_GEN[o.row]
            text = f"смещение {row} {num(round(o.end - o.start))}"
            mid = (sx + dx * (a + b) / 2, sy + dy * (a + b) / 2)
            placer.put(_outside([text], _SMALL, mid, (dx, dy), (nx, ny), horizontal, length), text)
        depth = ", ".join(f"{ROW_PL_NOM[k]} {v}" for k, v in sorted(w.depths.items()))
        lines = [f"Стена {w.label}, {num(w.length)}"] + ([f"глубина: {depth}"] if depth else [])
        mid = ((sx + ex) / 2, (sy + ey) / 2)
        placer.put(_outside(lines, 8.0, mid, (dx, dy), (nx, ny), horizontal, length, bold=True), lines[0])
    walls = {w.label: w for w in scene.walls}
    for m in upper + lower:
        region = _rect_box(module_shapes[m.label])
        reach = walls[m.wall].depths.get("upper", 0)
        if m.row == "lower" and not m.tall and 0 < reach < m.depth:
            # нижний модуль подписан перед верхним рядом, чтобы метки рядов не сходились
            region = _rect_box(
                rect(
                    _footprint(
                        walls[m.wall].start, walls[m.wall].rotation, m.front.x, m.front.width, reach, m.depth
                    ),
                    "lower",
                )
            )
        placer.put(_in_region([[m.label]], region, bold=True, background=True), m.label)
    for g in scene.gaps:
        head = " ".join([g.label, *g.appliances])
        variants = [[head], [g.label, *g.appliances], [g.label]] if g.appliances else [[g.label]]
        placer.put(_in_region(variants, _rect_box(gap_shapes[g.label]), background=True), g.label)
    return ViewPage(
        kind="plan", title="План сверху", scale=n, shapes=shapes, segments=segments, labels=placer.labels
    )


def _outside(lines: list[str], size: float, mid, d, normal, horizontal: bool, length: float,
             bold: bool = False) -> Iterator[list[Label]]:  # fmt: skip
    """Варианты подписи у стены снаружи комнаты: дальше от стены и со сдвигом вдоль неё."""
    _, h = _extent(lines, size, bold)
    for dist in (9.0, 16.0, 23.0, 30.0, 37.0, 44.0):
        for t in _steps(length, 6.0):
            cx = mid[0] + d[0] * t + normal[0] * (dist + h / 2)
            cy = mid[1] + d[1] * t + normal[1] * (dist + h / 2)
            yield _block(lines, size, cx, cy, not horizontal, bold=bold)


# --- вид стены спереди --------------------------------------------------------


def _levels(scene: ViewScene, wall: str) -> list[Level]:
    mods = [m for m in scene.modules if m.wall == wall]
    lowers = [m for m in mods if m.row == "lower" and not m.tall]
    uppers = [m for m in mods if m.row == "upper" and not m.tall]
    levels = [Level(name="", z=0)]
    if mods:
        levels.append(Level(name="ножки", z=scene.legs))
    if lowers:
        levels.append(Level(name="верх нижних", z=_lower_top(mods, wall)))
    if uppers:
        levels.append(Level(name="низ верхних", z=min(m.front.z for m in uppers)))
    if uppers or any(m.tall for m in mods):
        levels.append(Level(name="верх верхних", z=scene.top_of_uppers))
    levels.append(Level(name="потолок", z=scene.ceiling))
    return levels


def _chain_parts(scene: ViewScene, wall: ViewWall, kind: RowKind) -> list[tuple[float, float]]:
    """Участки ряда по порядку: (начало, ширина) модулей (пенал — в обоих рядах), пустых мест и смещений."""
    parts = [(o.start, o.end - o.start) for o in wall.offsets if o.row == kind]
    own = [(g.front.x, g.front.width) for g in scene.gaps if g.wall == wall.label and g.row == kind]
    own += [
        (m.front.x, m.front.width)
        for m in scene.modules
        if m.wall == wall.label and (m.row == kind or m.tall)
    ]
    return sorted(parts + own) if own else []


def _wall(scene: ViewScene, wall: ViewWall, errors: int) -> ViewPage:
    area = page_area(landscape=True)
    placer = _Placer(area)
    title = f"Стена {wall.label}, {num(wall.length)} мм — вид спереди"
    levels = _levels(scene, wall.label)
    level_texts = [f"{num(lv.z)} {lv.name}".strip() for lv in levels]
    left = max(_width(t, _SMALL, False) for t in level_texts) + 14
    bottom, above = 46.0, 26.0
    header_h = _TITLE + 6 + (25 if errors else 0)
    mods = [m for m in scene.modules if m.wall == wall.label]
    height = max([scene.ceiling, scene.top_of_uppers] + [m.front.z + m.front.height for m in mods])
    room_w = area.w - left - 6
    room_h = area.h - header_h - bottom - above
    n = _scale(wall.length, height, room_w, room_h)
    s = mm / n
    x0 = area.x + left + (room_w - wall.length * s) / 2
    z0 = area.y + bottom

    def rect(r: Rect, kind, **kw) -> Shape:
        return Shape(kind=kind, x=x0 + r.x * s, y=z0 + r.z * s, w=r.width * s, h=r.height * s, **kw)

    shapes, _ = _header(placer, area, title, n, errors)
    segments: list[Segment] = []
    shapes.append(
        rect(
            Rect(x=0, z=0, width=wall.length, height=scene.ceiling), "wall", line=0.4, stroke=(150, 150, 150)
        )
    )
    ceiling_y = z0 + scene.ceiling * s
    segments.append(Segment(x0=x0, y0=z0, x1=x0 + wall.length * s, y1=z0, line=2.0))
    segments.append(Segment(x0=x0, y0=ceiling_y, x1=x0 + wall.length * s, y1=ceiling_y, dashed=True))
    offset_shapes = [(o, rect(o.front, "offset", dashed=True, hatch=True, line=0.4)) for o in wall.offsets]
    shapes += [sh for _, sh in offset_shapes]
    gap_shapes = [(g, rect(g.front, "gap", dashed=True)) for g in scene.gaps if g.wall == wall.label]
    shapes += [sh for _, sh in gap_shapes]
    placed_modules = []
    for m in mods:
        outline = rect(m.front, "tall" if m.tall else m.row, line=0.8)
        shapes.append(outline)
        shapes += [rect(f, "facade", line=0.3, fill=m.rgb) for f in m.facades]
        blind = rect(m.blind, "blind", hatch=True, line=0.3) if m.blind else None
        if blind:
            shapes.append(blind)
        placed_modules.append((m, outline, blind))
    for m, _, blind in placed_modules:
        if blind:
            text = f"глухая часть {m.blind_width}"
            placer.put(_in_region([[text], ["глухая", "часть", str(m.blind_width)]], _rect_box(blind),
                                  background=True), text)  # fmt: skip
    for m, outline, blind in placed_modules:
        region = _rect_box(outline)
        if blind:  # подпись модуля — на его двери, глухая часть подписана своей
            bx0, bx1 = blind.x, blind.x + blind.w
            region = (bx1 + 0.8, region[1], region[2], region[3]) if bx0 <= outline.x + 1e-6 else (
                region[0], region[1], bx0 - 0.8, region[3])  # fmt: skip
        extra = list(m.appliances) + ([m.color or ""] if not m.color_known and m.color else [])
        placer.put(_in_region(_variants(f"{m.label} {m.template}", extra), region, background=True), m.label)
    for g, sh in gap_shapes:
        placer.put(_in_region([[g.label, *g.appliances]], _rect_box(sh), background=True), g.label)
    for o, sh in offset_shapes:
        text = f"угол {o.module}"
        placer.put(_in_region([[text], ["угол", o.module]], _rect_box(sh), background=True), text)
    # цепочки ширин: под нижним рядом и над верхним
    chains: list[Chain] = []
    lowest = z0 - 4
    for kind in ("lower", "upper"):
        parts = _chain_parts(scene, wall, kind)
        if not parts:
            continue
        chains.append(Chain(row=kind, values=[num(w) for _, w in parts]))
        tops = [m.front.z + m.front.height for m in mods if m.row == "upper" or m.tall]
        y = z0 - 6 if kind == "lower" else z0 + max(tops or [scene.top_of_uppers]) * s + 6
        segments.append(
            Segment(x0=x0 + parts[0][0] * s, y0=y, x1=x0 + (parts[-1][0] + parts[-1][1]) * s, y1=y)
        )
        for start, width in parts:
            for edge in (start, start + width):
                segments.append(Segment(x0=x0 + edge * s, y0=y - 2, x1=x0 + edge * s, y1=y + 2))
        for start, width in parts:
            text = str(num(round(width, 1)))
            cx = x0 + (start + width / 2) * s
            tw = _width(text, _SMALL, False)
            if kind == "lower":
                rows = [y - 2.5 - 0.95 * _SMALL - k * 7.5 for k in range(5)]
            else:
                rows = [y + 2.5 + 0.25 * _SMALL + k * 7.5 for k in range(5)]
            block = placer.put(([_label(text, _SMALL, cx - tw / 2, base)] for base in rows), text)
            if kind == "lower":
                lowest = min(lowest, block[0].box()[1])
    # длина стены общей стрелкой
    y = lowest - 5
    segments.append(Segment(x0=x0, y0=y, x1=x0 + wall.length * s, y1=y, arrows=True))
    total = str(num(wall.length))
    tw = _width(total, _SMALL, True)
    cx = x0 + wall.length * s / 2
    placer.put(
        (
            [_label(total, _SMALL, cx - tw / 2 + t, y - 2 - 0.95 * _SMALL, bold=True)]
            for t in _steps(wall.length * s, 6)
        ),
        total,
    )
    # высоты от пола слева
    lx = x0 - 5
    segments.append(Segment(x0=lx, y0=z0, x1=lx, y1=z0 + max(lv.z for lv in levels) * s))
    for lv, text in zip(levels, level_texts, strict=True):
        ly = z0 + lv.z * s
        segments.append(Segment(x0=lx - 2, y0=ly, x1=lx + 2, y1=ly))
        tw = _width(text, _SMALL, False)
        placer.put(
            ([_label(text, _SMALL, lx - 3 - tw - k * 4, ly - 0.35 * _SMALL + dy)]
             for dy in (0.0, 3.0, -3.0, 6.0, -6.0) for k in range(10)),
            text,
        )  # fmt: skip
    return ViewPage(
        kind="wall",
        wall=wall.label,
        title=title,
        scale=n,
        shapes=shapes,
        segments=segments,
        labels=placer.labels,
        chains=chains,
        levels=levels,
    )


def layout(scene: ViewScene, errors: int = 0) -> list[ViewPage]:
    """Страницы видов: план сверху, затем по странице на стену в порядке цепочки. `errors` — число ошибок
    проверок проекта: при ошибках на каждой странице плашка `checks_banner`."""
    register_fonts()
    return [_plan(scene, errors), *(_wall(scene, w, errors) for w in scene.walls)]

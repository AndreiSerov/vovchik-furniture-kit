"""Присадка (тикет 19): соединения деталей внутри шаблона и двери на петлях -> отверстия и фурнитура.

Шаблон только называет, какая деталь крепится к какой (`Joint`) и какой фасад висит на петлях с какой
стороны (`HingedDoor`). Отсюда одним местом считаются и число конфирматов, петель, планок, и отверстия
под них: второго правила подсчёта нет. Числа — из `construction.yaml` (`присадка`, таблицы ступеней).
Схема отверстия — в `kitchen.geometry`; `parts.build_module` зовёт этот модуль после шаблона. Деталь
(`kitchen.parts.Part`) модуль получает готовой и импортирует её только для типов: `parts` импортирует его.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Literal, cast

from pydantic import BaseModel

from kitchen.errors import KitchenError, OrderError
from kitchen.geometry import (
    PURPOSE_RU,
    DrawerRunners,
    Face,
    HingedDoor,
    Hole,
    Joint,
    Mm,
    PinnedShelf,
    Point,
    Purpose,
    side_of_face,
    steps_pick,
    to_mm,
)
from kitchen.models import Workshop

if TYPE_CHECKING:
    from kitchen.parts import Part

__all__ = ["DrillingCheck", "apply_drilling", "part_extent", "plate_warnings", "reconcile"]

Extent = dict[str, tuple[int, int]]


def part_extent(part: Part) -> Extent:
    """Габарит детали в модуле: по каждой оси (начало, конец), мм."""
    o = part.orientation
    sizes = {o.length: part.length, o.width: part.width, o.thickness: part.thickness}
    at = part.position
    return {a: (getattr(at, a), getattr(at, a) + sizes[a]) for a in "xyz"}


def _round(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_HALF_UP))


def _spread(n: int, lo: int, hi: int, start: int, end: int) -> list[int]:
    """`n` точек на отрезке [lo, hi]: одна — посередине; крайние — в `start` от lo и `end` от hi, остальные делят
    промежуток поровну с округлением до мм. Список идёт от `lo` к `hi`."""
    if n <= 0:
        return []
    if n == 1:
        return [lo + _round(Decimal(hi - lo) / 2)]
    first, last = lo + start, hi - end
    if last < first:
        return []  # не помещается: вызывающий называет ошибку
    return [first + _round(Decimal(last - first) * i / (n - 1)) for i in range(n)]


def _sign_end(face: str, ext: Extent) -> int:
    lo, hi = ext[face[1]]
    return lo if face[0] == "-" else hi


@dataclass
class _Draft:
    """Отверстие до расчёта расстояний от базовых кромок."""

    purpose: Purpose
    surface: Literal["face", "end"]
    from_face: Face
    diameter: int
    depth: int
    center: dict[str, Mm]
    through: bool = False
    holds_shelf: bool = False


def _confirmats(module: str, plate: Part, end: Part, ws: Workshop) -> tuple[list[_Draft], list[_Draft]]:
    """Отверстия одного соединения: сквозные в пласти `plate` и в торце `end`, оси совпадают. Длина соединения —
    размер торца `end` по глубине модуля; число конфирматов — по таблице `конфирматы_по_длине_соединения`."""
    c = ws.construction
    rules = c.drilling.confirmat
    axis = plate.orientation.thickness
    pe, ee = part_extent(plate), part_extent(end)
    if ee[axis][0] == pe[axis][1]:
        end_face = plate_face = cast(
            Face, f"-{axis}"
        )  # `end` справа от `plate`: торец «-», наружная пласть «-»
    elif ee[axis][1] == pe[axis][0]:
        end_face = plate_face = cast(Face, f"+{axis}")
    else:
        raise KitchenError(
            f"Модуль {module}: детали {plate.id} и {end.id} не соприкасаются, конфирмат не поставить."
        )
    along = next(a for a in (end.orientation.length, end.orientation.width) if a != axis)
    across = end.orientation.thickness
    if along != "y":
        raise KitchenError(f"Модуль {module}: соединение {plate.id} и {end.id} идёт не по глубине модуля.")
    lo, hi = ee["y"]
    n = steps_pick(c.confirmats_by_joint_length, hi - lo, "конфирматы_по_длине_соединения")
    # от лицевой (передней, +y) кромки к задней
    ys = list(reversed(_spread(n, lo, hi, rules.back_offset, rules.front_offset)))
    if len(ys) != n:
        raise OrderError(
            f"Модуль {module}: соединение {plate.id} и {end.id} длиной {hi - lo} мм короче отступов конфирматов "
            f"({rules.front_offset} + {rules.back_offset} мм) при {n} конфирматах."
        )
    mid = ee[across][0] + _round(Decimal(end.thickness) / 2)
    face_holes, end_holes = [], []
    for y in ys:
        face_holes.append(
            _Draft(
                "confirmat",
                "face",
                plate_face,
                rules.face_diameter,
                plate.thickness,
                {axis: _sign_end(plate_face, pe), "y": y, across: mid},
                through=True,
            )
        )
        end_holes.append(
            _Draft(
                "confirmat",
                "end",
                end_face,
                rules.end_diameter,
                rules.end_depth,
                {axis: _sign_end(end_face, ee), "y": y, across: mid},
            )
        )
    return face_holes, end_holes


def _hinges(
    module: str, door: HingedDoor, facade: Part, side: Part, ws: Workshop
) -> tuple[list[_Draft], list[_Draft]]:
    """Чашки на фасаде со стороны петель и отверстия ответных планок на боковине на высоте каждой чашки.
    Число петель — по таблице `петли_по_высоте_фасада` от высоты фасада."""
    c = ws.construction
    hinge, plate = c.drilling.hinge, c.drilling.plate
    fe, se = part_extent(facade), part_extent(side)
    (x0, x1), (z0, z1) = fe["x"], fe["z"]
    n = steps_pick(c.hinges_by_facade_height, z1 - z0, "петли_по_высоте_фасада")
    zs = _spread(n, z0, z1, hinge.end_offset, hinge.end_offset)
    if len(zs) != n:
        raise OrderError(
            f"Модуль {module}: фасад {facade.id} высотой {z1 - z0} мм ниже двух отступов чашек петель "
            f"({hinge.end_offset} мм от торцов)."
        )
    x = x0 + hinge.edge_offset if door.side == "left" else x1 - hinge.edge_offset
    cups = [
        _Draft(
            "hinge_cup", "face", "-y", hinge.cup_diameter, hinge.cup_depth, {"x": x, "y": fe["y"][0], "z": z}
        )
        for z in zs
    ]
    inner: Face = "+x" if door.side == "left" else "-x"
    y = se["y"][1] - plate.front_offset
    plates = [
        _Draft(
            "hinge_plate",
            "face",
            inner,
            plate.diameter,
            plate.depth,
            {
                "x": _sign_end(inner, se),
                "y": y,
                "z": z + _round(Decimal(plate.pitch) * (2 * k - plate.holes + 1) / 2),
            },
        )
        for z in zs
        for k in range(plate.holes)
    ]
    return cups, plates


def _inner_face(module: str, side: Part, inside: Part) -> Face:
    """Пласть боковины, обращённая к детали `inside` (полке, ящику): у левой `+x`, у правой `-x`."""
    se, ie = part_extent(side), part_extent(inside)
    if se["x"][1] <= ie["x"][0]:
        return "+x"
    if se["x"][0] >= ie["x"][1]:
        return "-x"
    raise KitchenError(f"Модуль {module}: деталь {inside.id} не между боковинами, боковина {side.id}.")


def _fits(draft: _Draft, ext: Extent) -> bool:
    """Окружность устья целиком в своей грани детали: по каждой оси плоскости — не ближе радиуса к краю."""
    c, d = draft.center, draft.diameter
    return all(2 * min(c[a] - ext[a][0], ext[a][1] - c[a]) >= d for a in "xyz" if a != draft.from_face[1])


def _under(draft: _Draft, covers: list[Part]) -> Part | None:
    """Деталь из `covers` (прикреплена торцом к пласти: дно, крыша, царга, перегородка), под торец которой
    заходит окружность отверстия в этой пласти. Касание допустимо."""
    c, plane = draft.center, [a for a in "xyz" if a != draft.from_face[1]]
    for part in covers:
        pe = part_extent(part)
        gap = [max(pe[a][0] - c[a], 0, c[a] - pe[a][1]) for a in plane]
        if 4 * sum(g * g for g in gap) < draft.diameter**2:
            return part
    return None


_SHELF_KEYS = (
    "ключи «присадка.полкодержатель.отступ_от_{}_кромки_полки» и «полка.отступ_спереди» construction.yaml"
)


def _shelf_pins(module: str, shelf: Part, side: Part, covers: list[Part], ws: Workshop) -> list[_Draft]:
    """Ряды под полкодержатели на боковине: в `отступ_от_передней_кромки_полки` от передней кромки полки и в
    `отступ_от_задней_кромки_полки` от задней, по `отверстий_в_ряду` с шагом `шаг`. Центр отверстия под полку —
    на `центр_ниже_полки` ниже её нижней пласти (верх штыря — низ полки), в ряду по столько же шагов выше и ниже.
    Ряд, который у дна или крыши выходит за боковину или заходит под деталь корпуса, сдвигается целиком на целые
    шаги (сначала на меньший), пока не встанет; отверстие под полку остаётся в ряду
    (решение мастера). Не встаёт и так — ошибка модуля."""
    rules = ws.construction.drilling.shelf_pin
    face = _inner_face(module, side, shelf)
    se, he = part_extent(side), part_extent(shelf)
    z = Decimal(he["z"][0]) - Decimal(str(rules.below_shelf))
    half = rules.holes // 2
    shifts = sorted(
        range(-half, half + 1), key=abs
    )  # встать могут не оба равных сдвига: мешает край с одной стороны
    out = []
    for edge, y in (
        ("передней", he["y"][1] - rules.front_offset),
        ("задней", he["y"][0] + rules.back_offset),
    ):
        if not he["y"][0] <= y <= he["y"][1]:
            raise OrderError(
                f"Модуль {module}: ряд полкодержателей на боковине {side.id} (y={y}) не под полкой {shelf.id} "
                f"глубиной {he['y'][1] - he['y'][0]} мм; {_SHELF_KEYS.format(edge)}."
            )

        def row(shift: int, y: int = y) -> list[_Draft]:
            return [
                _Draft(
                    "shelf_pin",
                    "face",
                    face,
                    rules.diameter,
                    rules.depth,
                    {"x": _sign_end(face, se), "y": y, "z": to_mm(z + k * rules.pitch)},
                    holds_shelf=k == 0,
                )
                for k in range(shift - half, shift + half + 1)
            ]

        fitted = next(
            (r for r in map(row, shifts) if all(_fits(d, se) and _under(d, covers) is None for d in r)), None
        )
        if fitted is None:
            raise OrderError(
                f"Модуль {module}: ряд полкодержателей на боковине {side.id} под полку {shelf.id} "
                f"(отверстие под полку на {to_mm(z)} мм) не помещается между деталями корпуса даже со сдвигом."
            )
        out += fitted
    return out


def _runners(module: str, drawer: DrawerRunners, side: Part, inside: Part, ws: Workshop) -> list[_Draft]:
    """Отверстия направляющей Tandembox на внутренней пласти боковины: расстояния от передней кромки — по
    ключу `отверстия_от_передней_кромки`, высота — дно ящика плюс `над_дном_ящика`."""
    rules = ws.construction.drilling.runner
    face = _inner_face(module, side, inside)
    se = part_extent(side)
    z = drawer.bottom_z + rules.above_bottom
    return [
        _Draft(
            "runner",
            "face",
            face,
            rules.diameter,
            rules.depth,
            {"x": _sign_end(face, se), "y": se["y"][1] - d, "z": z},
        )
        for d in rules.from_front
    ]


def _base_edges(part: Part, ws: Workshop) -> tuple[str, str]:
    plane = (part.orientation.length, part.orientation.width)
    first, second = (f for f in ws.construction.drilling.base_edges if f[1] in plane)
    return first, second


def _hole(part: Part, draft: _Draft, base: tuple[str, str], ext: Extent) -> Hole:
    """Отверстие детали с расстояниями от её базовых кромок (модель проверяется pydantic как обычно)."""

    def distance(face: str) -> Mm:
        lo, hi = ext[face[1]]
        value = Decimal(str(draft.center[face[1]]))
        return to_mm(value - lo if face[0] == "-" else hi - value)

    end = draft.surface == "end"
    th = part.orientation.thickness
    o = part.orientation
    return Hole(
        purpose=draft.purpose,
        surface=draft.surface,
        from_face=draft.from_face,
        edge_side=side_of_face(draft.from_face, o.length, o.width, part.id) if end else None,
        diameter=draft.diameter,
        depth=draft.depth,
        through=draft.through,
        center=Point(**draft.center),
        from_base=(distance(base[0]), distance(base[1])),
        in_thickness=draft.center[th] - ext[th][0] if end else None,
        holds_shelf=draft.holds_shelf,
    )


def _where(module: str, part: Part, draft: _Draft) -> str:
    c = draft.center
    return (
        f"Модуль {module}: отверстие «{PURPOSE_RU[draft.purpose]}» Ø{draft.diameter} на детали {part.id} "
        f"(x={c['x']}, y={c['y']}, z={c['z']})"
    )


def _check_inside(module: str, part: Part, draft: _Draft, ext: Extent) -> None:
    """Отверстие целиком в детали: устье на грани `from_face`, окружность внутри грани, глубина не больше
    размера детали по оси сверления. Иначе — ошибка модуля, а не отверстие мимо детали."""
    axis = draft.from_face[1]
    on_face = draft.center[axis] == _sign_end(draft.from_face, ext)
    expected = (part.orientation.thickness == axis) == (draft.surface == "face")
    deep = draft.depth <= ext[axis][1] - ext[axis][0]
    if not (on_face and expected and _fits(draft, ext) and deep):
        raise OrderError(f"{_where(module, part, draft)} выходит за деталь.")


_FREE = ("shelf_pin", "runner")  # отверстия, которым не место под торцом прикреплённой детали


def _check_free(module: str, part: Part, draft: _Draft, covers: list[Part]) -> None:
    """Полкодержатель и направляющая не заходят под дно, крышу, царгу или перегородку на этой пласти."""
    if draft.purpose in _FREE and (cover := _under(draft, covers)) is not None:
        raise OrderError(f"{_where(module, part, draft)} заходит под деталь {cover.id}.")


def _check_apart(module: str, part: Part, drafts: list[_Draft], ext: Extent) -> None:
    """Отверстия детали не пересекаются: в пласти — окружности, в торце — встречные по одной оси."""
    plane = [k for k in "xyz" if k != part.orientation.thickness]
    for i, a in enumerate(drafts):
        ca, axis = a.center, a.from_face[1]
        for b in drafts[i + 1 :]:
            if a.surface != b.surface:
                continue
            cb = b.center
            if a.surface == "face":
                d2 = sum((ca[k] - cb[k]) ** 2 for k in plane)
                # глухие отверстия с противоположных пластей не встречаются, если глубины не сходятся
                apart = a.from_face != b.from_face and a.depth + b.depth <= part.thickness
                clash = 4 * d2 < (a.diameter + b.diameter) ** 2 and not apart
            else:
                same_line = b.from_face[1] == axis and all(ca[k] == cb[k] for k in "xyz" if k != axis)
                clash = same_line and (
                    a.from_face == b.from_face or a.depth + b.depth > ext[axis][1] - ext[axis][0]
                )
            if clash:
                raise OrderError(
                    f"{_where(module, part, a)} пересекается с отверстием «{PURPOSE_RU[b.purpose]}»."
                )


def apply_drilling(
    module: str,
    parts: list[Part],
    joints: list[Joint],
    doors: list[HingedDoor],
    ws: Workshop,
    *,
    shelves: list[PinnedShelf] | None = None,
    drawers: list[DrawerRunners] | None = None,
) -> tuple[list[Part], Counter]:
    """Детали с отверстиями и фурнитура соединений и петель модуля (конфирматы и заглушки, петли, планки,
    саморезы на петли). Позиции фурнитуры появляются, только если в модуле есть соединения или двери на
    петлях. Планок больше одной на петлю присадка не размечает: их отверстий нет, расчёт предупреждает один
    раз (`plate_warnings`), а сверка карты присадки (`reconcile`) отказывает с этой причиной.
    Полкодержатели и направляющие (тикет 20) считает шаблон; здесь — только их отверстия на боковинах."""
    c = ws.construction
    hw = c.furniture
    by_id = {p.id: p for p in parts}
    drafts: dict[str, list[_Draft]] = {}

    def get(pid: str) -> Part:
        if pid not in by_id:
            raise KitchenError(f"Модуль {module}: присадка ссылается на деталь {pid}, которой нет в модуле.")
        return by_id[pid]

    hardware: Counter = Counter()
    if joints:
        confirmats = 0
        for joint in joints:
            face, end = _confirmats(module, get(joint.plate), get(joint.end), ws)
            drafts.setdefault(joint.plate, []).extend(face)
            drafts.setdefault(joint.end, []).extend(end)
            confirmats += len(face)
        hardware[hw.confirmats] += confirmats
        hardware[hw.caps] += confirmats * c.caps.per_unit
    if doors:
        plated = c.plates.per_unit == 1  # иначе планки не размечаются: см. `plate_warnings`
        hinges = 0
        for door in doors:
            side = door.side_part
            cups, plates = _hinges(module, door, get(door.facade), get(side), ws)
            drafts.setdefault(door.facade, []).extend(cups)
            if plated:
                drafts.setdefault(side, []).extend(plates)
            hinges += len(cups)
        hardware[hw.hinges] += hinges
        hardware[hw.plates] += hinges * c.plates.per_unit
        hardware[hw.screws] += hinges * c.screws_3_5x16.per_hinge

    def covers(pid: str) -> list[Part]:
        return [get(j.end) for j in joints if j.plate == pid]

    for shelf in shelves or []:
        for pid in shelf.sides:
            drafts.setdefault(pid, []).extend(
                _shelf_pins(module, get(shelf.shelf), get(pid), covers(pid), ws)
            )
    for drawer in drawers or []:
        left, right = (get(pid) for pid in drawer.sides)
        for side, other in ((left, right), (right, left)):
            drafts.setdefault(side.id, []).extend(_runners(module, drawer, side, other, ws))
    out = []
    for part in parts:
        if part.id not in drafts:
            out.append(part)
            continue
        ext = part_extent(part)
        base = _base_edges(part, ws)
        on_it = covers(part.id)
        for d in drafts[part.id]:
            _check_inside(module, part, d, ext)
            _check_free(module, part, d, on_it)
        _check_apart(module, part, drafts[part.id], ext)
        made = [_hole(part, d, base, ext) for d in drafts[part.id]]
        out.append(part.model_copy(update={"holes": made, "base_edges": list(base)}))
    return out, hardware


def _plates_not_marked(ws: Workshop) -> str | None:
    """Почему отверстия планок не размечены: присадка ставит одну планку на петлю."""
    per_unit = ws.construction.plates.per_unit
    if per_unit == 1:
        return None
    return (
        f"отверстия ответных планок не размечены: планки по {per_unit} на петлю (ключ «ответные_планки» "
        "construction.yaml) присадка не размечает, она ставит одну планку на петлю"
    )


def plate_warnings(ws: Workshop, hardware: dict[str, int]) -> list[str]:
    """Одно предупреждение на расчёт, если в нём есть планки, а размечать их присадка не умеет."""
    reason = _plates_not_marked(ws)
    return [reason] if reason and hardware.get(ws.construction.furniture.plates) else []


class DrillingCheck(BaseModel):
    """Строка сверки присадки с фурнитурой этапа 1: отверстий в деталях и сколько их должно быть. `short` —
    короткое имя для строки сверки, когда полная не помещается на карте."""

    name: str
    short: str
    holes: int
    expected: int
    basis: str  # откуда ожидаемое число


ROWS_PER_SHELF = 4  # ряд спереди и сзади на каждой из двух боковин (решение мастера, тикет 20)
RUNNERS_PER_SET = 2  # комплект Tandembox — левая и правая направляющие


def _pins_not_marked(ws: Workshop) -> str | None:
    pins = ws.construction.shelf.pins
    if pins == ROWS_PER_SHELF:
        return None
    return (
        f"полкодержателей по {pins} на полку (ключ «полка.полкодержателей» construction.yaml), а присадка ставит "
        f"{ROWS_PER_SHELF} ряда на полку — по ряду спереди и сзади на каждой боковине"
    )


def reconcile(parts: list[Part], hardware: dict[str, int], ws: Workshop) -> list[DrillingCheck]:
    """Отверстий в пласть = в торец = конфирматов, чашек = петель, отверстий планок = планок × отверстий на планку,
    отверстий полкодержателей = полкодержателей × отверстий в ряду (ряд на полкодержатель), отверстий
    направляющих = комплектов Tandembox × отверстий на комплект (две направляющие).
    Расхождение — ошибка: карта присадки не должна разойтись с закупкой."""
    c = ws.construction
    hw = c.furniture
    d = c.drilling

    def count(purpose: str, surface: str | None = None) -> int:
        return sum(
            1
            for p in parts
            for h in p.holes or []
            if h.purpose == purpose and (surface is None or h.surface == surface)
        )

    per_plate = d.plate.holes
    per_row = d.shelf_pin.holes
    per_set = RUNNERS_PER_SET * len(d.runner.from_front)
    # сверка и почему она может не сойтись по правилам мастерской (присадка такого не размечает)
    rows: list[tuple[DrillingCheck, str | None]] = [
        (
            DrillingCheck(
                name="конфирматы: отверстий в пласть",
                short="конфирматы в пласть",
                holes=count("confirmat", "face"),
                expected=hardware.get(hw.confirmats, 0),
                basis=f"{hw.confirmats} в фурнитуре",
            ),
            None,
        ),
        (
            DrillingCheck(
                name="конфирматы: отверстий в торец",
                short="в торец",
                holes=count("confirmat", "end"),
                expected=hardware.get(hw.confirmats, 0),
                basis=f"{hw.confirmats} в фурнитуре",
            ),
            None,
        ),
        (
            DrillingCheck(
                name="чашки петель",
                short="чашки",
                holes=count("hinge_cup"),
                expected=hardware.get(hw.hinges, 0),
                basis=f"{hw.hinges} в фурнитуре",
            ),
            None,
        ),
        (
            DrillingCheck(
                name="ответные планки: отверстий",
                short="планки",
                holes=count("hinge_plate"),
                expected=hardware.get(hw.plates, 0) * per_plate,
                basis=f"{hw.plates} в фурнитуре × {per_plate} на планку",
            ),
            _plates_not_marked(ws),
        ),
        (
            DrillingCheck(
                name="полкодержатели: отверстий",
                short="полкодержатели",
                holes=count("shelf_pin"),
                expected=hardware.get(hw.shelf_pins, 0) * per_row,
                basis=f"{hw.shelf_pins} в фурнитуре × {per_row} в ряду",
            ),
            _pins_not_marked(ws),
        ),
        (
            DrillingCheck(
                name="направляющие: отверстий",
                short="направляющие",
                holes=count("runner"),
                expected=hardware.get(c.drawers.hardware, 0) * per_set,
                basis=f"{c.drawers.hardware} в фурнитуре × {per_set} на комплект",
            ),
            None,
        ),
    ]
    wrong = [ch for ch, _ in rows if ch.holes != ch.expected]
    if wrong:
        causes = [f" Причина: {why}." for ch, why in rows if why and ch in wrong]
        raise OrderError(
            "Присадка не сходится с фурнитурой: "
            + "; ".join(f"{ch.name} — {ch.holes}, а нужно {ch.expected} ({ch.basis})" for ch in wrong)
            + "."
            + "".join(causes)
        )
    return [ch for ch, _ in rows]

"""Присадка (тикет 19): соединения деталей внутри шаблона и двери на петлях -> отверстия и фурнитура.

Шаблон только называет, какая деталь крепится к какой (`Joint`) и какой фасад висит на петлях с какой
стороны (`HingedDoor`). Отсюда одним местом считаются и число конфирматов, петель, планок, и отверстия
под них: второго правила подсчёта нет. Числа — из `construction.yaml` (`присадка`, таблицы ступеней).
Схема отверстия — в `kitchen.parts` рядом с деталью; `parts.build_module` зовёт этот модуль после шаблона.
"""

from collections import Counter
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from pydantic import BaseModel

from kitchen.errors import KitchenError, OrderError
from kitchen.models import Workshop
from kitchen.parts import (
    PURPOSE_RU,
    HingedDoor,
    Hole,
    Joint,
    Part,
    Position,
    Purpose,
    _side_of_face,
    steps_pick,
)

__all__ = ["DrillingCheck", "apply_drilling", "part_extent", "reconcile"]

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
    from_face: str
    diameter: int
    depth: int
    center: dict[str, int]
    through: bool = False


def _confirmats(module: str, plate: Part, end: Part, ws: Workshop) -> tuple[list[_Draft], list[_Draft]]:
    """Отверстия одного соединения: сквозные в пласти `plate` и в торце `end`, оси совпадают. Длина соединения —
    размер торца `end` по глубине модуля; число конфирматов — по таблице `конфирматы_по_длине_соединения`."""
    c = ws.construction
    rules = c.drilling.confirmat
    axis = plate.orientation.thickness
    pe, ee = part_extent(plate), part_extent(end)
    if ee[axis][0] == pe[axis][1]:
        end_face = plate_face = f"-{axis}"  # `end` справа от `plate`: торец «-», наружная пласть `plate` «-»
    elif ee[axis][1] == pe[axis][0]:
        end_face = plate_face = f"+{axis}"
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
    inner = "+x" if door.side == "left" else "-x"
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


def _base_edges(part: Part, ws: Workshop) -> tuple[str, str]:
    plane = (part.orientation.length, part.orientation.width)
    first, second = (f for f in ws.construction.drilling.base_edges if f[1] in plane)
    return first, second


def _hole(part: Part, draft: _Draft, base: tuple[str, str], ext: Extent) -> Hole:
    """Отверстие с расстояниями от базовых кромок. Числа уже проверены (`_check_inside`), поэтому модель
    собирается без повторной валидации: расчёт деталей зовут много раз за команду."""

    def distance(face: str) -> int:
        lo, hi = ext[face[1]]
        value = draft.center[face[1]]
        return value - lo if face[0] == "-" else hi - value

    end = draft.surface == "end"
    th = part.orientation.thickness
    o = part.orientation
    return Hole(
        purpose=draft.purpose,
        surface=draft.surface,
        from_face=draft.from_face,
        edge_side=_side_of_face(draft.from_face, o.length, o.width, part.id) if end else None,
        diameter=draft.diameter,
        depth=draft.depth,
        through=draft.through,
        center=Position(**draft.center),
        from_base=(distance(base[0]), distance(base[1])),
        in_thickness=draft.center[th] - ext[th][0] if end else None,
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
    axis, center = draft.from_face[1], draft.center
    on_face = center[axis] == _sign_end(draft.from_face, ext)
    expected = (part.orientation.thickness == axis) == (draft.surface == "face")
    fits = all(
        2 * min(center[a] - ext[a][0], ext[a][1] - center[a]) >= draft.diameter for a in "xyz" if a != axis
    )
    deep = draft.depth <= ext[axis][1] - ext[axis][0]
    if not (on_face and expected and fits and deep):
        raise OrderError(f"{_where(module, part, draft)} выходит за деталь.")


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
                clash = 4 * d2 < (a.diameter + b.diameter) ** 2
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
    module: str, parts: list[Part], joints: list[Joint], doors: list[HingedDoor], ws: Workshop
) -> tuple[list[Part], Counter, list[str]]:
    """Детали с отверстиями, фурнитура соединений и петель модуля (конфирматы и заглушки, петли, планки,
    саморезы на петли) и предупреждения. Позиции фурнитуры появляются, только если в модуле есть соединения
    или двери на петлях. Планок больше одной на петлю присадка не размечает: их отверстий нет, есть
    предупреждение, а сверка карты присадки (`reconcile`) отказывает."""
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
    warnings = []
    if doors:
        plated = c.plates.per_unit == 1
        if not plated:
            warnings.append(
                "отверстия ответных планок не размечены: присадка ставит одну планку на петлю, "
                f"а «ответные_планки» в construction.yaml — по {c.plates.per_unit} на петлю"
            )
        hinges = 0
        for door in doors:
            side = f"{module}-бок-{'Л' if door.side == 'left' else 'П'}"
            cups, plates = _hinges(module, door, get(door.facade), get(side), ws)
            drafts.setdefault(door.facade, []).extend(cups)
            if plated:
                drafts.setdefault(side, []).extend(plates)
            hinges += len(cups)
        hardware[hw.hinges] += hinges
        hardware[hw.plates] += hinges * c.plates.per_unit
        hardware[hw.screws] += hinges * c.screws_3_5x16.per_hinge
    out = []
    for part in parts:
        if part.id not in drafts:
            out.append(part)
            continue
        ext = part_extent(part)
        base = _base_edges(part, ws)
        for d in drafts[part.id]:
            _check_inside(module, part, d, ext)
        _check_apart(module, part, drafts[part.id], ext)
        made = [_hole(part, d, base, ext) for d in drafts[part.id]]
        out.append(part.model_copy(update={"holes": made, "base_edges": list(base)}))
    return out, hardware, warnings


class DrillingCheck(BaseModel):
    """Строка сверки присадки с фурнитурой этапа 1: отверстий в деталях и сколько их должно быть."""

    name: str
    holes: int
    expected: int
    basis: str  # откуда ожидаемое число


def reconcile(parts: list[Part], hardware: dict[str, int], ws: Workshop) -> list[DrillingCheck]:
    """Отверстий в пласть = в торец = конфирматов, чашек = петель, отверстий планок = планок × отверстий на планку.
    Расхождение — ошибка: карта присадки не должна разойтись с закупкой."""
    c = ws.construction
    hw = c.furniture

    def count(purpose: str, surface: str | None = None) -> int:
        return sum(
            1
            for p in parts
            for h in p.holes or []
            if h.purpose == purpose and (surface is None or h.surface == surface)
        )

    per_plate = c.drilling.plate.holes
    checks = [
        DrillingCheck(
            name="конфирматы: отверстий в пласть",
            holes=count("confirmat", "face"),
            expected=hardware.get(hw.confirmats, 0),
            basis=f"{hw.confirmats} в фурнитуре",
        ),
        DrillingCheck(
            name="конфирматы: отверстий в торец",
            holes=count("confirmat", "end"),
            expected=hardware.get(hw.confirmats, 0),
            basis=f"{hw.confirmats} в фурнитуре",
        ),
        DrillingCheck(
            name="чашки петель",
            holes=count("hinge_cup"),
            expected=hardware.get(hw.hinges, 0),
            basis=f"{hw.hinges} в фурнитуре",
        ),
        DrillingCheck(
            name="ответные планки: отверстий",
            holes=count("hinge_plate"),
            expected=hardware.get(hw.plates, 0) * per_plate,
            basis=f"{hw.plates} в фурнитуре × {per_plate} на планку",
        ),
    ]
    wrong = [
        f"{ch.name} — {ch.holes}, а нужно {ch.expected} ({ch.basis})"
        for ch in checks
        if ch.holes != ch.expected
    ]
    if wrong:
        raise OrderError("Присадка не сходится с фурнитурой: " + "; ".join(wrong) + ".")
    return checks

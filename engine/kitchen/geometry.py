"""Геометрия детали и схема отверстий: оси и грани модуля, положение, отверстие, соединение, дверь на петлях,
полка на полкодержателях, ящик на направляющих.

Модуль ни от чего в движке не зависит, кроме ошибок и типов мастерской: его читают и шаблоны (`kitchen.parts`),
и присадка (`kitchen.drilling`), поэтому они не импортируют друг друга по кругу.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from kitchen.errors import WorkshopError
from kitchen.models import Num

__all__ = [
    "FACES",
    "PURPOSE_RU",
    "Axis",
    "DrawerRunners",
    "EdgeSide",
    "Face",
    "HingeSide",
    "HingedDoor",
    "Hole",
    "Joint",
    "Mm",
    "Orientation",
    "PinnedShelf",
    "Point",
    "Position",
    "Purpose",
    "side_of_face",
    "steps_pick",
    "to_mm",
    "turn",
]

Axis = Literal["x", "y", "z"]
Face = Literal["+x", "-x", "+y", "-y", "+z", "-z"]  # грань в осях модуля: знак и ось
FACES: tuple[Face, ...] = ("+x", "-x", "+y", "-y", "+z", "-z")
EdgeSide = Literal["L1", "L2", "W1", "W2"]
Purpose = Literal["confirmat", "hinge_cup", "hinge_plate", "shelf_pin", "runner"]
PURPOSE_RU: dict[str, str] = {
    "confirmat": "конфирмат",
    "hinge_cup": "чашка петли",
    "hinge_plate": "ответная планка",
    "shelf_pin": "полкодержатель",
    "runner": "направляющая",
}
HingeSide = Literal["left", "right"]


class Position(BaseModel):
    """Угол детали в системе модуля, мм: x вдоль стены, y от стены, z вверх от низа корпуса."""

    x: int
    y: int
    z: int


Mm = int | float  # координата отверстия, мм: целая, у полкодержателя — до десятых (центр ниже полки на 2.5)


def to_mm(value: Decimal) -> Mm:
    """Миллиметры без хвостов float: целое — `int`, иначе `float` до десятых."""
    return int(value) if value == value.to_integral_value() else float(value.quantize(Decimal("0.1")))


def turn(rotation: int, u: Mm, v: Mm) -> tuple[Mm, Mm]:
    """Вектор (u вдоль стены, v от стены) в осях комнаты при повороте стены `rotation`.

    Оси комнаты зеркальны (тикет 23): план PDF исправляет это переворотом y в `views._plan`, 3D — `toThree` в
    `web/viewer.js`; правка здесь меняет оба места."""
    return {
        0: (u, v),
        90: (-v, u),
        180: (-u, -v),
        270: (v, -u),
    }[rotation % 360]


class Point(BaseModel):
    """Центр отверстия в системе модуля, мм (как `Position`, но может быть дробным)."""

    x: Mm
    y: Mm
    z: Mm


class Orientation(BaseModel):
    """Какой оси модуля соответствует длина, ширина и толщина детали."""

    length: Axis
    width: Axis
    thickness: Axis


def side_of_face(face: str, length_axis: str, width_axis: str, pid: str) -> EdgeSide:
    """Грань детали (`+y`) -> сторона кромки: на оси ширины `+` — L1, `-` — L2;
    на оси длины `-` — W1 (начало), `+` — W2 (конец)."""
    sign, axis = face[0], face[1]
    if axis == width_axis:
        return "L1" if sign == "+" else "L2"
    if axis == length_axis:
        return "W2" if sign == "+" else "W1"
    raise ValueError(f"Деталь {pid}: грань {face} — плоскость детали, кромить её нельзя.")


def steps_pick(steps: list[tuple[Num, int]], value: Num, table: str) -> int:
    """Ступенчатая таблица: первая ступень, где значение ≤ порога."""
    for threshold, count in steps:
        if value <= threshold:
            return count
    thresholds = ", ".join(str(t) for t, _ in steps)
    raise WorkshopError(
        f"construction.yaml: в таблице «{table}» нет ступени для значения {value} (пороги: {thresholds})."
    )


class Hole(BaseModel):
    """Отверстие детали (тикет 19). `surface` — пласть (`face`) или торец (`end`); `from_face` — грань детали
    в осях модуля, с которой сверлят; `center` — центр устья в координатах модуля; `from_base` — расстояния
    от центра до базовых кромок детали (`Part.base_edges`, в том же порядке)."""

    purpose: Purpose
    surface: Literal["face", "end"]
    from_face: Face
    edge_side: EdgeSide | None = None  # у торца — сторона детали
    diameter: int
    depth: int
    through: bool = False
    center: Point
    from_base: tuple[Mm, Mm]
    in_thickness: int | None = None  # у торца — от пласти с меньшей координатой до центра, мм
    holds_shelf: bool = False  # отверстие ряда полкодержателей, на котором лежит полка (тикет 20)


@dataclass(frozen=True)
class Joint:
    """Деталь `end` торцом крепится конфирматами к пласти детали `plate` (маркировки)."""

    plate: str
    end: str


@dataclass(frozen=True)
class HingedDoor:
    """Фасад `facade` висит на петлях со стороны `side`; ответные планки — на боковине `side_part` той же
    стороны. Маркировки деталей называет шаблон."""

    facade: str
    side: HingeSide
    side_part: str


@dataclass(frozen=True)
class PinnedShelf:
    """Полка `shelf` лежит на полкодержателях в боковинах `sides` (маркировки: левая, правая), тикет 20."""

    shelf: str
    sides: tuple[str, str]


@dataclass(frozen=True)
class DrawerRunners:
    """Ящик на направляющих Tandembox: дно ящика на высоте `bottom_z` от низа корпуса, направляющие — на
    боковинах `sides` (маркировки: левая, правая), тикет 20."""

    bottom_z: int
    sides: tuple[str, str]

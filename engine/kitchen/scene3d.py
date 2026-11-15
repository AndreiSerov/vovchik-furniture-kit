"""Данные 3D-вида (5.1, тикет 42): модули кухни коробками в комнате, стены, пол и камера.

Строятся из тех же сцены видов (`views.build_scene`) и расстановки (`placement.place_project`), что и PDF
тикета 41: другой расстановки здесь нет. Оси — оси расстановки (x вдоль первой стены, y от стены в комнату,
z вверх, мм), в three.js их переводит скрипт страницы (`web/viewer.js`, `toThree`): оси расстановки
зеркальны по отношению к осям three.js (y вверх, правая тройка), поэтому он меняет y и z местами — это и есть
отражение, оси расстановки не трогаются.

Структура рассчитана на тикет 37 (детали, фасады, столешница): у модуля массив `boxes`, у каждой коробки — вид
`part`; сейчас одна коробка `body` на модуль. Нажатие в HTML читает `caption` (подпись для клиента) и
`size_label`.
"""

from typing import Literal

from pydantic import BaseModel

from kitchen.format import num
from kitchen.geometry import turn
from kitchen.models import Workshop
from kitchen.parts import TEMPLATES, ModuleParts
from kitchen.placement import Placement
from kitchen.project import ModuleSpec, Project, Row, is_gap
from kitchen.quote import describe_gap, describe_module
from kitchen.views import Footprint, ViewGap, ViewModule, ViewScene

__all__ = [
    "CAMERA_HEIGHT",
    "SCENE_VERSION",
    "Scene3D",
    "SceneBox",
    "SceneCamera",
    "SceneFloor",
    "SceneModule",
    "SceneWall",
    "build_scene3d",
]

SCENE_VERSION = 1
CAMERA_HEIGHT = 1600  # решение мастера 2026-11-15: камера в комнате на высоте 1600
TARGET_HEIGHT = 1000  # куда смотрит камера у угла (решение агента, ПРОВЕРИТЬ МАСТЕРУ)


class SceneBox(BaseModel):
    """Коробка в осях комнаты, мм: от `min` до `max`. `part` — что это: `body` — коробка модуля с фасадом;
    тикет 37 добавит детали, фасады и столешницу."""

    part: Literal["body"]
    min: list[float]
    max: list[float]
    rgb: str  # «#RRGGBB»


class SceneModule(BaseModel):
    label: str
    gap: bool  # пустое место, а не модуль
    wall: str
    row: Literal["lower", "upper"]
    caption: str  # «Н1 — нижний модуль с тремя ящиками, 600 мм»: для клиента, как строка состава КП
    size_label: str  # «Ш×В×Г 600×720×560 мм»
    width: float
    height: float
    depth: float
    front: list[float]  # единичный вектор лицевой грани в осях комнаты (x, y)
    color: str | None  # цвет фасадов, как записан в проекте
    color_known: bool
    boxes: list[SceneBox]


class SceneWall(BaseModel):
    label: str
    start: list[float]
    end: list[float]
    normal: list[float]  # единичный вектор в комнату (x, y)
    height: float


class SceneFloor(BaseModel):
    min: list[float]  # x, y
    max: list[float]


class SceneCamera(BaseModel):
    """Камера при открытии: на высоте `height` в комнате, смотрит на `target` (угол кухни). `direction` —
    единичный вектор (x, y) от угла в комнату, по нему камера отходит от угла; расстояние страница подбирает
    по экрану, чтобы вся кухня была в кадре."""

    height: int
    target: list[float]
    direction: list[float]


class Scene3D(BaseModel):
    version: int
    units: Literal["mm"]
    walls: list[SceneWall]
    floor: SceneFloor
    modules: list[SceneModule]
    camera: SceneCamera


def _num(value: float) -> int | float:
    return num(value, 4)


def _pair(x: float, y: float) -> list[float]:
    return [_num(x), _num(y)]


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def _size_label(width: float, height: float, depth: float) -> str:
    return f"Ш×В×Г {_num(width)}×{_num(height)}×{_num(depth)} мм"


def _depth_of(rotation: int, plan: Footprint) -> float:
    """Глубина полосы плана — её размер от стены."""
    return plan.y1 - plan.y0 if rotation % 180 == 0 else plan.x1 - plan.x0


def _scene_module(
    view: ViewModule | ViewGap,
    rotation: int,
    *,
    gap: bool,
    caption: str,
    depth: float,
    color: str | None,
    color_known: bool,
    rgb: str,
) -> SceneModule:
    """Модуль или пустое место сцены: одна коробка корпуса с габаритом, подписью и цветом."""
    z0 = view.front.z
    return SceneModule(
        label=view.label,
        gap=gap,
        wall=view.wall,
        row=view.row,
        caption=caption,
        size_label=_size_label(view.front.width, view.front.height, depth),
        width=_num(view.front.width),
        height=_num(view.front.height),
        depth=_num(depth),
        front=_pair(*turn(rotation, 0, 1)),
        color=color,
        color_known=color_known,
        boxes=[
            SceneBox(
                part="body",
                min=[_num(view.plan.x0), _num(view.plan.y0), _num(z0)],
                max=[_num(view.plan.x1), _num(view.plan.y1), _num(z0 + view.front.height)],
                rgb=rgb,
            )
        ],
    )


def _module(
    spec: ModuleSpec,
    row: Row,
    view: ViewModule,
    rotation: int,
    built: ModuleParts | None,
    project: Project,
    ws: Workshop,
) -> SceneModule:
    if built is not None:
        text = describe_module(spec, row, built, project, ws)
    else:  # модуль не строится (ошибка проверок): подпись без наполнения, голая коробка
        title = TEMPLATES[spec.template].title if spec.template in TEMPLATES else "модуль"
        text = f"{title}, {spec.width:g} мм"
    return _scene_module(
        view,
        rotation,
        gap=False,
        caption=f"{view.label} — {text}",
        depth=view.depth,
        color=view.color,
        color_known=view.color_known,
        rgb=_hex(view.rgb),
    )


def _gap(gap, view: ViewGap, rotation: int, project: Project) -> SceneModule:
    return _scene_module(
        view,
        rotation,
        gap=True,
        caption=f"{view.label} — {describe_gap(gap, project)}",
        depth=_depth_of(rotation, view.plan),
        color=None,
        color_known=False,
        rgb="#D8D8D8",
    )


def _camera(walls: list[SceneWall], floor: SceneFloor) -> SceneCamera:
    """Угол цепочки — конец первой стены (она встречается с началом второй); у одной стены — её середина.
    Камера отходит от угла по прямой к середине пола, а у одной стены — по её нормали."""
    first = walls[0]
    if len(walls) > 1:
        cx, cy = first.end
    else:
        cx, cy = (first.start[0] + first.end[0]) / 2, (first.start[1] + first.end[1]) / 2
    mx, my = (floor.min[0] + floor.max[0]) / 2, (floor.min[1] + floor.max[1]) / 2
    dx, dy = mx - cx, my - cy
    length = (dx * dx + dy * dy) ** 0.5
    if len(walls) == 1 or length < 1:
        dx, dy = first.normal
    else:
        dx, dy = dx / length, dy / length
    return SceneCamera(
        height=CAMERA_HEIGHT,
        target=[_num(cx), _num(cy), TARGET_HEIGHT],
        direction=[round(dx, 4), round(dy, 4)],
    )


def build_scene3d(
    project: Project, placed: Placement, view: ViewScene, built: list[ModuleParts], ws: Workshop
) -> Scene3D:
    """3D-сцена из сцены видов и расстановки. Модули и пустые места идут в порядке проекта (как в КП)."""
    by_label = {m.label: m for m in view.modules}
    gaps = {g.label: g for g in view.gaps}
    parts_of = {m.module: m for m in built}
    rotation = {w.label: w.rotation for w in placed.walls}
    modules: list[SceneModule] = []
    for wall in project.walls:
        for row in wall.rows:
            for entry in row.modules:
                if is_gap(entry):
                    modules.append(_gap(entry, gaps[entry.label], rotation[wall.label], project))
                else:
                    modules.append(
                        _module(
                            entry,
                            row,
                            by_label[entry.label],
                            rotation[wall.label],
                            parts_of.get(entry.label),
                            project,
                            ws,
                        )
                    )
    walls = [
        SceneWall(
            label=w.label,
            start=_pair(*w.start),
            end=_pair(*w.end),
            normal=_pair(*turn(w.rotation, 0, 1)),
            height=_num(view.ceiling),
        )
        for w in view.walls
    ]
    (floor_obj,) = [o for o in placed.scene if o.kind == "floor"]
    lo, hi = floor_obj.bounds()
    floor = SceneFloor(min=_pair(lo[0], lo[1]), max=_pair(hi[0], hi[1]))
    return Scene3D(
        version=SCENE_VERSION,
        units="mm",
        walls=walls,
        floor=floor,
        modules=modules,
        camera=_camera(walls, floor),
    )

"""Схема детали (1.2), шаблон `base_door` (1.3) и расчёт деталей проекта.

Все размеры и количества — из `construction.yaml` и `equipment.yaml` по формулам
и ступенчатым таблицам; в коде шаблона только структурные числа (число боковин,
царг, соединений). Идентификаторы фурнитуры берутся из ключа «фурнитура»
construction.yaml (МОК), ручек — из материалов проекта.
"""

from collections import Counter
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, Decimal
from typing import Literal

from pydantic import BaseModel

from kitchen import formulas
from kitchen.errors import OrderError, WorkshopError
from kitchen.models import Num, Workshop
from kitchen.project import ModuleSpec, Project, ProjectMaterials, Row

__all__ = [
    "TEMPLATES",
    "EdgeTotals",
    "ModuleDims",
    "ModuleParts",
    "Orientation",
    "Part",
    "PartsComputation",
    "Position",
    "compute_parts",
]

EDGE_SIDES = ("L1", "L2", "W1", "W2")


# --- схема детали ------------------------------------------------------------


class Position(BaseModel):
    """Угол детали в системе модуля, мм: x вдоль стены, y от стены, z вверх от низа корпуса."""

    x: int
    y: int
    z: int


class Orientation(BaseModel):
    """Какой оси модуля соответствует длина, ширина и толщина детали."""

    length: Literal["x", "y", "z"]
    width: Literal["x", "y", "z"]
    thickness: Literal["x", "y", "z"]


class Part(BaseModel):
    """Деталь: маркировка, размеры, материал, кромка по 4 сторонам, положение в модуле."""

    id: str  # маркировка вида «Н1-бок-Л»
    module: str
    name: str  # по-русски: «боковина», «дно», «царга», «полка», «задняя стенка», «фасад»
    length: int  # вдоль текстуры; для нетекстурных — больший размер
    width: int
    thickness: int
    material: str
    texture: bool
    edges: dict[str, str]  # L1/L2 (длинные), W1/W2 (короткие) — id кромки или пусто
    position: Position
    orientation: Orientation


class EdgeTotals(BaseModel):
    """Метраж кромки одного типа: чистая, с припуском, к закупке."""

    clean_m: float
    with_allowance_m: float
    purchase_m: float


class ModuleParts(BaseModel):
    """Детали одного модуля."""

    module: str
    template: str
    parts: list[Part]


def make_part(
    *,
    pid: str,
    module_label: str,
    name: str,
    size_a: int,
    axis_a: str,
    size_b: int,
    axis_b: str,
    thickness: int,
    material_id: str,
    textured: bool,
    edges: dict[str, str],
    x: int,
    y: int,
    z: int,
) -> Part:
    """Собирает деталь: длина — вдоль текстуры (ось текстуры задаёт шаблон),
    у нетекстурных — больший размер."""
    if textured or size_a >= size_b:
        length, length_axis = size_a, axis_a
        width, width_axis = size_b, axis_b
    else:
        length, length_axis = size_b, axis_b
        width, width_axis = size_a, axis_a
    if length_axis == width_axis:
        raise ValueError(f"Деталь {pid}: ось длины и ось ширины совпадают ({length_axis}).")
    thickness_axis = next(a for a in ("x", "y", "z") if a not in (length_axis, width_axis))
    edge_map = dict.fromkeys(EDGE_SIDES, "")
    edge_map.update(edges)
    return Part(
        id=pid,
        module=module_label,
        name=name,
        length=length,
        width=width,
        thickness=thickness,
        material=material_id,
        texture=textured,
        edges=edge_map,
        position=Position(x=x, y=y, z=z),
        orientation=Orientation(length=length_axis, width=width_axis, thickness=thickness_axis),
    )


# --- общие помощники шаблонов -------------------------------------------------


def steps_pick(steps: list[tuple[Num, int]], value: Num, table: str) -> int:
    """Ступенчатая таблица: первая ступень, где значение ≤ порога."""
    for threshold, count in steps:
        if value <= threshold:
            return int(count)
    thresholds = ", ".join(str(t) for t, _ in steps)
    raise WorkshopError(
        f"construction.yaml: в таблице «{table}» нет ступени для значения {value} (пороги: {thresholds})."
    )


@dataclass
class ModuleDims:
    """Габариты модуля в мм: из проекта или по ряду из construction.yaml."""

    width: int
    height: int
    depth: int


def _whole(value: Num, what: str) -> int:
    """Габарит в целых миллиметрах."""
    if float(value).is_integer():
        return int(value)
    raise OrderError(f"{what} — {value}, а ожидалось целое число миллиметров.")


def _module_dims(module: ModuleSpec, row_kind: str, ws: Workshop) -> ModuleDims:
    base = ws.construction.lower if row_kind == "lower" else ws.construction.upper
    return ModuleDims(
        width=_whole(module.width, f"Ширина модуля {module.label}"),
        height=(
            _whole(module.height, f"Высота модуля {module.label}")
            if module.height is not None
            else int(base.height)
        ),
        depth=(
            _whole(module.depth, f"Глубина модуля {module.label}")
            if module.depth is not None
            else int(base.depth)
        ),
    )


def _sheet(ws: Workshop, material_id: str, what: str):
    for m in ws.materials.sheets:
        if m.id == material_id:
            return m
    ids = ", ".join(sorted(m.id for m in ws.materials.sheets))
    raise OrderError(f"{what} «{material_id}» не найден в materials.yaml (листовые: {ids}).")


def _facade(ws: Workshop, material_id: str, what: str):
    for m in ws.materials.facades:
        if m.id == material_id:
            return m
    ids = ", ".join(sorted(m.id for m in ws.materials.facades))
    raise OrderError(f"{what} «{material_id}» не найден в materials.yaml (фасады: {ids}).")


def _edge_id(ws: Workshop, material_id: str) -> str:
    """Кромка для материала корпуса — из ключа «кромка_к_материалу» materials.yaml."""
    edge_id = ws.materials.edge_by_material.get(material_id)
    if not edge_id:
        raise OrderError(
            f"В materials.yaml не указана кромка для материала «{material_id}» (ключ «кромка_к_материалу»)."
        )
    return edge_id


def _required_thickness(material, where: str) -> int:
    if material.thickness is None:
        raise WorkshopError(
            f"materials.yaml: у материала «{material.id}» не указана толщина "
            f"(добавьте «толщина: N»), она нужна для {where}."
        )
    return int(material.thickness)


@dataclass
class TemplateOut:
    """Результат шаблона: детали, фурнитура, площадь фасадов, предупреждения."""

    parts: list[Part] = field(default_factory=list)
    hardware: Counter = field(default_factory=Counter)
    facade_area_mm2: int = 0
    warnings: list[str] = field(default_factory=list)


# --- шаблон base_door (1.3) ---------------------------------------------------


def build_base_door(
    module: ModuleSpec, dims: ModuleDims, materials: ProjectMaterials, ws: Workshop
) -> TemplateOut:
    """Нижний распашной: боковины на всю высоту, дно и 2 царги между боковинами,
    накладная задняя ХДФ, полки, двери по правилу ширины, петли и конфирматы по ступеням."""
    if module.shelves != 1:
        raise OrderError(
            f"Модуль {module.label}: пока поддерживается ровно одна полка, "
            f"а задано полок: {module.shelves}. Наполнение придёт с полной схемой (тикет 06)."
        )
    c = ws.construction
    hw = ws.construction.furniture
    t = int(c.case_thickness)
    W, H, D = dims.width, dims.height, dims.depth
    body = _sheet(ws, materials.body, "Материал корпуса")
    edge_id = _edge_id(ws, body.id)
    textured = bool(body.texture)
    out = TemplateOut()

    # боковины на всю высоту и глубину; кромится передняя длинная сторона (L1)
    for suffix, x in (("Л", 0), ("П", W - t)):
        out.parts.append(
            make_part(
                pid=f"{module.label}-бок-{suffix}",
                module_label=module.label,
                name="боковина",
                size_a=H,
                axis_a="z",
                size_b=D,
                axis_b="y",
                thickness=t,
                material_id=body.id,
                textured=textured,
                edges={"L1": edge_id},
                x=x,
                y=0,
                z=0,
            )
        )

    # дно между боковинами, кромится передняя сторона
    out.parts.append(
        make_part(
            pid=f"{module.label}-дно",
            module_label=module.label,
            name="дно",
            size_a=W - 2 * t,
            axis_a="x",
            size_b=D,
            axis_b="y",
            thickness=t,
            material_id=body.id,
            textured=textured,
            edges={"L1": edge_id},
            x=t,
            y=0,
            z=0,
        )
    )

    # 2 царги между боковинами у верха корпуса: передняя (у фасада) и задняя
    rail = int(c.lower.rail_width)
    for n, y in ((1, D - rail), (2, 0)):
        out.parts.append(
            make_part(
                pid=f"{module.label}-царга-{n}",
                module_label=module.label,
                name="царга",
                size_a=W - 2 * t,
                axis_a="x",
                size_b=rail,
                axis_b="y",
                thickness=t,
                material_id=body.id,
                textured=textured,
                edges={"L1": edge_id},
                x=t,
                y=y,
                z=H - rail,
            )
        )

    # полка с отступами от боковин и спереди
    shelf = c.shelf
    side_gap = int(shelf.side_gap)
    out.parts.append(
        make_part(
            pid=f"{module.label}-полка",
            module_label=module.label,
            name="полка",
            size_a=W - 2 * t - 2 * side_gap,
            axis_a="x",
            size_b=D - int(shelf.front_gap),
            axis_b="y",
            thickness=t,
            material_id=body.id,
            textured=textured,
            edges={"L1": edge_id},
            x=t + side_gap,
            y=0,
            z=int(shelf.height),
        )
    )

    # задняя стенка ХДФ накладная, с отступом по контуру; сзади корпуса
    bp = c.back_panel
    hdf = _sheet(ws, bp.material, "Материал задней стенки")
    hdf_t = _required_thickness(hdf, "задней стенки")
    offset = int(bp.contour_offset)
    back_h = H - 2 * offset
    back_w = W - 2 * offset
    out.parts.append(
        make_part(
            pid=f"{module.label}-зад",
            module_label=module.label,
            name="задняя стенка",
            size_a=back_h,
            axis_a="z",
            size_b=back_w,
            axis_b="x",
            thickness=hdf_t,
            material_id=hdf.id,
            textured=False,
            edges={},
            x=offset,
            y=-hdf_t,
            z=offset,
        )
    )

    # двери по правилу ширины: одна при W ≤ порога, иначе две; высота — по формуле
    f = c.facades
    gap = int(f.gap)
    facade_mat = _facade(ws, materials.facades_lower, "Материал фасадов")
    facade_t = _required_thickness(facade_mat, "фасадов")
    facade_h = formulas.evaluate(f.height_lower, H=H, W=W)
    if W > f.two_doors_if_wider_than:
        n_doors = 2
        facade_w = formulas.evaluate(f.two_doors_width, H=H, W=W)
    else:
        n_doors = 1
        facade_w = formulas.evaluate(f.single_door_width, H=H, W=W)
    for i in range(n_doors):
        suffix = ("Л", "П")[i] if n_doors == 2 else ""
        pid = f"{module.label}-фасад-{suffix}" if suffix else f"{module.label}-фасад"
        out.parts.append(
            make_part(
                pid=pid,
                module_label=module.label,
                name="фасад",
                size_a=facade_h,
                axis_a="z",
                size_b=facade_w,
                axis_b="x",
                thickness=facade_t,
                material_id=facade_mat.id,
                textured=False,
                edges={},
                x=gap + i * (facade_w + gap),
                y=D,
                z=gap,
            )
        )
        out.facade_area_mm2 += facade_h * facade_w

    # фурнитура
    hinges_per_facade = steps_pick(c.hinges_by_facade_height, facade_h, "петли_по_высоте_фасада")
    hinges = hinges_per_facade * n_doors
    out.hardware[hw.hinges] += hinges
    out.hardware[hw.plates] += hinges  # ответные планки: по одной на петлю
    joints = [(D, 2)] + [(rail, 2)] * 2  # дно к обеим боковинам; каждая царга к обеим
    confirmats = 0
    for length, count in joints:
        confirmats += (
            steps_pick(c.confirmats_by_joint_length, length, "конфирматы_по_длине_соединения") * count
        )
    out.hardware[hw.confirmats] += confirmats
    out.hardware[hw.caps] += confirmats  # заглушки: по одной на конфирмат
    out.hardware[hw.shelf_pins] += shelf.pins * module.shelves
    out.hardware[hw.legs] += int(c.legs.up_to_800) if W <= c.legs.threshold else int(c.legs.over_800)
    screws = int(c.screws_3_5x16.per_hinge) * hinges
    perimeter = 2 * (back_h + back_w)
    screws += _ceil_div(perimeter, int(bp.fastening_pitch))  # саморезы на ХДФ: периметр / шаг
    out.hardware[hw.screws] += screws
    out.hardware[materials.handles] += n_doors  # ручки: по одной на фасад
    return out


def _ceil_div(a: int, b: int) -> int:
    return int((Decimal(a) / Decimal(b)).to_integral_value(rounding=ROUND_CEILING))


def _edge_totals(parts: list[Part], ws: Workshop) -> dict[str, EdgeTotals]:
    """Метраж кромки по типам: чистая длина, с припуском на каждую кромлёную сторону, к закупке."""
    clean_mm: Counter = Counter()
    sides: Counter = Counter()
    for p in parts:
        for side, edge_id in p.edges.items():
            if not edge_id:
                continue
            clean_mm[edge_id] += p.length if side.startswith("L") else p.width
            sides[edge_id] += 1
    allowance = int(ws.equipment.edging.allowance_per_side)
    edging = ws.construction.edging
    totals = {}
    for edge_id in sorted(clean_mm):
        clean = Decimal(clean_mm[edge_id])
        with_allowance = clean + Decimal(sides[edge_id] * allowance)
        exact = with_allowance * (1 + Decimal(str(edging.reserve)))
        purchase = edging.purchase_rounding.apply_mm(exact)
        totals[edge_id] = EdgeTotals(
            clean_m=float(clean / 1000),
            with_allowance_m=float(with_allowance / 1000),
            purchase_m=float(purchase / 1000),
        )
    return totals


TEMPLATES = {"base_door": build_base_door}


# --- расчёт деталей проекта ----------------------------------------------------


@dataclass
class PartsComputation:
    """Итог расчёта по всему проекту."""

    modules: list[ModuleParts]
    hardware: dict[str, int]
    edges: dict[str, EdgeTotals]
    facade_area: float
    warnings: list[str]


def compute_parts(project: Project, ws: Workshop) -> PartsComputation:
    """Детали по модулям, фурнитура итогом, метраж кромки, площадь фасадов."""
    modules: list[ModuleParts] = []
    hw: Counter = Counter()
    seen: dict[str, str] = {}
    facade_mm2 = 0
    warnings: list[str] = []
    for wall in project.walls:
        for row in wall.rows:
            for module in row.modules:
                out = _build_module(module, row, project.materials, ws)
                for part in out.parts:
                    if part.id in seen:
                        raise OrderError(
                            f"Маркировка «{part.id}» не уникальна: уже есть у детали модуля {seen[part.id]}."
                        )
                    seen[part.id] = module.label
                modules.append(ModuleParts(module=module.label, template=module.template, parts=out.parts))
                hw.update(out.hardware)
                facade_mm2 += out.facade_area_mm2
                warnings += [f"{module.label}: {w}" for w in out.warnings]
    known = {i.id for i in ws.hardware.items}
    unknown = sorted(set(hw) - known)
    if unknown:
        raise OrderError(f"Неизвестные позиции фурнитуры (нет в hardware.yaml): {', '.join(unknown)}.")
    all_parts = [p for m in modules for p in m.parts]
    return PartsComputation(
        modules=modules,
        hardware=dict(sorted(hw.items())),
        edges=_edge_totals(all_parts, ws),
        facade_area=round(facade_mm2 / 1_000_000, 4),
        warnings=warnings,
    )


def _build_module(module: ModuleSpec, row: Row, materials: ProjectMaterials, ws: Workshop) -> TemplateOut:
    """Один модуль → результат шаблона: выбор шаблона, габариты, ряд."""
    builder = TEMPLATES.get(module.template)
    if builder is None:
        known = ", ".join(sorted(TEMPLATES))
        raise OrderError(
            f"Модуль {module.label}: неизвестный шаблон «{module.template}» (известные: {known})."
        )
    if module.template == "base_door" and row.kind != "lower":
        raise OrderError(
            f"Модуль {module.label}: шаблон base_door применяется в нижнем ряду, а он в ряде «{row.kind}»."
        )
    dims = _module_dims(module, row.kind, ws)
    return builder(module, dims, materials, ws)

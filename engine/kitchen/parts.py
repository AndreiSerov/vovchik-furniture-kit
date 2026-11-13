"""Схема детали (1.2), шаблон `base_door` (1.3) и расчёт деталей проекта.

Все размеры и количества — из `construction.yaml` и `equipment.yaml` по формулам
и ступенчатым таблицам; в коде шаблона только структурные числа (число боковин,
царг, соединений). Идентификаторы фурнитуры берутся из ключа «фурнитура»
construction.yaml (МОК), ручек — из материалов проекта.
"""

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from typing import Literal

from pydantic import BaseModel

from kitchen import formulas
from kitchen.errors import OrderError, WorkshopError
from kitchen.formulas import FormulaError, InexactDivisionError
from kitchen.models import EdgeRule, Num, Workshop
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
    "Span",
    "Template",
    "compute_parts",
]

EDGE_SIDES = ("L1", "L2", "W1", "W2")

Axis = Literal["x", "y", "z"]


# --- схема детали ------------------------------------------------------------


class Position(BaseModel):
    """Угол детали в системе модуля, мм: x вдоль стены, y от стены, z вверх от низа корпуса."""

    x: int
    y: int
    z: int


class Orientation(BaseModel):
    """Какой оси модуля соответствует длина, ширина и толщина детали."""

    length: Axis
    width: Axis
    thickness: Axis


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
    area_m2: float | None = None  # только у фасадов (1.2): длина × ширина, м² без округления


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


@dataclass(frozen=True)
class Span:
    """Размер детали вдоль оси модуля, мм."""

    size: int
    axis: Axis


_FACES = {f"{sign}{axis}" for sign in "+-" for axis in "xyz"}


def _side_of_face(face: str, length_axis: str, width_axis: str, pid: str) -> str:
    """Грань детали (`+y`) -> сторона кромки: на оси ширины `+` — L1, `-` — L2;
    на оси длины `-` — W1 (начало), `+` — W2 (конец)."""
    sign, axis = face[0], face[1]
    if axis == width_axis:
        return "L1" if sign == "+" else "L2"
    if axis == length_axis:
        return "W2" if sign == "+" else "W1"
    raise ValueError(f"Деталь {pid}: грань {face} — плоскость детали, кромить её нельзя.")


def make_part(
    *,
    pid: str,
    module_label: str,
    name: str,
    a: Span,
    b: Span,
    thickness: int,
    material_id: str,
    textured: bool,
    at: Position,
    edges: dict[str, str] | None = None,
    area: bool = False,
) -> Part:
    """Собирает деталь. Длина — вдоль текстуры (ось `a` — ось текстуры, её задаёт шаблон),
    у нетекстурных — больший размер. Кромка задаётся гранями в осях модуля (`+y` — передняя)
    или сторонами детали (`L1`); по осям длины и ширины, выбранным здесь, грани
    превращаются в L1/L2/W1/W2, поэтому поворот детали кромку не сбивает."""
    first, second = (a, b) if textured or a.size >= b.size else (b, a)
    if first.axis == second.axis:
        raise ValueError(f"Деталь {pid}: ось длины и ось ширины совпадают ({first.axis}).")
    thickness_axis = next(x for x in ("x", "y", "z") if x not in (first.axis, second.axis))
    edge_map = dict.fromkeys(EDGE_SIDES, "")
    for key, edge_id in (edges or {}).items():
        if key in _FACES:
            key = _side_of_face(key, first.axis, second.axis, pid)
        elif key not in edge_map:
            raise ValueError(f"Деталь {pid}: неизвестная сторона кромки «{key}».")
        edge_map[key] = edge_id
    return Part(
        id=pid,
        module=module_label,
        name=name,
        length=first.size,
        width=second.size,
        thickness=thickness,
        material=material_id,
        texture=textured,
        edges=edge_map,
        position=at,
        orientation=Orientation(length=first.axis, width=second.axis, thickness=thickness_axis),
        area_m2=float(Decimal(first.size * second.size) / 1_000_000) if area else None,
    )


# --- общие помощники шаблонов -------------------------------------------------


def steps_pick(steps: list[tuple[Num, int]], value: Num, table: str) -> int:
    """Ступенчатая таблица: первая ступень, где значение ≤ порога."""
    for threshold, count in steps:
        if value <= threshold:
            return count
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
            else base.height
        ),
        depth=(
            _whole(module.depth, f"Глубина модуля {module.label}") if module.depth is not None else base.depth
        ),
    )


def _material(items: list, material_id: str, what: str, kind: str):
    """Материал по id из списка materials.yaml (`kind` — «листовые» или «фасады»)."""
    for m in items:
        if m.id == material_id:
            return m
    ids = ", ".join(sorted(m.id for m in items))
    raise OrderError(f"{what} «{material_id}» не найден в materials.yaml ({kind}: {ids}).")


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
    return material.thickness


def _calc(module: ModuleSpec, dims: ModuleDims, what: str, formula: str, **variables) -> int:
    """Формула шаблона: единственное место, где считаются формулы construction.yaml.
    Деление не нацело — русская ошибка с модулем и шириной, а не трассировка."""
    try:
        result = formulas.evaluate(formula, **variables)
    except InexactDivisionError as e:
        raise OrderError(
            f"Модуль {module.label}: ширина {dims.width} — {what} «{formula}» не делятся нацело "
            f"({e}); возьмите чётную ширину."
        ) from e
    except FormulaError as e:
        raise OrderError(f"Модуль {module.label}: {what}: {e}.") from e
    if not isinstance(result, int):
        raise OrderError(f"Модуль {module.label}: {what} «{formula}» — ожидалось одно число, а не пара.")
    return result


@dataclass
class TemplateOut:
    """Результат шаблона: детали, фурнитура, предупреждения."""

    parts: list[Part] = field(default_factory=list)
    hardware: Counter = field(default_factory=Counter)
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
    hw = c.furniture
    t = c.case_thickness
    W, H, D = dims.width, dims.height, dims.depth
    body = _material(ws.materials.sheets, materials.body, "Материал корпуса", "листовые")
    edge_id = _edge_id(ws, body.id)
    textured = bool(body.texture)
    out = TemplateOut()

    def carcass(pid: str, name: str, a: Span, b: Span, at: Position, rule: EdgeRule) -> None:
        out.parts.append(
            make_part(
                pid=f"{module.label}-{pid}",
                module_label=module.label,
                name=name,
                a=a,
                b=b,
                thickness=t,
                material_id=body.id,
                textured=textured,
                at=at,
                edges={rule.face: edge_id},
            )
        )

    # боковины на всю высоту и глубину
    for suffix, x in (("Л", 0), ("П", W - t)):
        carcass(
            f"бок-{suffix}", "боковина", Span(H, "z"), Span(D, "y"), Position(x=x, y=0, z=0), c.edging.case
        )

    # дно между боковинами
    carcass("дно", "дно", Span(W - 2 * t, "x"), Span(D, "y"), Position(x=t, y=0, z=0), c.edging.case)

    # 2 царги между боковинами у верха корпуса, плашмя: передняя (у фасада) и задняя
    rail = c.lower.rail_width
    for n, y in ((1, D - rail), (2, 0)):
        carcass(
            f"царга-{n}",
            "царга",
            Span(W - 2 * t, "x"),
            Span(rail, "y"),
            Position(x=t, y=y, z=H - t),
            c.edging.rails,
        )

    # полка с отступами от боковин и спереди
    shelf = c.shelf
    carcass(
        "полка",
        "полка",
        Span(W - 2 * t - 2 * shelf.side_gap, "x"),
        Span(D - shelf.front_gap, "y"),
        Position(x=t + shelf.side_gap, y=0, z=shelf.height),
        c.edging.shelves,
    )

    # задняя стенка ХДФ накладная, с отступом по контуру; сзади корпуса
    bp = c.back_panel
    hdf = _material(ws.materials.sheets, bp.material, "Материал задней стенки", "листовые")
    hdf_t = _required_thickness(hdf, "задней стенки")
    back_h = H - 2 * bp.contour_offset
    back_w = W - 2 * bp.contour_offset
    out.parts.append(
        make_part(
            pid=f"{module.label}-зад",
            module_label=module.label,
            name="задняя стенка",
            a=Span(back_h, "z"),
            b=Span(back_w, "x"),
            thickness=hdf_t,
            material_id=hdf.id,
            textured=False,
            at=Position(x=bp.contour_offset, y=-hdf_t, z=bp.contour_offset),
        )
    )

    # двери по правилу ширины: одна при W ≤ порога, иначе две; высота — по формуле
    f = c.facades
    facade_mat = _material(ws.materials.facades, materials.facades_lower, "Материал фасадов", "фасады")
    facade_t = _required_thickness(facade_mat, "фасадов")
    facade_h = _calc(module, dims, "высота фасада", f.height_lower, H=H, W=W)
    if W > f.two_doors_if_wider_than:
        n_doors = 2
        facade_w = _calc(module, dims, "фасады", f.two_doors_width, H=H, W=W)
    else:
        n_doors = 1
        facade_w = _calc(module, dims, "фасад", f.single_door_width, H=H, W=W)
    for i in range(n_doors):
        suffix = ("Л", "П")[i] if n_doors == 2 else ""
        out.parts.append(
            make_part(
                pid=f"{module.label}-фасад-{suffix}" if suffix else f"{module.label}-фасад",
                module_label=module.label,
                name="фасад",
                a=Span(facade_h, "z"),
                b=Span(facade_w, "x"),
                thickness=facade_t,
                material_id=facade_mat.id,
                textured=False,
                at=Position(x=f.gap + i * (facade_w + f.gap), y=D, z=f.gap),
                area=True,
            )
        )

    # фурнитура; соотношения («по одной на петлю») — из construction.yaml
    hinges = steps_pick(c.hinges_by_facade_height, facade_h, "петли_по_высоте_фасада") * n_doors
    out.hardware[hw.hinges] += hinges
    out.hardware[hw.plates] += hinges * c.plates.per_unit
    joints = [(D, 2)] + [(rail, 2)] * 2  # дно к обеим боковинам; каждая царга к обеим
    confirmats = sum(
        steps_pick(c.confirmats_by_joint_length, length, "конфирматы_по_длине_соединения") * count
        for length, count in joints
    )
    out.hardware[hw.confirmats] += confirmats
    out.hardware[hw.caps] += confirmats * c.caps.per_unit
    out.hardware[hw.shelf_pins] += shelf.pins * module.shelves
    # ключи ножек «до_800»/«свыше_800» должны совпадать с порогом (DECISIONS, тикет 05)
    out.hardware[hw.legs] += c.legs.up_to_800 if W <= c.legs.threshold else c.legs.over_800
    screws = c.screws_3_5x16.per_hinge * hinges
    perimeter = 2 * (back_h + back_w)
    screws += _ceil_div(perimeter, bp.fastening_pitch)  # саморезы на ХДФ: периметр / шаг
    out.hardware[hw.screws] += screws
    out.hardware[materials.handles] += n_doors * c.handles.per_unit
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
    allowance = ws.equipment.edging.allowance_per_side
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


@dataclass(frozen=True)
class Template:
    """Шаблон модуля: построитель деталей и ряд, в котором он применяется."""

    build: Callable[[ModuleSpec, ModuleDims, ProjectMaterials, Workshop], TemplateOut]
    row_kind: str  # lower | upper | tall


TEMPLATES = {"base_door": Template(build_base_door, "lower")}

_ROW_RU = {"lower": "нижнем ряду", "upper": "верхнем ряду", "tall": "пенале"}


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
                warnings += [f"{module.label}: {w}" for w in out.warnings]
    known = {i.id for i in ws.hardware.items}
    unknown = sorted(set(hw) - known)
    if unknown:
        raise OrderError(f"Неизвестные позиции фурнитуры (нет в hardware.yaml): {', '.join(unknown)}.")
    all_parts = [p for m in modules for p in m.parts]
    # площадь фасадов — сумма площадей их деталей; округляется один раз, до 4 знаков
    facade_area = sum((Decimal(str(p.area_m2)) for p in all_parts if p.area_m2 is not None), Decimal(0))
    return PartsComputation(
        modules=modules,
        hardware=dict(sorted(hw.items())),
        edges=_edge_totals(all_parts, ws),
        facade_area=float(facade_area.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)),
        warnings=warnings,
    )


def _build_module(module: ModuleSpec, row: Row, materials: ProjectMaterials, ws: Workshop) -> TemplateOut:
    """Один модуль → результат шаблона: выбор шаблона, габариты, ряд."""
    template = TEMPLATES.get(module.template)
    if template is None:
        known = ", ".join(sorted(TEMPLATES))
        raise OrderError(
            f"Модуль {module.label}: неизвестный шаблон «{module.template}» (известные: {known})."
        )
    if template.row_kind != row.kind:
        raise OrderError(
            f"Модуль {module.label}: шаблон {module.template} применяется в "
            f"{_ROW_RU[template.row_kind]}, а он в ряде «{row.kind}»."
        )
    if module.contents is not None or module.without_back or module.corner is not None:
        raise OrderError(
            f"Модуль {module.label}: наполнение или параметры модуля ещё не поддерживаются расчётом."
        )
    values = materials.model_dump()
    for override in (row.materials, module.materials):
        if override is not None:
            for key, value in override.model_dump(exclude_none=True).items():
                if key == "facades":
                    values["facades_lower" if row.kind == "lower" else "facades_upper"] = value
                elif key == "facade_color":
                    raise OrderError(f"Модуль {module.label}: цвет фасада ещё не поддерживается расчётом.")
                else:
                    values[key] = value
    dims = _module_dims(module, row.kind, ws)
    return template.build(module, dims, ProjectMaterials.model_validate(values), ws)

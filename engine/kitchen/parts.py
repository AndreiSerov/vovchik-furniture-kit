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
from kitchen.models import ApplianceItem, EdgeRule, Num, Workshop
from kitchen.project import Appliance, ModuleSpec, Project, ProjectMaterials, Row

__all__ = [
    "APPLIANCE_NAMES",
    "APPLIANCE_ROWS",
    "BACK_PANEL",
    "DRAWER_BACK",
    "DRAWER_BOTTOM",
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
    "accepts",
    "build_module",
    "built_niche",
    "compute_parts",
    "drawer_depth_shortfall",
    "drawer_parts",
    "facade_color",
    "lower_facades",
    "module_dims",
    "niche_item",
    "resolve_materials",
    "unknown_template_error",
]

EDGE_SIDES = ("L1", "L2", "W1", "W2")
DRAWER_BOTTOM = "дно ящика"  # детали ящика Tandembox: по именам их отличают смета и присадка
DRAWER_BACK = "задняя стенка ящика"
BACK_PANEL = "задняя стенка"  # деталь, по которой находится корпус без задней стенки

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


def module_dims(module: ModuleSpec, row_kind: str, ws: Workshop) -> ModuleDims:
    template = TEMPLATES.get(module.template)
    base = (
        ws.construction.tall
        if template and template.row_kind == "tall"
        else ws.construction.lower
        if row_kind == "lower"
        else ws.construction.upper
    )
    default_depth = template.default_depth(ws) if template and template.default_depth else base.depth
    return ModuleDims(
        width=_whole(module.width, f"Ширина модуля {module.label}"),
        height=(
            _whole(module.height, f"Высота модуля {module.label}")
            if module.height is not None
            else base.height
        ),
        depth=(
            _whole(module.depth, f"Глубина модуля {module.label}")
            if module.depth is not None
            else default_depth
        ),
    )


def built_niche(appliance: Appliance | None, item: ApplianceItem) -> tuple[int, int, int]:
    """Ниша (ширина, высота, глубина), которую шаблон строит под прибор: размеры ниши из проекта
    (`niche_*`), недостающие — ниша из `appliances.yaml`. Габариты корпуса прибора на нишу не влияют.
    Одна функция для шаблонов пенала и вытяжки и для проверок."""
    if appliance is None:
        return item.niche_width, item.niche_height, item.niche_depth

    def pick(value, default: int, what: str) -> int:
        return _whole(value, what) if value is not None else default

    return (
        pick(appliance.niche_width, item.niche_width, "Ширина ниши техники"),
        pick(appliance.niche_height, item.niche_height, "Высота ниши техники"),
        pick(appliance.niche_depth, item.niche_depth, "Глубина ниши техники"),
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


DOOR_SUFFIXES = ("Л", "П")  # больше двух дверей на модуль шаблоны не строят


def _shelf_count(module: ModuleSpec, dims: ModuleDims, c) -> int:
    """Число полок: `contents.shelves`, иначе `shelves` модуля (по умолчанию 1). Полки стоят
    с шагом из construction.yaml и должны помещаться под верхней деталью корпуса."""
    count = module.shelves
    if module.contents is not None and module.contents.shelves is not None:
        count = module.contents.shelves  # новое поле перекрывает старое `shelves` модуля
    t, shelf = c.case_thickness, c.shelf
    room = (
        (dims.height - 2 * t - shelf.height) // shelf.step + 1 if dims.height - 2 * t >= shelf.height else 0
    )
    if count > room:
        raise OrderError(
            f"Модуль {module.label}: при высоте {dims.height} мм помещается не более {room} полок "
            f"(первая на {shelf.height} мм, шаг {shelf.step} мм), а задано {count}."
        )
    return count


def _door_count(module: ModuleSpec, dims: ModuleDims, c, *, no_doors: bool, single_door: bool) -> int:
    """Число дверей: по правилу ширины или из `contents.doors`, если оно допустимо для ширины модуля."""
    f = c.facades
    if no_doors:
        return 0
    if single_door:
        return 1
    requested = module.contents.doors if module.contents is not None else None
    if requested is None:
        return 2 if dims.width > f.two_doors_if_wider_than else 1
    if requested > len(DOOR_SUFFIXES):
        raise OrderError(
            f"Модуль {module.label}: дверей задано {requested}, а модуль строит не более {len(DOOR_SUFFIXES)}."
        )
    if requested == 1 and dims.width > f.single_door_max_module_width:
        raise OrderError(
            f"Модуль {module.label}: одна дверь допустима при ширине модуля не более "
            f"{f.single_door_max_module_width:g} мм, а ширина {dims.width} мм."
        )
    if requested == 2 and dims.width < f.two_doors_min_module_width:
        raise OrderError(
            f"Модуль {module.label}: две двери допустимы при ширине модуля не менее "
            f"{f.two_doors_min_module_width:g} мм, а ширина {dims.width} мм."
        )
    return requested


# --- шаблон base_door (1.3) ---------------------------------------------------


def build_base_door(
    module: ModuleSpec,
    dims: ModuleDims,
    materials: ProjectMaterials,
    ws: Workshop,
    *,
    no_doors: bool = False,
    no_hinges: bool = False,
    single_door: bool = False,
    no_shelf: bool = False,
    no_back: bool = False,
    upper_carcass: bool = False,
    no_bottom: bool = False,
) -> TemplateOut:
    """Нижний распашной: боковины на всю высоту, дно и 2 царги между боковинами,
    накладная задняя ХДФ, полки, двери по правилу ширины, петли и конфирматы по ступеням."""
    c = ws.construction
    hw = c.furniture
    t = c.case_thickness
    W, H, D = dims.width, dims.height, dims.depth
    body = _material(ws.materials.sheets, materials.body, "Материал корпуса", "листовые")
    edge_id = _edge_id(ws, body.id)
    textured = bool(body.texture)
    n_shelves = 0 if no_shelf else _shelf_count(module, dims, c)
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

    if not no_bottom:
        carcass("дно", "дно", Span(W - 2 * t, "x"), Span(D, "y"), Position(x=t, y=0, z=0), c.edging.case)
    rail = c.lower.rail_width
    if upper_carcass:
        carcass(
            "крыш", "крыша", Span(W - 2 * t, "x"), Span(D, "y"), Position(x=t, y=0, z=H - t), c.edging.case
        )
    else:
        for n, y in ((1, D - rail), (2, 0)):
            carcass(
                f"царга-{n}",
                "царга",
                Span(W - 2 * t, "x"),
                Span(rail, "y"),
                Position(x=t, y=y, z=H - t),
                c.edging.rails,
            )

    # полки с отступами от боковин и спереди; i-я полка выше предыдущей на шаг
    shelf = c.shelf
    for n in range(1, n_shelves + 1):
        carcass(
            f"полка-{n}" if n_shelves > 1 else "полка",
            "полка",
            Span(W - 2 * t - 2 * shelf.side_gap, "x"),
            Span(D - shelf.front_gap, "y"),
            Position(x=t + shelf.side_gap, y=0, z=shelf.height + (n - 1) * shelf.step),
            c.edging.shelves,
        )

    if not no_back:
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
                name=BACK_PANEL,
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
    n_doors = _door_count(module, dims, c, no_doors=no_doors, single_door=single_door)
    facade_w = 0
    if n_doors == 2:
        facade_w = _calc(module, dims, "фасады", f.two_doors_width, H=H, W=W)
    elif n_doors == 1:
        facade_w = _calc(module, dims, "фасад", f.single_door_width, H=H, W=W)
    for i in range(n_doors):
        suffix = DOOR_SUFFIXES[i] if n_doors == 2 else ""
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
    hinges = 0
    if n_doors and not no_hinges:
        hinges = steps_pick(c.hinges_by_facade_height, facade_h, "петли_по_высоте_фасада") * n_doors
        out.hardware[hw.hinges] += hinges
        out.hardware[hw.plates] += hinges * c.plates.per_unit
    joints = [(D, 2)] * (1 + int(not no_bottom)) if upper_carcass else [(D, 2)] + [(rail, 2)] * 2
    confirmats = sum(
        steps_pick(c.confirmats_by_joint_length, length, "конфирматы_по_длине_соединения") * count
        for length, count in joints
    )
    out.hardware[hw.confirmats] += confirmats
    out.hardware[hw.caps] += confirmats * c.caps.per_unit
    if n_shelves:
        out.hardware[hw.shelf_pins] += c.shelf.pins * n_shelves
    # ключи ножек «до_800»/«свыше_800» должны совпадать с порогом (DECISIONS, тикет 05)
    if not upper_carcass:
        out.hardware[hw.legs] += c.legs.up_to_800 if W <= c.legs.threshold else c.legs.over_800
    screws = c.screws_3_5x16.per_hinge * hinges
    if not no_back:
        perimeter = 2 * (back_h + back_w)
        screws += _ceil_div(perimeter, bp.fastening_pitch)  # саморезы на ХДФ: периметр / шаг
    out.hardware[hw.screws] += screws
    if n_doors:
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
class BuildContext:
    """Что шаблон знает о проекте кроме самого модуля: техника проекта и ряд, в котором модуль стоит."""

    appliances: list[Appliance]
    row: Row


@dataclass(frozen=True)
class Template:
    """Шаблон модуля: построитель деталей, ряд и свойства, по которым его выбирают проверки и расчёты."""

    build: Callable[[ModuleSpec, ModuleDims, ProjectMaterials, Workshop, BuildContext], TemplateOut]
    row_kind: str  # lower | upper | tall
    carcass: bool = True  # False: только фасад между соседними модулями (ПММ)
    drawers: bool = False  # ящики на направляющих: проверяется внутренняя глубина
    appliances: tuple[str, ...] = ()  # виды техники, под которые шаблон строит нишу
    both_rows: bool = False  # модуль записан в одном ряду стены и занимает оба (пенал)
    default_depth: Callable[[Workshop], int] | None = None  # глубина по умолчанию вместо глубины ряда
    title: str = field(kw_only=True)  # русское название для строки состава КП: «нижний модуль», «пенал»


def _plain(build: Callable[[ModuleSpec, ModuleDims, ProjectMaterials, Workshop], TemplateOut]):
    """Шаблон без привязки к проекту: контекст ему не нужен."""
    return lambda module, dims, materials, ws, ctx: build(module, dims, materials, ws)


def build_base_sink(
    module: ModuleSpec, dims: ModuleDims, materials: ProjectMaterials, ws: Workshop
) -> TemplateOut:
    c = ws.construction
    no_back = c.sink.without_back or module.without_back
    out = build_base_door(
        module.model_copy(update={"shelves": 1}),
        dims,
        materials,
        ws,
        no_shelf=c.sink.without_shelf,
        no_back=no_back,
    )
    if no_back:
        out.warnings.append("без задней стенки")
    if module.contents:
        for insert in module.contents.inserts:
            out.hardware[insert] += 1
    return out


def build_dishwasher_panel(
    module: ModuleSpec, dims: ModuleDims, materials: ProjectMaterials, ws: Workshop
) -> TemplateOut:
    """Фасад ПММ: крепёж поставляется с техникой, корпус отсутствует."""
    f = ws.construction.facades
    facade = _material(ws.materials.facades, materials.facades_lower, "Материал фасадов", "фасады")
    h = _calc(module, dims, "высота фасада", f.height_lower, H=dims.height, W=dims.width)
    w = _calc(module, dims, "ширина фасада", f.single_door_width, H=dims.height, W=dims.width)
    return TemplateOut(
        parts=[
            make_part(
                pid=f"{module.label}-фасад",
                module_label=module.label,
                name="фасад",
                a=Span(h, "z"),
                b=Span(w, "x"),
                thickness=_required_thickness(facade, "фасадов"),
                material_id=facade.id,
                textured=False,
                at=Position(x=f.gap, y=dims.depth, z=f.gap),
                area=True,
            )
        ]
    )


def build_base_bottle(
    module: ModuleSpec, dims: ModuleDims, materials: ProjectMaterials, ws: Workshop
) -> TemplateOut:
    c = ws.construction
    if dims.width > c.bottle.max_width:
        raise OrderError(f"Модуль {module.label}: ширина бутылочницы больше допустимой для одного фасада.")
    out = build_base_door(
        module.model_copy(update={"shelves": 1}),
        dims,
        materials,
        ws,
        single_door=True,
        no_hinges=True,
        no_shelf=True,
    )
    inserts = module.contents.inserts if module.contents and module.contents.inserts else [c.bottle.insert]
    if inserts != [c.bottle.insert]:
        raise OrderError(f"Модуль {module.label}: нужна одна вставка бутылочницы «{c.bottle.insert}».")
    for insert in inserts:
        out.hardware[insert] += 1
    return out


def _drawer_sizes(module: ModuleSpec, dims: ModuleDims, ws: Workshop):
    """Внутренняя ширина, дно и задняя стенка ящика по формулам мастерской."""
    c = ws.construction
    inner = _calc(
        module, dims, "внутренняя ширина ящика", c.drawers.inner_width, W=dims.width, T=c.case_thickness
    )
    try:
        bottom = formulas.evaluate(c.tandembox.bottom, внутр_ширина=inner)
        back = formulas.evaluate(c.tandembox.back, внутр_ширина=inner)
    except FormulaError as e:
        raise OrderError(f"Модуль {module.label}: размеры ящика: {e}.") from e
    if not isinstance(bottom, tuple) or not isinstance(back, tuple):
        raise OrderError(f"Модуль {module.label}: формулы деталей ящика должны задавать два размера.")
    return inner, bottom, back


def drawer_depth_shortfall(module: ModuleSpec, dims: ModuleDims, ws: Workshop) -> tuple[float, str] | None:
    """Внутренняя глубина — глубина корпуса (задняя стенка накладная, фасад снаружи). Нужная глубина — одно
    правило для проверок и `drawer_parts`: дно ящика по формуле мастерской плюс толщина задней стенки ящика,
    но не меньше длины направляющей. Возвращает (нужная глубина, русский текст ошибки) или `None`."""
    c = ws.construction
    material = _material(ws.materials.sheets, c.tandembox.material, "Материал ящика", "листовые")
    thickness = _required_thickness(material, "деталей ящика")
    _, bottom, _ = _drawer_sizes(module, dims, ws)
    guide = c.tandembox.guide_length
    need = max(guide, bottom[1] + thickness)
    if dims.depth >= need:
        return None
    why = (
        f"меньше нужной {need:g} мм (дно ящика {bottom[1]} мм + задняя стенка {thickness} мм, "
        f"но не меньше направляющей {guide:g} мм)"
    )
    return need, f"Модуль {module.label}: внутренняя глубина {dims.depth} мм {why}."


def drawer_parts(
    module: ModuleSpec, dims: ModuleDims, ws: Workshop, *, number: int, z: int, top: int
) -> list[Part]:
    """Дно и задняя стенка ящика Tandembox за фасадом, нижний край которого `z`, верхний `top` (мм от низа
    корпуса). Одна функция для `base_drawers` и пенала. Ящик, который не помещается за свой фасад или по
    глубине корпуса, — ошибка с цифрами, а не деталь отрицательного размера."""
    c = ws.construction
    t, D = c.case_thickness, dims.depth
    material = _material(ws.materials.sheets, c.tandembox.material, "Материал ящика", "листовые")
    thickness = _required_thickness(material, "деталей ящика")
    shortfall = drawer_depth_shortfall(module, dims, ws)
    if shortfall:
        raise OrderError(shortfall[1])
    inner, bottom, back = _drawer_sizes(module, dims, ws)
    if min(*bottom, *back) <= 0:
        raise OrderError(
            f"Модуль {module.label}: ящик не помещается: размеры деталей ящика должны быть положительными, "
            f"а при внутренней ширине {inner} мм они {bottom[0]}×{bottom[1]} и {back[0]}×{back[1]} мм."
        )
    drawer_z = _calc(module, dims, "высота дна ящика", c.drawers.bottom_height, Z=z, T=t)
    drawer_top = drawer_z + max(thickness, back[1])
    if drawer_z < t or drawer_top > top:
        raise OrderError(
            f"Модуль {module.label}: ящик не помещается: ящик занимает высоту от {drawer_z} до {drawer_top} мм, "
            f"а место под него от {t} до {top} мм (фасад ящика {top - z} мм)."
        )
    return [
        make_part(
            pid=f"{module.label}-{suffix}-{number}",
            module_label=module.label,
            name=name,
            a=Span(sizes[0], axes[0]),
            b=Span(sizes[1], axes[1]),
            thickness=thickness,
            material_id=material.id,
            textured=bool(material.texture),
            at=Position(x=(dims.width - sizes[0]) // 2, y=y, z=drawer_z),
            edges=dict.fromkeys(EDGE_SIDES, _edge_id(ws, material.id)) if c.drawers.edge_parts else None,
        )
        for suffix, name, sizes, axes, y in (
            ("дно-ящ", DRAWER_BOTTOM, bottom, ("x", "y"), D - bottom[1]),
            ("зад-ящ", DRAWER_BACK, back, ("x", "z"), D - bottom[1] - thickness),
        )
    ]


def build_base_drawers(
    module: ModuleSpec, dims: ModuleDims, materials: ProjectMaterials, ws: Workshop
) -> TemplateOut:
    c = ws.construction
    out = build_base_door(
        module.model_copy(update={"shelves": 1}), dims, materials, ws, no_doors=True, no_shelf=True
    )
    out.hardware[materials.handles] = 3 * c.handles.per_unit
    out.hardware[c.drawers.hardware] += 3
    out.hardware[c.furniture.screws] += 3 * c.screws_3_5x16.per_drawer
    facades = c.facades
    height = _calc(module, dims, "высота фасадов", facades.height_lower, H=dims.height, W=dims.width)
    width = _calc(module, dims, "ширина фасадов", facades.single_door_width, H=dims.height, W=dims.width)
    top = _whole(facades.three_drawers.top, "Высота верхнего фасада")
    lower_height = _calc(
        module,
        dims,
        "высота фасадов ящика",
        c.drawers.lower_facade_height,
        H=height,
        TOP=top,
        GAP=facades.gap,
    )
    if lower_height <= 0:
        raise OrderError(f"Модуль {module.label}: высота фасада ящика должна быть положительной.")
    facade = _material(ws.materials.facades, materials.facades_lower, "Материал фасадов", "фасады")
    z = facades.gap
    for n, h in enumerate((lower_height, lower_height, top), 1):
        out.parts.append(
            make_part(
                pid=f"{module.label}-фасад-{n}",
                module_label=module.label,
                name="фасад",
                a=Span(width, "x"),
                b=Span(h, "z"),
                thickness=_required_thickness(facade, "фасадов"),
                material_id=facade.id,
                textured=False,
                at=Position(x=facades.gap, y=dims.depth, z=z),
                area=True,
            )
        )
        out.parts += drawer_parts(module, dims, ws, number=n, z=z, top=z + h)
        z += h + facades.gap
    return out


def build_wall_door(
    module: ModuleSpec,
    dims: ModuleDims,
    materials: ProjectMaterials,
    ws: Workshop,
    *,
    no_shelf: bool = False,
    no_bottom: bool = False,
    lift: bool = False,
    no_doors: bool = False,
) -> TemplateOut:
    c = ws.construction
    t = c.case_thickness
    if (
        dims.width <= 2 * t + 2 * c.shelf.side_gap
        or dims.depth <= c.shelf.front_gap
        or dims.height <= 2 * t
        or (not no_shelf and not (t <= c.shelf.height <= dims.height - 2 * t))
    ):
        raise OrderError(
            f"Модуль {module.label}: габариты {dims.width}×{dims.height}×{dims.depth} не позволяют разместить детали."
        )
    # Используем общую геометрию корпуса и фасадов, заменив царги крышей.
    upper_facades = c.facades.model_copy(update={"height_lower": c.facades.height_upper})
    upper_construction = c.model_copy(update={"facades": upper_facades})
    upper_ws = ws.model_copy(update={"construction": upper_construction})
    upper_materials = materials.model_copy(update={"facades_lower": materials.facades_upper})
    out = build_base_door(
        module,
        dims,
        upper_materials,
        upper_ws,
        no_shelf=no_shelf,
        no_doors=no_doors,
        single_door=lift,
        no_hinges=lift,
        upper_carcass=True,
        no_bottom=no_bottom,
    )
    out.hardware[c.upper_rules.hanger] += c.upper_rules.hangers
    if lift:
        out.hardware[c.upper_rules.lift] += 1
    return out


def build_wall_lift(
    module: ModuleSpec, dims: ModuleDims, materials: ProjectMaterials, ws: Workshop
) -> TemplateOut:
    return build_wall_door(module, dims, materials, ws, lift=True)


def build_wall_dryer(
    module: ModuleSpec, dims: ModuleDims, materials: ProjectMaterials, ws: Workshop
) -> TemplateOut:
    if dims.width != ws.construction.upper_rules.dryer_width:
        raise OrderError(
            f"Модуль {module.label}: ширина сушки должна быть {ws.construction.upper_rules.dryer_width} мм."
        )
    out = build_wall_door(
        module,
        dims,
        materials,
        ws,
        no_shelf=True,
        no_bottom=True,
        lift=bool(module.contents and module.contents.lift),
    )
    out.hardware[ws.construction.upper_rules.dryer] += 1
    return out


def build_wall_hood(
    module: ModuleSpec,
    dims: ModuleDims,
    materials: ProjectMaterials,
    ws: Workshop,
    appliance: Appliance | None = None,
) -> TemplateOut:
    c = ws.construction
    hood = niche_item("wall_hood", "hood", ws)
    if hood is None or hood.type != "hood":
        raise WorkshopError(f"appliances.yaml: вытяжка «{c.upper_rules.hood}» не найдена.")
    t = c.case_thickness
    opening_width, opening_height, opening_depth = built_niche(appliance, hood)
    if min(opening_width, opening_depth, opening_height) <= 0:
        raise OrderError(f"Модуль {module.label}: размеры вытяжки должны быть положительными.")
    free_width = dims.width - 2 * t - opening_width
    front_depth = dims.depth - opening_depth
    if front_depth < c.upper_rules.hood_rail_min:
        raise OrderError(
            f"Модуль {module.label}: глубина планки {front_depth} мм меньше допустимых {c.upper_rules.hood_rail_min} мм."
        )
    if free_width < 0 or opening_height > dims.height - t:
        raise OrderError(
            f"Модуль {module.label}: ниша вытяжки {opening_width}×{opening_depth}×{opening_height} не помещается в корпус."
        )
    out = build_wall_door(
        module,
        dims,
        materials,
        ws,
        no_shelf=True,
        no_bottom=True,
        lift=bool(module.contents and module.contents.lift),
    )
    body = _material(ws.materials.sheets, materials.body, "Материал корпуса", "листовые")
    out.parts.append(
        make_part(
            pid=f"{module.label}-дно-выт-1",
            module_label=module.label,
            name="передняя планка дна вытяжки",
            a=Span(dims.width - 2 * t, "x"),
            b=Span(front_depth, "y"),
            thickness=t,
            material_id=body.id,
            textured=bool(body.texture),
            at=Position(x=t, y=opening_depth, z=0),
            edges={c.edging.case.face: _edge_id(ws, body.id)},
        )
    )
    added = 2 * steps_pick(c.confirmats_by_joint_length, front_depth, "конфирматы_по_длине_соединения")
    out.hardware[c.furniture.confirmats] += added
    out.hardware[c.furniture.caps] += added * c.caps.per_unit
    return out


def _build_corner(
    module: ModuleSpec, dims: ModuleDims, materials: ProjectMaterials, ws: Workshop, row: Row, *, upper: bool
) -> TemplateOut:
    """Прямой угловой корпус с одной дверью и неподвижной заглушкой.

    Глухая часть — со стороны угла: у последнего модуля ряда (стена идёт слева направо, соседняя стена
    начинается от угла) справа, у первого при других модулях в ряду слева; дверь — со стороны прохода."""
    c = ws.construction
    rules = c.corner_rules
    gap = c.facades.gap
    facade_width = dims.width - 3 * gap - rules.upper_filler_width if upper else rules.base_facade_width
    filler_width = dims.width - 3 * gap - facade_width
    blind_left = len(row.modules) > 1 and row.modules[0] is module
    filler_x = gap if blind_left else 2 * gap + facade_width
    facade_x = 2 * gap + filler_width if blind_left else gap
    if (
        min(facade_width, filler_width, dims.height - 2 * gap) <= 0
        or dims.width <= 2 * c.case_thickness
        or dims.depth <= c.case_thickness
        or dims.height <= 2 * c.case_thickness
        or (not upper and dims.depth < c.lower.rail_width)
    ):
        raise OrderError(f"Модуль {module.label}: габариты не позволяют разместить заглушку и фасад.")
    if upper:
        out = build_wall_door(
            module.model_copy(update={"shelves": 1}), dims, materials, ws, no_shelf=False, no_doors=True
        )
    else:
        # нижний угловой: карусель или одна полка с полкодержателями (замена варианта «экономия», тикет 17)
        shelf = bool(module.contents and module.contents.shelves)
        out = build_base_door(
            module.model_copy(update={"shelves": 1}), dims, materials, ws, no_shelf=not shelf, no_doors=True
        )
    if upper:
        facade_h = _calc(module, dims, "высота фасада", c.facades.height_upper, H=dims.height, W=dims.width)
    else:
        facade_h = _calc(module, dims, "высота фасада", c.facades.height_lower, H=dims.height, W=dims.width)
        if not shelf:
            out.hardware[rules.carousel] += 1
    body = _material(ws.materials.sheets, rules.filler_material, "Материал заглушки", "листовые")
    facade_id = materials.facades_upper if upper else materials.facades_lower
    facade = _material(ws.materials.facades, facade_id, "Материал фасадов", "фасады")
    out.parts.append(
        make_part(
            pid=f"{module.label}-загл",
            module_label=module.label,
            name="заглушка",
            a=Span(facade_h, "z"),
            b=Span(filler_width, "x"),
            thickness=_required_thickness(body, "заглушки"),
            material_id=body.id,
            textured=bool(body.texture),
            at=Position(x=filler_x, y=dims.depth, z=gap),
            edges=dict.fromkeys(EDGE_SIDES, _edge_id(ws, body.id)) if rules.filler_edges else None,
        )
    )
    out.parts.append(
        make_part(
            pid=f"{module.label}-фасад",
            module_label=module.label,
            name="фасад",
            a=Span(facade_h, "z"),
            b=Span(facade_width, "x"),
            thickness=_required_thickness(facade, "фасадов"),
            material_id=facade.id,
            textured=False,
            at=Position(x=facade_x, y=dims.depth, z=gap),
            area=True,
        )
    )
    hinges = steps_pick(c.hinges_by_facade_height, facade_h, "петли_по_высоте_фасада")
    out.hardware[c.furniture.hinges] += hinges
    out.hardware[c.furniture.plates] += hinges * c.plates.per_unit
    out.hardware[c.furniture.screws] += hinges * c.screws_3_5x16.per_hinge + rules.filler_screws
    out.hardware[materials.handles] += c.handles.per_unit
    return out


def build_base_corner(
    module: ModuleSpec, dims: ModuleDims, materials: ProjectMaterials, ws: Workshop, ctx: BuildContext
) -> TemplateOut:
    return _build_corner(module, dims, materials, ws, ctx.row, upper=False)


def build_wall_corner(
    module: ModuleSpec, dims: ModuleDims, materials: ProjectMaterials, ws: Workshop, ctx: BuildContext
) -> TemplateOut:
    return _build_corner(module, dims, materials, ws, ctx.row, upper=True)


def build_tall_oven(
    module: ModuleSpec, dims: ModuleDims, materials: ProjectMaterials, ws: Workshop, ctx: BuildContext
) -> TemplateOut:
    from kitchen.tall import build_tall_oven as build

    return build(module, dims, materials, ws, ctx.appliances)


def build_hood_of_project(
    module: ModuleSpec, dims: ModuleDims, materials: ProjectMaterials, ws: Workshop, ctx: BuildContext
) -> TemplateOut:
    matching = [item for item in ctx.appliances if item.module == module.label and item.type == "hood"]
    if len(matching) > 1:
        raise OrderError(f"Модуль {module.label}: указано несколько вытяжек.")
    return build_wall_hood(module, dims, materials, ws, matching[0] if matching else None)


TEMPLATES = {
    "tall_oven": Template(
        build_tall_oven,
        "tall",
        drawers=True,
        appliances=("oven", "microwave"),
        both_rows=True,
        title="пенал",
    ),
    "base_door": Template(_plain(build_base_door), "lower", title="нижний модуль"),
    "base_corner": Template(build_base_corner, "lower", title="нижний угловой модуль"),
    "wall_corner": Template(
        build_wall_corner,
        "upper",
        default_depth=lambda ws: ws.construction.corner_rules.upper_depth,
        title="верхний угловой модуль",
    ),
    "wall_door": Template(_plain(build_wall_door), "upper", title="верхний модуль"),
    "wall_lift": Template(_plain(build_wall_lift), "upper", title="верхний модуль"),
    "wall_hood": Template(build_hood_of_project, "upper", appliances=("hood",), title="модуль над вытяжкой"),
    "wall_dryer": Template(_plain(build_wall_dryer), "upper", title="верхний модуль"),
    "base_sink": Template(_plain(build_base_sink), "lower", appliances=("sink",), title="модуль под мойку"),
    "base_bottle": Template(_plain(build_base_bottle), "lower", title="нижний модуль-бутылочница"),
    "base_drawers": Template(_plain(build_base_drawers), "lower", drawers=True, title="нижний модуль"),
    "dishwasher_panel": Template(
        _plain(build_dishwasher_panel),
        "lower",
        carcass=False,
        appliances=("dishwasher",),
        title="фасад посудомоечной машины",
    ),
}

# Русские названия видов техники для строки состава КП (ключи — `models.APPLIANCE_KINDS`, сверяет тест).
APPLIANCE_NAMES = {
    "sink": "мойка",
    "hob": "варочная панель",
    "cooker": "отдельностоящая плита",
    "oven": "духовой шкаф",
    "microwave": "микроволновка",
    "dishwasher": "посудомойка",
    "washing_machine": "стиральная машина",
    "coffee_machine": "кофемашина",
    "wine_cooler": "винный шкаф",
    "other": "другая техника",
    "hood": "вытяжка",
}

# Явная несовместимость вида техники с рядом модуля: вытяжка только вверху, остальное только внизу
# (духовка ещё и в пенале). Что не названо (СВЧ, холодильник…), не ограничено.
APPLIANCE_ROWS = {
    "hood": ("upper",),
    "oven": ("lower", "tall"),
    "dishwasher": ("lower",),
    "hob": ("lower",),
    "sink": ("lower",),
}


def niche_item(template: str, appliance_type: str, ws: Workshop) -> ApplianceItem | None:
    """Позиция каталога, по которой шаблон строит нишу под прибор (пенал, вытяжка), иначе `None`.
    Одна функция для шаблонов и проверок."""
    c = ws.construction
    wanted = {
        ("tall_oven", "oven"): c.tall_rules.oven,
        ("tall_oven", "microwave"): c.tall_rules.microwave,
        ("wall_hood", "hood"): c.upper_rules.hood,
    }.get((template, appliance_type))
    return next((i for i in ws.appliances.items if i.id == wanted), None) if wanted else None


def accepts(template: str, appliance_type: str, ws: Workshop) -> bool:
    """Принимает ли шаблон прибор: строит под него нишу или это техника «в столешницу»
    и шаблон — нижний корпусной модуль."""
    t = TEMPLATES[template]
    on_top = appliance_type in ws.construction.countertop_appliances
    return appliance_type in t.appliances or (on_top and t.carcass and t.row_kind == "lower")


_ROW_RU = {"lower": "нижнем ряду", "upper": "верхнем ряду", "tall": "пенале"}


# --- расчёт деталей проекта ----------------------------------------------------


@dataclass
class PartsComputation:
    """Итог расчёта по всему проекту."""

    modules: list[ModuleParts]
    hardware: dict[str, int]
    edges: dict[str, EdgeTotals]
    facade_area: float
    facade_by_color: dict[str, float]  # м² по цветам; без цвета в проекте — «без цвета»
    warnings: list[str]


NO_COLOR = "без цвета"


def lower_facades(module: ModuleSpec, row: Row) -> bool:
    """Фасады модуля нижние: нижний ряд или шаблон, входящий в оба ряда (пенал)."""
    template = TEMPLATES.get(module.template)
    return row.kind == "lower" or (template is not None and template.both_rows)


def resolve_materials(module: ModuleSpec, row: Row, materials: ProjectMaterials) -> ProjectMaterials:
    """Материалы модуля: проект, поверх него переопределения ряда, затем модуля. Фасады и цвет
    фасада относятся к верхним или нижним фасадам по ряду (пенал — нижние). Одно место для
    расчёта деталей, площади по цветам и закупки."""
    values = materials.model_dump()
    lower = lower_facades(module, row)
    keys = {
        "facades": "facades_lower" if lower else "facades_upper",
        "facade_color": "facades_lower_color" if lower else "facades_upper_color",
    }
    for override in (row.materials, module.materials):
        if override is not None:
            for key, value in override.model_dump(exclude_none=True).items():
                values[keys.get(key, key)] = value
    return ProjectMaterials.model_validate(values)


def facade_color(module: ModuleSpec, row: Row, materials: ProjectMaterials) -> str:
    """Цвет фасадов модуля с учётом переопределений; без цвета — «без цвета»."""
    resolved = resolve_materials(module, row, materials)
    lower = lower_facades(module, row)
    return (resolved.facades_lower_color if lower else resolved.facades_upper_color) or NO_COLOR


def _area_4(area: Decimal) -> float:
    return float(area.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def compute_parts(project: Project, ws: Workshop) -> PartsComputation:
    """Детали по модулям, фурнитура итогом, метраж кромки, площадь фасадов."""
    modules: list[ModuleParts] = []
    hw: Counter = Counter()
    seen: dict[str, str] = {}
    warnings: list[str] = []
    colored: dict[str, Decimal] = {}
    for wall in project.walls:
        for row in wall.rows:
            for module in row.modules:
                if module.template in ("base_corner", "wall_corner") and module.corner:
                    adjacent = module.corner.adjacent_wall
                    if adjacent == wall.label or adjacent not in {w.label for w in project.walls}:
                        raise OrderError(
                            f"Модуль {module.label}: соседняя стена «{adjacent}» должна существовать и отличаться от стены «{wall.label}»."
                        )
                out = build_module(module, row, project.materials, ws, project.appliances)
                for part in out.parts:
                    if part.id in seen:
                        raise OrderError(
                            f"Маркировка «{part.id}» не уникальна: уже есть у детали модуля {seen[part.id]}."
                        )
                    seen[part.id] = module.label
                modules.append(ModuleParts(module=module.label, template=module.template, parts=out.parts))
                color = facade_color(module, row, project.materials)
                for part in out.parts:
                    if part.area_m2 is not None:
                        colored[color] = colored.get(color, Decimal(0)) + Decimal(str(part.area_m2))
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
        facade_area=_area_4(facade_area),
        facade_by_color={c: _area_4(a) for c, a in sorted(colored.items())},
        warnings=warnings,
    )


def unknown_template_error(module: ModuleSpec) -> OrderError:
    """Ошибка неизвестного шаблона: один текст для расчёта деталей и проверок."""
    known = ", ".join(sorted(TEMPLATES))
    return OrderError(f"Модуль {module.label}: неизвестный шаблон «{module.template}» (известные: {known}).")


def build_module(
    module: ModuleSpec, row: Row, materials: ProjectMaterials, ws: Workshop, appliances: list[Appliance]
) -> TemplateOut:
    """Один модуль → результат шаблона: выбор шаблона, габариты, ряд."""
    template = TEMPLATES.get(module.template)
    if template is None:
        raise unknown_template_error(module)
    if template.row_kind != "tall" and template.row_kind != row.kind:
        raise OrderError(
            f"Модуль {module.label}: шаблон {module.template} применяется в "
            f"{_ROW_RU[template.row_kind]}, а он в ряде «{row.kind}»."
        )
    if module.template in ("base_sink", "base_bottle", "base_drawers"):
        if "shelves" in module.model_fields_set:
            raise OrderError(
                f"Модуль {module.label}: число полок {module.shelves} не поддерживается шаблоном."
            )
        contents = module.contents
        unsupported = bool(module.corner or (module.without_back and module.template != "base_sink"))
        if contents:
            unsupported |= bool(contents.doors is not None or contents.shelves is not None or contents.lift)
            unsupported |= bool(
                contents.drawers not in (None, 3)
                if module.template == "base_drawers"
                else contents.drawers is not None
            )
            unsupported |= bool(contents.inserts and module.template == "base_drawers")
        if unsupported:
            raise OrderError(
                f"Модуль {module.label}: заданное наполнение или параметры не поддерживаются шаблоном."
            )
    if module.template in ("wall_lift", "wall_dryer", "wall_hood"):
        contents = module.contents
        if contents and (
            contents.doors is not None
            or contents.shelves is not None
            or contents.drawers is not None
            or contents.lift not in (None, ws.construction.upper_rules.lift)
            or (
                contents.inserts not in ([], [ws.construction.upper_rules.dryer])
                if module.template == "wall_dryer"
                else bool(contents.inserts)
            )
        ):
            raise OrderError(f"Модуль {module.label}: неподдерживаемое наполнение.")
        if module.without_back or module.corner or "shelves" in module.model_fields_set:
            raise OrderError(f"Модуль {module.label}: неподдерживаемые параметры.")
    contents = module.contents
    if module.template in ("base_corner", "wall_corner") and (
        module.corner is None or not module.corner.adjacent_wall.strip()
    ):
        raise OrderError(f"Модуль {module.label}: укажите соседнюю стену углового модуля.")
    if module.template in ("base_corner", "wall_corner") and (
        module.without_back
        or "shelves" in module.model_fields_set
        or (
            contents
            and (
                contents.doors not in (None, 1)
                or contents.shelves not in ((None, 0, 1) if module.template == "base_corner" else (None, 1))
                or contents.drawers is not None
                or contents.lift
                or contents.inserts
                not in (
                    [],
                    [ws.construction.corner_rules.carousel] if module.template == "base_corner" else [],
                )
                or (contents.shelves == 1 and contents.inserts and module.template == "base_corner")
            )
        )
    ):
        raise OrderError(f"Модуль {module.label}: неподдерживаемое наполнение.")
    if module.template == "tall_oven" and (
        module.without_back
        or module.corner
        or "shelves" in module.model_fields_set
        or (
            contents
            and (
                contents.doors not in (None, 1)
                or contents.shelves not in (None, 1)
                or contents.drawers not in (None, 1)
                or contents.lift
                or contents.inserts
            )
        )
    ):
        raise OrderError(f"Модуль {module.label}: неподдерживаемое наполнение.")
    if module.template in ("base_door", "wall_door"):
        extra = [
            name
            for name, given in (
                ("ящики", contents is not None and contents.drawers is not None),
                ("вставки", contents is not None and bool(contents.inserts)),
                ("подъёмник", contents is not None and contents.lift is not None),
                ("«без задней стенки»", module.without_back),
                ("угловые параметры", module.corner is not None),
            )
            if given
        ]
        if extra:
            raise OrderError(
                f"Модуль {module.label}: шаблон {module.template} строит только двери и полки, "
                f"наполнение или параметры не поддерживаются: {', '.join(extra)}."
            )
    elif module.template not in (
        "tall_oven",
        "base_corner",
        "wall_corner",
        "base_sink",
        "base_bottle",
        "base_drawers",
        "wall_lift",
        "wall_dryer",
        "wall_hood",
    ) and (module.contents is not None or module.without_back or module.corner is not None):
        raise OrderError(
            f"Модуль {module.label}: наполнение или параметры модуля ещё не поддерживаются расчётом."
        )
    resolved_materials = resolve_materials(module, row, materials)
    dims = module_dims(module, row.kind, ws)
    out = template.build(module, dims, resolved_materials, ws, BuildContext(appliances, row))
    _check_sound(module, dims, out)
    return out


def _negative_hardware(hardware: Counter) -> list[str]:
    """Позиции фурнитуры с количеством меньше нуля, «id количество»."""
    return sorted(f"{item} {n}" for item, n in hardware.items() if n < 0)


def _check_sound(module: ModuleSpec, dims: ModuleDims, out: TemplateOut) -> None:
    """Шаблон на этих габаритах строит деталь, которой нет: размер не больше нуля, положение за пределами
    модуля или фурнитуры меньше нуля — ошибка модуля, а не молчаливо неверный вывод. Единственное
    место проверки для всех шаблонов. Задняя стенка ХДФ накладная: она стоит за корпусом (`y < 0`)."""
    problems = []
    for part in out.parts:
        if min(part.length, part.width, part.thickness) <= 0:
            problems.append(f"{part.id} {part.length}×{part.width} мм")
        else:
            at = part.position
            if min(at.x, at.z) < 0 or (at.y < 0 and part.name != BACK_PANEL):
                problems.append(f"{part.id} за пределами модуля (x={at.x}, y={at.y}, z={at.z})")
    if problems:
        raise OrderError(
            f"Модуль {module.label}: при габаритах {dims.width}×{dims.height}×{dims.depth} мм шаблон "
            f"{module.template} строит деталь неверного размера или положения: {'; '.join(problems)}."
        )
    negative = _negative_hardware(out.hardware)
    if negative:
        raise OrderError(f"Модуль {module.label}: количество фурнитуры меньше нуля: {', '.join(negative)}.")
